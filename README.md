# chore-aisist

Household chore planner: knows the house and garden chores, reads the family calendars, and plans what to do
each weekend into a dedicated Nextcloud "Chores" calendar. Completions are reported through Claude Code.

- [CLAUDE.md](CLAUDE.md) — guide for AI coding agents (and humans): architecture rules, tests, workflow
- [DESIGN.md](DESIGN.md) — goals, architecture, data model, planning algorithm, build plan
- [CALENDAR-GUIDE.md](CALENDAR-GUIDE.md) — **how to use your calendar with the planner**: adding/removing chore time, travel, connecting another calendar
- [CALENDAR.md](CALENDAR.md) — Nextcloud calendars, the app user, CalDAV access

## Layout

```
src/planner/
  config.py     settings from environment variables
  models.py     data shapes (Chore, Person, Completion)
  db.py         all SQL, including migrations
  seed.py       import / export chores.json
  scoring.py    which chores are due, and how urgent
  availability.py  free chore time per person and day (default windows + calendar keywords)
  fitting.py    who does what, when, in the free time
  calendars.py  reads the family calendars from Nextcloud (CalDAV) → busy time and travel
  plans.py      the saved plan: freeze/unknown rules, re-planning, Chores calendar events
  jobs.py       ties it together: plan runs, done/skip, calendar sync
  cli.py        the `planner` command
  migrations/   schema changes, applied in name order by `planner migrate`
tests/          pytest; database tests need a throwaway MariaDB (see below)
deploy/         compose template + .env.example for the deployment folder
chores.json     seed data, loaded once with `planner import`
```

A later phase adds the scheduler (daily 20:00 run) and the weekly brief (see DESIGN.md §10).

## Development

Uses [uv](https://docs.astral.sh/uv/). Exact dependency versions are pinned in `uv.lock`; the Docker image installs
from the same file.

```bash
uv sync                                    # creates .venv with the locked versions (+ pytest)
cp .env.example .env && chmod 600 .env     # fill in passwords
.venv/bin/planner --help
```

Changing dependencies: edit `pyproject.toml`, run `uv lock`, commit both files.
Upgrading: `uv lock --upgrade`, review the `uv.lock` diff in a PR.

### Tests

```bash
# pure tests only
.venv/bin/pytest

# including database tests, against a throwaway MariaDB on port 3308
docker run -d --name chore-test-db -e MARIADB_ROOT_PASSWORD=test -e MARIADB_DATABASE=chores_test \
  -p 127.0.0.1:3308:3306 mariadb:11
TEST_DB_PORT=3308 TEST_DB_USER=root TEST_DB_PASSWORD=test .venv/bin/pytest
docker rm -f chore-test-db
```

### Calendars

`planner plan` reads the family calendars over CalDAV as the `ChoreAisist` user (settings in `.env`, see
`.env.example`). The Nextcloud certificate only covers its hostname, so the machine running the planner must resolve
`nextcloud.local`: the Docker setup maps it with `extra_hosts`; elsewhere add an `/etc/hosts` entry, or use
`--no-calendar`. Live check (reads the calendars, and writes one event dated 5 Jan 2030 to the Chores calendar,
then deletes it): `CALDAV_LIVE_TEST=1 .venv/bin/pytest tests/test_calendars_live.py`.

## Commands

```
planner migrate                          create / upgrade tables
planner import chores.json [--update] [--retire]
                                         load seed data: adds new chores; --update also applies
                                         changes to existing chores/people; --retire switches off
                                         chores missing from the file (history is kept)
planner export out.json                  snapshot the database as JSON
planner chore list [--all]               show the catalog
planner chore add <id> name=... category=home|garden type=recurring|one_off [more fields]
planner chore set <id> key=value ...     e.g. interval=7,10,14 duration_h=1.5 priority=3
                                              assignee=nicolas|anna|either|both outdoor=yes needs=a,b
                                              not_before=2026-12-01 (not planned before that day)
planner calendars                        list the calendars the app can read and whom each blocks
planner plan                             plan the next 7 days, save it, update the Chores calendar
planner done <chore> [--date D] [--by nicolas,anna]   record a chore as done (✅ in the calendar)
planner skip <chore> [--date D]          record a chore as skipped (⏭)
planner status                           last/next 7 days with ✅ ⏭ ❔ 🧹, and what's overdue
planner plan --dry-run [--date YYYY-MM-DD] [--weeks N] [--no-calendar]
                                         print the plan for the next 7 days (and the next N-1
                                         periods, assuming planned chores get done), around
                                         the family calendars; writes nothing
```

`none` clears a field (`assignee=none`, `needs=none`, `not_before=none`).

## Deployment

The image is built from this repo; the running stack lives in `<deploy-dir>/chore-aisist/`
(template in `deploy/`).

```bash
docker build -t chore-aisist:0.1.0 .
cd <deploy-dir>/chore-aisist
docker compose up -d chore-aisist-db
docker compose run --rm chore-aisist migrate
docker compose run --rm chore-aisist import /app/chores.json
```

| Container | Port | Notes |
|---|---|---|
| `chore-aisist-db` | `127.0.0.1:3307` → 3306 | MariaDB, this machine only |
| `chore-aisist` | none | runs one-off commands until the scheduler exists |
