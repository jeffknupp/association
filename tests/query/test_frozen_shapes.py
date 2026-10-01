"""Decision D4 of ``ROADMAP.md``: new shapes are frozen while the pipeline
is rebuilt. An intent retires with its slice; none is added. The other
directions the roadmap set are held by ``scripts/check_ratchets.py``."""

from __future__ import annotations

from association.query.compose import COMPILED_INTENTS
from association.query.parse import PARENT_GRAMMAR
from association.query.router import CODE_ASSIGNED_INTENTS
from association.query.subject import KIND_ASSIGNED_INTENTS
from association.query.templates import TEMPLATES

# Every intent the reader could name on 2026-09-30. Remove a name when its
# slice deletes it; adding one is a decision the roadmap has to change for.
FROZEN = frozenset(
    {
        "coach",
        "fingerprint",
        "game_log",
        "head_to_head",
        "leaderboard",
        "period_leaderboard",
        "period_split",
        "player_compare",
        "player_history",
        "player_matchup",
        "player_netpoints",
        "player_splits",
        "player_stat",
        "record_when",
        "shot_chart",
        "shot_distance",
        "single_game_high",
        "streak",
        "team_leaderboard",
        "team_outlook",
        "team_quarter_points",
        "team_record",
        "team_stat",
        "threshold_count",
        "with_without",
    }
)


def _named_today() -> set[str]:
    return set(TEMPLATES) | set(COMPILED_INTENTS) | set(CODE_ASSIGNED_INTENTS) | set(KIND_ASSIGNED_INTENTS) | {row[-1] for row in PARENT_GRAMMAR}


def test_no_intent_is_added_and_a_retired_one_leaves_the_list() -> None:
    named = _named_today()
    assert named - FROZEN == set(), "a new intent: ROADMAP.md decision D4 freezes new shapes until the pipeline is rebuilt"
    assert FROZEN - named == set(), "an intent retired: remove it from FROZEN so it cannot come back"
