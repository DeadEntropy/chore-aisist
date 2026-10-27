"""Database backups: a gzipped `mariadb-dump` per day, kept for BACKUP_KEEP_DAYS days.

The scheduler makes one before each scheduled re-plan (unless one was already made since the last scheduled time),
and `planner backup` makes one on demand (e.g. before `migrate`). Dumps run `mariadb-dump --single-transaction`,
so they're consistent without locking the planner out. The password goes through a temporary option file, never the
command line.
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
    with tempfile.NamedTemporaryFile("w", suffix=".cnf", delete=False) as options:
        os.chmod(options.name, 0o600)
        options.write(f"[client]\nuser={settings.db_user}\npassword={settings.db_password}\n"
                      f"host={settings.db_host}\nport={settings.db_port}\n")
    try:
        with gzip.open(partial, "wb") as out:
            proc = subprocess.Popen(
                ["mariadb-dump", f"--defaults-extra-file={options.name}", "--single-transaction",
                 "--skip-lock-tables", settings.db_name],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            shutil.copyfileobj(proc.stdout, out)
            error = proc.stderr.read().decode(errors="replace").strip()
            if proc.wait() != 0:
                raise ValueError(f"mariadb-dump failed: {error or f'exit code {proc.returncode}'}")
        partial.replace(target)                     # only complete dumps get the real name
    finally:
        os.unlink(options.name)
        partial.unlink(missing_ok=True)
    for path in to_prune(backups(folder), keep_days, now.date()):
        path.unlink()
    return target
