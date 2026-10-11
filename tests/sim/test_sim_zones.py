"""The zone rule on a dozen points - the restricted/paint/mid-range radii,
the corner against the above-the-break three on both sides of the corner's
top, the label winning over the geometry, the heave by clock and by type,
the unknown-position shots and the free throw - in Python and in SQL."""

import duckdb
import pyarrow as pa
import pytest

from association.sim import params, zones

# (x, y, value, clock_remaining, shot_type) -> zone
CASES: list[tuple[tuple[int | None, int | None, int | None, float | None, str], str | None]] = [
    ((25, 1, 2, 500.0, "Layup Shot"), "restricted"),  # a foot from the rim
    ((28, 2, 2, 500.0, "Layup Shot"), "restricted"),  # 3.6 feet
    ((25, 4, 2, 500.0, "Jump Shot"), "paint"),  # exactly 4 feet: the paint starts here
    ((25, 13, 2, 500.0, "Jump Shot"), "paint"),
    ((25, 14, 2, 500.0, "Jump Shot"), "midrange"),  # exactly 14 feet: mid-range starts here
    ((25, 23, 2, 500.0, "Jump Shot"), "midrange"),  # 23 feet, inside the 23.5 cutoff
    ((25, 24, 3, 500.0, "Jump Shot"), "above_break_three"),
    ((3, 3, 3, 500.0, "Jump Shot"), "corner_three"),  # 22 feet across, 3 up: the corner
    ((2, 8, 3, 500.0, "Jump Shot"), "corner_three"),  # 8 up, under the corner's top at 8.95
    ((3, 9, 3, 500.0, "Jump Shot"), "above_break_three"),  # 9 up: past the corner's top, 23.8 feet out
    ((47, 3, 3, 500.0, "Jump Shot"), "corner_three"),  # the other corner
    ((25, 24, 2, 500.0, "Jump Shot"), "midrange"),  # labeled a two past the line: the label wins
    ((25, 22, 3, 500.0, "Jump Shot"), "above_break_three"),  # labeled a three inside it: the label wins
    ((25, 40, 3, 2.9, "Jump Shot"), "heave"),  # 40 feet with 2.9 seconds left
    ((25, 40, 3, 3.0, "Jump Shot"), "above_break_three"),  # 3.0 seconds: a shot
    ((25, 40, 3, None, "Jump Shot"), "above_break_three"),  # no clock: never a geometric heave
    ((25, 34, 3, 1.0, "Jump Shot"), "above_break_three"),  # 34 feet: inside the heave distance
    ((10, 5, 3, 600.0, "Heave Jump Shot"), "heave"),  # typed: a heave wherever and whenever (every one of 2026's 1,153 is labeled a three)
    ((None, None, 2, 500.0, "Jump Shot"), "unknown_two"),
    ((0, 0, 3, 500.0, "Jump Shot"), "unknown_three"),  # (0, 0) is "no position" in the older seasons
    ((0, 0, None, 500.0, "Jump Shot"), None),  # neither a position nor a value
    ((25, 1, 1, 500.0, "Free Throw - 1 of 2"), None),  # a free throw has no zone
    ((25, 1, None, 500.0, "Free Throw - 1 of 2"), None),
    ((25, 20, None, 500.0, "Jump Shot"), "midrange"),  # unlabeled: the geometry decides
    ((25, 25, None, 500.0, "Jump Shot"), "above_break_three"),
]


@pytest.mark.parametrize(("point", "zone"), CASES)
def test_zone_of(point: tuple[int | None, int | None, int | None, float | None, str], zone: str | None) -> None:
    assert zones.zone_of(*point) == zone


def test_every_zone_is_worth_what_its_label_says() -> None:
    for (x, y, value, clock, shot_type), zone in CASES:
        if zone is not None and value in (2, 3):
            assert params.value_of(zone) == value, (x, y, value, clock, shot_type, zone)


def test_the_sql_rule_agrees_with_the_python_rule_on_every_case() -> None:
    con = duckdb.connect()
    rows = [{"n": i, "coordinate_x": x, "coordinate_y": y, "shot_value": v, "clock_remaining": c, "shot_type": t} for i, ((x, y, v, c, t), _) in enumerate(CASES)]
    schema = pa.schema([("n", pa.int64()), ("coordinate_x", pa.int64()), ("coordinate_y", pa.int64()), ("shot_value", pa.int64()), ("clock_remaining", pa.float64()), ("shot_type", pa.string())])
    con.register("shots", pa.Table.from_pylist(rows, schema=schema))
    sql = zones.zone_sql("shot_value", "clock_remaining", "shot_type")
    got = dict(con.execute(f"SELECT n, {sql} FROM shots").fetchall())
    assert [got[i] for i in range(len(CASES))] == [zone for _, zone in CASES]


def test_has_position_reads_null_and_the_sideline_point_as_none() -> None:
    assert not zones.has_position(None, None)
    assert not zones.has_position(0, 0)
    assert zones.has_position(0, 1)
    assert zones.has_position(25, 0)
