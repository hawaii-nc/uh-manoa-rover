"""Command line for the campus-rover pipeline (run `campus-rover --help`)."""

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


def cmd_scan_saved_page(args) -> int:
    from .arcgis import scan_text_for_arcgis

    found: dict[str, list[str]] = {"service_urls": [], "item_ids": [], "portals": []}
    files: list[Path] = []
    for raw in args.paths:
        path = Path(raw)
        files.extend(sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else [path])
    for path in files:
        result = scan_text_for_arcgis(path.read_text(encoding="utf-8", errors="replace"))
        for key, values in result.items():
            found[key].extend(v for v in values if v not in found[key])
    print(f"scanned {len(files)} files")
    for key, title in (
        ("service_urls", "ArcGIS service/layer URLs"),
        ("item_ids", "Web map / item ids"),
        ("portals", "Portals"),
    ):
        print(f"\n{title}: {len(found[key])}")
        for value in found[key]:
            print(f"  {value}")
    if found["item_ids"]:
        print("\nNext: campus-rover discover --webmap <id> [--portal <portal>]")
    if found["service_urls"]:
        print("Next: campus-rover discover --url <.../FeatureServer or .../MapServer>")
    return 0


def cmd_discover(args) -> int:  # pragma: no cover - network
    from .arcgis import list_service_layers, list_webmap_layers

    if args.webmap:
        layers = list_webmap_layers(args.webmap, portal=args.portal)
        for layer in layers:
            print(f"{layer['title']}\n    {layer['url']}")
    else:
        for layer in list_service_layers(args.url):
            print(f"[{layer['id']}] {layer['name']} ({layer['geometryType']})\n    {layer['url']}")
    return 0


def cmd_fetch_layers(args) -> int:  # pragma: no cover - network
    from .arcgis import query_layer, save_geojson
    from .campus_model import layer_path, load_layer_specs

    site = load_site(args.site, args.sites_dir)
    specs = [s for s in load_layer_specs(site) if s.url and (not args.only or s.key in args.only)]
    if not specs:
        print("no layers with a `url:` in layers.yaml (see docs/UH_MAP_MODEL.md)")
        return 1
    for spec in specs:
        features = query_layer(spec.url, site.crs, where=spec.where, pause_s=0.5)
        path = layer_path(site, spec)
        save_geojson(features, path, site.crs, spec.url)
        print(f"{spec.key:<28}{len(features):>6} features -> {path}")
    return 0


def cmd_build_model(args) -> int:
    site = load_site(args.site, args.sites_dir)
    model = site.load_model()
    if model is None:
        print(f"site {site.name!r} has no layers.yaml", file=sys.stderr)
        return 1
    try:
        graph = site.load_graph()
        landmarks = site.landmark_nodes(graph)
    except FileNotFoundError as e:
        if not args.allow_no_graph:
            raise
        print(f"warning: {e}\n(continuing without walkways)")
        graph, landmarks = None, {}
    print(f"{site.name} map model ({site.crs})")
    for key, n in model.summary().items():
        print(f"  {key:<16}{n:>6}")
    if model.missing_layers:
        print(f"  not downloaded yet: {', '.join(model.missing_layers)}")
    if args.usd:
        from .usd_export import export_usd

        result = export_usd(model, graph, Path(args.usd), site.name, landmarks)
        print(f"usd: {result['path']} (origin offset {result['origin_offset']})")
    if args.plot:
        from .viz import plot_model

        out = plot_model(model, graph, Path(args.plot), f"{site.name}: campus map model", landmarks)
        print(f"plot: {out}")
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

    scan = sub.add_parser(
        "scan-saved-page", help="find ArcGIS layer URLs in a saved copy of the map page"
    )
    scan.add_argument("paths", nargs="+", help="saved .html file and/or its _files folder")
    scan.set_defaults(func=cmd_scan_saved_page)

    disc = sub.add_parser("discover", help="list layers of an ArcGIS service or web map")
    src = disc.add_mutually_exclusive_group(required=True)
    src.add_argument("--url", help=".../FeatureServer or .../MapServer URL")
    src.add_argument("--webmap", help="ArcGIS web map item id")
    disc.add_argument("--portal", default="https://www.arcgis.com")
    disc.set_defaults(func=cmd_discover)

    fetch = sub.add_parser("fetch-layers", help="download the site's map layers (layers.yaml)")
    fetch.add_argument("--site", required=True)
    fetch.add_argument("--only", nargs="*", help="layer keys to fetch")
    fetch.set_defaults(func=cmd_fetch_layers)

    build = sub.add_parser("build-model", help="build the campus map model; export USD/plot")
    build.add_argument("--site", required=True)
    build.add_argument("--usd", help="write an OpenUSD scene for Isaac Sim (.usda/.usd)")
    build.add_argument("--plot", help="write a top-down PNG of the model")
    build.add_argument(
        "--allow-no-graph",
        action="store_true",
        help="build even if the walkway graph hasn't been imported yet",
    )
    build.set_defaults(func=cmd_build_model)
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
