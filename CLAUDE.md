# CLAUDE.md — working on chore-aisist

Household chore planner: plans chores into free time from the family's Nextcloud calendars and writes the plan to a
dedicated **Chores** calendar. Python 3.12, MariaDB, CalDAV, Docker. **This repository is public.**

## Read first

| File | What it is |
|---|---|
| `DESIGN.md` | Goals, architecture, data model, the planning algorithm, decisions made (§13) and open ones (§12) |
| `CALENDAR-GUIDE.md` | User guide: what to put in a calendar, the Chores calendar, reporting, settings |
| `CALENDAR.md` | Technical: Nextcloud calendars, the app user, shares, CalDAV access |
| `README.md` | Layout, development setup, commands, deployment |

Operating the running app (reporting chores, re-planning, editing chores) is a separate job: on the home server it
is covered by the `/chores` Claude Code command in the homelab repo, not by this file.

## Architecture rules (keep them)

- `src/planner/db.py` is the **only** module with SQL; `calendars.py` the only one that talks to Nextcloud.
- `scoring.py`, `availability.py`, `fitting.py`, `plans.py` are **pure** (no database, no network) and tested offline.
  `jobs.py` wires them to the database and calendars (and holds the business operations: real plan runs with the
  run log, the scheduler's check, health); `cli.py` stays thin (parse, call, print).
- `scheduler.py` decides *when* (pure); `backup.py` is a tool (dump, list, prune) that knows nothing about the
  schedule; `daemon.py` only keeps the process alive (loop, sleep, SIGTERM, log lines) and calls `jobs`.
- The `planned` table is the source of truth; the Chores calendar mirrors it. Only events whose UID ends in
  `@chore-aisist` may ever be deleted.
- The database is the chore catalog. `tests/fixtures/chores.json` is sample data for the tests only: don't sync
  it with the real catalog, and don't `import --update` it into the real database.
- Schema changes are new files in `src/planner/migrations/NNN_*.sql` (never edit an applied one), applied by
  `planner migrate`.
- Settings come from environment variables (`.env`, see `.env.example`); add new ones to `.env.example`,
  `deploy/.env.example`, `deploy/docker-compose.yml` and the settings table in `CALENDAR-GUIDE.md`.

## Development

```bash
uv sync                                   # .venv with the locked versions (uv lives in ~/.local/bin)
.venv/bin/pytest -q                       # pure tests; database tests are skipped

# full suite, against a throwaway MariaDB on port 3308
docker run -d --name chore-test-db -e MARIADB_ROOT_PASSWORD=test -e MARIADB_DATABASE=chores_test \
  -p 127.0.0.1:3308:3306 mariadb:11
TEST_DB_PORT=3308 TEST_DB_USER=root TEST_DB_PASSWORD=test .venv/bin/pytest -q
docker rm -f chore-test-db
```

- **Commit only when the full suite passes.** Don't pipe pytest through `tail` in a `&&` chain: it hides the exit
  code. Gate the commit on pytest itself.
- Add tests with every behaviour change; for planner logic prefer pure tests (see `tests/test_jobs.py`,
  `tests/test_plans.py`), and `tests/test_db.py` for database round trips (it has a fake Chores calendar).
- `tests/test_calendars_live.py` runs only with `CALDAV_LIVE_TEST=1`: it reads the real calendars and writes one
  event dated 5 Jan 2030 to the Chores calendar, then deletes it.
- Dependencies: edit `pyproject.toml`, run `uv lock`, commit both.

## Workflow (how the owner wants changes made)

1. Discuss non-trivial design changes before implementing; the owner decides.
2. Work on a branch, never directly on `master`. Commit author: the owner's git identity.
3. Push only when asked, then open a PR. Comment `@coderabbitai review` to get a CodeRabbit review (auto-review is
   off for this repo; the plan allows about one review per hour). SonarQube Cloud analyses every PR on its own.
4. Pull review comments in, check each against the code, and discuss which to address before fixing. Reply on each
   thread with the fix commit.
5. **Merge only when the owner says so.**

## Public repository: no private details

The database tests drop every table: they refuse to run unless `TEST_DB_NAME` ends in `_test`. Never weaken that.

Never commit internal IPs, hostnames other than `nextcloud.local`, file paths on the server, or Nextcloud user
names other than `ChoreAisist`. Use placeholders (`<server-ip>`, `<deploy-dir>`, `<owner-user>`) in docs and put
real values in `.env` (gitignored). Check before committing:
```bash
grep -rnE '192\.168|/mnt/' deploy src tests .env.example *.md
```

## Deploying a change

The running stack lives in the deployment folder (`<deploy-dir>/chore-aisist`, template in `deploy/`):
```bash
docker build --build-arg APP_UID=$(id -u) -t chore-aisist:0.1.0 .   # from this repo, on master after the merge
cd <deploy-dir>/chore-aisist
docker compose exec chore-aisist planner backup           # before a migration
docker compose run --rm chore-aisist migrate              # if there's a new migration
docker compose up -d                                      # recreate the scheduler container on the new image
docker compose exec chore-aisist planner plan --dry-run   # check
```

## Careful: these touch the family's phones

`planner plan` (without `--dry-run`), `planner done` and `planner skip` write to the shared Chores calendar,
and `done`/`skip` re-plan from now. So does `planner serve`, every day at `PLAN_TIMES` (and once on start if a run
was missed): don't run it from the dev checkout against the real database. Use `--dry-run` while developing, and **ask the owner before any real run**.
