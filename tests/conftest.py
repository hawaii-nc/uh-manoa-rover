from pathlib import Path

import pytest

from campus_rover.planner import RoutePlanner
from campus_rover.site import load_site

SITES = Path(__file__).resolve().parents[1] / "sites"


@pytest.fixture
def sites_dir() -> Path:
    return SITES


@pytest.fixture
def demo_site():
    return load_site("demo_campus", SITES)


@pytest.fixture
def demo_planner(demo_site) -> RoutePlanner:
    return RoutePlanner.from_site(demo_site)


@pytest.fixture
def bare_sites_dir(tmp_path, sites_dir):
    """Site configs as a fresh clone has them: no downloaded layers, no imported graph."""
    import shutil

    shutil.copytree(sites_dir, tmp_path / "sites",
                    ignore=shutil.ignore_patterns("cache", "graph.osm.yaml"))
    return tmp_path / "sites"
