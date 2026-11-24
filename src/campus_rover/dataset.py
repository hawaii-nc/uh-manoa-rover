"""A training dataset from the campus model: images along the route with their labels.

For each frame the rover's camera is placed on the route (with the small errors a real rover
makes) and the scene is drawn four ways from that one camera:

- `rgb`    what the camera sees, in a random time of day, wet or dry pavement, exposure, with or
           without people
- `sem`    a class id per pixel (0 = sky; the ids are in `classes.json`)
- `trav`   0 blocked, 1 conditional, 2 traversable, 3 people and vehicles, 255 sky; the same
           table `traversal.py` uses everywhere, so the label and the planner agree
- `depth`  distance along the camera axis in millimetres, 16-bit (65535 = farther than 65 m)

Each frame also records the pose, the camera intrinsics, the lighting, and where the route runs
relative to the camera (heading error and sideways offset), for a planner that follows the
segmentation. Frames are grouped into *places* (stretches of the route) and whole places are held
out for validation and test, so a test score measures transfer to new ground, not memory of
neighbouring frames.

The camera is `sites/<site>/camera.yaml`. Until the real rover's numbers are in it, the file says
so, and so does the dataset's README.

The browser does the drawing (the explorer page, with a flat-colour pass for each label), so any
machine with Chromium works; a GPU makes it fast. `pip install playwright` for the driver.
"""

from __future__ import annotations

import base64
import functools
import http.server
import io
import json
import math
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import yaml


@dataclass
class CameraSpec:
    """The rover's camera. Values here are PLACEHOLDERS until `placeholder: false`."""

    height_m: float = 0.7              # lens above the ground
    tilt_deg: float = 0.0              # + looks up
    hfov_deg: float = 90.0             # horizontal field of view
    width: int = 640
    height: int = 480
    height_jitter_m: float = 0.04      # the body rocks on uneven ground
    tilt_jitter_deg: float = 3.0
    yaw_jitter_deg: float = 20.0       # heading error against the route
    lateral_jitter_m: float = 0.5      # sideways error on the path
    placeholder: bool = True
    note: str = ""

    @classmethod
    def load(cls, path: Path | None) -> CameraSpec:
        if path is None or not Path(path).exists():
            return cls()
        data = yaml.safe_load(Path(path).read_text()) or {}
        unknown = set(data) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"camera.yaml: unknown fields {sorted(unknown)}")
        return cls(**data)

    def intrinsics(self) -> dict:
        """Pinhole intrinsics in pixels (no lens distortion yet)."""
        fx = self.width / 2 / math.tan(math.radians(self.hfov_deg) / 2)
        return {"fx": round(fx, 3), "fy": round(fx, 3), "cx": self.width / 2, "cy": self.height / 2,
                "width": self.width, "height": self.height, "distortion": None}


@dataclass
class DatasetSpec:
    frames: int = 120
    spacing_m: float = 3.0             # candidate camera positions along the route
    hours: list = field(default_factory=lambda: [7.5, 9.33, 12.0, 15.5, 17.75])
    wet_share: float = 0.3
    exposure: tuple = (0.8, 1.2)       # scale on the sky's own exposure
    people_share: float = 0.8          # share of frames with people and vehicles drawn
    place_m: float = 80.0              # route length of one place
    split: tuple = (0.7, 0.15, 0.15)   # train, validation, test (by place)
    supersample: int = 2               # the real image is drawn at this multiple, then shrunk
    noise_sigma: float = 2.0           # sensor noise on the real image, in 8-bit levels
    jpeg_quality: int = 92
    seed: int = 7


HIDE_PAGE_UI = (
    "() => { for (const id of ['top', 'side', 'mini', 'hud', 'ask', 'pad', 'help', 'loading']) "
    "{ const e = document.getElementById(id); if (e) e.style.display = 'none'; } }")


# ---- pure parts (tested offline) -----------------------------------------------------------


def route_samples(legs: list[list], spacing: float) -> list[dict]:
    """Points every `spacing` m along each route polyline (viewer coordinates x, y, z), with the
    heading of the route there and the distance travelled from the start of the route."""
    out, travelled = [], 0.0
    for pts in legs:
        if len(pts) < 2:
            continue
        seg = [math.hypot(b[0] - a[0], b[2] - a[2]) for a, b in zip(pts, pts[1:], strict=False)]
        total, start = sum(seg), 0.0
        for d in np.arange(0.0, total, spacing):
            while start + seg[0] < d and len(seg) > 1:      # step to the segment holding d
                start += seg.pop(0)
                pts = pts[1:]
            a, b = pts[0], pts[1]
            t = (d - start) / seg[0] if seg[0] else 0.0
            out.append({"x": a[0] + (b[0] - a[0]) * t, "z": a[2] + (b[2] - a[2]) * t,
                        "heading": heading_of(a, b), "s": travelled + float(d)})
        travelled += total
    return out


def heading_of(a, b) -> float:
    """Yaw of the direction a -> b in the viewer's convention (0 faces -z, turning left is +)."""
    return math.atan2(-(b[0] - a[0]), -(b[2] - a[2]))


def assign_places(samples: list[dict], place_m: float, split: tuple, seed: int) -> dict:
    """Place id for every sample, and a split (train / val / test) for every place.

    Whole places are held out, never single frames, and places are dealt out at random so that
    held-out stretches come from the whole route, not just one end."""
    for s in samples:
        s["place"] = int(s["s"] // place_m)
    places = sorted({s["place"] for s in samples})
    rng = np.random.default_rng(seed)
    order = list(rng.permutation(places))
    n = len(order)
    n_val = max(1, round(split[1] * n)) if n >= 3 else 0
    n_test = max(1, round(split[2] * n)) if n >= 3 else 0
    assignment = {}
    for i, place in enumerate(order):
        assignment[int(place)] = "test" if i < n_test else "val" if i < n_test + n_val else "train"
    return assignment


def pick_frames(samples: list[dict], frames: int) -> list[dict]:
    """At most `frames` samples, spread evenly along the route."""
    if len(samples) <= frames:
        return list(samples)
    idx = np.linspace(0, len(samples) - 1, frames).round().astype(int)
    return [samples[i] for i in sorted(set(idx.tolist()))]


def jitter_pose(sample: dict, camera: CameraSpec, rng) -> dict:
    """The camera pose for a sample: the route point, shifted sideways and turned a little."""
    h = sample["heading"]
    side = float(rng.uniform(-camera.lateral_jitter_m, camera.lateral_jitter_m))
    left = (-math.cos(h), math.sin(h))          # a heading h faces (-sin h, -cos h) in the viewer
    yaw_err = float(rng.uniform(-camera.yaw_jitter_deg, camera.yaw_jitter_deg))
    return {"x": sample["x"] + left[0] * side, "z": sample["z"] + left[1] * side,
            "yaw": h + math.radians(yaw_err), "yaw_error_deg": yaw_err, "side_m": side,
            "pitch": math.radians(camera.tilt_deg + float(rng.uniform(-camera.tilt_jitter_deg,
                                                                      camera.tilt_jitter_deg))),
            "eye": camera.height_m + float(rng.uniform(-camera.height_jitter_m,
                                                       camera.height_jitter_m))}


def decode_depth_mm(rgb: np.ndarray) -> np.ndarray:
    """uint16 millimetres from the depth pass (red = high byte, green = low byte)."""
    return (rgb[..., 0].astype(np.uint16) << 8) | rgb[..., 1].astype(np.uint16)


def class_fractions(sem: np.ndarray, names: dict) -> dict:
    ids, counts = np.unique(sem, return_counts=True)
    total = float(sem.size)
    return {names.get(int(i), "sky" if i == 0 else str(i)): round(c / total, 4)
            for i, c in zip(ids, counts, strict=True)}


def png_array(data_url: str) -> np.ndarray:
    from PIL import Image

    raw = base64.b64decode(data_url.split(",", 1)[1])
    return np.asarray(Image.open(io.BytesIO(raw)).convert("RGB"))


# ---- the run (drives a browser) --------------------------------------------------------------


def build_dataset(explorer_dir: Path, out_dir: Path, camera: CameraSpec, spec: DatasetSpec,
                  progress=print) -> dict:  # pragma: no cover - browser
    """Draw the dataset from a built explorer folder (`campus-rover build-explorer`, not
    `--hosted`). Returns the summary written to `dataset.json`."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise SystemExit("build-dataset drives a browser: pip install playwright") from e
    from PIL import Image

    data = json.loads((explorer_dir / "data.json").read_text())
    if not isinstance(data["files"]["ground"], str):
        raise SystemExit("this explorer folder was built with --hosted (text chunks); build one "
                         "without it for the dataset")
    legs = [leg["pts"] for leg in data["routes"][next(reversed(data["routes"]))]["legs"]
            if leg["pts"]]
    samples = route_samples(legs, spec.spacing_m)
    if not samples:
        raise SystemExit("the route has no drivable points to place the camera on")
    places = assign_places(samples, spec.place_m, spec.split, spec.seed)
    chosen = pick_frames(samples, spec.frames)
    rng = np.random.default_rng(spec.seed)
    ox, oy, oz = data["origin"]
    (out_dir / "frames").mkdir(parents=True, exist_ok=True)
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args, **kwargs) -> None:
            pass

    handler = functools.partial(Quiet, directory=str(explorer_dir))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    rows, skipped = [], 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader",
                                           "--enable-unsafe-swiftshader",
                                           "--ignore-gpu-blocklist"])
        page = browser.new_page(viewport={"width": camera.width, "height": camera.height})
        page.set_default_timeout(600_000)
        port = server.server_address[1]
        page.goto(f"http://127.0.0.1:{port}/index.html?shot=1&aa=0&shadows=1")
        page.wait_for_function("window.__ready === true")
        if not page.evaluate("!!window.__dataset"):
            raise SystemExit(
                "this explorer folder is from an older build: run build-explorer again")
        page.evaluate(HIDE_PAGE_UI)
        classes = page.evaluate("window.__dataset.classes()")
        names = {c["id"]: c["name"] for c in classes}
        aspect = camera.width / camera.height
        for n, sample in enumerate(chosen):
            pose = jitter_pose(sample, camera, rng)
            look = {"hour": float(rng.choice(spec.hours)),
                    "wet": bool(rng.random() < spec.wet_share),
                    "exposure": float(rng.uniform(*spec.exposure)),
                    "agents": bool(rng.random() < spec.people_share)}
            placed = page.evaluate("(a) => window.__dataset.setView(a)", {
                "x": pose["x"], "z": pose["z"], "yaw": pose["yaw"], "pitch": pose["pitch"],
                "eye": pose["eye"], "hfov": camera.hfov_deg, "aspect": aspect})
            if not placed:
                skipped += 1
                continue
            page.evaluate("(l) => window.__dataset.setLook(l)", look)
            ss = spec.supersample
            page.evaluate("() => window.__dataset.pass('real')")
            rgb = Image.fromarray(png_array(page.evaluate(
                f"window.__dataset.render({camera.width * 1}, {camera.height * 1}, {ss})")))
            rgb = rgb.resize((camera.width, camera.height), Image.LANCZOS)
            passes = {}
            for kind in ("sem", "trav", "depth"):
                page.evaluate(f"() => window.__dataset.pass('{kind}')")
                passes[kind] = png_array(page.evaluate(
                    f"window.__dataset.render({camera.width}, {camera.height}, 1)"))
            page.evaluate("() => window.__dataset.pass('real')")
            sem, trav = passes["sem"][..., 0], passes["trav"][..., 0]
            depth = decode_depth_mm(passes["depth"])
            near = depth[(depth < 65535)]
            if near.size == 0 or np.median(near) < 300:
                skipped += 1                       # pressed against a wall or inside a crown
                continue
            arr = np.asarray(rgb).astype(np.float32)
            if spec.noise_sigma:
                arr += rng.normal(0, spec.noise_sigma, arr.shape)
            fid = f"{n:05d}"
            Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).save(
                out_dir / "frames" / f"{fid}_rgb.jpg", quality=spec.jpeg_quality)
            Image.fromarray(sem.astype(np.uint8)).save(out_dir / "frames" / f"{fid}_sem.png")
            Image.fromarray(trav.astype(np.uint8)).save(out_dir / "frames" / f"{fid}_trav.png")
            Image.fromarray(depth.astype(np.uint16)).save(out_dir / "frames" / f"{fid}_depth.png")
            rows.append({
                "id": fid, "place": sample["place"], "split": places[sample["place"]],
                "site_xy": [round(ox + placed["x"], 2), round(oy - placed["z"], 2)],
                "ground_m": round(placed["foot"] + oz, 2),
                "yaw_deg": round(math.degrees(pose["yaw"]), 2),
                "pitch_deg": round(math.degrees(pose["pitch"]), 2),
                "camera_height_m": round(pose["eye"], 3),
                "route_heading_error_deg": round(-pose["yaw_error_deg"], 2),
                "route_side_offset_m": round(-pose["side_m"], 3),
                "route_s_m": round(sample["s"], 1), **look,
                "classes": class_fractions(sem, names),
                "traversable_share": round(float((trav == 2).mean()), 4),
                "blocked_share": round(float((trav == 0).mean()), 4)})
            if (n + 1) % 5 == 0 or n + 1 == len(chosen):
                progress(f"  frame {n + 1}/{len(chosen)} ({len(rows)} kept)")
        browser.close()
    server.shutdown()
    return write_outputs(out_dir, rows, classes, camera, spec, skipped, places)


def write_outputs(out_dir: Path, rows: list, classes: list, camera: CameraSpec, spec: DatasetSpec,
                  skipped: int, places: dict) -> dict:
    (out_dir / "metadata.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    (out_dir / "classes.json").write_text(json.dumps({
        "sem": {"0": "sky", **{str(c["id"]): c["name"] for c in classes}},
        "sem_level": {c["name"]: c["level"] for c in classes},
        "trav": {"0": "blocked", "1": "conditional", "2": "traversable", "3": "dynamic",
                 "255": "sky"},
        "depth": "uint16 millimetres along the camera axis; 65535 = farther than 65 m"}, indent=1))
    splits = {s: [r["id"] for r in rows if r["split"] == s] for s in ("train", "val", "test")}
    (out_dir / "splits.json").write_text(json.dumps({
        "by": "place (a stretch of the route); whole places are held out",
        "place_m": spec.place_m, "places": {str(k): v for k, v in places.items()}, **splits},
        indent=1))
    summary = {"frames": len(rows), "skipped": skipped, "camera": asdict(camera),
               "intrinsics": camera.intrinsics(), "spec": asdict(spec),
               "split_counts": {k: len(v) for k, v in splits.items()},
               "mean_traversable_share": round(float(np.mean(
                   [r["traversable_share"] for r in rows])), 4) if rows else None}
    (out_dir / "dataset.json").write_text(json.dumps(summary, indent=1))
    (out_dir / "README.md").write_text(readme(summary, camera))
    return summary


PLACEHOLDER_WARNING = """> **The camera in this dataset is a placeholder.** Its height, tilt, field of view and resolution are guesses until the real rover's numbers go in `camera.yaml` and the dataset is built again. A model trained on it will expect this camera.

"""  # noqa: E501

README_TEMPLATE = """# UH Mānoa route dataset

{warning}{frames} frames along Holmes Hall → Campus Center → Architecture ({train} train, {val} validation, {test} test). Built by `campus-rover build-dataset` from the 3D campus model; `docs/WORLD.md` says where every part of the model comes from and what is assumed.

Per frame, `frames/<id>_*`:

| File | Content |
|---|---|
| `_rgb.jpg` | camera image ({width} x {height}): random time of day, wet or dry pavement, exposure, people or not, sensor noise |
| `_sem.png` | class id per pixel (8-bit); `classes.json` maps ids to names; 0 = sky |
| `_trav.png` | 0 blocked, 1 conditional (grass, road, gravel), 2 traversable (walkway, ramp), 3 people and vehicles, 255 sky |
| `_depth.png` | 16-bit, millimetres along the camera axis; 65535 = farther than 65 m |

`metadata.jsonl` has one record per frame: site position (EPSG:32604), heading, camera height, the route's heading error and sideways offset from the camera (positive = to the left), lighting, and the share of each class. `splits.json` lists the frames of each split. **Splits are by place**: whole stretches of the route are held out, so a score on `test` measures how well a model handles ground it has not seen.

## What the labels are, and what they are not

- The labels are exact for the model, not for the real campus. Where the model is wrong (a ramp that is not there, a missing curb cut) the label is wrong the same way. The explorer lists the places to check.
- Traversability follows `traversal.py`: walkways and ramps are traversable; grass, road and gravel are conditional; stairs, curbs, walls, fences, buildings, trees and furniture are blocked; people and vehicles are their own class. The 8.3 % slope limit is the ADA figure, not your rover's.
- The images are rendered, not photographed. Expect a domain gap: measure it against real footage before trusting a model trained only on this.
- Intrinsics are an ideal pinhole: no lens distortion, no rolling shutter.

Camera: {intrinsics}
"""  # noqa: E501


def readme(summary: dict, camera: CameraSpec) -> str:
    c = summary["split_counts"]
    return README_TEMPLATE.format(
        warning=PLACEHOLDER_WARNING if camera.placeholder else "", frames=summary["frames"],
        train=c["train"], val=c["val"], test=c["test"], width=camera.width, height=camera.height,
        intrinsics=json.dumps(summary["intrinsics"]))


# ---- reading a dataset back -------------------------------------------------------------------


def frame_ids(root: Path, split: str) -> list[str]:
    """Frame ids of one split ("train", "val" or "test")."""
    return json.loads((Path(root) / "splits.json").read_text())[split]


def load_frame(root: Path, frame_id: str) -> dict:
    """One frame as arrays: `rgb` (H, W, 3 uint8), `sem` (H, W uint8 class ids), `trav` (H, W
    uint8), `depth_m` (H, W float32 meters, NaN beyond 65 m) and `meta` (the metadata record)."""
    from PIL import Image

    root = Path(root)
    frames = root / "frames"
    depth = np.asarray(Image.open(frames / f"{frame_id}_depth.png")).astype(np.float32)
    depth_m = np.where(depth >= 65535, np.nan, depth / 1000.0).astype(np.float32)
    meta = next(json.loads(line) for line in (root / "metadata.jsonl").read_text().splitlines()
                if json.loads(line)["id"] == frame_id)
    return {"rgb": np.asarray(Image.open(frames / f"{frame_id}_rgb.jpg").convert("RGB")),
            "sem": np.asarray(Image.open(frames / f"{frame_id}_sem.png")),
            "trav": np.asarray(Image.open(frames / f"{frame_id}_trav.png")),
            "depth_m": depth_m, "meta": meta}
