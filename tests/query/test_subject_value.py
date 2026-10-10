"""The subject's own family, typed (Phase 3, step 2's seventh slice): the
typed subject on the Scope (:class:`association.query.reading.Subject` - the
kind, the players, the teams, the position group) in place of the four name
slots and the point's ``position``, its door and its projection; what the
subject reading hands the grammar's rules (:func:`association.query.grammar.settle`,
the typed subject); the words the reading
reads them by, in the lexicon; the characters it claims for the names it
settled (:func:`association.query.subject.subject_claims`); and the
behavioral check contract 4 asks for - the typed subject on each relation
changes what the read sees.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from shapes import asked
from slots import slots_scope
from test_compose import cx_ctx  # noqa: F401 - the fixture with positions, imported by name
from test_conditions import league  # noqa: F401 - the league fixture, imported by name

from association.query import lexicon
from association.query.answer import AnswerContext
from association.query.compose.core import Query, compile_query, rows_of
from association.query.entities import names_of
from association.query.grammar import settle
from association.query.parse import read_route
from association.query.player_relation import POSITION_CODES
from association.query.reading import Filtered, Reading, ScopeError, Span
from association.query.reading import Subject as Who
from association.query.subject import is_team_name, subject_claims, subject_named_in, team_named_in_text, team_words_in
from association.query.team_relation import scoped_team

# ---------------- the typed value: its door and its projection ----------------


@pytest.mark.parametrize(
    ("slots", "who"),
    [
        ({"player": "Joel Embiid"}, Who(kind="player", players=("Joel Embiid",))),
        ({"players": ["Luka Doncic", "Shai Gilgeous-Alexander"]}, Who(kind="pair", players=("Luka Doncic", "Shai Gilgeous-Alexander"))),
        ({"team": "Boston Celtics"}, Who(kind="team", teams=("Boston Celtics",))),
        ({"teams": ["Knicks", "Celtics"]}, Who(kind="teams", teams=("Knicks", "Celtics"))),
        ({"player": "Anthony Edwards", "team": "thunder"}, Who(kind="player", players=("Anthony Edwards",), teams=("thunder",))),
        ({}, Who()),
    ],
)
def test_the_name_slots_pass_the_door_into_the_typed_subject_and_project_back(slots: dict[str, Any], who: Who) -> None:
    """Every shape the parser writes - one player or two and more, one team
    or a list - is the typed subject and comes back as it went in."""
    scope = slots_scope(slots)
    assert scope.who == who
    assert scope.to_slots() == slots
    record = scope.scope_record()
    assert {key: record[key] for key in ("player", "players", "team", "teams")} == {
        "player": slots.get("player"),
        "players": tuple(slots.get("players", ())),
        "team": slots.get("team"),
        "teams": tuple(slots.get("teams", ())),
    }


def test_a_router_era_slot_shape_comes_back_as_its_count_says() -> None:
    """One name listed in a plural slot, or two teams split between ``team``
    and ``teams``, are slot dicts no reader of the words writes (0 of the
    2,710 readings): the typed subject holds the names, and the count says
    which slot each projects to."""
    assert slots_scope({"players": ["Luka Doncic"]}).to_slots() == {"player": "Luka Doncic"}
    assert slots_scope({"team": "Knicks", "teams": ["Celtics"]}).who.teams == ("Knicks", "Celtics")
    assert slots_scope({"team": "Knicks", "teams": ["Knicks"]}).to_slots() == {"team": "Knicks"}


def test_the_subject_holds_its_kind_its_names_and_its_position_to_their_sets() -> None:
    assert Who(kind="player", players=("A",)).player == "A" and Who(kind="pair", players=("A", "B")).player is None
    assert Who(kind="team", teams=("Boston Celtics",)).team == "Boston Celtics" and Who(kind="teams", teams=("A", "B")).team is None
    for bad in ({"kind": "squad"}, {"players": ("",)}, {"teams": [" "]}, {"position": "QB"}):
        with pytest.raises(ScopeError):
            Who(**bad)
    assert Who(kind="position", position="C").without_position() == Who(kind="position")
    # One set of position groups: the words read as these, and the league's read reaches each.
    assert frozenset(POSITION_CODES) == lexicon.POSITION_GROUPS


def test_the_points_position_is_its_subjects_where_the_league_is_read() -> None:
    """The point's ``position`` was a field until this slice: a league-wide
    point honors its subject's position group, and every other Reading held
    none - which the record keeps (``Reading.projected``)."""
    scope = replace(Reading(), who=Who(kind="position", position="C"))
    assert Reading(**scope.filters(), relation="everyone").projected()["position"] == "C"
    assert Reading(**scope.filters()).projected()["position"] is None


# ---------------- what the grammar's rules are handed ----------------


def test_the_grammars_rules_take_the_names_typed() -> None:
    """The rules read the subject the reading hands them, typed - a name is
    never a slot (``router.settle`` refused one passed as a slot until Phase
    3, step 4, slice (b), when its slot dict went)."""
    settled = settle("embiid stats", asked("player_stat"), subject=Who(kind="player", players=("Joel Embiid",)))
    assert settled.subject.players == ("Joel Embiid",)


def test_the_rules_settle_a_dropped_subject_from_the_grammar_the_reading_read() -> None:
    """ "most points curry scored in a game" with nobody handed: the grammar's
    subject, which the subject reading reads (``subject_named_in``), is the
    single game's player."""
    question = "most points curry scored in a game this season"
    assert subject_named_in(question) == "curry"
    assert settle(question, asked("single_game_high")).subject.player == "curry"
    # With nobody in the question's grammar, nobody is settled.
    assert subject_named_in("most points scored in a game this season") is None
    assert settle("most points scored in a game this season", asked("single_game_high")).subject.players == ()


def test_the_words_the_reading_reads_a_team_and_a_dropped_subject_by() -> None:
    assert subject_named_in("how many times has embiid fouled out?") == "embiid"
    assert subject_named_in("how many points did Jokic score in the 3rd quarter") == "Jokic" and not is_team_name("Jokic")
    assert subject_named_in("how many points did the sixers score in the 3rd quarter") == "sixers" and is_team_name("sixers")
    assert is_team_name("Orlando") and is_team_name("det") and not is_team_name("Magic Johnson") and not is_team_name("PJ Washington")
    assert team_words_in("Celtics 2nd half scoring vs the cavs") == ("celtics", "cavs")
    assert team_named_in_text("jokic 3rd quarter against Boston", "Boston Celtics") == "Boston Celtics"
    assert team_named_in_text("jokic 3rd quarter against Boston", "Denver Nuggets") is None


# ---------------- the characters it claims ----------------


def test_the_subject_reading_claims_the_names_and_the_position_it_settled(league: AnswerContext) -> None:  # noqa: F811 - the fixture
    from association.query.entities import teams_of

    teams = teams_of(league.con)

    def said(question: str, scope: Filtered) -> list[tuple[str, str]]:
        return [(question[c.start : c.end], c.what) for c in subject_claims(teams, question, scope)]

    assert said("jayson tatum stats vs the lakers", slots_scope({"player": "Jayson Tatum", "opponent": "Los Angeles Lakers"})) == [
        ("jayson tatum", "player"),
        ("vs the lakers", "opponent"),
    ]
    # A near spelling where no word of the name stands as typed; a team's
    # nickname and its abbreviation; a team named "for".
    assert said("embid's points", slots_scope({"player": "Joel Embiid"})) == [("embid", "player")]
    assert said("sixers record vs BOS", slots_scope({"team": "Philadelphia 76ers", "opponent": "Boston Celtics"})) == [("sixers", "team"), ("vs BOS", "opponent")]
    assert said("lebron stats for the lakers", slots_scope({"player": "LeBron James", "own_team": "Los Angeles Lakers"})) == [("lebron", "player"), ("for the lakers", "tenure")]
    # The position group's words.
    assert said("most rebounds by a center", replace(Reading(), who=Who(kind="position", position="C"))) == [("center", "position")]
    # A name the scope does not settle claims nothing.
    assert said("best true shooting percentage", Reading()) == []


def test_the_claims_ride_the_reading(league: AnswerContext) -> None:  # noqa: F811 - the fixture
    question = "jayson tatum points vs the lakers"
    route = read_route(names_of(league.con), question, ["jayson tatum", "lakers"], "points")
    reading = route
    claimed = {question[c.start : c.end]: c.what for c in reading.claims}
    assert claimed["jayson tatum"] == "player" and claimed["vs the lakers"] == "opponent"


# ---------------- contract 4: the typed subject changes what each relation reads ----------------


def _total(ctx: AnswerContext, scope: Filtered, *, subject: str = "player", position: str | None = None) -> tuple[int, int]:
    query = Query(**scope.filters(), skeleton="scalar", measures=["points"], aggregate="total", subject="everyone" if subject == "everyone" else "player", position=position)
    (row,) = rows_of(ctx.con, compile_query(ctx.con, query))
    return int(row["games"]), int(row["points"] or 0)


def test_the_player_changes_what_the_player_relation_reads(league: AnswerContext) -> None:  # noqa: F811 - the fixture
    span = Span(season_type=2)
    brown = _total(league, Reading(who=Who(kind="player", players=("Jaylen Brown",)), span=span))
    tatum = _total(league, Reading(who=Who(kind="player", players=("Jayson Tatum",)), span=span))
    assert brown != tatum and brown[0] > 0 and tatum[0] > 0


def test_the_team_changes_what_the_league_relation_and_the_team_relation_read(league: AnswerContext) -> None:  # noqa: F811 - the fixture
    """The league's read narrowed to a team's players (the draft's
    ``of_team``), and the team relation's own subject."""
    span = Span(season_type=2)
    every = _total(league, Reading(span=span), subject="everyone")
    celtics = _total(league, Reading(who=Who(kind="team", teams=("Boston Celtics",)), span=span), subject="everyone")
    assert every[0] > celtics[0] > 0
    boston = scoped_team(league.con, Reading(who=Who(kind="team", teams=("Boston Celtics",)), span=span), "no team")
    lakers = scoped_team(league.con, Reading(who=Who(kind="team", teams=("Los Angeles Lakers",)), span=span), "no team")
    assert isinstance(boston, tuple) and isinstance(lakers, tuple) and boston[0].id != lakers[0].id
    with pytest.raises(Exception, match="no team"):
        scoped_team(league.con, Reading(span=span), "no team")


def test_the_position_group_changes_what_the_league_relation_reads(cx_ctx: AnswerContext) -> None:  # noqa: F811 - the fixture
    every = _total(cx_ctx, Reading(), subject="everyone")
    centers = _total(cx_ctx, replace(Reading(), who=Who(kind="position", position="C")), subject="everyone", position="C")
    assert every[0] > centers[0] > 0 and centers[1] == 18 + 14 + 0  # Sabonis, the one center on record: 18 and 14 this season


def test_two_players_are_the_pair_a_matchup_reads(league: AnswerContext) -> None:  # noqa: F811 - the fixture
    from association.query.compose.core import _resolve_pair

    pair = Query(**(Reading(who=Who(kind="pair", players=("Jayson Tatum", "LeBron James")), span=Span(season_type=2))).filters(), skeleton="pair", measures=["points"])
    a, b, _span, _narrowed = _resolve_pair(league.con, pair)
    assert (a.name, b.name) == ("Jayson Tatum", "LeBron James")


def test_a_period_is_never_the_one_who_scored_in_it_and_most_recent_is_no_rank(league: AnswerContext) -> None:  # noqa: F811 - the fixture
    """ "display the first quarter scores for the Sixers' most recent 10
    games" read "first quarter" as the player who scores (the grammar's
    subject), dropped the Sixers for him and answered "No player found
    matching 'first quarter' - did you mean Tim Quarterman?"; and "most
    recent" read as the ranking's "most", so a team's quarter answered the
    highest of the ten. A period is no subject, and "most recent" is the
    window's end."""
    question = "display the first quarter scores for the Sixers' most recent 10 games"
    assert subject_named_in(question) is None
    route = read_route(names_of(league.con), question, ["Sixers"], "points")
    reading = route
    assert reading.intent == "team_quarter_points" and reading.who.team == "Philadelphia 76ers" and reading.who.players == ()
    assert (reading.window.order, reading.window.count, reading.window.rank) == ("recent", 10, None)


def test_a_players_name_after_vs_is_not_a_team_it_only_clips() -> None:
    """ "KEVIN GARNETT VS TIM DUNCAN GAMES" read "TIM" as the Minnesota
    Timberwolves (the start of "Timberwolves"), the opponent of a matchup
    that has none, and refused it ("player_matchup cannot honor
    ['opponent']"). A word the team only clips that is a name the question
    gives a player is the player; a team's own word, abbreviation or
    singular stays the team whoever else shares it."""
    import duckdb

    from association.query.entities import teams_of
    from association.query.subject import _team_after_versus

    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    con.execute("INSERT INTO teams VALUES ('16', 'MIN', 'Minnesota Timberwolves'), ('15', 'MIL', 'Milwaukee Bucks')")

    def versus(question: str, named: tuple[str, ...]) -> str | None:
        found = _team_after_versus(teams_of(con), question, names=named)
        return found.name if found is not None else None

    assert versus("KEVIN GARNETT VS TIM DUNCAN GAMES", ("Kevin Garnett", "Tim Duncan")) is None
    assert versus("KEVIN GARNETT VS TIM DUNCAN GAMES", ()) == "Minnesota Timberwolves"
    assert versus("jalen brunson vs buck", ("Jalen Brunson", "Buck Williams")) == "Milwaukee Bucks"
    assert versus("kevin garnett vs the wolves", ("Kevin Garnett",)) == "Minnesota Timberwolves"


def test_the_76ers_are_read_from_their_own_spelling(league: AnswerContext) -> None:  # noqa: F811 - the fixture
    """ "76ers", the one team name spelled with a digit, split into the word
    "ers" every name is read by, and no team was read from it: "giannis
    stats vs 76ers this season" answered his whole season, "76ers leaders in
    3s" the league's. Read whole, where the question gives it - in the
    question's order, so "chicago bulls vs 76ers" is still the Bulls against
    the 76ers."""
    from association.query.entities import teams_of
    from association.query.subject import _team_after_versus, _teams_by_word, team_named_in

    teams = teams_of(league.con)
    assert team_named_in(teams, "76ers leaders in 3s") == "Philadelphia 76ers"
    # Every team the words name is one no player's name is read from:
    # "boston" beside the 76ers is the Celtics, not Brandon Boston Jr.
    assert _teams_by_word(teams, "how many times did the 76ers play boston") == {"Philadelphia 76ers", "Boston Celtics"}
    assert team_named_in(teams, "the 76ers' record this season") == "Philadelphia 76ers"
    assert team_named_in(teams, "celtics vs 76ers") == "Boston Celtics"
    found = _team_after_versus(teams, "jayson tatum stats vs 76ers this season")
    assert found is not None and found.name == "Philadelphia 76ers"
    question = "jayson tatum stats vs 76ers this season"
    route = read_route(names_of(league.con), question, [], "")
    assert route.cuts.opponent == "Philadelphia 76ers"
