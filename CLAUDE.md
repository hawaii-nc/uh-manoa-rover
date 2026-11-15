# uh-manoa-rover

Camera-only campus rover project. Python package `campus_rover` (src layout), site data in `sites/<name>/`, all code site-agnostic.

## Commands

- Setup: `python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev,world-data]"`
- Check: `pytest -q && ruff check .` (line length 100; both must pass before a commit)
- Route planner: `campus-rover plan --site demo_campus --route A --time "Mon 09:20"`
- 3D world: `campus-rover build-world --site uh_manoa --out out/uh_manoa --scenario class_change` (needs `fetch-layers` and `fetch-world-data` first; `demo_campus` needs neither)

## Map of the code

| Area | Files |
|---|---|
| Sites and config | `site.py`, `sites/<name>/site.yaml`, `layers.yaml`, `schedule.yaml` |
| Route planner | `graph.py`, `crowd.py`, `schedule.py`, `planner.py` |
| Map data in | `arcgis.py` (UH map layers), `overture.py` (walkways, footprints), `osm.py`, `lidar.py` |
| Campus model | `campus_model.py` (buildings, trees, props, closures) |
| 3D world | `world.py` (assemble + export), `massing.py` (roof levels from lidar), `style.py` + `buildgen.py` (facades), `agents.py`, `meshkit.py`, `viewer/` |
| Older flat export | `usd_export.py`, `viz.py` |

Docs: `docs/SCOPE.md` (whole project), `docs/CAMPUS_3D_PLAN.md` (3D plan and progress), `docs/WORLD.md` (how the world is built, known limits).

## Rules that matter

- `sites/*/cache/` and `out/` are not in git. UH map downloads and lidar stay there.
- Network code is marked `# pragma: no cover - network`; everything else is tested offline. New features need a test that runs on `demo_campus` or synthetic data.
- Building looks live in `sites/<site>/buildings/<id>.yaml`. Change the sheet, not the generator, to fix one building. Every sheet needs a `sources` entry.
- Coordinates are the site CRS in meters (UH: EPSG:32604), Z up. Exports recentre and record the offset.
- Do not use Google Maps or Street View content, or Mixamo assets (see `docs/SCOPE.md` §3a and `docs/CAMPUS_3D_PLAN.md` §6).
- This cloud environment cannot reach `overpass-api.de`; use `overture.py`. It has no GPU, so Isaac Sim cannot run here; previews are rendered with the bundled Three.js viewer in headless Chromium.
