"""Settings, read once from environment variables (a local `.env` is loaded if present)."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class CalendarSettings:
    url: str                                   # CalDAV base, e.g. https://nextcloud.local/remote.php/dav
    user: str
    password: str                              # Nextcloud app password
    ca_cert: str | bool                        # path to the CA that signed Nextcloud's certificate, or True
    calendars: tuple[tuple[str, tuple[str, ...]], ...]   # (calendar URI, people it blocks)
    buffer_minutes: int                        # added before and after every timed event
    travel_keywords: tuple[str, ...]           # all-day events whose title starts a word with one of these
    timezone: str
    timeout_seconds: int = 30                  # per CalDAV request, so a stuck server can't hang the planner

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
        travel_keywords=tuple(
            k.strip().lower() for k in os.environ.get("TRAVEL_KEYWORDS", "travel,trip,away").split(",") if k.strip()),
        timezone=os.environ.get("PLANNER_TIMEZONE", "America/New_York"),
        timeout_seconds=timeout,
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
