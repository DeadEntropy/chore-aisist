from datetime import date, datetime, timedelta

from planner.calendars import busy_from_events
from planner.fitting import plan_weekend
from planner.jobs import blocked, next_available, next_saturday, plan_weekends
from planner.models import CalEvent, Chore, Person, Scored

SAT = date(2026, 10, 31)
PEOPLE = ["nicolas", "anna"]
PERSONS = [Person("nicolas", "Nicolas", 6), Person("anna", "Anna", 6)]


def trip(who, first: date, nights: int):
    start = datetime.combine(first, datetime.min.time())
    return CalEvent(who, "Travel", start, start + timedelta(days=nights), travel=True)


def recurring(id, lo, mid, hi, **kw):
    return Chore(id=id, name=id, category="garden", type="recurring", interval_min_days=lo,
                 interval_target_days=mid, interval_max_days=hi, duration_h=1, priority=3, assignee="nicolas",
                 created_on=SAT - timedelta(days=400), **kw)


def test_next_saturday():
    assert next_saturday(date(2026, 10, 26)) == SAT        # Monday
    assert next_saturday(SAT) == SAT


def test_weekend_blocked_only_when_nobody_is_free_either_day():
    nxt = SAT + timedelta(weeks=1)
    assert blocked(nxt, PEOPLE, busy_from_events([trip(PEOPLE, nxt, 2)], 30))
    assert not blocked(nxt, PEOPLE, busy_from_events([trip(["nicolas"], nxt, 2)], 30))   # Anna is home
    assert not blocked(nxt, PEOPLE, busy_from_events([trip(PEOPLE, nxt, 1)], 30))        # back for Sunday


def test_timed_event_covering_the_weekend_blocks_it():
    camping = CalEvent(PEOPLE, "Camping", datetime(2026, 10, 30, 17), datetime(2026, 11, 1, 19))
    assert blocked(SAT, PEOPLE, busy_from_events([camping], 30))


def test_next_available_skips_blocked_weekends():
    assert next_available(SAT, PEOPLE, []) == SAT + timedelta(weeks=1)
    away = busy_from_events([trip(PEOPLE, SAT + timedelta(days=5), 12)], 30)   # Thu 5 Nov → Tue 17 Nov
    assert next_available(SAT, PEOPLE, away) == SAT + timedelta(weeks=3)


def test_travel_week_front_loads_only_what_would_go_overdue():
    """Away 5-17 Nov (two weekends): back on 21 Nov."""
    events = [trip(PEOPLE, SAT + timedelta(days=5), 12)]
    fertilize = recurring("fertilize", 28, 35, 49)      # done 17 Oct: 14 days now, on target (35) by 21 Nov
    mow = recurring("mow", 7, 10, 14)                   # done 24 Oct: 7 days now, 28 days (overdue) by 21 Nov
    hedge = recurring("hedge", 30, 45, 60)              # done 1 Oct: 30 days now, 51 by 21 Nov (not overdue)
    last = {"fertilize": SAT - timedelta(days=14), "mow": SAT - timedelta(days=7), "hedge": SAT - timedelta(days=30)}

    first = plan_weekends([fertilize, mow, hedge], PERSONS, last, SAT, 4, events, 30)
    planned = [{i.chore.id for i in r.plan.items} for r in first]
    assert first[0].next_available == SAT + timedelta(weeks=3)
    assert "mow" in planned[0]                    # front-loaded: would be overdue by the time we're back
    assert "fertilize" not in planned[0]          # waits: it isn't due before 21 Nov
    assert "hedge" in planned[0]                  # due anyway (past min), scored as of today
    assert first[1].blocked and first[2].blocked and planned[1] == planned[2] == set()
    assert "fertilize" in planned[3]              # catch-up after the trip


def test_without_travel_nothing_is_brought_forward():
    mow = recurring("mow", 7, 10, 14)
    results = plan_weekends([mow], PERSONS, {"mow": SAT - timedelta(days=5)}, SAT, 1, [], 30)
    assert results[0].next_available is None and results[0].plan.items == []


def test_travel_leaves_no_capacity_and_events_have_buffers():
    chore = Chore(id="fix", name="fix", category="home", type="one_off", duration_h=1, priority=3,
                  assignee="nicolas")
    plan = plan_weekend([Scored(chore, 1, 3)], PERSONS, busy_from_events([trip(["nicolas"], SAT, 2)], 30), SAT)
    assert plan.items == [] and plan.unplanned[0][1] == "no free slot long enough"

    # A party 08:30-17:30 with 30 min buffers leaves no 1 h slot inside 08:00-18:00 on Saturday.
    party = CalEvent(["nicolas"], "Party", datetime(2026, 10, 31, 8, 30), datetime(2026, 10, 31, 17, 30))
    plan = plan_weekend([Scored(chore, 1, 3)], PERSONS, busy_from_events([party], 30), SAT)
    assert plan.items[0].start.date() == SAT + timedelta(days=1)


def test_weekend_events_listed_per_weekend():
    party = CalEvent(PEOPLE, "Party", datetime(2026, 10, 31, 14), datetime(2026, 10, 31, 16))
    later = CalEvent(PEOPLE, "Dinner", datetime(2026, 11, 7, 19), datetime(2026, 11, 7, 21))
    results = plan_weekends([], PERSONS, {}, SAT, 2, [later, party], 30)
    assert [e.summary for e in results[0].events] == ["Party"]
    assert [e.summary for e in results[1].events] == ["Dinner"]
