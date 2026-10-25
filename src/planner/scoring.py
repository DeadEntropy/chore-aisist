"""Which chores are due, and how urgent. Pure logic: no database, no network."""

from datetime import date

from planner.models import Chore, Scored

ONE_OFF_START = 0.5          # urgency of a one-off on the day it's added
ONE_OFF_PER_WEEK = 0.2       # added for every week it has been waiting


def urgency_recurring(chore: Chore, last_done: date | None, today: date) -> float | None:
    """Urgency from the interval window; None if not due yet (before `min`).

    0 at min → 0.5 at target → 1.0 at max, then keeps growing while overdue.
    A chore that has never been done counts as fully due (1.0).
    """
    if last_done is None:
        return 1.0
    lo, mid, hi = chore.interval_min_days, chore.interval_target_days, chore.interval_max_days
    d = (today - last_done).days
    if d < lo:
        return None
    if d <= mid:
        return 0.5 * (d - lo) / (mid - lo) if mid > lo else 0.5
    if d <= hi:
        return 0.5 + 0.5 * (d - mid) / (hi - mid) if hi > mid else 1.0
    return 1.0 + (d - hi) / max(hi - lo, 1)


def urgency_one_off(waiting_since: date | None, today: date) -> float:
    weeks = max((today - waiting_since).days, 0) / 7 if waiting_since else 0
    return ONE_OFF_START + ONE_OFF_PER_WEEK * weeks


def missing_details(chore: Chore) -> list[str]:
    missing = [f for f in ("duration_h", "priority", "assignee") if getattr(chore, f) is None]
    if chore.type == "recurring" and chore.interval_min_days is None:
        missing.append("interval")
    return missing


def _need_met(need_id: str, chore: Chore, last_done: dict[str, date]) -> bool:
    """A need counts once done; for a recurring chore, it must be done again since the chore was last done
    (e.g. a fresh mulch order before each yearly re-mulch)."""
    if need_id not in last_done:
        return False
    if chore.type == "recurring" and chore.id in last_done:
        return last_done[need_id] > last_done[chore.id]
    return True


def score_chores(
    chores: list[Chore],
    last_done: dict[str, date],
    today: date,
) -> tuple[list[Scored], list[tuple[Chore, str]]]:
    """Return (eligible chores sorted by score, highest first; excluded chores with the reason).

    A chore whose needs aren't done yet stays eligible if every pending need is itself eligible: fitting then
    places it after them on the same weekend. Each pending need inherits the job's score if that is higher,
    so a purchase is never ranked below the job it unlocks.
    """
    eligible: list[Scored] = []
    excluded: list[tuple[Chore, str]] = []
    for chore in chores:
        if not chore.active:
            continue
        if chore.type == "one_off" and chore.id in last_done:
            continue                                         # finished for good
        if missing := missing_details(chore):
            excluded.append((chore, "needs details: " + ", ".join(missing)))
            continue
        if chore.type == "recurring":
            u = urgency_recurring(chore, last_done.get(chore.id), today)
            if u is None:
                continue                                     # not due yet
        else:
            u = urgency_one_off(chore.created_on, today)
        pending = [n for n in chore.needs if not _need_met(n, chore, last_done)]
        eligible.append(Scored(chore, round(u, 3), round(u * chore.priority, 3), pending))

    # Drop chores waiting for something that can't be planned now (repeat: dropping one can strand another).
    while True:
        ids = {s.chore.id for s in eligible}
        stuck = [s for s in eligible if any(n not in ids for n in s.waits_for)]
        if not stuck:
            break
        for s in stuck:
            eligible.remove(s)
            excluded.append((s.chore, "waiting for: " + ", ".join(n for n in s.waits_for if n not in ids)))

    by_id = {s.chore.id: s for s in eligible}
    for s in sorted(eligible, key=lambda s: -s.score):
        for n in s.waits_for:
            by_id[n].score = max(by_id[n].score, s.score)

    # Ties: chores others wait for go first.
    eligible.sort(key=lambda s: (-s.score, len(s.waits_for), -s.chore.priority, s.chore.id))
    return eligible, excluded
