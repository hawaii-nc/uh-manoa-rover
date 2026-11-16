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


def add_person(scene: Scene, group: str, x, y, z, yaw, rng, seated: float = 0.0) -> None:
    """A standing or walking person. `seated` lowers the hips (for riders)."""
    scale = float(rng.uniform(0.88, 1.1))
    skin = _mat(scene, "skin", SKIN[rng.integers(len(SKIN))])
    top = _mat(scene, "cloth", CLOTHES[rng.integers(len(CLOTHES))])
    legs = _mat(scene, "cloth", PANTS[rng.integers(len(PANTS))])
    body = scene.mesh(group, "person", top)
    lower = scene.mesh(group, "person", legs)
    head = scene.mesh(group, "person", skin)
    leg_h = (0.86 - seated) * scale
    stride = 0.0 if seated else float(rng.uniform(-0.18, 0.18))
    for side, phase in ((0.1, stride), (-0.1, -stride)):
        cx, cy = _local(x, y, yaw, phase, side * scale)
        lower.box((cx, cy, z), (0.16 * scale, 0.15 * scale, leg_h), yaw)
    torso_h = 0.6 * scale
    body.box((x, y, z + leg_h), (0.24 * scale, 0.42 * scale, torso_h), yaw)
    for side in (0.26, -0.26):
        cx, cy = _local(x, y, yaw, -stride * 0.6 * math.copysign(1, side), side * scale)
        body.box((cx, cy, z + leg_h + 0.05), (0.11 * scale, 0.1 * scale, 0.55 * scale), yaw)
    head.blob((x, y, z + leg_h + torso_h + 0.13 * scale),
              (0.105 * scale, 0.1 * scale, 0.125 * scale), seed=0, wobble=0.0)
    if rng.random() < 0.45:      # backpack: half the students carry one
        bx, by = _local(x, y, yaw, -0.19 * scale, 0.0)
        scene.mesh(group, "person", _mat(scene, "cloth", PANTS[rng.integers(len(PANTS))])).box(
            (bx, by, z + leg_h + 0.1), (0.16 * scale, 0.3 * scale, 0.42 * scale), yaw)


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
                counts["bicycle"] += 1
                counts["person"] += 1
            elif data.get("kind") != "stairs" and roll < scenario.wheeled_share:
                add_skateboarder(scene, "agents/skateboards", px, py, z_at(px, py), heading, rng)
                counts["skateboard"] += 1
                counts["person"] += 1
            else:
                add_person(scene, "agents/people", px, py, z_at(px, py), heading, rng)
                counts["person"] += 1

    # Parked bikes at bike racks.
    for prop in model.props if model else []:
        if prop.cls == "bike_rack" and inside(*prop.xy):
            base = rng.uniform(0, math.pi)
            for i in range(int(rng.integers(1, 5))):
                px, py = _local(prop.xy[0], prop.xy[1], base, 0.0, (i - 1.5) * 0.55)
                add_bicycle(scene, "agents/bicycles", px, py, z_at(px, py),
                            base + math.pi / 2, rng, rider=False)
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
            counts["chicken"] += 1
        else:
            add_cat(scene, "agents/animals", px, py, z_at(px, py), yaw, rng)
            counts["cat"] += 1
        placed += 1
    return counts
