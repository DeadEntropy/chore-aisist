"""Connects the pieces: load data, score, fit. (The real weekly run and scheduler come later.)"""

from datetime import date, timedelta

from planner import db
from planner.fitting import plan_weekend
from planner.models import Plan
from planner.scoring import score_chores


def next_saturday(today: date) -> date:
    return today + timedelta(days=(5 - today.weekday()) % 7)


def dry_run(conn, saturday: date, weeks: int = 1) -> list[tuple[Plan, list]]:
    """Plan `weeks` consecutive weekends without writing anything.

    From the second weekend on, every chore planned the weekend before is assumed done on its day,
    so the output shows how the backlog would be worked through. No calendars are read yet:
    everyone is treated as free all weekend.
    """
    chores = db.get_chores(conn)
    people = db.get_people(conn)
    last_done = db.last_done_dates(conn)

    results = []
    for week in range(weeks):
        day = saturday + timedelta(weeks=week)
        scored, excluded = score_chores(chores, last_done, today=day)
        plan = plan_weekend(scored, people, busy=[], saturday=day)
        results.append((plan, excluded))
        for item in plan.items:
            last_done[item.chore.id] = item.start.date()
    return results
