"""Connects the pieces: load data, read calendars, score, fit. (The real weekly run and scheduler come later.)"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from planner import db
from planner.calendars import away_days, busy_from_events
from planner.fitting import plan_weekend
from planner.models import CalEvent, Plan
from planner.scoring import score_chores

LOOKAHEAD_WEEKS = 6        # how far ahead to look for the next weekend you're not away


@dataclass
class WeekendResult:
    plan: Plan
    excluded: list
    events: list[CalEvent] = field(default_factory=list)   # what blocks time this weekend
    scored_as_of: date | None = None                         # set when travel look-ahead moved the scoring date


def next_saturday(today: date) -> date:
    return today + timedelta(days=(5 - today.weekday()) % 7)


def calendar_range(saturday: date, weeks: int) -> tuple[date, date]:
    """Dates to fetch events for: the planned weekends plus the look-ahead."""
    return saturday, saturday + timedelta(weeks=weeks + LOOKAHEAD_WEEKS)


def blocked(saturday: date, people: list[str], away: set[tuple[str, date]]) -> bool:
    """A weekend is blocked when everyone is away both days."""
    days = (saturday, saturday + timedelta(days=1))
    return all((p, d) in away for p in people for d in days)


def scoring_date(saturday: date, people: list[str], away: set[tuple[str, date]]) -> date:
    """Score chores as of the weekend before the next available one.

    Normally that's this weekend. If the following weekend(s) are blocked by travel, chores are scored as if
    it were already the last blocked weekend, so whatever would go overdue during the trip is done now.
    """
    nxt = saturday + timedelta(weeks=1)
    for _ in range(LOOKAHEAD_WEEKS):
        if not blocked(nxt, people, away):
            break
        nxt += timedelta(weeks=1)
    return nxt - timedelta(weeks=1)


def dry_run(conn, saturday: date, weeks: int = 1, events: list[CalEvent] | None = None,
            buffer_minutes: int = 0) -> list[WeekendResult]:
    """Plan `weeks` consecutive weekends without writing anything.

    `events` come from the family calendars (None = everyone free); timed events get `buffer_minutes` on both
    sides. From the second weekend on, every chore planned the weekend before is assumed done on its day.
    """
    chores = db.get_chores(conn)
    people = db.get_people(conn)
    person_ids = [p.id for p in people]
    last_done = db.last_done_dates(conn)
    events = events or []
    busy = busy_from_events(events, buffer_minutes)
    away = away_days(events)

    results = []
    for week in range(weeks):
        day = saturday + timedelta(weeks=week)
        as_of = scoring_date(day, person_ids, away)
        scored, excluded = score_chores(chores, last_done, today=as_of)
        plan = plan_weekend(scored, people, busy, saturday=day)
        weekend_events = sorted((e for e in events if _overlaps_weekend(e, day)), key=lambda e: e.start)
        results.append(WeekendResult(plan, excluded, weekend_events, as_of if as_of != day else None))
        for item in plan.items:
            last_done[item.chore.id] = item.start.date()
    return results


def _overlaps_weekend(e: CalEvent, saturday: date) -> bool:
    start = datetime.combine(saturday, time(0))
    return e.start < start + timedelta(days=2) and e.end > start
