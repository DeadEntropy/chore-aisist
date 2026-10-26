"""Database round-trip tests. Need a throwaway MariaDB; skipped unless TEST_DB_PASSWORD is set.

    docker run -d --name chore-test-db -e MARIADB_ROOT_PASSWORD=test -e MARIADB_DATABASE=chores_test \
        -p 127.0.0.1:3308:3306 mariadb:11
    TEST_DB_PORT=3308 TEST_DB_USER=root TEST_DB_PASSWORD=test pytest
"""

import json
import os
from datetime import date

from pathlib import Path

import pymysql
import pytest

from planner import db, seed
from planner.config import Settings
from planner.models import Completion

pytestmark = pytest.mark.skipif("TEST_DB_PASSWORD" not in os.environ, reason="no test database")

SEED = Path(__file__).resolve().parents[1] / "chores.json"
SEED_FIRST_ID = json.loads(SEED.read_text())["chores"][0]["id"]


@pytest.fixture
def settings():
    return Settings(
        db_host=os.environ.get("TEST_DB_HOST", "127.0.0.1"),
        db_port=int(os.environ.get("TEST_DB_PORT", "3308")),
        db_name=os.environ.get("TEST_DB_NAME", "chores_test"),
        db_user=os.environ.get("TEST_DB_USER", "root"),
        db_password=os.environ["TEST_DB_PASSWORD"],
    )


@pytest.fixture
def conn(settings):
    with db.connect(settings) as c:
        with c.cursor() as cur:
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
