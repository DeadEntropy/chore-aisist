import pytest

from planner import daemon


def test_serve_refuses_to_start_without_calendars(monkeypatch):
    from planner.availability import parse_window, parse_windows
    from planner.config import PlanningSettings
    monkeypatch.setattr(daemon, "load_plan_times", lambda: ())
    monkeypatch.setattr(daemon, "load_planning_settings",
                        lambda: PlanningSettings(parse_windows("sat 08:00-18:00"), parse_window("08:00-18:00")))
    monkeypatch.setattr(daemon, "load_calendar_settings", lambda: None)
    with pytest.raises(ValueError, match="CALDAV_URL not set"):
        daemon.serve(None)
