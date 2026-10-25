"""Database round-trip tests. Need a throwaway MariaDB; skipped unless TEST_DB_PASSWORD is set.

    docker run -d --name chore-test-db -e MARIADB_ROOT_PASSWORD=test -e MARIADB_DATABASE=chores_test \
        -p 127.0.0.1:3308:3306 mariadb:11
    TEST_DB_PORT=3308 TEST_DB_USER=root TEST_DB_PASSWORD=test pytest
"""

import json
import os
from datetime import date

import pytest

from planner import db, seed
from planner.config import REPO_ROOT, Settings
from planner.models import Completion

pytestmark = pytest.mark.skipif("TEST_DB_PASSWORD" not in os.environ, reason="no test database")

SEED = REPO_ROOT / "chores.json"


@pytest.fixture
def settings():
    return Settings(
        db_host=os.environ.get("TEST_DB_HOST", "127.0.0.1"),
        db_port=int(os.environ.get("TEST_DB_PORT", "3308")),
        db_name=os.environ.get("TEST_DB_NAME", "chores_test"),
        db_user=os.environ.get("TEST_DB_USER", "root"),
        db_password=os.environ["TEST_DB_PASSWORD"],
        migrations_dir=REPO_ROOT / "migrations",
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
        db.migrate(c, settings.migrations_dir)
        yield c


def test_migrate_is_idempotent(conn, settings):
    assert db.migrate(conn, settings.migrations_dir) == []


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
    with pytest.raises(Exception):
        db.update_chore(conn, "weed-pool-deck", {"priority": 9})
    with pytest.raises(Exception):
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
