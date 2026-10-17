"""The measure, typed: the one catalog of
what a question can ask about (:data:`association.query.measure.CATALOG`,
with every spelling the six vocabularies named a measure by resolving into
it), the one tagger that reads the family from the lexicon's words
(:func:`association.query.measure.read_measure`) and the characters it
claims, the typed value (:class:`association.query.reading.Measure`) and
the door the model's stat reaches it by (``Measure.from_slots``), the one
cell the tables declare (``rate``), and the behavioral check that the cell
changes what the season-line ranking reads, and the planner declines it
where no reader states it.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, get_args

import duckdb
import pytest
from routed import measure_of
from routed import staged as settle
from test_career_spans import season_ctx  # noqa: F401 - the season line's fixture, imported by name
from test_parser import con  # noqa: F401 - the parser's name fixture, imported by name
from test_readers import pg_ctx, player_log, player_ranking  # noqa: F401 - the player relation's fixture, imported by name

from association.query import lexicon
from association.query.answer import AnswerContext
from association.query.compose.plan import cells_stated, plan_point
from association.query.entities import names_of
from association.query.measure import (
    ALIASES,
    CATALOG,
    MeasureContext,
    MeasureKey,
    key_of,
    measure_name,
    measured,
    named,
    named_by_a_team_metric,
    names_a_stat,
    read_measure,
    stats_named,
    team_metric_named,
)
from association.query.measures import DERIVED_MEASURES, GAME_COLUMNS, LINE, PERIOD_COLUMNS, SPLIT_LINE, STAT_LINE, log_extras, resolve_metric, stat_column, stat_measure
from association.query.metrics import LEADERBOARD_METRICS
from association.query.normalizer import NORMALIZER_STATS
from association.query.parse import read_route
from association.query.point import _team_measure, measure_under
from association.query.reading import (
    GAME_HIGHS,
    GAMES_COUNTED,
    LINE_RUNS,
    NETPOINTS_FINGERPRINT,
    PERIOD_LOG,
    PLAYER_COMPARISON,
    PLAYER_LINE,
    PLAYER_LOG,
    PLAYER_RANKING,
    PLAYER_SPLITS,
    SEASON_HISTORY,
    SHOT_DISTANCE,
    TEAM_LINE,
    TEAM_RANKING,
    Claim,
    Filtered,
    Measure,
    PointShape,
    Reading,
    ScopeError,
    Span,
    cell_set,
    unhonored_cells,
)
from association.query.reading import Subject as Who
from association.query.team_metrics import TEAM_METRICS, resolve_team_metric

# ---------------- the tagger: words to a Measure ----------------


def _read(question: str, shape: PointShape | None, key: str | None = None, **context: Any) -> Measure | None:
    return measure_under(shape, read_measure(question), MeasureContext(key=key, **context)).measure


@pytest.mark.parametrize(
    ("question", "shape", "key", "expected"),
    [
        # The words' own measure stands over the model's key; the model's key stands where the words name none.
        ("how many points does embiid average", PLAYER_LINE, "rebounds", measure_of("points")),
        ("jokic stats this season", PLAYER_LOG, "assists", measure_of("assists")),
        ("compare sga and embiid", PLAYER_COMPARISON, "points", None),
        # The grammar's spellings, as the tagger writes them, and the catalog's key behind each.
        ("who were the top 10 in defensive netpoints / 100 possessions", PLAYER_RANKING, None, Measure(key="netpoints", as_typed="netpoints_defense_per_100", how="per_100", side="defense")),
        ("who were the top 10 in defensive netpoints / 90", PLAYER_RANKING, "netpoints", Measure(key="netpoints", as_typed="netpoints_defense", how="per_90", unit="/ 90", side="defense")),
        ("centers by pf per 100", PLAYER_RANKING, "fouls", Measure(key="fouls", as_typed="fouls", how="per_100", unit="per 100")),
        ("kevin durant true shooting percentage career", PLAYER_LINE, "threePointFieldGoalPct", measure_of("ts_pct")),
        ("game score nba leader", PLAYER_RANKING, "points", Measure(key="game_score", as_typed="avg_game_score", how="per_game")),
        ("lebron game score this season", PLAYER_LINE, "points", measure_of("game_score")),
        ("show me lebron's 2pt percentage for the past 5 years", SEASON_HISTORY, "threePointFieldGoalPct", measure_of("twoPointFieldGoalPct")),
        ("who lead the league in avg 3 point distance", PLAYER_RANKING, "threePointFieldGoalPct", measure_of("shot_distance")),
        ("who attempted the most three pointers this season?", PLAYER_RANKING, "threePointFieldGoalsMade", measure_of("threePointFieldGoalsAttempted")),
        # A team metric's alias text, as asked - and what a team gives up as the opponent's figure.
        ("Lowest defensive rating by a team this season", TEAM_RANKING, "usage_pct_defense", measure_of("defensive rating")),
        ("rebounds allowed per team", TEAM_RANKING, "rebounds", Measure(key="rebounds", as_typed="rebounds allowed", whose="opponent")),
        # The LONGEST team alias the words hold names the key ("points per game" over the grammar's "points allowed"), and the
        # verb "allowed" makes it the opponent's (#361).
        ("which team allowed the most points per game", TEAM_RANKING, "points", replace(measure_of("points per game"), whose="opponent")),
        ("which team has the most points allowed", TEAM_RANKING, "points", Measure(key="points", as_typed="points allowed", whose="opponent")),
        ("which team has the lowest scoring allowed", TEAM_RANKING, "points", Measure(key=None, as_typed="scoring allowed")),
        # A team's season total, a quarter's log, a run's result, a fingerprint's side, a chart's shots, the columns beside a ranking.
        (
            "how many 3 pointers have the magic made so far this season",
            TEAM_LINE,
            "threePointFieldGoalsMade",
            Measure(key="threePointFieldGoalsMade", as_typed="3 pointers", how="total", unit="total"),
        ),
        ("tyrese maxey first half games this season", PERIOD_LOG, None, Measure(how="per_game")),
        ("lakers longest losing streak this season", LINE_RUNS, "points", Measure(won=False)),
        ("lakers longest winning streak this season", LINE_RUNS, None, Measure(won=True)),
        ("Show me Wembanyama's defensive fingerprint chart", NETPOINTS_FINGERPRINT, None, Measure(side="defense")),
        ("what was steph curry's avg 3pt shot distance", SHOT_DISTANCE, None, Measure(shot_value=3)),
        ("Top 5 scorers with their rebounds and assists", PLAYER_RANKING, "points", Measure(key="points", as_typed="points", beside=("rebounds", "assists"))),
        # A measure the WORDS name stands on a whole-line reader whether or not the stat-word list knows its word (the drop is the model's key's alone):
        # "3s", "3pm" and "rebs" are box-score words and no STAT_WORDS word, and each names its measure.
        ("grayson allen 3s made last season", PLAYER_LINE, None, measure_of("threePointFieldGoalsMade")),
        ("klay 3pm each HOME game LAST season", PLAYER_LINE, None, measure_of("threePointFieldGoalsMade")),
        ("Michael porter Rebs 2h vs mavericks game log", PERIOD_LOG, None, Measure(key="rebounds", as_typed="rebounds", how="per_game")),
        # The comparison's whole line holds the stat asked about, and its reader refuses a named one: the words' key is dropped there too.
        ("compare luka and sga in netpts", PLAYER_COMPARISON, None, None),
        # Fouling out is the count's own stat; a games count is no measure; "td3s" is a triple-double and never a shot.
        ("how many times has embiid fouled out", GAMES_COUNTED, "fouls", measure_of("fouls")),
        ("how many games did embiid play", PLAYER_LINE, "games_played", None),
        ("luka td3s home", PLAYER_LINE, "threePointFieldGoalsMade", measure_of("triple_double")),
        # Two stats or more a whole line shows read no one measure: the line, the log, the period's log, the splits, the
        # history and a team's line say them all (#454: "James harden rebound and assist vs POR" answered his rebounds).
        ("James harden rebound and assist vs POR", PLAYER_LINE, None, None),
        ("Giannis first half points rebounds assists gamelog vs washington", PERIOD_LOG, None, Measure(how="per_game")),
        ("Tyus jones points reb ast last 10 games", PLAYER_LOG, "points", None),
        ("lebron home and away splits points and assists", PLAYER_SPLITS, None, None),
        ("lebron points and rebounds by season", SEASON_HISTORY, None, None),
        ("celtics points and rebounds per game this season", TEAM_LINE, None, None),
        # ... not where the whole line leaves one out, nor on a shape that reads one stat.
        ("lebron points and true shooting this season", PLAYER_LINE, None, measure_of("ts_pct")),
        ("lebron steals and blocks by season", SEASON_HISTORY, None, measure_of("steals")),
        ("most points and rebounds in a game this season", GAME_HIGHS, None, measure_of("rebounds")),
    ],
)
def test_the_words_read_as_one_measure(question: str, shape: PointShape | None, key: str | None, expected: Measure | None) -> None:
    assert _read(question, shape, key) == expected


def test_every_stat_the_words_name_is_read_once_in_order() -> None:
    """The stats a question names, each once by its catalog key, with the
    characters that name it (#454)."""
    assert [key for key, _ in stats_named("James harden rebound and assist vs POR")] == ["rebounds", "assists"]
    assert [key for key, _ in stats_named("Tyus jones points reb ast pts last 10 games")] == ["points", "rebounds", "assists"]
    assert [(key, (claim.start, claim.end)) for key, claim in stats_named("lebron 3 pointers and assists")] == [("threePointFieldGoalsMade", (7, 17)), ("assists", (22, 29))]
    assert stats_named("lebron stats") == ()


def test_the_models_key_is_context_the_words_confirm_and_never_the_source_where_they_name_one() -> None:
    """The one REQUIRED key the normalizer fills whether or not the question
    names a stat: a whole-line reader drops it where the words name none, a
    reader that reads it keeps it, and the words win where both name one."""
    assert _read("Knicks stats this season", TEAM_LINE, "points") is None
    assert _read("Knicks pace this season", TEAM_LINE, "pace") == measure_of("pace")
    assert _read("who led the league in assists in 2019", PLAYER_RANKING, "assists") == measure_of("assists")
    assert _read("How far was Curry average three pointer?", SHOT_DISTANCE, "threePointFieldGoalsAttempted") == Measure(
        key="threePointFieldGoalsMade", as_typed="threePointFieldGoalsMade", shot_value=3
    )


def test_the_tagger_claims_the_characters_it_read_once_and_they_ride_the_route() -> None:
    question = "who were the top 10 in defensive netpoints / 100 possessions"
    read = measure_under(PLAYER_RANKING, read_measure(question), MeasureContext())
    assert read.measure is not None and read.measure.as_typed == "netpoints_defense_per_100"
    assert [question[c.start : c.end] for c in read.claims] == ["defensive netpoints / 100", "/ 100"]
    route = settle(PLAYER_RANKING, question)
    # The rate's stretch inside the grammar's is the one reading, folded into one claim of it on the route.
    assert [(c.what, question[c.start : c.end]) for c in route.claims if c.what != "window"] == [("measure", "defensive netpoints / 100")]
    # Two stretches of one reading that overlap without one holding the other are one claim of it,
    # not a joined one - cut to the words the tagger needed (span.needed): "per"
    # reads the rate with either "100" or "possessions" gone, so neither is claimed, and both are
    # unread words, as the claims ledger counts them.
    question = "which players ranked in the top 10 for offensive netpoints per 100 possessions"
    route = settle(PLAYER_RANKING, question)
    assert [(c.what, question[c.start : c.end]) for c in route.claims if c.what != "window"] == [("measure", "offensive netpoints per")]
    # A word the split found between characters its pattern breaks on is read, with no characters to claim.
    assert named("most (3ot + 4ot) points in a game by a team nba") == ("points", Claim(17, 23, "measure"))
    assert named("who averages the most TO per game") == ("turnovers", Claim(22, 24, "measure"))
    assert named("compared to other teams") is None
    # "percent" spelled out is the percentage: "3pt percent" ranked 3-pointers made, "fg percent" read nothing.
    assert named("highest single season 3pt percent") == ("threePointFieldGoalPct", Claim(22, 33, "measure"))
    assert named("highest fg percent in nba history") == ("fieldGoalPct", Claim(8, 18, "measure"))
    assert named("highest ft pct") == ("freeThrowPct", Claim(8, 14, "measure"))


def test_the_two_measure_readers_the_grammars_rules_ask() -> None:
    assert names_a_stat("compare sga and embiid on rebounding") and not names_a_stat("compare sga and embiid")
    assert team_metric_named("rebounds allowed per team") == ("rebounds allowed", Claim(0, 16, "measure"))
    assert team_metric_named("celtics rebounds against the knicks") == ("rebounds", Claim(8, 16, "measure"))
    # The LONGEST alias the question holds names the metric, wherever it stands.
    assert team_metric_named("including record and ts% and efg% and turnover percentage") == ("turnover", Claim(38, 46, "measure"))
    assert team_metric_named("jokic stats") is None


# ---------------- the typed value: the door, the projection, the cells ----------------


def test_the_door_resolves_the_spelling_and_keeps_it() -> None:
    """The model's stat reaches the Reading through the measure's door
    (:meth:`~association.query.reading.Measure.from_slots`, which the
    parser reads it by): the catalog folds a side, a rate or a category
    into the key and keeps the spelling it was given."""
    folded = Measure.from_slots({"stat": "netpoints_defense_per_100"})
    assert folded == Measure(key="netpoints", as_typed="netpoints_defense_per_100", how="per_100", side="defense")
    opponent = Measure.from_slots({"stat": "points_allowed"})
    assert opponent == Measure(key="points", as_typed="points_allowed", whose="opponent")
    unknown = Measure.from_slots({"stat": "career_playoffs"})
    assert unknown == Measure(key=None, as_typed="career_playoffs")
    fingerprint = Measure.from_slots({"stat": "assist_o_net_pts"})
    assert fingerprint == Measure(key="netpoints", as_typed="assist_o_net_pts", side="offense", category="assist")
    assert Measure.from_slots({"stat": ""}) is None and Measure.from_slots({}) is None


def test_a_value_no_measure_can_hold_is_refused_by_the_measure() -> None:
    with pytest.raises(ScopeError):
        Measure(side="middle")  # type: ignore[arg-type]
    with pytest.raises(ScopeError):
        Measure(shot_value=4)  # type: ignore[arg-type]
    with pytest.raises(ScopeError):
        Measure(key="points", as_typed="points", how="per_36")  # type: ignore[arg-type]


def test_the_cell_its_decline_name_and_what_a_reader_leaves_unhonored() -> None:
    assert frozenset({"rate"}) == Measure.CELLS and "rate" in Filtered.CELLS
    unit = Reading(measure=Measure(key="fouls", as_typed="fouls", how="per_100", unit="per 100"))
    assert cell_set(unit, "rate") and unhonored_cells(unit, frozenset()) == ["rate"] and unhonored_cells(unit, Measure.CELLS) == []
    assert not cell_set(Reading(measure=Measure(key="netpoints", as_typed="netpoints_per_100", how="per_100")), "rate")  # the key's own rate is no cell
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


def test_every_compiler_name_reads_back_to_one_catalog_measure() -> None:
    """The point states what it measures typed (``Reading.measures``): each name its words and lines read a measure by is
    the catalog's Measure (``measure.measured``), which the compiler names
    back once (``measure_name``) - so the names are distinct across keys,
    every name the point reader reads by has a key, and a game's label
    ("margin") is no measure a point reads."""
    names = [spec.measure for spec in CATALOG.values() if spec.measure is not None]
    assert len(names) == len(set(names))
    for key, spec in CATALOG.items():
        if spec.measure is not None:
            assert measured(spec.measure) == Measure(key=key)
            assert measure_name(measured(spec.measure)) == spec.measure
    read_by = {*LINE, *SPLIT_LINE, *STAT_LINE, *PERIOD_COLUMNS, *(name for _, name in lexicon.WORD_MEASURES), *(name for _, name in lexicon.BOOLEAN_RANK_WORDS)}
    assert all(measured(name).key in CATALOG for name in read_by)
    assert (measured("fg_pct"), measured("won"), measured("three_pct")) == (Measure(key="fieldGoalPct"), Measure(key="wins"), Measure(key="threePointFieldGoalPct"))
    with pytest.raises(KeyError):
        measured("margin")


#: A metric or a derived measure named outright, and the grammar's two
#: spellings for keys the model spells otherwise: each reads as its key, so
#: its facets are the key's, not the ones recorded for its spelling.
_UNIFIED = frozenset({"avg_game_score", "avg_points", "total_points", "plus_minus", "points_differential", "three_pct", "three_point_pct", "three_pt_pct", "two_pct", "true_shooting", "usage", "won"})


def test_every_spelling_resolves_as_recorded_on_the_facets_its_vocabulary_reaches() -> None:
    """``measure_spellings.json``: every spelling the six vocabularies could
    hand the answer side, with what ``stat_measure``, ``stat_column``,
    ``resolve_metric`` (a season and a career) and ``resolve_team_metric``
    return for it. A team metric's alias text reaches the team readers
    alone, a box-score abbreviation the line readers alone; a key's own
    spelling reaches every facet. The unified spellings (``_UNIFIED``) read
    as their keys and are not checked against the record."""
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
    assert resolve_metric(Measure(key="points", as_typed="points", how="total", unit="total")) == "total_points"
    # A total asked of a metric with no total form keeps the form it has (fouls per game), as the ranking read it.
    assert resolve_metric(Measure(key="fouls", as_typed="fouls", how="total", unit="total")) == "avg_fouls"
    assert resolve_metric(measure_of("netpoints_defense")) == "netpoints_defense" and resolve_metric(measure_of("assist_o_net_pts")) == "assist_o_net_pts"
    assert resolve_metric(Measure(key="netpoints", as_typed="netpoints_defense", how="per_90", unit="/ 90", side="defense")) == "netpoints_defense"
    assert resolve_team_metric(measure_of("points_allowed")) == "opponent_points" and resolve_team_metric(measure_of("rebounds allowed")) is None
    assert stat_measure(measure_of("wins")) == "won" and stat_measure(measure_of("points_allowed")) is None


def test_a_plus_minus_log_shows_the_column_it_was_asked_for(pg_ctx: AnswerContext) -> None:  # noqa: F811 - the fixture
    """The grammar's "plus_minus" and the key ``plusMinus`` both read
    through the catalog to the +/- column, so a plus-minus log ("<player>
    plus minus game log", "<player> +/- log") shows it beside the line
    columns."""
    assert log_extras(measure_of("plus_minus")) == ("+/-",) == log_extras(measure_of("plusMinus"))
    answer = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), measure=Measure(key="plusMinus", as_typed="plus_minus"))).text or ""
    assert "+/-" in answer


def test_a_fingerprint_metric_named_outright_is_refused_by_the_log() -> None:
    with pytest.raises(Exception, match="no per-game column for 'assist_o_net_pts'"):
        log_extras(measure_of("assist_o_net_pts"))
    assert log_extras(measure_of("games_played")) == () and log_extras(None) == ()


def test_a_team_total_named_by_a_metrics_alias_stays_the_per_game_line() -> None:
    """A team total named by a metric's alias ("fgm") reads no team figure,
    so the per-game line answers; the catalog keeps that by the spelling's
    vocabulary, and the key's own spelling reads the total."""
    assert named_by_a_team_metric(measure_of("fgm")) and not named_by_a_team_metric(measure_of("fieldGoalsMade"))
    assert (
        _team_measure(
            Reading(who=Who(kind="team", teams=("Orlando Magic",)), measure=Measure(key="fieldGoalsMade", as_typed="fgm", how="total", unit="total")),
            "how many fgm did the magic have",
        )
        is None
    )
    assert _team_measure(Reading(who=Who(kind="team", teams=("Orlando Magic",)), measure=measure_of("fieldGoalsMade")), "how many did the magic have") == Measure(key="fieldGoalsMade")
    assert _team_measure(Reading(who=Who(kind="team", teams=("Orlando Magic",)), measure=measure_of("points_allowed")), "magic total this season") == Measure(key="points", whose="opponent")


# ---------------- the cell changes what a relation reads, and the planner declines it elsewhere ----------------


def test_the_rate_cell_changes_what_the_season_line_ranking_reads(season_ctx: AnswerContext) -> None:  # noqa: F811 - the fixture
    assert player_ranking(season_ctx, Reading(measure=Measure(key="points", as_typed="points"))).data["leaders"][0]["display_name"] == "Low Volume"
    total = player_ranking(season_ctx, Reading(measure=Measure(key="points", as_typed="points", how="total", unit="total")))
    assert total.data["leaders"][0]["display_name"] == "Volume Scorer" and "in total points" in total.text


def test_the_planner_declines_the_rate_cell_where_no_reader_states_it(con: Any) -> None:  # noqa: F811 - the fixture
    """The game-level ranking (a position group, no season-line metric)
    cannot honor a unit, and the planner says so; the season line's ranking refuses a unit its metric has no
    form of by name (the point's ``ranking_unit`` cause)."""
    route = read_route(names_of(con), "centers by pf per 100", [], "fouls")
    reading = route
    assert reading.measure == Measure(key="fouls", as_typed="fouls", how="per_100", unit="per 100")
    assert str(plan_point(reading)) == "a rate per 100 cannot be read here - it would answer for a different span than was asked"
    route = read_route(names_of(con), "who were the top 10 in defensive netpoints / 90", [], "netpoints")
    reading = route
    assert reading.point_refusal is not None and reading.point_refusal.kind == "ranking_unit" and reading.point_refusal.facts == {"metric": "netpoints_defense", "rate": "/ 90"}
    assert reading.span == Span(season_type=2) or reading.span.season_type == 2


@pytest.mark.parametrize(
    ("question", "unit"),
    [("jayson tatum per 36 minutes stats", "per 36 minutes"), ("jamal crawford lou williams career per36", "per36"), ("most free throw attempts per 36 minutes", "per 36 minutes")],
)
def test_a_rate_per_n_minutes_is_a_unit_on_every_key(con: duckdb.DuckDBPyConnection, question: str, unit: str) -> None:  # noqa: F811 - the fixture
    """#388: "per 36 minutes" is the measure's unit, not a floor of 36
    minutes a game: the ``rate`` cell every reader but the season ranking
    declines and the ranking refuses by name."""
    reading = read_route(names_of(con), question, [], "")
    assert reading.measure is not None and reading.measure.unit == unit and reading.measure.how is None
    assert not [line for line in reading.lines if line.measure == "minutes"]


@pytest.mark.parametrize(
    ("question", "key"),
    [
        ("how many rebounds did the celtics give up this season", "rebounds"),
        ("how many assists on average do the mavericks give up", "assists"),
        ("steals allowed by the knicks this season", "steals"),
        ("celtics opp 3pm this season", "threePointFieldGoalsMade"),
    ],
)
def test_a_teams_box_score_count_given_up_is_its_opponents(question: str, key: str) -> None:
    """#425: a box-score count given up or "allowed" ("how many assists on
    average do the mavericks give up") is the opponents' figure, for every
    count the team compiler sums from the opponents' box rows, not points
    alone."""
    measure = _read(question, TEAM_LINE)
    assert measure is not None and measure.key == key and measure.whose == "opponent"
