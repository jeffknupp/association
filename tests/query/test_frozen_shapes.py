"""Decision D4 of ``ROADMAP.md``: new shapes are frozen while the pipeline
is rebuilt. An intent or a per-intent renderer retires with its slice; none
is added. The other directions the roadmap set are held by
``scripts/check_ratchets.py``.

Phase 2 (2026-10-03 to 2026-10-05) removed what the rest of this file froze:
the twelve templates, the presenters and ``compose/present.py``, the ten
adapters and ``compose/adapt.py``, and - with step 6 - ``HONORED_SCOPING``,
``check_scope`` and the ``templates`` package itself. Their freezes went with
them. Twelve scoping declarations remain, and are frozen by module and name
(:data:`FROZEN_SCOPING_TABLES`): the four per-relation cell tables the
roadmap keeps, the planner's ``STATED_SCOPING`` with its ``with_without``
row, which Phase 3's cells replace, and six that are debt, each labeled with
the step that owes its deletion."""

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


# Every module-level declaration of which scoping slots a read honors,
# states, excludes or refuses, by module and name, as it stood after Phase 2
# (restored 2026-10-05, #330: step 6 deleted this freeze while 12 of its 14
# entries were still in src). Each is labeled with why it is still here:
CELL_TABLE = "a per-relation cell table: stays through Phase 4"
STATED = "STATED_SCOPING: Phase 3's cells (the planner's checks over the Reading's typed filters) replace it"
FROZEN_SCOPING_TABLES: dict[tuple[str, str], str] = {
    ("player_relation", "RELATION_SCOPING"): CELL_TABLE,
    ("player_relation", "RELATION_SCOPING_EXCLUDED"): CELL_TABLE,
    ("team_relation", "TEAM_RELATION_SCOPING"): CELL_TABLE,
    ("team_relation", "TEAM_RELATION_SCOPING_EXCLUDED"): CELL_TABLE,
    ("compose.plan", "STATED_SCOPING"): STATED,
    # STATED_SCOPING's with_without row, named apart because the point reader
    # cites it; it goes with the table.
    ("compose.plan", "WITH_WITHOUT_STATED"): STATED,
    ("compose.plan", "_TEAM_READER_REFUSES"): (
        "debt, owed by a decline-to-Cause commit after 'the Result is typed' (2026-10-05): the cells a team's log, splits and run"
        " refuse at RUN with a sentence of their own, let through the planner; RUN's refusals are typed now, but these are"
        " declines (Unsupported), and making them the planner's Causes moves answered_by on the unit tests' team calls"
        " (0 of 628 recorded, 0 of 2,082 feed answers reach them) - Jeff's enumerated-move rule, its own commit"
    ),
    ("compose.core", "COMPILER_SLOTS"): "debt, owed by Phase 3: `ranked_by` is a measure the typed Reading carries, not a scoping slot that narrows nothing",
    ("reading", "SCOPING_SLOTS"): "debt, owed by Phase 3: the Reading's typed filters replace the scope's list of slot names",
    ("coverage", "_BOX_SCORE_SCOPING"): (
        "debt, owed by Phase 3: which floor a `player_stat` reads could follow the planned point's relation (Planned.shape, since"
        " 2026-10-05), but the answering loop's declined path checks the floor with no plan (agent: check_coverage(intent, scope))"
    ),
    ("conditions", "_CONDITION_PLAYER_ONLY_CELLS"): (
        "debt, owed by Phase 3: the complement of TEAM_RELATION_SCOPING restated, which the planner's cell check over the team relation refuses by itself"
    ),
    ("router", "_MODEL_SLOTS"): "debt, owed by Phase 3, whose exit deletes router.py and Route",
}
_LABELS = (CELL_TABLE, STATED, "debt, owed by ")


def _scoping_tables_today() -> set[tuple[str, str]]:
    """Every module-level name under ``query/`` that looks like a scoping
    declaration, read from the source: a table nobody imports yet is still a
    table. The pattern is the one the freeze had before step 6 (widened
    2026-10-03, the Phase 1 review, when two tables of cells a reader
    refuses matched none of its first three words)."""
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
            found.update((module, name) for name in names if re.search(r"SCOPING|STATED|_SLOTS$|CELLS|REFUSES|HONOR|EXCLUDED", name))
    return found


def test_no_scoping_declaration_is_added_and_each_says_why_it_is_here() -> None:
    _frozen("scoping declaration", _scoping_tables_today(), frozenset(FROZEN_SCOPING_TABLES))
    unlabeled = {key for key, why in FROZEN_SCOPING_TABLES.items() if not why.startswith(_LABELS)}
    assert unlabeled == set(), "each scoping declaration is a cell table, STATED_SCOPING, or debt naming the step that owes it"


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
