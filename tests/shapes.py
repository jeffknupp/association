"""The answer side's keys, by the retired words the tests still name a shape
with. Since Phase 3, step 1 the planner and the answer side key a reader,
the cells its words state and its coverage floor by the point's own
:class:`~association.query.reading.PointShape`, and nothing in ``src``
looks one up by a template's name; the tests that assert what a shape's
words state (``compose.plan.cells_stated``) and what floor it is held to still speak
in those names until step 4 re-seats them as question-to-Reading cases,
and this is the one table that translates. Where several shapes share the
words (a player's log and a team's), ``key`` gives the player relation's,
and a test about the team's names its key outright.
"""

from __future__ import annotations

from association.query import reading
from association.query.compose.plan import cells_stated, cells_unhonored
from association.query.reading import PointShape, Scope

KEYS: dict[str, PointShape] = {
    "game_log": PointShape("player_games", "rows", "date"),
    "player_stat": PointShape("player_games", "scalar", "line"),
    "record_when": PointShape("player_games", "split", "line"),
    "player_splits": PointShape("player_games", "split", "splits"),
    "threshold_count": PointShape("player_games", "scalar", "count"),
    "single_game_high": PointShape("player_games", "rows", "measure"),
    "streak": PointShape("player_games", "runs", "line"),
    "player_matchup": PointShape("player_games", "comparison", "met"),
    "period_split": PointShape("player_periods", "rows", "date"),
    "period_leaderboard": PointShape("player_periods", "ranking", "player"),
    "leaderboard": PointShape("player_seasons", "ranking", "player"),
    "player_history": PointShape("player_seasons", "split", "season"),
    "player_compare": PointShape("player_seasons", "comparison", "subject"),
    "with_without": PointShape("team_games", "split", "presence"),
    "head_to_head": PointShape("team_games", "comparison", "opponent"),
    "team_quarter_points": PointShape("team_periods", "scalar", "total"),
    "team_record": PointShape("team_games", "scalar", "record"),
    "team_stat": PointShape("team_seasons", "scalar", "line"),
    "team_leaderboard": PointShape("team_seasons", "ranking", "team"),
    "team_outlook": PointShape("team_snapshots", "scalar", "projection"),
    "player_netpoints": PointShape("netpoints", "scalar", "ratings"),
    "fingerprint": PointShape("netpoints", "chart", "fingerprint"),
    "shot_chart": PointShape("shots", "chart", "shots"),
    "shot_distance": PointShape("shots", "scalar", "distance"),
}


#: What the words ask, by the retired intent's name (Phase 3, step 4: the
#: grammar names a :class:`~association.query.reading.PointShape`, and the
#: tests that still speak in the retired names translate here, the one
#: test-side table, until slice (c) re-seats them).
ASKED: dict[str, PointShape | None] = {
    "game_log": reading.PLAYER_LOG,
    "player_stat": reading.PLAYER_LINE,
    "player_splits": reading.PLAYER_SPLITS,
    "record_when": reading.LINE_RECORD,
    "period_split": reading.PERIOD_LOG,
    "threshold_count": reading.GAMES_COUNTED,
    "single_game_high": reading.GAME_HIGHS,
    "streak": reading.LINE_RUNS,
    "player_matchup": reading.PLAYER_MEETINGS,
    "with_without": reading.PRESENCE_SPLIT,
    "head_to_head": reading.TEAM_MEETINGS,
    "team_quarter_points": reading.TEAM_PERIOD_TOTAL,
    "period_leaderboard": reading.PERIOD_RANKING,
    "team_record": reading.TEAM_RECORD,
    "player_netpoints": reading.NETPOINTS_RATINGS,
    "fingerprint": reading.NETPOINTS_FINGERPRINT,
    "shot_chart": reading.SHOT_CHART,
    "shot_distance": reading.SHOT_DISTANCE,
    "leaderboard": reading.PLAYER_RANKING,
    "player_compare": reading.PLAYER_COMPARISON,
    "player_history": reading.SEASON_HISTORY,
    "team_stat": reading.TEAM_LINE,
    "team_leaderboard": reading.TEAM_RANKING,
    "team_outlook": reading.TEAM_OUTLOOK,
    "coach": reading.TEAM_COACH,
    "other": None,
}


def asked(words: str) -> PointShape | None:
    """What the words ask, by the retired intent's name: the grammar's key
    (``None`` for ``other``), which the label table names back
    (``reading.asked_label``)."""
    found = ASKED[words]
    assert reading.asked_label(found) == words, f"{words!r} names {found}, which the table labels {reading.asked_label(found)!r}"
    return found


def key(words: str) -> PointShape:
    """The shape a retired template's words name (the player relation's where two share them)."""
    return KEYS[words]


def stated(words: str) -> frozenset[str]:
    """What the shape named by ``words`` states (``compose.plan.cells_stated``)."""
    return cells_stated(KEYS[words])


def unhonored(words: str, scope: Scope) -> list[str]:
    """The slot names ``scope`` sets beyond what the shape named by ``words``
    states (``compose.plan.cells_unhonored``) - where ``reading.unhonored_scoping``
    over that shape's ``STATED_SCOPING`` row stood until Phase 3, step 2's
    closing slice."""
    return [slot for slot, _, _ in cells_unhonored(scope, KEYS[words])]
