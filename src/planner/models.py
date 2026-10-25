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
    needs: list[str] = field(default_factory=list)
    active: bool = True
    created_on: date | None = None     # set by the database; one-offs wait from this date


@dataclass
class Person:
    id: str
    name: str
    weekend_hours: float


@dataclass
class Completion:
    chore_id: str
    completed_on: date
    outcome: str                       # done | skipped
    source: str                        # claude | manual | import
    person_id: str | None = None


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
    score: float                       # urgency × priority


@dataclass
class PlannedItem:
    chore: Chore
    people: list[str]                  # one person, or both for 'both' chores
    start: datetime
    end: datetime
    score: float


@dataclass
class Plan:
    saturday: date
    items: list[PlannedItem]
    unplanned: list[tuple[Chore, str]]   # eligible chores that didn't fit, with the reason
