"""``scripts/check_ratchets.py``: each ratchet finds the shape it holds, and
the gate fails both on a new violation and on a listed one that is gone."""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_ratchets.py"


def _load() -> ModuleType:
    """Import the script by file path, since ``scripts`` is not a package."""
    spec = importlib.util.spec_from_file_location("check_ratchets_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ratchets = _load()


def _modules(**sources: str) -> dict[str, ast.Module]:
    return {name.replace("__", "."): ast.parse(source) for name, source in sources.items()}


def test_a_function_outside_the_reader_that_takes_the_question_is_found() -> None:
    modules = _modules(
        entities="def find(con, question): ...\nclass Index:\n    def named(self, *, question): ...\n    def other(self, name): ...",
        parse="def read_route(con, question): ...",
        agent="class Agent:\n    def ask(self, question): ...",
        compose__move="def read_point(reading):\n    def inner(question): ...",
    )
    assert ratchets.question_outside_the_reader(modules) == {"entities:find", "entities:Index.named", "compose.move:read_point.inner"}


def test_a_module_that_executes_sql_is_found() -> None:
    modules = _modules(
        player_games="def rows(con):\n    return con.execute('SELECT 1').fetchall()",
        compose__sentence="def say(result):\n    return str(result)",
        leaderboard="def top(con):\n    return con.sql('SELECT 1')",
    )
    assert ratchets.sql_outside_the_relations(modules) == {"player_games": 1, "leaderboard": 1}


def test_the_bypasses_the_phase_1_review_found_are_caught() -> None:
    """Six ways past the ratchets that passed on 2026-10-03 (the review of
    Phase 1): SQL through a string-SQL helper or ``.query``, a question
    parameter under another name, ``re`` through importlib, a reader's
    compiled pattern reused, a private template name through a module
    alias."""
    modules = _modules(
        point="from association.query.entities import _read_table\ndef read(con):\n    return _read_table(con, 'SELECT 1')",
        compose__present=(
            "import importlib\n_re = importlib.import_module('re')\n"
            "from association.query.point import _TEAM_NOT_SUBJECT\n"
            "from association.query.templates import splits as _m\n_y = _m._condition_needs_player_refusal\n"
            "def say(question_text, con):\n    return con.query('SELECT 1')"
        ),
    )
    assert ratchets.sql_outside_the_relations(modules) == {"point": 1, "compose.present": 1}
    assert ratchets.question_outside_the_reader(modules) == {"compose.present:say"}
    assert ratchets.regex_outside_the_reader(modules) == {"compose.present", "compose.present:_TEAM_NOT_SUBJECT"}
    assert ratchets.private_template_imports(modules) == {"compose.present:_condition_needs_player_refusal"}
    assert ratchets.con_in_the_reader(modules) == {"point:read"}


def test_sql_is_counted_per_module_and_a_count_may_only_fall() -> None:
    # Phase 1's order from here, step 4: a listed module could grow
    # statements freely while the ratchet listed modules; it counts them.
    modules = _modules(leaderboard="def top(con):\n    con.execute('SELECT 1')\n    return con.sql('SELECT 2').fetchall()")
    assert ratchets.sql_outside_the_relations(modules) == {"leaderboard": 2}
    listed = {name: ([] if name != "sql_outside_the_relations" else {"leaderboard": 2}) for name in ratchets.CHECKS}
    now = {name: ([] if name != "sql_outside_the_relations" else {"leaderboard": 2}) for name in ratchets.CHECKS}
    assert ratchets.compare(listed, now) == []
    grown = {**now, "sql_outside_the_relations": {"leaderboard": 3}}
    assert ratchets.compare(listed, grown) == ["sql_outside_the_relations: NEW leaderboard (2 -> 3) - the roadmap is deleting this shape; do not add to it"]
    fallen = {**now, "sql_outside_the_relations": {"leaderboard": 1}}
    assert ratchets.compare(listed, fallen) == ["sql_outside_the_relations: GONE leaderboard (2 -> 1) - remove it from scripts/ratchets.json (--shrink) so it cannot come back"]
    # --shrink lowers a count to the code's and never raises one.
    assert ratchets.shrink(listed, fallen)["sql_outside_the_relations"] == {"leaderboard": 1}
    assert ratchets.shrink(listed, grown)["sql_outside_the_relations"] == {"leaderboard": 2}


def test_a_private_name_the_compiler_takes_from_a_template_is_found() -> None:
    modules = _modules(
        compose__present="from association.query.templates.games import _period_split_from, team_game_log\nfrom .core import _resolve",
        compose__core="def f():\n    from ..templates.players import _leaderboard_ranking",
        templates__games="from .common import _clamp_limit",
    )
    assert ratchets.private_template_imports(modules) == {"compose.present:_period_split_from", "compose.core:_leaderboard_ranking"}


def test_a_regex_outside_the_reader_is_found() -> None:
    modules = _modules(router="import re", refusals="import re", entities="from re import compile", metrics="import json")
    assert ratchets.regex_outside_the_reader(modules) == {"refusals", "entities"}


def test_a_new_violation_and_a_listed_one_that_is_gone_both_fail() -> None:
    names = list(ratchets.CHECKS)
    clean = {name: ["a"] for name in names}
    assert ratchets.compare(clean, clean) == []
    grown = {**clean, names[0]: ["a", "b"]}
    assert ratchets.compare(clean, grown) == [f"{names[0]}: NEW b - the roadmap is deleting this shape; do not add to it"]
    assert ratchets.compare(grown, clean) == [f"{names[0]}: GONE b - remove it from scripts/ratchets.json (--shrink) so it cannot come back"]
    # A ratchet the list does not hold yet lists nothing: everything found is new.
    assert len(ratchets.compare({}, clean)) == len(names)


def test_the_list_on_disk_is_this_trees_and_shrink_never_adds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    listed = json.loads(ratchets.RATCHETS.read_text())
    found = ratchets.measure()
    assert set(listed) == set(ratchets.CHECKS) and ratchets.compare(listed, found) == []
    # --shrink over a list holding one stale entry and missing one real one
    # drops the stale entry and does not add the missing one.
    name = "private_template_imports"
    doctored = {**listed, name: [*listed[name][1:], "a_name_that_is_gone"]}
    copy = tmp_path / "ratchets.json"
    copy.write_text(json.dumps(doctored))
    monkeypatch.setattr(ratchets, "RATCHETS", copy)
    monkeypatch.setattr("sys.argv", ["check_ratchets.py", "--shrink"])
    assert ratchets.main() == 1
    assert json.loads(copy.read_text())[name] == listed[name][1:]
