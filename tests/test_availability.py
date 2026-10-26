from datetime import date, datetime, timedelta

import pytest

from planner.availability import first_available_day, free_slots, parse_window, parse_windows
from planner.models import CalEvent

SAT = date(2026, 10, 31)
THU, MON = SAT - timedelta(days=2), SAT + timedelta(days=2)
WEEK = [THU + timedelta(days=i) for i in range(7)]            # Thu 29 Oct … Wed 4 Nov
DEFAULT = parse_windows("sat 08:00-18:00; sun 08:00-18:00")
DAY = parse_window("08:00-18:00")
BOTH = ["nicolas", "anna"]


def ev(kind, who, start, end):
    return CalEvent(who, kind, start, end, kind)


def whole(d, nights=1):
    start = datetime(d.year, d.month, d.day)
    return start, start + timedelta(days=nights)


def slots(events, buffer=30):
    return free_slots(BOTH, WEEK, DEFAULT, events, buffer, DAY)


def test_parse_windows():
    assert parse_windows("Sat 08:00-18:00; sunday 9:30-12:00; sat 19:00-20:00") == {
        5: [(480, 1080), (1140, 1200)], 6: [(570, 720)]}
    for bad in ("someday 08:00-18:00", "sat 18:00-08:00", "sat 8-18"):
        with pytest.raises(ValueError):
            parse_windows(bad)


def test_default_pattern_only_weekend():
    free = slots([])
    assert free[("nicolas", SAT)] == [(480, 1080)] and free[("anna", SAT + timedelta(days=1))] == [(480, 1080)]
    assert free[("nicolas", THU)] == [] and free[("anna", MON)] == []


def test_day_off_adds_a_whole_day_for_that_calendars_people():
    free = slots([ev("extra_day", ["nicolas"], *whole(MON))])            # long weekend, Nicolas only
    assert free[("nicolas", MON)] == [DAY] and free[("anna", MON)] == []


def test_chore_time_adds_a_window():
    free = slots([ev("extra_time", ["nicolas"], datetime(2026, 10, 29, 14), datetime(2026, 10, 29, 18))])
    assert free[("nicolas", THU)] == [(14 * 60, 18 * 60)] and free[("anna", THU)] == []


def test_busy_with_buffer_is_removed_even_from_extra_time():
    free = slots([ev("extra_time", ["nicolas"], datetime(2026, 10, 29, 14), datetime(2026, 10, 29, 18)),
                  ev("busy", ["nicolas"], datetime(2026, 10, 29, 15), datetime(2026, 10, 29, 16))])
    assert free[("nicolas", THU)] == [(14 * 60, 14 * 60 + 30), (16 * 60 + 30, 18 * 60)]


def test_away_and_no_chores_remove_whole_days_even_a_day_off():
    free = slots([ev("away", ["anna"], *whole(SAT, 2)), ev("extra_day", BOTH, *whole(MON)),
                  ev("no_chores", BOTH, *whole(MON))])
    assert free[("anna", SAT)] == [] and free[("nicolas", SAT)] == [(480, 1080)]
    assert free[("nicolas", MON)] == []


def test_first_available_day_skips_travel():
    away = [ev("away", BOTH, *whole(SAT + timedelta(days=7), 9))]      # away 7-15 Nov
    assert first_available_day(BOTH, SAT + timedelta(days=5), DEFAULT, away, 30, DAY, 42) == SAT + timedelta(days=21)
