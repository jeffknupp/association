"""The answer side's keys, by the retired words the tests still name a shape
with. Since Phase 3, step 1 the planner and the answer side key a reader,
its stated scoping and its coverage floor by the point's own
:class:`~association.query.reading.PointShape`, and nothing in ``src``
looks one up by a template's name; the tests that assert what a shape's
words state (``STATED_SCOPING``) and what floor it is held to still speak
in those names until step 4 re-seats them as question-to-Reading cases,
and this is the one table that translates. Where several shapes share the
words (a player's log and a team's), ``key`` gives the player relation's,
and a test about the team's names its key outright.
"""

from __future__ import annotations

from association.query.compose.plan import STATED_SCOPING
from association.query.reading import PointShape

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


def key(words: str) -> PointShape:
    """The shape a retired template's words name (the player relation's where two share them)."""
    return KEYS[words]


def stated(words: str) -> frozenset[str]:
    """What the shape named by ``words`` states (``compose.plan.STATED_SCOPING``)."""
    return STATED_SCOPING[KEYS[words]]
