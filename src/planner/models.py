"""Data shapes shared by all modules. No logic, no I/O."""

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class Chore:
    id: str
    name: str
    category: str                      # home | garden
    type: str                          # recurring | one_off
    interval_min_days: int | None = None
    interval_target_days: int | None = None
    interval_max_days: int | None = None
    duration_h: float | None = None
    priority: int | None = None        # 1 nice to have … 5 costly to miss
    assignee: str | None = None        # nicolas | anna | either | both
    outdoor: bool = False              # outdoor = weather-sensitive
    not_before: date | None = None     # not planned before this day (one-offs wait from it)
    needs: list[str] = field(default_factory=list)
    active: bool = True
    created_on: date | None = None     # set by the database; one-offs wait from this date
    description: str | None = None     # details shown in its calendar events (which trees, a store link...)


@dataclass
class Person:
    id: str
    name: str


@dataclass
class Completion:
    chore_id: str
    completed_on: date
    outcome: str                       # done | skipped
    source: str                        # claude | manual | import
    person_id: str | None = None


@dataclass
class CalEvent:
    """A calendar event in local time, classified by what it means for chore time (see calendars.classify).

    kind: busy (timed event, blocks its time) | away (all-day travel) | no_chores (all-day "No chores")
        | extra_day (all-day "Day off"/"Holiday": the whole day becomes available)
        | extra_time (timed "Chores": that time becomes available)
    All-day events cover whole days, end exclusive.
    """
    people: list[str]
    summary: str
    start: datetime
    end: datetime
    kind: str = "busy"


@dataclass
class HourWeather:
    """The forecast for one hour (local time), from `weather`. None where the forecast has no value."""
    rain_percent: float | None = None      # chance of rain
    feels_like_c: float | None = None      # apparent temperature
    uv_index: float | None = None


@dataclass
class Busy:
    """A span of time when a person can't do chores (from their calendars)."""
    person_id: str
    start: datetime
    end: datetime


@dataclass
class Scored:
    chore: Chore
    urgency: float
    score: float                       # urgency × priority (purchases inherit the score of the job they unlock)
    waits_for: list[str] = field(default_factory=list)   # needs not done yet; must be planned earlier this weekend


@dataclass
class PlannedItem:
    chore: Chore
    people: list[str]                  # one person, or both for 'both' chores
    start: datetime
    end: datetime
    score: float


@dataclass
class Plan:
    start: date                          # first day of the planning period
    days: list[date]                     # the days planned (every day of the period, free time or not)
    items: list[PlannedItem]
    unplanned: list[tuple[Chore, str]]   # eligible chores that didn't fit, with the reason


@dataclass
class PlannedRecord:
    """A chore saved in the `planned` table (and shown in the Chores calendar)."""
    chore_id: str
    people: list[str]
    start: datetime
    end: datetime
    uid: str
    status: str = "open"                 # open | done | skipped | unknown
    id: int | None = None
    weather: str | None = None           # outdoor chores: the forecast over its time, from the last run that had one


@dataclass
class RunRecord:
    """A planning run in the `run_log` table."""
    started_at: datetime
    finished_at: datetime
    trigger: str                         # schedule | manual | report
    outcome: str                         # ok | error
    message: str | None = None
