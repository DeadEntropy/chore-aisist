"""Connects the pieces: load data, read calendars, score, fit. (The scheduled daily run comes later.)

Planning works on periods of 7 days. Each day in a period has whatever chore time `availability` gives it:
by default Saturday and Sunday, plus extra time and minus busy time from the calendars.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from planner import db
from planner.availability import first_available_day, first_default_day, free_slots
from planner.config import PlanningSettings
from planner.fitting import plan_period
from planner.models import CalEvent, Chore, Person, Plan
from planner.scoring import score_chores

PERIOD_DAYS = 7
LOOKAHEAD_DAYS = 42        # how far past a period to look for the next day anyone can do chores


@dataclass
class PeriodResult:
    plan: Plan
    excluded: list
    events: list[CalEvent] = field(default_factory=list)   # events that affect chore time in this period
    next_available: date | None = None                       # set when travel pushes the next chore day back
    blocked: bool = False                                    # nobody has any free chore time in this period


def calendar_range(start: date, periods: int) -> tuple[date, date]:
    """Dates to fetch events for: the planned periods plus the look-ahead."""
    return start, start + timedelta(days=PERIOD_DAYS * periods + LOOKAHEAD_DAYS + 1)


def dry_run(conn, start: date, periods: int, events: list[CalEvent], buffer_minutes: int,
            planning: PlanningSettings) -> list[PeriodResult]:
    """Load chores, people and history from the database, then `plan_periods` without writing anything."""
    return plan_periods(db.get_chores(conn), db.get_people(conn), db.last_done_dates(conn),
                        start, periods, events, buffer_minutes, planning)


def plan_periods(chores: list[Chore], people: list[Person], last_done: dict[str, date], start: date,
                 periods: int, events: list[CalEvent], buffer_minutes: int,
                 planning: PlanningSettings) -> list[PeriodResult]:
    """Plan `periods` consecutive 7-day periods from `start` (pure: no database, no network).

    From the second period on, every chore planned in the period before is assumed done on its day.
    """
    person_ids = [p.id for p in people]
    last_done = dict(last_done)
    results = []
    for k in range(periods):
        first = start + timedelta(days=PERIOD_DAYS * k)
        days = [first + timedelta(days=i) for i in range(PERIOD_DAYS)]
        free = free_slots(person_ids, days, planning.windows, events, buffer_minutes, planning.extra_day_window)

        ahead = next_available(person_ids, days[-1], events, buffer_minutes, planning)
        scored, excluded = score_chores(chores, last_done, today=first, next_available=ahead)
        plan = plan_period(scored, people, days, free)
        chore_days = _days_with_chore_time(person_ids, days, events, planning)
        period_events = sorted((e for e in events if _overlaps(e, days) and (e.kind != "busy" or _overlaps(e, chore_days))),
                               key=lambda e: e.start)
        blocked = not any(free[(p, d)] for p in person_ids for d in days)
        results.append(PeriodResult(plan, excluded, period_events, ahead, blocked))
        for item in plan.items:
            last_done[item.chore.id] = item.start.date()
    return results


def next_available(people: list[str], last_day: date, events: list[CalEvent], buffer_minutes: int,
                   planning: PlanningSettings) -> date | None:
    """The next chore day after the period, if the calendars push it later than the default pattern would
    (travel, "No chores"). None when the next chore day is the normal one."""
    normal = first_default_day(last_day, planning.windows)
    actual = first_available_day(people, last_day, planning.windows, events, buffer_minutes,
                                 planning.extra_day_window, LOOKAHEAD_DAYS)
    if actual is None:
        return last_day + timedelta(days=LOOKAHEAD_DAYS)
    return actual if normal is not None and actual > normal else None


def _days_with_chore_time(people: list[str], days: list[date], events: list[CalEvent],
                          planning: PlanningSettings) -> list[date]:
    """Days where someone has chore windows before busy time is removed: busy events on other days don't matter."""
    extras = [e for e in events if e.kind in ("extra_day", "extra_time")]
    base = free_slots(people, days, planning.windows, extras, 0, planning.extra_day_window)
    return [d for d in days if any(base[(p, d)] for p in people)]


def _overlaps(e: CalEvent, days: list[date]) -> bool:
    return any(e.start < datetime.combine(d + timedelta(days=1), time(0)) and e.end > datetime.combine(d, time(0))
               for d in days)
