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
        path = layer_path(site, spec)
        if path.exists() and not args.force:
            print(f"{spec.key:<28} cached -> {path}")
            continue
        features = query_layer(spec.url, site.crs, where=spec.where, pause_s=0.5)
        save_geojson(features, path, site.crs, spec.url)
        print(f"{spec.key:<28}{len(features):>6} features -> {path}")
    return 0


def cmd_fetch_world_data(args) -> int:  # pragma: no cover - network
    """Download what build-world needs: walkways, roads, off-campus footprints, lidar."""
    import json
    import urllib.request

    from .lidar import rasterize_tiles
    from .overture import fetch_buildings, fetch_segments, segments_to_campus_graph
    from .world import WorldConfig, corridor_bbox

    site = load_site(args.site, args.sites_dir)
    cfg = WorldConfig.from_site(site)
    bbox = tuple(site.raw["graph"]["osm"]["bbox"])
    if cfg.segments and (args.force or not (site.root / cfg.segments).exists()):
        segments = fetch_segments(bbox, site.crs)
        path = site.root / cfg.segments
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(segments))
        print(f"segments   {len(segments):>6} -> {path}")
        if args.force or not site.graph_path.exists():
            graph = segments_to_campus_graph(segments)
            graph.save(site.graph_path)
            print(f"graph      {graph.g.number_of_edges():>6} edges -> {site.graph_path}")
    if cfg.background_buildings and (args.force
                                     or not (site.root / cfg.background_buildings).exists()):
        footprints = fetch_buildings(bbox, site.crs)
        path = site.root / cfg.background_buildings
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(footprints))
        print(f"footprints {len(footprints):>6} -> {path}")
    if cfg.lidar_tiles and cfg.rasters:
        folder = site.root / "cache" / "lidar"
        folder.mkdir(parents=True, exist_ok=True)
        tiles = []
        for url in cfg.lidar_tiles:
            tile = folder / url.rsplit("/", 1)[-1]
            if not tile.exists():
                print(f"downloading {tile.name} ...")
                urllib.request.urlretrieve(url, tile)
            tiles.append(tile)
        model = site.load_model()
        box = corridor_bbox(site, model, cfg)
        rasters = rasterize_tiles(tiles, box, cfg.lidar_res, progress=lambda m: print(f"  {m}"))
        rasters.save(site.root / cfg.rasters)
        print(f"lidar grids {rasters.shape} at {cfg.lidar_res} m -> {site.root / cfg.rasters}")
    if cfg.open_data:
        from .lidar import Rasters
        from .opendata import FT, contour_check, contour_lines, fetch_open_data

        model = site.load_model()
        box = corridor_bbox(site, model, cfg)
        folder = site.root / cfg.open_data
        fetch_open_data(site, box, folder, force=args.force)
        contours = folder / "cch_contours_5ft.json"
        if cfg.rasters and (site.root / cfg.rasters).exists() and contours.exists():
            check = contour_check(Rasters.load(site.root / cfg.rasters),
                                  contour_lines(json.loads(contours.read_text()), "ELEVATION", FT))
            check.pop("levels_m", None)
            print(f"lidar vs. 5 ft contours (before any shift): {check}")
            print("  set world.vertical_shift_m in site.yaml to minus mean_diff_m")
    return 0


def cmd_build_plans(args) -> int:
    """Fit every cached floor-plan PDF onto its building and save the layouts."""
    import json

    from .lidar import Rasters
    from .plans import build_layout, layout_ok, roof_mask_for
    from .world import WorldConfig, building_polygon

    site = load_site(args.site, args.sites_dir)
    cfg = WorldConfig.from_site(site)
    model = site.load_model()
    rasters = Rasters.load(site.root / cfg.rasters)
    if cfg.vertical_shift_m:
        rasters.shift_z(cfg.vertical_shift_m)
    pdfs = site.root / "cache" / "floorplans"
    out = site.root / (cfg.plans or "cache/plans")
    out.mkdir(parents=True, exist_ok=True)
    x0, y0, x1, y1 = rasters.bounds
    done = good = 0
    for b in model.buildings:
        number = str(b.props.get("PROP_NUM") or "")
        pdf = pdfs / f"{number}.pdf"
        if not number or not pdf.exists() or (args.only and number not in args.only):
            continue
        poly = building_polygon(b)
        c = poly.centroid
        if not (x0 + 5 < c.x < x1 - 5 and y0 + 5 < c.y < y1 - 5):
            continue
        mask, mx, my = roof_mask_for(poly, rasters)
        if not mask.any():
            continue
        layout = build_layout(pdf, number, mask, mx, my, 1.0, (c.x, c.y))
        if layout is None:
            print(f"{number:<7}{b.name[:34]:<36}no layout")
            continue
        done += 1
        ok = layout_ok(layout.quality)
        good += ok
        q = layout.quality
        print(f"{number:<7}{(b.name or '')[:34]:<36}{'OK ' if ok else 'no '} levels {q['levels']}"
              f" score {q['min_score']:.2f} agree {q['min_agreement']:.2f}"
              f" extent {q['extent_error_m']:.0f}m angle {q['angle_deg']:.0f}"
              f" cols {q['column_aligned_sheets']}/{q['column_alignment_needed']}")
        (out / f"{number}.json").write_text(json.dumps(layout.to_json()))
    print(f"{good} of {done} floor-plan layouts are good enough to build from")
    return 0


def cmd_fetch_assets(args) -> int:  # pragma: no cover - network
    from .assets import fetch_assets
    from .world import WorldConfig

    site = load_site(args.site, args.sites_dir)
    cfg = WorldConfig.from_site(site)
    done = fetch_assets(site.root / (cfg.assets or "cache/assets"), force=args.force)
    print(f"{len(done)} model(s) prepared" if done else "all models already in the cache")
    return 0


def cmd_view(args) -> int:  # pragma: no cover - serves files until interrupted
    import functools
    import http.server

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=args.dir)
    with http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
        print(f"viewer: http://127.0.0.1:{args.port}/  (Ctrl+C to stop)")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


def cmd_build_world(args) -> int:
    import yaml

    from .agents import Scenario
    from .world import build_world, write_world

    site = load_site(args.site, args.sites_dir)
    scenario = None
    if args.scenario:
        path = site.root / "scenarios" / f"{args.scenario}.yaml"
        scenario = Scenario.from_dict(yaml.safe_load(path.read_text())) if path.exists() \
            else Scenario(name=args.scenario)
    world = build_world(site, scenario, args.route, progress=lambda m: print(f"  {m}..."))
    files = write_world(world, Path(args.out), usd=not args.no_usd)
    r = world.report
    campus = [b for b in r["buildings"] if b["campus"]]
    how = {k: sum(b["style_source"] == k for b in r["buildings"])
           for k in ("sheet", "unverified", "procedural")}
    print(f"{site.name}: {r['size_m'][0]} x {r['size_m'][1]} m, ground {r['elevation_m'][0]}"
          f"-{r['elevation_m'][1]} m, lidar: {'yes' if r['lidar'] else 'no (flat)'}")
    print(f"buildings {len(r['buildings'])}: {len(campus)} campus, "
          f"{len(r['buildings']) - len(campus)} other · looks from a style sheet {how['sheet']}, "
          f"plain unverified {how['unverified']}, far background {how['procedural']}"
          f" · doors {sum(b['doors'] for b in r['buildings'])}"
          f" · trees {r['trees']} · props {sum(r['props'].values())}")
    for key, value in (r.get("checks") or {}).items():
        if isinstance(value, dict):
            value = {k: v for k, v in value.items() if k != "levels_m"}
        print(f"  check {key}: {value}")
    print(f"triangles {r['mesh']['triangles']:,} · surfaces {r['surface_share']}")
    if r["agents"]:
        print("agents: " + ", ".join(f"{k} {v}" for k, v in r["agents"].items() if v))
    for name, path in files.items():
        print(f"  {name:<12} {path}  ({path.stat().st_size / 1e6:.1f} MB)")
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
    fetch.add_argument("--force", action="store_true", help="re-download cached layers")
    fetch.set_defaults(func=cmd_fetch_layers)

    world = sub.add_parser("build-world", help="build the 3D training world (GLB + USD)")
    world.add_argument("--site", required=True)
    world.add_argument("--out", required=True, help="output folder")
    world.add_argument("--scenario", help="add people/bikes/cars from scenarios/<name>.yaml")
    world.add_argument("--route", help="route key that defines the corridor (default: last)")
    world.add_argument("--no-usd", action="store_true", help="skip the USD files")
    world.set_defaults(func=cmd_build_world)

    data = sub.add_parser("fetch-world-data",
                          help="download walkways, roads, footprints and lidar for build-world")
    data.add_argument("--site", required=True)
    data.add_argument("--force", action="store_true", help="re-download what is already cached")
    data.set_defaults(func=cmd_fetch_world_data)

    plans = sub.add_parser("build-plans",
                           help="fit the cached floor-plan PDFs onto their buildings")
    plans.add_argument("--site", required=True)
    plans.add_argument("--only", nargs="*", help="building numbers to do (default: all cached)")
    plans.set_defaults(func=cmd_build_plans)

    models = sub.add_parser("fetch-assets", help="download the free 3D models (benches, lights)")
    models.add_argument("--site", required=True)
    models.add_argument("--force", action="store_true")
    models.set_defaults(func=cmd_fetch_assets)

    view = sub.add_parser("view", help="open a built world in the browser viewer")
    view.add_argument("--dir", required=True, help="the folder build-world wrote")
    view.add_argument("--port", type=int, default=8000)
    view.set_defaults(func=cmd_view)

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
