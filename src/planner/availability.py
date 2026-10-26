"""When each person can do chores. Pure logic: no database, no network.

Free time per person and day =
    the default weekly windows (CHORE_WINDOWS, e.g. Saturday and Sunday 08:00-18:00)
  + extra availability from the calendars (an all-day "Day off" / "Holiday", a timed "Chores" event)
  - days marked unavailable (all-day travel, all-day "No chores")
  - busy events, with a buffer before and after.
"""

import re
from datetime import date, datetime, time, timedelta

from planner.models import CalEvent

Slots = list[tuple[int, int]]          # free spans in minutes from midnight, sorted, non-overlapping
Windows = dict[int, Slots]             # weekday (0 = Monday) → default chore windows
DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def parse_windows(text: str) -> Windows:
    """'sat 08:00-18:00; sun 08:00-18:00' → {5: [(480, 1080)], 6: [(480, 1080)]}"""
    windows: Windows = {}
    for entry in text.split(";"):
        if not entry.strip():
            continue
        m = re.fullmatch(r"\s*([a-z]{3})[a-z]*\s+(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*", entry.lower())
        if not m or m.group(1) not in DAY_NAMES:
            raise ValueError(f"CHORE_WINDOWS entry '{entry.strip()}' should look like 'sat 08:00-18:00'")
        start, end = int(m.group(2)) * 60 + int(m.group(3)), int(m.group(4)) * 60 + int(m.group(5))
        if not 0 <= start < end <= 24 * 60:
            raise ValueError(f"CHORE_WINDOWS entry '{entry.strip()}' has an empty or invalid time range")
        windows[DAY_NAMES.index(m.group(1))] = union(windows.get(DAY_NAMES.index(m.group(1)), []), [(start, end)])
    return windows


def parse_window(text: str) -> tuple[int, int]:
    """'08:00-18:00' → (480, 1080)"""
    return parse_windows(f"mon {text}")[0][0]


def free_slots(people: list[str], days: list[date], default: Windows, events: list[CalEvent],
               buffer_minutes: int, extra_day_window: tuple[int, int]) -> dict[tuple[str, date], Slots]:
    """Free chore time per (person, day)."""
    free = {(p, d): list(default.get(d.weekday(), [])) for p in people for d in days}
    pad = timedelta(minutes=buffer_minutes)

    def each_day(e: CalEvent):
        for d in days:
            if (span := clip(e.start, e.end, d)) is not None:
                yield d, span

    # 1. extra availability
    for e in events:
        if e.kind in ("extra_day", "extra_time"):
            for d, span in each_day(e):
                add = [extra_day_window] if e.kind == "extra_day" else [span]
                for p in e.people:
                    if (p, d) in free:
                        free[(p, d)] = union(free[(p, d)], add)
    # 2. whole days off (travel, "No chores")
    for e in events:
        if e.kind in ("away", "no_chores"):
            for d, _ in each_day(e):
                for p in e.people:
                    if (p, d) in free:
                        free[(p, d)] = []
    # 3. busy time, with the buffer on both sides
    for e in events:
        if e.kind == "busy":
            start, end = e.start - pad, e.end + pad
            for d in days:
                if (span := clip(start, end, d)) is not None:
                    for p in e.people:
                        if (p, d) in free:
                            free[(p, d)] = subtract(free[(p, d)], span)
    return free


def first_available_day(people: list[str], after: date, default: Windows, events: list[CalEvent],
                        buffer_minutes: int, extra_day_window: tuple[int, int], horizon_days: int) -> date | None:
    """The first day after `after` (within the horizon) when anyone has free chore time."""
    days = [after + timedelta(days=i) for i in range(1, horizon_days + 1)]
    free = free_slots(people, days, default, events, buffer_minutes, extra_day_window)
    return next((d for d in days if any(free[(p, d)] for p in people)), None)


def first_default_day(after: date, default: Windows) -> date | None:
    """The first day after `after` that has default chore windows (ignoring the calendars)."""
    return next((after + timedelta(days=i) for i in range(1, 8) if (after + timedelta(days=i)).weekday() in default),
                None)


# --- slot arithmetic ----------------------------------------------------------

def clip(start: datetime, end: datetime, day: date) -> tuple[int, int] | None:
    """The part of [start, end) that falls on `day`, in minutes from midnight."""
    day_start = datetime.combine(day, time(0))
    lo, hi = max(start, day_start), min(end, day_start + timedelta(days=1))
    if lo >= hi:
        return None
    return int((lo - day_start).total_seconds() // 60), int((hi - day_start).total_seconds() // 60)


def union(slots: Slots, more: Slots) -> Slots:
    merged: Slots = []
    for a, b in sorted(slots + more):
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def subtract(slots: Slots, span: tuple[int, int]) -> Slots:
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


def intersect(x: Slots, y: Slots) -> Slots:
    out = []
    for a, b in x:
        for c, d in y:
            lo, hi = max(a, c), min(b, d)
            if lo < hi:
                out.append((lo, hi))
    return sorted(out)
