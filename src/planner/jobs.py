"""Connects the pieces: load data, read calendars, score, fit, save the plan and mirror it to the Chores calendar;
the real runs (`planner plan`, the re-plan after a report, the scheduler's runs) with their run log, the scheduler's
check (back up, then re-plan when due) and health. `daemon` keeps the scheduler running; `cli` just calls in here.

Planning works on periods of 7 days. Each day in a period has whatever chore time `availability` gives it:
by default Saturday and Sunday, plus extra time and minus busy time from the calendars. With a weather forecast,
outdoor chores avoid the times it rules out (see `weather`); without one (not configured, or the weather service
failed) they're planned as before.
"""

import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

from planner import backup, calendars, db, plans, scheduler, weather
from planner.availability import first_available_day, first_default_day, free_slots, subtract
from planner.config import BackupSettings, CalendarSettings, PlanningSettings, Settings
from planner.fitting import plan_period
from planner.models import CalEvent, Chore, Completion, Person, Plan, PlannedRecord, RunRecord
from planner.weather import Forecast
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
    weather: list[tuple[date, str]] = field(default_factory=list)   # forecast on the days with chore time


def calendar_range(start: date, periods: int) -> tuple[date, date]:
    """Dates to fetch events for: the planned periods plus the look-ahead."""
    return start, start + timedelta(days=PERIOD_DAYS * periods + LOOKAHEAD_DAYS + 1)


HISTORY_DAYS = 14         # how far back the saved plan is checked (freezing, unknowns) and mirrored to the calendar


class CalendarSyncError(Exception):
    """The database change is committed, but the Chores calendar couldn't be updated (the next run retries)."""


def dry_run(conn, start: date, periods: int, events: list[CalEvent], buffer_minutes: int,
            planning: PlanningSettings, now: datetime, forecast: Forecast | None = None) -> list[PeriodResult]:
    """Load chores, people and history from the database, then `plan_periods` without writing anything.
    Planning from today starts at `now`; chores in progress aren't planned again."""
    _, in_progress = plans.freeze(_saved(conn, now.date()), now)
    return plan_periods(db.get_chores(conn), db.get_people(conn), db.last_done_dates(conn), start, periods, events,
                        buffer_minutes, planning, not_before=now if start <= now.date() else None,
                        in_progress=in_progress, forecast=forecast)


@dataclass
class RunSummary:
    result: PeriodResult
    changes: plans.Changes
    unknown: list[PlannedRecord]
    calendar_put: int = 0
    calendar_deleted: int = 0
    weather_error: str | None = None       # the forecast couldn't be read: planned without it


def run_plan(conn, cal, planning: PlanningSettings, now: datetime, events: list[CalEvent],
             forecast: Forecast | None = None) -> RunSummary:
    """Plan from `now` to the end of the 7-day period starting today, save it, and make the Chores calendar match
    (see `plans` for the rules). Raises CalendarSyncError if only the calendar update failed.
    Holds the planner lock throughout, so a report from another process can't be overwritten mid-run."""
    with db.plan_lock(conn):
        start = now.date()
        records = _saved(conn, start)
        to_unknown, in_progress = plans.freeze(records, now)
        result = plan_periods(db.get_chores(conn), db.get_people(conn), db.last_done_dates(conn), start, 1, events,
                              cal.buffer_minutes, planning, not_before=now, in_progress=in_progress,
                              forecast=forecast)[0]
        changes = plans.reconcile(records, result.plan.items, now)

        for r in to_unknown:
            r.status = "unknown"
            db.update_planned(conn, r)
        for r in changes.add:
            db.add_planned(conn, r)
        for r in changes.move:
            db.update_planned(conn, r)
        db.delete_planned(conn, [r.uid for r in changes.remove])
        conn.commit()                       # the database is the source of truth; the calendar mirrors it

        summary = RunSummary(result, changes, to_unknown)
        try:
            summary.calendar_put, summary.calendar_deleted = sync_calendar(
                conn, cal, start - timedelta(days=HISTORY_DAYS), start + timedelta(days=PERIOD_DAYS))
        except Exception as exc:
            raise CalendarSyncError(f"{type(exc).__name__}: {exc}") from exc
        return summary


def sync_calendar(conn, cal, first: date, last: date) -> tuple[int, int]:
    """Make the planner's events in the Chores calendar between `first` and `last` match the `planned` table."""
    records = db.planned_between(conn, _at(first), _at(last))
    names = {c.id: c.name for c in db.get_chores(conn, include_inactive=True)}
    desired = plans.desired_events(records, names, cal.reminder_minutes)
    existing = calendars.chore_events(cal, first, last)
    put, delete = plans.calendar_diff(desired, existing)
    calendars.write_chore_events(cal, put, delete)
    return len(put), len(delete)


def report(conn, cal, chore_id: str, outcome: str, day: date, by: list[str] | None) -> PlannedRecord | None:
    """Record that a chore was done or skipped on `day`, mark its planned item, and update the calendar.

    The planned item is the open or unknown one closest to `day`; `by` defaults to the people it was planned for.
    Holds the planner lock throughout (see `run_plan`).
    """
    with db.plan_lock(conn):
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
            if outcome == "done":
                record.people = who                    # `planner status` shows who actually did it
            db.update_planned(conn, record)
        conn.commit()
        if cal is not None and record is not None:
            try:
                sync_calendar(conn, cal, record.start.date(), record.start.date() + timedelta(days=1))
            except Exception as exc:
                raise CalendarSyncError(f"{type(exc).__name__}: {exc}") from exc
        return record


# --- real runs: safety checks and the run log ---------------------------------------

def check_catalog(conn) -> None:
    """Stop if the database has no people or no chores: it's the wrong or a freshly created database (e.g. the
    data folder wasn't mounted), and planning on it would empty the Chores calendar."""
    missing = [what for what, rows in (("people", db.get_people(conn)), ("chores", db.get_chores(conn))) if not rows]
    if missing:
        raise ValueError(f"The database has no {' and no '.join(missing)}: is the right data folder mounted? "
                         "Refusing to plan (it would empty the Chores calendar).")


def check_calendar_people(calendar_people: set[str], known: set[str]) -> None:
    """Stop if BUSY_CALENDARS names someone who isn't a person in the database: their events would be ignored."""
    unknown = sorted(calendar_people - known)
    if unknown:
        raise ValueError(f"BUSY_CALENDARS names unknown people: {', '.join(unknown)} "
                         f"(known: {', '.join(sorted(known))})")


def fetch_events(cal: CalendarSettings, first: date, last: date) -> list[CalEvent]:
    try:
        return calendars.fetch_events(cal, first, last)
    except Exception as exc:          # network, TLS, auth: report it plainly, don't plan on wrong data
        raise ValueError(f"Couldn't read the calendars ({type(exc).__name__}: {exc}). "
                         "Use --dry-run --no-calendar to plan without them.") from exc


def fetch_weather(planning: PlanningSettings, today: date) -> tuple[Forecast | None, str | None]:
    """(forecast, error). The forecast is a nice-to-have: if it can't be read, the error is printed (the scheduler's
    log) and returned, and the plan is made without it, as when the weather isn't configured."""
    if planning.weather is None:
        return None, None
    try:
        return weather.fetch_forecast(planning.weather, today), None
    except Exception as exc:          # network, timeout, unexpected answer: plan as if there were no forecast
        error = f"{type(exc).__name__}: {exc}"
        print(f"error: couldn't read the weather forecast ({error}); planning without it", file=sys.stderr)
        return None, error


def plan_now(conn, cal: CalendarSettings | None, planning: PlanningSettings, trigger: str) -> RunSummary:
    """A real run, as `planner plan`, the re-plan after a report and the scheduler do it: check the database and the
    calendar settings, read the calendars, re-plan from now (`run_plan`), and record the run in the run log, failed
    or not (the scheduler reads it to catch up and retry)."""
    started = datetime.now()
    try:
        summary = _plan_now(conn, cal, planning)
    except Exception as exc:
        try:
            conn.rollback()
        except db.DatabaseError:               # e.g. the connection dropped: keep the original error, not this one
            pass
        _log_run(conn, RunRecord(started, datetime.now(), trigger, "error", f"{type(exc).__name__}: {exc}"))
        raise
    _log_run(conn, RunRecord(started, datetime.now(), trigger, "ok", describe(summary)))
    return summary


def _plan_now(conn, cal: CalendarSettings | None, planning: PlanningSettings) -> RunSummary:
    check_catalog(conn)
    if cal is None:
        raise ValueError("Writing the plan needs the calendars (CALDAV_URL not set); use --dry-run to only print it")
    check_calendar_people(cal.people(), {p.id for p in db.get_people(conn)})
    now = datetime.now()
    events = fetch_events(cal, *calendar_range(now.date(), 1))
    forecast, weather_error = fetch_weather(planning, now.date())
    try:
        summary = run_plan(conn, cal, planning, now, events, forecast)
        summary.weather_error = weather_error
        return summary
    except CalendarSyncError as exc:
        raise ValueError(f"Plan saved, but couldn't update the Chores calendar ({exc}); "
                         "run `planner plan` again to retry") from exc


def describe(summary: RunSummary) -> str:
    """One line on what a run changed (printed, and kept in the run log)."""
    c = summary.changes
    return (f"{len(c.add)} added, {len(c.move)} moved, {len(c.remove)} removed"
            f"{f', {len(summary.unknown)} past chores marked unknown' if summary.unknown else ''}."
            f" Chores calendar: {summary.calendar_put} events written, {summary.calendar_deleted} deleted."
            + (f" Weather forecast unavailable ({summary.weather_error}): planned without it."
               if summary.weather_error else ""))


def _log_run(conn, run: RunRecord) -> None:
    try:
        db.log_run(conn, run)
        conn.commit()
    except db.DatabaseError as exc:          # the run itself matters more than its log entry
        print(f"warning: couldn't write the run log ({exc})", file=sys.stderr)


# --- the scheduler: one check, backups, health ----------------------------------------

def scheduled_check(settings: Settings, cal: CalendarSettings, planning: PlanningSettings, times: tuple[time, ...],
                    backups: BackupSettings | None, log) -> None:
    """One check of the scheduler (`daemon` calls it, then sleeps): if a scheduled run is due, back up (when
    configured) and re-plan. A failed re-plan raises; the daemon logs it and retries. `log(message, error=False)`
    reports progress."""
    with db.connect(settings) as conn:        # a fresh connection per check: a day-old one would have timed out
        last = db.last_run(conn, trigger="schedule", outcome="ok")
        now = datetime.now()
        if not scheduler.is_due(now, times, last.started_at if last else None):
            return
        if backups:
            backup_before_run(settings, backups, now, times, log)
        log("Scheduled re-plan")
        log(f"Re-planned: {describe(plan_now(conn, cal, planning, 'schedule'))}")


def backup_before_run(settings: Settings, backups: BackupSettings, now: datetime, times: tuple[time, ...],
                      log) -> None:
    """Back up before a scheduled run, unless a backup was already made since the last scheduled time (a retried
    run doesn't back up again). A failed backup is logged; the re-plan still runs (it never deletes chores or history)."""
    folder = Path(backups.folder)
    try:                                       # anything here, even listing the folder, must not stop the re-plan
        made = backup.latest(folder)
        if made is not None and made >= scheduler.latest_scheduled(now, times).replace(second=0):
            return
        log(f"Backed up to {backup.dump(settings, folder, now, backups.keep_days)}")
    except Exception as exc:
        log(f"Backup failed: {type(exc).__name__}: {exc}", error=True)


def health(conn, now: datetime, backups: BackupSettings | None) -> tuple[list[str], str]:
    """(problems, summary) for `planner health`: unhealthy without a successful *scheduled* run, or (when backups
    are on) a backup, in the last 26 hours. Only scheduled runs count, so a hung scheduler isn't hidden by the
    re-plans that `done`/`skip` make."""
    problems = []
    last = db.last_run(conn, trigger="schedule", outcome="ok")
    if not scheduler.is_healthy(now, last.started_at if last else None):
        failed = db.last_run(conn, trigger="schedule")
        problems.append("no successful scheduled run in the last 26 hours"
                        + (f" (last attempt {failed.started_at:%a %d %b %H:%M}: {failed.message})" if failed else ""))
    made = backup.latest(Path(backups.folder)) if backups else None
    if backups and not scheduler.is_healthy(now, made):
        problems.append(f"no backup in {backups.folder} in the last 26 hours")
    summary = (f"last scheduled run {last.started_at:%a %d %b %H:%M}" if last else "no scheduled run yet") \
        + (f", last backup {made:%a %d %b %H:%M}" if made else "")
    return problems, summary


def _saved(conn, start: date) -> list[PlannedRecord]:
    return db.planned_between(conn, _at(start - timedelta(days=HISTORY_DAYS)),
                              _at(start + timedelta(days=PERIOD_DAYS)))


def _at(day: date) -> datetime:
    return datetime.combine(day, time(0))


def plan_periods(chores: list[Chore], people: list[Person], last_done: dict[str, date], start: date,
                 periods: int, events: list[CalEvent], buffer_minutes: int,
                 planning: PlanningSettings, not_before: datetime | None = None,
                 in_progress: list[PlannedRecord] = (), forecast: Forecast | None = None) -> list[PeriodResult]:
    """Plan `periods` consecutive 7-day periods from `start` (pure: no database, no network).

    With `not_before` (normally now), nothing is planned before the next quarter hour after it. `in_progress` items
    (started, not reported, not ended) aren't planned again in the period they're in; they keep their people's time
    taken there, and chores that need them are planned after they end. From the next period on (and in a preview
    that starts after them), they and every chore planned in the period before are assumed done on their day.
    With a `forecast` (and `planning.weather`), outdoor chores avoid the times it rules out.
    """
    person_ids = [p.id for p in people]
    last_done = dict(last_done)
    use_weather = forecast is not None and planning.weather is not None
    rule = weather.check(forecast, planning.weather) if use_weather else None
    for r in in_progress:
        if r.start.date() < start:                           # a preview starting later: it will be done by then
            last_done[r.chore_id] = r.start.date()
    results = []
    for k in range(periods):
        first = start + timedelta(days=PERIOD_DAYS * k)
        days = [first + timedelta(days=i) for i in range(PERIOD_DAYS)]
        free = free_slots(person_ids, days, planning.windows, events, buffer_minutes, planning.extra_day_window)
        if not_before is not None and not_before.date() in days:
            cutoff = not_before.hour * 60 + not_before.minute + (1 if not_before.second else 0)   # fitting rounds up
            for p in person_ids:
                free[(p, not_before.date())] = subtract(free[(p, not_before.date())], (0, cutoff))
        current = [r for r in in_progress if r.start.date() in days]
        for r in current:                                    # in progress: their time stays taken
            day = r.start.date()
            span = (r.start.hour * 60 + r.start.minute,
                    24 * 60 if r.end.date() > day else r.end.hour * 60 + r.end.minute)
            for p in r.people:
                if (p, day) in free:
                    free[(p, day)] = subtract(free[(p, day)], span)

        ahead = next_available(person_ids, days[-1], events, buffer_minutes, planning)
        scored, excluded = score_chores(chores, last_done, today=first, next_available=ahead, until=days[-1],
                                        in_progress={r.chore_id for r in current})
        plan = plan_period(scored, people, days, free, ready_at={r.chore_id: r.end for r in current}, weather=rule)
        chore_hours = _chore_hours(person_ids, days, events, planning)
        chore_days = list(chore_hours)
        outlook = [(d, text) for d, (a, b) in chore_hours.items()
                   if use_weather and (text := weather.day_summary(forecast, d, a, b))]
        period_events = sorted((e for e in events if _overlaps(e, days) and (e.kind != "busy" or _overlaps(e, chore_days))),
                               key=lambda e: e.start)
        blocked = not any(free[(p, d)] for p in person_ids for d in days)
        results.append(PeriodResult(plan, excluded, period_events, ahead, blocked, outlook))
        for item in plan.items:
            last_done[item.chore.id] = item.start.date()
        for r in current:
            last_done[r.chore_id] = r.start.date()
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


def _chore_hours(people: list[str], days: list[date], events: list[CalEvent],
                 planning: PlanningSettings) -> dict[date, tuple[int, int]]:
    """Days where someone has chore windows before busy time is removed (busy events on other days don't matter),
    with the first and last minute of those windows."""
    extras = [e for e in events if e.kind in ("extra_day", "extra_time")]
    base = free_slots(people, days, planning.windows, extras, 0, planning.extra_day_window)
    out = {}
    for d in days:
        if slots := [s for p in people for s in base[(p, d)]]:
            out[d] = (min(a for a, _ in slots), max(b for _, b in slots))
    return out


def _overlaps(e: CalEvent, days: list[date]) -> bool:
    return any(e.start < datetime.combine(d + timedelta(days=1), time(0)) and e.end > datetime.combine(d, time(0))
               for d in days)
