"""Floor plans: reading sheets, fitting them onto a roof, building floors from them.

Plans are made here as tiny synthetic PDFs, so nothing needs UH's real files."""

import json
import math

import numpy as np
import pymupdf
import pytest
from shapely.geometry import box

from campus_rover.meshkit import Scene
from campus_rover.planbuild import generate_from_plan, level_heights
from campus_rover.plans import (
    Layout,
    Level,
    Placement,
    Room,
    Sheet,
    _match_columns,
    anchor_to_end,
    build_layout,
    is_open,
    layout_ok,
    parse_scale,
    read_sheet,
    register,
    wing_ok,
)
from campus_rover.style import Style

GREY = (0.93, 0.92, 0.9)
BLUE = (0.69, 0.8, 0.88)
K = 20 * 0.3048 / 72          # meters per point at 1" = 20'


def make_pdf(path, code="9999-200", rooms=(), columns=(), scale="1\" = 20'-0\""):
    """One-page plan. rooms: (x0, y0, x1, y1, fill, [label words]) in page points, y down."""
    doc = pymupdf.open()
    page = doc.new_page(width=1224, height=792)
    page.insert_text((60, 700), scale, fontsize=10)
    page.insert_text((900, 700), code, fontsize=20)
    for x0, y0, x1, y1, fill, words in rooms:
        page.draw_rect(pymupdf.Rect(x0, y0, x1, y1), color=(0, 0, 0), fill=fill, width=0.5)
        for i, word in enumerate(words):
            page.insert_text((x0 + 6, y0 + 14 + i * 11), word, fontsize=8)
    for cx, cy in columns:
        page.draw_rect(pymupdf.Rect(cx - 4.5, cy - 4.5, cx + 4.5, cy + 4.5), color=None,
                       fill=(1, 1, 1))
    doc.save(str(path))
    doc.close()
    return path


L_SHAPE = [(100, 100, 400, 220, GREY, ["CORR", "200PC1", "900", "SF", "NON"]),
           (100, 220, 220, 400, BLUE, ["OFC", "201", "300", "SF", "MACE"]),
           (220, 220, 400, 300, GREY, ["STAIR", "200S1", "100", "SF", "NON"]),
           (220, 300, 320, 360, BLUE, ["LAB", "202", "200", "SF", "MACE"])]


def test_parse_scale_handles_both_drawing_scale_styles():
    assert parse_scale("1\" = 20'-0\"") == pytest.approx(20 * 0.3048 / 72)
    assert parse_scale("3/64\" = 1'-0\"") == pytest.approx((64 / 3) * 0.3048 / 72)
    assert parse_scale("no scale here") is None


def test_read_sheet_finds_rooms_labels_columns_and_the_level(tmp_path):
    pdf = make_pdf(tmp_path / "p.pdf", "9999-200E", L_SHAPE,
                   columns=[(150, 150), (250, 150), (350, 150)])
    sheet = read_sheet(pymupdf.open(str(pdf))[0])
    assert (sheet.code, sheet.level, sheet.wing) == ("9999-200E", 200, "E")
    kinds = {r.kind: r for r in sheet.rooms}
    assert set(kinds) == {"CORR", "OFC", "STAIR", "LAB"}
    assert kinds["OFC"].number == "201" and kinds["OFC"].area_sf == 300
    assert kinds["OFC"].org == "MACE" and is_open(kinds["CORR"]) and not is_open(kinds["OFC"])
    assert len(sheet.columns) == 3 and sheet.column_m == pytest.approx(9 * K, rel=0.05)
    assert sheet.outline.area == pytest.approx(
        (300 * 120 + 120 * 180 + 180 * 80 + 100 * 60) * K * K, rel=0.03)


def test_pages_that_are_not_plans_are_skipped(tmp_path):
    doc = pymupdf.open()
    doc.new_page().insert_text((50, 50), "Summary page", fontsize=12)
    assert read_sheet(doc[0]) is None


def roof_for(sheet, placement, size=160, res=1.0):
    """A roof mask that is exactly the sheet's outline, moved by `placement`."""
    from campus_rover.plans import _raster

    return _raster(placement.apply(sheet.outline), 0.0, 0.0, res, (size, size))


def test_register_recovers_where_a_plan_sits_on_its_roof(tmp_path):
    sheet = read_sheet(pymupdf.open(str(make_pdf(tmp_path / "p.pdf", "9999-200", L_SHAPE)))[0])
    truth = Placement(0.0, 40.0, 30.0, 1.0, 1.0)
    placed = register(sheet, roof_for(sheet, truth), 0.0, 0.0, 1.0, angles=[0.0])
    assert (placed.dx, placed.dy) == pytest.approx((40, 30), abs=1.01)
    assert placed.score > 0.9 and placed.agree_plan_cells > 0.95
    # A sheet drawn a quarter turn is found by trying every orientation.
    turned = Placement(90.0, 120.0, 20.0, 1.0, 1.0)
    found = register(sheet, roof_for(sheet, turned), 0.0, 0.0, 1.0,
                     angles=np.arange(-180, 180, 5.0))
    assert found.angle_deg == pytest.approx(90.0, abs=5.0) and found.score > 0.85


def test_wing_names_decide_which_side_a_plan_belongs_on():
    assert wing_ok("E", 120, 0, (100, 0)) and not wing_ok("E", 80, 0, (100, 0))
    assert wing_ok("W", 80, 0, (100, 0)) and not wing_ok("W", 120, 0, (100, 0))
    assert wing_ok("ME", 120, 0, (100, 0)) and wing_ok("", 0, 0, (100, 0))
    assert wing_ok("N", 0, 5, (0, 0)) and not wing_ok("S", 0, 5, (0, 0))


def test_east_sheet_is_anchored_to_the_end_of_the_building():
    sheet = Sheet("x-100E", 100, "E", 0.1, outline=box(0, 0, 50, 20))
    placed = Placement(0.0, 300.0, 0.0, 1.0, 1.0)             # plan spans x 300..350
    moved = anchor_to_end(placed, sheet, (280.0, -5.0, 362.0, 30.0))
    assert moved.apply(sheet.outline).bounds[2] == pytest.approx(362.0)
    west = Sheet("x-100W", 100, "W", 0.1, outline=box(0, 0, 50, 20))
    assert anchor_to_end(placed, west, (280.0, -5.0, 362.0, 30.0)).apply(
        west.outline).bounds[0] == pytest.approx(280.0)
    whole = Sheet("x-100", 100, "", 0.1, outline=box(0, 0, 50, 20))
    assert anchor_to_end(placed, whole, (0, 0, 1, 1)) is placed


def test_column_grid_lines_levels_up_with_each_other():
    ref = [(x * 9.0, y * 7.5) for x in range(6) for y in range(3)]
    upper = [(x + 2.3, y - 0.7) for (x, y) in ref[::2]]       # fewer columns, shifted
    shift, matched = _match_columns(upper, ref)
    assert shift == pytest.approx((-2.3, 0.7), abs=0.11) and matched == len(upper)
    assert _match_columns(upper[:2], ref)[1] == 0              # too few to trust


def stacked_pdf(tmp_path, levels=(100, 200)):
    """Two levels of one building, drawn with the same columns, one shifted on the page."""
    docs = pymupdf.open()
    for n, lv in enumerate(levels):
        shift = 0 if n == 0 else 40                           # level 200 is drawn 40 pt right
        rooms = [(x0 + shift, y0, x1 + shift, y1, fill, words)
                 for x0, y0, x1, y1, fill, words in L_SHAPE]
        cols = [(x + shift, y) for x, y in [(130, 130), (210, 130), (290, 130), (370, 130),
                                            (130, 300), (130, 380), (210, 260), (370, 260)]]
        tmp = make_pdf(tmp_path / f"s{lv}.pdf", f"9999-{lv}", rooms, cols)
        docs.insert_pdf(pymupdf.open(str(tmp)))
    out = tmp_path / "stack.pdf"
    docs.save(str(out))
    return out


def test_build_layout_fits_every_level_into_one_frame(tmp_path):
    pdf = stacked_pdf(tmp_path)
    sheet = read_sheet(pymupdf.open(str(pdf))[0])
    truth = Placement(0.0, 50.0, 40.0, 1.0, 1.0)
    mask = roof_for(sheet, truth, size=200)
    layout = build_layout(str(pdf), "9999", mask, 0.0, 0.0, 1.0, (100.0, 100.0))
    assert layout is not None and [lv.level for lv in layout.levels] == [100, 200]
    q = layout.quality
    assert q["min_score"] > 0.85 and q["column_aligned_sheets"] == 1 and layout_ok(q)
    # Level 200 was drawn 40 pt (3.4 m) to the right on its page, and is shifted back.
    a, b = (lv.slab.bounds for lv in layout.levels)
    assert b == pytest.approx(a, abs=0.5)
    assert layout.levels[0].enclosed.area == pytest.approx(
        (120 * 180 + 180 * 80 + 100 * 60) * K * K, rel=0.05)   # rooms, not the open corridor


def test_layout_survives_json_and_the_quality_gate_has_teeth(tmp_path):
    lay = Layout("1", [Level(100, box(0, 0, 10, 10), box(0, 0, 5, 10), [(1.0, 2.0)],
                             [Room(box(0, 0, 5, 10), "OFC", "101", 300.0, "MACE")])], 1.5,
                 {"min_score": 0.8, "min_agreement": 0.9, "extent_error_m": 3,
                  "column_alignment_needed": 2, "column_aligned_sheets": 2})
    back = Layout.from_json(json.loads(json.dumps(lay.to_json())))
    assert back.levels[0].slab.area == 100 and back.levels[0].rooms[0].kind == "OFC"
    assert back.levels[0].columns == [(1.0, 2.0)] and layout_ok(back.quality)
    good = dict(lay.quality)
    for field, value in (("min_score", 0.5), ("min_agreement", 0.7), ("extent_error_m", 12),
                         ("column_aligned_sheets", 1)):
        assert not layout_ok({**good, field: value}), field
    assert not layout_ok({})
    # A sparse, unaligned ground level only blocks buildings whose rooms there would show.
    sparse = {**good, "sparse_unaligned": 1}
    assert not layout_ok(sparse) and not layout_ok(sparse, "open") and layout_ok(sparse, "enclosed")


# ---- building from a layout ------------------------------------------------------------


def ground_100(x, y):
    return np.full_like(np.asarray(x, dtype=float), 100.0)


def gallery_layout():
    """Two full floors of 40 x 20 m with rooms only in the west quarter, plus a penthouse."""
    slab = box(0, 0, 40, 20).difference(box(15, 6, 25, 14))                 # a courtyard void
    rooms = box(0, 0, 10, 20)
    cols = [(15.0, 5.0), (25.0, 5.0), (15.0, 15.0), (25.0, 15.0)]
    levels = [Level(100, slab, rooms, cols), Level(200, slab, rooms, cols),
              Level(300, box(0, 0, 10, 20), box(0, 0, 10, 20), [])]
    return Layout("9999", levels, 0.0, {})


def test_floor_heights_come_from_the_roof_and_a_small_top_level_is_a_penthouse():
    h = level_heights(gallery_layout(), roof_main=108.0, roof_top=112.0, ground=100.0)
    assert h["full"] == [100, 200] and h["partial"] == [300]
    assert h["floor_height"] == pytest.approx(4.0)
    assert h["z"] == {100: 100.0, 200: 104.0, 300: 108.0}


def test_a_sparse_ground_floor_still_counts_as_a_floor():
    layout = gallery_layout()
    layout.levels = [Level(100, box(0, 0, 10, 10), box(0, 0, 10, 10), []),     # 100 m2 ground
                     Level(200, box(0, 0, 40, 20), box(0, 0, 40, 20), []),
                     Level(300, box(0, 0, 40, 20), box(0, 0, 40, 20), [])]
    h = level_heights(layout, roof_main=111.7, roof_top=111.7, ground=100.0)
    assert h["full"] == [100, 200, 300] and h["floor_height"] == pytest.approx(3.9)


def built(style):
    scene = Scene()
    counts = generate_from_plan(scene, "buildings/t", gallery_layout(), style, ground_100,
                                roof_main=108.0, roof_top=112.0)
    return scene, counts


def test_open_galleries_get_railings_not_walls():
    scene, counts = built(Style(id="t", windows="ribbon", corridors="open"))
    assert counts["levels"] == 3 and counts["floor_height"] == 4.0
    assert counts["parapets"] > 0 and counts["columns"] == 8 and counts["windows"] > 0
    classes = {c for (_, c, _), m in scene.parts.items() if m.count}
    assert {"building", "window", "roof"} <= classes
    walls = scene.parts[("buildings/t", "building", "wall_c9c2b2")]
    xs = walls.arrays()[0][:, 0]
    assert xs.max() <= 10.01          # walls only around the rooms, none across the gallery
    # The ground-level slab is the ground itself: no floor slab is built at z = 100.
    trim_z = scene.parts[("buildings/t", "building", "trim_b3ad9f")].arrays()[0][:, 2]
    assert not np.isclose(trim_z, 100.0).any() and np.isclose(trim_z, 104.0).any()


def test_enclosed_buildings_are_walled_around_the_whole_floor():
    scene, counts = built(Style(id="t", windows="ribbon", corridors="enclosed"))
    walls = scene.parts[("buildings/t", "building", "wall_c9c2b2")].arrays()[0]
    assert walls[:, 0].max() == pytest.approx(40.0)              # out to the far wall
    # ... and around the courtyard, which stays a void.
    inner = walls[(walls[:, 0] > 14) & (walls[:, 0] < 26) & (walls[:, 1] > 5) & (walls[:, 1] < 15)]
    assert len(inner) > 0 and counts["room_walls"] > 0


def test_world_uses_a_plan_only_when_the_style_sheet_says_how_corridors_work():
    assert Style.from_dict({"id": "x"}).corridors is None
    assert Style.from_dict({"id": "x", "corridors": "open"}).corridors == "open"
    with pytest.raises(ValueError, match="corridors"):
        Style.from_dict({"id": "x", "corridors": "sometimes"})
    from campus_rover.site import load_site
    from campus_rover.style import load_styles

    styles = load_styles(load_site("uh_manoa").root / "buildings")
    marked = {k: s.corridors for k, s in styles.items() if s.corridors}
    assert marked["holmes_hall"] == "open" and marked["architecture_building"] == "enclosed"
    assert all("photo" in styles[k].notes.lower() for k in marked)
    assert math.isfinite(len(marked))
