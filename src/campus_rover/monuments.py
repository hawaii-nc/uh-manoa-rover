"""The sculptures and monuments along the route, drawn from UH's photos of each piece.

UH's art layer gives every piece a name, an artist, a year and a photo
(`https://map.hawaii.edu/images/MA/Art/ART_0nn.jpg`), and the surveyed point where it stands. This
module turns the outdoor pieces nearest the route into recognisable shapes at those points. Murals
and indoor pieces are not drawn: they hang on walls inside buildings.

Sizes are read from the photos (a person or a bench gives the scale) except where the title states
them (Krypton is "1 x 6 x 18", feet). They are estimates, listed in `MONUMENTS`, so a check against
the real piece is worth doing. Colours are what the photos show.
"""

from __future__ import annotations

import math

import numpy as np

from .assets import Asset, Primitive

# keyword in the piece's name -> (builder name, what the size is based on)
MONUMENTS = {
    "gate of hope": ("gate_of_hope", "estimate from the photo: about 6 m tall"),
    "divers": ("divers", "estimate from the photo: a ribbed lens about 7 m long"),
    "krypton": ("krypton", "title: 1 x 6 x 18 ft = 0.30 x 1.83 x 5.49 m"),
    "fourth sign": ("fourth_sign", "estimate from the photo: about 9 m long, 2.5 m high"),
    "peace memorial": ("peace_memorial", "estimate from the photo: about 3 m tall"),
    "nth power": ("nth_power", "estimate from the photo: about 3 m tall"),
    "hina o na lani": ("hina", "estimate from the photo: about 1.5 m with its plinth"),
    "instructional steel": ("steel_connection", "estimate from the photo: about 3 m tall"),
    "spirit of loyalty": ("spirit_of_loyalty", "estimate from the photo: about 2 m tall"),
    "mind & heart": ("mind_and_heart", "estimate from the photo: about 1.8 m tall"),
    "chance meeting": ("chance_meeting", "life-size figures (about 1.75 m)"),
    "varney circle": ("varney_fountain", "estimate from the photo: basin 6.8 m across"),
}


# ---- geometry -----------------------------------------------------------------------------


class _Parts:
    """Meshes grouped by colour."""

    def __init__(self) -> None:
        self.by_color: dict = {}

    def add(self, color, verts, faces) -> None:
        v, f = self.by_color.setdefault(tuple(color), ([], []))
        n = sum(len(x) for x in v)
        v.append(np.asarray(verts, float))
        f.append(np.asarray(faces, np.int64) + n)

    def asset(self, name: str, cls: str, note: str, photo: str) -> Asset:
        prims = []
        for color, (v, f) in self.by_color.items():
            prims.append(Primitive(np.vstack(v), np.vstack(f), None, color))
        return Asset(name, cls, prims, {"source": "drawn for this project", "licence": "own work",
                                        "from": photo, "size": note})


def _rot(yaw=0.0, tilt=0.0, roll=0.0):
    cy, sy, cp, sp, cr, sr = (math.cos(yaw), math.sin(yaw), math.cos(tilt), math.sin(tilt),
                              math.cos(roll), math.sin(roll))
    rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
    ry = np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]])
    rx = np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]])
    return rz @ ry @ rx


def box(center, size, rot=None):
    sx, sy, sz = (s / 2 for s in size)
    v = np.array([[x * sx, y * sy, z * sz] for z in (-1, 1) for y in (-1, 1) for x in (-1, 1)])
    if rot is not None:
        v = v @ np.asarray(rot).T
    v = v + np.asarray(center)
    f = [(0, 2, 1), (1, 2, 3), (4, 5, 6), (5, 7, 6), (0, 1, 4), (1, 5, 4), (2, 6, 3), (3, 6, 7),
         (0, 4, 2), (2, 4, 6), (1, 3, 5), (3, 7, 5)]
    return v, f


def cylinder(p0, p1, r, seg=18, cut0=None, cut1=None, r1=None):
    """A cylinder (or cone) from p0 to p1. `cut0`/`cut1` = (plane normal) slice that end
    obliquely through the end point, as a pipe is cut."""
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    a = p1 - p0
    a /= np.linalg.norm(a)
    ref = np.array([0.0, 0.0, 1.0]) if abs(a[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    u = np.cross(ref, a)
    u /= np.linalg.norm(u)
    v = np.cross(a, u)
    ring = [(u * math.cos(t) + v * math.sin(t)) for t in np.linspace(0, 2 * math.pi, seg, False)]
    r1 = r if r1 is None else r1
    rows = []
    for p, rad, cut in ((p0, r, cut0), (p1, r1, cut1)):
        pts = []
        for d in ring:
            q = p + d * rad
            if cut is not None:
                n = np.asarray(cut, float)
                q = q + a * (np.dot(n, p - q) / np.dot(n, a))
            pts.append(q)
        rows.append(pts)
    verts = np.array(rows[0] + rows[1] + [p0, p1])
    faces = []
    for i in range(seg):
        j = (i + 1) % seg
        faces += [(i, j, j + seg), (i, j + seg, i + seg), (2 * seg, j, i),
                  (2 * seg + 1, i + seg, j + seg)]
    return verts, faces


def sphere(center, radii, detail=1):
    from .meshkit import _icosphere

    v, f = _icosphere(detail)
    return v * np.asarray(radii) + np.asarray(center), f


def annulus(center, normal, r_out, r_in, thick, seg=28):
    """A flat ring (a washer) with its axis along `normal`."""
    n = np.asarray(normal, float)
    n /= np.linalg.norm(n)
    ref = np.array([0.0, 0.0, 1.0]) if abs(n[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    u = np.cross(ref, n)
    u /= np.linalg.norm(u)
    w = np.cross(n, u)
    ang = np.linspace(0, 2 * math.pi, seg, False)
    ring = [(u * math.cos(t) + w * math.sin(t)) for t in ang]
    c = np.asarray(center, float)
    pts = []
    for off in (-thick / 2, thick / 2):
        for rad in (r_out, r_in):
            pts += [c + n * off + d * rad for d in ring]
    faces = []
    for i in range(seg):
        j = (i + 1) % seg
        o0, i0, o1, i1 = 0, seg, 2 * seg, 3 * seg
        faces += [(o0 + i, o0 + j, o1 + j), (o0 + i, o1 + j, o1 + i),            # outer wall
                  (i0 + j, i0 + i, i1 + i), (i0 + j, i1 + i, i1 + j),            # inner wall
                  (o1 + i, o1 + j, i1 + j), (o1 + i, i1 + j, i1 + i),            # top face
                  (o0 + j, o0 + i, i0 + i), (o0 + j, i0 + i, i0 + j)]            # bottom face
    return np.array(pts), faces


# ---- the pieces ---------------------------------------------------------------------------


def gate_of_hope() -> Asset:
    """Alexander Liberman, 1972: red-orange painted steel cylinders cut on the slant."""
    p, orange = _Parts(), (0.80, 0.38, 0.13)
    for a, b, r, cut in [((-2.3, 0, 0), (-1.5, 0.1, 1.7), 0.55, (0.5, 0, 0.87)),
                         ((-0.6, 0, 0), (-0.35, 0, 3.0), 0.8, (0.55, 0, 0.83)),
                         ((0.7, 0, 0), (1.1, 0, 2.5), 0.7, (-0.5, 0, 0.87)),
                         ((0.1, 0, 2.2), (1.9, 0.1, 4.3), 0.75, (0.4, 0, 0.9)),
                         ((-1.0, 0, 3.5), (2.3, 0, 6.3), 0.85, (-0.3, 0, 0.95))]:
        p.add(orange, *cylinder(a, b, r, 20, cut0=(0.3, 0, 0.95), cut1=cut))
    return p.asset("monument_gate_of_hope", "art_piece", MONUMENTS["gate of hope"][1],
                   "UH art photo ART_020")


def divers() -> Asset:
    """Robert Stackhouse, 1991: a boat's ribbed framework in red brass, lying on the lawn."""
    p, brass = _Parts(), (0.50, 0.37, 0.29)
    ribs = 11
    for k in range(ribs):
        y = (k - (ribs - 1) / 2) * 0.17
        half = 3.5 * math.sqrt(max(1 - (y / 0.98) ** 2, 0.02))
        xs = np.linspace(-half, half, 14)
        for x0, x1 in zip(xs, xs[1:], strict=False):
            z = 0.08 + 0.18 * (1 - ((x0 + x1) / 2 / half) ** 2)
            ang = math.atan2(0.0, 1.0)
            p.add(brass, *box(((x0 + x1) / 2, y, z), (x1 - x0, 0.05, 0.05), _rot(ang)))
    for x in np.linspace(-3.2, 3.2, 9):                          # the keel
        p.add(brass, *box((x, 0, 0.08), (0.7, 0.06, 0.06)))
    return p.asset("monument_divers", "art_piece", MONUMENTS["divers"][1], "UH art photo ART_022")


def krypton() -> Asset:
    """Bruce Hopper, 1973: a pale concrete monolith, 1 x 6 x 18 feet."""
    p = _Parts()
    p.add((0.68, 0.70, 0.66), *box((0, 0, 5.49 / 2), (0.30, 1.83, 5.49)))
    return p.asset("monument_krypton", "art_piece", MONUMENTS["krypton"][1],
                   "UH art photo ART_023")


def fourth_sign() -> Asset:
    """Tony Smith, 1976: black painted steel, two heavy angular arches joined by a low bar."""
    p, black = _Parts(), (0.11, 0.11, 0.12)
    for cx in (-3.0, 3.0):
        p.add(black, *box((cx - 1.2, 0, 1.2), (1.4, 1.8, 2.4), _rot(0, 0.12)))
        p.add(black, *box((cx + 1.2, 0, 1.2), (1.4, 1.8, 2.4), _rot(0, -0.12)))
        p.add(black, *box((cx, 0, 2.5), (3.4, 1.8, 0.6)))
    p.add(black, *box((0, 0, 0.5), (3.0, 1.8, 1.0)))
    return p.asset("monument_fourth_sign", "art_piece", MONUMENTS["fourth sign"][1],
                   "UH art photo ART_048")


def peace_memorial() -> Asset:
    """Koji, 1986: a five-tier granite pagoda on a stepped base."""
    p, granite = _Parts(), (0.62, 0.62, 0.60)
    p.add(granite, *box((0, 0, 0.4), (2.2, 2.2, 0.8)))
    p.add(granite, *box((0, 0, 1.15), (1.5, 1.5, 0.7)))
    p.add(granite, *box((0, 0, 1.85), (0.95, 0.95, 0.7)))
    p.add(granite, *sphere((0, 0, 2.55), (0.5, 0.5, 0.5)))
    p.add(granite, *cylinder((0, 0, 3.0), (0, 0, 3.55), 0.55, 4, r1=0.05))
    return p.asset("monument_peace_memorial", "art_piece", MONUMENTS["peace memorial"][1],
                   "UH art photo ART_024")


def nth_power() -> Asset:
    """Charles Watson, 1971: five interlocked weathering-steel rings."""
    p, rust = _Parts(), (0.46, 0.21, 0.13)
    for c, n in [((0, 0, 2.7), (1, 0.15, 0.2)), ((0.7, 0.6, 1.5), (0.3, 1, 0.5)),
                 ((-0.6, 0.3, 1.2), (1, 0.2, 0.9)), ((0.1, -0.5, 0.75), (0.2, 1, 0.1)),
                 ((0.8, -0.2, 0.55), (0.1, 0.3, 1))]:
        p.add(rust, *annulus(c, n, 0.95, 0.42, 0.22))
    return p.asset("monument_nth_power", "art_piece", MONUMENTS["nth power"][1],
                   "UH art photo ART_053")


def hina() -> Asset:
    """Greg Clurman, 1975: a seated figure in pink granite on a black plinth."""
    p = _Parts()
    p.add((0.05, 0.05, 0.06), *box((0, 0, 0.2), (1.3, 1.1, 0.4)))
    p.add((0.62, 0.56, 0.54), *sphere((0, 0, 0.95), (0.52, 0.4, 0.62), 2))
    p.add((0.62, 0.56, 0.54), *sphere((0, 0, 1.55), (0.3, 0.27, 0.28), 2))
    return p.asset("monument_hina", "art_piece", MONUMENTS["hina o na lani"][1],
                   "UH art photo ART_055")


def steel_connection() -> Asset:
    """AISC, 2004: bolted steel beam connections stacked into a teaching sculpture."""
    p, steel = _Parts(), (0.62, 0.41, 0.37)
    p.add(steel, *box((0, 0, 1.4), (0.3, 0.3, 2.8)))
    for i, z in enumerate((0.7, 1.35, 1.95, 2.6)):
        p.add(steel, *box((0, 0, z), (1.7 - 0.2 * i, 0.32, 0.26), _rot(i * 0.7)))
        p.add(steel, *box((0.5 * math.cos(i), 0.5 * math.sin(i), z + 0.3), (0.5, 0.5, 0.1)))
    p.add(steel, *annulus((0, 0, 0.25), (0, 0, 1), 1.7, 1.45, 0.5, 20))
    return p.asset("monument_steel_connection", "art_piece", MONUMENTS["instructional steel"][1],
                   "UH art photo ART_070")


def spirit_of_loyalty() -> Asset:
    """Rick Mills, 1995: a green-patina spire on a pale conical base."""
    p = _Parts()
    p.add((0.78, 0.76, 0.70), *cylinder((0, 0, 0), (0, 0, 0.95), 0.75, 14, r1=0.38))
    p.add((0.28, 0.55, 0.48), *cylinder((0, 0, 0.95), (0, 0, 2.25), 0.46, 4, r1=0.04))
    return p.asset("monument_spirit_of_loyalty", "art_piece", MONUMENTS["spirit of loyalty"][1],
                   "UH art photo ART_050")


def mind_and_heart() -> Asset:
    """Frank Sheriff, 1995: a twisting bronze flame on a dish and a stone pedestal."""
    p = _Parts()
    p.add((0.70, 0.68, 0.62), *cylinder((0, 0, 0), (0, 0, 0.85), 0.55, 12, r1=0.4))
    p.add((0.38, 0.27, 0.16), *cylinder((0, 0, 0.85), (0, 0, 0.95), 0.62, 16))
    for k in range(7):
        t = k / 6
        a = t * 2.6
        p.add((0.38, 0.27, 0.16), *sphere((0.1 * math.cos(a), 0.1 * math.sin(a), 1.05 + 0.75 * t),
                                          (0.36 * (1 - 0.6 * t), 0.2 * (1 - 0.4 * t), 0.17)))
    return p.asset("monument_mind_and_heart", "art_piece", MONUMENTS["mind & heart"][1],
                   "UH art photo ART_051")


def chance_meeting() -> Asset:
    """George Segal, 1991: three life-size figures in long coats talking at a street corner."""
    p, bronze = _Parts(), (0.21, 0.21, 0.17)
    figures = [(-0.45, 0.1, 1.78, 0.3), (0.15, 0.3, 1.70, -2.5), (0.5, -0.1, 1.72, 2.4)]
    for x, y, h, yaw in figures:
        c, s = math.cos(yaw), math.sin(yaw)
        p.add(bronze, *cylinder((x, y, 0.1), (x, y, h * 0.78), 0.2, 10, r1=0.17))        # coat
        for side in (-0.07, 0.07):
            p.add(bronze, *cylinder((x - s * side, y + c * side, 0.0),
                                    (x - s * side, y + c * side, 0.4), 0.05, 6))
        p.add(bronze, *sphere((x, y, h * 0.89), (0.1, 0.09, 0.12)))
        for side in (-1, 1):                                                    # arms
            p.add(bronze, *cylinder((x - s * side * 0.19, y + c * side * 0.19, h * 0.74),
                                    (x + c * 0.14 - s * side * 0.17, y + s * 0.14 + c * side * 0.17,
                                     h * 0.5), 0.045, 6))
    return p.asset("monument_chance_meeting", "art_piece", MONUMENTS["chance meeting"][1],
                   "UH art photo ART_008")


def varney_fountain() -> Asset:
    """Rempel and McIntyre, 1934: a round cast-concrete fountain on a circular lawn."""
    p, concrete = _Parts(), (0.82, 0.80, 0.76)
    p.add(concrete, *annulus((0, 0, 0.27), (0, 0, 1), 3.4, 2.95, 0.54, 36))
    p.add((0.38, 0.52, 0.55), *cylinder((0, 0, 0.1), (0, 0, 0.42), 2.95, 36))
    p.add(concrete, *cylinder((0, 0, 0), (0, 0, 1.1), 0.95, 20, r1=0.8))
    p.add(concrete, *cylinder((0, 0, 1.1), (0, 0, 1.5), 0.62, 20))
    return p.asset("monument_varney_fountain", "art_piece", MONUMENTS["varney circle"][1],
                   "UH art photo ART_049")


BUILDERS = {fn.__name__: fn for fn in (gate_of_hope, divers, krypton, fourth_sign, peace_memorial,
                                      nth_power, hina, steel_connection, spirit_of_loyalty,
                                      mind_and_heart, chance_meeting, varney_fountain)}


def monument_for(name: str | None, cache: dict | None = None) -> Asset | None:
    """The model for an art piece by its name, or None (murals and indoor pieces)."""
    text = (name or "").lower()
    for keyword, (builder, _note) in MONUMENTS.items():
        if keyword in text:
            if cache is None:
                return BUILDERS[builder]()
            if builder not in cache:
                cache[builder] = BUILDERS[builder]()
            return cache[builder]
    return None
