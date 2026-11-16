"""Build a building floor by floor from its registered floor plans.

`buildgen` extrudes one outline and paints windows on it. This builds what the
plans actually show: for every level a floor slab with its real outline and its
voids (courtyards), walls only around the rooms that have walls, open galleries
with a parapet where there are none, and the structural columns at their drawn
positions. A building drawn this way can be walked through and looked into.

Whether a plan's "corridor" is open to the air or inside the walls is not in the plan.
It is taken from the building's style sheet (`corridors: open | enclosed`), which comes
from the photos; without it a plan is not used.

Heights come from the lidar roof: the main roof level is shared between the full
floors, and a level that covers much less than the one below (a penthouse) sits
on top of it.
"""

from __future__ import annotations

import math

import numpy as np

from .meshkit import Scene, polygon_rings, wall_normal
from .style import Style

SLAB_M = 0.35            # floor slab thickness
PARAPET_M = 1.1          # solid railing along an open edge
ROOF_PARAPET_M = 0.5
GALLERY_GAP_M = 0.4      # a slab edge this close to a room wall is not an open edge
FULL_LEVEL = 0.5         # a level below this share of the largest one is partial
WINDOW_SILL = 0.9
WINDOW_HEAD = 2.5
WINDOW_BAY_M = 3.0


def level_heights(layout, roof_main: float, roof_top: float, ground: float) -> dict:
    """z of each level's floor, and the roof, from the lidar roof heights.

    roof_main / roof_top are absolute elevations of the main roof and the highest
    roof; `ground` is the elevation of the ground floor. Every level up to the highest
    full one is a floor, even a sparse ground level; a small level above that is a
    penthouse standing on the main roof."""
    area = {lv.level: lv.slab.area for lv in layout.levels}
    big = max(area.values())
    top_full = max(lv for lv in area if area[lv] >= FULL_LEVEL * big)
    floors = [lv for lv in sorted(area) if lv <= top_full]
    above = [lv for lv in sorted(area) if lv > top_full]
    fh = (roof_main - ground) / len(floors)
    z = {lv: ground + i * fh for i, lv in enumerate(floors)}
    for i, lv in enumerate(above):
        z[lv] = roof_main + i * ((roof_top - roof_main) / max(1, len(above)))
    return {"z": z, "floor_height": fh, "full": floors, "partial": above,
            "roof_main": roof_main, "roof_top": max(roof_top, roof_main)}


def _segments(poly):
    for ring in polygon_rings(poly):
        yield from zip(ring, ring[1:] + ring[:1], strict=True)


def _parts(geom):
    return [g for g in getattr(geom, "geoms", [geom]) if g.geom_type == "Polygon"
            and g.area > 0.05]


def generate_from_plan(scene: Scene, group: str, layout, style: Style, ground_z,
                       roof_main: float, roof_top: float, flat_roof: bool = True) -> dict:
    """Add one building to the scene from its layout. Returns counts for the report."""
    from shapely.geometry import Point

    wall_mat = scene.color_material("wall", style.wall, roughness=0.9, texture="plaster")
    trim_mat = scene.color_material("trim", style.trim, roughness=0.85, texture="plaster")
    glass_mat = scene.color_material("glass", style.glass, roughness=0.15, metallic=0.4)
    roof_mat = scene.color_material("roof", style.roof or "#8c8c88", roughness=0.95,
                                    texture="gravel")
    under_mat = scene.color_material("soffit", style.trim, roughness=1.0, texture="plaster")
    walls = scene.mesh(group, "building", wall_mat)
    trim = scene.mesh(group, "building", trim_mat)
    soffit = scene.mesh(group, "building", under_mat)
    glass = scene.mesh(group, "window", glass_mat)
    roof = scene.mesh(group, "roof", roof_mat)

    ground = float(np.median([ground_z(x, y) for lv in layout.levels[:1]
                              for x, y in [(c.x, c.y) for c in
                                           [lv.slab.representative_point()]]]
                             + [ground_z(*p) for p in _sample_points(layout.levels[0].slab)]))
    h = level_heights(layout, roof_main, roof_top, ground)
    z, fh = h["z"], h["floor_height"]
    counts = {"levels": len(z), "slab_edges": 0, "parapets": 0, "room_walls": 0, "columns": 0,
              "windows": 0}
    ordered = sorted(z)
    open_corridors = style.corridors != "enclosed"
    for lv in layout.levels:
        if lv.level not in z:
            continue
        if not open_corridors:
            # Enclosed corridors: the whole floor is under walls (courtyards stay open).
            # The ground floor takes the footprint of the floor above it, because a sparse
            # ground-level plan leaves the rest as a solid base, not stilts.
            whole = lv.slab
            if lv.level == ordered[0] and len(ordered) > 1:
                above = next(x for x in layout.levels if x.level == ordered[1])
                whole = lv.slab.union(above.slab)
            lv = type(lv)(lv.level, lv.slab, whole.buffer(0), lv.columns, lv.rooms)
        z0 = z[lv.level]
        top_of_level = h["roof_main"] if lv.level == h["full"][-1] else (
            z[ordered[ordered.index(lv.level) + 1]] - SLAB_M if lv.level != ordered[-1]
            else h["roof_top"])
        ceiling = top_of_level
        on_grade = lv.level == ordered[0] and abs(z0 - ground) < 0.45
        slab = lv.slab.buffer(0)
        # --- floor slab (not when it is the ground itself)
        if not on_grade:
            for part in _parts(slab):
                trim.polygon_cap(part, z0, up=True)
                soffit.polygon_cap(part, z0 - SLAB_M, up=False)
                trim.prism_walls(part, z0 - SLAB_M, z0)
            counts["slab_edges"] += len(_parts(slab))
        # --- room walls
        for part in _parts(lv.enclosed):
            walls.prism_walls(part, z0, ceiling - (SLAB_M if lv.level != ordered[-1] else 0.0))
            counts["room_walls"] += len(list(_segments(part)))
            counts["windows"] += _glaze(glass, part, z0, min(fh, ceiling - z0), style)
        # --- parapets along open edges
        enclosed_zone = lv.enclosed.buffer(GALLERY_GAP_M) if not lv.enclosed.is_empty else None
        if not on_grade:
            for p, q in (seg for part in _parts(slab) for seg in _segments(part)):
                mid = Point((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
                if enclosed_zone is not None and enclosed_zone.contains(mid):
                    continue
                if math.dist(p, q) < 0.2:
                    continue
                trim.wall(p, q, z0, z0 + PARAPET_M)
                trim.wall(q, p, z0, z0 + PARAPET_M, offset=0.14)
                nx, ny = wall_normal(p, q)
                a = (p[0] + nx * 0, p[1] + ny * 0, z0 + PARAPET_M)
                b = (q[0], q[1], z0 + PARAPET_M)
                c = (q[0] - nx * 0.14, q[1] - ny * 0.14, z0 + PARAPET_M)
                d = (p[0] - nx * 0.14, p[1] - ny * 0.14, z0 + PARAPET_M)
                trim.quad(a, d, c, b)
                counts["parapets"] += 1
        # --- columns
        size = 0.6
        floor_z = ground - 0.3 if on_grade else z0
        for cx, cy in lv.columns:
            base = min(floor_z, float(ground_z(cx, cy)) - 0.2) if lv.level == ordered[0] \
                else z0
            trim.box((cx, cy, base), (size, size, ceiling - base))
            counts["columns"] += 1
    # --- roof over the main roof level, and over the penthouse
    top_full = layout.levels[[lv.level for lv in layout.levels].index(h["full"][-1])]
    for part in _parts(top_full.slab.buffer(0)):
        if flat_roof:
            roof.polygon_cap(part, h["roof_main"], up=True)
            trim.prism_walls(part, h["roof_main"] - SLAB_M, h["roof_main"] + ROOF_PARAPET_M)
        else:                      # a pitched roof is added by the caller; close the ceiling
            soffit.polygon_cap(part, h["roof_main"], up=False)
    for lv in layout.levels:
        if lv.level in h["partial"] and lv.level in z and z[lv.level] >= h["roof_main"] - 0.1:
            for part in _parts(lv.slab.buffer(0)):
                roof.polygon_cap(part, h["roof_top"], up=True)
    counts["floor_height"] = round(fh, 2)
    counts["ground"] = round(ground, 2)
    return counts


def _sample_points(poly, n: int = 24):
    minx, miny, maxx, maxy = poly.bounds
    from shapely.geometry import Point

    pts, step = [], max((maxx - minx) / 6, 1.0)
    for x in np.arange(minx, maxx, step):
        for y in np.arange(miny, maxy, step):
            if poly.contains(Point(x, y)):
                pts.append((x, y))
    return pts[:n] or [(poly.centroid.x, poly.centroid.y)]


def _glaze(glass, room, z0: float, height: float, style: Style) -> int:
    """A ribbon of glass along the walls of an enclosed room, set just off the wall."""
    if style.windows == "none" or height < 2.6:
        return 0
    n = 0
    for p, q in _segments(room):
        length = math.dist(p, q)
        if length < 2.4:
            continue
        nx, ny = wall_normal(p, q)
        ux, uy = (q[0] - p[0]) / length, (q[1] - p[1]) / length
        bays = max(1, int(length // WINDOW_BAY_M))
        w = (length / bays) * style.window_ratio
        for i in range(bays):
            s = (i + 0.5) * length / bays - w / 2
            a = (p[0] + ux * s + nx * 0.04, p[1] + uy * s + ny * 0.04)
            b = (a[0] + ux * w, a[1] + uy * w)
            glass.quad((a[0], a[1], z0 + WINDOW_SILL), (b[0], b[1], z0 + WINDOW_SILL),
                       (b[0], b[1], z0 + min(WINDOW_HEAD, height - 0.4)),
                       (a[0], a[1], z0 + min(WINDOW_HEAD, height - 0.4)))
            n += 1
    return n
