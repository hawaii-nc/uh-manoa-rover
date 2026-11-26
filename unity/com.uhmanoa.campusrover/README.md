# UH Manoa Campus Rover (Unity package)

This package builds the campus scene from `campus-rover build-unity`. The repo stays the source of truth: ground, curbs, ramps, stairs, crossings and buildings come from open data. Unity adds the look: free models, materials, light and weather.

## Install (once per project)

1. In the Package Manager, install **glTFast** (`com.unity.cloud.gltfast`, Unity's glTF importer) and **Newtonsoft Json** (`com.unity.nuget.newtonsoft-json`). The package lists both, so this happens on its own if the registry allows it.
2. Add this package. Either use *Package Manager → + → Add package from disk…* and pick `unity/com.uhmanoa.campusrover/package.json` in your clone of the repo, or add it to `Packages/manifest.json`:
   ```json
   "com.uhmanoa.campusrover": "file:C:/path/to/uh-manoa-rover/unity/com.uhmanoa.campusrover"
   ```
3. Use URP. The randomizer sets URP Lit's `_BaseColor` / `_Smoothness` for wet pavement.

## Import the campus

1. In the repo, write the export:
   ```bash
   campus-rover build-unity --site uh_manoa --out out/unity_uh --scenario class_change
   ```
2. In Unity, open **Tools → Campus Rover → Import Export Folder…** and pick `out/unity_uh`. The folder is copied into `Assets/CampusExport/`. Then:
   - **Geometry:** each `group|class|material` node gets a `SemanticLabel` (class, id, traversability), a layer, a collider and a friction material:

     | Layer | Classes |
     |---|---|
     | Traversable | walkway, ramp |
     | Conditional | road, grass, crosswalk paint, … |
     | Blocked | curbs, stairs, walls, buildings, … |
     | Dynamic | people, bikes, cars, animals |

     Friction comes from `physics.py` and is a placeholder until measured with the rover.
   - **Placements:** every tree, plant, prop, sculpture and person stands where the data put it, with its heading and size. Each uses the model from the **Dress Map** (`Assets/CampusExport/DressMap.asset`), or the export's plain fallback model if none is set.
   - **North:** the campus is turned so north is +Z and east is +X.
   - **Routes:** routes are drawn as editor-only lines (orange = passable, red = blocked).

## Dress it with free models

1. Open `DressMap.asset`. Each entry is one kind (`tree:umbrella`, `prop:bench`, `agent:person`, …), sorted by how many there are.
2. Drag one or more models into **Models**. Each placement then gets one of them at random.
3. Set **Fit**:
   - `MatchHeight` (default): uniform scale to the placement's height.
   - `MatchFootprint`: trees, to crown width and height.
   - `AsAuthored`: no scaling.
4. Use **Yaw Offset** if the model faces another way.
5. **Fill in Source and Licence.** Tick **Restricted** if the licence limits use, for example Asset Store "Unity only" or no redistribution.
6. Run **Tools → Campus Rover → Re-dress Placements**. Positions and sizes never change, only the look.
7. Run **Tools → Campus Rover → Write Dress Log**. It saves the choices to `Assets/CampusExport/dress_log.json`; copy it back to the repo so the model list and licences are on record.

Models from Google Maps / Street View and Mixamo are not allowed (repo `docs/SCOPE.md` §3a).

## Ground materials

Use `Assets/CampusExport/MaterialMap.asset`. It has one entry per class in the meshes (walkway, road, grass, curb, building, …), most-used first.

1. Set a **Material**, for example a URP Lit material made from a CC0 Poly Haven / ambientCG texture set.
2. Set **Texture Size M**: the real metres one repeat of the texture covers.
3. Fill in **Source** and **Licence**.
4. Run **Re-dress Placements**. It re-imports and applies the materials. The importer scales each one from the export's `node_uv.json`, so a 3 m paving texture stays 3 m.

The dress log lists these materials too.

## Training looks

Add `CampusRandomizer` to an object, set **Sun** to your directional light, and call `Randomize()` each episode. It varies:
- Honolulu sun for a random date and hour (real solar position)
- Overcast
- Wet pavement on hard surfaces
- Haze
- The share of people present

Geometry never changes.

## Files in the export

See the module docstring of `src/campus_rover/unity_export.py`. All JSON positions are in the export frame (metres, x east, y up, z south, from the site origin). `CampusSite.FromExport()` and `FromSite()` convert them to scene positions.
