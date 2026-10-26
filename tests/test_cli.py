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
