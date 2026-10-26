import pytest

from planner import config
from planner.cli import check_calendar_people

BASE = {"CALDAV_URL": "https://nextcloud.local/remote.php/dav", "NEXTCLOUD_USER": "app",
        "NEXTCLOUD_APP_PASSWORD": "secret", "BUSY_CALENDARS": "personal=nicolas;family=nicolas,anna",
        "DB_PASSWORD": "x"}


@pytest.fixture
def env(monkeypatch):
    """Isolated environment: no .env file is read."""
    monkeypatch.setattr(config, "load_dotenv", lambda: None)
    for key in list(BASE) + ["EVENT_BUFFER_MINUTES", "CALDAV_TIMEOUT_SECONDS", "CALDAV_CA_CERT", "CHORE_WINDOWS",
                             "EXTRA_DAY_WINDOW", "TRAVEL_KEYWORDS", "NO_CHORES_KEYWORDS", "DAY_OFF_KEYWORDS",
                             "CHORE_TIME_KEYWORDS"]:
        monkeypatch.delenv(key, raising=False)

    def set_env(**values):
        for k, v in values.items():
            monkeypatch.setenv(k, v)
    return set_env


def test_calendar_is_optional(env):
    env(DB_PASSWORD="x")
    assert config.load_calendar_settings() is None


def test_calendar_settings_defaults(env):
    env(**BASE)
    cal = config.load_calendar_settings()
    assert (cal.buffer_minutes, cal.timeout_seconds, cal.ca_cert) == (30, 30, True)
    assert cal.people() == {"nicolas", "anna"}


def test_bad_calendar_settings_do_not_block_database_commands(env):
    env(DB_PASSWORD="x", CALDAV_URL=BASE["CALDAV_URL"])         # calendar half-configured
    assert config.load_settings().db_password == "x"            # database commands still work
    with pytest.raises(ValueError, match="NEXTCLOUD_USER"):
        config.load_calendar_settings()


@pytest.mark.parametrize("key, value", [("EVENT_BUFFER_MINUTES", "-30"), ("CALDAV_TIMEOUT_SECONDS", "0")])
def test_rejects_negative_buffer_and_zero_timeout(env, key, value):
    env(**BASE, **{key: value})
    with pytest.raises(ValueError, match=key):
        config.load_calendar_settings()


def test_unknown_calendar_people_are_rejected():
    check_calendar_people({"nicolas", "anna"}, {"nicolas", "anna"})
    with pytest.raises(ValueError, match="nicholas"):
        check_calendar_people({"nicholas", "anna"}, {"nicolas", "anna"})


def test_caldav_url_must_be_https(env):
    env(**{**BASE, "CALDAV_URL": "http://nextcloud.local/remote.php/dav"})
    with pytest.raises(ValueError, match="https"):
        config.load_calendar_settings()


def test_planning_defaults_are_the_weekend(env):
    planning = config.load_planning_settings()
    assert planning.windows == {5: [(480, 1080)], 6: [(480, 1080)]} and planning.extra_day_window == (480, 1080)


def test_planning_windows_from_env(env):
    env(CHORE_WINDOWS="sat 09:00-17:00; sun 08:00-12:00; wed 18:00-20:00", EXTRA_DAY_WINDOW="09:00-16:00")
    planning = config.load_planning_settings()
    assert planning.windows[2] == [(1080, 1200)] and planning.extra_day_window == (540, 960)


def test_keywords_from_env(env):
    env(**BASE, DAY_OFF_KEYWORDS="Day off, Vacation day", CHORE_TIME_KEYWORDS="bricolage")
    cal = config.load_calendar_settings()
    assert cal.keywords["extra_day"] == ("day off", "vacation day")
    assert cal.keywords["extra_time"] == ("bricolage",) and cal.keywords["away"] == ("travel", "trip", "away")
