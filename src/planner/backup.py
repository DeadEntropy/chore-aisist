"""Database backups: a gzipped `mariadb-dump` per day, kept for BACKUP_KEEP_DAYS days.

The scheduler makes one before each scheduled re-plan (unless one was already made since the last scheduled time),
and `planner backup` makes one on demand (e.g. before `migrate`). Dumps run `mariadb-dump --single-transaction`,
so they're consistent without locking the planner out. The password goes through the MYSQL_PWD environment variable:
never the command line, and nothing to escape (an option file would read a `#` in it as a comment). A dump that takes
longer than DUMP_TIMEOUT is stopped, so a hung dump can't hang the scheduler.
"""

import gzip
import os
import re
import shutil
import subprocess
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

from planner.config import Settings

NAME = re.compile(r"^chores-(\d{8})-(\d{4})\.sql\.gz$")
DUMP_TIMEOUT = 600          # seconds; today's dump takes well under one


def backup_name(now: datetime) -> str:
    return f"chores-{now:%Y%m%d-%H%M}.sql.gz"


def backups(folder: Path) -> list[tuple[datetime, Path]]:
    """The planner's backups in `folder`, oldest first (other files are ignored)."""
    found = []
    for path in folder.iterdir() if folder.is_dir() else []:
        if m := NAME.match(path.name):
            found.append((datetime.strptime(m[1] + m[2], "%Y%m%d%H%M"), path))
    return sorted(found)


def latest(folder: Path) -> datetime | None:
    found = backups(folder)
    return found[-1][0] if found else None


def to_prune(found: list[tuple[datetime, Path]], keep_days: int, today: date) -> list[Path]:
    """Backups older than `keep_days`, never the newest one."""
    cutoff = today - timedelta(days=keep_days)
    return [path for when, path in found[:-1] if when.date() < cutoff]


def dump(settings: Settings, folder: Path, now: datetime, keep_days: int) -> Path:
    """Write a gzipped dump of the database to `folder`, then delete backups older than `keep_days`."""
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / backup_name(now)
    partial = target.with_name(target.name + ".partial")
    command = ["mariadb-dump", f"--host={settings.db_host}", f"--port={settings.db_port}",
               f"--user={settings.db_user}", "--single-transaction", "--skip-lock-tables", settings.db_name]
    try:
        with tempfile.TemporaryFile() as raw:
            try:
                # stdout to a file and stderr through communicate(): neither pipe can fill up and block the dump
                # MYSQL_PWD rather than an option file (decided in #9): the child's environment is readable only by
                # this user, and this process already holds the same password in its own environment (DB_PASSWORD),
                # so nothing new is exposed; an option file would need quoting for #, \ and " in the password.
                proc = subprocess.run(command, stdout=raw, stderr=subprocess.PIPE, timeout=DUMP_TIMEOUT,
                                      env={**os.environ, "MYSQL_PWD": settings.db_password})
            except subprocess.TimeoutExpired:
                raise ValueError(f"mariadb-dump took longer than {DUMP_TIMEOUT} s and was stopped") from None
            if proc.returncode != 0:
                error = proc.stderr.decode(errors="replace").strip()
                raise ValueError(f"mariadb-dump failed: {error or f'exit code {proc.returncode}'}")
            raw.seek(0)
            with gzip.open(partial, "wb") as out:
                shutil.copyfileobj(raw, out)
        partial.replace(target)                     # only complete dumps get the real name
    finally:
        partial.unlink(missing_ok=True)
    for path in to_prune(backups(folder), keep_days, now.date()):
        path.unlink()
    return target
