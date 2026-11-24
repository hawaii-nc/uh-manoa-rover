import json
man = json.load(open("render/v5.json")); NAMES = [m["name"] for m in man]
GREY, CTX, RED, ORANGE, BLUE, GREEN, DARK, BAT = "#c9cdd3", "#e4e7eb", "#e74c3c", "#e67e22", "#2e86c1", "#1a8c6b", "#2c2f36", "#34495e"
SRC = "render/v5"


def base(n):
    return n.split(":", 1)[1].split("#")[0]


def pick(*prefixes, kinds=("k", "n")):
    return [n for n in NAMES if n.split(":", 1)[0] in kinds and base(n).startswith(prefixes)]


FRAME = pick("rail_", "bracket_")
BODY = pick("pb:tub", "pb:hood", "hood_", "inlet_", "hinge", "latch", "keeper", "tub_exhaust", "tub_intake")
FLOOR = pick("pb:floor")
FRONT = pick("front_wheel", "knuckle", "c_hub", "servo_left", "servo_right", "servo_horn", "stub_axle", "kingpin", "pushrod", "roll_stop",
             "front_axle_beam", "pivot_hub", "pivot_rod", "rod_", "pivot_bearing", "pb:bumper", "pb:roller", "bumper_")
REAR_KEEP = pick("pivot_bracket_rear", "shock_rear", "fender_rear", "gland_")
GREEN_PARTS = pick("shock_tower", "deck_post")
DECK = pick("deck_beam", "pb:tote_guide")
TOTE = pick("pb:tote_544")
DRIVE_L = pick("drive_arm_left", "rs00_left", "hex_adapter_left", "bearing_in_left", "bearing_out_left", "collar_in_left", "collar_out_left",
               "wheel_insert_in_left", "wheel_insert_out_left", "wheel_left", "shaft_left")
DRIVE = DRIVE_L + pick("drive_arm_right", "rs00_right", "hex_adapter_right", "bearing_in_right", "bearing_out_right", "collar_in_right",
                       "collar_out_right", "wheel_insert_in_right", "wheel_insert_out_right", "wheel_right", "shaft_right")
TILES = pick("tile_")
CRADLE = pick("battery_", "batt_stop", "strap")
STRIPS = pick("strip_")
ELEC_NEW = pick("xt90s", "fuse_", "ssr_", "wago_", "pololu", "canable", "pca9685", "can_term")
ELEC_MOVED = pick("raspberry_pi_5", "imu_bno085", "level_shifters", "servo_fuse")
ELEC_KEEP = pick("usb_hub", "bay_fan", "fan_plate", "estop", "hood_estop", "cam_", "hood_stereo", "hood_pod", "charge_socket")
REMOVED = [n for n in NAMES if n.startswith("r:")]


def items(groups, fit=None, offsets=None):
    out, seen = [], set()
    for names, color, op in groups:
        for n in names:
            if n in seen:
                continue
            seen.add(n)
            it = {"src": SRC, "name": n, "color": color, "opacity": op, "fit": (fit is None or n in fit)}
            if offsets and n in offsets:
                it["offset"] = offsets[n]
            out.append(it)
    return out


def spec(out, title, groups, cam, fit=None, labels=(), offsets=None, w=1500, h=1000):
    return {"out": out, "w": w, "h": h, "title": title, "items": items(groups, fit, offsets), "cam": cam,
            "labels": [{"text": t, "at": a} for t, a in labels]}


AX = (-220.0, 76.2)
specs = []
# ---- R1: what comes out
specs.append(spec("step01_strip.png", "Step 1. Take out the red parts (v4.4). Keep everything grey.",
                  [(REMOVED, RED, 1), (FRAME + FRONT + REAR_KEEP + GREEN_PARTS + ELEC_MOVED + ELEC_KEEP + pick("usb_hub"), GREY, 1), (FLOOR, CTX, 0.5)],
                  {"dir": [-0.55, -0.65, 0.65], "zoom": 0.62}, fit=set(FLOOR + REMOVED),
                  labels=[("hub motors + swing arms + clamp caps", [-220, -300, 120]), ("ZS-X11H drivers + plates", [-190, -130, 200]),
                          ("phone + tray", [195, 0, 185]), ("36 V pack + cradle", [65, 0, 220]), ("charger + bridge + C14 inlet", [-300, 0, 200]),
                          ("Pico, ESP32, RF kill, old regulators", [-150, -40, 175]), ("servo power plate + 50 V regulators", [295, -40, 175])]))
# ---- D1..D3: drive module on the bench (left side)
bench_cam = {"dir": [-0.35, 0.85, 0.45], "zoom": 0.85}
d_arm = pick("drive_arm_left"); d_brg = pick("bearing_in_left", "bearing_out_left")
d_mot = pick("rs00_left", "hex_adapter_left"); d_rest = pick("shaft_left", "collar_in_left", "collar_out_left", "wheel_insert_in_left", "wheel_insert_out_left", "wheel_left")
specs.append(spec("step02_arm_bearings.png", "Step 2. Drive arm: press in the bearings (left shown, right is the mirror)",
                  [(d_arm, ORANGE, 1), (d_brg, BLUE, 1)], {"dir": [0.45, -0.8, 0.4], "zoom": 0.9},
                  labels=[("2x 608ZZ into the pivot boss (one each side)", [-290, 204, 102]), ("1/2 in hex flanged bearing, flange outside", [-220, 204, 96]),
                          ("motor plate: 5 M3 holes (6th is under the shock mount)", [-245, 165, 60]), ("shock clevis", [-195, 186, 90])]))
specs.append(spec("step03_motor.png", "Step 3. RS00 on the arm: 5x M3x12 from the wheel side, connector up. Then the hex adapter: 6x M3x14",
                  [(pick("hex_adapter_left"), ORANGE, 1), (d_mot, DARK, 1), (d_arm, GREY, 0.35), (d_brg, GREY, 0.35)],
                  {"dir": [-0.55, 0.7, 0.45], "zoom": 0.8},
                  labels=[("RS00, connector up", [-220, 120, 105]), ("M3x12 (5x) into the RS00 ring, from this side", [-242, 174, 89]),
                          ("hex adapter on the output boss, M3x14 (6x)", [-220, 178, 92])],
                  offsets={"n:hex_adapter_left": [0, 0, 0]}))
specs.append(spec("step04_wheel.png", "Step 4. Shaft (1/2 in hex, cut to 144 mm), collars, wheel with printed hex insert",
                  [(d_arm + d_brg + d_mot, GREY, 1), (pick("shaft_left"), BLUE, 1), (pick("collar_in_left", "collar_out_left"), BLUE, 1),
                   (pick("wheel_insert_in_left", "wheel_insert_out_left"), ORANGE, 1), (pick("wheel_left"), BAT, 1)],
                  {"dir": [-0.45, 0.8, 0.4], "zoom": 0.9},
                  labels=[("inner collar: slide on between adapter and bearing", [-220, 198, 92]),
                          ("Colson 6x1.5 + 2 printed inserts, 4x M4x50 through drilled holes", [-220, 278, 152]),
                          ("outer collar", [-220, 305, 90]), ("shaft end seats in the adapter", [-220, 170, 70])],
                  offsets={"n:wheel_left": [0, 40, 0], "n:wheel_insert_in_left": [0, 25, 0], "n:wheel_insert_out_left": [0, 55, 0], "n:collar_out_left": [0, 75, 0]}))
# ---- R2: install the drive modules
specs.append(spec("step05_install_drive.png", "Step 5. Bolt each drive module to its pivot bracket (M8x50) and refit the shock (M3x35 self-tapping)",
                  [(DRIVE, ORANGE, 1), (pick("rs00_left", "rs00_right"), DARK, 1), (pick("wheel_left", "wheel_right"), BAT, 1),
                   (FRAME + REAR_KEEP + pick("shock_tower_rear"), GREY, 1), (FLOOR, CTX, 0.35)],
                  {"dir": [-0.75, -0.45, -0.35], "zoom": 0.75}, fit=set(DRIVE + pick("pivot_bracket_rear", "shock_tower_rear")),
                  labels=[("M8x50 pivot through 2x 608ZZ", [-290, -221, 102]), ("lower shock eye, M3x35", [-195, -186, 85]),
                          ("motor cable up through the rear gland", [-250, -160, 149]), ("RS00", [-220, -140, 60])]))
# ---- R3: bay tiles
specs.append(spec("step06_tiles.png", "Step 6. Grid tiles: M5 through the existing floor holes (T-nut where a rail is under, nyloc nut from below elsewhere)",
                  [(TILES, ORANGE, 1), (FRAME + FLOOR, GREY, 1), (GREEN_PARTS + REAR_KEEP, CTX, 1)],
                  {"dir": [-0.45, -0.55, 0.75], "zoom": 0.62}, fit=set(TILES + FLOOR),
                  labels=[("T1 (rail T-nuts)", [-210, 0, 160]), ("T2", [-20, 0, 160]), ("T3 (nuts under the floor)", [180, 0, 160]), ("T4", [304, 0, 160]),
                          ("rail under: M5x14 + T-nut", [-280, 76, 160]), ("no rail: M5x16 + nyloc from below", [116, 60, 160])]))
# ---- R4: green structure that carries the deck and the tote
specs.append(spec("step07_green_towers_posts.png", "Step 7. The green parts that carry the deck: 4 shock towers + 4 deck posts, M5 into T-nuts in the rails",
                  [(GREEN_PARTS, GREEN, 1), (FRAME + FLOOR, GREY, 1), (TILES, CTX, 1)],
                  {"dir": [-0.5, -0.6, 0.6], "zoom": 0.62}, fit=set(FLOOR + GREEN_PARTS),
                  labels=[("shock tower (rear): foot 2x M5x14 into the long rail", [-172, -224, 160]), ("tower top: M5x10 into the deck beam", [-210, -172, 242]),
                          ("deck post: foot M5x18 into the cross rail", [-130, -170, 162]), ("post top: M5x12 into the deck beam", [-130, -130, 242]),
                          ("front tower", [172, -224, 160]), ("front post", [60, -180, 162])]))
specs.append(spec("step08_deck_exploded.png", "Step 8. Deck beams sit on the tower and post tops; the tote sits in the 4 tote guides on the beams",
                  [(GREEN_PARTS, GREEN, 1), (pick("deck_beam"), GREY, 1), (pick("pb:tote_guide"), ORANGE, 1), (TOTE, "#5d8fc9", 0.45), (FRAME, CTX, 1)],
                  {"dir": [-0.55, -0.65, 0.5], "zoom": 0.75},
                  offsets={**{n: [0, 0, 60] for n in pick("deck_beam")}, **{n: [0, 0, 110] for n in pick("pb:tote_guide")}, **{n: [0, 0, 230] for n in TOTE}},
                  labels=[("2020 deck beam x4 (360 mm), 2 T-nuts each in the bottom slot", [-130, 0, 312]),
                          ("tote guide x4", [-248, 173, 400]), ("tote (the bag): lifts straight out", [0, 0, 720])]))
# ---- R5: battery cradle
specs.append(spec("step09_battery.png", "Step 9. Battery: slide the 4 stops up to the pack, then 2 straps through the strap loops",
                  [(pick("battery_"), BAT, 1), (pick("batt_stop", "strap_loop"), ORANGE, 1), (pick("strap_"), "#c0392b", 1), (TILES, GREY, 1)],
                  {"dir": [-0.5, -0.7, 0.6], "zoom": 0.9},
                  labels=[("any 13S pack up to 300 x 150 x 78 mm", [-170, 0, 230]), ("end stop (slots: +-8 mm)", [-313, 0, 200]),
                          ("side stop", [-150, -65, 200]), ("strap loop", [-250, -61, 172]), ("25 mm cam strap", [-85, 0, 232])]))
# ---- R6: electronics
specs.append(spec("step10_electronics.png", "Step 10. Electronics on slot strips (any board: slide the strips to its holes)",
                  [(ELEC_NEW, BLUE, 1), (ELEC_MOVED, "#1f618d", 1), (STRIPS, ORANGE, 1), (pick("tile_T2", "tile_T3", "tile_T4"), GREY, 1), (pick("battery_"), CTX, 1)],
                  {"dir": [-0.35, -0.55, 0.8], "zoom": 0.75}, fit=set(pick("tile_T2", "tile_T3", "tile_T4")),
                  labels=[("Raspberry Pi 5", [140, 50, 190]), ("CANable (USB-CAN)", [125, -11, 176]), ("PCA9685 (servo PWM)", [227, 57, 180]),
                          ("74AHCT125", [218, 20, 172]), ("BNO085 IMU", [45, 39, 172]), ("SSR (e-stop cut)", [128, -75, 190]),
                          ("WAGO: +48 always / switched / GND", [180, -65, 169]), ("5 V regulator (Pi)", [221, -86, 178]),
                          ("7.5 V regulators (servos)", [304, 35, 178]), ("XT90-S main key", [0, -88, 181]), ("58 V fuses", [56, -73, 172])]))
# ---- overview
specs.append(spec("step11_done.png", "Done: Campus Rover v5 (body see-through)",
                  [(BODY, "#ecebe6", 0.13), (TOTE, "#5d8fc9", 0.25), (DRIVE, ORANGE, 1), (pick("rs00_"), DARK, 1), (pick("wheel_left", "wheel_right"), BAT, 1),
                   (GREEN_PARTS, GREEN, 1), (TILES + STRIPS + pick("batt_stop", "strap_loop"), ORANGE, 1), (pick("battery_"), BAT, 1),
                   (ELEC_NEW + ELEC_MOVED + ELEC_KEEP, BLUE, 1), (FRAME + FRONT + REAR_KEEP + DECK + FLOOR, GREY, 1)],
                  {"dir": [-0.6, -0.7, 0.5], "zoom": 0.58}))
json.dump(specs, open("specs_steps.json", "w"))
print(len(specs), "specs")
