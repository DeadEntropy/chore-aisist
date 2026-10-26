"""Reads the real calendars (read-only). Skipped unless CALDAV_LIVE_TEST=1 and CalDAV settings are present."""

import os
from datetime import date, timedelta

import pytest

from planner.calendars import fetch_events
from planner.config import load_calendar_settings
from planner.models import CalEvent

pytestmark = pytest.mark.skipif(os.environ.get("CALDAV_LIVE_TEST") != "1", reason="live CalDAV test not enabled")


def test_fetch_next_two_weeks():
    settings = load_calendar_settings()
    assert settings is not None, "CALDAV_URL etc. not configured"
    start = date.today()
    events = fetch_events(settings, start, start + timedelta(weeks=2))
    assert all(isinstance(e, CalEvent) and e.end >= e.start for e in events)


def test_write_read_delete_one_far_future_event():
    """Writes ONE event on 5 Jan 2030 in the Chores calendar, reads it back, then deletes it."""
    from datetime import datetime

    from planner.calendars import chore_events, write_chore_events
    from planner.plans import EventData

    settings = load_calendar_settings()
    day = date(2030, 1, 5)
    event = EventData("chore-live-test-2030-01-05@chore-aisist", "🧹 Live test (Nicolas)",
                      datetime(2030, 1, 5, 9), datetime(2030, 1, 5, 10))
    try:
        write_chore_events(settings, [event], [])
        assert chore_events(settings, day, day + timedelta(days=1)).get(event.uid) == event
    finally:
        write_chore_events(settings, [], [event.uid])
    assert event.uid not in chore_events(settings, day, day + timedelta(days=1))
