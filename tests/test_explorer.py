"""The explorer bundle: data the page reads, and the split into shareable files."""

import json
import struct

import pytest

from campus_rover.explorer import KINDS, build_explorer, collect, split_files
from campus_rover.site import load_site
from campus_rover.world import build_world, export_glb


@pytest.fixture(scope="module")
def demo():
    site = load_site("demo_campus")
    return site, build_world(site, lite=True)


def test_every_scene_part_goes_in_exactly_one_file(demo):
    _, world = demo
    filters = split_files()
    for key in world.scene.parts:
        assert sum(bool(keep(key)) for keep in filters.values()) == 1, key


def test_lite_export_is_smaller_and_keeps_ground_textures(demo, tmp_path):
    _, world = demo
    full, lite = tmp_path / "full.glb", tmp_path / "lite.glb"
    export_glb(world.scene, full)
    export_glb(world.scene, lite, lite=True)
    assert lite.stat().st_size < full.stat().st_size

    def textured(path):
        raw = path.read_bytes()
        meta = json.loads(raw[20:20 + struct.unpack("<I", raw[12:16])[0]])
        return {m["name"] for m in meta["materials"]
                if "baseColorTexture" in m["pbrMetallicRoughness"]}

    assert textured(lite) <= textured(full)
    assert all(name.startswith(("ground_", "walkway", "road", "grass")) or "ground" in name
               for name in textured(lite))


def test_data_describes_the_network_routes_and_checks(demo):
    site, world = demo
    data = collect(site, world)
    json.dumps(data)                                  # the page reads it as JSON
    assert data["edges"] and all(len(e) == 13 for e in data["edges"])
    assert all(0 <= e[6] < len(KINDS) for e in data["edges"])
    ids = [c["id"] for c in data["checks"]]
    assert len(ids) == len(set(ids)), "check ids must be unique so marks do not collide"
    assert set(data["routes"]) == set(site.routes)
    for route in data["routes"].values():
        for leg in route["legs"]:
            assert leg["passable"] == (not leg["stretches"] and bool(leg["pts"]))
    for b in data["buildings"]:
        assert len(b["ring"]) >= 3 and b["how"] in ("sheet", "unverified", "procedural")
    for c in data["checks"]:
        assert c["title"] and c["why"] and len(c["pos"]) == 3


def test_check_ids_are_stable_between_builds(demo):
    site, world = demo
    first = [c["id"] for c in collect(site, world)["checks"]]
    second = [c["id"] for c in collect(site, world)["checks"]]
    assert first == second


def test_bundle_has_the_page_the_data_and_small_files(demo, tmp_path):
    site, world = demo
    # The demo ground is one dense raster mesh, so allow it more than a real site's ground.
    with pytest.raises(RuntimeError, match="over 1 MiB"):
        build_explorer(site, world, tmp_path / "small", max_file_mb=1)
    files = build_explorer(site, world, tmp_path / "x", max_file_mb=64)
    for name in ("index.html", "data.json", "world_ground.glb", "world_structures.glb",
                 "world_objects.glb"):
        assert files[name].exists()
    assert (tmp_path / "x" / "vendor" / "three.module.min.js").exists()
    data = json.loads(files["data.json"].read_text())
    assert set(data["files"]) == {"ground", "structures", "objects", "agents"}
