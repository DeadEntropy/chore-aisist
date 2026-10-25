"""Who does what, on which day, at what time. Pure logic: no database, no network.

Greedy: take chores from highest score down and give each one the first free slot that fits,
on the weekend day where that person (or both, for 'both' chores) has the most free time left.
A person's chores never exceed their weekend budget (`weekend_hours`).
"""

from datetime import date, datetime, time, timedelta

from planner.models import Busy, Person, Plan, PlannedItem, Scored

DAY_START = time(8, 0)       # chores can be planned between these times, Saturday and Sunday
DAY_END = time(18, 0)

Slots = list[tuple[int, int]]    # free spans in minutes from midnight, sorted, non-overlapping


def plan_weekend(
    scored: list[Scored],
    people: list[Person],
    busy: list[Busy],
    saturday: date,
    day_start: time = DAY_START,
    day_end: time = DAY_END,
) -> Plan:
    days = [saturday, saturday + timedelta(days=1)]
    window = (_minutes(day_start), _minutes(day_end))
    free = {(p.id, d): [window] for p in people for d in days}
    for b in busy:
        for d in days:
            if (b.person_id, d) in free:
                span = _clip(b, d)
                if span:
                    free[(b.person_id, d)] = _subtract(free[(b.person_id, d)], span)
    budget = {p.id: round(p.weekend_hours * 60) for p in people}

    items: list[PlannedItem] = []
    unplanned = []
    for s in scored:
        duration = round(s.chore.duration_h * 60)
        groups = _candidate_groups(s.chore.assignee, people, budget)
        if not groups:
            unplanned.append((s.chore, f"unknown assignee '{s.chore.assignee}'"))
            continue

        placed = None
        over_budget = True
        for group in groups:
            if any(budget[p] < duration for p in group):
                continue
            over_budget = False
            slot = _best_slot(group, days, free, duration)
            if slot:
                placed = (group, *slot)
                break

        if placed is None:
            unplanned.append((s.chore, "weekend budget used up" if over_budget else "no free slot long enough"))
            continue

        group, day, start = placed
        span = (start, start + duration)
        for p in group:
            free[(p, day)] = _subtract(free[(p, day)], span)
            budget[p] -= duration
        items.append(PlannedItem(s.chore, group, _at(day, span[0]), _at(day, span[1]), s.score))

    items.sort(key=lambda i: (i.start, i.people))
    return Plan(saturday, items, unplanned)


def _candidate_groups(assignee: str, people: list[Person], budget: dict[str, int]) -> list[list[str]]:
    ids = [p.id for p in people]
    if assignee == "both":
        return [ids]
    if assignee == "either":
        return [[p] for p in sorted(ids, key=lambda p: -budget[p])]   # most budget left first
    return [[assignee]] if assignee in ids else []


def _best_slot(group, days, free, duration) -> tuple[date, int] | None:
    """First slot long enough, on the day where the group has the most common free time."""
    options = []
    for i, day in enumerate(days):
        common = free[(group[0], day)]
        for p in group[1:]:
            common = _intersect(common, free[(p, day)])
        fitting = [start for start, end in common if end - start >= duration]
        if fitting:
            total_free = sum(end - start for start, end in common)
            options.append((-total_free, i, day, fitting[0]))
    if not options:
        return None
    _, _, day, start = min(options)
    return day, start


def _subtract(slots: Slots, span: tuple[int, int]) -> Slots:
    s0, s1 = span
    out = []
    for a, b in slots:
        if s1 <= a or s0 >= b:
            out.append((a, b))
            continue
        if a < s0:
            out.append((a, s0))
        if s1 < b:
            out.append((s1, b))
    return out


def _intersect(x: Slots, y: Slots) -> Slots:
    out = []
    for a, b in x:
        for c, d in y:
            lo, hi = max(a, c), min(b, d)
            if lo < hi:
                out.append((lo, hi))
    return sorted(out)


def _clip(b: Busy, day: date) -> tuple[int, int] | None:
    """The part of a busy span that falls on `day`, in minutes from midnight."""
    day_start = datetime.combine(day, time(0))
    lo = max(b.start, day_start)
    hi = min(b.end, day_start + timedelta(days=1))
    if lo >= hi:
        return None
    return int((lo - day_start).total_seconds() // 60), int((hi - day_start).total_seconds() // 60)


def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def _at(day: date, minutes: int) -> datetime:
    return datetime.combine(day, time(0)) + timedelta(minutes=minutes)
