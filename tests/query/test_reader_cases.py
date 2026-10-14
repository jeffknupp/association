"""The reader's question-to-Reading snapshot cases (Phase 3, step 4, slice
(c)): each case is one call of the parser's door - a question, the names
the normalizer copied, its stat key - and its expected value is the typed
record the stage snapshot writes for the Reading, compared whole. The
cases, their reasons and the warehouse they read against are
``tests/reader_cases.py``'s; ``tests/query/reader_cases.json`` holds them.

A case that moves on purpose is regenerated with the move named in the
commit, as a population baseline is: nothing here is edited by hand.
"""

from __future__ import annotations

import duckdb
import pytest
from reader_cases import load_cases, read, record, warehouse

from association.query.stages import value_differences

CASES = load_cases()


@pytest.fixture(scope="module")
def con() -> duckdb.DuckDBPyConnection:
    return warehouse()


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_the_reader_reads_the_case_as_recorded(con: duckdb.DuckDBPyConnection, case: dict) -> None:
    got = record(read(con, case["question"], case["names"] or None, case["stat"]))
    differences = value_differences("reading", case["expected"], got)
    assert not differences, "\n".join(f"{case['question']!r}: {d.path} {d.before!r} -> {d.after!r}" for d in differences[:12]) + f"\n\nwhy: {case['why']}"


def test_every_case_names_its_reason_and_a_question() -> None:
    """A case is a retired test's wording and reason, kept; nothing here is a bare record."""
    assert len(CASES) == len({case["name"] for case in CASES})
    assert all(case["question"].strip() and case["why"].strip() for case in CASES)


def test_the_planned_shape_is_what_the_point_is_read_as_but_the_game_level_move(con: duckdb.DuckDBPyConnection) -> None:
    """The point declares what it is read as, one :class:`PointShape`
    (``Reading.read_as``, Phase 5, step 4, D9), and the planner plans that
    key as it stands - but in its one move: a season-line point its reader
    does not read, planned at the game level (a ``Query`` whose source is
    the games), is on the player-games relation, its shape and ``by`` kept
    (``compose.plan.point_shape``). Held over every case the reader reads."""
    from dataclasses import replace

    from association.query.compose.core import Query
    from association.query.compose.plan import plan_point, planned_shape

    planned = moved = 0
    for case in CASES:
        reading = read(con, case["question"], case["names"] or None, case["stat"])
        verdict = plan_point(reading)
        key = planned_shape(reading, verdict)
        if key is None:
            assert reading.point is None or reading.refused is not None or reading.point_refusal is not None, case["question"]
            continue
        assert reading.point is not None
        declared = reading.point.read_as
        planned += 1
        if isinstance(verdict, Query) and verdict.source == "games" and declared.relation == "player_seasons":
            moved += 1
            assert key == replace(declared, relation="player_games"), case["question"]
        else:
            assert key == declared, case["question"]
    assert planned > 400 and moved > 0, (planned, moved)
