"""People, bikes, vehicles and animals for the 3D world.

These are simple procedural stand-ins: correct size, pose and label, random
clothes and paint. They let a perception model learn *where* things are and
how big; swap them for rigged characters (see docs) for final appearance.
They go into their own scene so one static world can host many scenarios.

How many, and where, comes from the crowd model the route planner already
uses: expected people per 100 m of walkway at the scenario's time.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .meshkit import Mesh, Scene

SKIN = [(0.94, 0.80, 0.69), (0.85, 0.66, 0.50), (0.69, 0.50, 0.36), (0.50, 0.34, 0.24),
        (0.36, 0.24, 0.17)]
CLOTHES = [(0.15, 0.25, 0.45), (0.75, 0.15, 0.15), (0.92, 0.92, 0.90), (0.12, 0.12, 0.14),
           (0.10, 0.45, 0.30), (0.85, 0.70, 0.15), (0.55, 0.35, 0.65), (0.90, 0.50, 0.20),
           (0.45, 0.60, 0.80), (0.60, 0.60, 0.62)]
PANTS = [(0.15, 0.17, 0.25), (0.25, 0.25, 0.27), (0.55, 0.47, 0.35), (0.20, 0.28, 0.40),
         (0.75, 0.72, 0.65)]
CAR_PAINT = [(0.85, 0.85, 0.87), (0.10, 0.10, 0.11), (0.55, 0.56, 0.58), (0.60, 0.08, 0.08),
             (0.10, 0.20, 0.45), (0.75, 0.76, 0.78), (0.20, 0.30, 0.25)]


@dataclass
class Scenario:
    name: str = "class_change"
    time: str = "Mon 09:20"
    seed: int = 7
    people_scale: float = 1.0        # multiplies the crowd model's density
    min_people_per_100m: float = 0.6
    wheeled_share: float = 0.12      # share of walkers on bikes or skateboards
    parked_share: float = 0.7        # share of parking stalls with a car in them
    moving_car_every_m: float = 90.0
    carts: int = 4
    chickens: int = 14
    cats: int = 4

    @classmethod
    def from_dict(cls, data: dict) -> Scenario:
        unknown = set(data) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"scenario: unknown fields {sorted(unknown)}")
        return cls(**data)


# ---- mesh helpers ------------------------------------------------------------------


def _mat(scene: Scene, prefix: str, rgb, **kw) -> str:
    name = f"{prefix}_{int(rgb[0] * 255):02x}{int(rgb[1] * 255):02x}{int(rgb[2] * 255):02x}"
    return scene.material(name, rgb, **kw)


def _local(x, y, yaw, dx, dy):
    """Offset (dx forward, dy left) from (x, y) facing yaw."""
    c, s = math.cos(yaw), math.sin(yaw)
    return x + dx * c - dy * s, y + dx * s + dy * c


def wheel(mesh: Mesh, x, y, z, yaw, radius, width=0.08, segments=10) -> None:
    """Upright wheel rolling along yaw, centered at (x, y, z)."""
    ang = np.linspace(0, 2 * math.pi, segments, endpoint=False)
    c, s = math.cos(yaw), math.sin(yaw)
    pts = []
    for side in (-width / 2, width / 2):
        for a in ang:
            f, up = radius * math.cos(a), radius * math.sin(a)
            pts.append((x + f * c - side * s, y + f * s + side * c, z + up))
    n = segments
    faces = []
    for i in range(n):
        j = (i + 1) % n
        faces += [(i, j, j + n), (i, j + n, i + n)]          # tread
    for i in range(1, n - 1):
        faces += [(0, i + 1, i), (n, n + i, n + i + 1)]       # two flat sides
    mesh.add(pts, faces)


HAIR = [(0.06, 0.05, 0.05), (0.12, 0.08, 0.06), (0.25, 0.16, 0.10), (0.45, 0.32, 0.18),
        (0.62, 0.60, 0.58), (0.78, 0.66, 0.38)]
SHOES = [(0.08, 0.08, 0.09), (0.88, 0.88, 0.86), (0.30, 0.22, 0.16), (0.55, 0.12, 0.10),
         (0.12, 0.30, 0.50)]
# Rubber slippers ("slippahs") are the usual footwear in Hawaiʻi: a flat sole, skin above.


def _limb(mesh: Mesh, p0, p1, r0: float, r1: float, seg: int = 6, stretch: float = 1.0,
          yaw: float = 0.0) -> None:
    """A tapered tube from p0 to p1 (world x, y, z), optionally wider across the body."""
    a, b = np.asarray(p0, float), np.asarray(p1, float)
    axis = b - a
    n = np.linalg.norm(axis)
    if n < 1e-6:
        return
    axis /= n
    ref = np.array([0.0, 0.0, 1.0]) if abs(axis[2]) < 0.95 else np.array([1.0, 0.0, 0.0])
    u = np.cross(ref, axis)
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    ang = np.linspace(0, 2 * math.pi, seg, endpoint=False)
    side = np.array([-math.sin(yaw), math.cos(yaw), 0.0])      # across the shoulders
    verts = []
    for p, r in ((a, r0), (b, r1)):
        for t in ang:
            off = (u * math.cos(t) + v * math.sin(t)) * r
            if stretch != 1.0:
                off = off + side * np.dot(off, side) * (stretch - 1.0)
            verts.append(p + off)
    i = np.arange(seg)
    j = (i + 1) % seg
    faces = np.r_[np.c_[i, j, j + seg], np.c_[i, j + seg, i + seg]]
    verts.append(b)
    faces = np.r_[faces, np.c_[i + seg, j + seg, np.full(seg, 2 * seg)]]       # close the top
    mesh.add(np.asarray(verts), faces)


def add_person(scene: Scene, group: str, x, y, z, yaw, rng, seated: float = 0.0) -> None:
    """A standing, walking or seated person: head with hair, neck, torso, arms with elbows, legs
    with knees, shoes, often a backpack. About 300 triangles, so a crowd stays light.
    `seated` lowers the hips (for riders)."""
    scale = float(rng.uniform(0.9, 1.08))
    skin = _mat(scene, "skin", SKIN[rng.integers(len(SKIN))])
    shirt_rgb = CLOTHES[rng.integers(len(CLOTHES))]
    shirt = _mat(scene, "cloth", shirt_rgb)
    pants_rgb = PANTS[rng.integers(len(PANTS))]
    shorts = rng.random() < 0.55                       # warm island: shorts are common
    pants = _mat(scene, "cloth", pants_rgb)
    hair = _mat(scene, "hair", HAIR[rng.integers(len(HAIR))], roughness=0.9)
    shoes = _mat(scene, "shoe", SHOES[rng.integers(len(SHOES))])
    m_skin, m_shirt = scene.mesh(group, "person", skin), scene.mesh(group, "person", shirt)
    m_pants, m_hair = scene.mesh(group, "person", pants), scene.mesh(group, "person", hair)
    m_shoe = scene.mesh(group, "person", shoes)

    def at(f, s, h):                                    # forward, left, height in the body frame
        px, py = _local(x, y, yaw, f * scale, s * scale)
        return (px, py, z + h * scale)

    walking = not seated and rng.random() < 0.7
    swing = float(rng.uniform(-1, 1)) * 0.38 if walking else 0.0          # stride, radians
    hip_h = 0.93 - seated
    if seated:
        thigh = 0.42
        for side in (0.09, -0.09):                      # thighs forward, shins down
            knee = at(thigh, side, hip_h + 0.0)
            ankle = at(thigh + 0.04, side, hip_h - 0.43)
            _limb(m_pants, at(0, side, hip_h), knee, 0.075, 0.056, 6, 1.0, yaw)
            _limb(m_skin if shorts else m_pants, knee, ankle, 0.054, 0.04, 6, 1.0, yaw)
            _limb(m_shoe, at(thigh + 0.0, side, hip_h - 0.46), at(thigh + 0.17, side, hip_h - 0.46),
                  0.045, 0.04, 5, 1.0, yaw)
    else:
        for side, sg in ((0.09, 1), (-0.09, -1)):
            a = swing * sg
            knee = at(math.sin(a) * 0.43, side, hip_h - math.cos(a) * 0.43)
            back = max(-a, 0) * 0.5                       # the trailing knee bends
            ankle = at(math.sin(a) * 0.43 - math.sin(a - back) * 0.42, side,
                       hip_h - math.cos(a) * 0.43 - math.cos(a - back) * 0.42)
            _limb(m_pants, at(0, side, hip_h), knee, 0.078, 0.057, 6, 1.0, yaw)
            _limb(m_skin if shorts else m_pants, knee, ankle, 0.054, 0.039, 6, 1.0, yaw)
            _limb(m_shoe, (ankle[0], ankle[1], ankle[2] + 0.03 * scale),
                  at(math.sin(a) * 0.43 - math.sin(a - back) * 0.42 + 0.17, side,
                     0.03 + max(0, 0.0)), 0.05, 0.042, 5, 1.0, yaw)
    shoulder_h = hip_h + 0.56
    _limb(m_shirt, at(0, 0, hip_h), at(0, 0, shoulder_h), 0.145, 0.165, 8, 1.35, yaw)    # torso
    _limb(m_pants, at(0, 0, hip_h - 0.06), at(0, 0, hip_h + 0.1), 0.15, 0.15, 8, 1.3, yaw)  # hips
    for side, sg in ((0.2, 1), (-0.2, -1)):
        a = -swing * sg * 0.9                              # arms swing against the legs
        elbow = at(math.sin(a) * 0.29, side, shoulder_h - 0.02 - math.cos(a) * 0.29)
        wrist = at(math.sin(a) * 0.29 + math.sin(a + 0.25 + max(a, 0) * 0.5) * 0.27, side,
                   shoulder_h - 0.02 - math.cos(a) * 0.29
                   - math.cos(a + 0.25 + max(a, 0) * 0.5) * 0.27)
        _limb(m_shirt, at(0, side, shoulder_h - 0.02), elbow, 0.05, 0.043, 6, 1.0, yaw)
        _limb(m_skin, elbow, wrist, 0.04, 0.032, 6, 1.0, yaw)
        m_skin.blob(wrist, (0.035 * scale,) * 3, seed=1, detail=0, wobble=0.0)
    _limb(m_skin, at(0, 0, shoulder_h), at(0.01, 0, shoulder_h + 0.09), 0.05, 0.045, 6, 1.0, yaw)
    head = at(0.015, 0, shoulder_h + 0.2)
    m_skin.blob(head, (0.095 * scale, 0.082 * scale, 0.115 * scale), seed=3, detail=1, wobble=0.0)
    cap = rng.random() < 0.18
    hr = at(0.0, 0, shoulder_h + 0.235)
    m_hair.blob(hr, (0.1 * scale, 0.088 * scale, 0.1 * scale), seed=5, detail=1, wobble=0.0)
    if cap:
        m_shirt.blob(at(0.03, 0, shoulder_h + 0.255), (0.115 * scale, 0.095 * scale, 0.05 * scale),
                     seed=7, detail=0, wobble=0.0)
    if rng.random() < 0.5:      # backpack: half the students carry one
        pack = _mat(scene, "cloth", PANTS[rng.integers(len(PANTS))])
        bx, by = _local(x, y, yaw, -0.17 * scale, 0.0)
        scene.mesh(group, "person", pack).box(
            (bx, by, z + (hip_h + 0.1) * scale), (0.15 * scale, 0.3 * scale, 0.42 * scale), yaw)


def add_bicycle(scene: Scene, group: str, x, y, z, yaw, rng, rider: bool = True) -> None:
    frame_mat = _mat(scene, "paint", CAR_PAINT[rng.integers(len(CAR_PAINT))], roughness=0.4)
    tire = scene.material("rubber", (0.06, 0.06, 0.06))
    wheels = scene.mesh(group, "bicycle", tire)
    frame = scene.mesh(group, "bicycle", frame_mat)
    for d in (0.52, -0.52):
        wx, wy = _local(x, y, yaw, d, 0.0)
        wheel(wheels, wx, wy, z + 0.34, yaw, 0.34, 0.05)
    frame.box((x, y, z + 0.55), (0.95, 0.05, 0.08), yaw)                 # top tube
    sx, sy = _local(x, y, yaw, -0.2, 0.0)
    frame.box((sx, sy, z + 0.34), (0.06, 0.05, 0.62), yaw)               # seat tube
    hx, hy = _local(x, y, yaw, 0.42, 0.0)
    frame.box((hx, hy, z + 0.34), (0.06, 0.05, 0.75), yaw)               # fork + stem
    frame.box((hx, hy, z + 1.05), (0.05, 0.5, 0.04), yaw)                # handlebar
    frame.box((sx, sy, z + 0.94), (0.24, 0.14, 0.06), yaw)               # saddle
    if rider:
        rx, ry = _local(x, y, yaw, -0.12, 0.0)
        add_person(scene, group, rx, ry, z + 0.42, yaw, rng, seated=0.28)


def add_skateboarder(scene: Scene, group: str, x, y, z, yaw, rng) -> None:
    board = scene.mesh(group, "skateboard", scene.material("board", (0.35, 0.22, 0.12)))
    board.box((x, y, z + 0.08), (0.8, 0.2, 0.03), yaw)
    add_person(scene, group, x, y, z + 0.11, yaw + math.pi / 2, rng)


def add_car(scene: Scene, group: str, x, y, z, yaw, rng) -> None:
    paint = _mat(scene, "paint", CAR_PAINT[rng.integers(len(CAR_PAINT))], roughness=0.35,
                 metallic=0.5)
    suv = rng.random() < 0.4
    length, width = (4.7, 1.9) if suv else (4.4, 1.78)
    body_h, cabin_h = (0.85, 0.75) if suv else (0.7, 0.6)
    body = scene.mesh(group, "car", paint)
    body.box((x, y, z + 0.28), (length, width, body_h), yaw)
    cx, cy = _local(x, y, yaw, -0.25, 0.0)
    glass = scene.mesh(group, "car", scene.material("car_glass", (0.12, 0.16, 0.2),
                                                    roughness=0.1, metallic=0.5))
    glass.box((cx, cy, z + 0.28 + body_h), (length * 0.55, width * 0.9, cabin_h), yaw)
    tire = scene.mesh(group, "car", scene.material("rubber", (0.06, 0.06, 0.06)))
    for dx in (length * 0.3, -length * 0.3):
        for dy in (width / 2 - 0.08, -width / 2 + 0.08):
            wx, wy = _local(x, y, yaw, dx, dy)
            wheel(tire, wx, wy, z + 0.33, yaw, 0.33, 0.22)


def add_cart(scene: Scene, group: str, x, y, z, yaw, rng) -> None:
    """Utility / golf cart, the kind campus staff drive on walkways."""
    paint = _mat(scene, "paint", [(0.92, 0.92, 0.9), (0.1, 0.35, 0.2)][rng.integers(2)])
    body = scene.mesh(group, "cart", paint)
    body.box((x, y, z + 0.25), (2.4, 1.2, 0.45), yaw)
    sx, sy = _local(x, y, yaw, -0.2, 0.0)
    body.box((sx, sy, z + 0.7), (0.5, 1.1, 0.5), yaw)                     # seat
    body.box((x, y, z + 1.75), (1.9, 1.25, 0.06), yaw)                    # roof
    for dx in (0.75, -0.95):
        for dy in (0.55, -0.55):
            px, py = _local(x, y, yaw, dx, dy)
            body.box((px, py, z + 0.7), (0.05, 0.05, 1.05), yaw)
    tire = scene.mesh(group, "cart", scene.material("rubber", (0.06, 0.06, 0.06)))
    for dx in (0.8, -0.8):
        for dy in (0.55, -0.55):
            wx, wy = _local(x, y, yaw, dx, dy)
            wheel(tire, wx, wy, z + 0.22, yaw, 0.22, 0.16)


def add_chicken(scene: Scene, group: str, x, y, z, yaw, rng) -> None:
    colors = [(0.45, 0.22, 0.10), (0.12, 0.10, 0.09), (0.85, 0.80, 0.70), (0.60, 0.35, 0.15)]
    body = scene.mesh(group, "chicken", _mat(scene, "fur", colors[rng.integers(len(colors))]))
    body.blob((x, y, z + 0.2), (0.17, 0.11, 0.12), wobble=0.0)
    hx, hy = _local(x, y, yaw, 0.14, 0.0)
    body.blob((hx, hy, z + 0.34), (0.055, 0.05, 0.06), wobble=0.0)
    tx, ty = _local(x, y, yaw, -0.17, 0.0)
    body.blob((tx, ty, z + 0.3), (0.08, 0.03, 0.1), wobble=0.0)
    comb = scene.mesh(group, "chicken", scene.material("comb", (0.8, 0.1, 0.1)))
    comb.box((hx, hy, z + 0.39), (0.06, 0.015, 0.04), yaw)
    for dy in (0.04, -0.04):
        lx, ly = _local(x, y, yaw, 0.0, dy)
        comb.box((lx, ly, z), (0.015, 0.015, 0.1), yaw)


def add_cat(scene: Scene, group: str, x, y, z, yaw, rng) -> None:
    colors = [(0.15, 0.14, 0.13), (0.75, 0.50, 0.25), (0.55, 0.55, 0.55), (0.9, 0.88, 0.82)]
    body = scene.mesh(group, "cat", _mat(scene, "fur", colors[rng.integers(len(colors))]))
    body.blob((x, y, z + 0.2), (0.22, 0.09, 0.1), wobble=0.0)
    hx, hy = _local(x, y, yaw, 0.24, 0.0)
    body.blob((hx, hy, z + 0.27), (0.075, 0.07, 0.07), wobble=0.0)
    tx, ty = _local(x, y, yaw, -0.28, 0.0)
    body.box((tx, ty, z + 0.2), (0.04, 0.04, 0.26), yaw)
    for dx in (0.14, -0.14):
        for dy in (0.05, -0.05):
            lx, ly = _local(x, y, yaw, dx, dy)
            body.box((lx, ly, z), (0.035, 0.035, 0.14), yaw)


# ---- placement ---------------------------------------------------------------------


def populate(scene: Scene, site, graph, model, ground_z, bbox, scenario: Scenario,
             road_segments: list[dict] | None = None, surface=None, library=None) -> dict:
    """Fill `scene` with agents for one scenario. Returns counts per class.

    ground_z(x, y) -> elevation. `surface(x, y)` -> "grass" | "walkway" | "road" (optional).
    """
    from .crowd import CrowdModel
    from .schedule import WeekTime

    rng = np.random.default_rng(scenario.seed)
    when = WeekTime.parse(scenario.time)
    crowd = CrowdModel.from_site(site, graph)
    counts = dict.fromkeys(["person", "bicycle", "skateboard", "car", "cart", "chicken", "cat"],
                           0)
    x0, y0, x1, y1 = bbox

    def inside(x, y) -> bool:
        return x0 + 2 < x < x1 - 2 and y0 + 2 < y < y1 - 2

    def z_at(x, y) -> float:
        return float(ground_z(x, y))

    from .assets import of_class

    car_models = of_class(library or {}, "car")

    def car(x, y, yaw) -> None:
        """A real car model when one is in the library, else the box stand-in."""
        if car_models:
            scene.place(car_models[rng.integers(len(car_models))], "agents/cars", x, y,
                        z_at(x, y), yaw)
        else:
            add_car(scene, "agents/cars", x, y, z_at(x, y), yaw, rng)
            scene.record("agent", "car", x, y, z_at(x, y), yaw, cls="car")

    # People (and riders) along walkways, as many as the crowd model expects.
    for u, v, data in graph.g.edges(data=True):
        (ax, ay), (bx, by) = graph.xy(u), graph.xy(v)
        length = math.dist((ax, ay), (bx, by))
        if length < 0.5 or data.get("closed") or not (inside(ax, ay) and inside(bx, by)):
            continue
        if data.get("kind") == "crosswalk" and data.get("tier") == "candidate":
            continue                  # only a suggested line across a road: nobody walks there
        roadside = data.get("zone") == "roadside"
        density = max(crowd.density(u, v, when) * scenario.people_scale,
                      scenario.min_people_per_100m * (0.3 if roadside else 1.0))
        yaw = math.atan2(by - ay, bx - ax)
        for _ in range(rng.poisson(density * length / 100.0)):
            t = rng.uniform(0, 1)
            side = rng.uniform(-1.1, 1.1) + (3.2 if roadside else 0.0)   # roadside = sidewalk
            px, py = _local(ax + (bx - ax) * t, ay + (by - ay) * t, yaw, 0.0, side)
            heading = yaw + (math.pi if rng.random() < 0.5 else 0.0) + rng.normal(0, 0.15)
            roll = rng.random()
            if data.get("kind") != "stairs" and roll < scenario.wheeled_share * 0.6:
                add_bicycle(scene, "agents/bicycles", px, py, z_at(px, py), heading, rng)
                scene.record("agent", "cyclist", px, py, z_at(px, py), heading, cls="bicycle",
                             activity="riding", path=data.get("kind"))
                counts["bicycle"] += 1
                counts["person"] += 1
            elif data.get("kind") != "stairs" and roll < scenario.wheeled_share:
                add_skateboarder(scene, "agents/skateboards", px, py, z_at(px, py), heading, rng)
                scene.record("agent", "skateboarder", px, py, z_at(px, py), heading,
                             cls="skateboard", activity="riding", path=data.get("kind"))
                counts["skateboard"] += 1
                counts["person"] += 1
            else:
                add_person(scene, "agents/people", px, py, z_at(px, py), heading, rng)
                scene.record("agent", "person", px, py, z_at(px, py), heading, cls="person",
                             activity="walking", path=data.get("kind"))
                counts["person"] += 1

    # Parked bikes at bike racks.
    for prop in model.props if model else []:
        if prop.cls == "bike_rack" and inside(*prop.xy):
            base = rng.uniform(0, math.pi)
            for i in range(int(rng.integers(1, 5))):
                px, py = _local(prop.xy[0], prop.xy[1], base, 0.0, (i - 1.5) * 0.55)
                add_bicycle(scene, "agents/bicycles", px, py, z_at(px, py),
                            base + math.pi / 2, rng, rider=False)
                scene.record("agent", "parked_bicycle", px, py, z_at(px, py), base + math.pi / 2,
                             cls="bicycle", activity="parked")
                counts["bicycle"] += 1

    # Cars: parked in mapped stalls, and a few driving on the roads.
    roads = [s for s in road_segments or [] if s.get("class") in
             ("service", "residential", "tertiary", "secondary", "unclassified")]

    def road_heading(x, y) -> float:
        best, best_d = 0.0, 1e9
        for seg in roads:
            for a, b in zip(seg["coords"], seg["coords"][1:], strict=False):
                d = math.dist(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), (x, y))
                if d < best_d:
                    best, best_d = math.atan2(b[1] - a[1], b[0] - a[0]), d
        return best

    for stall in model.vehicle_spawns if model else []:
        x, y = stall.xy
        if inside(x, y) and rng.random() < scenario.parked_share:
            car(x, y, road_heading(x, y) + math.pi / 2)
            counts["car"] += 1
    for seg in roads:
        if seg.get("subclass") in ("parking_aisle", "driveway"):
            continue
        for a, b in zip(seg["coords"], seg["coords"][1:], strict=False):
            length = math.dist(a, b)
            for _ in range(rng.poisson(length / scenario.moving_car_every_m)):
                t = rng.uniform(0, 1)
                yaw = math.atan2(b[1] - a[1], b[0] - a[0])
                forward = rng.random() < 0.5
                px, py = _local(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, yaw, 0.0,
                                -1.5 if forward else 1.5)
                if inside(px, py):
                    car(px, py, yaw if forward else yaw + math.pi)
                    counts["car"] += 1

    # Carts on walkways, chickens on grass, cats near buildings.
    walk_edges = [(u, v) for u, v, d in graph.g.edges(data=True)
                  if d.get("kind") == "walkway" and inside(*graph.xy(u)) and inside(*graph.xy(v))]
    for _ in range(min(scenario.carts, len(walk_edges))):
        u, v = walk_edges[rng.integers(len(walk_edges))]
        (ax, ay), (bx, by) = graph.xy(u), graph.xy(v)
        t = rng.uniform(0.2, 0.8)
        px, py = ax + (bx - ax) * t, ay + (by - ay) * t
        add_cart(scene, "agents/carts", px, py, z_at(px, py), math.atan2(by - ay, bx - ax), rng)
        scene.record("agent", "cart", px, py, z_at(px, py), math.atan2(by - ay, bx - ax),
                     cls="cart")
        counts["cart"] += 1
    placed, tries = 0, 0
    while placed < scenario.chickens + scenario.cats and tries < 4000 and walk_edges:
        tries += 1
        u, v = walk_edges[rng.integers(len(walk_edges))]
        ax, ay = graph.xy(u)
        px, py = ax + rng.uniform(-9, 9), ay + rng.uniform(-9, 9)
        if not inside(px, py) or (surface and surface(px, py) != "grass"):
            continue
        yaw = rng.uniform(0, 2 * math.pi)
        if placed < scenario.chickens:
            add_chicken(scene, "agents/animals", px, py, z_at(px, py), yaw, rng)
            scene.record("agent", "chicken", px, py, z_at(px, py), yaw, cls="chicken")
            counts["chicken"] += 1
        else:
            add_cat(scene, "agents/animals", px, py, z_at(px, py), yaw, rng)
            scene.record("agent", "cat", px, py, z_at(px, py), yaw, cls="cat")
            counts["cat"] += 1
        placed += 1
    return counts
