"""Tests for season naming and dating."""

from datetime import date, datetime, timedelta, timezone

import duckdb
import pytest

from association.season import eastern_date, eastern_date_sql, eastern_day_utc_range


def test_a_game_is_dated_by_the_day_it_was_played() -> None:
    """An evening tip is stored as the next UTC day; four answers printed that."""
    assert eastern_date("2026-04-13T00:30Z") == "2026-04-12"
    assert eastern_date("2026-04-12T22:00Z") == "2026-04-12"
    assert eastern_date("2026-04-12") == "2026-04-12"


def test_a_date_only_stamp_is_dated_the_day_it_names() -> None:
    """ESPN stores a game with no tip time as midnight Eastern. A fixed
    five-hour shift put every summer one (04:00Z, under EDT) on the day before:
    the Pistons' 1990 title clincher, 14 June, printed as 13 June."""
    assert eastern_date("1990-06-14T04:00Z") == "1990-06-14"
    assert eastern_date("1990-01-14T05:00Z") == "1990-01-14"
    # The same stamp in winter is a real 11pm EST tip, and it is the day before.
    # 2026 holds ten of these; this is why the rule cannot be "04:00Z is a date".
    assert eastern_date("2025-11-05T04:00Z") == "2025-11-04"


def test_the_changeover_days_follow_the_rules_of_their_year() -> None:
    # 2007 moved the start from April to the second Sunday of March.
    assert eastern_date("2006-03-13T04:00Z") == "2006-03-12"
    assert eastern_date("2007-03-12T04:00Z") == "2007-03-12"
    # The Sunday itself is still standard time at 04:00Z; the Sunday it ends is not.
    assert eastern_date("2007-03-11T04:00Z") == "2007-03-10"
    assert eastern_date("2007-11-04T04:00Z") == "2007-11-04"
    assert eastern_date("2007-11-05T04:00Z") == "2007-11-04"


def _stamps() -> list[str]:
    day, stamps = date(1976, 1, 1), []
    while day < date(2040, 1, 1):
        stamps += [f"{day.isoformat()}T{clock}Z" for clock in ("00:30", "03:59", "04:00", "04:59", "05:00", "23:30")]
        day += timedelta(days=1)
    return stamps


def test_the_sql_rule_and_the_python_rule_agree_on_every_day() -> None:
    """A query that filters on one and an answer that prints the other must
    never put a game on two different days."""
    con = duckdb.connect()
    con.execute("CREATE TABLE t (date VARCHAR)")
    con.executemany("INSERT INTO t VALUES (?)", [(s,) for s in _stamps()])
    rows = con.execute(f"SELECT date, CAST({eastern_date_sql('date')} AS VARCHAR) FROM t").fetchall()
    assert [(stamp, eastern_date(stamp)) for stamp, _ in rows] == rows


def test_the_hand_written_daylight_rules_match_the_tz_database() -> None:
    """The rules are written out so a machine with no tz database still dates
    games; this proves they are the real ones wherever a database exists."""
    zoneinfo = pytest.importorskip("zoneinfo")
    try:
        new_york = zoneinfo.ZoneInfo("America/New_York")
    except zoneinfo.ZoneInfoNotFoundError:
        pytest.skip("no tz database on this machine")
    for stamp in _stamps():
        real = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(new_york).date().isoformat()
        assert eastern_date(stamp) == real, stamp
    day = date(1976, 1, 1)
    while day < date(2040, 1, 1):
        start, end = (datetime.combine(d, datetime.min.time(), new_york).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ") for d in (day, day + timedelta(days=1)))
        assert eastern_day_utc_range(day.isoformat()) == (start, end), day
        day += timedelta(days=1)
