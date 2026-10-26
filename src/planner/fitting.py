"""Who does what, on which day, at what time. Pure logic: no database, no network.

Greedy: take chores from highest score down and give each one the first free slot that fits, on the day where
that person (or both, for 'both' chores) has the most free time left. Free time comes from `availability`;
there is no hours budget: any free time in the chore windows can be used. A chore waiting for another (e.g. a job
and its purchase) is only placed after that one ends; chores that others wait for take the earliest slot instead,
to leave room after them.
"""

from datetime import date, datetime, time, timedelta

from planner.availability import Slots, intersect, subtract
from planner.models import Person, Plan, PlannedItem, Scored


def plan_period(scored: list[Scored], people: list[Person], days: list[date],
                free: dict[tuple[str, date], Slots]) -> Plan:
    free = {key: list(slots) for key, slots in free.items()}      # don't modify the caller's slots
    items: list[PlannedItem] = []
    placed_end: dict[str, datetime] = {}
    waited_on = {n for s in scored for n in s.waits_for}
    unplanned = []
    for s in scored:
        duration = round(s.chore.duration_h * 60)
        if missing := [n for n in s.waits_for if n not in placed_end]:
            unplanned.append((s.chore, "needs " + ", ".join(missing) + " first, which didn't fit"))
            continue
        not_before = max((placed_end[n] for n in s.waits_for), default=None)
        groups = _candidate_groups(s.chore.assignee, people, days, free)
        if not groups:
            unplanned.append((s.chore, f"unknown assignee '{s.chore.assignee}'"))
            continue

        placed = None
        for group in groups:
            slot = _best_slot(group, days, free, duration, not_before, first_free=s.chore.id in waited_on)
            if slot:
                placed = (group, *slot)
                break
        if placed is None:
            unplanned.append((s.chore, "no free slot long enough"))
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


def _best_slot(group, days, free, duration, not_before=None, first_free=False) -> tuple[date, int] | None:
    """First slot long enough, on the day where the group has the most common free time
    (or simply the earliest slot of the period, with `first_free`).

    With `not_before`, the slot must start at or after that moment.
    """
    options = []
    for i, day in enumerate(days):
        earliest = 0
        if not_before is not None:
            if day < not_before.date():
                continue
            if day == not_before.date():
                earliest = _minutes(not_before.time())
        common = free[(group[0], day)]
        for p in group[1:]:
            common = intersect(common, free[(p, day)])
        fitting = [max(start, earliest) for start, end in common if end - max(start, earliest) >= duration]
        if fitting:
            total_free = 0 if first_free else sum(end - start for start, end in common)
            options.append((-total_free, i, day, fitting[0]))
    if not options:
        return None
    _, _, day, start = min(options)
    return day, start


def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def _at(day: date, minutes: int) -> datetime:
    return datetime.combine(day, time(0)) + timedelta(minutes=minutes)
