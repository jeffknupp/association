"""Team records, team stats, team rankings and ESPN's outlook for a team.

.. versionadded:: 3.0.0
   Split out of the former ``association.query.templates`` module.
"""

from __future__ import annotations

import re
from typing import Any

import duckdb

from association.nba.coverage import unavailable
from association.nba.franchises import season_name_sql
from association.nba.season import current_season
from association.query.reading import Reading, Scope

from ..calendar import CalendarNarrowing, parse_situation
from ..conditions import _MONTH_NAMES, _season_month_order, _table
from ..entities import Entity
from ..notes import note
from ..team_games import TeamNarrowed
from ..team_games import games_subquery as team_games_subquery
from ..team_games import rows_sql as team_rows_sql
from ..team_metrics import (
    FIRST_FULL_REGULAR_SEASON,
    TEAM_GAMES_SQL,
    games_scope,
)
from .common import (
    TemplateContext,
    TemplateResult,
    TemplateUnsupported,
    _format_value,
    _joined,
    _ordinal,
    _period,
    _resolved_team,
    _season_name,
    _slot_season,
    _span_of,
    _team_span_clause,
    _validated_until,
)

# Conference and division words. The warehouse holds no membership for either:
# no table maps a team to one, and standings carry only each team's record in
# its OWN conference's and division's games. So a team slot naming one cannot be
# answered, and is refused by name. Resolved as a team it would match nothing
# and be refused for the wrong cause.
_CONFERENCE_WORDS = re.compile(r"\b(?:conferences?|divisions?|east(?:ern)?|west(?:ern)?|atlantic|central|southeast|northwest|pacific|southwest)\b", re.IGNORECASE)


def conference_refusal(scope: Scope) -> TemplateResult | None:
    """A refusal naming the real cause, if any team slot holds a conference or a
    division rather than a team.

    .. versionadded:: 5.0.0
       Public, for the team-season readers (``compose.team_stats``);
       ``_conference_refusal`` until then.
    """
    named = next((c for c in (scope.team, scope.opponent, *scope.teams) if c and _CONFERENCE_WORDS.search(c)), None)
    if named is None:
        return None
    message = (
        f"The warehouse has no conference or division membership for any team, so nothing about {named!r} can be tallied from it. "
        "The only conference figure it holds is each team's record in its own conference's games."
    )
    return TemplateResult(data={"message": message, "unanswerable": named}, answer=message)


_conference_refusal = conference_refusal


def _record_pct(value: float) -> str:
    """Basketball convention for a winning percentage: .646, not 0.646."""
    text = f"{value:.3f}"
    return text[1:] if text.startswith("0") else text


def _parse_record(text: Any) -> tuple[int, int] | None:
    """standings' "Home"/"Road" strings: '30-10' -> (30, 10)."""
    if not isinstance(text, str):
        return None
    match = re.fullmatch(r"\s*(\d+)-(\d+)\s*", text)
    return (int(match.group(1)), int(match.group(2))) if match else None


def _tally(wins: int, losses: int) -> str:
    return f"{wins:,}-{losses:,} ({_record_pct(wins / (wins + losses))})" if wins + losses else "0-0"


def _possessive(name: str) -> str:
    """ "the Knicks'" but "the Thunder's" - a team name is plural only sometimes."""
    return f"{name}'" if name.endswith("s") else f"{name}'s"


VENUE_WORDS = {"home": "at home", "away": "on the road"}


# A `situation` slot's only honored shape: "in october". check_scope lets any
# `situation` value through for team_record now (see HONORED_SCOPING), so this
# is where the ones that are not a real calendar month are still refused - the
# same discipline #84 already established: a weekday, a holiday, an age, "vs
# southeast division" or a window ("since january 31st") name nothing this
# table can filter on, and only a bare "in <month>" does. Deliberately
# anchored to that exact shape rather than searching for a month name
# anywhere in the text, so "since january 31st" - a window, not a month filter
# - is not mistaken for one.
_MONTH_SITUATION = re.compile(
    r"^in (january|february|march|april|may|june|july|august|september|october|november|december)$",
    re.IGNORECASE,
)


def _team_record_month(situation: str | None) -> int | None:
    """The calendar month ``situation`` names ("in october" -> 10), or None for
    anything else - including a value naming no month at all. A real column
    (the game's own Eastern date) can filter to a month; nothing here can
    filter to a weekday, an age or a window, so those stay refused.

    .. versionadded:: 4.3.0
    """
    if situation is None:
        return None
    match = _MONTH_SITUATION.fullmatch(situation.strip())
    if match is None:
        return None
    return [name.lower() for name in _MONTH_NAMES].index(match.group(1).lower()) + 1


def _team_record_month_and_split(split: str | None, situation: str | None, limit: int | None) -> tuple[str | None, int | None, CalendarNarrowing | None]:
    """The validated ``split``, the calendar ``month`` a question narrows to,
    and - step 3, K1 - the fuller calendar narrowing (a weekday, a fixed
    holiday, or "since <month day>") ``situation`` names when it is not a bare
    month. Or the refusal for a ``split`` that is not "month", a ``situation``
    naming no calendar narrowing at all, a non-month narrowing that combines
    with a month split (a table already broken out by every month has no
    further one-weekday-or-holiday reading built), or a bare ``limit`` with
    none of the three. Pulled out of ``team_record`` itself so that function
    reads as one linear sequence of steps rather than growing a branch for
    each of these; called with the scope's fields themselves (``scope.split``
    and so on), not the whole Scope, so team_record's own source still names
    every slot it honors - test_every_template_honoring_a_scope_slot_actually_reads_it
    checks that literally.

    .. versionchanged:: 4.4.0
       Reads the fuller calendar narrowing too (step 3, K1), not only a bare
       month - "the Knicks' record on Christmas" and "... on Saturdays" now
       answer, where before only "... in October" did.
    """
    if split is not None and split != "month":
        raise TemplateUnsupported(f"no split named {split!r}")
    month = _team_record_month(situation)
    narrowing = None
    if situation and month is None:
        narrowing = parse_situation(situation)
        if narrowing is None:
            raise TemplateUnsupported(f'no calendar narrowing in situation {situation!r} - a weekday, a month, a holiday or "since <day>" is read; an age, a conference or a division is not')
        if split == "month":
            raise TemplateUnsupported("a month split already covers every month; narrowing it further to one weekday or holiday is not built")
    if limit and split is None and month is None and narrowing is None:
        # "how did they do in their last 10 games?" is a game_log question -
        # standings only has the full-season record, and answering with it
        # under a "last 10" question is a silent substitution. game_log already
        # tallies the record over exactly the games it lists. Measured over
        # this corpus, a month narrowing or a by-month split never carries a
        # real limit of its own - the router fills `limit` with a default
        # (commonly 12, one slot per month) whether or not the question named
        # one - so only a bare limit, with none of the three, is read as "last
        # N games".
        raise TemplateUnsupported("a record over a limited set of games is a game_log question")
    return split, month, narrowing


def _span_phrase(since: int | None, until: int | None) -> str:
    """ " since 2022"" or, once ``until`` bounds it (step 3, K1), " from 2011
    through 2019"" - or "" for no span at all. One place for the wording so
    every reader of a since-bounded team record, leaderboard or head-to-head
    says a bounded range the same way.

    .. versionadded:: 4.4.0
    """
    if since is None:
        return ""
    return f" from {since} through {until}" if until is not None else f" since {since}"


def _calendar_phrase(month: int | None, narrowing: CalendarNarrowing | None) -> str:
    """ " in October"" (the bare-month case ``team_record`` has always read) or
    the fuller calendar label a general ``situation`` narrowing carries (step
    3, K1: "on Saturdays", "on Christmas Day", "since January 31") - or "" for
    neither.

    .. versionadded:: 4.4.0
    """
    if narrowing is not None:
        return f" {narrowing.label}"
    return f" in {_MONTH_NAMES[month - 1]}" if month is not None else ""


def _team_record_since(since: int | None, career: bool, season: int | None) -> int | None:
    """The validated ``since`` slot, the same int-or-None reading
    ``team_leaderboard`` already gives it, plus the two conflicts that are
    ``team_record``'s own: ``since`` and a single named season are two
    different spans (the same conflict ``span`` "career" already raises for a
    season), and so are ``since`` and "career" - the same answer put two ways,
    which is worth a refusal rather than a silent pick between them.

    .. versionadded:: 4.4.0
    """
    if not since:
        return None
    if season is not None:
        raise TemplateUnsupported(f"since {since} and the {season} season at once")
    if career:
        raise TemplateUnsupported(f"since {since} and a career span at once")
    return since


def team_record(ctx: TemplateContext, reading: Reading) -> TemplateResult:
    """A team's win-loss record: for a season, at home or on the road, against
    one team, in a postseason, since a season, in game N of each playoff
    series, in one calendar month, broken out by month, or across every
    season the warehouse holds.

    A season's record and its home/road split are read from standings, the
    authoritative source - its "Home" and "Road" strings agree with a tally of
    ``games`` for every team-season from 1994 to 2026 once each era's
    neutral-site rule is applied (through 2024 a neutral-site game counts for
    its designated home team; from 2025 it counts as neither). A record against
    one team, in a postseason, since a season, in game N of each series, in one
    named month or broken out by month has no standings column, and is tallied
    from ``games`` instead - see team_metrics.TEAM_GAMES_SQL for what that
    tally removes.

    Every answer names the span it covers, because "all-time" here is not the
    franchise's history: standings begin with 1987-88, and ``games`` holds every
    regular-season game only from 1993-94.

    .. versionchanged:: 2.1.0
       Honors ``venue``, ``opponent`` and ``span``, answers a postseason
       record from ``games`` instead of refusing it, and adds points for and
       against, games behind and the last ten games to a season's record.

    .. versionchanged:: 4.3.0
       Honors ``situation`` where it names a real calendar month ("in
       october") and ``split`` where it is "month" (broken out by month) -
       every other value of either still falls through, the same as before.

    .. versionchanged:: 4.4.0
       Honors ``since`` (a since-bounded career, read the same way
       :func:`_record_narrowed` reads a whole one) and ``game_n`` (one game of
       each playoff series) instead of refusing both (step 3, team cells,
       ISSUES.md).

    .. versionchanged:: 4.4.0
       Honors ``until`` beside ``since`` (step 3, K1): an inclusive last
       season, so "best record from 2010-11 to 2018-19" names a bounded
       range rather than an open-ended one. Honors a ``situation`` naming a
       weekday, a fixed holiday or "since <month day>", not only a bare month
       - "the Knicks' record on Christmas" now answers. A month split now
       covers a ``since``/``until``-bounded span too, as one table per season
       (:func:`_team_record_by_month_span`) - "Knicks record by month 2024
       2025" (ISSUES.md).
    """
    scope = reading.scope
    con = ctx.con
    refused = _conference_refusal(scope)
    if refused is not None:
        return refused
    split, month, calendar_narrowing = _team_record_month_and_split(scope.split, scope.situation, scope.limit)

    teams = _team_record_teams(con, scope, scope.opponent)
    if isinstance(teams, TemplateResult):
        return teams
    team, opponent = teams

    season_type = scope.season_type or 2
    venue = scope.venue
    career = scope.span == "career"
    season = scope.season
    if career and season is not None:
        # "all-time ... in 2020" is either a slip or a range this cannot read.
        raise TemplateUnsupported("a career span and a single season at once")
    since = _team_record_since(scope.since, career, season)
    until = _validated_until(scope.until, since)
    game_n = scope.game_n
    if scope.season_type_unstated:
        # "including the playoffs"/"and the playoffs" - router._BOTH_SEASON_TYPES_WORDS
        # reuses this flag (c9930ad, the player relation's own fix); checked
        # before the game_n/season_type conflict below, which assumes one
        # named type.
        return _team_record_combined_types(con, team, opponent, split, month, venue, career, season, since, until, game_n, calendar_narrowing)
    if game_n and season_type != 3:
        raise TemplateUnsupported(f"game {game_n} names a game of a playoff series, and this is a regular season question")
    return _team_record_route(con, team, opponent, split, month, season_type, venue, career, season, since, until, game_n, calendar_narrowing)


def _extract_note(answer: str | None) -> str:
    """Any ``"Note: ..."`` tail a single-season-type answer already carries
    (:func:`_standings_gap`/:func:`_game_list_gaps`, appended by the standings
    and games-based paths in two different but both grep-able formats) -
    reused rather than recomputed, so a combined answer does not silently
    drop a real caveat (a short 2000 season, a 2001 postseason gap) that a
    single-type question about the same span would have shown."""
    if not answer:
        return ""
    idx = answer.find("Note:")
    return answer[idx:].strip() if idx != -1 else ""


def _team_record_combined_types(
    con: duckdb.DuckDBPyConnection,
    team: Entity,
    opponent: Entity | None,
    split: str | None,
    month: int | None,
    venue: str | None,
    career: bool,
    season: int | None,
    since: int | None,
    until: int | None,
    game_n: int | None,
    calendar_narrowing: CalendarNarrowing | None,
) -> TemplateResult:
    """Both season types combined - "including the playoffs"/"and the
    playoffs" (``season_type_unstated``, the flag ``game_log``'s "last N
    games" reader and ``router.py``'s own both-season-types phrase reading
    already carry - c9930ad, the player relation's fix; F116, ISSUES.md, is
    the team relation's own case). Reads each season type through
    ``team_record``'s own single-type routing (:func:`_team_record_route`),
    so a combined answer reads from EXACTLY the source a single-type
    question about the same span would (standings for a plain
    regular-season career, the team-games relation otherwise), and sums the
    two records - stating the combined total AND each type's own split,
    never silently answering one type alone.

    .. versionadded:: 4.4.0
    """
    if game_n:
        raise TemplateUnsupported("a game of a playoff series needs one named season type, not both combined")
    if split == "month":
        raise TemplateUnsupported("a month split has no combined-season-type form yet")
    regular = _team_record_route(con, team, opponent, None, month, 2, venue, career, season, since, until, None, calendar_narrowing)
    playoff = _team_record_route(con, team, opponent, None, month, 3, venue, career, season, since, until, None, calendar_narrowing)
    return _combined_record_result(team, opponent, venue, regular, playoff)


def _combined_span_phrase(first_season: Any, *, playoff: bool) -> str:
    """ " from 1993-94" or " from 1989", or "" when a half's own data carries
    no first season (a single named season, where the two halves start
    together and a "from" clause would only restate it) - #204, ISSUES.md.
    A regular-season year is named the way a person names a season
    (``_season_name``); a postseason is dated by the calendar year it was
    actually played in, which is already a plain year, not a range."""
    if not isinstance(first_season, int):
        return ""
    return f" from {first_season if playoff else _season_name(first_season)}"


def _combined_record_result(team: Entity, opponent: Entity | None, venue: str | None, regular: TemplateResult, playoff: TemplateResult) -> TemplateResult:
    """The combined-season-types sentence: the total, and each type's own
    split named beside it, so the two populations combined are stated rather
    than left for the reader to guess which games were counted - including,
    since #204 (ISSUES.md), the season each half's own count actually starts
    from, so "regular season" and "playoffs" are not read as covering the
    same span when they do not (the regular half can start years after the
    playoff half's own floor)."""
    r_wins, r_losses = int(regular.data.get("wins") or 0), int(regular.data.get("losses") or 0)
    p_wins, p_losses = int(playoff.data.get("wins") or 0), int(playoff.data.get("losses") or 0)
    wins, losses = r_wins + p_wins, r_losses + p_losses
    against = f" against the {opponent.name}" if opponent else ""
    where_played = f" {VENUE_WORDS[venue]}" if venue else ""
    r_from = _combined_span_phrase(regular.data.get("first_season"), playoff=False)
    p_from = _combined_span_phrase(playoff.data.get("first_season"), playoff=True)
    answer = (
        f"The {team.name} are {_tally(wins, losses)} combined{where_played}{against}, including the playoffs "
        f"({_tally(r_wins, r_losses)} regular season{r_from}, {_tally(p_wins, p_losses)} playoffs{p_from})."
    )
    notes = [n for n in (_extract_note(regular.answer), _extract_note(playoff.answer)) if n]
    if notes:
        answer += "\n  " + "\n  ".join(notes)
    data = {
        "team": team.name,
        "opponent": opponent.name if opponent else None,
        "venue": venue,
        "wins": wins,
        "losses": losses,
        "win_pct": wins / (wins + losses) if wins + losses else 0.0,
        "regular_season": {"wins": r_wins, "losses": r_losses, "first_season": regular.data.get("first_season")},
        "postseason": {"wins": p_wins, "losses": p_losses, "first_season": playoff.data.get("first_season")},
    }
    return TemplateResult(data=data, answer=answer)


def _team_record_route(
    con: duckdb.DuckDBPyConnection,
    team: Entity,
    opponent: Entity | None,
    split: str | None,
    month: int | None,
    season_type: int,
    venue: str | None,
    career: bool,
    season: int | None,
    since: int | None,
    until: int | None,
    game_n: int | None,
    calendar_narrowing: CalendarNarrowing | None,
) -> TemplateResult:
    """``team_record``'s last step: which of the four answer shapes the
    settled slots pick out - pulled out of ``team_record`` itself so that
    function stays inside the complexity gate, the same move
    ``_team_record_month_and_split`` already made for the month/situation
    reading above it.

    .. versionadded:: 4.4.0

    .. versionchanged:: 4.4.0
       Routes a month split with ``since`` set to :func:`_team_record_by_month_span`
       (step 3, K1) instead of refusing it.
    """
    if split == "month":
        if game_n:
            raise TemplateUnsupported("a month split has no one-game-of-a-series form yet")
        if since is not None:
            return _team_record_by_month_span(con, team, opponent, season_type, venue, since, until)
        return _team_record_by_month(con, team, opponent, None if career else (season or current_season()), season_type, venue)
    if since is not None:
        return _games_record(con, team, opponent, None, season_type, venue, month, since=since, until=until, game_n=game_n, calendar_narrowing=calendar_narrowing)
    if opponent is None and season_type == 2 and month is None and not game_n and calendar_narrowing is None:
        if career:
            return _standings_career(con, team, venue)
        return _standings_season(con, team, season or current_season(), venue)
    return _games_record(con, team, opponent, None if career else (season or current_season()), season_type, venue, month, game_n=game_n, calendar_narrowing=calendar_narrowing)


def _team_record_teams(con: duckdb.DuckDBPyConnection, scope: Scope, opponent_text: str | None) -> tuple[Entity, Entity | None] | TemplateResult:
    """The team a record is for and the opponent it is against, if any - or the
    clarifying question one of the names needs. ``opponent_text`` is the
    ``opponent`` slot."""
    team_text = scope.team
    listed = [n for n in scope.teams if n.strip()]
    if not (team_text and team_text.strip()) and listed:
        # "celtics vs bulls record" can land both teams in `teams`, which
        # subject.apply_subject leaves alone; the first is the subject.
        team_text, listed = listed[0], listed[1:]
    team = _resolved_team(con, team_text, season=_slot_season(scope))
    if isinstance(team, TemplateResult):
        return team
    opponent = _team_record_opponent(con, scope, team, listed, opponent_text)
    if isinstance(opponent, TemplateResult):
        return opponent
    return team, opponent


def _team_record_opponent(con: duckdb.DuckDBPyConnection, scope: Scope, team: Entity, listed: list[str], opponent_text: str | None) -> Entity | TemplateResult | None:
    """The ``opponent`` slot's team, or else the first other team in ``teams``."""
    if opponent_text and opponent_text.strip():
        found = _resolved_team(con, opponent_text, season=_slot_season(scope))
        if isinstance(found, TemplateResult):
            return found
        if found.id == team.id:
            raise TemplateUnsupported("team_record's opponent must differ from the team")
        return found
    for text in listed:
        found = _resolved_team(con, text, season=_slot_season(scope))
        if isinstance(found, TemplateResult):
            return found
        if found.id != team.id:
            return found
    return None


def _standings_gap(con: duckdb.DuckDBPyConnection, team: Entity, seasons: list[tuple[int, int]]) -> str | None:
    """A note for seasons whose standings cover fewer games than the team's own
    season totals. ESPN's 2000 standings stop two games short for most teams
    (the Lakers finished 67-15 and read 67-13), and nothing in the row says so.
    `seasons` is (season, games in standings)."""
    if not seasons:
        return None
    totals = dict(
        con.execute(
            f"SELECT season, gamesPlayed FROM team_season_stats WHERE team_id = ? AND season_type = 2 AND season IN ({', '.join('?' for _ in seasons)})",
            [team.id, *(s for s, _ in seasons)],
        ).fetchall()
    )
    # Short only: that is the failure measured, and it is the one a reader
    # cannot see from the row.
    short = [(s, g, int(totals[s])) for s, g in seasons if s in totals and totals[s] and g < int(totals[s])]
    if not short:
        return None
    parts = _joined([f"{s} ({g} of {t} games)" for s, g, t in short])
    said = f"Note: ESPN's standings do not cover the {_possessive(team.name)} whole season in {parts}, so this record is short by those games."
    return note("standings_short", said, team=team.name, seasons=[{"season": int(s), "held": int(g), "played": t} for s, g, t in short])


def _standings_season(con: duckdb.DuckDBPyConnection, team: Entity, season: int, venue: str | None) -> TemplateResult:
    row = con.execute(
        'SELECT wins, losses, winPercent, streak, playoffSeed, gamesBehind, "Home", "Road", "Last Ten Games", avgPointsFor, avgPointsAgainst, differential '
        "FROM standings WHERE team_id = ? AND season = ?",
        [team.id, season],
    ).fetchone()
    if row is None:
        return TemplateResult(
            data={"team": team.name, "season": season},
            answer=f"There are no {season} standings for the {team.name} in the warehouse.",
        )
    wins, losses, win_pct, streak, seed, behind, home_text, road_text, last_ten, points_for, points_against, differential = row
    # standings stores these as DOUBLE; reporting a 53-29 record as "53.0-29.0"
    # is the kind of detail that makes a correct answer look untrustworthy.
    w, lost = int(wins), int(losses)
    split, neutral, neutral_note = _standings_season_split(w, lost, home_text, road_text)
    data: dict[str, Any] = {"team": team.name, "season": season, "wins": w, "losses": lost, "win_pct": win_pct}
    gap = _standings_gap(con, team, [(season, w + lost)])

    if venue is not None:
        return _standings_season_venue(team, season, venue, (w, lost, win_pct), split, neutral, neutral_note, data, gap)

    answer = f"The {team.name} were {w}-{lost} in the {season} regular season"
    if win_pct is not None:
        answer += f" ({_record_pct(win_pct)})"
    extras = []
    if seed:
        extras.append(f"{_ordinal(int(seed))} seed")
    if streak:
        extras.append(f"{'won' if streak > 0 else 'lost'} {abs(int(streak))} straight")
    answer += f", {', '.join(extras)}." if extras else "."
    headline = answer
    answer += _standings_season_detail(split, neutral_note, last_ten, behind)
    if points_for is not None and points_against is not None:
        answer += f"\n  {points_for:.1f} points per game, {points_against:.1f} allowed ({(differential if differential is not None else points_for - points_against):+.1f})."
    data.update(
        {
            "home": split[0] if split else None,
            "road": split[1] if split else None,
            "last_ten": last_ten,
            "games_behind": behind,
            "points_for": points_for,
            "points_against": points_against,
            # The sentence names these only when non-zero ("3rd seed",
            # "won 4 straight") but the data key is worth carrying either
            # way - None reads as "not seeded"/"no active streak" rather
            # than "the page never asked".
            "seed": int(seed) if seed else None,
            "streak": int(streak) if streak else None,
            "headline": headline,
        }
    )
    data["notes"] = [gap] if gap else []
    return TemplateResult(data=data, answer=f"{answer}\n  {gap}" if gap else answer)


def _standings_season_split(w: int, lost: int, home_text: Any, road_text: Any) -> tuple[tuple[tuple[int, int], tuple[int, int]] | None, int, str]:
    """The standings' home and road records, if the season has a split, and the
    neutral-site games that are in neither - with the note that says so."""
    home, road = _parse_record(home_text), _parse_record(road_text)
    # '0-0' is how standings say "no split", every season before 1993-94.
    split = (home, road) if home and road and sum(home) + sum(road) > 0 else None
    # Neutral-site games count as neither home nor away from 2025 on, so the
    # halves can sum to less than the whole - and a reader adding them up
    # deserves to know why.
    neutral = w + lost - sum(split[0]) - sum(split[1]) if split else 0
    said = f" ({neutral} neutral-site game{'s' if neutral != 1 else ''} count{'s' if neutral == 1 else ''} as neither home nor away)"
    neutral_note = note("definition", said, term="neutral_site", games=neutral) if neutral > 0 else ""
    return split, neutral, neutral_note


def _standings_season_venue(
    team: Entity,
    season: int,
    venue: str,
    record: tuple[int, int, Any],
    split: tuple[tuple[int, int], tuple[int, int]] | None,
    neutral: int,
    neutral_note: str,
    data: dict[str, Any],
    gap: str | None,
) -> TemplateResult:
    """A season's home or road record, from the standings' own split. ``record`` is (wins, losses, win_pct) for the whole season."""
    w, lost, win_pct = record
    if split is None:
        message = f"ESPN's {season} standings carry no home/road split for the {team.name} (it reads 0-0 before 1993-94), and the warehouse has no full game list for that season to tally one from."
        return TemplateResult(data={**data, "message": message}, answer=message)
    vw, vl = split[0] if venue == "home" else split[1]
    headline = f"The {team.name} were {_tally(vw, vl)} {VENUE_WORDS[venue]} in the {season} regular season, {w}-{lost} overall{neutral_note}."
    # `wins`/`losses`/`win_pct` are what the web page draws as the record
    # card, so they carry the record that was asked for. Leaving the
    # season's there would print 53-29 in large type under a question
    # about the home record - the substitution this path exists to stop.
    data.update(
        {
            "wins": vw,
            "losses": vl,
            "win_pct": vw / (vw + vl) if vw + vl else 0.0,
            "season_wins": w,
            "season_losses": lost,
            "season_win_pct": win_pct,
            "venue": venue,
            "venue_wins": vw,
            "venue_losses": vl,
            "neutral_site_games": neutral,
            # See _standings_season's own comment: the card renderer used to
            # keep the WHOLE sentence as its caption so nothing under it was
            # lost, which duplicated the headline underneath itself. `gap`
            # (ESPN's standings falling short of the team's own season total)
            # is the one thing here beyond the headline sentence, so it moves
            # to `notes` instead of staying glued onto `headline` with a space.
            "headline": headline,
        }
    )
    data["notes"] = [gap] if gap else []
    return TemplateResult(data=data, answer=f"{headline} {gap}" if gap else headline)


def _standings_season_detail(split: tuple[tuple[int, int], tuple[int, int]] | None, neutral_note: str, last_ten: Any, behind: Any) -> str:
    """The line under a season's record: home and road, the last ten games, and games back."""
    detail = []
    if split:
        detail.append(f"Home {'-'.join(map(str, split[0]))}, road {'-'.join(map(str, split[1]))}{neutral_note}")
    if isinstance(last_ten, str) and last_ten.strip():
        detail.append(f"last 10: {last_ten}")
    if behind:
        detail.append(f"{_format_value(float(behind))} game{'s' if behind != 1 else ''} back")
    return "\n  " + "; ".join(detail) + "." if detail else ""


def _standings_career(con: duckdb.DuckDBPyConnection, team: Entity, venue: str | None) -> TemplateResult:
    rows = con.execute(
        'SELECT season, wins, losses, "Home", "Road" FROM standings WHERE team_id = ? AND wins + losses > 0 ORDER BY season',
        [team.id],
    ).fetchall()
    if not rows:
        return TemplateResult(data={"team": team.name}, answer=f"The warehouse has no standings at all for the {team.name}.")
    if venue is not None:
        return _standings_career_venue(con, team, venue, rows)

    wins = sum(int(r[1]) for r in rows)
    losses = sum(int(r[2]) for r in rows)
    first, last = rows[0][0], rows[-1][0]
    # The start is the warehouse's, not the franchise's, and saying which is
    # the whole difference between an all-time record and a partial one.
    start = (
        note("floor", "the warehouse's standings begin there, so this is not the franchise's whole history", table="standings", first=int(first))
        if first == min(r[0] for r in con.execute("SELECT MIN(season) FROM standings").fetchall())
        else "the first season the warehouse holds for them"
    )
    headline = f"The {team.name} are {_tally(wins, losses)} across the {len(rows)} regular seasons from {_season_name(first)} through {_season_name(last)} - {start}."
    gap = _standings_gap(con, team, [(int(r[0]), int(r[1]) + int(r[2])) for r in rows])
    return TemplateResult(
        data={
            "team": team.name,
            "wins": wins,
            "losses": losses,
            "win_pct": wins / (wins + losses) if wins + losses else 0.0,
            "first_season": first,
            "last_season": last,
            "seasons": len(rows),
            "headline": headline,
            "notes": [gap] if gap else [],
        },
        answer=f"{headline} {gap}" if gap else headline,
    )


def _standings_career_venue(con: duckdb.DuckDBPyConnection, team: Entity, venue: str, rows: list[tuple[Any, ...]]) -> TemplateResult:
    """Every season's home or road record added up, over the seasons whose standings carry a split."""
    halves = []
    for season, wins, losses, home_text, road_text in rows:
        home, road = _parse_record(home_text), _parse_record(road_text)
        if home and road and sum(home) + sum(road) > 0:
            halves.append((season, int(wins) + int(losses), home if venue == "home" else road, sum(home) + sum(road)))
    if not halves:
        message = f"ESPN's standings carry no home/road split for the {team.name} in any season the warehouse holds."
        return TemplateResult(data={"team": team.name, "message": message}, answer=message)
    vw = sum(h[2][0] for h in halves)
    vl = sum(h[2][1] for h in halves)
    neutral = sum(h[1] - h[3] for h in halves)
    first, last = halves[0][0], halves[-1][0]
    floor = " - ESPN's standings carry no home/road split before 1993-94"
    headline = (
        f"The {team.name} are {_tally(vw, vl)} {VENUE_WORDS[venue]} across the {len(halves)} regular seasons from {_season_name(first)} through {_season_name(last)}"
        + (note("floor", floor, table="standings", first=FIRST_FULL_REGULAR_SEASON, what="home_road_split") if first == FIRST_FULL_REGULAR_SEASON else "")
        + "."
    )
    if neutral > 0:
        said = f" {neutral} neutral-site game{'s' if neutral != 1 else ''} count{'s' if neutral == 1 else ''} as neither."
        headline += note("definition", said, term="neutral_site", games=neutral)
    data: dict[str, Any] = {
        "team": team.name,
        "venue": venue,
        "wins": vw,
        "losses": vl,
        "win_pct": vw / (vw + vl) if vw + vl else 0.0,
        "first_season": first,
        "last_season": last,
        "seasons": len(halves),
        "headline": headline,
    }
    gap = _standings_gap(con, team, [(h[0], h[1]) for h in halves])
    data["notes"] = [gap] if gap else []
    return TemplateResult(data=data, answer=f"{headline} {gap}" if gap else headline)


def _record_narrowed(team: Entity, season: int | None, season_type: int, *, since: int | None = None, until: int | None = None) -> TeamNarrowed:
    """``team``'s games for a win-loss RECORD tally, over
    :class:`association.query.team_games.TeamNarrowed`:
    :func:`association.query.team_metrics.games_scope`'s own clause (which
    excludes the NBA Cup final from a regular season) plus the team filter -
    the shared base every ``team_record``/``team_leaderboard`` read against
    :data:`association.query.team_metrics.TEAM_GAMES_SQL` builds on.

    .. versionchanged:: 4.4.0
       Honors ``since`` (step 3, team cells): a since-bounded span reads the
       same :func:`common._span_of`/:func:`common._team_span_clause` clause
       :func:`_team_leaderboard_since_records` already tallies from -
       ``games_scope`` only ever means "one season" or "every season", with no
       notion of a starting point. The NBA Cup final is still excluded from a
       regular-season record either way.

    .. versionchanged:: 4.4.0
       Honors ``until`` beside ``since`` (step 3, K1): the inclusive last
       season of a bounded range - "best record from 2010-11 to 2018-19".
    """
    if since is not None:
        span = _span_of(None, None, season_type, "games", since=since, until=until)
        clause, params = _team_span_clause(span)
        base = ["tg.team_id = ?", "tg.season_type = ?", clause]
        if season_type == 2:
            base.append("NOT tg.cup_final")
        return TeamNarrowed(base=base, base_params=[team.id, season_type, *params], team=team)
    scope, params = games_scope(season_type, season)
    return TeamNarrowed(base=["tg.team_id = ?", scope], base_params=[team.id, *params], team=team)


def _game_list_gaps(con: duckdb.DuckDBPyConnection, team: Entity, season_type: int, season: int | None, *, since: int | None = None, until: int | None = None) -> str | None:
    """Seasons where the games tallied for a team do not number its games in
    team_season_stats - the check that makes a tally from ``games`` safe to
    state. Measured: the 2000 and 2001 postseasons hold 15 of the Lakers' 23
    and 10 of their 16 games, and 1995 holds a Miami playoff game in a season
    Miami did not make the playoffs. Only seasons from 1994, where the totals
    exist, can be checked.

    .. versionchanged:: 4.4.0
       Honors ``since`` (step 3, team cells), the same way :func:`_record_narrowed`
       does, so a since-bounded record's gap note only names seasons the
       question actually covers.

    .. versionchanged:: 4.4.0
       Honors ``until`` beside ``since`` (step 3, K1), bounding the gap check
       to the same range the record itself covers.
    """
    subquery, sub_params = team_games_subquery(_record_narrowed(team, season, season_type, since=since, until=until))
    by_season = "year(eastern_date)" if season_type == 3 else "season"
    season_filter = "" if season is None else "AND ts.season = ?"
    since_filter = "" if since is None else "AND ts.season >= ?"
    until_filter = "" if until is None else "AND ts.season <= ?"
    floor = max(FIRST_FULL_REGULAR_SEASON, since) if since is not None else FIRST_FULL_REGULAR_SEASON
    rows = con.execute(
        f"""
WITH tallied AS (SELECT {by_season} AS season, count(*) AS games FROM ({subquery}) t GROUP BY 1),
totals AS (SELECT ts.season, ts.gamesPlayed AS games FROM team_season_stats ts WHERE ts.team_id = ? AND ts.season_type = ? {season_filter}{since_filter}{until_filter})
SELECT coalesce(l.season, t.season) AS season, coalesce(l.games, 0), coalesce(t.games, 0)
FROM tallied l FULL OUTER JOIN totals t ON t.season = l.season
WHERE coalesce(l.season, t.season) >= ? AND coalesce(l.games, 0) <> coalesce(t.games, 0)
ORDER BY 1""",
        [*sub_params, team.id, season_type, *([season] if season is not None else []), *([since] if since is not None else []), *([until] if until is not None else []), floor],
    ).fetchall()
    if not rows:
        return None
    parts = _joined([f"{s} ({int(listed)} listed, {int(played)} played)" for s, listed, played in rows])
    kind = "postseason" if season_type == 3 else "regular-season"
    said = f"Note: ESPN's game list and the {_possessive(team.name)} season totals disagree on how many {kind} games they played in {parts}, so this tally is off by those games."
    return note("game_list_disagrees", said, team=team.name, what=kind, seasons=[{"season": int(s), "listed": int(listed), "played": int(played)} for s, listed, played in rows])


def _no_team_games(
    con: duckdb.DuckDBPyConnection,
    team: Entity,
    opponent: Entity | None,
    season: int | None,
    season_type: int,
    month: int | None = None,
    *,
    since: int | None = None,
    until: int | None = None,
    game_n: int | None = None,
    calendar_narrowing: CalendarNarrowing | None = None,
) -> str:
    """Why a tally found nothing. Three different facts, and three sentences:
    the warehouse has no games that season at all (it holds no 1988
    postseason), the team played none, or the two teams did not meet -
    each narrowed to the named month too, where the question asked for one, so
    a team with games in OTHER months is not told it has none at all.

    .. versionchanged:: 4.4.0
       Names ``since`` and ``game_n`` too (step 3, team cells), so a
       since-bounded or one-game-of-a-series record that finds nothing says
       which narrowing emptied it rather than reading as "no games at all".

    .. versionchanged:: 4.4.0
       Names ``until`` beside ``since`` and the fuller calendar narrowing
       beside a bare month (step 3, K1).
    """
    kind = "postseason" if season_type == 3 else "regular-season"
    where = _calendar_phrase(month, calendar_narrowing)
    since_phrase = _span_phrase(since, until)
    game_n_phrase = f" in game {game_n} of {'the' if opponent is not None else 'each'} series" if game_n else ""
    if season is None:
        if opponent is None:
            return f"The warehouse holds no {kind} games for the {team.name}{since_phrase}{game_n_phrase}{where}."
        return f"The warehouse holds no {kind} games between the {team.name} and the {opponent.name}{since_phrase}{game_n_phrase}{where}."
    scope, params = games_scope(season_type, season)
    period = _period(season, season_type)
    counts = con.execute(f"{TEAM_GAMES_SQL} SELECT count(*), count(*) FILTER (WHERE team_id = ?) FROM team_games WHERE {scope}", [team.id, *params]).fetchone()
    league, own = counts if counts else (0, 0)
    if not league:
        return f"The warehouse holds no {period} games for any team."
    if opponent is not None and own:
        return f"The {team.name} and the {opponent.name} did not meet{game_n_phrase}{where} in the {period}."
    return f"The {team.name} played no games{game_n_phrase}{where} in the {period}."


def _games_record_span(con: duckdb.DuckDBPyConnection, narrowed: TeamNarrowed, season_type: int) -> tuple[int | None, int | None]:
    """The first and last season a narrowed team-games read actually reaches
    - `None, None` where it reaches none.

    A postseason is read by the calendar year it was actually played in
    (``year(tg.eastern_date)``), never ``tg.season`` itself: that column is
    ESPN's own raw label, which for a postseason before 1993-94 names the
    year the SEASON STARTED, not the year the games were played - the same
    fault ``AGENTS.md`` ("Select a postseason by the calendar year") and
    :func:`association.query.templates.splits._team_season_range` both
    record, read here the same way. A regular season's label already agrees
    with the calendar year it was played in, so it is read directly.

    .. versionadded:: 4.4.0
    """
    season_col = "year(tg.eastern_date)" if season_type == 3 else "tg.season"
    where, params = narrowed.clauses()
    row = con.execute(f"{TEAM_GAMES_SQL} SELECT MIN({season_col}), MAX({season_col}) FROM team_games tg WHERE {where}", params).fetchone()
    return (row[0], row[1]) if row else (None, None)


def _games_record(
    con: duckdb.DuckDBPyConnection,
    team: Entity,
    opponent: Entity | None,
    season: int | None,
    season_type: int,
    venue: str | None,
    month: int | None = None,
    *,
    since: int | None = None,
    until: int | None = None,
    game_n: int | None = None,
    calendar_narrowing: CalendarNarrowing | None = None,
) -> TemplateResult:
    """A record tallied from ``games``: against one team, or in a postseason,
    for one season, since a season (``season`` None, ``since`` set, optionally
    ``until``-bounded) or (``season`` and ``since`` both None) every season it
    holds, optionally narrowed to one calendar month or the fuller calendar
    narrowing - ``standings`` has no game-level date to filter a month from,
    so a calendar narrowing reaches this path even where neither an opponent
    nor a postseason would have.

    .. versionchanged:: 4.4.0
       Honors ``since`` and ``game_n`` (step 3, team cells) - see
       :func:`_record_narrowed` and :func:`_games_record_games`.

    .. versionchanged:: 4.4.0
       Honors ``until`` beside ``since`` and a fuller ``calendar_narrowing``
       beside a bare month (step 3, K1).
    """
    if season is not None:
        # _sources_for sends this path through the `games` floor, but a record
        # against a team named only in `teams` reaches it with the standings'
        # floor instead - so the floor is checked where the table is read.
        refused = unavailable(("games",), season, season_type)
        if refused is not None:
            return TemplateResult(data={"team": team.name, "season": season, "message": refused}, answer=refused)
    games, narrowed = _games_record_games(con, team, opponent, season, season_type, month, since=since, until=until, game_n=game_n, calendar_narrowing=calendar_narrowing)
    shown = [g for g in games if venue is None or g["venue"] == venue]
    wins = sum(1 for g in shown if g["won"])
    losses = len(shown) - wins
    kind = "postseason" if season_type == 3 else "regular season"
    # first_season/last_season: for one named season, trivially itself; for a
    # career or since-bounded span, the relation's own season column - which
    # already selects a pre-1994 postseason by the calendar year it was
    # played in, not ESPN's own-year label (team_games.py) - read once here
    # so a caller (the combined-season-types sentence, #204, ISSUES.md) can
    # state each half's own start without parsing either one's prose.
    first_season, last_season = (season, season) if season is not None else _games_record_span(con, narrowed, season_type)
    data: dict[str, Any] = {
        "team": team.name,
        "opponent": opponent.name if opponent else None,
        "season": season,
        "season_type": kind,
        "venue": venue,
        "month": _MONTH_NAMES[month - 1] if month is not None else None,
        "wins": wins,
        "losses": losses,
        # Read by the web page's record card, like the standings paths' own.
        "win_pct": wins / (wins + losses) if wins + losses else 0.0,
        "first_season": first_season,
        "last_season": last_season,
    }

    if not games:
        none_message = _no_team_games(con, team, opponent, season, season_type, month, since=since, until=until, game_n=game_n, calendar_narrowing=calendar_narrowing)
        return TemplateResult(data={**data, "games": [], "headline": none_message}, answer=none_message)

    answer = _games_record_answer(team, opponent, season, season_type, venue, games, shown, month, since=since, until=until, narrowed=narrowed)
    if opponent is not None and season_type == 2:
        cup_text, cup_final = _games_record_cup_final(con, team, opponent, season, since=since, until=until)
        answer += cup_text
        data["cup_final"] = cup_final
    gap = _game_list_gaps(con, team, season_type, season, since=since, until=until)
    if gap:
        answer += f"\n  {gap}"
    data["games"] = shown
    data["headline"] = answer.split("\n")[0]
    data["notes"] = [gap] if gap else []
    return TemplateResult(data=data, answer=answer)


def _games_record_games(
    con: duckdb.DuckDBPyConnection,
    team: Entity,
    opponent: Entity | None,
    season: int | None,
    season_type: int,
    month: int | None = None,
    *,
    since: int | None = None,
    until: int | None = None,
    game_n: int | None = None,
    calendar_narrowing: CalendarNarrowing | None = None,
) -> tuple[list[dict[str, Any]], TeamNarrowed]:
    """The team's games in scope, against ``opponent`` and/or in ``month`` (or
    a fuller calendar narrowing) if named, oldest first - and the
    :class:`TeamNarrowed` they were read through, so a caller can phrase what
    else narrowed them (:meth:`TeamNarrowed.filters`) without rebuilding it.

    .. versionchanged:: 4.4.0
       Honors ``since`` (through :func:`_record_narrowed`) and ``game_n``
       (step 3, team cells): one game of each playoff series, the same check
       :func:`association.query.templates.common.team_games` makes for every
       other team template - a series has games 1-7, and a regular season has
       nothing "game 4" names.

    .. versionchanged:: 4.4.0
       Honors ``until`` beside ``since`` and ``calendar_narrowing`` (step 3,
       K1) via :meth:`association.query.team_games.TeamNarrowed.narrow_calendar` -
       a weekday, a fixed holiday, or "since <month day>", where ``month`` is
       not a bare-month narrowing.
    """
    narrowed = _record_narrowed(team, season, season_type, since=since, until=until)
    if opponent is not None:
        narrowed.narrow("tg.opponent_id = ?", opponent.id)
        # Read by TeamNarrowed.filters()' own series_game phrase ("of THE
        # series" vs "of EACH series") - never by the WHERE clause above,
        # which the opponent id parameter already carries.
        narrowed.opponent = opponent
    if month is not None:
        narrowed.narrow("month(tg.eastern_date) = ?", month)
    if calendar_narrowing is not None:
        narrowed.narrow_calendar(calendar_narrowing)
    if game_n:
        if season_type != 3:
            raise TemplateUnsupported(f"game {game_n} names a game of a playoff series, and this is a regular-season question")
        narrowed.narrow_series_game(game_n)
    select = f"tg.eastern_date, tg.side, tg.neutral, tg.team_score, tg.opponent_score, tg.won, {season_name_sql('o.team_id', 'tg.season', 'o.display_name')}"
    sql, params = team_rows_sql(narrowed, select, order="tg.eastern_date", join=" JOIN teams o ON o.team_id = tg.opponent_id")
    rows = con.execute(sql, params).fetchall()
    return [{"date": str(r[0]), "venue": "neutral" if r[2] else r[1], "team_score": r[3], "opponent_score": r[4], "won": bool(r[5]), "opponent": r[6]} for r in rows], narrowed


def _games_record_span_text(season: int | None, season_type: int, since: int | None, until: int | None = None) -> str:
    """The span phrase in ``_games_record_answer``'s lead sentence - pulled out
    so that function stays inside the complexity gate. Four shapes: one named
    season, a since-bounded range - open-ended or ``until``-bounded (step 3,
    K1's "2011-2019" wording) - every postseason on record, or every regular
    season on record."""
    if season is not None:
        return f"the {_period(season, season_type)}"
    if since is not None:
        kind = "postseasons" if season_type == 3 else "regular seasons"
        bound = f"from {since} through {until}" if until is not None else f"since {since}"
        return f"the {kind} {bound}"
    if season_type == 3:
        return "every postseason from 1989 through the latest" + note("floor", " - the warehouse's game list starts with the 1989 playoffs", table="games", first=1989, what="postseason")
    floor = note("floor", " - the first the warehouse holds every game of", table="games", first=FIRST_FULL_REGULAR_SEASON, what="regular season")
    return f"the regular seasons from {_season_name(FIRST_FULL_REGULAR_SEASON)} on{floor}"


def _games_record_answer(
    team: Entity,
    opponent: Entity | None,
    season: int | None,
    season_type: int,
    venue: str | None,
    games: list[dict[str, Any]],
    shown: list[dict[str, Any]],
    month: int | None = None,
    *,
    since: int | None = None,
    until: int | None = None,
    narrowed: TeamNarrowed | None = None,
) -> str:
    """The tallied record, its home/away split and, for one season against one
    team, the meetings.

    .. versionchanged:: 4.4.0
       Names ``since`` (a since-bounded span reads like the existing
       whole-career one, but says the year it starts from) and ``game_n``
       (step 3, team cells) - the latter via ``narrowed.filters(opponent=False)``,
       the same idiom :func:`association.query.templates.games.team_quarter_points`
       uses, rather than a hand-written phrase of its own: the game-of-series
       wording lives in one place, :class:`TeamNarrowed` itself.

    .. versionchanged:: 4.4.0
       Names ``until`` beside ``since`` (step 3, K1). A fuller calendar
       narrowing beside a bare month is named too, but through ``narrowed``
       (its own ``.calendar``, via ``narrowed.filters()`` below) rather than a
       parameter here - it is already set on ``narrowed`` by
       :func:`_games_record_games` before this is called.
    """
    wins = sum(1 for g in shown if g["won"])
    losses = len(shown) - wins
    span = _games_record_span_text(season, season_type, since, until)
    against = f" against the {opponent.name}" if opponent else ""
    where_played = f" {VENUE_WORDS[venue]}" if venue else ""
    # A bare month has no cell on TeamNarrowed itself (see _games_record_games),
    # so it is worded here; a fuller calendar narrowing DOES (`.calendar`, set
    # by `narrow_calendar`), and `narrowed.filters()` below already renders it
    # - together with game_n - so it is not repeated here. `month` and
    # `calendar_narrowing` are never both set (`_team_record_month_and_split`).
    month_phrase = f" in {_MONTH_NAMES[month - 1]}" if month is not None else ""
    game_n_phrase = narrowed.filters(opponent=False) if narrowed is not None else ""
    verb = "went" if season is not None else "are"
    answer = f"The {team.name} {verb} {_tally(wins, losses)}{month_phrase}{where_played}{against}{game_n_phrase} in {span}."
    if venue is None:
        answer += _games_record_split(games)
    elif len(shown) < len(games) and any(g["venue"] == "neutral" for g in games):
        answer += _games_record_answer_neutral(games)
    # A season's meetings are few enough to list, and the list is what "vs"
    # questions usually want next.
    if opponent is not None and season is not None and shown:
        answer += "\n" + "\n".join(
            f"  {g['date']}  {'W' if g['won'] else 'L'} {g['team_score']}-{g['opponent_score']}  {'at' if g['venue'] == 'away' else 'vs'} {g['opponent']}"
            + (" (neutral site)" if g["venue"] == "neutral" else "")
            for g in shown
        )
    return answer


def _games_record_answer_neutral(games: list[dict[str, Any]]) -> str:
    """The line under a home or road tally that leaves neutral-site games out,
    saying why the two halves do not add up to the whole."""
    neutral = sum(1 for g in games if g["venue"] == "neutral")
    return note("definition", "\n  Neutral-site games count as neither home nor away.", term="neutral_site", games=neutral)


def _team_record_month_table(team: Entity, opponent: Entity | None, venue: str | None, span_text: str, season: int | None, shown: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """The record-by-month table for one already venue-filtered set of games,
    headed with ``span_text`` - shared by :func:`_team_record_by_month` and
    :func:`_team_record_by_month_span` (step 3, K1's since/until-bounded
    reading), so the two say a month row identically. ``season`` is carried
    into each row of the returned ``months_data`` (``None`` for a whole-history
    or plain career table) so a since/until-bounded caller's several tables
    are still tellable apart in the structured data, not only the headings.

    .. versionadded:: 4.4.0
    """
    by_month: dict[int, list[dict[str, Any]]] = {}
    for g in shown:
        by_month.setdefault(int(g["date"][5:7]), []).append(g)
    rows: list[tuple[str, list[str]]] = []
    months_data: list[dict[str, Any]] = []
    for month in sorted(by_month, key=_season_month_order):
        entries = by_month[month]
        wins = sum(1 for g in entries if g["won"])
        losses = len(entries) - wins
        rows.append((_MONTH_NAMES[month - 1], [str(len(entries)), f"{wins}-{losses}"]))
        months_data.append({"season": season, "month": _MONTH_NAMES[month - 1], "games": len(entries), "wins": wins, "losses": losses})
    against = f" against the {opponent.name}" if opponent else ""
    where_played = f" {VENUE_WORDS[venue]}" if venue else ""
    title = f"The {team.name}, record by month{where_played}{against}, {span_text}:"
    return _table(title, ["G", "W-L"], rows), months_data


def _team_record_by_month(con: duckdb.DuckDBPyConnection, team: Entity, opponent: Entity | None, season: int | None, season_type: int, venue: str | None) -> TemplateResult:
    """team_record's answer for ``split == "month"``: the team's record broken
    out by calendar month. ``standings`` has no game-level date to group a
    month from, so - the same as a single month's filter - this reads a tally
    of ``games`` instead, the source a record against one opponent or in a
    postseason already uses.

    .. versionadded:: 4.3.0
    """
    if season is not None:
        refused = unavailable(("games",), season, season_type)
        if refused is not None:
            return TemplateResult(data={"team": team.name, "season": season, "message": refused}, answer=refused)
    games, _narrowed = _games_record_games(con, team, opponent, season, season_type)
    shown = [g for g in games if venue is None or g["venue"] == venue]
    if not shown:
        return TemplateResult(data={"team": team.name, "season": season, "months": []}, answer=_no_team_games(con, team, opponent, season, season_type))
    # The same span wording as a tallied record's, from the one place that
    # writes it (and its floor note).
    span = _games_record_span_text(season, season_type, None)
    answer, months_data = _team_record_month_table(team, opponent, venue, span, season, shown)
    data = {"team": team.name, "season": season, "opponent": opponent.name if opponent else None, "venue": venue, "months": months_data, "headline": answer.split("\n")[0].rstrip(":")}
    return TemplateResult(data=data, answer=answer)


def _team_record_by_month_span(con: duckdb.DuckDBPyConnection, team: Entity, opponent: Entity | None, season_type: int, venue: str | None, since: int, until: int | None) -> TemplateResult:
    """team_record's by-month split honoring a ``since``/``until``-bounded
    span (step 3, K1) - "Knicks record by month 2024 2025" (ISSUES.md), a
    range the router files as ``since=2024, until=2025``. One table PER
    SEASON, since a by-month split covering several seasons is still, within
    each season, a table of at most twelve months: merging seasons into one
    table would either double-count a calendar month across the years or need
    a season column bolted onto its plain two-column shape, and the table
    already used for one season (:func:`_team_record_month_table`) reads
    cleanly headed by its own season instead.

    .. versionadded:: 4.4.0
    """
    last = until if until is not None else current_season()
    tables: list[str] = []
    months_data: list[dict[str, Any]] = []
    for season in range(since, last + 1):
        if unavailable(("games",), season, season_type) is not None:
            continue  # a season the games table cannot reach is skipped, not refused whole
        games, _narrowed = _games_record_games(con, team, opponent, season, season_type)
        shown = [g for g in games if venue is None or g["venue"] == venue]
        if not shown:
            continue
        table, season_months = _team_record_month_table(team, opponent, venue, f"the {_period(season, season_type)}", season, shown)
        tables.append(table)
        months_data.extend(season_months)
    if not tables:
        message = _no_team_games(con, team, opponent, None, season_type, since=since, until=until)
        return TemplateResult(data={"team": team.name, "months": []}, answer=message)
    answer = "\n\n".join(tables)
    data = {"team": team.name, "opponent": opponent.name if opponent else None, "venue": venue, "months": months_data, "since": since, "until": until, "headline": answer.split("\n")[0].rstrip(":")}
    return TemplateResult(data=data, answer=answer)


def _games_record_split(games: list[dict[str, Any]]) -> str:
    """The home, away and neutral-site records within a tally."""
    home = [g for g in games if g["venue"] == "home"]
    away = [g for g in games if g["venue"] == "away"]
    split = f"Home {sum(g['won'] for g in home)}-{sum(not g['won'] for g in home)}, away {sum(g['won'] for g in away)}-{sum(not g['won'] for g in away)}"
    neutral = len(games) - len(home) - len(away)
    if neutral:
        split += f", neutral site {sum(g['won'] for g in games if g['venue'] == 'neutral')}-{sum(not g['won'] for g in games if g['venue'] == 'neutral')}"
    return f"\n  {split}."


def _games_record_cup_final(
    con: duckdb.DuckDBPyConnection, team: Entity, opponent: Entity, season: int | None, *, since: int | None = None, until: int | None = None
) -> tuple[str, list[dict[str, Any]]]:
    """Any NBA Cup final the two teams met in, as sentences and as data.

    .. versionchanged:: 4.4.0
       Honors ``since`` (step 3, team cells), the same way ``season`` already
       narrowed this to one year.

    .. versionchanged:: 4.4.0
       Honors ``until`` beside ``since`` (step 3, K1).
    """
    # The NBA Cup final is a regular-season game that counts in no
    # standings, so it is not in the record above (_record_narrowed's
    # games_scope excludes it) - but it is a meeting, and leaving it out
    # without a word would read as a missing game. Narrowed by hand rather
    # than through _record_narrowed, since this asks for exactly the game
    # that clause excludes.
    narrowed = TeamNarrowed(base=["tg.team_id = ?", "tg.season_type = 2", "tg.cup_final"], base_params=[team.id], team=team)
    narrowed.narrow("tg.opponent_id = ?", opponent.id)
    if season is not None:
        narrowed.narrow("tg.season = ?", season)
    elif since is not None:
        narrowed.narrow("tg.season >= ?", since)
        if until is not None:
            narrowed.narrow("tg.season <= ?", until)
    sql, params = team_rows_sql(narrowed, "tg.eastern_date, tg.team_score, tg.opponent_score, tg.won", order="tg.eastern_date")
    cup = con.execute(sql, params).fetchall()
    text = "".join(f"\n  They also met in the NBA Cup final on {date}, which counts in no standings: {'won' if won else 'lost'} {own}-{theirs}." for date, own, theirs, won in cup)
    return text, [{"date": str(d), "won": bool(won), "team_score": own, "opponent_score": theirs} for d, own, theirs, won in cup]
