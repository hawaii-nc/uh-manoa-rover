"""Procedural building generator: massing + style sheet -> labelled meshes.

Every wall of every roof-level block gets a facade built floor by floor:
windows (grid, ribbon, curtain wall or slits), sun shading (fins, eyebrows
or open-air lanai corridors), a ground floor (solid, arcade or storefront),
then a roof, and doors where a door is mapped. Parts are labelled `building`, `window`, `door` and
`roof`, so segmentation ground truth comes for free.
"""

from __future__ import annotations

import math

import numpy as np

from .massing import Massing
from .meshkit import Scene, polygon_rings, wall_normal
from .style import Style

MAX_DOOR_SNAP_M = 10.0   # a mapped door further than this from any wall is ignored
DEFAULT_ROOF = "#8c8c88"
MIN_FACADE_M = 2.4      # shorter wall pieces stay blank
PARAPET_M = 0.45
FOUNDATION_M = 2.5      # walls reach below the base so slopes never show a gap


def floor_height(massing: Massing, style: Style) -> tuple[int, float]:
    """(number of floors, height of one floor) for the building's main roof level."""
    main = max(massing.blocks, key=lambda b: b.polygon.area).height if massing.blocks \
        else massing.height
    main = massing.eave if massing.pitched else main
    floors = style.floors or max(1, round(main / 3.9))
    return floors, max(main / floors, 2.6)


def generate_building(scene: Scene, group: str, massing: Massing, style: Style,
                      door_points=(), roof_color: str | None = None) -> dict:
    """Build one building. `door_points` are mapped door positions (x, y): doors are
    only placed there, never guessed. `roof_color` is used when the style sheet
    leaves the roof colour open (e.g. a grey measured from lidar)."""
    wall_mat = scene.color_material("wall", style.wall, roughness=0.9)
    trim_mat = scene.color_material("trim", style.trim, roughness=0.85)
    glass_mat = scene.color_material("glass", style.glass, roughness=0.15, metallic=0.4)
    roof_mat = scene.color_material("roof", style.roof or roof_color or DEFAULT_ROOF,
                                    roughness=0.95)
    door_mat = scene.color_material("door", style.door, roughness=0.6)
    dark_mat = scene.material("shadow", (0.08, 0.08, 0.09), roughness=1.0)

    walls = scene.mesh(group, "building", wall_mat)
    trim = scene.mesh(group, "building", trim_mat)
    dark = scene.mesh(group, "building", dark_mat)
    glass = scene.mesh(group, "window", glass_mat)
    roof = scene.mesh(group, "roof", roof_mat)
    door = scene.mesh(group, "door", door_mat)

    base = massing.base_z
    floors, fh = floor_height(massing, style)
    ground_edges = []   # (length, p, q) of walls that reach the ground, for doors
    n_windows = 0

    for block in massing.blocks:
        top = base + block.height
        flat = not massing.pitched
        cap_z = top - (PARAPET_M if flat and block.polygon.area > 40 else 0.0)
        roof.polygon_cap(block.polygon, cap_z)
        for ring in polygon_rings(block.polygon):
            for p, q in zip(ring, ring[1:] + ring[:1], strict=True):
                length = math.dist(p, q)
                if length < 0.3:
                    continue
                nx, ny = wall_normal(p, q)
                mx, my = (p[0] + q[0]) / 2 + nx * 0.8, (p[1] + q[1]) / 2 + ny * 0.8
                outside = massing.height_at(mx, my)     # roof height just outside this wall
                if outside >= block.height - 0.6:
                    continue                             # buried in a taller or equal block
                at_ground = outside < 1.0
                walls.wall(p, q, base - FOUNDATION_M if at_ground else base + outside, top)
                if flat and cap_z < top:                 # inside face of the parapet
                    walls.wall(q, p, cap_z, top)
                if length < MIN_FACADE_M:
                    continue
                if at_ground and length >= 4.0:
                    ground_edges.append((length, p, q))
                first = 0 if at_ground else math.ceil((outside - 0.3) / fh)
                last = int((block.height + 0.4) // fh)
                n_windows += _facade(p, q, length, base, fh, first, last, style,
                                     glass, trim, dark)

    if massing.pitched and massing.roof is not None:
        _pitched_roof(roof, massing)
    doors = _doors(ground_edges, base, door_points, door)
    return {"floors": floors, "floor_height": round(fh, 2), "blocks": len(massing.blocks),
            "windows": n_windows, "doors": doors, "pitched": massing.pitched}


def _facade(p, q, length, base, fh, first, last, style, glass, trim, dark) -> int:
    ux, uy = (q[0] - p[0]) / length, (q[1] - p[1]) / length
    nx, ny = wall_normal(p, q)
    yaw = math.atan2(uy, ux)
    bays = max(1, int(length // style.bay_m))
    bay = style.bay_m
    margin = (length - bays * bay) / 2

    def at(s: float, z: float, out: float = 0.0):
        return (p[0] + ux * s + nx * out, p[1] + uy * s + ny * out, z)

    def rect(mesh, s0, s1, z0, z1, out):
        mesh.quad(at(s0, z0, out), at(s1, z0, out), at(s1, z1, out), at(s0, z1, out))

    def slab(s0, s1, z, depth, thick):       # horizontal shelf sticking out of the wall
        c = at((s0 + s1) / 2, z, depth / 2)
        trim.box(c, (s1 - s0, depth, thick), yaw)

    def post(s, z0, z1, size, out):          # vertical column or fin
        c = at(s, z0, out)
        trim.box(c, (size[0], size[1], z1 - z0), yaw)

    count = 0
    for k in range(first, last):
        z = base + k * fh
        ground = k == 0
        if ground and style.ground_floor == "arcade":
            rect(dark, 0.25, length - 0.25, z + 0.05, z + fh - 0.55, 0.03)
            for i in range(bays + 1):
                post(margin + i * bay, z, z + fh - 0.55, (0.45, 0.45), 0.25)
            continue
        if ground and style.ground_floor == "storefront":
            rect(glass, 0.3, length - 0.3, z + 0.15, z + fh - 0.7, 0.04)
            slab(0.0, length, z + fh - 0.6, 1.2, 0.15)
            count += 1
            continue

        if style.shading == "lanai":
            # Open-air corridor: a dark opening above a solid railing, columns between bays.
            rect(dark, 0.2, length - 0.2, z + 1.05, z + fh - 0.5, 0.03)
            for i in range(bays + 1):
                post(margin + i * bay, z + 1.05, z + fh - 0.5, (0.3, 0.3), 0.1)
            continue

        sill, head = z + 0.95, z + min(fh - 0.6, 0.95 + fh * 0.48)
        if style.windows == "grid":
            w = bay * style.window_ratio
            for i in range(bays):
                s = margin + i * bay + (bay - w) / 2
                rect(glass, s, s + w, sill, head, 0.04)
            count += bays
        elif style.windows == "slit":
            for i in range(bays):
                s = margin + i * bay + bay / 2 - 0.35
                rect(glass, s, s + 0.7, z + 0.6, z + fh - 0.5, 0.04)
            count += bays
        elif style.windows == "ribbon":
            rect(glass, max(margin * 0.5, 0.3), length - max(margin * 0.5, 0.3), sill, head, 0.04)
            count += 1
        elif style.windows == "curtain":
            rect(glass, 0.25, length - 0.25, z + 0.2, z + fh - 0.35, 0.04)
            for i in range(1, bays * 2):
                post(margin + i * bay / 2, z + 0.2, z + fh - 0.35, (0.08, 0.1), 0.08)
            count += 1

        if style.shading == "eyebrows" and style.windows != "none":
            slab(0.1, length - 0.1, head + 0.1, 0.8, 0.12)
    if style.shading == "fins" and last > first:
        z0, z1 = base + first * fh + 0.3, base + last * fh - 0.1
        for i in range(bays + 1):
            post(margin + i * bay, z0, z1, (0.16, 0.55), 0.27)
    return count


def _pitched_roof(roof, massing: Massing) -> None:
    """Roof surface straight from the lidar heights, sampled every meter."""
    step = max(1, round(1.0 / massing.res))
    grid = massing.roof[::step, ::step]
    solid = grid > 0
    z = massing.base_z + np.maximum(grid, massing.eave)
    rows, cols = grid.shape
    xs = massing.x0 + (np.arange(cols) * step + 0.5) * massing.res
    ys = massing.y0 + (np.arange(rows) * step + 0.5) * massing.res
    gx, gy = np.meshgrid(xs, ys)
    verts = np.c_[gx.ravel(), gy.ravel(), z.ravel()]
    ok = solid[:-1, :-1] & solid[1:, :-1] & solid[:-1, 1:] & solid[1:, 1:]
    r, c = np.nonzero(ok)
    a = r * cols + c
    b, d, e = a + 1, a + cols, a + cols + 1
    roof.add(verts, np.r_[np.c_[a, b, e], np.c_[a, e, d]])


def _doors(ground_edges, base, door_points, door) -> int:
    """One door per mapped door point, on the nearest ground-level wall."""
    placed = 0
    for px, py in door_points:
        best = None
        for length, p, q in ground_edges:
            ux, uy = (q[0] - p[0]) / length, (q[1] - p[1]) / length
            s = min(max((px - p[0]) * ux + (py - p[1]) * uy, 1.2), length - 1.2)
            d = math.dist((px, py), (p[0] + ux * s, p[1] + uy * s))
            if best is None or d < best[0]:
                best = (d, p, ux, uy, s, wall_normal(p, q))
        if best is None or best[0] > MAX_DOOR_SNAP_M:
            continue
        _, p, ux, uy, s, (nx, ny) = best

        def at(t, z, p=p, ux=ux, uy=uy, nx=nx, ny=ny):
            return (p[0] + ux * t + nx * 0.07, p[1] + uy * t + ny * 0.07, z)

        door.quad(at(s - 1.0, base), at(s + 1.0, base), at(s + 1.0, base + 2.3),
                  at(s - 1.0, base + 2.3))
        placed += 1
    return placed
