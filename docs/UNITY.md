# Unity: how the campus gets into Unity

The goal is an accurate campus to train the rover's driving in. Unity is where the map gets assembled and dressed with other people's free models. Where the training itself runs is not decided yet, so the export also works for other engines (the same GLBs and JSON).

## Who does what

| | Repo (`campus-rover build-unity`) | Unity (`unity/com.uhmanoa.campusrover`) |
|---|---|---|
| Ground, curbs, ramps, stairs, crossings, buildings | built from the open data, as labelled meshes | colliders, layers, friction, materials |
| Trees, plants, props, sculptures, people, cars | recorded as placements: what, where, which way, how big; plus a plain fallback model per kind | dressed with a free model chosen in the Dress Map, scaled to the recorded size |
| Labels | class and traversability for every mesh and placement (`classes.json`) | `SemanticLabel` on every object, one layer per traversability level |
| Routes and network | `network.json` (same as the explorer) | routes drawn as editor-only lines; `CampusSite.FromExport()` for training code |
| Sun and weather | site latitude/longitude, time zone | `CampusRandomizer`: real Honolulu sun, overcast, wet pavement, haze, crowd size |

When the data improves, rebuild the export and import again. Dress Map choices are kept, because they are stored per kind, not per object.

## Accuracy

What is measured and what is assumed is the same as the browser explorer, since both come from one build. See `WORLD.md`. In short:
- **Measured:** footprints, heights, terrain and tree positions/sizes, from open data.
- **Assumed:** curb height (15 cm, below lidar resolution) and friction (placeholders until measured on the rover).

A free model can make a tree or bench look right, but it never moves it.

## Licences

Any free model may be used (owner decision 2026-11-26).
- **Record every one:** fill in Source and Licence in the Dress Map, then run *Write Dress Log* and commit `dress_log.json`.
- **Mark restricted licences:** tick `restricted` for licences that limit use. For example, some Asset Store packs may only be used inside Unity.
- **Not allowed:** Google Maps / Street View content and Mixamo (`SCOPE.md` §3a).

## Coordinates

Every position in the JSON files is in the export frame: metres, x east, y up, z south, relative to the origin in `site.json` (UH: UTM 4N 622585.1 E, 2355638.1 N; 21.2985° N, 157.8182° W).

`placements.glb` also has four marker nodes (`site|origin`, `site|east10`, `site|up10`, `site|south10`). The importer reads them after import, so the frame comes out right whatever the glTF importer does with axes.
