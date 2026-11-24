"""Campus Rover v5: RobStride 00 rear drive, universal bay grid, adjustable battery cradle.

Frame: x forward, y left, z up, mm, ground z=0 (same as the v4.4 STEP).
Every number that came from somewhere is commented with its source.
"""
import math, os, sys, json
from build123d import (Box, Cylinder, Polyline, RegularPolygon, Axis, Pos, Location, Compound,
                       extrude, make_face, import_step, Solid, Vector, Plane, Sketch, Rectangle, Circle,
                       SlotOverall, Locations, mirror, Plane as P)

HERE = os.path.dirname(os.path.abspath(__file__))
SCR = os.environ.get("ROVER_V5_WORK", os.path.expanduser("~/rover_v5_work"))  # holds step_dl/ and gh/ (see README)
RS00_STEP = os.path.join(SCR, "gh/RobStride_Product_Information/Product Literature/CAN2.0/RS00/RS00-20261117.stp")

# ---------------------------------------------------------------- parameters
# From the v4.4 STEP (measured with cyl_report.py)
PIVOT = (-290.0, 102.0)          # swing-arm pivot axis (x, z), M8, two 608ZZ
PIVOT_Y = (204.0, 238.0)         # arm pivot boss between the bracket cheeks
SHOCK_EYE = (-195.0, 85.0)       # lower shock eye (x, z), eye 7.6 wide at y 182.2..189.8
SHOCK_Y = 186.0
WHEEL_C_Y = 275.5                # old hub-motor tyre centre plane (tyre y 245.5..305.5)
# Wheel: 6 in x 1.5 in with 1/2 in hex bore (Colson Performa style)
WHEEL_D, WHEEL_W = 152.4, 38.1
AX = (-220.0, WHEEL_D / 2)       # new axle: same x as v4.4, z so the tyre sits on z=0
# RS00 (official STEP, RobStride/Product_Information RS00-20261117.stp)
RS_OD, RS_LEN = 57.0, 52.1       # local z -6 (output boss) .. 46.1 (back)
RS_RING_PCD, RS_OUT_PCD = 50.0, 27.0   # fixed ring 6x M3 (5 deep), output 6x M3 (6 deep)
RS_BOSS_D = 34.6                 # output boss protrudes 1 mm past the ring face
# 1/2 in hex FRC hardware (check your supplier's drawing)
HEX_AF = 12.7
BRG_OD, BRG_W, BRG_FL_D, BRG_FL_T = 28.575, 7.94, 31.75, 1.6
COLLAR_OD, COLLAR_W = 28.6, 9.5
# Layout along y (left side), chosen here
Y_MOTOR_FACE = 165.0             # RS00 fixed-ring face = inboard face of the motor plate
PLATE_T = 8.0
Y_BLOCK = (206.0, 252.0)         # bearing block
ADAPTER_D, ADAPTER_T = 32.0, 12.0
# Bay
FLOOR_TOP = 153.1
TILE_T = 5.0
TILE_FEET = 2.0                  # pads under the tile so screws may poke 2 mm past the captured nuts
TILE_TOP = 153.1 + 2.0 + 5.0
GRID = 15.0
DECK_BEAM_BOTTOM = 242.0


def box(x0, x1, y0, y1, z0, z1):
    return Pos((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2) * Box(x1 - x0, y1 - y0, z1 - z0)


def cyl_y(cx, cz, r, y0, y1):
    c = Cylinder(r, y1 - y0).rotate(Axis.X, -90)
    return Pos(cx, (y0 + y1) / 2, cz) * c


def cyl_x(x0, x1, y, z, r):
    return Pos((x0 + x1) / 2, y, z) * Cylinder(r, x1 - x0).rotate(Axis.Y, 90)


def cyl_z(cx, cy, r, z0, z1):
    return Pos(cx, cy, (z0 + z1) / 2) * Cylinder(r, z1 - z0)


def xz_prism(pts, y0, y1):
    f = make_face(Polyline(*pts, close=True))
    s = extrude(f, amount=y1 - y0).rotate(Axis.X, 90)
    return Pos(0, y1, 0) * s


def hex_y(cx, cz, af, y0, y1):
    f = RegularPolygon(af / math.sqrt(3), 6)  # circumradius
    s = extrude(f, amount=y1 - y0).rotate(Axis.X, -90)
    return Pos(cx, y0, cz) * s


def mirror_y(shape):
    return mirror(shape, about=P.XZ)


# ---------------------------------------------------------------- RS00 placement
def rs00_left():
    """Official model, output facing +y, connector up, fixed-ring face at Y_MOTOR_FACE."""
    rs = import_step(RS00_STEP)
    s = rs.rotate(Axis.X, 90).rotate(Axis.Y, -90)        # local z -> -y, local x -> +z
    return Pos(AX[0], Y_MOTOR_FACE - 5.0, AX[1]) * s       # local z=-5 (ring face) -> Y_MOTOR_FACE


def rs00_right():
    rs = import_step(RS00_STEP)
    s = rs.rotate(Axis.X, -90).rotate(Axis.Y, -90)       # local z -> +y, local x -> +z
    return Pos(AX[0], -(Y_MOTOR_FACE - 5.0), AX[1]) * s


def ring_xz(pcd, angles_deg):
    """RS00 local hole angles -> global (x, z) for the placement above (local x->z, local y->-x)."""
    out = []
    for a in angles_deg:
        t = math.radians(a)
        lx, ly = pcd / 2 * math.cos(t), pcd / 2 * math.sin(t)
        out.append((AX[0] - ly, AX[1] + lx))
    return out


RING_HOLES = ring_xz(RS_RING_PCD, [0, 60, 120, 180, 240, 300])
OUT_HOLES = ring_xz(RS_OUT_PCD, [-150, -90, -30, 30, 90, 150])


# ---------------------------------------------------------------- drive arm (left)
# One printed part: pivot boss, beam, bearing block, shock clevis and the RS00 mounting plate.
RING_USED = [0, 1, 2, 3, 4]          # RING_HOLES[5] sits under the shock clevis and is left empty


def drive_arm_left():
    px, pz = PIVOT
    ax, az = AX
    boss = cyl_y(px, pz, 16, PIVOT_Y[0], PIVOT_Y[1])
    beam = xz_prism([(px, pz + 16), (-262, 112), (-240, az + 20), (-240, az - 20), (-262, 66), (px, pz - 16)], 206, PIVOT_Y[1])
    block = box(ax - 20, ax + 20, Y_BLOCK[0], Y_BLOCK[1], az - 20, az + 20)
    y0, y1 = Y_MOTOR_FACE, Y_MOTOR_FACE + PLATE_T               # motor plate
    plate = cyl_y(ax, az, 33, y0, y1) + box(-262, ax, y0, y1, 48, 100)
    front = box(-202, -188, y1, Y_BLOCK[0], 68, 90)               # clevis rib, top below the shock body (z~92)
    rear = box(-260, -251, y1, Y_BLOCK[0] + 1, 48, 100)
    arm = boss + beam + block + plate + front + rear
    # pivot: M8 through, 608ZZ seats 7 deep each end
    arm -= cyl_y(px, pz, 4.25, PIVOT_Y[0] - 1, PIVOT_Y[1] + 1)
    arm -= cyl_y(px, pz, 11.05, PIVOT_Y[0] - 1, PIVOT_Y[0] + 7)
    arm -= cyl_y(px, pz, 11.05, PIVOT_Y[1] - 7, PIVOT_Y[1] + 1)
    # bearings: flanged 1/2 in hex bearings pressed in from each end; shaft clearance through
    arm -= cyl_y(ax, az, BRG_OD / 2 + 0.1, Y_BLOCK[0] - 1, Y_BLOCK[0] + BRG_W)
    arm -= cyl_y(ax, az, BRG_OD / 2 + 0.1, Y_BLOCK[1] - BRG_W, Y_BLOCK[1] + 1)
    arm -= cyl_y(ax, az, 9.0, Y_BLOCK[0] - 1, Y_BLOCK[1] + 1)
    # motor plate: output boss + adapter pass through; 5x M3x12 into the RS00 ring
    arm -= cyl_y(ax, az, 18, y0 - 1, y1 + 1)
    for i in RING_USED:
        x, z = RING_HOLES[i]
        arm -= cyl_y(x, z, 1.7, y0 - 1, y1 + 1)
    # shock clevis: slot for the eye; M3x35 self-tapping from the outboard face (3.4 clear, 2.5 pilot)
    ex, ez = SHOCK_EYE
    arm -= box(-214, -176, SHOCK_Y - 4.5, SHOCK_Y + 4.5, ez - 8, 140)
    arm -= cyl_y(ex, ez, 1.7, SHOCK_Y + 4.5, Y_BLOCK[0] + 1)
    arm -= cyl_y(ex, ez, 1.25, y1 - 2, SHOCK_Y - 4.5)
    return arm


def hex_adapter_left():
    ax, az = AX
    y0 = Y_MOTOR_FACE + 1.0                                    # sits on the output boss face
    y1 = y0 + ADAPTER_T
    a = cyl_y(ax, az, ADAPTER_D / 2, y0, y1)
    a -= hex_y(ax, az, HEX_AF + 0.15, y0 + 1.0, y1 + 1)       # blind hex pocket, 1 mm floor
    for (x, z) in OUT_HOLES:                                   # 6x M3x14, heads sunk 3.5
        a -= cyl_y(x, z, 1.7, y0 - 1, y1 + 1)
        a -= cyl_y(x, z, 3.1, y1 - 3.5, y1 + 1)
    return a


# Colson Performa 6 x 1.5 has a 1-3/16 in (30.16 mm) plain bore (WCP-1536): a printed two-piece hex insert,
# clamped through the wheel core by 4x M4 on a 45 mm circle, carries the torque.
INS_BORE = 30.16
INS_FL_IN, INS_FL_OUT, INS_FL_D, INS_PCD = 5.0, 4.0, 56.0, 45.0


def wheel_insert_halves_left(y_start):
    ax, az = AX
    y_w0 = y_start + INS_FL_IN
    y_w1 = y_w0 + WHEEL_W
    hole = hex_y(ax, az, HEX_AF + 0.15, y_start - 1, y_w1 + INS_FL_OUT + 1)
    inner = cyl_y(ax, az, INS_FL_D / 2, y_start, y_w0) + cyl_y(ax, az, INS_BORE / 2 - 0.1, y_w0, y_w0 + WHEEL_W / 2 - 0.2)
    outer = cyl_y(ax, az, INS_FL_D / 2, y_w1, y_w1 + INS_FL_OUT) + cyl_y(ax, az, INS_BORE / 2 - 0.1, y_w1 - WHEEL_W / 2 + 0.2, y_w1)
    inner -= hole; outer -= hole
    for k in range(4):
        t = math.radians(45 + 90 * k)
        x, z = ax + INS_PCD / 2 * math.cos(t), az + INS_PCD / 2 * math.sin(t)
        inner -= cyl_y(x, z, 2.25, y_start - 1, y_w0 + 1)
        inner -= cyl_y(x, z, 4.0, y_start - 1, y_start + 4.2)          # M4 SHCS head sunk flush
        outer -= cyl_y(x, z, 2.25, y_w1 - 1, y_w1 + INS_FL_OUT + 1)
    return inner, outer, y_w0, y_w1


def axle_stack_left():
    """Shaft, bearings, collars, insert, wheel for the left side."""
    ax, az = AX
    parts = {}
    y_ad1 = Y_MOTOR_FACE + 1.0 + ADAPTER_T
    b_in = cyl_y(ax, az, BRG_OD / 2, Y_BLOCK[0], Y_BLOCK[0] + BRG_W) + cyl_y(ax, az, BRG_FL_D / 2, Y_BLOCK[0] - BRG_FL_T, Y_BLOCK[0])
    b_out = cyl_y(ax, az, BRG_OD / 2, Y_BLOCK[1] - BRG_W, Y_BLOCK[1]) + cyl_y(ax, az, BRG_FL_D / 2, Y_BLOCK[1], Y_BLOCK[1] + BRG_FL_T)
    hole = hex_y(ax, az, HEX_AF, 0, 400)
    parts["bearing_in"] = b_in - hole
    parts["bearing_out"] = b_out - hole
    y_c0 = Y_BLOCK[0] - BRG_FL_T - 0.5 - COLLAR_W
    parts["collar_in"] = cyl_y(ax, az, COLLAR_OD / 2, y_c0, y_c0 + COLLAR_W) - hole
    y_s = Y_BLOCK[1] + BRG_FL_T
    ins_in, ins_out, y_w0, y_w1 = wheel_insert_halves_left(y_s)
    parts["wheel_insert_in"] = ins_in
    parts["wheel_insert_out"] = ins_out
    tyre = cyl_y(ax, az, WHEEL_D / 2, y_w0, y_w1) - cyl_y(ax, az, 57, y_w0 - 1, y_w1 + 1)
    core = cyl_y(ax, az, 57.5, y_w0, y_w1) - cyl_y(ax, az, INS_BORE / 2, y_w0 - 1, y_w1 + 1)
    for k in range(4):
        t = math.radians(45 + 90 * k)
        core -= cyl_y(ax + INS_PCD / 2 * math.cos(t), az + INS_PCD / 2 * math.sin(t), 2.25, y_w0 - 1, y_w1 + 1)
    parts["wheel"] = tyre + core
    y_c1 = y_w1 + INS_FL_OUT
    parts["collar_out"] = cyl_y(ax, az, COLLAR_OD / 2, y_c1, y_c1 + COLLAR_W) - hole
    parts["shaft"] = hex_y(ax, az, HEX_AF, Y_MOTOR_FACE + 2.0, y_c1 + COLLAR_W + 1.0)
    shaft_len = (y_c1 + COLLAR_W + 1.0) - (Y_MOTOR_FACE + 2.0)
    return parts, shaft_len, y_ad1


# ---------------------------------------------------------------- bay grid tiles
# Mount holes reuse holes already in the printed floor (printed_body_v4_5_1 floor tiles).
# rail=True: a 2020 cross rail is under the hole -> M5 into a T-nut; else M5 + nyloc from below.
TILES = {
    "tile_T1": ((-300, -120), [(-280, 76, True), (-280, -76, True), (-130, 90, True), (-130, -90, True)]),
    "tile_T2": ((-120, 80), [(-43, 92, False), (-43, -92, False), (60, 82, True), (60, -82, True)]),
    "tile_T3": ((80, 280), [(116, 60, False), (116, -60, False), (263, 72, False), (263, -72, False)]),
    "tile_T4": ((280, 328), [(305, 72, True), (305, -72, True)]),
}
TILE_Y = (-100.0, 100.0)


def grid_points(x0, x1, y0, y1, mounts, pitch=GRID):
    pts = []
    nx = int((x1 - x0 - 10) // pitch) + 1
    ny = int((y1 - y0 - 10) // pitch) + 1
    ox = x0 + ((x1 - x0) - (nx - 1) * pitch) / 2
    oy = y0 + ((y1 - y0) - (ny - 1) * pitch) / 2
    for i in range(nx):
        for j in range(ny):
            x, y = ox + i * pitch, oy + j * pitch
            if any(math.hypot(x - mx, y - my) < 9.5 for mx, my, _ in mounts):
                continue
            pts.append((x, y))
    return pts


def tile(name, for_print=False):
    (x0, x1), mounts = TILES[name]
    y0, y1 = TILE_Y if name != "tile_T4" else (-90.0, 90.0)
    zf = FLOOR_TOP
    z0, z1 = FLOOR_TOP + TILE_FEET, FLOOR_TOP + TILE_FEET + TILE_T
    t = box(x0, x1, y0, y1, z0, z1)
    # feet: a 6 mm rim plus a pad round every mount hole
    t += box(x0, x1, y0, y0 + 6, zf, z0) + box(x0, x1, y1 - 6, y1, zf, z0)
    t += box(x0, x0 + 6, y0, y1, zf, z0) + box(x1 - 6, x1, y0, y1, zf, z0)
    for (x, y, rail) in mounts:
        if rail is not None:
            t += cyl_z(x, y, 7.5, zf, z0)
    for (x, y) in grid_points(x0, x1, y0, y1, mounts):
        t -= cyl_z(x, y, 1.65, zf - 1, z1 + 1)
        if for_print:   # M3 nut pocket from below, 2.6 deep (push the nut in before fitting the tile)
            t -= Pos(x, y, z0 - 0.01) * extrude(RegularPolygon(5.6 / math.sqrt(3) + 0.15, 6), amount=2.6)
    for (x, y, rail) in mounts:
        if rail is None:
            continue
        t -= cyl_z(x, y, 2.75, zf - 1, z1 + 1)
        t -= cyl_z(x, y, 4.6, z1 - 3.0, z1 + 1)                 # M5 head sunk 3
    return t


# ---------------------------------------------------------------- battery cradle (adjustable)
def stop_block(x, y, facing, h=46.0):
    """L-shaped stop. facing = direction from stop to battery (+x, -x, +y, -y). Slots +-8 mm."""
    z0 = TILE_TOP
    base_l, base_w, wall = 34.0, 44.0, 6.0
    if facing in ("+x", "-x"):
        s = box(-base_l / 2, base_l / 2, -base_w / 2, base_w / 2, 0, 4)
        sx = 1 if facing == "+x" else -1
        s += box(sx * base_l / 2 - (wall if sx > 0 else 0), sx * base_l / 2 + (0 if sx > 0 else wall), -base_w / 2, base_w / 2, 0, h)
        for yy in (-12, 12):
            s -= Pos(-sx * 3, yy, 2) * extrude(SlotOverall(20, 3.4), amount=10, both=True)
    else:
        s = box(-base_w / 2, base_w / 2, -base_l / 2, base_l / 2, 0, 4)
        sy = 1 if facing == "+y" else -1
        s += box(-base_w / 2, base_w / 2, sy * base_l / 2 - (wall if sy > 0 else 0), sy * base_l / 2 + (0 if sy > 0 else wall), 0, h)
        for xx in (-12, 12):
            s -= Pos(xx, -sy * 3, 2) * extrude(SlotOverall(20, 3.4, rotation=90), amount=10, both=True)
    return Pos(x, y, z0) * s


def strap_loop(x, y):
    """Arch for a 25 mm strap running along y. Two feet with slotted M3 holes (span 30-45 mm)."""
    z0 = TILE_TOP
    s = box(-26, -14, -12, 12, 0, 4) + box(14, 26, -12, 12, 0, 4)          # feet
    s += box(-18, -14, -5, 5, 0, 12) + box(14, 18, -5, 5, 0, 12)           # posts
    s += box(-18, 18, -5, 5, 8, 12)                                        # bar, strap passes under (28 x 8)
    for xx in (-20, 20):
        s -= Pos(xx, 8.5, 2) * extrude(SlotOverall(10, 3.4, rotation=0), amount=10, both=True)
        s -= Pos(xx, -8.5, 2) * extrude(SlotOverall(10, 3.4, rotation=0), amount=10, both=True)
    return Pos(x, y, z0) * s


def slot_strip_y(y0, y1, x):
    """Same strip running along y."""
    return slot_strip(y0, y1, 0).rotate(Axis.Z, 90).translate((x, 0, 0))


STRIP_T = 5.0


def slot_strip(x0, x1, y):
    """Universal board strip, 20 wide x 5 thick. Top: 3.2 slot for the board screws. Underneath: a 6.2 x 2.6
    channel for M3 nuts (slide them in from the end before fitting). Ends: cross slots for the grid screws."""
    z0 = TILE_TOP
    L = x1 - x0
    s = box(0, L, -10, 10, 0, STRIP_T)
    s -= Pos(L / 2, 0, 2.5) * extrude(SlotOverall(L - 24, 3.2), amount=10, both=True)
    s -= box(9, L - 9, -3.1, 3.1, -0.01, 2.6)
    s -= box(-1, 10, -3.1, 3.1, -0.01, 2.6)          # nut entry from the end
    for xe in (5.0, L - 5.0):
        s -= Pos(xe, 0, 2.5) * extrude(SlotOverall(GRID + 3.4, 3.4, rotation=90), amount=10, both=True)
    return Pos(x0, y, z0) * s


if __name__ == "__main__":
    a = drive_arm_left()
    print("arm vol cm3", round(a.volume / 1000, 1), a.bounding_box())
    h = hex_adapter_left(); print("adapter", round(h.volume / 1000, 2), h.bounding_box())
    st, L, _ = axle_stack_left(); print("shaft length", round(L, 1))
    r = rs00_left(); print("rs00 L", r.bounding_box())
    rr = rs00_right(); print("rs00 R", rr.bounding_box())
    for n in TILES:
        t = tile(n); print(n, round(t.volume / 1000, 1), len(grid_points(*TILES[n][0], *TILE_Y, TILES[n][1])), "holes")
