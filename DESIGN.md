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
2. **Reuse Nextcloud for display.** Storage of events, sync to phones and sharing with family all come from CalDAV. The only custom component is the planner.
3. **Single writer.** Only the planner (its own Nextcloud user, `<app-user>`) writes to the Chores calendar. The family sees it read-only.
4. **Claude Code is the interface.** Completions, skips and catalog edits are reported by talking to Claude Code on the home server ("I've mowed the lawn"); no phone task app is needed.
5. **Data lives apart from code.** Chores and their history live in a dedicated database container on the home server, not in git. The app connects to it when it needs to; rebuilding or replacing the app never touches the data.
6. **Windows, not dates.** Chores have a tolerance window ("every 3–5 weeks"), not a fixed due date.
7. **Count from actual completion.** The next due date is computed from when a chore was *actually done*, so a slip doesn't compound.
8. **Capacity is a budget.** Each person has a realistic number of hours per weekend; the planner never schedules beyond it.

## 3. Context & constraints

- Two adults share chores; two kids with recurring events and parties.
- One week per month away for travel → roughly one lost weekend per month.
- Regular gym sessions (fixed blocks).
- South Florida climate: year-round growing, rainy season → weather matters for outdoor work.
- Infrastructure: home server (Intel N100) running Docker; every service is a compose stack in its own folder on the NAS share (so backed up). Nextcloud hosts the family calendars.

## 4. Architecture

```
┌──────────────────────────── N100 home server ────────────────────────────────┐
│                                                                              │
│  Claude Code ── docker exec chore-aisist planner done mow-lawn ──┐           │
│  (Nicolas / Anna)                                                ▼           │
│  ┌──────────────────────┐  SQL   ┌────────────────────────────────────────┐  │
│  │ chore-aisist-db      │◄──────►│ chore-aisist (app container, Python)   │  │
│  │ MariaDB              │        │ - CLI: done / skip / plan / chore …    │  │
│  │ - chores             │        │ - scheduler (weekly run)               │  │
│  │ - completion history │        │ - scoring + fitting                    │  │
│  │ - plans, run log     │        └───────┬───────────────────┬────────────┘  │
│  └──────────────────────┘                │ CalDAV as         │               │
│                                          │ <app-user>        │               │
│                          read ┌──────────▼───────────────────▼───────────┐   │
│                               │ Nextcloud                                │   │
│                               │ - Nicolas, Family, School, Anna (read)   │   │
│                               │ - "Chores" calendar (write)              │   │
│                               └──────────────────────────────────────────┘   │
└──────────────────────────────────────────┬───────────────────────────────────┘
                                           │ HTTPS (outbound only)
                                           ├──► Weather API (forecast for the weekend)
                                           ├──► Claude API (weekly brief text)
                                           └──► Notification channel (Home Assistant / email / Nextcloud Talk)

Phones / laptops ◄── CalDAV sync ── Nextcloud  (view plan, read-only)
```

### Components

| Component | Role |
|---|---|
| **`chore-aisist` (app)** | Python, one long-running container. Runs the weekly job on an internal schedule and exposes a CLI (`planner …`) that Claude Code calls with `docker exec`. Holds no data of its own. |
| **`chore-aisist-db` (MariaDB)** | Dedicated database container: the chore catalog, completion history, plans and run log. Data files on the NAS share (backed up). Separate from Nextcloud's database so each can be upgraded/restored independently. |
| **App Nextcloud user (`<app-user>`)** | The app's identity. Owns the Chores calendar; read-only access to the family calendars. Details in `CALENDAR.md`. |
| **Nextcloud calendars (read)** | Nicolas, Family, School, Anna: kids' events, gym, travel → blocked time. |
| **"Chores" calendar (write)** | Planned chores as events; shared read-only with Nicolas and Anna. |
| **Claude Code** | The human interface: turns "I've done the lawn" or "hedges take 3 hours" into CLI calls; can also query the database directly for questions ("when did we last fertilize?"). |
| **Weather API** | Moves outdoor work to the drier day. |
| **Claude API** | Turns the plan into a readable brief; later, parses natural-language edits. |
| **Notifier** | Delivers the brief (Home Assistant push, email, or Nextcloud Talk). |

## 5. Data model

Everything lives in the `chore-aisist-db` MariaDB database. `chores.json` in this repo is only the **seed**: it is
imported once (`planner import chores.json`) and after that the database is the source of truth.
`planner export` writes the current catalog back out as JSON for a readable snapshot.

```sql
-- The chore catalog: what needs doing and how often.
CREATE TABLE chore (
  id                 VARCHAR(64)  PRIMARY KEY,          -- e.g. 'weed-front-yard'
  name               VARCHAR(255) NOT NULL,
  category           VARCHAR(32)  NOT NULL,             -- home | garden
  type               VARCHAR(16)  NOT NULL,             -- recurring | one_off
  interval_min_days  INT NULL,                          -- recurring only
  interval_target_days INT NULL,
  interval_max_days  INT NULL,
  duration_h         DECIMAL(4,2) NULL,
  priority           TINYINT NULL,                      -- 1 nice to have … 5 costly to miss
  assignee           VARCHAR(16) NULL,                  -- nicolas | anna | either | both
  outdoor            BOOLEAN NOT NULL DEFAULT FALSE,    -- outdoor = weather-sensitive
  active             BOOLEAN NOT NULL DEFAULT TRUE,     -- FALSE = retired, kept for history
  created_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- Ordering: a chore can only be planned once everything it needs is done.
CREATE TABLE chore_need (
  chore_id   VARCHAR(64) NOT NULL REFERENCES chore(id),   -- e.g. 'caulk-kids-bathroom-tap'
  needs_id   VARCHAR(64) NOT NULL REFERENCES chore(id),   -- e.g. 'buy-caulk'
  PRIMARY KEY (chore_id, needs_id)
);

-- Who does chores and how much time they have.
CREATE TABLE person (
  id              VARCHAR(32) PRIMARY KEY,              -- nicolas | anna
  name            VARCHAR(64) NOT NULL,
  weekend_hours   DECIMAL(4,2) NOT NULL
);

-- History: every time a chore was done or skipped.
CREATE TABLE completion (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  chore_id      VARCHAR(64) NOT NULL REFERENCES chore(id),
  person_id     VARCHAR(32) NULL REFERENCES person(id),
  completed_on  DATE NOT NULL,
  outcome       VARCHAR(16) NOT NULL,                   -- done | skipped
  source        VARCHAR(16) NOT NULL,                   -- claude | manual | import
  recorded_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- What the planner put on the calendar.
CREATE TABLE planned (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  chore_id    VARCHAR(64) NOT NULL REFERENCES chore(id),
  person_id   VARCHAR(32) NOT NULL REFERENCES person(id),
  plan_date   DATE NOT NULL,
  caldav_uid  VARCHAR(255) NOT NULL UNIQUE,
  status      VARCHAR(16) NOT NULL                      -- open | done | skipped | expired
);

CREATE TABLE run_log (
  id       INT AUTO_INCREMENT PRIMARY KEY,
  run_at   DATETIME NOT NULL,
  kind     VARCHAR(16) NOT NULL,                        -- weekly | daily | manual
  summary  TEXT NULL
);
```

- `last_done(chore)` = latest `completion.completed_on` with `outcome = done`. A chore with no history is treated as due.
- A `one_off` chore is finished once it has a `done` completion; it is never planned again. No separate status column
  to keep in sync. Items already ticked in the ToDoList import get a completion with `source = import`.
- `outdoor` doubles as "weather-sensitive": every outdoor chore can be moved by rain.
- Purchases are not a field: anything that needs buying is its own `Buy …` chore, and the job lists it in
  `needs` (JSON) / `chore_need` (database). A chore is only eligible once every chore it needs has a `done`
  completion. A list rather than a single value, because one purchase can unblock several jobs (`buy-caulk`
  → both caulking chores) and a job could need several purchases.
- Unknown values (duration, priority, interval…) stay `NULL` until filled in; the planner skips chores it can't size yet
  and lists them in the brief as "needs details".
- Schema changes are versioned migration scripts shipped inside the package (`src/planner/migrations/NNN_*.sql`), applied by `planner migrate`.

## 6. Planning algorithm

### 6.1 Urgency score

For each eligible chore, with `d` = days since last done:

```
if d < min:            urgency = 0                       # not yet eligible
elif d <= target:      urgency = (d - min) / (target - min) * 0.5
elif d <= max:         urgency = 0.5 + (d - target) / (max - target) * 0.5
else:                  urgency = 1 + (d - max) / (max - min)   # overdue, keeps growing

score = urgency * priority
```

### 6.2 Look-ahead (travel weeks)

Before scoring, find the **next available weekend** after this one (skipping weekends blocked by travel). If a chore would pass `max` before that next available weekend, bump its score as if it were overdue now. This front-loads work before a trip and naturally produces a catch-up plan afterwards.

Decided for phase 1:

- Never-done recurring chores count as fully due (urgency 1.0). Degenerate windows (`min = target` or `target = max`)
  are handled without division by zero.
- **One-off chores** have no window: urgency starts at **0.5** when the chore is added and grows by **0.2 per week**
  waiting (`score = urgency × priority`), so a neglected one-off overtakes a fully-due recurring chore after ~2.5 weeks.
- `needs`: a job whose needs aren't done yet can still be planned if every pending need is planned **earlier the same
  weekend** (buy Saturday morning, do the job that afternoon or Sunday). Pending needs inherit the job's score when
  it's higher, and take the earliest free slot so the job has room after them. If the need doesn't fit, neither does
  the job.
- Chores missing duration, priority, assignee (or interval, for recurring) are not planned and are listed as
  "needs details".

### 6.3 Capacity

For each person and each weekend day:

```
available_h = weekend_hours share for that day
            - hours blocked by calendar events (kids, gym, parties)
```

Fully blocked weekends (travel) have zero capacity.

Implemented as **time slots** rather than subtracting event hours from the budget:

- Chores can be placed between **08:00 and 18:00** on Saturday and Sunday.
- Each person's busy events are removed from that window. Calendar → person: Nicolas → Nicolas, Anna → Anna,
  Family and School → both.
- A person's chores never exceed their `weekend_hours` budget for the weekend (6 h each to start).

### 6.4 Fitting

1. Drop chores whose `needs` aren't all done yet, then sort eligible chores by `score` descending.
2. For each chore, assign it to the eligible person (per `assignee`) with the most remaining capacity on a suitable day.
3. Outdoor chores go to the day with the better forecast; if both days are bad, they stay in the plan but are flagged "weather permitting".
4. Skip a chore if no one has enough remaining capacity; it will score higher next week.
5. Mark the lowest-scored scheduled chores as "skippable if rushed" in the brief.

Greedy fitting is good enough for a handful of chores per weekend; revisit only if results look wrong.

Placement details (phase 1): `either` goes to the person with the most budget left; `both` needs a slot where both
are free and uses both budgets. Each chore goes in the first long-enough free slot on the day where that person (or
pair) has the most free time left, which spreads work over the two days. Chores appear in the Chores calendar as
**timed events** (e.g. `Sat 09:00–10:30 🧹 Mow the lawn (Nicolas)`).

`planner plan --dry-run [--date D] [--weeks N]` prints the plan without writing anything; with `--weeks N` it assumes
each planned chore is done on its day and plans the following weekends too, to show how the backlog is worked off.
Until calendar integration (phase 2), everyone is treated as free all weekend.

## 7. CalDAV integration

All access is as the app's Nextcloud user (`<app-user>`) with an app password (see `CALENDAR.md` for URLs, shares and secrets).

- **Read**: query the shared family calendars (Nicolas, Family, School, Anna) for events in the planning horizon
  (this weekend + look-ahead), expanding recurring events. Any event overlapping a weekend day reduces capacity;
  events tagged/categorized as travel (or on a dedicated "Travel" calendar) block the whole weekend.
- **Write**: one VEVENT per planned chore in the Chores calendar, title `🧹 Mow the lawn (Nicolas)`,
  with a deterministic UID: `chore-{chore_id}-{plan_date}@household-planner`.
  Events only — no VTODOs.
- **Single writer**: Nicolas and Anna have read-only access. Each run reconciles the Chores calendar against the
  `planned` table, so the calendar is always exactly what the planner decided.
- **Clients**: any CalDAV calendar app (iOS Calendar, DAVx5 + any Android calendar, Nextcloud web UI) — view only.

## 7b. Reporting completions (Claude Code)

The feedback loop is conversational. On the home server, Nicolas or Anna tells Claude Code what happened:

> "I mowed the lawn and weeded the pool deck this morning. Skipped the hedges."

Claude Code maps that to planner CLI calls inside the running container:

```bash
docker exec chore-aisist planner done mow-lawn --date 2026-10-31
docker exec chore-aisist planner done weed-pool-deck --date 2026-10-31
docker exec chore-aisist planner skip trim-hedge-mendel
```

A `/chores` Claude Code command file on the home server (like the existing `/calendar` command) documents this
vocabulary for Claude Code.

- `done` inserts a `completion` row (`outcome = done`, `source = claude`), marks the `planned` row `done`, and
  updates the calendar event (e.g. prefix `✅`).
- `skip` records `outcome = skipped` (does not reset the interval) and marks the planned row `skipped`.
- Planned chores never reported by the end of their weekend become `expired` and simply rescore next run.
- Catalog edits go through the CLI too ("hedges take closer to 3 hours" → `planner chore set trim-hedge-mendel
  duration_h=3`; "add: clean the gutters every 6 months" → `planner chore add …`), then `planner plan` refreshes
  the current weekend.
- For read-only questions, Claude Code may query the database directly
  (`docker exec chore-aisist-db mariadb … -e "SELECT …"`); writes always go through the CLI so the rules are enforced.

## 8. Runtime & deployment

| Schedule | Job |
|---|---|
| Thursday 20:00 | Weekly run: expire unreported items → score → fit → write events → send brief |
| Daily 07:00 | Optional: send a short "today" note on weekends |

The schedule runs **inside** the app container (so `docker exec` always has a running container to talk to);
no host cron.

### Two folders

| Folder | Contents | In git |
|---|---|---|
| `<dev-dir>/chore-aisist` (this repo) | Code, tests, `Dockerfile`, migrations, `chores.json` seed, docs | `DeadEntropy/chore-aisist` |
| `<deploy-dir>/chore-aisist/` | Deployment, same pattern as the other services: `docker-compose.yml`, `README.md`, `.env`, `.env.example`, `db/` (MariaDB data files) | smarthome repo (`.env` and `db/` gitignored) |

### Compose stack (`<deploy-dir>/chore-aisist/docker-compose.yml`)

- `chore-aisist-db` — `mariadb` image, data in `./db`, on an internal network shared only with the app.
  Port `127.0.0.1:3307` published to the host only, so code run from the dev folder can connect during development.
- `chore-aisist` — image `chore-aisist:<version>`, built from this repo (`docker build -t chore-aisist:0.1.0 .`);
  later possibly published from GitHub Actions to GHCR. No ports exposed.
  - `TZ=America/New_York`
  - `extra_hosts: <nextcloud-host>:<server-ip>` (the name doesn't resolve on the server itself)
  - mounts the homelab CA certificate read-only so CalDAV over HTTPS is trusted
  - reaches Nextcloud through NPM (`https://<nextcloud-host>`), not Nextcloud's internal network

### Config & secrets

- Environment variables only, from `.env` (gitignored, mode 600): DB connection, `<app-user>` app password,
  calendar names, Claude API key, notifier settings. No separate config file.
- `<app-user>` is a dedicated app account, never a personal one.

### Python

- Dependencies: `pymysql` (database), `caldav` (calendars; brings `icalendar` and `recurring-ical-events`),
  `python-dotenv`. Later: the Anthropic SDK. CLI uses the standard-library `argparse`; no ORM.
- Exact versions (with hashes) of every dependency are pinned in `uv.lock`; development (`uv sync`) and the Docker
  image install from it, so builds are reproducible and upgrades are explicit (`uv lock --upgrade`).
- The image is a two-stage build (uv only in the build stage) and runs as a non-root `app` user.

### Modules (`src/planner/`)

| Module | Does | Talks to |
|---|---|---|
| `config.py` | Settings from environment variables | — |
| `models.py` | Data shapes: `Chore`, `Person`, `Completion` (+ `PlannedItem`, `Busy` later) | — |
| `db.py` | All SQL, including `migrate()` | MariaDB |
| `seed.py` | Import / export `chores.json` | via `db.py` |
| `cli.py` | The `planner` command | the above |
| `scoring.py` | Which chores are due and how urgent (pure) | — |
| `fitting.py` | Who does what on which day within capacity (pure) | — |
| `calendars.py` | Read busy time, sync the Chores calendar | Nextcloud |
| `jobs.py` | `weekly_run()` and the long-running `serve()` | the above |
| `weather.py`, `brief.py`, `notify.py` | Phases 4–5 | external APIs |

Only `db.py` contains SQL and only `calendars.py` speaks CalDAV; `scoring.py` and `fitting.py` take plain data and
are tested without a database or network.

### Development

No separate dev environment for now: there is no precious data yet, so development uses the real database and the
real Chores calendar. Code is run from the dev checkout (Python venv) against `127.0.0.1:3307`, or as the
container. Revisit once the planner is live.

### Idempotency

Re-running the weekly job replaces that weekend's planned items (same UIDs) rather than duplicating them.

## 9. The weekly brief

The planner produces structured output (JSON); the Claude API turns it into a short message, e.g.:

> **This weekend**
> Saturday: party 2–4pm. Morning: mow the lawn (Nicolas), weed front beds (Anna).
> Sunday: fertilize the lawn — dry morning forecast, do it before 11.
> Skippable if rushed: trim the hedges.
> Heads-up: travel week starts the 26th, so hedges are moved up to next weekend.

The LLM never decides *what* is scheduled; it only explains the plan. If the API is unavailable, fall back to a plain templated text.

## 10. Build plan

| Phase | Deliverable | Done when |
|---|---|---|
| 0 | MariaDB container + schema/migrations; `planner import` of `chores.json`; `planner chore list/add/set` | The 22 chores are in the database and editable through Claude Code |
| 1 | Scoring + fitting; `planner plan --dry-run` prints a plan | Plans for sample weekends look sensible; unit tests on scoring/edge cases |
| 2 | CalDAV read of family calendars; capacity + travel look-ahead | A real travel week produces front-loading and catch-up |
| 3 | CalDAV write of events; `done`/`skip` CLI | Telling Claude Code "I mowed the lawn" changes next week's plan |
| 4 | App Docker image in the compose stack, internal scheduler, notifier, Claude brief | Brief arrives Thursday evening without manual steps |
| 5 | Weather integration, natural-language catalog edits, polish | — |

## 11. Testing

- **Unit**: scoring at window boundaries, seasonality, overdue growth, look-ahead across a blocked weekend, capacity never exceeded, assignee rules.
- **Simulation**: replay 12 months of synthetic weeks (with travel weeks and random skips) and check that no chore stays overdue for long and that weekend load stays within capacity.
- **Unit tests** run without a database or network (scoring and fitting are pure functions).
- **Integration**: database round-trip against a throwaway MariaDB container; CalDAV round-trip against the Chores calendar.

## 12. Open decisions

- Notification channel: Home Assistant push, email, or Nextcloud Talk.
- Weekends only, or daily guidance as well?
- How travel is marked in the calendar (dedicated calendar vs. category/keyword).
- Whether hired help (e.g. a lawn service) should be modeled as a third "person" with its own schedule.
- Per-day capacity split (e.g. Saturday lighter when there are kids' events).
- How `one_off` chores are scored (they have no interval window — e.g. fixed urgency from priority, growing with age).

## 13. Decisions made

- **Language**: Python (mature CalDAV / recurring-event libraries; small image).
- **Storage**: dedicated MariaDB container (`chore-aisist-db`), separate from code and from Nextcloud's database.
- **Calendar**: dedicated Chores calendar owned by the app user (`<app-user>`); events only; family read-only.
- **Feedback loop**: completions reported through Claude Code → CLI; no phone task apps.
- **Dev environment**: none separate for now; use the real database and calendar.
- **Seasonality**: removed from the chore fields for now; add back if a chore really only applies part of the year.
