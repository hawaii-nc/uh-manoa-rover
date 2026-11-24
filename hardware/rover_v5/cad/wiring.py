"""Wiring diagram for Campus Rover v5 as SVG (+ PNG via Chromium)."""
import os, sys
W, H = 2000, 1200
C = {"b48": "#d62728", "sw48": "#ff7f0e", "gnd": "#111111", "v5": "#8e44ad", "v75": "#e377c2", "v33": "#17becf",
     "can": "#1f77b4", "sig": "#2ca02c", "usb": "#7f7f7f"}
out = []


def block(x, y, w, h, title, lines=(), fill="#f7f9fb", stroke="#34495e"):
    out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{fill}" stroke="{stroke}" stroke-width="2"/>')
    out.append(f'<text x="{x + 10}" y="{y + 24}" font-size="17" font-weight="700">{title}</text>')
    for i, t in enumerate(lines):
        out.append(f'<text x="{x + 10}" y="{y + 46 + i * 19}" font-size="14" fill="#333">{t}</text>')


def wire(pts, color, label=None, lpos=None, width=4, dash=None):
    d = "M " + " L ".join(f"{x},{y}" for x, y in pts)
    da = f' stroke-dasharray="{dash}"' if dash else ""
    out.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}" stroke-linejoin="round"{da}/>')
    if label:
        lx, ly = lpos if lpos else ((pts[0][0] + pts[1][0]) / 2, (pts[0][1] + pts[1][1]) / 2 - 6)
        out.append(f'<text x="{lx}" y="{ly}" font-size="13" fill="{color}" font-weight="600">{label}</text>')


def dot(x, y, color):
    out.append(f'<circle cx="{x}" cy="{y}" r="6" fill="{color}"/>')


# ---------------- blocks
block(30, 70, 230, 120, "Battery 48 V (13S)", ["any 13S Li-ion, BMS common port", "54.6 V full, >= 15 A BMS", "XT60 out"], fill="#eef2f7")
block(310, 70, 170, 95, "XT90-S main key", ["anti-spark plug pair", "pull = everything off"])
block(530, 70, 170, 95, "Main fuse 15 A", ["58 V MINI blade", "(Littelfuse 0997)"])
block(750, 60, 230, 110, "WAGO 221-415  W1", ["+48 V ALWAYS ON", "after the main fuse"], fill="#fdecea")
block(30, 255, 230, 100, "Charge socket (rear)", ["matches your charger plug", "in the old C14 cutout", "5 A 58 V fuse in the + lead"])
block(750, 250, 230, 140, "SSR  Crydom D1D12", ["1 (+)  <- W1 +48 V", "2 (-)  -> W2 switched", "3 (+)  <- 5 V via E-stop NC-A", "4 (-)  -> GND (W3)"], fill="#fff4e5")
block(1060, 250, 250, 105, "WAGO 221-415  W2", ["+48 V SWITCHED", "dead while E-stop is pressed"], fill="#fff4e5")
block(1060, 60, 250, 120, "Pololu D42V55F5  (5 V)", ["IN  <- W1 via 3 A 58 V fuse", "OUT 5 V -> Pi 5 pins 2+4,", "74AHCT125 VCC, fan, E-stop A"], fill="#f4ecf7")
block(1380, 40, 590, 300, "Raspberry Pi 5", [
    "pin 2, 4  5 V in      pin 6, 9, 14, 20  GND",
    "pin 1  3.3 V -> PCA9685 VCC, BNO085 VIN",
    "pin 3  SDA / pin 5  SCL -> PCA9685 + BNO085 (I2C1)",
    "pin 11 GPIO17 <- E-stop NC-B (pressed / broken = 1)",
    "pin 13 GPIO27 <- left bumper NC",
    "pin 15 GPIO22 <- right bumper NC",
    "USB3 (blue) x2 -> front stereo cams",
    "USB2 -> left cam;  USB2 -> hub -> right cam + CANable",
    "EEPROM: PSU_MAX_CURRENT=5000 (fed through GPIO 5 V)",
    "can0 at 1 Mbit/s (gs_usb / SocketCAN)"], fill="#eaf2fb")
block(30, 430, 300, 130, "E-stop (hood, kept)", ["NC-A: 5 V -> SSR 3 (+)", "NC-B: GPIO17 -> GND", "(add a 2nd NC contact block)", "pressed: motors + servos off"], fill="#fdecea")
block(380, 430, 300, 130, "WAGO 221-415  W3  GND", ["battery -, SSR 4, 3 regulators,", "motor harness -, charge socket -", "(Pi GND comes via the 5 V reg)"], fill="#f2f2f2")
block(1060, 430, 250, 115, "2x Pololu D42V55F7 (7.5 V)", ["IN <- W2 via 5 A 58 V fuse", "OUT 7.5 V -> servo L, servo R", "(one regulator per servo)"], fill="#fdf2f8")
block(1380, 430, 280, 130, "PCA9685 servo board", ["VCC 3.3 V (Pi pin 1), I2C 0x40", "ch0 -> 74AHCT125 1A", "ch1 -> 74AHCT125 2A", "V+ terminal NOT used"], fill="#eef7ee")
block(1700, 430, 270, 130, "74AHCT125 (kept)", ["VCC 5 V, 1OE + 2OE -> GND", "1Y -> left servo signal", "2Y -> right servo signal"], fill="#eef7ee")
block(1380, 610, 280, 110, "MKS HBL380 servos x2", ["power: its own 7.5 V regulator", "signal: 74AHCT125 (5 V)", "ground common with the Pi"], fill="#fdf2f8")
block(1700, 610, 270, 110, "BNO085 IMU (kept)", ["VIN 3.3 V, GND", "SDA/SCL on I2C1 (0x4A)"], fill="#eef7ee")
block(30, 760, 320, 130, "RS00 right (CAN ID 2)", ["XT30(2+2): +48 V, GND, CAN-H/L", "last node: 120 ohm across H-L", "CAN_TIMEOUT 4000 (0.2 s)"], fill="#ececec")
block(420, 760, 320, 130, "RS00 left  (CAN ID 1)", ["XT30(2+2): +48 V, GND, CAN-H/L", "T-splice at the plug, stub <= 5 cm", "CAN_TIMEOUT 4000 (0.2 s)"], fill="#ececec")
block(820, 760, 300, 130, "CANable 2.0 (USB-CAN)", ["candleLight / gs_usb firmware", "termination jumper ON", "USB -> Pi (through the hub)"], fill="#eaf2fb")
block(1380, 790, 280, 95, "Bumper roller switches x2", ["NC to GND, other side to GPIO", "pull-ups on in software"], fill="#eef7ee")
block(1700, 790, 270, 95, "Cameras x4 + USB hub (kept)", ["MJPEG 640 x 480, 30 fps", "lock exposure + WB (v4l2-ctl)"], fill="#eaf2fb")

# ---------------- +48 V always
wire([(260, 110), (310, 110)], C["b48"], "14 AWG", (262, 100))
wire([(480, 110), (530, 110)], C["b48"])
wire([(700, 110), (750, 110)], C["b48"], "14 AWG", (702, 100))
wire([(260, 165), (285, 165), (285, 232), (145, 232), (145, 255)], C["b48"], "charge lead: battery side of the key", (152, 226), width=3)
wire([(865, 170), (865, 250)], C["b48"], "14 AWG", (872, 215))
wire([(980, 100), (1060, 100)], C["b48"], "3 A fuse, 18 AWG", (985, 90), width=3)
# ---------------- +48 V switched
wire([(980, 300), (1060, 300)], C["sw48"], "14 AWG", (990, 290))
wire([(1185, 355), (1185, 430)], C["sw48"], "5 A fuse, 18 AWG", (1192, 398), width=3)
wire([(1072, 355), (1072, 405), (1040, 405), (1040, 700), (600, 700), (600, 760)], C["sw48"], "16 AWG to the motors (+48 V sw, GND, CAN in one cable, rear-left gland)", (610, 692), width=4)
# ---------------- ground
wire([(60, 190), (15, 190), (15, 600), (530, 600), (530, 560)], C["gnd"], "battery -, 14 AWG", (25, 594), width=4)
wire([(865, 390), (865, 495), (680, 495)], C["gnd"], "SSR 4", (690, 488), width=2)
wire([(1060, 525), (1000, 525), (1000, 535), (680, 535)], C["gnd"], "regulator GND", (690, 528), width=2)
wire([(460, 560), (460, 760)], C["gnd"], "harness GND", (468, 740), width=3)
# ---------------- 5 V
wire([(1310, 90), (1380, 90)], C["v5"], "5 V, 18 AWG", (1314, 80))
wire([(1120, 180), (1120, 215), (720, 215), (720, 415), (300, 415), (300, 430)], C["v5"], "5 V -> E-stop NC-A", (520, 427), width=3)
wire([(250, 430), (250, 400), (735, 400), (735, 330), (750, 330)], C["v5"], "NC-A -> SSR 3 (+)", (420, 393), width=3, dash="8 5")
wire([(1310, 160), (1340, 160), (1340, 410), (1835, 410), (1835, 430)], C["v5"], "5 V -> 74AHCT125 VCC", (1500, 404), width=2)
# ---------------- 7.5 V
wire([(1310, 500), (1345, 500), (1345, 665), (1380, 665)], C["v75"], "7.5 V", (1300, 600), width=3)
# ---------------- CAN + USB
wire([(820, 830), (740, 830)], C["can"], "CAN-H/L", (748, 822), width=3)
wire([(420, 800), (350, 800)], C["sw48"], None, None, width=3)
wire([(420, 820), (350, 820)], C["gnd"], None, None, width=3)
wire([(420, 840), (350, 840)], C["can"], None, None, width=3)
out.append('<text x="40" y="915" font-size="13" fill="#555">left -> right motor: +48 V, GND, CAN-H/L under the floor, slack loop at each motor</text>')
wire([(970, 760), (970, 730), (1352, 730), (1352, 330), (1380, 330)], C["usb"], "USB (via hub)", (1180, 724), width=3)
wire([(1835, 790), (1835, 760), (1950, 760), (1950, 340)], C["usb"], "USB", (1890, 754), width=3)
# ---------------- signals
wire([(1520, 340), (1520, 430)], C["sig"], "I2C", (1527, 390), width=2)
wire([(1685, 340), (1685, 665), (1700, 665)], C["sig"], "I2C", (1660, 600), width=2)
wire([(1660, 490), (1700, 490)], C["sig"], "PWM", (1664, 482), width=2)
wire([(1760, 560), (1760, 590), (1560, 590), (1560, 610)], C["sig"], "servo signals (5 V)", (1580, 583), width=2)
wire([(330, 530), (360, 530), (360, 575), (1362, 575), (1362, 300), (1380, 300)], C["sig"], "NC-B -> GPIO17", (700, 568), width=2, dash="8 5")
wire([(1380, 840), (1372, 840), (1372, 345)], C["sig"], None, None, width=2)
out.append('<text x="1388" y="905" font-size="13" fill="#2ca02c">bumpers -> GPIO27 / GPIO22</text>')

# legend
lx, ly = 30, 960
out.append(f'<text x="{lx}" y="{ly}" font-size="18" font-weight="700">Wire colours</text>')
for i, (k, t) in enumerate([("b48", "+48 V always on (battery, after key + main fuse)"), ("sw48", "+48 V switched by the SSR (E-stop)"),
                             ("gnd", "ground (one star point: WAGO W3)"), ("v5", "5 V"), ("v75", "7.5 V servo power"),
                             ("can", "CAN bus 1 Mbit/s (twisted pair)"), ("sig", "signals (I2C, PWM, switches)"), ("usb", "USB")]):
    y = ly + 28 + i * 26
    out.append(f'<line x1="{lx}" y1="{y - 5}" x2="{lx + 50}" y2="{y - 5}" stroke="{C[k]}" stroke-width="5"/>')
    out.append(f'<text x="{lx + 62}" y="{y}" font-size="15">{t}</text>')
notes = ["Rules",
         "1. Every part on the battery side is rated 60 V or more: 54.6 V full charge, RS00 trips at 60 V.",
         "2. Pull the XT90-S key before working inside. Press the E-stop before charging.",
         "3. One ground point (W3). The CAN, I2C and servo signals all share it.",
         "4. CAN: CANable end terminated (jumper ON), far end 120 ohm at the right motor plug. Stubs <= 5 cm.",
         "5. The SSR only cuts the motors and servos. The Pi stays on, sees the E-stop on GPIO17 and logs it.",
         "6. Set each RS00 once with RobStride's tool or SDK: CAN ID 1 (left), 2 (right), CAN_TIMEOUT 4000.",
         "7. Different battery? Any 7S-13S Li-ion with a common-port BMS works; speed scales with voltage.",
         "8. Different board? Keep its job (5 V for the Pi, 7.5 V servo power, SocketCAN adapter, 3.3 V I2C)."]
for i, t in enumerate(notes):
    out.append(f'<text x="{620}" y="{ly + i * 26}" font-size="{18 if i == 0 else 15}" font-weight="{700 if i == 0 else 400}">{t}</text>')

svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="DejaVu Sans, Arial, sans-serif">'
       f'<rect width="{W}" height="{H}" fill="#ffffff"/>'
       f'<text x="30" y="40" font-size="26" font-weight="700">Campus Rover v5 wiring (RS00 rear drive, 48 V, Pi 5, cameras only)</text>'
       + "".join(out) + "</svg>")
od = sys.argv[1]
open(os.path.join(od, "wiring_v5.svg"), "w").write(svg)
if len(sys.argv) > 2:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        pg.set_content(f"<html><body style='margin:0'>{svg}</body></html>")
        pg.screenshot(path=os.path.join(od, "wiring_v5.png"), full_page=True)
        b.close()
print("ok")
