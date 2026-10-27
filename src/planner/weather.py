"""The weather forecast, and whether it allows an outdoor chore at a given time.

`fetch_forecast` is the only code that talks to the weather service (Open-Meteo: free, no key). The rest is pure:
`parse_forecast` turns its answer into hours (each value from the first of `WeatherSettings.models` that has it:
weather models disagree, and not every model has every value, e.g. ECMWF has no UV index), `check` says why a time is too wet, too hot or too sunny for an
outdoor chore (or None when it's fine), `day_summary` describes a day for `planner plan --dry-run`.

The forecast is only used for today and the next `WeatherSettings.days` days: further out it's too unreliable, so
those days are planned as if the weather were fine, and the daily re-plan applies the forecast as the day comes near.
"""

import json
from collections.abc import Callable
from datetime import date, datetime, timedelta
from urllib.parse import urlencode
from urllib.request import urlopen

from planner.config import WeatherSettings
from planner.models import HourWeather

API_URL = "https://api.open-meteo.com/v1/forecast"
HOURLY = ("precipitation_probability", "apparent_temperature", "uv_index")

Forecast = dict[datetime, HourWeather]          # by the hour's start, local time
Check = Callable[[date, int, int], str | None]  # (day, start minute, end minute) → why not, or None


def fetch_forecast(settings: WeatherSettings, today: date) -> Forecast:
    """Hourly forecast for today and the next `settings.days` days. Raises on network or format errors."""
    query = urlencode({"latitude": settings.latitude, "longitude": settings.longitude, "hourly": ",".join(HOURLY),
                       "timezone": settings.timezone, "forecast_days": settings.days + 1,
                       "temperature_unit": "celsius", "models": ",".join(settings.models)})
    with urlopen(f"{API_URL}?{query}", timeout=settings.timeout_seconds) as response:
        data = json.load(response)
    return parse_forecast(data, today, settings.days, settings.models)


def parse_forecast(data: dict, today: date, days: int, models: tuple[str, ...] = ()) -> Forecast:
    """Open-Meteo's JSON answer → hours from today to `days` days after (later hours are dropped). With several
    `models`, columns are named e.g. `uv_index_ecmwf_ifs025`; each hour's value comes from the first model that has
    it. With one model (or none), they're plain `uv_index`."""
    hourly = data["hourly"]
    times = hourly["time"]
    columns = [_column(hourly, name, models, len(times)) for name in HOURLY]
    last = today + timedelta(days=days)
    out: Forecast = {}
    for t, rain, feels, uv in zip(times, *columns):
        hour = datetime.fromisoformat(t)
        if today <= hour.date() <= last:
            out[hour] = HourWeather(rain, feels, uv)
    return out


def _column(hourly: dict, name: str, models: tuple[str, ...], length: int) -> list:
    sources = [hourly.get(f"{name}_{m}") for m in models] if len(models) > 1 else [hourly.get(name)]
    sources = [s for s in sources if s is not None]
    if any(len(s) != length for s in sources):
        raise ValueError(f"weather forecast: {name} has a different number of hours than the times")
    return [next((s[i] for s in sources if s[i] is not None), None) for i in range(length)]


def check(forecast: Forecast, settings: WeatherSettings) -> Check:
    """The rule for outdoor chores, for `fitting`: a time is ruled out when the chance of rain, averaged over the
    chore (each hour weighted by the minutes it covers), is over `max_rain_percent`, or when any hour of it is over
    `max_feels_like_c` or `max_uv_index`. Hours without a forecast (e.g. past the horizon) don't count against it."""

    def why_not(day: date, start: int, end: int) -> str | None:
        hours = _hours(forecast, day, start, end)
        rain = [(h.rain_percent, minutes) for h, minutes in hours if h.rain_percent is not None]
        if rain:
            average = sum(p * m for p, m in rain) / sum(m for _, m in rain)
            if average > settings.max_rain_percent:
                return f"rain {average:.0f}%"
        feels = [h.feels_like_c for h, _ in hours if h.feels_like_c is not None]
        if feels and max(feels) > settings.max_feels_like_c:
            return f"feels like {max(feels):.0f}°C"
        uv = [h.uv_index for h, _ in hours if h.uv_index is not None]
        if uv and max(uv) > settings.max_uv_index:
            return f"UV {max(uv):.0f}"
        return None

    return why_not


def day_summary(forecast: Forecast, day: date, start: int, end: int) -> str | None:
    """'rain 12-53%, feels like up to 32°C, UV up to 6' for the hours of `day` between `start` and `end` minutes,
    or None when the forecast doesn't cover them."""
    hours = [h for h, _ in _hours(forecast, day, start, end)]
    if not hours:
        return None
    parts = []
    rain = [h.rain_percent for h in hours if h.rain_percent is not None]
    if rain:
        lo, hi = min(rain), max(rain)
        parts.append(f"rain {lo:.0f}%" if lo == hi else f"rain {lo:.0f}-{hi:.0f}%")
    feels = [h.feels_like_c for h in hours if h.feels_like_c is not None]
    if feels:
        parts.append(f"feels like up to {max(feels):.0f}°C")
    uv = [h.uv_index for h in hours if h.uv_index is not None]
    if uv:
        parts.append(f"UV up to {max(uv):.0f}")
    return ", ".join(parts) or None


def _hours(forecast: Forecast, day: date, start: int, end: int) -> list[tuple[HourWeather, int]]:
    """The forecast hours overlapping `start`-`end` (minutes of `day`), with the minutes each one covers."""
    out = []
    midnight = datetime.combine(day, datetime.min.time())
    for h in range(start // 60, -(-end // 60)):
        covered = min(end, (h + 1) * 60) - max(start, h * 60)
        hour = forecast.get(midnight + timedelta(hours=h))
        if covered > 0 and hour is not None:
            out.append((hour, covered))
    return out
