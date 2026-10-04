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
