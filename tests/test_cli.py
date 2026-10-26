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
