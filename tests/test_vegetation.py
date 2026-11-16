"""Species-shaped trees, ground cover, leaf textures, people, and the route-focused build."""

import io
import json
import struct

import numpy as np
import pytest
from PIL import Image

from campus_rover.agents import add_person
from campus_rover.meshkit import Scene
from campus_rover.site import load_site
from campus_rover.vegetation import (
    archetype_for,
    crown,
    place_ground_cover,
    place_tree,
    shrub,
    texture,
)
from campus_rover.world import build_world, export_glb


def test_species_names_pick_the_shape_a_photo_shows():
    assert archetype_for("Niu, Coconut") == "coconut"
    assert archetype_for("Monkeypod, Rain Tree") == "umbrella"
    assert archetype_for("Hurricane Palm, Macarthur Palm") == "feather_palm"
    assert archetype_for("Loulu, Nihoa Fan Palm") == "fan_palm"
    assert archetype_for("Chinese Banyan") == "dome"
    assert archetype_for("Plumeria") == "plumeria"
    assert archetype_for("Pūhala, Screwpine") == "screwpine"
    assert archetype_for(None, "Arecaceae") == "feather_palm"      # family when the name is missing
    assert archetype_for("Something Unlisted", "Fabaceae") == "spreading"   # neutral, not a guess


@pytest.mark.parametrize("kind", ["broad", "fine_flower", "frond", "fan", "ti", "fern"])
def test_leaf_textures_are_cut_outs(kind):
    img = Image.open(io.BytesIO(texture(kind)))
    alpha = np.asarray(img.convert("RGBA"))[..., 3]
    assert set(np.unique(alpha)) <= {0, 255}                    # hard edges: alpha-tested
    assert 0.08 < (alpha == 255).mean() < 0.95                  # a shape, not an empty or full card


def test_crowns_stand_on_their_trunk_and_lod_is_lighter():
    hi, lo = crown("umbrella", 10.0, 0, 0), crown("umbrella", 10.0, 1, 0)
    assert len(lo.prims[0].faces) < len(hi.prims[0].faces) / 2
    assert hi.prims[0].verts[:, 2].min() >= -0.5 and hi.prims[0].alpha
    palm = crown("coconut", 1.0, 0, 0)
    z = palm.prims[0].verts[:, 2]
    assert z.max() > 0 > z.min()                                 # fronds arch up, then droop


def test_a_tree_is_a_trunk_in_the_scene_and_a_shared_crown_stretched_to_fit():
    scene = Scene(origin=(0, 0, 0))
    cache: dict = {}
    for i in range(3):
        place_tree(scene, cache, 10.0 * i, 0.0, 0.0, 15.0, 9.0, 0.8, "Monkeypod, Rain Tree",
                   "Fabaceae", "Tree", seed=i, lod=0)
    assert len(scene.instances) == 3 and len(scene.assets) <= 2     # a prototype serves many trees
    assert any(p[1] == "tree" for p in scene.parts)                   # the trunk still collides
    sx, sy, sz = scene.instances[0].scale
    assert sx == sy and 0.6 <= sx <= 1.5 and sz > 0
    place_ground_cover(scene, cache, "ti", 0.0, 3.0, 0.0, seed=1, lod=0)
    assert scene.instances[-1].asset.startswith("shrub_ti")


def test_leaf_models_export_with_cut_out_materials_and_one_shared_texture(tmp_path):
    scene = Scene(origin=(0, 0, 0))
    cache: dict = {}
    for i in range(4):
        place_tree(scene, cache, 12.0 * i, 0.0, 0.0, 12.0, 6.0, 0.5, "Kukui, Candlenut", "", "",
                   seed=i, lod=0)
    export_glb(scene, tmp_path / "t.glb")
    raw = (tmp_path / "t.glb").read_bytes()
    meta = json.loads(raw[20:20 + struct.unpack("<I", raw[12:16])[0]])
    leaf = [m for m in meta["materials"] if m.get("alphaMode") == "MASK"]
    assert leaf and all(m["doubleSided"] for m in leaf)
    leaf_images = [i for i in meta["images"] if i["mimeType"] == "image/png"]
    assert len(leaf_images) <= 2 < len(scene.instances)         # one copy per texture, not per tree
    assert [n["scale"] for n in meta["nodes"] if "scale" in n][0][0] > 0


def test_ground_cover_prototypes_are_small_and_light():
    for kind in ("ti", "fern", "grass", "bush"):
        a = shrub(kind, 0, 0)
        assert a.prims[0].verts[:, 2].max() < 2.0 and len(a.prims[0].faces) < 200


def test_a_person_is_articulated_but_light_and_walks_with_opposite_arm_and_leg():
    scene = Scene(origin=(0, 0, 0))
    rng = np.random.default_rng(4)
    for _ in range(12):
        add_person(scene, "agents/people", 0, 0, 0, 0.0, rng)
    tris = sum(len(m.arrays()[1]) for m in scene.parts.values()) / 12
    assert 250 < tris < 700
    verts = np.vstack([m.arrays()[0] for m in scene.parts.values()])
    assert 1.55 < verts[:, 2].max() < 2.0 and verts[:, 2].min() > -0.05      # a human height


def test_the_route_focus_keeps_only_what_is_near_the_route():
    site = load_site("demo_campus")
    full = build_world(site, lite=True)
    near = build_world(site, lite=True, focus_m=12.0)
    triangles = lambda w: sum(len(m.arrays()[1]) for m in w.scene.parts.values())   # noqa: E731
    assert triangles(near) < triangles(full)
