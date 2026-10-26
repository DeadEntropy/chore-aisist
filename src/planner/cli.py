"""The `planner` command. Thin wrappers around the other modules."""

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import pymysql

from planner import calendars, db, jobs, seed
from planner.config import load_calendar_settings, load_settings
from planner.models import Chore

NULLS = {"", "none", "null"}
TRUE_WORDS = {"1", "true", "yes", "y"}
FALSE_WORDS = {"0", "false", "no", "n"}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings()
        with db.connect(settings) as conn:
            return args.func(conn, args, settings) or 0
    except (ValueError, pymysql.err.MySQLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="planner", description="Household chore planner")
    sub = parser.add_subparsers(required=True, metavar="command")

    p = sub.add_parser("migrate", help="create or upgrade the database tables")
    p.set_defaults(func=cmd_migrate)

    p = sub.add_parser("import", help="load a seed file such as chores.json (adds missing chores)")
    p.add_argument("path", type=Path)
    p.add_argument("--update", action="store_true", help="also update existing chores and people from the file")
    p.add_argument("--retire", action="store_true", help="retire (not delete) chores missing from the file")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("export", help="write the database out as JSON")
    p.add_argument("path", type=Path)
    p.set_defaults(func=cmd_export)

    chore = sub.add_parser("chore", help="list, add or edit chores").add_subparsers(
        required=True, metavar="action")

    p = chore.add_parser("list", help="show the catalog")
    p.add_argument("--all", action="store_true", help="include retired chores")
    p.set_defaults(func=cmd_chore_list)

    p = chore.add_parser("add", help="add a chore: planner chore add <id> name=... category=... type=...")
    p.add_argument("id")
    p.add_argument("fields", nargs="+", metavar="key=value")
    p.set_defaults(func=cmd_chore_add)

    p = chore.add_parser("set", help="edit a chore: planner chore set <id> duration_h=3 needs=buy-caulk")
    p.add_argument("id")
    p.add_argument("fields", nargs="+", metavar="key=value")
    p.set_defaults(func=cmd_chore_set)

    p = sub.add_parser("plan", help="plan the coming weekend")
    p.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    p.add_argument("--date", type=date.fromisoformat, help="plan the weekend on/after this date (YYYY-MM-DD)")
    p.add_argument("--weeks", type=_positive_int, default=1, help="dry run: also plan the following N-1 weekends")
    p.add_argument("--no-calendar", action="store_true", help="ignore the family calendars (everyone free)")
    p.set_defaults(func=cmd_plan)
    return parser


# --- commands ---------------------------------------------------------------

def cmd_migrate(conn, args, settings):
    applied = db.migrate(conn)
    print("Applied: " + ", ".join(applied) if applied else "Database is up to date.")


def cmd_import(conn, args, settings):
    s = seed.import_json(conn, args.path, update=args.update, retire=args.retire)
    print(f"Chores: {s['chores']} added, {s['updated']} updated, {s['retired']} retired, {s['unchanged']} unchanged"
          f"{' (use --update to apply changes to existing chores)' if not args.update else ''}.")
    print(f"People: {s['people']} added, {s['people_updated']} updated. Completions recorded: {s['completions']}.")


def cmd_export(conn, args, settings):
    n = seed.export_json(conn, args.path)
    print(f"Exported {n} chores to {args.path}")


def cmd_chore_list(conn, args, settings):
    done = db.done_chore_ids(conn)
    chores = db.get_chores(conn, include_inactive=args.all)
    rows = [["ID", "TYPE", "EVERY (DAYS)", "HOURS", "PRIO", "WHO", "OUT", "NEEDS", "STATUS"]]
    for c in chores:
        every = (f"{c.interval_min_days}-{c.interval_target_days}-{c.interval_max_days}"
                 if c.interval_min_days is not None else "")
        status = "done" if c.type == "one_off" and c.id in done else ("retired" if not c.active else "")
        rows.append([c.id, c.type, every, _fmt(c.duration_h), _fmt(c.priority), c.assignee or "",
                     "yes" if c.outdoor else "", ",".join(c.needs), status])
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    for r in rows:
        print("  ".join(v.ljust(w) for v, w in zip(r, widths)).rstrip())
    print(f"\n{len(chores)} chores")


def cmd_chore_add(conn, args, settings):
    fields = parse_fields(args.fields)
    needs = fields.pop("needs", [])
    missing = {"name", "category", "type"} - set(fields)
    if missing:
        raise ValueError(f"Missing required field(s): {', '.join(sorted(missing))}")
    if db.get_chore(conn, args.id):
        raise ValueError(f"Chore '{args.id}' already exists; use 'chore set'")
    db.add_chore(conn, Chore(id=args.id, **fields))
    db.set_needs(conn, args.id, needs)
    print(f"Added {args.id}")


def cmd_chore_set(conn, args, settings):
    fields = parse_fields(args.fields)
    if db.get_chore(conn, args.id) is None:
        raise ValueError(f"No chore with id '{args.id}'")
    if "needs" in fields:
        db.set_needs(conn, args.id, fields.pop("needs"))
    db.update_chore(conn, args.id, fields)
    print(f"Updated {args.id}")


def cmd_plan(conn, args, settings):
    if not args.dry_run:
        raise ValueError("Writing plans to the calendar comes with calendar integration; use --dry-run")
    saturday = jobs.next_saturday(args.date or date.today())
    events, buffer = None, 0
    cal = None if args.no_calendar else load_calendar_settings()
    if args.no_calendar:
        print("Calendars ignored (--no-calendar): everyone is treated as free.\n")
    elif cal is None:
        print("Calendars not configured (CALDAV_URL not set): everyone is treated as free.\n")
    else:
        check_calendar_people(cal.people(), {p.id for p in db.get_people(conn)})
        start, end = jobs.calendar_range(saturday, args.weeks)
        try:
            events = calendars.fetch_events(cal, start, end)
        except Exception as exc:          # network, TLS, auth: report it plainly, don't plan on wrong data
            raise ValueError(f"Couldn't read the calendars ({type(exc).__name__}: {exc}). "
                             "Use --no-calendar to plan without them.") from exc
        buffer = cal.buffer_minutes

    shown: set[tuple[str, str]] = set()
    for result in jobs.dry_run(conn, saturday, args.weeks, events, buffer):
        new = [(c, why) for c, why in result.excluded if (c.id, why) not in shown]   # each reason once
        shown.update((c.id, why) for c, why in new)
        print_plan(result, new)


def check_calendar_people(calendar_people: set[str], known: set[str]) -> None:
    """Stop if BUSY_CALENDARS names someone who isn't a person in the database: their events would be ignored."""
    unknown = sorted(calendar_people - known)
    if unknown:
        raise ValueError(f"BUSY_CALENDARS names unknown people: {', '.join(unknown)} "
                         f"(known: {', '.join(sorted(known))})")


def print_plan(result, excluded) -> None:
    plan = result.plan
    print(f"=== Weekend of {plan.saturday:%a %d %b %Y}")
    for e in result.events:
        who = " & ".join(p.capitalize() for p in e.people)
        if e.travel:
            last = (e.end - timedelta(days=1)).date()
            print(f"  Away: {e.start:%a %d %b}–{last:%a %d %b}  {e.summary}  ({who})")
        elif e.end.date() == e.start.date():
            print(f"  Busy: {e.start:%a %H:%M}-{e.end:%H:%M}  {e.summary}  ({who})")
        else:
            print(f"  Busy: {e.start:%a %H:%M} – {e.end:%a %H:%M}  {e.summary}  ({who})")
    if result.blocked:
        print("  Nobody has free time this weekend: nothing planned\n")
        return
    if result.next_available:
        print(f"  Look-ahead: nobody free until the weekend of {result.next_available:%a %d %b}, so chores that "
              "would be overdue by then are brought forward")
    hours: dict[str, float] = {}
    day = None
    for item in plan.items:
        if item.start.date() != day:
            day = item.start.date()
            print(f"  {day:%A}")
        who = " & ".join(p.capitalize() for p in item.people)
        print(f"    {item.start:%H:%M}-{item.end:%H:%M}  {item.chore.name}  ({who})  score {item.score:g}")
        for p in item.people:
            hours[p] = hours.get(p, 0) + item.chore.duration_h
    if not plan.items:
        print("  nothing planned")
    if hours:
        print("  Hours: " + ", ".join(f"{p.capitalize()} {h:g}" for p, h in sorted(hours.items())))
    if plan.unplanned:
        print("  Didn't fit: " + "; ".join(f"{c.id} ({why})" for c, why in plan.unplanned))
    for chore, why in excluded:
        print(f"  Not eligible: {chore.id} ({why})")
    print()


# --- helpers ----------------------------------------------------------------

def _positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def parse_fields(pairs: list[str]) -> dict:
    """Turn ['duration_h=1.5', 'interval=7,10,14', 'needs=a,b'] into chore column values."""
    out: dict = {}
    for pair in pairs:
        key, sep, raw = pair.partition("=")
        if not sep:
            raise ValueError(f"Expected key=value, got '{pair}'")
        value = raw.strip()
        empty = value.lower() in NULLS
        if key == "interval":
            parts = [] if empty else [int(x) for x in value.split(",")]
            if len(parts) not in (0, 3):
                raise ValueError("interval must be min,target,max (or none)")
            out["interval_min_days"], out["interval_target_days"], out["interval_max_days"] = (
                parts or [None, None, None])
        elif key == "needs":
            out["needs"] = [] if empty else [x.strip() for x in value.split(",")]
        elif key in ("outdoor", "active"):
            flag = value.lower()
            if flag in TRUE_WORDS:
                out[key] = True
            elif flag in FALSE_WORDS:
                out[key] = False
            else:
                raise ValueError(f"{key} must be yes or no, got '{raw}'")
        elif key == "duration_h":
            out[key] = None if empty else float(value)
        elif key == "priority":
            out[key] = None if empty else int(value)
        elif key in ("name", "category", "type", "assignee"):
            out[key] = None if empty else value
        else:
            raise ValueError(f"Unknown field '{key}'")
    return out


def _fmt(value) -> str:
    if value is None:
        return ""
    return f"{value:g}" if isinstance(value, float) else str(value)


if __name__ == "__main__":
    sys.exit(main())
