"""Database round-trip tests. Need a throwaway MariaDB; skipped unless TEST_DB_PASSWORD is set.

    docker run -d --name chore-test-db -e MARIADB_ROOT_PASSWORD=test -e MARIADB_DATABASE=chores_test \
        -p 127.0.0.1:3308:3306 mariadb:11
    TEST_DB_PORT=3308 TEST_DB_USER=root TEST_DB_PASSWORD=test pytest
"""

import json
import os
from datetime import date, datetime, timedelta

from pathlib import Path

import pymysql
import pytest

from planner import db, seed
from planner.config import Settings
from planner.models import Completion

pytestmark = pytest.mark.skipif("TEST_DB_PASSWORD" not in os.environ, reason="no test database")

SEED = Path(__file__).resolve().parent / "fixtures" / "chores.json"
SEED_FIRST_ID = json.loads(SEED.read_text())["chores"][0]["id"]


@pytest.fixture
def settings():
    name = os.environ.get("TEST_DB_NAME", "chores_test")
    if not name.endswith("_test"):              # these tests drop every table: never point them at real data
        pytest.exit(f"TEST_DB_NAME must end in _test (got '{name}'): the database tests drop every table", 2)
    return Settings(
        db_host=os.environ.get("TEST_DB_HOST", "127.0.0.1"),
        db_port=int(os.environ.get("TEST_DB_PORT", "3308")),
        db_name=name,
        db_user=os.environ.get("TEST_DB_USER", "root"),
        db_password=os.environ["TEST_DB_PASSWORD"],
    )


@pytest.fixture
def conn(settings):
    with db.connect(settings) as c:
        with c.cursor() as cur:
            cur.execute("SELECT DATABASE() AS name")
            assert cur.fetchone()["name"].endswith("_test"), "refusing to drop tables outside a *_test database"
            cur.execute("SET FOREIGN_KEY_CHECKS = 0")
            cur.execute("SHOW TABLES")
            for row in cur.fetchall():
                cur.execute(f"DROP TABLE `{next(iter(row.values()))}`")
            cur.execute("SET FOREIGN_KEY_CHECKS = 1")
        db.migrate(c)
        yield c


def test_migrate_is_idempotent(conn):
    assert db.migrate(conn) == []


def test_migrate_refuses_empty_folder(conn, tmp_path):
    with pytest.raises(ValueError):
        db.migrate(conn, tmp_path)


def test_import_seed(conn):
    expected = json.loads(SEED.read_text())
    summary = seed.import_json(conn, SEED, today=date(2026, 10, 25))
    assert summary["chores"] == len(expected["chores"])
    assert summary["people"] == len(expected["people"])
    assert summary["completions"] == sum(c["status"] == "done" for c in expected["chores"])

    caulk = db.get_chore(conn, "caulk-kids-bathroom-tap")
    assert caulk.needs == ["buy-caulk"]
    assert "fix-extension-ac" in db.done_chore_ids(conn)


def test_import_twice_adds_nothing(conn):
    seed.import_json(conn, SEED)
    again = seed.import_json(conn, SEED)
    assert again["chores"] == 0 and again["completions"] == 0


def _edited_seed(tmp_path, edit):
    data = json.loads(SEED.read_text())
    edit(data)
    path = tmp_path / "edited.json"
    path.write_text(json.dumps(data))
    return path


def test_import_without_update_leaves_existing_chores(conn, tmp_path):
    seed.import_json(conn, SEED)
    path = _edited_seed(tmp_path, lambda d: d["chores"][0].update(duration_h=9))
    s = seed.import_json(conn, path)
    assert s["updated"] == 0
    assert db.get_chore(conn, SEED_FIRST_ID).duration_h != 9


def test_import_update_applies_fields_needs_and_people(conn, tmp_path):
    seed.import_json(conn, SEED)

    def edit(d):
        d["people"][0]["name"] = "Nico"
        for c in d["chores"]:
            if c["id"] == "weed-pool-deck":
                c["duration_h"] = 1.5
            if c["id"] == "paint-wall":
                c["needs"] = ["buy-paint-greek-villa", "buy-wall-hangers"]
        d["chores"].append({"id": "clean-gutters", "name": "Clean gutters", "category": "home",
                            "type": "one_off", "status": "open", "duration_h": 2, "priority": 3,
                            "assignee": "nicolas", "outdoor": True, "needs": []})

    s = seed.import_json(conn, _edited_seed(tmp_path, edit), update=True)
    assert (s["chores"], s["updated"], s["people_updated"]) == (1, 2, 1)
    assert db.get_chore(conn, "weed-pool-deck").duration_h == 1.5
    assert db.get_chore(conn, "paint-wall").needs == ["buy-paint-greek-villa", "buy-wall-hangers"]
    assert {p.id: p.name for p in db.get_people(conn)}["nicolas"] == "Nico"


def test_import_retire_keeps_history(conn, tmp_path):
    seed.import_json(conn, SEED)
    db.record_completion(conn, Completion("screw-green-chairs", date(2026, 10, 26), "done", "claude"))
    path = _edited_seed(tmp_path, lambda d: d.update(
        chores=[c for c in d["chores"] if c["id"] != "screw-green-chairs"]))
    s = seed.import_json(conn, path, update=True, retire=True)
    assert s["retired"] == 1
    chore = db.get_chore(conn, "screw-green-chairs")
    assert chore is not None and chore.active is False
    assert "screw-green-chairs" in db.done_chore_ids(conn)


def test_update_and_needs(conn):
    seed.import_json(conn, SEED)
    db.update_chore(conn, "weed-pool-deck", {
        "interval_min_days": 14, "interval_target_days": 21, "interval_max_days": 35,
        "duration_h": 1.5, "assignee": "either"})
    db.set_needs(conn, "paint-wall", ["buy-paint-greek-villa", "buy-wall-hangers"])
    weed = db.get_chore(conn, "weed-pool-deck")
    assert (weed.interval_target_days, weed.duration_h, weed.assignee) == (21, 1.5, "either")
    assert db.get_chore(conn, "paint-wall").needs == ["buy-paint-greek-villa", "buy-wall-hangers"]


def test_constraints_reject_bad_values(conn):
    seed.import_json(conn, SEED)
    with pytest.raises(pymysql.err.MySQLError):
        db.update_chore(conn, "weed-pool-deck", {"priority": 9})
    with pytest.raises(pymysql.err.MySQLError):
        db.set_needs(conn, "paint-wall", ["no-such-chore"])


def test_export_round_trip(conn, tmp_path):
    seed.import_json(conn, SEED)
    db.record_completion(conn, Completion("screw-green-chairs", date(2026, 10, 26), "done", "claude"))
    out = tmp_path / "export.json"
    seed.export_json(conn, out)
    exported = {c["id"]: c for c in json.loads(out.read_text())["chores"]}
    original = {c["id"]: c for c in json.loads(SEED.read_text())["chores"]}
    assert exported.keys() == original.keys()
    assert exported["screw-green-chairs"]["status"] == "done"
    assert exported["caulk-breakfast-room-window"]["needs"] == ["buy-caulk"]
    assert "not_before" not in exported["caulk-breakfast-room-window"]


def test_not_before_round_trip(conn, tmp_path):
    seed.import_json(conn, SEED)
    db.update_chore(conn, "paint-wall", {"not_before": date(2026, 12, 1)})
    assert db.get_chore(conn, "paint-wall").not_before == date(2026, 12, 1)
    out = tmp_path / "export.json"
    seed.export_json(conn, out)
    exported = {c["id"]: c for c in json.loads(out.read_text())["chores"]}
    assert exported["paint-wall"]["not_before"] == "2026-12-01"
    db.update_chore(conn, "paint-wall", {"not_before": None})
    seed.import_json(conn, out, update=True)                          # the file brings the date back
    assert db.get_chore(conn, "paint-wall").not_before == date(2026, 12, 1)


# --- phase 3: saved plan, Chores calendar, done/skip ------------------------------------

class FakeChoresCalendar:
    """Stands in for the Chores calendar: remembers what the planner wrote."""

    def __init__(self, monkeypatch):
        from planner import calendars
        self.events = {}
        monkeypatch.setattr(calendars, "chore_events", lambda cal, first, last: {
            uid: e for uid, e in self.events.items()
            if datetime.combine(first, datetime.min.time()) <= e.start < datetime.combine(last, datetime.min.time())})
        monkeypatch.setattr(calendars, "write_chore_events", self.write)

    def write(self, cal, put, delete):
        for e in put:
            self.events[e.uid] = e
        for uid in delete:
            self.events.pop(uid, None)


def _cal():
    from planner.config import CalendarSettings
    return CalendarSettings(url="https://nc/dav", user="app", password="p", ca_cert=True,
                            calendars=(("family", ("nicolas", "anna")),), buffer_minutes=30, keywords={},
                            timezone="America/New_York")


def _planning():
    from planner.availability import parse_window, parse_windows
    from planner.config import PlanningSettings
    return PlanningSettings(parse_windows("sat 08:00-18:00; sun 08:00-18:00"), parse_window("08:00-18:00"))


def test_plan_saves_and_mirrors_to_the_chores_calendar(conn, monkeypatch):
    from planner import jobs
    fake = FakeChoresCalendar(monkeypatch)
    seed.import_json(conn, SEED, today=date(2026, 10, 25))
    thursday = datetime(2026, 10, 29, 20)

    first = jobs.run_plan(conn, _cal(), _planning(), thursday, [])
    saved = db.planned_between(conn, datetime(2026, 10, 30), datetime(2026, 11, 6))
    assert len(saved) == len(first.result.plan.items) == len(first.changes.add) > 0
    assert set(fake.events) == {r.uid for r in saved}
    assert all(e.title.startswith("🧹 ") for e in fake.events.values())

    again = jobs.run_plan(conn, _cal(), _planning(), thursday, [])           # nothing changed → nothing to do
    assert not again.changes and again.calendar_put == again.calendar_deleted == 0


def test_done_skip_and_unknown(conn, monkeypatch):
    from planner import jobs
    fake = FakeChoresCalendar(monkeypatch)
    seed.import_json(conn, SEED, today=date(2026, 10, 25))
    jobs.run_plan(conn, _cal(), _planning(), datetime(2026, 10, 29, 20), [])
    sat = [r for r in db.planned_between(conn, datetime(2026, 10, 31), datetime(2026, 11, 1))]
    done, skipped, forgotten = sat[0], sat[1], sat[2]

    rec = jobs.report(conn, _cal(), done.chore_id, "done", date(2026, 10, 31), None)
    assert rec.uid == done.uid and done.uid not in fake.events             # reported: taken off the calendar
    assert db.last_done_dates(conn)[done.chore_id] == date(2026, 10, 31)
    jobs.report(conn, _cal(), skipped.chore_id, "skip", date(2026, 10, 31), None)
    assert skipped.uid not in fake.events

    # Saturday 20:00 run plans from Sunday: Saturday's unreported items become unknown; history stays.
    summary = jobs.run_plan(conn, _cal(), _planning(), datetime(2026, 10, 31, 20), [])
    statuses = {r.uid: r.status for r in db.planned_between(conn, datetime(2026, 10, 31), datetime(2026, 11, 1))}
    assert statuses[done.uid] == "done" and statuses[skipped.uid] == "skipped" and statuses[forgotten.uid] == "unknown"
    assert forgotten.uid in {r.uid for r in summary.unknown}
    assert fake.events[forgotten.uid].title.startswith("❔ ")


def test_report_unplanned_chore_needs_who(conn):
    from planner import jobs
    seed.import_json(conn, SEED, today=date(2026, 10, 25))
    with pytest.raises(ValueError, match="--by"):
        jobs.report(conn, None, "buy-caulk", "done", date(2026, 10, 31), None)      # 'either', not planned
    assert jobs.report(conn, None, "buy-caulk", "done", date(2026, 10, 31), ["anna"]) is None
    assert db.last_done_dates(conn)["buy-caulk"] == date(2026, 10, 31)
    with pytest.raises(ValueError, match="Unknown person"):
        jobs.report(conn, None, "buy-caulk", "done", date(2026, 10, 31), ["nicholas"])


def test_reporting_sunday_morning_then_replanning_restores_sunday(conn, monkeypatch):
    """Saturday's chores aren't reported by the Saturday 20:00 run; they're reported Sunday morning, which re-plans."""
    from planner import jobs
    FakeChoresCalendar(monkeypatch)
    seed.import_json(conn, SEED, today=date(2026, 10, 25))
    jobs.run_plan(conn, _cal(), _planning(), datetime(2026, 10, 29, 20), [])
    sunday = lambda: {r.chore_id for r in db.planned_between(conn, datetime(2026, 11, 1), datetime(2026, 11, 2))
                      if r.status == "open"}
    planned_sunday = sunday()
    saturday = [r for r in db.planned_between(conn, datetime(2026, 10, 31), datetime(2026, 11, 1))]

    jobs.run_plan(conn, _cal(), _planning(), datetime(2026, 10, 31, 20), [])   # nothing reported yet
    assert {r.chore_id for r in saturday} & sunday()                          # Saturday's ❔ chores moved to Sunday

    for r in saturday:                                                        # Sunday 08:00: "I did all of it"
        jobs.report(conn, _cal(), r.chore_id, "done", date(2026, 10, 31), None)
    jobs.run_plan(conn, _cal(), _planning(), datetime(2026, 11, 1, 8), [])
    assert sunday() == planned_sunday                                         # Sunday is back to the original plan


def test_done_by_someone_else_is_recorded_for_them(conn, monkeypatch):
    from planner import jobs
    FakeChoresCalendar(monkeypatch)
    seed.import_json(conn, SEED, today=date(2026, 10, 25))
    jobs.run_plan(conn, _cal(), _planning(), datetime(2026, 10, 29, 20), [])
    mine = next(r for r in db.planned_between(conn, datetime(2026, 10, 31), datetime(2026, 11, 2))
                if r.people == ["nicolas"])
    jobs.report(conn, _cal(), mine.chore_id, "done", mine.start.date(), ["anna"])
    saved = next(r for r in db.planned_between(conn, datetime(2026, 10, 31), datetime(2026, 11, 2)) if r.uid == mine.uid)
    assert (saved.status, saved.people) == ("done", ["anna"])


# --- phase 4a: run log, scheduler, health -----------------------------------------------

def test_run_log_round_trip(conn):
    from planner.models import RunRecord
    assert db.last_run(conn) is None
    db.log_run(conn, RunRecord(datetime(2026, 10, 26, 20, 0), datetime(2026, 10, 26, 20, 0, 4), "schedule", "ok",
                               "1 added"))
    db.log_run(conn, RunRecord(datetime(2026, 10, 27, 9, 0), datetime(2026, 10, 27, 9, 0, 2), "report", "error",
                               "calendar down"))
    assert db.last_run(conn).trigger == "report"
    assert db.last_run(conn, trigger="schedule", outcome="ok").message == "1 added"
    assert db.last_run(conn, outcome="error").message == "calendar down"
    assert db.last_run(conn, trigger="manual") is None


def test_real_runs_are_logged_even_when_they_fail(conn, monkeypatch):
    from planner import jobs
    monkeypatch.setattr(jobs, "_plan_now", lambda c, cal, planning: "summary")
    monkeypatch.setattr(jobs, "describe", lambda summary: "2 added")
    assert jobs.plan_now(conn, _cal(), _planning(), "manual") == "summary"
    assert (db.last_run(conn).outcome, db.last_run(conn).message) == ("ok", "2 added")

    def boom(c, cal, planning):
        raise ValueError("Couldn't read the calendars")
    monkeypatch.setattr(jobs, "_plan_now", boom)
    with pytest.raises(ValueError):
        jobs.plan_now(conn, _cal(), _planning(), "schedule")
    last = db.last_run(conn)
    assert (last.trigger, last.outcome, last.message) == ("schedule", "error", "ValueError: Couldn't read the calendars")


def test_health(conn):
    from planner import jobs
    from planner.models import RunRecord
    now = datetime.now()
    problems, _ = jobs.health(conn, now, None)
    assert problems == ["no successful scheduled run in the last 26 hours"]
    db.log_run(conn, RunRecord(now - timedelta(hours=1), now, "report", "ok", "ok"))
    problems, _ = jobs.health(conn, now, None)
    assert problems == ["no successful scheduled run in the last 26 hours"]     # a report's re-plan doesn't count
    db.log_run(conn, RunRecord(now - timedelta(hours=1), now, "schedule", "ok", "ok"))
    problems, summary = jobs.health(conn, now, None)
    assert problems == [] and summary.startswith("last scheduled run")


def test_scheduled_check_runs_when_due_and_backs_up_first(conn, settings, monkeypatch, tmp_path):
    from planner import jobs
    from planner.config import BackupSettings
    seed.import_json(conn, SEED)
    conn.commit()
    steps = []
    monkeypatch.setattr(jobs.backup, "dump", lambda s, folder, now, keep: steps.append("backup") or folder / "x")
    monkeypatch.setattr(jobs, "_plan_now", lambda c, cal, planning: steps.append("plan") or "summary")
    monkeypatch.setattr(jobs, "describe", lambda summary: "done")
    times = (datetime.now().time().replace(microsecond=0),)
    lines = []

    def check():
        jobs.scheduled_check(settings, _cal(), _planning(), times, BackupSettings(str(tmp_path), 30),
                             lambda message, error=False: lines.append(message))

    check()
    assert steps == ["backup", "plan"]                   # due: back up, then re-plan
    assert db.last_run(conn, trigger="schedule", outcome="ok") is not None
    check()
    assert steps == ["backup", "plan"]                   # not due again until the same time tomorrow
    assert "Re-planned: done" in lines


def test_plan_lock_is_exclusive_across_connections(conn, settings):
    with db.connect(settings) as other:
        with db.plan_lock(conn):
            with pytest.raises(ValueError, match="still busy"):
                with db.plan_lock(other, timeout_seconds=0):
                    pass
        with db.plan_lock(other, timeout_seconds=0):          # free again once released
            pass


def test_plan_lock_reads_what_the_previous_holder_committed(conn, settings):
    seed.import_json(conn, SEED)
    conn.commit()
    with db.connect(settings) as other:
        assert db.get_chore(other, "paint-wall").duration_h == 2      # `other` now reads from a snapshot
        db.update_chore(conn, "paint-wall", {"duration_h": 5})
        conn.commit()
        assert db.get_chore(other, "paint-wall").duration_h == 2      # same snapshot: the change isn't visible
        with db.plan_lock(other):
            assert db.get_chore(other, "paint-wall").duration_h == 5  # the lock starts a fresh transaction


def test_a_report_waits_for_a_running_plan(conn, settings, monkeypatch):
    from planner import jobs
    FakeChoresCalendar(monkeypatch)
    seed.import_json(conn, SEED, today=date(2026, 10, 25))
    conn.commit()
    with db.connect(settings) as scheduler:
        with db.plan_lock(scheduler):                                  # a plan run in progress elsewhere
            monkeypatch.setattr(db, "PLAN_LOCK_TIMEOUT", 0)
            with pytest.raises(ValueError, match="still busy"):
                jobs.report(conn, _cal(), "paint-wall", "done", date(2026, 10, 31), ["nicolas"])
    assert "paint-wall" not in db.last_done_dates(conn)                # nothing was recorded


# --- safety: empty database, backups in health ------------------------------------------

def test_planning_refuses_an_empty_database(conn):
    from planner import jobs
    with pytest.raises(ValueError, match="no people and no chores"):
        jobs.check_catalog(conn)
    with pytest.raises(ValueError, match="right data folder"):
        jobs.plan_now(conn, _cal(), _planning(), "manual")
    seed.import_json(conn, SEED)
    jobs.check_catalog(conn)                                           # fine once it has a catalog


def test_serve_refuses_to_start_on_an_empty_database(conn, settings, monkeypatch):
    from planner import daemon
    monkeypatch.setattr(daemon, "load_plan_times", lambda: (datetime.now().time(),))
    monkeypatch.setattr(daemon, "load_planning_settings", _planning)
    monkeypatch.setattr(daemon, "load_calendar_settings", _cal)
    monkeypatch.setattr(daemon, "load_backup_settings", lambda: None)
    with pytest.raises(ValueError, match="no people and no chores"):
        daemon.serve(settings)


def test_health_also_needs_a_recent_backup(conn, tmp_path):
    from planner import jobs
    from planner.backup import backup_name
    from planner.config import BackupSettings
    from planner.models import RunRecord
    now = datetime.now()
    db.log_run(conn, RunRecord(now - timedelta(hours=1), now, "schedule", "ok", "ok"))
    backups = BackupSettings(str(tmp_path), 30)
    problems, _ = jobs.health(conn, now, backups)
    assert problems == [f"no backup in {tmp_path} in the last 26 hours"]
    (tmp_path / backup_name(now - timedelta(hours=2))).write_bytes(b"")
    problems, summary = jobs.health(conn, now, backups)
    assert problems == [] and "last backup" in summary


def test_a_dropped_connection_logs_the_real_error(conn, settings, monkeypatch, capsys):
    from pymysql.cursors import DictCursor

    from planner import jobs
    victim = pymysql.connect(host=settings.db_host, port=settings.db_port, user=settings.db_user,
                             password=settings.db_password, database=settings.db_name, cursorclass=DictCursor)

    def lose_connection(c, cal, planning):
        with conn.cursor() as cur:
            cur.execute(f"KILL {victim.thread_id()}")
        with victim.cursor() as cur:
            cur.execute("SELECT 1")                             # fails: the connection is gone
    monkeypatch.setattr(jobs, "_plan_now", lose_connection)
    with pytest.raises(db.DatabaseError) as raised:
        jobs.plan_now(victim, _cal(), _planning(), "schedule")
    assert raised.value.args[0] in (2006, 2013)                 # the real error, not one from the rollback
    assert "couldn't write the run log" in capsys.readouterr().err


def test_many_reports_a_minute_apart_never_stack_chores(conn, monkeypatch):
    """This morning's case: each report re-plans from "now"; items placed a minute earlier count as started."""
    from planner import jobs
    fake = FakeChoresCalendar(monkeypatch)
    seed.import_json(conn, SEED, today=date(2026, 10, 25))
    conn.commit()
    for minute in range(12, 21):                                       # Saturday 10:12 … 10:20
        jobs.run_plan(conn, _cal(), _planning(), datetime(2026, 10, 31, 10, minute), [])
    today = [r for r in db.planned_between(conn, datetime(2026, 10, 31), datetime(2026, 11, 1)) if r.status == "open"]
    assert today
    for person in ("nicolas", "anna"):
        mine = sorted((r for r in today if person in r.people), key=lambda r: r.start)
        assert all(a.end <= b.start for a, b in zip(mine, mine[1:])), f"{person}'s chores overlap"
    assert all(r.start.minute % 15 == 0 for r in today)                # quarter hours, never 10:17
    on_saturday = {uid for uid, e in fake.events.items() if e.start.date() == date(2026, 10, 31)}
    assert on_saturday == {r.uid for r in today}                       # the calendar shows exactly these


def test_a_done_chore_leaves_the_calendar(conn, monkeypatch):
    from planner import jobs
    fake = FakeChoresCalendar(monkeypatch)
    seed.import_json(conn, SEED, today=date(2026, 10, 25))
    jobs.run_plan(conn, _cal(), _planning(), datetime(2026, 10, 29, 20), [])
    first = db.planned_between(conn, datetime(2026, 10, 31), datetime(2026, 11, 2))[0]
    assert first.uid in fake.events
    jobs.report(conn, _cal(), first.chore_id, "done", date(2026, 10, 31), None)
    assert first.uid not in fake.events                                # gone from the calendar …
    assert first.chore_id in db.last_done_dates(conn)                  # … but recorded in the database


def test_a_missed_morning_chore_turns_unknown_and_is_not_moved(conn, monkeypatch):
    """Review of #11: a 07:00 run, then a 10:17 run with no reports, must leave the morning items where they were."""
    from planner import jobs
    fake = FakeChoresCalendar(monkeypatch)
    seed.import_json(conn, SEED, today=date(2026, 10, 25))
    conn.commit()
    jobs.run_plan(conn, _cal(), _planning(), datetime(2026, 10, 31, 7, 0), [])
    morning = [r for r in db.planned_between(conn, datetime(2026, 10, 31), datetime(2026, 11, 1))
               if r.end <= datetime(2026, 10, 31, 10, 17)]
    assert morning
    summary = jobs.run_plan(conn, _cal(), _planning(), datetime(2026, 10, 31, 10, 17), [])
    after = {r.uid: r for r in db.planned_between(conn, datetime(2026, 10, 31), datetime(2026, 11, 1))}
    for r in morning:
        assert (after[r.uid].start, after[r.uid].status) == (r.start, "unknown")   # same time, now ❔
        assert fake.events[r.uid].title.startswith("❔ ")
    assert len(summary.unknown) == len(morning)


def test_reporting_a_missed_chore_picks_the_unknown_item_and_drops_the_copy(conn, monkeypatch):
    from planner import jobs
    FakeChoresCalendar(monkeypatch)
    seed.import_json(conn, SEED, today=date(2026, 10, 25))
    conn.commit()
    jobs.run_plan(conn, _cal(), _planning(), datetime(2026, 10, 31, 7, 0), [])
    missed = min(db.planned_between(conn, datetime(2026, 10, 31), datetime(2026, 11, 1)), key=lambda r: r.start)
    jobs.run_plan(conn, _cal(), _planning(), datetime(2026, 10, 31, 10, 17), [])
    copies = [r for r in db.planned_between(conn, datetime(2026, 10, 31), datetime(2026, 11, 1))
              if r.chore_id == missed.chore_id and r.uid != missed.uid]
    assert copies                                                          # re-planned later that day
    record = jobs.report(conn, _cal(), missed.chore_id, "done", date(2026, 10, 31), ["nicolas"])
    assert record.uid == missed.uid                                        # the ❔ item is the one reported
    jobs.run_plan(conn, _cal(), _planning(), datetime(2026, 10, 31, 10, 20), [])
    left = [r for r in db.planned_between(conn, datetime(2026, 10, 31), datetime(2026, 11, 1))
            if r.chore_id == missed.chore_id and r.status == "open"]
    assert left == []                                                      # the same-day copy is gone
