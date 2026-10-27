"""When the daily re-plan is due. Pure logic: no database, no network, no sleeping.

The schedule is a list of times of day (PLAN_TIMES, default 20:00), in local time. A run is due when the last
successful scheduled run started before the most recent scheduled time: that covers the normal case, a run missed
while the container was down (caught up once on start, not once per missed day), and a failed run (retried).
"""

from datetime import datetime, time, timedelta

RETRY_AFTER = timedelta(minutes=15)       # wait after a failed run before trying again
HEALTHY_WITHIN = timedelta(hours=26)      # a successful run must be this recent for `planner health` (daily + slack)


def parse_times(text: str) -> tuple[time, ...]:
    """'20:00' or '07:00, 20:00' → sorted times of day, no duplicates."""
    out = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            hh, mm = part.split(":")
            out.add(time(int(hh), int(mm)))
        except ValueError:
            raise ValueError(f"PLAN_TIMES entry '{part}' should look like HH:MM (e.g. 20:00)") from None
    if not out:
        raise ValueError("PLAN_TIMES is empty")
    return tuple(sorted(out))


def latest_scheduled(now: datetime, times: tuple[time, ...]) -> datetime:
    """The most recent scheduled moment at or before `now` (yesterday's last one if none today yet)."""
    today = [datetime.combine(now.date(), t) for t in times if datetime.combine(now.date(), t) <= now]
    return today[-1] if today else datetime.combine(now.date() - timedelta(days=1), times[-1])


def next_scheduled(now: datetime, times: tuple[time, ...]) -> datetime:
    """The first scheduled moment strictly after `now`."""
    for t in times:
        moment = datetime.combine(now.date(), t)
        if moment > now:
            return moment
    return datetime.combine(now.date() + timedelta(days=1), times[0])


def is_due(now: datetime, times: tuple[time, ...], last_ok: datetime | None) -> bool:
    """True if no successful scheduled run has started since the most recent scheduled time."""
    return last_ok is None or last_ok < latest_scheduled(now, times)


def next_wake(now: datetime, times: tuple[time, ...], failed: bool) -> datetime:
    """When to check again: the next scheduled time, or sooner to retry a failed run."""
    wake = next_scheduled(now, times)
    return min(wake, now + RETRY_AFTER) if failed else wake


def is_healthy(now: datetime, last_ok: datetime | None) -> bool:
    return last_ok is not None and now - last_ok <= HEALTHY_WITHIN
