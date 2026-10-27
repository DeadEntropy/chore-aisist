from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import icalendar

from planner.models import Chore, PlannedItem, PlannedRecord
from planner.plans import (EventData, build_ics, calendar_diff, desired_events, event_from_component, event_title,
                           event_uid, find_record, freeze, reconcile)

TZ = ZoneInfo("America/New_York")
SAT = date(2026, 10, 31)


def rec(chore, day, hh, status="open", people=("nicolas",), hours=1):
    start = datetime(day.year, day.month, day.day, hh)
    return PlannedRecord(chore, list(people), start, start + timedelta(hours=hours), uid(chore, day), status)


def uid(chore, day):
    return event_uid(chore, day, "t0")                     # a fixed token, so tests can name the UIDs


def item(chore, day, hh, people=("nicolas",), hours=1):
    c = Chore(id=chore, name=chore, category="home", type="one_off", duration_h=hours, priority=3, assignee=people[0])
    start = datetime(day.year, day.month, day.day, hh)
    return PlannedItem(c, list(people), start, start + timedelta(hours=hours), 3.0)


def test_uid_and_title():
    assert event_uid("mow-lawn", SAT, "ab12cd") == "chore-mow-lawn-2026-10-31-ab12cd@chore-aisist"
    first, second = event_uid("mow-lawn", SAT), event_uid("mow-lawn", SAT)
    assert first != second and first.startswith("chore-mow-lawn-2026-10-31-") and first.endswith("@chore-aisist")
    assert event_title("Mow the lawn", ["nicolas"], "open") == "🧹 Mow the lawn (Nicolas)"
    assert event_title("Re-mulch", ["nicolas", "anna"], "done") == "✅ Re-mulch (Nicolas & Anna)"
    assert event_title("Hedge", ["anna"], "skipped").startswith("⏭") and event_title("x", ["anna"], "unknown")[0] == "❔"


def test_freeze_marks_past_open_items_unknown_and_keeps_in_progress_pending():
    sun = SAT + timedelta(days=1)
    records = [rec("morning", SAT, 9), rec("now", SAT, 17, hours=2), rec("evening", SAT, 19),
               rec("done", SAT, 10, "done"), rec("future", sun, 9)]
    unknown, in_progress = freeze(records, now=datetime(2026, 10, 31, 18, 0))
    assert [r.chore_id for r in unknown] == ["morning"]      # ended before now, never reported
    assert [r.chore_id for r in in_progress] == ["now"]      # in progress: not planned again, its time stays taken
    # "evening" starts after now: it is re-planned like any future item


def test_reconcile_keeps_moves_adds_and_removes_only_future_open_items():
    sun = SAT + timedelta(days=1)
    records = [rec("past", SAT, 9),                       # frozen (before start): untouched
               rec("same", sun, 9), rec("moved", sun, 10), rec("gone", sun, 11),
               rec("history", sun, 12, "done")]           # done: untouched even though it's not in the new plan
    new = [item("same", sun, 9), item("moved", sun, 14), item("new", sun, 15)]
    ch = reconcile(records, new, now=datetime.combine(sun, datetime.min.time()))
    assert [r.chore_id for r in ch.add] == ["new"]
    assert ch.add[0].uid.startswith("chore-new-2026-11-01-") and ch.add[0].uid != uid("new", sun)
    assert [(r.chore_id, r.start.hour, r.uid) for r in ch.move] == [("moved", 14, uid("moved", sun))]  # keeps its UID
    assert [r.chore_id for r in ch.remove] == ["gone"]


def test_reconcile_does_not_overwrite_history_on_the_same_day():
    sun = SAT + timedelta(days=1)
    ch = reconcile([rec("hedge", sun, 9, "skipped")], [item("hedge", sun, 14)], now=datetime(2026, 11, 1, 8))
    assert not ch and [i.chore.id for i in ch.dropped] == ["hedge"]


def test_find_record_picks_open_or_unknown_closest_to_the_day():
    records = [rec("mow", SAT - timedelta(days=7), 9, "unknown"), rec("mow", SAT, 9),
               rec("mow", SAT + timedelta(days=7), 9), rec("mow", SAT - timedelta(days=1), 9, "done")]
    assert find_record(records, "mow", SAT + timedelta(days=1)).start.date() == SAT
    assert find_record(records, "mow", SAT - timedelta(days=6)).status == "unknown"
    assert find_record(records, "trim", SAT) is None


def test_calendar_shows_only_open_and_unknown_items():
    records = [rec("mow", SAT, 9), rec("trim", SAT, 10, "done"), rec("hedge", SAT, 11, "skipped"),
               rec("weed", SAT, 8, "unknown")]
    assert sorted(e.title for e in desired_events(records, {}, 0).values()) == ["❔ weed (Nicolas)", "🧹 mow (Nicolas)"]


def test_calendar_diff_only_touches_our_events():
    desired = desired_events([rec("mow", SAT, 9), rec("trim", SAT, 10, "done")], {"mow": "Mow the lawn"}, 0)
    reported = EventData(uid("trim", SAT), "🧹 trim (Nicolas)", datetime(2026, 10, 31, 10), datetime(2026, 10, 31, 11))
    existing = {reported.uid: reported,
                uid("old", SAT): EventData(uid("old", SAT), "🧹 old", datetime(2026, 10, 31, 8),
                                           datetime(2026, 10, 31, 9)),
                "someone-else@example.com": EventData("someone-else@example.com", "Mine", datetime(2026, 10, 31, 8),
                                                      datetime(2026, 10, 31, 9))}
    put, delete = calendar_diff(desired, existing)
    assert [e.uid for e in put] == [uid("mow", SAT)]
    assert sorted(delete) == sorted([uid("old", SAT), uid("trim", SAT)])   # done: taken off; never the one not ours


def test_ics_has_timezone_reminder_and_reads_back_the_same():
    event = EventData(uid("mow", SAT), "🧹 Mow the lawn (Nicolas)", datetime(2026, 10, 31, 9),
                      datetime(2026, 10, 31, 10, 30), reminder=30)
    text = build_ics(event, TZ, now=datetime(2026, 10, 30, 20, tzinfo=TZ))
    cal = icalendar.Calendar.from_ical(text)
    ev = cal.walk("VEVENT")[0]
    assert "BEGIN:VTIMEZONE" in text and "TZID=America/New_York" in text
    assert ev.walk("VALARM")[0].get("trigger").dt == timedelta(minutes=-30)
    assert event_from_component(ev, TZ) == event
    assert "VALARM" not in build_ics(EventData(event.uid, event.title, event.start, event.end, 0), TZ)


def test_reminder_change_rewrites_the_event():
    desired = desired_events([rec("mow", SAT, 9)], {}, 15)
    existing = {u: EventData(e.uid, e.title, e.start, e.end, 30) for u, e in desired.items()}
    put, delete = calendar_diff(desired, existing)
    assert [e.reminder for e in put] == [15] and delete == []
