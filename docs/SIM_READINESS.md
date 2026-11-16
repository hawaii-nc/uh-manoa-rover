# What the world still needs to train a rover that works on campus

**Status:** assessment of the 3D world as of 2026-11-16 · measured on the Holmes Hall → Campus Center → Architecture corridor

The 3D world looks like UH. A rover model trained in it has to *drive* on campus, so the question is different: does the world behave like the campus when a camera looks at it from 0.7 m, when wheels roll on it, and when people walk through it? This document measures that and lists what to build, in order.

## Progress (2026-11-16): items 1–4 built

| Item | State | What it found |
|---|---|---|
| 1. Walkway network from the survey | Built (`build-walknet`) | 27.9 km of centerline in 86 pieces. **Holmes Hall and Campus Center are connected; the Architecture side is a separate piece** and the two come no closer than 9.7 m. For a rover (no stairs, grade up to 8.3 %, closures respected), Holmes → Campus Center has 2 blocked stretches of 7 m each, and Campus Center → Architecture has no route at all. |
| 2. Traversability labels | Built (`build-traversal`) | 11 % of the mapped ground is traversable, 47 % conditional (lawn, road), 43 % blocked. 15 % of surveyed walkway is too steep for the 8.3 % limit. |
| 3. Terrain a wheel can feel | Partly | The lidar **cannot resolve curb height** (sidewalk reads +4.6 cm above road against a ±3.5 cm noise floor), so 15 cm stays an assumption. The survey has **no curb-cut evidence** at any crosswalk. Slab joints and cracks cannot be sourced; they remain domain-randomisation hooks. |
| 4. Collision and friction | Built (`physics.py`) | 69 % of scene triangles collide (954k of 1.39M); roofs, windows, crowns, road paint and agents no longer do. Friction per surface is a **placeholder table** to calibrate with the rover. Untested in Isaac Sim. |

Details and commands are in [WORLD.md](WORLD.md#for-the-rover-network-traversability-physics).

**Update (path work):** ramps are now path of their own, crossings carry an evidence tier, and a building counts as reached anywhere along its walls. Holmes Hall → Campus Center is a 348 m route a rover can drive; Campus Center → Architecture needs one road crossing nobody has mapped or checked (see [WORLD.md](WORLD.md#crossings-ramps-and-goals-crossingspy-walknetpy-plannerlandmark_goals)).

To check all of this against the real campus, use the explorer (`campus-rover build-explorer`, see [WORLD.md](WORLD.md#the-explorer-walk-through-it-and-check-it)): it walks the map as a person or as the rover and lists what to verify.

## 1. What the current world is, measured (before items 1–4)

| Question | Measured | What it means |
|---|---|---|
| Is there any sensor simulation (camera, depth, IMU)? | **No.** No camera model exists in `src/`; the only camera is the browser viewer. | The world cannot yet produce one training image the way the real rover's camera would. |
| Is the route the planner picks drivable for a rover? | The planned 1,202 m route lies **39 % on surveyed walkways, 46 % on road, 22 % on grass** (they overlap), and crosses 9 curbs. | The route graph comes from OpenStreetMap, which has few UH footpaths. It is not a rover's route. |
| Is the walkable surface connected? | The surveyed walkway and ramp polygons form **219 separate pieces**; the largest holds 54 % of the area. Holmes Hall and Campus Center touch it. **Architecture does not**: its nearest walkway pieces are separate from the main network. | A traversability network built from the surveys needs gap repair, and the data has real breaks to check. |
| How steep is it? | **18 %** of walkable area is steeper than the 8.3 % ADA ramp limit, 12 % steeper than 12 %. On the planned route, 9 % of samples exceed 8.3 % and the steepest is 28 %. | Slope matters on this campus. The planner's slope limit is not yet fed by the lidar. |
| Is the world physically usable? | The scene has 2.1 million triangles, all marked as exact triangle-mesh collision. | Too heavy and too jagged for a wheeled-robot physics engine. |
| Does anything move? | People, bikes and cars are placed **once, in a frozen pose**. | A perception model must learn from motion, occlusion and intent. |
| Has it been opened in Isaac Sim? | **No.** It has only been read back with OpenUSD and drawn in a browser. | The biggest single risk. |

## 2. What matters for a camera at 0.7 m, and what does not

A rover's view is filled by the ground, curbs, steps, people, bikes, poles, benches, trunks and the bottom few meters of buildings. Roofs, upper floors and the exact colour of a facade above about 4 m barely appear and carry little for navigation. So the building work from here should go into **ground-level facades**: entrances and doors, glass and reflections, arcades and overhangs, signs, steps up to doors. Upper-floor realism stops at silhouette and colour.

What closes the gap between simulation and the real rover is, in order of evidence from the sim-to-real literature: (1) wide randomization of lighting, weather, textures and camera, (2) correct geometry and labels where the wheels go, (3) a measured comparison against real footage from the same camera. Photoreal rendering of one building helps less than any of these.

## 3. What to build, in order

### A. Make the world traversable (can be done now, no GPU)

1. **A traversability network from the surveyed walkways.** Replace the OSM route graph with centerlines taken from UH's walkway and ramp polygons: widths, surface type, stair edges, curb cuts, crossings, construction closures. Repair gaps so the pieces connect, and flag the places where they really do not (Architecture). Route A and B must run on walkways, with the planner's crowd costs kept.
2. **Traversability labels.** Every pixel and every ground cell gets *traversable / conditional / blocked*: walkway and ramp yes; grass, gravel and planter conditional; curb, stairs, wall, water, planter edge blocked. Plus a slope map from the lidar and a bird's-eye cost map, exported with each image.
3. **Terrain a wheel can feel.** The ground is draped at 1–3 m and smooth; add measured slope, cracks and joints between slabs, curb cuts at crossings, and tree-root heave only where the data shows it. Curb height is assumed at 15 cm; measure it on real curbs.
4. **Simple collision.** Box and convex shapes for buildings and props, a clean ground mesh with a friction value per surface (concrete, asphalt, wet, grass, gravel). Keep the detailed mesh for rendering only.

### B. Make it a sensor, not a picture (needs an RTX GPU and Isaac Sim)

5. **The real rover's camera.** Mounting height and tilt, field of view, resolution, lens distortion, rolling shutter, exposure and white balance, noise and motion blur, all taken from the actual camera. Also GPS, IMU and wheel-odometry noise.
6. **Ground truth per frame.** RGB, depth, semantic and instance masks, surface normals, 2D and 3D boxes for people and vehicles, optical flow, traversability, and the rover pose. Split train and test **by place** (hold out whole buildings), so the test measures transfer, not memory.
7. **Randomization.** Time of day and sun angle, overcast, rain and wet ground with glare, dappled shade under the monkeypods, random tints and materials per class, camera exposure and colour, clutter, and seasonal or trimmed vegetation. Hawaiʻi's strong sun and sudden showers are the hard cases.

### C. Make it alive

8. **Animated pedestrians and vehicles.** Real walking-cycle characters (Isaac Sim's people or Microsoft Rocketbox), cyclists, skateboards, golf carts, cars, and the campus chickens, moving along the walkways according to the class-change crowd model, with occlusion and crossing paths.
9. **The long tail.** Construction closures and barriers (UH publishes them), puddles, drain grates, speed bumps, bollards, signs, fences, bike racks, dropped bags, doors that open. Rare events are where a rover fails.

### D. Learning loop

10. **A training environment** (Isaac Lab or Gymnasium): observations (image, goal direction, odometry), actions (speed, turn), rewards, a safety supervisor, randomized start and goal on the network, and benchmarks: route A, route B, route AB, quiet and class-change scenarios, with success rate, collisions, interventions and time as metrics. A classical planner as the baseline to beat.
11. **Localization the rover will have.** GPS and IMU fused with wheel odometry and visual odometry, with simulated errors, matched against the map.

### E. Prove it transfers

12. **Real data from the same camera.** Walk and drive the routes at the rover's height with GPS and IMU, label a few hundred frames, and measure sim-only against sim plus a little real. This single table (milestone M5 in `SCOPE.md`) is the real answer to "does it work".
13. **Photoreal upgrade where it counts.** Gaussian-splat reconstructions from that footage along the route replace the synthetic ground and lower facades.
14. **Field safety.** Supervised runs, a geofence and speed limit, a disengagement log, and face and licence-plate blurring in stored footage.

## 4. What I need from you

1. **The rover and its camera:** model, camera height and tilt, field of view, resolution, whether it has depth, GPS or IMU. This sets item 5.
2. **What the learned model outputs:** a steering command straight from images, or segmentation plus a planner? This decides which labels matter most.
3. **An NVIDIA RTX GPU,** or cloud GPU time. Items 5–7, 10 and 12 cannot run without one.
4. **Footage of the routes** with a phone at hip height and GPS on. It is the only thing that tells us the size of the gap.

## 5. Order of work

Items 1–4 (traversability, collision) need no GPU and fix the problems measured above; I can start them now. Items 5–10 follow once a GPU is available, and items 12–14 once there is real footage. Skipping to photoreal buildings first would be the wrong order.
