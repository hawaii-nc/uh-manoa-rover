"""Training dataset generation: route sampling, poses, places and splits, label decoding, and one
small end-to-end run in a headless browser (skipped when there is none)."""

import json
import math

import numpy as np
import pytest

from campus_rover.dataset import (
    CameraSpec,
    DatasetSpec,
    assign_places,
    class_fractions,
    decode_depth_mm,
    heading_of,
    jitter_pose,
    pick_frames,
    readme,
    route_samples,
)

LEG = [[0.0, 0.0, 0.0], [0.0, 0.0, -10.0], [10.0, 0.0, -10.0]]       # north 10 m, then east 10 m


def test_samples_run_along_the_route_with_its_heading_and_distance():
    pts = route_samples([LEG], 2.5)
    assert len(pts) == 8 and pts[0]["s"] == 0 and pts[-1]["s"] == pytest.approx(17.5)
    assert pts[1]["z"] == pytest.approx(-2.5) and pts[1]["heading"] == pytest.approx(0.0)
    east = [p for p in pts if p["x"] > 0]
    assert east and east[0]["heading"] == pytest.approx(-math.pi / 2)     # facing +x turns right
    assert heading_of((0, 0, 0), (-1, 0, 0)) == pytest.approx(math.pi / 2)   # facing -x: left


def test_two_legs_keep_counting_distance_from_the_start_of_the_route():
    pts = route_samples([LEG, LEG], 5.0)
    assert pts[len(pts) // 2]["s"] >= 20.0 and pts[-1]["s"] > 30.0


def test_whole_places_are_held_out_and_the_split_is_reproducible():
    samples = route_samples([[[0, 0, 0], [0, 0, -400]]], 4.0)
    first = assign_places(samples, 40.0, (0.7, 0.15, 0.15), seed=3)
    again = assign_places(route_samples([[[0, 0, 0], [0, 0, -400]]], 4.0), 40.0,
                          (0.7, 0.15, 0.15), seed=3)
    assert first == again and set(first.values()) == {"train", "val", "test"}
    assert len(first) == 10
    by_place = {}
    for s in samples:
        by_place.setdefault(s["place"], set()).add(first[s["place"]])
    assert all(len(v) == 1 for v in by_place.values())          # a place is never split


def test_frames_are_spread_along_the_route():
    samples = route_samples([[[0, 0, 0], [0, 0, -300]]], 1.0)
    picked = pick_frames(samples, 10)
    assert len(picked) == 10 and picked[0]["s"] == 0 and picked[-1]["s"] > 290
    assert len(pick_frames(samples[:5], 10)) == 5


def test_poses_stay_within_the_cameras_error_budget_and_record_the_route_offset():
    cam = CameraSpec(lateral_jitter_m=0.5, yaw_jitter_deg=20, height_m=0.7, height_jitter_m=0.04,
                     tilt_deg=2, tilt_jitter_deg=3)
    rng = np.random.default_rng(1)
    sample = {"x": 10.0, "z": 5.0, "heading": 0.0, "s": 0.0}
    for _ in range(200):
        pose = jitter_pose(sample, cam, rng)
        assert abs(pose["side_m"]) <= 0.5 and abs(pose["yaw_error_deg"]) <= 20
        assert 0.66 <= pose["eye"] <= 0.74 and -1.0 <= math.degrees(pose["pitch"]) <= 5.0
        # facing -z, "left" is -x: a camera moved left by `side` stands that far from the route
        assert pose["x"] == pytest.approx(10.0 - pose["side_m"])


def test_depth_and_class_images_decode_exactly():
    rgb = np.zeros((2, 2, 3), np.uint8)
    rgb[0, 0] = (1, 44, 0)                       # 1 * 256 + 44 = 300 mm
    rgb[1, 1] = (255, 255, 0)                    # beyond 65 m
    assert decode_depth_mm(rgb).tolist() == [[300, 0], [0, 65535]]
    sem = np.array([[0, 0], [3, 3]])
    assert class_fractions(sem, {3: "walkway"}) == {"sky": 0.5, "walkway": 0.5}


def test_the_camera_file_is_validated_and_gives_pinhole_intrinsics(tmp_path):
    path = tmp_path / "camera.yaml"
    path.write_text("hfov_deg: 90\nwidth: 640\nheight: 480\nplaceholder: false\n")
    cam = CameraSpec.load(path)
    k = cam.intrinsics()
    assert k["fx"] == pytest.approx(320.0) and k["cx"] == 320 and k["cy"] == 240
    path.write_text("hfv_deg: 90\n")
    with pytest.raises(ValueError, match="unknown fields"):
        CameraSpec.load(path)
    assert CameraSpec.load(tmp_path / "missing.yaml").placeholder is True


def test_the_readme_says_when_the_camera_is_only_a_placeholder():
    summary = {"frames": 10, "split_counts": {"train": 7, "val": 2, "test": 1},
               "intrinsics": CameraSpec().intrinsics()}
    assert "placeholder" in readme(summary, CameraSpec())
    assert "placeholder" not in readme(summary, CameraSpec(placeholder=False))
    assert DatasetSpec().split == (0.7, 0.15, 0.15)


def test_a_small_dataset_is_drawn_in_a_browser(tmp_path):
    pytest.importorskip("playwright")
    from campus_rover.dataset import build_dataset
    from campus_rover.explorer import build_explorer
    from campus_rover.site import load_site
    from campus_rover.world import build_world

    site = load_site("demo_campus")
    build_explorer(site, build_world(site, lite=True), tmp_path / "x", max_file_mb=64)
    cam = CameraSpec(width=160, height=120)
    try:
        summary = build_dataset(tmp_path / "x", tmp_path / "d", cam,
                                DatasetSpec(frames=3, spacing_m=5, supersample=1),
                                progress=lambda m: None)
    except Exception as e:                      # no Chromium or no WebGL on this machine
        pytest.skip(f"no usable browser: {e}")
    assert summary["frames"] >= 1
    classes = json.loads((tmp_path / "d" / "classes.json").read_text())
    from PIL import Image

    sem = np.asarray(Image.open(tmp_path / "d" / "frames" / "00000_sem.png"))
    assert set(np.unique(sem).tolist()) <= {int(k) for k in classes["sem"]}
    trav = np.asarray(Image.open(tmp_path / "d" / "frames" / "00000_trav.png"))
    assert set(np.unique(trav).tolist()) <= {0, 1, 2, 3, 255}
    assert (tmp_path / "d" / "README.md").exists()
