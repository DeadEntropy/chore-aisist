"""Who does what, on which day, at what time. Pure logic: no database, no network.

Greedy: take chores from highest score down and give each one the earliest free slot that fits that person (or both,
for 'both' chores). Earliest-first keeps re-plans stable: a daily run that adds a new day at the end of the window
doesn't pull chores away from tomorrow. Free time comes from `availability`; there is no hours budget. A chore
waiting for another (e.g. a job and its purchase) is only placed after that one ends, and a chore with a
`not_before` date is not placed on an earlier day. An outdoor chore skips the times the weather rules out (`weather`
passes the rule in; without it, the weather is ignored).
"""

from collections.abc import Callable
from datetime import date, datetime, time, timedelta

from planner.availability import Slots, intersect, subtract
from planner.models import Person, Plan, PlannedItem, Scored

START_STEP_MINUTES = 15     # every chore starts on a quarter hour (:00, :15, :30, :45)


def round_up(minutes: int, step: int = START_STEP_MINUTES) -> int:
    return -(-minutes // step) * step


def plan_period(scored: list[Scored], people: list[Person], days: list[date],
                free: dict[tuple[str, date], Slots], ready_at: dict[str, datetime] | None = None,
                weather: Callable[[date, int, int], str | None] | None = None) -> Plan:
    """`ready_at`: when chores already in progress (not in `scored`) end; chores that need them start after that.
    `weather(day, start, end)`: why that time is unsuitable for an outdoor chore, or None (see `weather.check`)."""
    free = {key: list(slots) for key, slots in free.items()}      # don't modify the caller's slots
    items: list[PlannedItem] = []
    placed_end: dict[str, datetime] = dict(ready_at or {})
    unplanned = []
    for s in scored:
        duration = round(s.chore.duration_h * 60)
        if missing := [n for n in s.waits_for if n not in placed_end]:
            unplanned.append((s.chore, "needs " + ", ".join(missing) + " first, which didn't fit"))
            continue
        not_before = max((placed_end[n] for n in s.waits_for), default=None)
        if s.chore.not_before is not None:
            held = datetime.combine(s.chore.not_before, time(0))
            not_before = max(not_before, held) if not_before else held
        groups = _candidate_groups(s.chore.assignee, people, days, free)
        if not groups:
            unplanned.append((s.chore, f"unknown assignee '{s.chore.assignee}'"))
            continue

        placed, ruled_out = None, None
        for group in groups:
            slot, why = _earliest_slot(group, days, free, duration, not_before,
                                       weather if s.chore.outdoor else None)
            ruled_out = ruled_out or why
            if slot:
                placed = (group, *slot)
                break
        if placed is None:
            unplanned.append((s.chore, f"weather whenever it would fit ({ruled_out})" if ruled_out
                              else "no free slot long enough"))
            continue

        group, day, start = placed
        span = (start, start + duration)
        for p in group:
            free[(p, day)] = subtract(free[(p, day)], span)
        items.append(PlannedItem(s.chore, group, _at(day, span[0]), _at(day, span[1]), s.score))
        placed_end[s.chore.id] = _at(day, span[1])

    items.sort(key=lambda i: (i.start, i.people))
    return Plan(days[0], days, items, unplanned)


def _candidate_groups(assignee: str, people: list[Person], days: list[date],
                      free: dict[tuple[str, date], Slots]) -> list[list[str]]:
    ids = [p.id for p in people]
    if assignee == "both":
        return [ids]
    if assignee == "either":
        left = {p: sum(b - a for d in days for a, b in free[(p, d)]) for p in ids}
        return [[p] for p in sorted(ids, key=lambda p: -left[p])]      # most free time left first
    return [[assignee]] if assignee in ids else []


def _earliest_slot(group, days, free, duration, not_before=None,
                   weather=None) -> tuple[tuple[date, int] | None, str | None]:
    """The earliest slot long enough where everyone in the group is free (at or after `not_before`, if given),
    starting on a quarter hour, and that `weather` allows; with the first time the weather ruled out, e.g.
    'Sun 01 Nov 08:00: rain 23%' (None if it ruled out none)."""
    ruled_out = None
    for day in days:
        earliest = 0
        if not_before is not None:
            if day < not_before.date():
                continue
            if day == not_before.date():
                earliest = _minutes(not_before.time())
        common = free[(group[0], day)]
        for p in group[1:]:
            common = intersect(common, free[(p, day)])
        for start, end in common:
            at = round_up(max(start, earliest))
            while end - at >= duration:
                why = weather(day, at, at + duration) if weather else None
                if why is None:
                    return (day, at), ruled_out
                ruled_out = ruled_out or f"{_at(day, at):%a %d %b %H:%M}: {why}"
                at += START_STEP_MINUTES
    return None, ruled_out


def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def _at(day: date, minutes: int) -> datetime:
    return datetime.combine(day, time(0)) + timedelta(minutes=minutes)
