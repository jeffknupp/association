"""The ``team_game`` relation: one row per team per played game, read one way.

"A team's games" used to be defined three times, and they disagreed. The
correct one is the one this module adopts, built once as ``team_games`` (see
:data:`TEAM_GAMES_SQL`, moved here from :mod:`association.query.team_metrics`,
which re-exports it): from ``real_games`` alone, home and away sides unioned
into a team-perspective row, with two things every reader gets for free
because they live in the relation instead of in each caller:

- **the phantom 1993 season cannot double a game.** ``team_games``' own
  ``played`` step keeps one row per ``(season_type, home_team_id,
  away_team_id, eastern_date)`` - the highest ``season`` label when two agree
  - so a caller never needs its own ``season NOT IN (1993, ...)`` guard the
  way ``association.query.templates.games._season_games`` used to write one
  per call site (removed, step 3, C4b: its one caller, ``team_quarter_points``,
  now takes its games from this relation);
- **a postseason is selected by the calendar year it was played in**, from
  the relation's own :data:`Eastern date <association.nba.season.eastern_date_sql>`
  rather than a raw UTC timestamp - never by ESPN's pre-1993-94 label, which
  files a season under the year it STARTED (`AGENTS.md`, "Select a postseason
  by the calendar year"; :data:`association.query.team_metrics.games_scope`
  already read the relation this way, and now every reader can).

What this module does NOT decide is whether the NBA Cup final counts.
``team_games`` FLAGS it (``cup_final``) rather than dropping it, because a
regular-season RECORD must skip it while a plain game listing or a
head-to-head count should not - the cup final is a real game the two teams
played. :func:`association.query.team_metrics.games_scope` applies the
exclusion for a record; :func:`association.query.templates.common.team_games`
(the shared narrowing step) does not, since a team's plain game list or a
head-to-head count is not a record.

:class:`TeamNarrowed` mirrors :class:`association.query.player_games.Narrowed`:
base clauses (team, season type, span) kept apart from the rest so an empty
answer can say which narrowing emptied it, and the same four readers
(:func:`rows_sql`, :func:`aggregate_sql`, :func:`games_subquery`,
:func:`named`) over the one relation. Entity binding is not here, the same
rule the player relation follows: this module takes ids the entity layer has
already resolved, never a name.

The old ``conditions._team_games``, which scoped by season LABEL rather than
calendar year (the wrong-year fault this module's docstring above describes),
is gone: ``player_splits``, ``record_when`` and ``streak``'s team branches and
``with_without``'s windows were ported onto this relation in step 3, C4, and
``head_to_head`` and ``game_log``'s team half in the same step. Every reader of
a team's games now goes through here.

Step 3, C4b adds the cells a team's games can be narrowed to that C4 left off:
:attr:`TeamNarrowed.window` (the newest or oldest N of the narrowed games, the
team counterpart of :attr:`association.query.player_games.Narrowed.window`)
and :attr:`TeamNarrowed.series_game` (one game of each playoff series, the
team counterpart of :attr:`association.query.player_games.Narrowed.series_game`).
A team's ``since`` (a career that starts partway through) needed no new cell
here at all: :func:`association.query.templates.common._span_of` already
reads it into the ``_Span`` a caller passes as ``team_games``'s own ``span``,
so the relation's ``team_games`` CTE and :func:`association.query.templates.common._team_span_clause`
narrow by it the same way they already narrow a career.

Step 3, K1 brings the player relation's remaining two cells over: :meth:`TeamNarrowed.narrow_calendar`
(a ``situation`` - a weekday, a month, a fixed holiday, "since <month day>" - the same
:func:`association.query.calendar.calendar_clause` the player relation's own
:meth:`association.query.player_games.Narrowed.narrow_calendar` reads, over the relation's
own already-Eastern ``eastern_date`` column) and ``until`` (the inclusive last season of a
``since``-bounded span, read the same way ``since`` already is - see
:func:`association.query.templates.common._span_of`).

.. versionadded:: 4.4.0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from association.nba.season import eastern_date_sql

from .entities import Entity

# TYPE_CHECKING avoided a real circular import at module load: until
# 2026-10-03 `calendar.py` imported `_MONTH_NAMES` from `conditions.py`,
# which imports `TeamNarrowed` from this module, so a top-level
# `from .calendar import ...` here cycled back on itself (the months are
# `season_text.MONTH_NAMES` now). `narrow_calendar` below still imports the
# names it calls at call time.
if TYPE_CHECKING:
    from .calendar import AlignmentNarrowing, CalendarNarrowing

# One row per team per game, from that team's side, over every game that was
# actually played - built from `real_games` alone, because it is the only
# table holding who won and it needs no second table to say which side a team
# was on.
#
# `real_games` (fetch/repairs/real_games.py) is the shared filtered list, and
# it is where the 0-0 placeholders, the rows naming a team id no franchise
# has, and the same-day duplicates now go. This used to do two of those three
# for itself: it dropped rows with no winner and same-day duplicates, and kept
# every phantom that carried a winner - which is how the 1995 Finals game
# filed as MIA-ORL gave Orlando an 11th playoff loss and Miami a postseason it
# never played. Building it here meant `conditions` and `head_to_head` each
# had to reach the same conclusion separately, and neither did.
#
# What is left here is the ONE thing `real_games` deliberately does not do:
# collapse season 1993, which is a full copy of 1994 under a second label
# (see coverage.COVERAGE). That is a phantom SEASON rather than a phantom row,
# its two copies differ only in the `season` column, and a postseason is
# selected by the year it was PLAYED, so nothing else would drop the second
# copy. The 1994 label is the one kept - and every reader that groups by
# `(season_type, home, away, eastern_date)` gets this dedup for free, without
# writing its own `season NOT IN (...)` guard.
#
# The NBA Cup final is FLAGGED rather than dropped: it is a
#   season_type 2 game that counts in no standings and no team season totals
#   (the 2026 Knicks' 82 games and 9,549 points in both leave out their 124 in
#   the final), so a regular-season record must skip it, but a record against
#   the team they beat in it should still mention it. It is identified as the
#   last neutral-site regular-season game in Las Vegas each season, which
#   picks out exactly the two teams per season (2024-2026) that `games` holds
#   83 regular-season games for and standings 82.
#
# A game's date is its US Eastern date - games.date is a UTC timestamp, read
# through season.eastern_date_sql, which follows daylight time.
_TEAM_GAMES = f"""
WITH cup_finals AS (
    SELECT arg_max(event_id, date) AS event_id
    FROM real_games
    WHERE season_type = 2 AND neutral_site AND venue_city = 'Las Vegas'
    GROUP BY season
),
listed AS (
    SELECT g.event_id, g.season, g.season_type, g.home_team_id, g.away_team_id, g.home_score, g.away_score, g.winner_team_id,
           coalesce(g.neutral_site, false) AS neutral,
           {eastern_date_sql("g.date")} AS eastern_date,
           g.event_id IN (SELECT event_id FROM cup_finals) AS cup_final
    FROM real_games g
),
played AS (
    SELECT * FROM listed
    QUALIFY row_number() OVER (PARTITION BY season_type, home_team_id, away_team_id, eastern_date ORDER BY season DESC, event_id) = 1
),
team_games AS (
    SELECT season, season_type, eastern_date, event_id, cup_final, neutral, home_team_id AS team_id, away_team_id AS opponent_id, 'home' AS side,
           home_score AS team_score, away_score AS opponent_score, winner_team_id = home_team_id AS won
    FROM played
    UNION ALL
    SELECT season, season_type, eastern_date, event_id, cup_final, neutral, away_team_id AS team_id, home_team_id AS opponent_id, 'away' AS side,
           away_score AS team_score, home_score AS opponent_score, winner_team_id = away_team_id AS won
    FROM played
)
"""

TEAM_GAMES_SQL = _TEAM_GAMES
"""A ``WITH`` clause defining ``team_games``: one row per team per played
game, with ``season``, ``season_type``, ``eastern_date``, ``event_id``,
``cup_final``, ``neutral``, ``team_id``, ``opponent_id``, ``side`` (home/away),
``team_score``, ``opponent_score`` and ``won``.

.. versionadded:: 2.1.0
.. versionchanged:: 4.4.0
   Moved here from :mod:`association.query.team_metrics`, which imports it
   back under the same name - the relation's own readers
   (:func:`rows_sql`, :func:`aggregate_sql`, :func:`games_subquery`) read it as
   ``_TEAM_GAMES``, and every other caller keeps reading it under this name.
"""


@dataclass
class TeamNarrowed:
    """One team's games in a span, and whatever the question narrowed them
    by. Every clause applies to :data:`TEAM_GAMES_SQL`'s ``team_games``;
    the base ones (team, season type, span) are kept apart from the rest so an
    empty answer can say which narrowing emptied it - the team counterpart of
    :class:`association.query.player_games.Narrowed`.

    .. versionadded:: 4.4.0
    """

    base: list[str]
    base_params: list[Any]
    extra: list[str] = field(default_factory=list)
    extra_params: list[Any] = field(default_factory=list)
    team: Entity | None = None
    opponent: Entity | None = None
    venue: str | None = None
    #: The one Eastern date the games were narrowed to, as the answer says it.
    date: str | None = None
    #: The game of a playoff series the question named ("game 4"), or None -
    #: the team counterpart of :attr:`association.query.player_games.Narrowed.series_game`.
    series_game: int | None = None
    #: The window: the newest (``"recent"``) or oldest (``"first"``) N of the
    #: narrowed games, or None for all of them - cut AFTER every row filter,
    #: the team counterpart of :attr:`association.query.player_games.Narrowed.window`.
    window: tuple[str, int] | None = None
    #: The calendar narrowing a ``situation`` slot named - a weekday, a month,
    #: a fixed day, every game from a day of the season on - or None. The team
    #: counterpart of :attr:`association.query.player_games.Narrowed.calendar`.
    calendar: CalendarNarrowing | None = None
    #: The conference or division a ``situation`` slot named the OPPONENT to
    #: be in - or None. The team counterpart of
    #: :attr:`association.query.player_games.Narrowed.alignment`.
    alignment: AlignmentNarrowing | None = None
    #: The periods a quarter or a half narrowed each game to - ``(1,)``,
    #: ``(3, 4)`` - or None for the whole game. Set by :meth:`narrow_periods`;
    #: every read then sees the period's own figures (:func:`_team_period_source`).
    periods: tuple[int, ...] | None = None
    #: How the answer names those periods: ``"1st quarter"``, ``"2nd half"``.
    period_label: str | None = None
    #: Whether the warehouse holds the shots and the player rows a period's
    #: line is rebuilt from (``shot_chart``, ``player_box_stats``); without
    #: them only the linescore's points are known.
    period_shots: bool = True
    #: Whether the warehouse holds ``plays``, which every rebuilt column but
    #: the shot ones is read from.
    period_plays: bool = True

    def clauses(self, *, narrowed: bool = True) -> tuple[str, list[Any]]:
        """The WHERE body and its parameters - without the narrowing when
        ``narrowed`` is false, so a caller can ask how many games the base
        span alone holds, before an opponent, a venue or a date is applied."""
        where = list(self.base)
        params = list(self.base_params)
        if narrowed:
            where += self.extra
            params += self.extra_params
        return " AND ".join(where), params

    def filters(self, *, opponent: bool = True, date: bool = True, period: bool = True) -> str:
        """What the games were narrowed to, as it follows a name: ``" vs the
        Detroit Pistons at home"``. ``opponent`` is False for a caller that
        already names the opponent its own way (``team_quarter_points`` says
        "against the Pistons", not "vs the Pistons") and wants the rest of the
        narrowing without a second, differently-worded mention of it. ``date``
        is False the same way, for a caller whose sentence already names the
        one game's date its own way (a single-game answer that already prints
        ``g['date']``) and would otherwise say it twice, and ``period`` for a
        caller that names the quarter or half itself (``team_quarter_points``).

        .. versionchanged:: 5.0.0
           Says the period (``"in the 1st quarter"``) a narrowing set, and
           takes ``period``.
        """
        parts = [f"in the {self.period_label}"] if self.period_label and period else []
        if self.opponent is not None and opponent:
            parts.append(f"vs the {self.opponent.name}")
        if self.venue:
            parts.append("at home" if self.venue == "home" else "on the road")
        if self.series_game is not None:
            # "of the series" only where one series is in view: an opponent
            # names it. Across a postseason it is game 4 of each series.
            parts.append(f"in game {self.series_game} of {'the' if self.opponent is not None else 'each'} series")
        if self.date and date:
            parts.append(f"on {self.date}")
        if self.calendar is not None:
            parts.append(self.calendar.label)
        if self.alignment is not None:
            parts.append(self.alignment.label)
        if self.window is not None:
            order, n = self.window
            parts.append(f"over their {'last' if order == 'recent' else 'first'} {n} game{'s' if n != 1 else ''}")
        return "".join(f" {part}" for part in parts)

    def narrow(self, clause: str, *params: Any) -> None:
        """One more clause over the same rows - how every narrowing composes."""
        self.extra.append(clause)
        self.extra_params.extend(params)

    def narrow_series_game(self, n: int) -> None:
        """Only the ``n``th game of each playoff series: the games between the
        same two teams in one postseason, numbered by date over ``real_games``
        - the team counterpart of :meth:`association.query.player_games.Narrowed.narrow_series_game`.

        .. versionadded:: 4.4.0
        """
        self.narrow(f"tg.event_id IN (SELECT event_id FROM ({_TEAM_SERIES_GAMES}) WHERE game_of_series = ?)", n)
        self.series_game = n

    def narrow_calendar(self, narrowing: CalendarNarrowing) -> None:
        """Only the games on a weekday, in a month, on a fixed day, or from a
        day of the season on - the team counterpart of
        :meth:`association.query.player_games.Narrowed.narrow_calendar`.

        Unlike the player relation, :data:`TEAM_GAMES_SQL`'s own
        ``eastern_date`` column is already the Eastern calendar DATE - the
        relation's ``listed`` CTE converts ``real_games.date`` once, via
        :func:`association.nba.season.eastern_date_sql` - so this clause reads
        it directly rather than converting a raw UTC timestamp a second time,
        the way :func:`association.query.calendar.calendar_clause`'s
        ``eastern_date`` argument is written for the player relation's own raw
        ``g.date``.

        .. versionadded:: 4.4.0
        """
        from .calendar import calendar_clause  # local import breaks a module-load cycle; see the TYPE_CHECKING import above

        clause, params = calendar_clause(narrowing, "tg.eastern_date", "tg.season")
        self.narrow(clause, *params)
        self.calendar = narrowing

    def narrow_alignment(self, narrowing: AlignmentNarrowing) -> None:
        """Only the games against an opponent in this conference or division,
        for that game's own season - the team counterpart of
        :meth:`association.query.player_games.Narrowed.narrow_alignment`,
        over ``team_games``'s own ``opponent_id``/``season`` columns rather
        than a raw join to ``games``.

        .. versionadded:: 4.4.0
        """
        from .calendar import alignment_clause  # local import breaks a module-load cycle; see the TYPE_CHECKING import above

        clause, params = alignment_clause(narrowing, "tg.opponent_id", "tg.season")
        self.narrow(clause, *params)
        self.alignment = narrowing

    def narrow_periods(self, periods: tuple[int, ...], label: str, *, shots: bool = True, plays: bool = True) -> None:
        """Only ``periods`` of each game - a quarter, a half, an overtime -
        the team counterpart of
        :meth:`association.query.player_games.Narrowed.narrow_periods`.

        Every read then sees the period's figures (:func:`_team_period_source`):
        ``team_score``/``opponent_score`` and ``points`` from ESPN's own
        linescore, and every other :data:`TEAM_PERIOD_COLUMNS` column from the
        line :func:`team_period_line_sql` rebuilds. The row filters still read
        the whole game, and ``won`` stays the game's result.

        .. versionadded:: 5.0.0
        """
        _team_period_in(periods)  # validates the periods before anything is read
        self.periods = tuple(periods)
        self.period_label = label
        self.period_shots = shots
        self.period_plays = plays


# Every postseason game numbered within its series, over the two teams that
# played it - the team relation's own copy of
# :data:`association.query.player_games._SERIES_GAMES`. Kept separate rather
# than imported, the same way :func:`named` is its own copy: the two relations
# share no base class, on purpose, so a change to one's clause-composition
# rules cannot silently reach the other.
_TEAM_SERIES_GAMES = (
    "SELECT s.event_id, ROW_NUMBER() OVER (PARTITION BY s.season, LEAST(s.home_team_id, s.away_team_id), GREATEST(s.home_team_id, s.away_team_id) ORDER BY s.date, s.event_id) AS game_of_series "
    "FROM real_games s WHERE s.season_type = 3"
)


# A team's period line (ROADMAP plan item 4, the team half; ISSUES.md #161).
# Nothing ESPN serves splits a team's box score by period except the
# linescore, which holds points alone, so the rest is rebuilt: the sum of the
# team's players' period lines (`player_games.period_line_sql`, each player
# placed on his team by his own `player_box_stats` row) PLUS the team's own
# plays that name no player. The two relations share no base class
# (see `named`), and this does not add one: it shares the READ of the plays -
# the same rules for what a turnover or a foul is, measured once against the
# player box scores - not the rules for composing clauses, which stay each
# relation's own.
#
# Which box column each rebuilt column reproduces, decided by measurement
# (2026-09-29, every 2002-2026 team-game the shot table covers, summed over
# every period against `team_box_stats`):
#
# - rebounds are the players' own, the split columns
#   (`offensiveRebounds + defensiveRebounds`). The plays credit ~6 rebounds a
#   game to the team itself (`Offensive Rebound`/`Defensive Rebound` with no
#   athlete); added in, offensive rebounds agree with the box in 0.1-3.4% of
#   team-games instead of 98-99%, and the total agrees with `totalRebounds`
#   (which carried team rebounds until 2022, DATA.md) in 9-21% in 2007-2012 -
#   the plays' team-rebound count is not ESPN's. The split is also what every
#   other team reader means by rebounds (`team_metrics`, `conditions`).
# - turnovers are the team's whole count, `totalTurnovers` (`turnovers +
#   teamTurnovers` in 2018, whose `totalTurnovers` is NULL - DATA.md): the
#   players' plus the team's own `Shot Clock Turnover`, `8-Second Turnover`
#   and the rest, by the same rule a player's are counted. It is what a
#   team's turnovers means everywhere else here (`record_when`'s team branch).
# - fouls are the players' (a team's own `Technical Foul` is not a personal
#   foul, by the same rule a player's is not).
# - field goals and free throws include the few shots ESPN charted with no
#   shooter (313 in 2007): with them 2007 field goals made agree in 99.5% of
#   team-games, without them 98.4%.
# - points are the linescore's, never the shots': it is ESPN's own official
#   per-period score, where the shots' sum agrees with it in 76.5-99% of
#   team-quarters by season (`templates.games.PERIOD_RECONCILIATION`).
TEAM_PERIOD_COLUMNS: tuple[str, ...] = (
    "points",
    "fieldGoalsMade",
    "fieldGoalsAttempted",
    "threePointFieldGoalsMade",
    "threePointFieldGoalsAttempted",
    "freeThrowsMade",
    "freeThrowsAttempted",
    "rebounds",
    "offensiveRebounds",
    "defensiveRebounds",
    "assists",
    "steals",
    "blocks",
    "turnovers",
    "fouls",
)
"""The columns a period-narrowed team read carries, under the player line's
names: ``points`` from the linescore, the rest rebuilt by
:func:`team_period_line_sql`.

.. versionadded:: 5.0.0
"""

TEAM_PERIOD_BOX: dict[str, str] = {
    "points": "the final score",
    "rebounds": "b.offensiveRebounds + b.defensiveRebounds",
    "turnovers": "COALESCE(b.totalTurnovers, b.turnovers + COALESCE(l.teamTurnovers, 0))",
}
"""For the :data:`TEAM_PERIOD_COLUMNS` a team box column does not reproduce by
its own name, what the rebuilt figure is checked against - an expression over
``team_box_stats`` aliased ``b`` (and, for ``points``, the game's score) - the
definitions the comment above :data:`TEAM_PERIOD_COLUMNS` measured. Every
other column is checked against the box column of its own name.

2018 has no ``totalTurnovers`` and a ``teamTurnovers`` that is not that
game's (DATA.md, "2018's `teamTurnovers` is not the game's own"), so a 2018
team-game's turnovers are checked on the players' share alone, with the
team's own turnovers (``l.teamTurnovers``, the line's) taken as read - they
match ESPN's own count in 99.9-100% of team-games in 2016, 2017 and 2019.

.. versionadded:: 5.0.0
"""

# Measured 2026-09-29 (`scripts/check_team_period_lines.py`) over 60,422
# team-games 2002-2026, both season types, in games the shot table covers
# and with a team box score: each team-game's line summed over EVERY period,
# overtime included, against `team_box_stats`, and the linescore's periods
# summed against the final score. A team-level check is stricter than the
# player one - any one player's missed play breaks the team's game - so a
# season 99% right per player is ~90% right per team. Only the cells under
# 99% are listed; 70 cells are under 90% and refused, 60 of them in
# 2002-2006. The causes are ESPN's, and DATA.md ("A team's play-by-play does
# not add up to its box score in six seasons") has them: 2002 is half a
# season of play-by-play with no shot values; before 2006 a play's second
# participant (the assister, the stealer, the blocker) is mostly absent;
# 2003-2006 attempts are miscounted in the plays; 2013 and 2016 play-by-play
# lacks made shots its own running score counts; 2007-2008 plays are short of
# turnovers, and 2018's of personal fouls.
TEAM_PERIOD_AGREEMENT: dict[str, dict[int, float]] = {
    "points": {},
    "fieldGoalsMade": {2002: 81.7, 2003: 82.2, 2004: 89.9, 2005: 90.8, 2006: 89.5, 2013: 83.1, 2014: 98.6, 2015: 97.7, 2016: 56.7, 2017: 98.9},
    "fieldGoalsAttempted": {
        2002: 80.3,
        2003: 70.1,
        2004: 76.8,
        2005: 73.7,
        2006: 75.5,
        2007: 97.7,
        2008: 97.5,
        2010: 98.4,
        2011: 98.7,
        2012: 98.6,
        2013: 77.3,
        2014: 98.2,
        2015: 97.6,
        2016: 55.6,
        2017: 93.3,
        2018: 94.8,
        2019: 95.9,
        2020: 96.9,
        2021: 98.3,
        2022: 97.2,
        2023: 98.6,
        2024: 97.9,
        2025: 98.6,
        2026: 97.7,
    },
    "threePointFieldGoalsMade": {2002: 88.1, 2003: 93.0, 2004: 98.6, 2005: 97.9, 2006: 98.3, 2013: 96.0, 2016: 98.6, 2022: 97.7},
    "threePointFieldGoalsAttempted": {
        2002: 83.9,
        2003: 88.3,
        2004: 95.6,
        2005: 94.3,
        2006: 94.5,
        2007: 98.4,
        2008: 98.4,
        2013: 93.4,
        2016: 94.5,
        2017: 96.7,
        2018: 98.7,
        2019: 98.9,
        2022: 93.0,
        2023: 98.7,
        2024: 98.7,
        2025: 98.8,
        2026: 98.4,
    },
    "freeThrowsMade": {2002: 86.2, 2003: 89.1, 2004: 94.9, 2005: 96.1, 2006: 95.1, 2013: 94.4},
    "freeThrowsAttempted": {2002: 81.0, 2003: 80.8, 2004: 87.5, 2005: 71.2, 2006: 68.2, 2013: 92.9},
    "rebounds": {
        2002: 82.8,
        2003: 77.0,
        2004: 82.6,
        2005: 82.1,
        2006: 82.3,
        2007: 97.5,
        2008: 97.8,
        2009: 98.9,
        2010: 97.6,
        2011: 98.5,
        2012: 98.3,
        2013: 81.2,
        2014: 98.9,
        2015: 98.5,
        2016: 98.4,
        2017: 91.3,
        2018: 93.5,
        2019: 93.8,
        2020: 95.9,
        2021: 97.6,
        2022: 96.2,
        2023: 98.2,
        2024: 97.6,
        2025: 98.0,
        2026: 97.2,
    },
    "offensiveRebounds": {
        2002: 57.1,
        2003: 84.1,
        2004: 88.4,
        2005: 86.1,
        2006: 87.4,
        2007: 98.5,
        2008: 98.7,
        2010: 98.7,
        2012: 98.9,
        2013: 90.7,
        2016: 99.0,
        2017: 94.7,
        2018: 96.7,
        2019: 96.3,
        2020: 97.2,
        2021: 98.5,
        2022: 97.9,
        2023: 98.9,
        2024: 98.6,
        2025: 98.6,
        2026: 98.2,
    },
    "defensiveRebounds": {
        2002: 56.0,
        2003: 84.5,
        2004: 90.4,
        2005: 91.2,
        2006: 90.0,
        2007: 98.3,
        2008: 98.7,
        2010: 98.6,
        2011: 98.7,
        2012: 98.9,
        2013: 87.5,
        2017: 95.4,
        2018: 96.4,
        2019: 97.0,
        2020: 98.1,
        2021: 98.8,
        2022: 98.2,
        2023: 99.0,
        2026: 98.6,
    },
    "assists": {2002: 0.0, 2003: 0.0, 2004: 0.0, 2005: 0.0, 2006: 78.9, 2007: 97.8, 2008: 98.7, 2013: 90.2, 2014: 99.0, 2015: 98.7, 2016: 65.6, 2018: 98.4, 2019: 98.5, 2026: 98.6},
    "steals": {
        2002: 0.1,
        2003: 0.1,
        2004: 0.0,
        2005: 0.2,
        2006: 77.4,
        2007: 97.8,
        2008: 98.9,
        2013: 89.7,
        2014: 93.0,
        2015: 92.1,
        2016: 92.5,
        2017: 90.9,
        2018: 89.9,
        2019: 96.8,
        2020: 98.2,
        2022: 97.6,
        2024: 99.0,
    },
    "blocks": {
        2002: 1.1,
        2003: 1.4,
        2004: 1.0,
        2005: 2.2,
        2006: 80.3,
        2007: 98.7,
        2008: 98.7,
        2009: 98.6,
        2010: 97.9,
        2011: 99.0,
        2013: 96.4,
        2015: 98.6,
        2016: 97.4,
        2017: 91.9,
        2018: 94.7,
        2019: 95.0,
        2020: 97.3,
        2021: 98.5,
        2022: 97.2,
        2024: 97.9,
        2025: 98.4,
        2026: 98.1,
    },
    "turnovers": {
        2002: 70.1,
        2003: 77.2,
        2004: 28.3,
        2005: 63.4,
        2006: 71.5,
        2007: 86.2,
        2008: 89.4,
        2009: 92.5,
        2010: 91.6,
        2011: 98.9,
        2012: 98.8,
        2013: 77.2,
        2014: 92.8,
        2015: 92.9,
        2016: 64.9,
        2017: 97.2,
        2018: 93.8,
        2019: 96.2,
        2020: 97.5,
        2021: 98.8,
        2022: 97.5,
        2024: 98.9,
        2026: 98.6,
    },
    "fouls": {
        2002: 79.3,
        2003: 80.2,
        2004: 87.1,
        2005: 84.0,
        2006: 77.4,
        2007: 95.0,
        2008: 97.0,
        2009: 97.5,
        2010: 98.0,
        2011: 97.4,
        2012: 97.3,
        2013: 90.4,
        2014: 98.7,
        2015: 97.3,
        2016: 98.0,
        2017: 98.5,
        2018: 83.2,
        2023: 98.9,
        2026: 98.2,
    },
}
"""Per :data:`TEAM_PERIOD_COLUMNS` column, per season, the percentage of
team-games whose period figures, summed over the whole game, equal the box
score (the final score, for points) - for the seasons under 99% only. A team
period answer reading a column in a listed season says so, and refuses under
90%; ``scripts/check_team_period_lines.py`` re-measures it.

.. versionadded:: 5.0.0
"""


def _team_period_in(periods: tuple[int, ...] | None) -> str:
    """``{alias}.period IN (1, 2)`` for ``periods``, or ``TRUE`` for every
    period - written as integers, never bound, after checking they are."""
    if periods is None:
        return "TRUE"
    if not periods or not all(isinstance(p, int) and not isinstance(p, bool) and 1 <= p <= 10 for p in periods):
        raise ValueError(f"periods must be integers 1-10, got {periods!r}")
    return f"{{alias}}.period IN ({', '.join(str(p) for p in periods)})"


def team_period_points_sql(linescores: str, periods: tuple[int, ...]) -> str:
    """SQL for one side's points in ``periods``, read from a linescore column
    (``'25,32,25,26'``) - NULL where the game reached none of them, never a
    zero for an overtime that was not played.

    .. versionadded:: 5.0.0
    """
    _team_period_in(periods)
    listed = ", ".join(str(p) for p in periods)
    return f"list_sum(list_filter(list_transform(list_select(string_split({linescores}, ','), [{listed}]), x -> TRY_CAST(trim(x) AS BIGINT)), x -> x IS NOT NULL))"


def team_period_line_sql(periods: tuple[int, ...] | None, games: str, *, plays: bool = True) -> str:
    """One row per team per game - ``event_id``, ``season``, ``team_id``,
    every :data:`TEAM_PERIOD_COLUMNS` column but ``points``, and
    ``teamTurnovers`` (the share of ``turnovers`` charged to the team itself,
    the column the team box score calls by that name) - summed over
    ``periods`` (every period, overtime included, when ``None``), for the
    games ``games`` names: SQL selecting ``event_id, season`` pairs.

    The sum of the team's players' period lines
    (:func:`association.query.player_games.period_line_sql`) and the team's
    own plays - the shots ESPN charted with no shooter and the turnovers it
    charged to the team rather than a player. With ``plays`` false every
    column the plays carry is NULL - unknown, never zero.

    .. versionadded:: 5.0.0
    """
    # Called here, not imported at the top: player_games imports conditions,
    # which imports this module.
    from association.query.player_games import _PERIOD_FREE_THROW, _PERIOD_HEAVE_MISS, _PERIOD_TURNOVER, PERIOD_COLUMNS, PERIOD_PLAYS_COLUMNS, period_line_sql

    in_periods = _team_period_in(periods)
    # The team's own turnovers are carried a second time on their own, as
    # `teamTurnovers`, so the check can read the players' share by itself.
    columns = [c for c in PERIOD_COLUMNS if c != "points"] + ["teamTurnovers"]
    players = period_line_sql(periods, "SELECT event_id, season FROM _team_period_keys", plays=plays)
    keyed = "EXISTS (SELECT 1 FROM _team_period_keys k WHERE k.event_id = {a}.event_id AND k.season = {a}.season)"
    shot_zero = ", ".join(f"0 AS {c}" for c in columns if c not in PERIOD_PLAYS_COLUMNS and c != "teamTurnovers")
    plays_zero = ", ".join(f"{'0' if plays else 'CAST(NULL AS BIGINT)'} AS {c}" for c in columns if c in PERIOD_PLAYS_COLUMNS and c != "turnovers")
    own_shots = f"""
        SELECT sc.event_id, sc.season, sc.team_id,
            SUM(CASE WHEN sc.made AND NOT {_PERIOD_FREE_THROW} THEN 1 ELSE 0 END) AS fieldGoalsMade,
            SUM(CASE WHEN NOT {_PERIOD_FREE_THROW} AND NOT {_PERIOD_HEAVE_MISS} THEN 1 ELSE 0 END) AS fieldGoalsAttempted,
            0 AS threePointFieldGoalsMade, 0 AS threePointFieldGoalsAttempted,
            SUM(CASE WHEN sc.made AND {_PERIOD_FREE_THROW} THEN 1 ELSE 0 END) AS freeThrowsMade,
            SUM(CASE WHEN {_PERIOD_FREE_THROW} THEN 1 ELSE 0 END) AS freeThrowsAttempted,
            {plays_zero}, {"0" if plays else "CAST(NULL AS BIGINT)"} AS turnovers, {"0" if plays else "CAST(NULL AS BIGINT)"} AS teamTurnovers
        FROM shot_chart sc
        WHERE {keyed.format(a="sc")} AND (sc.athlete_id IS NULL OR sc.athlete_id = '') AND sc.team_id IS NOT NULL AND {in_periods.format(alias="sc")}
        GROUP BY 1, 2, 3"""
    own_plays = f"""
        SELECT p.event_id, p.season, p.team_id, {shot_zero}, {plays_zero.replace("CAST(NULL AS BIGINT)", "0")},
            SUM(CASE WHEN {_PERIOD_TURNOVER} THEN 1 ELSE 0 END) AS turnovers,
            SUM(CASE WHEN {_PERIOD_TURNOVER} THEN 1 ELSE 0 END) AS teamTurnovers
        FROM plays p
        WHERE {keyed.format(a="p")} AND (p.athlete_id IS NULL OR p.athlete_id = '') AND p.team_id IS NOT NULL AND {in_periods.format(alias="p")}
        GROUP BY 1, 2, 3"""
    listed = ", ".join(columns)
    player_columns = ", ".join("0 AS teamTurnovers" if c == "teamTurnovers" else f"l.{c}" for c in columns)
    parts = f"SELECT r.event_id, r.season, r.team_id, {player_columns} FROM ({players}) l JOIN _team_period_roster r USING (event_id, season, athlete_id)"
    parts += f" UNION ALL SELECT event_id, season, team_id, {listed} FROM ({own_shots})"
    if plays:
        parts += f" UNION ALL SELECT event_id, season, team_id, {listed} FROM ({own_plays})"
    sums = ", ".join(f"CAST(SUM({c}) AS BIGINT) AS {c}" for c in columns)
    return f"""
        WITH _team_period_keys AS ({games}),
        _team_period_roster AS (
            SELECT DISTINCT b.event_id, b.season, b.athlete_id, b.team_id FROM player_box_stats b WHERE {keyed.format(a="b")}
        ),
        _team_period_parts AS ({parts})
        SELECT event_id, season, team_id, {sums}
        FROM _team_period_parts GROUP BY event_id, season, team_id"""


def _team_period_source(narrowed: TeamNarrowed) -> tuple[str, list[Any]]:
    """The relation as a period-narrowed read sees it, and the parameters it
    binds ahead of the WHERE: ``team_games`` with ``team_score`` and
    ``opponent_score`` replaced by each side's linescore over the periods,
    ``points`` beside them, and every other :data:`TEAM_PERIOD_COLUMNS`
    column rebuilt by :func:`team_period_line_sql` - re-aliased ``tg`` so
    every clause and every reader's SQL is unchanged.

    A rebuilt column is NULL for a game the shot table does not cover - a
    game with no charted shots would otherwise contribute a confident zero -
    and for a warehouse without the tables it is read from. The line is
    summed over the games the base clauses (team, span, season type) select,
    never all 14 million plays. Every alias here is one ``TEAM_GAMES_SQL``
    does not use (see ``templates.games._team_quarter_points_games`` for the
    binder error a reused one raised).
    """
    assert narrowed.periods is not None
    periods = narrowed.periods
    own = team_period_points_sql("CASE WHEN tg.side = 'home' THEN tpl_g.home_linescores ELSE tpl_g.away_linescores END", periods)
    theirs = team_period_points_sql("CASE WHEN tg.side = 'home' THEN tpl_g.away_linescores ELSE tpl_g.home_linescores END", periods)
    columns = [c for c in TEAM_PERIOD_COLUMNS if c != "points"]
    base = " AND ".join(narrowed.base) or "TRUE"
    if not narrowed.period_shots:
        rebuilt = ", ".join(f"CAST(NULL AS BIGINT) AS {c}" for c in columns)
        return (
            f"(SELECT tg.* REPLACE ({own} AS team_score, {theirs} AS opponent_score), {own} AS points, {rebuilt} "
            "FROM team_games tg JOIN real_games tpl_g ON tpl_g.event_id = tg.event_id AND tpl_g.season = tg.season) tg",
            [],
        )
    from association.query.player_games import PERIOD_PLAYS_COLUMNS  # call time: see team_period_line_sql

    line = team_period_line_sql(periods, f"SELECT DISTINCT tg.event_id, tg.season FROM team_games tg WHERE {base}", plays=narrowed.period_plays)
    covered = "tg.event_id IN (SELECT DISTINCT tpl_cov.event_id FROM shot_chart tpl_cov WHERE tpl_cov.season = tg.season)"

    def _figure(c: str) -> str:
        # A plays column stays NULL where the warehouse has no plays, and
        # every column where the shot table does not cover the game.
        known = f"COALESCE(tpl_l.{c}, 0)" if narrowed.period_plays or c not in PERIOD_PLAYS_COLUMNS else f"tpl_l.{c}"
        return f"CASE WHEN {covered} THEN {known} END AS {c}"

    rebuilt = ", ".join(_figure(c) for c in columns)
    sql = (
        f"(WITH _team_period_line AS ({line}) "
        f"SELECT tg.* REPLACE ({own} AS team_score, {theirs} AS opponent_score), {own} AS points, {rebuilt} "
        "FROM team_games tg JOIN real_games tpl_g ON tpl_g.event_id = tg.event_id AND tpl_g.season = tg.season "
        "LEFT JOIN _team_period_line tpl_l ON tpl_l.event_id = tg.event_id AND tpl_l.season = tg.season AND tpl_l.team_id = tg.team_id) tg"
    )
    return sql, list(narrowed.base_params)


def _team_source(narrowed: TeamNarrowed) -> tuple[str, list[Any]]:
    """The relation a read's FROM names - ``team_games`` itself, or the
    period's (:func:`_team_period_source`) - and the parameters it binds
    ahead of the WHERE."""
    if narrowed.periods is not None:
        return _team_period_source(narrowed)
    return "team_games tg", []


def _windowed(narrowed: TeamNarrowed, *, join: str = "") -> tuple[str, list[Any]]:
    """The FROM ... WHERE of a read: the relation under every row filter, and
    under the window too when one is set - as a subquery cut to the newest or
    oldest N by Eastern date, aliased so ``tg.`` still names the columns a
    reader wrote against. ``join`` is any extra table a caller's own SELECT
    needs (see :func:`rows_sql`); it always follows ``tg`` - inside the window
    it has nothing left to filter, since the window has already cut the rows.

    The team counterpart of :func:`association.query.player_games._windowed`.
    A period-narrowed read reads the period's relation (:func:`_team_source`).

    .. versionadded:: 4.4.0
    """
    where, params = narrowed.clauses()
    source, source_params = _team_source(narrowed)
    if narrowed.window is None:
        return f"FROM {source}{join} WHERE {where}", source_params + params
    order, n = narrowed.window
    direction = "DESC" if order == "recent" else "ASC"
    inner = f"SELECT tg.* FROM {source} WHERE {where} ORDER BY tg.eastern_date {direction}, tg.event_id LIMIT {int(n)}"
    return f"FROM ({inner}) tg{join}", source_params + params


def rows_sql(narrowed: TeamNarrowed, select: str, *, order: str, limit: int | None = None, join: str = "") -> tuple[str, list[Any]]:
    """The rows themselves - a team's game log, a season's games tallied by
    month. ``select`` and ``order`` are column expressions written in code.

    ``join`` is any extra ``FROM`` clause a caller's own ``select`` needs -
    typically ``" JOIN teams o ON o.team_id = tg.opponent_id"``, for the
    opponent's own-season name (:func:`association.nba.franchises.season_name_sql`)
    - since the base FROM is always ``team_games tg`` alone.

    .. versionchanged:: 4.4.0
       Honors :attr:`TeamNarrowed.window`.
    """
    source, params = _windowed(narrowed, join=join)
    sql = f"{_TEAM_GAMES} SELECT {select} {source} ORDER BY {order}"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return sql, params


def aggregate_sql(narrowed: TeamNarrowed, selects: list[str], *, join: str = "") -> tuple[str, list[Any]]:
    """One row of aggregates over the narrowed games: a count, a tally, a
    sum of points. See :func:`rows_sql` for ``join``.

    .. versionchanged:: 4.4.0
       Honors :attr:`TeamNarrowed.window`.
    """
    source, params = _windowed(narrowed, join=join)
    return f"{_TEAM_GAMES} SELECT {', '.join(selects)} {source}", params


def games_subquery(narrowed: TeamNarrowed, *, join: str = "") -> tuple[str, list[Any]]:
    """The narrowed games as a subquery holding every ``team_games`` column -
    what a reader that groups a team's games by a condition (a split, a
    streak, a with/without window) wraps. See :func:`rows_sql` for ``join``.

    .. versionchanged:: 4.4.0
       Honors :attr:`TeamNarrowed.window`.
    """
    source, params = _windowed(narrowed, join=join)
    return f"{_TEAM_GAMES} SELECT tg.* {source}", params


def named(sql: str, params: list[Any], prefix: str = "r") -> tuple[str, dict[str, Any]]:
    """``sql`` with each positional ``?`` renamed ``$<prefix><n>``, and the
    parameters as that dict - for a reader that nests the same subquery more
    than once, or binds names of its own beside it. A ``?`` can appear only
    once in a statement; a name can be reused, and DuckDB will not mix the two
    in one statement. Only the relation's own SQL is rewritten, never a value.

    The player relation's own :func:`association.query.player_games.named` in
    every particular but the module it lives in - kept as a separate copy
    rather than a shared import, the same way :class:`TeamNarrowed` is its own
    dataclass rather than a subclass of
    :class:`association.query.player_games.Narrowed`: the two relations share
    no base class, on purpose, so a change to one's clause-composition rules
    cannot silently reach the other.

    .. versionadded:: 4.4.0
    """
    parts = sql.split("?")
    if len(parts) - 1 != len(params):
        raise ValueError(f"{len(parts) - 1} placeholders for {len(params)} parameters")
    out = parts[0]
    for i, part in enumerate(parts[1:]):
        out += f"${prefix}{i}" + part
    return out, {f"{prefix}{i}": v for i, v in enumerate(params)}
