"""The with/without reader: a team's record in the games named teammates
played against the games they missed, side by side, with the subject's
averages in each where a player is named - read into a
:class:`~association.query.result.Result` with a
:class:`~association.query.result.Grouped` body by ``presence`` (one row
per team and side). Phase 2's slice (iv): the retired ``with_without``
template's read (``templates.splits._with_without_read``, said through the
presenter ``compose.present._present_with_without`` until then) moved here,
its games now the team compiler's ``presence`` statement
(:func:`~association.query.compose.team.compile_team_presence`) executed
through the one door; the sayer (:mod:`association.query.compose.say`,
``say_with_without``) words it.

Only games inside a teammate's time on the team count - StatMuse answers
"Nets record without KD" all-time with 439-672, decades of games before he
arrived every one "without" him - and a question naming two teammates is
divided by both of them: "Celtics record without Tatum and Brown" is the
games NEITHER of them played, and "record when A and B play" the games both
did; the games where one played and one sat belong to neither of those, and
go on the other row (:func:`_with_without_played`). Only the time they were
ALL on the same team is counted, for the same reason one teammate's tenure
is.

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from typing import Any

import duckdb

from association.nba.franchises import season_name
from association.query.answer import Reply
from association.query.conditions import _PLAYER_GAME_TABLES, _names, _overlaps, _Scope, _stints, _with_without_group, presence_games
from association.query.entities import Entity
from association.query.notes import Note
from association.query.player_games import _joined
from association.query.reading import Scope, Unsupported
from association.query.result import Grouped, Narrowing, Part, Result, Span
from association.query.subject import with_without_named
from association.query.templates.common import BOX_SCORES, career_end, condition_scope, optional_team, resolved_player, unhonored_scoping

from .core import rows_of
from .team import TeamQuery, compile_team_presence, team_coverage_refusal


def read_with_without(con: duckdb.DuckDBPyConnection, q: TeamQuery, *, stated: frozenset[str]) -> Result | Reply | None:
    """``with_without``'s point - the team relation's ``presence`` group -
    read: the teammates (``without``, ``with_player``, a ``conditions``
    role, or the one name beside a team), the subject where a player is
    named, the team where one is, resolved against the box scores; the
    overlap of their stints on the team; and every game a window's team
    played inside it, marked with HOW MANY of the named teammates held
    their condition, narrowed to the opponent the question named, since
    both rows narrow together (#163). ``None`` where the point is not the
    split, or carries a narrowing its words do not state (``stated``); a
    :class:`~association.query.answer.Reply` back is an
    answer the reading gives up with: the coverage floor, which player was
    meant, a teammate with no box score, a time together outside the span.

    .. versionadded:: 5.0.0
    """
    if q.shape != "grouped" or q.group != "presence":
        return None
    if unhonored_scoping("with_without", q.scope, stated):
        return None
    refused = team_coverage_refusal(q)
    if refused is not None:
        return refused
    return _with_without_read(con, q.scope)


def _with_without_read(con: duckdb.DuckDBPyConnection, scope: Scope) -> Result | Reply:
    """:func:`read_with_without`'s read: who, their time together, and the
    games in it - or the answer the reading gives up with."""
    mate_texts, asked_without, roles = with_without_named(scope)
    texts = list(dict.fromkeys(n.strip() for n in (scope.player, *scope.players) if n is not None and n.strip()))
    team = optional_team(con, scope.team, season=scope.season)
    if isinstance(team, Reply):
        return team
    if not mate_texts:
        mate_texts, texts = _with_without_infer_teammate(team, texts)

    covered = condition_scope(scope.season, scope.span, scope.season_type, _PLAYER_GAME_TABLES)
    resolved = _with_without_resolve(con, mate_texts, texts, covered)
    if isinstance(resolved, Reply):
        return resolved
    mates, subject = resolved

    named = [m.name for m in mates]
    all_of = _joined(named)
    windows = _with_without_windows(con, mates, subject, team, covered)
    if isinstance(windows, Reply):
        return windows
    if not windows:
        return _with_without_empty_windows(team, subject, mates, named, all_of)

    # The opponent the question named, if any: "Embiid career record vs boston"
    # is his record in the games his team played BOSTON, not overall. Narrowing
    # is honest here because both rows narrow together - the split is still
    # played against missed, over the same pool (#163).
    against = optional_team(con, scope.opponent, season=scope.season)
    if isinstance(against, Reply):
        return against
    predicates = _with_without_predicates(con, mates, roles, covered)
    compiled = compile_team_presence(con, covered, windows, [m.id for m in mates], subject.id if subject else None, against.id if against else None, predicates)
    games, unknown = presence_games(rows_of(con, compiled), windows)
    team_names = _names(con, "teams", "team_id", {w.team_id for w in windows})
    if not games:
        spell_text = "; ".join(f"{_with_without_stint_team(w, team_names)} {w.first} to {w.last}" for w in windows)
        return _with_without_empty_games(subject, mates, named, all_of, covered, spell_text, unknown)
    return _with_without_result(covered, subject, mates, asked_without, predicates, windows, against, games, unknown, team_names)


def _with_without_result(
    covered: _Scope,
    subject: Entity | None,
    mates: list[Entity],
    asked_without: bool,
    predicates: list[tuple[str, tuple[str, int] | None]],
    windows: list[Any],
    against: Entity | None,
    games: list[dict[str, Any]],
    unknown: int,
    team_names: dict[str, str],
) -> Result:
    """The groups - team by team in the order the games were played, so a
    career reads forwards, and side by side, the question's own side first -
    the tenure counted, and the remarks: what was counted, what "played" (or
    "out") means for one teammate against two, the games with no box score
    on neither side, and a player subject's columns."""
    named = [m.name for m in mates]
    team_order, groups = _with_without_groups(games, team_names, asked_without, len(mates))
    first, last = min(g["season"] for g in games), max(g["season"] for g in games)
    used = [w for w in windows if any(g["team_id"] == w.team_id and w.first <= g["day"] <= w.last for g in games)]
    tenure = [{"team": team_names[w.team_id], "from": str(w.first), "to": str(w.last)} for w in used]
    notes = _with_without_notes(named, tenure, asked_without, unknown, subject)
    teams = [team_names[t] for t in team_order]
    return Result(
        subject=subject.name if subject is not None else ", ".join(teams),
        relation="team",
        span=Span(season=covered.season, season_type=covered.season_type, career=covered.season is None, first=first, last=last, phrase=covered.label(first, last)),
        narrowing=Narrowing(phrase=f" vs the {against.name}" if against is not None else "", opponent=against.name if against is not None else None),
        parts=(Part(body=Grouped(by="presence", rows=tuple(groups))),),
        notes=tuple(notes),
        facts={
            "teammates": named,
            "player": subject.name if subject is not None else None,
            "teams": teams,
            "asked_without": asked_without,
            "predicates": [(kind, None if line is None else (line[0], line[1])) for kind, line in predicates],
        },
    )


def _with_without_groups(games: list[dict[str, Any]], team_names: dict[str, str], asked_without: bool, n_mates: int) -> tuple[list[str], list[dict[str, Any]]]:
    """The teams in the order the games were played, and each team's two
    groups - the question's own side first - with the record in each and
    the subject's averages over the games he played."""
    team_order = list(dict.fromkeys(g["team_id"] for g in sorted(games, key=lambda g: g["day"])))
    order = (False, True) if asked_without else (True, False)
    groups: list[dict[str, Any]] = []
    for team_id in team_order:
        for played in order:
            chosen = [g for g in games if g["team_id"] == team_id and _with_without_played(g, asked_without, n_mates) == played]
            groups.append({"team": team_names[team_id], "teammate_played": played, **_with_without_group(chosen)})
    return team_order, groups


def _with_without_notes(named: list[str], tenure: list[dict[str, str]], asked_without: bool, unknown: int, subject: Entity | None) -> list[Note]:
    """The remarks under the table, in the template's order: the tenure
    counted, what "played" (or "out") means for one teammate against two,
    the games with no box score, and a player subject's columns."""
    notes = [Note("definition", {"term": "tenure_counted", "names": named, "stints": tenure})]
    if len(named) == 1 or not asked_without:
        notes.append(Note("definition", {"term": "played", "names": named}))
    else:
        notes.append(Note("definition", {"term": "out", "names": named}))
    if unknown:
        # A game with no box score is not a game he missed - see
        # conditions._box_missing - so it is on neither side, and said so.
        notes.append(Note("games_unseen", {"games": unknown, "why": "no_box_score"}))
    if subject is not None:
        notes.append(Note("definition", {"term": "columns", "whose": subject.name}))
    return notes


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


def _with_without_predicates(con: duckdb.DuckDBPyConnection, mates: list[Entity], roles: dict[str, tuple[str, tuple[str, int] | None]], scope: _Scope) -> list[tuple[str, tuple[str, int] | None]]:
    """Each resolved teammate's predicate: the role a ``conditions`` entry
    naming him gives, else ``played``."""
    by_id: dict[str, tuple[str, tuple[str, int] | None]] = {}
    for text, role in roles.items():
        found = resolved_player(con, text, available=BOX_SCORES, season=scope.season, through=career_end(scope.season))
        if not isinstance(found, Reply):
            by_id[found.id] = role
    return [by_id.get(m.id, ("played", None)) for m in mates]


def _with_without_infer_teammate(team: Entity | None, texts: list[str]) -> tuple[list[str], list[str]]:
    """The teammate to divide by when the question named no "with" or
    "without": whichever name is not the subject - the only name beside a
    team, or the second of two. More than that is "record when A and B and C
    play", which this does not answer."""
    if team is not None and len(texts) == 1:
        return texts, []
    if team is None and len(texts) == 2:
        return texts[1:], texts[:1]
    raise Unsupported(f"with_without needs exactly one teammate, got {texts!r}")


def _with_without_resolve(con: duckdb.DuckDBPyConnection, mate_texts: list[str], texts: list[str], scope: _Scope) -> tuple[list[Entity], Entity | None] | Reply:
    """The teammates, and the subject if one is named, resolved against the box
    scores. More than one leftover name after the teammates are matched is
    refused rather than guessed at."""
    mates: list[Entity] = []
    for text in mate_texts:
        found = resolved_player(con, text, "with_without needs a teammate", available=BOX_SCORES, season=scope.season, through=career_end(scope.season))
        if isinstance(found, Reply):
            return found
        if found.id not in {m.id for m in mates}:
            mates.append(found)
    subjects: list[Entity] = []
    for text in texts:
        found = resolved_player(con, text, available=BOX_SCORES, season=scope.season, through=career_end(scope.season))
        if isinstance(found, Reply):
            return found
        # The router often repeats a teammate in `player`; that is not a subject.
        if found.id not in {m.id for m in mates} and found.id not in {s.id for s in subjects}:
            subjects.append(found)
    if len(subjects) > 1:
        raise Unsupported(f"with_without answers for one player, got {[s.name for s in subjects]}")
    subject = subjects[0] if subjects else None
    return mates, subject


def _with_without_windows(con: duckdb.DuckDBPyConnection, mates: list[Entity], subject: Entity | None, team: Entity | None, scope: _Scope) -> list[Any] | Reply:
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
        return Reply(data={"teammate": absent.name, "teammates": named, "groups": [], "headline": message}, answer=message)
    windows = stints[0]
    for spells in stints[1:]:
        windows = _overlaps(windows, spells)
    if subject is not None:
        windows = _overlaps(_stints(con, subject.id, scope.phantoms), windows)
    if team is not None:
        windows = [w for w in windows if w.team_id == team.id]
    return windows


def _with_without_empty_windows(team: Entity | None, subject: Entity | None, mates: list[Entity], named: list[str], all_of: str) -> Reply:
    """The refusal for a subject and teammates (and, if named, a team) who were
    never on the same roster together at all, as the box scores show it."""
    on = f" the {team.name}" if team else ""
    if subject is None and len(mates) == 1:
        message = f"{all_of} never appeared in a box score for{on}, so there are no {team.name if team else ''} games with or without him to count."
    else:
        whom = _joined([subject.name, *named]) if subject is not None else all_of
        played_phrase = "he played" if len(mates) == 1 else "they all played"
        message = f"{whom} were never on{on or ' the same team'} together in the box scores on record, so there are no games to divide by whether {played_phrase}."
    return Reply(data={"teammate": all_of, "teammates": named, "player": subject.name if subject else None, "groups": [], "headline": message}, answer=message)


def _with_without_season_of_day(day: Any) -> int:
    """The season a calendar day falls in: season Y runs from October of Y-1."""
    return day.year + 1 if day.month >= 10 else day.year


def _with_without_stint_team(w: Any, team_names: dict[str, str]) -> str:
    """The team a stint was spent on, named as it was then. A stint across a
    rename names both - the Nets' 2012 and 2013 are one stint on one id."""
    start, end = (season_name(w.team_id, _with_without_season_of_day(day), team_names[w.team_id]) for day in (w.first, w.last))
    return start if start == end else f"{start} / {end}"


def _with_without_empty_games(subject: Entity | None, mates: list[Entity], named: list[str], all_of: str, scope: _Scope, spell_text: str, unknown: int) -> Reply:
    """The refusal for a subject and teammates whose time together, as the box
    scores show it, falls outside the scope asked about - or holds games but
    none of them with a box score."""
    whose = f"{all_of}'s time" if subject is None else f"The time {_joined([subject.name, *named])} spent together"
    message = f"{whose} on the team, as the box scores show it ({spell_text}), falls outside the {scope.label() if scope.season else 'seasons on record'}."
    if unknown:
        # Inside the time, but every game of it without a box score - a
        # different fact from the time missing the season altogether.
        message = f"All {unknown} games inside {whose[0].lower() + whose[1:]} on the team in the {scope.label()} have no box score in the warehouse, so whether {all_of} played them cannot be told."
    return Reply(data={"teammate": all_of, "teammates": named, "player": subject.name if subject else None, "groups": [], "headline": message}, answer=message)
