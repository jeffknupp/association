"""Player rankings and lines: threshold counts, leaderboards, a player's stats, comparisons, multi-season history and single-game highs.

.. versionadded:: 3.0.0
   Split out of the former ``association.query.templates`` module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import duckdb

from association.nba.coverage import POSTSEASON
from association.nba.franchises import season_name_sql
from association.nba.season import current_season
from association.query.measures import STAT_LINE as STAT_LINE
from association.query.reading import Scope, scope_reads_box_scores

from ..conditions import box_source
from ..entities import Availability, Entity
from ..leaderboard import SEASON_TOTAL_OF, LeaderboardError, LeaderboardResult, not_a_postseason_copy, resolve_metric, run_career_leaderboard, run_leaderboard
from ..metrics import EXTRA_FIELD_COLUMNS, LEADERBOARD_METRICS, SEASON_TYPE_LABELS
from ..notes import decided, note
from ..player_games import scope_without_guard, season_type_clause
from .common import (
    HISTORY_COLUMNS,
    PLAYER_STAT_COLUMNS,
    SEASON_TYPE_NAMES,
    TemplateResult,
    TemplateUnsupported,
    _clamp_limit,
    _defaulted_season_note,
    _format_value,
    _period,
    _resolved_player,
    _Span,
    _span_of,
    _table_cell,
    scoped_player,
    season_redirect,
)

DEFAULT_LEADERBOARD_LIMIT = 10


# Where each player template's answer is read from, for narrowing an ambiguous
# name to the candidates with a row there. The tables TEMPLATE_SOURCES
# declares, or the per-game table a one-game answer reads instead - so a
# candidate these eliminate is one whose answer would have been empty.
_SEASON_LINES = Availability("player_season_stats_deduped")


def _career_span(intent: str, span: Literal["career"] | None, season: int | None) -> bool:
    """True for a career question, False for a one-season one; raises for a
    career with a year named.

    A career with a year named is refused rather than read. The router keeps a
    year the question named alongside "career", so "most points ever in a game
    in 2024" (that season), "career leaders since 2015" (a range) and "career
    points through 2010" (a cutoff) all arrive as the same two slots. Answering
    any of them as one of the others is the substitution this module exists to
    prevent.

    Not `_span_of` (`common.py`, step 3/C1) for the refusal's wording ("cannot
    tell whether a career span ... means" vs. "a career span and the ... season
    at once") and one edge: this refuses `season == 0`, where `_span_of`'s
    `and season` check would not. A span other than a career no longer
    reaches either - the Scope's door refuses it (``ScopeError``), and the
    question falls through before a template runs."""
    if span is None:
        return False
    if season is not None:
        raise TemplateUnsupported(f"{intent} cannot tell whether a career span with {season} named means that season, since it, or through it")
    return True


def _season_label(season: int) -> str:
    """1994 -> "1993-94", the way a person names a season."""
    return f"{season - 1}-{season % 100:02d}"


def seasons_on_record(con: duckdb.DuckDBPyConnection, athlete_id: str, season_type: int) -> tuple[Any, Any]:
    """A player's first and last season in the per-player season table, which
    reaches back to 1976-77 - before any box score here. A postseason copied
    from the regular season is not a postseason on record.

    .. versionchanged:: 4.4.0
       Honors :data:`~association.query.player_games.BOTH_SEASON_TYPES`
       through :func:`~association.query.player_games.season_type_clause`,
       rather than an equality that a sentinel outside (2, 3) could never
       match.

    .. versionchanged:: 5.0.0
       Public (``_seasons_on_record`` until then): the compiler and the
       count's and the high's readers take it.
    """
    copy = f" AND {not_a_postseason_copy(('points',))}" if season_type == POSTSEASON else ""
    type_clause, type_params = season_type_clause("t.season_type", season_type)
    row = con.execute(f"SELECT MIN(t.season), MAX(t.season) FROM player_season_stats t WHERE t.athlete_id = ? AND {type_clause}{copy}", [athlete_id, *type_params]).fetchone()
    return (row[0], row[1]) if row else (None, None)


def rebuilt_in_scope(con: duckdb.DuckDBPyConnection, season: int | None, season_type: int, athlete_id: str | None) -> int:
    """How many games in scope carry a line rebuilt from play-by-play.

    Used to explain a refusal rather than to answer: when the stat asked for is
    outside :data:`REBUILT_STATS`, "no games with a box score" is true of the
    fetched lines and hides that rebuilt ones exist and were withheld on
    purpose. Saying which is the difference between a gap and a decision.

    .. versionadded:: 2.2.0

    .. versionchanged:: 5.0.0
       Public (``_rebuilt_in_scope`` until then): the count's and the
       high's readers take it.
    """
    if not box_source(con).rebuilt:
        return 0
    scope, params = scope_without_guard("l", season, season_type)
    where = f"{scope} AND l.reconstructed"
    if athlete_id is not None:
        where += " AND l.athlete_id = ?"
        params.append(athlete_id)
    row = con.execute(f"SELECT COUNT(*) FROM player_game_log l WHERE {where}", params).fetchone()
    return int(row[0]) if row else 0


def empty_box_scores(con: duckdb.DuckDBPyConnection, season: int | None, season_type: int, athlete_id: str | None, *, covered_by_rebuild: bool = False) -> tuple[int, int | None, int | None]:
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

    .. versionchanged:: 5.0.0
       Public (``_empty_box_scores`` until then): the count's and the
       high's readers take it.
    """
    if covered_by_rebuild and box_source(con).rebuilt:
        # What is still unseen: no minutes AND no rebuild to stand in for them.
        scope, params = scope_without_guard("l", season, season_type)
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

    scope, params = scope_without_guard("b", season, season_type)
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


def _signed_cell(value: Any) -> str:
    """A NetPoints cell. Signed, because the sign is the whole reading of it -
    an unmarked "0.42" beside "-1.10" loses which one helped their team."""
    return "-" if value is None else f"{value:+.2f}"


def _leaderboard_no_such_rate(rate: Any, metric: str) -> TemplateResult:
    """Refuse a ranking in a unit the metric has no form of, saying so.

    `route()` sets `rate` only where it could NOT switch the metric itself: a
    NetPoints question asking for per-100 becomes the per-100 metric and no
    `rate` slot survives, so a `rate` arriving here always means the unit does
    not exist for what was asked. "who were the top 10 in defensive netpoints
    / 90" is the measured case - per 90 minutes is a football unit, and
    nothing in the warehouse is stored in it.

    Refusing beats the fall-through this used to get. `check_scope` raised
    instead, which reads as a refusal in the trace and is not one: the
    question went to the agent, which has no per-90 anything to read and is
    then free to fill the silence from its own weights. Naming the unit also
    names what IS available, so the reader can ask again.
    """
    # Name the forms THIS metric actually has, not a generic list: saying
    # "per 100 possessions" while refusing a per-100 request would be the
    # refusal-with-the-wrong-cause shape all over again, and only the three
    # NetPoints metrics have a per-100 sibling.
    forms = ["as a season total" if metric.startswith("total_") else "per game"]
    if metric in SEASON_TOTAL_OF:
        forms.append("as a season total")
    # `netpoints_total`'s per-100 sibling is `netpoints_per_100`, not
    # `netpoints_total_per_100`, so the suffix comes off before looking.
    if f"{metric.removeprefix('avg_').removesuffix('_total')}_per_100" in LEADERBOARD_METRICS:
        forms.append("per 100 possessions")
    asked = "per 90 minutes" if "90" in str(rate) else str(rate).replace("_", " ")
    message = f"No leaderboard ranks {metric.replace('_', ' ')} {asked} - the warehouse stores it only {' or '.join(forms)}."
    return TemplateResult(data={"message": message, "headline": message}, answer=message)


class LeaderboardStepsAside(TemplateUnsupported):
    """``_leaderboard_ranking`` declining a point the compiler's own ranking
    over box scores reads at least as well - a stat with no season-line
    metric, a position group - as distinct from a refusal that stands (an
    unknown field, an ambiguous team, a career list with columns).

    .. versionadded:: 5.0.0
    """


def leaderboard_shot_distance_refusal() -> TemplateResult:
    """The refusal for a shot-distance ranking, naming the real cause (ISSUES.md
    #114): no leaderboard metric ranks distance, and the nearest real one is
    a percentage.

    .. versionadded:: 5.0.0
    """
    message = "Shot distance is not ranked league-wide yet - ask about one named player's average shot distance instead."
    return TemplateResult(data={"message": message, "headline": message}, answer=message)


def _leaderboard_ranking(con: duckdb.DuckDBPyConnection, scope: Scope, *, position: str | None = None) -> TemplateResult:
    """ "Top N players by X" for the metrics in LEADERBOARD_METRICS - the
    ``leaderboard`` template's reader, called by the compiler's presenter
    (``compose.present._present_leaderboard``) since the template retired
    (ROADMAP plan item 6, step (g)). Raises ``TemplateUnsupported`` exactly
    where the template did, and the presenter steps aside for the compiler's
    own ranking over box scores, which answered behind those refusals.

    Thin on purpose: run_leaderboard owns the season default, minimum-sample
    floor and traded-player dedup. This adds slot mapping and phrasing.

    .. versionchanged:: 2.1.0
       Honors ``span`` "career", ranking whole careers (see
       :func:`~association.query.leaderboard.run_career_leaderboard`) and
       saying whose. ``rate`` "total" ranks a season total rather than a
       per-game average. Every stat name the router is taught now maps to a
       metric, and a qualifier, when one applies, is named in the answer.

    .. versionchanged:: 4.4.0
       Refuses a shot-distance ranking naming the real cause - no leaderboard
       metric ranks distance - rather than resolving `stat` to the nearest
       real one (`threePointFieldGoalPct`, answered as a percentage) or
       falling through to a refusal about a filler `player` slot instead
       (ISSUES.md #114).

    .. versionchanged:: 4.4.0
       The shot-distance refusal now says the ranking is not BUILT rather
       than that none is possible - yardstick-v2 F019 marked the first
       wording false: the key computes a real league leader (Porzingis,
       27.37 ft with a 100-attempt floor) straight from `shot_chart`. Filed
       in ISSUES.md as the gap it names - a `shot_distance` metric a leaderboard
       could rank, with an attempt floor and end-of-period heaves excluded -
       rather than built here, since the ranking needs its own qualifying
       floor measured (not simply plugged into `LEADERBOARD_METRICS`).

    .. versionchanged:: 5.0.0
       A reader over the settled scope (``con``, ``scope``, the subject's
       ``position``), not a template.
    """
    if scope.stat == "shot_distance":
        # router._route_leaderboard_shot_distance's sentinel - see the
        # comment there. Checked before resolve_metric and before the named-
        # player refusal just below, on purpose: "who lead the league in avg
        # 3 point distance" used to resolve `stat` to the nearest real metric
        # and answer a PERCENTAGE, and its "shot distance" sibling used to
        # arrive with a filler player slot ("player": "player") that
        # the invented-name check (agent.py) refused first, for naming a
        # player the question does not mention - honest-sounding, and the
        # wrong cause, since no leaderboard metric exists either way. Neither
        # check below gets a chance to name the wrong cause now.
        return leaderboard_shot_distance_refusal()
    career = _career_span("leaderboard", scope.span, scope.season)
    metric = resolve_metric(scope.stat, career=career)
    if metric is None:
        raise LeaderboardStepsAside(f"no leaderboard metric for stat {scope.stat!r}")
    rate = scope.rate
    if rate == "total":
        # `stat` names a category, never which of its two readings; "most
        # points this season" is a total and "leads in points" a per-game rate.
        metric = SEASON_TOTAL_OF.get(metric, metric)
    elif rate is not None:
        return _leaderboard_no_such_rate(rate, metric)
    _leaderboard_refuse_a_subject(scope, position)
    fields = _leaderboard_fields(scope, metric)
    if career:
        return _career_leaderboard(con, metric, scope, fields)
    show_team = "team" in fields
    box_fields = [f for f in fields if f != "team"]
    try:
        result = run_leaderboard(
            con,
            metric,
            season=scope.season,
            season_type=scope.season_type or 2,
            team=scope.team,
            fields=box_fields or None,
            limit=_clamp_limit(scope.limit, default=DEFAULT_LEADERBOARD_LIMIT),
        )
    except LeaderboardError as exc:
        # An ambiguous team, an unknown metric, or a table that needs a
        # warehouse flag - all reasons to fall through, never to guess.
        raise TemplateUnsupported(str(exc)) from exc
    if show_team and result.season_type is None:
        # A metric with no season_type (a fingerprint-shaped one) has nothing
        # for player_game_log's own season_type column to join on - refused
        # rather than silently dropping the team column nobody asked to lose.
        raise TemplateUnsupported(f"{result.label} has no season type to look a team up by")

    period = _period(result.season, result.season_type or 2)
    where = f"the {result.team_name}" if result.team_name else "the league"
    summary = f"{result.label}, {period}"
    ratio = LEADERBOARD_METRICS[metric].ratio
    trade_note = _leaderboard_show_teams(con, result) if show_team else ""
    answer = (
        _tabulate_leaderboard(result.rows, result.label, where, period, fields, result.min_sample_applied, result.min_sample_column, ratio)
        if fields
        else _phrase_leaderboard(result.rows, result.label, where, period, _qualifier(result.min_sample_applied, result.min_sample_column), ratio)
    )
    data = {"question_shape": summary, "season": result.season, "fields": fields, "min_sample": result.min_sample_applied, "leaders": result.rows}
    return TemplateResult(data=_leaderboard_result_data(data, answer, trade_note), answer=answer + trade_note)


def _leaderboard_refuse_a_subject(scope: Scope, position: str | None) -> None:
    """``leaderboard``'s refusal of a subject it cannot rank for: a position
    group, and one named player. Split out of :func:`_leaderboard_ranking`
    for the complexity gate, in its order."""
    if position is not None:
        # A position group is part of the league no leaderboard metric
        # narrows to - "highest 3 point percentage ... by a shooting guard"
        # (F056) ranked the whole league before this refused it; the
        # compiler reads the group off the subject (query/point.py's
        # league-wide point). Refused here, where a named player is, so what
        # refuses first is unchanged.
        raise LeaderboardStepsAside(f"a leaderboard cannot narrow to a position group ({position!r})")
    if scope.player is not None and scope.player.strip():
        # A leaderboard ranks the league or a team, never one named person.
        # Confirmed live: "Klay Thompson's 3pt percentage over the past 4
        # seasons" landed here and came back with the league's true-shooting
        # leaders, Klay silently dropped.
        raise TemplateUnsupported(f"a leaderboard cannot answer about one named player ({scope.player!r})")


def _leaderboard_result_data(data: dict[str, Any], answer: str, trade_note: str) -> dict[str, Any]:
    """``leaderboard``'s own ``headline``/``notes`` - split out to keep the
    caller under the complexity gate. ``answer`` is the table or sentence
    BEFORE ``trade_note`` (``_leaderboard_show_teams``) is glued on: the
    trade note is already its own line (a leading ``"\\n"``), so it becomes
    the only entry in ``notes``, and ``headline`` is stripped of the trailing
    "Next: ..." list the same way the page's own fallback already reads it
    (``firstLine``, ``web/static/index.html``).

    .. versionadded:: 4.4.0
    """
    headline = answer.split("\n")[0].rstrip(":").split(" Next: ")[0]
    return {**data, "headline": headline, "notes": [trade_note.strip()] if trade_note else []}


def _leaderboard_fields(scope: Scope, metric: str) -> list[str]:
    """The extra columns a leaderboard was asked to show beside its metric -
    a box-score average (:data:`~association.query.metrics.EXTRA_FIELD_COLUMNS`),
    or ``"team"`` (F017, ISSUES.md), which :func:`leaderboard` looks up
    separately rather than through the same generic box-score join."""
    # "top 10 in NetPoints ALONGSIDE their points per game" used to be answered
    # without the second half and without saying so - a silent partial answer,
    # the failure this whole architecture exists to prevent. An unknown field
    # falls through rather than being dropped.
    requested = scope.fields
    unknown = [f for f in requested if f not in EXTRA_FIELD_COLUMNS and f != "team"]
    if unknown:
        raise TemplateUnsupported(f"unknown leaderboard field(s) {unknown}")
    # Deduplicated, order preserved: the router repeats itself sometimes
    # (["points","minutes","minutes"]), which is a slip, not a reason to spend
    # minutes in the agent. A field restating the ranked metric goes too - it
    # rendered the same 33.5 twice under two headings.
    return [f for f in dict.fromkeys(requested) if metric != f"avg_{f}"]


def _leaderboard_team_names(con: duckdb.DuckDBPyConnection, athlete_ids: list[str], season: int, season_type: int) -> tuple[dict[str, str], bool]:
    """Each athlete's team for a leaderboard row that asked to see it (F017,
    ISSUES.md): the team he played his most recent game for that season and
    season type - read off ``player_game_log``, the one table that orders a
    traded player's stints by date, unlike the ranked table itself (whose own
    "combined" row for a traded player has no single team at all). Team names
    are read for the season asked about (:func:`~association.nba.franchises.season_name_sql`),
    since a franchise's own name can differ by season.

    Returns each athlete's team by id, and whether ANY of them played for more
    than one team that season - the fact behind the caller's own "shown the
    most recent team" note.

    .. versionadded:: 4.4.0
    """
    placeholders = ", ".join("?" for _ in athlete_ids)
    rows = con.execute(
        f"""
        SELECT athlete_id, team, traded FROM (
            SELECT pgl.athlete_id AS athlete_id,
                   {season_name_sql("pgl.team_id", "pgl.season", "t.display_name")} AS team,
                   COUNT(DISTINCT pgl.team_id) OVER (PARTITION BY pgl.athlete_id) > 1 AS traded,
                   ROW_NUMBER() OVER (PARTITION BY pgl.athlete_id ORDER BY pgl.game_date DESC) AS rn
            FROM player_game_log pgl JOIN teams t ON t.team_id = pgl.team_id
            WHERE pgl.season = ? AND pgl.season_type = ? AND pgl.athlete_id IN ({placeholders})
        ) WHERE rn = 1
        """,
        [season, season_type, *athlete_ids],
    ).fetchall()
    names = {athlete_id: team for athlete_id, team, _ in rows}
    return names, any(traded for _, _, traded in rows)


def _leaderboard_show_teams(con: duckdb.DuckDBPyConnection, result: LeaderboardResult) -> str:
    """Injects each shown row's team into it (F017, ISSUES.md), mutating
    ``result.rows`` in place so :func:`_tabulate_leaderboard`'s existing
    generic column reader (``row.get("team")``) needs no column of its own to
    know about - and returns the trade note, or "" when nobody shown played
    for more than one team that season.

    .. versionadded:: 4.4.0
    """
    assert result.season_type is not None  # the caller already refused this case
    ids = [athlete_id for athlete_id in result.athlete_ids if athlete_id is not None]
    if not ids:
        for row in result.rows:
            row["team"] = "-"
        return ""
    names, traded = _leaderboard_team_names(con, ids, result.season, result.season_type)
    for athlete_id, row in zip(result.athlete_ids, result.rows, strict=True):
        row["team"] = names.get(athlete_id, "-") if athlete_id is not None else "-"
    # "team" always makes `fields` non-empty, so `leaderboard` always appends
    # this after a table (never the plain sentence) - a new line, the same
    # way a table's own truncation and box-score notes follow it elsewhere.
    return note("definition", "\nTeam is each player's most recent team that season.", term="most_recent_team") if traded else ""


def _career_leaderboard(con: duckdb.DuckDBPyConnection, metric: str, scope: Scope, fields: list[str]) -> TemplateResult:
    """A career ranking, on its own path because its pool is its own - see
    :func:`~association.query.leaderboard.run_career_leaderboard`."""
    if fields:
        raise TemplateUnsupported("a career leaderboard cannot add per-game columns")
    if scope.team is not None and scope.team.strip():
        # A franchise's career list sums the per-team rows by team, and where a
        # franchise moved, which years are the franchise's is a question of its
        # own. Refused until that is decided, rather than answered with the
        # league's list under the team's name.
        raise TemplateUnsupported("franchise career leaderboards are not supported")
    season_type = scope.season_type or 2
    try:
        result = run_career_leaderboard(con, metric, season_type=season_type, limit=_clamp_limit(scope.limit, default=DEFAULT_LEADERBOARD_LIMIT))
    except LeaderboardError as exc:
        raise TemplateUnsupported(str(exc)) from exc
    kind = SEASON_TYPE_NAMES.get(season_type, "regular season")
    label = f"career {result.label.removeprefix('total ')}"
    since = _season_label(result.pool_first_season)
    qualifier = _qualifier(result.min_sample_applied, result.min_sample_column)
    answer = _phrase_career_leaderboard(result.rows, label, kind, since, qualifier, LEADERBOARD_METRICS[metric].ratio, pool_first=result.pool_first_season)
    return TemplateResult(
        data={
            "question_shape": f"{label}, {kind}, players active since {since}",
            "season": None,
            "span": "career",
            "pool_first_season": result.pool_first_season,
            "fields": [],
            "min_sample": result.min_sample_applied,
            "leaders": result.rows,
            "headline": answer.split("\n")[0].rstrip(":").split(" Next: ")[0],
        },
        answer=answer,
    )


def _phrase_career_leaderboard(rows: list[dict[str, Any]], label: str, kind: str, since: str, qualifier: str, ratio: tuple[str, str] | None, *, pool_first: int | None = None) -> str:
    """Says whose careers, every time. The pool is every player active in
    1993-94 or later, counted over his whole career, and nobody whose career
    ended before it - Kareem Abdul-Jabbar is not in the warehouse at all - so
    presenting it as "all-time" would be the unrepresentative ranking
    nba/coverage.py's second floor exists to refuse."""
    gap = note("floor", f"Careers that ended before {since} are not in this warehouse, so this is not an all-time list.", table="season_line", first=pool_first, what="career_pool")
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
    unit = MIN_SAMPLE_LABELS.get(column or "", column or "")
    return decided("minimum", f" (minimum {min_sample:,} {unit})", field="minimum", chose=min_sample, of=unit, column=column)


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


def _player_history_subject(con: duckdb.DuckDBPyConnection, scope: Scope) -> Entity | TemplateResult:
    """The player a history is about, settled the one way both
    ``player_history`` and the compiler's season-line source
    (``compose.present._present_player_history``) settle him.

    .. versionadded:: 5.0.0
    """
    # A named season anchors the range's END rather than replacing it, so
    # "3pt% over the 4 seasons through 2024" still spans four rows.
    latest = scope.season or current_season()
    # Narrowed over every season the history could read, not the last N: the
    # query takes each player's last seasons up to `latest` wherever they fall,
    # so a player who retired a decade earlier still has an answer here.
    return _resolved_player(con, scope.player, "player_history needs a player name", available=_SEASON_LINES, through=latest)


def _player_history_read(con: duckdb.DuckDBPyConnection, player: Entity, scope: Scope) -> TemplateResult:
    """``player_history``'s table over a settled player: the stat's columns
    season by season from ``player_season_stats_deduped``, and the career
    line under a career. Raises :class:`TemplateUnsupported` for a stat with
    no per-season column.

    .. versionadded:: 5.0.0
    """
    latest = scope.season or current_season()

    stat = scope.stat
    if stat is None or stat not in HISTORY_COLUMNS:
        raise TemplateUnsupported(f"no per-season history for stat {stat!r}")
    label, columns = HISTORY_COLUMNS[stat]

    career = scope.span == "career"
    season_type = scope.season_type or 2
    limit = scope.limit
    seasons = limit if limit is not None and limit <= MAX_HISTORY_SEASONS else DEFAULT_HISTORY_SEASONS

    # A career is every season, however many - not the default four, and not a
    # count the model put in `limit`, which the router asks it for on this
    # intent whether or not the question gave one. The heading says which
    # seasons are shown either way.
    bound = "" if career else " LIMIT ?"
    rows = con.execute(
        f"SELECT season, gamesPlayed, {', '.join(c for c, _, _ in columns)} FROM player_season_stats_deduped WHERE athlete_id = ? AND season_type = ? AND season <= ? ORDER BY season DESC{bound}",
        [player.id, season_type, latest, *([] if career else [seasons])],
    ).fetchall()

    period = SEASON_TYPE_NAMES.get(season_type, "regular season")
    history, labels = _history_rows(rows, columns)
    answer = _phrase_history(player.name, label, period, history, columns, career=career)
    # Only for a career: the default four seasons is already a window a
    # reader chose, and a combined figure over a window nobody asked to see
    # summed would be the substitution this module exists to stop.
    if career and history:
        career_line = (
            _player_history_career_rate(player.name, label, columns, history) if len(columns) == 3 else _player_history_career_count(con, player.id, season_type, latest, stat, label, player.name)
        )
        if career_line:
            answer += f"\n{career_line}"
    return TemplateResult(
        data={"player": player.name, "stat": stat, "span": "career" if career else None, "seasons": history, "labels": labels},
        answer=answer,
    )


def _history_rows(rows: list[tuple[Any, ...]], columns: list[tuple[str, str, str]]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """``player_history``'s fetched rows as ``data["seasons"]``, and the
    labels beside them - split out of ``player_history`` to keep it under
    the complexity gate; the two comprehensions are unchanged.

    Each row is keyed by the STABLE name (``columns``' third element - see
    ``HISTORY_COLUMNS``' own comment), never the raw SQL a column reads
    through: the page looks a stat up by its slot name, and for
    ``twoPointFieldGoalPct`` that SQL is not even a legal key. The page
    reads a season row's keys generically (``Object.keys``) and title-cases
    whatever it finds - fine for a real column name, useless for "PPG"/
    "2PT%" - so ``labels`` carries the header ``HISTORY_COLUMNS`` already
    computed for the printed text table, rather than leaving the page to
    guess a short label back out of a camelCase key.

    .. versionadded:: 4.4.0
    """
    history = [dict(zip(["season", "games"] + [k for _, _, k in columns], r, strict=True)) for r in rows]
    labels = {k: h for _, h, k in columns}
    return history, labels


def _player_history_career_rate(name: str, label: str, columns: list[tuple[str, str, str]], history: list[dict[str, Any]]) -> str | None:
    """The games-weighted career percentage behind a per-season shooting
    column - the makes and attempts summed across every season shown, never a
    mean of means (F041, ISSUES.md). ``columns`` is the (percentage, made,
    attempted) triple a shooting entry in :data:`HISTORY_COLUMNS` carries, in
    that order.

    .. versionadded:: 4.4.0
    """
    made_key, attempted_key = columns[1][2], columns[2][2]  # the stable key - history's own dict key, not the raw SQL
    made = sum(row.get(made_key) or 0 for row in history)
    attempted = sum(row.get(attempted_key) or 0 for row in history)
    if not attempted:
        return None
    pct = 100.0 * made / attempted
    return f"{name}'s career {label}: {pct:.1f}% ({int(made):,} of {int(attempted):,})."


def _player_history_career_count(con: duckdb.DuckDBPyConnection, player_id: str, season_type: int, latest: int, stat: str, label: str, name: str) -> str | None:
    """The plain career total behind a per-season counting column - the
    stored season total where the table has one, ``avg * gamesPlayed`` where
    it does not (minutes), summed exactly the way :func:`_career_player_stat`
    sums a career total (F041, ISSUES.md).

    .. versionadded:: 4.4.0
    """
    per_game_col = HISTORY_COLUMNS[stat][1][0][0]
    total_col = _CAREER_TOTALS.get(stat)
    amount = f"COALESCE({total_col}, {per_game_col} * gamesPlayed)" if total_col else f"{per_game_col} * gamesPlayed"
    row = con.execute(
        f"SELECT SUM({amount}) FROM player_season_stats_deduped WHERE athlete_id = ? AND season_type = ? AND season <= ? AND gamesPlayed > 0",
        [player_id, season_type, latest],
    ).fetchone()
    if row is None or row[0] is None:
        return None
    noun = label.removesuffix(" per game")
    return f"{name}'s career total: {round(row[0]):,} {noun}."


def _phrase_history(name: str, label: str, period: str, history: list[dict[str, Any]], columns: list[tuple[str, str, str]], *, career: bool = False) -> str:
    if not history:
        return f"The warehouse has no {period} seasons on record for {name}."
    headers = ["season", "G"] + [h for _, h, _ in columns]
    keys = ["season", "games"] + [k for _, _, k in columns]  # history's own dict key (see player_history)
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


def wanted_stats(scope: Scope, default: tuple[str, ...] = STAT_LINE) -> list[str]:
    """The stats to report: the one named, or ``default`` if none was.

    A stat that was NAMED but is not supported must not fall back to the
    default line - that is how "what was Steph Curry's avg 3pt shot distance"
    came back as "26.6 points, 3.6 rebounds and 4.7 assists per game". A
    refusal names the stat; answering a different question is worse."""
    stat = scope.stat
    if stat is None or not stat.strip():
        return list(default)
    if stat in PLAYER_STAT_COLUMNS:
        return [stat]
    raise TemplateUnsupported(f"no per-game column for stat {stat!r}")


@dataclass(frozen=True)
class _ShootingStat:
    """One percentage ``player_stat`` answers, computed from makes and
    attempts rather than read from a stored percentage - a season, a career
    and a narrowed set of games are all the same sum.

    ``made``/``attempted`` are the SQL spliced into a SELECT list - a bare
    column for every stat but ``twoPointFieldGoalPct``, whose "column" is an
    expression (neither table stores a 2-point make/attempt count; see the
    class's own entry below). The season and career readers splice them in;
    a narrowed set of games reads the compiler's own rate and its two sums
    instead (``compose.core.SHOT_RATES``, ``compose.stats``).

    ``made_key``/``attempted_key`` are the STABLE names an answer's
    ``data["stats"]`` exposes the makes and attempts under - identical to
    ``made``/``attempted`` for every stat but ``twoPointFieldGoalPct``, whose
    SQL expression is not a valid key at all. Before these existed, the raw
    expression itself became the dict key: the page looked up the stat it
    wanted by its slot name (``twoPointFieldGoalPct``), found nothing, and
    printed the SQL text as a column header with no value beneath it (seen
    live on the rendered page, 2026-09-24). ``made_label``/``attempted_label``/
    ``pct_label`` are the short forms ("2PM", "2PA", "2PT%") a page prints
    beside those keys, carried in ``data["labels"]`` alongside ``data["stats"]``
    rather than left for a renderer to guess from the key's own spelling.

    .. versionadded:: 4.4.0
    """

    made: str
    attempted: str
    how: str
    noun: str
    made_key: str
    attempted_key: str
    made_label: str
    attempted_label: str
    pct_label: str


SHOOTING_STATS: dict[str, _ShootingStat] = {
    "fieldGoalPct": _ShootingStat("fieldGoalsMade", "fieldGoalsAttempted", "from the field", "field goals", "fieldGoalsMade", "fieldGoalsAttempted", "FGM", "FGA", "FG%"),
    "threePointFieldGoalPct": _ShootingStat(
        "threePointFieldGoalsMade", "threePointFieldGoalsAttempted", "on 3-pointers", "3-pointers", "threePointFieldGoalsMade", "threePointFieldGoalsAttempted", "3PM", "3PA", "3PT%"
    ),
    "freeThrowPct": _ShootingStat("freeThrowsMade", "freeThrowsAttempted", "on free throws", "free throws", "freeThrowsMade", "freeThrowsAttempted", "FTM", "FTA", "FT%"),
    "twoPointFieldGoalPct": _ShootingStat(
        "(fieldGoalsMade - threePointFieldGoalsMade)",
        "(fieldGoalsAttempted - threePointFieldGoalsAttempted)",
        "on 2-pointers",
        "2-pointers",
        "twoPointFieldGoalsMade",
        "twoPointFieldGoalsAttempted",
        "2PM",
        "2PA",
        "2PT%",
    ),
}
"""Shooting percentages ``player_stat`` answers, always with the makes and
attempts behind them - computed from those, never read from a stored
percentage, so a season, a career and a set of games are all the same sum.

.. versionadded:: 2.1.0
.. versionchanged:: 4.4.0
   Added ``twoPointFieldGoalPct``, computed from field goals less the
   three-point columns rather than read from a stored column - ESPN's season
   table has no 2-point make/attempt count of its own.
.. versionchanged:: 4.4.0
   Values are :class:`_ShootingStat` rather than a bare 4-tuple, carrying a
   stable ``data["stats"]`` key and a short label for the makes, the attempts
   and the percentage itself, alongside the SQL each was already keeping.
"""


# A made-count stat's attempted sibling column - the same name in the season
# table and in player_game_log, exactly like SHOOTING_STATS' own pairs, but
# for the three stats the router files as a plain COUNT ("3-pointers made")
# rather than a percentage. "Davion Mitchell 3 point stats" answered makes and
# games and nothing else - not the attempts or the percentage they make,
# both `must_include` in the yardstick key (F051, ISSUES.md). `player_stat`
# reads this only for a single named made-count stat, never a multi-stat
# line, where singling out one entry's "out of how many?" would read as
# though only it needed the qualifier.
MADE_STAT_ATTEMPTS: dict[str, str] = {
    "threePointFieldGoalsMade": "threePointFieldGoalsAttempted",
    "fieldGoalsMade": "fieldGoalsAttempted",
    "freeThrowsMade": "freeThrowsAttempted",
}
"""``player_stat`` made-count stat -> its attempted column.

.. versionadded:: 4.4.0
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


def _player_stat_season_line_subject(con: duckdb.DuckDBPyConnection, scope: Scope) -> tuple[Entity, _Span] | TemplateResult:
    """The player and span an unnarrowed ``player_stat`` reads the season
    line over, settled the one way both the template and the compiler's
    season-line source (``compose.present._present_player_stat``) settle them.

    .. versionadded:: 5.0.0
    """
    return scoped_player(con, scope, "player_stat needs a player name", table="player_season_stats_deduped", available=_SEASON_LINES, span=scope.span, season=scope.season)


def _player_stat_season_line(con: duckdb.DuckDBPyConnection, player: Entity, span: _Span, scope: Scope) -> TemplateResult:
    """An unnarrowed ``player_stat``: one season's line or a career, read
    from ``player_season_stats_deduped`` over a settled player and span.

    .. versionadded:: 5.0.0
    """
    stat = scope.stat
    # Before the ESPN-served columns, because these carry their own table, their
    # own floor and their own career arithmetic - and because wanted_stats
    # would otherwise refuse them as unknown, which is how "kevin durant true
    # shooting percentage career" fell through while the leaderboard ranked the
    # same stat happily.
    if stat is not None and stat in ADVANCED_STATS:
        return _player_stat_advanced(con, player, span, stat)

    shooting = SHOOTING_STATS.get(stat) if stat is not None else None
    wanted = [] if shooting else wanted_stats(scope)
    if span.career:
        return _career_player_stat(con, player, span, wanted, shooting)

    return _season_player_stat(con, player, span, scope.season_type or 2, wanted, shooting)


_player_stat_reads_box_scores = scope_reads_box_scores


def _season_player_stat(con: duckdb.DuckDBPyConnection, player: Entity, span: _Span, season_type: int, wanted: list[str], shooting: _ShootingStat | None) -> TemplateResult:
    """One season's line, read from the deduplicated season table.

    Split out of ``player_stat`` so that function stays inside the complexity
    gate; the steps are in the order they were, and each keeps its comment.
    """
    from association.query.compose.say import phrase_player_stat, shooting_result, stat_value_labels

    season = span.season or current_season()
    columns = ["gamesPlayed"]
    for name in wanted:
        per_game, total, _ = PLAYER_STAT_COLUMNS[name]
        columns.append(per_game)
        if total:
            columns.append(total)
    # A single made-count stat ("3-pointers made") brings its attempted
    # sibling along, the same "out of how many?" discipline SHOOTING_STATS
    # keeps for a percentage (F051, ISSUES.md) - never for a multi-stat line,
    # where a bare made-count is one entry among several and asking "out of
    # how many" of only one of them would read as singling it out.
    attempted_col = MADE_STAT_ATTEMPTS.get(wanted[0]) if len(wanted) == 1 else None
    if attempted_col:
        columns.append(attempted_col)
    if shooting:
        columns += [shooting.made, shooting.attempted]
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
            redirect = season_redirect(con, player.id, season_type, "player_season_stats_deduped")
            answer += _defaulted_season_note(redirect, SEASON_TYPE_NAMES.get(season_type, "regular season"))
        return TemplateResult(
            data={"player": player.name, "season": season, "stats": {}},
            answer=answer,
        )
    values = dict(zip(columns, row, strict=True))
    # The span words the season - "in the 2025 regular season", or "in his 1st
    # season (2025 regular season)" when the question named it by ordinal.
    if shooting:
        return shooting_result(player.name, {"season": season, "season_n": span.ordinal}, values, shooting, when=span.during())
    attempted = values.get(attempted_col) if attempted_col else None
    return TemplateResult(
        data={"player": player.name, "season": season, "season_n": span.ordinal, "stats": values, "labels": stat_value_labels(wanted)},
        answer=phrase_player_stat(player.name, period, values, wanted, when=span.during(), attempted=attempted),
    )


def _career_player_stat(con: duckdb.DuckDBPyConnection, player: Entity, span: _Span, wanted: list[str], shooting: _ShootingStat | None) -> TemplateResult:
    """A career line summed from the season table. A season whose total is
    missing falls back to its average times its games, and each per-game figure
    is divided by the games that actually carry that stat."""
    from association.query.compose.say import phrase_player_stat, rounded, shooting_result, stat_value_labels

    selects = ["SUM(gamesPlayed)", "MIN(season)", "MAX(season)", "COUNT(*)"]
    for stat in wanted:
        per_game = PLAYER_STAT_COLUMNS[stat][0]
        total = _CAREER_TOTALS[stat]
        amount = f"COALESCE({total}, {per_game} * gamesPlayed)" if total else f"{per_game} * gamesPlayed"
        selects += [f"SUM({amount}) / SUM(CASE WHEN {amount} IS NOT NULL THEN gamesPlayed END)", f"SUM({amount})"]
    # See _season_player_stat's own comment: a single made-count stat's
    # attempted total, never a multi-stat line's (F051, ISSUES.md).
    attempted_col = MADE_STAT_ATTEMPTS.get(wanted[0]) if len(wanted) == 1 else None
    if attempted_col:
        selects.append(f"SUM({attempted_col})")
    if shooting:
        made, attempted = shooting.made, shooting.attempted
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
        values[shooting.made], values[shooting.attempted] = row[4], row[5]
        return shooting_result(player.name, scope, values, shooting, when=when)
    for index, stat in enumerate(wanted):
        per_game_col, total_col, _ = PLAYER_STAT_COLUMNS[stat]
        values[per_game_col] = rounded(row[4 + 2 * index])
        amount = row[5 + 2 * index]
        if total_col and amount is not None:
            values[total_col] = round(amount)
    attempted_total = row[4 + 2 * len(wanted)] if attempted_col else None
    if attempted_col and attempted_total is not None:
        values[attempted_col] = int(attempted_total)
    return TemplateResult(
        data={"player": player.name, **scope, "stats": values, "labels": stat_value_labels(wanted)},
        answer=phrase_player_stat(player.name, f"career {span.kind}s", values, wanted, when=when, attempted=attempted_total),
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
    said = f" {missing} {subject} not counted: ESPN's box scores for {them} are empty, so no {spec.label} can be computed from {them}."
    return note("seasons_missing", said, seasons=missing, why="empty_box_score", stat=spec.column, label=spec.label)


def _player_stat_advanced(con: duckdb.DuckDBPyConnection, player: Entity, span: _Span, stat: str) -> TemplateResult:
    """One player's computed advanced stat, for a season or a career.

    Read from ``player_season_advanced_stats``, which starts in 1994 and has
    1993 as a phantom copy of it - a shorter reach than the season line this
    template normally uses, so the span is rebuilt against that table rather
    than inherited, and a career excludes the phantom by name. The season
    line's alone: over a narrowed set of games the line's reader declines an
    advanced stat (``compose.stats``), and the compiler's own sentence
    answers it where it has the measure and declines it where not.
    """
    spec = ADVANCED_STATS[stat]
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


MAX_COMPARED_PLAYERS = 4


def _player_compare_lines(con: duckdb.DuckDBPyConnection, scope: Scope) -> TemplateResult:
    """Two or more named players' season numbers side by side -
    ``player_compare``'s answer, over the scope the parser settled. The
    retired template's own body (5.0.0); its caller is the compiler's
    presenter (:func:`~association.query.compose.present._present_player_compare`),
    for a pair's point on the season line (``source="seasons"``), and the
    point itself refuses fewer than two distinct names and any narrowing
    (:func:`~association.query.point._compare_point`), as the
    template's ``check_scope`` did.

    The agent wrote correct SQL but expanded "SGA" to '%Scottie G. Allen%' and
    compared Luka Doncic to Luka Garza. Nickname resolution is a lookup, not
    something to hope a 7B model knows - see entities.PLAYER_NICKNAMES.

    .. versionadded:: 5.0.0
       ``player_compare``'s body, over the settled scope.
    """
    names = scope.players
    season = scope.season or current_season()
    resolved: list[Entity] = []
    for name in names[:MAX_COMPARED_PLAYERS]:
        player = _resolved_player(con, name, available=_SEASON_LINES, season=season)
        if isinstance(player, TemplateResult):
            return player
        if player.id not in {p.id for p in resolved}:
            resolved.append(player)
    if len(resolved) < 2:
        raise TemplateUnsupported("the named players resolved to the same person")

    season_type = scope.season_type or 2
    wanted = wanted_stats(scope, COMPARE_STAT_LINE)
    columns = ["gamesPlayed"] + [PLAYER_STAT_COLUMNS[name][0] for name in wanted]

    rows: dict[str, dict[str, Any]] = {}
    for player in resolved:
        row = _season_row(con, player.id, columns, season, season_type)
        rows[player.name] = dict(zip(columns, row, strict=True)) if row else {}

    net = _compare_netpoints(con, resolved, season, season_type)
    period = _period(season, season_type)
    answer = _phrase_compare(rows, wanted, period, net)
    lines = answer.split("\n")
    # `_phrase_compare`'s own last line, and only when it added one: "(X has
    # no {period} numbers in the warehouse.)" - a caveat about a missing
    # player's row, not part of the table itself.
    missing_note = lines[-1] if lines[-1].startswith("(") and lines[-1].endswith(")") else None
    return TemplateResult(
        data={"season": season, "players": rows, "netpoints": net, "headline": lines[0].rstrip(":"), "notes": [missing_note] if missing_note else []},
        answer=answer,
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
        lines.append(note("no_data_for", f"({', '.join(missing)} has no {period} numbers in the warehouse.)", names=missing, period=period))
    return "\n".join(lines)
