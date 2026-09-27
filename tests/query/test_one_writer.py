"""The parser is the one writer of a question's slots (ROADMAP plan item 6,
step (d), part 3c): the router-era repairs that ran after every reading -
``override_nicknames``, ``restore_dropped_players``, ``undo_name_completion``
- are gone, and what the parser's reading still needed of them is its own.

Each check here goes through the whole agent on a small warehouse, with the
normalizer's reply stubbed the way the rehearsal replays recorded ones, so
it is the template's own view of the slots that is asserted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pytest

from association.query.agent import Agent
from association.query.normalizer import Normalized
from association.query.reading import Reading
from association.query.templates.common import TemplateResult

_PLAYERS = (
    "Joel Embiid",
    "Nikola Jokic",
    "Ben Simmons",
    "Jayson Tatum",
    "Jaylen Brown",
    "Bobby Brown",
    "Kwame Brown",
    "Bam Adebayo",
    "Mo Bamba",
    "Stephen Curry",
    "Seth Curry",
    "DeMar DeRozan",
    "Travis Best",
)


@pytest.fixture
def ask(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    """``ask(question, names)``: the players and intent each template saw, with
    the normalizer's reply stubbed as ``names``."""
    db_path = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    con.executemany("INSERT INTO players VALUES (?, ?)", [(str(i), name) for i, name in enumerate(_PLAYERS)])
    con.close()
    seen: list[tuple[str, str | None, tuple[str, ...]]] = []

    def record(ctx: Any, reading: Reading) -> TemplateResult:
        del ctx
        seen.append((reading.intent, reading.scope.player, reading.scope.players))
        return TemplateResult(data={}, answer="answered")

    monkeypatch.setattr("association.query.agent.TEMPLATES", {"fingerprint": record, "player_stat": record, "player_compare": record})

    def run(question: str, names: list[str], stat: str = "") -> list[tuple[str, str | None, tuple[str, ...]]]:
        monkeypatch.setattr("association.query.normalizer.normalize", lambda model, q: Normalized(names, stat))
        seen.clear()
        Agent("qwen2.5:7b", str(db_path), tmp_path / "out", history_dir=tmp_path / ".history", fallthrough=False).ask(question)
        return list(seen)

    return run


def test_a_surname_the_model_completed_is_asked_about(ask: Any) -> None:
    """ "brown" is three players here, and the model filling in Jaylen is the
    prominence tiebreak this project rejected: the template sees the
    question's own word and asks. Was ``undo_name_completion``, run after
    the router; now the parser cuts the model's completion back itself."""
    assert ask("how many points does brown average?", ["Jaylen Brown"], "points") == [("player_stat", "Brown", ())]
    assert ask("who is better, tatum or brown", ["Jayson Tatum", "Jaylen Brown"]) == [("player_compare", None, ("Jayson Tatum", "Brown"))]


def test_a_completion_that_names_nobody_still_reaches_the_player(ask: Any) -> None:
    """ "derozan" came back "Derozan Valenčić" (day5), a surname no player
    has. The part the question holds reaches DeMar DeRozan by itself - the
    parser's cut takes it there, and so does the reading, which replaces a
    name the question never held with the one it does: an outcome check,
    which holds with either step alone."""
    assert ask("derozan career points", ["Derozan Valenčić"], "points") == [("player_stat", "DeMar DeRozan", ())]


def test_a_completion_that_changes_no_answer_is_kept(ask: Any) -> None:
    """Completing "embiid" reaches the one player it can be; a nickname the
    question used ("steph curry") is the curated table's reading, not a
    guess."""
    assert ask("how many points does embiid average", ["Joel Embiid"], "points") == [("player_stat", "Joel Embiid", ())]
    assert ask("what was steph curry's 3pt percentage", ["Stephen Curry"]) == [("player_stat", "Stephen Curry", ())]


def test_a_typod_surname_is_read_not_cut_back(ask: Any) -> None:
    """ "Bam Adeyebu" is the question's own spelling, which the reading reads
    as Bam Adebayo. The router-era cut ran after the reading and saw the
    corrected "Adebayo" as a word the question lacks - so it cut the name to
    the ambiguous "Bam" and asked (the day10 paraphrase "Jan 19 Bam
    Adeyebu"). The parser's cut runs on the model's own spelling instead."""
    assert ask("Jan 19 Bam Adeyebu", ["Bam Adeyebu"]) == [("player_stat", "Bam Adebayo", ())]


def test_a_fingerprint_keeps_every_player_the_question_named(ask: Any) -> None:
    """Two polygons on shared axes IS the comparison: a reply that dropped
    the second name still draws both, because the reading takes the names
    from the question. Was ``restore_dropped_players``, for a router that
    returned one."""
    both = [("fingerprint", None, ("Joel Embiid", "Nikola Jokic"))]
    assert ask("compare fingerprints for embiid vs jokic in 2026", ["embiid", "jokic"]) == both
    assert ask("compare fingerprints for embiid vs jokic in 2026", ["embiid"]) == both
    # Said without a comparison word, which the router-era restoration was
    # gated on, and so lost the second name.
    assert ask("plot jokic and embiid fingerprints", ["jokic"]) == [("fingerprint", None, ("Nikola Jokic", "Joel Embiid"))]


def test_an_ordinary_word_is_no_second_fingerprint(ask: Any) -> None:
    """ "best" is Travis Best's whole surname and an ordinary word here: the
    fingerprint is Jokic's alone - the reason the router-era restoration
    needed a comparison word before it added anybody."""
    assert ask("plot jokic's fingerprint from his best season", ["jokic"]) == [("fingerprint", "Nikola Jokic", ())]


def test_for_me_is_the_asker_not_the_memphis_grizzlies(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """ "for me" after a player is who is asking, not his own team: "Memphis"
    starts with "me", and read as the team it made "show kat's average
    points for me" his career with the Grizzlies - who he never played for.
    The router-era nickname repair hid it by rewriting "kat" to a name the
    question does not hold, so the own-team reading never matched; with the
    name as typed it did (a day10 paraphrase, plan item 6 step (d) part 3c)."""
    from association.query.entities import _team_after_for

    db_path = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    con.execute("INSERT INTO players VALUES ('1', 'Karl-Anthony Towns'), ('2', 'LeBron James')")
    con.execute("INSERT INTO teams VALUES ('29', 'MEM', 'Memphis Grizzlies'), ('14', 'MIA', 'Miami Heat')")
    assert _team_after_for(con, "Display kat's average points for me") is None
    miami = _team_after_for(con, "lebron stats as a starter for Miami")
    assert miami is not None and miami[0].name == "Miami Heat"
    con.close()
    seen: list[str | None] = []

    def record(ctx: Any, reading: Reading) -> TemplateResult:
        del ctx
        seen.append(reading.scope.own_team)
        return TemplateResult(data={}, answer="answered")

    monkeypatch.setattr("association.query.agent.TEMPLATES", {"player_stat": record})
    monkeypatch.setattr("association.query.normalizer.normalize", lambda model, q: Normalized(["kat"], "points"))
    Agent("qwen2.5:7b", str(db_path), tmp_path / "out", history_dir=tmp_path / ".history", fallthrough=False).ask("Display kat's average points for me")
    assert seen == [None]


@pytest.fixture
def league() -> duckdb.DuckDBPyConnection:
    """Players and teams enough for the shapes the router used to scramble."""
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    players = [
        "Stephen Curry",
        "Seth Curry",
        "Jaylen Brown",
        "Luka Doncic",
        "Steven Adams",
        "Jayson Tatum",
        "Jay Huff",
        "Joel Embiid",
        "De'Aaron Fox",
        "Victor Wembanyama",
        "Magic Johnson",
        "Brandon Boston Jr.",
    ]
    con.executemany("INSERT INTO players VALUES (?, ?)", [(str(i), name) for i, name in enumerate(players)])
    teams = [
        ("2", "BOS", "Boston Celtics"),
        ("19", "ORL", "Orlando Magic"),
        ("17", "BKN", "Brooklyn Nets"),
        ("9", "GS", "Golden State Warriors"),
        ("23", "SAC", "Sacramento Kings"),
        ("13", "LAL", "Los Angeles Lakers"),
        ("8", "DET", "Detroit Pistons"),
    ]
    con.executemany("INSERT INTO teams VALUES (?, ?, ?)", teams)
    return con


_SIDES = ("player", "players", "team", "opponent", "without")


@pytest.mark.parametrize(
    ("question", "names", "intent", "sides"),
    [
        # A team the question plays against, never a second player to compare:
        # "boston" alone is Brandon Boston Jr.'s whole surname.
        ("how did curry do against the celtics this year", ["curry", "celtics"], "player_stat", {"player": "curry", "opponent": "Boston Celtics"}),
        # Nobody named: two teams, and "magic" is the Magic, not Magic Johnson.
        ("magic vs nets last 10", ["magic", "nets"], "game_log", {"team": "Orlando Magic", "opponent": "Brooklyn Nets"}),
        # A city written as one word still reaches its team.
        ("jaylen brown last 10 games vs goldenstate", ["jaylen brown", "goldenstate"], "game_log", {"player": "Jaylen Brown", "opponent": "Golden State Warriors"}),
        # An accented name reaches the plain-letter spelling the warehouse holds.
        ("luka dončić last 15 games vs. magic", ["luka dončić", "magic"], "game_log", {"player": "Luka Doncic", "opponent": "Orlando Magic"}),
        ("steve adam's vs kings last 10 games", ["steve adam's", "kings"], "game_log", {"player": "Steven Adams", "opponent": "Sacramento Kings"}),
        ("jaylen brown last 8 games vs pistons", ["jaylen brown", "pistons"], "game_log", {"player": "Jaylen Brown", "opponent": "Detroit Pistons"}),
        # Two players set against each other are the pair, whatever "vs" joins.
        ("jay huff game log vs Embiid", ["jay huff", "Embiid"], "player_matchup", {"players": ("Jay Huff", "Joel Embiid")}),
        # A teammate named with "without" narrows; the team is the opponent.
        (
            "de'aaron fox vs magic last five games without wembyanama",
            ["de'aaron fox", "magic", "wembyanama"],
            "game_log",
            {"player": "De'Aaron Fox", "opponent": "Orlando Magic", "without": ("wembyanama",)},
        ),
    ],
)
def test_the_parser_files_each_side_where_the_router_scrambled_it(league: duckdb.DuckDBPyConnection, question: str, names: list[str], intent: str, sides: dict[str, Any]) -> None:
    """The team-and-opponent shapes the router got wrong - a team in
    ``players``, the two sides swapped, a player in ``team``, a player
    displaced by his own team - each once had a repair after the router
    (``subject.apply_subject``'s opponent-team, team-slot and team-subject
    passes). Measured over 628 recorded questions, the parser's output never
    needed one (ROADMAP plan item 6, step (d), part 3c), because it files
    each side from its own reading; these are those questions, read here."""
    from association.query.parse import read_route
    from association.query.subject import apply_subject, read_subject

    route, _, _ = read_route(league, question, names, "")
    slots = dict(route.slots)
    applied = apply_subject(read_subject(league, question, route.intent, slots), slots, intent=route.intent)
    got = {key: tuple(value) if isinstance(value, list) else value for key, value in slots.items() if key in _SIDES}
    assert (applied.intent, got, applied.dropped) == (intent, sides, [])
