"""The saved plan: what's fixed, what gets re-planned, and what the Chores calendar should show. Pure logic.

Rules (each run plans from `now`: the evening run, and a re-plan right after every done/skip report):
- Items that started before `now` are **frozen**: never moved or deleted.
  - A frozen item still `open` whose end time has passed becomes **unknown** (❔): nobody reported it.
  - A frozen item still `open` that hasn't ended yet (in progress) stays open, and its chore isn't planned again.
- Items starting from `now` on that are still `open` are **re-planned**: kept if unchanged, moved if their time
  changed, removed if the chore dropped out of the plan, and new ones are added.
- `done` (✅) and `skipped` (⏭) items are never touched: they stay as history.
The Chores calendar mirrors the `planned` table: `calendar_diff` makes the calendar match the records.
"""

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import icalendar

from planner.models import PlannedItem, PlannedRecord

UID_DOMAIN = "chore-aisist"                     # every event the planner writes has a UID ending in @chore-aisist
STATUS_MARK = {"open": "🧹", "done": "✅", "skipped": "⏭", "unknown": "❔"}


def event_uid(chore_id: str, day: date) -> str:
    return f"chore-{chore_id}-{day.isoformat()}@{UID_DOMAIN}"


def is_ours(uid: str) -> bool:
    return uid.endswith(f"@{UID_DOMAIN}")


def event_title(chore_name: str, people: list[str], status: str) -> str:
    who = " & ".join(p.capitalize() for p in people)
    return f"{STATUS_MARK[status]} {chore_name} ({who})"


def freeze(records: list[PlannedRecord], now: datetime) -> tuple[list[PlannedRecord], set[str]]:
    """(records to mark unknown, chores with an item in progress at `now`)."""
    frozen_open = [r for r in records if r.start < now and r.status == "open"]
    to_unknown = [r for r in frozen_open if r.end <= now]
    pending = {r.chore_id for r in frozen_open if r.end > now}
    return to_unknown, pending


@dataclass
class Changes:
    add: list[PlannedRecord] = field(default_factory=list)
    move: list[PlannedRecord] = field(default_factory=list)      # same UID, new time or people
    remove: list[PlannedRecord] = field(default_factory=list)
    dropped: list[PlannedItem] = field(default_factory=list)     # planned, but its UID is already history

    def __bool__(self) -> bool:
        return bool(self.add or self.move or self.remove)


def reconcile(records: list[PlannedRecord], items: list[PlannedItem], now: datetime) -> Changes:
    """How the saved plan from `now` on must change to match the new plan `items`."""
    by_uid = {r.uid: r for r in records}
    changes = Changes()
    wanted = set()
    for item in items:
        uid = event_uid(item.chore.id, item.start.date())
        new = PlannedRecord(item.chore.id, list(item.people), item.start, item.end, uid)
        old = by_uid.get(uid)
        if old is None:
            changes.add.append(new)
        elif old.status != "open":
            changes.dropped.append(item)          # e.g. skipped earlier that same day: leave the history alone
            continue
        elif (old.start, old.end, old.people) != (new.start, new.end, new.people):
            changes.move.append(replace(new, id=old.id))
        wanted.add(uid)
    changes.remove = [r for r in records if r.start >= now and r.status == "open" and r.uid not in wanted]
    return changes


def find_record(records: list[PlannedRecord], chore_id: str, day: date) -> PlannedRecord | None:
    """The open or unknown item of `chore_id` closest to `day` (what 'I did X' most likely refers to)."""
    candidates = [r for r in records if r.chore_id == chore_id and r.status in ("open", "unknown")]
    if not candidates:
        return None
    return min(candidates, key=lambda r: (abs((r.start.date() - day).days), r.start))


# --- the Chores calendar ----------------------------------------------------------

@dataclass(frozen=True)
class EventData:
    uid: str
    title: str
    start: datetime
    end: datetime
    reminder: int = 0                    # minutes before the start (0 = no reminder)


def desired_events(records: list[PlannedRecord], names: dict[str, str], reminder: int) -> dict[str, EventData]:
    return {r.uid: EventData(r.uid, event_title(names.get(r.chore_id, r.chore_id), r.people, r.status),
                             r.start, r.end, reminder)
            for r in records}


def calendar_diff(desired: dict[str, EventData], existing: dict[str, EventData]) -> tuple[list[EventData], list[str]]:
    """(events to create or update, UIDs to delete) so the calendar shows exactly `desired`.
    Only the planner's own events (UID @chore-aisist) are ever deleted."""
    put = [e for uid, e in desired.items() if existing.get(uid) != e]
    delete = [uid for uid in existing if uid not in desired and is_ours(uid)]
    return put, delete


def build_ics(event: EventData, tz: ZoneInfo, now: datetime | None = None) -> str:
    """The iCalendar text for one chore event, with its reminder (if any)."""
    cal = icalendar.Calendar()
    cal.add("prodid", "-//chore-aisist//EN")
    cal.add("version", "2.0")
    ev = icalendar.Event()
    ev.add("uid", event.uid)
    ev.add("dtstamp", (now or datetime.now(tz)).astimezone(ZoneInfo("UTC")))
    ev.add("dtstart", event.start.replace(tzinfo=tz))
    ev.add("dtend", event.end.replace(tzinfo=tz))
    ev.add("summary", event.title)
    ev.add("transp", "OPAQUE")
    if event.reminder > 0:
        alarm = icalendar.Alarm()
        alarm.add("action", "DISPLAY")
        alarm.add("description", event.title)
        alarm.add("trigger", timedelta(minutes=-event.reminder))
        ev.add_component(alarm)
    cal.add_component(ev)
    cal.add_missing_timezones()
    return cal.to_ical().decode()


def event_from_component(component, tz: ZoneInfo) -> EventData:
    """Read back an event from the Chores calendar, in local time, for comparison with `desired_events`."""
    def local(value):
        if not isinstance(value, datetime):
            return datetime.combine(value, datetime.min.time())
        return value.astimezone(tz).replace(tzinfo=None) if value.tzinfo else value
    end = component.get("dtend")
    start = local(component.get("dtstart").dt)
    reminder = 0
    for alarm in component.walk("VALARM"):
        trigger = alarm.get("trigger")
        if trigger is not None and isinstance(trigger.dt, timedelta):
            reminder = round(-trigger.dt.total_seconds() / 60)
    return EventData(str(component.get("uid")), str(component.get("summary", "")), start,
                     local(end.dt) if end else start, reminder)
