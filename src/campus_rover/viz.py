"""Plot a campus graph with crowd density and planned routes (needs matplotlib)."""

from __future__ import annotations

from pathlib import Path

from .planner import Comparison, RoutePlanner
from .schedule import WeekTime


def plot_comparison(
    planner: RoutePlanner, comparison: Comparison, out: Path, title: str = ""
) -> Path:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.colors import Normalize
    except ImportError as e:
        raise ImportError('plotting needs matplotlib: pip install -e ".[viz]"') from e

    g = planner.graph.g
    t: WeekTime = comparison.chosen.depart
    fig, ax = plt.subplots(figsize=(11, 6.5))
    cmap = plt.get_cmap("YlOrRd")
    norm = Normalize(vmin=0, vmax=max(30.0, comparison.baseline.peak_density))

    for u, v, data in g.edges(data=True):
        (x1, y1), (x2, y2) = planner.graph.xy(u), planner.graph.xy(v)
        blocked = planner.why_impassable(data)
        if blocked:
            ax.plot([x1, x2], [y1, y2], color="#888", lw=1.5, ls=(0, (2, 2)), zorder=1)
            mx, my = planner.graph.midpoint(u, v)
            ax.text(
                mx,
                my,
                f"✕ {blocked.split(' (')[0]}",
                fontsize=7,
                color="#555",
                ha="center",
                va="center",
                zorder=5,
                bbox={"fc": "white", "ec": "none", "alpha": 0.7, "pad": 0.5},
            )
        else:
            density = planner.crowd.density(u, v, t)
            ax.plot(
                [x1, x2],
                [y1, y2],
                color=cmap(norm(density)),
                lw=5,
                solid_capstyle="round",
                zorder=2,
            )

    def draw(plan, color, lw, ls, label):
        xs = [planner.graph.xy(n)[0] for n in plan.nodes]
        ys = [planner.graph.xy(n)[1] for n in plan.nodes]
        ax.plot(xs, ys, color=color, lw=lw, ls=ls, label=label, zorder=4)

    if not comparison.same_route:
        draw(
            comparison.baseline,
            "#444",
            1.8,
            "--",
            f"Shortest passable: {comparison.baseline.distance_m:.0f} m, "
            f"{comparison.baseline.crowd_exposure:.0f} people passed",
        )
    draw(
        comparison.chosen,
        "#1f6feb",
        2.8,
        "-",
        f"Crowd-aware: {comparison.chosen.distance_m:.0f} m, "
        f"{comparison.chosen.crowd_exposure:.0f} people passed",
    )

    for n, data in g.nodes(data=True):
        if data.get("label"):
            x, y = planner.graph.xy(n)
            ax.scatter([x], [y], s=40, color="black", zorder=6)
            ax.annotate(
                data["label"],
                (x, y),
                xytext=(4, 6),
                textcoords="offset points",
                fontsize=8,
                zorder=6,
            )

    sm = plt.cm.ScalarMappable(norm=norm, cmap=cmap)
    fig.colorbar(sm, ax=ax, fraction=0.03, pad=0.02, label="Expected people / 100 m")
    ax.set_aspect("equal")
    ax.set_xlabel("x (m)")
    ax.set_ylabel("y (m)")
    surge = planner.crowd.schedule.surge(t)
    ax.set_title(f"{title}\n{t}  ·  class-change surge {surge:.0%}".strip())
    ax.legend(loc="lower right", fontsize=8)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return out


def plot_model(model, graph, out: Path, title: str = "", landmarks: dict | None = None) -> Path:
    """Top-down view of the campus map model: what the USD scene will contain."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Circle, Polygon
    except ImportError as e:
        raise ImportError('plotting needs matplotlib: pip install -e ".[viz]"') from e

    fig, ax = plt.subplots(figsize=(12, 7))
    for b in model.buildings:
        ax.add_patch(Polygon(b.rings[0], closed=True, fc="#d9d2c3", ec="#7a705f", lw=1, zorder=2))
        if b.name:
            cx = sum(p[0] for p in b.rings[0]) / len(b.rings[0])
            cy = sum(p[1] for p in b.rings[0]) / len(b.rings[0])
            ax.text(
                cx,
                cy,
                f"{b.name}\n{b.height_m:.0f} m",
                fontsize=7,
                ha="center",
                va="center",
                zorder=6,
            )
    for a in model.closures:
        ax.add_patch(
            Polygon(a.rings[0], closed=True, fc="none", ec="#e8790c", lw=2, hatch="//", zorder=3)
        )
    for src in model.crowd_sources:
        for p in src.points:
            ax.add_patch(Circle(p, src.radius_m, fc="#e5484d22", ec="#e5484d", ls="--", zorder=1))
            ax.text(p[0], p[1] - src.radius_m - 8, src.zone, fontsize=7, color="#b32", ha="center")

    styles = {
        "walkway": ("#8a8a8a", "-", 3),
        "ramp": ("#4a7bd0", "-", 3),
        "crosswalk": ("#222", ":", 3),
        "stairs": ("#8a8a8a", (0, (1, 1.5)), 3),
    }
    if graph is not None:
        for u, v, data in graph.g.edges(data=True):
            color, ls, lw = styles.get(data["kind"], styles["walkway"])
            if data["closed"]:
                color, ls = "#e8790c", "--"
            (x1, y1), (x2, y2) = graph.xy(u), graph.xy(v)
            ax.plot([x1, x2], [y1, y2], color=color, ls=ls, lw=lw, zorder=4)

    def scatter(items, **kw):
        if items:
            ax.scatter([p.xy[0] for p in items], [p.xy[1] for p in items], zorder=5, **kw)

    scatter(model.trees, s=60, c="#2f7d32", alpha=0.7, label=f"trees ({len(model.trees)})")
    scatter(model.props, s=14, c="#333", marker="s", label=f"props ({len(model.props)})")
    scatter(
        model.entrances,
        s=50,
        c="#1f6feb",
        marker="^",
        label=f"accessible entrances ({len(model.entrances)})",
    )
    scatter(
        model.gcps, s=60, c="#a020f0", marker="x", label=f"survey benchmarks ({len(model.gcps)})"
    )
    scatter(
        model.vehicle_spawns,
        s=60,
        c="#555",
        marker="P",
        label=f"vehicle spawns ({len(model.vehicle_spawns)})",
    )
    if graph is not None and landmarks:
        for key, node in landmarks.items():
            x, y = graph.xy(node)
            ax.scatter([x], [y], s=70, facecolors="white", edgecolors="black", zorder=7)
            ax.annotate(
                key,
                (x, y),
                xytext=(5, -12),
                textcoords="offset points",
                fontsize=8,
                weight="bold",
                zorder=7,
            )
    ax.plot([], [], color="#e8790c", ls="--", lw=2, label="closed (construction)")
    ax.set_aspect("equal")
    ax.set_xlabel("x (m)")
    ax.set_ylabel("y (m)")
    ax.set_title(title or "Campus map model")
    ax.legend(loc="upper left", fontsize=7, framealpha=0.9)
    ax.autoscale_view()
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return out


def preview_usd(usd_path: Path, out: Path, elev: float = 35, azim: float = -60) -> Path:
    """Rough 3D render of an exported stage, read back from the USD file.

    Meshes are drawn as-is; other prims (cubes, cylinders, spheres) as their world
    bounding boxes. Use it to sanity-check exports without Isaac Sim.
    """
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection
        from pxr import Usd, UsdGeom
    except ImportError as e:
        raise ImportError('preview needs matplotlib and usd-core: pip install -e ".[dev]"') from e

    stage = Usd.Stage.Open(str(usd_path))
    xcache = UsdGeom.XformCache()
    bcache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
    polys, colors = [], []
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Gprim) or prim.GetName() == "Ground":
            continue  # the ground plane breaks matplotlib's depth sort; drawn as background
        gprim = UsdGeom.Gprim(prim)
        color = gprim.GetDisplayColorPrimvar().Get()
        rgb = tuple(color[0]) if color else (0.6, 0.6, 0.6)
        if prim.IsA(UsdGeom.Mesh):
            m = UsdGeom.Mesh(prim)
            xf = xcache.GetLocalToWorldTransform(prim)
            pts = [xf.Transform(p) for p in m.GetPointsAttr().Get()]
            idx = list(m.GetFaceVertexIndicesAttr().Get())
            k = 0
            for count in m.GetFaceVertexCountsAttr().Get():
                polys.append([tuple(pts[i]) for i in idx[k : k + count]])
                colors.append(rgb)
                k += count
        else:
            box = bcache.ComputeWorldBound(prim).ComputeAlignedRange()
            (x0, y0, z0), (x1, y1, z1) = box.GetMin(), box.GetMax()
            corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
            polys.append([(x, y, z1) for x, y in corners])
            colors.append(rgb)
            for i in range(4):
                (ax_, ay_), (bx_, by_) = corners[i], corners[(i + 1) % 4]
                polys.append([(ax_, ay_, z0), (bx_, by_, z0), (bx_, by_, z1), (ax_, ay_, z1)])
                colors.append(rgb)

    fig = plt.figure(figsize=(12, 8))
    ax = fig.add_subplot(projection="3d")
    ax.add_collection3d(Poly3DCollection(polys, facecolors=colors, edgecolors="none", linewidths=0))
    xs = [p[0] for poly in polys for p in poly]
    ys = [p[1] for poly in polys for p in poly]
    zs = [p[2] for poly in polys for p in poly]
    ax.set_xlim(min(xs), max(xs))
    ax.set_ylim(min(ys), max(ys))
    ax.set_zlim(0, max(max(zs), 1.0))
    ax.set_box_aspect((max(xs) - min(xs), max(ys) - min(ys), max(max(zs), 1.0) * 3), zoom=1.15)
    ax.view_init(elev=elev, azim=azim)
    ax.set_axis_off()
    fig.patch.set_facecolor("#5b7f4a")  # grass
    ax.set_facecolor("#5b7f4a")
    ax.set_title(f"USD preview: {Path(usd_path).name} (heights ×3)")
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return out
