import pytest

from campus_rover.schedule import ClassSchedule, WeekTime, parse_days, parse_hhmm

MWF = {"blocks": [{"days": ["Mon", "Wed", "Fri"], "starts": ["09:30"], "duration_min": 50}]}


def test_weektime_parse_and_format():
    t = WeekTime.parse("Wed 13:05")
    assert t.day == 2
    assert t.minute_of_day == 13 * 60 + 5
    assert str(t) == "Wed 13:05"
    assert WeekTime.parse("monday 9:24") == WeekTime.parse("Mon 09:24")


def test_weektime_wraps_sunday_to_monday():
    t = WeekTime.parse("Sun 23:58").plus_seconds(5 * 60)
    assert str(t) == "Mon 00:03"


@pytest.mark.parametrize("bad", ["09:24", "Funday 09:00", "Mon 9"])
def test_weektime_rejects_bad_input(bad):
    with pytest.raises(ValueError):
        WeekTime.parse(bad)


def test_parse_helpers():
    assert parse_hhmm("07:30") == 450
    assert parse_days("weekdays") == frozenset(range(5))
    assert parse_days(["Tue", "Thu"]) == frozenset({1, 3})
    with pytest.raises(ValueError):
        parse_hhmm("25:00")


def test_surge_peaks_after_class_ends_and_before_it_starts():
    s = ClassSchedule.from_dict(MWF)
    assert s.surge(WeekTime.parse("Mon 10:23")) == pytest.approx(1.0)  # 10:20 end + 3
    assert s.surge(WeekTime.parse("Mon 09:26")) == pytest.approx(1.0)  # 09:30 start - 4
    assert s.surge(WeekTime.parse("Mon 10:00")) == 0.0  # mid-class
    assert s.surge(WeekTime.parse("Tue 10:23")) == 0.0  # no TR block
    assert 0 < s.surge(WeekTime.parse("Mon 10:27")) < 1


def test_surge_wraps_across_the_week():
    data = {"blocks": [{"days": ["Mon"], "starts": ["00:02"], "duration_min": 50}]}
    s = ClassSchedule.from_dict(data)
    # Arrival peak is Sun 23:58 on the weekly circle.
    assert s.surge(WeekTime.parse("Sun 23:58")) == pytest.approx(1.0)
