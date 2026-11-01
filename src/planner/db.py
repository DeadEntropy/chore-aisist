"""All database access. The only module that contains SQL."""

from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

import pymysql
from pymysql.cursors import DictCursor

from planner.config import Settings
from planner.models import Chore, Completion, Person, PlannedRecord, RunRecord

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
DatabaseError = pymysql.err.MySQLError          # so other modules can catch database errors without pymysql
DB_TIMEOUT_SECONDS = 300                        # per read/write; far longer than any query here (GET_LOCK waits 120)

CHORE_COLUMNS = [
    "id", "name", "category", "type",
    "interval_min_days", "interval_target_days", "interval_max_days",
    "duration_h", "priority", "assignee", "outdoor", "not_before", "active", "description",
]


@contextmanager
def connect(settings: Settings):
    """Open a connection; commit if the block succeeds, roll back if it raises."""
    conn = pymysql.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        database=settings.db_name,
        cursorclass=DictCursor,
        autocommit=False,
        connect_timeout=10,
        read_timeout=DB_TIMEOUT_SECONDS,       # a dead connection fails instead of blocking the scheduler forever
        write_timeout=DB_TIMEOUT_SECONDS,
    )
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except pymysql.err.MySQLError:        # the connection dropped: raise the original error, not this one
            pass
        raise
    finally:
        conn.close()


PLAN_LOCK = "chore-aisist-plan"
PLAN_LOCK_TIMEOUT = 120         # seconds to wait for another run to finish


@contextmanager
def plan_lock(conn, timeout_seconds: int | None = None):
    """Hold the planner's named lock (MariaDB GET_LOCK) for the block, so plan runs and done/skip reports from
    different processes (the scheduler and one-off commands) never interleave their read → save → calendar steps.

    Once the lock is held, the current transaction is committed, so the block's reads see everything the previous
    holder committed (a transaction started earlier would keep reading an older snapshot).
    """
    if timeout_seconds is None:
        timeout_seconds = PLAN_LOCK_TIMEOUT
    with conn.cursor() as cur:
        cur.execute("SELECT GET_LOCK(%s, %s) AS got", (PLAN_LOCK, timeout_seconds))
        if cur.fetchone()["got"] != 1:
            raise ValueError(f"Another planner run is still busy (waited {timeout_seconds} s); try again in a minute")
    try:
        conn.commit()
        yield
    finally:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT RELEASE_LOCK(%s)", (PLAN_LOCK,))
        except pymysql.err.MySQLError:        # connection gone: the server drops its locks anyway; keep the real error
            pass


# --- migrations -------------------------------------------------------------

def migrate(conn, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply migration files not yet recorded in schema_migrations, in name order."""
    files = sorted(migrations_dir.glob("*.sql"))
    if not files:
        raise ValueError(f"No migration files found in {migrations_dir}")
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version VARCHAR(255) PRIMARY KEY,"
            " applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        cur.execute("SELECT version FROM schema_migrations")
        done = {row["version"] for row in cur.fetchall()}

        applied = []
        for path in files:
            if path.name in done:
                continue
            for statement in _split_sql(path.read_text()):
                cur.execute(statement)
            cur.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.name,))
            conn.commit()
            applied.append(path.name)
    return applied


def _split_sql(text: str) -> list[str]:
    lines = [line for line in text.splitlines() if not line.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";\n") if s.strip().rstrip(";")]


# --- chores -----------------------------------------------------------------

def get_chores(conn, include_inactive: bool = False) -> list[Chore]:
    with conn.cursor() as cur:
        where = "" if include_inactive else "WHERE active"
        cur.execute(f"SELECT {', '.join(CHORE_COLUMNS)}, created_at FROM chore {where} ORDER BY category, id")
        rows = cur.fetchall()
        cur.execute("SELECT chore_id, needs_id FROM chore_need ORDER BY needs_id")
        needs: dict[str, list[str]] = {}
        for row in cur.fetchall():
            needs.setdefault(row["chore_id"], []).append(row["needs_id"])
    return [_row_to_chore(row, needs.get(row["id"], [])) for row in rows]


def get_chore(conn, chore_id: str) -> Chore | None:
    with conn.cursor() as cur:
        cur.execute(f"SELECT {', '.join(CHORE_COLUMNS)}, created_at FROM chore WHERE id = %s", (chore_id,))
        row = cur.fetchone()
        if row is None:
            return None
        cur.execute("SELECT needs_id FROM chore_need WHERE chore_id = %s ORDER BY needs_id", (chore_id,))
        needs = [r["needs_id"] for r in cur.fetchall()]
    return _row_to_chore(row, needs)


def add_chore(conn, chore: Chore) -> None:
    """Insert the chore row only; call set_needs() once every referenced chore exists. `created_on`, if set, is
    its creation date (else now)."""
    columns = CHORE_COLUMNS + (["created_at"] if chore.created_on else [])
    values = [getattr(chore, col) for col in CHORE_COLUMNS] + ([chore.created_on] if chore.created_on else [])
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO chore ({', '.join(columns)})"
            f" VALUES ({', '.join(['%s'] * len(columns))})",
            values,
        )


def update_chore(conn, chore_id: str, changes: dict) -> None:
    unknown = set(changes) - set(CHORE_COLUMNS[1:])
    if unknown:
        raise ValueError(f"Unknown chore field(s): {', '.join(sorted(unknown))}")
    if not changes:
        return
    assignments = ", ".join(f"{col} = %s" for col in changes)
    with conn.cursor() as cur:
        cur.execute(f"UPDATE chore SET {assignments} WHERE id = %s", [*changes.values(), chore_id])
        if cur.rowcount == 0 and get_chore(conn, chore_id) is None:
            raise ValueError(f"No chore with id '{chore_id}'")


def set_needs(conn, chore_id: str, needs: list[str]) -> None:
    """Replace the list of chores that must be done before this one."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM chore_need WHERE chore_id = %s", (chore_id,))
        cur.executemany(
            "INSERT INTO chore_need (chore_id, needs_id) VALUES (%s, %s)",
            [(chore_id, n) for n in needs],
        )


def _row_to_chore(row: dict, needs: list[str]) -> Chore:
    created_at = row.pop("created_at")
    return Chore(
        **{**row, "duration_h": float(row["duration_h"]) if row["duration_h"] is not None else None,
           "outdoor": bool(row["outdoor"]), "active": bool(row["active"])},
        needs=needs,
        created_on=created_at.date(),
    )


# --- people -----------------------------------------------------------------

def get_people(conn) -> list[Person]:
    with conn.cursor() as cur:
        cur.execute("SELECT id, name FROM person ORDER BY id")
        return [Person(r["id"], r["name"]) for r in cur.fetchall()]


def add_person(conn, person: Person) -> bool:
    """Insert if missing. Returns True if a row was added."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT IGNORE INTO person (id, name) VALUES (%s, %s)",
            (person.id, person.name),
        )
        return cur.rowcount == 1


def update_person(conn, person: Person) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE person SET name = %s WHERE id = %s", (person.name, person.id))


# --- history ----------------------------------------------------------------

def record_completion(conn, completion: Completion) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO completion (chore_id, person_id, completed_on, outcome, source)"
            " VALUES (%s, %s, %s, %s, %s)",
            (completion.chore_id, completion.person_id, completion.completed_on,
             completion.outcome, completion.source),
        )


def done_chore_ids(conn) -> set[str]:
    """Chores with at least one 'done' completion."""
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT chore_id FROM completion WHERE outcome = 'done'")
        return {r["chore_id"] for r in cur.fetchall()}


def last_done_dates(conn) -> dict[str, date]:
    """Most recent 'done' date per chore."""
    with conn.cursor() as cur:
        cur.execute("SELECT chore_id, MAX(completed_on) AS last FROM completion"
                    " WHERE outcome = 'done' GROUP BY chore_id")
        return {r["chore_id"]: r["last"] for r in cur.fetchall()}


# --- planned chores -------------------------------------------------------------

def planned_between(conn, start: datetime, end: datetime) -> list[PlannedRecord]:
    """Planned chores starting in [start, end), oldest first."""
    with conn.cursor() as cur:
        cur.execute("SELECT id, chore_id, people, start_at, end_at, caldav_uid, status, weather FROM planned"
                    " WHERE start_at >= %s AND start_at < %s ORDER BY start_at, id", (start, end))
        return [PlannedRecord(r["chore_id"], r["people"].split(","), r["start_at"], r["end_at"], r["caldav_uid"],
                              r["status"], r["id"], r["weather"]) for r in cur.fetchall()]


def add_planned(conn, record: PlannedRecord) -> None:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO planned (chore_id, people, start_at, end_at, caldav_uid, status, weather)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (record.chore_id, ",".join(record.people), record.start, record.end, record.uid, record.status,
                     record.weather))


def update_planned(conn, record: PlannedRecord) -> None:
    """Update time, people, status and forecast of an existing record (matched on its UID)."""
    with conn.cursor() as cur:
        cur.execute("UPDATE planned SET people = %s, start_at = %s, end_at = %s, status = %s, weather = %s"
                    " WHERE caldav_uid = %s",
                    (",".join(record.people), record.start, record.end, record.status, record.weather, record.uid))


def delete_planned(conn, uids: list[str]) -> None:
    with conn.cursor() as cur:
        cur.executemany("DELETE FROM planned WHERE caldav_uid = %s", [(u,) for u in uids])


# --- run log ----------------------------------------------------------------------

def log_run(conn, run: RunRecord) -> None:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO run_log (started_at, finished_at, trigger_kind, outcome, message)"
                    " VALUES (%s, %s, %s, %s, %s)",
                    (run.started_at, run.finished_at, run.trigger, run.outcome, run.message))


def last_run(conn, trigger: str | None = None, outcome: str | None = None) -> RunRecord | None:
    """The most recent run, optionally only of one trigger and/or outcome."""
    where, args = [], []
    if trigger:
        where.append("trigger_kind = %s")
        args.append(trigger)
    if outcome:
        where.append("outcome = %s")
        args.append(outcome)
    with conn.cursor() as cur:
        cur.execute("SELECT started_at, finished_at, trigger_kind, outcome, message FROM run_log"
                    + (" WHERE " + " AND ".join(where) if where else "")
                    + " ORDER BY started_at DESC, id DESC LIMIT 1", args)
        r = cur.fetchone()
    return RunRecord(r["started_at"], r["finished_at"], r["trigger_kind"], r["outcome"], r["message"]) if r else None
