from campus_rover.crowd import CrowdModel
from campus_rover.schedule import WeekTime


def test_class_change_raises_density_near_buildings(demo_site):
    crowd = CrowdModel.from_site(demo_site)
    quiet, rush = WeekTime.parse("Mon 09:00"), WeekTime.parse("Mon 09:23")
    assert crowd.density("mall_w", "mall_e", quiet) == 4.0  # zone baseline only
    assert crowd.density("mall_w", "mall_e", rush) > 15
    # The south path is far from the lecture hall, so it stays much calmer.
    assert crowd.density("s1", "s2", rush) < crowd.density("mall_w", "mall_e", rush) / 2


def test_zone_windows(demo_site):
    crowd = CrowdModel.from_site(demo_site)
    lunch = WeekTime.parse("Tue 12:10")
    assert crowd.zone_density("food", lunch) == 30.0  # lunch peak beats all-day baseline
    assert crowd.zone_density("food", WeekTime.parse("Tue 16:00")) == 3.0
    assert crowd.zone_density("food", WeekTime.parse("Sat 12:10")) == 0.0
    assert crowd.zone_density(None, lunch) == 0.0


def test_weekend_is_empty(demo_site):
    crowd = CrowdModel.from_site(demo_site)
    t = WeekTime.parse("Sat 09:23")
    assert all(crowd.density(u, v, t) == 0 for u, v in crowd.graph.g.edges)
