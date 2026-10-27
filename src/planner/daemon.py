"""Keeps the scheduler running (`planner serve`, the container's command): check, sleep, repeat.

Only what can't usefully be unit-tested lives here: the forever loop, sleeping, the SIGTERM handler and timestamped
log lines. What a check does is `jobs.scheduled_check`; when the next one is due is `scheduler`.
"""

import signal
import sys
import time as clock
from datetime import datetime

from planner import db, jobs, scheduler
from planner.config import (Settings, load_backup_settings, load_calendar_settings, load_plan_times,
                            load_planning_settings)


def serve(settings: Settings) -> None:
    """Re-plan at every PLAN_TIMES moment, forever. A run missed while the container was down is caught up once on
    start; a failed run is retried every 15 minutes until it succeeds or the next scheduled time comes."""
    times = load_plan_times()
    planning = load_planning_settings()
    cal = load_calendar_settings()
    if cal is None:                                # fail at start, not at 20:00, if the settings are wrong
        raise ValueError("The scheduler needs the calendars to write the plan (CALDAV_URL not set)")
    backups = load_backup_settings()
    with db.connect(settings) as conn:             # refuse to start on an empty (wrong or freshly created) database
        jobs.check_catalog(conn)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))      # `docker stop`: exit at once (we're PID 1)
    log(f"Scheduler started: re-plans every day at {', '.join(t.strftime('%H:%M') for t in times)}; "
        + (f"backs up to {backups.folder} first (kept {backups.keep_days} days)" if backups
           else "no backups (BACKUP_DIR not set)"))
    while True:
        failed = False
        try:
            jobs.scheduled_check(settings, cal, planning, times, backups, log)
        except Exception as exc:                   # log and carry on: the next run starts from the saved plan
            failed = True
            log(f"Run failed: {type(exc).__name__}: {exc}", error=True)
        wake = scheduler.next_wake(datetime.now(), times, failed)
        log(f"Next check {wake:%a %d %b %H:%M}")
        sleep_until(wake)


def sleep_until(moment: datetime) -> None:
    """Sleep in short steps, so a clock change (or a suspended host) doesn't make us oversleep."""
    while (left := (moment - datetime.now()).total_seconds()) > 0:
        clock.sleep(min(left, 300))


def log(message: str, error: bool = False) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}", file=sys.stderr if error else sys.stdout, flush=True)
