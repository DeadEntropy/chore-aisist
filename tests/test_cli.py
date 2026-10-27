from datetime import date

import pytest

from planner.cli import build_parser, parse_fields


def test_parse_fields():
    assert parse_fields(["interval=7,10,14", "duration_h=1.5", "outdoor=yes", "needs=a,b", "assignee=none"]) == {
        "interval_min_days": 7, "interval_target_days": 10, "interval_max_days": 14,
        "duration_h": 1.5, "outdoor": True, "needs": ["a", "b"], "assignee": None,
    }


def test_parse_fields_rejects_unknown_and_bad_interval():
    with pytest.raises(ValueError):
        parse_fields(["notes=hello"])
    with pytest.raises(ValueError):
        parse_fields(["interval=7,10"])


def test_parse_fields_not_before():
    assert parse_fields(["not_before=2026-12-01"]) == {"not_before": date(2026, 12, 1)}
    assert parse_fields(["not_before=none"]) == {"not_before": None}
    with pytest.raises(ValueError):
        parse_fields(["not_before=early december"])


def test_parse_fields_booleans():
    assert parse_fields(["outdoor=no", "active=YES"]) == {"outdoor": False, "active": True}
    with pytest.raises(ValueError):
        parse_fields(["active=ture"])


@pytest.mark.parametrize("weeks", ["0", "-2"])
def test_plan_rejects_nonpositive_weeks(weeks):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["plan", "--dry-run", "--weeks", weeks])


def test_plan_accepts_weeks():
    assert build_parser().parse_args(["plan", "--dry-run", "--weeks", "3"]).weeks == 3


def test_blocked_weekend_still_lists_exclusions(capsys):
    from datetime import date

    from planner.cli import print_plan
    from planner.jobs import PeriodResult
    from planner.models import Chore, Plan

    walkway = Chore(id="walkway", name="walkway", category="garden", type="one_off")
    result = PeriodResult(Plan(date(2026, 11, 7), [date(2026, 11, 7)], [], []), [], blocked=True)
    print_plan(result, [(walkway, "needs details: duration_h")])
    out = capsys.readouterr().out
    assert "Nobody has free chore time" in out and "Not eligible: walkway (needs details: duration_h)" in out


def test_calendar_table_shows_use_and_missing_shares():
    from planner.cli import calendar_table

    table = calendar_table([("chores", "Chores"), ("work_shared_by_x", "Work")],
                           {"work_shared_by_x": ("nicolas",), "gone_shared_by_x": ("anna",)})
    rows = {line.split()[0]: line.split(None, 2)[1:] for line in table.splitlines()[1:]}
    assert rows["work_shared_by_x"] == ["Work", "nicolas"]
    assert rows["chores"] == ["Chores", "(not used)"]
    assert rows["gone_shared_by_x"][0] == "?" and "check the share" in rows["gone_shared_by_x"][1]


@pytest.mark.parametrize("flags", [["--date", "2026-10-31"], ["--weeks", "2"], ["--no-calendar"]])
def test_real_plan_rejects_dry_run_only_flags(flags):
    from planner.cli import cmd_plan

    args = build_parser().parse_args(["plan", *flags])
    with pytest.raises(ValueError, match="only works? with --dry-run"):
        cmd_plan(None, args, None)


def test_serve_refuses_to_start_without_calendars(monkeypatch):
    from planner import cli
    from planner.availability import parse_window, parse_windows
    from planner.config import PlanningSettings
    monkeypatch.setattr(cli, "load_plan_times", lambda: ())
    monkeypatch.setattr(cli, "load_planning_settings",
                        lambda: PlanningSettings(parse_windows("sat 08:00-18:00"), parse_window("08:00-18:00")))
    monkeypatch.setattr(cli, "load_calendar_settings", lambda: None)
    with pytest.raises(ValueError, match="CALDAV_URL not set"):
        cli.cmd_serve(None, None, None)


def test_scheduled_backup_once_per_scheduled_time_and_never_fatal(tmp_path, monkeypatch, capsys):
    from datetime import datetime, time

    from planner import cli
    from planner.backup import backup_name
    from planner.config import BackupSettings
    made = []
    monkeypatch.setattr(cli.backup, "dump", lambda s, folder, now, keep: made.append(now) or folder / backup_name(now))
    backups, times = BackupSettings(str(tmp_path), 30), (time(20, 0),)
    now = datetime(2026, 10, 27, 20, 0, 4)

    (tmp_path / backup_name(datetime(2026, 10, 26, 20, 0))).write_bytes(b"")          # yesterday's
    cli.scheduled_backup(None, backups, now, times)
    assert made == [now]

    (tmp_path / backup_name(now)).write_bytes(b"")                                    # a retry at 20:15
    cli.scheduled_backup(None, backups, datetime(2026, 10, 27, 20, 15), times)
    assert made == [now]

    def broken(*args):
        raise ValueError("disk full")
    monkeypatch.setattr(cli.backup, "dump", broken)
    cli.scheduled_backup(None, backups, datetime(2026, 10, 28, 20, 0), times)          # logged, not raised
    assert "Backup failed: ValueError: disk full" in capsys.readouterr().err
