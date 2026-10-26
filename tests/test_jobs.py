from datetime import date, datetime, timedelta

from planner.calendars import away_days, busy_from_events
from planner.fitting import plan_weekend
from planner.jobs import blocked, next_saturday, scoring_date
from planner.models import CalEvent, Chore, Person, Scored

SAT = date(2026, 10, 31)
PEOPLE = ["nicolas", "anna"]


def trip(who, first: date, nights: int):
    start = datetime.combine(first, datetime.min.time())
    return CalEvent(who, "Travel", start, start + timedelta(days=nights), all_day=True, travel=True)


def test_next_saturday():
    assert next_saturday(date(2026, 10, 26)) == SAT        # Monday
    assert next_saturday(SAT) == SAT


def test_weekend_blocked_only_when_everyone_is_away_both_days():
    nxt = SAT + timedelta(weeks=1)
    assert blocked(nxt, PEOPLE, away_days([trip(PEOPLE, nxt, 2)]))
    assert not blocked(nxt, PEOPLE, away_days([trip(["nicolas"], nxt, 2)]))     # Anna is home
    assert not blocked(nxt, PEOPLE, away_days([trip(PEOPLE, nxt, 1)]))          # back for Sunday


def test_scoring_date_looks_past_blocked_weekends():
    assert scoring_date(SAT, PEOPLE, set()) == SAT
    away = away_days([trip(PEOPLE, SAT + timedelta(days=5), 12)])   # Thu 5 Nov → Tue 17 Nov: two weekends away
    assert scoring_date(SAT, PEOPLE, away) == SAT + timedelta(weeks=2)


def test_travel_leaves_no_capacity_and_events_have_buffers():
    chore = Chore(id="fix", name="fix", category="home", type="one_off", duration_h=1, priority=3,
                  assignee="nicolas")
    people = [Person("nicolas", "Nicolas", 6), Person("anna", "Anna", 6)]
    events = [trip(["nicolas"], SAT, 2)]
    plan = plan_weekend([Scored(chore, 1, 3)], people, busy_from_events(events, 30), SAT)
    assert plan.items == [] and plan.unplanned[0][1] == "no free slot long enough"

    # A party 08:30-17:30 with 30 min buffers leaves no 1 h slot inside 08:00-18:00 on Saturday.
    party = CalEvent(["nicolas"], "Party", datetime(2026, 10, 31, 8, 30), datetime(2026, 10, 31, 17, 30))
    plan = plan_weekend([Scored(chore, 1, 3)], people, busy_from_events([party], 30), SAT)
    assert plan.items[0].start.date() == SAT + timedelta(days=1)
