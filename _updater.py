"""Background updater for the extrude-ai addon.

Downloads a versioned zip from a signed GCS URL, verifies its SHA-256,
extracts it to ``versions/<latest>.partial/``, atomically renames to
``versions/<latest>/``, and rewrites ``versions/current.txt`` to point at
the new version.

The bootstrap loads whatever ``current.txt`` says on the next FreeCAD launch,
so the user-visible effect is "newer addon on next start" with zero clicks.

Threading model:
  - ``apply_addon_update`` runs in a daemon thread spawned by ws_client.
  - All work is stdlib — urllib + zipfile + hashlib + shutil — to avoid any
    pip dependency on FreeCAD's bundled Python.

Atomicity:
  - The download lands in ``<latest>.zip.partial`` and is renamed only after
    sha256 verification.
  - The extraction lands in ``<latest>.partial/`` and is renamed only after
    successful unzip.
  - ``current.txt`` is written via tmp + os.replace.

Dev-mode safety:
  - If ``current.txt`` reads "dev", we refuse to apply anything.  The dev
    workflow points ``versions/dev/`` at the workspace and we don't want
    backend updates to clobber that.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import zipfile
from pathlib import Path
from typing import Callable
from urllib.request import Request, urlopen

logger = logging.getLogger("extrude-ai.updater")


_DEV_VERSION_NAME = "dev"
_KEEP_VERSIONS = 2  # current + previous; older ones are GC'd on each successful update
_DOWNLOAD_CHUNK = 1024 * 256  # 256 KiB


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def apply_addon_update(
    frame: dict,
    addon_root: Path,
    on_progress: Callable[[int, int], None] | None = None,
) -> bool:
    """Download + stage the version described by an addon_update frame.

    Returns True on success (a fresh version is now staged + activated).
    Returns False on any failure; the addon stays on the version it was on.
    Never raises — callers running in a daemon thread can rely on a clean exit.

    on_progress(downloaded_bytes, total_bytes) is called periodically during
    the download for UI updates.  total_bytes may be 0 if the server didn't
    advertise a Content-Length.
    """
    try:
        latest = str(frame["latest_version"])
        download_url = str(frame["download_url"])
        sha256 = str(frame["sha256"]).lower()
    except (KeyError, TypeError, ValueError) as exc:
        logger.error("Malformed addon_update frame: %s", exc)
        return False

    versions_dir = addon_root / "versions"
    versions_dir.mkdir(parents=True, exist_ok=True)

    if _is_dev_mode(versions_dir):
        logger.info("Dev mode active (current.txt=dev) — refusing update.")
        return False

    target_dir = versions_dir / latest
    if target_dir.is_dir():
        logger.info(
            "Version %s already staged — pointing current.txt at it.", latest,
        )
        _write_current(versions_dir, latest)
        _gc_old_versions(versions_dir, keep_latest=latest)
        return True

    partial_zip = versions_dir / f".{latest}.zip.partial"
    final_zip = versions_dir / f".{latest}.zip"
    extract_dir = versions_dir / f".{latest}.partial"

    # Wipe any leftovers from a previous interrupted run.
    for stale in (partial_zip, final_zip):
        if stale.exists():
            stale.unlink()
    if extract_dir.exists():
        shutil.rmtree(extract_dir, ignore_errors=True)

    try:
        _download_with_verify(
            url=download_url,
            dest=partial_zip,
            expected_sha256=sha256,
            on_progress=on_progress,
        )
        partial_zip.rename(final_zip)
    except Exception as exc:
        logger.error("Download failed for version %s: %s", latest, exc)
        partial_zip.unlink(missing_ok=True)
        return False

    try:
        extract_dir.mkdir(parents=True, exist_ok=False)
        with zipfile.ZipFile(final_zip) as zf:
            zf.extractall(extract_dir)
        _flatten_single_top_level_dir(extract_dir)
    except Exception as exc:
        logger.error("Extraction failed for version %s: %s", latest, exc)
        shutil.rmtree(extract_dir, ignore_errors=True)
        final_zip.unlink(missing_ok=True)
        return False

    final_zip.unlink(missing_ok=True)

    # Atomic activation.
    try:
        if target_dir.exists():
            shutil.rmtree(target_dir)
        extract_dir.rename(target_dir)
        _write_current(versions_dir, latest)
    except Exception as exc:
        logger.error("Activation of version %s failed: %s", latest, exc)
        shutil.rmtree(extract_dir, ignore_errors=True)
        return False

    _gc_old_versions(versions_dir, keep_latest=latest)
    logger.info("Addon version %s staged; takes effect on next FreeCAD restart.", latest)
    return True


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _is_dev_mode(versions_dir: Path) -> bool:
    current_file = versions_dir / "current.txt"
    if not current_file.exists():
        return False
    return current_file.read_text(encoding="utf-8").strip() == _DEV_VERSION_NAME


def _write_current(versions_dir: Path, version: str) -> None:
    target = versions_dir / "current.txt"
    tmp = target.with_suffix(".txt.tmp")
    tmp.write_text(version, encoding="utf-8")
    tmp.replace(target)


def _download_with_verify(
    url: str,
    dest: Path,
    expected_sha256: str,
    on_progress: Callable[[int, int], None] | None,
) -> None:
    """Stream-download ``url`` into ``dest``, verifying the SHA-256."""
    req = Request(url, headers={"User-Agent": "extrude-ai-addon-updater/1"})
    h = hashlib.sha256()
    downloaded = 0

    with urlopen(req, timeout=60) as resp:  # noqa: S310 - URL is signed by trusted backend
        total = int(resp.headers.get("Content-Length") or 0)
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(_DOWNLOAD_CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                downloaded += len(chunk)
                if on_progress is not None:
                    try:
                        on_progress(downloaded, total)
                    except Exception:  # progress callback bugs must not fail the update
                        pass

    actual = h.hexdigest()
    if actual != expected_sha256:
        raise RuntimeError(
            f"sha256 mismatch: expected {expected_sha256}, got {actual}"
        )


def _flatten_single_top_level_dir(extract_dir: Path) -> None:
    """If the zip has a single top-level directory, move its contents up.

    Some build pipelines wrap everything in ``<name>-<ver>/`` which would
    leave us with ``versions/0.4.2/extrude-ai-freecad-0.4.2/InitGui.py``
    instead of ``versions/0.4.2/InitGui.py``.  Flatten that.
    """
    children = list(extract_dir.iterdir())
    if len(children) != 1 or not children[0].is_dir():
        return
    nested = children[0]
    for item in nested.iterdir():
        shutil.move(str(item), str(extract_dir / item.name))
    nested.rmdir()


def _gc_old_versions(versions_dir: Path, keep_latest: str) -> None:
    """Keep the most recent ``_KEEP_VERSIONS`` semver dirs; remove the rest.

    Never deletes ``versions/dev/`` and never deletes ``keep_latest`` even if
    sorting would otherwise drop it.
    """
    pairs: list[tuple[tuple[int, ...], Path]] = []
    for child in versions_dir.iterdir():
        if not child.is_dir() or child.name == _DEV_VERSION_NAME:
            continue
        v = _parse_semver(child.name)
        if v is None:
            continue
        pairs.append((v, child))

    pairs.sort(key=lambda p: p[0])
    keepers = {keep_latest, *(p[1].name for p in pairs[-_KEEP_VERSIONS:])}

    for _v, path in pairs:
        if path.name in keepers:
            continue
        logger.info("Removing old addon version %s", path.name)
        shutil.rmtree(path, ignore_errors=True)


def _parse_semver(s: str) -> tuple[int, ...] | None:
    parts = s.split(".")
    try:
        return tuple(int(p) for p in parts if p)
    except ValueError:
        return None
