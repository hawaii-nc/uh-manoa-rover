"""Small mesh-building toolkit for the 3D world.

A `Scene` holds triangle meshes grouped by (group, class, material):

- `group`     where it sits in the scene tree, e.g. "buildings/holmes_hall"
- `class`     the semantic label a perception model learns, e.g. "window"
- `material`  a name in `Scene.materials` (colour, roughness, metalness, texture)

Coordinates are meters, Z up. Faces are counter-clockwise seen from outside.
Only NumPy is needed here; polygon capping uses `mapbox_earcut`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np


@dataclass
class Mesh:
    verts: list = field(default_factory=list)   # list of (k, 3) arrays
    faces: list = field(default_factory=list)   # list of (m, 3) int arrays, already offset
    count: int = 0

    def add(self, verts, faces) -> None:
        verts = np.asarray(verts, dtype=np.float64).reshape(-1, 3)
        faces = np.asarray(faces, dtype=np.int64).reshape(-1, 3)
        if len(faces) == 0:
            return
        self.verts.append(verts)
        self.faces.append(faces + self.count)
        self.count += len(verts)

    def arrays(self) -> tuple[np.ndarray, np.ndarray]:
        if not self.verts:
            return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64)
        return np.vstack(self.verts), np.vstack(self.faces)

    # ---- primitives --------------------------------------------------------------

    def quad(self, a, b, c, d) -> None:
        """Corners in counter-clockwise order seen from the front."""
        self.add([a, b, c, d], [[0, 1, 2], [0, 2, 3]])

    def wall(self, p, q, z0: float, z1: float, offset: float = 0.0) -> None:
        """Vertical rectangle from p to q (xy). Faces right of the p->q direction,
        which is outward for a counter-clockwise footprint ring. `offset` pushes
        it outward."""
        nx, ny = wall_normal(p, q)
        px, py = p[0] + nx * offset, p[1] + ny * offset
        qx, qy = q[0] + nx * offset, q[1] + ny * offset
        self.quad((px, py, z0), (qx, qy, z0), (qx, qy, z1), (px, py, z1))

    def box(self, center, size, yaw: float = 0.0) -> None:
        """Box with its base center at `center` (x, y, z) and size (sx, sy, sz)."""
        sx, sy, sz = size[0] / 2, size[1] / 2, size[2]
        c, s = math.cos(yaw), math.sin(yaw)
        pts = []
        for z in (0.0, sz):
            for dx, dy in ((-sx, -sy), (sx, -sy), (sx, sy), (-sx, sy)):
                pts.append((center[0] + dx * c - dy * s, center[1] + dx * s + dy * c,
                            center[2] + z))
        self.add(pts, _BOX_FACES)

    def cylinder(self, base, radius: float, height: float, segments: int = 10,
                 top_radius: float | None = None, cap: bool = True) -> None:
        top_radius = radius if top_radius is None else top_radius
        ang = np.linspace(0, 2 * math.pi, segments, endpoint=False)
        ring0 = np.c_[base[0] + radius * np.cos(ang), base[1] + radius * np.sin(ang),
                      np.full(segments, base[2])]
        ring1 = np.c_[base[0] + top_radius * np.cos(ang), base[1] + top_radius * np.sin(ang),
                      np.full(segments, base[2] + height)]
        i = np.arange(segments)
        j = (i + 1) % segments
        faces = np.r_[np.c_[i, j, j + segments], np.c_[i, j + segments, i + segments]]
        verts = np.vstack([ring0, ring1])
        if cap:
            verts = np.vstack([verts, [[base[0], base[1], base[2] + height]]])
            faces = np.r_[faces, np.c_[i + segments, j + segments,
                                       np.full(segments, 2 * segments)]]
        self.add(verts, faces)

    def blob(self, center, radii, seed: int = 0, detail: int = 1, wobble: float = 0.12) -> None:
        """Lumpy ellipsoid (tree canopies, bushes, heads)."""
        verts, faces = _icosphere(detail)
        rng = np.random.default_rng(seed)
        scale = 1.0 + wobble * rng.standard_normal(len(verts))
        v = verts * scale[:, None] * np.asarray(radii) + np.asarray(center)
        self.add(v, faces)

    def polygon_cap(self, polygon, z: float, up: bool = True) -> None:
        """Flat fill of a shapely Polygon (holes respected) at height z."""
        verts, faces = triangulate_polygon(polygon)
        if len(faces) == 0:
            return
        if not up:
            faces = faces[:, ::-1]
        self.add(np.c_[verts, np.full(len(verts), z)], faces)

    def sloped_wall(self, p, q, zp0: float, zp1: float, zq0: float, zq1: float) -> None:
        """Wall from p to q whose bottom and top follow the ground: different heights
        at each end. Faces right of p -> q."""
        self.quad((p[0], p[1], zp0), (q[0], q[1], zq0), (q[0], q[1], zq1), (p[0], p[1], zp1))

    def prism_walls(self, polygon, z0: float, z1: float) -> None:
        for ring in polygon_rings(polygon):
            for p, q in zip(ring, ring[1:] + ring[:1], strict=True):
                self.wall(p, q, z0, z1)


_BOX_FACES = np.array([
    [0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],          # bottom, top
    [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
    [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7],
])


def wall_normal(p, q) -> tuple[float, float]:
    """Unit normal pointing right of the direction p -> q."""
    dx, dy = q[0] - p[0], q[1] - p[1]
    length = math.hypot(dx, dy) or 1.0
    return dy / length, -dx / length


def polygon_rings(polygon) -> list[list[tuple[float, float]]]:
    """Exterior ring counter-clockwise, holes clockwise, without the closing point.
    With that winding, `wall()` always faces away from the solid."""
    from shapely.geometry.polygon import orient

    poly = orient(polygon, 1.0)
    return [list(r.coords)[:-1] for r in [poly.exterior, *poly.interiors]]


def triangulate_polygon(polygon) -> tuple[np.ndarray, np.ndarray]:
    import mapbox_earcut as earcut

    rings = polygon_rings(polygon)
    verts = np.array([p for ring in rings for p in ring], dtype=np.float64).reshape(-1, 2)
    ends = np.cumsum([len(r) for r in rings]).astype(np.uint32)
    if len(verts) < 3:
        return verts, np.zeros((0, 3), dtype=np.int64)
    tris = earcut.triangulate_float64(verts, ends).reshape(-1, 3).astype(np.int64)
    # earcut does not promise a winding; make every triangle counter-clockwise.
    a, b, c = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
    cross = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    tris[cross < 0] = tris[cross < 0][:, ::-1]
    return verts, tris[np.abs(cross) > 1e-9]


def _icosphere(detail: int) -> tuple[np.ndarray, np.ndarray]:
    if detail in _ICO_CACHE:
        return _ICO_CACHE[detail]
    t = (1 + 5 ** 0.5) / 2
    v = [(-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0), (0, -1, t), (0, 1, t),
         (0, -1, -t), (0, 1, -t), (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1)]
    f = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4),
         (11, 10, 2), (10, 7, 6), (7, 1, 8), (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8),
         (3, 8, 9), (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1)]
    verts = [np.array(p, dtype=float) / np.linalg.norm(p) for p in v]
    for _ in range(detail):
        cache: dict = {}
        out = []

        def mid(i, j, cache=cache):
            key = (min(i, j), max(i, j))
            if key not in cache:
                m = verts[i] + verts[j]
                verts.append(m / np.linalg.norm(m))
                cache[key] = len(verts) - 1
            return cache[key]

        for a, b, c in f:
            ab, bc, ca = mid(a, b), mid(b, c), mid(c, a)
            out += [(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)]
        f = out
    _ICO_CACHE[detail] = (np.array(verts), np.array(f, dtype=np.int64))
    return _ICO_CACHE[detail]


_ICO_CACHE: dict = {}


@dataclass
class Scene:
    """Meshes keyed by (group, semantic class, material), plus the material table."""

    parts: dict[tuple[str, str, str], Mesh] = field(default_factory=dict)
    materials: dict[str, dict] = field(default_factory=dict)
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0)   # subtracted on export
    meta: dict = field(default_factory=dict)
    assets: dict = field(default_factory=dict)       # name -> assets.Asset, stored once
    instances: list = field(default_factory=list)    # assets.Instance, one per placement

    def place(self, asset, group: str, x: float, y: float, z: float, yaw: float = 0.0,
              scale: float = 1.0) -> None:
        """Put one copy of a shared model in the scene."""
        from .assets import Instance

        self.assets[asset.name] = asset
        self.instances.append(Instance(asset.name, group, x, y, z, yaw, scale))

    def mesh(self, group: str, cls: str, material: str) -> Mesh:
        if material not in self.materials:
            raise KeyError(f"unknown material {material!r}")
        return self.parts.setdefault((group, cls, material), Mesh())

    def material(self, name: str, color, roughness: float = 0.9, metallic: float = 0.0,
                 opacity: float = 1.0, texture: str | None = None) -> str:
        """`texture` names a tiling detail map in campus_rover/textures; `color` tints it."""
        self.materials.setdefault(name, {
            "color": tuple(float(c) for c in color), "roughness": roughness,
            "metallic": metallic, "opacity": opacity, "texture": texture,
        })
        return name

    def color_material(self, prefix: str, hex_color: str, **kw) -> str:
        """Material named after its colour, e.g. wall_cfc6b4. Reused when repeated."""
        rgb = hex_to_rgb(hex_color)
        return self.material(f"{prefix}_{hex_color.lstrip('#').lower()}", rgb, **kw)

    def stats(self) -> dict:
        by_class: dict[str, int] = {}
        for (_, cls, _), mesh in self.parts.items():
            by_class[cls] = by_class.get(cls, 0) + sum(len(f) for f in mesh.faces)
        for inst in self.instances:
            asset = self.assets[inst.asset]
            by_class[asset.cls] = by_class.get(asset.cls, 0) + asset.triangles
        return {"parts": len(self.parts), "triangles": sum(by_class.values()),
                "instances": len(self.instances), "by_class": dict(sorted(by_class.items()))}


def hex_to_rgb(text: str) -> tuple[float, float, float]:
    text = text.lstrip("#")
    if len(text) != 6:
        raise ValueError(f"colour must look like #rrggbb, got {text!r}")
    return tuple(int(text[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
