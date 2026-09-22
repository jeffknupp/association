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

from ..conditions import _MONTH_NAMES, _season_month_order, _table
from ..entities import Entity
from ..team_games import TeamNarrowed
from ..team_games import games_subquery as team_games_subquery
from ..team_games import rows_sql as team_rows_sql
from ..team_metrics import (
    DEFAULT_TEAM_LINE,
    FIRST_FULL_REGULAR_SEASON,
    TEAM_GAMES_SQL,
    TEAM_METRICS,
    TeamLine,
    TeamMetric,
    TeamRecord,
    descending_for,
    games_scope,
    ranked,
    record_table,
    resolve_team_metric,
    season_table,
)
from .common import (
    TemplateContext,
    TemplateResult,
    TemplateUnsupported,
    _clamp_limit,
    _format_value,
    _joined,
    _ordinal,
    _period,
    _resolved_team,
    _season_name,
    _slot_season,
    _span_of,
    _team_span_clause,
)

# Conference and division words. The warehouse holds no membership for either:
# no table maps a team to one, and standings carry only each team's record in
# its OWN conference's and division's games. So a team slot naming one cannot be
# answered, and is refused by name. Resolved as a team it would match nothing
# and fall through to an agent with no better source.
_CONFERENCE_WORDS = re.compile(r"\b(?:conferences?|divisions?|east(?:ern)?|west(?:ern)?|atlantic|central|southeast|northwest|pacific|southwest)\b", re.IGNORECASE)


#: What a coach question is answered with, and why it is a refusal rather than
#: a fall-through.
#:
#: No table here holds a coach - 20 base tables and 6 views, zero columns named
#: anything like it - so the SQL agent has nothing to find. Left to fall
#: through it would spend a slow round trip and then be free to fill the
#: silence from its own weights, which is the failure `check_coverage` exists
#: to stop: an agent with nothing to read writes a confident answer. So this
#: refuses, and names which fact is missing.
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
"""


def coach(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """Refuse a question about a coach, naming the real cause.

    Assigned by :func:`association.query.router.route` from the question's own
    words rather than by the model - it is in
    :data:`association.query.router.CODE_ASSIGNED_INTENTS`, so no
    ``ROUTER_PROMPT`` or ``ROUTER_SCHEMA`` change was needed and no other
    question's routing can have moved.

    Takes no slots and reads no table: there is nothing to read.

    .. versionadded:: 4.0.0
    """
    del ctx, slots  # A refusal needs neither a connection nor a slot.
    return TemplateResult(data={"message": COACH_REFUSAL, "unanswerable": "coach"}, answer=COACH_REFUSAL)


def _conference_refusal(slots: dict[str, Any]) -> TemplateResult | None:
    """A refusal naming the real cause, if any team slot holds a conference or a
    division rather than a team."""
    listed: list[Any] = slots["teams"] if isinstance(slots.get("teams"), list) else []
    named = next((c for c in [slots.get("team"), slots.get("opponent"), *listed] if isinstance(c, str) and _CONFERENCE_WORDS.search(c)), None)
    if named is None:
        return None
    message = (
        f"The warehouse has no conference or division membership for any team, so nothing about {named!r} can be tallied from it. "
        "The only conference figure it holds is each team's record in its own conference's games."
    )
    return TemplateResult(data={"message": message, "unanswerable": named}, answer=message)


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


def _team_record_month(situation: Any) -> int | None:
    """The calendar month ``situation`` names ("in october" -> 10), or None for
    anything else - including a value naming no month at all. A real column
    (the game's own Eastern date) can filter to a month; nothing here can
    filter to a weekday, an age or a window, so those stay refused.

    .. versionadded:: 4.3.0
    """
    if not isinstance(situation, str):
        return None
    match = _MONTH_SITUATION.fullmatch(situation.strip())
    if match is None:
        return None
    return [name.lower() for name in _MONTH_NAMES].index(match.group(1).lower()) + 1


def _team_record_month_and_split(split: Any, situation: Any, limit: Any) -> tuple[str | None, int | None]:
    """The validated ``split`` and calendar ``month`` a question narrows to -
    or the refusal for a ``split`` that is not "month", a ``situation`` naming
    no month, or a bare ``limit`` with neither. Pulled out of ``team_record``
    itself so that function reads as one linear sequence of steps rather than
    growing a branch for each of the three; called with the slots themselves
    (``slots.get("split")`` and so on), not the whole dict, so team_record's
    own source still names every slot it honors -
    test_every_template_honoring_a_scope_slot_actually_reads_it checks that
    literally."""
    if split is not None and split != "month":
        raise TemplateUnsupported(f"no split named {split!r}")
    month = _team_record_month(situation)
    if situation and month is None:
        raise TemplateUnsupported(f"no calendar month narrowing in situation {situation!r}")
    if limit and split is None and month is None:
        # "how did they do in their last 10 games?" is a game_log question -
        # standings only has the full-season record, and answering with it
        # under a "last 10" question is a silent substitution. game_log already
        # tallies the record over exactly the games it lists. Measured over
        # this corpus, a month narrowing or a by-month split never carries a
        # real limit of its own - the router fills `limit` with a default
        # (commonly 12, one slot per month) whether or not the question named
        # one - so only a bare limit, with neither of those, is read as "last
        # N games".
        raise TemplateUnsupported("a record over a limited set of games is a game_log question")
    return split, month


def team_record(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """A team's win-loss record: for a season, at home or on the road, against
    one team, in a postseason, in one calendar month, broken out by month, or
    across every season the warehouse holds.

    A season's record and its home/road split are read from standings, the
    authoritative source - its "Home" and "Road" strings agree with a tally of
    ``games`` for every team-season from 1994 to 2026 once each era's
    neutral-site rule is applied (through 2024 a neutral-site game counts for
    its designated home team; from 2025 it counts as neither). A record against
    one team, in a postseason, in one named month or broken out by month has no
    standings column, and is tallied from ``games`` instead - see
    team_metrics.TEAM_GAMES_SQL for what that tally removes.

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
    """
    con = ctx.con
    refused = _conference_refusal(slots)
    if refused is not None:
        return refused
    split, month = _team_record_month_and_split(slots.get("split"), slots.get("situation"), slots.get("limit"))

    teams = _team_record_teams(con, slots, slots.get("opponent"))
    if isinstance(teams, TemplateResult):
        return teams
    team, opponent = teams

    season_type = slots.get("season_type") or 2
    venue = slots.get("venue") if slots.get("venue") in VENUE_WORDS else None
    career = slots.get("span") == "career"
    season = slots.get("season") if isinstance(slots.get("season"), int) else None
    if career and season is not None:
        # "all-time ... in 2020" is either a slip or a range this cannot read.
        raise TemplateUnsupported("a career span and a single season at once")

    if split == "month":
        return _team_record_by_month(con, team, opponent, None if career else (season or current_season()), season_type, venue)
    if opponent is None and season_type == 2 and month is None:
        if career:
            return _standings_career(con, team, venue)
        return _standings_season(con, team, season or current_season(), venue)
    return _games_record(con, team, opponent, None if career else (season or current_season()), season_type, venue, month)


def _team_record_teams(con: duckdb.DuckDBPyConnection, slots: dict[str, Any], opponent_text: Any) -> tuple[Entity, Entity | None] | TemplateResult:
    """The team a record is for and the opponent it is against, if any - or the
    clarifying question one of the names needs. ``opponent_text`` is the
    ``opponent`` slot."""
    team_text = slots.get("team")
    listed = [n for n in slots.get("teams") or [] if isinstance(n, str) and n.strip()] if isinstance(slots.get("teams"), list) else []
    if not (isinstance(team_text, str) and team_text.strip()) and listed:
        # "celtics vs bulls record" can land both teams in `teams`, which
        # scope_from_question leaves alone; the first is the subject.
        team_text, listed = listed[0], listed[1:]
    team = _resolved_team(con, team_text, season=_slot_season(slots))
    if isinstance(team, TemplateResult):
        return team
    opponent = _team_record_opponent(con, slots, team, listed, opponent_text)
    if isinstance(opponent, TemplateResult):
        return opponent
    return team, opponent


def _team_record_opponent(con: duckdb.DuckDBPyConnection, slots: dict[str, Any], team: Entity, listed: list[str], opponent_text: Any) -> Entity | TemplateResult | None:
    """The ``opponent`` slot's team, or else the first other team in ``teams``."""
    if isinstance(opponent_text, str) and opponent_text.strip():
        found = _resolved_team(con, opponent_text, season=_slot_season(slots))
        if isinstance(found, TemplateResult):
            return found
        if found.id == team.id:
            raise TemplateUnsupported("team_record's opponent must differ from the team")
        return found
    for text in listed:
        found = _resolved_team(con, text, season=_slot_season(slots))
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
    return f"Note: ESPN's standings do not cover the {_possessive(team.name)} whole season in {parts}, so this record is short by those games."


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
        }
    )
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
    neutral_note = f" ({neutral} neutral-site game{'s' if neutral != 1 else ''} count{'s' if neutral == 1 else ''} as neither home nor away)" if neutral > 0 else ""
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
    answer = f"The {team.name} were {_tally(vw, vl)} {VENUE_WORDS[venue]} in the {season} regular season, {w}-{lost} overall{neutral_note}."
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
        }
    )
    return TemplateResult(data=data, answer=f"{answer} {gap}" if gap else answer)


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
        "the warehouse's standings begin there, so this is not the franchise's whole history"
        if first == min(r[0] for r in con.execute("SELECT MIN(season) FROM standings").fetchall())
        else "the first season the warehouse holds for them"
    )
    answer = f"The {team.name} are {_tally(wins, losses)} across the {len(rows)} regular seasons from {_season_name(first)} through {_season_name(last)} - {start}."
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
        },
        answer=f"{answer} {gap}" if gap else answer,
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
    answer = (
        f"The {team.name} are {_tally(vw, vl)} {VENUE_WORDS[venue]} across the {len(halves)} regular seasons from {_season_name(first)} through {_season_name(last)}"
        + (" - ESPN's standings carry no home/road split before 1993-94" if first == FIRST_FULL_REGULAR_SEASON else "")
        + "."
    )
    if neutral > 0:
        answer += f" {neutral} neutral-site game{'s' if neutral != 1 else ''} count{'s' if neutral == 1 else ''} as neither."
    data: dict[str, Any] = {
        "team": team.name,
        "venue": venue,
        "wins": vw,
        "losses": vl,
        "win_pct": vw / (vw + vl) if vw + vl else 0.0,
        "first_season": first,
        "last_season": last,
        "seasons": len(halves),
    }
    gap = _standings_gap(con, team, [(h[0], h[1]) for h in halves])
    return TemplateResult(data=data, answer=f"{answer} {gap}" if gap else answer)


def _record_narrowed(team: Entity, season: int | None, season_type: int) -> TeamNarrowed:
    """``team``'s games for a win-loss RECORD tally, over
    :class:`association.query.team_games.TeamNarrowed`:
    :func:`association.query.team_metrics.games_scope`'s own clause (which
    excludes the NBA Cup final from a regular season) plus the team filter -
    the shared base every ``team_record``/``team_leaderboard`` read against
    :data:`association.query.team_metrics.TEAM_GAMES_SQL` builds on."""
    scope, params = games_scope(season_type, season)
    return TeamNarrowed(base=["tg.team_id = ?", scope], base_params=[team.id, *params], team=team)


def _game_list_gaps(con: duckdb.DuckDBPyConnection, team: Entity, season_type: int, season: int | None) -> str | None:
    """Seasons where the games tallied for a team do not number its games in
    team_season_stats - the check that makes a tally from ``games`` safe to
    state. Measured: the 2000 and 2001 postseasons hold 15 of the Lakers' 23
    and 10 of their 16 games, and 1995 holds a Miami playoff game in a season
    Miami did not make the playoffs. Only seasons from 1994, where the totals
    exist, can be checked."""
    subquery, sub_params = team_games_subquery(_record_narrowed(team, season, season_type))
    by_season = "year(eastern_date)" if season_type == 3 else "season"
    season_filter = "" if season is None else "AND ts.season = ?"
    rows = con.execute(
        f"""
WITH tallied AS (SELECT {by_season} AS season, count(*) AS games FROM ({subquery}) t GROUP BY 1),
totals AS (SELECT ts.season, ts.gamesPlayed AS games FROM team_season_stats ts WHERE ts.team_id = ? AND ts.season_type = ? {season_filter})
SELECT coalesce(l.season, t.season) AS season, coalesce(l.games, 0), coalesce(t.games, 0)
FROM tallied l FULL OUTER JOIN totals t ON t.season = l.season
WHERE coalesce(l.season, t.season) >= ? AND coalesce(l.games, 0) <> coalesce(t.games, 0)
ORDER BY 1""",
        [*sub_params, team.id, season_type, *([season] if season is not None else []), FIRST_FULL_REGULAR_SEASON],
    ).fetchall()
    if not rows:
        return None
    parts = _joined([f"{s} ({int(listed)} listed, {int(played)} played)" for s, listed, played in rows])
    kind = "postseason" if season_type == 3 else "regular-season"
    return f"Note: ESPN's game list and the {_possessive(team.name)} season totals disagree on how many {kind} games they played in {parts}, so this tally is off by those games."


def _no_team_games(con: duckdb.DuckDBPyConnection, team: Entity, opponent: Entity | None, season: int | None, season_type: int, month: int | None = None) -> str:
    """Why a tally found nothing. Three different facts, and three sentences:
    the warehouse has no games that season at all (it holds no 1988
    postseason), the team played none, or the two teams did not meet -
    each narrowed to the named month too, where the question asked for one, so
    a team with games in OTHER months is not told it has none at all."""
    kind = "postseason" if season_type == 3 else "regular-season"
    where = f" in {_MONTH_NAMES[month - 1]}" if month is not None else ""
    if season is None:
        if opponent is None:
            return f"The warehouse holds no {kind} games for the {team.name}{where}."
        return f"The warehouse holds no {kind} games between the {team.name} and the {opponent.name}{where}."
    scope, params = games_scope(season_type, season)
    period = _period(season, season_type)
    counts = con.execute(f"{TEAM_GAMES_SQL} SELECT count(*), count(*) FILTER (WHERE team_id = ?) FROM team_games WHERE {scope}", [team.id, *params]).fetchone()
    league, own = counts if counts else (0, 0)
    if not league:
        return f"The warehouse holds no {period} games for any team."
    if opponent is not None and own:
        return f"The {team.name} and the {opponent.name} did not meet{where} in the {period}."
    return f"The {team.name} played no games{where} in the {period}."


def _games_record(con: duckdb.DuckDBPyConnection, team: Entity, opponent: Entity | None, season: int | None, season_type: int, venue: str | None, month: int | None = None) -> TemplateResult:
    """A record tallied from ``games``: against one team, or in a postseason,
    for one season or (``season`` None) every season it holds, optionally
    narrowed to one calendar month - ``standings`` has no game-level date to
    filter a month from, so a month narrowing reaches this path even where
    neither an opponent nor a postseason would have."""
    if season is not None:
        # _sources_for sends this path through the `games` floor, but a record
        # against a team named only in `teams` reaches it with the standings'
        # floor instead - so the floor is checked where the table is read.
        refused = unavailable(("games",), season, season_type)
        if refused is not None:
            return TemplateResult(data={"team": team.name, "season": season, "message": refused}, answer=refused)
    games = _games_record_games(con, team, opponent, season, season_type, month)
    shown = [g for g in games if venue is None or g["venue"] == venue]
    wins = sum(1 for g in shown if g["won"])
    losses = len(shown) - wins
    kind = "postseason" if season_type == 3 else "regular season"
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
    }

    if not games:
        return TemplateResult(data={**data, "games": []}, answer=_no_team_games(con, team, opponent, season, season_type, month))

    answer = _games_record_answer(team, opponent, season, season_type, venue, games, shown, month)
    if opponent is not None and season_type == 2:
        cup_text, cup_final = _games_record_cup_final(con, team, opponent, season)
        answer += cup_text
        data["cup_final"] = cup_final
    gap = _game_list_gaps(con, team, season_type, season)
    if gap:
        answer += f"\n  {gap}"
    data["games"] = shown
    return TemplateResult(data=data, answer=answer)


def _games_record_games(con: duckdb.DuckDBPyConnection, team: Entity, opponent: Entity | None, season: int | None, season_type: int, month: int | None = None) -> list[dict[str, Any]]:
    """The team's games in scope, against ``opponent`` and/or in ``month`` if
    named, oldest first."""
    narrowed = _record_narrowed(team, season, season_type)
    if opponent is not None:
        narrowed.narrow("tg.opponent_id = ?", opponent.id)
    if month is not None:
        narrowed.narrow("month(tg.eastern_date) = ?", month)
    select = f"tg.eastern_date, tg.side, tg.neutral, tg.team_score, tg.opponent_score, tg.won, {season_name_sql('o.team_id', 'tg.season', 'o.display_name')}"
    sql, params = team_rows_sql(narrowed, select, order="tg.eastern_date", join=" JOIN teams o ON o.team_id = tg.opponent_id")
    rows = con.execute(sql, params).fetchall()
    return [{"date": str(r[0]), "venue": "neutral" if r[2] else r[1], "team_score": r[3], "opponent_score": r[4], "won": bool(r[5]), "opponent": r[6]} for r in rows]


def _games_record_answer(
    team: Entity, opponent: Entity | None, season: int | None, season_type: int, venue: str | None, games: list[dict[str, Any]], shown: list[dict[str, Any]], month: int | None = None
) -> str:
    """The tallied record, its home/away split and, for one season against one team, the meetings."""
    wins = sum(1 for g in shown if g["won"])
    losses = len(shown) - wins
    if season is not None:
        span = f"the {_period(season, season_type)}"
    elif season_type == 3:
        span = "every postseason from 1989 through the latest - the warehouse's game list starts with the 1989 playoffs"
    else:
        span = f"the regular seasons from {_season_name(FIRST_FULL_REGULAR_SEASON)} on - the first the warehouse holds every game of"
    against = f" against the {opponent.name}" if opponent else ""
    where_played = f" {VENUE_WORDS[venue]}" if venue else ""
    month_phrase = f" in {_MONTH_NAMES[month - 1]}" if month is not None else ""
    verb = "went" if season is not None else "are"
    answer = f"The {team.name} {verb} {_tally(wins, losses)}{month_phrase}{where_played}{against} in {span}."
    if venue is None:
        answer += _games_record_split(games)
    elif len(shown) < len(games) and any(g["venue"] == "neutral" for g in games):
        answer += "\n  Neutral-site games count as neither home nor away."
    # A season's meetings are few enough to list, and the list is what "vs"
    # questions usually want next.
    if opponent is not None and season is not None and shown:
        answer += "\n" + "\n".join(
            f"  {g['date']}  {'W' if g['won'] else 'L'} {g['team_score']}-{g['opponent_score']}  {'at' if g['venue'] == 'away' else 'vs'} {g['opponent']}"
            + (" (neutral site)" if g["venue"] == "neutral" else "")
            for g in shown
        )
    return answer


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
    games = _games_record_games(con, team, opponent, season, season_type)
    shown = [g for g in games if venue is None or g["venue"] == venue]
    if not shown:
        return TemplateResult(data={"team": team.name, "season": season, "months": []}, answer=_no_team_games(con, team, opponent, season, season_type))
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
        months_data.append({"month": _MONTH_NAMES[month - 1], "games": len(entries), "wins": wins, "losses": losses})
    if season is not None:
        span = f"the {_period(season, season_type)}"
    elif season_type == 3:
        span = "every postseason from 1989 through the latest - the warehouse's game list starts with the 1989 playoffs"
    else:
        span = f"the regular seasons from {_season_name(FIRST_FULL_REGULAR_SEASON)} on - the first the warehouse holds every game of"
    against = f" against the {opponent.name}" if opponent else ""
    where_played = f" {VENUE_WORDS[venue]}" if venue else ""
    title = f"The {team.name}, record by month{where_played}{against}, {span}:"
    answer = _table(title, ["G", "W-L"], rows)
    data = {"team": team.name, "season": season, "opponent": opponent.name if opponent else None, "venue": venue, "months": months_data}
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


def _games_record_cup_final(con: duckdb.DuckDBPyConnection, team: Entity, opponent: Entity, season: int | None) -> tuple[str, list[dict[str, Any]]]:
    """Any NBA Cup final the two teams met in, as sentences and as data."""
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
    sql, params = team_rows_sql(narrowed, "tg.eastern_date, tg.team_score, tg.opponent_score, tg.won", order="tg.eastern_date")
    cup = con.execute(sql, params).fetchall()
    text = "".join(f"\n  They also met in the NBA Cup final on {date}, which counts in no standings: {'won' if won else 'lost'} {own}-{theirs}." for date, own, theirs, won in cup)
    return text, [{"date": str(d), "won": bool(won), "team_score": own, "opponent_score": theirs} for d, own, theirs, won in cup]


DEFAULT_TEAM_LEADERBOARD_LIMIT = 10


RATING_NOTE = "Ratings and pace count possessions as FGA - OREB + TOV + 0.44 x FTA."


def _metric_cell(metric: TeamMetric, value: float) -> str:
    return f"{value:.1f}%" if metric.percent else f"{value:.1f}"


def _uses_possessions(keys: list[str]) -> bool:
    return any(k in ("offensive_rating", "defensive_rating", "net_rating", "pace") for k in keys)


def _first_season_refusal(metric: TeamMetric, season: int) -> TemplateResult | None:
    if season >= metric.first_season:
        return None
    message = f"{metric.label.capitalize()} can't be given for {season}: {metric.first_season_reason}."
    return TemplateResult(data={"message": message, "season": season}, answer=message)


def _incomplete_opponents(metric: TeamMetric, period: str, lines: list[TeamLine], subject: str | None = None) -> str:
    """Why an opponent-based metric has no value: the games behind the points
    allowed do not number the games behind everything else. Names the team
    asked about when it is one of the short ones."""
    short = [line for line in lines if line.values.get(_metric_key(metric)) is None]
    example = next((line for line in short if line.team == subject), short[0])
    others = len(short) - 1
    return (
        f"{metric.label.capitalize()} can't be given for the {period}: ESPN's game list holds "
        f"{example.listed_games or 0} of the {_possessive(example.team)} {example.games} games"
        + (f", and is short for {others} other team{'s' if others != 1 else ''}" if others else "")
        + " - points allowed over fewer games than everything else would make the figure wrong without looking wrong."
    )


def _metric_key(metric: TeamMetric) -> str:
    return next(key for key, candidate in TEAM_METRICS.items() if candidate is metric)


def team_stat(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """One team's season numbers, each with its rank in the league.

    With a ``stat`` it answers that one metric; with none, a compact line -
    points for and against, pace, offensive/defensive/net rating, 3-point
    percentage, rebounds, assists and turnovers. The stat is mapped through
    team_metrics.STAT_ALIASES, an explicit whitelist, and a word it does not
    know is refused rather than matched to something close.

    .. versionadded:: 2.1.0
    """
    con = ctx.con
    refused = _conference_refusal(slots)
    if refused is not None:
        return refused
    team = _resolved_team(con, slots.get("team"), season=_slot_season(slots))
    if isinstance(team, TemplateResult):
        return team
    stat = slots.get("stat")
    key = resolve_team_metric(stat)
    if key is None and isinstance(stat, str) and stat.strip():
        raise TemplateUnsupported(f"no team metric for stat {stat!r}")
    season = slots.get("season") or current_season()
    season_type = slots.get("season_type") or 2
    period = _period(season, season_type)

    if key is not None and TEAM_METRICS[key].expression is None:
        return _team_record_rank(con, team, key, season, season_type)
    wanted = [key] if key else list(DEFAULT_TEAM_LINE)
    if key is not None:
        refusal = _first_season_refusal(TEAM_METRICS[key], season)
        if refusal is not None:
            return refusal

    lines = season_table(con, season, season_type)
    mine = next((line for line in lines if line.team == team.name), None)
    if mine is None:
        return _team_stat_missing(con, team, season, season_type, period)

    stats = _team_stat_table(lines, wanted, team, mine)

    if key is not None:
        return _team_stat_single(key, stats, period, team, mine, season, lines)
    return _team_stat_summary(stats, wanted, team, period, mine, season)


def _team_stat_missing(con: duckdb.DuckDBPyConnection, team: Entity, season: int, season_type: int, period: str) -> TemplateResult:
    """team_stat's answer when the team has no season line. Which fact is
    missing decides the sentence: a team that did not reach the postseason is
    not a team the warehouse lacks numbers for."""
    if season_type == 3 and any(line.team == team.name for line in season_table(con, season, 2)):
        answer = f"The {team.name} did not play in the {period}."
    else:
        answer = f"The warehouse has no {period} team stats for the {team.name}."
    return TemplateResult(data={"team": team.name, "season": season, "stats": {}}, answer=answer)


def _team_stat_table(lines: list[TeamLine], wanted: list[str], team: Entity, mine: TeamLine) -> dict[str, dict[str, Any]]:
    """team_stat's value and league rank for each stat asked for."""
    stats: dict[str, dict[str, Any]] = {}
    for name in wanted:
        metric = TEAM_METRICS[name]
        value = mine.values.get(name)
        complete = all(line.values.get(name) is not None for line in lines)
        rank = None
        if value is not None and complete:
            rank = next(r for r, t, _ in ranked({line.team: line.values[name] or 0.0 for line in lines}, descending_for(metric, "best")) if t == team.name)
        stats[metric.label] = {"value": value, "rank": rank, "of": len(lines)}
    return stats


def _team_stat_single(key: str, stats: dict[str, dict[str, Any]], period: str, team: Entity, mine: TeamLine, season: int, lines: list[TeamLine]) -> TemplateResult:
    """team_stat's answer for one named stat: its value and rank, or the
    incomplete-opponents refusal where the value itself is missing."""
    metric = TEAM_METRICS[key]
    entry = stats[metric.label]
    if entry["value"] is None:
        answer = _incomplete_opponents(metric, period, lines, team.name)
        return TemplateResult(data={"team": team.name, "season": season, "stats": stats, "message": answer}, answer=answer)
    where = ""
    if entry["rank"] is not None:
        order = "best" if metric.lower_is_better is not None else "highest"
        where = f", {_ordinal(entry['rank'])}-{order} of {entry['of']} teams"
    answer = f"The {_possessive(team.name)} {metric.label} was {_metric_cell(metric, entry['value'])} in the {period} ({mine.games} games){where}."
    if _uses_possessions([key]):
        answer += f" {RATING_NOTE}"
    return TemplateResult(data={"team": team.name, "season": season, "games": mine.games, "stats": stats}, answer=answer)


def _team_stat_summary(stats: dict[str, dict[str, Any]], wanted: list[str], team: Entity, period: str, mine: TeamLine, season: int) -> TemplateResult:
    """team_stat's compact multi-stat table, with a note where a value or a
    rank had to be left out."""
    label_width = max(len(label) for label in stats)
    cells = {label: "-" if e["value"] is None else _metric_cell(TEAM_METRICS[name], e["value"]) for (label, e), name in zip(stats.items(), wanted, strict=True)}
    value_width = max(5, *(len(c) for c in cells.values()))
    lines_out = [f"{team.name}, {period} ({mine.games} games):", f"{' ' * label_width}  {'value'.rjust(value_width)}  rank"]
    for label, entry in stats.items():
        rank_cell = f"{_ordinal(entry['rank'])} of {entry['of']}" if entry["rank"] is not None else "-"
        lines_out.append(f"{label.ljust(label_width)}  {cells[label].rjust(value_width)}  {rank_cell}")
    notes = ["Rank 1st is the best in the league (for pace, the fastest).", RATING_NOTE]
    if any(e["value"] is None for e in stats.values()):
        notes.append("A '-' needs points allowed, and ESPN's game list does not hold all of this team's games that season.")
    elif any(e["rank"] is None for e in stats.values()):
        notes.append("A rank is left out where ESPN's game list is short for other teams that season.")
    return TemplateResult(
        data={"team": team.name, "season": season, "games": mine.games, "stats": stats},
        answer="\n".join([*lines_out, *notes]),
    )


def _team_record_rank(con: duckdb.DuckDBPyConnection, team: Entity, key: str, season: int, season_type: int) -> TemplateResult:
    """team_stat for a record: the team's record and where it ranks."""
    records = record_table(con, season, season_type)
    period = _period(season, season_type)
    mine = next((r for r in records if r.team == team.name), None)
    if mine is None:
        answer = f"The {team.name} have no {period} record in the warehouse."
        return TemplateResult(data={"team": team.name, "season": season}, answer=answer)
    order = ranked({r.team: r.win_pct for r in records}, True)
    rank = next(r for r, t, _ in order if t == team.name)
    answer = f"The {team.name} were {_tally(mine.wins, mine.losses)} in the {period}, the {_ordinal(rank)}-best record of {len(records)} teams."
    return TemplateResult(data={"team": team.name, "season": season, "wins": mine.wins, "losses": mine.losses, "rank": rank, "of": len(records)}, answer=answer)


def _venue_records(con: duckdb.DuckDBPyConnection, season: int, season_type: int, venue: str) -> list[TeamRecord] | str:
    """Every team's home or road record: standings' own strings for a regular
    season, a tally of ``games`` for a postseason. A string explains why there
    is none."""
    if season_type == 2:
        column = '"Home"' if venue == "home" else '"Road"'
        rows = con.execute(
            f"SELECT {season_name_sql('t.team_id', 's.season', 't.display_name')}, s.{column} FROM standings s JOIN teams t ON t.team_id = s.team_id WHERE s.season = ? ORDER BY 1", [season]
        ).fetchall()
        parsed = [(name, _parse_record(text)) for name, text in rows]
        records = [TeamRecord(team=name, wins=r[0], losses=r[1]) for name, r in parsed if r and sum(r) > 0]
        if rows and not records:
            return f"ESPN's {season} standings carry no home/road split (it reads 0-0 for every team before 1993-94)."
        return records
    scope, params = games_scope(season_type, season)
    rows = con.execute(
        f"{TEAM_GAMES_SQL} SELECT {season_name_sql('t.team_id', 'tg.season', 't.display_name')}, sum(won::INT), sum((NOT won)::INT) FROM team_games tg JOIN teams t ON t.team_id = tg.team_id "
        f"WHERE {scope} AND tg.side = ? AND NOT tg.neutral GROUP BY 1 ORDER BY 1",
        [*params, venue],
    ).fetchall()
    return [TeamRecord(team=name, wins=int(w), losses=int(lost)) for name, w, lost in rows]


def team_leaderboard(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """Every team ranked by one metric from team_metrics.TEAM_METRICS - "which
    team scores the most points per game", "lowest defensive rating", "best
    record".

    The ``rank`` slot picks the end: "most" and "fewest" are the raw ends of the
    scale, "best" and "worst" depend on the metric (the fewest turnovers are the
    best), and no rank means best. The answer says which end it lists first,
    because a list of the fastest teams under a question about the slowest
    would otherwise look perfectly right.

    Before this, "which team scores the most points per game" was answered with
    the players' scoring leaders.

    .. versionadded:: 2.1.0

    .. versionchanged:: 4.4.0
       Honors ``since`` for a record metric (step 3, C4b): "nba team with
       least playoff wins since 2022" (ISSUES.md) - see
       :func:`_team_leaderboard_values`.
    """
    con = ctx.con
    refused = _conference_refusal(slots)
    if refused is not None:
        return refused
    stat = slots.get("stat")
    key = resolve_team_metric(stat)
    if key is None:
        raise TemplateUnsupported(f"no team metric for stat {stat!r}")
    metric = TEAM_METRICS[key]
    since = slots.get("since")
    since = since if isinstance(since, int) and since and not isinstance(since, bool) else None
    if since is not None and isinstance(slots.get("season"), int) and slots["season"]:
        raise TemplateUnsupported(f"since {since} and the {slots['season']} season at once")
    # `season` still settles to a real year even under `since` - unread by
    # _team_leaderboard_values' since-bounded path, and here only for the
    # named team's own-season name lookup below, which stays "now" either way.
    season = slots.get("season") or current_season()
    season_type = slots.get("season_type") or 2
    period = f"seasons since {since}" if since is not None else _period(season, season_type)
    rank_word = slots.get("rank") if slots.get("rank") in ("most", "fewest", "best", "worst") else None
    descending = descending_for(metric, rank_word)
    limit = _clamp_limit(slots.get("limit"), default=DEFAULT_TEAM_LEADERBOARD_LIMIT)
    venue = slots.get("venue") if slots.get("venue") in VENUE_WORDS else None

    named = _team_leaderboard_named(con, slots)
    if isinstance(named, TemplateResult):
        return named

    values_or_result = _team_leaderboard_values(con, key, metric, season, season_type, venue, period, since=since)
    if isinstance(values_or_result, TemplateResult):
        return values_or_result
    values, display = values_or_result

    title = f"{metric.label.capitalize()}{f' {VENUE_WORDS[venue]}' if venue else ''}, {period}"
    if not values:
        answer = f"The warehouse has no {period} numbers to rank teams by {metric.label}."
        return TemplateResult(data={"question_shape": title, "season": None if since is not None else season, "teams": []}, answer=answer)

    order = ranked(values, descending)
    end = _team_leaderboard_order_label(metric, key, rank_word, descending)
    return _team_leaderboard_result(order, display, limit, named, title, None if since is not None else season, end, key)


def _team_leaderboard_named(con: duckdb.DuckDBPyConnection, slots: dict[str, Any]) -> Entity | TemplateResult | None:
    """team_leaderboard's named team, resolved so its own row can be appended
    past the limit where it would otherwise be cut off; None where the
    question named none."""
    if isinstance(slots.get("team"), str) and slots["team"].strip():
        return _resolved_team(con, slots["team"], season=_slot_season(slots))
    return None


def _team_leaderboard_since_records(con: duckdb.DuckDBPyConnection, season_type: int, since: int) -> list[TeamRecord]:
    """Every team's win-loss record across the postseasons or regular seasons
    from ``since`` on, tallied straight off the team-games relation and
    grouped by team - the record metrics' since-bounded counterpart to
    :func:`team_metrics.record_table`, which only ever reads one season.

    Named by the team's CURRENT display name rather than a per-season one
    (:func:`association.nba.franchises.season_name_sql`): a total across many
    seasons has no single season left to key a franchise name off, and every
    "since" question measured asks about a span recent enough that the
    current name is also the right one for all of it.

    .. versionadded:: 4.4.0
    """
    span = _span_of(None, None, season_type, "games", since=since)
    clause, params = _team_span_clause(span)
    narrowed = TeamNarrowed(base=["tg.team_id IN (SELECT team_id FROM teams)", "tg.season_type = ?", clause], base_params=[season_type, *params])
    base, sub_params = team_games_subquery(narrowed)
    rows = con.execute(
        f"SELECT t.display_name, count(*) FILTER (WHERE x.won) AS wins, count(*) FILTER (WHERE NOT x.won) AS losses FROM ({base}) x JOIN teams t ON t.team_id = x.team_id GROUP BY 1",
        sub_params,
    ).fetchall()
    return [TeamRecord(team=name, wins=int(wins), losses=int(losses)) for name, wins, losses in rows]


def _team_leaderboard_values(
    con: duckdb.DuckDBPyConnection, key: str, metric: TeamMetric, season: int, season_type: int, venue: str | None, period: str, since: int | None = None
) -> tuple[dict[str, float], dict[str, str]] | TemplateResult:
    """team_leaderboard's per-team values and their display strings: the
    standings for a record metric (venue-split where asked, or a tally of the
    relation across a ``since``-bounded span of seasons), team_metrics
    otherwise - each with its own early-refusal path.

    .. versionchanged:: 4.4.0
       Honors ``since`` for a record metric (step 3, C4b) - "nba team with
       least playoff wins since 2022" (ISSUES.md) used to refuse for want of
       it. Every other metric still refuses it: a season line (team_metrics'
       own numbers) has no way to sum across a span of seasons yet.
    """
    if metric.expression is None:
        if since:
            if venue is not None:
                raise TemplateUnsupported("a since-bounded record has no home/road split yet")
            records: list[TeamRecord] | str = _team_leaderboard_since_records(con, season_type, since)
        else:
            records = _venue_records(con, season, season_type, venue) if venue else record_table(con, season, season_type)
        if isinstance(records, str):
            return TemplateResult(data={"message": records, "season": season}, answer=records)
        values = {r.team: (r.win_pct if key == "record" else 1 - r.win_pct) for r in records}
        display = {r.team: _tally(r.wins, r.losses) for r in records}
        return values, display
    if since:
        raise TemplateUnsupported(f"team season stats have no way to sum {metric.label} across a span of seasons yet")
    if venue is not None:
        # Team season stats have no home/road split; team_box_stats does,
        # and the agent can reach it.
        raise TemplateUnsupported(f"team season stats have no {venue} split for {metric.label}")
    refusal = _first_season_refusal(metric, season)
    if refusal is not None:
        return refusal
    lines = season_table(con, season, season_type)
    if lines and any(line.values.get(key) is None for line in lines):
        message = _incomplete_opponents(metric, period, lines)
        return TemplateResult(data={"message": message, "season": season}, answer=message)
    values = {line.team: line.values[key] or 0.0 for line in lines}
    display = {team: _metric_cell(metric, value) for team, value in values.items()}
    return values, display


def _team_leaderboard_order_label(metric: TeamMetric, key: str, rank_word: str | None, descending: bool) -> str:
    """team_leaderboard's "highest/lowest/best/worst first" phrase - the
    answer says which end it lists first, because a list of the fastest teams
    under a question about the slowest would otherwise look perfectly right."""
    if metric.lower_is_better is None or rank_word in ("most", "fewest"):
        end = "highest first" if descending else "lowest first"
    else:
        best_first = descending == (metric.lower_is_better is False)
        end = ("best first" if best_first else "worst first") + (" (highest)" if descending else " (lowest)")
    if key in ("record", "losses"):
        end = "best record first" if (key == "record") == descending else "worst record first"
    return end


def _team_leaderboard_result(order: list[tuple[int, str, float]], display: dict[str, str], limit: int, named: Entity | None, title: str, season: int | None, end: str, key: str) -> TemplateResult:
    """team_leaderboard's final table: the ranked rows up to the limit, with a
    named team's own row appended past it where it would otherwise be cut.
    ``season`` is None for a since-bounded ranking, which spans more than one."""
    shown = order[:limit]
    extra = [row for row in order[limit:] if named is not None and row[1] == named.name]
    name_width = max(len(team) for _, team, _ in [*shown, *extra])
    value_width = max(len(display[team]) for _, team, _ in [*shown, *extra])
    rows_out = [f"{rank:>2}  {team.ljust(name_width)}  {display[team].rjust(value_width)}" for rank, team, _ in shown]
    if extra:
        rows_out += ["    ...", *(f"{rank:>2}  {team.ljust(name_width)}  {display[team].rjust(value_width)}" for rank, team, _ in extra)]
    lines_out = [f"{title} - {end}, of {len(order)} teams:", *rows_out]
    if _uses_possessions([key]):
        lines_out.append(RATING_NOTE)
    teams = [{"rank": rank, "team": team, "value": value, "display": display[team]} for rank, team, value in [*shown, *extra]]
    return TemplateResult(data={"question_shape": title, "season": season, "order": end, "teams": teams}, answer="\n".join(lines_out))


# ESPN's season types, as the power index uses them. 5 is not in the rest of
# the warehouse: its snapshots are dated between the regular season's end and
# the first playoff game (2026-04-18, 2025-04-19), i.e. the play-in.
#: ESPN's season-type code for a preseason power-index snapshot.
#:
#: Named because it is a tiebreak, not a filter: a preseason rating is a real
#: answer where it is the only snapshot holding a team, and merely the worst
#: one to pick among the pre-playoff snapshots when neither is the
#: regular-season one - :data:`BPI_REGULAR` is preferred outright and never
#: reaches this tiebreak.
#:
#: .. versionadded:: 2.2.0
#: .. versionchanged:: 4.0.1
#:    Its docstring now says what actually reaches the tiebreak - a
#:    regular-season question no longer compares dates against preseason at
#:    all, `team_outlook` reads the regular-season snapshot outright.
BPI_PRESEASON = 1


#: ESPN's season-type code for a regular-season power-index snapshot.
#:
#: A regular-season question (``season_type`` unset or ``2``) reads this
#: snapshot outright when it holds the team asked about, rather than
#: whichever pre-playoff snapshot ESPN stamped last - see `team_outlook`.
#:
#: .. versionadded:: 4.0.1
BPI_REGULAR = 2


BPI_SNAPSHOT_NAMES = {1: "preseason", 2: "regular-season", 3: "postseason", 5: "play-in"}


# (label, column) for the chances a snapshot carries. `probmakeconfchamp` is
# reaching the conference finals, not winning them: in the 2026 postseason
# snapshot every conference finalist reads 100 and every other team 0.
_BPI_CHANCES = (("playoffs", "probmakeplayoffs"), ("conference finals", "probmakeconfchamp"), ("Finals", "probmaketitlegame"), ("title", "probwintitle"))


def _team_outlook_choose(snapshots: list[tuple[Any, ...]], postseason: bool) -> tuple[Any, ...] | None:
    """The one row of `snapshots` (grouped by ``season_type``, one row per
    type) that answers this question, or None where nothing does.

    A postseason question reads the postseason snapshot. A regular-season
    question reads the regular-season snapshot outright whenever it holds the
    team, never "whichever pre-playoff snapshot is latest" - that used to be
    the play-in one (season type 5) in 2023, 2025 and 2026, because the paging
    fix gave it all 30 teams and it is stamped after the regular-season
    snapshot in those years. See ``DATA.md``, "ESPN's power index is a paged
    collection, and holds all 30 teams", and ``ISSUES.md`` #88. Falling back
    to the latest OTHER pre-playoff snapshot (preseason or play-in), and then
    to the postseason one, only happens when no regular-season snapshot holds
    this team - measured across every season `team_power_index` holds
    (2017-2026), that never happens today, but the fallback exists so a gap
    answers from the next-best snapshot instead of refusing outright.

    .. versionadded:: 4.0.1
    """
    regular = next((s for s in snapshots if s[0] == BPI_REGULAR and s[3]), None)
    pre = [s for s in snapshots if s[0] not in (BPI_REGULAR, 3) and s[3]]
    post = [s for s in snapshots if s[0] == 3 and s[3]]
    if postseason:
        candidates = post
    elif regular is not None:
        candidates = [regular]
    else:
        candidates = pre or post
    return candidates[-1] if candidates else None


def team_outlook(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """A team's ESPN Basketball Power Index: its rating and where it sits,
    projected record, playoff and title chances, and strength of schedule.

    A season holds one snapshot per season type - regular season, postseason and
    play-in from 2023, preseason as well in 2017-18 - each covering all 30 teams,
    and ESPN overwrites rather than keeping a dated series. Every answer names
    the snapshot, its date and how many teams it holds, and a team missing from
    one is told which snapshots exist rather than that there is "no data". A
    regular-season question reads the regular-season snapshot outright when it
    holds the team asked about; a postseason one reads the postseason snapshot.

    The sizes this docstring used to quote ("2026 has a play-in snapshot of 13
    teams and a postseason one of 12, and no regular-season snapshot at all")
    were an artifact of reading one 25-row page of a paged collection, not of
    ESPN's coverage. See ``DATA.md``, "ESPN's power index is a paged collection,
    and holds all 30 teams".

    Where a team stands is counted among the teams in the snapshot, from their
    ratings. ESPN's own rank columns are ranks only from 2022: before it they
    hold values like 83, 2,625 and 26,058.

    .. versionadded:: 2.1.0
    .. versionchanged:: 4.0.1
       A regular-season question used to read whichever pre-playoff snapshot
       ESPN stamped last, which was the play-in one (season type 5) in 2023,
       2025 and 2026 once the paging fix gave it all 30 teams - so the same
       question named a different snapshot depending on the season. It now
       reads the regular-season snapshot outright whenever one holds the team,
       and falls back to the latest other pre-playoff snapshot, then the
       postseason one, only where no regular-season snapshot does. See
       ``ISSUES.md``, "A regular-season BPI question answers from the play-in
       snapshot in 2023, 2025 and 2026" (#88).
    """
    con = ctx.con
    refused = _conference_refusal(slots)
    if refused is not None:
        return refused
    team = _resolved_team(con, slots.get("team"), season=_slot_season(slots))
    if isinstance(team, TemplateResult):
        return team
    season = slots.get("season") or current_season()
    postseason = (slots.get("season_type") or 2) == 3

    # Ordered by date, then with a PRESEASON snapshot pushed behind any other of
    # the same date, because `candidates[-1]` below takes the last row. Measured
    # on the backfilled table, nothing needs this yet: 2018's two snapshots are
    # stamped one minute apart (preseason 07:47Z, regular season 07:48Z on
    # 2020-10-12, the day ESPN backfilled both), so the regular season already
    # sorts last. That one minute is the whole margin, and it is ESPN's to
    # change - the tiebreak makes the choice explicit rather than resting on it.
    snapshots = con.execute(
        "SELECT season_type, max(last_updated), count(DISTINCT team_id), bool_or(team_id = ?) FROM team_power_index WHERE season = ? "
        f"GROUP BY 1 ORDER BY 2, CASE season_type WHEN {BPI_PRESEASON} THEN 0 ELSE 1 END",
        [team.id, season],
    ).fetchall()

    listing = [_team_outlook_describe(k, u, n) for k, u, n, _ in snapshots]
    if not snapshots:
        message = f"ESPN's power index has no {season} snapshot in the warehouse."
        return TemplateResult(data={"team": team.name, "season": season, "message": message}, answer=message)
    chosen = _team_outlook_choose(snapshots, postseason)
    if chosen is None:
        return _team_outlook_missing(team, season, postseason, snapshots, listing)

    kind = chosen[0]
    row = con.execute(
        "SELECT last_updated, bpi, bpioffense, bpidefense, numwins, numlosses, projectedw, projectedl, probmakeplayoffs, probmakeconfchamp, probmaketitlegame, probwintitle, "
        "sosoverall, sosoverallrank, (SELECT count(*) FROM team_power_index o WHERE o.season = p.season AND o.season_type = p.season_type AND o.bpi > p.bpi) "
        "FROM team_power_index p WHERE season = ? AND season_type = ? AND team_id = ? ORDER BY last_updated DESC LIMIT 1",
        [season, kind, team.id],
    ).fetchone()
    assert row is not None  # the snapshot was chosen because it holds this team
    return _team_outlook_detail(team, season, postseason, chosen, kind, row, snapshots, listing)


def _team_outlook_describe(kind: int, updated: Any, teams: int) -> str:
    """team_outlook's phrase for one snapshot: its kind, date and team count."""
    return f"a {BPI_SNAPSHOT_NAMES.get(kind, f'type-{kind}')} snapshot ({str(updated)[:10]}, {teams} team{'s' if teams != 1 else ''})"


def _team_outlook_missing(team: Entity, season: int, postseason: bool, snapshots: list[tuple[Any, ...]], listing: list[str]) -> TemplateResult:
    """team_outlook's answer when no snapshot of the kind asked for holds the
    team. Which snapshot is missing, or which one the team is missing from, is
    the whole answer - "no data" would send the reader to the wrong place."""
    have = _joined(listing)
    if postseason and not any(s[0] == 3 for s in snapshots):
        gap = "and no postseason snapshot"
    elif postseason:
        gap = f"and the {team.name} are not in its postseason snapshot"
    else:
        gap = f"and the {team.name} are {'not in it' if len(listing) == 1 else 'in neither' if len(listing) == 2 else 'in none of them'}"
    message = f"ESPN's power index for {season} has {have}, {gap}."
    holding = [d for (*_, has), d in zip(snapshots, listing, strict=True) if has]
    if holding:
        message += f" The {team.name} are only in {_joined(holding)} - ask about the regular season to see it."
    return TemplateResult(data={"team": team.name, "season": season, "snapshots": listing, "message": message}, answer=message)


def _team_outlook_record_line(kind: int, wins: Any, losses: Any, proj_w: Any, proj_l: Any) -> str:
    """team_outlook's record line: for a postseason snapshot, the finished
    regular season plus any playoff games added on top - a team whose record
    still equals the projection played none (the 2026 Hornets, out in the
    play-in) - or the record so far with a projection otherwise."""
    played = int(wins) + int(losses)
    regular = (round(proj_w), round(proj_l)) if proj_w is not None and proj_l is not None else None
    if kind == 3:
        # In a postseason snapshot the "projection" is the finished regular
        # season, and the record adds the playoff games to it.
        if regular and played > sum(regular):
            return f"  record {int(wins)}-{int(losses)} including the playoffs; {regular[0]}-{regular[1]} in the regular season"
        return f"  record {int(wins)}-{int(losses)}, no playoff games"
    projection = f", projected {regular[0]}-{regular[1]}" if regular else ""
    return f"  record {int(wins)}-{int(losses)}{projection}" if played else f"  no games played yet{projection}"


def _team_outlook_headline(team: Entity, season: int, postseason: bool, chosen: tuple[Any, ...], kind: int, updated: Any) -> list[str]:
    """team_outlook's opening line, plus a note when the postseason snapshot
    stands in for a missing regular-season one, or when ESPN stamped the
    snapshot after the season it describes had ended (every 2017-2020
    snapshot is stamped 2019 or 2020, and the 2017 preseason and
    regular-season snapshots carry identical ratings under different
    records)."""
    name = BPI_SNAPSHOT_NAMES.get(kind, f"type-{kind}")
    lines_out = [f"ESPN's power index for the {team.name}, {season} {name} snapshot (updated {str(updated)[:10]}, {chosen[2]} teams):"]
    if not postseason and kind == 3:
        lines_out.append("  (No pre-playoff snapshot for that season holds them, so this is the postseason one.)")
    if str(updated)[:4] > str(season):
        lines_out.append(f"  (ESPN stamps this snapshot {str(updated)[:10]}, after the {season} season ended, so it may not reflect any one moment of it.)")
    return lines_out


def _team_outlook_bpi_line(bpi: Any, offense: Any, defense: Any, higher: Any, teams: int) -> str | None:
    """team_outlook's BPI line, or a sentence saying the snapshot carries no
    rating.

    Never None, and that is the point: the power index IS this answer's
    headline, so dropping the line silently leaves a reader with the record and
    the chances and no sign that the number they asked for is missing - short
    of the truth with no caveat, which is what this file's P2 means. ESPN's
    2026 regular-season snapshot is the live case: all 30 of its teams carry a
    NULL ``bpi`` while their records, projections, chances and SOS are all
    populated, and it is the only one of the table's 21 (season, season_type)
    groups with any NULL rating (measured 2026-09-18). The other snapshots for
    that season DO have ratings, and ``_team_outlook_others_line`` already
    lists them, so saying the rating is absent here points the reader straight
    at the one that has it.

    .. versionchanged:: 4.0.1
       Says so when the snapshot carries no rating, rather than omitting the
       line.
    """
    if bpi is None:
        return f"  no BPI rating in this snapshot - ESPN left it empty for all {teams} teams, though the record and projections below are its own"
    detail = f" (offense {offense:+.1f}, defense {defense:+.1f})" if offense is not None and defense is not None else ""
    return f"  BPI {bpi:+.1f}{detail}, {_ordinal(int(higher) + 1)} of the {teams} teams in the snapshot"


def _team_outlook_chances_line(chances: dict[str, Any]) -> str | None:
    """team_outlook's playoff/title-chances line, or None where the snapshot
    carries none of them."""
    odds = [f"{label} {chances[column]:.1f}%" for label, column in _BPI_CHANCES if chances[column] is not None]
    return "  chances: " + ", ".join(odds) if odds else None


def _team_outlook_sos_line(sos: Any, sos_rank: Any) -> str | None:
    """team_outlook's strength-of-schedule line, or None where the snapshot
    carries none. ESPN's schedule-strength rank is a league-wide rank only
    from 2022; the values before it (7,909 to 59,238) are not ranks."""
    if sos is None or not (0 < sos < 1):
        return None
    rank_note = f", {_ordinal(int(sos_rank))} hardest in the league" if sos_rank is not None and 1 <= sos_rank <= 30 else ""
    return f"  strength of schedule {_record_pct(sos)}{rank_note}"


def _team_outlook_others_line(season: int, kind: int, snapshots: list[tuple[Any, ...]], listing: list[str]) -> str | None:
    """team_outlook's note about the season's other snapshots, or None where
    the chosen one is the only one."""
    others = [d for (k, *_), d in zip(snapshots, listing, strict=True) if k != kind]
    return f"  ESPN's power index for {season} also has {_joined(others)}." if others else None


def _team_outlook_data(
    team: Entity,
    season: int,
    name: str,
    chosen: tuple[Any, ...],
    updated: Any,
    bpi: Any,
    offense: Any,
    defense: Any,
    higher: Any,
    wins: Any,
    losses: Any,
    proj_w: Any,
    proj_l: Any,
    chances: dict[str, Any],
    sos: Any,
) -> dict[str, Any]:
    """team_outlook's structured data, alongside its prose answer."""
    return {
        "team": team.name,
        "season": season,
        "snapshot": name,
        "updated": str(updated)[:10],
        "teams_in_snapshot": chosen[2],
        "bpi": bpi,
        "bpi_offense": offense,
        "bpi_defense": defense,
        "position": int(higher) + 1,
        "wins": wins,
        "losses": losses,
        "projected_wins": proj_w,
        "projected_losses": proj_l,
        "chances": {label: chances[column] for label, column in _BPI_CHANCES},
        "strength_of_schedule": sos,
    }


def _team_outlook_detail(team: Entity, season: int, postseason: bool, chosen: tuple[Any, ...], kind: int, row: tuple[Any, ...], snapshots: list[tuple[Any, ...]], listing: list[str]) -> TemplateResult:
    """team_outlook's answer once a snapshot holds the team: the BPI line, the
    record and its projection, title chances, and strength of schedule."""
    updated, bpi, offense, defense, wins, losses, proj_w, proj_l, *chances_and_sos = row
    chances = dict(zip([c for _, c in _BPI_CHANCES], chances_and_sos[:4], strict=True))
    sos, sos_rank, higher = chances_and_sos[4], chances_and_sos[5], chances_and_sos[6]
    name = BPI_SNAPSHOT_NAMES.get(kind, f"type-{kind}")
    lines_out = _team_outlook_headline(team, season, postseason, chosen, kind, updated)
    bpi_line = _team_outlook_bpi_line(bpi, offense, defense, higher, chosen[2])
    if bpi_line is not None:
        lines_out.append(bpi_line)
    if wins is not None and losses is not None:
        lines_out.append(_team_outlook_record_line(kind, wins, losses, proj_w, proj_l))
    chances_line = _team_outlook_chances_line(chances)
    if chances_line is not None:
        lines_out.append(chances_line)
    sos_line = _team_outlook_sos_line(sos, sos_rank)
    if sos_line is not None:
        lines_out.append(sos_line)
    others_line = _team_outlook_others_line(season, kind, snapshots, listing)
    if others_line is not None:
        lines_out.append(others_line)
    data = _team_outlook_data(team, season, name, chosen, updated, bpi, offense, defense, higher, wins, losses, proj_w, proj_l, chances, sos)
    return TemplateResult(data=data, answer="\n".join(lines_out))
