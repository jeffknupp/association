"""The coverage checks read the typed filters (ROADMAP plan item 6, step
(d)): every shared step takes the Reading's or the query's own filters
(:class:`~association.query.reading.Filtered`). The three coverage checks
took a slot dict too until Phase 3, step 4, slice (b), and a test held them
to answer a scope and its slot dict alike; the slot door is the tests' own
since (``tests/slots.py``), and that test went with it.
"""

from __future__ import annotations

from slots import slots_scope

from association.query.coverage import sources_for
from association.query.reading import Measure, PointShape, Reading, Span
from association.query.reading import Subject as Who


def test_a_team_season_line_names_the_games_table_only_where_it_reads_opponent_points() -> None:
    """ISSUES.md #227: "how many 3 pointers did the sixers make in the 2001
    playoffs" reads ESPN's own 23-game season line, and carried the games
    table's note that the run "reads 16 games against the 23" - a caveat
    about a table the figure was not read from. The games table stays
    declared where the read needs opponent points: the whole line (its
    ratings and points allowed are dashed where the tally is short) and a
    metric built on them."""
    line = PointShape("team_seasons", "scalar", "line")
    assert sources_for(
        line, Reading(who=Who(kind="team", teams=("Philadelphia 76ers",)), measure=Measure(key="threePointFieldGoalsMade", as_typed="3 pointers made"), span=Span(season=2001, season_type=3))
    ) == ("team_season_stats",)
    assert sources_for(line, Reading(who=Who(kind="team", teams=("Philadelphia 76ers",)), measure=Measure(key="points", as_typed="points"), span=Span(season=2001, season_type=3))) == (
        "team_season_stats",
    )
    for stat in (None, "point differential", "points allowed", "defensive rating"):
        assert sources_for(line, slots_scope({"team": "Philadelphia 76ers", "stat": stat, "season": 2001, "season_type": 3})) == ("team_season_stats", "games"), stat
