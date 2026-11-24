"""Assemble v5 from the v4.4 mated assembly, check interference and suspension sweep, export."""
import json, math, os, pickle, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build123d import (import_step, Axis, Pos, Box, Cylinder, Compound, Location, export_step, export_stl, Vector)
from design_v5 import *            # noqa: F401,F403
from load_step import leaves

SCR = os.environ.get("ROVER_V5_WORK", os.path.expanduser("~/rover_v5_work"))  # holds step_dl/, gh/ and the outputs in v5/
OUT = os.path.join(SCR, "v5", "out"); os.makedirs(OUT, exist_ok=True)

# Coroplast body (not in the mated v4.4 assembly: the printed body replaced it)
CORO = ("tub_band", "hood_band", "hood_lid", "floor_sheet", "bin_wall", "bin_deck", "former_", "hood_former", "wall_clip",
        "tub_splice", "hood_splice", "hood_seam", "hood_cap", "bin_clip")
REPLACED_BY_PB = {"bumper_block_left", "bumper_block_right", "bumper_switch_left", "bumper_switch_right"}
REMOVE = {
    # hoverboard drive
    "hub_motor_rear_left": "hoverboard hub motor (replaced by RS00 + 6 in hex wheel)",
    "hub_motor_rear_right": "hoverboard hub motor",
    "swing_arm_rear_left": "swing arm (replaced by the RS00 drive arm)",
    "swing_arm_rear_right": "swing arm",
    "clamp_cap_rear_left": "hub-motor axle clamp cap", "clamp_cap_rear_right": "hub-motor axle clamp cap",
    "motor_driver_rear_left": "ZS-X11H driver (RS00 has its own driver)", "motor_driver_rear_right": "ZS-X11H driver",
    "plate_drive_rear_left": "driver plate", "plate_drive_rear_right": "driver plate",
    # not needed for a camera-only rover on a Pi
    "phone_celero5g": "phone", "phone_tray": "phone tray", "raspberry_pi_pico": "Pico (Pi 5 does it all now)",
    "esp32_tracker": "ESP32 tracker", "tracker_regulator_d36v6f5": "tracker regulator", "rf_kill_receiver": "RF kill receiver",
    "gps_bn220": "GPS", "gps_ground_plane": "GPS ground plane", "hood_gps_tray": "GPS tray",
    # power system that cannot take a 48 V pack
    "battery_36v": "36 V pack (any 24-48 V pack now fits the adjustable cradle)",
    "battery_cradle": "fixed battery cradle (replaced by grid + adjustable stops)",
    "charger_42v": "internal 42 V charger (the 48 V pack's own charger is used outside)",
    "rear_bridge": "charger hold-down", "ac_inlet_c14": "AC inlet (replaced by a DC charge socket)",
    "regulator_d36v50f5": "5 V regulator, 50 V max (replaced by D42V55F5, 60 V)",
    "servo_regulator_left": "7.5 V regulator, 50 V max (replaced by D42V55F7, 60 V)",
    "servo_regulator_right": "7.5 V regulator, 50 V max",
    "fuse_and_xt90": "old fuse + XT90 (replaced by 58 V fuses + XT90-S)",
    # fixed plates -> universal grid
    "plate_compute": "fixed compute plate (replaced by grid tiles)", "plate_servo_power": "fixed servo power plate (grid tile)",
}
# moved: name -> (dx, dy, dz)   (new spot = on a slot strip on a grid tile)
Z_ON_STRIP = TILE_TOP + STRIP_T      # top of a slot strip
MOVE = {
    "raspberry_pi_5": (95 - (-194.2), 22 - 15.3, (Z_ON_STRIP + 4.0) - 161.1),      # Pi 5 on 4 mm standoffs
    "imu_bno085": (32 - (-107.8), 28 - (-41.4), (Z_ON_STRIP + 3.0) - 157.1),
    "level_shifters": (196 - (-82.0), 2 - (-80.0), (Z_ON_STRIP + 3.0) - 157.1),
    "servo_fuse": (290 - 278.0, 0.0, TILE_TOP + 1.0 - 157.1),
}
COLORS = {
    "green": "#1a8c6b", "new": "#e67e22", "bought": "#5d6d7e", "elec": "#2e86c1", "battery": "#34495e",
    "frame": "#b8bec7", "body": "#e8e8e3", "rs00": "#2c2f36",
}


def load_v44():
    parts = {}
    for f, pre in [(os.path.join(SCR, "step_dl/rover_v4.step"), ""), (os.path.join(SCR, "step_dl/printed_body_v4_5_1.step"), "pb:")]:
        root = import_step(f)
        for path, shp in leaves(root):
            n = pre + path[-1]
            k, i = n, 2
            while k in parts:
                k = f"{n}#{i}"; i += 1
            parts[k] = shp
    return parts


def base(n):
    return n.split("#")[0]


def electronics():
    """Bought parts that are new in v5, as simple boxes with their real outside sizes (approx. where noted)."""
    z = TILE_TOP
    e = {}
    # power path near the battery (tile T2)
    e["xt90s_main_key"] = box(-30, 30, -98, -77, z, z + 21)                         # XT90-S pair ~60 long
    e["fuse_main_15A_58V"] = box(36, 76, -98, -84, z, z + 12)                      # inline MINI holder (approx.)
    e["fuse_pi_3A_58V"] = box(36, 76, -80, -66, z, z + 12)
    e["fuse_charge_5A_58V"] = box(36, 76, -62, -48, z, z + 12)
    # tile T3: switching, distribution, compute
    e["ssr_crydom_d1d12"] = box(100, 157.2, -97, -52.5, z, z + 30)                 # 2.25 x 1.75 in puck
    e["wago_221_415_48v_always"] = box(165, 195.6, -97, -78, z, z + 8.5)
    e["wago_221_415_48v_switched"] = box(165, 195.6, -74, -55, z, z + 8.5)
    e["wago_221_415_gnd"] = box(165, 195.6, -51, -32, z, z + 8.5)
    e["pololu_d42v55f5_pi"] = box(205, 238, -97, -76, Z_ON_STRIP, Z_ON_STRIP + 10)  # approx. board size
    e["canable_usb_can"] = box(95, 155, -22, 0, Z_ON_STRIP, Z_ON_STRIP + 12)
    e["pca9685_servo_board"] = box(196, 258.2, 45, 70.4, Z_ON_STRIP, Z_ON_STRIP + 12)
    e["can_terminator_120R"] = box(-180, -170, -150, -140, 120, 128)               # at the far (right) motor plug
    # tile T4: servo power next to the steering glands
    e["pololu_d42v55f7_servo_left"] = box(288, 321, 25, 46, Z_ON_STRIP, Z_ON_STRIP + 10)
    e["pololu_d42v55f7_servo_right"] = box(288, 321, -46, -25, Z_ON_STRIP, Z_ON_STRIP + 10)
    e["pca_driver_cable_tie"] = box(0, 0.1, 0, 0.1, z, z + 0.1)
    del e["pca_driver_cable_tie"]
    # charge socket in the old C14 cutout (keeps the rain hood and flap)
    e["charge_socket_plate"] = box(-340.5, -337.5, 48, 102, 210, 240)
    e["charge_socket"] = cyl_x(-337.5, -317.5, 75, 225, 8.0)
    # e-stop: second NC contact block (one cuts the SSR, one tells the Pi)
    e["estop_contact_block_2"] = box(-265, -235, 64, 94, 507.6, 545.6)
    return e


def battery_and_cradle():
    z = TILE_TOP
    L, W, H = 255.0, 95.0, 70.0              # example 13S4P pack (eBay listing 10.04 x 3.74 x 2.76 in)
    x0, x1 = -296.0, -296.0 + L
    p = {"battery_48v_example": box(x0, x1, -W / 2, W / 2, z, z + H)}
    p["batt_stop_rear"] = stop_block(x0 - 17, 0, "+x")
    p["batt_stop_front"] = stop_block(x1 + 17, 0, "-x")
    p["batt_stop_left"] = stop_block(-150, W / 2 + 17, "-y")
    p["batt_stop_right"] = stop_block(-150, -(W / 2 + 17), "+y")
    for i, xs in enumerate((-250.0, -85.0)):
        p[f"strap_loop_{i}L"] = strap_loop(xs, W / 2 + 14)
        p[f"strap_loop_{i}R"] = strap_loop(xs, -(W / 2 + 14))
        strap = box(xs - 12.5, xs + 12.5, -W / 2 - 2, W / 2 + 2, z + H, z + H + 1.5)
        strap += box(xs - 12.5, xs + 12.5, W / 2, W / 2 + 1.5, z + 2, z + H + 1.5)
        strap += box(xs - 12.5, xs + 12.5, -W / 2 - 1.5, -W / 2, z + 2, z + H + 1.5)
        p[f"strap_{i}"] = strap
    return p, (L, W, H)


def strips():
    """Universal slot strips under each board (two per board)."""
    s = {}
    s["strip_pi_a"] = slot_strip(90, 185, 26.5)          # Pi 5 holes 58 x 49
    s["strip_pi_b"] = slot_strip(90, 185, 75.5)
    s["strip_can"] = slot_strip(90, 160, -11)
    s["strip_pca_a"] = slot_strip_y(40, 76, 199.2)       # PCA9685 holes 55.9 x 19
    s["strip_pca_b"] = slot_strip_y(40, 76, 255.1)
    s["strip_ls"] = slot_strip(190, 245, 20)
    s["strip_imu"] = slot_strip(20, 70, 39)
    s["strip_5v"] = slot_strip(200, 245, -86.5)
    s["strip_servoL"] = slot_strip(283, 328, 35.5)
    s["strip_servoR"] = slot_strip(283, 328, -35.5)
    return s


def build_new():
    new = {}
    arm_l = drive_arm_left()
    new["drive_arm_left"] = arm_l
    new["drive_arm_right"] = mirror_y(arm_l)
    ad = hex_adapter_left()
    new["hex_adapter_left"] = ad
    new["hex_adapter_right"] = mirror_y(ad)
    new["rs00_left"] = rs00_left()
    new["rs00_right"] = rs00_right()
    stack, shaft_len, _ = axle_stack_left()
    for k, v in stack.items():
        new[f"{k}_left"] = v
        new[f"{k}_right"] = mirror_y(v)
    for n in TILES:
        new[n] = tile(n)
    bat, dims = battery_and_cradle()
    new.update(bat)
    new.update(strips())
    new.update(electronics())
    return new, shaft_len, dims


def classify(name):
    b = base(name)
    if b.startswith(("drive_arm", "hex_adapter", "tile_", "batt_stop", "strap_loop", "strip_", "charge_socket_plate", "wheel_insert")):
        return "new"
    if b.startswith("rs00"):
        return "rs00"
    if b.startswith(("bearing_", "collar_", "spacer_", "wheel_", "shaft_", "strap_")):
        return "bought"
    if b.startswith("battery"):
        return "battery"
    return None


def interferences(new, kept, tol=2.0):
    """Volume of every overlap between a new part and any other part (bbox prefilter)."""
    items = list(new.items()) + list(kept.items())
    bbs = {n: s.bounding_box() for n, s in items}
    hits = []
    names_new = list(new)
    others = [n for n, _ in items]
    allp = dict(items)
    for i, a in enumerate(names_new):
        ba = bbs[a]
        for b in others:
            if b == a or (b in new and names_new.index(b) <= i if b in names_new else False):
                continue
            bb = bbs[b]
            if (ba.min.X > bb.max.X or bb.min.X > ba.max.X or ba.min.Y > bb.max.Y or bb.min.Y > ba.max.Y or
                    ba.min.Z > bb.max.Z or bb.min.Z > ba.max.Z):
                continue
            try:
                inter = allp[a] & allp[b]
                v = inter.volume if inter is not None else 0.0
            except Exception as ex:  # noqa: BLE001
                v = -1.0
            if v > tol or v < 0:
                hits.append((a, b, round(v, 1)))
    return hits


def sweep(new, kept):
    """Rotate the left drive assembly about the pivot for wheel travel -4..+20 mm."""
    px, pz = PIVOT
    ax, az = AX
    piv = Axis((px, 0, pz), (0, 1, 0))
    moving = {n: new[n] for n in new if n.endswith("_left") and n.startswith(("drive_arm", "hex_adapter", "rs00", "bearing_", "collar_", "wheel", "shaft_"))}
    moving["rs00_left"] = Pos(ax, (Y_MOTOR_FACE - 51.1 + Y_MOTOR_FACE + 1) / 2, az) * Cylinder(28.5, 52.1).rotate(Axis.X, -90)
    static = {n: s for n, s in kept.items() if base(n) in (
        "rail_long_left", "rail_cross_1", "rail_cross_2", "pivot_bracket_rear_left", "shock_tower_rear_left",
        "fender_rear_left", "gland_motor_rear_left", "bracket_1", "bracket_2", "bracket_5", "bracket_6") or base(n).startswith("pb:floor_")}
    r0 = math.hypot(ax - px, az - pz); a0 = math.atan2(az - pz, ax - px)
    out = []
    upper = Vector(-172.4, SHOCK_Y, 213.0)
    for w in (-4, 0, 4, 8, 12, 16, 20):
        a1 = math.asin((math.sin(a0) * r0 + w) / r0)
        ang = -math.degrees(a1 - a0)                                  # rotation about +y (see notes)
        mv = {n: s.rotate(piv, ang) for n, s in moving.items()}
        eye = Vector(SHOCK_EYE[0], SHOCK_Y, SHOCK_EYE[1])
        # rotate the eye point about the pivot
        dx, dz = eye.X - px, eye.Z - pz
        c, s_ = math.cos(math.radians(ang)), math.sin(math.radians(ang))
        e2 = Vector(px + dx * c + dz * s_, SHOCK_Y, pz - dx * s_ + dz * c)
        axv = upper - e2
        length = axv.length
        body_len = length - 12.0                                      # body starts ~9 mm above the eye centre
        mid = e2 + axv.normalized() * (12.0 + body_len / 2)
        tilt = math.degrees(math.atan2(axv.X, axv.Z))
        shock = Pos(mid.X, mid.Y, mid.Z) * Cylinder(13.0, body_len).rotate(Axis.Y, tilt)
        hits = []
        for a, sa in mv.items():
            for b, sb in list(static.items()):
                ba, bb = sa.bounding_box(), sb.bounding_box()
                if (ba.min.X > bb.max.X or bb.min.X > ba.max.X or ba.min.Y > bb.max.Y or bb.min.Y > ba.max.Y or
                        ba.min.Z > bb.max.Z or bb.min.Z > ba.max.Z):
                    continue
                i = sa & sb
                v = i.volume if i is not None else 0
                if v > 2: hits.append((a, b, round(v, 1)))
            i = sa & shock
            v = i.volume if i is not None else 0
            if v > 2 and not a.startswith("drive_arm"): hits.append((a, "shock body", round(v, 1)))
        # arm vs shock: only the body above the eye matters
        i = mv["drive_arm_left"] & shock
        v = i.volume if i is not None else 0
        if v > 2: hits.append(("drive_arm_left", "shock body", round(v, 1)))
        low = min(s.bounding_box().min.Z for s in mv.values())
        top_motor = mv["rs00_left"].bounding_box().max.Z
        out.append({"wheel_travel_mm": w, "arm_rotation_deg": round(ang, 2), "shock_eye_to_eye_mm": round(length, 1),
                    "lowest_point_z": round(low, 1), "motor_top_z": round(top_motor, 1), "hits": hits})
        print("sweep", w, round(ang, 2), round(length, 1), hits)
    return out


def export_all(kept, new, removed):
    from build123d import Color
    from meshes import write
    # 1) meshes for the renderer (kept + new + removed, names prefixed)
    allp = {}
    for n, sh in kept.items(): allp["k:" + n] = sh
    for n, sh in new.items(): allp["n:" + n] = sh
    for n, sh in removed.items(): allp["r:" + n] = sh
    write(allp, os.path.join(SCR, "v5", "render", "v5"))
    # 2) assembly STEP: one labelled solid per part
    cols = {"new": (0.90, 0.49, 0.13), "rs00": (0.17, 0.18, 0.21), "bought": (0.36, 0.43, 0.49), "battery": (0.20, 0.29, 0.37)}
    kids = []
    for n, sh in list(kept.items()) + list(new.items()):
        try:
            c = sh if isinstance(sh, Compound) else Compound([sh])
            c = Compound(c.solids()) if c.solids() else c
            c.label = n.replace("pb:", "").replace("#", "_")
            cl = classify(n)
            c.color = Color(*cols[cl]) if cl in cols else Color(0.75, 0.77, 0.80)
            kids.append(c)
        except Exception as ex:  # noqa: BLE001
            print("skip in STEP", n, ex)
    asm = Compound(children=kids); asm.label = "Campus Rover v5"
    export_step(asm, os.path.join(OUT, "campus_rover_v5.step"))
    print("STEP written", len(kids), "parts")


if __name__ == "__main__":
    t0 = time.time()
    v44 = load_v44()
    print("loaded", len(v44), round(time.time() - t0, 1), "s")
    kept, removed = {}, {}
    for n, s in v44.items():
        b = base(n)
        if (not b.startswith("pb:") and (b.startswith(CORO) or b in REPLACED_BY_PB)):
            continue
        if b in REMOVE:
            removed[n] = s; continue
        if b in MOVE:
            dx, dy, dz = MOVE[b]
            s = Pos(dx, dy, dz) * s
        kept[n] = s
    new, shaft_len, bdims = build_new()
    print("kept", len(kept), "removed", len(removed), "new", len(new), round(time.time() - t0, 1), "s")
    moved = {n: kept[n] for n in kept if base(n) in MOVE}
    check_new = dict(new); check_new.update(moved)
    kept_static = {n: s for n, s in kept.items() if n not in moved}
    hits = interferences(check_new, kept_static)
    print("static hits:", hits)
    sw = sweep(new, kept)
    # battery envelope: the biggest pack the cradle is meant for, at the same rear stop
    env = box(-296, -296 + 300, -75, 75, TILE_TOP, TILE_TOP + 78)
    env_hits = []
    for n, s in kept_static.items():
        bb = s.bounding_box(); be = env.bounding_box()
        if bb.min.X > be.max.X or be.min.X > bb.max.X or bb.min.Y > be.max.Y or be.min.Y > bb.max.Y or bb.min.Z > be.max.Z or be.min.Z > bb.max.Z:
            continue
        i = env & s
        v = i.volume if i is not None else 0
        if v > 2: env_hits.append((n, round(v, 1)))
    print("battery envelope 300x150x78 hits:", env_hits)
    report = {"removed": {base(n): REMOVE[base(n)] for n in removed}, "moved": list(MOVE), "new": sorted(new),
              "shaft_length_mm": round(shaft_len, 1), "battery_example_mm": bdims,
              "static_interference": hits, "suspension_sweep": sw, "battery_envelope_hits": env_hits,
              "deck_clearance_above_tiles_mm": round(DECK_BEAM_BOTTOM - TILE_TOP, 1)}
    json.dump(report, open(os.path.join(OUT, "checks.json"), "w"), indent=1)
    export_all(kept, new, removed)
    print("done", round(time.time() - t0, 1), "s")
