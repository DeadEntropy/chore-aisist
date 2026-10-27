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


def test_never_done_recurring_keeps_growing():
    added = ago(0)
    chore = recurring("weed", lo=60, mid=90, hi=120, created_on=added)
    week0, _ = score_chores([chore], {}, TODAY)
    later, _ = score_chores([chore], {}, TODAY + timedelta(weeks=6))
    assert week0[0].urgency == 1.0
    assert later[0].urgency == pytest.approx(1.0 + 42 / 60)


@pytest.mark.parametrize("lo, mid, hi", [(7, None, None), (7, 10, None), (None, None, 14)])
def test_partial_interval_needs_details(lo, mid, hi):
    chore = recurring("half", lo=lo, mid=mid, hi=hi)
    eligible, excluded = score_chores([chore], {"half": ago(30)}, TODAY)
    assert eligible == []
    assert excluded[0][1] == "needs details: interval"


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


def test_recurring_job_needs_a_fresh_need_each_round():
    mulch = recurring("mulch", lo=300, mid=365, hi=450, needs=["order"])
    order = recurring("order", lo=300, mid=365, hi=450)
    last_year = {"mulch": ago(400), "order": ago(401)}
    eligible, _ = score_chores([mulch, order], last_year, TODAY)
    assert {s.chore.id: s.waits_for for s in eligible}["mulch"] == ["order"]   # last year's order doesn't count
    fresh = {"mulch": ago(400), "order": ago(3)}
    eligible, _ = score_chores([mulch], fresh, TODAY)
    assert eligible[0].waits_for == []


def test_recurring_job_keeps_a_done_one_off_need():
    trim = recurring("trim", lo=7, mid=10, hi=14, needs=["buy-trimmer"])
    buy = one_off("buy-trimmer")
    last = {"trim": ago(20), "buy-trimmer": ago(60)}       # bought once, long before the last trim
    eligible, excluded = score_chores([trim, buy], last, TODAY)
    assert [s.chore.id for s in eligible] == ["trim"]
    assert eligible[0].waits_for == [] and excluded == []


def test_chain_puts_prerequisites_first_and_passes_the_score_down():
    # relay needs lift, lift needs buy; relay is the most urgent. Same priority, so ids would sort relay first.
    chores = [one_off("relay", priority=5, needs=["lift"]), one_off("lift", priority=1, needs=["buy"]),
              one_off("buy", priority=1), one_off("other", priority=3)]
    eligible, _ = score_chores(chores, {}, TODAY)
    assert [s.chore.id for s in eligible] == ["buy", "lift", "relay", "other"]
    assert [s.score for s in eligible[:3]] == [2.5, 2.5, 2.5]


def test_score_reaches_the_bottom_of_a_chain_in_any_order():
    # 'top' (9) waits for 'mid'; 'mid' waits for 'low'; 'side' (6) also waits for 'low'.
    chores = [one_off("top", priority=5, needs=["mid"], created=ago(56)), one_off("mid", priority=1, needs=["low"]),
              one_off("low", priority=1), one_off("side", priority=4, needs=["low"], created=ago(35))]
    eligible, _ = score_chores(chores, {}, TODAY)
    scores = {s.chore.id: s.score for s in eligible}
    assert scores["low"] == scores["mid"] == scores["top"]


def test_circular_needs_are_excluded():
    chores = [one_off("a", needs=["b"]), one_off("b", needs=["a"]), one_off("c", needs=["a"]), one_off("ok")]
    eligible, excluded = score_chores(chores, {}, TODAY)
    assert [s.chore.id for s in eligible] == ["ok"]
    assert dict((c.id, why) for c, why in excluded) == {
        "a": "circular needs: b", "b": "circular needs: a", "c": "waiting for: a"}


def test_job_is_not_pushed_behind_unrelated_chores_on_a_tie():
    # order (3.0, inherits) → mulch (3.0) must stay together, ahead of an unrelated 3.0 chore with a later id
    chores = [recurring("mulch", needs=["order"], created_on=TODAY), recurring("order", created_on=TODAY),
              recurring("zzz-unrelated", created_on=TODAY)]
    eligible, _ = score_chores(chores, {}, TODAY)
    assert [s.chore.id for s in eligible] == ["order", "mulch", "zzz-unrelated"]


def test_needs_met_once_done():
    eligible, _ = score_chores([one_off("job", needs=["buy"])], {"buy": ago(1)}, TODAY)
    assert [s.chore.id for s in eligible] == ["job"]


# --- not_before ---------------------------------------------------------------

def test_not_before_holds_a_chore_back_until_the_period_reaches_it():
    car = one_off("car", not_before=date(2026, 12, 1))
    eligible, excluded = score_chores([car], {}, TODAY, until=TODAY + timedelta(days=6))
    assert eligible == []
    assert [(c.id, why) for c, why in excluded] == [("car", "not before Tue 01 Dec 2026")]
    eligible, _ = score_chores([car], {}, date(2026, 11, 28), until=date(2026, 12, 4))
    assert [s.chore.id for s in eligible] == ["car"]


def test_not_before_defaults_to_today_without_until():
    eligible, _ = score_chores([one_off("car", not_before=TODAY + timedelta(days=1))], {}, TODAY)
    assert eligible == []
    eligible, _ = score_chores([one_off("car", not_before=TODAY)], {}, TODAY)
    assert [s.chore.id for s in eligible] == ["car"]


def test_one_off_waits_from_not_before_not_from_when_it_was_added():
    car = one_off("car", created=ago(35), not_before=TODAY)
    eligible, _ = score_chores([car], {}, TODAY)
    assert eligible[0].urgency == pytest.approx(0.5)              # fresh, not 5 weeks old


def test_not_before_in_the_past_changes_nothing():
    old = one_off("fix", created=ago(14), not_before=ago(21))
    eligible, _ = score_chores([old], {}, TODAY)
    assert eligible[0].urgency == pytest.approx(0.9)              # still waiting since it was added


def test_job_waits_for_a_need_that_is_held_back():
    chores = [one_off("install", needs=["buy"]), one_off("buy", not_before=TODAY + timedelta(days=30))]
    eligible, excluded = score_chores(chores, {}, TODAY)
    assert eligible == []
    assert dict((c.id, why) for c, why in excluded)["install"] == "waiting for: buy"
