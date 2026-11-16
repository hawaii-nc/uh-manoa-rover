"""A walkway network for the rover, built from the surveyed walkway polygons.

OpenStreetMap has few UH footpaths, so a route planned on it runs down roads.
UH's campus map instead publishes every walkway, ramp, stair tread, curb and wall
as a polygon. This module turns those into a graph a rover can plan on:

1. the walkable surface (walkway + ramp, minus anything blocked) is cleaned up:
   hairline gaps between touching polygons are closed, strips too narrow for the
   rover are dropped;
2. its centerline (skeleton) becomes the edges, each with its width, its steepest
   grade measured on the lidar, and whether it is a ramp;
3. flights of stairs become `stairs` edges, so the planner knows they exist and
   that they are not for wheels;
4. marked crosswalks become `crosswalk` edges, with a flag for whether both ends
   have a curb cut;
5. where the survey leaves a break, short links over grass or gravel join the
   pieces, flagged `gap`, so a data break is visible and not silently bridged.

Everything is measured from the data. The two assumptions are named in `WalkParams`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import networkx as nx
import numpy as np

from .graph import EDGE_DEFAULTS, CampusGraph


@dataclass
class WalkParams:
    res: float = 0.25               # raster cell, meters
    close_m: float = 0.5            # gaps up to this between touching polygons are healed
    min_width_m: float = 1.0        # ASSUMPTION: a strip narrower than this is not a path
    spur_m: float = 2.0             # skeleton branches shorter than this are noise
    simplify_m: float = 0.6         # centerline simplification tolerance
    fragment_m: float = 6.0         # pieces with less path than this are dropped
    gap_max_m: float = 12.0         # ASSUMPTION: longest break bridged over grass
    gap_max_grade: float = 0.12
    stairs_snap_m: float = 5.0
    crossing_snap_m: float = 12.0   # how far a crossing's end may be from a path node
    snap_to_surface_m: float = 4.0  # how far a crossing's end may be from walkable surface
    curb_cut_radius_m: float = 1.5
    ramp_cut_radius_m: float = 3.0
    grade_window_m: float = 6.0     # longer windows average out lidar height noise (~2 % over 3 m)


@dataclass
class WalkReport:
    nodes: int = 0
    edges: int = 0
    length_m: float = 0.0
    components_before_repair: int = 0
    components_after: int = 0
    fragments_dropped: int = 0
    gap_links: int = 0
    gap_length_m: float = 0.0
    stairs_edges: int = 0
    stairs_only_links: int = 0
    crosswalks: int = 0
    crosswalks_unsnapped: int = 0
    crosswalks_with_curb_cuts: int = 0
    crossing_candidates: int = 0
    ramp_edges: int = 0
    by_kind: dict = field(default_factory=dict)
    widths: dict = field(default_factory=dict)
    steep_share: float = 0.0


# ---- surface -------------------------------------------------------------------------


def _raster(poly, x0, y0, res, shape) -> np.ndarray:
    """Boolean grid (row 0 = south) of the cells inside `poly`, holes respected."""
    from PIL import Image, ImageDraw

    rows, cols = shape
    img = Image.new("L", (cols, rows), 0)
    draw = ImageDraw.Draw(img)
    for part in getattr(poly, "geoms", [poly]):
        if part.geom_type != "Polygon" or part.is_empty:
            continue
        draw.polygon([((x - x0) / res, (y - y0) / res) for x, y in part.exterior.coords], fill=255)
        for hole in part.interiors:
            draw.polygon([((x - x0) / res, (y - y0) / res) for x, y in hole.coords], fill=0)
    return np.asarray(img) > 0


def walkable_surface(by: dict, bbox, params: WalkParams):
    """Union of walkway and ramp polygons, with the blocked classes removed and hairline
    gaps closed. `by` maps a land-cover class to its polygons."""
    import shapely
    from shapely.ops import unary_union

    box = shapely.box(*bbox)
    walk = unary_union([g for c in ("walkway", "ramp") for g in by.get(c, [])]).intersection(box)
    # Heal hairline gaps between touching walkway polygons first ...
    h = params.close_m / 2
    walk = walk.buffer(h, join_style="mitre").buffer(-h, join_style="mitre")
    # ... then remove what blocks wheels, so closing can never bridge a wall or curb.
    blocked = blocked_surface(by)
    if not blocked.is_empty:
        walk = walk.difference(blocked)
    return walk


def blocked_surface(by: dict):
    """Everything that blocks wheels at ground level: curbs, walls, planters, water and
    flights of stairs (blocked as a whole, including the gaps between treads)."""
    from shapely.ops import unary_union

    parts = [g for c in ("curb", "wall", "rock_wall", "planter", "water", "fence")
             for g in by.get(c, [])]
    parts += [t.buffer(0.35) for t in by.get("stairs", [])]
    return unary_union(parts)


# ---- centerline ----------------------------------------------------------------------


def skeleton_chains(mask: np.ndarray, res: float, spur_m: float):
    """Skeleton of a mask as nodes and chains.

    Returns (nodes, chains): nodes[i] = (row, col) of a junction or end; chains = list of
    (a, b, [(row, col), ...]) between node ids a and b. Short dead-end spurs are pruned."""
    from scipy import ndimage
    from skimage.morphology import skeletonize

    skel = skeletonize(mask)
    ids = -np.ones(mask.shape, dtype=np.int64)
    pts = np.argwhere(skel)
    if len(pts) == 0:
        return [], []
    ids[pts[:, 0], pts[:, 1]] = np.arange(len(pts))
    shifts = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
    nbrs = [[] for _ in range(len(pts))]
    rows, cols = mask.shape
    for dr, dc in shifts:
        rr, cc = pts[:, 0] + dr, pts[:, 1] + dc
        ok = (rr >= 0) & (rr < rows) & (cc >= 0) & (cc < cols)
        other = np.where(ok, ids[np.clip(rr, 0, rows - 1), np.clip(cc, 0, cols - 1)], -1)
        for i in np.nonzero(other >= 0)[0]:
            nbrs[i].append(int(other[i]))
    deg = np.array([len(n) for n in nbrs])
    is_node = deg != 2
    node_grid = np.zeros(mask.shape, dtype=bool)
    node_grid[pts[is_node, 0], pts[is_node, 1]] = True
    labels, n_clusters = ndimage.label(node_grid, structure=np.ones((3, 3)))
    cluster_of = labels[pts[:, 0], pts[:, 1]] - 1          # -1 for chain interiors
    used = np.zeros(len(pts), dtype=bool)
    chains = []
    for p in np.nonzero(is_node)[0]:
        for q in nbrs[p]:
            if is_node[q] and cluster_of[q] == cluster_of[p]:
                continue
            if not is_node[q] and used[q]:
                continue
            path, prev, cur = [p, q], p, q
            while not is_node[cur]:
                used[cur] = True
                nxt = [n for n in nbrs[cur] if n != prev and n not in path[-3:]]
                if not nxt:
                    break
                prev, cur = cur, nxt[0]
                path.append(cur)
            if cluster_of[cur] < 0:
                continue
            a, b = int(cluster_of[p]), int(cluster_of[cur])
            if a == b and len(path) < 8:
                continue
            chains.append((a, b, [(int(pts[i, 0]), int(pts[i, 1])) for i in path]))
    centroids = ndimage.center_of_mass(node_grid, labels, range(1, n_clusters + 1))
    nodes = [(float(r), float(c)) for r, c in centroids]
    # Prune short spurs hanging off a junction (twice: pruning exposes new ones).
    min_px = spur_m / res
    for _ in range(2):
        degree = np.zeros(len(nodes), dtype=int)
        for a, b, _path in chains:
            degree[a] += 1
            degree[b] += 1
        keep = []
        for a, b, path in chains:
            spur = (degree[a] == 1 and degree[b] >= 3) or (degree[b] == 1 and degree[a] >= 3)
            if spur and len(path) < min_px:
                continue
            keep.append((a, b, path))
        if len(keep) == len(chains):
            break
        chains = keep
    return nodes, chains


# ---- the graph -----------------------------------------------------------------------


def _grade(line, d_mid: float, ground_z, window: float) -> float:
    """Grade over a window centered on distance d_mid along a line (|rise| / run)."""
    d0, d1 = max(d_mid - window / 2, 0.0), min(d_mid + window / 2, line.length)
    if d1 - d0 < 0.5:
        return 0.0
    a, b = line.interpolate(d0), line.interpolate(d1)
    run = math.dist((a.x, a.y), (b.x, b.y))
    return abs(float(ground_z(b.x, b.y)) - float(ground_z(a.x, a.y))) / run if run > 0.1 else 0.0


def clean_ground(rasters, building_polys, margin_m: float = 1.5, smooth_m: float = 1.0):
    """Ground elevation for judging grades. Next to a building the lidar ground is unreliable
    (eaves shadow it, foundations step up), so cells within `margin_m` of a footprint are
    dropped and filled from the nearest trustworthy cell, then lightly smoothed."""
    from scipy import ndimage
    from shapely.ops import unary_union

    shape = rasters.dtm.shape
    bad = np.zeros(shape, dtype=bool)
    if building_polys:
        near = unary_union([p.buffer(margin_m) for p in building_polys])
        bad = _raster(near, rasters.x0, rasters.y0, rasters.res, shape)
    z = rasters.dtm.copy()
    if bad.any() and not bad.all():
        idx = ndimage.distance_transform_edt(bad, return_distances=False, return_indices=True)
        z = z[tuple(idx)]
    return ndimage.gaussian_filter(z, smooth_m / rasters.res)


def build_walk_graph(by: dict, rasters, bbox, crosswalks=(), params: WalkParams | None = None,
                     passages=None, building_polys=(), crossings=()):
    """Build the network. `by`: land-cover class -> polygons; `crosswalks`: coordinate lists
    for marked crossings; `crossings`: `crossings.Crossing` objects (any tier: the usable ones
    join the network before gap repair, `candidate` ones are added last and flagged, and the
    planner ignores them unless told otherwise); `passages`: extra walkable polygons (open
    ground floors). Returns (CampusGraph, WalkReport)."""
    import shapely
    from scipy import ndimage
    from shapely.geometry import LineString, Point
    from shapely.ops import unary_union

    params = params or WalkParams()
    rep = WalkReport()
    surface = walkable_surface(by, bbox, params)
    if passages is not None and not passages.is_empty:
        surface = unary_union([surface, passages])
    x0, y0, x1, y1 = bbox
    res = params.res
    shape = (int(math.ceil((y1 - y0) / res)), int(math.ceil((x1 - x0) / res)))
    mask = _raster(surface, x0, y0, res, shape)
    # Drawing a polygon bleeds about one cell past its edge, which would bridge a 0.3 m wall
    # or curb. Erase the blocked shapes from the grid so a thin barrier still cuts the path.
    blocked = blocked_surface(by)
    if not blocked.is_empty:
        mask &= ~_raster(blocked.buffer(res * 0.5), x0, y0, res, shape)
    r = max(1, int(round(params.min_width_m / 2 / res)))
    yy, xx = np.ogrid[-r:r + 1, -r:r + 1]
    mask = ndimage.binary_opening(mask, structure=(xx * xx + yy * yy) <= r * r)
    half_width = ndimage.distance_transform_edt(mask) * res
    nodes_px, chains = skeleton_chains(mask, res, params.spur_m)

    zsm = clean_ground(rasters, list(building_polys))

    def ground_z(x, y):
        return _bilinear(zsm, rasters, x, y)

    ramps = unary_union(by.get("ramp", [])) if by.get("ramp") else None
    if ramps is not None:
        shapely.prepare(ramps)
    road_zone = unary_union(by.get("road", [])) if by.get("road") else None
    if road_zone is not None:
        shapely.prepare(road_zone)

    def world(rc):
        return (x0 + (rc[1] + 0.5) * res, y0 + (rc[0] + 0.5) * res)

    g = nx.Graph()
    node_id = {}

    def node_at(xy) -> str:
        key = (round(xy[0], 1), round(xy[1], 1))
        if key not in node_id:
            node_id[key] = f"w{len(node_id)}"
            g.add_node(node_id[key], x=float(xy[0]), y=float(xy[1]), label=None)
        return node_id[key]

    def add_edge(u, v, **attrs):
        if u == v or math.dist((g.nodes[u]["x"], g.nodes[u]["y"]),
                               (g.nodes[v]["x"], g.nodes[v]["y"])) < 0.05:
            return
        data = {**EDGE_DEFAULTS, **attrs}
        data["length"] = float(attrs.get("length") or math.dist(
            (g.nodes[u]["x"], g.nodes[u]["y"]), (g.nodes[v]["x"], g.nodes[v]["y"])))
        if g.has_edge(u, v) and g.edges[u, v]["length"] <= data["length"]:
            return
        g.add_edge(u, v, **data)

    for a, b, path in chains:
        pts = [world(p) for p in path]
        pts[0], pts[-1] = world(nodes_px[a]), world(nodes_px[b])
        line = LineString(pts).simplify(params.simplify_m)
        if line.length < 0.3:
            continue
        widths = np.array([2 * half_width[p] for p in path])
        width, width_min = float(np.median(widths)), float(np.percentile(widths, 10))
        coords = list(line.coords)
        for (ax, ay), (bx, by_) in zip(coords, coords[1:], strict=False):
            seg = LineString([(ax, ay), (bx, by_)])
            mid = seg.interpolate(0.5, normalized=True)
            d_mid = line.project(mid)
            on_ramp = ramps is not None and ramps.contains(mid)
            ramp_kind = None
            if on_ramp:
                at_road = road_zone is not None and road_zone.distance(mid) <= 3.0
                ramp_kind = "curb" if at_road else "access"
            add_edge(node_at((ax, ay)), node_at((bx, by_)), kind="ramp" if on_ramp else "walkway",
                     slope=_grade(line, d_mid, ground_z, params.grade_window_m),
                     width=width, width_min=width_min, surface="ramp" if on_ramp else "walkway",
                     source="survey", ramp_kind=ramp_kind)

    # Drop fragments too short to be a path.
    for comp in list(nx.connected_components(g)):
        total = sum(d["length"] for _, _, d in g.subgraph(comp).edges(data=True))
        if total < params.fragment_m:
            g.remove_nodes_from(comp)
            rep.fragments_dropped += 1
    rep.components_before_repair = nx.number_connected_components(g)

    surf_buf = surface.buffer(0.3)
    shapely.prepare(surf_buf)
    node_list = list(g.nodes)
    xy = np.array([(g.nodes[n]["x"], g.nodes[n]["y"]) for n in node_list]) if node_list \
        else np.zeros((0, 2))

    def nearest_nodes(p, k=6, radius=8.0):
        if not len(xy):
            return []
        d = np.hypot(xy[:, 0] - p[0], xy[:, 1] - p[1])
        order = np.argsort(d)[:k]
        return [(float(d[i]), node_list[i]) for i in order if d[i] <= radius]

    def inside_share(a, b) -> float:
        n = max(2, int(math.dist(a, b) / 0.5))
        pts = [Point(a[0] + (b[0] - a[0]) * t / n, a[1] + (b[1] - a[1]) * t / n)
               for t in range(n + 1)]
        return float(np.mean([surf_buf.contains(p) for p in pts]))

    # --- stairs
    treads = by.get("stairs", [])
    if treads:
        flights = unary_union([t.buffer(0.35) for t in treads])
        before = {n: i for i, comp in enumerate(nx.connected_components(g)) for n in comp}
        for fl in getattr(flights, "geoms", [flights]):
            if fl.area < 1.5:
                continue
            rect = fl.minimum_rotated_rectangle
            if rect.geom_type != "Polygon":
                continue
            c = list(rect.exterior.coords)[:4]
            mids = [((c[i][0] + c[(i + 1) % 4][0]) / 2, (c[i][1] + c[(i + 1) % 4][1]) / 2)
                    for i in range(4)]
            # A flight is walked along one axis: the ends are the pair of opposite sides whose
            # midpoints both sit close to the path network.
            best_pair, best_score = None, None
            for pair in ((0, 2), (1, 3)):
                hits = [nearest_nodes(mids[i], k=1, radius=params.stairs_snap_m) for i in pair]
                if all(hits) and hits[0][0][1] != hits[1][0][1]:
                    score = hits[0][0][0] + hits[1][0][0]
                    if best_score is None or score < best_score:
                        best_pair, best_score = pair, score
            ends = [mids[i] for i in best_pair] if best_pair else [mids[0], mids[2]]
            found = []
            for e in ends:
                near = nearest_nodes(e, k=1, radius=params.stairs_snap_m)
                found.append(near[0][1] if near else None)
            if best_pair and found[0] and found[1] and found[0] != found[1]:
                a, b = found
                run = math.dist(ends[0], ends[1])
                rise = abs(float(ground_z(*ends[0])) - float(ground_z(*ends[1])))
                add_edge(a, b, kind="stairs", slope=rise / max(run, 0.1), complexity=0.3,
                         surface="stairs", source="survey", length=max(run, 1.0))
                rep.stairs_edges += 1
                if before.get(a) != before.get(b):
                    rep.stairs_only_links += 1

    # --- marked crosswalks
    curbs = unary_union(by.get("curb", [])) if by.get("curb") else None
    if curbs is not None:
        shapely.prepare(curbs)
    ramp_zone = ramps

    def has_curb_cut(p) -> bool:
        """Evidence of a curb cut at a crossing end: a mapped ramp beside it, or a gap in the
        mapped curb. Never evidence against: the survey draws curbs continuously, so a
        curb nearby proves nothing and the answer stays unknown."""
        pt = Point(p)
        if ramp_zone is not None and ramp_zone.distance(pt) <= params.ramp_cut_radius_m:
            return True
        return curbs is None or curbs.distance(pt) > params.curb_cut_radius_m

    def nearest_edge(q, radius):
        """(u, v, nearest point) of the closest path edge within `radius` of q."""
        best = None
        for u, v, d in g.edges(data=True):
            if d["kind"] not in ("walkway", "ramp") or d.get("gap"):
                continue
            a, b = (g.nodes[u]["x"], g.nodes[u]["y"]), (g.nodes[v]["x"], g.nodes[v]["y"])
            dx, dy = b[0] - a[0], b[1] - a[1]
            span = dx * dx + dy * dy
            t = 0.0 if span == 0 else max(0.0, min(1.0, ((q[0] - a[0]) * dx + (q[1] - a[1]) * dy)
                                                  / span))
            proj = (a[0] + t * dx, a[1] + t * dy)
            dist = math.dist(q, proj)
            if dist <= radius and (best is None or dist < best[0]):
                best = (dist, u, v, proj)
        return None if best is None else best[1:]

    def split_edge(u, v, point) -> str:
        """Put a node at `point` on the edge u-v (or reuse an end very close to it)."""
        for end in (u, v):
            if math.dist((g.nodes[end]["x"], g.nodes[end]["y"]), point) < 0.5:
                return end
        attrs = {k: val for k, val in g.edges[u, v].items() if k != "length"}
        mid = node_at(point)
        g.remove_edge(u, v)
        add_edge(u, mid, **attrs)
        add_edge(mid, v, **attrs)
        return mid

    def attach(p):
        """A graph node where a crossing meets the walkway: the end is first moved onto the
        nearest walkable surface (crossings are drawn to the curb line, in the street), then
        an existing node very near it, or a new node joined by a short connector."""
        from shapely.ops import nearest_points

        if surface.is_empty:
            return None
        on_surface = nearest_points(surface, Point(p))[0]
        if on_surface.distance(Point(p)) > params.snap_to_surface_m:
            return None
        q = (on_surface.x, on_surface.y)
        near = nearest_nodes(q, k=8, radius=params.crossing_snap_m)
        for d, n in near:
            if d < 1.0:
                return n
        # Skeleton nodes sit at bends and junctions only, so the crossing may meet the
        # middle of a long straight path: split that path where the crossing arrives.
        hit = nearest_edge(q, params.crossing_snap_m)
        if hit is not None:
            u, v, proj = hit
            if inside_share(q, proj) >= 0.85:
                mid = split_edge(u, v, proj)
                if math.dist(q, proj) <= 0.5:
                    return mid
                new = node_at(q)
                add_edge(new, mid, kind="walkway", slope=0.0, surface="walkway",
                         source="connector")
                return new
        for _d, n in near:
            if inside_share(q, (g.nodes[n]["x"], g.nodes[n]["y"])) >= 0.85:
                new = node_at(q)
                add_edge(new, n, kind="walkway", slope=0.0, surface="walkway", source="connector")
                return new
        return None

    # A centerline that runs along a flight of treads is stairs, whatever the skeleton called it.
    if treads:
        zone = unary_union([t.buffer(0.35) for t in treads])
        shapely.prepare(zone)
        for u, v, d in list(g.edges(data=True)):
            if d["kind"] == "walkway" and not d.get("gap"):
                mid = Point((g.nodes[u]["x"] + g.nodes[v]["x"]) / 2,
                            (g.nodes[u]["y"] + g.nodes[v]["y"]) / 2)
                if zone.contains(mid):
                    d.update(kind="stairs", surface="stairs", source="survey_treads")
                    rep.stairs_edges += 1

    # --- every mapped ramp is a piece of path of its own. Narrow or short ramps vanish from the
    # skeleton (they are only a metre or two wide) and the skeleton passes beside the others, so
    # the ramp's axis is added from its polygon, with the grade measured end to end.
    for ramp in by.get("ramp", []):
        for part in getattr(ramp, "geoms", [ramp]):
            if part.geom_type != "Polygon" or part.area < 2.0:
                continue
            axis = _long_axis(part)
            if axis is None or axis[2] < 2.0:
                continue
            e1, e2, length, width = axis
            near1 = {n for _d, n in nearest_nodes(e1, 6, 1.5)}
            near2 = {n for _d, n in nearest_nodes(e2, 6, 1.5)}
            if any(m in near2 for n in near1 for m in g.neighbors(n)):
                continue                      # the skeleton already runs through it
            a, b = attach(e1), attach(e2)
            if a is None or b is None or a == b:
                continue
            rise = abs(_mean_z(ground_z, e1) - _mean_z(ground_z, e2))
            kind = "curb" if road_zone is not None and part.distance(road_zone) <= 1.5 else "access"
            add_edge(a, b, kind="ramp", slope=rise / max(length, 0.1), width=width,
                     width_min=width, surface="ramp", source="survey_ramp", ramp_kind=kind,
                     length=max(length, 1.0))
            rep.ramp_edges += 1

    from .crossings import USABLE, Crossing

    def add_crossing(c) -> bool:
        a, b = attach(c.a), attach(c.b)
        if a is None or b is None or a == b:
            rep.crosswalks_unsnapped += 1
            return False
        line = LineString([c.a, c.b])
        add_edge(a, b, kind="crosswalk", slope=_grade(line, line.length / 2, ground_z, 6.0),
                 complexity=0.6, curb_cut=c.curb_cut, surface="crosswalk", source=c.tier,
                 width=None, tier=c.tier, evidence=", ".join(c.evidence) or None, road=c.road)
        return True

    given = []
    for coords in crosswalks:
        if len(coords) < 2:
            continue
        c = Crossing(tuple(coords[0]), tuple(coords[-1]), math.dist(coords[0], coords[-1]),
                     "marked", ["marked crosswalk"])
        c.curb_cut_a, c.curb_cut_b = (True if has_curb_cut(e) else None for e in (c.a, c.b))
        given.append(c)
    for c in [*given, *[c for c in crossings if c.tier in USABLE]]:
        if add_crossing(c):
            rep.crosswalks += 1
            rep.crosswalks_with_curb_cuts += int(c.curb_cut is True)

    # --- breaks in the survey: short links over grass or gravel, always flagged
    blocked = unary_union([gm for c in ("building_slab", "wall", "rock_wall", "water", "road",
                                        "curb", "stairs", "planter")
                           for gm in by.get(c, [])])
    shapely.prepare(blocked)
    comp_of = {n: i for i, comp in enumerate(nx.connected_components(g)) for n in comp}
    node_list = list(g.nodes)
    xy = np.array([(g.nodes[n]["x"], g.nodes[n]["y"]) for n in node_list]) if node_list \
        else np.zeros((0, 2))
    cands = []
    if len(xy):
        from scipy.spatial import cKDTree

        tree = cKDTree(xy)
        for i, n in enumerate(node_list):
            for d, j in zip(*tree.query(xy[i], k=min(12, len(xy)),
                                        distance_upper_bound=params.gap_max_m), strict=False):
                if np.isfinite(d) and j < len(node_list) and comp_of[node_list[j]] != comp_of[n]:
                    cands.append((float(d), n, node_list[j]))
    parent = {c: c for c in set(comp_of.values())}

    def find(c):
        while parent[c] != c:
            parent[c] = parent[parent[c]]
            c = parent[c]
        return c

    grass = unary_union(by.get("grass", []) + by.get("gravel", []) + by.get("dirt", [])
                        + by.get("stepstone", [])) if by.get("grass") else None
    for d, u, v in sorted(cands):
        cu, cv = find(comp_of[u]), find(comp_of[v])
        if cu == cv:
            continue
        a, b = (g.nodes[u]["x"], g.nodes[u]["y"]), (g.nodes[v]["x"], g.nodes[v]["y"])
        line = LineString([a, b])
        if blocked.intersects(line.buffer(0.2)):
            continue
        grade = _grade(line, line.length / 2, ground_z, line.length)
        if grade > params.gap_max_grade:
            continue
        on_grass = grass is not None and line.intersection(grass.buffer(0.3)).length > 0.5 * d
        add_edge(u, v, kind="walkway", slope=grade, complexity=0.5, gap=True,
                 surface="grass" if on_grass else "gap", source="gap_repair", width=None)
        parent[cu] = cv
        rep.gap_links += 1
        rep.gap_length_m += d
    rep.components_after = nx.number_connected_components(g)

    # Crossings with nothing but geometry behind them: kept, flagged, never used by default.
    for c in crossings:
        if c.tier not in USABLE and add_crossing(c):
            rep.crossing_candidates += 1

    graph = CampusGraph(g)
    rep.nodes, rep.edges = g.number_of_nodes(), g.number_of_edges()
    for _, _, d in g.edges(data=True):
        rep.length_m += d["length"]
        rep.by_kind[d["kind"]] = rep.by_kind.get(d["kind"], 0) + d["length"]
    ws = [d["width"] for _, _, d in g.edges(data=True) if d.get("width")]
    if ws:
        rep.widths = {p: round(float(np.percentile(ws, p)), 1) for p in (10, 50, 90)}
    walk_edges = [d for _, _, d in g.edges(data=True) if d["kind"] in ("walkway", "ramp")]
    if walk_edges:
        steep = sum(d["length"] for d in walk_edges if d["slope"] > 0.083)
        rep.steep_share = steep / sum(d["length"] for d in walk_edges)
    return graph, rep


def _long_axis(poly):
    """(end 1, end 2, length, width) of the long axis of a polygon's minimum rotated rectangle:
    the ends are the middles of the two short sides."""
    rect = poly.minimum_rotated_rectangle
    if rect.geom_type != "Polygon":
        return None
    c = list(rect.exterior.coords)[:4]
    sides = [math.dist(c[i], c[(i + 1) % 4]) for i in range(4)]
    i = 0 if sides[0] >= sides[1] else 1
    short = [(c[(i + 1) % 4], c[(i + 2) % 4]), (c[(i + 3) % 4], c[i])]
    mid = [((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) for a, b in short]
    return mid[0], mid[1], max(sides[0], sides[1]), min(sides[0], sides[1])


def _mean_z(ground_z, p, r: float = 0.75) -> float:
    """Ground height at p, averaged over a small cross to damp lidar noise."""
    pts = [(p[0] + dx, p[1] + dy) for dx, dy in ((0, 0), (r, 0), (-r, 0), (0, r), (0, -r))]
    return float(np.mean([float(ground_z(*q)) for q in pts]))


def _bilinear(grid, rasters, x, y):
    from .lidar import bilinear

    return bilinear(grid, (np.asarray(x) - rasters.x0) / rasters.res - 0.5,
                    (np.asarray(y) - rasters.y0) / rasters.res - 0.5)


def landmark_connectivity(graph: CampusGraph, polygons: dict, within_m: float = 25.0) -> dict:
    """For named building polygons: the nearest walkway node, its component, and whether
    all of them are in one component (so a route between them exists)."""
    comp = {n: i for i, c in enumerate(nx.connected_components(graph.g)) for n in c}
    out = {}
    for name, poly in polygons.items():
        best = None
        for n in graph.g.nodes:
            d = poly.distance(Point_(graph.xy(n)))
            if best is None or d < best[0]:
                best = (d, n)
        out[name] = {"node": best[1], "distance_m": round(best[0], 1),
                     "component": comp[best[1]]} if best and best[0] <= within_m else None
    comps = {v["component"] for v in out.values() if v}
    return {"landmarks": out, "all_connected": len(comps) == 1 and all(out.values())}


def Point_(xy):  # noqa: N802 - shapely Point without importing it at module load
    from shapely.geometry import Point

    return Point(xy)
