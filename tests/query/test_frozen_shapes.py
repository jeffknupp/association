"""New shapes are frozen: a shape comes only by Jeff's lifting, and brings
what decision D11 of ``ROADMAP.md`` lists. The grammar names a
:class:`~association.query.reading.PointShape` (the relation, the shape and
``by`` the words ask for), and :data:`FROZEN` holds those keys; the page
holds one renderer per ``reading.Shape``; and the four scoping declarations,
the per-relation cell tables (one table and one table of shapes' rows per
relation), are frozen by module and name (:data:`FROZEN_SCOPING_TABLES`).
``templates/``, ``compose/present.py`` and ``compose/adapt.py`` do not come
back as source files. The other directions are held by
``scripts/check_ratchets.py``."""

from __future__ import annotations

from typing import Any

from association.query.grammar import CODE_ASSIGNED_ASKS
from association.query.parse import PARENT_GRAMMAR
from association.query.reading import PointShape
from association.query.subject import KIND_ASSIGNED_ASKS

# Every shape the words can ask, as (relation, shape, by): the grammar's
# keys, each said in words by the sayer (``compose.say.asked_as``, which
# ``test_say.test_every_key_the_grammar_names_is_said_in_words`` holds to
# every one). A key the grammar no longer names leaves this set; adding one
# is Jeff's lifting, with what D11 lists.
FROZEN = frozenset(
    {
        ("team_seasons", "scalar", "coach"),
        ("netpoints", "chart", "fingerprint"),
        ("player_games", "rows", "date"),
        ("team_games", "comparison", "opponent"),
        ("player_seasons", "ranking", "player"),
        ("player_periods", "ranking", "player"),
        ("player_periods", "rows", "date"),
        ("player_seasons", "comparison", "subject"),
        ("player_seasons", "split", "season"),
        ("player_games", "comparison", "met"),
        ("netpoints", "scalar", "ratings"),
        ("player_games", "split", "splits"),
        ("player_seasons", "scalar", "line"),
        ("player_games", "split", "line"),
        ("shots", "chart", "shots"),
        ("shots", "scalar", "distance"),
        ("player_games", "rows", "measure"),
        ("player_games", "runs", "line"),
        ("team_seasons", "ranking", "team"),
        ("team_snapshots", "scalar", "projection"),
        ("team_periods", "scalar", "total"),
        ("team_games", "scalar", "record"),
        ("team_seasons", "scalar", "line"),
        ("player_games", "scalar", "count"),
        ("team_games", "split", "presence"),
        # How many players a season line holds ("how many players averaged
        # 30 ppg in 2026", #390): the season ranking kept past the line,
        # counted.
        ("player_seasons", "scalar", "count"),
        # A team's own games, or every team's, ranked by one figure (#364).
        ("team_games", "rows", "measure"),
        # A team's season line season by season over a range or every
        # season (#372).
        ("team_seasons", "split", "season"),
    }
)


def _named_today() -> set[tuple[str, str, str]]:
    asked: set[PointShape] = set(CODE_ASSIGNED_ASKS) | set(KIND_ASSIGNED_ASKS) | {row[-1] for row in PARENT_GRAMMAR}
    return {(shape.relation, shape.shape, shape.by) for shape in asked}


def test_no_shape_is_added_to_what_the_words_ask_and_a_retired_one_leaves_the_list() -> None:
    named = _named_today()
    assert named - FROZEN == set(), "a new shape the words ask: a new shape comes only by Jeff's lifting, with what ROADMAP.md decision D11 lists"
    assert FROZEN - named == set(), "a shape retired: remove it from FROZEN so it cannot come back"


# Every module-level declaration of which cells a read honors, states,
# excludes or refuses, by module and name (#330), each labeled with why it
# is here:
CELL_TABLE = "a per-relation cell table"
FROZEN_SCOPING_TABLES: dict[tuple[str, str], str] = {
    ("player_relation", "RELATION_SCOPING"): CELL_TABLE,
    ("player_relation", "RELATION_SCOPING_EXCLUDED"): CELL_TABLE,
    ("team_relation", "TEAM_RELATION_SCOPING"): CELL_TABLE,
    ("team_relation", "TEAM_RELATION_SCOPING_EXCLUDED"): CELL_TABLE,
}
_LABELS = (CELL_TABLE, "debt, owed by ")


def _scoping_tables_today() -> set[tuple[str, str]]:
    """Every module-level name under ``query/`` that looks like a scoping
    declaration, read from the source: a table nobody imports yet is still a
    table. The pattern matches a table of cells a reader refuses as well as
    one it states or honors."""
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
    assert unlabeled == set(), "each scoping declaration is a cell table, or debt naming the step that owes it"


# No new renderer either, held the same way - the set as it stands, which
# may lose a member and gain none. The page keys its renderers by the
# planned point's shape, one per ``reading.Shape``, and a renderer is added
# only with a shape.
FROZEN_RENDERERS = frozenset({"scalar", "rows", "ranking", "comparison", "split", "runs", "chart"})


def _renderers_today() -> set[str]:
    import re
    from pathlib import Path

    import association.web

    page = (Path(association.web.__file__).parent / "static" / "index.html").read_text()
    block = re.search(r"const RENDERERS = \{(.*?)\n  \};", page, re.S)
    assert block is not None, "could not find the RENDERERS table in index.html"
    return set(re.findall(r"^    (\w+): \{", block.group(1), re.M))


def test_the_page_renders_by_the_shapes_and_nothing_else() -> None:
    """The frozen renderers are the shapes a planned point is read and said
    as, one each: a renderer keyed by anything else is a label come back."""
    from typing import get_args

    from association.query.reading import Shape

    assert frozenset(get_args(Shape)) == FROZEN_RENDERERS


def _frozen(name: str, today: set[Any], frozen: frozenset[Any]) -> None:
    assert today - frozen == set(), f"a new {name}: a new shape comes only by Jeff's lifting, with what ROADMAP.md decision D11 lists"
    assert frozen - today == set(), f"a {name} retired: remove it from the frozen set so it cannot come back"


def _query_sources(pattern: str) -> list[str]:
    from pathlib import Path

    import association.query

    root = Path(association.query.__file__).parent
    return sorted(str(path.relative_to(root)) for path in root.glob(pattern))


def test_no_template_presenter_or_adapter_comes_back_and_no_renderer_is_added() -> None:
    # Each shape is a reader and the sayer: no template, presenter or
    # adapter stands between them, and none comes back.
    #
    # Asked of the source files, never of the import system: an ignored
    # ``templates/__pycache__`` left in a checkout is a namespace package to
    # ``importlib.util.find_spec`` (#333). A ``.pyc`` is not a ``.py``, so a
    # stale cache cannot fail it.
    assert _query_sources("compose/present.py") == [], "a presenter came back: each shape is a reader and the sayer"
    assert _query_sources("compose/adapt.py") == [], "an adapter came back: each shape is a reader and the sayer"
    assert _query_sources("templates/**/*.py") == [], "a template came back: each shape is a reader and the sayer"
    _frozen("renderer", _renderers_today(), FROZEN_RENDERERS)
