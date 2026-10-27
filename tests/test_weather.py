from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from planner import weather
from planner.config import WeatherSettings
from planner.models import HourWeather

SUN = date(2026, 11, 1)
SETTINGS = WeatherSettings(latitude=0.0, longitude=0.0, timezone="America/New_York")


def hours(day, start_hour, values, feels=30.0, uv=3.0):
    """A forecast with one hour per value of rain %, from `start_hour` on `day`."""
    first = datetime.combine(day, datetime.min.time()) + timedelta(hours=start_hour)
    return {first + timedelta(hours=i): HourWeather(rain, feels, uv) for i, rain in enumerate(values)}


def minutes(hhmm):
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def test_rain_is_averaged_over_the_chore():
    forecast = hours(SUN, 8, [40, 0, 0])                    # 40% the first hour, then dry: 13% over 3 hours
    assert weather.check(forecast, SETTINGS)(SUN, minutes("08:00"), minutes("11:00")) is None


def test_rain_over_the_limit_rules_the_time_out():
    forecast = hours(SUN, 8, [75] * 10)                     # a wet Sunday
    assert weather.check(forecast, SETTINGS)(SUN, minutes("08:00"), minutes("12:00")) == "rain 75%"


def test_rain_at_the_limit_is_fine():
    forecast = hours(SUN, 8, [20, 20])
    assert weather.check(forecast, SETTINGS)(SUN, minutes("08:00"), minutes("10:00")) is None


def test_partial_hours_count_for_the_minutes_they_cover():
    forecast = hours(SUN, 8, [0, 60])                       # 08:15-09:15: 45 min at 0%, 15 min at 60% → 15%
    rule = weather.check(forecast, SETTINGS)
    assert rule(SUN, minutes("08:15"), minutes("09:15")) is None
    assert rule(SUN, minutes("08:30"), minutes("09:30")) == "rain 30%"


def test_one_hot_or_sunny_hour_is_enough():
    forecast = hours(SUN, 12, [0, 0, 0])
    forecast[datetime(2026, 11, 1, 14)] = HourWeather(0, 38.0, 5)
    forecast[datetime(2026, 11, 1, 13)] = HourWeather(0, 30.0, 9)
    rule = weather.check(forecast, SETTINGS)
    assert rule(SUN, minutes("14:00"), minutes("15:00")) == "feels like 38°C"
    assert rule(SUN, minutes("13:00"), minutes("14:00")) == "UV 9"
    assert rule(SUN, minutes("12:00"), minutes("13:00")) is None


def test_hours_without_a_forecast_are_fine():
    rule = weather.check(hours(SUN, 8, [75]), SETTINGS)
    assert rule(SUN + timedelta(days=1), minutes("08:00"), minutes("12:00")) is None      # past the horizon
    forecast = hours(SUN, 8, [10])
    forecast[datetime(2026, 11, 1, 9)] = HourWeather(None, None, None)
    assert weather.check(forecast, SETTINGS)(SUN, minutes("08:00"), minutes("10:00")) is None


def test_limits_come_from_the_settings():
    strict = WeatherSettings(0.0, 0.0, "UTC", max_rain_percent=5, max_feels_like_c=40, max_uv_index=11)
    forecast = hours(SUN, 8, [10], feels=38.0, uv=10)
    assert weather.check(forecast, strict)(SUN, minutes("08:00"), minutes("09:00")) == "rain 10%"


def answer(first: date, days: int):
    """Open-Meteo's answer (Unix times) for `days` days from UTC midnight on `first`."""
    start = datetime.combine(first, datetime.min.time(), timezone.utc)
    times = [start + timedelta(hours=h) for h in range(24 * days)]
    return {"hourly": {"time": [int(t.timestamp()) for t in times],
                       "precipitation_probability": [h % 24 for h in range(len(times))],
                       "apparent_temperature": [30.5] * len(times),
                       "uv_index": [None] * len(times)}}


def test_parse_keeps_today_and_the_next_days_only():
    today = date(2026, 10, 29)
    forecast = weather.parse_forecast(answer(today, 7), today, 3, "UTC")
    assert min(forecast).date() == today and max(forecast).date() == today + timedelta(days=3)
    assert forecast[datetime(2026, 11, 1, 14)] == HourWeather(14, 30.5, None)


def test_parse_rejects_a_broken_answer():
    data = answer(SUN, 1)
    data["hourly"]["uv_index"] = [1.0]
    with pytest.raises(ValueError):
        weather.parse_forecast(data, SUN, 3, "UTC")


def test_missing_columns_are_treated_as_no_forecast():
    data = answer(SUN, 1)
    del data["hourly"]["uv_index"]
    assert weather.parse_forecast(data, SUN, 0, "UTC")[datetime(2026, 11, 1, 9)].uv_index is None


def test_day_summary():
    forecast = hours(SUN, 8, [12, 26, 53], feels=31.6, uv=5.8)
    assert weather.day_summary(forecast, SUN, minutes("08:00"), minutes("18:00")) == \
        "rain 12-53%, feels like up to 32°C, UV up to 6"
    assert weather.day_summary(forecast, SUN + timedelta(days=1), 0, 24 * 60) is None


def test_each_value_comes_from_the_first_model_that_has_it():
    times = [int(datetime(2026, 11, 1, h, tzinfo=timezone.utc).timestamp()) for h in (8, 9)]
    data = {"hourly": {"time": times,
                       "precipitation_probability_ecmwf_ifs025": [33, None],
                       "apparent_temperature_ecmwf_ifs025": [26.3, 27.9],
                       "uv_index_ecmwf_ifs025": [None, None],                 # ECMWF has no UV index
                       "precipitation_probability_best_match": [12, 26],
                       "apparent_temperature_best_match": [28.5, 29.0],
                       "uv_index_best_match": [0.1, 1.1]}}
    forecast = weather.parse_forecast(data, SUN, 0, "UTC", ("ecmwf_ifs025", "best_match"))
    assert forecast[datetime(2026, 11, 1, 8)] == HourWeather(33, 26.3, 0.1)
    assert forecast[datetime(2026, 11, 1, 9)] == HourWeather(26, 27.9, 1.1)


def test_hours_are_local_time_across_the_clock_change():
    """Clocks go back at 02:00 on Sun 1 Nov 2026 in New York: 09:00 local is 13:00 UTC on Saturday, 14:00 UTC on
    Sunday. (Open-Meteo's ISO labels would keep Saturday's offset and call Sunday's 09:00 "10:00".)"""
    ny = ZoneInfo("America/New_York")
    local = [datetime(2026, 10, 31, 9, tzinfo=ny), datetime(2026, 11, 1, 9, tzinfo=ny)]
    data = {"hourly": {"time": [int(t.timestamp()) for t in local], "precipitation_probability": [10, 75],
                       "apparent_temperature": [30, 30], "uv_index": [1, 1]}}
    forecast = weather.parse_forecast(data, date(2026, 10, 31), 3, "America/New_York")
    assert sorted(forecast) == [datetime(2026, 10, 31, 9), datetime(2026, 11, 1, 9)]
    assert forecast[datetime(2026, 11, 1, 9)].rain_percent == 75
