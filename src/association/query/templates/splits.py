"""Games under a condition: a player's splits, with or without a teammate, a record when a stat line is reached, and streaks.

.. versionadded:: 3.0.0
   Split out of the former ``association.query.templates`` module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import duckdb

from association.nba.franchises import season_name
from association.query.reading import SPLIT_KINDS as SPLIT_KINDS
from association.query.reading import Scope
from association.query.subject import with_without_named

from ..conditions import (
    _PLAYER_GAME_TABLES,
    _cell,
    _margin,
    _names,
    _overlaps,
    _Scope,
    _stints,
    _table,
    _totals,
    _win_pct,
    _with_without_games,
    _with_without_group,
)
from ..entities import Entity
from ..notes import note
from ..team_games import TEAM_GAMES_SQL, TeamNarrowed
from ..team_games import aggregate_sql as team_aggregate_sql
from ..team_games import games_subquery as team_games_subquery
from ..team_games import named as team_named
from .common import (
    _BOX_SCORES,
    STAT_LABELS,
    THRESHOLD_STAT_COLUMNS,
    TemplateResult,
    TemplateUnsupported,
    _career_end,
    _condition_scope,
    _joined,
    _optional_team,
    _period,
    _resolved_player,
    _Span,
    _span_of,
    _team_span_clause,
    ordinal_word,
    team_games,
    whole_span,
)


def _season_of_day(day: Any) -> int:
    """The season a calendar day falls in: season Y runs from October of Y-1."""
    return day.year + 1 if day.month >= 10 else day.year


def condition_span_label(covered: _Scope, scope: Scope, first: Any, last: Any) -> str:
    """The season(s) a condition answer covers, in words: "since 2022
    (2022-2026 regular seasons)" or "in his 18th season (2021 regular
    season)" when the question named it that way - the same phrasing
    ``_Span.during`` gives ``game_log`` and ``player_stat`` (``_Span.since``,
    ``_Span.ordinal``).

    record_when and streak build their heading off the relation's own
    ``_Scope`` (``covered``) rather than the ``_Span`` ``condition_player``
    resolves internally (its docstring: "the one place the two readers of a
    player's games disagreed, and not this refactor's to settle"), so the
    phrase is composed here from the question's own ``since`` and
    ``season_n`` instead. ``first``/``last`` already carry the real
    narrowing - ``covered`` has its own ``season`` forced to None wherever
    ``since`` or ``season_n`` apply (the same "career"-shaped outer scope
    ``scoped_player`` builds internally for ``season_n``), so
    :meth:`_Scope.label` reads the actual seasons the narrowed games came
    from rather than defaulting to "now".

    .. versionchanged:: 5.0.0
       Says "from X through Y" once ``scope.until`` bounds the other end too,
       matching :meth:`_Span.during`'s wording (see :func:`team_span_label`,
       fixed the same way) - before this, "from 2019-20 to 2021-22" was
       labeled "since 2019 (...)", with the range's real upper bound nowhere
       in the sentence (ISSUES.md).
    """
    label = covered.label(first, last)
    if scope.since:
        return f"from {scope.since} through {scope.until} ({label})" if scope.until else f"since {scope.since} ({label})"
    if scope.season_n:
        return f"in his {ordinal_word(scope.season_n)} season ({label})"
    return label


# The relation's cells record_when's team branch and streak's team/league
# branches cannot honor: each needs a named PLAYER to settle a teammate's
# absence, a starter/bench half, or a line on a box-score column against -
# `condition_player` is what reads all of them, and neither branch calls it.
# `HONORED_SCOPING` claims the whole relation for both intents regardless of
# branch (the same declaration the player branch needs), so a team-only or
# league-wide question setting one of these would otherwise be silently
# answered as though it had been applied. Refusing here, by name, is the same
# discipline `_player_splits_team` already applies to a bare `starter_bench`
# split.
#
# `opponent` and `venue` are NOT here (step 3, C4): both branches now read the
# team-games relation through `common.team_games`, the same shared narrowing
# `team_record` reads, so a team's own opponent/venue narrowing is a real,
# implemented shape rather than a refusal. `since` is NOT here either (step 3,
# C4b): `_span_of`'s own `since` branch already exists and both team branches
# call it for the ordinary span, so honoring it needed no new mechanism (see
# ISSUES.md, "record_when's team branch and streak's team/league branches
# still refuse ..." - rewritten to match). `streak`'s LEAGUE branch (no team
# named either) still cannot narrow to a single opponent or venue - a
# league-wide streak has no one team's home/road split or rival to read - so
# the planner refuses those two there specifically (`compose.plan._streak_league_cells`).
#
# `game_n` is honored for `record_when`'s team branch (a threshold record can
# meaningfully be narrowed to one game of each series - "Celtics record when
# they scored 120+, game 4 of the series") but stays refused for `streak`'s
# team and league branches, passed as `extra` at each of those two call sites:
# a streak is a run of CONSECUTIVE games, and the games "game 4 of each
# series" picks out are not consecutive to each other - a streak over them
# would silently answer a run over a scattered, non-adjacent subset rather
# than the real games in between.
#
# `conditions` is here (added 5.0.0, ISSUES.md) for the same reason as
# `without`/`split`/`below`/`above`: a companion's role - he started, came off
# the bench, or reached a line - is a fact about a named PLAYER's game, and a
# team or league branch has no such player settled to check it against.
# Silently dropping it answered a team's or the league's whole span as though
# "76ers record when they score 120 when embiid starts" had named no
# condition at all.
_CONDITION_PLAYER_ONLY_CELLS: tuple[str, ...] = ("without", "split", "season_n", "below", "above", "conditions")


def _condition_needs_player_refusal(intent: str, scope: Scope, *extra: str) -> None:
    """Raise if a team-only or league-wide question set a relation cell that
    needs a named player to honor - see :data:`_CONDITION_PLAYER_ONLY_CELLS`.
    ``extra`` adds cells refused for this call site only (see ``streak``'s own
    call, which passes ``"game_n"``).

    .. versionchanged:: 4.4.0
       Takes ``*extra`` (step 3, C4b), so the two intents' team branches no
       longer have to agree on exactly the same refused set.
    """
    claimed = sorted(cell for cell in (*_CONDITION_PLAYER_ONLY_CELLS, *extra) if getattr(scope, cell))
    if claimed:
        raise TemplateUnsupported(f"{intent} cannot honor {claimed} without a named player - only his own games can be narrowed that way")


def team_span_label(span: _Span, first: Any = None, last: Any = None) -> str:
    """The team span in words - :meth:`conditions._Scope.label`'s shape, over
    a :class:`~association.query.templates.common._Span` instead: one season,
    a since-bounded range, or the seasons the rows actually came from. Shared
    by :func:`_player_splits_team`, :func:`_record_when_team_answer` and
    :func:`_streak_team`, the same way :func:`condition_span_label` is shared
    by the player branches - and, like that function, has to check ``since``
    itself rather than merely a career's own ``first``/``last``: a since-bounded
    span is career-SHAPED (``span.season`` is None the same way a career's is),
    so without this a "since 2022" record read exactly like "every season on
    record" once its first and last rows were known.

    .. versionchanged:: 4.4.0
       Reads ``span.since`` (step 3, C4b) - before this, a since-bounded team
       span rendered the same label as a plain career one, with nothing
       saying the question had named a starting year at all.

    .. versionchanged:: 5.0.0
       Says "from X through Y" once ``span.until`` bounds the other end too -
       :meth:`_Span.during`'s own wording. Before this, a team's own record
       "from 2019-20 to 2021-22" was labeled "since 2020 (2020-2026 ...)",
       the range's real upper bound nowhere in the sentence, because
       ``span.until`` never reached here at all (ISSUES.md).
    """
    if span.season is not None:
        return _period(span.season, span.season_type)
    if span.since is not None:
        years = span.years(first, last) if isinstance(first, int) and isinstance(last, int) else f"{span.kind}s"
        return f"from {span.since} through {span.until} ({years})" if span.until is not None else f"since {span.since} ({years})"
    if isinstance(first, int) and isinstance(last, int):
        return span.years(first, last)
    return f"every {span.kind} on record ({span.first} onward)"


def team_where_in(span: _Span) -> str:
    """:func:`common._where_in`'s shape, over a ``_Span``: "in the 2026
    regular season", or "in any regular season on record" for a span with
    nothing in it."""
    return f"in the {team_span_label(span)}" if span.season is not None else f"in any {span.kind} on record ({span.first} onward)"


def _team_span_floor_note(span: _Span, first: Any) -> str:
    """The box-score floor caveat over a ``_Span`` (the player branches' is the sayer's ``floor`` phrase, ``compose.say``): a team
    career's own box-score-floor caveat - not shown for a since-bounded span
    (``span.since is not None``), the same guard
    :func:`~association.query.templates.common._box_score_notes` applies for a
    player: the question named its own starting year, so a note that games
    "start with" that year would read as though the WAREHOUSE, not the
    question, put the floor there."""
    if span.season is None and span.since is None and first == span.first:
        return note("floor", f" Box scores start with the {span.first} {span.kind}; anything earlier is not counted.", table="box_scores", first=span.first, what=span.kind)
    return ""


def condition_team_no_games(con: duckdb.DuckDBPyConnection, team: Entity, span: _Span, narrowed: TeamNarrowed) -> TemplateResult:
    """Nothing to report for a team's own games under a condition template
    (:func:`_player_splits_team`, :func:`_record_when_team_answer`,
    :func:`_streak_team`) - which fact is missing, the team's games in this
    span at all or the match to an opponent/venue narrowing, the same
    discipline :func:`~association.query.templates.games._team_game_log_none`
    and :func:`common._no_games` already apply. Before ``opponent``/``venue``
    reached these three branches (step 3, C4) there was only one tier to get
    wrong; now a team with real games in a span but none against a named
    opponent gets that sentence instead of the misleading "no games in this
    span" it would have read as before opponent/venue could narrow anything
    here at all."""
    where, params = narrowed.clauses(narrowed=False)
    season_col = "year(tg.eastern_date)" if span.season_type == 3 else "tg.season"
    found = con.execute(f"{TEAM_GAMES_SQL} SELECT COUNT(*), MIN({season_col}), MAX({season_col}) FROM team_games tg WHERE {where}", params).fetchone()
    total, first, last = found if found else (0, None, None)
    label = team_span_label(span, first, last)
    if not total:
        message = f"The warehouse has no games with a result for the {team.name} {team_where_in(span)}."
        return TemplateResult(data={"team": team.name, "span": label, "games": 0}, answer=message)
    message = f"The {team.name} played {total:,} games {span.during(first, last, whose='all seasons on record')}, none of them{narrowed.filters()}."
    return TemplateResult(data={"team": team.name, "span": label, "games": 0}, answer=message)


@dataclass
class PresenceSplit:
    """A team's games inside the named teammates' time on the team, each
    marked with how many of them held their condition and the subject's
    line where he played - the team relation's ``presence`` group, read by
    :func:`_with_without_read` for the compiler's grouped shape on the team
    relation (``compose.team._compile_team_presence``) and said by
    :func:`_with_without_said`.

    .. versionadded:: 5.0.0
    """

    covered: _Scope
    team: Entity | None
    subject: Entity | None
    mates: list[Entity]
    asked_without: bool
    predicates: list[tuple[str, tuple[str, int] | None]]
    windows: list[Any]
    against: Entity | None
    games: list[dict[str, Any]]
    unknown: int
    team_names: dict[str, str]


def _with_without_read(con: duckdb.DuckDBPyConnection, scope: Scope) -> PresenceSplit | TemplateResult:
    """A team's games divided by a teammate's presence - the ``presence``
    group on the team relation, read: the teammates (``without``,
    ``with_player``, a ``conditions`` role, or the one name beside a team),
    the subject where a player is named, the team where one is, resolved
    against the box scores; the overlap of their stints on the team (only
    games inside a teammate's time on the team count - StatMuse answers
    "Nets record without KD" all-time with 439-672, decades of games before
    he arrived every one "without" him); and every game a window's team
    played inside it, marked with HOW MANY of the named teammates held
    their condition (:func:`~association.query.conditions._with_without_games`),
    narrowed to the opponent the question named, since both rows narrow
    together (#163). A ``TemplateResult`` is an answer the reading gives up
    with: which player was meant, a teammate with no box score, a time
    together outside the span.

    **A question naming two teammates is divided by both of them.** "Celtics
    record without Tatum and Brown" is the games NEITHER of them played, and
    "record when A and B play" the games both did; the games where one played
    and one sat belong to neither of those, and go on the other row
    (:func:`_with_without_played`). Only the time they were ALL on the same
    team is counted, for the same reason one teammate's tenure is.

    .. versionadded:: 5.0.0
    """
    mate_texts, asked_without, roles = with_without_named(scope)
    texts = list(dict.fromkeys(n.strip() for n in (scope.player, *scope.players) if n is not None and n.strip()))
    team = _optional_team(con, scope.team, season=scope.season)
    if isinstance(team, TemplateResult):
        return team
    if not mate_texts:
        mate_texts, texts = _with_without_infer_teammate(team, texts)

    covered = _condition_scope(scope.season, scope.span, scope.season_type, _PLAYER_GAME_TABLES)
    resolved = _with_without_resolve(con, mate_texts, texts, covered)
    if isinstance(resolved, TemplateResult):
        return resolved
    mates, subject = resolved

    named = [m.name for m in mates]
    all_of = _joined(named)
    windows = _with_without_windows(con, mates, subject, team, covered)
    if isinstance(windows, TemplateResult):
        return windows
    if not windows:
        return _with_without_empty_windows(team, subject, mates, named, all_of)

    # The opponent the question named, if any: "Embiid career record vs boston"
    # is his record in the games his team played BOSTON, not overall. Narrowing
    # is honest here because both rows narrow together - the split is still
    # played against missed, over the same pool (#163).
    against = _optional_team(con, scope.opponent, season=scope.season)
    if isinstance(against, TemplateResult):
        return against
    predicates = _with_without_predicates(con, mates, roles, covered)
    games, unknown = _with_without_games(con, covered, windows, [m.id for m in mates], subject.id if subject else None, against.id if against else None, predicates)
    team_names = _names(con, "teams", "team_id", {w.team_id for w in windows})
    if not games:
        spell_text = "; ".join(f"{_with_without_stint_team(w, team_names)} {w.first} to {w.last}" for w in windows)
        return _with_without_empty_games(subject, mates, named, all_of, covered, spell_text, unknown)
    return PresenceSplit(covered, team, subject, mates, asked_without, predicates, windows, against, games, unknown, team_names)


def _with_without_said(split: PresenceSplit) -> TemplateResult:
    """A :class:`PresenceSplit` as ``with_without``'s retired template said
    it: the team's record in the games the teammate(s) played against the
    games they missed, side by side because the comparison is the question,
    with the subject's averages in each where a player is named, and the
    notes under the table - the tenure counted, what "played" means for one
    teammate against two, the games with no box score on neither side.

    .. versionadded:: 5.0.0
    """
    named = [m.name for m in split.mates]
    all_of, any_of = _joined(named), _joined(named, "or")
    groups, rows, team_order = _with_without_rows(split.games, split.team_names, split.asked_without, len(split.mates), split.subject, all_of, any_of, verbs=_with_without_verbs(split.predicates))
    return _with_without_answer(
        split.covered, split.games, split.team_names, team_order, split.windows, split.subject, split.mates, named, all_of, split.asked_without, split.unknown, groups, rows, split.against
    )


def _with_without_predicates(con: duckdb.DuckDBPyConnection, mates: list[Entity], roles: dict[str, tuple[str, tuple[str, int] | None]], scope: _Scope) -> list[tuple[str, tuple[str, int] | None]]:
    """Each resolved teammate's predicate: the role a ``conditions`` entry
    naming him gives, else ``played``.

    .. versionadded:: 5.0.0
    """
    by_id: dict[str, tuple[str, tuple[str, int] | None]] = {}
    for text, role in roles.items():
        found = _resolved_player(con, text, available=_BOX_SCORES, season=scope.season, through=_career_end(scope.season))
        if not isinstance(found, TemplateResult):
            by_id[found.id] = role
    return [by_id.get(m.id, ("played", None)) for m in mates]


def _with_without_verbs(predicates: list[tuple[str, tuple[str, int] | None]]) -> tuple[str, str]:
    """How the two rows name their side: "played"/"out" for appearances,
    "started"/"did not start" for starts, "came off the bench"/"started or
    out" for the bench, "had 20+ points"/"did not" for a line - the words
    the question used, never "played" for a start it asked about.

    .. versionadded:: 5.0.0
    """
    kinds = {p for p, _ in predicates}
    if kinds == {"started"}:
        return "started", "did not start"
    if kinds == {"bench"}:
        return "came off the bench", "started or out"
    if kinds == {"reached"}:
        lines = [f"{line[1]}+ {STAT_LABELS.get(line[0], line[0])}s" for _, line in predicates if line is not None]
        return f"had {_joined(sorted(set(lines)))}", "did not"
    if kinds == {"played"}:
        return "played", "out"
    return "met the condition", "did not"


def _with_without_infer_teammate(team: Entity | None, texts: list[str]) -> tuple[list[str], list[str]]:
    """The teammate to divide by when the question named no "with" or
    "without": whichever name is not the subject - the only name beside a
    team, or the second of two. More than that is "record when A and B and C
    play", which this does not answer."""
    if team is not None and len(texts) == 1:
        return texts, []
    if team is None and len(texts) == 2:
        return texts[1:], texts[:1]
    raise TemplateUnsupported(f"with_without needs exactly one teammate, got {texts!r}")


def _with_without_resolve(con: duckdb.DuckDBPyConnection, mate_texts: list[str], texts: list[str], scope: _Scope) -> tuple[list[Entity], Entity | None] | TemplateResult:
    """The teammates, and the subject if one is named, resolved against the box
    scores. More than one leftover name after the teammates are matched is
    refused rather than guessed at."""
    mates: list[Entity] = []
    for text in mate_texts:
        found = _resolved_player(con, text, "with_without needs a teammate", available=_BOX_SCORES, season=scope.season, through=_career_end(scope.season))
        if isinstance(found, TemplateResult):
            return found
        if found.id not in {m.id for m in mates}:
            mates.append(found)
    subjects: list[Entity] = []
    for text in texts:
        found = _resolved_player(con, text, available=_BOX_SCORES, season=scope.season, through=_career_end(scope.season))
        if isinstance(found, TemplateResult):
            return found
        # The router often repeats a teammate in `player`; that is not a subject.
        if found.id not in {m.id for m in mates} and found.id not in {s.id for s in subjects}:
            subjects.append(found)
    if len(subjects) > 1:
        raise TemplateUnsupported(f"with_without answers for one player, got {[s.name for s in subjects]}")
    subject = subjects[0] if subjects else None
    return mates, subject


def _with_without_windows(con: duckdb.DuckDBPyConnection, mates: list[Entity], subject: Entity | None, team: Entity | None, scope: _Scope) -> list[Any] | TemplateResult:
    """The overlap of every teammate's (and, if named, the subject's) stints on
    the team - or the refusal naming whichever teammate has no box-score
    appearance to build a stint from at all."""
    named = [m.name for m in mates]
    stints = [_stints(con, m.id, scope.phantoms) for m in mates]
    absent = next((m for m, spells in zip(mates, stints, strict=True) if not spells), None)
    if absent is not None:
        # Named, rather than reported as "one of them": which player the
        # warehouse has never seen is the fact that is missing.
        message = f"{absent.name} has no box-score appearance in the warehouse, so there is no time on a team to count games in."
        return TemplateResult(data={"teammate": absent.name, "teammates": named, "groups": [], "headline": message}, answer=message)
    windows = stints[0]
    for spells in stints[1:]:
        windows = _overlaps(windows, spells)
    if subject is not None:
        windows = _overlaps(_stints(con, subject.id, scope.phantoms), windows)
    if team is not None:
        windows = [w for w in windows if w.team_id == team.id]
    return windows


def _with_without_empty_windows(team: Entity | None, subject: Entity | None, mates: list[Entity], named: list[str], all_of: str) -> TemplateResult:
    """The refusal for a subject and teammates (and, if named, a team) who were
    never on the same roster together at all, as the box scores show it."""
    on = f" the {team.name}" if team else ""
    if subject is None and len(mates) == 1:
        message = f"{all_of} never appeared in a box score for{on}, so there are no {team.name if team else ''} games with or without him to count."
    else:
        whom = _joined([subject.name, *named]) if subject is not None else all_of
        played_phrase = "he played" if len(mates) == 1 else "they all played"
        message = f"{whom} were never on{on or ' the same team'} together in the box scores on record, so there are no games to divide by whether {played_phrase}."
    return TemplateResult(data={"teammate": all_of, "teammates": named, "player": subject.name if subject else None, "groups": [], "headline": message}, answer=message)


def _with_without_stint_team(w: Any, team_names: dict[str, str]) -> str:
    """The team a stint was spent on, named as it was then. A stint across a
    rename names both - the Nets' 2012 and 2013 are one stint on one id."""
    start, end = (season_name(w.team_id, _season_of_day(day), team_names[w.team_id]) for day in (w.first, w.last))
    return start if start == end else f"{start} / {end}"


def _with_without_empty_games(subject: Entity | None, mates: list[Entity], named: list[str], all_of: str, scope: _Scope, spell_text: str, unknown: int) -> TemplateResult:
    """The refusal for a subject and teammates whose time together, as the box
    scores show it, falls outside the scope asked about - or holds games but
    none of them with a box score."""
    whose = f"{all_of}'s time" if subject is None else f"The time {_joined([subject.name, *named])} spent together"
    message = f"{whose} on the team, as the box scores show it ({spell_text}), falls outside the {scope.label() if scope.season else 'seasons on record'}."
    if unknown:
        # Inside the time, but every game of it without a box score - a
        # different fact from the time missing the season altogether.
        message = f"All {unknown} games inside {whose[0].lower() + whose[1:]} on the team in the {scope.label()} have no box score in the warehouse, so whether {all_of} played them cannot be told."
    return TemplateResult(data={"teammate": all_of, "teammates": named, "player": subject.name if subject else None, "groups": [], "headline": message}, answer=message)


def _with_without_played(game: dict[str, Any], asked_without: bool, n_mates: int) -> bool:
    """Whether a game belongs on the "played" row rather than the "out" one.

    One teammate splits the games in two and there is nothing to decide.
    Two do not: "without A and B" asks for the games NEITHER played, so a
    game one of them played belongs on the other row, while "with A and B"
    asks for the games BOTH played. Either way the question's own side is
    exact and everything else is the remainder - which is what keeps a
    two-player question from being answered about one of them.
    """
    return game["mates_played"] > 0 if asked_without else game["mates_played"] == n_mates


def _with_without_rows(
    games: list[dict[str, Any]], team_names: dict[str, str], asked_without: bool, n_mates: int, subject: Entity | None, all_of: str, any_of: str, verbs: tuple[str, str] = ("played", "out")
) -> tuple[list[dict[str, Any]], list[tuple[str, list[str]]], list[str]]:
    """The table's groups and rows, team by team and side by side - the teams in
    the order the games were actually played, so a career reads forwards."""
    # Teams in the order the games were played, so a career reads forwards.
    team_order = list(dict.fromkeys(g["team_id"] for g in sorted(games, key=lambda g: g["day"])))
    order = (False, True) if asked_without else (True, False)
    groups: list[dict[str, Any]] = []
    rows: list[tuple[str, list[str]]] = []
    for team_id in team_order:
        for played in order:
            chosen = [g for g in games if g["team_id"] == team_id and _with_without_played(g, asked_without, n_mates) == played]
            group = {"team": team_names[team_id], "teammate_played": played, **_with_without_group(chosen)}
            groups.append(group)
            cells = [str(group["games"]), f"{group['wins']}-{group['losses']}", _win_pct(group["wins"], group["games"]), _margin(group["avg_margin"])]
            if subject is not None:
                cells += [str(group["player_games"]), *(_cell(group[k]) for k in ("minutes", "points", "rebounds", "assists", "fg_pct"))]
            prefix = f"{team_names[team_id]}, " if len(team_order) > 1 else ""
            # "A and B out" against "A or B played": the row label says which
            # of the two it is, since with two names they are not opposites.
            whom = (any_of if asked_without else all_of) if played else (all_of if asked_without else any_of)
            rows.append((f"{prefix}{whom} {verbs[0] if played else verbs[1]}", cells))
    return groups, rows, team_order


def _with_without_answer(
    scope: _Scope,
    games: list[dict[str, Any]],
    team_names: dict[str, str],
    team_order: list[str],
    windows: list[Any],
    subject: Entity | None,
    mates: list[Entity],
    named: list[str],
    all_of: str,
    asked_without: bool,
    unknown: int,
    groups: list[dict[str, Any]],
    rows: list[tuple[str, list[str]]],
    against: Entity | None = None,
) -> TemplateResult:
    """The table and the notes under it: title, counted span, what "played"
    means for one teammate against two, and the caveats for games with no box
    score and, with a player subject, what the extra columns are."""
    label = scope.label(min(g["season"] for g in games), max(g["season"] for g in games))
    counted_teams = ", ".join(team_names[t] for t in team_order)
    title, headers, whose = _with_without_heading(subject, mates, named, all_of, counted_teams, label, against)
    used = [w for w in windows if any(g["team_id"] == w.team_id and w.first <= g["day"] <= w.last for g in games)]
    spell_text = "; ".join(f"{team_names[w.team_id]} {w.first} to {w.last}" if len(team_order) > 1 else f"{w.first} to {w.last}" for w in used)
    tenure = [{"team": team_names[w.team_id], "from": str(w.first), "to": str(w.last)} for w in used]
    notes = _with_without_notes(whose, spell_text, mates, asked_without, all_of, unknown, subject, tenure)
    answer = _table(title, headers, rows) + "\n" + " ".join(notes)
    data = {
        "teammate": all_of,
        "teammates": named,
        "player": subject.name if subject else None,
        "teams": [team_names[t] for t in team_order],
        "span": label,
        "groups": groups,
        "tenure": tenure,
        "headline": title.rstrip(":"),
        "notes": notes,
    }
    return TemplateResult(data=data, answer=answer)


def _with_without_heading(subject: Entity | None, mates: list[Entity], named: list[str], all_of: str, counted_teams: str, label: str, against: Entity | None = None) -> tuple[str, list[str], str]:
    """The table's title and headers, and the phrase naming whose time together
    is counted - with a player subject's own columns added to the headers.

    An opponent the question named goes in the TITLE, never only in the slots:
    a record over one opponent's games, headed as though it covered every
    game, is the silent narrowing this module exists to stop."""
    headers = ["G", "W-L", "Win%", "Margin"]
    versus = f" vs the {against.name}" if against is not None else ""
    if subject is None:
        title = f"{counted_teams} with and without {all_of}{versus}, {label}:"
        whose = f"{all_of}'s time with the team" if len(mates) == 1 else f"the time {all_of} were on the team together"
        return title, headers, whose
    title = f"{subject.name} with and without {all_of} ({counted_teams}){versus}, {label}:"
    whose = f"the time {subject.name} and {all_of} were both on the team" if len(mates) == 1 else f"the time {_joined([subject.name, *named])} were on the team together"
    headers += ["Played", "MIN", "PTS", "REB", "AST", "FG%"]
    return title, headers, whose


def _with_without_notes(whose: str, spell_text: str, mates: list[Entity], asked_without: bool, all_of: str, unknown: int, subject: Entity | None, tenure: list[dict[str, str]]) -> list[str]:
    """The caveats under the table: what "played" means for one teammate
    against two, games with no box score, and a player subject's extra
    columns. ``tenure`` is the stints ``spell_text`` spells out, as values."""
    names = [mate.name for mate in mates]
    said = f"Counted: games inside {whose} ({spell_text}), which runs from the first box score that lists {'him' if len(mates) == 1 else 'them'} there to the last."
    notes = [note("definition", said, term="tenure_counted", names=names, stints=tenure)]
    if len(mates) == 1:
        notes.append(note("definition", f"Played means {all_of} appeared in the game; out is a DNP or no box-score row at all.", term="played", names=names))
    elif asked_without:
        said = f"Out means none of {all_of} appeared in the game - a DNP or no box-score row at all; the other row is every game at least one of them played."
        notes.append(note("definition", said, term="out", names=names))
    else:
        said = f"Played means every one of {all_of} appeared in the game; the other row is every game at least one of them missed - a DNP or no box-score row at all."
        notes.append(note("definition", said, term="played", names=names))
    if unknown:
        # A game with no box score is not a game he missed - see
        # conditions._box_missing - so it is on neither side, and said so.
        said = f"{unknown} game{'' if unknown == 1 else 's'} inside that time {'has' if unknown == 1 else 'have'} no box score, so whether he played is unknown; they are on neither side."
        notes.append(note("games_unseen", said, games=unknown, why="no_box_score"))
    if subject is not None:
        said = f"G, W-L and margin are the team's; Played counts {subject.name}'s games, and his averages are over those."
        notes.append(note("definition", said, term="columns", whose=subject.name))
    return notes


def _record_when_group(by_hit: dict[bool | None, Any], hit: bool | None) -> dict[str, Any]:
    """The record in the games the threshold was reached (True), fell short of
    with a known value (False), or every game regardless of whether the value
    is known at all (None) - shared by the player branch (``compose.records``,
    since Phase 2's slice (i)) and the team branch (:func:`_record_when_team_answer_table`), whose own
    query structurally excludes a blank value from ``by_hit`` rather than
    grouping it (:func:`_record_when_team_base`), so ``None`` never appears
    there and this falls back to summing True and False alone.

    .. versionchanged:: 5.0.0
       Keys on the value's own presence (``True``/``False``/``None``) - a
       blank-stat game still has a real result, so "every game" (``hit=None``)
       now counts it too, where it used to be reachable only through the
       collision the player branch's answer fixed.
    """
    rows = [by_hit[h] for h in ((hit,) if hit is not None else (True, False, None)) if h in by_hit]
    games = sum(int(r[1]) for r in rows)
    wins = sum(int(r[2]) for r in rows)
    margin = sum((r[3] or 0) * int(r[1]) for r in rows) / games if games else None
    return {"games": games, "wins": wins, "losses": games - wins, "avg_margin": margin}


# Team-level columns record_when's team branch can read, over team_box_stats
# aliased `tbs`. Not every player stat THRESHOLD_STAT_COLUMNS whitelists has a
# team counterpart:
# - `points` is handled separately (see _record_when_team_query): it reads the
#   game's OWN score off real_games directly rather than a team_box_stats row,
#   so it needs no box row at all and is immune to the empty 2013-2018
#   Chicago/New Orleans team boxes (AGENTS.md, "Whole team-seasons of box
#   scores are empty" - measured on the 2026-09-20 warehouse, 3,610 NULL rows
#   shared by every other team_box_stats column here).
# - `rebounds` reads offensiveRebounds + defensiveRebounds, not totalRebounds -
#   the same substitution _TEAM_LINE makes and for the same reason (AGENTS.md,
#   "The team totalRebounds column stops including team rebounds in 2022").
# - `turnovers` reads `totalTurnovers`, not the bare `turnovers` column: DATA.md
#   ("The team box `turnovers` column is zero before 2013") establishes that
#   `totalTurnovers` is ESPN's right figure in every era, and that the
#   warehouse's `turnovers` is a DIFFERENT number - the player-box turnover sum,
#   repaired in at load time - so the two are not interchangeable and the
#   smaller of them is not a stricter reading of the same fact. 2018 is short
#   here: 2,134 of that regular season's rows and 146 of its postseason carry a
#   real box score with `totalTurnovers` specifically NULL (measured on the
#   2026-09-20 warehouse), on top of the empty-box seasons every other column
#   shares - caught the same way, by `_record_when_team_unseen`'s caveat count.
# - `minutes` is refused, by _record_when_team_stat: a team has no minutes total.
_RECORD_WHEN_TEAM_STAT_COLUMNS: dict[str, str] = {
    "points": "points",
    "rebounds": "tbs.offensiveRebounds + tbs.defensiveRebounds",
    "assists": "tbs.assists",
    "steals": "tbs.steals",
    "blocks": "tbs.blocks",
    "turnovers": "tbs.totalTurnovers",
    "threePointFieldGoalsMade": "tbs.threePointFieldGoalsMade",
    "fieldGoalsMade": "tbs.fieldGoalsMade",
    "freeThrowsMade": "tbs.freeThrowsMade",
    "fouls": "tbs.fouls",
}


def _record_when_team_stat(stat: str | None, threshold: int | None) -> tuple[str, int]:
    """The SQL a team's threshold reads, and the threshold narrowed to
    ``int`` - or the refusal, which names the real cause rather than
    record_when's player-only "needs a player" message (AGENTS.md, "the same
    bug has a mirror image"): an unrecognized stat or bad threshold reads the
    same as the player branch's own refusal, and a stat that is only ever a
    PLAYER's (a team has no minutes total) says so by name."""
    if threshold is None or threshold < 1:
        raise TemplateUnsupported(f"record_when needs a known stat and a positive threshold, got {stat!r}/{threshold!r}")
    if stat is None or stat not in THRESHOLD_STAT_COLUMNS:
        raise TemplateUnsupported(f"record_when needs a known stat and a positive threshold, got {stat!r}/{threshold!r}")
    column = _RECORD_WHEN_TEAM_STAT_COLUMNS.get(stat)
    if column is None:
        # The only whitelisted player stat left with no team mapping above.
        raise TemplateUnsupported(f"record_when has no team figure for {STAT_LABELS.get(stat, stat)}s - a team has no minutes total")
    return column, threshold


#: The join record_when's team branch adds to the relation for every stat but
#: `points` - the box-score columns `_RECORD_WHEN_TEAM_STAT_COLUMNS` names,
#: none of which the relation itself carries.
_TEAM_RECORD_WHEN_JOIN = " JOIN team_box_stats tbs ON tbs.event_id = tg.event_id AND tbs.season = tg.season AND tbs.team_id = tg.team_id"


def _record_when_team_unseen(con: duckdb.DuckDBPyConnection, narrowed: TeamNarrowed, column: str) -> int:
    """How many of the team's own games under ``narrowed`` have a
    team_box_stats row but no usable value for this stat - the games a
    non-points threshold cannot see (AGENTS.md, "Whole team-seasons of box
    scores are empty"). Counted over the relation with the same join
    :func:`_record_when_team_base` uses, rather than through
    conditions._box_missing, which answers a different question (no PLAYER
    appeared in the game at all, not this one team column)."""
    sql, params = team_aggregate_sql(narrowed, ["COUNT(*)"], join=f"{_TEAM_RECORD_WHEN_JOIN} AND ({column}) IS NULL")
    row = con.execute(sql, params).fetchone()
    return int(row[0]) if row else 0


def _record_when_team_unseen_note(count: int, unit: str, stat: Any) -> str:
    """The team counterpart to the player branches' ``games_unseen`` note (``compose.say``): how many of the
    team's games in this span carry no usable figure for this stat at all, so
    they sit in neither row. Not shown for `points`, which reads the game's
    own score and always has one."""
    if not count:
        return ""
    return note("stat_blank", f" {count} of their games in that span have no {unit} figure on record, so they are in neither row.", games=count, stat=stat, whose="team")


def _record_when_team_no_stat(team: Entity, span: _Span, narrowed: TeamNarrowed, stat: Any, games: int) -> TemplateResult:
    """The team played ``games`` games under this narrowing, but not one of
    them carries a usable figure for the stat at all - the empty 2013-2018
    team boxes, reached through record_when's threshold rather than a plain
    average. Distinct from :func:`condition_team_no_games`, which fires when
    there are no NARROWED games at all: this fires when there are, and every
    one of them lacks the stat."""
    unit = f"{STAT_LABELS.get(stat or '', stat or '')}s"
    which = "it" if games == 1 else "any of them"
    label = team_span_label(span)
    message = f"The warehouse has {games} game{'' if games == 1 else 's'} with a result for the {team.name}{narrowed.filters()} {team_where_in(span)}, but no {unit} figure on record for {which}."
    return TemplateResult(data={"team": team.name, "span": label, "games": 0}, answer=message)


def _record_when_team_base(narrowed: TeamNarrowed, stat: Any, column: str) -> tuple[str, dict[str, Any]]:
    """The team's own games under ``narrowed``, each with its ``stat_value`` -
    the team counterpart to the player branch's ``base``, over the
    relation instead of a hand-rolled join.

    ``points`` is read straight off the relation's own score (see
    :data:`_RECORD_WHEN_TEAM_STAT_COLUMNS`: it needs no box row at all, so it
    is immune to the empty 2013-2018 Chicago/New Orleans team boxes). Every
    other stat joins ``team_box_stats`` and excludes a game with no value
    there right in the join, the same "team's own row decides which side it
    was on" shape the old ``conditions._team_games``-based join used, since a
    home/away-only join would silently return half the games -
    :func:`_record_when_team_unseen` counts the excluded games back for the
    caveat."""
    if stat == "points":
        select, join = ["tg.event_id", "tg.season", "tg.eastern_date AS day", "tg.team_score AS stat_value", "tg.team_score", "tg.opponent_score", "tg.won"], ""
    else:
        select = ["tg.event_id", "tg.season", "tg.eastern_date AS day", f"{column} AS stat_value", "tg.team_score", "tg.opponent_score", "tg.won"]
        join = f"{_TEAM_RECORD_WHEN_JOIN} AND ({column}) IS NOT NULL"
    return team_named(*team_aggregate_sql(narrowed, select, join=join))


def _record_when_team_query(con: duckdb.DuckDBPyConnection, base: str, params: dict[str, Any], threshold: int, span: _Span) -> list[Any]:
    """The team's own games under ``base``, grouped by whether the stat
    reached ``threshold`` - the team counterpart to :func:`_record_when_query`'s
    own grouping query.

    The season range in each group's row reads the calendar year for a
    postseason, not the ``season`` label - see ``compose.team.compile_team_range``,
    whose column choice this repeats inline because it runs inside one
    grouped query rather than a separate totals read."""
    season_col = "year(t.day)" if span.season_type == 3 else "t.season"
    return con.execute(
        f"WITH t AS ({base}) SELECT t.stat_value >= $threshold, COUNT(*), COUNT(*) FILTER (WHERE t.won), AVG(t.team_score - t.opponent_score), MIN({season_col}), MAX({season_col}) FROM t GROUP BY 1",
        {**params, "threshold": threshold},
    ).fetchall()


def _record_when_team_answer_table(con: duckdb.DuckDBPyConnection, span: _Span, team: Entity, narrowed: TeamNarrowed, stat: Any, column: str, threshold: int, found: list[Any]) -> TemplateResult:
    """The two-row table for a team's own record above/below its threshold -
    the team counterpart to _record_when_answer, with no player to key on and
    a team pronoun in place of a player's. Keyed by ``bool(row[0])``, never
    ``None``: the query behind ``found`` (:func:`_record_when_team_base`)
    excludes a blank stat in its own join rather than grouping it, so there is
    no blank group here to collide with - see :func:`_record_when_group`."""
    by_hit: dict[bool | None, Any] = {bool(row[0]): row for row in found}
    unit = f"{STAT_LABELS.get(stat or '', stat or '')}s"
    reached, short, every = _record_when_group(by_hit, True), _record_when_group(by_hit, False), _record_when_group(by_hit, None)
    label = team_span_label(span, min(r[4] for r in found), max(r[5] for r in found))
    title = f"{team.name} record when they had {threshold}+ {unit}{narrowed.filters()}, {label}:"
    rows = [(f"{threshold}+ {unit}", reached), (f"under {threshold} {unit}", short), ("all their games", every)]
    table = _table(title, ["G", "W-L", "Win%", "Margin"], [(name, [str(g["games"]), f"{g['wins']}-{g['losses']}", _win_pct(g["wins"], g["games"]), _margin(g["avg_margin"])]) for name, g in rows])
    caveat = "" if stat == "points" else _record_when_team_unseen_note(_record_when_team_unseen(con, narrowed, column), unit, stat)
    pool = note("definition", f"Over the {every['games']} games with a result.", term="pool", games=every["games"], what="games_with_a_result")
    trailer = f"{pool}{_team_span_floor_note(span, min(r[4] for r in found))}{caveat}"
    answer = f"{table}\n{trailer}"
    data = {
        "team": team.name,
        "stat": stat,
        "threshold": threshold,
        "span": label,
        "reached": reached,
        "fell_short": short,
        "headline": title.rstrip(":"),
        "notes": [trailer],
    }
    return TemplateResult(data=data, answer=answer)


def _record_when_team_answer(con: duckdb.DuckDBPyConnection, scope: Scope) -> TemplateResult:
    """The team half of ``record_when``: a record above/below a threshold of
    the TEAM's own scoring or another box-score stat, reached when the
    question names no player at all ("what was the celtics record when they
    scored 120 points" - ISSUES.md #144). A question naming neither a player
    nor a team is unanswerable and refuses saying so - not with the
    player-only "record_when needs a player" message, which would name the
    wrong cause for a team question with nobody to resolve.

    .. versionchanged:: 4.4.0
       Honors ``opponent`` and ``venue`` (step 3, C4), reading the team-games
       relation through :func:`common.team_games` the way ``team_record``
       does, and says the narrowing in the heading via
       :meth:`~association.query.team_games.TeamNarrowed.filters`. A
       postseason before 1993-94 is now selected by the calendar year it was
       played in rather than ESPN's own-year label, so the pre-1994 refusal
       that used to run here no longer applies - see CHANGES.md for the moved
       cases.

    .. versionchanged:: 4.4.0
       Honors ``since`` (a team's own record over a range of seasons) and
       ``game_n`` (one game of each playoff series) for the team branch (step
       3, C4b) - both now real, answered shapes rather than the
       player-only refusal :func:`_condition_needs_player_refusal` used to
       give them (ISSUES.md).
    """
    _condition_needs_player_refusal("record_when", scope)
    team = _optional_team(con, scope.team, season=scope.season)
    if isinstance(team, TemplateResult):
        return team
    if team is None:
        raise TemplateUnsupported("record_when needs a player or a team")
    stat = scope.stat
    column, threshold = _record_when_team_stat(stat, scope.threshold)
    span = _span_of(scope.span, scope.season, scope.season_type or 2, "games", since=scope.since, until=scope.until)
    narrowed = team_games(con, team, span, scope, opponent=scope.opponent)
    if isinstance(narrowed, TemplateResult):
        return narrowed
    # A split, a record, a run: read over every game in the span (common.whole_span).
    whole_span(narrowed)
    # The narrowed pool BEFORE any stat availability is checked, so a team
    # with real games in this span/narrowing but none carrying the stat
    # (`_record_when_team_no_stat`) is told apart from a team with no games
    # matching the narrowing at all (`condition_team_no_games`).
    matched, _, _ = _totals(con, *team_games_subquery(narrowed))
    if not matched:
        return condition_team_no_games(con, team, span, narrowed)
    base, params = _record_when_team_base(narrowed, stat, column)
    found = _record_when_team_query(con, base, params, threshold, span)
    if not found:
        return _record_when_team_no_stat(team, span, narrowed, stat, matched)
    return _record_when_team_answer_table(con, span, team, narrowed, stat, column, threshold, found)


#: A team's games for a streak, over the relation - `team_id`, `season`,
#: `won` and an event ordering (`day`, a stand-in `stamp`, `event_id`) are all
#: `_longest_runs_sql` reads. The relation guarantees at most one row per team per
#: Eastern date (`team_games.py`'s own docstring), so `day` doubling as
#: `stamp` never actually breaks a tie - there is none to break.
_TEAM_STREAK_SELECT: tuple[str, ...] = ("tg.team_id", "tg.season", "tg.event_id", "tg.eastern_date AS day", "tg.eastern_date AS stamp", "tg.won")


def _streak_league_team_narrowed(span: _Span) -> TeamNarrowed:
    """Every team's games in ``span``, for the league's longest run of wins
    or losses - over the relation, with no single team to narrow to the way
    :func:`common.team_games` always takes one, so the base clause is built
    the same way it builds one, minus the ``team_id`` clause it always adds.
    A postseason before 1993-94 is selected by the calendar year it was
    played in, not ESPN's own-year label (step 3, C4).

    .. versionadded:: 5.0.0
    """
    clause, clause_params = _team_span_clause(span)
    # Teams the `teams` table does not hold are exhibition opponents that
    # turn up in a few regular-season rows (1992-2000), not franchises - the
    # same guard `_team_games` used to apply inline.
    return TeamNarrowed(base=["tg.team_id IN (SELECT team_id FROM teams)", "tg.season_type = ?", clause], base_params=[span.season_type, *clause_params])
