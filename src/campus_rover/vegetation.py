"""Trees and plants that look like plants: species-shaped trunks and crowns of leaf cards.

The old trees were low-poly blobs. These are built from what the UH tree layer says (species,
height, crown radius, trunk width) and what UH's photos of the route show: tall coconut palms
along Holmes Hall, a huge flat-topped monkeypod over the Campus Center lawn, ti plants and
ferns at building bases.

Everything here is drawn by this module (leaf textures included, so there is nothing to
license). A tree is a trunk in the scene (so it still collides like before) plus a crown that
is a shared prototype placed as an instance: a few dozen prototypes cover thousands of trees.

Shapes are archetypes (`archetype_for`): species names map to a shape, anything unknown gets a
neutral spreading crown. Leaf colours and flower flecks are an artist's reading of the photos,
not measurements.
"""

from __future__ import annotations

import functools
import io
import math

import numpy as np

from .assets import Asset, Primitive

GREENS = {
    "broad": [(30, 74, 34), (42, 92, 40), (58, 112, 48), (76, 128, 56), (96, 142, 62)],
    "pale": [(70, 104, 56), (88, 124, 66), (108, 140, 78), (126, 152, 90), (96, 120, 72)],
    "fine": [(52, 104, 44), (68, 122, 52), (86, 138, 60), (104, 150, 70), (60, 110, 48)],
}

# species keyword -> archetype, first match wins
SPECIES = [
    (("niu", "coconut"), "coconut"),
    (("loulu", "fan palm", "nihoa", "washingtonia", "bismarck"), "fan_palm"),
    (("palm",), "feather_palm"),
    (("monkeypod", "rain tree", "albizia", "samanea"), "umbrella"),
    (("banyan", "bo tree", "kukui", "candlenut", "mango", "false olive", "breadfruit", "ficus",
      "kou", "mahogany"), "dome"),
    (("plumeria", "frangipani", "pua kenikeni"), "plumeria"),
    (("pūhala", "puhala", "screwpine", "pandan", "hala"), "screwpine"),
    (("paperbark", "melaleuca", "eucalyptus", "ironwood", "norfolk"), "narrow"),
]
CROWN_CLASSES = {"umbrella": (6.0, 10.0, 14.0), "dome": (3.0, 5.5, 8.5),
                 "spreading": (2.5, 4.5, 7.5), "plumeria": (1.8, 2.8), "narrow": (1.6, 2.6),
                 "screwpine": (1.6, 2.4)}


BARK = {"default": (0.36, 0.29, 0.22), "coconut": (0.56, 0.5, 0.42),
        "feather_palm": (0.5, 0.46, 0.4), "fan_palm": (0.45, 0.4, 0.34),
        "umbrella": (0.28, 0.22, 0.17), "narrow": (0.78, 0.73, 0.63),
        "plumeria": (0.5, 0.46, 0.4), "screwpine": (0.45, 0.38, 0.3)}


def archetype_for(species: str | None, family: str | None = "", habit: str | None = "") -> str:
    name = (species or "").lower()
    for words, arch in SPECIES:
        if any(w in name for w in words):
            return arch
    if "arecaceae" in (family or "").lower():
        return "feather_palm"
    if "pandanaceae" in (family or "").lower():
        return "screwpine"
    if "moraceae" in (family or "").lower():
        return "dome"
    return "spreading"


# ---- leaf textures -----------------------------------------------------------------------


def _ellipse(cx, cy, length, width, angle, n=14):
    t = np.linspace(0, 2 * math.pi, n, endpoint=False)
    # a leaf is pointed at both ends: squeeze the width toward the tips
    x = np.cos(t) * length / 2
    y = np.sin(t) * width / 2 * (0.55 + 0.45 * np.abs(np.sin(t)))
    c, s = math.cos(angle), math.sin(angle)
    return [(cx + x[i] * c - y[i] * s, cy + x[i] * s + y[i] * c) for i in range(n)]


def _png(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


@functools.cache
def texture(kind: str, size: int = 384) -> bytes:
    """An RGBA leaf texture. Cut-outs are hard-edged (alpha-tested), so the edges stay clean at
    any distance."""
    from PIL import Image, ImageDraw

    rng = np.random.default_rng(sum(map(ord, kind)))
    s = size * 2                                    # draw big, shrink for smooth edges
    w, h = (s // 2, s) if kind.startswith(("frond", "fern", "strap", "ti")) else (s, s)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def shade(pal, k=None):
        c = pal[rng.integers(len(pal))] if k is None else pal[k]
        j = rng.integers(-8, 9)
        return (max(c[0] + j, 0), max(c[1] + j, 0), max(c[2] + j, 0), 255)

    if kind in ("broad", "pale"):
        pal = GREENS[kind]
        for _ in range(70):
            r = math.sqrt(rng.random()) * s * 0.42
            a = rng.random() * 2 * math.pi
            cx, cy = s / 2 + math.cos(a) * r, s / 2 + math.sin(a) * r
            ln, wd = s * rng.uniform(0.17, 0.26), s * rng.uniform(0.07, 0.11)
            ang = rng.random() * math.pi
            d.polygon(_ellipse(cx, cy, ln, wd, ang), fill=shade(pal))
            dx, dy = math.cos(ang) * ln * 0.45, math.sin(ang) * ln * 0.45
            d.line([(cx - dx, cy - dy), (cx + dx, cy + dy)], fill=(30, 60, 30, 255), width=3)
    elif kind in ("fine", "fine_flower"):
        pal = GREENS["fine"]
        for _ in range(26):                          # feathery twigs radiating from the middle
            a = rng.random() * 2 * math.pi
            length = s * rng.uniform(0.25, 0.46)
            for k in range(14):
                f = (k + 1) / 14
                px, py = s / 2 + math.cos(a) * length * f, s / 2 + math.sin(a) * length * f
                for side in (-1, 1):
                    ang = a + side * 1.05
                    d.polygon(_ellipse(px + math.cos(ang) * 6, py + math.sin(ang) * 6,
                                       s * 0.075, s * 0.026, ang, 8), fill=shade(pal))
        if kind == "fine_flower":                     # pale pink flecks (monkeypod, shower trees)
            for _ in range(70):
                r = math.sqrt(rng.random()) * s * 0.4
                a = rng.random() * 2 * math.pi
                cx, cy = s / 2 + math.cos(a) * r, s / 2 + math.sin(a) * r
                d.ellipse([cx - 9, cy - 9, cx + 9, cy + 9],
                          fill=(236, 170 + int(rng.integers(40)), 178, 255))
    elif kind in ("frond", "frond_feather"):
        mid = w / 2
        d.polygon([(mid - 6, h), (mid + 6, h), (mid + 2, 4), (mid - 2, 4)], fill=(86, 104, 48, 255))
        n = 30 if kind == "frond" else 20
        for k in range(n):
            f = k / n
            y = h * (0.97 - 0.92 * f)
            length = w * (0.49 if kind == "frond" else 0.44) * (1 - 0.6 * f ** 1.5)
            base = h * (0.034 if kind == "frond" else 0.05)          # leaflet width at the rachis
            for side in (-1, 1):
                fwd = length * (0.55 if kind == "frond" else 0.4)    # leaflets lean toward the tip
                tip = (mid + side * length, y - fwd + length * 0.18)
                col = shade([(34, 86, 34), (48, 104, 42), (66, 122, 48), (84, 138, 54)])
                d.polygon([(mid, y + base / 2), (mid, y - base / 2), tip], fill=col)
    elif kind == "fan":
        cx, cy = w / 2, h / 2
        for k in range(18):
            a = 2 * math.pi * k / 18
            tip = (cx + math.cos(a) * w * 0.46, cy + math.sin(a) * h * 0.46)
            lo = (cx + math.cos(a - 0.17) * w * 0.40, cy + math.sin(a - 0.17) * h * 0.40)
            hi = (cx + math.cos(a + 0.17) * w * 0.40, cy + math.sin(a + 0.17) * h * 0.40)
            d.polygon([(cx, cy), lo, tip, hi], fill=shade(GREENS["fine"]))
    elif kind in ("ti", "strap"):
        mid = w / 2
        red = kind == "ti"
        pts = [(mid, h * 0.99)]
        n = 20
        half = [(w * 0.34 * math.sin(math.pi * (i / n) ** 0.8) + 2, h * (0.99 - 0.97 * i / n))
                for i in range(1, n + 1)]
        left = [(mid - dx, y) for dx, y in half]
        right = [(mid + dx, y) for dx, y in half]
        base = (52, 104, 44, 255) if not red else (64, 104, 40, 255)
        d.polygon(pts + left + right[::-1], fill=base)
        if red:                                      # ti leaves blush red-purple toward the edge
            k = n // 2 + 6
            d.polygon(pts + left[:k] + [(mid - w * 0.12, h * 0.30)] + right[:k][::-1],
                      fill=(132, 70, 52, 255))
            core = [(mid - w * 0.17, h * 0.75), (mid, h * 0.08), (mid + w * 0.17, h * 0.75)]
            d.polygon(pts + core, fill=(70, 112, 46, 255))
        d.line([(mid, h * 0.99), (mid, h * 0.02)], fill=(110, 140, 70, 255), width=5)
    elif kind == "fern":
        mid = w / 2
        d.line([(mid, h), (mid, 6)], fill=(60, 96, 44, 255), width=6)
        for k in range(30):
            f = k / 30
            y = h * (0.97 - 0.9 * f)
            length = w * 0.46 * (1 - 0.7 * f ** 1.3)
            for side in (-1, 1):
                d.polygon(_ellipse(mid + side * length / 2, y - length * 0.18, length, 11,
                                   -side * 0.35, 8), fill=shade(GREENS["fine"]))
    out = img.resize((w // 2, h // 2), Image.LANCZOS)
    a = np.asarray(out)[..., 3]
    out.putalpha(Image.fromarray(np.where(a > 70, 255, 0).astype(np.uint8)))   # hard cut-out
    return _png(out)


# ---- geometry helpers ----------------------------------------------------------------------


class _Builder:
    def __init__(self) -> None:
        self.v: list = []
        self.f: list = []
        self.uv: list = []

    def card(self, c, u, v, uvs=((0, 0), (1, 0), (1, 1), (0, 1))) -> None:
        """A quad centred at c spanned by half-vectors u and v."""
        i = len(self.v)
        for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            self.v.append(tuple(np.asarray(c) + sx * np.asarray(u) + sy * np.asarray(v)))
        self.uv += list(uvs)
        self.f += [(i, i + 1, i + 2), (i, i + 2, i + 3)]

    def strip(self, pts, widths, side) -> None:
        """A ribbon along a polyline; texture runs from the first point (v = 0) to the last."""
        i0 = len(self.v)
        for k, (p, w) in enumerate(zip(pts, widths, strict=True)):
            self.v.append(tuple(np.asarray(p) - side * w / 2))
            self.v.append(tuple(np.asarray(p) + side * w / 2))
            t = k / (len(pts) - 1)
            self.uv += [(0, t), (1, t)]
            if k:
                a = i0 + 2 * (k - 1)
                self.f += [(a, a + 1, a + 3), (a, a + 3, a + 2)]

    def primitive(self, png: bytes) -> Primitive:
        return Primitive(np.asarray(self.v, float), np.asarray(self.f, np.int64),
                         np.asarray(self.uv, float), (1.0, 1.0, 1.0), png, alpha=True)


def _unit(a):
    a = np.asarray(a, float)
    n = np.linalg.norm(a)
    return a / n if n > 1e-9 else a


def _frame(normal, roll):
    """Two perpendicular unit vectors spanning the plane with this normal."""
    n = _unit(normal)
    ref = np.array([0.0, 0.0, 1.0]) if abs(n[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    a = _unit(np.cross(ref, n))
    b = np.cross(n, a)
    c, s = math.cos(roll), math.sin(roll)
    return a * c + b * s, -a * s + b * c


def _crown_cards(b: _Builder, rng, radius, thickness, count, size, flat=0.0, hollow=0.55,
                 lift=0.0) -> None:
    """Leaf cards over the shell of an oblate spheroid (the crown)."""
    for _ in range(count):
        while True:
            p = rng.normal(size=3)
            p /= max(np.linalg.norm(p), 1e-9)
            if p[2] > -0.35:                         # crowns are domes: little under the middle
                break
        shell = hollow + (1 - hollow) * rng.random() ** 0.5
        c = np.array([p[0] * radius, p[1] * radius, p[2] * thickness / 2]) * shell
        c[2] += thickness / 2 + lift
        out = _unit([p[0], p[1], p[2] * (1.2 - flat)])
        normal = _unit(out * 0.55 + np.array([0, 0, 0.9]) + rng.normal(scale=0.25, size=3))
        u, v = _frame(normal, rng.random() * 2 * math.pi)
        sz = size * rng.uniform(0.85, 1.15)
        b.card(c, u * sz, v * sz)


# ---- prototypes ---------------------------------------------------------------------------


def crown(archetype: str, radius: float, lod: int, seed: int) -> Asset:
    """A crown prototype with its base on z = 0 and the trunk top at the origin."""
    rng = np.random.default_rng(seed * 7919 + int(radius * 10))
    b = _Builder()
    if archetype in ("coconut", "feather_palm", "fan_palm"):
        kind = {"coconut": "frond", "feather_palm": "frond_feather", "fan_palm": "fan"}[archetype]
        n = {0: 16, 1: 10}[lod] if archetype != "fan_palm" else {0: 14, 1: 8}[lod]
        length = {"coconut": 5.4, "feather_palm": 3.2, "fan_palm": 1.6}[archetype] * radius
        width = {"coconut": 1.3, "feather_palm": 0.9, "fan_palm": 1.6}[archetype] * radius
        for i in range(n):
            a = 2 * math.pi * i / n + rng.uniform(-0.2, 0.2)
            up = rng.uniform(0.45, 0.95) if archetype != "fan_palm" else 0.1
            d_ = np.array([math.cos(a), math.sin(a), 0.0])
            side = np.array([-math.sin(a), math.cos(a), 0.0])
            if archetype == "fan_palm":              # a round fan held out on a stalk
                c = d_ * length * 0.55 + np.array([0, 0, 0.2])
                u, v = _frame(np.array([d_[0] * 0.5, d_[1] * 0.5, 1.0]), a)
                b.card(c, u * width / 2, v * width / 2)
                continue
            pts = [np.array([0, 0, 0.0]) + d_ * length * t
                   + np.array([0, 0, length * (up * t - (up + 0.2) * t * t * 0.85)])
                   for t in np.linspace(0, 1, 6 if lod == 0 else 4)]
            b.strip(pts, [width * (0.35 + 0.65 * math.sin(math.pi * min(t * 1.05, 1)))
                          for t in np.linspace(0, 1, len(pts))], side)
        tex = kind
        height = length * 0.6
    else:
        counts = {"umbrella": 520, "dome": 380, "spreading": 340, "plumeria": 120, "narrow": 200,
                  "screwpine": 0}[archetype]
        counts = int(counts * (radius / CROWN_CLASSES[archetype][1]) ** 1.2)
        counts = max(18, counts // (1 if lod == 0 else 3))
        size = {"umbrella": 1.1, "dome": 1.35, "spreading": 1.2, "plumeria": 0.9,
                "narrow": 0.8, "screwpine": 1.0}[archetype] * (1.0 if lod == 0 else 1.7)
        thickness = {"umbrella": radius * 0.55, "dome": radius * 1.3, "spreading": radius * 1.0,
                     "plumeria": radius * 0.9, "narrow": radius * 3.2, "screwpine": 0.5}[archetype]
        if archetype == "screwpine":
            n = 12 if lod == 0 else 7
            for i in range(n):
                a = 2 * math.pi * i / n + rng.uniform(-0.2, 0.2)
                d_ = np.array([math.cos(a), math.sin(a), 0.0])
                side = np.array([-math.sin(a), math.cos(a), 0.0])
                pts = [d_ * radius * 1.4 * t + np.array([0, 0, radius * (0.9 * t - 0.8 * t * t)])
                       for t in np.linspace(0, 1, 5)]
                b.strip(pts, [0.35 * radius * (1 - 0.8 * t) for t in np.linspace(0, 1, 5)], side)
            tex, height = "strap", radius
        else:
            _crown_cards(b, rng, radius, thickness, counts, size,
                         flat=0.5 if archetype == "umbrella" else 0.0)
            tex = {"umbrella": "fine_flower", "dome": "pale" if seed % 2 else "broad",
                   "spreading": "fine_flower" if seed % 2 else "fine", "plumeria": "broad",
                   "narrow": "fine"}[archetype]
            height = thickness
    asset = Asset(f"crown_{archetype}_{int(radius)}_{lod}_{seed}", "tree",
                  [b.primitive(texture(tex))],
                  {"archetype": archetype, "height": float(height), "radius": float(radius)})
    return asset


def shrub(kind: str, lod: int, seed: int) -> Asset:
    """Ground cover: ti plant, fern, grass tuft, low bush. Base on z = 0, about a metre across."""
    rng = np.random.default_rng(seed * 104729 + sum(map(ord, kind)))
    b = _Builder()
    n = {"ti": 14, "fern": 11, "grass": 16, "bush": 0}[kind] // (1 if lod == 0 else 2)
    tall = {"ti": 1.0, "fern": 0.7, "grass": 0.45, "bush": 0.9}[kind]
    for i in range(n):
        a = 2 * math.pi * i / max(n, 1) + rng.uniform(-0.25, 0.25)
        d_ = np.array([math.cos(a), math.sin(a), 0.0])
        side = np.array([-math.sin(a), math.cos(a), 0.0])
        out = rng.uniform(0.2, 0.6)
        lift = tall * rng.uniform(0.7, 1.0) * 1.6
        pts = [d_ * out * t + np.array([0, 0, lift * (1 - 0.55 * t * t) * t])
               for t in np.linspace(0, 1, 4)]
        wd = {"ti": 0.22, "fern": 0.34, "grass": 0.07, "bush": 0.0}[kind]
        b.strip(pts, [wd * (1 - 0.5 * t) for t in np.linspace(0, 1, 4)], side)
    if kind == "bush":
        _crown_cards(b, rng, 0.55, 0.9, 16 // (1 if lod == 0 else 2), 0.45, hollow=0.3)
    tex = {"ti": "ti", "fern": "fern", "grass": "strap", "bush": "broad"}[kind]
    return Asset(f"shrub_{kind}_{lod}_{seed}", "tree", [b.primitive(texture(tex, 256))],
                 {"archetype": kind})


def trunk_tube(points, radii, segments: int = 8):
    """Vertices and faces of a tube along a polyline (bottom open, top capped by taper)."""
    pts = [np.asarray(p, float) for p in points]
    verts, faces = [], []
    for k, (p, r) in enumerate(zip(pts, radii, strict=True)):
        a = pts[min(k + 1, len(pts) - 1)] - pts[max(k - 1, 0)]
        u, v = _frame(a, 0.0)
        for j in range(segments):
            t = 2 * math.pi * j / segments
            verts.append(tuple(p + (u * math.cos(t) + v * math.sin(t)) * r))
        if k:
            base = (k - 1) * segments
            for j in range(segments):
                j2 = (j + 1) % segments
                faces += [(base + j, base + j2, base + segments + j2),
                          (base + j, base + segments + j2, base + segments + j)]
    return np.asarray(verts, float), np.asarray(faces, np.int64)


def trunk_shape(archetype: str, height: float, radius: float, trunk_r: float, rng):
    """Trunk and limbs as [(points, radii)]: where the crown starts and what holds it up."""
    tubes = []
    if archetype in ("coconut", "feather_palm", "fan_palm"):
        lean = rng.uniform(-0.12, 0.12, size=2) * (2.0 if archetype == "coconut" else 0.6)
        top = height * (0.97 if archetype != "fan_palm" else 0.9)
        pts = [np.array([lean[0] * (k / 6) ** 2 * height, lean[1] * (k / 6) ** 2 * height,
                         top * k / 6]) for k in range(7)]
        r = [trunk_r * (1.5 - 0.65 * k / 6) for k in range(7)] if archetype == "coconut" \
            else [trunk_r * (1.15 - 0.3 * k / 6) for k in range(7)]
        tubes.append((pts, r))
        return tubes, pts[-1]
    if archetype == "screwpine":
        top = max(height * 0.5, 1.0)
        pts = [np.array([0, 0, top * k / 3]) for k in range(4)]
        tubes.append((pts, [trunk_r * (1.4 - 0.4 * k / 3) for k in range(4)]))
        return tubes, pts[-1]
    split = {"umbrella": 0.42, "dome": 0.4, "spreading": 0.45, "plumeria": 0.3,
             "narrow": 0.5}[archetype]
    top = height * split
    pts = [np.array([0, 0, top * k / 3]) for k in range(4)]
    tubes.append((pts, [trunk_r * (1.2 - 0.3 * k / 3) for k in range(4)]))
    n_limbs = {"umbrella": 5, "dome": 4, "spreading": 3, "plumeria": 4, "narrow": 2}[archetype]
    reach = radius * {"umbrella": 0.6, "dome": 0.5, "spreading": 0.5, "plumeria": 0.55,
                      "narrow": 0.2}[archetype]
    for i in range(n_limbs):
        a = 2 * math.pi * i / n_limbs + rng.uniform(-0.4, 0.4)
        end = np.array([math.cos(a) * reach, math.sin(a) * reach,
                        top + (height - top) * rng.uniform(0.35, 0.7)])
        mid = (np.array([0, 0, top]) + end) / 2 + np.array([0, 0, 0.1 * radius])
        tubes.append(([np.array([0, 0, top]), mid, end],
                      [trunk_r * 0.62, trunk_r * 0.45, trunk_r * 0.18]))
    return tubes, np.array([0, 0, top])


def place_tree(scene, cache: dict, x, y, z, height, radius, trunk_d, species, family, habit,
               seed: int, lod: int) -> str:
    """A tree of the right shape: trunk and limbs in the scene, the crown a shared prototype.
    Returns the archetype used."""
    arch = archetype_for(species, family, habit)
    rng = np.random.default_rng(seed)
    palm = arch in ("coconut", "feather_palm", "fan_palm")
    trunk_r = min(max(trunk_d / 2, 0.12 if palm else 0.08), 0.32 if palm else 1.1)
    height = max(height, 2.0)
    tubes, top = trunk_shape(arch, height, radius, trunk_r, rng)
    tint = BARK.get(arch, BARK["default"])
    bark = scene.material(f"bark_{arch}", tint, texture="bark")
    mesh = scene.mesh("trees", "tree", bark)
    for k, (pts, radii) in enumerate(tubes):
        if lod and k:
            continue                                # far trees: the trunk alone, no limbs
        if lod:
            pts, radii = [pts[0], pts[-1]], [radii[0], radii[-1]]
        v, f = trunk_tube([p + np.array([x, y, z]) for p in pts], radii, 10 if not lod else 5)
        mesh.add([tuple(r) for r in v], [list(r) for r in f])
    if palm:
        key = (arch, 1.0, lod, seed % 2)
        if key not in cache:
            cache[key] = crown(arch, 1.0, lod, seed % 2)
        s = min(max(radius / 3.0, 0.7), 1.4)
        scene.place(cache[key], "trees", x + top[0], y + top[1], z + top[2], rng.uniform(0, 6.283),
                    scale=s)
        return arch
    classes = CROWN_CLASSES[arch]
    cls = min(classes, key=lambda c: abs(c - radius))
    key = (arch, cls, lod, seed % 2)
    if key not in cache:
        cache[key] = crown(arch, cls, lod, seed % 2)
    asset = cache[key]
    sh = min(max(radius / cls, 0.6), 1.5)
    sv = min(max((height - top[2]) / max(asset.meta["height"], 0.5), 0.55), 1.7)
    scene.place(asset, "trees", x, y, z + top[2], rng.uniform(0, 6.283), scale=(sh, sh, sv))
    return arch


def place_ground_cover(scene, cache: dict, kind: str, x, y, z, seed: int, lod: int) -> None:
    key = (kind, lod, seed % 3)
    if key not in cache:
        cache[key] = shrub(kind, lod, seed % 3)
    rng = np.random.default_rng(seed)
    s = rng.uniform(0.8, 1.35)
    scene.place(cache[key], "plants", x, y, z, rng.uniform(0, 6.283), scale=(s, s, s))
