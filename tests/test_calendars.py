from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import icalendar
import pytest

from planner.calendars import away_days, busy_from_events, is_travel, parse_event
from planner.config import parse_busy_calendars
from planner.models import CalEvent

TZ = ZoneInfo("America/New_York")
KEYWORDS = ("travel", "trip", "away")


def vevent(**props) -> icalendar.Event:
    """Build a VEVENT the way the CalDAV client hands it over."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "BEGIN:VEVENT", "UID:x"]
    lines += [f"{k.replace('_', '-').upper()}{v}" for k, v in props.items()]
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return icalendar.Calendar.from_ical("\r\n".join(lines)).walk("VEVENT")[0]


def test_timed_event_in_new_york_time():
    e = parse_event(vevent(summary=":Tennis", dtstart=";TZID=America/New_York:20261031T073000",
                           dtend=";TZID=America/New_York:20261031T090000"), ["anna"], TZ, KEYWORDS)
    assert (e.start, e.end, e.travel) == (datetime(2026, 10, 31, 7, 30), datetime(2026, 10, 31, 9, 0), False)


def test_utc_event_is_converted_to_local():
    e = parse_event(vevent(summary=":Call", dtstart=":20261107T150000Z", dtend=":20261107T160000Z"),
                    ["nicolas"], TZ, KEYWORDS)
    assert e.start == datetime(2026, 11, 7, 10, 0)          # EST, UTC-5


def test_duration_instead_of_dtend():
    e = parse_event(vevent(summary=":Swim", dtstart=":20261031T100000", duration=":PT1H30M"), ["anna"], TZ, KEYWORDS)
    assert e.end == datetime(2026, 10, 31, 11, 30)


@pytest.mark.parametrize("props", [
    {"status": ":CANCELLED"},
    {"transp": ":TRANSPARENT"},
])
def test_cancelled_and_free_events_are_ignored(props):
    ev = vevent(summary=":Party", dtstart=":20261031T140000", dtend=":20261031T160000", **props)
    assert parse_event(ev, ["nicolas"], TZ, KEYWORDS) is None


def test_all_day_events_ignored_unless_travel():
    birthday = vevent(summary=":Lucy's birthday", dtstart=";VALUE=DATE:20261031", dtend=";VALUE=DATE:20261101")
    assert parse_event(birthday, ["nicolas", "anna"], TZ, KEYWORDS) is None
    trip = vevent(summary=":Trip to Paris", dtstart=";VALUE=DATE:20261106", dtend=";VALUE=DATE:20261110")
    e = parse_event(trip, ["nicolas"], TZ, KEYWORDS)
    assert e.travel and e.start == datetime(2026, 11, 6) and e.end == datetime(2026, 11, 10)


@pytest.mark.parametrize("title, expected", [
    ("Travel", True), ("Trip to Paris", True), ("Away – conference", True), ("Travelling to NY", True),
    ("Business TRIP", True), ("Road-trip", True), ("Giveaway party", False), ("Birthday", False),
])
def test_travel_keywords(title, expected):
    assert is_travel(title, KEYWORDS) is expected


def test_buffer_on_timed_events_but_not_travel():
    party = CalEvent(["nicolas", "anna"], "Party", datetime(2026, 10, 31, 14), datetime(2026, 10, 31, 16))
    trip = CalEvent(["anna"], "Trip", datetime(2026, 11, 7), datetime(2026, 11, 9), all_day=True, travel=True)
    busy = busy_from_events([party, trip], 30)
    spans = {(b.person_id, b.start, b.end) for b in busy}
    assert ("nicolas", datetime(2026, 10, 31, 13, 30), datetime(2026, 10, 31, 16, 30)) in spans
    assert ("anna", datetime(2026, 10, 31, 13, 30), datetime(2026, 10, 31, 16, 30)) in spans
    assert ("anna", datetime(2026, 11, 7), datetime(2026, 11, 9)) in spans
    assert len(busy) == 3


def test_away_days_cover_each_day_end_exclusive():
    trip = CalEvent(["nicolas"], "Away", datetime(2026, 11, 6), datetime(2026, 11, 9), all_day=True, travel=True)
    assert away_days([trip]) == {("nicolas", date(2026, 11, 6)), ("nicolas", date(2026, 11, 7)),
                                 ("nicolas", date(2026, 11, 8))}


def test_parse_busy_calendars():
    assert parse_busy_calendars(" personal=nicolas ; family=nicolas, anna ;") == (
        ("personal", ("nicolas",)), ("family", ("nicolas", "anna")))
    with pytest.raises(ValueError):
        parse_busy_calendars("family")
    with pytest.raises(ValueError):
        parse_busy_calendars("")
