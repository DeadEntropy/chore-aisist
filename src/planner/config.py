"""Settings, read once from environment variables (a local `.env` is loaded if present)."""

import math
import os
from dataclasses import dataclass
from datetime import time

from dotenv import load_dotenv

from planner.availability import Windows, parse_window, parse_windows
from planner.scheduler import parse_times

KEYWORD_SETTINGS = {                 # event kind → (environment variable, default keywords)
    "away": ("TRAVEL_KEYWORDS", "travel,trip,away"),
    "no_chores": ("NO_CHORES_KEYWORDS", "no chores"),
    "extra_day": ("DAY_OFF_KEYWORDS", "day off,holiday"),
    "extra_time": ("CHORE_TIME_KEYWORDS", "chore"),
}


@dataclass(frozen=True)
class WeatherSettings:
    latitude: float                            # WEATHER_LATITUDE / WEATHER_LONGITUDE: where the chores happen
    longitude: float
    timezone: str                              # forecast hours in local time (PLANNER_TIMEZONE)
    days: int = 3                              # WEATHER_DAYS: forecast used for today and this many days after
    models: tuple[str, ...] = ("ecmwf_ifs025", "best_match")   # WEATHER_MODELS: Open-Meteo models, preferred first
    max_rain_percent: float = 20               # MAX_RAIN_PERCENT: average chance of rain over the chore
    max_feels_like_c: float = 35               # MAX_FEELS_LIKE_C: hottest hour of the chore
    max_uv_index: float = 7                    # MAX_UV_INDEX: sunniest hour of the chore
    timeout_seconds: int = 15


@dataclass(frozen=True)
class PlanningSettings:
    windows: Windows                           # default chore windows per weekday (CHORE_WINDOWS)
    extra_day_window: tuple[int, int]          # chore window on a "Day off" (EXTRA_DAY_WINDOW)
    weather: WeatherSettings | None = None     # None: outdoor chores are planned whatever the weather


@dataclass(frozen=True)
class CalendarSettings:
    url: str                                   # CalDAV base, e.g. https://nextcloud.local/remote.php/dav
    user: str
    password: str                              # Nextcloud app password
    ca_cert: str | bool                        # path to the CA that signed Nextcloud's certificate, or True
    calendars: tuple[tuple[str, tuple[str, ...]], ...]   # (calendar URI, people it blocks)
    buffer_minutes: int                        # added before and after every busy timed event
    keywords: dict[str, tuple[str, ...]]       # event kind → title keywords (see KEYWORD_SETTINGS)
    timezone: str
    timeout_seconds: int = 30                  # per CalDAV request, so a stuck server can't hang the planner
    chores_calendar: str = "chores"            # URI of the calendar the planner writes (owned by the app user)
    reminder_minutes: int = 30                 # phone reminder before each chore event (0 = none)

    def people(self) -> set[str]:
        return {p for _, names in self.calendars for p in names}


@dataclass(frozen=True)
class Settings:
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str


def load_settings() -> Settings:
    load_dotenv()
    password = os.environ.get("DB_PASSWORD")
    if not password:
        raise ValueError("DB_PASSWORD is not set (see .env.example)")
    return Settings(
        db_host=os.environ.get("DB_HOST", "127.0.0.1"),
        db_port=int(os.environ.get("DB_PORT", "3307")),
        db_name=os.environ.get("DB_NAME", "chores"),
        db_user=os.environ.get("DB_USER", "chore_aisist"),
        db_password=password,
    )


def load_planning_settings() -> PlanningSettings:
    load_dotenv()
    return PlanningSettings(
        windows=parse_windows(os.environ.get("CHORE_WINDOWS", "sat 08:00-18:00; sun 08:00-18:00")),
        extra_day_window=parse_window(os.environ.get("EXTRA_DAY_WINDOW", "08:00-18:00")),
        weather=load_weather_settings(),
    )


def load_weather_settings() -> WeatherSettings | None:
    """Weather settings, or None when WEATHER_LATITUDE and WEATHER_LONGITUDE aren't set (no weather)."""
    lat, lon = os.environ.get("WEATHER_LATITUDE", "").strip(), os.environ.get("WEATHER_LONGITUDE", "").strip()
    if not lat and not lon:
        return None
    if not lat or not lon:
        raise ValueError("Set both WEATHER_LATITUDE and WEATHER_LONGITUDE, or neither (no weather)")
    latitude, longitude = float(lat), float(lon)
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ValueError(f"WEATHER_LATITUDE/WEATHER_LONGITUDE out of range: {latitude}, {longitude}")
    days = int(os.environ.get("WEATHER_DAYS", "3"))
    if not 0 <= days <= 15:
        raise ValueError(f"WEATHER_DAYS must be 0 to 15, got {days}")
    limits = {var: float(os.environ.get(var, default)) for var, default in
              (("MAX_RAIN_PERCENT", "20"), ("MAX_FEELS_LIKE_C", "35"), ("MAX_UV_INDEX", "7"))}
    if bad := [f"{var}={value}" for var, value in limits.items() if not math.isfinite(value)]:
        raise ValueError(f"Weather limits must be numbers: {', '.join(bad)}")   # nan would never rule anything out
    return WeatherSettings(
        latitude=latitude,
        longitude=longitude,
        timezone=os.environ.get("PLANNER_TIMEZONE", "America/New_York"),
        days=days,
        models=tuple(m.strip() for m in os.environ.get("WEATHER_MODELS", "ecmwf_ifs025,best_match").split(",")
                     if m.strip()) or ("best_match",),
        max_rain_percent=limits["MAX_RAIN_PERCENT"],
        max_feels_like_c=limits["MAX_FEELS_LIKE_C"],
        max_uv_index=limits["MAX_UV_INDEX"],
    )


@dataclass(frozen=True)
class BackupSettings:
    folder: str                                # where dumps go (BACKUP_DIR); in the container, a mounted folder
    keep_days: int = 30                        # older dumps are deleted (BACKUP_KEEP_DAYS); the newest is always kept


def load_backup_settings() -> BackupSettings | None:
    """Backup settings, or None when BACKUP_DIR isn't set (no backups)."""
    load_dotenv()
    folder = os.environ.get("BACKUP_DIR")
    if not folder:
        return None
    keep = int(os.environ.get("BACKUP_KEEP_DAYS", "30"))
    if keep < 1:
        raise ValueError(f"BACKUP_KEEP_DAYS must be 1 or more, got {keep}")
    return BackupSettings(folder, keep)


def load_plan_times() -> tuple[time, ...]:
    """Times of day for the scheduled re-plan (PLAN_TIMES, default 20:00), sorted."""
    load_dotenv()
    return parse_times(os.environ.get("PLAN_TIMES", "20:00"))


def load_calendar_settings() -> CalendarSettings | None:
    """CalDAV settings, or None when CALDAV_URL isn't set. Only called by commands that read the calendars,
    so a calendar misconfiguration never blocks database-only commands."""
    load_dotenv()
    url = os.environ.get("CALDAV_URL")
    if not url:
        return None
    if not url.lower().startswith("https://"):
        raise ValueError("CALDAV_URL must start with https:// (the app password must not travel unencrypted)")
    missing = [k for k in ("NEXTCLOUD_USER", "NEXTCLOUD_APP_PASSWORD", "BUSY_CALENDARS") if not os.environ.get(k)]
    if missing:
        raise ValueError(f"CALDAV_URL is set but {', '.join(missing)} is not (see .env.example)")
    buffer = int(os.environ.get("EVENT_BUFFER_MINUTES", "30"))
    if buffer < 0:
        raise ValueError(f"EVENT_BUFFER_MINUTES must be 0 or more, got {buffer}")
    reminder = int(os.environ.get("REMINDER_MINUTES", "30"))
    if reminder < 0:
        raise ValueError(f"REMINDER_MINUTES must be 0 or more, got {reminder}")
    timeout = int(os.environ.get("CALDAV_TIMEOUT_SECONDS", "30"))
    if timeout <= 0:
        raise ValueError(f"CALDAV_TIMEOUT_SECONDS must be more than 0, got {timeout}")
    return CalendarSettings(
        url=url.rstrip("/"),
        user=os.environ["NEXTCLOUD_USER"],
        password=os.environ["NEXTCLOUD_APP_PASSWORD"],
        ca_cert=os.environ.get("CALDAV_CA_CERT") or True,
        calendars=parse_busy_calendars(os.environ["BUSY_CALENDARS"]),
        buffer_minutes=buffer,
        keywords={kind: _keywords(var, default) for kind, (var, default) in KEYWORD_SETTINGS.items()},
        timezone=os.environ.get("PLANNER_TIMEZONE", "America/New_York"),
        timeout_seconds=timeout,
        chores_calendar=os.environ.get("CHORES_CALENDAR_URI", "chores"),
        reminder_minutes=reminder,
    )


def parse_busy_calendars(text: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """'uri-a=nicolas; uri-b=nicolas,anna' → (('uri-a', ('nicolas',)), ('uri-b', ('nicolas', 'anna')))"""
    out = []
    for entry in text.split(";"):
        if not entry.strip():
            continue
        uri, sep, people = entry.partition("=")
        names = tuple(p.strip() for p in people.split(",") if p.strip())
        if not sep or not uri.strip() or not names:
            raise ValueError(f"BUSY_CALENDARS entry '{entry.strip()}' should look like <calendar-uri>=<person>[,<person>]")
        out.append((uri.strip(), names))
    if not out:
        raise ValueError("BUSY_CALENDARS is empty")
    return tuple(out)


def _keywords(var: str, default: str) -> tuple[str, ...]:
    return tuple(k.strip().lower() for k in os.environ.get(var, default).split(",") if k.strip())
