"""Import chores.json into the database, and export the database back to the same format."""

import json
from datetime import date
from pathlib import Path

from planner import db
from planner.models import Chore, Completion, Person


def import_json(conn, path: Path, today: date | None = None) -> dict:
    """Add people and chores that don't exist yet. Existing rows are left untouched.

    Chores marked "status": "done" get a completion dated today (the real date is unknown).
    """
    data = json.loads(path.read_text())
    today = today or date.today()
    summary = {"people": 0, "chores": 0, "completions": 0, "skipped_existing": 0}

    for p in data.get("people", []):
        if db.add_person(conn, Person(p["id"], p["name"], p["weekend_hours"])):
            summary["people"] += 1

    existing = {c.id for c in db.get_chores(conn, include_inactive=True)}
    added: list[Chore] = []
    for item in data["chores"]:
        chore = chore_from_json(item)
        if chore.id in existing:
            summary["skipped_existing"] += 1
            continue
        db.add_chore(conn, chore)
        added.append(chore)
        summary["chores"] += 1
        if item.get("status") == "done":
            db.record_completion(conn, Completion(chore.id, today, "done", "import"))
            summary["completions"] += 1

    # Needs last, so every referenced chore already exists.
    for chore in added:
        if chore.needs:
            db.set_needs(conn, chore.id, chore.needs)
    return summary


def export_json(conn, path: Path) -> int:
    done = db.done_chore_ids(conn)
    chores = db.get_chores(conn, include_inactive=True)
    data = {
        "people": [
            {"id": p.id, "name": p.name, "weekend_hours": p.weekend_hours}
            for p in db.get_people(conn)
        ],
        "chores": [chore_to_json(c, done) for c in chores],
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return len(chores)


def chore_from_json(item: dict) -> Chore:
    interval = item.get("interval_days") or {}
    return Chore(
        id=item["id"],
        name=item["name"],
        category=item["category"],
        type=item["type"],
        interval_min_days=interval.get("min"),
        interval_target_days=interval.get("target"),
        interval_max_days=interval.get("max"),
        duration_h=item.get("duration_h"),
        priority=item.get("priority"),
        assignee=item.get("assignee"),
        outdoor=bool(item.get("outdoor", False)),
        needs=list(item.get("needs", [])),
        active=bool(item.get("active", True)),
    )


def chore_to_json(chore: Chore, done_ids: set[str]) -> dict:
    has_interval = chore.interval_min_days is not None
    return {
        "id": chore.id,
        "name": chore.name,
        "category": chore.category,
        "type": chore.type,
        "status": "done" if chore.type == "one_off" and chore.id in done_ids else "open",
        "interval_days": {
            "min": chore.interval_min_days,
            "target": chore.interval_target_days,
            "max": chore.interval_max_days,
        } if has_interval else None,
        "duration_h": chore.duration_h,
        "priority": chore.priority,
        "assignee": chore.assignee,
        "outdoor": chore.outdoor,
        "needs": chore.needs,
        **({} if chore.active else {"active": False}),
    }
