from datetime import date, datetime, timedelta

from planner.availability import parse_window, parse_windows
from planner.config import PlanningSettings
from planner.jobs import next_available, plan_periods
from planner.models import CalEvent, Chore, Person

SAT = date(2026, 10, 31)
START = SAT - timedelta(days=1)                      # periods run Fri → Thu, like a Thursday-evening run
BOTH = ["nicolas", "anna"]
PERSONS = [Person("nicolas", "Nicolas"), Person("anna", "Anna")]
PLANNING = PlanningSettings(parse_windows("sat 08:00-18:00; sun 08:00-18:00"), parse_window("08:00-18:00"))


def all_day(kind, who, first: date, nights: int, title=None):
    start = datetime.combine(first, datetime.min.time())
    return CalEvent(who, title or kind, start, start + timedelta(days=nights), kind)


def recurring(id, lo, mid, hi, **kw):
    return Chore(id=id, name=id, category="garden", type="recurring", interval_min_days=lo,
                 interval_target_days=mid, interval_max_days=hi, duration_h=1, priority=3, assignee="nicolas",
                 created_on=SAT - timedelta(days=400), **kw)


def one_off(id, hours=1, who="nicolas"):
    return Chore(id=id, name=id, category="home", type="one_off", duration_h=hours, priority=3, assignee=who,
                 created_on=SAT)


def test_without_travel_the_next_chore_day_is_normal():
    assert next_available(BOTH, START + timedelta(days=6), [], 30, PLANNING) is None


def test_next_available_skips_blocked_weekends():
    events = [all_day("away", BOTH, SAT + timedelta(days=5), 12)]      # Thu 5 Nov → Tue 17 Nov
    assert next_available(BOTH, START + timedelta(days=6), events, 30, PLANNING) == SAT + timedelta(days=21)


def test_travel_week_front_loads_only_what_would_go_overdue():
    """Away 5-17 Nov (two weekends): next chore day is Sat 21 Nov."""
    events = [all_day("away", BOTH, SAT + timedelta(days=5), 12)]
    fertilize = recurring("fertilize", 28, 35, 49)      # done 17 Oct: on target (35) by 21 Nov
    mow = recurring("mow", 7, 10, 14)                   # done 24 Oct: overdue (28 days) by 21 Nov
    hedge = recurring("hedge", 30, 45, 60)              # done 29 Sep: past min now, not overdue by 21 Nov
    last = {"fertilize": SAT - timedelta(days=14), "mow": SAT - timedelta(days=7), "hedge": SAT - timedelta(days=32)}

    results = plan_periods([fertilize, mow, hedge], PERSONS, last, START, 4, events, 30, PLANNING)
    planned = [{i.chore.id for i in r.plan.items} for r in results]
    assert results[0].next_available == SAT + timedelta(days=21)
    assert "mow" in planned[0] and "hedge" in planned[0] and "fertilize" not in planned[0]
    assert results[1].blocked and results[2].blocked and planned[1] == planned[2] == set()
    assert "fertilize" in planned[3]              # catch-up after the trip


def test_long_weekend_and_extra_afternoon_are_used():
    thu, mon = SAT - timedelta(days=2), SAT + timedelta(days=2)
    events = [all_day("extra_day", ["anna"], mon, 1, "Day off"),          # Anna's long weekend
              CalEvent(["nicolas"], "Chores", datetime(2026, 10, 29, 14), datetime(2026, 10, 29, 18), "extra_time"),
              all_day("busy", BOTH, SAT, 2, "Camping")]          # weekend fully taken (as busy time)
    chores = [one_off("fix"), one_off("tidy", who="anna")]
    results = plan_periods(chores, PERSONS, {}, thu, 1, events, 30, PLANNING)
    days = {i.chore.id: i.start.date() for i in results[0].plan.items}
    assert days == {"fix": thu, "tidy": mon}               # Nicolas on his Thursday afternoon, Anna on her day off


def test_no_chores_day_removes_default_window():
    events = [all_day("no_chores", BOTH, SAT, 2, "No chores")]
    results = plan_periods([one_off("fix")], PERSONS, {}, START, 1, events, 30, PLANNING)
    assert results[0].blocked and results[0].plan.items == []


def test_events_listed_per_period():
    party = CalEvent(BOTH, "Party", datetime(2026, 10, 31, 14), datetime(2026, 10, 31, 16))
    later = CalEvent(BOTH, "Dinner", datetime(2026, 11, 7, 19), datetime(2026, 11, 7, 21))
    results = plan_periods([], PERSONS, {}, START, 2, [later, party], 30, PLANNING)
    assert [e.summary for e in results[0].events] == ["Party"]
    assert [e.summary for e in results[1].events] == ["Dinner"]


def test_busy_events_listed_only_on_days_with_chore_time():
    weekday = CalEvent(BOTH, "School run", datetime(2026, 11, 2, 7), datetime(2026, 11, 2, 7, 15))
    weekend = CalEvent(BOTH, "Party", datetime(2026, 10, 31, 14), datetime(2026, 10, 31, 16))
    trip = all_day("away", BOTH, date(2026, 11, 3), 2, "Trip")                 # a weekday trip is still shown
    results = plan_periods([], PERSONS, {}, START, 1, [weekday, weekend, trip], 30, PLANNING)
    assert [e.summary for e in results[0].events] == ["Party", "Trip"]
