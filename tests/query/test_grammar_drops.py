"""No typed value the grammar's text held is dropped silently (Phase 5, step 2).

Every reader case is read with the grammar's typed families recorded
(``tests/grammar_drops.py``): a line, an opponent, a companion, a window or
a date the parser typed into the grammar's text must land on the Reading,
or the Reading must carry a cause the planner says, or the drop is held -
by the rule (a companion who is the subject himself) or by its question in
``grammar_drops.HELD`` with its reason. The same rule and list read the
2,710 readings in ``~/association-research/stages/phase5/grammar_rows_live.py``,
which read 0 live drops at the step's close (25 held by the rule, 6 by
their text). A held question that no longer drops fails here, so the list
only shrinks.
"""

from __future__ import annotations

import duckdb
import pytest
from grammar_drops import HELD, caused, held_by_rule, kept, live_drop, typed_families
from reader_cases import load_cases, read, warehouse

from association.query import names

CASES = load_cases()


@pytest.fixture(scope="module")
def con() -> duckdb.DuckDBPyConnection:
    return warehouse()


@pytest.fixture(scope="module")
def drops(con: duckdb.DuckDBPyConnection) -> dict[str, set[str]]:
    """Each case's live drop, by question (the held ones included), read once."""
    out: dict[str, set[str]] = {}
    # Module-scoped, so outside the per-test block conftest enters.
    with names.loaded(), typed_families() as taken:
        for case in CASES:
            reading = read(con, case["question"], case["names"] or None, case["stat"])
            out[case["question"]] = live_drop(reading, taken())
    return out


def test_no_reader_case_drops_a_typed_value_silently(drops: dict[str, set[str]]) -> None:
    live = {q: d for q, d in drops.items() if d and q not in HELD}
    assert not live, live


def test_every_held_question_is_a_case_and_still_drops(drops: dict[str, set[str]]) -> None:
    assert set(HELD) <= set(drops), sorted(set(HELD) - set(drops))
    assert all(drops[q] for q in HELD), sorted(q for q in HELD if not drops[q])


def test_a_companion_who_is_the_subject_himself_is_held_by_the_rule(con: duckdb.DuckDBPyConnection) -> None:
    """The team's record in the games a companion reached a line: his line
    is the record's own and he its player, so no companion is written."""
    with typed_families() as taken:
        reading = read(con, "What was the 76ers record when Tyrese Maxey scores 20+ points?", ["76ers", "Tyrese Maxey"], "points")
        families = taken()
    assert "companion" in families and "companion" not in kept(reading) and not caused(reading)
    assert held_by_rule(reading, families - kept(reading))
