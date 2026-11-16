# The 3D Training World

`campus-rover build-world` turns a site into a labelled 3D world the rover's vision models can train in. The first one covers the UH Mānoa route corridor: **Holmes Hall → Campus Center → Architecture**, an 869 × 674 m box.

| From above | Holmes Hall and POST |
|---|---|
| ![aerial](img/world_aerial.jpg) | ![holmes](img/world_holmes.jpg) |

| Rover camera (0.7 m high) | The same view as labels |
|---|---|
| ![rover](img/world_rover.jpg) | ![labels](img/world_labels.jpg) |

The plan and the reasoning behind it are in [CAMPUS_3D_PLAN.md](CAMPUS_3D_PLAN.md).

## The sourcing rule: nothing in the corridor is guessed

Every shape, position and colour near the route comes from a named data source. Where there is no evidence, the world shows less instead of inventing something.

| Part | Source | If there is no data |
|---|---|---|
| Terrain | 2022–2023 NOAA/USGS lidar, ground points | Site is built flat and says so |
| Road / walkway / grass | Lidar brightness plus mapped roads | Left as grass |
| Building footprints | UH campus map; City and County of Honolulu footprints; OpenStreetMap | Building is not built |
| Building shape and roof type | Lidar roof heights inside each footprint | One box at the mapped height |
| Wall colour, windows, shading, ground floor | A style sheet citing UH's photo of that building | **Plain grey shape, no windows** (`unverified`) |
| Roof colour | The photo, when it shows the roof; otherwise a grey whose lightness is the measured lidar brightness of that roof | Neutral grey |
| Doors | UH's Automatic Doors layer | **No door** |
| Trees | UH tree survey: position, species, height, crown | Height from the lidar canopy |
| Props | UH campus map layers | Not placed |

Generic "background" buildings are only allowed more than 120 m from the route. The year a building was built is never used to guess its look.

**What still involves judgement.** Wall colours and window patterns are read off one photo per building by eye. Floor counts come from the photo where it shows them, and otherwise from the measured height. Prop shapes are generic and their facing is arbitrary. People and vehicles are synthetic by design.

### Checks against official data the world was not built from

| Check | Result |
|---|---|
| Lidar ground vs. Honolulu 5 ft contours (3,837 points) | Median difference 0.46 m, 90 % within 1.64 m |
| Lidar ground vs. State 100 ft contour (207 points) | Median difference 0.58 m |
| Lidar building heights vs. Honolulu footprint heights (78 buildings) | Median difference 2.2 m; 62 % within 3 m |

The contour check found that the lidar files measure height from a different reference: they sat a steady 21.8 m above both contour sets. `world.vertical_shift_m: -21.8` in `site.yaml` corrects that. The building-height gap is expected: Honolulu's figure is highest point minus lowest ground, ours is the main roof above typical ground.

## What is in it

| Part | Count (UH corridor) |
|---|---|
| Campus buildings, each with a photo-based style sheet | 95 |
| Other buildings from city and OSM footprints | 64: 13 near the route as plain grey, 51 far background |
| Open areas left to the terrain (tennis courts, amphitheatre) | 2 |
| Doors | 16, on 8 buildings |
| Trees | 2,057: 1,229 with surveyed height, 828 with lidar height |
| Benches, bins, call boxes, bike racks and more | 752 |
| People, bikes, skateboards, cars, carts, chickens, cats | 1,335 at a Monday class change |

Every mesh has a semantic label: `road`, `walkway`, `grass`, `building`, `window`, `door`, `roof`, `tree`, `person`, `bicycle`, `skateboard`, `car`, `cart`, `chicken`, `cat`, and one per prop type (`bench`, `trash_can`, `emergency_call_box`, ...).

## Build it

```bash
pip install -e ".[dev,world-data]"
```

```bash
campus-rover fetch-layers --site uh_manoa
```

```bash
campus-rover fetch-world-data --site uh_manoa
```

```bash
campus-rover build-world --site uh_manoa --out out/uh_manoa --scenario class_change
```

```bash
campus-rover view --dir out/uh_manoa
```

- `fetch-layers` downloads the UH campus map layers (buildings, trees, props, doors).
- `fetch-world-data` downloads walkways and roads, six lidar tiles (about 315 MB), and the official open data listed below. Downloads go to `sites/uh_manoa/cache/`, which is not in git.
- `build-world` takes about 30 seconds and writes:

| File | For |
|---|---|
| `world.usdc` | **Isaac Sim.** Z up, meters, labels, collision, preview materials. |
| `agents_<scenario>.usdc` | People and vehicles for one scenario, as a separate layer. |
| `world.glb`, `agents_<scenario>.glb` | The browser viewer, Blender, anything that reads glTF. |
| `world.json` | Report: each building's tier, height, floors and where its look came from; the checks above; every source with its retrieval date. |
| `index.html` | The viewer: fly around, ride the route at rover height, switch to label colours. |

The demo campus builds with no downloads: `campus-rover build-world --site demo_campus --out out/demo --scenario test`. It is an invented site with no lidar, so the ground is flat and buildings are boxes.

### In Isaac Sim

Open `world.usdc`, add `agents_class_change.usdc` as a sublayer or reference, add a Physics Scene, and drop in the rover. Site coordinates are recentred; the offset is stored in the layer metadata as `campus:originOffset`.

## Scenarios

A scenario is a small file in `sites/<site>/scenarios/`:

```yaml
name: class_change
time: "Mon 09:20"
seed: 7
```

The time drives the same crowd model the route planner uses, so walkways near lecture halls fill up at class changes. Other settings: `people_scale`, `min_people_per_100m`, `wheeled_share`, `parked_share`, `moving_car_every_m`, `carts`, `chickens`, `cats`. Change the `seed` for a different arrangement of the same crowd.

## Building style sheets

One file per building, about 20 lines, checked by code. Every sheet must cite its source.

```yaml
id: holmes_hall
floors: 5
wall: '#b9b4aa'
roof: null              # not visible in the photo: lightness is measured from lidar
windows: grid
shading: lanai          # open-air corridors
ground_floor: arcade
confidence: medium
sources: [https://map.hawaii.edu/images/MA/building_images/1163_01.jpg, ...]
```

- `windows`: `grid`, `ribbon`, `curtain`, `slit` or `none`
- `shading`: `none`, `fins`, `eyebrows` or `lanai`
- `ground_floor`: `solid`, `arcade` or `storefront`
- `solid: false` marks an open area such as courts, which is not built as a block.

**To correct a building, edit its file and rebuild.** If you know the campus, this is the fastest way to improve the world.

## What is not right yet

| Limit | Why | Fix |
|---|---|---|
| Looks like a game level, not a photo | Flat colours, no textures. The State's aerial imagery is licensed from Maxar, so it is not used. | CC0 textures, then splats from your own footage |
| People and vehicles are blocky | Procedural stand-ins | Rigged assets in Isaac Sim |
| Ground types have jagged 1 m edges; no curbs or real steps | Classified per cell from lidar brightness | Vector outlines for the route; stepped stair geometry |
| Most buildings have no door | UH maps only automatic doors: 16 in the corridor | Your photos or notes of where entrances are |
| 13 buildings near the route are plain grey | City or OSM footprints that UH's map does not name, so there is no photo | Tell me what they are; `world.json` lists them as `unverified` |
| Styles come from one photo each; 47 of them from small thumbnails | UH's campus-map photos | Your corrections; more photos |
| The route takes roads in places | OpenStreetMap has few UH footpaths | Add paths in OSM, or derive them from the paved-ground map |
| Not yet opened in Isaac Sim | No GPU in the build environment | Open `world.usdc` on an RTX machine |

## Data and credit

| Source | Used for |
|---|---|
| 2022–2023 NOAA/USGS Hawaiʻi lidar (public domain) | Terrain, roof shapes, tree heights, ground type, roof lightness |
| University of Hawaiʻi at Mānoa campus map | Building footprints, names, numbers and abbreviations; trees; props; automatic doors; building photos. UH marks its layers "for visualization purposes only"; downloads stay out of this repo. |
| City and County of Honolulu Open Geospatial Data, *Building Footprints (CCH)* | Footprints of buildings UH's map lacks; height check |
| City and County of Honolulu, *Elevation 5ft* and *Zoning* | Terrain check; zoning classes recorded in `world.json` |
| State of Hawaiʻi GIS, *Oahu Elevation Contours 100ft* | Terrain check |
| PacIOOS GeoServer: government land ownership, watersheds, streams | Regional context recorded in `world.json`. Its server only offers an older key exchange, so the cipher level is lowered for that host alone; its certificate is still checked. |
| UH Mānoa Library MAGIS | Consulted as a reference. It serves no machine-readable campus layers. |
| © OpenStreetMap contributors (ODbL), via Overture Maps | Walkways, roads, footprints the city layer lacks. `sites/uh_manoa/graph.osm.yaml` is derived from that data. |
