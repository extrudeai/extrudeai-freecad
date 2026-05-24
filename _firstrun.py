"""_firstrun.py — First-run initial version download for the extrude-ai addon.

Called by the bootstrap InitGui.py when no versions/ directory exists yet
(i.e. the user just ran the installer for the first time and FreeCAD has
never loaded the addon before).

Fetches manifest.json from the public GCS bucket, picks the latest stable
version for the current platform, downloads the versioned zip (with SHA-256
verification), and extracts it to versions/<x.y.z>/ by reusing the existing
_updater.apply_addon_update() machinery.

All network work is stdlib-only (urllib + json + hashlib).  Qt is used only
for the progress dialog — imported lazily so this module works in headless
or test contexts without crashing.
"""

from __future__ import annotations

import json
import logging
import platform
import sys
import threading
from pathlib import Path
from typing import Callable
from urllib.request import Request, urlopen

import _bootstrap

logger = logging.getLogger("extrude-ai.firstrun")

_UPDATE_LOCK = threading.Lock()

_INSTALLER_BUCKET_BASE = "https://storage.googleapis.com/extrude-ai-installer"
_CHANNEL = "stable"
_FETCH_TIMEOUT = 30  # seconds for manifest fetch


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def pick_platform_tag() -> str:
    """Return the platform tag matching this machine.

    Possible values: macos-arm64 | macos-x86_64 | win-x86_64 | linux-x86_64
    """
    plat = sys.platform
    machine = platform.machine().lower()
    if plat == "darwin":
        return "macos-arm64" if machine in ("arm64", "aarch64") else "macos-x86_64"
    if plat == "win32":
        return "win-x86_64"
    return "linux-x86_64"


def fetch_manifest() -> dict:
    """Download and parse manifest.json from the public installer bucket."""
    url = f"{_INSTALLER_BUCKET_BASE}/manifest.json"
    req = Request(url, headers={"User-Agent": "extrude-ai-firstrun/1"})
    with urlopen(req, timeout=_FETCH_TIMEOUT) as resp:  # noqa: S310 – trusted URL
        return json.loads(resp.read().decode("utf-8"))


def check_for_updates(
    addon_root: Path,
    on_progress: Callable[[int, int], None] | None = None,
    frame: dict | None = None,
) -> str | None:
    """Download a newer addon version when one is available.

    When ``frame`` is omitted, fetches the public manifest and compares the
    installed version to ``channels.stable.latest``.  When ``frame`` is provided
    (e.g. from a backend ``addon_update`` WebSocket message), applies it directly.

    Returns the staged version string on success, else None.  Never raises.
    """
    import _updater  # noqa: PLC0415 — bootstrap-local module

    versions_dir = addon_root / "versions"
    if _updater._is_dev_mode(versions_dir):
        logger.debug("Dev mode — skipping update check.")
        return None

    with _UPDATE_LOCK:
        try:
            if frame is None:
                manifest = fetch_manifest()
                platform_tag = pick_platform_tag()
                current = _bootstrap.read_active_version(addon_root)
                channel = manifest.get("channels", {}).get(_CHANNEL, {})
                latest = str(channel.get("latest") or "")
                if not latest:
                    logger.warning("Manifest has no stable latest version.")
                    return None
                cmp = _bootstrap.compare_semver(current, latest)
                if cmp is None:
                    logger.warning(
                        "Cannot compare versions current=%r latest=%r",
                        current,
                        latest,
                    )
                    return None
                if cmp >= 0:
                    logger.debug(
                        "Addon up to date (current=%s latest=%s).", current, latest
                    )
                    return None
                frame = _build_update_frame(manifest, platform_tag)
                logger.info(
                    "Update available: %s -> %s (%s)", current, latest, platform_tag
                )

            latest = str(frame["latest_version"])
            ok = _updater.apply_addon_update(
                frame, addon_root, on_progress=on_progress
            )
            return latest if ok else None
        except Exception as exc:
            logger.error("check_for_updates failed: %s", exc)
            return None


def download_initial_version(addon_root: Path) -> bool:
    """Download and stage the latest stable version for this platform.

    Shows a Qt progress dialog while downloading.  Returns True on success.
    On any failure, logs the error, shows a user-facing Qt error box, and
    returns False — the caller should handle False gracefully.
    """
    # --- resolve manifest -------------------------------------------------
    try:
        manifest = fetch_manifest()
        platform_tag = pick_platform_tag()
        frame = _build_update_frame(manifest, platform_tag)
        latest = str(frame["latest_version"])
        logger.info(
            "[extrude-ai firstrun] Downloading version %s for %s", latest, platform_tag
        )
    except Exception as exc:
        logger.error("[extrude-ai firstrun] Manifest fetch failed: %s", exc)
        _qt_error(
            f"Could not reach the extrude-ai update server:\n\n{exc}\n\n"
            "Check your internet connection and relaunch FreeCAD to try again."
        )
        return False

    # --- download with progress dialog ------------------------------------
    dialog = _ProgressDialog(f"Installing extrude-ai v{latest}…")
    dialog.show()

    def on_progress(downloaded: int, total: int) -> None:
        if total > 0:
            dialog.set_progress(int(downloaded * 100 / total))
        else:
            dialog.set_progress(-1)  # indeterminate

    try:
        import _updater  # noqa: PLC0415 — local module in bootstrap root

        ok = _updater.apply_addon_update(frame, addon_root, on_progress=on_progress)
    except Exception as exc:
        logger.error("[extrude-ai firstrun] apply_addon_update raised: %s", exc)
        ok = False
    finally:
        dialog.close()

    if not ok:
        _qt_error(
            f"Failed to download extrude-ai v{latest}.\n\n"
            "Check your internet connection and relaunch FreeCAD to try again."
        )
    return ok


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_update_frame(manifest: dict, platform_tag: str) -> dict:
    """Build the frame dict expected by _updater.apply_addon_update."""
    channel = manifest.get("channels", {}).get(_CHANNEL, {})
    latest = channel.get("latest")
    if not latest:
        raise RuntimeError(
            f"Manifest has no 'latest' version in channel '{_CHANNEL}'"
        )

    version_info = manifest.get("versions", {}).get(latest)
    if not version_info:
        raise RuntimeError(f"Version '{latest}' not found in manifest")

    plat_info = version_info.get("platforms", {}).get(platform_tag)
    if not plat_info:
        raise RuntimeError(
            f"Platform '{platform_tag}' not listed for version '{latest}'"
        )

    zip_key = plat_info["zip"]
    return {
        "latest_version": latest,
        "download_url": f"{_INSTALLER_BUCKET_BASE}/{zip_key}",
        "sha256": plat_info["sha256"],
    }


# ---------------------------------------------------------------------------
# Qt helpers — imported lazily; silently degrade when Qt is unavailable
# ---------------------------------------------------------------------------


def _qt_error(message: str) -> None:
    """Show a QMessageBox error, or fall back to stderr."""
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: PLC0415

        app = QApplication.instance()
        if app:
            box = QMessageBox()
            box.setWindowTitle("extrude-ai — Installation error")
            box.setText(message)
            box.setIcon(QMessageBox.Icon.Critical)
            box.exec()
            return
    except Exception:
        pass
    try:
        from PySide2.QtWidgets import QApplication, QMessageBox  # noqa: PLC0415

        app = QApplication.instance()
        if app:
            QMessageBox.critical(None, "extrude-ai — Installation error", message)
            return
    except Exception:
        pass
    print(f"[extrude-ai firstrun] ERROR: {message}", file=sys.stderr)


class _ProgressDialog:
    """Thin wrapper around QProgressDialog; silently does nothing if Qt is absent."""

    def __init__(self, label: str) -> None:
        self._dlg = None
        self._label = label
        for _qt in ("PySide6", "PySide2"):
            try:
                QtWidgets = __import__(f"{_qt}.QtWidgets", fromlist=["QProgressDialog", "QApplication"])
                QtCore = __import__(f"{_qt}.QtCore", fromlist=["Qt"])
                app = QtWidgets.QApplication.instance()
                if not app:
                    break
                dlg = QtWidgets.QProgressDialog(label, None, 0, 100)
                dlg.setWindowTitle("extrude-ai")
                dlg.setWindowModality(QtCore.Qt.ApplicationModal)
                dlg.setMinimumDuration(0)
                dlg.setValue(0)
                self._dlg = dlg
                break
            except Exception:
                continue

    def show(self) -> None:
        if self._dlg:
            self._dlg.show()
            self._process_events()

    def set_progress(self, value: int) -> None:
        if not self._dlg:
            return
        if value < 0:
            self._dlg.setMaximum(0)  # indeterminate / busy indicator
        else:
            if self._dlg.maximum() == 0:
                self._dlg.setMaximum(100)
            self._dlg.setValue(value)
        self._process_events()

    def close(self) -> None:
        if self._dlg:
            self._dlg.close()
            self._dlg = None

    def _process_events(self) -> None:
        for _qt in ("PySide6", "PySide2"):
            try:
                QtWidgets = __import__(f"{_qt}.QtWidgets", fromlist=["QApplication"])
                app = QtWidgets.QApplication.instance()
                if app:
                    app.processEvents()
                    return
            except Exception:
                continue
