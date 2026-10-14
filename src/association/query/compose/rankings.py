"""The ranking reader: a season's or a career's leaders by one metric over
the season line (``player_season_stats_deduped`` and the season-shaped
NetPoints tables) - ``leaderboard``'s own point, read into a
:class:`~association.query.result.Result` with a
:class:`~association.query.result.Grouped` body by ``player``, ranked by the
metric (``Grouped.ranked_by``). Phase 2's slice (iii) (``ROADMAP.md``,
"Phase 2, the expected steps", step 3): until 2026-10-05 the presenter
``compose.present._present_leaderboard`` handed the point to the retired
template's body (``templates.players._leaderboard_ranking``), which read
and worded it; the read is the season line's own door now
(:func:`~association.query.season_line.rank_season_line` - the floors that
need no refusal, the traded-player dedup, the qualifier, the career pool),
moved and not re-derived, and the words are the sayer's
(:func:`~association.query.compose.say.say_leaderboard`).

What the words added beyond the ranking's result object, measured on the 67
recorded presenter answers before the move
(``~/association-research/stages/leaderboard_measure.py``: 67 of 67 rows and
minimums identical to the result objects called directly), and where each is
now: the qualifier (58) - the ``minimum`` decision it already was; the
career pool (1) - a ``floor`` note on the season line; the most-recent-team
remark (4) - a ``definition`` note; the "also" columns and the team column
(2 and 4) - values on each row; the team filter (6) - :class:`~association.query.result.RankingFacts`' ``team``; the
season, named (12) or defaulted (54) - the span, unsaid when defaulted as it
was. The coverage floors (the missing season and the unrepresentative one,
``nba.coverage.Floor.unrepresentative``) are refusals the answering loop
gives before any reader runs (``coverage.check_coverage``, over
``RANKING_INTENTS``), unchanged.

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import duckdb

from association.query.measure import keyed
from association.query.measures import resolve_metric
from association.query.metrics import EXTRA_FIELD_COLUMNS, LEADERBOARD_METRICS, qualifies_by
from association.query.notes import Note
from association.query.reading import MAX_LIMIT, Filtered, ShapeDeclined, Unsupported, _clamp_limit
from association.query.result import Decided, Grouped, Part, RankingFacts, Refusal, Result, Scalar, Span, Unanswered
from association.query.season_line import MIN_SAMPLE_LABELS, CareerLeaderboardResult, LeaderboardError, LeaderboardResult, rank_season_line, rank_seasons_counted

from .core import Query

DEFAULT_LEADERBOARD_LIMIT = 10
"""How many leaders a ranking shows when the question names no count.

.. versionadded:: 5.0.0
   Moved from ``templates.players`` with the leaderboard's reader.
"""


def _leaderboard_is_own_point(q: Query) -> bool:
    """Whether ``q`` is the league's ranking on the season line - the point
    the retired presenter took, and no other."""
    return q.subject == "everyone" and q.source == "seasons" and q.skeleton == "grouped" and q.group == "player" and not q.predicates


def leaderboard_reads(q: Query) -> bool:
    """Whether :func:`read_leaderboard` reads ``q`` (or refuses it with a
    sentence or a decline of its own) rather than stepping aside for the
    game-level ranking: the league's ranking on the season line, and -
    unless a shot-distance ranking, a career with a year named or a unit the
    metric has no form of, which it says itself - a stat a season-line
    metric reads, over no position group. Read from the point alone, so the
    planner plans the rest as the game-level ranking (``compose.plan.plan``)
    before anything runs; a narrowing beyond what its words state is the
    planner's check beside it (``compose.plan.cells_unhonored``).

    .. versionadded:: 5.0.0

    .. versionchanged:: 6.0.0
       Takes no ``stated``: the answer side checks the point's cells against
       its shape's row before asking (``compose.plan.cells_unhonored``,
       Phase 3, step 2's closing slice).
    """
    if not _leaderboard_is_own_point(q):
        return False
    scope = q
    if keyed(scope.measure) == "shot_distance" or (scope.span.career and scope.span.season is not None):
        return True
    metric = resolve_metric(scope.measure, career=scope.span.career)
    if metric is None:
        return False
    if q.minimum is not None and not qualifies_by(metric, q.minimum.unit, q.minimum.of):
        # A qualifier the question states in a unit the metric does not
        # qualify by: the game-level ranking's, or its refusal by name - the
        # point reader planned it there already (ISSUES.md #377).
        return False
    unit = scope.measure.unit if scope.measure is not None else None
    if unit is not None and unit != "total":
        return True
    return q.position is None


def players_counted_reads(q: Query) -> bool:
    """Whether :func:`read_players_counted` reads ``q``: how many of the
    league's players a season line holds (ISSUES.md #390), the season
    ranking's own point counted - read wherever the ranking would read it.

    .. versionadded:: 6.0.0
    """
    if not (q.skeleton == "scalar" and q.subject == "everyone" and q.source == "seasons" and any(line.per != "game" for line in q.lines)):
        return False
    return leaderboard_reads(_as_ranking(q))


def _as_ranking(q: Query) -> Query:
    """The count's point as the ranking it counts."""
    return replace(q, skeleton="grouped", group="player")


def read_players_counted(con: duckdb.DuckDBPyConnection, q: Query) -> Result | Unanswered | None:
    """How many players a season line holds - "how many players averaged 30
    ppg in 2026" (ISSUES.md #390, the 26th key, D4 lifted for it by Jeff on
    2026-10-13) - read as the season ranking kept past the line
    (:func:`read_leaderboard`) and counted: a
    :class:`~association.query.result.Scalar` body ``how="players"`` with
    the count, and the ranking's players as the next part. ``None`` where
    the ranking would not read the point.

    .. versionadded:: 6.0.0
    """
    if not players_counted_reads(q):
        return None
    ranked = read_leaderboard(con, _as_ranking(q))
    if not isinstance(ranked, Result):
        return ranked
    pool = ranked.facts.pool if isinstance(ranked.facts, RankingFacts) else None
    return replace(ranked, parts=(Part(body=Scalar(games=0, values={"players": pool or 0}, how="players")), *ranked.parts))


def seasons_counted_reads(q: Query) -> bool:
    """Whether :func:`read_seasons_counted` reads ``q``: the league's
    players ranked by how many of their seasons a season line holds
    (ISSUES.md #391), on the season line, over no team or position group.

    .. versionadded:: 6.0.0
    """
    return (
        q.skeleton == "grouped"
        and q.group == "player"
        and q.subject == "everyone"
        and q.source == "seasons"
        and q.who.team is None
        and q.position is None
        and any(line.per != "game" for line in q.lines)
    )


def read_seasons_counted(con: duckdb.DuckDBPyConnection, q: Query) -> Result | Unanswered | None:
    """The players with the most seasons a season line holds - "most seasons
    averaging 30 ppg", "most 25 point seasons", "most seasons with 2000
    points" (ISSUES.md #391; D4 lifted for the season ranking's shapes by
    Jeff, 2026-10-13) - read through the season line's door
    (:func:`~association.query.season_line.rank_seasons_counted`) into a
    :class:`~association.query.result.Grouped` ranking by ``player`` whose
    ``ranked_by`` is ``seasons``, as a count of games over a line ranks by
    ``games``. ``None`` where the point is not that.

    .. versionadded:: 6.0.0
    """
    if not seasons_counted_reads(q):
        return None
    lines = tuple(line for line in q.lines if line.per != "game")
    season_type = q.span.season_type or 2
    try:
        found = rank_seasons_counted(con, lines, season_type=season_type, limit=_clamp_limit(q.window.count, default=DEFAULT_LEADERBOARD_LIMIT))
    except LeaderboardError as exc:
        raise Unsupported(str(exc)) from exc
    body = _seasons_body(found.rows)
    decisions = (Decided(kind="minimum", field="minimum", chose=found.min_games, facts={"of": "games", "column": "gamesPlayed"}),) if found.min_games is not None else ()
    facts = RankingFacts(label="seasons", lines=tuple((str(line.measure), line.op, line.value, line.per) for line in lines), pool=found.pool)
    span = Span(season=None, season_type=season_type, career=True, first=found.pool_first_season, source="seasons")
    return Result(subject="every player", relation="everyone", span=span, parts=(Part(body=body),), decisions=decisions, facts=facts)


def _seasons_body(rows: list[dict[str, Any]]) -> Grouped:
    """The players ranked by their count of seasons, a competition rank
    (two on the same count share it)."""
    ranked: list[dict[str, Any]] = []
    rank, previous = 0, None
    for place, row in enumerate(rows, start=1):
        if place == 1 or row["value"] != previous:
            rank = place
        previous = row["value"]
        ranked.append({"key": row["display_name"], "rank": rank, "values": {"seasons": row["value"], "first_season": row["first_season"], "last_season": row["last_season"]}})
    return Grouped(by="player", rows=tuple(ranked), ranked_by="seasons")


def _leaderboard_career(scope: Filtered) -> bool:
    """True for a career ranking, False for a one-season one; raises for a
    career with a year named. The router kept a year the question named
    alongside "career", so "most points ever in a game in 2024" (that
    season), "career leaders since 2015" (a range) and "career points
    through 2010" (a cutoff) all arrived as the same two slots - answering
    any of them as one of the others is the substitution this refuses
    (``templates.players._career_span`` until the template's words moved)."""
    if not scope.span.career:
        return False
    if scope.span.season is not None:
        raise Unsupported(f"leaderboard cannot tell whether a career span with {scope.span.season} named means that season, since it, or through it")
    return True


def _leaderboard_refuse_a_player(scope: Filtered) -> None:
    """A refusal for one named player: a leaderboard ranks the league or a
    team, never one person ("Klay Thompson's 3pt percentage over the past 4
    seasons" came back with the league's true-shooting leaders, Klay
    silently dropped). A position group, which the game-level ranking reads
    (F056), is the planner's (:func:`leaderboard_reads`), and was checked
    first, as the template had it."""
    if scope.who.player is not None:
        raise Unsupported(f"a leaderboard cannot answer about one named player ({scope.who.player!r})")


def _leaderboard_fields(scope: Filtered, metric: str) -> list[str]:
    """The extra columns a ranking was asked to show beside its metric - a
    box-score average (:data:`~association.query.metrics.EXTRA_FIELD_COLUMNS`)
    or ``"team"`` (F017, ISSUES.md). An unknown field is refused rather than
    dropped ("top 10 in NetPoints ALONGSIDE their points per game" was once
    answered without the second half, silently); a repeated one (the
    router's slip, when this was written) is said once, and one restating
    the ranked metric is
    left out (it rendered the same 33.5 twice under two headings)."""
    requested = scope.measure.beside if scope.measure is not None else ()
    unknown = [f for f in requested if f not in EXTRA_FIELD_COLUMNS and f != "team"]
    if unknown:
        raise Unsupported(f"unknown leaderboard field(s) {unknown}")
    return [f for f in dict.fromkeys(requested) if metric != f"avg_{f}"]


def _leaderboard_metric(scope: Filtered, career: bool) -> str | None:
    """The metric the ranking reads (a unit it has no form of declined), or ``None`` where no season-line metric reads the stat (the
    game-level ranking reads it instead). A season total asked for ranks the
    total: the key names a category, never which of its two readings ("most
    points this season" is a total, "leads in points" a per-game rate) - the
    catalog resolves the pair (:func:`~association.query.measure.metric_name`)."""
    metric = resolve_metric(scope.measure, career=career)
    if metric is None:
        return None
    unit = scope.measure.unit if scope.measure is not None else None
    if unit is not None and unit != "total":
        # A unit the metric has no form of is the point reader's refusal
        # (the ``ranking_unit`` Cause, said by the planner) before the
        # point is planned; read here it is declined, never ranked as
        # another unit.
        raise Unsupported(f"leaderboard has no {unit!r} form of {metric}")
    return metric


def read_leaderboard(con: duckdb.DuckDBPyConnection, q: Query) -> Result | Unanswered | None:
    """``leaderboard``'s own point - the league's (or a team's players')
    leaders by one season-line metric, over a season or a career - read into
    a Result through the season line's door
    (:func:`~association.query.season_line.rank_season_line`). ``None`` where
    the point is not that, names a stat no season-line metric reads, or
    ranks a position group: the game-level
    ranking answers those, as it did behind the retired template's refusal
    (planned so: :func:`leaderboard_reads`). A :class:`~association.query.result.Refusal` back is the ranking's own
    refusal (a shot-distance ranking); a
    ``Unsupported`` the relation's decline (an unknown field, an
    ambiguous team, a career list with columns or a franchise's).

    .. versionadded:: 5.0.0

    .. versionchanged:: 6.0.0
       Takes no ``stated``: the answer side checks the point's cells against
       its shape's row before asking (``compose.plan.cells_unhonored``,
       Phase 3, step 2's closing slice).
    """
    if not leaderboard_reads(q):
        return None
    scope = q
    if keyed(scope.measure) == "shot_distance":
        # The measure tagger's sentinel key, checked before
        # the metric and the named-player refusal so neither names the wrong
        # cause (ISSUES.md #114); the planner says it first today
        # (the point reader's ``shot_distance_ranking`` Cause).
        return Refusal(kind="shot_distance_ranking", under=("message", "headline"))
    career = _leaderboard_career(scope)
    metric = _leaderboard_metric(scope, career)
    # leaderboard_reads settled it: a stat no season-line metric reads is the
    # game-level ranking's, planned so; and so is a position group.
    assert metric is not None
    _leaderboard_refuse_a_player(scope)
    fields = _leaderboard_fields(scope, metric)
    if career:
        _leaderboard_career_refusals(scope, fields)
    # The season lines its players are kept past (ISSUES.md #390): every
    # player over them is the answer, so the list runs to the cap where no
    # count was asked, and how many there were is said beside it.
    lines = tuple(line for line in scope.lines if line.per != "game")
    limit = _clamp_limit(scope.window.count, default=MAX_LIMIT if lines else DEFAULT_LEADERBOARD_LIMIT)
    try:
        ranking = rank_season_line(
            con,
            metric,
            career=career,
            season=scope.span.season,
            season_type=scope.span.season_type or 2,
            team=scope.who.team,
            fields=fields,
            limit=limit,
            min_sample=q.minimum.count if q.minimum is not None else None,
            lines=lines,
            ascending=q.direction == "asc",
        )
    except LeaderboardError as exc:
        # An ambiguous team, an unknown metric, a team column with no season
        # type to look it up by, or a table that needs a warehouse flag - all
        # reasons to decline, never to guess.
        raise Unsupported(str(exc)) from exc
    found = ranking.result
    decisions, stated = _leaderboard_minimum(q, found)
    ratio = LEADERBOARD_METRICS[metric].ratio
    facts = RankingFacts(
        label=found.label,
        ratio=tuple(ratio) if ratio else None,
        fields=tuple(fields),
        stated_minimum=stated,
        lines=tuple((str(line.measure), line.op, line.value, line.per) for line in lines),
        pool=found.pool,
        ascending=q.direction == "asc",
    )
    if isinstance(found, CareerLeaderboardResult):
        pool = found.pool_first_season
        span = Span(season=None, season_type=found.season_type, career=True, first=pool, source="seasons")
        notes: tuple[Note, ...] = (Note("floor", {"table": "season_line", "first": pool, "what": "career_pool"}),)
    else:
        # season_type is None for a metric with no season type (a
        # fingerprint-shaped one); the sayer names it the regular season.
        span = Span(season=found.season, season_type=found.season_type, source="seasons")
        notes = (Note("definition", {"term": "most_recent_team"}),) if ranking.traded else ()
        facts = replace(facts, team=found.team_name)
        if scope.span.season is None:
            # The season was the reader's choice (the latest on record), not
            # the question's: recorded as the decision it is, said by the
            # span the heading names anyway (Jeff, 2026-10-05: the default is
            # visible there and a season named reaches the alternative).
            decisions = (*decisions, Decided(kind="season_default", field="season", chose=found.season, why="latest_on_record", facts={"season_type": found.season_type or 2}))
    return Result(subject="every player", relation="everyone", span=span, parts=(Part(body=ranking.body),), notes=notes, decisions=decisions, facts=facts)


def _leaderboard_minimum(q: Query, found: LeaderboardResult | CareerLeaderboardResult) -> tuple[tuple[Decided, ...], tuple[int, str] | None]:
    """The qualifier the ranking applied: the ``minimum`` decision where it
    was the default, or the floor the question stated - said as the
    default is, and no decision (ISSUES.md #377)."""
    minimum = found.min_sample_applied
    if minimum is None:
        return (), None
    unit = MIN_SAMPLE_LABELS.get(found.min_sample_column or "", found.min_sample_column or "")
    if q.minimum is not None:
        return (), (minimum, unit)
    return (Decided(kind="minimum", field="minimum", chose=minimum, facts={"of": unit, "column": found.min_sample_column}),), None


def _leaderboard_career_refusals(scope: Filtered, fields: list[str]) -> None:
    """What a career ranking declines: columns beside the metric, and a
    franchise's list - which sums the per-team rows by team, and where a
    franchise moved, which years are the franchise's is a question of its
    own. Declined until that is decided, rather than answered with the
    league's list under the team's name."""
    if fields:
        raise Unsupported("a career leaderboard cannot add per-game columns")
    if scope.who.team is not None:
        raise ShapeDeclined("franchise_career_ranking", {}, "franchise career leaderboards are not supported")
