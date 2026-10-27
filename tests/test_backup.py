import gzip
import os
import stat
from datetime import date, datetime

import pytest

from planner import backup
from planner.config import Settings

SETTINGS = Settings("db", 3306, "chores", "chore_aisist", "s3c#ret\"x")     # a # and a " must survive


def touch(folder, name):
    (folder / name).write_bytes(b"")
    return folder / name


def test_backups_are_listed_by_name_and_other_files_ignored(tmp_path):
    touch(tmp_path, "chores-20261025-2000.sql.gz")
    touch(tmp_path, "chores-20261026-2000.sql.gz")
    touch(tmp_path, "notes.txt")
    touch(tmp_path, "chores-20261027-2000.sql.gz.partial")
    assert [w for w, _ in backup.backups(tmp_path)] == [datetime(2026, 10, 25, 20), datetime(2026, 10, 26, 20)]
    assert backup.latest(tmp_path) == datetime(2026, 10, 26, 20)
    assert backup.latest(tmp_path / "missing") is None


def test_prune_keeps_recent_ones_and_always_the_newest(tmp_path):
    found = [(datetime(2026, 9, 1, 20), tmp_path / "a"), (datetime(2026, 10, 20, 20), tmp_path / "b")]
    assert backup.to_prune(found, 30, date(2026, 10, 27)) == [tmp_path / "a"]
    assert backup.to_prune(found[:1], 30, date(2026, 10, 27)) == []          # the only backup is never deleted


@pytest.fixture
def fake_dump(tmp_path, monkeypatch):
    """A stand-in mariadb-dump on PATH that records its arguments and the password it was given."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "mariadb-dump"
    script.write_text('#!/bin/sh\n'
                      'echo "$@" > "$FAKE_DIR/args"\n'
                      'printf "%s" "$MYSQL_PWD" > "$FAKE_DIR/password"\n'
                      'if [ -n "$FAKE_HANG" ]; then sleep 5; fi\n'
                      'if [ -n "$FAKE_FAIL" ]; then echo "Access denied" >&2; exit 2; fi\n'
                      'echo "CREATE TABLE chore (id INT);"\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_DIR", str(tmp_path))
    return tmp_path


def test_dump_writes_a_gzipped_file_without_the_password_on_the_command_line(fake_dump):
    out = fake_dump / "backups"
    old = touch(out.mkdir() or out, "chores-20260901-2000.sql.gz")
    path = backup.dump(SETTINGS, out, datetime(2026, 10, 27, 20, 0), keep_days=30)
    assert path.name == "chores-20261027-2000.sql.gz"
    assert gzip.decompress(path.read_bytes()) == b"CREATE TABLE chore (id INT);\n"
    args = (fake_dump / "args").read_text()
    assert "s3c" not in args and "--single-transaction" in args and args.strip().endswith("chores")
    assert "--user=chore_aisist" in args and "--host=db" in args
    assert (fake_dump / "password").read_text() == SETTINGS.db_password  # the whole password, # and " included
    assert not old.exists()                                           # pruned: older than 30 days


def test_a_failed_dump_leaves_no_file(fake_dump, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL", "1")
    out = fake_dump / "backups"
    with pytest.raises(ValueError, match="Access denied"):
        backup.dump(SETTINGS, out, datetime(2026, 10, 27, 20, 0), keep_days=30)
    assert list(out.iterdir()) == []


def test_a_hung_dump_is_stopped(fake_dump, monkeypatch):
    monkeypatch.setenv("FAKE_HANG", "1")
    monkeypatch.setattr(backup, "DUMP_TIMEOUT", 0.5)
    out = fake_dump / "backups"
    with pytest.raises(ValueError, match="took longer than"):
        backup.dump(SETTINGS, out, datetime(2026, 10, 27, 20, 0), keep_days=30)
    assert list(out.iterdir()) == []
