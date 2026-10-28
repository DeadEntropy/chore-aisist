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


def test_backup_command_reports_file_errors_cleanly(monkeypatch, tmp_path):
    from planner import cli
    from planner.config import BackupSettings
    monkeypatch.setattr(cli, "load_backup_settings", lambda: BackupSettings(str(tmp_path), 30))

    def unwritable(*args):
        raise PermissionError(13, "Permission denied", str(tmp_path))
    monkeypatch.setattr(cli.backup, "dump", unwritable)
    with pytest.raises(ValueError, match="Backup failed: PermissionError"):
        cli.cmd_backup(None, None, None)


def test_parse_description():
    assert parse_fields(["description=Brown mulch\\n70 bags"]) == {"description": "Brown mulch\n70 bags"}
    assert parse_fields(["description=none"]) == {"description": None}


def test_a_failed_calendar_refresh_after_an_edit_is_an_error(monkeypatch):
    import pytest

    from planner import cli

    def fail(conn, cal, today):
        raise cli.jobs.CalendarSyncError("ConnectionError: down")
    monkeypatch.setattr(cli, "load_calendar_settings", lambda: object())
    monkeypatch.setattr(cli.jobs, "refresh_calendar", fail)
    with pytest.raises(ValueError, match="Saved, but couldn't update the Chores calendar"):
        cli._refresh_calendar(None)
