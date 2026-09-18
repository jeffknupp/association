"""Where each warehouse table's data actually begins.

Every table here starts in a different year, and the gaps are ESPN's rather
than this project's - no pull fills them in, verified live against the game
summary, ``core/.../plays`` and the athlete gamelog, all of which return empty
for the years below. So the floors are permanent facts about the data, and a
question landing under one has no answer at all rather than an answer somebody
has not fetched yet.

Why this is a table and not a set of ``WHERE`` clauses: an out-of-range query
does not fail, it returns nothing, and "nothing" is then phrased as though the
filters were wrong ("No shots found for Michael Jordan with the given
filters") or, worse, as a real result. Measured on the current snapshot, a
1980 scoring leaderboard answered "Moses Malone led the league in points per
game, at 25.8. Next: Bill Cartwright (21.7)" - fluent, confident, and drawn
from a league of seven players.

The floors are also NOT arithmetic a model should do. They live here, next to
:func:`association.nba.season.current_season`, for the same reason relative-date
resolution does: a lookup against a fixed table is code, and putting it in a
prompt would spend tokens on every question to buy a 3B model's arithmetic.

.. versionadded:: 2.1.0

.. versionchanged:: 3.0.0
   Moved from ``association.coverage``.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The season type every table calls the regular season.
REGULAR_SEASON = 2

#: The season type every table calls the postseason.
POSTSEASON = 3


@dataclass(frozen=True)
class Floor:
    """The earliest season a table answers a particular kind of question, and
    why it starts there.

    ``unrepresentative`` separates the two reasons a season is out of reach,
    which need different sentences and would be a lie in each other's words.
    Below ``first_season`` the rows do not exist. Between ``first_season`` and
    ``first_ranking_season`` they exist and are correct for the player they
    name - it is only ranking them against a league that is not there which
    fails. Telling somebody there is "no data for 1980" when the warehouse
    holds Moses Malone's real 1980 line is exactly the false-cause answer this
    module was written to stop.

    .. versionadded:: 2.1.0
    """

    season: int
    reason: str
    coverage: Coverage
    unrepresentative: bool = False
    subject: str = ""

    def refusal(self, asked: int) -> str:
        """The sentence shown to a person who asked about ``asked``."""
        if self.unrepresentative:
            return f"A league-wide ranking for {asked} would not be one - {self.reason}. Rankings are answerable from {self.season} onward; {asked} numbers for a player you name are still available."
        return f"{self.subject or self.coverage.subject} only go back to {self.season} - {self.reason}. There is no data for {asked}, and no pull would add any."


@dataclass(frozen=True)
class Coverage:
    """One table's earliest usable season, and why it starts there.

    ``subject`` names the data the way a person would ("Shot charts"), because
    the refusal is shown to one; the table name means nothing to them.

    Four of the fields exist because a single floor cannot describe this
    warehouse:

    - ``postseason_first_season`` - ``games`` and ``team_box_stats`` hold full
      16-team brackets back to 1988 while their regular seasons before 1994 are
      one team's schedule. One number would either refuse real playoff data or
      admit a season that is 82 games out of 1,100.
    - ``first_ranking_season`` - ``player_season_stats`` answers a question
      about ONE named player from 1977, because it is fetched per player over a
      whole career. It cannot answer a question about the LEAGUE until the pool
      is the league.
    - ``partial`` and ``partial_note`` - a season that exists but holds only
      part of what it should is answerable, and has to say so. Part of the
      YEAR for most of them (2002's play-by-play is about half a season), and
      part of the LEAGUE for ``player_season_advanced_stats``, whose 2013-2018
      rates are summed from box scores ESPN serves zeroed for two franchises.
      One field, because the answer needs the same thing in both cases: a
      sentence naming what is not in it.
    - ``postseason_partial`` and ``postseason_partial_note`` - the same, for a
      postseason alone. Separate from ``partial`` because they are different
      claims about different halves of one year: ESPN's 2001 playoffs stop
      before the Finals while its 2001 REGULAR season is complete, so a single
      shared tuple would both caveat every 2001 regular-season answer and
      exempt that season from its own floor check.
    - ``phantom`` - a season whose rows are a full, healthy copy of a DIFFERENT
      season. ESPN answers ``season=1993`` and ``season=1994`` with the
      identical 1,185 events, so 1993 looks complete by every row count and is
      not the 1992-93 season at all. It is listed rather than merely excluded
      by the floor, so ``scripts/check_coverage.py`` can verify the duplication
      instead of reading a full season as a floor set too high.

    .. versionadded:: 2.1.0
    """

    subject: str
    first_season: int
    reason: str
    postseason_first_season: int | None = None
    postseason_subject: str = ""
    postseason_reason: str = ""
    first_ranking_season: int | None = None
    ranking_reason: str = ""
    partial: tuple[int, ...] = ()
    partial_note: str = ""
    postseason_partial: tuple[int, ...] = ()
    postseason_partial_note: str = ""
    phantom: tuple[int, ...] = ()

    def floor(self, season_type: int = REGULAR_SEASON, *, ranking: bool = False) -> Floor:
        """The earliest season this table answers, and why it starts there.

        .. versionadded:: 2.1.0
        """
        first, reason, subject = self.first_season, self.reason, ""
        if season_type == POSTSEASON and self.postseason_first_season is not None:
            # Its own words where it has them: refusing "the 1988 playoffs" with
            # the regular season's reason ("one team's 82 games") names the
            # wrong cause.
            first, reason, subject = self.postseason_first_season, self.postseason_reason or self.reason, self.postseason_subject
        if ranking and self.first_ranking_season is not None and self.first_ranking_season > first:
            return Floor(self.first_ranking_season, self.ranking_reason, self, unrepresentative=True)
        return Floor(first, reason, self, subject=subject)


# Ten games of the 2001 playoffs are not in ESPN's archive anywhere - probed
# live, 23 days across that postseason's conference finals and Final return no
# events at all - so no pull adds them and every table built from those game
# summaries is short by the same games. Each affected table declares it, and
# the sentences differ because a team's series and a player's own numbers are
# shortened in different words: a reader told "a series can look shorter than
# it was" about a single-game high is being sent to look in the wrong place.
_MISSING_2001_PLAYOFF_GAMES = "ESPN is missing ten games of the 2001 playoffs - Games 1-4 of the LAL-PHI Final, three games of the MIL-PHI conference final, two of MIL-CHA and one of LAL-SA"

_TEAM_2001_POSTSEASON_NOTE = f"{_MISSING_2001_PLAYOFF_GAMES} - so Philadelphia's run reads 16 games against the 23 ESPN's own season totals give it, and a series can look shorter than it was"

_PLAYER_2001_POSTSEASON_NOTE = (
    f"{_MISSING_2001_PLAYOFF_GAMES} - so a player on one of those five teams is missing games too, and a total, a count or a single-game high can be low: "
    "40 players have fewer playoff box scores than ESPN's own season line gives them, 152 games in all"
)


# Sourced from the coverage table in AGENTS.md and re-measured against the
# built warehouse - `python scripts/check_coverage.py` asserts every floor
# below against real row counts, because these are claims about the data and
# not about the code.
#
# Only tables a template can read are listed. A table nothing queries needs no
# floor, and inventing one would be an unverified claim.
COVERAGE: dict[str, Coverage] = {
    "standings": Coverage(
        subject="Standings",
        first_season=1988,
        reason="ESPN's standings endpoint reaches 1988 and no further, though what it does have is real and league-wide (23 teams in 1988, 27 by 1990)",
    ),
    # 37 distinct regular seasons but 39 postseasons: 1989 and 1990 hold no
    # regular-season games at all, and 1988, 1991 and 1992 hold exactly 82 -
    # one team's schedule, not a league's.
    "games": Coverage(
        subject="Regular-season games",
        first_season=1994,
        reason=(
            "ESPN has one team's 82 games per season before that and nothing at all for 1989-90, and it answers season=1993 with the identical 1,185 events "
            "it returns for 1994, so 1993 is a duplicate label rather than the 1992-93 season"
        ),
        # The postseason is selected by the calendar year it was played in
        # (templates._season_games), which is why its floor reads 1989 where the
        # first stored label is 1988.
        postseason_first_season=1989,
        postseason_subject="Playoff games",
        # 2000's gap was recoverable and has been recovered: its nine missing
        # games are on the scoreboard endpoint even though no team's schedule
        # lists them. 2001's is not - the scoreboard has exactly one of its
        # missing games, and 23 days across the conference finals and the Final
        # return nothing at all - so that season says what it is missing.
        postseason_partial=(2001,),
        postseason_partial_note=_TEAM_2001_POSTSEASON_NOTE,
        postseason_reason=(
            "ESPN files every season before 1993-94 under the year it STARTED, so the postseason it labels 1988 is the 1989 playoffs, and the 1987-88 playoffs are not in its archive at all"
        ),
        phantom=(1993,),
    ),
    "team_box_stats": Coverage(
        subject="Team box scores",
        first_season=1994,
        reason="they come from the same game summaries as `games`, which hold one team's schedule per season before 1994 and nothing for 1989-90",
        postseason_first_season=1989,
        postseason_subject="Playoff team box scores",
        # 2000's gap was recoverable and has been recovered: its nine missing
        # games are on the scoreboard endpoint even though no team's schedule
        # lists them. 2001's is not - the scoreboard has exactly one of its
        # missing games, and 23 days across the conference finals and the Final
        # return nothing at all - so that season says what it is missing.
        postseason_partial=(2001,),
        postseason_partial_note=_TEAM_2001_POSTSEASON_NOTE,
        postseason_reason=(
            "ESPN files every season before 1993-94 under the year it STARTED, so the postseason it labels 1988 is the 1989 playoffs, and the 1987-88 playoffs are not in its archive at all"
        ),
        phantom=(1993,),
    ),
    "player_box_stats": Coverage(
        subject="Player box scores",
        first_season=1994,
        reason="ESPN returns no box scores before 1993-94, and answers season=1993 with that same season's games",
        # The ten missing 2001 playoff games take their box scores with them, so
        # a player's postseason is short exactly where his team's is. Declared
        # here as well as on `games` because the caveat follows the TABLE a
        # template reads: single_game_high and threshold_count never touch
        # `games`, and without this a short count is stated as fact.
        postseason_partial=(2001,),
        postseason_partial_note=_PLAYER_2001_POSTSEASON_NOTE,
        phantom=(1993,),
    ),
    "player_game_log": Coverage(
        subject="Player game logs",
        first_season=1994,
        reason="they are built from box scores, which ESPN does not have before 1993-94",
        postseason_partial=(2001,),
        postseason_partial_note=_PLAYER_2001_POSTSEASON_NOTE,
        phantom=(1993,),
    ),
    "player_advanced_stats": Coverage(
        subject="Advanced player game stats",
        first_season=1994,
        reason="they are derived from box scores, which ESPN does not have before 1993-94",
        phantom=(1993,),
    ),
    "player_season_advanced_stats": Coverage(
        subject="Advanced season stats",
        first_season=1994,
        reason="they are derived from box scores, which ESPN does not have before 1993-94",
        # Summed from the STORED box scores, never the rebuilt ones: a rebuilt
        # season total is exact only about half the time and its error is
        # biased low (fetch/repairs/reconstructed_box.py), and no attempt column
        # was ever measured - the rebuild's figures are in UNGATED_ON_REBUILD
        # for exactly that reason. So the empty 2013-2018 games cost this table
        # the attempts they held, and the honest move is to say so rather than
        # to rank players on a derived denominator.
        partial=(2013, 2014, 2015, 2016, 2017, 2018),
        partial_note=(
            "ESPN serves every Chicago and New Orleans game from 2013 to 2018 with each player's line zeroed, and these rates are summed from those box scores, so the attempts "
            "are short for anyone who played in one - not just those two rosters. 21 to 33 players a season clear the qualifying floor by ESPN's own season totals and fall "
            "under it here, so they are missing from this ranking entirely"
        ),
        # Summed out of the same short box scores, so a 2001 postseason rate is
        # computed over fewer games than the player played.
        postseason_partial=(2001,),
        postseason_partial_note=_PLAYER_2001_POSTSEASON_NOTE,
        phantom=(1993,),
    ),
    "team_season_stats": Coverage(
        subject="Team season stats",
        first_season=1994,
        reason="ESPN does not have them before 1993-94",
    ),
    # The one table that looks like an exception and is not. Its 1980 "league"
    # is seven players: Moses Malone, Bill Cartwright, Magic Johnson, John
    # Long, James Edwards, Robert Parish and Tree Rollins. Kareem
    # Abdul-Jabbar, Larry Bird and Julius Erving are not in `players` at all.
    "player_season_stats": Coverage(
        subject="Season stats for a named player",
        first_season=1977,
        reason="ESPN's per-player career endpoint reaches no further",
        first_ranking_season=1994,
        ranking_reason=(
            "the table is fetched per player over a whole career, and players are discovered from box scores that start in 1994, so an earlier season holds only "
            "the players whose careers lasted into 1993-94 - 7 of them in 1980, 141 in 1988, 217 in 1990, against 403 in 1994. Ranking that pool measures who "
            "played longest, not who led the league"
        ),
    ),
    "player_season_stats_deduped": Coverage(
        subject="Season stats for a named player",
        first_season=1977,
        reason="ESPN's per-player career endpoint reaches no further",
        first_ranking_season=1994,
        ranking_reason=(
            "the underlying table is fetched per player over a whole career, and players are discovered from box scores that start in 1994, so an earlier season "
            "holds only the players whose careers lasted into 1993-94 - 7 of them in 1980, 141 in 1988, against 403 in 1994. Ranking that pool measures who "
            "played longest, not who led the league"
        ),
    ),
    "plays": Coverage(
        subject="Play-by-play records",
        first_season=2002,
        reason="ESPN's play-by-play endpoint returns empty for every earlier season",
        partial=(2002,),
        partial_note="2002 is about half a season of play-by-play (244,717 plays against 469,429 in 2003), so it covers part of the year rather than all of it",
    ),
    # Derived from `plays`, out of the same game summary, so there is no
    # separate shot source to fetch for 2002 and earlier.
    "shot_chart": Coverage(
        subject="Shot charts",
        first_season=2002,
        reason="shots are derived from play-by-play, which ESPN does not have before 2002",
        # 2003's play-by-play is complete (1,189 of 1,190 games) but its shots
        # are not: about 200 games' plays carry no located shot.
        partial=(2002, 2003),
        partial_note=("ESPN's shot data covers only part of that season's games - 509 of 2002's 1,190 and 986 of 2003's - so the answer covers part of the year rather than all of it"),
    ),
    "team_power_index": Coverage(
        subject="Team power index ratings",
        first_season=2017,
        reason="ESPN publishes none before 2017",
    ),
    "win_probability": Coverage(
        subject="Win probability records",
        first_season=2018,
        reason="ESPN publishes none before 2018",
    ),
    "net_points_player": Coverage(
        subject="NetPoints",
        first_season=2019,
        reason="the NetPoints bucket answers 403 for every earlier season",
    ),
    "net_points_player_game": Coverage(
        subject="Per-game NetPoints",
        first_season=2019,
        reason="the NetPoints bucket answers 403 for every earlier season",
    ),
    "net_points_player_fingerprint": Coverage(
        subject="NetPoints fingerprints",
        first_season=2019,
        reason="the NetPoints bucket answers 403 for every earlier season",
    ),
    "net_points_team_game": Coverage(
        subject="Per-game team NetPoints",
        first_season=2019,
        reason="the NetPoints bucket answers 403 for every earlier season",
    ),
    "net_points_player_game_fingerprint": Coverage(
        subject="Per-game NetPoints fingerprints",
        first_season=2019,
        reason="the NetPoints bucket answers 403 for every earlier season",
    ),
}


def unavailable(tables: tuple[str, ...], season: int, season_type: int = REGULAR_SEASON, *, ranking: bool = False) -> str | None:
    """Why ``season`` is out of reach for every one of ``tables``, or None.

    A question is answerable only as far back as its NARROWEST table, so the
    latest floor among them is the one that decides - and it is the one named,
    since telling somebody that standings go back to 1988 does not explain why
    their 1996 shot chart is empty.

    Args:
        tables: The warehouse tables the answer would be built from. A table
            with no declared floor is skipped rather than assumed.
        season: The season-ending year asked about.
        season_type: 2 for the regular season, 3 for the postseason.
        ranking: True when the answer ranks players against each other rather
            than reporting one named player's own numbers. A survivor sample
            answers the second honestly and the first not at all.

    Returns:
        A sentence written to be shown to a person, or None when the season is
        covered.

    .. versionadded:: 2.1.0
    """
    floors = [COVERAGE[t].floor(season_type, ranking=ranking) for t in tables if t in COVERAGE]
    if not floors:
        return None
    narrowest = max(floors, key=lambda floor: floor.season)
    return None if season >= narrowest.season else narrowest.refusal(season)


def caveat(tables: tuple[str, ...], season: int, season_type: int = REGULAR_SEASON) -> str | None:
    """A note for a season that is covered but only partly, or None.

    Not a refusal: half a season is a real answer, and saying which half it is
    beats both silence and a refusal.

    ``season_type`` picks between the two claims a table can make about one
    year. A postseason that stops early is caveated on postseason questions
    only: ESPN's 2001 playoffs are missing the Finals while its 2001 regular
    season is complete, and a note about the wrong half of the year is the
    false-cause answer this module exists to stop.

    .. versionadded:: 2.1.0

    .. versionchanged:: 2.2.0
       Takes ``season_type`` and reads ``postseason_partial`` for a postseason
       question. A caller that omits it gets the regular season, as before.
    """
    notes = []
    for table in tables:
        coverage = COVERAGE.get(table)
        if coverage is None:
            continue
        if season_type == POSTSEASON:
            if season in coverage.postseason_partial:
                notes.append(coverage.postseason_partial_note)
        elif season in coverage.partial:
            notes.append(coverage.partial_note)
    return f"Note: {notes[0]}." if notes else None
