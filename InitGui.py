"""Bootstrap entry point for the extrude-ai addon.

FreeCAD execs this file on startup (NOT imports — __file__ is undefined here).
We pick the active version under versions/<x.y.z>/, prepend it to sys.path,
and exec its real InitGui.py.  The bootstrap is intentionally tiny so we
never need to update it — only the versioned code under versions/ changes.

FreeCAD has already added the addon root (Mod/extrude-ai/) to sys.path
before this file runs, so we can `import _bootstrap` to resolve our own
location without relying on __file__.

First-run path: when no versions/ directory exists yet (e.g. fresh install
via Addon Manager), we call _firstrun.download_initial_version() to pull the
latest versioned zip from gs://extrude-ai-installer and try again.  If the
download succeeds, the addon loads normally without requiring a FreeCAD
restart.
"""

import sys
import threading
from pathlib import Path

import _bootstrap  # noqa: I001 — FreeCAD added the addon root to sys.path

_addon_root = Path(_bootstrap.__file__).absolute().parent

try:
    _version_dir = _bootstrap.select_version_dir(_addon_root)
except RuntimeError:
    # No versions/ directory — this is a fresh install.  Attempt to download
    # the initial version from the public GCS bucket.
    import FreeCAD  # type: ignore[import]
    FreeCAD.Console.PrintMessage(
        "[extrude-ai bootstrap] No addon version found — starting first-run download.\n"
    )
    try:
        import _firstrun  # noqa: PLC0415 — local module in bootstrap root
        _ok = _firstrun.download_initial_version(_addon_root)
    except Exception as _fr_exc:
        FreeCAD.Console.PrintError(
            f"[extrude-ai bootstrap] First-run download failed: {_fr_exc}\n"
            "Check your internet connection and restart FreeCAD.\n"
        )
        raise RuntimeError(
            f"First-run download failed: {_fr_exc}"
        ) from _fr_exc

    if not _ok:
        FreeCAD.Console.PrintError(
            "[extrude-ai bootstrap] First-run download returned failure.\n"
            "Check your internet connection and restart FreeCAD.\n"
        )
        raise RuntimeError("First-run download failed.")

    # Retry version selection after successful download.
    try:
        _version_dir = _bootstrap.select_version_dir(_addon_root)
    except Exception as _retry_exc:
        FreeCAD.Console.PrintError(
            f"[extrude-ai bootstrap] Version selection failed after download: {_retry_exc}\n"
        )
        raise
except Exception as exc:  # pragma: no cover — unexpected failure mode
    import FreeCAD  # type: ignore[import]
    FreeCAD.Console.PrintError(
        f"[extrude-ai bootstrap] Cannot select addon version: {exc}\n"
        "Reinstall the addon to recover.\n"
    )
    raise

_version_dir_str = str(_version_dir)
if _version_dir_str not in sys.path:
    sys.path.insert(0, _version_dir_str)

import FreeCAD  # type: ignore[import]
FreeCAD.Console.PrintMessage(
    f"[extrude-ai bootstrap] Loading addon from versions/{_version_dir.name}\n"
)

def _bootstrap_startup_update_check(
    _root=_addon_root,  # captured at def-time; safe under FreeCAD exec() globals
) -> None:
    try:
        import _firstrun  # noqa: PLC0415 — bootstrap root module

        import _updater  # noqa: PLC0415 — bootstrap-local module
        if _updater._is_dev_mode(_root / "versions"):
            FreeCAD.Console.PrintMessage(
                "[extrude-ai bootstrap] Dev mode — skipping startup update check.\n"
            )
            return

        staged = _firstrun.check_for_updates(_root)
        if staged:
            FreeCAD.Console.PrintMessage(
                f"[extrude-ai bootstrap] Addon v{staged} staged. "
                "Restart FreeCAD to activate.\n"
            )
        else:
            FreeCAD.Console.PrintMessage(
                "[extrude-ai bootstrap] Addon is up to date.\n"
            )
    except Exception as exc:
        FreeCAD.Console.PrintMessage(
            f"[extrude-ai bootstrap] Startup update check failed: {exc}\n"
        )


threading.Thread(
    target=_bootstrap_startup_update_check,
    daemon=True,
    name="extrude-ai-bootstrap-update",
).start()

with open(_version_dir / "InitGui.py", "rb") as _f:
    exec(compile(_f.read(), str(_version_dir / "InitGui.py"), "exec"))
