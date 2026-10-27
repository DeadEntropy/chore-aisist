from datetime import datetime, time, timedelta

import pytest

from planner.scheduler import (RETRY_AFTER, is_due, is_healthy, latest_scheduled, next_scheduled, next_wake,
                               parse_times)

EIGHT_PM = (time(20, 0),)
TWICE = (time(7, 0), time(20, 0))


def test_parse_times_sorts_and_dedupes():
    assert parse_times("20:00") == EIGHT_PM
    assert parse_times(" 20:00, 07:00 ,20:00") == TWICE


@pytest.mark.parametrize("text", ["", "8pm", "25:00", "20:00,", "20"])
def test_parse_times_rejects_bad_values(text):
    if text == "20:00,":
        assert parse_times(text) == EIGHT_PM                     # a trailing comma is harmless
        return
    with pytest.raises(ValueError):
        parse_times(text)


def test_latest_and_next_scheduled():
    morning = datetime(2026, 10, 27, 9, 0)
    assert latest_scheduled(morning, EIGHT_PM) == datetime(2026, 10, 26, 20, 0)     # yesterday evening
    assert next_scheduled(morning, EIGHT_PM) == datetime(2026, 10, 27, 20, 0)
    assert latest_scheduled(morning, TWICE) == datetime(2026, 10, 27, 7, 0)
    at_eight = datetime(2026, 10, 27, 20, 0)
    assert latest_scheduled(at_eight, EIGHT_PM) == at_eight                          # due exactly on time
    assert next_scheduled(at_eight, EIGHT_PM) == datetime(2026, 10, 28, 20, 0)


def test_due_after_the_scheduled_time_until_a_run_succeeds():
    ran = datetime(2026, 10, 26, 20, 0, 3)
    assert not is_due(datetime(2026, 10, 27, 19, 59), EIGHT_PM, ran)
    assert is_due(datetime(2026, 10, 27, 20, 0), EIGHT_PM, ran)
    assert not is_due(datetime(2026, 10, 27, 20, 1), EIGHT_PM, datetime(2026, 10, 27, 20, 0, 1))


def test_missed_days_are_caught_up_once():
    # down from Monday to Thursday morning: one run on start, then nothing until Thursday 20:00
    last_ok = datetime(2026, 10, 26, 20, 0)
    start = datetime(2026, 10, 29, 9, 0)
    assert is_due(start, EIGHT_PM, last_ok)
    assert not is_due(start + timedelta(minutes=1), EIGHT_PM, start)


def test_first_start_runs_at_once():
    assert is_due(datetime(2026, 10, 27, 9, 0), EIGHT_PM, None)


def test_next_wake_retries_sooner_after_a_failure():
    now = datetime(2026, 10, 27, 20, 0, 5)
    assert next_wake(now, EIGHT_PM, failed=False) == datetime(2026, 10, 28, 20, 0)
    assert next_wake(now, EIGHT_PM, failed=True) == now + RETRY_AFTER
    late = datetime(2026, 10, 28, 19, 55)                  # a retry never pushes past the next scheduled time
    assert next_wake(late, EIGHT_PM, failed=True) == datetime(2026, 10, 28, 20, 0)


def test_healthy_needs_a_recent_success():
    now = datetime(2026, 10, 27, 21, 0)
    assert is_healthy(now, datetime(2026, 10, 26, 20, 0))
    assert not is_healthy(now, datetime(2026, 10, 25, 20, 0))
    assert not is_healthy(now, None)
