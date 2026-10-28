from datetime import date, datetime, timedelta

from planner.availability import parse_window, parse_windows
from planner.config import PlanningSettings
from planner.jobs import next_available, plan_periods
from planner.models import CalEvent, Chore, Person

SAT = date(2026, 10, 31)
START = SAT - timedelta(days=1)                      # periods run Fri → Thu, like a Thursday-evening run
BOTH = ["nicolas", "anna"]
PERSONS = [Person("nicolas", "Nicolas"), Person("anna", "Anna")]
PLANNING = PlanningSettings(parse_windows("sat 08:00-18:00; sun 08:00-18:00"), parse_window("08:00-18:00"))


def all_day(kind, who, first: date, nights: int, title=None):
    start = datetime.combine(first, datetime.min.time())
    return CalEvent(who, title or kind, start, start + timedelta(days=nights), kind)


def recurring(id, lo, mid, hi, **kw):
    return Chore(id=id, name=id, category="garden", type="recurring", interval_min_days=lo,
                 interval_target_days=mid, interval_max_days=hi, duration_h=1, priority=3, assignee="nicolas",
                 created_on=SAT - timedelta(days=400), **kw)


def one_off(id, hours=1, who="nicolas"):
    return Chore(id=id, name=id, category="home", type="one_off", duration_h=hours, priority=3, assignee=who,
                 created_on=SAT)


def test_without_travel_the_next_chore_day_is_normal():
    assert next_available(BOTH, START + timedelta(days=6), [], 30, PLANNING) is None


def test_next_available_skips_blocked_weekends():
    events = [all_day("away", BOTH, SAT + timedelta(days=5), 12)]      # Thu 5 Nov → Tue 17 Nov
    assert next_available(BOTH, START + timedelta(days=6), events, 30, PLANNING) == SAT + timedelta(days=21)


def test_travel_week_front_loads_only_what_would_go_overdue():
    """Away 5-17 Nov (two weekends): next chore day is Sat 21 Nov."""
    events = [all_day("away", BOTH, SAT + timedelta(days=5), 12)]
    fertilize = recurring("fertilize", 28, 35, 49)      # done 17 Oct: on target (35) by 21 Nov
    mow = recurring("mow", 7, 10, 14)                   # done 24 Oct: overdue (28 days) by 21 Nov
    hedge = recurring("hedge", 30, 45, 60)              # done 29 Sep: past min now, not overdue by 21 Nov
    last = {"fertilize": SAT - timedelta(days=14), "mow": SAT - timedelta(days=7), "hedge": SAT - timedelta(days=32)}

    results = plan_periods([fertilize, mow, hedge], PERSONS, last, START, 4, events, 30, PLANNING)
    planned = [{i.chore.id for i in r.plan.items} for r in results]
    assert results[0].next_available == SAT + timedelta(days=21)
    assert "mow" in planned[0] and "hedge" in planned[0] and "fertilize" not in planned[0]
    assert results[1].blocked and results[2].blocked and planned[1] == planned[2] == set()
    assert "fertilize" in planned[3]              # catch-up after the trip


def test_long_weekend_and_extra_afternoon_are_used():
    thu, mon = SAT - timedelta(days=2), SAT + timedelta(days=2)
    events = [all_day("extra_day", ["anna"], mon, 1, "Day off"),          # Anna's long weekend
              CalEvent(["nicolas"], "Chores", datetime(2026, 10, 29, 14), datetime(2026, 10, 29, 18), "extra_time"),
              all_day("busy", BOTH, SAT, 2, "Camping")]          # weekend fully taken (as busy time)
    chores = [one_off("fix"), one_off("tidy", who="anna")]
    results = plan_periods(chores, PERSONS, {}, thu, 1, events, 30, PLANNING)
    days = {i.chore.id: i.start.date() for i in results[0].plan.items}
    assert days == {"fix": thu, "tidy": mon}               # Nicolas on his Thursday afternoon, Anna on her day off


def test_no_chores_day_removes_default_window():
    events = [all_day("no_chores", BOTH, SAT, 2, "No chores")]
    results = plan_periods([one_off("fix")], PERSONS, {}, START, 1, events, 30, PLANNING)
    assert results[0].blocked and results[0].plan.items == []


def test_events_listed_per_period():
    party = CalEvent(BOTH, "Party", datetime(2026, 10, 31, 14), datetime(2026, 10, 31, 16))
    later = CalEvent(BOTH, "Dinner", datetime(2026, 11, 7, 19), datetime(2026, 11, 7, 21))
    results = plan_periods([], PERSONS, {}, START, 2, [later, party], 30, PLANNING)
    assert [e.summary for e in results[0].events] == ["Party"]
    assert [e.summary for e in results[1].events] == ["Dinner"]


def test_busy_events_listed_only_on_days_with_chore_time():
    weekday = CalEvent(BOTH, "School run", datetime(2026, 11, 2, 7), datetime(2026, 11, 2, 7, 15))
    weekend = CalEvent(BOTH, "Party", datetime(2026, 10, 31, 14), datetime(2026, 10, 31, 16))
    trip = all_day("away", BOTH, date(2026, 11, 3), 2, "Trip")                 # a weekday trip is still shown
    results = plan_periods([], PERSONS, {}, START, 1, [weekday, weekend, trip], 30, PLANNING)
    assert [e.summary for e in results[0].events] == ["Party", "Trip"]


def test_nothing_is_planned_before_now():
    now = datetime(2026, 10, 31, 13, 20)                  # Saturday 13:20
    results = plan_periods([one_off("fix", hours=1)], PERSONS, {}, now.date(), 1, [], 30, PLANNING, not_before=now)
    assert results[0].plan.items[0].start >= now


def test_not_before_plans_the_chore_in_the_first_period_that_reaches_it():
    car = one_off("car", hours=2)
    car.not_before = date(2026, 11, 8)                    # a Sunday, in the second period (Fri 6 – Thu 12 Nov)
    results = plan_periods([car], PERSONS, {}, START, 3, [], 30, PLANNING)
    assert results[0].plan.items == []
    assert [(c.id, why) for c, why in results[0].excluded] == [("car", "not before Sun 08 Nov 2026")]
    item, = results[1].plan.items
    assert item.start == datetime(2026, 11, 8, 8, 0)      # Sunday, not Saturday 7th
    assert results[2].plan.items == []                    # done in period 2, so it's gone


def test_backup_before_run_once_per_scheduled_time_and_never_fatal(tmp_path, monkeypatch):
    from datetime import time

    from planner import jobs
    from planner.backup import backup_name
    from planner.config import BackupSettings
    made, lines = [], []
    monkeypatch.setattr(jobs.backup, "dump", lambda s, folder, now, keep: made.append(now) or folder / backup_name(now))
    backups, times = BackupSettings(str(tmp_path), 30), (time(20, 0),)
    now = datetime(2026, 10, 27, 20, 0, 4)

    def log(message, error=False):
        lines.append((message, error))

    (tmp_path / backup_name(datetime(2026, 10, 26, 20, 0))).write_bytes(b"")          # yesterday's
    jobs.backup_before_run(None, backups, now, times, log)
    assert made == [now]

    (tmp_path / backup_name(now)).write_bytes(b"")                                    # a retry at 20:15
    jobs.backup_before_run(None, backups, datetime(2026, 10, 27, 20, 15), times, log)
    assert made == [now]

    def broken(*args):
        raise ValueError("disk full")
    monkeypatch.setattr(jobs.backup, "dump", broken)
    jobs.backup_before_run(None, backups, datetime(2026, 10, 28, 20, 0), times, log)  # logged, not raised
    assert ("Backup failed: ValueError: disk full", True) in lines


def test_an_unreadable_backup_folder_doesnt_stop_the_run(tmp_path, monkeypatch):
    from datetime import time

    from planner import jobs
    from planner.config import BackupSettings
    lines = []

    def unreadable(folder):
        raise PermissionError(13, "Permission denied", str(folder))
    monkeypatch.setattr(jobs.backup, "latest", unreadable)
    jobs.backup_before_run(None, BackupSettings(str(tmp_path), 30), datetime(2026, 10, 27, 20, 0), (time(20, 0),),
                           lambda message, error=False: lines.append((message, error)))
    assert lines and lines[0][1] and "Backup failed: PermissionError" in lines[0][0]


# --- re-planning "from now": quarter hours, in-progress items ----------------------------

def _in_progress(chore, start, end, who="nicolas"):
    from planner.models import PlannedRecord
    return PlannedRecord(chore, [who], start, end, f"chore-{chore}-x@chore-aisist")


def test_planning_from_now_starts_at_the_next_quarter_hour():
    now = datetime(2026, 10, 31, 10, 17)                  # Saturday 10:17
    results = plan_periods([one_off("fix")], PERSONS, {}, now.date(), 1, [], 30, PLANNING, not_before=now)
    assert results[0].plan.items[0].start == datetime(2026, 10, 31, 10, 30)


def test_an_item_in_progress_keeps_its_time_taken():
    now = datetime(2026, 10, 31, 10, 17)
    busy = _in_progress("trim", datetime(2026, 10, 31, 9), datetime(2026, 10, 31, 12))
    results = plan_periods([one_off("fix")], PERSONS, {}, now.date(), 1, [], 30, PLANNING, not_before=now,
                           in_progress=[busy])
    assert results[0].plan.items[0].start == datetime(2026, 10, 31, 12)          # after the trim, not on top of it


def test_a_job_can_follow_a_need_in_progress():
    now = datetime(2026, 10, 31, 10, 17)
    order = _in_progress("order", datetime(2026, 10, 31, 10), datetime(2026, 10, 31, 11), who="anna")
    job = Chore(id="mulch", name="mulch", category="garden", type="one_off", duration_h=1, priority=3,
                assignee="nicolas", created_on=SAT, needs=["order"])
    results = plan_periods([job], PERSONS, {}, now.date(), 1, [], 30, PLANNING, not_before=now, in_progress=[order])
    item, = results[0].plan.items
    assert item.start == datetime(2026, 10, 31, 11)                              # waits for the order to end
    assert results[0].excluded == []


def test_an_in_progress_chore_comes_back_in_later_periods_of_a_preview():
    now = datetime(2026, 10, 31, 10, 0)
    weekly = recurring("water", 7, 7, 7)                  # due every 7 days
    doing = _in_progress("water", datetime(2026, 10, 31, 9), datetime(2026, 10, 31, 11))
    results = plan_periods([weekly], PERSONS, {}, now.date(), 2, [], 30, PLANNING, not_before=now,
                           in_progress=[doing])
    assert results[0].plan.items == []                    # in progress this period: not planned again
    assert [i.start.date() for i in results[1].plan.items] == [date(2026, 11, 7)]   # due again a week later


def test_a_preview_starting_later_keeps_dependents_of_an_in_progress_chore():
    now = datetime(2026, 10, 31, 10, 0)
    order = _in_progress("order", datetime(2026, 10, 31, 9), datetime(2026, 10, 31, 11))
    job = Chore(id="mulch", name="mulch", category="garden", type="one_off", duration_h=1, priority=3,
                assignee="nicolas", created_on=SAT, needs=["order"])
    order_chore = one_off("order")
    results = plan_periods([job, order_chore], PERSONS, {}, date(2026, 11, 6), 1, [], 30, PLANNING,
                           in_progress=[order])                  # preview from next Friday
    assert [i.chore.id for i in results[0].plan.items] == ["mulch"]   # the order counts as done by then


def test_an_in_progress_need_still_follows_the_recurring_needs_rule():
    # mulch was last done after the last trim, so the trim must be done again first: the one in progress counts,
    # and the mulch must wait for it to end (not start before it, on the strength of the old trim).
    now = datetime(2026, 10, 31, 10, 0)
    trim = recurring("trim", 7, 10, 14)
    mulch = recurring("mulch", 7, 10, 14, needs=["trim"])
    doing = _in_progress("trim", datetime(2026, 10, 31, 10), datetime(2026, 10, 31, 12))
    last = {"trim": SAT - timedelta(days=30), "mulch": SAT - timedelta(days=20)}
    results = plan_periods([trim, mulch], PERSONS, last, now.date(), 1, [], 30, PLANNING, not_before=now,
                           in_progress=[doing])
    item, = results[0].plan.items
    assert item.chore.id == "mulch" and item.start >= datetime(2026, 10, 31, 12)


# --- weather ------------------------------------------------------------------

from planner import jobs, weather                                    # noqa: E402
from planner.config import WeatherSettings                           # noqa: E402
from planner.models import HourWeather                               # noqa: E402

WEATHER = WeatherSettings(0.0, 0.0, "America/New_York")
WITH_WEATHER = PlanningSettings(PLANNING.windows, PLANNING.extra_day_window, WEATHER)


def wet_sunday(rain=75):
    """Dry Saturday, `rain`% all Sunday (8:00-18:00)."""
    out = {}
    for day, p in ((SAT, 0), (SAT + timedelta(days=1), rain)):
        for h in range(8, 18):
            out[datetime.combine(day, datetime.min.time()) + timedelta(hours=h)] = HourWeather(p, 30.0, 4)
    return out


def mulch(id, hours=4):
    c = one_off(id, hours=hours, who="both")
    c.outdoor = True
    return c


def test_a_wet_sunday_keeps_the_mulching_off_it():
    sun = SAT + timedelta(days=1)
    chores = [mulch("remulch-back"), mulch("remulch-front"), mulch("remulch-side")]
    without = plan_periods(chores, PERSONS, {}, START, 1, [], 30, WITH_WEATHER)[0]
    assert sun in {i.start.date() for i in without.plan.items}           # no forecast: Sunday is used

    result = plan_periods(chores, PERSONS, {}, START, 1, [], 30, WITH_WEATHER, forecast=wet_sunday())[0]
    assert {i.start.date() for i in result.plan.items} == {SAT}           # two 4 h jobs fit on Saturday
    assert [why for _, why in result.plan.unplanned] == ["weather whenever it would fit (Sun 01 Nov 08:00: rain 75% > 20%)"]
    assert result.weather == [(SAT, "rain 0%, feels like up to 30°C, UV up to 4"),
                              (sun, "rain 75%, feels like up to 30°C, UV up to 4")]


def test_the_forecast_is_ignored_without_weather_settings():
    result = plan_periods([mulch("m")], PERSONS, {}, SAT + timedelta(days=1), 1, [], 30, PLANNING,
                          forecast=wet_sunday())[0]
    assert result.plan.items and result.weather == []


def test_an_unreadable_forecast_is_retried_then_planned_without(monkeypatch, capsys):
    calls, waits = [], []

    def broken(settings, today):
        calls.append(today)
        raise TimeoutError("timed out")
    monkeypatch.setattr(weather, "fetch_forecast", broken)
    assert jobs.fetch_weather(WITH_WEATHER, SAT, sleep=waits.append) == (None, "TimeoutError: timed out")
    assert len(calls) == 3 and waits == [30, 60]
    assert "planning without it" in capsys.readouterr().err
    assert jobs.fetch_weather(PLANNING, SAT) == (None, None)            # not configured: no error


def test_a_retry_that_works_uses_the_forecast(monkeypatch):
    answers = [TimeoutError("timed out"), wet_sunday()]

    def flaky(settings, today):
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a
    monkeypatch.setattr(weather, "fetch_forecast", flaky)
    assert jobs.fetch_weather(WITH_WEATHER, SAT, sleep=lambda s: None) == (wet_sunday(), None)


def test_refused_settings_are_not_retried(monkeypatch):
    calls = []

    def refuse(settings, today):
        calls.append(today)
        raise weather.WeatherRejected("Open-Meteo refused the weather settings: bad model")
    monkeypatch.setattr(weather, "fetch_forecast", refuse)
    forecast, error = jobs.fetch_weather(WITH_WEATHER, SAT, sleep=lambda s: None)
    assert forecast is None and len(calls) == 1 and error.startswith(jobs.WEATHER_REJECTED)


def test_the_run_log_says_when_the_forecast_was_missing():
    from planner.plans import Changes
    summary = jobs.RunSummary(None, Changes(), [], weather_error="TimeoutError: timed out")
    assert jobs.describe(summary).endswith("Weather forecast unavailable (TimeoutError: timed out): planned without it.")


def test_outdoor_items_get_the_forecast_over_their_time():
    from planner.models import PlannedRecord
    from planner.plans import Changes
    sun = SAT + timedelta(days=1)
    now = datetime.combine(SAT - timedelta(days=1), datetime.min.time())
    kept = PlannedRecord("mulch", ["nicolas"], datetime.combine(sun, datetime.min.time()) + timedelta(hours=8),
                         datetime.combine(sun, datetime.min.time()) + timedelta(hours=10), "k@chore-aisist")
    same = PlannedRecord("weed", ["nicolas"], kept.start, kept.end, "s@chore-aisist",
                         weather="rain 75%, feels like up to 30°C, UV up to 4")
    indoor = PlannedRecord("clean", ["anna"], kept.start, kept.end, "i@chore-aisist", weather="stale")
    new = PlannedRecord("trim", ["anna"], kept.start - timedelta(days=1), kept.end - timedelta(days=1), "n@chore-aisist")
    restamped = jobs.stamp_weather([kept, same, indoor], Changes(add=[new]), now, {"mulch", "weed", "trim"},
                                   wet_sunday())
    assert kept.weather == "rain 75%, feels like up to 30°C, UV up to 4"
    assert new.weather == "rain 0%, feels like up to 30°C, UV up to 4"          # added items: set in place
    assert indoor.weather is None
    assert {r.uid for r in restamped} == {"k@chore-aisist", "i@chore-aisist"}   # only kept items whose text changed


def test_without_a_forecast_items_keep_their_text():
    from planner.models import PlannedRecord
    from planner.plans import Changes
    r = PlannedRecord("mulch", ["nicolas"], datetime(2026, 11, 1, 8), datetime(2026, 11, 1, 10), "k@chore-aisist",
                      weather="rain 10%")
    assert jobs.stamp_weather([r], Changes(), datetime(2026, 10, 30), {"mulch"}, None) == []
    assert r.weather == "rain 10%"
