"""Import a JSON catalog into the database, and export the database back to the same format
(see tests/fixtures/chores.json for the format)."""

import json
from datetime import date
from pathlib import Path

from planner import db
from planner.models import Chore, Completion, Person


EDITABLE = [c for c in db.CHORE_COLUMNS if c != "id"]


def import_json(conn, path: Path, today: date | None = None,
                update: bool = False, retire: bool = False) -> dict:
    """Load a seed file into the database.

    Always adds people and chores that don't exist yet. With `update`, existing ones are brought in line with
    the file (fields and needs). With `retire`, active chores missing from the file are retired (active = no),
    never deleted, so their history is kept.

    Chores marked "status": "done" get a completion dated today (the real date is unknown), unless they already
    have one.
    """
    data = json.loads(path.read_text())
    today = today or date.today()
    summary = {"people": 0, "people_updated": 0, "chores": 0, "updated": 0, "retired": 0,
               "completions": 0, "unchanged": 0}

    people = {p.id: p for p in db.get_people(conn)}
    for p in data.get("people", []):
        person = Person(p["id"], p["name"])           # any other keys (e.g. an old weekend_hours) are ignored
        if person.id not in people:
            db.add_person(conn, person)
            summary["people"] += 1
        elif update and person != people[person.id]:
            db.update_person(conn, person)
            summary["people_updated"] += 1

    existing = {c.id: c for c in db.get_chores(conn, include_inactive=True)}
    done = db.done_chore_ids(conn)
    in_file: list[Chore] = []
    for item in data["chores"]:
        chore = chore_from_json(item)
        in_file.append(chore)
        old = existing.get(chore.id)
        if old is None:
            db.add_chore(conn, chore)
            summary["chores"] += 1
        elif update and (changes := _changes(old, chore)):
            db.update_chore(conn, chore.id, changes)
            summary["updated"] += 1
        else:
            summary["unchanged"] += 1
        if item.get("status") == "done" and chore.id not in done and (old is None or update):
            db.record_completion(conn, Completion(chore.id, today, "done", "import"))
            summary["completions"] += 1

    # Needs last, so every referenced chore already exists.
    for chore in in_file:
        old = existing.get(chore.id)
        if old is None or (update and sorted(old.needs) != sorted(chore.needs)):
            db.set_needs(conn, chore.id, chore.needs)
            if old is not None and not _changes(old, chore):
                summary["updated"] += 1          # only the needs changed
                summary["unchanged"] -= 1

    if retire:
        file_ids = {c.id for c in in_file}
        for old in existing.values():
            if old.active and old.id not in file_ids:
                db.update_chore(conn, old.id, {"active": False})
                summary["retired"] += 1
    return summary


def _changes(old: Chore, new: Chore) -> dict:
    return {col: getattr(new, col) for col in EDITABLE if getattr(old, col) != getattr(new, col)}


def export_json(conn, path: Path) -> int:
    done = db.done_chore_ids(conn)
    chores = db.get_chores(conn, include_inactive=True)
    data = {
        "people": [
            {"id": p.id, "name": p.name}
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
