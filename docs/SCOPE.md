# Project Scope: UH Mānoa Campus Navigation Rover

**Status:** Draft v0.1 · **Owner:** Nathan Chong · **Last updated:** 2026-10-04

---

## 1. Goal

Build a ground rover that drives on campus walkways and uses **only cameras** for perception, like Tesla Vision. It should get from one side of the UH Mānoa campus to the other on its own, and it should:

- **Perceive** its surroundings in 360° from several cameras: walkways, curbs, stairs, ramps, people, bikes, carts, cars, and animals.
- **Predict** what moving agents will do next: where a pedestrian is heading, or whether a cart is about to cross.
- **Plan routes by time of day.** It should pick a slightly longer path that is less crowded and easier to read over a short path through a crowd, such as McCarthy Mall at a class change.
- **Act safely.** It should yield, slow down, stop, and reroute, and never pick a route it can't physically traverse (stairs, steep grass, high curbs).

All learning happens first in a **photorealistic digital twin of the campus in NVIDIA Isaac Sim**. The real rover comes after the sim policy is proven.

### Non-goals for v1

- Driving on roads with car traffic. The rover is a **sidewalk/walkway robot**. It only crosses roads at marked crosswalks, and road crossing is a late, separately gated milestone.
- Night operation. Daylight first, dusk later.
- Indoor navigation and elevators.
- Delivering anything. Navigation is the product for now.

---

## 2. System Overview

```
 ┌──────────────── DATA ────────────────┐   ┌────────────── SIMULATION ──────────────┐
 │ Own capture (phone/360 cam/drone*)   │   │ Isaac Sim + Isaac Lab                  │
 │ Reference: Google 3D Tiles, OSM,     │──▶│  • Static twin: Gaussian splats (look) │
 │   UH maps, GIS, class schedules      │   │    + mesh (collision, semantics)       │
 │ Transient removal (people/cars/etc.) │   │  • Dynamic agents: people, bikes,      │
 │ Georeferenced 3DGS + mesh per zone   │   │    carts, cars, chickens, cats         │
 └──────────────────────────────────────┘   │  • Time-of-day crowd scheduler         │
                                            │  • Weather/lighting randomization      │
                                            │  • Rover model w/ N cameras            │
                                            └───────────────────┬────────────────────┘
                                                                │ synthetic data + RL
 ┌──────────────────────────── AUTONOMY STACK (on rover) ────────▼───────────────────┐
 │ Cameras ─▶ Perception (BEV occupancy, drivable surface, agents, semantics)        │
 │         ─▶ Prediction (agent trajectories, crowd density)                         │
 │         ─▶ Localization (visual + map; GNSS/IMU/wheel odom as aids — see §9)      │
 │         ─▶ Route planner (time-dependent graph: distance + crowd + difficulty)    │
 │         ─▶ Local planner / learned policy (RL in Isaac Lab) ─▶ Safety supervisor  │
 │         ─▶ Motor control                                                          │
 └───────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Workstreams and Phases

Each phase has an **exit criterion**. Don't start the next phase until it's met. Phases 1–3 can overlap partly once Phase 0 is done.

### Phase 0: Foundations (2–4 weeks)

| Item | Detail |
|---|---|
| Permissions | Get written OK from UH (Facilities / Campus Security / your department) for photo/video capture, any drone flights, and eventually rover testing. Ask whether UH IRB or a privacy review applies to recording people. |
| Pick the MVP route | One short, well-defined corridor, for example **Hamilton Library ↔ Campus Center along McCarthy Mall**. It is busy at class changes, has an obvious quieter alternative, and has mixed surfaces. Expand later. |
| Compute | Isaac Sim needs an NVIDIA RTX GPU (16 GB+ VRAM recommended; 24 GB+ is comfortable for splats plus many agents). Splat training also wants 24 GB. Budget for a local RTX workstation or cloud GPUs. |
| Rover platform decision | Buy or build: an off-the-shelf base (e.g., AgileX Scout Mini, Clearpath Jackal) or a custom 4-wheel skid-steer. On-board compute: NVIDIA Jetson Orin. It needs to handle campus slopes and wet surfaces. |
| Camera rig spec | Fix this early, because sim and real must match exactly: number of cameras, FOV, resolution, mounting height and angles. Starting point: **6–8 cameras** (front wide + front narrow, 2 sides, 2 rear-quarter, rear), global-shutter or low-rolling-shutter, HDR for harsh Hawaiʻi sun and shadow. |
| Repo/tooling | Repo layout (§8), experiment tracking (W&B or MLflow), data versioning (DVC or similar), storage plan (raw capture is **TBs**). |

**Exit:** permissions in hand, MVP route chosen, camera rig and rover spec frozen at v1, GPU available.

---

### Phase 1: Data Collection (4–8 weeks for MVP zone; ongoing for full campus)

**Sources and how each is used:**

| Source | Use | Notes / constraints |
|---|---|---|
| **Your own capture** (phone, 360° camera such as Insta360, mirrorless camera, optional drone) | **Primary source for the reconstruction** | Best quality, and you own the data. Capture at ground level along every walkway, plus overlapping passes at several heights. |
| **Google Photorealistic 3D Tiles / Maps / Street View** | **Reference only**: layout, georeferencing sanity checks, planning capture routes | ⚠️ Google Maps Platform terms restrict caching, extracting, or creating derivative datasets from their content, and restrict using it for ML training. Don't build the training twin from scraped Google imagery. Read the current Terms of Service. If you want to use it, ask Google or use their sanctioned APIs within terms. |
| **OpenStreetMap** | Walkway graph, building footprints, crosswalks, stairs tags | ODbL license. Good starting point for the route graph. |
| **Hawaiʻi Statewide GIS / USGS lidar** | Terrain elevation, slopes, georeferencing | Public data, useful for the ground mesh and slope costs. |
| **UH campus maps, accessibility (ADA) maps** | Ramps, elevators, accessible routes | The rover has accessibility needs similar to a wheelchair. ADA routes are a ready-made traversability prior. |
| **UH class schedule** (time blocks such as MWF / TR) | Crowd model prior: when and where people flood the walkways | Class-change peaks are the main driver of crowding. |
| **Your own crowd counts** | Ground truth for the crowd model | Time-stamped video of key walkways at many times and days, used to count people per zone. Only aggregate counts are stored (see Privacy). |

**Capture protocol:**

- Shoot at **low-traffic times** (early weekend mornings, breaks) to reduce transients at the source. Removal in Phase 2 handles the rest.
- Use **consistent lighting** per session. Overcast is ideal for reconstruction; harsh noon shadows bake into the splat.
- Shoot at **rover camera height** as well as human height. The twin must look right from about 0.5–1 m off the ground.
- Record **GNSS tags and ground control points** (surveyed or RTK-GPS markers) so every zone lands in one shared campus coordinate frame.
- Capture in **overlapping zones** of roughly 100–200 m. The whole campus (~320 acres) is too big for one reconstruction.
- **Drone (optional):** UH Mānoa is near Honolulu airport (HNL) airspace. You need an FAA Part 107 certificate, LAANC authorization, and UH permission. Treat this as a nice-to-have, not something to depend on.

**Privacy:** blur faces and license plates in stored raw data. Never ship identifiable people into the twin (Phase 2 removes them anyway). Keep only aggregate crowd statistics.

**Exit:** the MVP zone is fully captured with GCPs, plus at least 2 weeks of crowd-count samples on the MVP route.

---

### Phase 2: Clean 3D Reconstruction (6–10 weeks for MVP zone)

The goal is a static, empty, georeferenced campus twin that looks photoreal from the rover's cameras and has accurate collision geometry.

**Pipeline per zone:**

1. **Camera poses (SfM):** COLMAP or GLOMAP (faster). Use GCPs to lock scale and the georeference.
2. **Transient masking (removing people, cars, animals):**
   - Run open-vocabulary segmentation (e.g., **Grounded-SAM 2**, or Mask2Former with COCO/Cityscapes classes) for *person, car, bicycle, scooter, dog, cat, bird, bag, umbrella...*
   - Exclude masked pixels from the reconstruction loss so transients never get "learned" into the scene.
   - Add a **robust / transient-aware training method** (e.g., SpotLessSplats, WildGaussians, or NeRF-W-style appearance embeddings) to catch what the masks miss and to absorb lighting changes between sessions.
   - Fill **holes** (ground under parked cars, behind crowds) from other views or other capture sessions. Use generative inpainting only as a last resort, since it invents geometry.
3. **Gaussian splatting (visuals):** 3DGS via **gsplat / nerfstudio**, or NVIDIA **3DGRUT** (3DGUT supports distorted/fisheye cameras and rolling shutter, which suits 360 and wide-FOV rigs, and it's the format Isaac Sim's neural-reconstruction rendering targets).
4. **Mesh (physics and semantics):** splats have no collision. Extract a surface with 2DGS / PGSR / SuGaR-style methods, or build a photogrammetry mesh (OpenMVS / RealityCapture), then simplify. Merge with the GIS terrain for the ground.
5. **Semantic labeling:** label the mesh as *walkway, road, crosswalk, grass, stairs, ramp, curb, building, vegetation, water/drain, bench, pole...* This drives traversability, rewards, and perception ground truth.
6. **Isaac Sim import:** convert to **OpenUSD**. Splat as the visual layer (Omniverse NuRec / neural rendering), mesh as an invisible collider with semantic labels, everything in one campus frame. Stitch zones together.

**Quality checks:**

- Render novel views from the **rover's exact camera poses** along the route and compare with held-out real photos (PSNR/SSIM/LPIPS plus eyeballing).
- **No ghosts:** no smeared half-people or floating car fragments on the MVP route.
- Geometric accuracy: mesh vs. GCPs within about 5–10 cm on walkways; stairs and curbs correctly shaped.

**Exit:** MVP zone in Isaac Sim with a photoreal splat, a clean collider, semantics, and the georeference. A virtual rover can drive the route and its camera views look like reality.

---

### Phase 3: Dynamic World in Isaac Sim (4–8 weeks, runs alongside Phase 2)

Add everything that moves, with full control so you can create any scenario.

| Agent type | How | Behaviors to support |
|---|---|---|
| **Pedestrians** | Isaac Sim people simulation (Replicator Agent / `omni.anim.people`) with navmesh on walkways, plus a **social-force / ORCA** crowd model for dense flows | Walking alone or in groups, standing in clusters, sudden stops, phone-staring, crossing the rover's path, lines outside buildings, class-change surges |
| **Bikes, skateboards, e-scooters** | Rigged assets + scripted/behavior-tree controllers | Fast overtakes, weaving, riding on walkways |
| **Service carts / golf carts / maintenance vehicles** | Vehicle assets on shared paths | Slow-moving, partially blocking walkways, reversing |
| **Cars** | Road network only, with crosswalk interactions | Stopping or *not* stopping at crosswalks; parking-lot movement |
| **Animals** | Rigged animal assets with simple wander/flee behaviors | **Feral chickens and cats** (very common at UH Mānoa), dogs on leashes, birds. Small and erratic, so they're a key test for perception. |
| **Static clutter (randomized)** | Asset library | Backpacks, signs, tents/booths (event days), construction barriers, fallen branches, puddles |

**Scenario engine:**

- **Time-of-day crowd scheduler:** spawn rates per zone driven by the class schedule and calibrated against Phase 1 counts, e.g., 10 minutes before class ends at Holmes Hall leads to a surge on the nearby paths.
- **Special events:** graduations, club fairs, game days at lower campus, rain (people bunch under covered walkways).
- **Domain randomization:** sun angle (seasonal/hourly), cloud cover, **rain and wet surfaces** (frequent in Mānoa valley), camera exposure/noise/blur, lens dirt and water drops, agent appearance and clothing.
- **Adversarial / edge cases:** a child running out, a crowd fully blocking the path, an animal darting under the rover, a person approaching the rover, a blocked ramp.
- **Ground-truth export (Replicator):** RGB from every rover camera plus depth, semantic/instance segmentation, 3D boxes, agent trajectories, BEV occupancy. This is the free labeled data that justifies the whole sim approach.

**Known gap:** the splat background is photoreal, but rendered agents are meshes, so they can look "pasted on" (lighting, shadows, contact). Mitigate with good assets, matched lighting, randomization, and mixing real-world data in Phase 4.

**Exit:** scenario configs that reproduce "quiet morning," "class change," "rain," and "event day" on the MVP route; synthetic datasets exporting from all cameras with full labels.

---

### Phase 4: Perception and Prediction (8–12 weeks)

Camera-only, multi-view, Tesla-style.

| Module | Approach (starting points) | Output |
|---|---|---|
| **Multi-camera → BEV** | Lift-Splat-Shoot / BEVFormer / BEVFusion (camera-only branch) style backbone; small enough to run on a Jetson Orin | Unified bird's-eye-view feature map around the rover |
| **Occupancy** | 3D/2.5D occupancy network head (Tesla-style) | Free vs. occupied space, including things that aren't any known class |
| **Drivable surface and semantics** | BEV segmentation head | Walkway, grass, curb, stairs, ramp, road, crosswalk |
| **Agent detection and tracking** | BEV detection head + tracker | Each person/bike/cart/animal with position, velocity, class |
| **Trajectory prediction** | Social-aware predictor (e.g., Trajectron++ / transformer-based), trained on sim trajectories plus real tracks | Multi-modal future paths for 3–5 s |
| **Crowd density estimation** | Per-zone density from detections, aggregated over time | Live "how crowded is it here" signal for the route planner |
| **Visual localization** | Match camera features against the twin (the splat/mesh is a map) | Position on the campus map |

**Training data strategy:** mostly synthetic (labeled for free), plus a smaller real set collected with the actual camera rig, plus pseudo-labeling of real data. Track **sim-only vs. sim+real** metrics to measure the sim-to-real gap directly.

**Exit:** on *real* held-out footage from the rover rig, pedestrian detection recall ≥ 95% within 10 m, drivable-surface IoU ≥ 90% on the MVP route, and real-time inference (≥ 10 Hz) on target hardware.

---

### Phase 5: Planning and Policy Learning (8–12 weeks)

Two levels. Don't try to learn the whole campus route end-to-end with RL.

**5a. Route planner (global, mostly classical plus learned costs)**

- **Campus graph:** nodes are intersections/doors/landmarks; edges are walkway segments, taken from OSM + UH maps + the semantic mesh.
- **Time-dependent edge cost:**

  `cost(edge, t) = w_d·distance + w_c·predicted_crowd(edge, t) + w_x·complexity(edge) + w_s·slope/surface + ∞·(non-traversable: stairs, closed)`

  - `predicted_crowd(edge, t)`: from the class schedule plus historical counts, updated live from perception.
  - `complexity(edge)`: how hard the segment is to read and predict (intersections, blind corners, mixed bike traffic, building exits).
- **Re-plan** whenever live perception says an edge is much worse than predicted.
- This directly implements "avoid places with a lot of people at a certain time in favor of a route that's easier to drive."

**5b. Local navigation policy (learned, Isaac Lab RL)**

- **Inputs:** BEV perception outputs (occupancy, semantics, agents + predictions) plus the route waypoint. Train on **perception outputs, not raw pixels**. This is more sample-efficient and transfers better from sim to real. Optionally fine-tune end-to-end later.
- **Outputs:** linear and angular velocity commands.
- **Training:** PPO in **Isaac Lab** with thousands of parallel environments, using cheap rendering (or privileged ground-truth state). Curriculum: empty walkway → sparse people → class-change crowds → edge cases. Use **teacher–student distillation**: a teacher trained on privileged ground-truth state, a student that uses only the camera-perception outputs.
- **Reward:** progress to waypoint, minus collisions (huge), minus personal-space intrusions (social distance), minus jerk / sudden stops, minus leaving the drivable surface, minus time; bonus for yielding behavior pedestrians find natural.
- **Baseline to beat:** a classical social-navigation planner (e.g., MPC with social-force costs, or DWA). If RL can't beat it in sim, use the classical one.

**5c. Safety supervisor (non-learned, always on)**

- Hard rules that override the policy: speed cap (~1.5 m/s, lower in crowds), minimum distance to people, emergency stop on occupancy breach or perception failure, geofence, no stairs/curb drops, heartbeat watchdog, and a remote e-stop.

**Exit (sim):** across 1,000+ randomized episodes on the MVP route: ≥ 99% success, 0 at-fault collisions, measurable crowd avoidance vs. the shortest-path baseline, and social-distance violations below a set threshold.

---

### Phase 6: Sim-to-Real and Field Testing (ongoing)

1. **Hardware-in-the-loop:** run the real on-board stack against the sim (camera feeds rendered from the twin).
2. **Real rover, supervised, quiet hours:** MVP route at off-peak times, with a human walking alongside holding an e-stop.
3. **Progressively busier times** on the MVP route.
4. **Log everything.** Every disengagement becomes a sim scenario (*real failure → recreate in twin → retrain → regression test*). This loop is the core long-term process.
5. **Expand the map** zone by zone (repeat Phases 1–3 per zone) until the rover can cross campus, e.g., **Lower Campus ↔ Hawaiʻi Hall / Bachman Hall**.

**Exit (v1 project complete):** autonomous, supervised traversal across campus at normal daytime traffic, with a disengagement rate below the target (e.g., < 1 per km), and route choices that measurably avoid peak crowds.

---

## 4. Deliverables

| # | Deliverable |
|---|---|
| D1 | Capture protocol + georeferenced raw dataset (privacy-processed) |
| D2 | Transient-free reconstruction pipeline (scripts, configs) |
| D3 | Campus digital twin in OpenUSD (splats + collision mesh + semantics), zone by zone |
| D4 | Isaac Sim scenario engine: agents, crowd scheduler, weather/lighting, edge cases |
| D5 | Synthetic data generator with multi-camera ground truth |
| D6 | Camera-only perception + prediction models (with real-data eval report) |
| D7 | Time-dependent crowd-aware route planner |
| D8 | RL local-navigation policy + classical baseline + safety supervisor |
| D9 | Rover hardware (base, camera rig, compute) with a calibrated sim twin of the rover |
| D10 | Field test reports, disengagement log, and the scenario regression suite |

---

## 5. Key Metrics

| Area | Metric |
|---|---|
| Reconstruction | PSNR/SSIM/LPIPS on held-out real views from rover height; ghost count; geometric error vs. GCPs |
| Perception | Detection recall/precision by class and range; BEV IoU; latency on Jetson |
| Prediction | ADE/FDE (avg / final displacement error) at 3 s |
| Route planning | Crowd exposure (people-meters passed) vs. shortest path; added travel time |
| Navigation | Success rate, collisions, social-distance violations, path smoothness, time to goal |
| Sim-to-real | Same metrics measured in sim vs. real; the gap should shrink over iterations |
| Field | Disengagements per km, interventions per hour |

---

## 6. Tech Stack (initial picks)

| Layer | Tools |
|---|---|
| Reconstruction | COLMAP / GLOMAP, gsplat / nerfstudio, NVIDIA 3DGRUT, Grounded-SAM 2, OpenMVS / 2DGS for meshes, Blender for cleanup |
| Sim | NVIDIA Isaac Sim 5.x, Isaac Lab, Omniverse Replicator, OpenUSD |
| ML | PyTorch, a BEV perception codebase, RL via Isaac Lab (rsl_rl / skrl), W&B |
| Robot | ROS 2 (Humble/Jazzy), Isaac ROS on Jetson Orin, Nav2 (for baselines and plumbing) |
| Data | DVC or LakeFS, object storage, Parquet for logs |
| Maps | OSM, QGIS, Hawaiʻi Statewide GIS data |

---

## 7. Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Google Maps content terms prohibit the intended use | Legal / redo work | Use Google only as a reference; your own capture is the primary source (§3, Phase 1) |
| Splats look photoreal but dynamic agents look "pasted on" | Perception overfits to sim artifacts | Good assets, lighting match, heavy randomization, real data in the mix, measure the gap explicitly |
| Splats have no physics; meshes from splats are noisy | Bad collisions, wrong stairs/curbs | Separate photogrammetry/2DGS collider, manual cleanup on the MVP route, GIS terrain |
| Lighting and weather baked into splats | Model only works in "capture weather" | Capture overcast, appearance embeddings, relighting/randomization, multi-session capture |
| Scale: whole campus is large | Compute and storage explode | Zone-based reconstruction and streaming; MVP route first |
| Crowd model doesn't match reality | Route choices wrong | Calibrate with real counts; live perception updates override priors |
| Camera-only fails in glare, rain, low light | Safety | HDR cameras, lens hoods and wipers, conservative supervisor, weather limits for operation |
| End-to-end RL is sample-hungry and opaque | Slow progress, hard to debug | Modular stack: learned perception, RL only for local control, classical route planner, classical baseline |
| People's reaction to the rover / campus rules | Project halted | Early UH engagement, supervised testing, visible signage, low speed |
| Privacy of people captured | Ethical / legal | Face/plate blurring, aggregate-only crowd data, IRB check |
| Scope creep | Never finishes | Strict phase exit criteria; MVP route before campus |

---

## 8. Proposed Repository Layout

```
uh-manoa-rover/
├── docs/                    # scope, design docs, capture protocol, test reports
├── capture/                 # capture planning, GCP management, ingest + privacy blurring
├── reconstruction/          # SfM, transient masking, 3DGS training, mesh extraction, USD export
├── maps/                    # campus graph (OSM + UH), semantics, crowd priors, schedules
├── sim/
│   ├── assets/              # rover USD, agents, clutter (large files via LFS/DVC)
│   ├── scenarios/           # quiet / class-change / rain / event / edge-case configs
│   ├── agents/              # pedestrian, bike, cart, animal behaviors
│   └── replicator/          # synthetic data generation
├── perception/              # BEV models, training, eval
├── prediction/              # trajectory + crowd density models
├── planning/
│   ├── route/               # time-dependent graph planner
│   ├── local/               # RL policy (Isaac Lab tasks) + classical baseline
│   └── safety/              # safety supervisor
├── rover/                   # ROS 2 packages, drivers, calibration, on-board deployment
└── tools/                   # shared utilities, scripts
```

---

## 9. Open Decisions (need your input)

1. **Camera-only vs. "camera-only perception".** Tesla also uses GPS, an IMU, and wheel odometry. Recommendation: keep perception camera-only, but allow **GNSS + IMU + wheel encoders** for localization. They're cheap and make localization far more robust under the tree canopy and near buildings.
2. **Rover platform:** buy (faster, reliable) or build (cheaper, custom)?
3. **MVP route:** is Hamilton Library ↔ Campus Center (McCarthy Mall) right, or is another corridor more useful to you?
4. **Final "one side to the other" route:** which endpoints define success?
5. **Drone capture:** pursue Part 107 + airspace authorization, or stay ground-only?
6. **Compute budget:** local RTX workstation, university HPC (UH has the *Koa* cluster), or cloud?
7. **Team and timeline:** solo or team? Rough calendar, e.g., MVP in sim in ~6 months and the full campus in 12–18 months.

---

## 10. Rough Timeline (single focused team; adjust after §9)

| Months | Milestone |
|---|---|
| 0–1 | Phase 0 complete |
| 1–3 | MVP zone captured and reconstructed; dynamic agents running in Isaac Sim |
| 3–6 | Perception + route planner + RL policy working **in sim** on the MVP route |
| 6–9 | Rover built; hardware-in-the-loop; supervised real runs on the MVP route |
| 9–18 | Zone-by-zone campus expansion; real-failure → sim → retrain loop; cross-campus traversal |
