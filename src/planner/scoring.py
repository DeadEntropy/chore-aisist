"""Which chores are due, and how urgent. Pure logic: no database, no network."""

from datetime import date, timedelta

from planner.models import Chore, Scored

ONE_OFF_START = 0.5          # urgency of a one-off on the day it's added
ONE_OFF_PER_WEEK = 0.2       # added for every week it has been waiting


def urgency_recurring(chore: Chore, last_done: date | None, today: date) -> float | None:
    """Urgency from the interval window; None if not due yet (before `min`).

    0 at min → 0.5 at target → 1.0 at max, then keeps growing while overdue.
    A chore that has never been done counts as fully due (1.0); see `score_chores` for how it keeps growing.
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
    if chore.type == "recurring" and None in (
            chore.interval_min_days, chore.interval_target_days, chore.interval_max_days):
        missing.append("interval")
    return missing


def _need_met(need_id: str, chore: Chore, last_done: dict[str, date], types: dict[str, str]) -> bool:
    """A need counts once done. When both the chore and the need are recurring, the need must be done again
    since the chore was last done (e.g. a fresh mulch order before each yearly re-mulch); a done one-off need
    stays met for good."""
    if need_id not in last_done:
        return False
    if chore.type == "recurring" and types.get(need_id) == "recurring" and chore.id in last_done:
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
    types = {c.id: c.type for c in chores}
    for chore in chores:
        if not chore.active:
            continue
        if chore.type == "one_off" and chore.id in last_done:
            continue                                         # finished for good
        if missing := missing_details(chore):
            excluded.append((chore, "needs details: " + ", ".join(missing)))
            continue
        if chore.type == "recurring":
            last = last_done.get(chore.id)
            if last is None and chore.created_on is not None:
                # Never done: as if last done `max` days before it was added, so it starts fully due (1.0)
                # and keeps growing like any overdue chore.
                last = chore.created_on - timedelta(days=chore.interval_max_days)
            u = urgency_recurring(chore, last, today)
            if u is None:
                continue                                     # not due yet
        else:
            u = urgency_one_off(chore.created_on, today)
        pending = [n for n in chore.needs if not _need_met(n, chore, last_done, types)]
        eligible.append(Scored(chore, round(u, 3), round(u * chore.priority, 3), pending))

    # Needs that loop back on themselves (A needs B, B needs A) can never be planned.
    for s in _in_cycles(eligible):
        eligible.remove(s)
        excluded.append((s.chore, "circular needs: " + ", ".join(s.waits_for)))

    # Drop chores waiting for something that can't be planned now (repeat: dropping one can strand another).
    while True:
        ids = {s.chore.id for s in eligible}
        stuck = [s for s in eligible if any(n not in ids for n in s.waits_for)]
        if not stuck:
            break
        for s in stuck:
            eligible.remove(s)
            excluded.append((s.chore, "waiting for: " + ", ".join(n for n in s.waits_for if n not in ids)))

    # Needs inherit the score of whatever waits on them, all the way down a chain (repeat until stable).
    by_id = {s.chore.id: s for s in eligible}
    changed = True
    while changed:
        changed = False
        for s in eligible:
            for n in s.waits_for:
                if by_id[n].score < s.score:
                    by_id[n].score = s.score
                    changed = True

    # Highest score first; each chore comes right after the needs it waits for. Needs score at least as high
    # as their jobs, so the result stays sorted by score without pushing jobs behind unrelated chores.
    ordered: list[Scored] = []
    seen: set[str] = set()

    def emit(s: Scored) -> None:
        if s.chore.id not in seen:
            seen.add(s.chore.id)
            for n in s.waits_for:
                emit(by_id[n])
            ordered.append(s)

    for s in sorted(eligible, key=lambda s: (-s.score, -s.chore.priority, s.chore.id)):
        emit(s)
    return ordered, excluded


def _in_cycles(eligible: list[Scored]) -> list[Scored]:
    """Chores whose pending needs eventually lead back to themselves."""
    waits = {s.chore.id: s.waits_for for s in eligible}

    def reaches(start: str, target: str, seen: set[str]) -> bool:
        for n in waits.get(start, []):
            if n == target or (n not in seen and (seen.add(n) or reaches(n, target, seen))):
                return True
        return False

    return [s for s in eligible if reaches(s.chore.id, s.chore.id, set())]
