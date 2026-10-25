# Household Planner — Design & Implementation Guide

## 1. Purpose

Family life, work, the house and the backyard generate more chores than fit into a weekend. Without a system, recurring tasks (trimming, fertilizing, weeding) are forgotten, kids' events crowd out house work, and the backlog compounds.

The Household Planner is a self-hosted assistant that:

- knows every chore, especially recurring ones, and when each is due;
- knows the family's constraints (kids' events, gym, travel, work);
- decides, each week, **what to do this weekend** given the time actually available;
- plans ahead of travel weeks and catches up gracefully afterwards;
- delivers the plan to the family's existing calendars and phones.

## 2. Guiding principles

1. **Deterministic core, AI at the edges.** Scoring and scheduling are plain, testable code. The LLM only writes the weekly brief and (later) parses natural-language input.
2. **Reuse Nextcloud for everything except the planner.** Storage of events, sync to phones, sharing with family, and check-off all come from CalDAV. The only custom component is the planner.
3. **Windows, not dates.** Chores have a tolerance window ("every 3–5 weeks"), not a fixed due date.
4. **Count from actual completion.** The next due date is computed from when a chore was *actually done*, so a slip doesn't compound.
5. **Capacity is a budget.** Each person has a realistic number of hours per weekend; the planner never schedules beyond it.

## 3. Context & constraints

- Two adults share chores; two kids with recurring events and parties.
- One week per month away for travel → roughly one lost weekend per month.
- Regular gym sessions (fixed blocks).
- South Florida climate: year-round growing, rainy season → weather matters for outdoor work.
- Infrastructure: home server (Intel N100) running Docker and Nextcloud; Nextcloud hosts the family calendars.

## 4. Architecture

```
┌──────────────────────── N100 home server ────────────────────────┐
│                                                                  │
│  ┌──────────────────┐   CalDAV    ┌───────────────────────────┐  │
│  │  planner (Docker)│◄───────────►│ Nextcloud                 │  │
│  │  - scheduler     │  read/write │  - family calendars (read)│  │
│  │  - scoring       │             │  - "Chores" calendar      │  │
│  │  - SQLite        │             │  - "Chores" task list     │  │
│  └───────┬──────────┘             │  - chores.yaml (Files)    │  │
│          │                        └───────────────────────────┘  │
└──────────┼───────────────────────────────────────────────────────┘
           │ HTTPS (outbound only)
           ├──► Weather API (forecast for the weekend)
           ├──► Claude API (weekly brief text)
           └──► Notification channel (Home Assistant / email / Nextcloud Talk)

Phones / laptops ◄── CalDAV sync ── Nextcloud  (view plan, tick tasks)
```

### Components

| Component | Role |
|---|---|
| **Planner service** | Small .NET worker (or Python) in one Docker container. Wakes on schedule, plans, writes, sleeps. |
| **SQLite** | Chore state and completion log, on a mounted volume (covered by existing backups). |
| **Chore catalog** | `chores.yaml` in a Nextcloud folder; editable from anywhere; reloaded each run. |
| **Nextcloud calendars (read)** | Kids' events, gym, travel → blocked time. |
| **"Chores" calendar (write)** | Planned chores as events, shared with spouse. |
| **"Chores" task list (read/write)** | Planned chores as VTODOs; ticking them off is the feedback loop. |
| **Weather API** | Moves weather-sensitive outdoor work to the drier day. |
| **Claude API** | Turns the plan into a readable brief; later, parses natural-language edits. |
| **Notifier** | Delivers the brief (Home Assistant push, email, or Nextcloud Talk). |

## 5. Data model

### 5.1 Chore catalog (`chores.yaml`)

```yaml
people:
  - id: nicolas
    weekend_hours: 5
  - id: spouse
    weekend_hours: 4

chores:
  - id: mow-lawn
    name: Mow the lawn
    interval_days: { min: 7, target: 10, max: 14 }
    duration_h: 1.5
    priority: 3            # 1 = nice to have … 5 = costly to miss
    assignee: nicolas      # nicolas | spouse | either | both
    outdoor: true
    weather_sensitive: true
    seasons: [all]         # or e.g. [mar, apr, may]

  - id: fertilize-lawn
    name: Fertilize the lawn
    interval_days: { min: 60, target: 75, max: 90 }
    duration_h: 1
    priority: 4
    assignee: either
    outdoor: true
    weather_sensitive: true
    seasons: [mar, apr, may, oct, nov]

  - id: trim-hedges
    name: Trim the hedges
    interval_days: { min: 28, target: 42, max: 60 }
    duration_h: 2
    priority: 2
    assignee: nicolas
    outdoor: true
    weather_sensitive: false
    seasons: [all]
```

### 5.2 Database (SQLite)

```sql
CREATE TABLE completion (
  id           INTEGER PRIMARY KEY,
  chore_id     TEXT NOT NULL,
  completed_at TEXT NOT NULL,      -- ISO 8601
  outcome      TEXT NOT NULL,      -- done | skipped
  source       TEXT NOT NULL       -- caldav | manual
);

CREATE TABLE planned (
  id           INTEGER PRIMARY KEY,
  chore_id     TEXT NOT NULL,
  person_id    TEXT NOT NULL,
  plan_date    TEXT NOT NULL,      -- the day it is scheduled
  caldav_uid   TEXT NOT NULL UNIQUE,
  status       TEXT NOT NULL       -- open | done | skipped | expired
);

CREATE TABLE run_log (
  id           INTEGER PRIMARY KEY,
  run_at       TEXT NOT NULL,
  kind         TEXT NOT NULL,      -- weekly | daily
  summary      TEXT
);
```

`last_done(chore)` = latest `completion.completed_at` with `outcome = done`. A chore with no history is treated as due.

## 6. Planning algorithm

### 6.1 Urgency score

For each in-season chore, with `d` = days since last done:

```
if d < min:            urgency = 0                       # not yet eligible
elif d <= target:      urgency = (d - min) / (target - min) * 0.5
elif d <= max:         urgency = 0.5 + (d - target) / (max - target) * 0.5
else:                  urgency = 1 + (d - max) / (max - min)   # overdue, keeps growing

score = urgency * priority
```

### 6.2 Look-ahead (travel weeks)

Before scoring, find the **next available weekend** after this one (skipping weekends blocked by travel). If a chore would pass `max` before that next available weekend, bump its score as if it were overdue now. This front-loads work before a trip and naturally produces a catch-up plan afterwards.

### 6.3 Capacity

For each person and each weekend day:

```
available_h = weekend_hours share for that day
            - hours blocked by calendar events (kids, gym, parties)
```

Fully blocked weekends (travel) have zero capacity.

### 6.4 Fitting

1. Sort eligible chores by `score` descending.
2. For each chore, assign it to the eligible person (per `assignee`) with the most remaining capacity on a suitable day.
3. Weather-sensitive chores go to the day with the better forecast; if both days are bad, they stay in the plan but are flagged "weather permitting".
4. Skip a chore if no one has enough remaining capacity; it will score higher next week.
5. Mark the lowest-scored scheduled chores as "skippable if rushed" in the brief.

Greedy fitting is good enough for a handful of chores per weekend; revisit only if results look wrong.

## 7. CalDAV integration

- **Read**: query each configured family calendar for events in the planning horizon (this weekend + look-ahead). Any event overlapping a weekend day reduces capacity; events tagged/categorized as travel (or on a dedicated "Travel" calendar) block the whole weekend.
- **Write events**: one VEVENT per planned chore in the "Chores" calendar, title `🧹 Mow the lawn (Nicolas)`.
- **Write tasks**: one VTODO per planned chore in the "Chores" task list, with a deterministic UID: `chore-{chore_id}-{plan_date}@household-planner`.
- **Read back completions**: on each run, fetch the planner's VTODOs:
  - `STATUS:COMPLETED` → insert `completion` with `COMPLETED` timestamp, outcome `done`.
  - `STATUS:CANCELLED` → outcome `skipped` (does not reset the interval).
  - still open after its weekend → mark `expired`; the chore simply rescores next run.
- Clients: Apple Calendar/Reminders on iOS, DAVx5 + Tasks.org on Android, Nextcloud web UI.

## 8. Runtime

| Schedule | Job |
|---|---|
| Thursday 20:00 | Weekly run: sync completions → score → fit → write events/tasks → send brief |
| Daily 07:00 | Light run: sync completions; optionally send a short "today" note on weekends |

- Packaged as a single Docker image; `docker-compose` service alongside Nextcloud.
- Config via environment variables / mounted `config.yaml`: CalDAV URL, app password, calendar names, weather location, API keys, notifier settings.
- Secrets: Nextcloud **app password** (not the main password); Claude API key in a `.env` file excluded from git.
- Idempotent runs: re-running the weekly job replaces that weekend's planned items (same UIDs) rather than duplicating them.

## 9. The weekly brief

The planner produces structured output (JSON); the Claude API turns it into a short message, e.g.:

> **This weekend**
> Saturday: party 2–4pm. Morning: mow the lawn (Nicolas), weed front beds (spouse).
> Sunday: fertilize the lawn — dry morning forecast, do it before 11.
> Skippable if rushed: trim the hedges.
> Heads-up: travel week starts the 26th, so hedges are moved up to next weekend.

The LLM never decides *what* is scheduled; it only explains the plan. If the API is unavailable, fall back to a plain templated text.

## 10. Build plan

| Phase | Deliverable | Done when |
|---|---|---|
| 1 | Catalog loader, SQLite schema, scoring + fitting, CLI that prints a plan from a seeded catalog | Plans for sample weekends look sensible; unit tests on scoring/edge cases |
| 2 | CalDAV read of family calendars; capacity + travel look-ahead | A real travel week produces front-loading and catch-up |
| 3 | CalDAV write of events + VTODOs; completion read-back | Ticking a task on a phone changes next week's plan |
| 4 | Docker image, scheduler, notifier, Claude brief | Brief arrives Thursday evening without manual steps |
| 5 | Weather integration, natural-language catalog edits, polish | — |

## 11. Testing

- **Unit**: scoring at window boundaries, seasonality, overdue growth, look-ahead across a blocked weekend, capacity never exceeded, assignee rules.
- **Simulation**: replay 12 months of synthetic weeks (with travel weeks and random skips) and check that no chore stays overdue for long and that weekend load stays within capacity.
- **Integration**: CalDAV round-trip against a test calendar on the Nextcloud instance.

## 12. Open decisions

- Language: .NET (C#) or Python.
- Notification channel: Home Assistant push, email, or Nextcloud Talk.
- Weekends only, or daily guidance as well?
- How travel is marked in the calendar (dedicated calendar vs. category/keyword).
- Whether hired help (e.g. a lawn service) should be modeled as a third "person" with its own schedule.
- Per-day capacity split (e.g. Saturday lighter when there are kids' events).
