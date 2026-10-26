"""Reads the real calendars (read-only). Skipped unless CALDAV_LIVE_TEST=1 and CalDAV settings are present."""

import os
from datetime import timedelta

import pytest

from planner.calendars import fetch_events
from planner.config import load_settings
from planner.jobs import next_saturday
from planner.models import CalEvent

pytestmark = pytest.mark.skipif(os.environ.get("CALDAV_LIVE_TEST") != "1", reason="live CalDAV test not enabled")


def test_fetch_next_two_weekends():
    settings = load_settings().calendar
    assert settings is not None, "CALDAV_URL etc. not configured"
    start = next_saturday(__import__("datetime").date.today())
    events = fetch_events(settings, start, start + timedelta(weeks=2))
    assert all(isinstance(e, CalEvent) and e.end >= e.start for e in events)
