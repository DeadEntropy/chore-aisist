"""Connects the pieces: load data, read calendars, score, fit. (The real weekly run and scheduler come later.)"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from planner import db
from planner.calendars import busy_from_events
from planner.fitting import free_minutes, plan_weekend
from planner.models import Busy, CalEvent, Chore, Person, Plan
from planner.scoring import score_chores

LOOKAHEAD_WEEKS = 6        # how far ahead to look for the next weekend you're not away


@dataclass
class WeekendResult:
    plan: Plan
    excluded: list
    events: list[CalEvent] = field(default_factory=list)   # what blocks time this weekend
    next_available: date | None = None                       # set when the following weekend(s) are blocked
    blocked: bool = False                                    # nobody has any free time this weekend


def next_saturday(today: date) -> date:
    return today + timedelta(days=(5 - today.weekday()) % 7)


def calendar_range(saturday: date, weeks: int) -> tuple[date, date]:
    """Dates to fetch events for: the planned weekends plus the look-ahead."""
    return saturday, saturday + timedelta(weeks=weeks + LOOKAHEAD_WEEKS)


def blocked(saturday: date, people: list[str], busy: list[Busy]) -> bool:
    """A weekend is blocked when nobody has any free time between DAY_START and DAY_END on either day,
    whatever the reason: all-day travel, or timed events covering the whole weekend."""
    days = (saturday, saturday + timedelta(days=1))
    return all(free_minutes(busy, p, d) == 0 for p in people for d in days)


def next_available(saturday: date, people: list[str], busy: list[Busy]) -> date:
    """The first weekend after this one that isn't blocked (or the end of the look-ahead)."""
    nxt = saturday + timedelta(weeks=1)
    for _ in range(LOOKAHEAD_WEEKS - 1):
        if not blocked(nxt, people, busy):
            break
        nxt += timedelta(weeks=1)
    return nxt


def dry_run(conn, saturday: date, weeks: int = 1, events: list[CalEvent] | None = None,
            buffer_minutes: int = 0) -> list[WeekendResult]:
    """Load chores, people and history from the database, then `plan_weekends` without writing anything."""
    return plan_weekends(db.get_chores(conn), db.get_people(conn), db.last_done_dates(conn),
                         saturday, weeks, events or [], buffer_minutes)


def plan_weekends(chores: list[Chore], people: list[Person], last_done: dict[str, date], saturday: date,
                  weeks: int, events: list[CalEvent], buffer_minutes: int) -> list[WeekendResult]:
    """Plan `weeks` consecutive weekends around the calendar `events` (pure: no database, no network).

    Timed events get `buffer_minutes` on both sides. From the second weekend on, every chore planned the weekend
    before is assumed done on its day.
    """
    person_ids = [p.id for p in people]
    last_done = dict(last_done)
    busy = busy_from_events(events, buffer_minutes)

    results = []
    for week in range(weeks):
        day = saturday + timedelta(weeks=week)
        nxt = next_available(day, person_ids, busy)
        ahead = nxt if nxt > day + timedelta(weeks=1) else None
        scored, excluded = score_chores(chores, last_done, today=day, next_available=ahead)
        plan = plan_weekend(scored, people, busy, saturday=day)
        weekend_events = sorted((e for e in events if _overlaps_weekend(e, day)), key=lambda e: e.start)
        results.append(WeekendResult(plan, excluded, weekend_events, ahead, blocked(day, person_ids, busy)))
        for item in plan.items:
            last_done[item.chore.id] = item.start.date()
    return results


def _overlaps_weekend(e: CalEvent, saturday: date) -> bool:
    start = datetime.combine(saturday, time(0))
    return e.start < start + timedelta(days=2) and e.end > start
