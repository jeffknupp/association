"""``scripts/check_ratchets.py``: each ratchet finds the shape it holds, and
the gate fails on a new violation and reports a listed one that is gone as slack."""

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


def test_sql_through_a_helper_a_renamed_question_and_a_borrowed_pattern_are_caught() -> None:
    """Five ways past the ratchets are caught: SQL through a string-SQL
    helper or ``.query``, a question parameter under another name, ``re``
    through importlib, and a reader's private compiled pattern reused."""
    modules = _modules(
        point="from association.query.entities import _read_table\ndef read(con):\n    return _read_table(con, 'SELECT 1')",
        compose__present=(
            "import importlib\n_re = importlib.import_module('re')\nfrom association.query.point import _TEAM_NOT_SUBJECT\ndef say(question_text, con):\n    return con.query('SELECT 1')"
        ),
    )
    assert ratchets.sql_outside_the_relations(modules) == {"point": 1, "compose.present": 1}
    assert ratchets.question_outside_the_reader(modules) == {"compose.present:say"}
    assert ratchets.regex_outside_the_lexicon(modules) == {"compose.present", "compose.present:_TEAM_NOT_SUBJECT"}
    assert ratchets.con_in_the_reader(modules) == {"point:read"}


def test_a_string_sql_helper_counts_its_callers_and_not_its_own_body() -> None:
    """A function that runs the SQL it is handed (``con.execute(sql)``, ``sql``
    a parameter) is a door: each call of it is a statement, and its own call
    is not one more (the two loads below count two, beside ids's own
    statement). A helper of any name is found by its body, so renaming
    one is no way past the count."""
    modules = _modules(
        entities=(
            "def _read_table(con, sql):\n    return con.execute(sql).fetchall()\n"
            "def players(con):\n    return _read_table(con, 'SELECT * FROM players')\n"
            "def teams(con):\n    return _read_table(con, 'SELECT * FROM teams')\n"
            "def ids(con, table):\n    return con.execute(f'SELECT id FROM {table}').fetchall()"
        ),
        compose__charts="def run(con, statement):\n    return con.sql(statement)\ndef read(con):\n    return run(con, 'SELECT 1')",
    )
    assert ratchets.sql_outside_the_relations(modules) == {"entities": 3, "compose.charts": 1}


def test_the_compilers_door_runs_its_parameters_attribute_and_counts_as_no_statement() -> None:
    """``compose.core._executed(con, statement)`` runs ``statement.sql`` -
    an ATTRIBUTE of its parameter - and every compiled and relation
    statement goes through it, so it is a door and no statement of
    ``compose.core``'s (#397). A helper of it that hands on its
    own parameter is a door too, and its callers are counted; ``rows_of``
    and ``values_of`` hand theirs on and stay counted by where their
    statement comes from (a compiled one is none); a method running its own
    attribute (``self.sql``) runs SQL it holds, and counts."""
    modules = _modules(
        compose__core=(
            "def _executed(con, statement):\n    return con.execute(statement.sql, statement.params)\n"
            "def rows_of(con, compiled):\n    return _executed(con, compiled).fetchall()\n"
            "def values_of(con, statement):\n    return _executed(con, statement).fetchall()\n"
            "def run(con, q):\n    return rows_of(con, compile_query(con, q))"
        ),
        compose__seasons=(
            "def line(con):\n    return values_of(con, Statement('SELECT 1', [])), _executed(con, Statement('SELECT 2', []))\n"
            "def _raw(con, statement):\n    return _executed(con, statement)\n"
            "def other(con):\n    return _raw(con, Statement('SELECT 3', []))\n"
            "class Cache:\n    def load(self):\n        return self.con.execute(self.sql)"
        ),
    )
    assert ratchets.sql_outside_the_relations(modules) == {"compose.seasons": 4}


def test_a_connection_under_another_name_is_a_connection_in_the_reader() -> None:
    """``con_in_the_reader`` reads a parameter's annotation as well as its
    name: a reader function handed the warehouse as ``warehouse:
    duckdb.DuckDBPyConnection`` is found; the name index the reader takes
    is not."""
    modules = _modules(
        parse="import duckdb\ndef read(warehouse: duckdb.DuckDBPyConnection, question): ...\ndef words(index: names.NameIndex, question): ...",
        subject="def read_subject(index, question, *, con=None): ...",
    )
    assert ratchets.con_in_the_reader(modules) == {"parse:read", "subject:read_subject"}


def test_a_hand_built_statement_through_the_compilers_door_counts_and_a_compiled_one_does_not() -> None:
    """A hand-written statement run through ``core.rows_of`` or
    ``core.values_of`` is counted. A statement the compiler built passes through
    the same door and is not counted; a function that only hands its
    parameter on is a door too, and its callers are counted."""
    modules = _modules(
        compose__stats=(
            "def read(con, q, c: Compiled):\n"
            "    compiled = compile_query(con, q)\n"
            "    per_type: dict[int, Compiled] = {}\n"
            "    per_type[2] = compile_over(con, q)\n"
            "    rows = {k: rows_of(con, each) for k, each in per_type.items()}\n"
            "    one = per_type[2]\n"
            "    return rows_of(con, compiled), rows_of(con, c), rows_of(con, one), rows_of(con, compile_team_over(q))"
        ),
        compose__seasons=(
            "def line(con, player):\n"
            "    statement, column = career_statement(player)\n"
            "    values_of(con, statement)\n"
            "    values_of(con, Statement(*games_sql(player)))\n"
            "    return values_of(con, season_statement(player))"
        ),
        season_line=(
            "def _values(con, statement: Statement):\n"
            "    return values_of(con, statement)\n"
            "def _raw(con, sql: str):\n"
            "    return values_of(con, Statement(sql, []))\n"
            "def first(con):\n"
            "    return _values(con, first_statement()), _raw(con, 'SELECT 1'), _raw(con, 'SELECT 2')"
        ),
    )
    assert ratchets.sql_outside_the_relations(modules) == {"compose.seasons": 3, "season_line": 3}


def test_sql_is_counted_per_module_and_a_count_may_only_fall() -> None:
    # A listed module could grow statements freely if the ratchet listed
    # modules; it counts them.
    modules = _modules(leaderboard="def top(con):\n    con.execute('SELECT 1')\n    return con.sql('SELECT 2').fetchall()")
    assert ratchets.sql_outside_the_relations(modules) == {"leaderboard": 2}
    listed = {name: ([] if name != "sql_outside_the_relations" else {"leaderboard": 2}) for name in ratchets.CHECKS}
    now = {name: ([] if name != "sql_outside_the_relations" else {"leaderboard": 2}) for name in ratchets.CHECKS}
    assert ratchets.compare(listed, now) == []
    grown = {**now, "sql_outside_the_relations": {"leaderboard": 3}}
    assert ratchets.compare(listed, grown) == ["sql_outside_the_relations: NEW leaderboard (2 -> 3) - the roadmap is deleting this shape; do not add to it"]
    fallen = {**now, "sql_outside_the_relations": {"leaderboard": 1}}
    assert ratchets.compare(listed, fallen) == []
    assert ratchets.slack(listed, fallen) == ["sql_outside_the_relations: SLACK leaderboard (2 -> 1) - gone from the code; the lead's merge runs --shrink so it cannot come back"]
    # --shrink lowers a count to the code's and never raises one.
    assert ratchets.shrink(listed, fallen)["sql_outside_the_relations"] == {"leaderboard": 1}
    assert ratchets.shrink(listed, grown)["sql_outside_the_relations"] == {"leaderboard": 2}


def test_a_regex_outside_the_lexicon_is_found() -> None:
    """Every module but the lexicon: one outside the reader that imports
    ``re`` by any route, and a reader module too."""
    modules = _modules(grammar="import re", refusals="import re", entities="from re import compile", metrics="import json", lexicon="import re\nWORD = re.compile(r'\\w+')")
    assert ratchets.regex_outside_the_lexicon(modules) == {"grammar", "refusals", "entities"}


def test_a_regex_inside_the_reader_is_found_and_an_annotation_is_not() -> None:
    """A reader module but the lexicon that
    calls ``re`` or imports it at run time is found; one that imports it
    under ``TYPE_CHECKING`` for an annotation is not, and neither is the
    lexicon."""
    modules = _modules(
        point="import re\n_VS = re.compile(r'vs', re.I)\n_LOG = re.compile('log')\ndef read(words: re.Pattern): ...",
        line="import re\ndef words(stat):\n    return re.sub(r'\\s+', ' ', stat)",
        lexicon="import re\nWORD = re.compile(r'\\w+')\nMARK = re.escape('x')",
        measure="import re\ndef kind(p: re.Pattern) -> bool:\n    return p.flags & re.I != 0",
        window="from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import re\ndef end(m: re.Match) -> str: ...",
        split="import typing\nif typing.TYPE_CHECKING:\n    import re\nelse:\n    import json",
        entities="import re\nX = re.compile('x')",
    )
    assert ratchets.regex_outside_the_lexicon(modules) == {"point", "line", "measure", "entities"}


def test_a_new_violation_fails_and_a_listed_one_that_is_gone_is_slack() -> None:
    names = list(ratchets.CHECKS)
    clean = {name: ["a"] for name in names}
    assert ratchets.compare(clean, clean) == []
    grown = {**clean, names[0]: ["a", "b"]}
    assert ratchets.compare(clean, grown) == [f"{names[0]}: NEW b - the roadmap is deleting this shape; do not add to it"]
    assert ratchets.compare(grown, clean) == []
    assert ratchets.slack(grown, clean) == [f"{names[0]}: SLACK b - gone from the code; the lead's merge runs --shrink so it cannot come back"]
    # A ratchet the list does not hold yet lists nothing: everything found is new.
    assert len(ratchets.compare({}, clean)) == len(names)


def test_the_list_on_disk_is_this_trees_and_shrink_never_adds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    listed = json.loads(ratchets.RATCHETS.read_text())
    found = ratchets.measure()
    assert set(listed) == set(ratchets.CHECKS) and ratchets.compare(listed, found) == []
    # --shrink over a list holding one stale entry and missing one real one
    # drops the stale entry and does not add the missing one.
    name = "question_outside_the_reader"
    doctored = {**listed, name: [*listed[name][1:], "a_name_that_is_gone"]}
    copy = tmp_path / "ratchets.json"
    copy.write_text(json.dumps(doctored))
    monkeypatch.setattr(ratchets, "RATCHETS", copy)
    monkeypatch.setattr("sys.argv", ["check_ratchets.py", "--shrink"])
    assert ratchets.main() == 1
    assert json.loads(copy.read_text())[name] == listed[name][1:]
