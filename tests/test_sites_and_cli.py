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
