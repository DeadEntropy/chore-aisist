# chore-aisist

Household chore planner: knows the house and garden chores, reads the family calendars, and plans what to do
each weekend into a dedicated Nextcloud "Chores" calendar. Completions are reported through Claude Code.

- [DESIGN.md](DESIGN.md) — goals, architecture, data model, planning algorithm, build plan
- [CALENDAR.md](CALENDAR.md) — Nextcloud calendars, the app user, CalDAV access

## Layout

```
src/planner/
  config.py     settings from environment variables
  models.py     data shapes (Chore, Person, Completion)
  db.py         all SQL, including migrations
  seed.py       import / export chores.json
  cli.py        the `planner` command
migrations/     schema changes, applied in name order by `planner migrate`
tests/          pytest; database tests need a throwaway MariaDB (see below)
deploy/         compose template + .env.example for the deployment folder
chores.json     seed data, loaded once with `planner import`
```

Later phases add `scoring.py`, `fitting.py`, `calendars.py`, `jobs.py` (see DESIGN.md §10).

## Development

Uses [uv](https://docs.astral.sh/uv/) for the virtual environment.

```bash
uv venv .venv --python 3.12
uv pip install --python .venv -e '.[dev]'
cp .env.example .env && chmod 600 .env     # fill in passwords
.venv/bin/planner --help
```

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

## Commands

```
planner migrate                          create / upgrade tables
planner import [chores.json]             load seed data (adds missing chores only)
planner export out.json                  snapshot the database as JSON
planner chore list [--all]               show the catalog
planner chore add <id> name=... category=home|garden type=recurring|one_off [more fields]
planner chore set <id> key=value ...     e.g. interval=7,10,14 duration_h=1.5 priority=3
                                              assignee=nicolas|anna|either|both outdoor=yes needs=a,b
```

`none` clears a field (`assignee=none`, `needs=none`).

## Deployment

The image is built from this repo; the running stack lives in `<deploy-dir>/chore-aisist/`
(template in `deploy/`).

```bash
docker build -t chore-aisist:0.1.0 .
cd <deploy-dir>/chore-aisist
docker compose up -d chore-aisist-db
docker compose run --rm chore-aisist migrate
docker compose run --rm chore-aisist import
```

| Container | Port | Notes |
|---|---|---|
| `chore-aisist-db` | `127.0.0.1:3307` → 3306 | MariaDB, this machine only |
| `chore-aisist` | none | runs one-off commands until the scheduler exists |
