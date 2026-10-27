from datetime import date, datetime, timedelta

from planner.fitting import plan_period
from planner.models import Chore, Person, Scored

SAT = date(2026, 10, 31)
SUN = SAT + timedelta(days=1)
DAYS = [SAT, SUN]
PEOPLE = [Person("nicolas", "Nicolas"), Person("anna", "Anna")]
WINDOW = [(8 * 60, 18 * 60)]


def free(**overrides):
    """Both people free 08:00-18:00 on both days, unless overridden: free(nicolas_sat=[...])."""
    slots = {(p.id, d): list(WINDOW) for p in PEOPLE for d in DAYS}
    for key, value in overrides.items():
        who, day = key.split("_")
        slots[(who, SAT if day == "sat" else SUN)] = value
    return slots


def chore(id, hours, who, score=1.0):
    c = Chore(id=id, name=id, category="home", type="one_off", duration_h=hours, priority=3, assignee=who)
    return Scored(c, score / 3, score)


def at(day, hhmm):
    h, m = map(int, hhmm.split(":"))
    return datetime(day.year, day.month, day.day, h, m)


def test_no_budget_fills_free_time_until_it_runs_out():
    scored = [chore(f"c{i}", 4, "nicolas", score=10 - i) for i in range(6)]
    plan = plan_period(scored, PEOPLE, DAYS, free())
    assert sum(i.chore.duration_h for i in plan.items) == 16          # two 4 h chores fit in each 10 h day
    assert all(why == "no free slot long enough" for _, why in plan.unplanned)


def test_higher_score_wins_the_time():
    plan = plan_period([chore("big", 10, "anna", 9), chore("small", 10, "anna", 1), chore("third", 10, "anna", 0.5)],
                       PEOPLE, DAYS, free())
    assert [i.chore.id for i in plan.items] == ["big", "small"]


def test_both_needs_a_common_slot():
    plan = plan_period([chore("together", 2, "both")], PEOPLE, DAYS, free(anna_sat=[(12 * 60, 18 * 60)]))
    item = plan.items[0]
    assert item.people == ["nicolas", "anna"]
    assert item.start == at(SAT, "12:00")        # earliest time both are free


def test_earliest_day_first():
    plan = plan_period([chore("a", 1, "nicolas", 3), chore("b", 1, "nicolas", 2)], PEOPLE, DAYS, free())
    assert [(i.chore.id, i.start) for i in plan.items] == [("a", at(SAT, "08:00")), ("b", at(SAT, "09:00"))]


def test_either_goes_to_person_with_more_free_time_left():
    plan = plan_period([chore("n-job", 9, "nicolas", 5), chore("any", 1, "either", 1)], PEOPLE, DAYS, free())
    assert {i.chore.id: i.people for i in plan.items}["any"] == ["anna"]


def test_only_free_slots_are_used():
    plan = plan_period([chore("fix", 1, "nicolas")], PEOPLE, DAYS, free(nicolas_sat=[(17 * 60, 17 * 60 + 30)],
                                                                        nicolas_sun=[]))
    assert plan.items == [] and plan.unplanned[0][1] == "no free slot long enough"


def test_any_list_of_days():
    thu, mon = SAT - timedelta(days=2), SAT + timedelta(days=2)
    slots = {("nicolas", thu): [(14 * 60, 18 * 60)], ("anna", thu): [], ("nicolas", mon): [], ("anna", mon): []}
    plan = plan_period([chore("fix", 2, "nicolas")], PEOPLE, [thu, mon], slots)
    assert plan.items[0].start == at(thu, "14:00") and plan.days == [thu, mon]


def test_chores_do_not_overlap_for_a_person():
    scored = [chore(f"c{i}", 1.5, "nicolas", score=10 - i) for i in range(4)]
    items = plan_period(scored, PEOPLE, DAYS, free()).items
    spans = sorted((i.start, i.end) for i in items)
    assert all(a_end <= b_start for (_, a_end), (b_start, _) in zip(spans, spans[1:]))


def test_unknown_assignee_is_reported():
    plan = plan_period([chore("x", 1, "grandma")], PEOPLE, DAYS, free())
    assert plan.unplanned[0][1] == "unknown assignee 'grandma'"


def test_job_goes_after_its_purchase():
    buy, job = chore("buy", 1, "anna", 5), chore("job", 2, "nicolas", 5)
    job.waits_for = ["buy"]
    items = {i.chore.id: i for i in plan_period([buy, job], PEOPLE, DAYS,
                                                free(anna_sat=[(16 * 60, 18 * 60)], anna_sun=[])).items}
    assert items["buy"].start == at(SAT, "16:00")
    assert items["job"].start >= items["buy"].end


def test_job_dropped_when_its_purchase_did_not_fit():
    buy, job = chore("buy", 11, "anna", 5), chore("job", 1, "nicolas", 5)   # 11 h > any day's window
    job.waits_for = ["buy"]
    plan = plan_period([buy, job], PEOPLE, DAYS, free())
    assert plan.items == []
    assert dict((c.id, why) for c, why in plan.unplanned)["job"] == "needs buy first, which didn't fit"


def test_three_step_chain_in_order():
    from planner.scoring import score_chores
    mk = lambda id, needs=(): Chore(id=id, name=id, category="garden", type="one_off", duration_h=1, priority=3,
                                    assignee="nicolas", needs=list(needs), created_on=SAT)
    scored, _ = score_chores([mk("relay", ["lift"]), mk("lift", ["buy"]), mk("buy")], {}, SAT)
    order = [i.chore.id for i in sorted(plan_period(scored, PEOPLE, DAYS, free()).items, key=lambda i: i.start)]
    assert order == ["buy", "lift", "relay"]


def test_not_before_keeps_a_chore_off_earlier_days():
    s = chore("car", 2, "nicolas", score=15)
    s.chore.not_before = SUN
    plan = plan_period([s], PEOPLE, DAYS, free())
    assert plan.items[0].start == at(SUN, "08:00")


def test_not_before_and_needs_both_apply():
    buy = chore("buy", 1, "nicolas", score=5)
    job = chore("job", 1, "nicolas", score=5)
    job.waits_for = ["buy"]
    job.chore.not_before = SUN
    plan = plan_period([buy, job], PEOPLE, DAYS, free())
    starts = {i.chore.id: i.start for i in plan.items}
    assert starts == {"buy": at(SAT, "08:00"), "job": at(SUN, "08:00")}


def test_every_start_is_on_a_quarter_hour():
    first, second = chore("short", 1 / 3, "nicolas", 5), chore("next", 1 / 3, "nicolas", 4)    # 20 minutes each
    plan = plan_period([first, second], PEOPLE, DAYS, free(nicolas_sat=[(8 * 60 + 40, 18 * 60)]))
    starts = {i.chore.id: i.start for i in plan.items}
    assert starts == {"short": at(SAT, "08:45"), "next": at(SAT, "09:15")}      # not 08:40 and 09:05


def test_a_quarter_hour_start_must_still_fit():
    plan = plan_period([chore("hour", 1, "nicolas")], PEOPLE, DAYS,
                       free(nicolas_sat=[(8 * 60 + 10, 9 * 60 + 10)], nicolas_sun=[]))
    assert plan.items == [] and plan.unplanned[0][1] == "no free slot long enough"   # 08:15–09:15 doesn't fit
