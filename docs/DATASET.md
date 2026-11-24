# Training dataset

`campus-rover build-dataset` draws images from the 3D campus along the route and writes the labels a
segmentation model and a planner need. It is the first thing to run once the rover's camera is known.

```bash
pip install -e ".[world,dataset]"          # playwright drives the browser
campus-rover build-explorer --site uh_manoa --out out/explorer --scenario class_change
campus-rover build-dataset --site uh_manoa --explorer out/explorer --out out/dataset --frames 400
```

On a PC with an NVIDIA card Chromium uses the GPU and a frame takes about a second; on a CPU-only
machine it takes about ten.

## The camera

`sites/uh_manoa/camera.yaml` holds the rover's camera: height, tilt, horizontal field of view, resolution,
and the errors a real rover makes (heading, sideways position, body rocking). **The values in it are
placeholders**; set the real ones and `placeholder: false`, then build again. A dataset made with a
placeholder camera says so in its README.

## What a frame is

| File | Content |
|---|---|
| `_rgb.jpg` | the camera image: random hour (7:30, 9:20, noon, 3:30, 5:45), wet or dry pavement, exposure, people or not, sensor noise |
| `_sem.png` | class id per pixel, `classes.json` has the names; 0 is sky |
| `_trav.png` | 0 blocked, 1 conditional, 2 traversable, 3 people and vehicles, 255 sky; the table in `traversal.py` |
| `_depth.png` | 16-bit millimetres along the camera axis; 65535 means farther than 65 m |

`metadata.jsonl` adds the pose, lighting, class shares, and where the route runs relative to the camera
(heading error, sideways offset), for a planner that follows the segmentation.

## Splits are by place

The route is cut into 80 m places. Whole places go to train, validation or test (70 / 15 / 15), so a test score
measures transfer to ground the model has not seen. Frames near a place boundary look alike across the
boundary; for a stricter test leave a place or two unused between them.

## What to trust

- Labels are exact for the **model**. The model has known doubts, all listed in the explorer: the crossing of
  Campus Rd or Farrington Rd that no map shows, grey buildings with no photo-backed look, paving extents read
  from photos, and sculpture sizes estimated from photos. Do not train the planner to trust a crossing nobody
  has checked.
- The 8.3 % slope limit is the ADA ramp figure, not your rover's.
- Images are rendered, not photographed. Measure the gap against real footage before relying on a model
  trained only on this.
- Not simulated yet: lens distortion, rolling shutter, motion blur, rain on the lens. They belong in the camera
  settings once the real camera is known.
