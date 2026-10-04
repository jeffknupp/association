"""``query/measures.py``: the measures' names, one definition for both sides."""

from __future__ import annotations

from association.query import measures


def test_the_sql_that_computes_each_measure_is_keyed_by_the_names_the_reader_reads() -> None:
    """ROADMAP.md, Phase 1, the read_point move, step 2: the measures are
    closed by name in ``measures`` (what the reader reads a question into)
    and computed on the answer side under exactly those names - one
    definition each, held together here rather than by the reader importing
    the SQL."""
    from association.query.compose.core import DERIVED
    from association.query.compose.team import GAME_MEASURES, SEASON_MEASURES
    from association.query.templates.common import HISTORY_COLUMNS

    assert frozenset(DERIVED) == measures.DERIVED_MEASURES
    assert measures.BOOLEAN_MEASURES <= measures.DERIVED_MEASURES
    assert frozenset(GAME_MEASURES) == measures.TEAM_GAME_MEASURES and frozenset(SEASON_MEASURES) == measures.TEAM_SEASON_MEASURES
    assert frozenset(HISTORY_COLUMNS) == measures.HISTORY_STATS
    # Phase 2, step 1: the season line's and the threshold's stat names, and
    # the log's extra columns, closed in measures too.
    from association.query.player_games import STARTER_SIDES
    from association.query.reading import STARTER_SIDES as READING_STARTER_SIDES
    from association.query.templates.common import PLAYER_STAT_COLUMNS, THRESHOLD_STAT_COLUMNS

    assert frozenset(PLAYER_STAT_COLUMNS) == measures.PLAYER_STAT_NAMES
    assert frozenset(THRESHOLD_STAT_COLUMNS) == measures.THRESHOLD_STAT_NAMES
    assert READING_STARTER_SIDES is STARTER_SIDES
    # A derived measure that is a line on one column says so once, and the
    # SQL agrees: "fouled out" is fouls >= 6.
    for name, (column, threshold) in measures.DERIVED_LINES.items():
        assert DERIVED[name] == f"(pgl.{column} >= {threshold})"
    assert set(measures.LINE) <= measures.GAME_COLUMNS
