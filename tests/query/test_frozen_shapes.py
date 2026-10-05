"""Decision D4 of ``ROADMAP.md``: new shapes are frozen while the pipeline
is rebuilt. An intent or a per-intent renderer retires with its slice; none
is added. The other directions the roadmap set are held by
``scripts/check_ratchets.py``.

Phase 2 (2026-10-03 to 2026-10-05) removed what the rest of this file froze:
the twelve templates, the presenters and ``compose/present.py``, the ten
adapters and ``compose/adapt.py``, and - with step 6 - ``HONORED_SCOPING``,
``check_scope`` and the ``templates`` package itself. Their freezes went with
them. The scoping declarations that remain are the per-relation cell tables
the roadmap keeps and the planner's ``STATED_SCOPING``, which Phase 3's cells
replace."""

from __future__ import annotations

from typing import Any

from association.query.compose import COMPILED_INTENTS
from association.query.parse import PARENT_GRAMMAR
from association.query.router import CODE_ASSIGNED_INTENTS
from association.query.subject import KIND_ASSIGNED_INTENTS

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
    return set(COMPILED_INTENTS) | set(CODE_ASSIGNED_INTENTS) | set(KIND_ASSIGNED_INTENTS) | {row[-1] for row in PARENT_GRAMMAR}


def test_no_intent_is_added_and_a_retired_one_leaves_the_list() -> None:
    named = _named_today()
    assert named - FROZEN == set(), "a new intent: ROADMAP.md decision D4 freezes new shapes until the pipeline is rebuilt"
    assert FROZEN - named == set(), "an intent retired: remove it from FROZEN so it cannot come back"


# D4 names more than the intents: no new per-intent renderer either (Phase
# 1's order from here, step 4), held the same way - the set as it stood,
# which may lose a member with its slice and gain none. The page's renderers
# become one per shape in Phase 4.
FROZEN_RENDERERS = frozenset(
    {
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


def _renderers_today() -> set[str]:
    import re
    from pathlib import Path

    import association.web

    page = (Path(association.web.__file__).parent / "static" / "index.html").read_text()
    block = re.search(r"const RENDERERS = \{(.*?)\n  \};", page, re.S)
    assert block is not None, "could not find the RENDERERS table in index.html"
    return set(re.findall(r"^    (\w+): \{", block.group(1), re.M))


def _frozen(name: str, today: set[Any], frozen: frozenset[Any]) -> None:
    assert today - frozen == set(), f"a new {name}: ROADMAP.md decision D4 freezes new shapes until the pipeline is rebuilt"
    assert frozen - today == set(), f"a {name} retired: remove it from the frozen set so it cannot come back"


def _query_sources(pattern: str) -> list[str]:
    from pathlib import Path

    import association.query

    root = Path(association.query.__file__).parent
    return sorted(str(path.relative_to(root)) for path in root.glob(pattern))


def test_no_presenter_or_renderer_is_added() -> None:
    # Every presenter is retired (the player relation's in slice (iii), the
    # team's - with_without's, a team's streak, a team's record over its own
    # line - in slice (iv), 2026-10-05), and compose/present.py with them:
    # each shape is a reader and the sayer, and no presenter comes back. The
    # templates package went with step 6, the adapters with slice (ii): no
    # template or adapter comes back either.
    #
    # Asked of the source files, never of the import system: an ignored
    # ``templates/__pycache__`` left behind in a checkout is a namespace
    # package to ``importlib.util.find_spec``, and failed this test in the
    # main checkout with nothing wrong in the tree (2026-10-05, #333). A
    # ``.pyc`` is not a ``.py``, so a stale cache cannot fail it now.
    assert _query_sources("compose/present.py") == [], "a presenter came back: ROADMAP.md decision D4"
    assert _query_sources("compose/adapt.py") == [], "an adapter came back: ROADMAP.md decision D4"
    assert _query_sources("templates/**/*.py") == [], "a template came back: ROADMAP.md decision D4"
    _frozen("renderer", _renderers_today(), FROZEN_RENDERERS)
