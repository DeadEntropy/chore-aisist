"""Everything CalDAV: read the family calendars and classify their events; read and write the Chores calendar.

`fetch_events` is the only function that talks to Nextcloud; the rest is pure and tested without a network.
"""

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from planner.config import CalendarSettings
from planner.models import CalEvent


def _client(settings: CalendarSettings):
    import caldav    # imported here so the rest of the planner works without it being reachable

    return caldav.DAVClient(url=settings.url, username=settings.user, password=settings.password,
                            ssl_verify_cert=settings.ca_cert, timeout=settings.timeout_seconds)


def list_calendars(settings: CalendarSettings) -> list[tuple[str, str]]:
    """(URI, display name) of every calendar the app user can read: its own and those shared with it."""
    found = []
    for cal in _client(settings).principal().calendars():
        uri = str(cal.url).rstrip("/").rsplit("/", 1)[-1]
        found.append((uri, cal.get_display_name() or uri))
    return sorted(found)


def _chores_calendar(settings: CalendarSettings):
    return _client(settings).calendar(url=f"{settings.url}/calendars/{settings.user}/{settings.chores_calendar}/")


def chore_events(settings: CalendarSettings, start: date, end: date) -> dict:
    """The planner's events in the Chores calendar between `start` and `end`, keyed by UID."""
    from planner.plans import event_from_component, is_ours

    tz = ZoneInfo(settings.timezone)
    found = _chores_calendar(settings).search(event=True, start=datetime.combine(start, time(0), tzinfo=tz),
                                              end=datetime.combine(end, time(0), tzinfo=tz))
    events = {}
    for item in found:
        event = event_from_component(item.icalendar_component, tz)
        if is_ours(event.uid):
            events[event.uid] = event
    return events


def write_chore_events(settings: CalendarSettings, put: list, delete: list[str]) -> None:
    """Create or update `put` (EventData) and delete the `delete` UIDs in the Chores calendar."""
    from planner.plans import build_ics, is_ours

    tz = ZoneInfo(settings.timezone)
    calendar = _chores_calendar(settings)
    for event in put:
        calendar.add_event(ical=build_ics(event, tz, settings.reminder_minutes), increase_seqno=False)
    for uid in delete:
        if is_ours(uid):                                   # never delete anything the planner didn't create
            calendar.event_by_uid(uid).delete()


def fetch_events(settings: CalendarSettings, start: date, end: date) -> list[CalEvent]:
    """All events on the configured calendars between `start` and `end` (exclusive), recurring ones expanded."""
    client = _client(settings)
    tz = ZoneInfo(settings.timezone)
    events: list[CalEvent] = []
    for uri, people in settings.calendars:
        calendar = client.calendar(url=f"{settings.url}/calendars/{settings.user}/{uri}/")
        found = calendar.search(event=True, start=datetime.combine(start, time(0), tzinfo=tz),
                                end=datetime.combine(end, time(0), tzinfo=tz), expand=True)
        for item in found:
            event = parse_event(item.icalendar_component, list(people), tz, settings.keywords)
            if event is not None:
                events.append(event)
    return events


def parse_event(component, people: list[str], tz: ZoneInfo, keywords: dict[str, tuple[str, ...]]) -> CalEvent | None:
    """One VEVENT → CalEvent in local time, or None if it doesn't matter for chore time.

    `keywords` maps 'away' / 'no_chores' / 'extra_day' / 'extra_time' to title keywords.
    Ignored: cancelled events, events marked free (TRANSP:TRANSPARENT), and all-day events without a keyword.
    """
    if str(component.get("status", "")).upper() == "CANCELLED":
        return None
    if str(component.get("transp", "")).upper() == "TRANSPARENT":
        return None
    summary = str(component.get("summary", "") or "")
    start = component.get("dtstart").dt
    end_prop = component.get("dtend")
    duration = component.get("duration")

    if not isinstance(start, datetime):                       # all-day
        end = end_prop.dt if end_prop else start + (duration.dt if duration else timedelta(days=1))
        kind = classify(summary, all_day=True, keywords=keywords)
        if kind is None:
            return None
        return CalEvent(people, summary, datetime.combine(start, time(0)), datetime.combine(end, time(0)), kind)

    end = end_prop.dt if end_prop else start + (duration.dt if duration else timedelta(0))
    return CalEvent(people, summary, _local(start, tz), _local(end, tz),
                    classify(summary, all_day=False, keywords=keywords))


def classify(summary: str, all_day: bool, keywords: dict[str, tuple[str, ...]]) -> str | None:
    """What an event means for chore time. First match wins, in this order:

    all-day: no_chores ("No chores") → away (travel/trip/away) → extra_day ("Day off", "Holiday", "Chores") → ignored
    timed:   no_chores → busy;  extra_time ("Chores") → available;  anything else → busy
    """
    def has(kind: str) -> bool:
        return matches(summary, keywords.get(kind, ()))

    if all_day:
        if has("no_chores"):
            return "no_chores"
        if has("away"):
            return "away"
        if has("extra_day") or has("extra_time"):
            return "extra_day"
        return None
    if not has("no_chores") and has("extra_time"):
        return "extra_time"
    return "busy"


def matches(summary: str, keywords: tuple[str, ...]) -> bool:
    """True if the title contains a keyword at the start of a word: 'Trip to Paris', 'Travelling', 'Day off'
    (not 'Giveaway'). Multi-word keywords match as a phrase."""
    return any(re.search(rf"\b{re.escape(k)}", summary, re.IGNORECASE) for k in keywords)


def _local(value: datetime, tz: ZoneInfo) -> datetime:
    """Aware datetimes → naive local time; floating (naive) ones are already local."""
    return value.astimezone(tz).replace(tzinfo=None) if value.tzinfo else value
