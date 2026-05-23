# extrude-ai — FreeCAD addon bootstrap

This repository hosts the bootstrap files for the **[extrude-ai](https://www.extrudeai.com)** FreeCAD addon. It is intended to be installed via FreeCAD's built-in **Addon Manager**.

## Install

1. Open FreeCAD (1.0 or later).
2. **Edit -> Preferences -> Addon Manager**.
3. Under **Custom Repositories**, click **+** and paste:

   ```
   https://github.com/extrudeai/extrudeai-freecad
   ```

   Branch: `main`

4. Click **OK** to close Preferences.
5. **Tools -> Addon Manager**, find **extrude-ai** in the list, click **Install**.
6. Restart FreeCAD.

On first launch, the addon downloads the latest versioned binary (~50 MB) automatically from the public installer bucket. An internet connection is required for this one-time step.

## What this repo contains

Only the ~56 KB bootstrap glue that FreeCAD's Addon Manager needs:

- `InitGui.py`         — bootstrap entry point that FreeCAD execs at startup
- `_bootstrap.py`      — version selector (picks the active `versions/<x.y.z>/`)
- `_firstrun.py`       — downloads the initial version on fresh installs
- `_updater.py`        — in-process auto-update for subsequent versions
- `package.xml`        — FreeCAD Addon Manager manifest
- `icon.svg`           — Addon Manager listing icon

The proprietary CAD logic (`adapters/`, `ui/`, `ws_client.py`, etc.) is **not** in this repo. It is downloaded by `_firstrun.py` from the public installer bucket on first FreeCAD launch.

## License

- The bootstrap glue files in this repository: **MIT License** — see [`LICENSE`](LICENSE).
- The extrude-ai addon as a whole (including the downloaded mixin code): **Proprietary** — see [extrudeai.com](https://www.extrudeai.com).

## Bug reports

Please file bugs at https://github.com/extrudeai/extrudeai-freecad/issues.

## Source

This repository is mirrored from the private development repo. Do not send pull requests here; contact dev@extrude-ai.com instead.
