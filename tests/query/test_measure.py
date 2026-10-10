"""The measure, typed (Phase 3, step 2's sixth slice): the one catalog of
what a question can ask about (:data:`association.query.measure.CATALOG`,
with every spelling the six vocabularies named a measure by resolving into
it), the one tagger that reads the family from the lexicon's words
(:func:`association.query.measure.read_measure`) and the characters it
claims, the typed value's door and projection
(:class:`association.query.reading.Measure`: the seven slots ``stat``,
``fields``, ``per_game``, ``rate``, ``side``, ``shot_value`` and ``kind``
given back exactly), the one cell the tables declare (``rate``), and the
behavioral check contract 4 asks for: the cell changes what the season-line
ranking reads, and the planner declines it where no reader states it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, get_args

import pytest
from routed import staged as settle
from test_career_spans import season_ctx  # noqa: F401 - the season line's fixture, imported by name
from test_parser import con  # noqa: F401 - the parser's name fixture, imported by name
from test_templates import game_log, leaderboard, pg_ctx  # noqa: F401 - the player relation's fixture, imported by name

from association.query import lexicon
from association.query.answer import AnswerContext
from association.query.compose.plan import cells_stated, plan_point
from association.query.measure import (
    ALIASES,
    CATALOG,
    MeasureContext,
    MeasureKey,
    key_of,
    measure_of,
    named,
    named_by_a_team_metric,
    names_a_stat,
    read_measure,
    team_metric_named,
)
from association.query.measures import DERIVED_MEASURES, GAME_COLUMNS, log_extras, resolve_metric, stat_column, stat_measure
from association.query.metrics import LEADERBOARD_METRICS
from association.query.normalizer import NORMALIZER_STATS
from association.query.parse import read_route, reading_from_route
from association.query.point import _team_measure
from association.query.reading import Claim, Measure, PointShape, Reading, Scope, ScopeError, Span, cell_set, unhonored_cells
from association.query.reading import Subject as Who
from association.query.team_metrics import TEAM_METRICS, resolve_team_metric

# ---------------- the tagger: words to a Measure ----------------


def _read(question: str, intent: str, key: str | None = None, **context: Any) -> Measure | None:
    return read_measure(question, MeasureContext(intent=intent, key=key, **context)).measure


@pytest.mark.parametrize(
    ("question", "intent", "key", "expected"),
    [
        # The words' own measure stands over the model's key; the model's key stands where the words name none.
        ("how many points does embiid average", "player_stat", "rebounds", measure_of("points")),
        ("jokic stats this season", "game_log", "assists", measure_of("assists")),
        ("compare sga and embiid", "player_compare", "points", None),
        # The grammar's spellings, as the stages wrote them, and the catalog's key behind each.
        ("who were the top 10 in defensive netpoints / 100 possessions", "leaderboard", None, Measure(key="netpoints", as_typed="netpoints_defense_per_100", how="per_100", side="defense")),
        ("who were the top 10 in defensive netpoints / 90", "leaderboard", "netpoints", Measure(key="netpoints", as_typed="netpoints_defense", how="per_90", unit="/ 90", side="defense")),
        ("centers by pf per 100", "leaderboard", "fouls", Measure(key="fouls", as_typed="fouls", how="per_100", unit="per 100")),
        ("kevin durant true shooting percentage career", "player_stat", "threePointFieldGoalPct", measure_of("ts_pct")),
        ("game score nba leader", "leaderboard", "points", Measure(key="game_score", as_typed="avg_game_score", how="per_game")),
        ("lebron game score this season", "player_stat", "points", measure_of("game_score")),
        ("show me lebron's 2pt percentage for the past 5 years", "player_history", "threePointFieldGoalPct", measure_of("twoPointFieldGoalPct")),
        ("who lead the league in avg 3 point distance", "leaderboard", "threePointFieldGoalPct", measure_of("shot_distance")),
        ("who attempted the most three pointers this season?", "leaderboard", "threePointFieldGoalsMade", measure_of("threePointFieldGoalsAttempted")),
        # A team metric's alias text, as asked - and what a team gives up as the opponent's figure.
        ("Lowest defensive rating by a team this season", "team_leaderboard", "usage_pct_defense", measure_of("defensive rating")),
        ("rebounds allowed per team", "team_leaderboard", "rebounds", Measure(key="rebounds", as_typed="rebounds allowed", whose="opponent")),
        # The LONGEST team alias the words hold wins, as the stages read it: "points per game" over the grammar's "points allowed" (ISSUES.md, "A team metric's alias...").
        ("which team allowed the most points per game", "team_leaderboard", "points", measure_of("points per game")),
        ("which team has the most points allowed", "team_leaderboard", "points", Measure(key="points", as_typed="points allowed", whose="opponent")),
        ("which team has the lowest scoring allowed", "team_leaderboard", "points", Measure(key=None, as_typed="scoring allowed")),
        # A team's season total, a quarter's log, a run's result, a fingerprint's side, a chart's shots, the columns beside a ranking.
        (
            "how many 3 pointers have the magic made so far this season",
            "team_stat",
            "threePointFieldGoalsMade",
            Measure(key="threePointFieldGoalsMade", as_typed="3 pointers", how="total", unit="total"),
        ),
        ("tyrese maxey first half games this season", "period_split", None, Measure(how="per_game")),
        ("lakers longest losing streak this season", "streak", "points", Measure(won=False)),
        ("lakers longest winning streak this season", "streak", None, Measure(won=True)),
        ("Show me Wembanyama's defensive fingerprint chart", "fingerprint", None, Measure(side="defense")),
        ("what was steph curry's avg 3pt shot distance", "shot_distance", None, Measure(shot_value=3)),
        ("Top 5 scorers with their rebounds and assists", "leaderboard", "points", Measure(key="points", as_typed="points", beside=("rebounds", "assists"))),
        # A measure the WORDS name stands on a whole-line reader whether or not the stat-word list knows its word (the drop is the model's key's alone):
        # "3s", "3pm" and "rebs" are box-score words and no STAT_WORDS word, and each answered the default line until this slice (ISSUES.md, closed).
        ("grayson allen 3s made last season", "player_stat", None, measure_of("threePointFieldGoalsMade")),
        ("klay 3pm each HOME game LAST season", "player_stat", None, measure_of("threePointFieldGoalsMade")),
        ("Michael porter Rebs 2h vs mavericks game log", "period_split", None, Measure(key="rebounds", as_typed="rebounds", how="per_game")),
        # The comparison's whole line holds the stat asked about, and its reader refuses a named one: the words' key is dropped there too.
        ("compare luka and sga in netpts", "player_compare", None, None),
        # Fouling out is the count's own stat; a games count is no measure; "td3s" is a triple-double and never a shot.
        ("how many times has embiid fouled out", "threshold_count", "fouls", measure_of("fouls")),
        ("how many games did embiid play", "player_stat", "games_played", None),
        ("luka td3s home", "player_stat", "threePointFieldGoalsMade", measure_of("triple_double")),
    ],
)
def test_the_words_read_as_one_measure(question: str, intent: str, key: str | None, expected: Measure | None) -> None:
    assert _read(question, intent, key) == expected


def test_the_models_key_is_context_the_words_confirm_and_never_the_source_where_they_name_one() -> None:
    """The one REQUIRED key the normalizer fills whether or not the question
    names a stat: a whole-line reader drops it where the words name none, a
    reader that reads it keeps it, and the words win where both name one."""
    assert _read("Knicks stats this season", "team_stat", "points") is None
    assert _read("Knicks pace this season", "team_stat", "pace") == measure_of("pace")
    assert _read("who led the league in assists in 2019", "leaderboard", "assists") == measure_of("assists")
    assert _read("How far was Curry average three pointer?", "shot_distance", "threePointFieldGoalsAttempted") == Measure(
        key="threePointFieldGoalsMade", as_typed="threePointFieldGoalsMade", shot_value=3
    )


def test_the_tagger_claims_the_characters_it_read_once_and_they_ride_the_route() -> None:
    question = "who were the top 10 in defensive netpoints / 100 possessions"
    read = read_measure(question, MeasureContext(intent="leaderboard"))
    assert read.measure is not None and read.measure.as_typed == "netpoints_defense_per_100"
    assert [question[c.start : c.end] for c in read.claims] == ["defensive netpoints / 100", "/ 100"]
    route = settle("leaderboard", {}, question)
    # The rate's stretch inside the grammar's is the one reading, folded into one claim of it on the route.
    assert [(c.what, question[c.start : c.end]) for c in route.claims if c.what != "window"] == [("measure", "defensive netpoints / 100")]
    # Two stretches of one reading that overlap without one holding the other are one claim of it,
    # not a joined one - cut to the words the tagger needed (Phase 3, step 3, span.needed): "per"
    # reads the rate with either "100" or "possessions" gone, so neither is claimed, and both are
    # unread words, as the claims ledger counts them.
    question = "which players ranked in the top 10 for offensive netpoints per 100 possessions"
    route = settle("leaderboard", {}, question)
    assert [(c.what, question[c.start : c.end]) for c in route.claims if c.what != "window"] == [("measure", "offensive netpoints per")]
    # A word the split found between characters its pattern breaks on is read, with no characters to claim.
    assert named("most (3ot + 4ot) points in a game by a team nba") == ("points", Claim(17, 23, "measure"))
    assert named("who averages the most TO per game") == ("turnovers", Claim(22, 24, "measure"))
    assert named("compared to other teams") is None


def test_the_two_readers_the_intent_stages_ask() -> None:
    assert names_a_stat("compare sga and embiid on rebounding") and not names_a_stat("compare sga and embiid")
    assert team_metric_named("rebounds allowed per team") == ("rebounds allowed", Claim(0, 16, "measure"))
    assert team_metric_named("celtics rebounds against the knicks") == ("rebounds", Claim(8, 16, "measure"))
    # The LONGEST alias the question holds names the metric, wherever it stands.
    assert team_metric_named("including record and ts% and efg% and turnover percentage") == ("turnover", Claim(38, 46, "measure"))
    assert team_metric_named("jokic stats") is None


# ---------------- the typed value: the door, the projection, the cells ----------------


@pytest.mark.parametrize(
    "slots",
    [
        {"stat": "points"},
        {"stat": "threePointFieldGoalPct", "rate": "total"},
        {"stat": "netpoints_defense_per_100"},
        {"stat": "fouls", "rate": "per 100"},
        {"stat": "avg_game_score"},
        {"per_game": True},
        {"stat": "rebounds", "per_game": True},
        {"side": "defense"},
        {"shot_value": 3},
        {"stat": "threePointFieldGoalsMade", "shot_value": 3},
        {"kind": "win"},
        {"stat": "assists", "kind": "win"},
        {"kind": "loss"},
        {"stat": "netpoints_per_100", "fields": ["team"]},
        {"stat": "points", "fields": ["rebounds", "assists"]},
        {"stat": "rebounds allowed"},
        {"stat": "career_playoffs"},
        {"stat": "assist_o_net_pts"},
    ],
)
def test_the_seven_slots_round_trip_through_the_measure(slots: dict[str, Any]) -> None:
    scope = Scope.from_slots(slots)
    assert scope.measure is not None and scope.to_slots() == slots
    assert Scope(measure=scope.measure).to_slots() == slots


def test_the_door_resolves_the_spelling_and_keeps_it() -> None:
    folded = Scope.from_slots({"stat": "netpoints_defense_per_100"}).measure
    assert folded == Measure(key="netpoints", as_typed="netpoints_defense_per_100", how="per_100", side="defense")
    opponent = Scope.from_slots({"stat": "points_allowed"}).measure
    assert opponent == Measure(key="points", as_typed="points_allowed", whose="opponent")
    unknown = Scope.from_slots({"stat": "career_playoffs"}).measure
    assert unknown == Measure(key=None, as_typed="career_playoffs")
    fingerprint = Scope.from_slots({"stat": "assist_o_net_pts"}).measure
    assert fingerprint == Measure(key="netpoints", as_typed="assist_o_net_pts", side="offense", category="assist")
    assert Scope.from_slots({"stat": ""}).measure is None and Scope.from_slots({}).measure is None


def test_the_projection_keeps_the_slot_era_shape_in_its_place() -> None:
    projected = Scope.from_slots({"stat": "points", "threshold": 30, "fields": ["rebounds"], "rate": "total", "kind": "win"}).projected()
    keys = list(projected)
    # The seven where the fields stood, the lines' three after `stat`, the window's `ranked_by` after `fields` and its `rank` after `kind`.
    assert keys[keys.index("stat") : keys.index("rank") + 1] == ["stat", "threshold", "above", "below", "fields", "ranked_by", "per_game", "rate", "side", "shot_value", "kind", "rank"]
    assert (projected["stat"], projected["threshold"], projected["fields"], projected["per_game"], projected["rate"], projected["side"], projected["shot_value"], projected["kind"]) == (
        "points",
        30,
        ("rebounds",),
        False,
        "total",
        None,
        None,
        "win",
    )
    empty = Scope().projected()
    assert (empty["stat"], empty["fields"], empty["per_game"], empty["rate"], empty["side"], empty["shot_value"], empty["kind"]) == (None, (), False, None, None, None, None)
    # A side or a rate the key folds in is the key's, never a slot of its own.
    assert Scope.from_slots({"stat": "netpoints_defense_per_100"}).projected()["side"] is None
    assert Scope.from_slots({"stat": "avg_game_score"}).projected()["per_game"] is False


def test_a_value_no_measure_can_hold_is_refused_at_the_door() -> None:
    with pytest.raises(ScopeError):
        Scope.from_slots({"side": "middle"})
    with pytest.raises(ScopeError):
        Scope.from_slots({"shot_value": 4})
    with pytest.raises(ScopeError):
        Measure(key="points", as_typed="points", how="per_36")  # type: ignore[arg-type]
    with pytest.raises(ScopeError):
        Scope.from_slots({"measure": measure_of("points"), "stat": "points"})


def test_the_cell_its_decline_name_and_what_a_reader_leaves_unhonored() -> None:
    assert frozenset({"rate"}) == Measure.CELLS and "rate" in Scope.CELLS
    unit = Scope.from_slots({"stat": "fouls", "rate": "per 100"})
    assert cell_set(unit, "rate") and unhonored_cells(unit, frozenset()) == ["rate"] and unhonored_cells(unit, Measure.CELLS) == []
    assert not cell_set(Scope.from_slots({"stat": "netpoints_per_100"}), "rate")  # the key's own rate is no cell
    assert cells_stated(PointShape("player_seasons", "ranking", "player")) >= Measure.CELLS
    from association.query import compose

    assert not any(Measure.CELLS & cells_stated(shape) for shape in compose._ROUTES if shape.relation == "player_games")


# ---------------- the catalog: one closed set of keys, six vocabularies as lookups into it ----------------


def test_the_catalog_is_one_closed_set_of_keys_the_six_vocabularies_resolve_into() -> None:
    assert set(get_args(MeasureKey)) == set(CATALOG)
    assert all(alias[0] in CATALOG for alias in ALIASES.values())
    # Every normalizer key, every grammar spelling and every team metric's alias names a catalog key.
    assert all(key_of(stat)[0] is not None for stat in NORMALIZER_STATS if stat)
    assert all(key_of(spelling)[0] is not None for _, spelling in lexicon.MEASURE_GRAMMAR)
    assert all(key_of(alias)[0] is not None for alias in lexicon.STAT_ALIASES)
    # What each key is read as on each relation exists there.
    for spec in CATALOG.values():
        assert spec.column is None or spec.column in GAME_COLUMNS
        assert spec.measure is None or spec.measure in GAME_COLUMNS or spec.measure in DERIVED_MEASURES
        assert all(metric in LEADERBOARD_METRICS for metric in (spec.metric, spec.career_metric, spec.total_metric, spec.per_game_metric) if metric)
        assert spec.team is None or spec.team in TEAM_METRICS
        assert spec.team_opponent is None or spec.team_opponent in TEAM_METRICS


#: A metric or a derived measure named outright, and the grammar's two
#: spellings for keys the model spells otherwise: each reads as its key,
#: where the slot-era tables gave it facets of its own. No reader reached
#: them with the facets that moved (the four populations are identical).
_UNIFIED = frozenset({"avg_game_score", "avg_points", "total_points", "plus_minus", "points_differential", "three_pct", "three_point_pct", "three_pt_pct", "two_pct", "true_shooting", "usage", "won"})


def test_every_spelling_resolves_as_it_did_on_the_facets_its_vocabulary_reached() -> None:
    """``measure_spellings.json``: every spelling the six vocabularies could
    hand the answer side, with what ``stat_measure``, ``stat_column``,
    ``resolve_metric`` (a season and a career) and ``resolve_team_metric``
    returned for it on the tree before this slice (``508d643``). A team
    metric's alias text reached the team readers alone, a box-score
    abbreviation the line readers alone; a key's own spelling reached every
    facet. The unified spellings are the catalog's one deliberate change."""
    recorded = json.loads((Path(__file__).parent / "measure_spellings.json").read_text())
    team_alias = set(lexicon.STAT_ALIASES) | {f"{alias} allowed" for alias in lexicon.STAT_ALIASES}
    keys_own = set(NORMALIZER_STATS) | {spelling for _, spelling in lexicon.MEASURE_GRAMMAR} | set(lexicon.MEASURE_WORDS.values())
    checked = 0
    for spelling, before in recorded.items():
        measure = measure_of(spelling)
        after = {
            "stat_measure": stat_measure(measure),
            "stat_column": stat_column(measure),
            "metric": resolve_metric(measure),
            "career_metric": resolve_metric(measure, career=True),
            "team": resolve_team_metric(measure),
        }
        if spelling in _UNIFIED:
            continue
        facets: tuple[str, ...]
        if spelling in team_alias and spelling not in keys_own:
            facets = ("team",)
        elif spelling in lexicon.MEASURE_WORDS and spelling not in keys_own:
            facets = ("stat_measure", "stat_column")
        else:
            facets = tuple(after)
        for facet in facets:
            assert after[facet] == before[facet], (spelling, facet, before[facet], after[facet])
            checked += 1
    assert checked >= 500


def test_a_metric_named_outright_keeps_its_form_and_a_total_asked_for_ranks_the_total() -> None:
    assert resolve_metric(measure_of("points")) == "avg_points" and resolve_metric(measure_of("points"), career=True) == "total_points"
    assert resolve_metric(measure_of("avg_points"), career=True) == "avg_points"
    assert resolve_metric(Scope.from_slots({"stat": "points", "rate": "total"}).measure) == "total_points"
    # A total asked of a metric with no total form keeps the form it has (fouls per game), as the ranking read it.
    assert resolve_metric(Scope.from_slots({"stat": "fouls", "rate": "total"}).measure) == "avg_fouls"
    assert resolve_metric(measure_of("netpoints_defense")) == "netpoints_defense" and resolve_metric(measure_of("assist_o_net_pts")) == "assist_o_net_pts"
    assert resolve_metric(Scope.from_slots({"stat": "netpoints_defense", "rate": "/ 90"}).measure) == "netpoints_defense"
    assert resolve_team_metric(measure_of("points_allowed")) == "opponent_points" and resolve_team_metric(measure_of("rebounds allowed")) is None
    assert stat_measure(measure_of("wins")) == "won" and stat_measure(measure_of("points_allowed")) is None


def test_a_plus_minus_log_shows_the_column_it_was_asked_for(pg_ctx: AnswerContext) -> None:  # noqa: F811 - the fixture
    """A fix the catalog makes: the grammar's "plus_minus" matched no column
    list on the slot-era tree (``log_extras("plus_minus")`` was ``()``, the
    column list being keyed ``plusMinus``), so a plus-minus log listed the
    four line columns and no +/-; through the key it adds the column. No
    recorded answer moves (the two plus-minus logs on the 2,710 readings
    are refused for a playoff round first); the wording is "<player> plus
    minus game log" and "<player> +/- log"."""
    assert log_extras(measure_of("plus_minus")) == ("+/-",) == log_extras(measure_of("plusMinus"))
    answer = game_log(pg_ctx, Reading.from_slots({"player": "Brandin Podziemski", "stat": "plus_minus"})).answer or ""
    assert "+/-" in answer


def test_a_fingerprint_metric_named_outright_is_refused_by_the_log_as_before() -> None:
    with pytest.raises(Exception, match="no per-game column for 'assist_o_net_pts'"):
        log_extras(measure_of("assist_o_net_pts"))
    assert log_extras(measure_of("games_played")) == () and log_extras(None) == ()


def test_a_team_total_named_by_a_metrics_alias_stays_the_per_game_line() -> None:
    """ISSUES.md, "A team total named by a metric's alias": the stages'
    alias text ("fgm") never matched a column, so the team compiler's total
    never read it and the per-game line answered; the catalog keeps that
    by the spelling's vocabulary, and the key's own spelling reads the total."""
    assert named_by_a_team_metric(measure_of("fgm")) and not named_by_a_team_metric(measure_of("fieldGoalsMade"))
    assert _team_measure(Scope(subject=Who(kind="team", teams=("Orlando Magic",)), measure=Scope.from_slots({"stat": "fgm", "rate": "total"}).measure), "how many fgm did the magic have") is None
    assert _team_measure(Scope(subject=Who(kind="team", teams=("Orlando Magic",)), measure=measure_of("fieldGoalsMade")), "how many did the magic have") == "fieldGoalsMade"
    assert _team_measure(Scope(subject=Who(kind="team", teams=("Orlando Magic",)), measure=measure_of("points_allowed")), "magic total this season") == "points_allowed"


# ---------------- contract 4: the cell changes what a relation reads, and the planner declines it elsewhere ----------------


def test_the_rate_cell_changes_what_the_season_line_ranking_reads(season_ctx: AnswerContext) -> None:  # noqa: F811 - the fixture
    assert leaderboard(season_ctx, Reading.from_slots({"stat": "points"})).data["leaders"][0]["display_name"] == "Low Volume"
    total = leaderboard(season_ctx, Reading.from_slots({"stat": "points", "rate": "total"}))
    assert total.data["leaders"][0]["display_name"] == "Volume Scorer" and "in total points" in total.answer


def test_the_planner_declines_the_rate_cell_where_no_reader_states_it(con: Any) -> None:  # noqa: F811 - the fixture
    """The game-level ranking (a position group, no season-line metric)
    cannot honor a unit, and says so in the sentence the relation check
    always gave; the season line's ranking refuses a unit its metric has no
    form of by name (the point's ``ranking_unit`` cause)."""
    route, _, _ = read_route(con, "centers by pf per 100", [], "fouls")
    reading = reading_from_route(con, "centers by pf per 100", route)
    assert reading.scope.measure == Measure(key="fouls", as_typed="fouls", how="per_100", unit="per 100")
    assert plan_point(reading).declined == "the relation cannot honor ['rate'] - it would answer for a different span than was asked"
    route, _, _ = read_route(con, "who were the top 10 in defensive netpoints / 90", [], "netpoints")
    reading = reading_from_route(con, "who were the top 10 in defensive netpoints / 90", route)
    assert reading.point_refusal is not None and reading.point_refusal.kind == "ranking_unit" and reading.point_refusal.facts == {"metric": "netpoints_defense", "rate": "/ 90"}
    assert reading.scope.span == Span(season_type=2) or reading.scope.span.season_type == 2
