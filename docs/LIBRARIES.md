# Open-Source Libraries, by Pipeline Stage

The goal is an all-open-source stack you can read, modify, and contribute to. ⭐ marks the **recommended starter pick** for each stage. Python is the main language throughout.

**License key:**
- ✅ Permissive (MIT / BSD / Apache): use freely, even commercially.
- 🟡 Copyleft (GPL / AGPL / LGPL / MPL): free to use; sharing modified versions carries obligations.
- 🔶 Non-commercial / research-only: fine for this personal/research project, but must be replaced if the project ever becomes commercial.

Licenses are listed as best known. **Check each repo's LICENSE file before depending on it**, because research code changes licenses often.

---

## 1. Capture and Ingest (M2)

| Library | What for | License |
|---|---|---|
| ⭐ **FFmpeg** | Extract frames from phone video, pick sharp frames | 🟡 LGPL/GPL |
| ⭐ **OpenCV** (`opencv-python`) | Blur detection, undistortion, calibration, general image work | ✅ Apache 2.0 |
| ⭐ **GPSLogger for Android** | Log phone GPS (GPX/CSV) alongside video; works without a SIM | 🟡 GPL |
| **ExifTool** | Read and write GPS/time metadata on frames | 🟡 Artistic/GPL |
| ⭐ **deface** | Automatic face blurring for stored footage | ✅ MIT |
| **gpxpy** | Parse GPX tracks and sync GPS to frame timestamps | ✅ Apache 2.0 |

## 2. Camera Poses / Structure-from-Motion (M1, M2)

| Library | What for | License |
|---|---|---|
| ⭐ **COLMAP** (+ `pycolmap`) | The standard SfM; camera poses + sparse points | ✅ BSD-3 |
| ⭐ **GLOMAP** | Much faster global SfM using COLMAP's formats | ✅ BSD-3 |
| **hloc** (Hierarchical Localization) | Better feature matching; also visual localization later (Phase 4) | ✅ Apache 2.0 (the toolbox itself) |
| **LightGlue** + **ALIKED / DISK** features | Robust learned matching for hard phone footage | ✅ Apache 2.0 (avoid SuperPoint weights, which are 🔶) |
| **OpenMVG** | Alternative SfM library | 🟡 MPL-2.0 |

## 3. Transient Removal: People, Cars, Animals (M1)

| Library | What for | License |
|---|---|---|
| ⭐ **SAM 2** (Segment Anything 2) | Pixel-accurate masks, with tracking through video | ✅ Apache 2.0 |
| ⭐ **Grounding DINO** / **Grounded-SAM-2** | Text-prompted detection ("person, car, bicycle, cat, chicken") → SAM 2 masks | ✅ Apache 2.0 |
| **RF-DETR** | Fast, accurate object detector | ✅ Apache 2.0 |
| **Detectron2** / **Mask2Former** | Classic instance/semantic segmentation | ✅ Apache 2.0 / MIT |
| **Ultralytics YOLO** | Very easy detection/segmentation | 🟡 AGPL-3.0 (strong copyleft) |

Robust splat-training methods (SpotLessSplats, WildGaussians, and similar) are research repos. Check each license, or implement the core idea (mask the loss + per-image appearance embeddings) directly in gsplat.

## 4. Gaussian Splatting and Meshes (M1, M2)

| Library | What for | License |
|---|---|---|
| ⭐ **gsplat** | Fast, clean, hackable Gaussian splatting library (CUDA + PyTorch). Best base for your own training code. | ✅ Apache 2.0 |
| ⭐ **nerfstudio** (`splatfacto`) | Full framework: data processing, training, viewer. Uses gsplat. Easiest way to start. | ✅ Apache 2.0 |
| ⭐ **NVIDIA 3DGRUT** | 3DGUT/3DGRT: supports fisheye/rolling shutter and targets Isaac Sim's neural rendering | ✅ Apache 2.0 |
| **Brush** | Cross-platform splat trainer in Rust; even runs without an NVIDIA GPU | ✅ Apache 2.0 |
| **SuperSplat** (PlayCanvas) | Browser editor to view, crop, and clean splats by hand | ✅ MIT |
| Original **3DGS (Inria)**, SuGaR, 2DGS | Reference implementations, mesh-from-splat methods | 🔶 Non-commercial (Inria license) |
| ⭐ **Open3D** | Point clouds, TSDF fusion, mesh extraction, mesh cleanup | ✅ MIT |
| **trimesh** | Mesh processing in Python, collision-mesh simplification | ✅ MIT |
| **PyMeshLab** / **MeshLab** | Mesh repair and decimation | 🟡 GPL |
| **OpenMVS** | Dense photogrammetry mesh from COLMAP output | 🟡 AGPL |

## 5. Geospatial / UH Layout Twin (M3)

| Library | What for | License |
|---|---|---|
| ⭐ **OSMnx** | Download OSM walkways, stairs, crosswalks around UH as a routable graph | ✅ MIT |
| ⭐ **GeoPandas** + **Shapely** + **pyproj** | Footprints, polygons, lat/long ↔ UTM 4N | ✅ BSD |
| ⭐ **PDAL** + **laspy** | Read and filter the Oʻahu lidar; classify ground/building/vegetation; make a DEM | ✅ BSD |
| **GDAL / rasterio** | DEM rasters, reprojection | ✅ MIT / BSD |
| **QGIS** | Desktop GIS for tracing/inspecting the route graph against the UH map | 🟡 GPL |
| ⭐ **Blender** + **BlenderGIS** add-on | Build the 3D campus: import OSM + DEM, extrude buildings, materials, export USD | 🟡 GPL |
| **CloudCompare** | Visual point-cloud editing | 🟡 GPL |
| **JOSM / iD** | Fix missing UH paths in OpenStreetMap itself | 🟡 GPL / ✅ ISC |

## 6. Simulation (M2–M6)

| Library | What for | License |
|---|---|---|
| ⭐ **NVIDIA Isaac Sim** | The simulator: RTX rendering, physics, sensors, people simulation, Replicator synthetic data | ✅ Apache 2.0 source on GitHub (some bundled Omniverse/RTX components are proprietary but free) |
| ⭐ **Isaac Lab** | RL training with thousands of parallel environments | ✅ BSD-3 |
| ⭐ **OpenUSD** (`usd-core`) | Scene format; script the twin assembly in Python | ✅ Modified Apache 2.0 |
| **Cesium for Omniverse** | Live Google 3D Tiles preview (reference only, §3a) | ✅ Apache 2.0 |
| ⭐ **PySocialForce** | Social-force crowd model for realistic pedestrian flows | ✅ MIT |
| **Python-RVO2 (ORCA)** | Collision-avoiding crowd motion | ✅ Apache 2.0 |
| **Menge** | Full crowd-simulation framework | ✅ Apache 2.0 |
| **MakeHuman / MPFB (Blender)** | Generate varied human models for agents | ✅ CC0 output assets (app is GPL) |

## 7. Perception and Prediction (M5)

| Library | What for | License |
|---|---|---|
| ⭐ **PyTorch** | Everything ML | ✅ BSD |
| ⭐ **MMDetection3D** | Multi-camera BEV models (BEVFusion camera branch, BEVDet, etc.) and nuScenes-style tooling | ✅ Apache 2.0 |
| **BEVFormer** | Transformer multi-camera → BEV | ✅ Apache 2.0 |
| **timm** | Pretrained image backbones | ✅ Apache 2.0 |
| **Depth Anything V2 (Small)** | Monocular depth as an extra perception cue | ✅ Apache 2.0 (larger models are 🔶) |
| ⭐ **ByteTrack** | Multi-object tracking of people/bikes/animals | ✅ MIT |
| ⭐ **Trajectron++** + **trajdata** | Pedestrian trajectory prediction and dataset loaders | ✅ MIT / Apache 2.0 |
| **Kornia** | Differentiable geometry/augmentation in PyTorch (camera projection for BEV) | ✅ Apache 2.0 |
| ⭐ **CVAT** / **Label Studio** | Label the few hundred real proxy frames for M5 evaluation | ✅ MIT / Apache 2.0 |
| ⭐ **ONNX** / **ONNX Runtime** | Export models for the Jetson (TensorRT itself is free but not open source) | ✅ Apache 2.0 / MIT |

Pretraining datasets to check: COCO (✅ CC BY 4.0 annotations); nuScenes, Waymo Open, and Cityscapes (🔶 non-commercial terms).

## 8. Planning and Policy (M3, M6)

| Library | What for | License |
|---|---|---|
| ⭐ **NetworkX** | Time-dependent campus graph, A*/Dijkstra with crowd costs | ✅ BSD |
| ⭐ **rsl_rl** | PPO for Isaac Lab (fast, simple) | ✅ BSD-3 |
| **skrl** / **Stable-Baselines3** | Alternative RL libraries | ✅ MIT |
| **CrowdNav** | Crowd-navigation RL baselines and env ideas | ✅ MIT |
| **CasADi** / **acados** | MPC for the classical social-navigation baseline | 🟡 LGPL / ✅ BSD |

## 9. Robot (M7)

| Library | What for | License |
|---|---|---|
| ⭐ **ROS 2** (Humble/Jazzy) | Robot middleware | ✅ Apache 2.0 |
| ⭐ **Nav2** | Navigation plumbing + classical baselines | ✅ Apache 2.0 |
| ⭐ **robot_localization** | EKF: phone GPS + phone IMU + wheel odometry | ✅ BSD |
| **Isaac ROS** | GPU-accelerated ROS 2 packages for Jetson | ✅ Apache 2.0 |
| **gpsd** | Phone NMEA → standard GPS device | ✅ BSD |
| **hoverboard-firmware-hack-FOC** | Drive the used-hoverboard base | 🟡 GPL-3 |

## 10. Tooling

| Library | What for | License |
|---|---|---|
| ⭐ **Hydra** | Config system, a natural fit for `sites/<name>/site.yaml` | ✅ MIT |
| ⭐ **Rerun** | Visualize everything (images, BEV, point clouds, trajectories) over time; also for robot logs | ✅ MIT / Apache 2.0 |
| ⭐ **DVC** | Version large datasets and models alongside git | ✅ Apache 2.0 |
| **MLflow** | Experiment tracking (self-hosted, fully open source) | ✅ Apache 2.0 |
| **pytest**, **ruff**, **uv** | Tests, linting, Python environments | ✅ MIT / Apache 2.0 |

---

## Minimal starter set per milestone

| Milestone | Install first |
|---|---|
| **M1** transient removal | nerfstudio (gsplat), COLMAP/GLOMAP, Grounded-SAM-2 |
| **M2** phone → sim | + FFmpeg, OpenCV, GPSLogger, deface, Open3D, usd-core, Isaac Sim, 3DGRUT |
| **M3** UH layout twin | OSMnx, GeoPandas, PDAL, Blender + BlenderGIS, NetworkX |
| **M4** dynamic world | Isaac Sim (people sim + Replicator), PySocialForce |
| **M5** perception | PyTorch, MMDetection3D, ByteTrack, Trajectron++, CVAT |
| **M6** policy | Isaac Lab, rsl_rl |
| **M7** rover | ROS 2, Nav2, robot_localization, gpsd, Isaac ROS |
| **Always** | Hydra, Rerun, DVC, MLflow |

## Good places to contribute back

- **OpenStreetMap:** add missing UH walkways, stairs, and ramps (immediately useful, and the easiest start).
- **gsplat / nerfstudio:** transient-masking or GPS-georeferencing improvements.
- **Isaac Lab:** a social-navigation task environment.
- **PySocialForce:** time-scheduled crowd spawning (class-change surges).
