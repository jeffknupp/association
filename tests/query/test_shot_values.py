"""The shot coordinate frame and the shot-value rule, pinned against real rows.

pytest runs offline, so the frame cannot be checked against the warehouse here.
What it can do is hold on to real observations: the rows below are copied
verbatim from ``shot_chart`` (seasons 2006, 2016 and 2023), each with the
distance ESPN wrote into its own description and the value it labeled it with.
A frame that is off - the rim at (25, 5.25), where it sat until 2.1.0 -
disagrees with both.
"""

import re

import duckdb
import pytest

from association.query.court import BEYOND_THE_ARC_SQL, HAS_POSITION_SQL, SHOT_DISTANCE_SQL
from association.query.shotchart import SHOT_VALUE_SQL, TEXT_NAMES_EVERY_THREE_UNTIL, UNSEPARABLE_SHOT_VALUES

# (season, x, y, points_attempted, shot_type, description) - real rows: a
# corner three, a three from the top, a long two just inside the line and a
# short two, per season.
REAL_SHOTS = [
    (2006, 2, -2, 3, "Jump Shot", "Peja Stojakovic makes 23-foot three point jumper (Sarunas Jasikevicius assists)"),
    (2006, 28, 26, 3, "Jump Shot", "Earl Watson misses 26-foot three point jumper"),
    (2006, 46, 1, 2, "Jump Shot", "Morris Peterson makes 21-foot jumper (Mike James assists)"),
    (2006, 26, 5, 2, "Jump Shot", "Chris Kaman makes 5-foot two point shot"),
    (2016, 48, 1, 3, "Jump Shot", "LeBron James  misses 23-foot three point jumper "),
    (2016, 24, 26, 3, "Jump Shot", "Nikola Mirotic makes 26-foot  three point jumper  (Jimmy Butler assists)"),
    (2016, 16, 19, 2, "Jump Shot", "Dennis Schroder makes 21-foot jumper"),
    (2016, 24, 2, 2, "Layup Shot", "Eric Moreland blocks Jason Thompson 's 2-foot  layup"),
    (2023, 2, 2, 3, "Jump Shot", "Bojan Bogdanovic makes 23-foot three point jumper (Alec Burks assists)"),
    (2023, 24, 27, 3, "Pullup Jump Shot", "De'Anthony Melton misses 27-foot three point pullup jump shot"),
    (2023, 44, 9, 2, "Pullup Jump Shot", "Kyrie Irving makes 21-foot pullup jump shot"),
    (2023, 27, 2, 2, "Cutting Dunk Shot", "Trey Lyles makes 2-foot dunk (Malik Monk assists)"),
]


def _con(rows: list[tuple[object, ...]]) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE shot_chart (season INTEGER, coordinate_x INTEGER, coordinate_y INTEGER, points_attempted INTEGER, shot_type VARCHAR, description VARCHAR)")
    con.executemany("INSERT INTO shot_chart VALUES (?, ?, ?, ?, ?, ?)", rows)
    return con


def _value(season: int, x: int | None, y: int | None, points: int, description: str, shot_type: str = "Jump Shot") -> int | None:
    row = _con([(season, x, y, points, shot_type, description)]).execute(f"SELECT {SHOT_VALUE_SQL} FROM shot_chart").fetchone()
    assert row is not None
    value: int | None = row[0]
    return value


@pytest.mark.parametrize("row", REAL_SHOTS, ids=lambda r: f"{r[0]}-{r[1]},{r[2]}")
def test_distance_is_measured_from_where_the_data_puts_the_rim(row: tuple[object, ...]) -> None:
    """Through 2012 the description's distance IS the rounded distance from the
    rim, exactly; later coordinates are rounded from a finer position, so within
    a foot. From (25, 5.25) every one of these is several feet short."""
    season, x, y, _, _, description = row
    match = re.search(r"(\d+)-foot", str(description))
    assert match is not None
    described = int(match.group(1))
    computed = _con([row]).execute(f"SELECT {SHOT_DISTANCE_SQL} FROM shot_chart").fetchone()
    assert computed is not None
    if isinstance(season, int) and season <= 2012:
        assert round(computed[0]) == described, (x, y)
    else:
        assert abs(computed[0] - described) <= 1, (x, y)


@pytest.mark.parametrize("row", REAL_SHOTS, ids=lambda r: f"{r[0]}-{r[1]},{r[2]}")
def test_the_three_point_line_separates_espns_own_labels(row: tuple[object, ...]) -> None:
    """The long twos here sit at 21 feet and the corner threes at 23, so a line
    drawn a few feet off in either direction gets some of them wrong."""
    beyond = _con([row]).execute(f"SELECT {BEYOND_THE_ARC_SQL} FROM shot_chart").fetchone()
    assert beyond is not None
    assert beyond[0] == (row[3] == 3)


def test_a_label_wins_over_everything_else() -> None:
    assert _value(2023, 25, 5, 3, "misses 5-foot jumper") == 3


def test_a_free_throw_is_a_free_throw_even_unlabeled_and_positioned() -> None:
    """2002, 2003 and 2022 leave free throws unlabeled, and 2002-2018 give them
    a position under the rim - which read as a layup until they were excluded
    by what they are rather than by a missing coordinate."""
    assert _value(2022, 25, 0, 0, "makes free throw 1 of 2", shot_type="Free Throw - 1 of 2") == 1


def test_unlabeled_shots_are_read_from_the_description_first() -> None:
    """ "Three point" in the text outranks a position inside the line, and "two
    point" one beyond it: ESPN wrote the value down, just not in the column."""
    assert _value(2022, 25, 10, 0, "misses 24-foot three point jumper") == 3
    assert _value(2022, 25, 26, 0, "makes two point shot") == 2


def test_unlabeled_shots_after_the_text_era_are_read_from_the_line() -> None:
    season = TEXT_NAMES_EVERY_THREE_UNTIL + 1
    assert _value(season, 2, 3, 0, "misses 23-foot step back jumpshot") == 3  # corner
    assert _value(season, 25, 26, 0, "misses running pullup jump shot") == 3  # top of the arc
    assert _value(season, 25, 20, 0, "misses 20-foot pullup jump shot") == 2


def test_unlabeled_shots_in_the_text_era_are_twos_wherever_they_sit() -> None:
    """Through 2012 the description names every three, so a shot it does not
    call one is a two - even one whose whole-foot position reads 24 feet. The
    line would call it a three, and was measured worse there than the text."""
    assert _value(TEXT_NAMES_EVERY_THREE_UNTIL, 25, 24, 0, "misses 24-foot jumper") == 2


def test_a_shot_with_no_position_and_no_label_has_no_value() -> None:
    assert _value(2022, 0, 0, 0, "misses jumper") is None
    assert _value(2022, None, None, 0, "misses jumper") is None


@pytest.mark.parametrize("season", sorted(UNSEPARABLE_SHOT_VALUES))
def test_an_unseparable_season_leaves_undescribed_shots_without_a_value(season: int) -> None:
    """Guards the SQL against drifting from the table that explains it: a
    season listed as unseparable must actually come out NULL, not as a two."""
    assert _value(season, 47, 0, 0, "made Jumper.") is None
    assert _value(season, 25, 25, 0, "made 25 ft Three Point Jumper.") == 3


def test_the_sideline_origin_is_not_a_position() -> None:
    con = _con([(2002, 0, 0, 0, "Jump Shot", "Doug Christie made Two Pointer."), (2002, 25, 0, 0, "Layup Shot", "made Layup."), (2023, None, None, 2, "Jump Shot", "x")])
    assert con.execute(f"SELECT COUNT(*) FROM shot_chart WHERE {HAS_POSITION_SQL}").fetchone() == (1,)
