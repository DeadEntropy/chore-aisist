from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import icalendar
import pytest

from planner.calendars import classify, matches, parse_event
from planner.config import KEYWORD_SETTINGS, parse_busy_calendars

TZ = ZoneInfo("America/New_York")
KEYWORDS = {kind: tuple(default.split(",")) for kind, (_, default) in KEYWORD_SETTINGS.items()}


def vevent(**props) -> icalendar.Event:
    """Build a VEVENT the way the CalDAV client hands it over."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "BEGIN:VEVENT", "UID:x"]
    lines += [f"{k.replace('_', '-').upper()}{v}" for k, v in props.items()]
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return icalendar.Calendar.from_ical("\r\n".join(lines)).walk("VEVENT")[0]


def test_timed_event_in_new_york_time():
    e = parse_event(vevent(summary=":Tennis", dtstart=";TZID=America/New_York:20261031T073000",
                           dtend=";TZID=America/New_York:20261031T090000"), ["anna"], TZ, KEYWORDS)
    assert (e.start, e.end, e.kind) == (datetime(2026, 10, 31, 7, 30), datetime(2026, 10, 31, 9, 0), "busy")


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


def test_all_day_events_ignored_unless_a_keyword():
    birthday = vevent(summary=":Lucy's birthday", dtstart=";VALUE=DATE:20261031", dtend=";VALUE=DATE:20261101")
    assert parse_event(birthday, ["nicolas", "anna"], TZ, KEYWORDS) is None
    trip = vevent(summary=":Trip to Paris", dtstart=";VALUE=DATE:20261106", dtend=";VALUE=DATE:20261110")
    e = parse_event(trip, ["nicolas"], TZ, KEYWORDS)
    assert e.kind == "away" and e.start == datetime(2026, 11, 6) and e.end == datetime(2026, 11, 10)


@pytest.mark.parametrize("title, all_day, kind", [
    ("Travel", True, "away"), ("Trip to Paris", True, "away"), ("Away – conference", True, "away"),
    ("Travelling to NY", True, "away"), ("Road-trip", True, "away"), ("Giveaway party", True, None),
    ("Day off", True, "extra_day"), ("Thanksgiving holiday", True, "extra_day"), ("Chores day", True, "extra_day"),
    ("No chores", True, "no_chores"), ("Birthday", True, None),
    ("Chores", False, "extra_time"), ("Chore time", False, "extra_time"), ("No chores", False, "busy"),
    ("Holiday party", False, "busy"), ("Tennis", False, "busy"),
])
def test_classify(title, all_day, kind):
    assert classify(title, all_day, KEYWORDS) == kind


def test_keyword_matches_start_of_word():
    assert matches("Business TRIP", ("trip",)) and not matches("Giveaway", ("away",))


def test_parse_busy_calendars():
    assert parse_busy_calendars(" personal=nicolas ; family=nicolas, anna ;") == (
        ("personal", ("nicolas",)), ("family", ("nicolas", "anna")))
    with pytest.raises(ValueError):
        parse_busy_calendars("family")
    with pytest.raises(ValueError):
        parse_busy_calendars("")


def test_fetch_events_uses_timeout_and_local_time_bounds(monkeypatch):
    import sys
    import types

    from planner.calendars import fetch_events
    from planner.config import CalendarSettings

    seen = {}

    class FakeCalendar:
        def search(self, **kw):
            seen["search"] = kw
            return []

    class FakeClient:
        def __init__(self, **kw):
            seen["client"] = kw

        def calendar(self, url):
            return FakeCalendar()

    monkeypatch.setitem(sys.modules, "caldav", types.SimpleNamespace(DAVClient=FakeClient))
    settings = CalendarSettings(url="https://nc/dav", user="app", password="p", ca_cert=True,
                                calendars=(("family", ("nicolas",)),), buffer_minutes=30,
                                keywords=KEYWORDS, timezone="America/New_York", timeout_seconds=12)
    fetch_events(settings, date(2026, 10, 31), date(2026, 11, 2))
    assert seen["client"]["timeout"] == 12
    assert seen["search"]["start"] == datetime(2026, 10, 31, tzinfo=TZ)
    assert seen["search"]["start"].utcoffset() == timedelta(hours=-4)        # EDT, not the machine's zone
