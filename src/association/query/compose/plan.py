"""The planner: a :class:`~association.query.reading.Reading` as the
compiler's point on a relation - :class:`~association.query.compose.core.Query`
over the player-games relation (one player, or everyone), or
:class:`~association.query.compose.team.TeamQuery` over the team-games
relation. A copy, not a decision: nothing here reads the question, and a
field the Reading did not settle is not settled here either.

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from association.query.measures import PERIOD_COLUMNS, stat_measure
from association.query.metrics import LEADERBOARD_METRICS
from association.query.point import TEAM_SEASON_POINTS
from association.query.reading import Cause, Reading, Scope, _career_scope, ordinal_word
from association.query.season_line import SEASON_TOTAL_OF
from association.query.templates.common import RELATION_SCOPING_EXCLUDED, STAT_LABELS, TemplateResult, TemplateUnsupported, check_coverage, unhonored_scoping
from association.query.templates.players import leaderboard_shot_distance_refusal
from association.query.templates.splits import _condition_needs_player_refusal

from .core import Query, Refused, Unsupported, _check_relation_scoping
from .rankings import leaderboard_reads
from .seasons import player_compare_reads, player_history_reads, player_line_reads
from .team import TeamQuery
from .team_stats import TeamSeasonQuery, team_season_declines

WITH_WITHOUT_STATED: frozenset[str] = frozenset({"span", "without", "opponent", "conditions"})
"""The scoping ``with_without``'s words state - a career, the teammates
divided by, one opponent (both rows narrow together, #163) and a
companion's role - the retired template's own declaration; any other
narrowing is declined by name (:func:`_shape_declines`), and the
presenter's words state the same (``compose.present.STATED_SCOPING``).

In the planner, which declines by it, since the last adapter
(``compose.adapt``, where it lived) was deleted.

.. versionadded:: 5.0.0
"""

#: The player-relation cells a team's log, splits and run refuse by name
#: with a sentence of their own (``templates.games._team_game_log_refusals``,
#: ``templates.splits.team_splits``, ``point._default_streak``): let
#: through here so that sentence, which says where the question belongs, is
#: the refusal. Anything else a team's games do not carry is refused here.
_TEAM_READER_REFUSES: frozenset[str] = frozenset({"without", "below", "above", "season_n", "conditions"})


def _team_shape_cells(reading: Reading) -> frozenset[str]:
    """What a team point's reader takes beyond the team relation's own
    cells: the with/without split reads the teammates it divides by, a
    splits table its category, a sum the unit it is asked in (which its
    mover refuses or reads), and the log, the splits and the run refuse a
    handful of player cells with their own sentence."""
    if reading.group == "presence":
        return frozenset({"without", "conditions"})
    if reading.shape == "grouped":
        return _TEAM_READER_REFUSES | {"split"}
    if reading.shape in ("rows", "run") or reading.aggregate == "record":
        # The log, the run and a record over a line (record_when's team
        # reader) each refuse these by name.
        return _TEAM_READER_REFUSES
    return frozenset({"rate"})


def _streak_league_cells(scope: Scope) -> None:
    """Raise if a league-wide streak (nobody named at all) set ``opponent`` or
    ``venue`` - cells only a named team's or player's games can be narrowed by.
    A league-wide streak has no single subject for either to narrow against,
    unlike a team's run (on the team relation) or a player's (the relation
    reads both). ``templates.splits._streak_league_needs_named_subject`` was
    this, the retired template's refusal."""
    claimed = sorted(cell for cell in ("opponent", "venue") if getattr(scope, cell))
    if claimed:
        raise TemplateUnsupported(f"streak cannot honor {claimed} without a named team or player - the league-wide streak has no single subject to narrow")


def _shape_declines(point: Reading) -> str | None:
    """A cell the point's own reader cannot honor beyond the relation's
    cells - why the planner declines the point, or None. The comparison
    over the season line honors no narrowing at all ("compare curry and
    lebron vs the celtics" answered for the whole season would be the
    substitution ``check_scope`` exists to stop); the with/without split
    only what its words state; a quarter's split, a run and two players'
    meetings each refuse the cells their retired template excluded, with
    that template's reason. Until 5.0.0's last change the point reader
    raised these itself while reading, so what was read depended on what
    would answer (``ROADMAP.md``, Phase 1, the ``read_point`` move, step 3).
    """

    intent, scope = point.intent, point.scope
    if intent == "player_compare":
        ignored = unhonored_scoping(intent, scope, frozenset())
        return f"player_compare cannot honor {ignored} - it would answer for a different span than was asked" if ignored else None
    if intent == "with_without":
        ignored = unhonored_scoping(intent, scope, WITH_WITHOUT_STATED)
        return f"with_without cannot honor {ignored} - it would answer for a different span than was asked" if ignored else None
    if intent == "streak" and point.relation != "player":
        # A team's or the league's run: the cells only a named player's
        # games can be narrowed by, and game_n (one numbered game of each
        # series is not a run of CONSECUTIVE games); a league-wide run has
        # no single subject for an opponent or a venue to narrow against.
        # The retired template's two refusals, in the planner since
        # 2026-10-03 (the Phase 1 review found them still in the adapter).
        try:
            _condition_needs_player_refusal(intent, scope, "game_n")
            if not (scope.team and scope.team.strip()):
                # The league's run (a win streak reads the team relation
                # with no team named; a stat's run reads everyone).
                _streak_league_cells(scope)
        except TemplateUnsupported as exc:
            return str(exc)
    if intent in ("period_split", "streak", "player_matchup"):
        excluded = RELATION_SCOPING_EXCLUDED[intent]
        # A period condition is excluded from period_split's presenter's
        # WORDS only: the point keeps it, and the compiler's own sentence
        # names both quarters.
        refused = [slot for slot in excluded if (intent != "period_split" or slot != "period_condition") and getattr(scope, slot) not in (None, "", (), False)]
        return f"{intent} cannot honor {refused} - {excluded[refused[0]]}" if refused else None
    return None


def plan(reading: Reading) -> Query | TeamQuery | TeamSeasonQuery:
    """The point ``reading`` names, on the relation it names - see
    :func:`_plan`. A team-season intent's point on another relation (a
    team's own total, "how many 3-pointers have the Magic made") that the
    relation declines is that intent's own team-season point instead, or
    the team-season reader's refusal of the narrowing
    (:func:`~association.query.compose.team_stats.team_season_declines`): the retired
    template was tried before the compiler, so the compiler declining
    never decided such a question (Phase 2, step 4).

    .. versionadded:: 5.0.0

    .. versionchanged:: 5.0.0
       Plans a team-season point (:data:`~association.query.point.TEAM_SEASON_POINTS`)
       as a :class:`~association.query.compose.team_stats.TeamSeasonQuery`.
    """
    try:
        return _plan(reading)
    except Unsupported as exc:
        if reading.intent not in TEAM_SEASON_POINTS:
            raise
        # At call time: compose.present imports this module (WITH_WITHOUT_STATED).
        from .present import STATED_SCOPING

        declined = team_season_declines(reading.intent, reading.scope, STATED_SCOPING[reading.intent])
        if declined is not None:
            raise Unsupported(declined) from exc
        relation, shape = TEAM_SEASON_POINTS[reading.intent]
        return TeamSeasonQuery(scope=reading.scope, relation=relation, shape=shape)


def _plan(reading: Reading) -> Query | TeamQuery | TeamSeasonQuery:
    """The point ``reading`` names, on the relation it names - or
    :class:`~association.query.compose.core.Unsupported` where that relation
    cannot honor a narrowing the scope carries (``round``, ``rate``, a
    ``situation`` naming no calendar): the planner's own refusal, the rule
    ``check_scope`` applies for a template, applied for the relation
    (:func:`~association.query.compose.core._check_relation_scoping`).
    The answering loop plans a question's point once, through
    :func:`plan_point`.

    .. versionadded:: 5.0.0

    .. versionchanged:: 5.0.0
       Refuses a narrowing the relation cannot honor (ROADMAP plan item 6,
       step (f)); the compiler's own compile step had, one call later.
    """
    declined = _shape_declines(reading)
    if declined is not None:
        raise Unsupported(declined)
    if reading.relation in ("team_seasons", "team_snapshots"):
        # A team's own season: its reader holds the question to the
        # narrowings its words state (team_season_declines), in the retired
        # template's order - before the coverage floor.
        return TeamSeasonQuery(scope=reading.scope, relation=reading.relation, shape="grouped" if reading.shape == "grouped" else "scalar")
    if reading.relation == "team":
        # Every team shape, the sums included, against the TEAM relation's
        # own cells and what this shape's reader takes beside them.
        _check_relation_scoping(reading.scope, "team", _team_shape_cells(reading))
        return TeamQuery(scope=reading.scope, measure=reading.measures[0], aggregate=reading.aggregate, shape=reading.shape, group=reading.group)
    subject = "everyone" if reading.relation == "everyone" else "player"
    # The season line's ranking (leaderboard's retired reader) honors `rate`
    # - a season total, or a unit refused by name - which no game-level read
    # does; a point its reader declines is refused with it (_game_level).
    _check_relation_scoping(reading.scope, subject, frozenset({"rate"}) if reading.source == "seasons" and subject == "everyone" else frozenset())
    planned = Query(
        scope=reading.scope,
        skeleton=reading.shape,
        measures=list(reading.measures),
        aggregate=reading.aggregate,
        group=reading.group,
        predicates=list(reading.predicates),
        order=reading.order,
        direction=reading.direction,
        limit=reading.limit,
        offset=reading.offset,
        minimum_games=reading.minimum_games,
        available=reading.available,
        span=reading.span,
        season=reading.season,
        source=reading.source,
        subject=subject,
        position=reading.position,
    )
    if planned.source == "seasons" and not _season_line_reads(reading.intent, planned):
        return _game_level(reading.intent, planned)
    return planned


#: The season line's readers' own checks, by intent: whether the reader
#: reads a point on the line (``compose.rankings``, ``compose.seasons``),
#: from the point alone.
_SEASON_LINE_READS = {
    "leaderboard": leaderboard_reads,
    "player_stat": player_line_reads,
    "player_history": player_history_reads,
    "player_compare": player_compare_reads,
}


def _season_line_reads(intent: str | None, q: Query) -> bool:
    """Whether the season line's reader for ``intent`` reads ``q`` - the
    relation saying for itself whether it reads a point, with the scoping
    its words state (``compose.present.STATED_SCOPING``)."""
    # At call time: compose.present imports this module (WITH_WITHOUT_STATED).
    from .present import STATED_SCOPING

    reads = _SEASON_LINE_READS.get(intent or "")
    return reads is not None and reads(q, STATED_SCOPING[intent or ""])


def _game_level(intent: str | None, q: Query) -> Query:
    """A season-line point (``source="seasons"``) its reader does not read,
    as the game-level relation reads it: a per-season history becomes his
    career's games grouped by season (the reading the compiler gave every
    history before the season line was a source); a ranking is the
    game-level ranking, exactly as it answered behind the retired
    template's refusal - except for what it cannot say: a stat this
    relation has no measure for is refused by name rather than ranked as
    points, and a ``rate`` only the season line reads is not dropped. An
    unnarrowed ``player_stat`` has no game-level reading that answers the
    same question, so it is declined. The season's coverage floor is
    checked first, as the answering loop checked the season-line point
    before it re-planned it (until Phase 2, step 3, ``games_reading``, in
    ``compose.answer``).
    """
    refusal = check_coverage(intent or "", q.scope)
    if refusal is not None:
        raise Refused(TemplateResult(data={"message": refusal, "season": q.scope.season}, answer=refusal))
    if q.group == "season":
        return replace(q, scope=_career_scope(q.scope), source="games")
    if q.group == "player" and q.subject == "everyone":
        stat = q.scope.stat
        if stat is not None and stat.strip() and stat_measure(stat) is None:
            raise Refused(no_ranking_for(stat))
        if q.scope.rate:
            raise Unsupported("the relation cannot honor ['rate'] - it would answer for a different span than was asked")
        if q.scope.fields:
            raise Unsupported("the game-level ranking shows no columns beside its measure")
        return replace(q, source="games")
    raise Unsupported("an unnarrowed player line the season line's reader did not say")


@dataclass(frozen=True)
class Planned:
    """What planning one question's Reading came to: the ``query`` on its
    relation, or why there is none - ``declined`` (no reading of the point,
    or a narrowing the relation cannot honor: the caller refuses, naming
    the reason) or ``refusal`` (an answer of its own to give: a stat nothing
    ranks, a floor no ranking applies).

    .. versionadded:: 5.0.0
    """

    query: Query | TeamQuery | TeamSeasonQuery | None = None
    declined: str | None = None
    refusal: TemplateResult | None = None


def no_ranking_for(stat: str) -> TemplateResult:
    """The refusal for a ranking by a stat this relation has no measure for.

    .. versionadded:: 5.0.0
    """
    message = f"No ranking reads {stat!r} on the player-games relation - it only ranks the box-score measures it knows, not a NetPoints or other outside figure."
    return TemplateResult(data={"message": message, "stat": stat}, answer=message)


def _ranking_floor_unit(unit: str, count: int) -> TemplateResult:
    """The refusal for a ranking floor in a unit no ranking applies (F056:
    "... with at least 100 attempts"). The sentence names the floor that IS
    applied, so the question can be re-asked with it."""
    message = f"A minimum of {count} {unit} is not a floor this ranking can apply yet - only a minimum number of games is. Ask with 'at least N games', or without the floor."
    return TemplateResult(data={"message": message, "floor": {"unit": unit, "count": count}}, answer=message)


def _ranking_unit(metric: str, rate: Any) -> TemplateResult:
    """The refusal for a ranking in a unit the metric has no form of,
    naming the forms THIS metric has ("who were the top 10 in defensive
    netpoints / 90": per 90 minutes is a football unit, and nothing in the
    warehouse is stored in it). Only the three NetPoints metrics have a
    per-100 sibling, so a generic list of units would be the
    refusal-with-the-wrong-cause shape. The retired leaderboard template's
    sentence, word for word (``compose.rankings._leaderboard_no_such_rate``
    until the point reader carried the cause)."""
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


#: How each shape over a line is named in its refusal, by the intent the
#: cause carries.
_LINE_SHAPES: dict[str, str] = {
    "single_game_high": "a single-game high",
    "record_when": "a record in the games over a line",
    "threshold_count": "a count of games over a line",
    "streak": "a streak",
    "game_log": "keeping only the games past a number",
}

#: What the stat is for, in each shape's refusal.
_STAT_FOR: dict[str, str] = {
    "single_game_high": "to rank games by",
    "record_when": "the line is on",
    "threshold_count": "the line is on",
    "streak": "each game has to reach",
    "game_log": "the games have to reach it in",
}


#: A shape over a line by the number its games reach, for a number read
#: with no stat.
_REACHING: dict[str, str] = {
    "game_log": "Keeping only the games past {}",
    "streak": "A streak of games reaching {}",
    "record_when": "A record in the games reaching {}",
    "threshold_count": "A count of games reaching {}",
}


def _stat_words(stat: Any) -> str:
    """A stat column as the answer names it, plural: "points", "3-pointers"."""
    label = STAT_LABELS.get(stat)
    return f"{label}s" if label else str(stat)


def _cause_sentence(kind: str, facts: Any) -> str | None:
    """The sentence each cause of a shape over a line - and the other
    declines slices (i) and (ii) gave a user as "Nothing here answers this
    question" (Phase 2, step 3) - is said with, naming the fact that is
    missing; ``None`` for a kind said elsewhere."""
    shape = _LINE_SHAPES.get(facts.get("intent", ""), "this answer")
    sentences = {
        "needs_stat": lambda: f"{shape.capitalize()} needs a stat {_STAT_FOR.get(facts.get('intent', ''), 'to read')}, and none was read.",
        "unknown_stat": lambda: _unknown_stat(facts.get("intent", ""), facts["stat"], shape),
        "needs_threshold": lambda: f"{shape.capitalize()} needs the number of {_stat_words(facts['stat'])} each game has to reach, and none was read.",
        "threshold_needs_stat": lambda: f"{_REACHING.get(facts.get('intent', ''), 'Games reaching {}').format(facts['threshold'])} needs the stat they reach it in, and none was read.",
        "threshold_counts_every_game": lambda: f"A threshold of {facts['threshold']} counts every game - there is no line there to keep games past.",
        "line_names_no_stat": lambda: f"{facts['phrase']!r} names no box-score stat a game can be kept {facts['side']}.",
        "needs_line": lambda: "A count of games across the league needs the line it counts - a stat and a number, as in '40-point games' - and none could be read from the question.",
        "career_place_needs_player": lambda: f"The {ordinal_word(facts['season_n'])} season is a place in one player's career, and no player was named.",
        "needs_subject": lambda: f"{shape.capitalize()} needs a player or a team to read it for, and neither was named.",
        "team_streak_of_stat": lambda: f"A team's streak is of wins or losses - a run of games reaching a number of {_stat_words(facts['stat'])} is read for a player, not a team.",
        "matchup_needs_two": lambda: _matchup_needs_two(list(facts["names"])),
        "no_coach_table": lambda: COACH_REFUSAL,
        "no_period_stat": lambda: (
            f"A quarter or half has no per-period {facts['stat']!r} - the period's line rebuilds {', '.join(PERIOD_COLUMNS)} from the plays, "
            "and a field goal, 3-point or free throw percentage is a ratio of those; nothing else."
        ),
    }
    say = sentences.get(kind)
    return say() if say is not None else None


#: What a coach question is answered with, and why it is a refusal naming the
#: source rather than one naming only the intent.
#:
#: No table here holds a coach - 20 base tables and 6 views, zero columns named
#: anything like it - so a reader has nothing to find. Left to fall through,
#: the retired SQL agent spent a slow round trip and was then free to fill the
#: silence from its own weights, which is the failure ``check_coverage`` exists
#: to stop: an agent with nothing to read writes a confident answer. So the
#: reading refuses (``point._read_point``: the ``no_coach_table`` cause), and
#: this names which fact is missing.
#:
#: The sentence says what it says because the obvious reading - "ESPN does not
#: publish coaches" - was checked on 2026-09-17 and is false. ESPN serves two
#: coach collections, and neither is usable: the league-wide one ignores the
#: season it is asked for (1977 answers with today's staff, Doug Christie and
#: JJ Redick among them), and the team-scoped one covers 12 of 30 teams in
#: 1996, never names two coaches for a team-season - so no mid-season change
#: exists in it - and is wrong about Detroit for every season sampled from
#: 1994 to 2026. Telling somebody the source has no coaches would be the
#: wrong-cause refusal this project keeps producing; telling them it has an
#: unusable one is true. See DATA.md, "ESPN publishes coaches, and the
#: collection that looks league-wide is not historical".
COACH_REFUSAL: str = (
    "No table here holds a coach, so nothing about one can be answered - not a record, not a tenure, not a game. "
    "ESPN does publish coaches, but not in a form worth storing: the season-by-season list it serves ignores the season asked for and returns the current staff, "
    "and its per-team list covers 12 of 30 teams in 1996, never shows a mid-season change, and names the wrong coach for some franchises outright. "
    "Player and team questions are unaffected."
)
"""The sentence a coach question is answered with. See above.

.. versionadded:: 4.0.0

.. versionchanged:: 5.0.0
   Moved from ``templates.teams``, with the ``coach`` template it was the
   whole answer of: the reading refuses by the ``no_coach_table`` cause,
   and :func:`refusal_result` says it.
"""


def _unknown_stat(intent: str, stat: Any, shape: str) -> str:
    """The refusal for a stat with no per-game column, in the shape's words."""
    if intent == "single_game_high":
        return f"A single-game high cannot rank games by {stat!r} - only by a box-score stat each game has a number for."
    if intent == "game_log":
        return f"A game log has no per-game column for {stat!r}."
    return f"{shape.capitalize()} cannot be read over {stat!r} - it has no per-game box-score column."


def _matchup_needs_two(names: list[str]) -> str:
    """A matchup's refusal for the count of players read: none, one, or more than two."""
    if not names:
        return "A matchup needs two players, and none was read."
    if len(names) == 1:
        return f"A matchup needs two players, and only {names[0]} was read."
    return f"A matchup is between two players, and {len(names)} were read: {', '.join(names)}."


def refusal_result(cause: Cause) -> TemplateResult:
    """The refusal a point reading's :class:`~association.query.reading.Cause`
    is said with: the sentence and the template-shaped data the answering
    loop hands on, one per kind in :data:`~association.query.reading.CAUSES`.
    The reader carries the cause and never the sentence (``ROADMAP.md``,
    Phase 1, the ``read_point`` move, step 4).

    .. versionadded:: 5.0.0
    """
    if cause.kind == "shot_distance_ranking":
        # The retired leaderboard template's own refusal, naming the real
        # cause (ISSUES.md #114).
        return leaderboard_shot_distance_refusal()
    if cause.kind == "no_ranking_measure":
        return no_ranking_for(cause.facts["stat"])
    if cause.kind == "ranking_floor_unit":
        return _ranking_floor_unit(cause.facts["unit"], cause.facts["count"])
    if cause.kind == "ranking_unit":
        return _ranking_unit(cause.facts["metric"], cause.facts["rate"])
    message = _cause_sentence(cause.kind, cause.facts)
    if message is None:
        raise ValueError(f"no sentence for the cause {cause.kind!r}")
    return TemplateResult(data={"message": message, **cause.facts}, answer=message)


def plan_point(reading: Reading) -> Planned:
    """The PLAN stage for one question (``ROADMAP.md``, Phase 1): the point
    the parser read (:attr:`Reading.point <association.query.reading.Reading.point>`)
    planned onto its relation, once, by the answering loop - never by the
    parser, which only reads. The parser's own verdict stands where it read
    no point (:attr:`~association.query.reading.Reading.point_declined`,
    :attr:`~association.query.reading.Reading.point_refusal`); the planner's
    refusal of a narrowing the relation cannot honor is a decline, with
    its reason.

    .. versionadded:: 5.0.0
    """
    if reading.point_refusal is not None:
        return Planned(refusal=refusal_result(reading.point_refusal))
    if reading.point is None:
        return Planned(declined=reading.point_declined or "the compiler has no reading of this point")
    try:
        return Planned(query=plan(reading.point))
    except Unsupported as exc:
        return Planned(declined=str(exc))
    except Refused as exc:
        return Planned(refusal=exc.result)
