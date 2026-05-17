"""Version selector for the extrude-ai addon bootstrap.

The bootstrap layer ships once and never auto-updates.  The real addon code
lives in sibling subdirectories ``versions/<x.y.z>/`` and is silently
auto-downloaded by ``_updater.py``.  ``versions/current.txt`` (one line, the
version identifier) tells this selector which directory to load.

We intentionally use the simplest possible version-selection logic:
  - if current.txt names a directory, load that.
  - otherwise pick the highest-sortable semver directory that exists.
  - otherwise raise — there's nothing to load.

The dev workflow uses ``current.txt = "dev"`` with ``versions/dev``
symlinked to the workspace, so editing the repo updates the running addon.
"""

from __future__ import annotations

from pathlib import Path

# packaging.version isn't shipped by FreeCAD's bundled Python, so we keep this
# fallback simple instead of depending on it.  Anything non-numeric (like the
# string "dev") is filtered out of the highest-version search.


def _parse_semver(s: str) -> tuple[int, ...] | None:
    parts = s.split(".")
    try:
        return tuple(int(p) for p in parts if p)
    except ValueError:
        return None


def select_version_dir(addon_root: Path) -> Path:
    """Return the path of the active addon version directory."""
    versions_dir = addon_root / "versions"
    current_file = versions_dir / "current.txt"
    if current_file.exists():
        target_name = current_file.read_text(encoding="utf-8").strip()
        target = versions_dir / target_name
        if target.is_dir():
            return target

    candidates = []
    if versions_dir.is_dir():
        for child in versions_dir.iterdir():
            if not child.is_dir():
                continue
            v = _parse_semver(child.name)
            if v is not None:
                candidates.append((v, child))
    if not candidates:
        raise RuntimeError(
            f"No addon version directories found under {versions_dir!s}"
        )
    candidates.sort(key=lambda pair: pair[0])
    return candidates[-1][1]


def read_active_version(addon_root: Path) -> str:
    """Return the version string the addon should advertise on the WS handshake.

    Falls back to "0.0.0" if nothing useful is on disk; the backend treats
    that as "force-update if any version is published".
    """
    versions_dir = addon_root / "versions"
    current_file = versions_dir / "current.txt"
    if current_file.exists():
        v = current_file.read_text(encoding="utf-8").strip()
        if v:
            return v
    return "0.0.0"


def write_active_version(addon_root: Path, version: str) -> None:
    """Atomically point current.txt at ``version``."""
    versions_dir = addon_root / "versions"
    versions_dir.mkdir(parents=True, exist_ok=True)
    target = versions_dir / "current.txt"
    tmp = target.with_suffix(".txt.tmp")
    tmp.write_text(version, encoding="utf-8")
    tmp.replace(target)
