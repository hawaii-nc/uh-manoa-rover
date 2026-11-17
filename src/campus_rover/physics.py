"""What a wheel feels: which surfaces collide, and how grippy each one is.

Two tables, both for the physics engine:

- `COLLISION`  which semantic classes are solid for a rover and how exactly. Only things
  at wheel height collide: ground, curbs, stairs, walls, building walls, trunks, furniture.
  Roofs, windows, tree crowns, road paint and moving agents do not, which removes
  phantom obstacles (a rover must
  not bump into a tree's canopy 4 m up). On the UH corridor about 69 % of the scene's
  triangles collide, down from all of them; building walls are the largest share.
  Keeping only the lowest few meters of each wall is the next saving (untested without
  Isaac Sim).
- `SURFACES`   friction and roughness per surface. **These are typical engineering
  ranges for a rubber tyre, not measurements of UH's pavement.** They are placeholders
  to be calibrated with the real rover (slide tests on each surface, dry and wet) and
  are given as ranges so a simulation can draw from them each episode.
"""

from __future__ import annotations

# Collision approximation per semantic class. "none" = the exact triangle mesh (static
# colliders may be exact); "convexHull" suits a single compact object. A class missing
# here does not collide.
COLLISION = {
    "walkway": "none", "ramp": "none", "road": "none", "grass": "none", "gravel": "none",
    "dirt": "none", "rock": "none", "planter": "none", "stepstone": "none",
    "sports_court": "none", "building_slab": "none", "stairs": "none", "curb": "none",
    "wall": "none", "building": "none", "fence": "none", "gate": "none", "rail": "none",
    "bench": "none", "trash_can": "none", "recycling_bin": "none", "bike_rack": "none",
    "skateboard_rack": "none", "bike_share_dock": "none", "emergency_call_box": "none",
    "ev_charger": "none", "mailbox": "none", "bulletin_board": "none", "art_piece": "none",
}
# Classes drawn from several materials where only some parts are solid:
# tree -> trunk (bark) only; the leaves are not.
PART_RULES = {("tree", "bark"): "none"}
# Shared models (one prototype, many placements) are single compact objects.
MODEL_COLLISION = {"bench": "convexHull", "street_light": "convexHull", "trash_can": "convexHull",
                   "art_piece": "convexHull"}

# Rubber on surface: (static friction range, roughness in mm range, wet multiplier).
SURFACES = {
    "walkway":      {"static": (0.60, 0.90), "roughness_mm": (0.5, 2.0), "wet": 0.65},
    "ramp":         {"static": (0.60, 0.90), "roughness_mm": (0.5, 2.0), "wet": 0.65},
    "road":         {"static": (0.70, 0.95), "roughness_mm": (1.0, 4.0), "wet": 0.65},
    "building_slab": {"static": (0.60, 0.90), "roughness_mm": (0.5, 2.0), "wet": 0.65},
    "stepstone":    {"static": (0.50, 0.80), "roughness_mm": (1.0, 5.0), "wet": 0.60},
    "sports_court": {"static": (0.50, 0.80), "roughness_mm": (1.0, 3.0), "wet": 0.60},
    "grass":        {"static": (0.30, 0.55), "roughness_mm": (10.0, 30.0), "wet": 0.70},
    "planter":      {"static": (0.30, 0.50), "roughness_mm": (10.0, 40.0), "wet": 0.70},
    "dirt":         {"static": (0.40, 0.60), "roughness_mm": (5.0, 20.0), "wet": 0.55},
    "gravel":       {"static": (0.45, 0.65), "roughness_mm": (5.0, 25.0), "wet": 0.85},
    "rock":         {"static": (0.50, 0.80), "roughness_mm": (5.0, 30.0), "wet": 0.70},
    "stairs":       {"static": (0.60, 0.90), "roughness_mm": (0.5, 2.0), "wet": 0.65},
    "curb":         {"static": (0.60, 0.90), "roughness_mm": (0.5, 2.0), "wet": 0.65},
    "wall":         {"static": (0.50, 0.80), "roughness_mm": (1.0, 5.0), "wet": 0.75},
    "building":     {"static": (0.50, 0.80), "roughness_mm": (1.0, 5.0), "wet": 0.75},
}
DEFAULT_SURFACE = {"static": (0.50, 0.80), "roughness_mm": (1.0, 5.0), "wet": 0.70}
KINETIC_RATIO = 0.85           # dynamic friction as a share of static (typical for rubber)


def collision_for(cls: str, material: str = "") -> str | None:
    """Approximation token for a mesh part, or None if it should not collide."""
    for (c, prefix), approx in PART_RULES.items():
        if cls == c:
            return approx if material.startswith(prefix) else None
    return COLLISION.get(cls)


def friction_for(cls: str, wet: bool = False) -> tuple[float, float]:
    """(static, dynamic) friction for a class: the middle of its range, lowered when wet."""
    spec = SURFACES.get(cls, DEFAULT_SURFACE)
    static = sum(spec["static"]) / 2 * (spec["wet"] if wet else 1.0)
    return round(static, 3), round(static * KINETIC_RATIO, 3)


def sample_friction(cls: str, rng, wet: bool = False) -> tuple[float, float]:
    """A random draw from the class's range, for domain randomization."""
    spec = SURFACES.get(cls, DEFAULT_SURFACE)
    static = float(rng.uniform(*spec["static"])) * (spec["wet"] if wet else 1.0)
    return round(static, 3), round(static * KINETIC_RATIO, 3)


def collision_stats(parts: dict) -> dict:
    """How many triangles collide, out of how many, by approximation and by class.
    `parts` is a Scene.parts mapping {(group, class, material): Mesh}."""
    total = solid = 0
    by_class: dict[str, int] = {}
    for (_, cls, material), mesh in parts.items():
        tris = sum(len(f) for f in mesh.faces)
        total += tris
        if collision_for(cls, material):
            solid += tris
            by_class[cls] = by_class.get(cls, 0) + tris
    return {"triangles": total, "collide": solid,
            "share": round(solid / total, 3) if total else 0.0,
            "by_class": dict(sorted(by_class.items(), key=lambda kv: -kv[1]))}
