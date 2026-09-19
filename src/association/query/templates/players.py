"""Player rankings and lines: threshold counts, leaderboards, a player's stats, comparisons, multi-season history and single-game highs.

.. versionadded:: 3.0.0
   Split out of the former ``association.query.templates`` module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import duckdb

from association.nba.coverage import COVERAGE, POSTSEASON
from association.nba.season import current_season
from association.nba.season import eastern_date as _eastern_date

from ..entities import Availability, Entity
from ..leaderboard import SEASON_TOTAL_OF, LeaderboardError, not_a_postseason_copy, resolve_metric, run_career_leaderboard, run_leaderboard
from ..metrics import EXTRA_FIELD_COLUMNS, LEADERBOARD_METRICS, SEASON_TYPE_LABELS
from ..player_games import Narrowed, aggregate_sql, grouped_sql, league, rows_sql
from .common import (
    _BOX_SCORES,
    _GAME_LOGS,
    HISTORY_COLUMNS,
    PLAYER_STAT_COLUMNS,
    REBUILT_STATS,
    SEASON_TYPE_NAMES,
    STARTER_SIDES,
    STAT_LABELS,
    THRESHOLD_STAT_COLUMNS,
    TemplateContext,
    TemplateResult,
    TemplateUnsupported,
    _box_score_notes,
    _career_end,
    _clamp_limit,
    _count_games,
    _defaulted_season_note,
    _format_value,
    _log_carries_rebuilt,
    _narrow_player_games,
    _Narrowed,
    _no_narrowed_games,
    _period,
    _resolved_player,
    _season_redirect,
    _Span,
    _span_of,
    _table_cell,
)

DEFAULT_LEADERBOARD_LIMIT = 10


# Where each player template's answer is read from, for narrowing an ambiguous
# name to the candidates with a row there. The tables TEMPLATE_SOURCES
# declares, or the per-game table a one-game answer reads instead - so a
# candidate these eliminate is one whose answer would have been empty.
_SEASON_LINES = Availability("player_season_stats_deduped")


def _career_span(intent: str, span: Any, season: Any) -> bool:
    """True for a career question, False for a one-season one; raises for a
    span this template cannot honor.

    A career with a year named is refused rather than read. The router keeps a
    year the question named alongside "career", so "most points ever in a game
    in 2024" (that season), "career leaders since 2015" (a range) and "career
    points through 2010" (a cutoff) all arrive as the same two slots. Answering
    any of them as one of the others is the substitution this module exists to
    prevent."""
    if not span:
        return False
    if span != "career":
        raise TemplateUnsupported(f"{intent} cannot honor span {span!r}")
    if isinstance(season, int):
        raise TemplateUnsupported(f"{intent} cannot tell whether a career span with {season} named means that season, since it, or through it")
    return True


def _season_label(season: int) -> str:
    """1994 -> "1993-94", the way a person names a season."""
    return f"{season - 1}-{season % 100:02d}"


def _box_scope(alias: str, season: int | None, season_type: int) -> tuple[str, list[Any]]:
    """The WHERE clause for one season of box scores, or for a career of them.

    A career starts at the box scores' floor, and that floor is also what keeps
    1993 out: ESPN answers season=1993 with the same games as 1994 (coverage's
    phantom), so a career counted from 1993 counts every 1993-94 game twice -
    26,350 duplicate player-games."""
    if season is None:
        return f"{alias}.season >= ? AND {alias}.season_type = ?", [COVERAGE["player_box_stats"].first_season, season_type]
    return f"{alias}.season = ? AND {alias}.season_type = ?", [season, season_type]


@dataclass(frozen=True)
class _GameSpan:
    """How an answer built from box scores names the games it covers."""

    when: str  # "in the 2026 regular season" - follows a verb
    caption: str  # a noun phrase, for question_shape
    games: str  # "2026 regular season games" - for "no ... in the warehouse"
    since: str  # the box scores' first season, "1993-94"
    preface: str = ""  # said FIRST, when the span is narrower than the question
    league_note: bool = False  # a league-wide career, which is not all-time


def _game_span(con: duckdb.DuckDBPyConnection, season: int | None, season_type: int, player: Entity | None) -> _GameSpan:
    """Name what a box-score answer covers - and, for a named player's career,
    whether the box scores hold it at all.

    Michael Jordan's career began in 1984-85, and the box scores here begin in
    1993-94. His "career high" from them is 55, not 69: fluent, real, and an
    answer to a different question. So a career that began before the box
    scores is answered for the part they hold, and says so before the number
    rather than after it - the reader who stops at the number has been told."""
    kind = SEASON_TYPE_NAMES.get(season_type, "regular season")
    floor = COVERAGE["player_box_stats"].first_season
    since = _season_label(floor)
    if season is not None:
        period = _period(season, season_type)
        return _GameSpan(when=f"in the {period}", caption=period, games=f"{period} games", since=since)
    if player is None:
        return _GameSpan(when=f"in the {kind} since {since}", caption=f"{kind} since {since}", games=f"{kind} games since {since}", since=since, league_note=True)
    began, ended = _seasons_on_record(con, player.id, season_type)
    if isinstance(began, int) and began < floor:
        preface = f"Box scores here begin in {since}, and {player.name}'s {kind} career began in {_season_label(began)}, so his whole career is not in them. "
        return _GameSpan(when=f"in the {kind} since {since}", caption=f"{kind} since {since}", games=f"{kind} games since {since}", since=since, preface=preface)
    years = f" ({_season_label(began)} through {_season_label(ended)})" if isinstance(began, int) and isinstance(ended, int) else ""
    return _GameSpan(when=f"in his {kind} career{years}", caption=f"{kind} career{years}", games=f"{kind} games", since=since)


def _seasons_on_record(con: duckdb.DuckDBPyConnection, athlete_id: str, season_type: int) -> tuple[Any, Any]:
    """A player's first and last season in the per-player season table, which
    reaches back to 1976-77 - before any box score here. A postseason copied
    from the regular season is not a postseason on record."""
    copy = f" AND {not_a_postseason_copy(('points',))}" if season_type == POSTSEASON else ""
    row = con.execute(f"SELECT MIN(t.season), MAX(t.season) FROM player_season_stats t WHERE t.athlete_id = ? AND t.season_type = ?{copy}", [athlete_id, season_type]).fetchone()
    return (row[0], row[1]) if row else (None, None)


def _rebuilt_in_scope(con: duckdb.DuckDBPyConnection, season: int | None, season_type: int, athlete_id: str | None) -> int:
    """How many games in scope carry a line rebuilt from play-by-play.

    Used to explain a refusal rather than to answer: when the stat asked for is
    outside :data:`REBUILT_STATS`, "no games with a box score" is true of the
    fetched lines and hides that rebuilt ones exist and were withheld on
    purpose. Saying which is the difference between a gap and a decision.

    .. versionadded:: 2.2.0
    """
    if not _log_carries_rebuilt(con):
        return 0
    scope, params = _box_scope("l", season, season_type)
    where = f"{scope} AND l.reconstructed"
    if athlete_id is not None:
        where += " AND l.athlete_id = ?"
        params.append(athlete_id)
    row = con.execute(f"SELECT COUNT(*) FROM player_game_log l WHERE {where}", params).fetchone()
    return int(row[0]) if row else 0


def _empty_box_scores(con: duckdb.DuckDBPyConnection, season: int | None, season_type: int, athlete_id: str | None, *, covered_by_rebuild: bool = False) -> tuple[int, int | None, int | None]:
    """Games in scope whose box score is empty: (count, first season, last season).

    Every game from 2012-13 through 2017-18 has a box score, but 161-166 a
    season hold nothing - every player's minutes NULL and every stat 0. LeBron
    James's 76 games of 2012-13 are all present and sum to 1,835 points, against
    the season table's 2,036. A zero can hide a real maximum or a real count but
    never invent one, so the answer stands - and says how many games it could
    not see. For a player, only the empty games he actually played in count.

    ``covered_by_rebuild`` excludes the games the answer DID see through
    ``player_box_stats_filled``. Without it the same answer both reads a game
    and reports it as unseen - "his highest was 43, rebuilt from play-by-play"
    beside "68 of his games have an empty box score, so a bigger game may be
    missing", where those 68 are the very games the 43 came from.
    """
    if covered_by_rebuild and _log_carries_rebuilt(con):
        # What is still unseen: no minutes AND no rebuild to stand in for them.
        scope, params = _box_scope("l", season, season_type)
        if athlete_id is None:
            sql = (
                f"SELECT COUNT(*), MIN(season), MAX(season) FROM (SELECT l.season FROM player_game_log l WHERE {scope} "
                "GROUP BY l.event_id, l.season HAVING MAX(l.minutes) IS NULL AND NOT BOOL_OR(COALESCE(l.reconstructed, FALSE)))"
            )
        else:
            sql = (
                f"SELECT COUNT(*), MIN(l.season), MAX(l.season) FROM player_game_log l WHERE {scope} "
                "AND l.minutes IS NULL AND NOT COALESCE(l.reconstructed, FALSE) AND NOT COALESCE(l.did_not_play, FALSE) AND l.athlete_id = ?"
            )
            params.append(athlete_id)
        row = con.execute(sql, params).fetchone()
        return (int(row[0]), row[1], row[2]) if row else (0, None, None)

    scope, params = _box_scope("b", season, season_type)
    empty = f"SELECT b.event_id, b.season FROM player_box_stats b WHERE {scope} GROUP BY b.event_id, b.season HAVING MAX(b.minutes) IS NULL"
    if athlete_id is None:
        sql = f"SELECT COUNT(*), MIN(season), MAX(season) FROM ({empty})"
    else:
        sql = (
            f"SELECT COUNT(*), MIN(r.season), MAX(r.season) FROM player_box_stats r JOIN ({empty}) e ON e.event_id = r.event_id AND e.season = r.season "
            "WHERE r.athlete_id = ? AND NOT COALESCE(r.did_not_play, FALSE)"
        )
        params.append(athlete_id)
    row = con.execute(sql, params).fetchone()
    return (int(row[0]), row[1], row[2]) if row else (0, None, None)


def _empty_note(found: tuple[int, int | None, int | None], name: str | None, consequence: str) -> str:
    count, first, last = found
    if not count or first is None or last is None:
        return ""
    whose = f"{count:,} of {name}'s games" if name else f"{count:,} {'game' if count == 1 else 'games'}"
    between = f"in {_season_label(first)}" if first == last else f"between {_season_label(first)} and {_season_label(last)}"
    return f" {whose} {between} {'has' if count == 1 else 'have'} an empty box score in this warehouse, so {consequence}."


def threshold_count(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """ "Most games with N+ of some stat" - the shape that motivated this split.

    A KNOWLEDGE_BASE entry covered it, but sat in the truncated-away head of the
    prompt, so three consecutive runs answered with a season-averages
    leaderboard instead. In code it cannot be truncated or substituted.

    .. versionchanged:: 2.1.0
       Honors ``span`` "career": every box score since 1993-94, for the league
       or for one player, saying which. A named player is resolved to one
       person; every player whose name contained the words used to be counted,
       and the top one reported. An ambiguous name is narrowed to the players
       with a box score in the season asked about before it is asked about.

    .. versionchanged:: 2.2.0
       Counts the games whose box score ESPN served empty from the line rebuilt
       out of play-by-play, for the stats a rebuild gets right
       (:data:`REBUILT_STATS`), and says how many of the counted games those
       are. A stat outside that set is still counted from the stored box scores
       alone, and a count of none then says the rebuilt lines were held back
       rather than implying there is nothing to read.
    """
    con = ctx.con
    stat = slots.get("stat")
    column, threshold = _threshold_count_ask(stat, slots.get("threshold"))

    career = _career_span("threshold_count", slots.get("span"), slots.get("season"))
    season = None if career else (slots.get("season") or current_season())
    season_type = slots.get("season_type") or 2
    limit = _clamp_limit(slots.get("limit"))

    player = _threshold_count_player(con, slots.get("player"), season)
    if isinstance(player, TemplateResult):
        return player
    player_id = player.id if player else None
    player_name = player.name if player else None

    # A rebuilt line may be COUNTED, but only for a stat a rebuild gets right
    # (REBUILT_STATS), and only where the warehouse actually carries the flag -
    # an older one has no such column, and a fixture may have no log at all.
    # Without both, the read is the stored table and no count moves.
    from_rebuilt = column in REBUILT_STATS and _log_carries_rebuilt(con)
    rows = _threshold_count_rows(con, column, threshold, season, season_type, limit, player_id, from_rebuilt=from_rebuilt)

    label = STAT_LABELS.get(stat or "", stat or "")
    scope_text = f"{threshold}+ {label}s"
    span = _game_span(con, season, season_type, player)
    # covered_by_rebuild: the games this answer could not see are only the ones
    # the rebuild could not reach either. Counting the rest would disclaim the
    # very games the count was built from.
    empty = _empty_box_scores(con, season, season_type, player_id, covered_by_rebuild=from_rebuilt)
    answer = span.preface + _phrase_threshold_count(rows, scope_text, span.when, player_name)
    if span.league_note:
        answer += f" Box scores begin in {span.since}, so these are not all-time counts: a career that began earlier is counted only from {span.since}."
    # Only when nothing was counted AND the stat was deliberately withheld: a
    # count of none that names a decision beats one that implies missing data.
    withheld = 0 if (rows and rows[0][1]) or column in REBUILT_STATS else _rebuilt_in_scope(con, season, season_type, player_id)
    if withheld:
        answer += (
            f" {withheld:,} of the games in that span were rebuilt from play-by-play, but a {label} is not counted from a rebuilt line: "
            f"rebuilt fouls are wrong in about one game in six, and turnovers in one in thirteen, against one in sixty for points."
        )
    else:
        answer += _empty_note(empty, player_name, "the count may be low" if player else "these counts may be low")
    answer += _threshold_count_rebuilt_note(rows, player is not None)
    leaders = [{"player": name, "games": games} for name, games, _ in rows]
    return TemplateResult(
        data={
            "question_shape": f"games with {scope_text}, {span.caption}",
            "season": season,
            "span": "career" if career else None,
            "leaders": leaders,
            "empty_box_scores": empty[0],
            "rebuilt_games": rows[0][2] if rows else 0,
        },
        answer=answer,
    )


def _threshold_count_ask(stat: Any, threshold: Any) -> tuple[str, int]:
    """The box-score column and the threshold a count is over; raises for a
    stat this does not know or a threshold that counts every game."""
    column = THRESHOLD_STAT_COLUMNS.get(stat) if isinstance(stat, str) else None
    if column is None or not isinstance(threshold, int):
        raise TemplateUnsupported(f"threshold_count needs a known stat and an integer threshold, got {stat!r}/{threshold!r}")
    if threshold < 1:
        # ">= 0" counts every game, which is never the question: measured, "most 3
        # pointers made since 2020" arrived as threshold 0 and was answered as
        # "the most games with 0+ 3-pointers".
        raise TemplateUnsupported(f"a threshold of {threshold} counts every game - not a question threshold_count answers")
    return column, threshold


def _threshold_count_player(con: duckdb.DuckDBPyConnection, text: Any, season: int | None) -> Entity | TemplateResult | None:
    """The one player a count is narrowed to, a clarifying question, or None for the league."""
    # Resolved to one person, as every other template does. This used to be an
    # ILIKE per word, so "Curry" counted Seth's games and Stephen's and reported
    # whichever had more - the prominence tiebreak AGENTS.md records as measured
    # and rejected, applied silently. Narrowed by who has a box score in the
    # season, NOT by who has a qualifying game: that would let the answer pick
    # the player, which is the same tiebreak by another route.
    if isinstance(text, str) and text.strip():
        return _resolved_player(con, text, available=_BOX_SCORES, season=season, through=_career_end(season))
    return None


def _threshold_count_rows(
    con: duckdb.DuckDBPyConnection, column: str, threshold: int, season: int | None, season_type: int, limit: int, player_id: str | None, *, from_rebuilt: bool
) -> list[tuple[Any, ...]]:
    """(name, qualifying games, rebuilt games among them) per player, most first.

    Read through the ``player_game`` relation, so the season floor, the phantom
    and the played guard are the relation's. The guard changes no count: an
    empty or did-not-play line carries 0, which never clears a threshold of 1
    or more - checked against the warehouse before the relation took over, and
    again by the golden comparison after. ``player_name IS NOT NULL`` keeps
    the old INNER JOIN semantics: the log LEFT JOINs ``players``, so a box
    score for an athlete missing from that table would otherwise be counted
    under a NULL name and reported as a nameless leader.
    """
    span = _span_of("career" if season is None else None, season, season_type, "player_game_log")
    season_clause, season_params = span.clause("pgl.season")
    narrowed = league(season_clause, season_params, season_type)
    if player_id is not None:
        narrowed.narrow("pgl.athlete_id = ?", player_id)
    narrowed.narrow_measure(column, ">=", threshold)
    narrowed.narrow("pgl.player_name IS NOT NULL")
    rebuilt_count = "COUNT(*) FILTER (WHERE pgl.reconstructed) AS rebuilt" if from_rebuilt else "0 AS rebuilt"
    # Grouped by athlete_id, not by name: two players can share one.
    sql, params = grouped_sql(narrowed, "pgl.athlete_id, pgl.player_name", ["pgl.player_name", "COUNT(*) AS n", rebuilt_count], order="2 DESC, 1", limit=limit, rebuilt=from_rebuilt)
    return con.execute(sql, params).fetchall()


def _threshold_count_rebuilt_note(rows: list[tuple[Any, ...]], named: bool) -> str:
    """The sentence saying how many of the leader's counted games were rebuilt, or nothing."""
    # Said whenever the COUNT rests on rebuilt games, not whenever one was read:
    # a rebuilt game that cleared no threshold changes nothing about the number
    # the reader was given.
    if not (rows and rows[0][2]):
        return ""
    rebuilt_shown, counted = rows[0][2], rows[0][1]
    whose = "those" if named else f"{rows[0][0]}'s"
    plural = rebuilt_shown != 1
    # "52 of those 52 games" is true and reads as a bug, which costs the
    # sentence the trust it exists to calibrate. Where EVERY counted game
    # was rebuilt - Anthony Davis's whole 2015, and every Chicago and New
    # Orleans season from 2013 to 2018 - say so outright. Two sentences
    # rather than one template with a swapped subject: the shared form gave
    # "every one of those 52 games HAVE", agreeing with the count instead
    # of with its own subject.
    if rebuilt_shown == counted:
        lead = f"None of {whose} {counted} games has a box score from ESPN" if plural else f"{'That' if named else whose + ' only'} game has no box score from ESPN"
    else:
        lead = f"{rebuilt_shown} of {whose} {counted} games {'have' if plural else 'has'} no box score from ESPN"
    return f" {lead} - {'those figures are' if plural else 'that figure is'} rebuilt from play-by-play, so treat the count as close rather than exact."


def _phrase_threshold_count(rows: list[tuple[Any, ...]], scope: str, when: str, player: str | None) -> str:
    """Always names the season outright rather than echoing "this season" back.
    The original failure answered for 2024 while the user meant the current
    season, and said nothing about it - so the season is stated, every time.
    ``when`` is that statement: one season, or a career and where it starts."""
    label = f"games with {scope}"
    if player is not None:
        games = rows[0][1] if rows else 0
        return f"{player} had {games} {label} {when}." if games else f"{player} had no {label} {when}."
    if not rows:
        return f"No player had a game with {scope} {when}."

    top = rows[0][1]
    tied = [name for name, games, _ in rows if games == top]
    if len(tied) > 1:
        leaders = ", ".join(tied[:-1]) + f" and {tied[-1]}"
        sentence = f"{leaders} tied for the most {label} {when}, with {top} each."
    else:
        sentence = f"{rows[0][0]} had the most {label} {when}, with {top}."
    rest = [f"{name} ({games})" for name, games, _ in rows if games != top]
    return sentence + (f" Next: {', '.join(rest)}." if rest else "")


def _signed_cell(value: Any) -> str:
    """A NetPoints cell. Signed, because the sign is the whole reading of it -
    an unmarked "0.42" beside "-1.10" loses which one helped their team."""
    return "-" if value is None else f"{value:+.2f}"


def leaderboard(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """ "Top N players by X" for the metrics in LEADERBOARD_METRICS.

    Thin on purpose: run_leaderboard owns the season default, minimum-sample
    floor and traded-player dedup, and the agent's get_leaderboard tool calls
    the same function. This adds slot mapping and phrasing.

    .. versionchanged:: 2.1.0
       Honors ``span`` "career", ranking whole careers (see
       :func:`~association.query.leaderboard.run_career_leaderboard`) and
       saying whose. ``rate`` "total" ranks a season total rather than a
       per-game average. Every stat name the router is taught now maps to a
       metric, and a qualifier, when one applies, is named in the answer.
    """
    con = ctx.con
    career = _career_span("leaderboard", slots.get("span"), slots.get("season"))
    metric = resolve_metric(slots.get("stat"), career=career)
    if metric is None:
        raise TemplateUnsupported(f"no leaderboard metric for stat {slots.get('stat')!r}")
    if slots.get("rate") == "total":
        # `stat` names a category, never which of its two readings; "most
        # points this season" is a total and "leads in points" a per-game rate.
        metric = SEASON_TOTAL_OF.get(metric, metric)
    if isinstance(slots.get("player"), str) and slots["player"].strip():
        # A leaderboard ranks the league or a team, never one named person.
        # Confirmed live: "Klay Thompson's 3pt percentage over the past 4
        # seasons" landed here and came back with the league's true-shooting
        # leaders, Klay silently dropped.
        raise TemplateUnsupported(f"a leaderboard cannot answer about one named player ({slots['player']!r})")
    fields = _leaderboard_fields(slots, metric)
    if career:
        return _career_leaderboard(con, metric, slots, fields)
    try:
        result = run_leaderboard(
            con,
            metric,
            season=slots.get("season"),
            season_type=slots.get("season_type") or 2,
            team=slots.get("team") if isinstance(slots.get("team"), str) else None,
            fields=fields or None,
            limit=_clamp_limit(slots.get("limit"), default=DEFAULT_LEADERBOARD_LIMIT),
        )
    except LeaderboardError as exc:
        # An ambiguous team, an unknown metric, or a table that needs a
        # warehouse flag - all reasons to fall through, never to guess.
        raise TemplateUnsupported(str(exc)) from exc

    period = _period(result.season, result.season_type or 2)
    where = f"the {result.team_name}" if result.team_name else "the league"
    summary = f"{result.label}, {period}"
    ratio = LEADERBOARD_METRICS[metric].ratio
    answer = (
        _tabulate_leaderboard(result.rows, result.label, where, period, fields, result.min_sample_applied, result.min_sample_column, ratio)
        if fields
        else _phrase_leaderboard(result.rows, result.label, where, period, _qualifier(result.min_sample_applied, result.min_sample_column), ratio)
    )
    return TemplateResult(
        data={"question_shape": summary, "season": result.season, "fields": fields, "min_sample": result.min_sample_applied, "leaders": result.rows},
        answer=answer,
    )


def _leaderboard_fields(slots: dict[str, Any], metric: str) -> list[str]:
    """The extra columns a leaderboard was asked to show beside its metric."""
    # "top 10 in NetPoints ALONGSIDE their points per game" used to be answered
    # without the second half and without saying so - a silent partial answer,
    # the failure this whole architecture exists to prevent. An unknown field
    # falls through rather than being dropped.
    requested = [f for f in slots.get("fields") or [] if isinstance(f, str)]
    unknown = [f for f in requested if f not in EXTRA_FIELD_COLUMNS]
    if unknown:
        raise TemplateUnsupported(f"unknown leaderboard field(s) {unknown}")
    # Deduplicated, order preserved: the router repeats itself sometimes
    # (["points","minutes","minutes"]), which is a slip, not a reason to spend
    # minutes in the agent. A field restating the ranked metric goes too - it
    # rendered the same 33.5 twice under two headings.
    return [f for f in dict.fromkeys(requested) if metric != f"avg_{f}"]


def _career_leaderboard(con: duckdb.DuckDBPyConnection, metric: str, slots: dict[str, Any], fields: list[str]) -> TemplateResult:
    """A career ranking, on its own path because its pool is its own - see
    :func:`~association.query.leaderboard.run_career_leaderboard`."""
    if fields:
        raise TemplateUnsupported("a career leaderboard cannot add per-game columns")
    if isinstance(slots.get("team"), str) and slots["team"].strip():
        # A franchise's career list sums the per-team rows by team, and where a
        # franchise moved, which years are the franchise's is a question of its
        # own. Refused until that is decided, rather than answered with the
        # league's list under the team's name.
        raise TemplateUnsupported("franchise career leaderboards are not supported")
    season_type = slots.get("season_type") or 2
    try:
        result = run_career_leaderboard(con, metric, season_type=season_type, limit=_clamp_limit(slots.get("limit"), default=DEFAULT_LEADERBOARD_LIMIT))
    except LeaderboardError as exc:
        raise TemplateUnsupported(str(exc)) from exc
    kind = SEASON_TYPE_NAMES.get(season_type, "regular season")
    label = f"career {result.label.removeprefix('total ')}"
    since = _season_label(result.pool_first_season)
    qualifier = _qualifier(result.min_sample_applied, result.min_sample_column)
    return TemplateResult(
        data={
            "question_shape": f"{label}, {kind}, players active since {since}",
            "season": None,
            "span": "career",
            "pool_first_season": result.pool_first_season,
            "fields": [],
            "min_sample": result.min_sample_applied,
            "leaders": result.rows,
        },
        answer=_phrase_career_leaderboard(result.rows, label, kind, since, qualifier, LEADERBOARD_METRICS[metric].ratio),
    )


def _phrase_career_leaderboard(rows: list[dict[str, Any]], label: str, kind: str, since: str, qualifier: str, ratio: tuple[str, str] | None) -> str:
    """Says whose careers, every time. The pool is every player active in
    1993-94 or later, counted over his whole career, and nobody whose career
    ended before it - Kareem Abdul-Jabbar is not in the warehouse at all - so
    presenting it as "all-time" would be the unrepresentative ranking
    nba/coverage.py's second floor exists to refuse."""
    gap = f"Careers that ended before {since} are not in this warehouse, so this is not an all-time list."
    if not rows:
        return f"No player qualified for {label} in the {kind}{qualifier}. {gap}"
    top = rows[0]
    years = f"{_season_label(top['first_season'])} through {_season_label(top['last_season'])}"
    detail = f", over {int(top['games']):,} games ({years})" if top.get("games") else ""
    sentence = f"Among players active in {since} or later, {top['display_name']} leads in {label} in the {kind}{qualifier}: {_leader_value(top, ratio)}{detail}."
    rest = [f"{r['display_name']} ({_leader_value(r, ratio, short=True)})" for r in rows[1:]]
    return " ".join([sentence, *([f"Next: {', '.join(rest)}."] if rest else []), gap])


def _leader_value(row: dict[str, Any], ratio: tuple[str, str] | None, *, short: bool = False) -> str:
    """A ranked value as a reader expects it: a percentage as one, with the
    makes and attempts behind it - the "out of how many?" a bare percentage
    always draws - and a count with its thousands separated."""
    value = row.get("value")
    if value is None:
        return "-"
    if ratio:
        made, attempted = row.get(ratio[0]), row.get(ratio[1])
        text = f"{value * 100:.1f}%"
        return text if short or made is None or attempted is None else f"{text} ({int(made):,} of {int(attempted):,})"
    if isinstance(value, int):
        return f"{value:,}"
    # ESPN's averages carry one decimal, and "4" beside "3.8" reads as a count.
    return f"{value:.1f}" if isinstance(value, float) and value == round(value, 1) and abs(value) >= 1 else _format_value(value)


def _qualifier(min_sample: int | None, column: str | None) -> str:
    """ " (minimum 200 3-point attempts)", or nothing. Shown because it answers
    "why isn't X here?" before it is asked - and makes an empty early-season
    board say why it is empty."""
    if not min_sample:
        return ""
    return f" (minimum {min_sample:,} {MIN_SAMPLE_LABELS.get(column or '', column or '')})"


def _phrase_leaderboard(rows: list[dict[str, Any]], label: str, where: str, period: str, qualifier: str = "", ratio: tuple[str, str] | None = None) -> str:
    if not rows:
        return f"No players qualified for {label} in {where} in the {period}{qualifier}."
    top = rows[0]
    sentence = f"{top['display_name']} led {where} in {label} in the {period}{qualifier}, at {_leader_value(top, ratio)}."
    rest = [f"{r['display_name']} ({_leader_value(r, ratio, short=True)})" for r in rows[1:]]
    return sentence + (f" Next: {', '.join(rest)}." if rest else "")


# The qualifying column's real name is not something to put in front of a
# reader ("total_minutes", "gamesPlayed").
MIN_SAMPLE_LABELS = {
    "total_minutes": "minutes",
    "gamesPlayed": "games",
    "games_played": "games",
    "minutes": "minutes",
    "fieldGoalsAttempted": "field-goal attempts",
    "threePointFieldGoalsAttempted": "3-point attempts",
    "freeThrowsAttempted": "free-throw attempts",
    "field_goals_attempted": "field-goal attempts",
    "true_shooting_attempts": "true-shooting attempts",
}


def _tabulate_leaderboard(
    rows: list[dict[str, Any]],
    label: str,
    where: str,
    period: str,
    fields: list[str],
    min_sample: int | None,
    min_sample_column: str | None,
    ratio: tuple[str, str] | None = None,
) -> str:
    """A table once extra columns are asked for - a sentence carrying three
    numbers per player across ten players is unreadable, and the qualifying
    minimum belongs on screen so "why isn't X here?" has a visible answer."""
    header_note = _qualifier(min_sample, min_sample_column)
    if not rows:
        return f"No players qualified for {label} in {where} in the {period}{header_note}."
    columns = [(label, "value")] + [(f, f) for f in fields]
    name_width = max(len(r["display_name"]) for r in rows)
    # The ranked metric keeps its own precision (9.91, not 9.9); the extra
    # box-score columns are per-game averages, where one decimal is the norm.
    cell = lambda row, key: _leader_value(row, ratio, short=True) if key == "value" else _table_cell(row.get(key))  # noqa: E731
    widths = [max(len(title), *(len(cell(r, key)) for r in rows)) for title, key in columns]
    lines = [f"{label}, {where}, {period}{header_note}:"]
    lines.append(" " * name_width + "  " + "  ".join(t.rjust(w) for (t, _), w in zip(columns, widths, strict=True)))
    for row in rows:
        cells = "  ".join(cell(row, key).rjust(w) for (_, key), w in zip(columns, widths, strict=True))
        lines.append(f"{row['display_name'].ljust(name_width)}  {cells}")
    return "\n".join(lines)


DEFAULT_HISTORY_SEASONS = 4


MAX_HISTORY_SEASONS = 20


def player_history(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """One player's stat across several seasons: the last four by default, a
    number of them the question named, or every one of them for a career.

    Every other template answers about a single season, so a multi-season
    question routed to `leaderboard`, which dropped the player and ranked the
    league. Distinct from the other gaps here: a missing DIMENSION cutting
    across the shapes that existed, not a missing shape.

    .. versionchanged:: 2.1.0
       Honors ``span``: "his career" is every season on record, where it used
       to be the default four under a heading that did not say so. The heading
       now names the seasons shown.
    """
    con = ctx.con
    # A named season anchors the range's END rather than replacing it, so
    # "3pt% over the 4 seasons through 2024" still spans four rows.
    latest = slots.get("season") or current_season()
    # Narrowed over every season the history could read, not the last N: the
    # query takes each player's last seasons up to `latest` wherever they fall,
    # so a player who retired a decade earlier still has an answer here.
    player = _resolved_player(con, slots.get("player"), "player_history needs a player name", available=_SEASON_LINES, through=latest)
    if isinstance(player, TemplateResult):
        return player

    stat = slots.get("stat")
    if not isinstance(stat, str) or stat not in HISTORY_COLUMNS:
        raise TemplateUnsupported(f"no per-season history for stat {stat!r}")
    label, columns = HISTORY_COLUMNS[stat]

    span = slots.get("span")
    if span and span != "career":
        raise TemplateUnsupported(f"no span called {span!r}")
    career = span == "career"
    season_type = slots.get("season_type") or 2
    limit = slots.get("limit")
    seasons = limit if isinstance(limit, int) and 1 <= limit <= MAX_HISTORY_SEASONS else DEFAULT_HISTORY_SEASONS

    # A career is every season, however many - not the default four, and not a
    # count the model put in `limit`, which the router asks it for on this
    # intent whether or not the question gave one. The heading says which
    # seasons are shown either way.
    bound = "" if career else " LIMIT ?"
    rows = con.execute(
        f"SELECT season, gamesPlayed, {', '.join(c for c, _ in columns)} FROM player_season_stats_deduped WHERE athlete_id = ? AND season_type = ? AND season <= ? ORDER BY season DESC{bound}",
        [player.id, season_type, latest, *([] if career else [seasons])],
    ).fetchall()

    period = SEASON_TYPE_NAMES.get(season_type, "regular season")
    history = [dict(zip(["season", "games"] + [c for c, _ in columns], r, strict=True)) for r in rows]
    return TemplateResult(
        data={"player": player.name, "stat": stat, "span": "career" if career else None, "seasons": history},
        answer=_phrase_history(player.name, label, period, history, columns, career=career),
    )


def _phrase_history(name: str, label: str, period: str, history: list[dict[str, Any]], columns: list[tuple[str, str]], *, career: bool = False) -> str:
    if not history:
        return f"The warehouse has no {period} seasons on record for {name}."
    headers = ["season", "G"] + [h for _, h in columns]
    keys = ["season", "games"] + [c for c, _ in columns]
    widths = [max(len(h), *(len(_table_cell(row.get(k))) for row in history)) for h, k in zip(headers, keys, strict=True)]
    newest, oldest = history[0]["season"], history[-1]["season"]
    years = f"{oldest}" if oldest == newest else f"{oldest}-{newest}"
    shown = f"career, {years}" if career else years
    lines = [f"{name}, {label} by {period}, {shown} (most recent first):", "  ".join(h.rjust(w) for h, w in zip(headers, widths, strict=True))]
    lines.extend("  ".join(_table_cell(row.get(k)).rjust(w) for k, w in zip(keys, widths, strict=True)) for row in history)
    return "\n".join(lines)


def _season_row(con: duckdb.DuckDBPyConnection, athlete_id: str, columns: list[str], season: int, season_type: int) -> tuple[Any, ...] | None:
    """One player's season line. Reads player_season_stats_deduped, the view
    that has already collapsed a traded player's per-stint rows into one, so no
    caller has to remember to. `columns` comes from PLAYER_STAT_COLUMNS or
    HISTORY_COLUMNS - never from a slot."""
    return con.execute(
        f"SELECT {', '.join(columns)} FROM player_season_stats_deduped WHERE athlete_id = ? AND season = ? AND season_type = ?",
        [athlete_id, season, season_type],
    ).fetchone()


STAT_LINE = ("points", "rebounds", "assists")


# A comparison's default line is longer than a single player's, because the two
# answers are read differently. "How many points did Luka average" wants the
# number it asked for; "compare Luka and SGA" is asking which of them is
# better, and three counting stats cannot answer that - they leave out both
# halves of the defensive line and everything a player gives back. Prose is
# already refused here for the same reason (see _phrase_compare); a table costs
# nothing per extra row, so the rows a comparison actually turns on are all
# present by default.
#
# A NAMED stat still narrows to that one. Somebody asking "who scores more"
# gets scoring, not a wall.
COMPARE_STAT_LINE = ("points", "rebounds", "assists", "steals", "blocks", "turnovers", "fouls", "minutes")


def _wanted_stats(slots: dict[str, Any], default: tuple[str, ...] = STAT_LINE) -> list[str]:
    """The stats to report: the one named, or ``default`` if none was.

    A stat that was NAMED but is not supported must not fall back to the
    default line - that is how "what was Steph Curry's avg 3pt shot distance"
    came back as "26.6 points, 3.6 rebounds and 4.7 assists per game". Falling
    through to the agent is slow; answering a different question is worse."""
    stat = slots.get("stat")
    if stat is None or (isinstance(stat, str) and not stat.strip()):
        return list(default)
    if stat in PLAYER_STAT_COLUMNS:
        return [stat]
    raise TemplateUnsupported(f"no per-game column for stat {stat!r}")


def _rounded(value: Any) -> float | None:
    """A computed per-game figure to one decimal, as ESPN's stored ones are -
    "21.33 points" next to a stored "27.7" reads as a different unit."""
    return None if value is None else round(float(value), 1)


# The three shooting percentages, as (made column, attempted column, how the
# sentence says it, what the shots are called). The column names are the same
# in the season table and in the box scores, so every span reads them alike.
SHOOTING_STATS: dict[str, tuple[str, str, str, str]] = {
    "fieldGoalPct": ("fieldGoalsMade", "fieldGoalsAttempted", "from the field", "field goals"),
    "threePointFieldGoalPct": ("threePointFieldGoalsMade", "threePointFieldGoalsAttempted", "on 3-pointers", "3-pointers"),
    "freeThrowPct": ("freeThrowsMade", "freeThrowsAttempted", "on free throws", "free throws"),
}
"""Shooting percentages ``player_stat`` answers, always with the makes and
attempts behind them - computed from those, never read from a stored
percentage, so a season, a career and a set of games are all the same sum.

.. versionadded:: 2.1.0
"""


@dataclass(frozen=True)
class _AdvancedStat:
    """One of the stats computed from box scores rather than served by ESPN.

    ``column`` is its name in ``player_season_advanced_stats``; ``label`` is how
    the sentence says it; ``weight`` is the volume column a career is weighted
    by, with ``volume`` naming it in prose, and ``None`` means the stat has no
    career answer.
    """

    column: str
    label: str
    weight: str | None
    percentage: bool
    volume: str = ""


# These were RANKABLE and not LOOKUP-ABLE, which is one concept carrying two
# vocabularies. `leaderboard` has ranked true shooting, effective FG% and usage
# since 2.1.0, and the router emits their names correctly - "kevin durant true
# shooting percentage career" arrives with `stat="ts_pct"` - but `player_stat`
# knew only PLAYER_STAT_COLUMNS and refused it with "no per-game column for
# stat 'ts_pct'". A stat the system can rank is one it should be able to look
# up, and the gap was invisible because each half was correct on its own.
#
# A career is weighted by the stat's OWN denominator, never averaged across
# seasons - the discipline SHOOTING_STATS already states. TS% weighted by true
# shooting attempts is exact, because a season's ts_pct times its attempts IS
# that season's points over two; the same holds for eFG% over field-goal
# attempts. Usage and game score have no such denominator (usage is a rate per
# possession while on court, and weighting it by games is an approximation
# nobody asked for), so they answer for a season and refuse a career rather
# than quietly reporting a mean of means. That is the same line
# `metrics.LeaderboardMetric.career` draws for the ranking.
ADVANCED_STATS: dict[str, _AdvancedStat] = {
    "ts_pct": _AdvancedStat("ts_pct", "true shooting percentage", "true_shooting_attempts", percentage=True, volume="true-shooting attempts"),
    "efg_pct": _AdvancedStat("efg_pct", "effective field goal percentage", "field_goals_attempted", percentage=True, volume="field-goal attempts"),
    "usage_pct": _AdvancedStat("usage_pct", "usage rate", None, percentage=False),
    "game_score": _AdvancedStat("avg_game_score", "game score", None, percentage=False),
}
"""The computed advanced stats ``player_stat`` can look up, mapped to how each
one reads and how it adds up over a career.

.. versionadded:: 4.3.0
"""


# A career per-game figure is the career total over career games, never an
# average of season averages. Rebounds' total column is named differently from
# the per-stat key; minutes have none, and are weighted by games instead.
_CAREER_TOTALS: dict[str, str | None] = {
    "points": "points",
    "rebounds": "totalRebounds",
    "assists": "assists",
    "steals": "steals",
    "blocks": "blocks",
    "turnovers": "turnovers",
    "minutes": None,
    "fouls": "fouls",
    "threePointFieldGoalsMade": "threePointFieldGoalsMade",
    "fieldGoalsMade": "fieldGoalsMade",
    "freeThrowsMade": "freeThrowsMade",
}


def player_stat(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """One named player's numbers: a season line, a career, or the games a
    question narrowed to.

    - One season comes from player_season_stats_deduped, so a traded player's
      multi-row season is already collapsed.
    - A career (``span``) is summed from the same table - totals over games,
      never an average of averages - which reaches back to 1977 because it is
      fetched per player over a whole career.
    - An ``opponent``, ``venue`` or ``without`` narrows the games, which only
      box scores can do, so those are summed over player_game_log from 1994.
    - A shooting percentage comes with the makes and attempts behind it.

    An incomplete name ("Luka", "Curry") is answered with a question, not a
    guess: falling through costs minutes and guesses anyway, and a prominence
    tiebreak was measured and rejected (no threshold separates Luka Doncic from
    Luka Garza without also wrongly resolving "Brown").

    .. versionchanged:: 2.1.0
       Honors ``opponent``, ``venue``, ``without`` and ``span``, answers
       shooting percentages, and refuses a ``limit`` - a player's numbers over
       his last N games is a game log, which averages the games it lists.

    .. versionchanged:: 2.2.0
       ``without`` takes every teammate the question names and counts a game
       only where none of them played.

    .. versionchanged:: 4.0.1
       A game narrowed by ``opponent``, ``venue`` or ``without`` now reads a
       line rebuilt from play-by-play in place of an ESPN box score served
       empty, for a stat the rebuild gets right - "Anthony Davis points vs the
       Lakers in 2015" no longer refuses a season that is entirely Pelicans
       games ESPN zeroed. A span left with no games at all says whose box
       scores are empty rather than that no games were found.

    .. versionchanged:: 4.1.0
       A season that was never named - the slot defaulted to "now" rather than
       being asked for - now redirects to the seasons the player actually has
       on record when the current one has nothing, instead of a refusal that
       reads as though his whole career were missing. "Allen Iverson's points"
       used to answer "no 2026 regular season numbers", true and about the
       wrong year; it now also says he last appears in 2010 and names his
       1997-2010 range. A season the question named outright keeps the plain
       refusal, because it is the correct answer.
    """
    con = ctx.con
    # Settled before the name is resolved: the span, and the table it is read
    # from, are what narrow an ambiguous name to the players who could be the
    # answer - a career keeps Dell Curry, this season does not.
    season_type = slots.get("season_type") or 2
    opponent, venue, without = slots.get("opponent"), slots.get("venue"), slots.get("without")
    # A named half of the starter/bench split narrows the GAMES, so it reads
    # box scores like the other three - the season line has no such column.
    split_side = slots.get("split") if slots.get("split") in STARTER_SIDES else None
    from_box_scores = bool(opponent or venue or without or split_side)
    span = _span_of(slots.get("span"), slots.get("season"), season_type, "player_game_log" if from_box_scores else "player_season_stats_deduped")
    lines = _GAME_LOGS if from_box_scores else _SEASON_LINES
    player = _resolved_player(con, slots.get("player"), "player_stat needs a player name", available=lines, season=span.season, through=_career_end(span.season))
    if isinstance(player, TemplateResult):
        return player
    if slots.get("limit"):
        # "Jokic averages last 10 games" answered with his season line would be
        # the substitution this module exists to stop; game_log averages
        # exactly the games it lists.
        raise TemplateUnsupported("a player's numbers over a limited set of games is a game_log question")

    stat = slots.get("stat")
    # Before the ESPN-served columns, because these carry their own table, their
    # own floor and their own career arithmetic - and because _wanted_stats
    # would otherwise refuse them as unknown, which is how "kevin durant true
    # shooting percentage career" fell through while the leaderboard ranked the
    # same stat happily.
    if isinstance(stat, str) and stat in ADVANCED_STATS:
        return _player_stat_advanced(con, player, span, stat, from_box_scores)

    shooting = SHOOTING_STATS.get(stat) if isinstance(stat, str) else None
    wanted = [] if shooting else _wanted_stats(slots)

    if from_box_scores:
        narrowed = _narrow_player_games(con, player, span, opponent=opponent, venue=venue, without=without, split=split_side)
        if isinstance(narrowed, TemplateResult):
            return narrowed
        return _box_score_player_stat(con, player, span, narrowed, wanted, shooting)
    if span.career:
        return _career_player_stat(con, player, span, wanted, shooting)

    return _season_player_stat(con, player, span, season_type, wanted, shooting)


def _season_player_stat(con: duckdb.DuckDBPyConnection, player: Entity, span: _Span, season_type: int, wanted: list[str], shooting: tuple[str, str, str, str] | None) -> TemplateResult:
    """One season's line, read from the deduplicated season table.

    Split out of ``player_stat`` so that function stays inside the complexity
    gate; the steps are in the order they were, and each keeps its comment.
    """
    season = span.season or current_season()
    columns = ["gamesPlayed"]
    for name in wanted:
        per_game, total, _ = PLAYER_STAT_COLUMNS[name]
        columns.append(per_game)
        if total:
            columns.append(total)
    if shooting:
        columns += [shooting[0], shooting[1]]
    row = _season_row(con, player.id, columns, season, season_type)

    period = _period(season, season_type)
    if row is None:
        answer = f"{player.name} has no {period} numbers in the warehouse."
        if span.defaulted:
            # The season was defaulted to "now", not asked for - a retired
            # player's "now" is empty and the refusal above, read alone, sounds
            # like his whole career is missing (issue #18). Redirect to what
            # the warehouse actually holds for him rather than guessing a
            # season or falling back to a career summary that can badly
            # misrepresent him (Iverson's last season is 13.8 ppg against a
            # 26.7 career average). A season the question named outright keeps
            # this refusal plain, because it is the correct answer.
            redirect = _season_redirect(con, player.id, season_type, "player_season_stats_deduped")
            answer += _defaulted_season_note(redirect, SEASON_TYPE_NAMES.get(season_type, "regular season"))
        return TemplateResult(
            data={"player": player.name, "season": season, "stats": {}},
            answer=answer,
        )
    values = dict(zip(columns, row, strict=True))
    if shooting:
        return _shooting_result(player.name, {"season": season}, values, shooting, when=f"in the {period}")
    return TemplateResult(
        data={"player": player.name, "season": season, "stats": values},
        answer=_phrase_player_stat(player.name, period, values, wanted),
    )


def _career_player_stat(con: duckdb.DuckDBPyConnection, player: Entity, span: _Span, wanted: list[str], shooting: tuple[str, str, str, str] | None) -> TemplateResult:
    """A career line summed from the season table. A season whose total is
    missing falls back to its average times its games, and each per-game figure
    is divided by the games that actually carry that stat."""
    selects = ["SUM(gamesPlayed)", "MIN(season)", "MAX(season)", "COUNT(*)"]
    for stat in wanted:
        per_game = PLAYER_STAT_COLUMNS[stat][0]
        total = _CAREER_TOTALS[stat]
        amount = f"COALESCE({total}, {per_game} * gamesPlayed)" if total else f"{per_game} * gamesPlayed"
        selects += [f"SUM({amount}) / SUM(CASE WHEN {amount} IS NOT NULL THEN gamesPlayed END)", f"SUM({amount})"]
    if shooting:
        made, attempted = shooting[0], shooting[1]
        selects += [f"SUM(CASE WHEN {attempted} IS NOT NULL THEN {made} END)", f"SUM({attempted})"]
    row = con.execute(
        f"SELECT {', '.join(selects)} FROM player_season_stats_deduped WHERE athlete_id = ? AND season_type = ? AND gamesPlayed > 0",
        [player.id, span.season_type],
    ).fetchone()
    if row is None or not row[0]:
        return TemplateResult(data={"player": player.name, "span": "career", "stats": {}}, answer=f"{player.name} has no {span.kind} numbers in the warehouse.")
    games, first, last, seasons = row[:4]
    plural = "" if seasons == 1 else "s"
    when = f"over his career ({seasons} {span.kind}{plural}, {first}-{last})" if first != last else f"over his career (the {first} {span.kind})"
    scope = {"span": "career", "seasons": [first, last], "season_count": seasons}
    values: dict[str, Any] = {"gamesPlayed": int(games)}
    if shooting:
        values[shooting[0]], values[shooting[1]] = row[4], row[5]
        return _shooting_result(player.name, scope, values, shooting, when=when)
    for index, stat in enumerate(wanted):
        per_game_col, total_col, _ = PLAYER_STAT_COLUMNS[stat]
        values[per_game_col] = _rounded(row[4 + 2 * index])
        amount = row[5 + 2 * index]
        if total_col and amount is not None:
            values[total_col] = round(amount)
    return TemplateResult(
        data={"player": player.name, **scope, "stats": values},
        answer=_phrase_player_stat(player.name, f"career {span.kind}s", values, wanted, when=when),
    )


def _player_stat_advanced_value(spec: _AdvancedStat, value: Any) -> str:
    """One advanced figure as the sentence prints it.

    A percentage is printed as a three-decimal fraction (``.622``), the way a
    shooting line reads, because these are stored as 0-1 fractions; usage is
    already a 0-100 rate and game score is a raw composite, so both take one
    decimal."""
    if spec.percentage:
        return f"{float(value):.3f}".lstrip("0")
    return f"{float(value):.1f}"


def _player_stat_advanced_gap(missing: int, spec: _AdvancedStat) -> str:
    """What a career figure says about the seasons it could not see.

    Silence here would be the failure this project keeps producing: a precise
    number over a span it does not actually cover, printed as fluently as a
    complete one. The seasons are not scattered games - ESPN serves whole
    team-seasons of empty box scores from 2013 to 2018 (``DATA.md``), and a
    player who spent them on Chicago or New Orleans has nothing at all for
    those years."""
    if not missing:
        return ""
    subject = "season in that span is" if missing == 1 else "seasons in that span are"
    them = "it" if missing == 1 else "them"
    return f" {missing} {subject} not counted: ESPN's box scores for {them} are empty, so no {spec.label} can be computed from {them}."


def _player_stat_advanced(con: duckdb.DuckDBPyConnection, player: Entity, span: _Span, stat: str, from_box_scores: bool) -> TemplateResult:
    """One player's computed advanced stat, for a season or a career.

    Read from ``player_season_advanced_stats``, which starts in 1994 and has
    1993 as a phantom copy of it - a shorter reach than the season line this
    template normally uses, so the span is rebuilt against that table rather
    than inherited, and a career excludes the phantom by name.
    """
    spec = ADVANCED_STATS[stat]
    if from_box_scores:
        # The per-game figures exist (`player_advanced_stats`, and the same
        # columns on `player_game_log`), so this is a gap rather than an
        # impossibility - but narrowing them to an opponent, a venue or a
        # teammate's absence is the machinery in _narrow_player_games, and
        # answering a season line to a question that narrowed the games is the
        # substitution this module exists to stop. Refused with its own cause.
        raise TemplateUnsupported(f"{spec.label} over a narrowed set of games is not supported yet - it is computed per season")
    if span.career and spec.weight is None:
        raise TemplateUnsupported(f"{spec.label} has no career figure - it has no volume column to weight the seasons by, so a career would be a mean of means")
    span = _span_of("career" if span.career else None, span.season, span.season_type, "player_season_advanced_stats") if span.career else span
    where, params = span.clause("season")
    if span.career:
        assert spec.weight is not None
        # Every figure but the last is filtered to the seasons that actually
        # carry the stat, and the last counts the ones that do not. A season
        # ESPN served empty has a row here with 0 attempts and a NULL rate
        # (`player_season_advanced_stats` is summed from the STORED box scores,
        # and 2013-2018 has whole team-seasons of empty ones) - so an
        # unfiltered SUM(games_played) counts games the rate never saw, and an
        # unfiltered MIN/MAX names a span the answer does not cover. Jimmy
        # Butler is the worked example: 2013, 2014, 2015 and 2017 are empty,
        # and a career "2012-2026" over 824 games was really 12 seasons over
        # 534. See `_player_stat_advanced_gap`.
        have = f"{spec.column} IS NOT NULL"
        selects = (
            f"SUM({spec.column} * {spec.weight}) / NULLIF(SUM({spec.weight}), 0), SUM({spec.weight}), "
            f"SUM(games_played) FILTER (WHERE {have}), MIN(season) FILTER (WHERE {have}), MAX(season) FILTER (WHERE {have}), "
            f"COUNT(*) FILTER (WHERE {have}), COUNT(*) FILTER (WHERE NOT {have})"
        )
    else:
        selects = f"{spec.column}, NULL, games_played, season, season, 1, 0"
    row = con.execute(
        f"SELECT {selects} FROM player_season_advanced_stats WHERE athlete_id = ? AND season_type = ? AND {where}",
        [player.id, span.season_type, *params],
    ).fetchone()

    when = span.during(row[3], row[4]) if row and row[0] is not None else span.during()
    if row is None or row[0] is None:
        return TemplateResult(
            data={"player": player.name, "stat": stat, "stats": {}},
            answer=f"{player.name} has no {spec.label} on record {when} - it is computed from box scores, which start in 1994.",
        )
    value, volume, games, first, last, seasons, missing = row
    printed = _player_stat_advanced_value(spec, value)
    # The volume goes in the sentence for the same reason a shooting line
    # carries its attempts: a rate without it is the thing people ask "out of
    # how many?" about.
    behind = f" on {int(volume):,} {spec.volume}" if volume is not None and spec.volume else ""
    scope: dict[str, Any] = {"span": "career", "seasons": [first, last], "season_count": seasons} if span.career else {"season": first}
    sentence = f"{player.name} has a {printed} {spec.label} {when}{behind}, in {int(games):,} games." if games else f"{player.name} has a {printed} {spec.label} {when}{behind}."
    return TemplateResult(
        data={"player": player.name, "stat": stat, **scope, "stats": {spec.column: value, "games_played": int(games) if games is not None else None}, "seasons_missing": int(missing)},
        answer=sentence + _player_stat_advanced_gap(int(missing), spec),
    )


def _box_score_stat_rebuilt(con: duckdb.DuckDBPyConnection, wanted: list[str], shooting: tuple[str, str, str, str] | None) -> bool:
    """Whether ``_box_score_player_stat`` may read a line rebuilt from
    play-by-play for a game ESPN served with an empty box score.

    Every stat asked for has to be one a rebuild gets right (:data:`REBUILT_STATS`)
    - never a shooting percentage, whose makes and, especially, attempts are
    outside what the rebuild was measured for (see ``UNGATED_ON_REBUILD`` in
    :mod:`association.query.conditions`). Without this, "Anthony Davis points vs the
    Lakers in 2015" read only the stored table, which is empty for every
    Pelicans game that season, and answered the wrong-cause refusal this
    project keeps producing even though points is exactly the stat the rebuild
    is trusted for.

    .. versionadded:: 4.0.1
    """
    return bool(wanted) and shooting is None and all(stat in REBUILT_STATS for stat in wanted) and _log_carries_rebuilt(con)


def _box_score_player_stat(con: duckdb.DuckDBPyConnection, player: Entity, span: _Span, narrowed: _Narrowed, wanted: list[str], shooting: tuple[str, str, str, str] | None) -> TemplateResult:
    """Averages over exactly the games a question narrowed to. The box-score
    column for each stat is the stat's own name (``points``, ``fouls``...), so
    PLAYER_STAT_COLUMNS' keys reach SQL here, never the slot text itself.

    .. versionchanged:: 4.0.1
       Reads a line rebuilt from play-by-play in place of an empty ESPN box
       score, for the stats a rebuild gets right (see
       :func:`_box_score_stat_rebuilt`) - previously this narrowed reading
       (an ``opponent``, a ``venue`` or ``without``) never did, even for
       points. A span left with no games at all now says so - "his games have
       an empty box score" - rather than the wrong-cause "no games found".
    """
    rebuilt = _box_score_stat_rebuilt(con, wanted, shooting)
    selects = ["COUNT(*)", "MIN(pgl.season)", "MAX(pgl.season)"]
    for stat in wanted:
        selects += [f"AVG(pgl.{stat})", f"SUM(pgl.{stat})"]
    if shooting:
        selects += [f"SUM(pgl.{shooting[0]})", f"SUM(pgl.{shooting[1]})"]
    if rebuilt:
        selects.append("SUM(CASE WHEN pgl.reconstructed THEN 1 ELSE 0 END)")
    sql, params = aggregate_sql(narrowed, selects, rebuilt=rebuilt)
    row = con.execute(sql, params).fetchone()
    filters = narrowed.filters()
    scope: dict[str, Any] = {
        "season": span.season,
        "span": "career" if span.career else None,
        "opponent": narrowed.opponent.name if narrowed.opponent else None,
        "venue": narrowed.venue,
        "without": [mate.name for mate in narrowed.without],
        "started": narrowed.started,
    }
    if row is None or not row[0]:
        message = _no_narrowed_games(con, player, span, narrowed, rebuilt=rebuilt)
        return TemplateResult(data={"player": player.name, **scope, "games": 0, "stats": {}}, answer=message)
    games, first, last = row[:3]
    rebuilt_shown = int(row[-1]) if rebuilt and row[-1] is not None else 0
    when = span.during(first, last)
    notes = _box_score_notes(con, player, span, narrowed, rebuilt=rebuilt, rebuilt_shown=rebuilt_shown)
    values: dict[str, Any] = {"gamesPlayed": int(games)}
    if shooting:
        values[shooting[0]], values[shooting[1]] = row[3], row[4]
        result = _shooting_result(player.name, {**scope, "seasons": [first, last]}, values, shooting, when=when, games_note=filters)
    else:
        for index, stat in enumerate(wanted):
            per_game_col, total_col, _ = PLAYER_STAT_COLUMNS[stat]
            values[per_game_col] = _rounded(row[3 + 2 * index])
            if total_col and row[4 + 2 * index] is not None:
                values[total_col] = int(row[4 + 2 * index])
        answer = _phrase_player_stat(player.name, _period(first, span.season_type), values, wanted, games_note=filters, when=when)
        result = TemplateResult(data={"player": player.name, **scope, "seasons": [first, last], "stats": values}, answer=answer)
    if notes:
        result.answer = " ".join([result.answer, *notes])
    return result


def _shooting_result(name: str, scope: dict[str, Any], values: dict[str, Any], shooting: tuple[str, str, str, str], *, when: str, games_note: str = "") -> TemplateResult:
    """A percentage with the makes and attempts behind it - "out of how many?"
    is the first thing anybody asks of a percentage without them."""
    made_col, attempted_col, how, noun = shooting
    made, attempted, games = values.get(made_col), values.get(attempted_col), values.get("gamesPlayed")
    played = f" in {_count_games(games)}{games_note}" if games else games_note
    if attempted is None or made is None:
        answer = f"{name} has no {noun} on record{played} {when}."
        pct = None
    elif not attempted:
        answer = f"{name} attempted no {noun}{played} {when}."
        pct = None
    else:
        pct = 100.0 * made / attempted
        answer = f"{name} shot {pct:.1f}% {how} ({made:,} of {attempted:,}){played} {when}."
    return TemplateResult(data={"player": name, **scope, "stats": {**values, "pct": pct}}, answer=answer)


def _phrase_player_stat(name: str, period: str, values: dict[str, Any], wanted: list[str], *, games_note: str = "", when: str | None = None) -> str:
    games = values.get("gamesPlayed")
    parts = []
    for stat in wanted:
        per_game_col, _, label = PLAYER_STAT_COLUMNS[stat]
        per_game = values.get(per_game_col)
        if per_game is not None:
            parts.append(f"{_format_value(per_game)} {label}")
    if not parts:
        return f"{name} has no {period} numbers in the warehouse."
    body = ", ".join(parts[:-1]) + f" and {parts[-1]}" if len(parts) > 1 else parts[0]
    played = f" in {_count_games(games)}{games_note}" if games else games_note
    sentence = f"{name} averaged {body} per game{played} {when or f'in the {period}'}."
    # The season total goes in its own clause rather than inline, and only when
    # a single stat was asked for - inline it read as "33.5 points (2143 total)
    # per game", which says something false.
    if len(wanted) == 1:
        total_col = PLAYER_STAT_COLUMNS[wanted[0]][1]
        total = values.get(total_col) if total_col else None
        if total is not None:
            sentence += f" That is {total:,} in total."
    return sentence


DEFAULT_SINGLE_GAME_LIMIT = 3


def single_game_high(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """ "Most assists in a single game" - a per-game MAXIMUM, not a season
    ranking.

    With no such intent the router picked the nearest shape it had: "most
    assists in a single game" was answered with a season average, in 1.76s, off
    by 13. A missing shape does not produce a refusal - it produces a confident
    answer to a different question, so the fix is a template, not prompt
    wording.

    .. versionchanged:: 2.1.0
       Honors ``span`` "career": a named player's career high, or the league's
       best since 1993-94, each saying what it covers. A game's date is the
       Eastern calendar day it was played; it used to be the UTC day it is
       stored under, a day late for every game tipping after 7pm Eastern.

    .. versionchanged:: 4.1.0
       A defaulted (unnamed) season with no games for a named player now
       redirects to the seasons he does have on record, when there are any,
       rather than a refusal that reads as though he never played - "Allen
       Iverson's highest point total" used to answer "no 2026 regular season
       games", true of the wrong year. A season the question named outright
       is unaffected.
    """
    stat = slots.get("stat")
    column = THRESHOLD_STAT_COLUMNS.get(stat) if isinstance(stat, str) else None
    if column is None:
        raise TemplateUnsupported(f"single_game_high needs a known stat, got {stat!r}")

    career = _career_span("single_game_high", slots.get("span"), slots.get("season"))
    # Whether the season came from the question or from "now" - the same
    # distinction _span_of.defaulted makes for the templates built on it. This
    # one is not, so it is read straight from the raw slot.
    defaulted = not career and not (isinstance(slots.get("season"), int) and slots.get("season"))
    season = None if career else (slots.get("season") or current_season())
    season_type = slots.get("season_type") or 2
    limit = _clamp_limit(slots.get("limit"), default=DEFAULT_SINGLE_GAME_LIMIT)

    scoped = _single_game_high_scope(ctx, column, season, season_type, slots.get("player"))
    if isinstance(scoped, TemplateResult):
        return scoped
    narrowed, named_player, from_rebuilt = scoped

    sql, params = rows_sql(
        narrowed,
        f"pgl.player_name, pgl.{column}, pgl.game_date, pgl.opponent_abbr, {'pgl.reconstructed' if from_rebuilt else 'FALSE'}",
        order=f"pgl.{column} DESC, pgl.game_date",
        limit=limit,
        rebuilt=from_rebuilt,
    )
    rows = ctx.con.execute(sql, params).fetchall()

    label = STAT_LABELS.get(stat or "", stat or "")
    span = _game_span(ctx.con, season, season_type, named_player)
    games = [{"player": r[0], "value": r[1], "date": _eastern_date(r[2]), "opponent": r[3], "reconstructed": bool(r[4])} for r in rows]
    # `question_shape` names the scope in the same form leaderboard and
    # threshold_count use it: a caption for a caller that renders the rows
    # itself and would otherwise have no way to say what season they are from
    # except by reusing the whole sentence, which already lists them.
    shape = f"most {label}s in a single game" + (f", {named_player.name}" if named_player else "") + f", {span.caption}"
    # Counted BEFORE the sentence is built, because when the guard above has
    # left nothing it is the difference between "he has no games" and "his
    # games have no box score" - which are different facts.
    # covered_by_rebuild: when the answer read rebuilt lines, the games it could
    # not see are only those the rebuild could not reach either. Counting the
    # rest would disclaim the very games the number came from.
    empty = _empty_box_scores(ctx.con, season, season_type, named_player.id if named_player else None, covered_by_rebuild=from_rebuilt)
    who = named_player.name if named_player else None
    # Only when the answer is empty AND the stat was deliberately withheld: a
    # refusal that names a decision beats one that implies missing data.
    withheld = 0 if games or column in REBUILT_STATS else _rebuilt_in_scope(ctx.con, season, season_type, named_player.id if named_player else None)
    answer = _single_game_high_answer(games, label, span, who, empty=empty, withheld=withheld)
    answer += _single_game_high_redirect(ctx.con, defaulted, named_player, games, empty, withheld, season_type)
    return TemplateResult(
        data={"question_shape": shape, "season": season, "span": "career" if career else None, "stat": stat, "games": games, "empty_box_scores": empty[0]},
        answer=answer,
    )


def _single_game_high_scope(ctx: TemplateContext, column: str, season: int | None, season_type: int, player_text: Any) -> tuple[Narrowed, Entity | None, bool] | TemplateResult:
    """The relation read for the qualifying rows, whether a rebuilt
    (play-by-play) line may stand in for a missing box score line, and the
    named player if the question asked about one - unset means "the league".

    The played guard is the relation's, and it matters here more than
    anywhere: a line with no minutes is a game with NO BOX SCORE, not a game
    he played and did nothing in. Those lines carry 0 rather than NULL, and
    where a whole team-season is empty (every Chicago and New Orleans season
    from 2013 to 2018) a zero then wins the maximum outright - "Anthony
    Davis's highest point total in a single game in the 2015 regular season
    was 0, on 2014-10-28 vs ORL" - fluent, dated, and false. A rebuilt line
    may answer, but only for a stat a rebuild gets right (REBUILT_STATS) and
    only where the warehouse carries the flag.
    """
    span = _span_of("career" if season is None else None, season, season_type, "player_game_log")
    season_clause, season_params = span.clause("pgl.season")
    narrowed = league(season_clause, season_params, season_type)
    from_rebuilt = column in REBUILT_STATS and _log_carries_rebuilt(ctx.con)
    narrowed.narrow(f"pgl.{column} IS NOT NULL")
    named_player: Entity | None = None
    # The player slot is optional here: unset means "the league".
    if isinstance(player_text, str) and player_text.strip():
        resolved = _resolved_player(ctx.con, player_text, available=_GAME_LOGS, season=season, through=_career_end(season))
        if isinstance(resolved, TemplateResult):
            return resolved
        named_player = resolved
        narrowed.narrow("pgl.athlete_id = ?", resolved.id)
    return narrowed, named_player, from_rebuilt


def _single_game_high_redirect(
    con: duckdb.DuckDBPyConnection, defaulted: bool, named_player: Entity | None, games: list[dict[str, Any]], empty: tuple[int, int | None, int | None], withheld: int, season_type: int
) -> str:
    """What ``single_game_high`` appends when its season was defaulted rather
    than named and the answer came back with nothing to show (issue #18) -
    see :func:`association.query.templates.common._season_redirect`.

    Empty whenever the empty answer is about something else: a season the
    question named outright, no named player to redirect (a league-wide
    question has no "his" to point at), an empty-box-scores gap, or a
    withheld stat - each of those already has its own sentence, and this one
    would either duplicate it or, worse, answer over it.
    """
    if not defaulted or named_player is None or games or empty[0] or withheld:
        return ""
    redirect = _season_redirect(con, named_player.id, season_type, "player_game_log")
    return _defaulted_season_note(redirect, SEASON_TYPE_NAMES.get(season_type, "regular season"))


def _single_game_high_answer(games: list[dict[str, Any]], label: str, span: _GameSpan, who: str | None, *, empty: tuple[int, int | None, int | None], withheld: int) -> str:
    """The full sentence: the phrase, any league-coverage caveat, the
    empty-box-scores note (suppressed when ``withheld`` already explains the
    gap), and the rebuilt-line caveat when the answer itself rests on one."""
    answer = span.preface + _phrase_single_game_high(games, label, span, who, empty=empty, withheld=withheld)
    if span.league_note:
        answer += f" Box scores begin in {span.since}, so this is not an all-time record: earlier games are not in this warehouse."
    # Suppressed when `withheld` fired: that sentence already gave the count and
    # the reason, and repeating it as "empty box scores, so there is no per-game
    # high" contradicts it - the lines are there, they were held back.
    if not withheld:
        answer += _empty_note(empty, who, "a bigger game may be missing" if games else "there is no per-game high to read from them")
    # Said whenever the ANSWER rests on a rebuilt line, not whenever one was
    # read: a rebuilt game that lost to a fetched one changes nothing a reader
    # needs to know about the number they were given.
    if games and games[0]["reconstructed"]:
        answer += " That game has no box score from ESPN - the figure is rebuilt from its play-by-play, so treat it as close rather than exact."
    return answer


def _phrase_single_game_high(
    games: list[dict[str, Any]], label: str, span: _GameSpan, named_player: str | None, *, empty: tuple[int, int | None, int | None] = (0, None, None), withheld: int = 0
) -> str:
    if not games:
        who = f"{named_player} has" if named_player else "There are"
        # A stat outside REBUILT_STATS with rebuilt lines in scope is a DECISION,
        # not a gap, and the refusal has to say which. "No games with a box
        # score" is true of the fetched lines and hides that the data exists and
        # was withheld because it is not accurate enough to quote.
        if withheld:
            return (
                f"{who} no {span.games} with a box score in the warehouse. {withheld:,} of them were rebuilt from play-by-play, "
                f"but a {label} is not read from a rebuilt line: rebuilt fouls are wrong in about one game in six, and turnovers "
                f"in one in thirteen, against one in sixty for points."
            )
        # "No games" and "no games WITH A BOX SCORE" are different claims, and
        # the first said of a player who played 68 of them is the wrong-cause
        # refusal this project keeps producing: true-sounding, and it sends the
        # reader to look for a missing season rather than a missing box score.
        # _empty_note then names the count and the years.
        if empty[0]:
            return f"{who} no {span.games} with a box score in the warehouse."
        return f"{who} no {span.games} in the warehouse."
    top = games[0]
    where = f" vs {top['opponent']}" if top["opponent"] else ""
    if named_player:
        return f"{named_player}'s highest {label} total in a single game {span.when} was {top['value']}, on {top['date']}{where}."

    tied = [g for g in games if g["value"] == top["value"]]
    if len(tied) > 1:
        names = ", ".join(g["player"] for g in tied[:-1]) + f" and {tied[-1]['player']}"
        sentence = f"{names} tied for the most {label}s in a single game {span.when}, with {top['value']} each."
    else:
        sentence = f"{top['player']} had the most {label}s in a single game {span.when}: {top['value']}, on {top['date']}{where}."
    rest = [f"{g['player']} ({g['value']})" for g in games if g["value"] != top["value"]]
    return sentence + (f" Next: {', '.join(rest)}." if rest else "")


MAX_COMPARED_PLAYERS = 4


def player_compare(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """Two or more named players' season numbers side by side.

    The agent wrote correct SQL but expanded "SGA" to '%Scottie G. Allen%' and
    compared Luka Doncic to Luka Garza. Nickname resolution is a lookup, not
    something to hope a 7B model knows - see entities.PLAYER_NICKNAMES."""
    con = ctx.con
    names = slots.get("players")
    if not isinstance(names, list) or len({n for n in names if isinstance(n, str) and n.strip()}) < 2:
        raise TemplateUnsupported("player_compare needs at least two distinct player names")

    season = slots.get("season") or current_season()
    resolved: list[Entity] = []
    for name in names[:MAX_COMPARED_PLAYERS]:
        player = _resolved_player(con, name, available=_SEASON_LINES, season=season)
        if isinstance(player, TemplateResult):
            return player
        if player.id not in {p.id for p in resolved}:
            resolved.append(player)
    if len(resolved) < 2:
        raise TemplateUnsupported("the named players resolved to the same person")

    season_type = slots.get("season_type") or 2
    wanted = _wanted_stats(slots, COMPARE_STAT_LINE)
    columns = ["gamesPlayed"] + [PLAYER_STAT_COLUMNS[name][0] for name in wanted]

    rows: dict[str, dict[str, Any]] = {}
    for player in resolved:
        row = _season_row(con, player.id, columns, season, season_type)
        rows[player.name] = dict(zip(columns, row, strict=True)) if row else {}

    net = _compare_netpoints(con, resolved, season, season_type)
    period = _period(season, season_type)
    return TemplateResult(
        data={"season": season, "players": rows, "netpoints": net},
        answer=_phrase_compare(rows, wanted, period, net),
    )


# The NetPoints summary rows, in the order player_netpoints reports them:
# label -> the net_points_player column it reads. Per 100 possessions, not
# season totals, because a comparison is exactly the question totals answer
# badly - they mostly rank by playing time. The same choice fingerprint.py
# makes, and for the same reason.
NETPOINTS_COMPARE_ROWS: tuple[tuple[str, str], ...] = (
    ("net pts/100", "overall_per_100_poss"),
    ("  offense", "offense_per_100_poss"),
    ("  defense", "defense_per_100_poss"),
)


def _compare_netpoints(con: duckdb.DuckDBPyConnection, players: list[Entity], season: int, season_type: int) -> dict[str, dict[str, Any]]:
    """Each player's NetPoints summary, by resolved name. Missing is normal.

    NetPoints starts in 2019 and is a separate opt-in fetch, so this is
    supplementary rather than required: a player with no row contributes an
    empty dict and a season with no rows at all drops the section. That is also
    why `net_points_player` is deliberately NOT in this template's
    TEMPLATE_SOURCES entry - listing it would put a 2019 coverage floor on
    every comparison and refuse the 1994-2018 ones outright.
    """
    # net_points_player uses its OWN string season_type; filtering it with the
    # numeric one every other table uses silently matches nothing.
    label = SEASON_TYPE_LABELS.get(season_type, "Regular Season")
    selected = ", ".join(column for _, column in NETPOINTS_COMPARE_ROWS)
    found: dict[str, dict[str, Any]] = {}
    for player in players:
        try:
            row = con.execute(
                f"SELECT {selected} FROM net_points_player WHERE athlete_id = ? AND season = ? AND net_points_season_type = ?",
                [player.id, season, label],
            ).fetchone()
        except duckdb.Error:
            # The table only exists if the NetPoints fetch was run. Unlike
            # _single_game_netpoints, which has nothing else to say, a
            # comparison is complete without it - so this drops the section
            # rather than failing the answer.
            return {}
        found[player.name] = dict(zip([column for _, column in NETPOINTS_COMPARE_ROWS], row, strict=True)) if row else {}
    return found


def _phrase_compare(rows: dict[str, dict[str, Any]], wanted: list[str], period: str, netpoints: dict[str, dict[str, Any]] | None = None) -> str:
    """A fixed-width table rather than prose. Comparisons are the one shape
    where a sentence actively hurts - the agent's prose version stated that a
    player with 0.4 steals led one with 1.6."""
    names = list(rows)
    missing = [name for name, values in rows.items() if not values]
    # A fixed decimal in every cell, not _format_value: in an aligned column a
    # trailing-zero-stripped "25" next to "27.7" reads as a different unit.
    entries: list[tuple[str, list[str]]] = [("games", [_table_cell(rows[name].get("gamesPlayed")) for name in names])]
    for stat in wanted:
        column, _, label = PLAYER_STAT_COLUMNS[stat]
        entries.append((label, [_table_cell(rows[name].get(column)) for name in names]))

    net = netpoints or {}
    # Shown only when somebody has a row: an empty NetPoints block under a
    # comparison of two 1990s players would read as "both contributed nothing"
    # rather than "this season predates the data".
    if any(net.get(name) for name in names):
        entries.append(("", ["" for _ in names]))
        for label, column in NETPOINTS_COMPARE_ROWS:
            entries.append((label, [_signed_cell(net.get(name, {}).get(column)) for name in names]))

    label_width = max(len(label) for label, _ in entries)
    name_width = max(len(text) for text in (*names, *(cell for _, cells in entries for cell in cells)))
    # rstripped so the blank separator row is an empty line rather than a line
    # of spaces, which shows up as trailing whitespace wherever this is stored.
    lines = [f"{' vs '.join(names)}, {period}:", (f"{' ' * label_width}  " + "  ".join(name.rjust(name_width) for name in names)).rstrip()]
    lines += [(f"{label.ljust(label_width)}  " + "  ".join(cell.rjust(name_width) for cell in cells)).rstrip() for label, cells in entries]
    if missing:
        lines.append(f"({', '.join(missing)} has no {period} numbers in the warehouse.)")
    return "\n".join(lines)
