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

import duckdb

from association.query.measures import resolve_metric
from association.query.metrics import EXTRA_FIELD_COLUMNS, LEADERBOARD_METRICS
from association.query.notes import Note
from association.query.reading import Scope, Unsupported, _clamp_limit, unhonored_scoping
from association.query.result import Decided, Part, RankingFacts, Refusal, Result, Span, Unanswered
from association.query.season_line import MIN_SAMPLE_LABELS, SEASON_TOTAL_OF, CareerLeaderboardResult, LeaderboardError, rank_season_line

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


def leaderboard_reads(q: Query, stated: frozenset[str]) -> bool:
    """Whether :func:`read_leaderboard` reads ``q`` (or refuses it with a
    sentence or a decline of its own) rather than stepping aside for the
    game-level ranking: the league's ranking on the season line, narrowed
    only by what its words state, and - unless a shot-distance ranking, a
    career with a year named or a unit the metric has no form of, which it
    says itself - a stat a season-line metric reads, over no position group.
    Read from the point alone, so the planner plans the rest as the
    game-level ranking (``compose.plan.plan``) before anything runs.

    .. versionadded:: 5.0.0
    """
    if not _leaderboard_is_own_point(q) or unhonored_scoping("leaderboard", q.scope, stated):
        return False
    scope = q.scope
    if scope.stat == "shot_distance" or (scope.span is not None and scope.season is not None):
        return True
    if resolve_metric(scope.stat, career=scope.span is not None) is None:
        return False
    if scope.rate is not None and scope.rate != "total":
        return True
    return q.position is None


def _leaderboard_career(scope: Scope) -> bool:
    """True for a career ranking, False for a one-season one; raises for a
    career with a year named. The router keeps a year the question named
    alongside "career", so "most points ever in a game in 2024" (that
    season), "career leaders since 2015" (a range) and "career points
    through 2010" (a cutoff) all arrive as the same two slots - answering
    any of them as one of the others is the substitution this refuses
    (``templates.players._career_span`` until the template's words moved)."""
    if scope.span is None:
        return False
    if scope.season is not None:
        raise Unsupported(f"leaderboard cannot tell whether a career span with {scope.season} named means that season, since it, or through it")
    return True


def _leaderboard_refuse_a_player(scope: Scope) -> None:
    """A refusal for one named player: a leaderboard ranks the league or a
    team, never one person ("Klay Thompson's 3pt percentage over the past 4
    seasons" came back with the league's true-shooting leaders, Klay
    silently dropped). A position group, which the game-level ranking reads
    (F056), is the planner's (:func:`leaderboard_reads`), and was checked
    first, as the template had it."""
    if scope.player is not None and scope.player.strip():
        raise Unsupported(f"a leaderboard cannot answer about one named player ({scope.player!r})")


def _leaderboard_fields(scope: Scope, metric: str) -> list[str]:
    """The extra columns a ranking was asked to show beside its metric - a
    box-score average (:data:`~association.query.metrics.EXTRA_FIELD_COLUMNS`)
    or ``"team"`` (F017, ISSUES.md). An unknown field is refused rather than
    dropped ("top 10 in NetPoints ALONGSIDE their points per game" was once
    answered without the second half, silently); a repeated one is the
    router's slip and is said once, and one restating the ranked metric is
    left out (it rendered the same 33.5 twice under two headings)."""
    requested = scope.fields
    unknown = [f for f in requested if f not in EXTRA_FIELD_COLUMNS and f != "team"]
    if unknown:
        raise Unsupported(f"unknown leaderboard field(s) {unknown}")
    return [f for f in dict.fromkeys(requested) if metric != f"avg_{f}"]


def _leaderboard_metric(scope: Scope, career: bool) -> str | None:
    """The metric the ranking reads (a unit it has no form of declined), or ``None`` where no season-line metric reads the stat (the
    game-level ranking reads it instead). ``rate`` "total" ranks a season
    total: ``stat`` names a category, never which of its two readings ("most
    points this season" is a total, "leads in points" a per-game rate)."""
    metric = resolve_metric(scope.stat, career=career)
    if metric is None:
        return None
    if scope.rate == "total":
        return SEASON_TOTAL_OF.get(metric, metric)
    if scope.rate is not None:
        # A unit the metric has no form of is the point reader's refusal
        # (the ``ranking_unit`` Cause, said by the planner) before the
        # point is planned; read here it is declined, never ranked as
        # another unit.
        raise Unsupported(f"leaderboard has no {scope.rate!r} form of {metric}")
    return metric


def read_leaderboard(con: duckdb.DuckDBPyConnection, q: Query, *, stated: frozenset[str]) -> Result | Unanswered | None:
    """``leaderboard``'s own point - the league's (or a team's players')
    leaders by one season-line metric, over a season or a career - read into
    a Result through the season line's door
    (:func:`~association.query.season_line.rank_season_line`). ``None`` where
    the point is not that, carries a narrowing the ranking's words do not
    state (``stated``: ``compose.plan.STATED_SCOPING``'s set), names a stat
    no season-line metric reads, or ranks a position group: the game-level
    ranking answers those, as it did behind the retired template's refusal
    (planned so: :func:`leaderboard_reads`). A :class:`~association.query.result.Refusal` back is the ranking's own
    refusal (a shot-distance ranking); a
    ``Unsupported`` the relation's decline (an unknown field, an
    ambiguous team, a career list with columns or a franchise's).

    .. versionadded:: 5.0.0
    """
    if not leaderboard_reads(q, stated):
        return None
    scope = q.scope
    if scope.stat == "shot_distance":
        # router._route_leaderboard_shot_distance's sentinel, checked before
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
    try:
        ranking = rank_season_line(
            con, metric, career=career, season=scope.season, season_type=scope.season_type or 2, team=scope.team, fields=fields, limit=_clamp_limit(scope.limit, default=DEFAULT_LEADERBOARD_LIMIT)
        )
    except LeaderboardError as exc:
        # An ambiguous team, an unknown metric, a team column with no season
        # type to look it up by, or a table that needs a warehouse flag - all
        # reasons to decline, never to guess.
        raise Unsupported(str(exc)) from exc
    found = ranking.result
    minimum = found.min_sample_applied
    decisions: tuple[Decided, ...] = ()
    if minimum is not None:
        unit = MIN_SAMPLE_LABELS.get(found.min_sample_column or "", found.min_sample_column or "")
        decisions = (Decided(kind="minimum", field="minimum", chose=minimum, facts={"of": unit, "column": found.min_sample_column}),)
    ratio = LEADERBOARD_METRICS[metric].ratio
    facts = RankingFacts(label=found.label, ratio=tuple(ratio) if ratio else None, fields=tuple(fields))
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
        if scope.season is None:
            # The season was the reader's choice (the latest on record), not
            # the question's: recorded as the decision it is, said by the
            # span the heading names anyway (Jeff, 2026-10-05: the default is
            # visible there and a season named reaches the alternative).
            decisions = (*decisions, Decided(kind="season_default", field="season", chose=found.season, why="latest_on_record", facts={"season_type": found.season_type or 2}))
    return Result(subject="every player", relation="everyone", span=span, parts=(Part(body=ranking.body),), notes=notes, decisions=decisions, facts=facts)


def _leaderboard_career_refusals(scope: Scope, fields: list[str]) -> None:
    """What a career ranking declines: columns beside the metric, and a
    franchise's list - which sums the per-team rows by team, and where a
    franchise moved, which years are the franchise's is a question of its
    own. Declined until that is decided, rather than answered with the
    league's list under the team's name."""
    if fields:
        raise Unsupported("a career leaderboard cannot add per-game columns")
    if scope.team is not None and scope.team.strip():
        raise Unsupported("franchise career leaderboards are not supported")
