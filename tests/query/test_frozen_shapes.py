"""Decision D4 of ``ROADMAP.md``: new shapes are frozen while the pipeline
is rebuilt. An intent, a template, a presenter, a scoping table or a
per-intent renderer retires with its slice; none is added. The other
directions the roadmap set are held by ``scripts/check_ratchets.py``."""

from __future__ import annotations

from typing import Any

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


# D4 names more than the intents: no new template, presenter, scoping table
# or per-intent renderer either (Phase 1's order from here, step 4). Each is
# held the same way - the set as it stood, which may lose a member with its
# slice and gain none.

FROZEN_PRESENTERS = frozenset({"leaderboard", "player_compare", "player_history", "player_matchup", "player_stat", "single_game_high", "streak", "threshold_count"})
FROZEN_TEAM_ONLY_PRESENTERS = frozenset({"with_without"})
FROZEN_ADAPTERS = frozenset({"player_matchup", "single_game_high", "streak", "threshold_count", "with_without"})
FROZEN_TEMPLATES = frozenset(
    {
        "coach",
        "fingerprint",
        "head_to_head",
        "period_leaderboard",
        "player_netpoints",
        "shot_chart",
        "shot_distance",
        "team_leaderboard",
        "team_outlook",
        "team_quarter_points",
        "team_record",
        "team_stat",
    }
)
# Every module-level declaration of what a reader honors, states or
# excludes, by module and name: the six the roadmap counted and their
# relatives. A seventh fails here.
FROZEN_SCOPING_TABLES = frozenset(
    {
        ("compose.adapt", "WITH_WITHOUT_STATED"),
        ("compose.core", "COMPILER_SLOTS"),
        ("compose.present", "STATED_SCOPING"),
        ("templates.common", "SCOPING_SLOTS"),
        ("templates.common", "RELATION_SCOPING"),
        ("templates.common", "RELATION_SCOPING_EXCLUDED"),
        ("templates.common", "TEAM_RELATION_SCOPING"),
        ("templates.common", "TEAM_RELATION_SCOPING_EXCLUDED"),
        ("templates.common", "HONORED_SCOPING"),
        ("templates.common", "_BOX_SCORE_SCOPING"),
        ("templates.shots", "_SHOTS_GAME_NARROWING_SLOTS"),
        ("router", "_MODEL_SLOTS"),
        ("compose.plan", "_TEAM_READER_REFUSES"),
        ("templates.splits", "_CONDITION_PLAYER_ONLY_CELLS"),
    }
)
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


def _scoping_tables_today() -> set[tuple[str, str]]:
    """Every module-level name under ``query/`` that looks like a scoping
    declaration, read from the source: a table nobody imports yet is still a
    table."""
    import ast
    import re
    from pathlib import Path

    import association.query

    root = Path(association.query.__file__).parent
    found: set[tuple[str, str]] = set()
    for path in sorted(root.rglob("*.py")):
        module = ".".join(part for part in path.relative_to(root).with_suffix("").parts if part != "__init__")
        for node in ast.parse(path.read_text()).body:
            names = (
                [t.id for t in node.targets if isinstance(t, ast.Name)]
                if isinstance(node, ast.Assign)
                else [node.target.id]
                if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                else []
            )
            # Widened 2026-10-03 (the Phase 1 review): two tables of cells a
            # reader refuses matched none of the first three words.
            found.update((module, name) for name in names if re.search(r"SCOPING|STATED|_SLOTS$|CELLS|REFUSES|HONOR|EXCLUDED", name))
    return found


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


def test_no_template_presenter_scoping_table_or_renderer_is_added() -> None:
    from association.query.compose.adapt import _ADAPTERS
    from association.query.compose.present import PRESENTERS, TEAM_ONLY_PRESENTERS

    _frozen("template", set(TEMPLATES), FROZEN_TEMPLATES)
    _frozen("presenter", set(PRESENTERS), FROZEN_PRESENTERS)
    _frozen("team-only presenter", set(TEAM_ONLY_PRESENTERS), FROZEN_TEAM_ONLY_PRESENTERS)
    _frozen("adapter", set(_ADAPTERS), FROZEN_ADAPTERS)
    _frozen("scoping table", _scoping_tables_today(), FROZEN_SCOPING_TABLES)
    _frozen("renderer", _renderers_today(), FROZEN_RENDERERS)
