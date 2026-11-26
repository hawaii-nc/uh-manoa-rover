# uh-manoa-rover

Camera-only campus rover project. Python package `campus_rover` (src layout), site data in `sites/<name>/`, all code site-agnostic.

## Commands

- Setup: `python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev,world-data]"`
- Check: `pytest -q && ruff check .` (line length 100; both must pass before a commit)
- Route planner: `campus-rover plan --site demo_campus --route A --time "Mon 09:20"`
- 3D world: `campus-rover build-world --site uh_manoa --out out/uh_manoa --scenario class_change` (needs `fetch-layers` and `fetch-world-data` first; `demo_campus` needs neither)
- Training dataset (images with class, traversability and depth labels along the route): `campus-rover build-dataset --site uh_manoa --explorer out/explorer --out out/dataset --frames 200` (needs `pip install playwright`; the camera is `sites/uh_manoa/camera.yaml`, placeholders until the rover's numbers are in)
- Map explorer (a page to walk through and check the model): `campus-rover build-explorer --site uh_manoa --out out/explorer --scenario class_change`, then `campus-rover view --dir out/explorer`
- Unity export (labelled meshes + placements Unity dresses): `campus-rover build-unity --site uh_manoa --out out/unity_uh --scenario class_change`, then in Unity *Tools → Campus Rover → Import Export Folder…* (package in `unity/com.uhmanoa.campusrover`, see `docs/UNITY.md`)

## Map of the code

| Area | Files |
|---|---|
| Sites and config | `site.py`, `sites/<name>/site.yaml`, `layers.yaml`, `schedule.yaml` |
| Route planner | `graph.py`, `crowd.py`, `schedule.py`, `planner.py` |
| Map data in | `arcgis.py` (UH map layers), `floorplans.py` (floor counts from UH Planning PDFs), `opendata.py` (Honolulu, State, PacIOOS: data and checks), `overture.py` (walkways, footprints), `osm.py`, `lidar.py` |
| Campus model | `campus_model.py` (buildings, trees, props, closures) |
| Rover network | `crossings.py` (crossings by evidence tier), `walknet.py` (surveyed walkway network, ramps as path, stairs by treads), `traversal.py` (traversable / conditional / blocked labels, slope and cost maps), `physics.py` (collision policy, friction), `planner.py` (`blockers` diagnosis) |
| 3D world | `plans.py` (read floor-plan sheets, fit them onto the lidar roof) + `planbuild.py` (build floors from a layout), `world.py` (assemble + GLB/USD export), `ground.py` (surfaces, curbs, stairs, walls from land cover), `massing.py` (roof levels from lidar), `style.py` + `buildgen.py` (facades), `assets.py` (free models placed as shared instances), `agents.py`, `meshkit.py`, `textures/` (CC0, see `textures.json`), `viewer/` |
| Dataset | `dataset.py` (route sampling, poses, place splits, browser driver), the `window.__dataset` API in `viewer/explorer.html`, `docs/DATASET.md` |
| Explorer page | `explorer.py` (split GLBs, `data.json`: network, routes, buildings, things to check) + `viewer/explorer.html` |
| Plants and furniture | `vegetation.py` (species-shaped trees, ground cover, leaf textures drawn here), `furniture.py` (lamp, trash can read from photos); route photo notes in `docs/ROUTE_PHOTOS.md` |
| Unity | `unity_export.py` (geometry GLBs, placements, fallback models, classes, site frame) + `unity/com.uhmanoa.campusrover` (C# importer, Dress Map, randomizer); every placed thing is recorded with `Scene.record` in `world.py` / `agents.py` |
| Older flat export | `usd_export.py`, `viz.py` |

Docs: `docs/UNITY.md` (Unity workflow), `docs/ROUTE_PHOTOS.md` (what UH's photos of the route show and what the model takes from them), `docs/SCOPE.md` (whole project), `docs/CAMPUS_3D_PLAN.md` (3D plan and progress), `docs/WORLD.md` (how the world is built, known limits).

## Data sourcing rule (set by the owner, mandatory)

Before generating any mesh, texture or attribute (building colours, door positions, pathway shapes), query real data. Do not guess.

- Sources to query: Honolulu Open Geospatial Data Hub (Building Footprints (CCH), zoning), State of Hawaiʻi GIS (lidar, 100 ft contours), UH Mānoa campus map and MAGIS (names, abbreviations, layouts), PacIOOS GeoServer (regional boundaries). `opendata.py` and `fetch-world-data` do this.
- Never derive a style sheet, roof type or placement from the year built. A corridor building with no evidence is drawn as plain grey massing (`unverified_style`), with no windows or doors.
- Procedural looks (`background_style`) are only for background buildings more than `tier_b_m` (120 m) from the route.
- Every style sheet cites its source. Doors are placed only at mapped door points.
- `world.json` records each source with its retrieval date and the cross-checks; keep that true.
- A floor plan is only used to build a building when its style sheet says `corridors: open` or `corridors: enclosed`, taken from a photo. A plan does not say whether a corridor is open to the air; do not guess it. Floor plans and the layouts fitted from them stay in `cache/` (UH asks that they are not passed on); never commit them or UH's photos.

## Rules for the rover network

- Crossings carry an evidence tier (`crossings.py`): `confirmed` (you walked it: `sites/<site>/crossings.yaml`), `marked` (OpenStreetMap/Overture draws a crosswalk), `supported` (UH maps a ramp or a gap in the curb at both ends), `candidate` (the two sides only line up across the road). The planner never uses a `candidate`; `blockers` names it as "unverified crossing" and `connecting_options` lists the places to check. Zebra paint is drawn only for `marked` and `confirmed`.
- A building is reached when the rover can pull up anywhere within 4 m of its walls (`landmark_goals`), not at one node: the single nearest node can sit in a pocket cut off by stairs.
- Never turn "no evidence" into "no": a crossing's `curb_cut` is `True` only with evidence (a mapped ramp or a gap in the curb), otherwise `None` (unknown), and the planner only refuses `False`.
- When there is no passable route, say why (`blockers`) and where; do not relax a limit or add a link to make one appear. Each assumption is named in `WalkParams` or `Limits`.
- Friction values in `physics.py` are placeholders until measured with the real rover; curb height (15 cm) is an assumption because the lidar cannot resolve it.
- Close hairline gaps *before* removing blocked shapes, and erase blocked shapes from the raster: rasterising bleeds a cell, which would bridge a 0.3 m wall.

## Rules that matter

- `sites/*/cache/` and `out/` are not in git. UH map downloads and lidar stay there.
- Network code is marked `# pragma: no cover - network`; everything else is tested offline. New features need a test that runs on `demo_campus` or synthetic data.
- Building looks live in `sites/<site>/buildings/<id>.yaml`. Change the sheet, not the generator, to fix one building. Every sheet needs a `sources` entry.
- Coordinates are the site CRS in meters (UH: EPSG:32604), Z up. Exports recentre and record the offset.
- Do not use Google Maps or Street View content (official API or unofficial downloaders), or Mixamo assets (see `docs/SCOPE.md` §3a, `docs/CAMPUS_3D_PLAN.md` §6 and `docs/WORLD.md`). The owner has asked for Street View once; the answer and the permitted alternatives are in `docs/WORLD.md`.
- New 3D models built into the repo's world go in `assets.py` with source and licence; CC0, MIT or similar only.
- Unity dressing (owner decision 2026-11-26): any free model may dress a placement, including Asset Store freebies. Each one's source and licence go in the Dress Map / `dress_log.json`; licences that limit use (engine-only, no redistribution) are marked `restricted`. A model changes the look only, never a position or size from the data.
- This cloud environment cannot reach `overpass-api.de`; use `overture.py`. It has no GPU, so Isaac Sim cannot run here; previews are rendered with the bundled Three.js viewer in headless Chromium.
