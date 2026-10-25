"""Settings, read once from environment variables (a local `.env` is loaded if present)."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str


def load_settings() -> Settings:
    load_dotenv()
    password = os.environ.get("DB_PASSWORD")
    if not password:
        raise ValueError("DB_PASSWORD is not set (see .env.example)")
    return Settings(
        db_host=os.environ.get("DB_HOST", "127.0.0.1"),
        db_port=int(os.environ.get("DB_PORT", "3307")),
        db_name=os.environ.get("DB_NAME", "chores"),
        db_user=os.environ.get("DB_USER", "chore_aisist"),
        db_password=password,
    )
