"""Settings, read once from environment variables (a local `.env` is loaded if present)."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str
    migrations_dir: Path


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        db_host=os.environ.get("DB_HOST", "127.0.0.1"),
        db_port=int(os.environ.get("DB_PORT", "3307")),
        db_name=os.environ.get("DB_NAME", "chores"),
        db_user=os.environ.get("DB_USER", "chore_aisist"),
        db_password=os.environ["DB_PASSWORD"],
        migrations_dir=Path(os.environ.get("MIGRATIONS_DIR", REPO_ROOT / "migrations")),
    )
