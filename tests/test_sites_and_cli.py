import pytest

from campus_rover.cli import main
from campus_rover.site import list_sites, load_site


def test_all_sites_load(sites_dir):
    names = list_sites(sites_dir)
    assert {"demo_campus", "uh_manoa"} <= set(names)
    for name in names:
        site = load_site(name, sites_dir)
        assert site.routes and site.schedule.blocks


def test_uh_site_routes(sites_dir):
    site = load_site("uh_manoa", sites_dir)
    assert site.routes["A"].waypoints == ["holmes_hall", "campus_center"]
    assert site.routes["B"].waypoints == ["campus_center", "architecture"]
    assert site.closures[0].near_landmark == "hamilton_library"


def test_route_with_unknown_landmark_is_rejected(tmp_path):
    (tmp_path / "site.yaml").write_text(
        "name: bad\ngraph: {source: file, path: g.yaml}\nlandmarks: {}\n"
        "routes: {A: {waypoints: [x, y]}}\n"
    )
    with pytest.raises(ValueError, match="unknown landmark"):
        load_site(str(tmp_path))


def test_cli_plan_and_plot(sites_dir, tmp_path, capsys):
    out = tmp_path / "plan.png"
    code = main(
        [
            "--sites-dir",
            str(sites_dir),
            "plan",
            "--site",
            "demo_campus",
            "--route",
            "A",
            "--time",
            "Mon 09:20",
            "--plot",
            str(out),
        ]
    )
    assert code == 0
    text = capsys.readouterr().out
    assert "Avoids Central Mall" in text
    assert out.stat().st_size > 10_000


def test_cli_sweep(sites_dir, capsys):
    code = main(
        [
            "--sites-dir",
            str(sites_dir),
            "sweep",
            "--site",
            "demo_campus",
            "--route",
            "A",
            "--start",
            "09:00",
            "--end",
            "09:30",
            "--step",
            "10",
        ]
    )
    assert code == 0
    assert "(detour)" in capsys.readouterr().out


def test_cli_uh_without_graph_gives_import_hint(sites_dir, capsys):
    code = main(
        [
            "--sites-dir",
            str(sites_dir),
            "plan",
            "--site",
            "uh_manoa",
            "--route",
            "A",
            "--time",
            "Mon 09:20",
        ]
    )
    assert code == 2
    assert "import-osm" in capsys.readouterr().err


def test_cli_build_model(sites_dir, tmp_path, capsys):
    pytest.importorskip("pxr")
    usd, png = tmp_path / "demo.usda", tmp_path / "model.png"
    code = main(
        [
            "--sites-dir",
            str(sites_dir),
            "build-model",
            "--site",
            "demo_campus",
            "--usd",
            str(usd),
            "--plot",
            str(png),
        ]
    )
    assert code == 0
    assert usd.exists() and png.stat().st_size > 10_000
    assert "buildings" in capsys.readouterr().out


def test_cli_build_model_uh_without_data(sites_dir, capsys):
    code = main(
        ["--sites-dir", str(sites_dir), "build-model", "--site", "uh_manoa", "--allow-no-graph"]
    )
    assert code == 0
    assert "not downloaded yet" in capsys.readouterr().out


def test_cli_scan_saved_page(tmp_path, capsys):
    folder = tmp_path / "Campus Map_files"
    folder.mkdir()
    (folder / "config.js.download").write_text(
        'webmap: "0123456789abcdef0123456789abcdef", '
        'url: "https://x.edu/server/rest/services/UHM/Trees/FeatureServer/0"'
    )
    (tmp_path / "page.html").write_text("<html></html>")
    assert main(["scan-saved-page", str(tmp_path / "page.html"), str(folder)]) == 0
    out = capsys.readouterr().out
    assert "0123456789abcdef0123456789abcdef" in out and "FeatureServer/0" in out
