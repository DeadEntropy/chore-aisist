from datetime import date, datetime

from planner.fitting import plan_weekend
from planner.models import Busy, Chore, Person, Scored

SAT = date(2026, 10, 31)
PEOPLE = [Person("nicolas", "Nicolas", 6), Person("anna", "Anna", 6)]


def chore(id, hours, who, score=1.0):
    c = Chore(id=id, name=id, category="home", type="one_off", duration_h=hours, priority=3, assignee=who)
    return Scored(c, score / 3, score)


def at(day, hhmm):
    h, m = map(int, hhmm.split(":"))
    return datetime(day.year, day.month, day.day, h, m)


def test_budget_is_never_exceeded():
    scored = [chore(f"c{i}", 2, "nicolas", score=10 - i) for i in range(5)]
    plan = plan_weekend(scored, PEOPLE, [], SAT)
    assert sum(i.chore.duration_h for i in plan.items) == 6
    assert [c.id for c, _ in plan.unplanned] == ["c3", "c4"]
    assert all(why == "weekend budget used up" for _, why in plan.unplanned)


def test_higher_score_wins_the_budget():
    plan = plan_weekend([chore("big", 6, "anna", 9), chore("small", 1, "anna", 1)], PEOPLE, [], SAT)
    assert [i.chore.id for i in plan.items] == ["big"]


def test_both_uses_both_budgets_and_a_common_slot():
    busy = [Busy("anna", at(SAT, "08:00"), at(SAT, "12:00"))]
    plan = plan_weekend([chore("together", 2, "both")], PEOPLE, busy, SAT)
    item = plan.items[0]
    assert item.people == ["nicolas", "anna"]
    # Sunday has more common free time (10 h vs 6 h), so it goes there, first thing.
    assert item.start == at(SAT.replace(day=1, month=11), "08:00")


def test_either_goes_to_person_with_more_budget():
    plan = plan_weekend([chore("n-job", 3, "nicolas", 5), chore("any", 1, "either", 1)], PEOPLE, [], SAT)
    assert {i.chore.id: i.people for i in plan.items}["any"] == ["anna"]


def test_busy_time_is_respected():
    busy = [Busy("nicolas", at(SAT, "08:00"), at(SAT, "17:30")),
            Busy("nicolas", at(SAT.replace(day=1, month=11), "00:00"), at(SAT.replace(day=2, month=11), "00:00"))]
    plan = plan_weekend([chore("fix", 1, "nicolas")], PEOPLE, busy, SAT)
    assert plan.items == []
    assert plan.unplanned[0][1] == "no free slot long enough"


def test_chores_do_not_overlap_for_a_person():
    scored = [chore(f"c{i}", 1.5, "nicolas", score=10 - i) for i in range(4)]
    items = plan_weekend(scored, PEOPLE, [], SAT).items
    spans = sorted((i.start, i.end) for i in items)
    assert all(a_end <= b_start for (_, a_end), (b_start, _) in zip(spans, spans[1:]))
    assert all(at(i.start.date(), "08:00") <= i.start and i.end <= at(i.start.date(), "18:00") for i in items)


def test_unknown_assignee_is_reported():
    plan = plan_weekend([chore("x", 1, "grandma")], PEOPLE, [], SAT)
    assert plan.unplanned[0][1] == "unknown assignee 'grandma'"
