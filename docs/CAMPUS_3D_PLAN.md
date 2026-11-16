# Plan: A Realistic 3D UH Mānoa Campus for Training the Rover

**Status:** P0–P3 and a first pass of P5 are built for the route corridor (see [WORLD.md](WORLD.md)) · **Date:** 2026-11-15

This plan covers one feature: turning the UH Mānoa campus into a 3D world, with buildings, ground, trees, people, bikes and cars, that the rover's computer-vision models can train on. It builds on what the repo already has (`campus_model.py`, `usd_export.py`, the Tier 1 plan in [SCOPE.md §3a](SCOPE.md)) and replaces the "hand-model it in Blender" steps with something an AI agent can do cheaply and correctly.

---

## 1. The short answer

**Do not have the AI write each building's 3D model by hand.** That is how the one-prompt demos work (a temple, a pagoda garden): one hero object, written as Three.js code, with nothing real to match. A campus is different. It has 150 to 160+ buildings on 320 acres, and each one has to sit at the right place, at the right size, on real slopes.

The better split is:

| Job | Who does it | Why |
|---|---|---|
| **Shape**: where things are, how big, how tall, how steep | **Open data** (footprints, lidar) | Measured, free, and costs no AI tokens |
| **Style**: what each building looks like | **The AI**, as a short style sheet per building | This is the part that needs "search it up and look at it" |
| **Mesh**: the actual 3D geometry | **Code** (a procedural generator, written once) | Consistent, testable, and re-runnable for free |

So the AI's output per building is about 25 lines of settings, not thousands of lines of geometry. Recent research on AI-built cities uses the same split: the model writes a compact "building program" and a procedural engine builds the geometry (CityGenAgent, 2026; I could only read its summary, not the full paper).

### What you will get, honestly

- **You will get** a campus that is accurate in layout, size and slope, with recognisable buildings, real tree positions, and moving people, bikes and cars. Think "good video-game level of UH", with perfect labels for every pixel.
- **You will not get** a photo-real copy from this step alone. No approach reaches photo-real without real footage of the campus. That upgrade (Gaussian splats from phone video) is already planned in SCOPE.md and drops onto this same world later.
- **That is enough for** route planning, navigation-policy training, and a first perception model trained with heavy randomisation. The real test is still milestone M5: measure the model on real frames.

---

## 2. Approaches compared

| Approach | Accuracy | Realism | AI token cost | Needs | Verdict |
|---|---|---|---|---|---|
| **A. AI hand-codes every building** (the temple-demo style) | Low: sizes are guessed | Pretty, but inconsistent | Very high, repeated 150+ times | Nothing | Only for up to ~5 one-off landmark features |
| **B. Open data + AI style sheets + procedural generator** | High | Stylised-realistic | Low | Network access to data hosts | **Recommended** |
| **C. Image-to-3D AI models** (e.g. TRELLIS.2, MIT-listed) | Low: scale and shape drift | Good for single objects | Low | A GPU and licensed photos | Later, for props and sculptures only |
| **D. Gaussian splats from your own video** | High | Photo-real | Low | Footage of campus, an NVIDIA GPU | The photo-real upgrade, already in SCOPE.md |
| **E. Google 3D tiles / Street View** | n/a | n/a | n/a | n/a | Not allowed for this use, and poor at rover height (SCOPE.md §3a) |

---

## 3. Where the detail should go

The rover's cameras sit about 0.5 to 1 m off the ground. What fills its view is, in order:

1. **The ground**: walkway surface, curbs, grass edges, stairs, ramps, drains, painted lines.
2. **Things at its own height**: people's legs, bike wheels, benches, bollards, bins, tree trunks, low lava-rock walls.
3. **The bottom 4 m of buildings**: doors, ground-floor windows, columns, covered walkways, signs.
4. **Everything above that**: upper floors and roofs, mostly as background.

So the modelling budget follows the same order. Upper floors only need the right height, colour and window rhythm. This matters more than any single building being perfect.

**Three tiers of buildings**, by distance from Routes A and B (Holmes Hall → Campus Center → Architecture):

| Tier | Which buildings | Rough count | How the style sheet is made |
|---|---|---|---|
| **A: hero** | On or facing the routes | ~15 | AI researches each one: a few searches plus reference photos |
| **B: visible** | Seen from the routes | ~40 | AI does one quick lookup each |
| **C: background** | The rest of campus | ~100 | Automatic defaults from data. No AI research. |

Counts are estimates until the real footprints are loaded.

---

## 4. Data sources (checked 2026-11-15)

| Need | Source | Licence | Reachable from this cloud session? |
|---|---|---|---|
| Terrain, building heights, roof shapes, tree positions | **2022–2023 NOAA/USGS Hawaiʻi lidar** (Oʻahu flown Aug 2022 to Jul 2023, designed to the high-density QL1 standard, classified LAZ) | Public domain | ✅ Yes (NOAA's S3 bucket answers) |
| Building footprints, some heights and floor counts | **Overture Maps** (includes OpenStreetMap buildings) | Open (ODbL / CDLA) | ✅ Yes (latest release 2026-10-21) |
| Walkways, stairs, crossings | **OpenStreetMap** via Overpass (what `import-osm` uses) | ODbL | ❌ `overpass-api.de` is blocked. Overture's transportation data may cover this instead; to verify. |
| Building names, entrances, trees and species, benches, call boxes, construction | **UH campus map** layers (already mapped in `sites/uh_manoa/layers.yaml`) | UH, "visualisation only" | ❌ `map.hawaii.edu` is blocked |
| Building descriptions: year, floors, materials, style | UH building directory, **UH Mānoa Campus Heritage Report**, SAH Archipedia, Wikipedia | Facts are free to use | Text search works. Opening pages directly is blocked. |
| Reference photos | Wikimedia Commons, Mapillary (CC-licensed) | CC BY / BY-SA | ❌ Blocked |
| Ground and wall textures | Poly Haven (CC0) | CC0 | ❌ Blocked |
| Older height cross-check | State of Hawaiʻi "Building Rooftops with Height" (2013) | State open data | ❌ `geodata.hawaii.gov` is blocked |

**Hosts to allow before building:** `overpass-api.de`, `map.hawaii.edu`, `manoa.hawaii.edu`, `en.wikipedia.org`, `commons.wikimedia.org`, `upload.wikimedia.org`, `graph.mapillary.com`, `polyhaven.com`, `geodata.hawaii.gov`. The UH map's layer server will be one more host, known once the map page can be read.

**Best source of all: you.** You know the campus. A dozen phone photos of Holmes Hall, Campus Center and the Architecture building, and your corrections on a preview ("Holmes is more beige and has outside stairs"), are worth more than any amount of web research and cost almost no tokens.

---

## 5. How it works

```
 footprints (Overture/OSM) ─┐
 lidar (NOAA 2022–23) ──────┼─▶ SHAPE: terrain mesh, building height + roof type, tree positions
 UH map layers ─────────────┘            │
                                         ▼
 web research + photos ─▶ STYLE: buildings/<id>.yaml ─▶ PROCEDURAL GENERATOR (code, written once)
 (AI, Tier A and B only)   (~25 settings each)                    │
                                                                  ▼
                              static world: world.usda (Isaac Sim) + world.glb (browser preview)
                                                                  │
 crowd model (already built) ─▶ scenario.yaml ─▶ people, bikes, carts, cars, chickens, cats
                                                                  ▼
                                    Isaac Sim Replicator: images + depth + labels for training
```

### The style sheet (what the AI fills in)

One small file per building. Every field is a fixed choice or a number, so code can check it.

```yaml
id: holmes_hall
tier: A
floors: 5                      # cross-checked against lidar height
era: 1970s
wall: {material: concrete, color: "#cfc6b4"}
windows: {pattern: ribbon, ratio: 0.35, shading: fins}
roof: flat_parapet
ground_floor: {type: open_breezeway, columns: true}
entrances: [{side: south, kind: double_door, covered: true}]
features: [exterior_stair_east, rooftop_equipment]
sign_text: "Holmes Hall"
sources: ["<url>", "<url>"]
confidence: medium             # low = guessed, shown in review
```

### The generator (code)

Takes a footprint, a height and a style sheet, and builds walls, window grids, doors, columns, covered walkways and roofs. Each part gets its own label (`wall`, `window`, `door`, `roof`), so segmentation ground truth is free. It is developed and tested on `demo_campus`, so UH is "just data", the same rule the rest of the repo follows.

### Two outputs

- **`world.usda`** for Isaac Sim, extending the existing `usd_export.py` (labels, collision, recentred coordinates).
- **`world.glb` plus a small Three.js viewer** you can open in any browser. This is the fly-around view like the demo that inspired you, and it is also how previews get checked in this cloud session, which has no GPU and cannot run Isaac Sim.

---

## 6. People, bikes, cars and animals

Keep these in a **separate layer** from the static campus, so one world can run many scenarios (quiet, class change, lunch, rain).

| Agent | Recommended source | Licence | Note |
|---|---|---|---|
| People | **Microsoft Rocketbox** (115 rigged avatars) and Isaac Sim's bundled people | MIT / NVIDIA | Good variety of ages and clothing |
| Bikes, skateboards, mopeds, carts, cars | CC0 packs (Kenney, Quaternius), Isaac Sim / Omniverse asset packs, CC-BY models for specific ones (golf cart, moped) | CC0 / CC-BY | Start low-poly, upgrade the common ones |
| Chickens, cats | Quaternius animal packs | CC0 | Small, low, and easy to miss: a real hazard class |

- **Avoid Mixamo.** Adobe's Mixamo terms forbid using its characters and animations to train machine-learning models.
- **Avoid SMPL-based bodies** unless the project stays non-commercial.
- Every asset gets a line in an `assets/manifest.yaml` with its source and licence.
- **Spawning** comes from the crowd model the repo already has: people per walkway per time of day becomes spawn rates.
- **Isaac Sim version matters.** Its people system changed: `omni.anim.people` was deprecated in 5.0 and replaced in 6.0. So scenarios are written in our own neutral `scenario.yaml`, with a thin adapter for whichever Isaac Sim version you install.

---

## 7. Keeping token use low

1. **The AI never writes vertices.** It writes settings. About 25 lines per building, not a mesh.
2. **Tiers.** Only about 55 of 150+ buildings get any AI research, and only about 15 get deep research.
3. **Research once, save forever.** Each style sheet is saved with its sources. A `manifest.yaml` tracks each building's status, so work resumes without redoing anything.
4. **Big data stays out of the conversation.** Lidar and map files are processed by scripts that print a short summary.
5. **Check with numbers before pictures.** Tests compare floor count against lidar height, footprint area, and label coverage. Images are only looked at as one small contact sheet per batch, not one image per building.
6. **Batch the research.** Buildings are researched in groups, and only the finished style sheets come back. If you allow it, a smaller, cheaper model can do the Tier B lookups.
7. **Add a `CLAUDE.md` repo map.** This session spent a good share of its effort rediscovering the repo. A one-page map removes that cost from every future session.
8. **One milestone per session.** The plan and the manifest carry the state between sessions.
9. **Your corrections beat more searching.** A one-line fix from you replaces several searches.

---

## 8. Build order

Each step ends in something you can look at.

| # | Step | Done when | AI effort | Blocked by |
|---|---|---|---|---|
| **P0** | Setup: `CLAUDE.md`, style-sheet schema, manifest | Schema validates the example above | Small | Nothing |
| **P1** | Shape: footprints + lidar → terrain, heights, roof types, trees. Gray-box campus. | You can fly around a gray UH in the browser; Holmes Hall's height matches lidar within about 1 m | Medium (code) | Nothing for Overture + lidar; walkways need network access |
| **P2** | Procedural building generator, on `demo_campus` | About 6 building types render with labelled parts; tests pass | Medium (code) | Nothing |
| **P3** | Style sheets: Tier A, then Tier B | You review a contact sheet of the hero buildings and correct them | Medium (research) | Network access helps a lot |
| **P4** | Ground detail on Routes A and B: surfaces, curbs, real stairs, ramps, walls, signs, benches, tree types by species | A rover-height walk-through of Route A looks right to you | Medium | UH map layers |
| **P5** | Agents: asset library with licences, `scenario.yaml`, spawning from the crowd model | The same scenario runs on `demo_campus` and `uh_manoa` | Medium | Nothing for the config; animation needs Isaac Sim |
| **P6** | Training data: Isaac Sim Replicator export with randomised light, weather, materials and camera | A labelled dataset of Route A, and a first model scored on real frames (M5) | Small (scripts) | **An NVIDIA RTX GPU on your side** |
| **P7** | Photo-real upgrade with splats | Later, when footage exists | n/a | Footage |

### Progress (2026-11-15)

| Step | State |
|---|---|
| P0 Setup | Done: style-sheet schema, `CLAUDE.md` |
| P1 Shape | Done for the corridor: lidar terrain, roof-level massing, trees, walkway graph |
| P2 Generator | Done: windows, sun shading, ground floors, doors, flat and pitched roofs |
| P3 Style sheets | Done for all 95 campus buildings in the corridor, each from UH's photo. No look is guessed from the year built; buildings with no evidence stay plain grey. Needs your review. |
| P4 Ground detail | Started: road / walkway / grass from lidar. No curbs, steps or textures yet. |
| P5 Agents | First pass: procedural stand-ins placed by the crowd model, two scenarios. Rigged assets still to do. |
| P6 Training data | Not started. Needs Isaac Sim on an RTX GPU. |

**Sourcing rule (2026-11-16).** Nothing in the corridor is guessed; see [WORLD.md](WORLD.md). Official Honolulu, State and PacIOOS data are now queried, and they showed the lidar heights were 21.8 m off the official height reference, which is corrected.

Two things changed from the plan. OpenStreetMap's Overpass server refused this session, so walkways come from Overture Maps, which republishes the same data. And the lidar turned out to label only ground, so roofs and trees are told apart by how many echoes each laser pulse returned.

---

## 9. Decisions needed from you

1. **Network access.** May the session reach the hosts in §4? Without them, P1 and P2 still work, but walkways, UH layers and photos wait.
2. **First pass: Routes A and B, or the whole campus?** Recommended: the route corridor in detail, the rest as gray boxes.
3. **GPU.** Do you have an NVIDIA RTX GPU for Isaac Sim (P6)? This is still open from SCOPE.md §9.
4. **Are you on campus now?** If yes, a dozen photos of the three route buildings would improve P3 more than anything else.

---

## Sources

- [2022–2023 NOAA USGS Lidar: Hawaii (incl. Oʻahu)](https://www.fisheries.noaa.gov/inport/item/75941)
- [USGS 3DEP lidar on AWS](https://registry.opendata.aws/usgs-lidar/)
- [State of Hawaiʻi building rooftops with height](https://geodata.hawaii.gov/arcgis/rest/services/Climate/MapServer/39/iteminfo)
- [OSM wiki: Honolulu building import](https://wiki.openstreetmap.org/wiki/Honolulu,_Hawaii_Building_Import)
- [Google Map Tiles API policies](https://developers.google.com/maps/documentation/tile/policies)
- [UH Mānoa Campus Environments](https://manoa.hawaii.edu/campus-environments) · [Campus Heritage Report](https://historichawaii.org/2021/12/16/university-of-hawaii-at-manoa-campus-heritage-report)
- [CityGenAgent: procedural 3D city generation (arXiv 2602.05362)](https://arxiv.org/abs/2602.05362)
- [TRELLIS.2](https://microsoft.github.io/TRELLIS.2)
- [Microsoft Rocketbox avatars](https://github.com/microsoft/Microsoft-Rocketbox) · [paper](https://www.frontiersin.org/journals/virtual-reality/articles/10.3389/frvir.2020.561558/pdf)
- [Mixamo additional terms (AI/ML restriction)](https://wwwimages2.adobe.com/content/dam/cc/en/legal/servicetou/Mixamo-Addl-Terms-en_US-20210623.pdf) · [Mixamo licensing FAQ](https://community.adobe.com/questions-696/mixamo-faq-licensing-royalties-ownership-eula-and-tos-589400)
- [Isaac Sim people simulation (5.1)](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/action_and_event_data_generation/ext_replicator-agent/ext_omni_anim_people.html) · [Isaac Sim 6.0 migration guide](https://docs.isaacsim.omniverse.nvidia.com/latest/migration_guides/isaac_sim_6_0/ext_isaacsim_replicator_agent_migration_guide.html)
