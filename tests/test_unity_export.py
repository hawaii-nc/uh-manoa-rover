import json
import struct

import pytest

from campus_rover.agents import Scenario
from campus_rover.site import load_site
from campus_rover.unity_export import (
    DRESSED_GROUPS,
    GEOMETRY_FILES,
    MARKERS,
    export_unity,
    frame_point,
    yaw_quaternion,
)
from campus_rover.world import build_world


@pytest.fixture(scope="module")
def export(tmp_path_factory):
    site = load_site("demo_campus")
    world = build_world(site, Scenario(name="test", time="Mon 09:20"), lite=True)
    out = tmp_path_factory.mktemp("unity")
    files = export_unity(site, world, out)
    return site, world, out, files


def glb_json(path):
    raw = path.read_bytes()
    n = struct.unpack("<I", raw[12:16])[0]
    return json.loads(raw[20:20 + n])


def test_each_mesh_part_goes_in_one_geometry_file_and_dressed_things_in_none(export):
    _, world, _, _ = export
    for key in world.scene.parts:
        homes = [name for name, keep in GEOMETRY_FILES.items() if keep(key)]
        if key[0] in DRESSED_GROUPS:
            assert homes == [], key
        else:
            assert len(homes) == 1, (key, homes)


def test_ground_nodes_carry_their_class_for_labels(export):
    _, _, out, _ = export
    names = [n["name"] for n in glb_json(out / "geometry" / "ground.glb")["nodes"]]
    classes = json.loads((out / "classes.json").read_text())["classes"]
    assert names and all(len(n.split("|")) == 3 for n in names)
    assert {n.split("|")[1] for n in names} <= set(classes)
    assert classes["walkway"]["level"] == "traversable"
    assert classes["curb"]["level"] == "blocked"
    assert classes["tree"]["collision"] == "trunk"


def test_placement_nodes_match_the_records_and_the_mesh_frame(export):
    _, world, out, _ = export
    data = json.loads((out / "placements.json").read_text())
    nodes = glb_json(out / "placements.glb")["nodes"]
    markers = {n["name"]: n for n in nodes if n["name"].startswith("site|")}
    assert {f"site|{k}" for k in MARKERS} == set(markers)
    places = [n for n in nodes if n["name"].startswith("p|")]
    assert len(places) == len(data["placements"]) > 0
    for node in places:
        _, i, kind, key = node["name"].split("|")
        rec = data["placements"][int(i)]
        assert (rec["kind"], rec["key"]) == (kind, key)
        assert node["translation"] == frame_point(world.scene.origin, *rec["xyz"])
        assert node["rotation"] == yaw_quaternion(rec["yaw"])


def test_every_placement_kind_has_a_fallback_model(export):
    _, _, out, _ = export
    data = json.loads((out / "placements.json").read_text())
    assert all(p["proto"] in data["prototypes"] for p in data["placements"])
    groups = {n["name"].split("|")[0] for n in glb_json(out / "prototypes.glb")["nodes"]}
    assert {v["group"] for v in data["prototypes"].values()} <= groups
    kinds = {p["kind"] for p in data["placements"]}
    assert {"tree", "prop", "agent"} <= kinds
    trees = [v for v in data["prototypes"].values() if v["kind"] == "tree"]
    assert all(v["height_m"] > 0 and v["crown_radius_m"] > 0 for v in trees)


def test_frame_and_yaw_conventions():
    # east stays x, up becomes y, north becomes -z (the GLB frame)
    assert frame_point((10.0, 20.0, 1.0), 13.0, 25.0, 3.0) == [3.0, 2.0, -5.0]
    q = yaw_quaternion(0.0)
    assert q == [0.0, 0.0, 0.0, 1.0]


def test_dress_map_starts_empty_with_rules(export):
    _, _, out, _ = export
    dm = json.loads((out / "dress_map.json").read_text())
    assert dm["rules"] and dm["kinds"]
    kinds = dm["kinds"].values()
    assert all(v["model"] is None and v["fallback"].startswith("proto:") for v in kinds)


def test_site_file_and_network(export):
    _, world, out, _ = export
    site = json.loads((out / "site.json").read_text())
    assert site["frame"]["z"] == "south" and site["origin_site_xyz"] == list(world.scene.origin)
    assert (out / "network.json").exists()
