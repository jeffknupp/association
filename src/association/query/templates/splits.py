"""Games under a condition: a player's splits, with or without a teammate, a record when a stat line is reached, and streaks.

.. versionadded:: 3.0.0
   Split out of the former ``association.query.templates`` module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import duckdb

from association.nba.franchises import season_name
from association.nba.season import current_season

from ..conditions import (
    _PLAYER_GAME_TABLES,
    _PLAYER_LINE,
    _SPLIT_TITLES,
    _TEAM_GAME_TABLES,
    _TEAM_LINE,
    _box_missing,
    _cell,
    _longest_runs,
    _margin,
    _names,
    _overlaps,
    _player_games,
    _player_streak_rows,
    _Scope,
    _split_cells,
    _split_label,
    _split_rows,
    _stints,
    _table,
    _team_games,
    _totals,
    _unseen,
    _unseen_note,
    _win_pct,
    _with_without_games,
    _with_without_group,
    box_source,
)
from ..entities import Entity, teammate_names
from .common import (
    _BOX_SCORES,
    STAT_LABELS,
    THRESHOLD_STAT_COLUMNS,
    TemplateContext,
    TemplateResult,
    TemplateUnsupported,
    _career_end,
    _checked_venue,
    _clamp_limit,
    _condition_scope,
    _joined,
    _no_games,
    _optional_team,
    _resolved_player,
    _slot_season,
    _where_in,
)


def _season_of_day(day: Any) -> int:
    """The season a calendar day falls in: season Y runs from October of Y-1."""
    return day.year + 1 if day.month >= 10 else day.year


SPLIT_KINDS: tuple[str, ...] = ("home_away", "starter_bench", "wins_losses", "month")
"""The splits :func:`player_splits` answers, and the values ``router.SPLIT_WORDS`` reads out of a question.

.. versionadded:: 2.1.0
"""


_DEFAULT_STREAK_LIMIT = 5


_UNSEEN_ENDS_RUN = " A game with no box score in the warehouse ends a run rather than being carried across, since it cannot be checked."


# What the model tends to put in the required `stat` slot for "longest winning
# streak". Anything else is a stat, and a stat with no threshold is refused
# rather than read as a win streak - "most consecutive double-doubles" must not
# come back as the Lakers' best run of wins.
_RESULT_STATS = frozenset({"win", "wins", "winning", "loss", "losses", "losing", "streak", "streaks", "record", "games", "winning streak", "losing streak"})


def _misfiled_postseason(scope: _Scope) -> TemplateResult | None:
    """A refusal for a single postseason before 1993-94, or None.

    The team tables hold playoff games back to 1988, but ESPN files every
    season before 1993-94 under the year it began: the warehouse's "1990"
    postseason runs from April to June 1991. Answered as asked, "Bulls 1990
    playoffs" would describe the 1991 playoffs under a 1990 label - the
    right-looking answer about another year this project keeps producing. A
    span of seasons starts in 1994 for the same reason (``_game_scope``)."""
    if not scope.misfiled:
        return None
    message = (
        f"The warehouse files playoff games from before 1993-94 under the year the season began - its {scope.season} postseason is the "
        f"{(scope.season or 0) + 1} playoffs - so an answer for {scope.season} would be about the wrong year."
    )
    return TemplateResult(data={"message": message, "season": scope.season}, answer=message)


def player_splits(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """A player's per-game averages divided by one condition of the game:
    home or away, starting or off the bench, won or lost, or the month.

    With no ``split``, all four come back as one table rather than a guess at
    which was meant: the router reads the split from the question's words
    (``router.SPLIT_WORDS``) and leaves it unset when they name none or
    several. A team works too ("76ers wins vs losses") with the team's own
    per-game line, except that a team has no starter/bench split of its own,
    which is refused rather than answered with something else.

    A named ``team`` narrows a player's games to that team ("westbrook stats as
    a starter for kings"), since a traded player's splits are otherwise a mix
    of two rosters. A named ``venue`` and/or ``opponent`` narrow them further -
    "Duren away vs Denver" - the same filters ``team_record`` already applies
    to a team. Only games he played count, and months are the US Eastern date
    the game was played on - see :mod:`association.query.conditions`.

    .. versionadded:: 2.1.0

    .. versionchanged:: 4.3.0
       Honors ``venue`` and ``opponent``, narrowing the games either subject's
       splits are computed over rather than falling through.
    """
    con = ctx.con
    split = slots.get("split")
    if split is not None and split not in SPLIT_KINDS:
        raise TemplateUnsupported(f"no split named {split!r}")
    limit = slots.get("limit")
    if isinstance(limit, int) and not isinstance(limit, bool) and limit > 1:
        # This divides a whole span into groups; it has no notion of "his last
        # N games" the way game_log does, and answering the whole span under
        # that framing would be the exact silent substitution check_scope
        # exists to stop - "Pat Spencer st home last four games" would answer
        # his entire 35-game home season instead. `limit` is not a scoping
        # slot check_scope reads (nothing else needs it to refuse), so it is
        # checked here. A bare 1 is left alone: router.py already treats it as
        # noise for the same reason elsewhere (_route_side_and_order drops a
        # limit of 1 once its `order` is dropped), and here it never changes
        # the answer - the games a venue/opponent narrows to are shown in
        # full either way, so a real "last one" and the router's filler 1
        # produce the same table.
        raise TemplateUnsupported("player_splits has no notion of a limited number of recent games")
    venue = _checked_venue(slots["venue"]) if slots.get("venue") else None
    if split == "home_away" and venue is not None:
        # Breaking games out by home/away while also narrowing to one of the
        # two asks the same axis twice; the narrowing wins rather than showing
        # one real row beside an empty one.
        raise TemplateUnsupported("a home/away split conflicts with a venue already narrowed to one")
    team = _optional_team(con, slots.get("team"), season=_slot_season(slots))
    if isinstance(team, TemplateResult):
        return team
    opponent = _optional_team(con, slots.get("opponent"), season=_slot_season(slots))
    if isinstance(opponent, TemplateResult):
        return opponent

    span = slots.get("span")
    name = slots.get("player")
    if isinstance(name, str) and name.strip():
        found = _player_splits_player(con, name, slots, span, team, venue, opponent)
    else:
        if team is None:
            raise TemplateUnsupported("player_splits needs a player or a team")
        found = _player_splits_team(con, slots, span, team, split, venue, opponent)
    if isinstance(found, TemplateResult):
        return found
    return _player_splits_answer(con, found, split)


def _player_splits_narrow_sql(venue: str | None, opponent: Entity | None, *, team_read: bool = False) -> tuple[str, dict[str, Any]]:
    """Extra ``WHERE`` SQL and its params narrowing to a venue and/or an
    opponent. A player's games are the relation's rows (``pgl`` joined to
    ``games`` ``g``), which carry the opponent and the side; a team's games
    (``conditions._team_games``) still alias ``team_box_stats`` ``tbs``, so
    ``team_read`` picks that spelling."""
    extra = ""
    params: dict[str, Any] = {}
    if venue is not None:
        extra += " AND tbs.home_away = $venue" if team_read else " AND (CASE WHEN g.home_team_id = pgl.team_id THEN 'home' ELSE 'away' END) = $venue"
        params["venue"] = venue
    if opponent is not None:
        extra += " AND (CASE WHEN tbs.home_away = 'home' THEN g.away_team_id ELSE g.home_team_id END) = $opponent" if team_read else " AND pgl.opponent_team_id = $opponent"
        params["opponent"] = opponent.id
    return extra, params


def _player_splits_narrow_phrase(venue: str | None, opponent: Entity | None) -> str:
    """ "(on the road vs the Denver Nuggets)" - what a venue and/or opponent
    narrowing reads as after a subject's name, so honoring it is said in the
    answer rather than left for the reader to assume."""
    parts = []
    if venue is not None:
        parts.append("at home" if venue == "home" else "on the road")
    if opponent is not None:
        parts.append(f"vs the {opponent.name}")
    return f" ({' '.join(parts)})" if parts else ""


#: The two halves `route()` narrows ``starter_bench`` to when the question names
#: one. This template wants the CATEGORY - a splits answer is both groups side
#: by side - so it folds them back, while `game_log` and the other filtering
#: templates read the half. See ``router._split_side``.
_STARTER_BENCH_SIDES = frozenset({"starter", "bench"})


def _player_splits_answer(con: duckdb.DuckDBPyConnection, found: _SplitSubject, split: Any) -> TemplateResult:
    """The shared table over whichever subject was resolved: one or all four
    splits, each split's rows, and the notes that qualify them."""
    if split in _STARTER_BENCH_SIDES:
        split = "starter_bench"
    kinds = [split] if split else [k for k in SPLIT_KINDS if found.alias == "p" or k != "starter_bench"]
    splits = {kind: _split_rows(con, found.base, found.params, found.alias, found.line, kind) for kind in kinds}
    label = found.scope.label(found.first, found.last)
    rows: list[tuple[str, list[str]]] = []
    for kind in kinds:
        if rows:
            rows.append(("", []))
        rows += [(_split_label(kind, entry), _split_cells(entry, found.line)) for entry in splits[kind]]
    what = _SPLIT_TITLES[split] if split else "splits"
    notes = []
    if found.alias == "p":
        notes.append("Played means he appeared in the game, and W-L is his team's record in those games.")
    if "month" in kinds:
        notes.append("Months go by the US Eastern date of the game.")
    answer = _table(f"{found.subject}, {what}, {label} ({found.counted}):", ["G", "W-L", *(h for _, h, _ in found.line)], rows)
    notes += [note.strip() for note in (found.scope.floor_note(found.first), found.caveat) if note]
    answer += "\n" + " ".join(notes)
    return TemplateResult(data={**found.data, "span": label, "games": found.games, "splits": splits}, answer=answer.strip())


@dataclass(frozen=True)
class _SplitSubject:
    """What player_splits divides - a player's own games or a team's - with
    the SQL already scoped and the label pieces ready for the shared table
    player_splits builds from either."""

    scope: _Scope
    base: str
    params: dict[str, Any]
    games: int
    first: int | None
    last: int | None
    subject: str
    alias: str
    line: tuple[tuple[str, str, str], ...]
    counted: str
    data: dict[str, Any]
    caveat: str


def _player_splits_player(
    con: duckdb.DuckDBPyConnection, name: str, slots: dict[str, Any], span: Any, team: Entity | None, venue: str | None, opponent: Entity | None
) -> _SplitSubject | TemplateResult:
    """A named player's own games, optionally narrowed to one team, one venue and/or one opponent."""
    scope = _condition_scope(slots.get("season"), span, slots.get("season_type"), _PLAYER_GAME_TABLES)
    player = _resolved_player(con, name, available=_BOX_SCORES, season=scope.season, through=_career_end(scope.season))
    if isinstance(player, TemplateResult):
        return player
    narrow_sql, narrow_params = _player_splits_narrow_sql(venue, opponent)
    params: dict[str, Any] = {**scope.params(), "player": player.id, **narrow_params}
    if team is not None:
        params["team"] = team.id
    base = _player_games(scope, extra=(" AND pgl.team_id = $team" if team else "") + narrow_sql, box=box_source(con))
    games, first, last = _totals(con, base, params)
    if not games:
        return _no_games(con, player, scope, team)
    subject = player.name + (f" for the {team.name}" if team else "") + _player_splits_narrow_phrase(venue, opponent)
    alias, line, counted = "p", _PLAYER_LINE, f"{games} game{'s' if games != 1 else ''} he played"
    data: dict[str, Any] = {"player": player.name, "team": team.name if team else None, "venue": venue, "opponent": opponent.name if opponent else None}
    caveat = _unseen_note(_unseen(con, scope, base, params, box_source(con)))
    return _SplitSubject(scope, base, params, games, first, last, subject, alias, line, counted, data, caveat)


def _player_splits_team(con: duckdb.DuckDBPyConnection, slots: dict[str, Any], span: Any, team: Entity, split: Any, venue: str | None, opponent: Entity | None) -> _SplitSubject | TemplateResult:
    """A named team's own games, with no player named, optionally narrowed to one venue and/or one opponent."""
    if split == "starter_bench" or split in _STARTER_BENCH_SIDES:
        # "Bench scoring" is a sum over a team's players - a different
        # question from any this template answers. True of one named half as
        # much as of the category.
        raise TemplateUnsupported("a team has no starter/bench split of its own")
    scope = _condition_scope(slots.get("season"), span, slots.get("season_type"), _TEAM_GAME_TABLES)
    misfiled = _misfiled_postseason(scope)
    if misfiled is not None:
        return misfiled
    narrow_sql, narrow_params = _player_splits_narrow_sql(venue, opponent, team_read=True)
    params = {**scope.params(), "team": team.id, **narrow_params}
    base = _team_games(scope, " AND tbs.team_id = $team" + narrow_sql)
    games, first, last = _totals(con, base, params)
    if not games:
        message = f"The warehouse has no games with a result for the {team.name} {_where_in(scope)}."
        return TemplateResult(data={"team": team.name, "span": scope.label(), "games": 0}, answer=message)
    subject, alias, line, counted = f"The {team.name}" + _player_splits_narrow_phrase(venue, opponent), "t", _TEAM_LINE, f"{games} game{'s' if games != 1 else ''}"
    data: dict[str, Any] = {"player": None, "team": team.name, "venue": venue, "opponent": opponent.name if opponent else None}
    # The score of a game with no box score is still on record, but its
    # team box stats are NULL - averaged over the rest, and said so.
    blank = con.execute(f"SELECT COUNT(*) FILTER (WHERE fieldGoalsAttempted IS NULL) FROM ({base})", params).fetchone()
    blanks = int(blank[0]) if blank else 0
    caveat = f" Rebounds, assists, 3-pointers and FG% are missing from {blanks} of those games' box scores and are averaged over the rest." if blanks else ""
    return _SplitSubject(scope, base, params, games, first, last, subject, alias, line, counted, data, caveat)


def with_without(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """A team's record in the games a teammate played against the games he
    missed - and, when the subject is a player, that player's averages in each.

    Both groups are shown side by side, because the comparison is the question.
    The subject is a team ("Celtics record without Tatum"), or a player whose
    team is implied ("jalen Duren stats without Cade Cunningham"). The
    teammates come from ``without`` or ``with_player`` (both read from the
    question by the router), or failing those from a second name.

    **A question naming two teammates is divided by both of them.** "Celtics
    record without Tatum and Brown" is the games NEITHER of them played, and
    "record when A and B play" the games both did; the games where one played
    and one sat belong to neither of those, and go on the other row. They have
    to be answered together, because answering for whichever name came first
    is the same fluent answer to a narrower question this module exists to
    stop - and with the second name simply gone, nothing in the answer says
    so. Only the time they were ALL on the same team is counted, for the same
    reason one teammate's tenure is.

    **Only games inside the teammate's time on that team count.** StatMuse
    answers "Nets record without KD" all-time with 439-672: decades of Nets
    games before he arrived, every one a game "without" him. A teammate's time
    on a team is read from the box scores as a run of rows for that team, from
    the first to the last - see ``conditions._stints`` for where a run ends -
    and the answer prints those dates, so what was counted is on the page.
    "Played" means he appeared in the game - a box-score row with minutes, or
    one rebuilt from play-by-play, which has none; a DNP and no box-score row at
    all are both "out", since a missed game appears both ways.

    .. versionadded:: 2.1.0

    .. versionchanged:: 2.2.0
       Divides by every teammate the question names at once, rather than by
       the first of them.
    """
    con = ctx.con
    mate_texts = teammate_names(slots.get("without"))
    asked_without = bool(mate_texts)
    if not asked_without:
        mate_texts = teammate_names(slots.get("with_player"))
    players = slots.get("players")
    listed: list[Any] = players if isinstance(players, list) else []
    texts = list(dict.fromkeys(n.strip() for n in [slots.get("player"), *listed] if isinstance(n, str) and n.strip()))
    team = _optional_team(con, slots.get("team"), season=_slot_season(slots))
    if isinstance(team, TemplateResult):
        return team
    if not mate_texts:
        mate_texts, texts = _with_without_infer_teammate(team, texts)

    scope = _condition_scope(slots.get("season"), slots.get("span"), slots.get("season_type"), _PLAYER_GAME_TABLES)
    resolved = _with_without_resolve(con, mate_texts, texts, scope)
    if isinstance(resolved, TemplateResult):
        return resolved
    mates, subject = resolved

    named = [m.name for m in mates]
    all_of, any_of = _joined(named), _joined(named, "or")
    windows = _with_without_windows(con, mates, subject, team, scope)
    if isinstance(windows, TemplateResult):
        return windows
    if not windows:
        return _with_without_empty_windows(team, subject, mates, named, all_of)

    games, unknown = _with_without_games(con, scope, windows, [m.id for m in mates], subject.id if subject else None)
    team_names = _names(con, "teams", "team_id", {w.team_id for w in windows})
    spell_text = "; ".join(f"{_with_without_stint_team(w, team_names)} {w.first} to {w.last}" for w in windows)
    if not games:
        return _with_without_empty_games(subject, mates, named, all_of, scope, spell_text, unknown)

    groups, rows, team_order = _with_without_rows(games, team_names, asked_without, len(mates), subject, all_of, any_of)
    return _with_without_answer(scope, games, team_names, team_order, windows, subject, mates, named, all_of, asked_without, unknown, groups, rows)


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
        return TemplateResult(data={"teammate": absent.name, "teammates": named, "groups": []}, answer=message)
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
    return TemplateResult(data={"teammate": all_of, "teammates": named, "player": subject.name if subject else None, "groups": []}, answer=message)


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
    return TemplateResult(data={"teammate": all_of, "teammates": named, "player": subject.name if subject else None, "groups": []}, answer=message)


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
    games: list[dict[str, Any]], team_names: dict[str, str], asked_without: bool, n_mates: int, subject: Entity | None, all_of: str, any_of: str
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
            rows.append((f"{prefix}{whom} {'played' if played else 'out'}", cells))
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
) -> TemplateResult:
    """The table and the notes under it: title, counted span, what "played"
    means for one teammate against two, and the caveats for games with no box
    score and, with a player subject, what the extra columns are."""
    label = scope.label(min(g["season"] for g in games), max(g["season"] for g in games))
    counted_teams = ", ".join(team_names[t] for t in team_order)
    title, headers, whose = _with_without_heading(subject, mates, named, all_of, counted_teams, label)
    used = [w for w in windows if any(g["team_id"] == w.team_id and w.first <= g["day"] <= w.last for g in games)]
    spell_text = "; ".join(f"{team_names[w.team_id]} {w.first} to {w.last}" if len(team_order) > 1 else f"{w.first} to {w.last}" for w in used)
    notes = _with_without_notes(whose, spell_text, mates, asked_without, all_of, unknown, subject)
    answer = _table(title, headers, rows) + "\n" + " ".join(notes)
    tenure = [{"team": team_names[w.team_id], "from": str(w.first), "to": str(w.last)} for w in used]
    data = {"teammate": all_of, "teammates": named, "player": subject.name if subject else None, "teams": [team_names[t] for t in team_order], "span": label, "groups": groups, "tenure": tenure}
    return TemplateResult(data=data, answer=answer)


def _with_without_heading(subject: Entity | None, mates: list[Entity], named: list[str], all_of: str, counted_teams: str, label: str) -> tuple[str, list[str], str]:
    """The table's title and headers, and the phrase naming whose time together
    is counted - with a player subject's own columns added to the headers."""
    headers = ["G", "W-L", "Win%", "Margin"]
    if subject is None:
        title = f"{counted_teams} with and without {all_of}, {label}:"
        whose = f"{all_of}'s time with the team" if len(mates) == 1 else f"the time {all_of} were on the team together"
        return title, headers, whose
    title = f"{subject.name} with and without {all_of} ({counted_teams}), {label}:"
    whose = f"the time {subject.name} and {all_of} were both on the team" if len(mates) == 1 else f"the time {_joined([subject.name, *named])} were on the team together"
    headers += ["Played", "MIN", "PTS", "REB", "AST", "FG%"]
    return title, headers, whose


def _with_without_notes(whose: str, spell_text: str, mates: list[Entity], asked_without: bool, all_of: str, unknown: int, subject: Entity | None) -> list[str]:
    """The caveats under the table: what "played" means for one teammate
    against two, games with no box score, and a player subject's extra
    columns."""
    notes = [f"Counted: games inside {whose} ({spell_text}), which runs from the first box score that lists {'him' if len(mates) == 1 else 'them'} there to the last."]
    if len(mates) == 1:
        notes.append(f"Played means {all_of} appeared in the game; out is a DNP or no box-score row at all.")
    elif asked_without:
        notes.append(f"Out means none of {all_of} appeared in the game - a DNP or no box-score row at all; the other row is every game at least one of them played.")
    else:
        notes.append(f"Played means every one of {all_of} appeared in the game; the other row is every game at least one of them missed - a DNP or no box-score row at all.")
    if unknown:
        # A game with no box score is not a game he missed - see
        # conditions._box_missing - so it is on neither side, and said so.
        notes.append(f"{unknown} game{'' if unknown == 1 else 's'} inside that time {'has' if unknown == 1 else 'have'} no box score, so whether he played is unknown; they are on neither side.")
    if subject is not None:
        notes.append(f"G, W-L and margin are the team's; Played counts {subject.name}'s games, and his averages are over those.")
    return notes


def record_when(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """A team's record above and below a stat threshold, beside its record the
    other side of it.

    "Sixers record when Embiid scores 30" - a count of wins and losses that
    ``threshold_count`` cannot give, since it counts games and not results. The
    stat is whitelisted like everywhere else, and both rows are always shown:
    the question is a comparison even when it names only one side.

    With a ``player`` slot, the threshold is HIS: only games he played count,
    and the team is his team in each one, so a traded player's record follows
    him; a named ``team`` narrows it to that one. With no player at all - "what
    was the celtics record when they scored 120 points" (ISSUES.md #144) - the
    threshold is the TEAM's own, read straight from the team named in ``team``.
    A question naming neither refuses saying so, not with the player-only
    "record_when needs a player" message that would name the wrong cause for a
    team question with nobody to resolve.

    .. versionadded:: 2.1.0
    """
    con = ctx.con
    if not slots.get("player"):
        return _record_when_team_answer(con, slots)
    stat = slots.get("stat")
    column, threshold = _record_when_stat(stat, slots.get("threshold"))
    scope = _condition_scope(slots.get("season"), slots.get("span"), slots.get("season_type"), _PLAYER_GAME_TABLES)
    player = _resolved_player(con, slots.get("player"), "record_when needs a player", available=_BOX_SCORES, season=scope.season, through=_career_end(scope.season))
    if isinstance(player, TemplateResult):
        return player
    team = _optional_team(con, slots.get("team"), season=_slot_season(slots))
    if isinstance(team, TemplateResult):
        return team

    query = _record_when_query(con, scope, player, team, column, threshold)
    if isinstance(query, TemplateResult):
        return query
    found, names, base, params = query
    return _record_when_answer(con, scope, player, stat, threshold, found, names, base, params)


def _record_when_stat(stat: Any, threshold: Any) -> tuple[str, int]:
    """The box-score column a whitelisted stat reads, and the threshold
    narrowed to ``int`` - or the refusal for an unknown stat or a threshold
    that is not a positive integer."""
    column = THRESHOLD_STAT_COLUMNS.get(stat) if isinstance(stat, str) else None
    if column is None or not isinstance(threshold, int) or isinstance(threshold, bool) or threshold < 1:
        raise TemplateUnsupported(f"record_when needs a known stat and a positive threshold, got {stat!r}/{threshold!r}")
    return column, threshold


def _record_when_query(
    con: duckdb.DuckDBPyConnection, scope: _Scope, player: Entity, team: Entity | None, column: str, threshold: int
) -> tuple[list[Any], dict[str, str], str, dict[str, Any]] | TemplateResult:
    """The player's games grouped by whether he reached the threshold, and the
    team names for whichever teams he suited up for in them - or the refusal
    for a player with no games in scope at all."""
    params: dict[str, Any] = {**scope.params(), "player": player.id}
    if team is not None:
        params["team"] = team.id
    base = _player_games(scope, extra=" AND pgl.team_id = $team" if team else "", box=box_source(con))
    found = con.execute(
        f"WITH p AS ({base}) SELECT p.{column} >= $threshold, COUNT(*), COUNT(*) FILTER (WHERE p.won), AVG(p.team_score - p.opponent_score), "
        "MIN(p.season), MAX(p.season), list(DISTINCT p.team_id) FROM p GROUP BY 1",
        {**params, "threshold": threshold},
    ).fetchall()
    if not found:
        return _no_games(con, player, scope, team)
    team_ids = {str(t) for row in found for t in row[6]}
    # One season names each team as it was then. A career groups every season
    # of an id together, so it keeps today's name rather than picking one era.
    names = {team_id: season_name(team_id, scope.season, name) for team_id, name in _names(con, "teams", "team_id", team_ids).items()}
    return found, names, base, params


def _record_when_group(by_hit: dict[bool, Any], hit: bool | None) -> dict[str, Any]:
    """The team's record in the games he reached the threshold (True), fell short (False), or both (None)."""
    rows = [by_hit[h] for h in ((hit,) if hit is not None else (True, False)) if h in by_hit]
    games = sum(int(r[1]) for r in rows)
    wins = sum(int(r[2]) for r in rows)
    margin = sum((r[3] or 0) * int(r[1]) for r in rows) / games if games else None
    return {"games": games, "wins": wins, "losses": games - wins, "avg_margin": margin}


def _record_when_answer(
    con: duckdb.DuckDBPyConnection, scope: _Scope, player: Entity, stat: Any, threshold: int, found: list[Any], names: dict[str, str], base: str, params: dict[str, Any]
) -> TemplateResult:
    """The two-row table - reached the threshold, fell short - and the caveats
    beside it: games with no box score, and the coverage floor."""
    by_hit = {bool(row[0]): row for row in found}
    unit = f"{STAT_LABELS.get(stat or '', stat or '')}s"
    reached, short, every = _record_when_group(by_hit, True), _record_when_group(by_hit, False), _record_when_group(by_hit, None)
    label = scope.label(min(r[4] for r in found), max(r[5] for r in found))
    teams = sorted(names.values())
    whose = f"{teams[0]} record" if len(teams) == 1 else f"Record of {player.name}'s teams ({', '.join(teams)})"
    title = f"{whose} when {player.name} had {threshold}+ {unit}, {label}:"
    rows = [(f"{threshold}+ {unit}", reached), (f"under {threshold} {unit}", short), ("all his games", every)]
    table = _table(title, ["G", "W-L", "Win%", "Margin"], [(name, [str(g["games"]), f"{g['wins']}-{g['losses']}", _win_pct(g["wins"], g["games"]), _margin(g["avg_margin"])]) for name, g in rows])
    caveat = _unseen_note(_unseen(con, scope, base, params, box_source(con)))
    answer = f"{table}\nOver the {every['games']} games he played; a game he missed is in neither row.{scope.floor_note(min(r[4] for r in found))}{caveat}"
    data = {"player": player.name, "teams": teams, "stat": stat, "threshold": threshold, "span": label, "reached": reached, "fell_short": short}
    return TemplateResult(data=data, answer=answer)


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


def _record_when_team_stat(stat: Any, threshold: Any) -> tuple[str, int]:
    """The SQL a team's threshold reads, and the threshold narrowed to
    ``int`` - or the refusal, which names the real cause rather than
    record_when's player-only "needs a player" message (AGENTS.md, "the same
    bug has a mirror image"): an unrecognized stat or bad threshold reads the
    same as the player branch's own refusal, and a stat that is only ever a
    PLAYER's (a team has no minutes total) says so by name."""
    if not isinstance(threshold, int) or isinstance(threshold, bool) or threshold < 1:
        raise TemplateUnsupported(f"record_when needs a known stat and a positive threshold, got {stat!r}/{threshold!r}")
    if not isinstance(stat, str) or stat not in THRESHOLD_STAT_COLUMNS:
        raise TemplateUnsupported(f"record_when needs a known stat and a positive threshold, got {stat!r}/{threshold!r}")
    column = _RECORD_WHEN_TEAM_STAT_COLUMNS.get(stat)
    if column is None:
        # The only whitelisted player stat left with no team mapping above.
        raise TemplateUnsupported(f"record_when has no team figure for {STAT_LABELS.get(stat, stat)}s - a team has no minutes total")
    return column, threshold


def _record_when_team_unseen(con: duckdb.DuckDBPyConnection, scope: _Scope, team: Entity, column: str) -> int:
    """How many of the team's own games in this scope have a team_box_stats
    row but no usable value for this stat - the games a non-points threshold
    cannot see (AGENTS.md, "Whole team-seasons of box scores are empty").
    Counted straight off team_box_stats with the same predicate
    _record_when_team_query excludes rows on, rather than through
    conditions._box_missing, which answers a different question (no PLAYER
    appeared in the game at all, not this one team column)."""
    params = {**scope.params(), "team": team.id}
    row = con.execute(f"SELECT COUNT(*) FROM team_box_stats tbs WHERE tbs.team_id = $team AND {scope.where('tbs')} AND ({column}) IS NULL", params).fetchone()
    return int(row[0]) if row else 0


def _record_when_team_unseen_note(count: int, unit: str) -> str:
    """The team counterpart to conditions._unseen_note: how many of the
    team's games in this span carry no usable figure for this stat at all, so
    they sit in neither row. Not shown for `points`, which reads the game's
    own score and always has one."""
    if not count:
        return ""
    return f" {count} of their games in that span have no {unit} figure on record, so they are in neither row."


def _record_when_team_no_games(con: duckdb.DuckDBPyConnection, scope: _Scope, team: Entity, stat: Any) -> TemplateResult:
    """Nothing to report for a team's own threshold - naming which fact is
    missing, the discipline templates.common._no_games applies for a player:
    a season the team actually played, answered "no games", would be the
    false-cause answer AGENTS.md warns against - and for a non-points stat the
    missing fact can be the STAT rather than the games themselves (the empty
    2013-2018 team boxes, see _RECORD_WHEN_TEAM_STAT_COLUMNS)."""
    params = {**scope.params(), "team": team.id}
    total = con.execute(f"SELECT COUNT(*) FROM real_games g WHERE (g.home_team_id = $team OR g.away_team_id = $team) AND {scope.where('g')}", params).fetchone()
    games = int(total[0]) if total else 0
    unit = f"{STAT_LABELS.get(stat or '', stat or '')}s"
    if games and stat != "points":
        which = "it" if games == 1 else "any of them"
        message = f"The warehouse has {games} game{'' if games == 1 else 's'} with a result for the {team.name} {_where_in(scope)}, but no {unit} figure on record for {which}."
    else:
        message = f"The warehouse has no games with a result for the {team.name} {_where_in(scope)}."
    return TemplateResult(data={"team": team.name, "span": scope.label(), "games": 0}, answer=message)


def _record_when_team_query(con: duckdb.DuckDBPyConnection, scope: _Scope, team: Entity, stat: Any, column: str, threshold: int) -> list[Any] | TemplateResult:
    """The team's own games grouped by whether IT reached the threshold - the
    team counterpart to _record_when_query, with no player to key on.

    `points` is read straight off real_games, home/away resolved from the
    team id rather than team_box_stats' `home_away` (see
    _RECORD_WHEN_TEAM_STAT_COLUMNS for why). Every other stat joins
    team_box_stats and excludes a game with no value there - the same "team's
    own row decides which side it was on" join conditions._team_games uses,
    since a home/away-only join silently returns half the games - and
    _record_when_team_unseen counts the excluded games back for the caveat.
    """
    params: dict[str, Any] = {**scope.params(), "team": team.id}
    if stat == "points":
        base = f"""
            SELECT g.event_id, g.season,
                   CASE WHEN g.home_team_id = $team THEN g.home_score ELSE g.away_score END AS stat_value,
                   CASE WHEN g.home_team_id = $team THEN g.home_score ELSE g.away_score END AS team_score,
                   CASE WHEN g.home_team_id = $team THEN g.away_score ELSE g.home_score END AS opponent_score,
                   g.winner_team_id = $team AS won
            FROM real_games g
            WHERE (g.home_team_id = $team OR g.away_team_id = $team) AND {scope.where("g")}"""
    else:
        base = f"""
            SELECT tbs.event_id, tbs.season, {column} AS stat_value,
                   CASE WHEN tbs.home_away = 'home' THEN g.home_score ELSE g.away_score END AS team_score,
                   CASE WHEN tbs.home_away = 'home' THEN g.away_score ELSE g.home_score END AS opponent_score,
                   g.winner_team_id = tbs.team_id AS won
            FROM team_box_stats tbs JOIN real_games g ON g.event_id = tbs.event_id AND g.season = tbs.season
            WHERE tbs.team_id = $team AND {scope.where("tbs")} AND ({column}) IS NOT NULL"""
    found = con.execute(
        f"WITH t AS ({base}) SELECT t.stat_value >= $threshold, COUNT(*), COUNT(*) FILTER (WHERE t.won), AVG(t.team_score - t.opponent_score), MIN(t.season), MAX(t.season) FROM t GROUP BY 1",
        {**params, "threshold": threshold},
    ).fetchall()
    return found if found else _record_when_team_no_games(con, scope, team, stat)


def _record_when_team_answer_table(con: duckdb.DuckDBPyConnection, scope: _Scope, team: Entity, stat: Any, column: str, threshold: int, found: list[Any]) -> TemplateResult:
    """The two-row table for a team's own record above/below its threshold -
    the team counterpart to _record_when_answer, with no player to key on and
    a team pronoun in place of a player's."""
    by_hit = {bool(row[0]): row for row in found}
    unit = f"{STAT_LABELS.get(stat or '', stat or '')}s"
    reached, short, every = _record_when_group(by_hit, True), _record_when_group(by_hit, False), _record_when_group(by_hit, None)
    label = scope.label(min(r[4] for r in found), max(r[5] for r in found))
    title = f"{team.name} record when they had {threshold}+ {unit}, {label}:"
    rows = [(f"{threshold}+ {unit}", reached), (f"under {threshold} {unit}", short), ("all their games", every)]
    table = _table(title, ["G", "W-L", "Win%", "Margin"], [(name, [str(g["games"]), f"{g['wins']}-{g['losses']}", _win_pct(g["wins"], g["games"]), _margin(g["avg_margin"])]) for name, g in rows])
    caveat = "" if stat == "points" else _record_when_team_unseen_note(_record_when_team_unseen(con, scope, team, column), unit)
    answer = f"{table}\nOver the {every['games']} games with a result.{scope.floor_note(min(r[4] for r in found))}{caveat}"
    data = {"team": team.name, "stat": stat, "threshold": threshold, "span": label, "reached": reached, "fell_short": short}
    return TemplateResult(data=data, answer=answer)


def _record_when_team_answer(con: duckdb.DuckDBPyConnection, slots: dict[str, Any]) -> TemplateResult:
    """The team half of ``record_when``: a record above/below a threshold of
    the TEAM's own scoring or another box-score stat, reached when the
    question names no player at all ("what was the celtics record when they
    scored 120 points" - ISSUES.md #144). A question naming neither a player
    nor a team is unanswerable and refuses saying so - not with the
    player-only "record_when needs a player" message, which would name the
    wrong cause for a team question with nobody to resolve."""
    team = _optional_team(con, slots.get("team"), season=_slot_season(slots))
    if isinstance(team, TemplateResult):
        return team
    if team is None:
        raise TemplateUnsupported("record_when needs a player or a team")
    stat = slots.get("stat")
    column, threshold = _record_when_team_stat(stat, slots.get("threshold"))
    scope = _condition_scope(slots.get("season"), slots.get("span"), slots.get("season_type"), _TEAM_GAME_TABLES)
    misfiled = _misfiled_postseason(scope)
    if misfiled is not None:
        return misfiled
    query = _record_when_team_query(con, scope, team, stat, column, threshold)
    if isinstance(query, TemplateResult):
        return query
    return _record_when_team_answer_table(con, scope, team, stat, column, threshold, query)


def streak(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """The longest run of consecutive games meeting a condition.

    For a team, its longest winning or losing run (``kind``) - within one
    season, as the record book counts them. With no team named, the league's
    longest, one per team-season ("longest winning streak in the NBA this
    season"). For a player, his longest run of games with ``stat`` at or above
    ``threshold`` ("most 40 point games in a row"), or with no stat his team's
    longest run of wins in games he played; with no player named, the league's
    longest such run. A player's run counts only games he played - a game he
    missed neither extends it nor ends it - and in a career it carries across
    seasons, as consecutive-game records do (Curry's 3-pointer streak ran
    through four of them). Each answer says which of those rules applied.

    Games are ordered by their US Eastern date, so two nights either side of
    midnight UTC land in the order they were played.

    .. versionadded:: 2.1.0
    """
    con = ctx.con
    want_win = slots.get("kind") != "loss"
    stat, threshold = slots.get("stat"), slots.get("threshold")
    column, by_stat, unit = _streak_kind(stat, threshold)
    result = "winning streak" if want_win else "losing streak"
    # A player's rows carry the stat as `value` (see conditions._player_streak_rows); a team's carry only `won`.
    hit = "x.value >= $threshold" if by_stat else "x.won = $want"
    condition: dict[str, Any] = {"threshold": threshold} if by_stat else {"want": want_win}
    span = slots.get("span")
    team = _optional_team(con, slots.get("team"), season=_slot_season(slots))
    if isinstance(team, TemplateResult):
        return team

    name = slots.get("player")
    if isinstance(name, str) and name.strip():
        return _streak_player(con, name, slots, span, team, by_stat, column, threshold, unit, want_win, result, hit, condition)

    if team is not None:
        return _streak_team(con, slots, span, team, by_stat, want_win, result, hit, condition)

    # Nobody named: the league's longest, each team-season or player once.
    return _streak_league(con, slots, span, by_stat, column, stat, threshold, unit, want_win, result, hit, condition)


def _streak_kind(stat: Any, threshold: Any) -> tuple[str | None, bool, str]:
    """Validate a streak's stat/threshold pair and derive its per-game column,
    whether it is a stat streak at all (as against one of wins or losses), and
    the unit its threshold is counted in.

    Raises :class:`TemplateUnsupported` for a named stat with no per-game
    column, or a stat/threshold pair that only half-names a condition -
    "most consecutive double-doubles" must not come back as a win streak."""
    column = THRESHOLD_STAT_COLUMNS.get(stat) if isinstance(stat, str) else None
    named_stat = isinstance(stat, str) and bool(stat.strip()) and stat.strip().casefold() not in _RESULT_STATS
    has_threshold = isinstance(threshold, int) and not isinstance(threshold, bool)
    if named_stat and column is None:
        raise TemplateUnsupported(f"no per-game column for stat {stat!r}")
    if has_threshold != (column is not None) or (isinstance(threshold, int) and threshold < 1):
        raise TemplateUnsupported(f"a streak of a stat needs both a known stat and a positive threshold, got {stat!r}/{threshold!r}")
    by_stat = column is not None
    unit = f"{STAT_LABELS.get(stat or '', stat or '')}s"
    return column, by_stat, unit


def _streak_player(
    con: duckdb.DuckDBPyConnection,
    name: str,
    slots: dict[str, Any],
    span: Any,
    team: Entity | None,
    by_stat: bool,
    column: str | None,
    threshold: Any,
    unit: str,
    want_win: bool,
    result: str,
    hit: str,
    condition: dict[str, Any],
) -> TemplateResult:
    """A named player's longest run of games meeting the condition."""
    scope = _condition_scope(slots.get("season"), span, slots.get("season_type"), _PLAYER_GAME_TABLES)
    player = _resolved_player(con, name, available=_BOX_SCORES, season=scope.season, through=_career_end(scope.season))
    if isinstance(player, TemplateResult):
        return player
    params: dict[str, Any] = {**scope.params(), "player": player.id}
    if team is not None:
        params["team"] = team.id
    base = _player_games(scope, extra=" AND pgl.team_id = $team" if team else "", box=box_source(con))
    games, first, last = _totals(con, base, params)
    if not games:
        return _no_games(con, player, scope, team)
    rows_sql = _player_streak_rows(scope, base, f"p.{column}" if by_stat else "NULL", box_source(con))
    runs = _longest_runs(con, rows_sql, {**params, **condition}, ("athlete_id",), hit, 3, best_per_partition=False)
    label = scope.label(first, last)
    what = f"consecutive games with {threshold}+ {unit}" if by_stat else f"{result} in games he played"
    rule = "Only games he played count: a game he missed neither extends the run nor ends it" + (", and a run carries on from one season into the next." if scope.season is None else ".")
    rule += _UNSEEN_ENDS_RUN if _unseen(con, scope, base, params, box_source(con)) else ""
    if not runs:
        never = f"never had a game with {threshold}+ {unit}" if by_stat else f"never {'won' if want_win else 'lost'} a game he played"
        return TemplateResult(data={"player": player.name, "span": label, "streaks": []}, answer=f"{player.name} {never} in the {label}.")
    return _single_streak(f"{player.name}'s longest run of {what}" if by_stat else f"{player.name}'s longest {what}", label, runs, rule, scope, {"player": player.name})


def _streak_team(
    con: duckdb.DuckDBPyConnection,
    slots: dict[str, Any],
    span: Any,
    team: Entity,
    by_stat: bool,
    want_win: bool,
    result: str,
    hit: str,
    condition: dict[str, Any],
) -> TemplateResult:
    """A named team's longest run of wins or losses in a season."""
    if by_stat:
        raise TemplateUnsupported("a team's streak is of wins or losses, not of a stat")
    scope = _condition_scope(slots.get("season"), span, slots.get("season_type"), _TEAM_GAME_TABLES)
    misfiled = _misfiled_postseason(scope)
    if misfiled is not None:
        return misfiled
    params = {**scope.params(), "team": team.id}
    base = _team_games(scope, " AND tbs.team_id = $team")
    games, first, last = _totals(con, base, params)
    label = scope.label(first, last)
    if not games:
        return TemplateResult(data={"team": team.name, "streaks": []}, answer=f"The warehouse has no games with a result for the {team.name} {_where_in(scope)}.")
    runs = _longest_runs(con, base, {**params, **condition}, ("team_id", "season"), hit, 3, best_per_partition=False)
    if not runs:
        return TemplateResult(data={"team": team.name, "span": label, "streaks": []}, answer=f"The {team.name} did not {'win' if want_win else 'lose'} a game in the {label}.")
    return _single_streak(
        f"The {team.name}' longest {result}" if team.name.endswith("s") else f"The {team.name}'s longest {result}",
        label,
        runs,
        "Streaks are counted within one season.",
        scope,
        {"team": team.name},
    )


def _streak_league_by_stat(
    con: duckdb.DuckDBPyConnection, scope: _Scope, column: str | None, threshold: Any, unit: str, hit: str, condition: dict[str, Any], limit: int
) -> tuple[str, list[dict[str, Any]], str, list[str], str]:
    """Each player's longest run of games meeting the stat threshold, one per player."""
    base = _player_streak_rows(scope, _player_games(scope, player="", box=box_source(con)), f"p.{column}", box_source(con))
    runs = _longest_runs(con, base, {**scope.params(), **condition}, ("athlete_id",), hit, limit, best_per_partition=True)
    names = _names(con, "players", "athlete_id", [r["athlete_id"] for r in runs])
    what, who = f"run of consecutive games with {threshold}+ {unit}", [names[r["athlete_id"]] for r in runs]
    rule = "Each player's longest run, counting only games he played" + (", carried across seasons." if scope.season is None else ".")
    rule += _UNSEEN_ENDS_RUN if _totals(con, _box_missing(scope, box_source(con)), scope.params())[0] else ""
    return base, runs, what, who, rule


def _streak_league_by_result(con: duckdb.DuckDBPyConnection, scope: _Scope, result: str, hit: str, condition: dict[str, Any], limit: int) -> tuple[str, list[dict[str, Any]], str, list[str], str]:
    """Each team's longest run of wins or losses in a season, one per team-season."""
    # Teams the `teams` table does not hold are exhibition opponents that
    # turn up in a few regular-season rows (1992-2000), not franchises.
    base = _team_games(scope, " AND tbs.team_id IN (SELECT team_id FROM teams)")
    runs = _longest_runs(con, base, {**scope.params(), **condition}, ("team_id", "season"), hit, limit, best_per_partition=True)
    names = _names(con, "teams", "team_id", [r["team_id"] for r in runs])
    what = result
    # Every run lies inside one season, so each is named as its team was
    # that season. This used to add "franchises are named as they are
    # today" to every all-seasons answer, which is what it was.
    who = [season_name(r["team_id"], int(r["season"]), names[r["team_id"]]) + (f" ({r['season']})" if scope.season is None else "") for r in runs]
    rule = "Each team's longest in a season, counted within that season."
    return base, runs, what, who, rule


def _streak_league(
    con: duckdb.DuckDBPyConnection,
    slots: dict[str, Any],
    span: Any,
    by_stat: bool,
    column: str | None,
    stat: Any,
    threshold: Any,
    unit: str,
    want_win: bool,
    result: str,
    hit: str,
    condition: dict[str, Any],
) -> TemplateResult:
    """The league's longest run with nobody named: one per player (a stat
    streak) or one per team-season (a win/loss streak)."""
    tables = _PLAYER_GAME_TABLES if by_stat else _TEAM_GAME_TABLES
    scope = _condition_scope(slots.get("season"), span, slots.get("season_type"), tables)
    misfiled = _misfiled_postseason(scope)
    if misfiled is not None:
        return misfiled
    limit = _clamp_limit(slots.get("limit"), _DEFAULT_STREAK_LIMIT)
    if by_stat:
        base, runs, what, who, rule = _streak_league_by_stat(con, scope, column, threshold, unit, hit, condition, limit)
    else:
        base, runs, what, who, rule = _streak_league_by_result(con, scope, result, hit, condition, limit)
    # The span searched, not the seasons the leaders' runs happen to fall in:
    # "1997-2023" under a question about every season reads as a narrower search.
    _, first, last = _totals(con, base, scope.params())
    label = scope.label(first, last)
    if not runs:
        nobody = f"No player had a game with {threshold}+ {unit}" if by_stat else "No team has a game with a result"
        return TemplateResult(data={"span": label, "streaks": []}, answer=f"{nobody} {_where_in(scope)}.")
    streaks = [
        {"name": n, "season": r["first_season"] if not by_stat else None, "length": r["length"], "from": str(r["first_day"]), "to": str(r["last_day"]), "open": bool(r["open"])}
        for n, r in zip(who, runs, strict=True)
    ]
    top = [s for s in streaks if s["length"] == streaks[0]["length"]]
    # A tie is reported as a tie, the way threshold_count reports one.
    leaders = " and ".join(s["name"] for s in top)
    headline = f"{leaders} {'shared' if len(top) > 1 else 'had'} the longest {what} of the {label}: {streaks[0]['length']} games."
    rows = [(s["name"], [str(s["length"]), s["from"], s["to"] + (" *" if s["open"] else "")]) for s in streaks]
    footnote = " * still going at the last game on record." if any(s["open"] for s in streaks) else ""
    answer = f"{headline}\n" + _table(f"Longest, {label}:", ["games", "from", "to"], rows) + f"\n{rule}{footnote}"
    return TemplateResult(
        data={"span": label, "stat": stat if by_stat else None, "threshold": threshold if by_stat else None, "kind": None if by_stat else ("win" if want_win else "loss"), "streaks": streaks},
        answer=answer,
    )


def _single_streak(subject: str, label: str, runs: list[dict[str, Any]], rule: str, scope: _Scope, who: dict[str, Any]) -> TemplateResult:
    """One named player's or team's longest run, with any run that ties it."""
    top = runs[0]
    ties = [r for r in runs[1:] if r["length"] == top["length"]]
    season = f" (the {top['first_season']} season)" if scope.season is None and top["first_season"] == top["last_season"] else ""
    answer = f"{subject}, {label}: {top['length']} games, {top['first_day']} to {top['last_day']}{season}."
    if ties:
        answer += " Matched by " + ", ".join(f"{r['first_day']} to {r['last_day']}" for r in ties) + "."
    if top["open"] and (scope.season is None or scope.season == current_season()):
        answer += " It was still going at the last game on record."
    streaks = [{"length": r["length"], "from": str(r["first_day"]), "to": str(r["last_day"]), "open": bool(r["open"])} for r in [top, *ties]]
    return TemplateResult(data={**who, "span": label, "streaks": streaks}, answer=f"{answer}\n{rule}")
