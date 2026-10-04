"""Command line: campus-rover {sites,plan,sweep,import-osm}."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .planner import NoRouteError, RoutePlanner, compare
from .schedule import WeekTime, parse_hhmm
from .site import DEFAULT_SITES_DIR, Site, list_sites, load_site


def _route_nodes(site: Site, planner: RoutePlanner, route_key: str) -> list[str]:
    if route_key not in site.routes:
        raise SystemExit(f"unknown route {route_key!r}; choose from {sorted(site.routes)}")
    nodes = site.landmark_nodes(planner.graph)
    return [nodes[wp] for wp in site.routes[route_key].waypoints]


def cmd_sites(args) -> int:
    for name in list_sites(args.sites_dir):
        site = load_site(name, args.sites_dir)
        routes = ", ".join(f"{k} ({r.label})" for k, r in site.routes.items())
        print(f"{name}: {site.description}\n  routes: {routes}")
    return 0


def cmd_plan(args) -> int:
    site = load_site(args.site, args.sites_dir)
    planner = RoutePlanner.from_site(site)
    depart = WeekTime.parse(args.time)
    waypoints = _route_nodes(site, planner, args.route)
    try:
        result = compare(planner, waypoints, depart)
    except NoRouteError as e:
        print(f"no route: {e}", file=sys.stderr)
        return 1
    plan = result.chosen
    route = site.routes[args.route]
    print(f"{site.name} · route {route.key}: {route.label}")
    print(f"depart {depart} · class-change surge {site.schedule.surge(depart):.0%}\n")
    print(f"{'segment':<24}{'length':>9}{'crowd/100m':>12}")
    for name, length, density in plan.segments():
        print(f"{name:<24}{length:>8.0f}m{density:>12.1f}")
    print(
        f"\ntotal {plan.distance_m:.0f} m · {plan.duration_s / 60:.1f} min · "
        f"arrive {plan.arrive} · ~{plan.crowd_exposure:.0f} people passed\n"
    )
    for reason in result.reasons:
        print(f"- {reason}")
    if args.plot:
        from .viz import plot_comparison

        out = plot_comparison(planner, result, Path(args.plot), f"{site.name}: {route.label}")
        print(f"\nplot: {out}")
    return 0


def cmd_sweep(args) -> int:
    site = load_site(args.site, args.sites_dir)
    planner = RoutePlanner.from_site(site)
    waypoints = _route_nodes(site, planner, args.route)
    start, end = parse_hhmm(args.start), parse_hhmm(args.end)
    day = WeekTime.parse(f"{args.day} 00:00").day
    print(f"{site.name} · route {args.route} · {args.day} {args.start}-{args.end}\n")
    print(f"{'time':<7}{'surge':>6}{'dist':>7}{'min':>6}{'people':>8}  route")
    labels: dict[tuple[str, ...], str] = {}
    for minute in range(start, end + 1, args.step):
        t = WeekTime.at(day, minute)
        result = compare(planner, waypoints, t)
        plan = result.chosen
        key = tuple(plan.nodes)
        if key not in labels:
            labels[key] = " → ".join(name for name, _, _ in plan.segments())
        tag = "" if result.same_route else "  (detour)"
        print(
            f"{str(t)[4:]:<7}{site.schedule.surge(t):>6.0%}{plan.distance_m:>6.0f}m"
            f"{plan.duration_s / 60:>6.1f}{plan.crowd_exposure:>8.0f}  {labels[key]}{tag}"
        )
    return 0


def cmd_import_osm(args) -> int:  # pragma: no cover - network
    from .osm import fetch_site_graph

    site = load_site(args.site, args.sites_dir)
    graph, missing = fetch_site_graph(site)
    graph.save(site.graph_path)
    print(
        f"wrote {site.graph_path}: {graph.g.number_of_nodes()} nodes, "
        f"{graph.g.number_of_edges()} edges"
    )
    for key in missing:
        print(
            f"warning: no OSM feature named {site.landmarks[key].get('osm_name')!r} ({key}); "
            f"set `node:` for it in site.yaml"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="campus-rover", description=__doc__)
    p.add_argument("--sites-dir", type=Path, default=DEFAULT_SITES_DIR)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("sites", help="list sites and routes").set_defaults(func=cmd_sites)

    plan = sub.add_parser("plan", help="plan a route at a given time")
    plan.add_argument("--site", required=True)
    plan.add_argument("--route", required=True)
    plan.add_argument("--time", required=True, help="e.g. 'Mon 09:24'")
    plan.add_argument("--plot", help="write a PNG of the routes and crowd map")
    plan.set_defaults(func=cmd_plan)

    sweep = sub.add_parser("sweep", help="show how the chosen route changes through a day")
    sweep.add_argument("--site", required=True)
    sweep.add_argument("--route", required=True)
    sweep.add_argument("--day", default="Mon")
    sweep.add_argument("--start", default="07:00")
    sweep.add_argument("--end", default="17:00")
    sweep.add_argument("--step", type=int, default=15, help="minutes")
    sweep.set_defaults(func=cmd_sweep)

    imp = sub.add_parser("import-osm", help="download the site's walkway graph from OSM")
    imp.add_argument("--site", required=True)
    imp.set_defaults(func=cmd_import_osm)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, KeyError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
