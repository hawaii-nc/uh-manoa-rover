"""3D world builder: mesh toolkit, lidar gridding, massing, styles, facades, export."""

import json
import math

import numpy as np
import pytest
from shapely.geometry import box

from campus_rover.agents import Scenario, add_car, add_person
from campus_rover.buildgen import generate_building
from campus_rover.lidar import Gridder, Rasters, fill_holes
from campus_rover.massing import box_massing, building_massing
from campus_rover.meshkit import Mesh, Scene, hex_to_rgb, triangulate_polygon, wall_normal
from campus_rover.overture import segments_to_campus_graph
from campus_rover.site import load_site
from campus_rover.style import Style, auto_style, load_styles, slug
from campus_rover.world import build_world, export_glb, export_usd, write_world


def mesh_area(mesh: Mesh) -> float:
    v, f = mesh.arrays()
    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    return float(np.linalg.norm(np.cross(b - a, c - a), axis=1).sum() / 2)


# ---- mesh toolkit ------------------------------------------------------------------


def test_wall_faces_outward_for_counter_clockwise_ring():
    # South edge of a counter-clockwise square runs west -> east; outside is south.
    assert wall_normal((0, 0), (10, 0)) == pytest.approx((0, -1))
    mesh = Mesh()
    mesh.wall((0, 0), (10, 0), 0, 3)
    v, f = mesh.arrays()
    normal = np.cross(v[f[0, 1]] - v[f[0, 0]], v[f[0, 2]] - v[f[0, 0]])
    assert normal[1] < 0 and mesh_area(mesh) == pytest.approx(30)


def test_polygon_cap_respects_holes():
    ring = box(0, 0, 10, 10).difference(box(4, 4, 6, 6))
    verts, tris = triangulate_polygon(ring)
    mesh = Mesh()
    mesh.polygon_cap(ring, 5.0)
    assert mesh_area(mesh) == pytest.approx(96)
    assert len(tris) > 0 and verts.shape[1] == 2


def test_scene_rejects_unknown_material_and_counts_triangles():
    scene = Scene()
    with pytest.raises(KeyError):
        scene.mesh("g", "building", "nope")
    mat = scene.color_material("wall", "#ff0000")
    scene.mesh("g", "building", mat).box((0, 0, 0), (1, 1, 1))
    assert scene.stats()["by_class"] == {"building": 12}
    assert hex_to_rgb("#ff0000") == (1.0, 0.0, 0.0)


# ---- lidar ------------------------------------------------------------------------


def test_gridder_splits_ground_roofs_and_leaves():
    grid = Gridder((0, 0, 4, 4), res=1.0)
    x = np.array([0.5, 0.5, 2.5, 2.5, 9.0])
    y = np.array([0.5, 0.5, 2.5, 2.5, 9.0])
    z = np.array([10.0, 12.0, 20.0, 18.0, 99.0])
    cls = np.array([2, 2, 1, 1, 2])            # two ground, two unclassified, one outside
    returns = np.array([1, 1, 1, 3, 1])        # the last unclassified one went through leaves
    kept = grid.add(x, y, z, cls, np.array([100, 300, 0, 0, 0]), returns)
    r = grid.finish()
    assert kept == 4
    assert r.dtm[0, 0] == pytest.approx(11.0) and r.intensity[0, 0] == pytest.approx(200)
    assert r.dsm_bldg[2, 2] == pytest.approx(20.0) and r.dsm_veg[2, 2] == pytest.approx(18.0)
    assert np.isfinite(r.dtm).all()            # holes are filled
    assert r.ground_z(0.5, 0.5) == pytest.approx(11.0)


def test_fill_holes_uses_nearest_value():
    grid = np.array([[1.0, np.nan, np.nan, 5.0]])
    out = fill_holes(grid, smooth_passes=0)
    assert out.tolist() == [[1.0, 1.0, 5.0, 5.0]]


def synthetic_rasters(roof) -> Rasters:
    """40 x 40 m of flat ground at z = 100 with `roof(x, y)` heights above it."""
    res, n = 0.5, 80
    xs = (np.arange(n) + 0.5) * res
    gx, gy = np.meshgrid(xs, xs)
    heights = roof(gx, gy)
    dsm = np.where(heights > 0, 100.0 + heights, np.nan)
    return Rasters(0.0, 0.0, res, np.full((n, n), 100.0), dsm, np.full((n, n), np.nan),
                   np.full((n, n), 20000.0), np.ones((n, n), dtype=np.uint16))


def test_massing_finds_tower_on_podium():
    def roof(x, y):
        inside = (x > 10) & (x < 30) & (y > 10) & (y < 30)
        tower = (x > 10) & (x < 20) & (y > 10) & (y < 30)
        return np.where(tower, 20.0, np.where(inside, 8.0, 0.0))

    m = building_massing(box(10, 10, 30, 30), synthetic_rasters(roof))
    heights = sorted(round(b.height) for b in m.blocks)
    assert heights == [8, 20] and not m.pitched
    assert m.base_z == pytest.approx(100.0) and m.height == pytest.approx(20.0, abs=0.5)
    areas = {round(b.height): b.polygon.area for b in m.blocks}
    assert areas[20] == pytest.approx(200, rel=0.15) and areas[8] == pytest.approx(200, rel=0.15)
    assert m.height_at(15, 20) == pytest.approx(20.0) and m.height_at(35, 35) == 0.0


def test_massing_detects_pitched_roof_and_falls_back_without_lidar():
    def gable(x, y):
        inside = (x > 10) & (x < 30) & (y > 10) & (y < 30)
        return np.where(inside, 6.0 + 5.0 * (1 - np.abs(x - 20) / 10), 0.0)

    m = building_massing(box(10, 10, 30, 30), synthetic_rasters(gable))
    assert m.pitched and len(m.blocks) == 1 and 6.0 <= m.eave < 8.0

    empty = synthetic_rasters(lambda x, y: np.zeros_like(x))
    fallback = building_massing(box(10, 10, 30, 30), empty, default_height=9.0)
    assert [b.height for b in fallback.blocks] == [9.0]


# ---- styles and facades -------------------------------------------------------------


def test_style_validation_and_auto_style():
    with pytest.raises(ValueError, match="windows"):
        Style.from_dict({"id": "x", "windows": "stained"})
    with pytest.raises(ValueError, match="unknown fields"):
        Style.from_dict({"id": "x", "colour": "#ffffff"})
    with pytest.raises(ValueError, match="colour"):
        Style.from_dict({"id": "x", "wall": "beige"})
    assert slug("QUEEN LILI`UOKALANI CENTER") == "queen_liliuokalani_center"
    assert auto_style("shed", area_m2=40).windows == "none"
    assert auto_style("old", year=1925, pitched=True).roof == "#7a4a36"
    assert auto_style("seventies", year=1972).shading == "fins"


def test_uh_style_sheets_are_valid_and_cite_sources():
    site = load_site("uh_manoa")
    styles = load_styles(site.root / "buildings")
    assert {"holmes_hall", "campus_center", "architecture_building"} <= set(styles)
    for style in styles.values():
        assert style.sources, f"{style.id} has no source"


@pytest.mark.parametrize("windows,shading,ground", [
    ("grid", "fins", "solid"), ("ribbon", "eyebrows", "arcade"),
    ("curtain", "none", "storefront"), ("slit", "lanai", "solid"), ("none", "none", "solid"),
])
def test_generate_building_makes_labelled_parts(windows, shading, ground):
    scene = Scene()
    style = Style(id="t", windows=windows, shading=shading, ground_floor=ground, floors=3)
    info = generate_building(scene, "buildings/t", box_massing(box(0, 0, 30, 15), 0.0, 12.0),
                             style)
    classes = {cls for (_, cls, _), m in scene.parts.items() if m.count}
    assert {"building", "roof", "door"} <= classes
    assert info["floors"] == 3 and info["doors"] == 1
    has_glass = windows != "none" and shading != "lanai" or ground == "storefront"
    assert ("window" in classes) == has_glass
    v, _ = scene.parts[("buildings/t", "roof", "roof_8c8c88")].arrays()
    assert v[:, 2].max() == pytest.approx(12.0 - 0.45)        # roof sits below the parapet


def test_taller_block_hides_shared_wall():
    # Two blocks side by side: the low block's wall against the tower must not be built.
    def roof(x, y):
        inside = (x > 10) & (x < 30) & (y > 10) & (y < 30)
        return np.where(inside & (x < 20), 20.0, np.where(inside, 8.0, 0.0))

    m = building_massing(box(10, 10, 30, 30), synthetic_rasters(roof))
    scene = Scene()
    generate_building(scene, "b", m, Style(id="b", windows="none"))
    wall = scene.parts[("b", "building", "wall_c9c2b2")]
    # Outside walls: 80 m around x up to 8 m high (plus foundation) + the tower's extra height.
    assert mesh_area(wall) < 80 * (8 + 2.5) + 50 * 12 + 400


# ---- walkway import -----------------------------------------------------------------


def test_overture_segments_become_a_connected_graph():
    segs = [
        {"coords": [(0, 0), (10, 0), (20, 0)], "class": "footway", "subclass": None},
        {"coords": [(10, 0), (10, 10)], "class": "steps", "subclass": None},
        {"coords": [(20, 0), (30, 0)], "class": "service", "subclass": "parking_aisle"},
        {"coords": [(0, 0), (0, 50)], "class": "motorway", "subclass": None},      # not walkable
        {"coords": [(500, 500), (510, 500)], "class": "footway", "subclass": None},  # stray
    ]
    g = segments_to_campus_graph(segs).g
    assert g.number_of_nodes() == 5 and g.number_of_edges() == 4
    kinds = sorted(d["kind"] for _, _, d in g.edges(data=True))
    assert kinds == ["stairs", "walkway", "walkway", "walkway"]
    zones = [d["zone"] for _, _, d in g.edges(data=True)]
    assert zones.count("roadside") == 1


# ---- whole world on the demo campus (no lidar, no network) ---------------------------


@pytest.fixture(scope="module")
def demo_world():
    return build_world(load_site("demo_campus"), Scenario(name="test", time="Mon 09:20"))


def test_demo_world_builds_without_lidar(demo_world):
    r = demo_world.report
    assert r["lidar"] is False and len(r["buildings"]) >= 5 and r["trees"] > 0
    classes = set(r["mesh"]["by_class"])
    assert {"grass", "walkway", "building", "window", "roof", "tree"} <= classes
    assert r["surface_share"]["walkway"] > 0.01
    assert r["route"]["length_m"] > 100 and any(b["tier"] == "A" for b in r["buildings"])
    assert r["agents"]["person"] > 0


def test_agents_are_a_separate_layer(demo_world):
    static = {cls for (_, cls, _) in demo_world.scene.parts}
    moving = {cls for (_, cls, _) in demo_world.agents.parts}
    assert "person" in moving and not ({"person", "car", "bicycle"} & static)


def test_person_and_car_have_real_world_sizes():
    scene = Scene()
    rng = np.random.default_rng(0)
    add_person(scene, "a", 0, 0, 0, 0.0, rng)
    add_car(scene, "c", 10, 0, 0, math.pi / 2, rng)
    person = np.vstack([m.arrays()[0] for (g, _, _), m in scene.parts.items() if g == "a"])
    car = np.vstack([m.arrays()[0] for (g, _, _), m in scene.parts.items() if g == "c"])
    assert 1.45 < person[:, 2].max() < 2.0
    assert 4.0 < np.ptp(car[:, 1]) < 5.0 and 1.6 < np.ptp(car[:, 0]) < 2.2   # turned 90°


def test_exports_keep_labels(demo_world, tmp_path):
    import trimesh

    files = write_world(demo_world, tmp_path)
    assert {"world.glb", "world.usd", "agents.glb", "world.json", "viewer"} <= set(files)
    loaded = trimesh.load(files["world.glb"])
    names = list(loaded.geometry)
    assert any(n.split("|")[1] == "window" for n in names)
    report = json.loads(files["world.json"].read_text())
    assert report["agents_file"] == "agents_test.glb"

    from pxr import Usd, UsdGeom, UsdPhysics, UsdSemantics

    stage = Usd.Stage.Open(str(files["world.usd"]))
    assert UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z
    labels = set()
    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.Mesh):
            labels.update(UsdSemantics.LabelsAPI(prim, "class").GetLabelsAttr().Get())
            assert prim.HasAPI(UsdPhysics.CollisionAPI)
    assert {"building", "window", "walkway", "grass", "tree"} <= labels


def test_glb_is_y_up_and_recentred(tmp_path):
    import trimesh

    scene = Scene(origin=(1000.0, 2000.0, 50.0))
    scene.mesh("g", "building", scene.color_material("wall", "#808080")).box(
        (1000, 2000, 50), (2, 4, 10))
    export_glb(scene, tmp_path / "t.glb")
    export_usd(scene, tmp_path / "t.usda")
    mesh = next(iter(trimesh.load(tmp_path / "t.glb").geometry.values()))
    lo, hi = mesh.bounds
    assert hi[1] - lo[1] == pytest.approx(10) and lo[1] == pytest.approx(0)   # height on Y
    assert hi[2] - lo[2] == pytest.approx(4)
    assert "campus:originOffset" in (tmp_path / "t.usda").read_text()
