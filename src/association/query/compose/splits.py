"""The splits reader: a player's or a team's games divided by venue, by
result, by month or (a player's) by starting and coming off the bench, read
into a :class:`~association.query.result.Result` with a
:class:`~association.query.result.Grouped` body - one row per group of each
split asked for, with the record and the per-game line in it. Phase 2's
slice (i): the retired ``player_splits`` template's two branches
(``templates.splits._player_splits_from`` and ``_player_splits_team``, said
by ``_player_splits_answer``) moved here whole, their remarks as kinds and
facts; the sayer (:mod:`association.query.compose.say`) words both.

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import duckdb

from association.query.conditions import _PLAYER_GAME_TABLES, _PLAYER_LINE, _SPLIT_GROUPS, _TEAM_LINE, _season_month_order, _totals, _unseen, box_source
from association.query.entities import Entity, optional_team
from association.query.lines import measure_filters
from association.query.notes import Note
from association.query.player_games import _PLAYER_GAMES, Narrowed, games_subquery
from association.query.player_relation import condition_scope, narrowed_cells, no_games, no_narrowed_games, span_of
from association.query.reading import SPLIT_KINDS, Scope, Unsupported, unhonored_scoping
from association.query.result import Grouped, Narrowing, Part, Result, Span, SplitsFacts, Unanswered
from association.query.season_text import MONTH_NAMES
from association.query.team_relation import condition_team_no_games, team_games, team_span_label

from .core import Compiled, Query, compile_over, compile_query, rows_of, unread_note
from .team import TeamQuery, compile_team_over

# Router stat name -> the standard split line's own key: naming one of these
# changes nothing about which columns are shown, since _PLAYER_LINE/_TEAM_LINE
# already carry it. Kept apart from SPLIT_EXTRA_STATS below so a stat that IS
# already on the table is neither refused nor given a redundant second column.
_SPLIT_LINE_STATS: dict[str, frozenset[str]] = {
    "p": frozenset({"points", "rebounds", "assists", "steals", "blocks", "turnovers", "minutes", "threePointFieldGoalsMade", "fieldGoalPct"}),
    "t": frozenset({"points", "rebounds", "assists", "threePointFieldGoalsMade", "fieldGoalPct"}),
}

# A stat player_splits can add as its own column beside the standard line,
# read straight off the player-games relation the way _PLAYER_LINE's own
# entries are - never silently left off a table that has no such column
# (F159: the usage rate asked for simply missing from the table). Player only:
# a team split (alias "t") has no per-player rate like this to show.
SPLIT_EXTRA_STATS: dict[str, tuple[str, str, str]] = {
    "usage_pct": ("usage_pct", "USG%", "AVG(p.usage_pct)"),
}
"""``player_splits`` extra-column stats, keyed by the router's stat name.

.. versionadded:: 4.4.0
"""

#: The two halves the stages narrow ``starter_bench`` to when the question
#: names one. A splits answer is both groups side by side, so it folds them
#: back to the category, while the log and the other filtering shapes read
#: the half.
_STARTER_BENCH_SIDES = frozenset({"starter", "bench"})


def _splits_line(stat: str | None, base_line: tuple[tuple[str, str, str], ...], *, alias: str) -> tuple[tuple[str, str, str], ...]:
    """``base_line`` (the player's or the team's), with a named ``stat`` the
    table does not already carry added as its own column - or a refusal
    naming the stat, never a table that quietly leaves it out (F159)."""
    if stat is None or not stat.strip():
        return base_line
    if stat in _SPLIT_LINE_STATS.get(alias, frozenset()):
        return base_line
    extra = SPLIT_EXTRA_STATS.get(stat) if alias == "p" else None
    if extra is None:
        raise Unsupported(f"player_splits has no column for stat {stat!r}")
    return (*base_line, extra)


def _splits_refusals(scope: Scope) -> None:
    """What no splits answer honors, refused before any name is resolved: a
    window of recent games (this divides a whole span into groups and has no
    notion of "his last N games"; answering the whole span under that
    framing would be the silent substitution the scoping cells exist to
    stop), and a home/away split beside a venue already narrowed to one
    (the same axis asked twice; the narrowing wins)."""
    if scope.limit is not None and scope.limit > 1:
        raise Unsupported("player_splits has no notion of a limited number of recent games")
    if scope.split == "home_away" and scope.venue is not None:
        raise Unsupported("a home/away split conflicts with a venue already narrowed to one")


def _kinds(split: Any, alias: str) -> tuple[str, list[str]]:
    """The split asked (a named half folded to its category) and the kinds
    the table shows: that one, or every kind the subject has."""
    if split in _STARTER_BENCH_SIDES:
        split = "starter_bench"
    kinds = [split] if split else [k for k in SPLIT_KINDS if alias == "p" or k != "starter_bench"]
    return split, kinds


#: A split kind -> the compiler's group that divides the games the same way.
_SPLIT_GROUP: dict[str, str] = {"home_away": "venue", "starter_bench": "starter", "wins_losses": "won", "month": "month_of_year"}
#: The compiler's group labels where the table's differ.
_GROUP_AS_SHOWN: dict[str, str] = {"win": "wins", "loss": "losses"}
#: A line entry's name where it is not the measure's own.
_LINE_MEASURE: dict[str, str] = {"threes": "threePointFieldGoalsMade"}


def _group_rows(found: list[dict[str, Any]], line: tuple[tuple[str, str, str], ...], kind: str) -> list[dict[str, Any]]:
    """The compiled statement's groups as the table shows them: both halves
    of a two-way split always (an empty one as zero games - "never came off
    the bench" is an answer, and a missing row reads as a bug), a value
    outside the expected pair shown rather than dropped, the months in
    season order (October first) under their names."""
    by_group = {_GROUP_AS_SHOWN.get(str(r["group"]), str(r["group"])): r for r in found if r["group"] is not None}
    # The months in season order; a two-way split's pair first, then anything else.
    keys = sorted(by_group, key=lambda m: _season_month_order(int(m))) if kind == "month" else [*_SPLIT_GROUPS[kind], *sorted(k for k in by_group if k not in _SPLIT_GROUPS[kind])]
    rows: list[dict[str, Any]] = []
    for key in keys:
        row = by_group.get(key)
        games = int(row["games"]) if row else 0
        wins = int(row["wins"]) if row else 0
        entry: dict[str, Any] = {"split": kind, "group": MONTH_NAMES[int(key) - 1] if kind == "month" else key, "games": games, "wins": wins, "losses": games - wins}
        for name, _, _ in line:
            entry[name] = row[_LINE_MEASURE.get(name, name)] if row else None
        rows.append(entry)
    return rows


def _player_groups(con: duckdb.DuckDBPyConnection, q: Query, compiled: Compiled, line: tuple[tuple[str, str, str], ...], kinds: list[str]) -> tuple[Grouped, Note | None]:
    """One row per group of each kind, in reading order - each kind one
    compiled statement: the point as a grouped read by that kind's group
    with the line's measures (:func:`~association.query.compose.core.compile_over`
    over the subject the first compile settled) - and the games the line's
    columns that read no rebuilt line could not read
    (:func:`~association.query.compose.core.unread_note`, over the first
    kind's groups: every kind divides the same games). The table's ``G``
    and ``W-L`` stay every game he played - the heading counts them, and a
    record has no rebuilt figure to skip - and the note says which columns
    are over fewer."""
    assert compiled.player is not None
    measures = [_LINE_MEASURE.get(name, name) for name, _, _ in line]
    rows: list[dict[str, Any]] = []
    unread: Note | None = None
    for index, kind in enumerate(kinds):
        point = replace(q, group=_SPLIT_GROUP[kind], measures=measures)
        found = rows_of(con, compile_over(con, point, compiled.player, compiled.span, compiled.narrowed))
        if index == 0:
            unread = unread_note(point, found)
        rows += _group_rows(found, line, kind)
    return Grouped(by="split", rows=tuple(rows)), unread


def _narrowing_emptied(con: duckdb.DuckDBPyConnection, narrowed: Narrowed) -> bool:
    """Whether the player has games under the base clauses alone - so it is
    the narrowing (an opponent, a condition, a venue) that left none, and
    the answer should name it rather than the span."""
    if not narrowed.extra:
        return False
    where, params = narrowed.clauses(narrowed=False, rebuilt=box_source(con).rebuilt)
    row = con.execute(f"SELECT COUNT(*) {_PLAYER_GAMES} WHERE {where}", params).fetchone()
    return bool(row and row[0])


def read_player_splits(con: duckdb.DuckDBPyConnection, q: Query, *, stated: frozenset[str]) -> Result | Unanswered | None:
    """A player's splits - one or all four, side by side - over the
    compiler's settled player and narrowing (#228). ``None`` where the point
    is not the splits' own (a grouped record by venue or by starter on the
    player relation with no predicate), carries a narrowing the words did not
    state (``stated``), or names a stat the line has no column for (the
    compiler's own point, said by its sentence); the template's own early
    refusals stand (a window, a home/away split beside a venue). A
    :class:`~association.query.result.Refusal` or :class:`~association.query.result.Clarify` back is the
    relation's refusal.

    .. versionadded:: 5.0.0
    """
    if q.skeleton != "grouped" or q.subject != "player" or q.predicates or q.group not in ("venue", "starter"):
        return None
    scope = q.scope
    if unhonored_scoping("player_splits", scope, stated):
        return None
    _splits_refusals(scope)
    try:
        line = _splits_line(scope.stat, _PLAYER_LINE, alias="p")
    except Unsupported:
        return None
    team = optional_team(con, scope.team, season=scope.season)
    if isinstance(team, Unanswered):
        return team
    opponent = optional_team(con, scope.opponent, season=scope.season)
    if isinstance(opponent, Unanswered):
        return opponent
    covered = condition_scope(scope.season, scope.span, scope.season_type, _PLAYER_GAME_TABLES, since=scope.since)
    compiled = compile_query(con, q)
    if compiled.player is None:
        return None
    return _player_splits(con, q, compiled, covered, team, opponent, line)


def _player_splits(
    con: duckdb.DuckDBPyConnection, q: Query, compiled: Compiled, covered: Any, team: Entity | None, opponent: Entity | None, line: tuple[tuple[str, str, str], ...]
) -> Result | Unanswered:
    """The groups from the compiled statements, the totals and the unseen
    games over the relation's own steps (``games_subquery``), the label and
    the remarks - :func:`read_player_splits`'s tail."""
    scope, player, narrowed, span = q.scope, compiled.player, compiled.narrowed, compiled.span
    assert player is not None
    base, params = games_subquery(narrowed, box_source(con))
    games, first, last = _totals(con, base, params)
    if not games:
        if span is not None and _narrowing_emptied(con, narrowed):
            # The narrowing emptied the games, not the span: "steph curry
            # record vs lebron" with no meeting this season said "listed in
            # 43 box scores but did not play in any of them" - a confident
            # refusal naming the wrong missing fact. The relation's own
            # sentence names the narrowing.
            refused = no_narrowed_games(con, player, span, narrowed, rebuilt=box_source(con).rebuilt)
            return replace(refused, shown={"player": player.name, "team": team.name if team else None, "span": covered.label(), "games": 0})
        return no_games(con, player, covered, team)
    if scope.season_n and first is not None and first == last and covered.season != first:
        # An ordinal season ("his 18th season") is not a year until the player
        # is known, so `covered` - built before the player was resolved -
        # could not carry it; the narrowed rows just settled it.
        covered = replace(covered, season=int(first))
    split, kinds = _kinds(scope.split, "p")
    groups, unread = _player_groups(con, q, compiled, line, kinds)
    notes = _player_notes(con, covered, base, params, first, kinds, unread)
    facts = _player_facts(team, line, split, kinds, games)
    return Result(
        subject=player.name,
        relation="player",
        span=Span(season=covered.season, season_type=covered.season_type, career=covered.season is None, first=first, last=last, phrase=covered.label(first, last)),
        narrowing=Narrowing(
            phrase=narrowed.filters(),
            opponent=opponent.name if opponent else None,
            venue=scope.venue,
            without=tuple(mate.name for mate in narrowed.without),
            cells=narrowed_cells(narrowed),
        ),
        parts=(Part(body=groups),),
        notes=notes,
        facts=facts,
    )


def _player_notes(con: duckdb.DuckDBPyConnection, covered: Any, base: str, params: Any, first: int | None, kinds: list[str], unread: Note | None) -> tuple[Note, ...]:
    """The remarks, in the order the template wrote them: the unseen games,
    the columns rebuilt lines could not fill (``unread``), the floor, then
    what the table's words mean."""
    notes: list[Note] = []
    unseen = _unseen(con, covered, base, params, box_source(con))
    if unseen:
        notes.append(Note("games_unseen", {"games": unseen, "why": "no_box_score", "whose": "his team's"}))
    if unread is not None:
        notes.append(unread)
    if covered.season is None and first == covered.first:
        notes.append(Note("floor", {"table": "box_scores", "first": covered.first, "what": covered.kind}))
    notes.append(Note("definition", {"term": "played"}))
    if "month" in kinds:
        notes.append(Note("definition", {"term": "months_eastern"}))
    return tuple(notes)


def _player_facts(team: Entity | None, line: tuple[tuple[str, str, str], ...], split: Any, kinds: list[str], games: int) -> SplitsFacts:
    """What the splits' sayer needs beside the rows: the split asked and the
    kinds shown, the line's names and headers, the count the heading says,
    and the team his games were played for."""
    return SplitsFacts(
        split=split,
        kinds=tuple(kinds),
        line=tuple((name, header) for name, header, _ in line),
        counted=f"{games} game{'s' if games != 1 else ''} he played",
        games=games,
        team=team.name if team else None,
    )


def read_team_splits(con: duckdb.DuckDBPyConnection, q: TeamQuery, *, stated: frozenset[str]) -> Result | Unanswered | None:
    """A team's own splits ("76ers wins vs losses"): the team's per-game line
    by venue, by result or by month, over the team-games relation. A team has
    no starter/bench split of its own, and the narrowings only a settled
    player's games take (a line on a box-score column, a game of a series, an
    ordinal season, a teammate's absence or role) are refused by name rather
    than silently ignored. ``None`` where the words do not state a narrowing
    the scope carries (``stated``), so the team compiler's own sentence
    answers.

    .. versionadded:: 5.0.0
    """
    from association.query.coverage import coverage_refusal
    from association.query.reading import PointShape

    scope = q.scope
    if unhonored_scoping("player_splits", scope, stated):
        return None
    refused = coverage_refusal(PointShape("team_games", "split", "splits"), scope)
    if refused is not None:
        return refused
    _splits_refusals(scope)
    measures = measure_filters(scope.below, scope.above)
    team = optional_team(con, scope.team, season=scope.season)
    if isinstance(team, Unanswered):
        return team
    opponent = optional_team(con, scope.opponent, season=scope.season)
    if isinstance(opponent, Unanswered):
        return opponent
    if team is None:
        raise Unsupported("player_splits needs a player or a team")
    if measures or scope.game_n or scope.season_n or scope.without or scope.conditions:
        # A team's own splits read the team tables directly, not the
        # player-games relation these narrow - so a line on a box-score
        # column, a playoff-series game, an ordinal season or a teammate's
        # absence (or role) have nowhere to apply. Refused by name rather
        # than silently ignored ("76ers splits without Embiid" once answered
        # the whole season).
        raise Unsupported("player_splits cannot honor below/above, game_n, season_n, without or conditions for a team with no player named")
    if scope.split == "starter_bench" or scope.split in _STARTER_BENCH_SIDES:
        # "Bench scoring" is a sum over a team's players - a different
        # question from any this shape answers.
        raise Unsupported("a team has no starter/bench split of its own")
    return _team_splits(con, q, team, opponent)


def _team_splits(con: duckdb.DuckDBPyConnection, q: TeamQuery, team: Entity, opponent: Entity | None) -> Result | Unanswered:
    """A named team's own games, narrowed to an opponent and/or a venue the
    same way every other team-facing read narrows them, divided by each
    kind through the team compiler's ``grouped`` shape
    (:func:`~association.query.compose.team.compile_team_over`) - the tail
    of :func:`read_team_splits`."""
    scope = q.scope
    line = _splits_line(scope.stat, _TEAM_LINE, alias="t")
    span = span_of(scope.span, scope.season, scope.season_type or 2, "games", since=scope.since, until=scope.until)
    narrowed = team_games(con, team, span, scope, opponent=opponent)
    if isinstance(narrowed, Unanswered):
        return narrowed
    split, kinds = _kinds(scope.split, "t")
    # One compiled grouped read per kind, over every game in the span (the
    # team compiler's ``grouped`` shape reads a split over the whole span).
    found = {kind: rows_of(con, compile_team_over(replace(q, group=_SPLIT_GROUP[kind]), team, span, narrowed)) for kind in kinds}
    # Every kind divides the same games, so the first one's groups give the
    # count, the seasons they came from and the games with no box score.
    each = found[kinds[0]]
    games = sum(int(r["games"]) for r in each)
    if not games:
        return condition_team_no_games(con, team, span, narrowed)
    first, last = min(r["first_season"] for r in each), max(r["last_season"] for r in each)
    # The score of a game with no box score is still on record, but its
    # team box stats are NULL - averaged over the rest, and said so.
    blanks = sum(int(r["blank"]) for r in each)
    notes: list[Note] = []
    if blanks:
        notes.append(Note("stat_blank", {"games": blanks, "columns": ["rebounds", "assists", "threes", "fg_pct"]}))
    if span.season is None and span.since is None and first == span.first:
        # Not shown for a since-bounded span: the question named its own
        # starting year, so a note that games "start with" it would read as
        # though the WAREHOUSE put the floor there.
        notes.append(Note("floor", {"table": "box_scores", "first": span.first, "what": span.kind}))
    if "month" in kinds:
        notes.append(Note("definition", {"term": "months_eastern"}))
    facts = SplitsFacts(split=split, kinds=tuple(kinds), line=tuple((name, header) for name, header, _ in line), counted=f"{games} game{'s' if games != 1 else ''}", games=games, team=team.name)
    return Result(
        subject=f"The {team.name}",
        relation="team",
        span=Span(season=span.season, season_type=span.season_type, career=span.season is None, first=first, last=last, phrase=team_span_label(span, first, last)),
        narrowing=Narrowing(phrase=narrowed.filters(), opponent=narrowed.opponent.name if narrowed.opponent else None, venue=narrowed.venue),
        parts=(Part(body=Grouped(by="split", rows=tuple(row for kind in kinds for row in _group_rows(found[kind], line, kind)))),),
        notes=tuple(notes),
        facts=facts,
    )
