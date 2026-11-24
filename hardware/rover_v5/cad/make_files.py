"""Print STLs (in print orientation), laser DXFs, and a parts table for the v5 printed/cut parts."""
import json, math, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build123d import (Axis, Pos, Rot, import_step, export_stl, ExportDXF, Rectangle, Circle, RegularPolygon,
                       SlotOverall, Compound, GeomType, Unit)
from design_v5 import *          # noqa: F401,F403
from load_step import leaves

SCR = os.environ.get("ROVER_V5_WORK", os.path.expanduser("~/rover_v5_work"))  # holds step_dl/, gh/ and the outputs in v5/
OUT = os.path.join(SCR, "v5", "out")
PRINT, LASER = os.path.join(OUT, "print"), os.path.join(OUT, "laser")
os.makedirs(PRINT, exist_ok=True); os.makedirs(LASER, exist_ok=True)
BED = (220.0, 220.0, 250.0)
rows = []


def to_bed(shape):
    bb = shape.bounding_box()
    return Pos(-(bb.min.X + bb.max.X) / 2, -(bb.min.Y + bb.max.Y) / 2, -bb.min.Z) * shape


def save(name, shape, qty, material, notes, orient=None):
    s = shape
    if orient:
        for ax, ang in orient:
            s = s.rotate({"x": Axis.X, "y": Axis.Y, "z": Axis.Z}[ax], ang)
    s = to_bed(s)
    bb = s.bounding_box()
    size = (round(bb.size.X, 1), round(bb.size.Y, 1), round(bb.size.Z, 1))
    fits = (size[0] <= BED[0] and size[1] <= BED[1]) or (size[1] <= BED[0] and size[0] <= BED[1])
    fn = f"{name}_x{qty}.stl"
    export_stl(s, os.path.join(PRINT, fn), tolerance=0.02, angular_tolerance=0.1)
    vol = s.volume / 1000.0
    grams = vol * 1.27 * 0.6          # PETG, ~60 % of solid with 4 walls + 40 % infill (rough)
    rows.append({"file": fn, "part": name, "qty": qty, "material": material, "size_mm": size, "fits_220_bed": fits,
                 "est_g_each": round(grams), "notes": notes})
    print(fn, size, "fits" if fits else "TOO BIG", round(grams), "g")


def best_orientation(shape):
    """Pick the axis-aligned orientation with the most flat area on the bed."""
    cands = [[], [("x", 90)], [("x", -90)], [("x", 180)], [("y", 90)], [("y", -90)]]
    best, best_a = None, -1
    for c in cands:
        s = shape
        for ax, ang in c:
            s = s.rotate({"x": Axis.X, "y": Axis.Y}[ax], ang)
        zmin = s.bounding_box().min.Z
        area = 0.0
        for f in s.faces():
            if f.geom_type != GeomType.PLANE:
                continue
            n = f.normal_at()
            if n.Z < -0.999 and abs(f.bounding_box().min.Z - zmin) < 0.05:
                area += f.area
        if area > best_a + 1:
            best, best_a = c, area
    return best


# ---------------------------------------------------------------- new printed parts
arm_l = drive_arm_left()
save("drive_arm_left", arm_l, 1, "PETG or ASA, 5 walls, 50 % gyroid",
     "Print motor-plate face down, tree supports under the pivot beam. Press 2x 608ZZ + 2x 1/2 in hex flanged bearings.", [("x", 90)])
save("drive_arm_right", mirror_y(arm_l), 1, "PETG or ASA, 5 walls, 50 % gyroid", "Mirror of the left arm.", [("x", -90)])
ad = hex_adapter_left()
save("hex_adapter", ad, 2, "PETG, 100 % infill (or 6 mm aluminium, see laser/)", "Boss face down. 6x M3x14 into the RS00 output.", [("x", 90)])
ins_in, ins_out, _, _ = wheel_insert_halves_left(0.0)
save("wheel_insert_inner", ins_in, 2, "PETG, 100 % infill", "Flange down. M4 heads sit flush.", [("x", 90)])
save("wheel_insert_outer", ins_out, 2, "PETG, 100 % infill", "Flange down.", [("x", -90)])
for n in TILES:
    save(n, tile(n, for_print=True), 1, "PETG or PLA, 3 walls, 25 % infill",
         "Top face down so nut pockets and feet print without supports. Push M3 nuts in from below only where you need them.", [("x", 180)])
save("battery_stop", Pos(0, 0, -TILE_TOP) * stop_block(0, 0, "+x"), 4, "PETG, 4 walls, 30 %", "Base down. Stick 3 mm foam on the inside face.")
save("strap_loop", Pos(0, 0, -TILE_TOP) * strap_loop(0, 0), 4, "PETG, 4 walls, 30 %", "Feet down.")
for L in (45, 70, 95):
    save(f"slot_strip_{L}", Pos(0, 0, -TILE_TOP) * slot_strip(0, L, 0), {45: 4, 70: 2, 95: 4}[L], "PETG or PLA", "Flat.")
plate = box(0, 3, 0, 54, 0, 30)
for (y, z) in ((27 - 9.5, 15 + 9.5), (27 + 9.5, 15 - 9.5)):
    plate -= cyl_x(-1, 4, y, z, 1.6)
plate -= cyl_x(-1, 4, 27, 15, 12.0)
save("charge_socket_plate", plate, 1, "PETG", "Hole 24 mm with 2x M3 on the 19 x 19 D-size pattern (XLR/DC D-size sockets). Fits inside the old C14 cutout.",
     [("y", 90)])

# ---------------------------------------------------------------- kept green parts, from the v4.4 STEP
KEEP_GREEN = {"deck_post_mid_left": 1, "deck_post_mid_right": 1, "deck_post_front_left": 1, "deck_post_front_right": 1,
              "shock_tower_rear_left": 1, "shock_tower_rear_right": 1, "shock_tower_front_left": 1, "shock_tower_front_right": 1,
              "fan_plate": 1, "usb_hub_bracket": 1}
root = import_step(os.path.join(SCR, "step_dl/rover_v4.step"))
for path, shp in leaves(root):
    n = path[-1]
    if n in KEEP_GREEN:
        o = best_orientation(shp)
        save(n, shp, KEEP_GREEN[n], "PETG, 4 walls, 30 % gyroid", "Unchanged from v4.4.", o)

# ---------------------------------------------------------------- laser DXFs (2D)
def dxf(name, face, qty, material):
    exp = ExportDXF(unit=Unit.MM)
    exp.add_layer("cut")
    exp.add_shape(face, layer="cut")
    fn = f"{name}_x{qty}.dxf"
    exp.write(os.path.join(LASER, fn))
    rows.append({"file": "laser/" + fn, "part": name, "qty": qty, "material": material, "notes": "laser cut"})
    print(fn)


for n, ((x0, x1), mounts) in TILES.items():
    y0, y1 = (-90.0, 90.0) if n == "tile_T4" else TILE_Y
    f = Pos((x0 + x1) / 2, (y0 + y1) / 2) * Rectangle(x1 - x0, y1 - y0)
    for (x, y) in grid_points(x0, x1, y0, y1, mounts):
        f -= Pos(x, y) * Circle(1.65)
    for (x, y, rail) in mounts:
        if rail is not None:
            f -= Pos(x, y) * Circle(2.75)
    dxf(n + "_3mm", Pos(-(x0 + x1) / 2, -(y0 + y1) / 2) * f, 1,
        "3 mm aluminium (tap M3 where needed) or 3 mm plywood/acrylic (use M3 standoffs + 2 mm washers under the mount holes)")
for L in (45, 70, 95):
    top = Pos(L / 2, 0) * Rectangle(L, 20)
    top -= Pos(L / 2, 0) * SlotOverall(L - 24, 3.2)
    bot = Pos(L / 2, 0) * Rectangle(L, 20)
    bot -= Pos(L / 2 - 4.5, 0) * Rectangle(L - 9, 6.2)               # nut channel, open at one end
    for xe in (5.0, L - 5.0):
        top -= Pos(xe, 0) * SlotOverall(GRID + 3.4, 3.4, rotation=90)
        bot -= Pos(xe, 0) * SlotOverall(GRID + 3.4, 3.4, rotation=90)
    q = {45: 4, 70: 2, 95: 4}[L]
    dxf(f"slot_strip_{L}_top_layer", top, q, "3 mm plywood/acrylic/aluminium; glue on the bottom layer")
    dxf(f"slot_strip_{L}_bottom_layer", bot, q, "3 mm plywood/acrylic (nut channel layer)")
f = Circle(ADAPTER_D / 2) - RegularPolygon((HEX_AF + 0.1) / math.sqrt(3), 6)
for a in (-150, -90, -30, 30, 90, 150):
    t = math.radians(a)
    f -= Pos(RS_OUT_PCD / 2 * math.cos(t), RS_OUT_PCD / 2 * math.sin(t)) * Circle(1.7)
dxf("hex_adapter_6mm_aluminium", f, 2, "6 mm aluminium (M3x10 into the RS00 output, no counterbore)")

json.dump(rows, open(os.path.join(OUT, "make_parts.json"), "w"), indent=1)
print(len(rows), "files")
