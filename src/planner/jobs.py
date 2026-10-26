"""Connects the pieces: load data, read calendars, score, fit, save the plan and mirror it to the Chores calendar.
(The scheduled daily run comes later.)

Planning works on periods of 7 days. Each day in a period has whatever chore time `availability` gives it:
by default Saturday and Sunday, plus extra time and minus busy time from the calendars.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from planner import calendars, db, plans
from planner.availability import first_available_day, first_default_day, free_slots
from planner.config import PlanningSettings
from planner.fitting import plan_period
from planner.models import CalEvent, Chore, Completion, Person, Plan, PlannedRecord
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


HISTORY_DAYS = 14         # how far back the saved plan is checked (freezing, unknowns) and mirrored to the calendar


def dry_run(conn, start: date, periods: int, events: list[CalEvent], buffer_minutes: int,
            planning: PlanningSettings, now: datetime) -> list[PeriodResult]:
    """Load chores, people and history from the database, then `plan_periods` without writing anything.
    Chores with an item still pending before `start` (later today) aren't planned again."""
    _, pending = plans.freeze(_saved(conn, start), start, now)
    chores = [c for c in db.get_chores(conn) if c.id not in pending]
    return plan_periods(chores, db.get_people(conn), db.last_done_dates(conn),
                        start, periods, events, buffer_minutes, planning)


@dataclass
class RunSummary:
    result: PeriodResult
    changes: plans.Changes
    unknown: list[PlannedRecord]
    calendar_put: int = 0
    calendar_deleted: int = 0


def run_plan(conn, cal, planning: PlanningSettings, start: date, now: datetime,
             events: list[CalEvent]) -> RunSummary:
    """Plan the 7 days from `start`, save it, and make the Chores calendar match (see `plans` for the rules)."""
    records = _saved(conn, start)
    to_unknown, pending = plans.freeze(records, start, now)
    chores = [c for c in db.get_chores(conn) if c.id not in pending]
    result = plan_periods(chores, db.get_people(conn), db.last_done_dates(conn), start, 1, events,
                          cal.buffer_minutes, planning)[0]
    changes = plans.reconcile(records, result.plan.items, start)

    for r in to_unknown:
        r.status = "unknown"
        db.update_planned(conn, r)
    for r in changes.add:
        db.add_planned(conn, r)
    for r in changes.move:
        db.update_planned(conn, r)
    db.delete_planned(conn, [r.uid for r in changes.remove])
    conn.commit()                       # the database is the source of truth; the calendar mirrors it

    put, deleted = sync_calendar(conn, cal, start - timedelta(days=HISTORY_DAYS), start + timedelta(days=PERIOD_DAYS))
    return RunSummary(result, changes, to_unknown, put, deleted)


def sync_calendar(conn, cal, first: date, last: date) -> tuple[int, int]:
    """Make the planner's events in the Chores calendar between `first` and `last` match the `planned` table."""
    records = db.planned_between(conn, _at(first), _at(last))
    names = {c.id: c.name for c in db.get_chores(conn, include_inactive=True)}
    desired = plans.desired_events(records, names)
    existing = calendars.chore_events(cal, first, last)
    put, delete = plans.calendar_diff(desired, existing)
    calendars.write_chore_events(cal, put, delete)
    return len(put), len(delete)


def report(conn, cal, chore_id: str, outcome: str, day: date, by: list[str] | None) -> PlannedRecord | None:
    """Record that a chore was done or skipped on `day`, mark its planned item, and update the calendar.

    The planned item is the open or unknown one closest to `day`; `by` defaults to the people it was planned for.
    """
    outcome = {"done": "done", "skip": "skipped", "skipped": "skipped"}[outcome]
    chore = db.get_chore(conn, chore_id)
    if chore is None:
        raise ValueError(f"No chore with id '{chore_id}' (see `planner chore list`)")
    people = {p.id for p in db.get_people(conn)}
    record = plans.find_record(db.planned_between(conn, _at(day - timedelta(days=HISTORY_DAYS)),
                                                  _at(day + timedelta(days=PERIOD_DAYS + 1))), chore_id, day)
    if by:
        who = by
    elif record:
        who = record.people
    elif chore.assignee in people:
        who = [chore.assignee]
    elif chore.assignee == "both":
        who = sorted(people)
    else:
        who = []
    if unknown := [p for p in who if p not in people]:
        raise ValueError(f"Unknown person: {', '.join(unknown)} (known: {', '.join(sorted(people))})")
    if outcome == "done" and not who:
        raise ValueError(f"Who did '{chore_id}'? It wasn't planned for anyone: use --by")

    for person in (who if outcome == "done" else who[:1] or [None]):
        db.record_completion(conn, Completion(chore_id, day, outcome, "claude", person))
    if record:
        record.status = outcome
        db.update_planned(conn, record)
    conn.commit()
    if cal is not None and record is not None:
        sync_calendar(conn, cal, record.start.date(), record.start.date() + timedelta(days=1))
    return record


def _saved(conn, start: date) -> list[PlannedRecord]:
    return db.planned_between(conn, _at(start - timedelta(days=HISTORY_DAYS)),
                              _at(start + timedelta(days=PERIOD_DAYS)))


def _at(day: date) -> datetime:
    return datetime.combine(day, time(0))


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
