"""The `planner` command. Thin wrappers around the other modules."""

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pymysql

from planner import backup, calendars, daemon, db, jobs, seed
from planner.config import load_backup_settings, load_calendar_settings, load_planning_settings, load_settings
from planner.models import Chore

NULLS = {"", "none", "null"}
TRUE_WORDS = {"1", "true", "yes", "y"}
FALSE_WORDS = {"0", "false", "no", "n"}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings()
        if getattr(args, "own_connection", False):      # long-running: opens a connection per run
            return args.func(None, args, settings) or 0
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

    p = sub.add_parser("import", help="load chores from a JSON file (adds missing chores)")
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

    p = chore.add_parser("set", help="edit a chore: planner chore set <id> duration_h=3 needs=buy-caulk not_before=2026-12-01")
    p.add_argument("id")
    p.add_argument("fields", nargs="+", metavar="key=value")
    p.set_defaults(func=cmd_chore_set)

    p = sub.add_parser("calendars", help="list the calendars the app can read, and who each one blocks")
    p.set_defaults(func=cmd_calendars)

    p = sub.add_parser("plan", help="re-plan from now to the end of the 7 days and update the Chores calendar")
    p.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    p.add_argument("--date", type=date.fromisoformat, help="dry run: first day to plan (YYYY-MM-DD); default now")
    p.add_argument("--weeks", type=_positive_int, default=1, help="dry run: plan N consecutive 7-day periods")
    p.add_argument("--no-calendar", action="store_true", help="dry run: ignore the family calendars (everyone free)")
    p.set_defaults(func=cmd_plan)

    for name, verb in (("done", "was done"), ("skip", "was skipped")):
        p = sub.add_parser(name, help=f"record that a chore {verb}")
        p.add_argument("chore", help="chore id, see `planner chore list`")
        p.add_argument("--date", type=date.fromisoformat, help="when (YYYY-MM-DD); default today")
        if name == "done":
            p.add_argument("--by", help="who did it, e.g. nicolas or nicolas,anna; default: who it was planned for")
        p.set_defaults(func=cmd_report, outcome=name)

    p = sub.add_parser("status", help="recent and upcoming planned chores, and what's overdue")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("serve", help="run the scheduler: re-plan every day at PLAN_TIMES (the container's command)")
    p.set_defaults(func=cmd_serve, own_connection=True)

    p = sub.add_parser("health", help="exit 0 if a planning run (and a backup) succeeded recently (Docker healthcheck)")
    p.set_defaults(func=cmd_health)

    p = sub.add_parser("backup", help="dump the database to BACKUP_DIR now (also done before every scheduled run)")
    p.set_defaults(func=cmd_backup, own_connection=True)
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
    rows = [["ID", "TYPE", "EVERY (DAYS)", "HOURS", "PRIO", "WHO", "OUT", "NEEDS", "FROM", "STATUS"]]
    for c in chores:
        every = (f"{c.interval_min_days}-{c.interval_target_days}-{c.interval_max_days}"
                 if c.interval_min_days is not None else "")
        status = "done" if c.type == "one_off" and c.id in done else ("retired" if not c.active else "")
        rows.append([c.id, c.type, every, _fmt(c.duration_h), _fmt(c.priority), c.assignee or "",
                     "yes" if c.outdoor else "", ",".join(c.needs),
                     c.not_before.isoformat() if c.not_before else "", status])
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


def cmd_calendars(conn, args, settings):
    cal = load_calendar_settings()
    if cal is None:
        raise ValueError("Calendars not configured (CALDAV_URL not set)")
    try:
        found = calendars.list_calendars(cal)
    except Exception as exc:
        raise ValueError(f"Couldn't read the calendars ({type(exc).__name__}: {exc})") from exc
    print(calendar_table(found, dict(cal.calendars)))


def calendar_table(found: list[tuple[str, str]], configured: dict[str, tuple[str, ...]]) -> str:
    rows = [["URI (use this in BUSY_CALENDARS)", "NAME", "BLOCKS"]]
    for uri, name in found:
        rows.append([uri, name, ", ".join(configured[uri]) if uri in configured else "(not used)"])
    for uri in configured:
        if uri not in dict(found):
            rows.append([uri, "?", "in BUSY_CALENDARS but not visible to the app: check the share"])
    widths = [max(len(r[i]) for r in rows) for i in range(2)]
    return "\n".join(f"{r[0].ljust(widths[0])}  {r[1].ljust(widths[1])}  {r[2]}" for r in rows)


def cmd_plan(conn, args, settings):
    planning = load_planning_settings()
    if not args.dry_run:
        dry_only = [flag for flag, used in (("--date", args.date), ("--weeks", args.weeks != 1),
                                            ("--no-calendar", args.no_calendar)) if used]
        if dry_only:
            verb = "works" if len(dry_only) == 1 else "work"
            raise ValueError(f"{', '.join(dry_only)} only {verb} with --dry-run: a real run always plans from now")
        summary = jobs.plan_now(conn, load_calendar_settings(), planning, "manual")
        print_plan(summary.result, summary.result.excluded)
        print_run(summary, "Saved")
        return
    start = args.date or date.today()
    events, buffer = [], 0
    cal = None if args.no_calendar else load_calendar_settings()
    if args.no_calendar:
        print("Calendars ignored (--no-calendar): only the default chore windows, everyone free.\n")
    elif cal is None:
        print("Calendars not configured (CALDAV_URL not set): only the default chore windows, everyone free.\n")
    else:
        jobs.check_calendar_people(cal.people(), {p.id for p in db.get_people(conn)})
        first, last = jobs.calendar_range(start, args.weeks)
        try:
            events = calendars.fetch_events(cal, first, last)
        except Exception as exc:          # network, TLS, auth: report it plainly, don't plan on wrong data
            raise ValueError(f"Couldn't read the calendars ({type(exc).__name__}: {exc}). "
                             "Use --no-calendar to plan without them.") from exc
        buffer = cal.buffer_minutes

    shown: set[tuple[str, str]] = set()
    for result in jobs.dry_run(conn, start, args.weeks, events, buffer, planning, datetime.now()):
        new = [(c, why) for c, why in result.excluded if (c.id, why) not in shown]   # each reason once
        shown.update((c.id, why) for c, why in new)
        print_plan(result, new)


def print_run(summary, verb: str) -> None:
    print(f"{verb}: {jobs.describe(summary)}")
    if summary.changes.dropped:
        print("Not planned again today (already done or skipped that day): "
              + ", ".join(i.chore.id for i in summary.changes.dropped))


def cmd_report(conn, args, settings):
    day = args.date or date.today()
    by = [p.strip() for p in args.by.split(",") if p.strip()] if getattr(args, "by", None) else None
    cal = load_calendar_settings()
    try:
        record = jobs.report(conn, cal, args.chore, args.outcome, day, by)
    except jobs.CalendarSyncError as exc:
        raise ValueError(f"Recorded, but couldn't update the Chores calendar ({exc}); "
                         "the next `planner plan` will fix it") from exc
    verb = "done" if args.outcome == "done" else "skipped"
    where = f" (planned {record.start:%a %d %b %H:%M})" if record else " (it wasn't in the plan)"
    print(f"Recorded {args.chore} as {verb} on {day:%a %d %b}{where}.")
    if cal is None:
        print("Calendars not configured: the Chores calendar wasn't updated or re-planned.")
        return
    print_run(jobs.plan_now(conn, cal, load_planning_settings(), "report"), "Re-planned")   # reporting re-plans


def cmd_status(conn, args, settings):
    from planner.plans import STATUS_MARK
    from planner.scoring import score_chores

    today = date.today()
    names = {c.id: c.name for c in db.get_chores(conn, include_inactive=True)}
    records = db.planned_between(conn, datetime.combine(today - timedelta(days=7), datetime.min.time()),
                                 datetime.combine(today + timedelta(days=8), datetime.min.time()))
    day = None
    for r in records:
        if r.start.date() != day:
            day = r.start.date()
            print(f"{day:%A %d %b}" + ("  (today)" if day == today else ""))
        who = " & ".join(p.capitalize() for p in r.people)
        print(f"  {STATUS_MARK[r.status]} {r.start:%H:%M}-{r.end:%H:%M}  {names.get(r.chore_id, r.chore_id)}"
              f"  ({who})  [{r.chore_id}]")
    if not records:
        print("Nothing planned in the last or next 7 days.")
    upcoming = {r.chore_id for r in records if r.status == "open" and r.start.date() >= today}
    scored, _ = score_chores(db.get_chores(conn), db.last_done_dates(conn), today)
    overdue = [s for s in scored if s.chore.type == "recurring" and s.urgency > 1 and s.chore.id not in upcoming]
    if overdue:
        print("\nOverdue and not planned: " + ", ".join(f"{s.chore.id} ({s.urgency:.1f})" for s in overdue))
    if run := db.last_run(conn, trigger="schedule"):
        print(f"\nLast scheduled run: {run.started_at:%a %d %b %H:%M}, "
              + ("ok" if run.outcome == "ok" else f"failed: {run.message}"))


def cmd_serve(conn, args, settings):
    daemon.serve(settings)


def cmd_backup(conn, args, settings):
    backups = load_backup_settings()
    if backups is None:
        raise ValueError("BACKUP_DIR is not set")
    try:
        target = backup.dump(settings, Path(backups.folder), datetime.now(), backups.keep_days)
    except OSError as exc:                     # unwritable folder, mariadb-dump not installed
        raise ValueError(f"Backup failed: {type(exc).__name__}: {exc}") from exc
    print(f"Backed up to {target}")


def cmd_health(conn, args, settings):
    problems, summary = jobs.health(conn, datetime.now(), load_backup_settings())
    print(("unhealthy: " + "; ".join(problems)) if problems else f"ok: {summary}")
    return 1 if problems else 0


EVENT_LABELS = {"busy": "Busy", "away": "Away", "no_chores": "No chores", "extra_day": "Day off",
                "extra_time": "Chore time"}


def print_plan(result, excluded) -> None:
    plan = result.plan
    print(f"=== {plan.days[0]:%a %d %b} – {plan.days[-1]:%a %d %b %Y}")
    for e in result.events:
        who = " & ".join(p.capitalize() for p in e.people)
        label = EVENT_LABELS.get(e.kind, e.kind)
        if e.kind in ("away", "no_chores", "extra_day"):
            last = (e.end - timedelta(days=1)).date()
            when = f"{e.start:%a %d %b}" + (f"–{last:%a %d %b}" if last != e.start.date() else "")
        elif e.end.date() == e.start.date():
            when = f"{e.start:%a %d %b %H:%M}-{e.end:%H:%M}"
        else:
            when = f"{e.start:%a %d %b %H:%M} – {e.end:%a %d %b %H:%M}"
        print(f"  {label}: {when}  {e.summary}  ({who})")
    if result.blocked:
        print("  Nobody has free chore time in this period: nothing planned")
        _print_excluded(excluded)
        print()
        return
    if result.next_available:
        print(f"  Look-ahead: no chore time again until {result.next_available:%a %d %b}, so chores that "
              "would be overdue by then are brought forward")
    hours: dict[str, float] = {}
    day = None
    for item in plan.items:
        if item.start.date() != day:
            day = item.start.date()
            print(f"  {day:%A %d %b}")
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
    _print_excluded(excluded)
    print()


def _print_excluded(excluded) -> None:
    for chore, why in excluded:
        print(f"  Not eligible: {chore.id} ({why})")


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
        elif key == "not_before":
            out[key] = None if empty else date.fromisoformat(value)
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
