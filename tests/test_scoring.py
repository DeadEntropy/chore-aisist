from datetime import date, timedelta

import pytest

from planner.models import Chore
from planner.scoring import score_chores, urgency_one_off, urgency_recurring

TODAY = date(2026, 10, 31)


def recurring(id="mow", lo=7, mid=10, hi=14, **kw):
    return Chore(id=id, name=id, category="garden", type="recurring", interval_min_days=lo,
                 interval_target_days=mid, interval_max_days=hi, duration_h=1, priority=3,
                 assignee="either", **kw)


def one_off(id="fix", created=TODAY, **kw):
    kw = {"duration_h": 1, "priority": 3, "assignee": "either", **kw}
    return Chore(id=id, name=id, category="home", type="one_off", created_on=created, **kw)


def ago(days):
    return TODAY - timedelta(days=days)


@pytest.mark.parametrize("days, expected", [
    (6, None),      # before min: not due
    (7, 0.0),       # min
    (10, 0.5),      # target
    (12, 0.75),
    (14, 1.0),      # max
    (21, 2.0),      # overdue keeps growing: 1 + (21-14)/(14-7)
])
def test_recurring_window(days, expected):
    u = urgency_recurring(recurring(), ago(days), TODAY)
    if expected is None:
        assert u is None
    else:
        assert u == pytest.approx(expected)


def test_recurring_never_done_is_fully_due():
    assert urgency_recurring(recurring(), None, TODAY) == 1.0


def test_degenerate_windows_do_not_divide_by_zero():
    assert urgency_recurring(recurring(lo=7, mid=7, hi=14), ago(7), TODAY) == 0.5
    assert urgency_recurring(recurring(lo=7, mid=14, hi=14), ago(14), TODAY) == 0.5
    assert urgency_recurring(recurring(lo=7, mid=7, hi=7), ago(8), TODAY) == 2.0


def test_one_off_grows_weekly():
    assert urgency_one_off(TODAY, TODAY) == 0.5
    assert urgency_one_off(ago(14), TODAY) == pytest.approx(0.9)


def test_score_filters_and_orders():
    chores = [
        one_off("done-already"),
        one_off("new", priority=2),
        one_off("old", created=ago(35)),                       # 0.5 + 1.0 = 1.5 → score 4.5
        recurring("not-due"),
        recurring("overdue"),                                  # 21 days → 2.0 → score 6
        one_off("blocked", needs=["buy-stuff"]),
        one_off("vague", duration_h=None),
        recurring("retired", active=False),
    ]
    last = {"done-already": ago(3), "not-due": ago(2), "overdue": ago(21)}
    eligible, excluded = score_chores(chores, last, TODAY)
    assert [s.chore.id for s in eligible] == ["overdue", "old", "new"]
    assert [s.score for s in eligible] == [6.0, 4.5, 1.0]
    assert dict((c.id, why) for c, why in excluded) == {
        "blocked": "waiting for: buy-stuff", "vague": "needs details: duration_h"}


def test_pending_need_keeps_job_eligible_and_lends_its_score():
    chores = [one_off("buy", priority=2), one_off("job", priority=4, needs=["buy"]), one_off("other")]
    eligible, excluded = score_chores(chores, {}, TODAY)
    assert excluded == []
    assert [s.chore.id for s in eligible] == ["buy", "job", "other"]     # buy inherits job's 2.0
    assert [s.score for s in eligible] == [2.0, 2.0, 1.5]
    assert eligible[1].waits_for == ["buy"]


def test_job_waiting_for_an_ineligible_chore_is_excluded():
    chores = [one_off("buy", duration_h=None), one_off("job", needs=["buy"])]
    _, excluded = score_chores(chores, {}, TODAY)
    assert dict((c.id, why) for c, why in excluded) == {
        "buy": "needs details: duration_h", "job": "waiting for: buy"}


def test_needs_met_once_done():
    eligible, _ = score_chores([one_off("job", needs=["buy"])], {"buy": ago(1)}, TODAY)
    assert [s.chore.id for s in eligible] == ["job"]
