"""Sculptures drawn from UH's art layer, benches and tables by seating type, stair rails."""

import numpy as np
import pytest
from shapely.geometry import box

from campus_rover.furniture import picnic_table, slat_bench
from campus_rover.ground import add_stair_rails, apply_paving
from campus_rover.meshkit import Scene
from campus_rover.monuments import BUILDERS, MONUMENTS, monument_for
from campus_rover.world import flat_rasters


def test_pieces_are_found_by_name_and_murals_are_not_drawn():
    assert monument_for("Gate of Hope").name == "monument_gate_of_hope"
    assert monument_for("Krypton 1 x 6 x 18") is not None
    assert monument_for("Varney Circle Fountain") is not None
    assert monument_for("[Untitled]") is None and monument_for("frescos") is None
    assert monument_for(None) is None


def test_every_listed_piece_has_a_builder_and_a_size_basis():
    for keyword, (builder, basis) in MONUMENTS.items():
        assert builder in BUILDERS and basis, keyword
        asset = BUILDERS[builder]()
        assert asset.cls == "art_piece" and asset.triangles > 8
        assert asset.meta["licence"] == "own work" and asset.meta["from"].startswith("UH art photo")
        size = asset.size
        assert 0.4 < max(size) < 15 and asset.prims[0].verts[:, 2].min() > -0.5


def test_krypton_takes_its_size_from_the_title_in_feet():
    size = monument_for("Krypton 1 x 6 x 18").size
    assert size[2] == pytest.approx(18 * 0.3048, abs=0.02)
    assert sorted(size[:2]) == pytest.approx([1 * 0.3048, 6 * 0.3048], abs=0.02)


def test_benches_are_slatted_and_tables_have_seats():
    bench, table = slat_bench(), picnic_table()
    assert bench.cls == table.cls == "bench" and 1.6 < bench.size[0] < 2.0 and bench.size[2] < 1.0
    assert len(bench.prims) == 2 and table.size[1] > bench.size[1]


def test_paving_regions_can_tint_stairs_too():
    stairs = box(0, 0, 4, 6)
    out = apply_paving([(stairs, "stairs", None), (box(0, 10, 5, 12), "walkway", None)],
                       [{"center": [2, 3], "radius_m": 20, "material": "green", "class": "stairs"}])
    assert [c for _, c, _ in out] == ["stairs:green", "walkway"]


def test_rails_follow_the_climb_and_only_where_a_photo_shows_them():
    rasters = flat_rasters((0, 0, 60, 60))
    treads = [box(10, 10 + 0.3 * k, 14, 10.3 + 0.3 * k) for k in range(10)]     # 4 m wide, 3 m run
    near = [box(8, 14, 16, 20)]
    scene = Scene(origin=(0, 0, 0))
    assert add_stair_rails(scene, treads, near, rasters, (0, 0, 60, 60)) == 2    # one per side
    verts = np.vstack([m.arrays()[0] for k, m in scene.parts.items() if k[1] == "rail"])
    assert verts[:, 0].min() < 10.2 and verts[:, 0].max() > 13.8                 # at both sides
    assert verts[:, 2].max() > 0.9                                               # handrail height
    plain = Scene(origin=(0, 0, 0))
    assert add_stair_rails(plain, treads, [box(40, 40, 50, 50)], rasters, (0, 0, 60, 60)) == 0
