"""The single-game high reader: a named player's biggest game by one stat,
or the league's - ``GAME_HIGHS``'s own point, read into a
:class:`~association.query.result.Result` whose body is a
:class:`~association.query.result.Rows` of games ranked by the measure
(``Rows.by``). The words are the sayer's
(:mod:`association.query.compose.say`), and what they need beyond the
compiled rows - the span's own seasons, the floor, the empty box scores,
the withheld stat, the rebuilt top game, and the defaulted season's
redirect, a decision - are values, notes and a
:class:`~association.query.result.Decided` here.

The statement is the planned point, compiled and executed
(:func:`~association.query.compose.core.compile_query`,
:func:`~association.query.compose.core.rows_of`) - :func:`~association.query.compose.core.run`'s
extras are nothing this shape reads.

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from typing import Any

import duckdb

from association.nba.coverage import COVERAGE
from association.nba.season import eastern_date
from association.query.entities import Entity, resolved_team, slot_season
from association.query.measure import spelled
from association.query.notes import Note
from association.query.player_games import REBUILT_STATS, STAT_LABELS, THRESHOLD_STAT_COLUMNS
from association.query.player_relation import empty_box_scores, player_relation_season_type, rebuilt_in_scope, span_of
from association.query.reading import Cuts
from association.query.result import CountFacts, Decided, Narrowing, Part, Result, Rows, Span, Unanswered
from association.query.season_line import season_redirect
from association.query.season_text import SEASON_TYPE_NAMES
from association.query.team_relation import team_games

from .core import Query, compile_query, rows_of
from .counts import box_score_span
from .team import TeamQuery, compile_team_high


def _single_game_high_column(q: Query) -> str | None:
    """The column ``q`` ranks games by where it is ``GAME_HIGHS``'s
    own point - the stat the reading measures (the measure tagger's),
    whitelisted, first among the
    measures, top games first, nothing the question's words added - else
    ``None``."""
    stat = spelled(q.measure)
    column = THRESHOLD_STAT_COLUMNS.get(stat) if stat is not None else None
    if stat is None or column is None or q.skeleton != "rows" or q.order != "measure" or q.direction != "desc" or q.predicates or q.position or not q.measures or q.measures[0] != column:
        return None
    return column


def read_single_game_high(con: duckdb.DuckDBPyConnection, q: Query) -> Result | Unanswered | None:
    """``GAME_HIGHS``'s own point - the top games by one stat, a named
    player's or the league's - read into a Result over the compiled
    statement. ``None`` where the point is not that, and the compiler's own
    sentence answers.

    .. versionadded:: 5.0.0

    .. versionchanged:: 6.0.0
       Takes no ``stated``: the answer side checks the point's cells against
       its shape's row before asking (``compose.plan.cells_unhonored``,
       Phase 3, step 2's closing slice).
    """
    scope = q
    column = _single_game_high_column(q)
    if column is None:
        return None
    compiled = compile_query(con, q)
    player = compiled.player
    name = player.name if player is not None else None
    games = [
        {"player": r.get("player") or name, "value": r[column], "date": eastern_date(r["day"]), "opponent": r["opponent"], "reconstructed": bool(r["reconstructed"])}
        for r in rows_of(con, compiled)
        if r[column] is not None
    ]
    season, season_type = compiled.span.season, player_relation_season_type(scope)
    span, notes = box_score_span(con, season, season_type, player)
    player_id = player.id if player is not None else None
    empty = empty_box_scores(con, season, season_type, player_id, covered_by_rebuild=compiled.rebuilt)
    withheld = 0 if games or column in REBUILT_STATS else rebuilt_in_scope(con, season, season_type, player_id)
    stat = spelled(scope.measure)
    notes += _single_game_high_notes(games, STAT_LABELS.get(stat or "", stat or ""), season, name, empty, withheld)
    # A season read by default ("career high" names none, "kawhi most threes in
    # a game" neither) that held nothing for a named player is redirected, not
    # answered as though the season had been asked.
    defaulted = season is not None and not scope.span.season
    redirect = _single_game_high_redirect(con, defaulted, player, bool(games), empty[0], withheld, season_type)
    # The league's high narrowed to one team's players says the team
    # ("... in a single game for the Cleveland Cavaliers ..."): the games
    # are theirs alone, and a sentence naming no team reads as the
    # league's (ISSUES.md #167).
    team = compiled.narrowed.team if player is None else None
    return Result(
        subject=name or "every player",
        relation="player" if player is not None else "everyone",
        span=span,
        narrowing=Narrowing(phrase=f" for the {team.name}") if team is not None else Narrowing(),
        parts=(Part(body=Rows(columns=(column,), rows=tuple(games), by=column)),),
        notes=tuple(notes),
        decisions=(redirect,) if redirect is not None else (),
        facts=CountFacts(stat=stat, box_scores_from=COVERAGE["player_box_stats"].first_season, empty_box_scores=empty[0]),
    )


def _single_game_high_notes(games: list[dict[str, Any]], label: str, season: int | None, name: str | None, empty: tuple[int, int | None, int | None], withheld: int) -> list[Note]:
    """The high's notes after its span's, in order: a stat
    withheld from rebuilt lines (a DECISION, not a gap, said where nothing
    was found), a league-wide career is not all-time, the empty box scores
    (not where the withheld stat already explains the gap - "empty box
    scores, so there is no per-game high" would contradict it: the lines
    are there, they were held back), and a top game that rests on a
    rebuilt line."""
    notes: list[Note] = []
    if not games and withheld:
        notes.append(Note("stat_withheld", {"games": withheld, "label": label}))
    if season is None and name is None:
        notes.append(Note("floor", {"table": "box_scores", "first": COVERAGE["player_box_stats"].first_season, "what": "league_record"}))
    if not withheld and empty[0] and empty[1] is not None and empty[2] is not None:
        notes.append(Note("games_unseen", {"why": "empty_box_score", "games": empty[0], "first": empty[1], "last": empty[2], "whose": name}))
    # Said whenever the ANSWER rests on a rebuilt line, not whenever one was
    # read: a rebuilt game that lost to a fetched one changes nothing a reader
    # needs to know about the number they were given.
    if games and games[0]["reconstructed"]:
        notes.append(Note("lines_rebuilt", {"games": 1, "what": "single_game"}))
    return notes


def _single_game_high_redirect(con: duckdb.DuckDBPyConnection, defaulted: bool, player: Entity | None, found: bool, empty: int, withheld: int, season_type: int) -> Decided | None:
    """Where the season was defaulted rather than named and nothing came
    back for a named player (issue #18): the seasons he IS on record for,
    as a decision the answer states - never where the empty answer is about
    something else (a season the question named outright, no named player
    to redirect, an empty-box-scores gap, a withheld stat: each has its own
    sentence, and this one would duplicate it or answer over it)."""
    if not defaulted or player is None or found or empty or withheld:
        return None
    on_record = season_redirect(con, player.id, season_type, "player_game_log")
    if on_record is None:
        return None
    first, last = on_record
    kind = SEASON_TYPE_NAMES.get(season_type, "regular season")
    return Decided(kind="season_redirected", field="season", chose=None, why="the season read by default holds nothing for him", facts={"first": first, "last": last, "what": kind})


#: How many of a team's (or every team's) best games a read lists: the top
#: game, said, and the two after it, as the league's player high lists them.
TEAM_HIGH_GAMES = 3
"""The games a team's best-single-game read keeps.

.. versionadded:: 6.0.0
"""


def read_team_game_high(con: duckdb.DuckDBPyConnection, q: TeamQuery) -> Result | Unanswered:
    """A team's best single games by one figure - or every team's, where the
    question names none ("most 3s made in a single game by a team") - read
    over the team-games relation and the team box row the figure is on
    (:func:`~association.query.compose.team.compile_team_high`) into a
    Result of :class:`~association.query.result.Rows` by the measure, each
    game's team, date, opponent and figure. A refusal back is the
    relation's own (no such team, a coverage floor); a measure no game of a
    team carries is refused by name (``Unsupported``; ISSUES.md #364).

    .. versionadded:: 6.0.0
    """
    from association.query.coverage import coverage_refusal
    from association.query.reading import TEAM_GAME_HIGHS

    refused = coverage_refusal(TEAM_GAME_HIGHS, q)
    if refused is not None:
        return refused
    team: Entity | None = None
    if q.who.team is not None:
        found = resolved_team(con, q.who.team, season=slot_season(q))
        if isinstance(found, Unanswered):
            return found
        team = found
    season_type = q.span.season_type or 2
    seasons = span_of(q.span, "games", season_type=season_type)
    narrowed = team_games(con, team, seasons, TeamQuery(cuts=Cuts(venue=q.cuts.venue, game_n=q.cuts.game_n, situation=q.cuts.situation)), opponent=q.cuts.opponent, date=q.cuts.date)
    if isinstance(narrowed, Unanswered):
        return narrowed
    measure = q.measure
    assert measure is not None
    rows = rows_of(con, compile_team_high(narrowed, team, seasons, measure, limit=TEAM_HIGH_GAMES))
    key = measure.key or "points"
    games = tuple(
        {
            "team": r["team"],
            "value": r["figure"] if r["attempts"] is None else round(100.0 * r["figure"] / r["attempts"], 1),
            "made": r["figure"],
            "attempts": r["attempts"],
            "date": eastern_date(r["day"]),
            "opponent": r["opponent"],
            "won": r["won"],
            "season": r["season"],
        }
        for r in rows
    )
    years = [g["season"] for g in games]
    about = Span(
        season=seasons.season,
        season_type=seasons.season_type,
        career=seasons.season is None,
        first=min(years) if years and seasons.season is None else None,
        last=max(years) if years and seasons.season is None else None,
    )
    return Result(
        subject=team.name if team is not None else "every team",
        relation="team",
        span=about,
        narrowing=Narrowing(phrase=narrowed.filters(), opponent=narrowed.opponent.name if narrowed.opponent else None, venue=narrowed.venue),
        parts=(Part(body=Rows(columns=(key,), rows=games, by=key)),),
        facts=CountFacts(stat=key, box_scores_from=COVERAGE["team_box_stats"].first_season, empty_box_scores=0),
    )
