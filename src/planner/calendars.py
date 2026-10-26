"""Everything CalDAV: read the family calendars and turn them into busy time. (Writing the Chores calendar: phase 3.)

`fetch_events` is the only function that talks to Nextcloud; the rest is pure and tested without a network.
"""

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from planner.config import CalendarSettings
from planner.models import Busy, CalEvent


def fetch_events(settings: CalendarSettings, start: date, end: date) -> list[CalEvent]:
    """All events on the configured calendars between `start` and `end` (exclusive), recurring ones expanded."""
    import caldav    # imported here so the rest of the planner works without it being reachable

    client = caldav.DAVClient(url=settings.url, username=settings.user, password=settings.password,
                              ssl_verify_cert=settings.ca_cert, timeout=settings.timeout_seconds)
    tz = ZoneInfo(settings.timezone)
    events: list[CalEvent] = []
    for uri, people in settings.calendars:
        calendar = client.calendar(url=f"{settings.url}/calendars/{settings.user}/{uri}/")
        found = calendar.search(event=True, start=datetime.combine(start, time(0), tzinfo=tz),
                                end=datetime.combine(end, time(0), tzinfo=tz), expand=True)
        for item in found:
            event = parse_event(item.icalendar_component, list(people), tz, settings.travel_keywords)
            if event is not None:
                events.append(event)
    return events


def parse_event(component, people: list[str], tz: ZoneInfo, travel_keywords: tuple[str, ...]) -> CalEvent | None:
    """One VEVENT → CalEvent in local time, or None if it shouldn't block anyone.

    Ignored: cancelled events, events marked free (TRANSP:TRANSPARENT), and all-day events that aren't travel.
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
        if not is_travel(summary, travel_keywords):
            return None
        return CalEvent(people, summary, datetime.combine(start, time(0)), datetime.combine(end, time(0)),
                        all_day=True, travel=True)

    end = end_prop.dt if end_prop else start + (duration.dt if duration else timedelta(0))
    return CalEvent(people, summary, _local(start, tz), _local(end, tz), all_day=False, travel=False)


def is_travel(summary: str, keywords: tuple[str, ...]) -> bool:
    """True if a word in the title starts with a keyword: 'Trip to Paris', 'Away', 'Travelling' (not 'Giveaway')."""
    return any(re.search(rf"\b{re.escape(k)}", summary, re.IGNORECASE) for k in keywords)


def busy_from_events(events: list[CalEvent], buffer_minutes: int) -> list[Busy]:
    """Busy spans per person: travel blocks whole days; timed events get the buffer on both sides."""
    pad = timedelta(minutes=buffer_minutes)
    busy = []
    for e in events:
        start, end = (e.start, e.end) if e.travel else (e.start - pad, e.end + pad)
        busy.extend(Busy(p, start, end) for p in e.people)
    return busy


def away_days(events: list[CalEvent]) -> set[tuple[str, date]]:
    """(person, day) pairs covered by a travel event."""
    out = set()
    for e in events:
        if e.travel:
            day = e.start.date()
            while datetime.combine(day, time(0)) < e.end:
                out.update((p, day) for p in e.people)
                day += timedelta(days=1)
    return out


def _local(value: datetime, tz: ZoneInfo) -> datetime:
    """Aware datetimes → naive local time; floating (naive) ones are already local."""
    return value.astimezone(tz).replace(tzinfo=None) if value.tzinfo else value
