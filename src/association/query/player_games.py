"""The ``player_game`` relation: one row per player per game, read one way.

This module is the one definition of the relation - its FROM, its "did he
play" guard and its season clause - because definitions kept per reader
drift apart. The rules it carries are the ones `DATA.md` and `AGENTS.md`
record, applied on every read:

- **the phantom 1993 season is excluded by name** in a career span, and every
  join to ``games`` is keyed on ``season`` as well as ``event_id`` (the 1993
  label shares its event ids with 1994);
- **a row is a game played** only when it is not a did-not-play entry and
  carries minutes - or a line rebuilt from play-by-play, where every column
  read is one a rebuild gets right (:data:`REBUILT_STATS`);
- **a teammate's absence** is measured inside the teammate's own tenure on
  the team (:func:`_tenure_clause`), never over games he was somewhere else.

One kind of read deliberately steps outside the guard: counting the games it
drops. An answer built from box scores says how many games it could not see
(the empty 2013-18 Bulls and Pelicans box scores, a rebuilt line held back
for a stat the rebuild gets wrong), and that count has to include exactly the
rows the guard excludes. :func:`scope_without_guard` is the span clause for
those reads, and its name is the reminder. The team-level counterpart - a
team-game with no box score at all, inside a spell a player was on the team -
is a read of ``team_box_stats``, not of this relation, and lives beside the
other team reads in :mod:`association.query.conditions` (``_box_missing``).

Narrowing composes: an opponent, a venue, a starter/bench half, a teammate's
absence, a threshold on a stat, a month - each is one more clause over the
same rows, so a new one reaches every reader of the relation at
once. Entity binding is not here: this module takes ids the entity layer has
already resolved, and never a name.

.. versionadded:: 4.3.0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import duckdb

from association.nba.coverage import COVERAGE, POSTSEASON, REGULAR_SEASON
from association.nba.period_line import PERIOD_PLAYS_COLUMNS, PLAYS_COMPLETE, gap_column, period_line_sql
from association.nba.season import eastern_date_sql, eastern_day_utc_range
from association.nba.shot_value import UNSEPARABLE_SHOT_VALUES
from association.query.calendar import AlignmentNarrowing, CalendarNarrowing, alignment_clause, calendar_clause
from association.query.measures import PERIOD_COLUMNS as PERIOD_COLUMNS

from .conditions import UNGATED_ON_REBUILD, BoxSource
from .entities import Entity
from .reading import STARTER_SIDES as STARTER_SIDES  # the one table, on the reader's side; a split names one half of it
from .reading import PlayoffRound
from .team_games import round_clause, round_phrase, series_game_clause

# A player-game ESPN lists as played but records no minutes for. Every such row
# in the warehouse carries no stats either (checked, 1994-2026), and they are of
# two kinds. In 2006-2012 they are ~10,000 a season of appearances nobody made,
# which ESPN's own games-played counts mostly leave out (dropping them makes 378
# of 445 players' 2009 counts agree, against 30). In 2013-2018 they are whole
# team box scores ESPN left empty - ~13% of team-games, which is why summing
# those seasons' box scores gives 87% of the season totals. Averaged in, either
# kind reads as a game of zeros, so they are left out and the answer says how
# many.
FLOOR: int = COVERAGE["player_box_stats"].first_season
"""The first season with box scores (1994); a career of them starts here.

.. versionadded:: 4.3.0
"""

_RECORDED = "pgl.minutes IS NOT NULL"


#: A ``season_type`` value meaning "both the regular season and the
#: postseason at once" - a sentinel outside the two real values a game row
#: carries (``REGULAR_SEASON`` 2, ``POSTSEASON`` 3), never written to a
#: ``season_type`` column itself. Read for a "last N games" log naming no
#: season type at all, and for a question that asks for both outright
#: ("including the playoffs", "regular season and playoffs") - the span
#: tagger's :attr:`~association.query.reading.Span.both`. :func:`season_type_clause` is the one
#: place this turns into SQL; every other reader of the relation still sees a
#: real ``season_type`` (2 or 3) or this sentinel, never a third meaning.
#:
#: .. versionadded:: 4.4.0
BOTH_SEASON_TYPES: int = 0


def season_type_clause(column: str, season_type: int) -> tuple[str, list[Any]]:
    """SQL restricting ``column`` to one season type, or - for
    :data:`BOTH_SEASON_TYPES` - both of the two real ones, and its parameters.

    The one place a ``season_type`` value becomes SQL, so a new caller that
    wants to honor "including the playoffs" gets the sentinel right by
    construction rather than by copying an ``IN`` list.

    .. versionadded:: 4.4.0
    """
    if season_type == BOTH_SEASON_TYPES:
        return f"{column} IN ({REGULAR_SEASON}, {POSTSEASON})", []
    return f"{column} = ?", [season_type]


#: The stats a rebuilt box line may be read for, and the reason the list is
#: short.
#:
#: Where ESPN serves an empty box score, ``player_box_stats_filled`` carries a
#: line rebuilt from play-by-play (see
#: :mod:`association.fetch.repairs.reconstructed_box`). Measured per player-game against
#: the 22,646 games of the 2015 regular season whose real box score survived,
#: the mean absolute error of a rebuilt figure is:
#:
#: ===================== ========= ==============
#: Stat                  Exact     Mean abs error
#: ===================== ========= ==============
#: ``freeThrowsMade``    100.0%    0.0003
#: ``blocks``            99.8%     0.002
#: ``rebounds``          99.6%     0.004
#: ``assists``           99.6%     0.004
#: ``fieldGoalsMade``    99.6%     0.005
#: ``steals``            99.1%     0.009
#: ``points``            98.3%     0.021
#: ``turnovers``         92.5%     0.080
#: ``fouls``             83.3%     0.181
#: ===================== ========= ==============
#:
#: ``threePointFieldGoalsMade`` 98.6% (the rebuild module's measurement over
#: the 22,660 games, :mod:`association.fetch.repairs.reconstructed_box`), as
#: close as ``points``: every made three is a scoring play the plays hold.
#: Once left out, so a count of games with two threes left the
#: rebuilt games out where one with twenty points kept them (ISSUES.md #501).
#:
#: ``turnovers`` and ``fouls`` are left out: an order of magnitude worse than
#: the rest, and a foul is wrong in one game in six. The attempts and the
#: split rebounds are left out with minutes, which play-by-play cannot recover:
#: a percentage or an attempt is read over the fetched games alone. The rest are
#: wrong by hundredths of a point per game, which is why this is a PER-GAME
#: list only.
#:
#: **A rebuilt SEASON total is a different matter and is not read anywhere.**
#: A season is exact only when the net error over every game is zero, so it
#: lands exactly right about half the time - and the error scales with games
#: played: over the Chicago and New Orleans player-seasons, a total that is
#: right averages 31.6 games and one that is wrong averages 55.6. 2016 is worse
#: still (18.9% exact, mean -16.8 points) because 180 of its scoring plays carry
#: ``type = 'Not Available'``.
#:
#: .. versionadded:: 2.2.0
REBUILT_STATS: frozenset[str] = frozenset({"points", "rebounds", "assists", "steals", "blocks", "fieldGoalsMade", "threePointFieldGoalsMade", "freeThrowsMade"})


# A rebuilt line has NULL minutes - play-by-play cannot recover them - so
# `_RECORDED` hides it from every reader by default. This is the opt-in.
_RECORDED_OR_REBUILT = "(pgl.minutes IS NOT NULL OR pgl.reconstructed)"


# One join serves venue and result both: games.home_team_id agrees with
# team_box_stats.home_away on every row (checked, all 87,008 as of the warehouse
# this was last verified against - the count grows with every pull). Keyed on
# season too, since the phantom 1993 shares its event ids with 1994.
_PLAYER_GAMES = "FROM player_game_log pgl JOIN games g ON g.event_id = pgl.event_id AND g.season = pgl.season"


REGULATION_QUARTERS: tuple[int, ...] = (1, 2, 3, 4)
"""The four quarters a "by quarter" breakdown divides each game into - the
league's table (``PERIOD_RANKING``) and a
named player's (``compose.core._compile_by_period``) alike. Overtime is no
quarter and is left out, and each answer says so.

.. versionadded:: 5.0.0
"""

PERIOD_BLANKED: tuple[str, ...] = ("plusMinus", "ts_pct", "efg_pct", "usage_pct", "game_score")
"""The columns play-by-play cannot restrict to a period, blanked (NULL) on a
period-narrowed row rather than left holding the whole game's figure under a
heading that says "first quarter". A rate a reader wants over a period is
computed from the rebuilt makes and attempts, never read from these.

``minutes`` cannot be restricted either, and is the one exception: it keeps
the whole game's figure because the played guard reads it. A reader showing
a period's line leaves it out, and a question asking for a period's minutes
is refused (it is not in :data:`PERIOD_COLUMNS`).

.. versionadded:: 5.0.0
"""


PERIOD_RATES: dict[str, tuple[str, str]] = {
    "fg_pct": ("fieldGoalsMade", "fieldGoalsAttempted"),
    "three_pct": ("threePointFieldGoalsMade", "threePointFieldGoalsAttempted"),
    "ft_pct": ("freeThrowsMade", "freeThrowsAttempted"),
}
"""A shooting percentage a period answer computes from the period's own makes
and attempts - the ratio of the two sums over the games, never a mean of
per-game rates - keyed by the compiler's own measure name
(:data:`association.query.compose.core.RATES`), so the point the reader plans
with one compiles as it is. The makes and the attempts are both on the
period's line (:data:`PERIOD_COLUMNS`) and on the team's
(:data:`~association.query.team_games.TEAM_PERIOD_COLUMNS`), which is what
makes a period's percentage a ratio away rather than a new read.

.. versionadded:: 5.0.0

.. versionchanged:: 5.0.0
   Lives on the period relation (was ``templates.games.PERIOD_RATES``).
"""


PERIOD_LOG_COLUMNS: tuple[str, ...] = ("points", "rebounds", "assists", "steals", "blocks", "turnovers", "fouls")
"""The period's whole line as a log shows it beside the field goals and free
throws, and the columns a whole-line caveat names.

.. versionadded:: 5.0.0
"""

# The columns whose figure needs a shot's VALUE (two or three), which 2002's
# shots do not carry (UNSEPARABLE_SHOT_VALUES).
_PERIOD_SHOT_VALUED = frozenset({"points", "threePointFieldGoalsMade", "threePointFieldGoalsAttempted"})


def period_columns(measure: str) -> tuple[str, ...]:
    """The period-line columns ``measure`` is read from: itself, or a rate's
    makes and attempts (:data:`PERIOD_RATES`) - so a caveat or a refusal
    about a rate is about both of the columns it divides.

    .. versionadded:: 5.0.0
    """
    return PERIOD_RATES.get(measure, (measure,))


def period_rate(games: list[dict[str, Any]], measure: str) -> tuple[int, int, float | None]:
    """A rate's makes, attempts and percentage over ``games`` (each a mapping
    holding the rate's two :data:`PERIOD_RATES` columns) - the ratio of the
    sums, and None where nothing was attempted (no percentage is a fact
    about zero attempts, not a zero).

    .. versionadded:: 5.0.0
    """
    made_column, attempted_column = PERIOD_RATES[measure]
    made = sum(int(g[made_column]) for g in games)
    attempted = sum(int(g[attempted_column]) for g in games)
    return made, attempted, (made * 100.0 / attempted if attempted else None)


PERIOD_MISSING_ABOVE = 0.10
"""Above this share of the games read whose play-by-play does not add up to
the box score in a column the question asks for, a period answer is refused
by that column and that share rather than bounded.

Every game is counted, its gap said (:class:`PeriodCounted`); but where many
games miss, the ones that miss are the ones richest in the stat (a game with
no assist always adds up on assists), so the bound stops being a few plays
and the figure is not an answer. Measured 2026-10-23 over the 628,074 played
player-games in a covered game (``scripts/check_period_lines.py`` prints the
table): from 2007 a column misses in at most 4.1% of a season's games (2017's
rebounds; 2016's points 9.6%, its field goal attempts 10.7%), while 2002-2005
miss assists in 68-70%, steals in 47-49% and blocks in 30-31%, which stay
refused - a share of one season, where the floor is read per answer over the
games it holds. Jeff's ruling (2026-10-23), his to move.

.. versionadded:: 7.0.0
"""


PERIOD_GAPS = "period_gaps"
"""The column every period-narrowed row carries beside the period's line:
for each :data:`PERIOD_COLUMNS` column in order, that player's box score
less his play-by-play summed over the whole game (0 where they agree), read
from the completeness table (:data:`~association.nba.period_line.PLAYS_COMPLETE`)
by :func:`period_counted` - or NULL where the game has no row there. The
table holds every played player-game the period relation reads but a line
rebuilt from the plays because ESPN served the box score empty (built over
the same played guard and the same covered games, and rebuilt on every
load), so where the warehouse holds the table, NULL is that rebuilt line:
no box score of its own to check the plays against.

.. versionadded:: 7.0.0
"""


@dataclass(frozen=True)
class PeriodCounted:
    """A period read's games, and what each one's play-by-play misses
    against its box score (:func:`period_counted`).

    .. versionadded:: 7.0.0
    """

    #: The games counted: every game the read selected but a season whose
    #: shots carry no value where a column read needs one.
    kept: list[dict[str, Any]]
    #: The games of a season whose shots carry no value
    #: (``UNSEPARABLE_SHOT_VALUES``), where a column read needs one.
    unseparable: list[dict[str, Any]]
    #: The kept games whose box score ESPN served empty and the warehouse
    #: rebuilt from the same plays: counted, with nothing to check them by.
    rebuilt: list[dict[str, Any]]

    def gaps(self, column: str) -> list[tuple[dict[str, Any], int]]:
        """The kept games whose play-by-play does not add up to the box
        score in ``column``, each with its gap (the box score less the
        plays): a period's figure in that game is off by at most that much."""
        at = PERIOD_COLUMNS.index(column)
        return [(row, int(row[PERIOD_GAPS][at])) for row in self.kept if row.get(PERIOD_GAPS) is not None and row[PERIOD_GAPS][at]]

    def checked(self) -> int:
        """How many kept games the completeness table checks (all but the
        rebuilt)."""
        return len(self.kept) - len(self.rebuilt)

    def too_short(self, columns: tuple[str, ...]) -> dict[str, Any] | None:
        """The column asked for whose share of checked games that miss is
        over :data:`PERIOD_MISSING_ABOVE` - the worst, with how many miss of
        how many - or None."""
        checked = self.checked()
        worst = max(((len(self.gaps(column)), column) for column in columns), default=(0, ""))
        if not checked or worst[0] <= PERIOD_MISSING_ABOVE * checked:
            return None
        return {"column": worst[1], "missing": worst[0], "games": checked}


def period_unseparable(season: int, columns: tuple[str, ...]) -> dict[str, Any] | None:
    """Why ``season``'s per-period ``columns`` cannot be read at all, as
    plain facts - a column that needs a shot's value (points, threes) in a
    season whose shots carry none (``UNSEPARABLE_SHOT_VALUES``) - or None.
    The one rule the per-game gap cannot replace: a 2002 game's points
    "add up" only where the unvalued shots happen to cancel out.

    .. versionadded:: 7.0.0
    """
    if season not in UNSEPARABLE_SHOT_VALUES:
        return None
    for column in columns:
        if column in _PERIOD_SHOT_VALUED:
            return {"season": season, "column": column, "unseparable": True}
    return None


def period_counted(rows: list[dict[str, Any]], columns: tuple[str, ...]) -> PeriodCounted:
    """The games of a period read, read as ``columns`` (the asked stat's,
    :func:`period_columns`, or a whole line's), and what each one's plays
    miss.

    Whatever the season, every game counts, and each one's gap against its
    box score (:data:`PERIOD_GAPS`) bounds how far its period figure can be
    off: where it adds up, nothing he did in it is missing from the plays
    (where every player of a 2016 team adds up on points, the period line's
    team-quarters agree with the linescore in 99.98% of 4,544). Two declared
    rules cover what the table cannot say: a season whose shots carry no
    value (:func:`period_unseparable`) is left out where a column read needs
    one, and a line ESPN served empty (NULL, rebuilt from the same plays) is
    counted unchecked, as every other read of that game counts it.

    .. versionadded:: 7.0.0
    """
    kept: list[dict[str, Any]] = []
    unseparable: list[dict[str, Any]] = []
    rebuilt: list[dict[str, Any]] = []
    for row in rows:
        if period_unseparable(int(row["season"]), columns) is not None:
            unseparable.append(row)
            continue
        kept.append(row)
        if row.get(PERIOD_GAPS) is None:
            rebuilt.append(row)
    return PeriodCounted(kept=kept, unseparable=unseparable, rebuilt=rebuilt)


def _period_source(narrowed: Narrowed) -> tuple[str, list[Any]]:
    """The FROM of a period-narrowed read and the parameters it binds ahead
    of the WHERE: the relation with each :data:`PERIOD_COLUMNS` column
    replaced by the period's own figure and each :data:`PERIOD_BLANKED`
    column blanked, re-aliased ``pgl`` beside ``g`` so every
    clause and every reader's SQL is unchanged.

    The replacement happens BEFORE the row filters, so a line on a stat
    ("games with 10+ first-quarter points") is a line on the period, as the
    question means it. ``minutes`` alone keeps the whole game's figure: the
    played guard reads it, and play-by-play has no period minutes to put in
    its place - a reader showing a period line leaves it out, and a question
    asking for a period's minutes is refused before this is read.

    Only games the shot table covers are kept - a game with no located shots
    would otherwise contribute a confident zero. The period line is summed over the games the
    base clauses (player, span, season type) select, never all 14 million
    plays.
    """
    base = " AND ".join(narrowed.base) or "TRUE"
    keys = f"SELECT DISTINCT pgl.event_id, pgl.season {_PLAYER_GAMES} WHERE {base}"
    line = period_line_sql(narrowed.periods, keys, plays=narrowed.period_plays)
    present = narrowed.period_log_columns

    def _period_figure(c: str) -> str:
        # A plays column stays NULL where the warehouse has no plays:
        # unknown, not a quiet quarter.
        return f"COALESCE(l.{c}, 0)" if narrowed.period_plays or c not in PERIOD_PLAYS_COLUMNS else f"l.{c}"

    # A column the log holds is REPLACEd in place; one it lacks (an older
    # warehouse, a fixture) is added, so a reader's `pgl.<column>` always binds.
    held = [c for c in PERIOD_COLUMNS if present is None or c in present]
    added = [c for c in PERIOD_COLUMNS if c not in held]
    blanked = [c for c in PERIOD_BLANKED if present is None or c in present]
    replaced = ", ".join([*(f"{_period_figure(c)} AS {c}" for c in held), *(f"NULL AS {c}" for c in blanked)])
    star = f"pgl.* REPLACE ({replaced})" if replaced else "pgl.*"
    extra = "".join(f", {_period_figure(c)} AS {c}" for c in added)
    covered = "pgl.event_id IN (SELECT DISTINCT cov.event_id FROM shot_chart cov WHERE cov.season = pgl.season)"
    gaps, checked = _period_gaps(narrowed.period_checked)
    sql = (
        f"FROM (WITH _period_line AS ({line}) "
        f"SELECT {star}{extra}, {gaps} AS {PERIOD_GAPS} FROM player_game_log pgl "
        "LEFT JOIN _period_line l ON l.event_id = pgl.event_id AND l.season = pgl.season AND l.athlete_id = pgl.athlete_id "
        f"{checked}WHERE {covered}) pgl JOIN games g ON g.event_id = pgl.event_id AND g.season = pgl.season"
    )
    return sql, list(narrowed.base_params)


def _period_gaps(checked: bool) -> tuple[str, str]:
    """A period-narrowed row's :data:`PERIOD_GAPS` - each
    :data:`PERIOD_COLUMNS` column's box score less its whole-game line, read
    from the completeness table
    (:data:`~association.nba.period_line.PLAYS_COMPLETE`) - and the join
    that reads it. NULL where the game has no row there (a line ESPN served
    empty, rebuilt from the very plays it would be checked against) or the
    warehouse has no such table."""
    if not checked:
        return "CAST(NULL AS BIGINT[])", ""
    gaps = ", ".join(f"pc.{gap_column(c)}" for c in PERIOD_COLUMNS)
    join = f"LEFT JOIN {PLAYS_COMPLETE} pc ON pc.event_id = pgl.event_id AND pc.season = pgl.season AND pc.athlete_id = pgl.athlete_id "
    return f"(CASE WHEN pc.event_id IS NULL THEN NULL ELSE [{gaps}] END)", join


def _source(narrowed: Narrowed) -> tuple[str, list[Any]]:
    """The relation's FROM for ``narrowed`` and the parameters it binds ahead
    of the WHERE - the plain one, or the period's (:func:`_period_source`)."""
    if narrowed.periods is not None:
        return _period_source(narrowed)
    return _PLAYER_GAMES, []


def _teammate_played(box: BoxSource) -> str:
    """SQL for "this teammate played that game", over the given box source.

    A game rebuilt from play-by-play has no minutes, so against the stored
    table every teammate in a rebuilt game reads as absent and the game counts
    as one played "without" him. Measured before this took a source: "Anthony
    Davis without Eric Gordon, 2015" listed games Gordon played in - he played
    48 of Davis's 68 that season.

    .. versionadded:: 2.2.0
    """
    appeared = "(m.minutes IS NOT NULL OR m.reconstructed)" if box.rebuilt else "m.minutes IS NOT NULL"
    return f"EXISTS (SELECT 1 FROM {box.table} m WHERE m.athlete_id = ? AND m.event_id = pgl.event_id AND m.season = pgl.season AND NOT m.did_not_play AND {appeared})"


def _joined(names: list[str], word: str = "and") -> str:
    """ "A", "A and B", "A, B and C" - a list of names as a sentence holds it."""
    if len(names) <= 1:
        return names[0] if names else ""
    return f"{', '.join(names[:-1])} {word} {names[-1]}"


#: The predicates a :class:`Condition` can state about its player in a game.
CONDITION_PREDICATES: frozenset[str] = frozenset({"played", "absent", "started", "bench", "reached"})
"""Every value :attr:`Condition.predicate` takes.

.. versionadded:: 5.0.0
"""


@dataclass
class Condition:
    """A player condition on a game: ``player`` on the subject's ``side``
    (``"own"`` - a teammate - or ``"opponent"``) with a ``predicate`` that
    held in that game - he ``played``, was ``absent``, ``started``, came off
    the ``bench``, or ``reached`` a line (``column op value``, said as
    ``label``). "Without Durant", "when Embiid and Paul George play", "when
    Maxey scores 20+", "vs LeBron" are each one of these; a list of them is
    ANDed: the one shape ``Narrowed.without`` (own,
    absent), the starter half (the subject's own ``started``), a record over a line's
    threshold (own, reached) and the pair relation's second player
    (opponent, played) were special cases of.

    ``tenure`` bounds an OWN-side absence to the games inside the player's
    time on the subject's team (:func:`_tenure_clause`): "Nets record
    without KD" must not count the decades before he arrived.

    .. versionadded:: 5.0.0
    """

    player: Entity
    side: str
    predicate: str
    line: tuple[str, str, int, str] | None = None
    tenure: tuple[str, list[Any]] | None = None

    def phrase(self) -> str:
        """How the answer says this condition after the subject's name."""
        name = self.player.name
        if self.predicate == "absent":
            return f"without {name}" + ("" if self.side == "own" else " on the other side")
        if self.predicate == "played":
            return f"with {name}" if self.side == "own" else f"vs {name}"
        if self.predicate == "started":
            return f"with {name} starting"
        if self.predicate == "bench":
            return f"with {name} off the bench"
        label = self.line[3] if self.line else "the line"
        return f"in games {name} had {label}"


def condition_clause(condition: Condition, box: BoxSource) -> tuple[list[str], list[Any]]:
    """The WHERE clauses (ANDed) and their parameters for one
    :class:`Condition` over the box source ``box`` - one EXISTS over the
    player's own row in the same game, on the stated side, with the
    predicate; an absence is its negation, bounded by the tenure clause
    where one is carried.

    .. versionadded:: 5.0.0
    """
    if condition.predicate == "absent" and condition.tenure is not None:
        # Word for word the clause pair `Narrowed.without` has always added:
        # the tenure first, then the negated appearance - the same rows.
        tenure, tenure_params = condition.tenure
        return [tenure, f"NOT {_teammate_played(box)}"], [*tenure_params, condition.player.id]
    appeared = "(m.minutes IS NOT NULL OR m.reconstructed)" if box.rebuilt else "m.minutes IS NOT NULL"
    side = "m.team_id = pgl.team_id" if condition.side == "own" else "m.team_id = pgl.opponent_team_id"
    inner = [f"m.athlete_id = ? AND m.event_id = pgl.event_id AND m.season = pgl.season AND NOT m.did_not_play AND {appeared} AND {side}"]
    params: list[Any] = [condition.player.id]
    if condition.predicate == "started":
        inner.append("m.starter")
    elif condition.predicate == "bench":
        inner.append("NOT m.starter")
    elif condition.predicate == "reached":
        assert condition.line is not None, "a reached condition carries its line"
        column, op, value, _ = condition.line
        inner.append(f"m.{column} {op} ?")
        params.append(value)
    exists = f"EXISTS (SELECT 1 FROM {box.table} m WHERE {' AND '.join(inner)})"
    return [f"NOT {exists}" if condition.predicate == "absent" else exists], params


@dataclass
class Narrowed:
    """One player's games in a span, and whatever the question narrowed them
    by. Every clause applies to ``_PLAYER_GAMES``; the base ones (player, season
    type, span, played) are kept apart from the rest so an empty answer can say
    which narrowing emptied it."""

    base: list[str]
    base_params: list[Any]
    extra: list[str] = field(default_factory=list)
    extra_params: list[Any] = field(default_factory=list)
    opponent: Entity | None = None
    #: The player's OWN team the question narrowed to - "for the Miami Heat",
    #: read only where the question names one and the model left no team
    #: at all (yardstick-v2 F166). Distinct from ``opponent``: this keeps
    #: only the games he played FOR this team, not games against it.
    team: Entity | None = None
    venue: str | None = None
    #: True for a log of starts, False for one off the bench, None when the
    #: question named neither half.
    started: bool | None = None
    #: Every player condition the question stated (:class:`Condition`), all
    #: of them at once: "without Tatum and Brown" is the games NEITHER
    #: played, so a dropped one would answer a wider question. ``without``
    #: and ``tenure`` below are views over the own-side absences, for the
    #: readers that phrase and count them.
    conditions: list[Condition] = field(default_factory=list)
    date: str | None = None
    #: Each box-score line the games were kept under or over, as the answer
    #: says it: ``"under 14 free throw attempts"``.
    measures: list[str] = field(default_factory=list)
    #: The same lines as values - ``(column, op, value, label)`` each - for
    #: the Result's typed cells (``result.Line``), in the same order.
    lines: list[tuple[str, str, Any, str]] = field(default_factory=list)
    #: The game of a playoff series the question named ("game 4"), or None.
    series_game: int | None = None
    #: The playoff round the question named ("the 2024 finals"), or None.
    playoff_round: PlayoffRound | None = None
    #: The window: the newest (``"recent"``) or oldest (``"first"``) N of the
    #: narrowed games, or None for all of them. Cut AFTER every row filter and
    #: BEFORE whatever a reader does with the rows, so "30-point games in his
    #: last 10" counts inside the ten and "his average over his last 5 vs
    #: Boston" averages the five Boston games - the one rule that is about
    #: the skeleton rather than the rows.
    window: tuple[str, int] | None = None
    #: The ordinal season a league-wide read was narrowed to - each player's
    #: Nth regular season ("most points in a 15th season") - or None.
    ordinal: int | None = None
    #: The calendar narrowing a ``situation`` slot named - a weekday, a month,
    #: a fixed day, every game from a day of the season on - or None.
    calendar: CalendarNarrowing | None = None
    #: The conference or division a ``situation`` slot named the OPPONENT to
    #: be in - "vs the west", "against the southeast division" - or None. The
    #: same slot as ``calendar`` and mutually exclusive with it: a value is
    #: read as one or the other, never both, by :func:`association.query.calendar.parse_situation`/
    #: :func:`~association.query.calendar.parse_alignment` in that order.
    alignment: AlignmentNarrowing | None = None
    #: The periods a quarter or a half narrowed each game to - ``(1,)``,
    #: ``(3, 4)`` - or None for the whole game. Set by :meth:`narrow_periods`;
    #: every read then sees the period's own line (:func:`period_line_sql`).
    periods: tuple[int, ...] | None = None
    #: How the answer names those periods: ``"1st quarter"``, ``"2nd half"``.
    period_label: str | None = None
    #: Whether the warehouse holds ``plays``, which every period column but
    #: the shot ones is read from (:data:`PERIOD_PLAYS_COLUMNS`).
    period_plays: bool = True
    #: Whether the warehouse holds the completeness table
    #: (:data:`~association.nba.period_line.PLAYS_COMPLETE`), which every
    #: period-narrowed row reads its ``period_gaps`` from
    #: (:func:`period_counted`); without it the column is NULL - unknown.
    period_checked: bool = False
    #: The columns ``player_game_log`` actually has, where the caller looked -
    #: an older warehouse (or a fixture) lacks the advanced ones, and a
    #: REPLACE naming a missing column fails to bind.
    period_log_columns: frozenset[str] | None = None
    #: A period used as a CONDITION on which games count - ``(periods,
    #: column, op, threshold, phrase)``, set by :meth:`narrow_period_condition`:
    #: only the games whose line in those periods reached the threshold,
    #: with every read still seeing the WHOLE game (or the measured period's
    #: line, where :attr:`periods` is set too). ``phrase`` is how the answer
    #: says it ("1+ 3-pointers in the 1st quarter").
    period_condition: tuple[tuple[int, ...], str, str, int, str] | None = None

    @property
    def without(self) -> list[Entity]:
        """The teammates whose absence the games were narrowed to - the
        own-side ``absent`` conditions' players, in order.

        .. versionchanged:: 5.0.0
           A view over :attr:`conditions`, not a field of its own.
        """
        return [c.player for c in self.conditions if c.side == "own" and c.predicate == "absent"]

    @property
    def tenure(self) -> list[tuple[str, list[Any]]]:
        """Each absent teammate's tenure clause, aligned with :attr:`without`.

        .. versionchanged:: 5.0.0
           A view over :attr:`conditions`.
        """
        return [c.tenure or ("TRUE", []) for c in self.conditions if c.side == "own" and c.predicate == "absent"]

    def add_condition(self, condition: Condition, box: BoxSource) -> None:
        """Narrow to the games ``condition`` held in - its clauses joined to
        ``extra`` - unless the same player is already held under the same
        predicate and side (the same man named twice narrows nothing).

        .. versionadded:: 5.0.0
        """
        if any(c.player.id == condition.player.id and c.predicate == condition.predicate and c.side == condition.side for c in self.conditions):
            return
        clauses, params = condition_clause(condition, box)
        self.conditions.append(condition)
        self.extra += clauses
        self.extra_params += params

    def clauses(self, *, narrowed: bool = True, recorded: bool = True, rebuilt: bool = False) -> tuple[str, list[Any]]:
        """The WHERE body and its parameters - without the narrowing when
        ``narrowed`` is false, and over the empty lines when ``recorded`` is.

        ``rebuilt`` widens what counts as a game he played to include a line
        rebuilt from play-by-play. The NEGATION uses the same widened guard, so
        "games not counted" stays the complement of "games counted" - otherwise
        a rebuilt game would be both listed and reported as skipped.
        """
        guard = _RECORDED_OR_REBUILT if rebuilt else _RECORDED
        where = [*self.base, guard if recorded else f"NOT ({guard})"]
        params = list(self.base_params)
        if narrowed:
            where += self.extra
            params += self.extra_params
        return " AND ".join(where), params

    def _situation_phrase(self) -> str | None:
        """The phrase a ``situation`` slot named, or None - the calendar one
        (``self.calendar``) or the conference/division one (``self.alignment``),
        whichever :meth:`narrow_calendar`/:meth:`narrow_alignment` set. The two
        are mutually exclusive, since a value is read as one or the other
        (never both) by :func:`association.query.calendar.parse_situation`/
        :func:`~association.query.calendar.parse_alignment` in that order -
        pulled out of :meth:`filters` to keep its own branch count down.

        .. versionadded:: 4.4.0
        """
        if self.calendar is not None:
            return self.calendar.label
        if self.alignment is not None:
            return self.alignment.label
        return None

    def _condition_parts(self) -> list[str]:
        """The absent teammates as one phrase ("without A and B"), then every
        other condition's own - a list so :meth:`filters` adds no branch."""
        parts = [f"without {_joined([mate.name for mate in self.without])}"] if self.without else []
        return parts + [c.phrase() for c in self.conditions if not (c.side == "own" and c.predicate == "absent")]

    def _ordinal_parts(self) -> list[str]:
        """``["in their 15th season"]`` for a league read narrowed to each
        player's Nth season, else nothing - a list so :meth:`filters` adds no
        branch for it."""
        if self.ordinal is None:
            return []
        n = self.ordinal
        suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")  # codespell:ignore nd - an ordinal suffix
        return [f"in their {n}{suffix} season"]

    def _postseason_parts(self) -> list[str]:
        """The game of a series and the playoff round the games were
        narrowed to, as the answer says them - a list so :meth:`filters`
        adds no branch for them."""
        parts: list[str] = []
        if self.series_game is not None:
            # "of the series" only where one series is in view: an opponent
            # names it. Across a postseason it is game 4 of each series.
            parts.append(f"in game {self.series_game} of {'the' if self.opponent is not None else 'each'} series")
        if self.playoff_round is not None:
            parts.append(f"in {round_phrase(self.playoff_round)}")
        return parts

    def filters(self, *, dated: bool = True, windowed: bool = False) -> str:
        """What the games were narrowed to, as it follows a name: ``" vs the
        Detroit Pistons at home"``.

        ``windowed`` is opt-in and defaults off: ``.window`` is set by
        :func:`association.query.player_relation.scoped_games` for every
        caller whose window names an end or a count - including ``PLAYER_LOG``
        and ``PLAYER_LINE``, which read it for their OWN row-fetching
        (:func:`rows_sql` never consults ``.window``) and already say "last N
        games" their own way. Including the phrase here unconditionally would
        say it a second time in theirs. Only a caller that reads its rows
        through :func:`games_subquery`/:func:`aggregate_sql`/:func:`grouped_sql`
        - where ``.window`` is what actually cut the rows - has a reason to
        ask for it (``SHOT_CHART``/``SHOT_DISTANCE``).

        .. versionchanged:: 4.4.0
           Takes ``windowed`` (default ``False``); the window phrase moved
           behind it.
        """
        parts = [f"in the {self.period_label}"] if self.period_label else []
        if self.team is not None:
            parts.append(f"with the {self.team.name}")
        if self.opponent is not None:
            parts.append(f"vs the {self.opponent.name}")
        if self.venue:
            parts.append("at home" if self.venue == "home" else "on the road")
        if self.started is not None:
            # Said outright, like every other narrowing here. A log of 50
            # starts headed only "last 50 games" is the silent narrowing this
            # module exists to stop - it reads as his last 50 games played.
            parts.append("as a starter" if self.started else "off the bench")
        parts.extend(self._condition_parts())
        if self.period_condition is not None:
            parts.append(f"in games with {self.period_condition[4]}")
        if self.measures:
            parts.append(f"with {_joined(self.measures)}")
        parts.extend(self._ordinal_parts())
        parts.extend(self._postseason_parts())
        if self.date and dated:
            parts.append(f"on {self.date}")
        situation = self._situation_phrase()
        if situation:
            parts.append(situation)
        if self.window is not None and windowed:
            order, n = self.window
            parts.append(f"over his {'last' if order == 'recent' else 'first'} {n} game{'s' if n != 1 else ''}")
        return "".join(f" {part}" for part in parts)

    def narrow(self, clause: str, *params: Any) -> None:
        """One more clause over the same rows - how every narrowing composes."""
        self.extra.append(clause)
        self.extra_params.extend(params)

    def narrow_measure(self, column: str, op: str, value: Any, label: str | None = None) -> None:
        """A threshold on a box-score column: ``points >= 30``. ``op`` is one of
        :data:`MEASURE_OPS`; the column is a name from this module's own lists,
        never text from a question. A ``label`` is how the answer says it
        (``"under 14 free throw attempts"``); a threshold the caller words for
        itself passes none."""
        if op not in MEASURE_OPS:  # ops are written in code, so this is a programming error, not a refusal
            raise ValueError(f"no comparison called {op!r}")
        self.narrow(f"pgl.{column} {MEASURE_OPS[op]} ?", value)
        if label:
            self.measures.append(label)
            self.lines.append((column, op, value, label))

    def narrow_calendar(self, narrowing: CalendarNarrowing) -> None:
        """Only the games on a weekday, in a month, on a fixed day, or from a
        day of the season on - the ``situation`` cell, read by
        :func:`association.query.calendar.parse_situation` and applied by
        :func:`association.query.player_relation.scoped_games`. The date is
        the game's US Eastern day, so "on Tuesdays" is the night the game was
        played, not ESPN's UTC stamp.

        .. versionadded:: 4.4.0
        """
        clause, params = calendar_clause(narrowing, eastern_date_sql("g.date"), "pgl.season")
        self.narrow(clause, *params)
        self.calendar = narrowing

    def narrow_alignment(self, narrowing: AlignmentNarrowing) -> None:
        """Only the games against an opponent in this conference or division,
        for that game's own season - the ``situation`` cell's other half,
        read by :func:`association.query.calendar.parse_alignment` and
        applied by :func:`association.query.player_relation.scoped_games`/
        :func:`~association.query.player_relation.league_games`.
        ``pgl.opponent_team_id`` is the column :func:`league_games` already
        narrows by name against; this reads it against every team in the
        conference or division instead of one.

        .. versionadded:: 4.4.0
        """
        clause, params = alignment_clause(narrowing, "pgl.opponent_team_id", "pgl.season")
        self.narrow(clause, *params)
        self.alignment = narrowing

    def narrow_periods(self, periods: tuple[int, ...], label: str, *, plays: bool = True, log_columns: frozenset[str] | None = None, checked: bool = False) -> None:
        """Only ``periods`` of each game - a quarter, a half, an overtime -
        read from the line :func:`period_line_sql` rebuilds, over the games
        the shot table covers. The row filters still read the whole game
        (see :func:`_period_source`); what a reader SELECTs is the period's.
        ``checked``: the warehouse holds the completeness table each row's
        ``period_gaps`` is read from.

        .. versionadded:: 5.0.0

        .. versionchanged:: 7.0.0
           Takes ``checked``.
        """
        period_line_sql(periods, "SELECT 1")  # validates the periods before anything is read
        self.periods = tuple(periods)
        self.period_label = label
        self.period_plays = plays
        self.period_log_columns = log_columns
        self.period_checked = checked

    def narrow_period_condition(self, periods: tuple[int, ...], column: str, threshold: int, phrase: str, *, op: str = ">=", plays: bool = True, log_columns: frozenset[str] | None = None) -> None:
        """Only the games whose line in ``periods`` - a quarter, a half - held
        ``column`` at or above ``threshold``, or exactly at it with ``op``
        ``"="`` (#275: "three
        points made per game after making one three in first quarter" is his
        whole-game threes over the games whose first quarter held one). One
        more clause over the same rows, like every narrowing here: an EXISTS
        over the period's own rebuilt line (:func:`period_line_sql`, summed
        over the games the base clauses select, the way :func:`_period_source`
        sums a measured period's), so every reader of the relation - a log,
        an average, a count, the box-score notes - applies it through
        :meth:`clauses` with no source of its own. What a reader SELECTs is
        still the whole game's, or the measured period's where
        :meth:`narrow_periods` narrowed the line too. A game the shot table
        does not cover has no line and fails the condition, as it is no
        game in any period read.

        .. versionadded:: 5.0.0
        """
        period_line_sql(periods, "SELECT 1")  # validates the periods before anything is read
        if column not in PERIOD_COLUMNS or op not in MEASURE_OPS:  # written in code, so this is a programming error, not a refusal
            raise ValueError(f"no period-line column {column!r} or comparison {op!r}")
        self.period_condition = (tuple(periods), column, op, int(threshold), phrase)
        self.period_plays = plays
        if log_columns is not None:
            self.period_log_columns = log_columns
        base = " AND ".join(self.base) or "TRUE"
        keys = f"SELECT DISTINCT pgl.event_id, pgl.season {_PLAYER_GAMES} WHERE {base}"
        line = period_line_sql(periods, keys, plays=plays)
        self.narrow(
            f"EXISTS (SELECT 1 FROM ({line}) pc WHERE pc.event_id = pgl.event_id AND pc.season = pgl.season AND pc.athlete_id = pgl.athlete_id AND pc.{column} {MEASURE_OPS[op]} ?)",
            *self.base_params,
            int(threshold),
        )

    def narrow_series_game(self, n: int) -> None:
        """Only the ``n``th game of each playoff series: ``real_games.series_game``,
        the game's number within the series the two teams played - the
        series' own games, so a game he sat out still counts toward the
        number, and his ``n``th game played is not mistaken for game ``n``.

        .. versionchanged:: 7.0.0
           Reads the number ``real_games`` carries (ESPN's count, or the
           game's place by date in its series) rather than numbering the
           games itself.
        """
        clause, params = series_game_clause(n, "pgl.event_id", "pgl.season")
        self.narrow(clause, *params)
        self.series_game = n

    def narrow_round(self, playoff_round: PlayoffRound) -> None:
        """Only the games of one playoff round (:func:`round_clause`), said
        as the round (:func:`round_phrase`). A round the words named only
        as the rounds around one ("before the finals") is refused by value:
        nothing narrows to it, and dropping it would answer the whole
        postseason under a heading that names a round.

        .. versionadded:: 7.0.0
        """
        clause, params = round_clause(playoff_round, "pgl.event_id", "pgl.season")
        self.narrow(clause, *params)
        self.playoff_round = playoff_round


#: The comparisons :meth:`Narrowed.narrow_measure` accepts, mapped to SQL.
MEASURE_OPS: dict[str, str] = {">=": ">=", ">": ">", "<=": "<=", "<": "<", "=": "="}
"""``{">=": ">=", ...}`` - an allowlist, so no question text reaches SQL as an operator.

.. versionadded:: 4.3.0
"""


def league(season_clause: str, season_params: list[Any], season_type: int) -> Narrowed:
    """Every player's games in a span - the read a league-wide count or
    single-game high starts from. Same guards as one player's, without him.

    .. versionchanged:: 4.4.0
       Honors :data:`BOTH_SEASON_TYPES` through :func:`season_type_clause`.
    """
    type_clause, type_params = season_type_clause("pgl.season_type", season_type)
    return Narrowed(base=[type_clause, season_clause, "NOT pgl.did_not_play"], base_params=[*type_params, *season_params])


#: The tiebreak every :func:`rows_sql` read falls back on after its caller's
#: own order: the game, then the player, so two rows a caller's order leaves
#: equal - two centers in the same game, on the same date, in a league-wide
#: log - still come out in the same order every run rather than DuckDB's
#: parallel scan order, which is not stable. Appended once here rather than
#: repeated (or forgotten) in each of :func:`rows_sql`'s callers, per
#: ISSUES.md "A game log's same-date rows come out in an unstable order".
#:
#: .. versionadded:: 5.0.0
ROWS_TIEBREAK = "pgl.event_id, pgl.player_name"


def ordered_cut(order: str, limit: int | None, ties: tuple[str, str] | None = None, *, offset: int = 0) -> str:
    """`` ORDER BY order`` and the cut a read is kept to: ``LIMIT limit``,
    or - where the rows are ranked by a figure (``ties``: the ranked
    expression and its direction, ``"DESC NULLS LAST"``) - every row ranked
    within ``limit``, so the rows tied with the last one kept are kept too,
    up to :data:`TIE_ROOM` rows past the limit.

    A limit that cuts a tie lists some of the rows sharing a figure and the
    answer says the cut list as if it were all of them: "most 3s made in a
    single game by a team" named three of the four teams with 29, and a
    player's "top 3 by points" two of his games with 18 (ISSUES.md #476).
    A tie at the cut too big for the room (a hundred players with one game
    at the foot of a ranking) is left out whole, so the list ends before
    it rather than naming two of the hundred; only a tie for the top is
    cut at the room, there being nothing ahead of it to list.

    .. versionadded:: 7.0.0
    """
    if limit is None:
        return f" ORDER BY {order}" + (f" OFFSET {int(offset)}" if offset else "")
    if ties is None or offset:
        return f" ORDER BY {order} LIMIT {int(limit)}" + (f" OFFSET {int(offset)}" if offset else "")
    figure, direction = ties
    rank = f"RANK() OVER (ORDER BY {figure} {direction})"
    fits = f"{rank} + COUNT(*) OVER (PARTITION BY {figure}) - 1 <= {int(limit) + TIE_ROOM}"
    return f" QUALIFY {rank} <= {int(limit)} AND ({rank} = 1 OR {fits}) ORDER BY {order} LIMIT {int(limit) + TIE_ROOM}"


#: How many rows past its limit a ranked read keeps while they tie the
#: last one kept (:func:`ordered_cut`).
TIE_ROOM = 10
"""The rows past a ranked read's limit kept for a tie at the cut.

.. versionadded:: 7.0.0
"""


def rows_sql(narrowed: Narrowed, select: str, *, order: str, limit: int | None = None, offset: int = 0, rebuilt: bool = False, ties: tuple[str, str] | None = None) -> tuple[str, list[Any]]:
    """The rows themselves - a log, a single game, the top game by a stat.
    ``select`` and ``order`` are column expressions written in code, ordered
    after by :data:`ROWS_TIEBREAK` so no caller has to add its own. Rows
    ranked by a figure (``ties``) keep a tie at the limit (:func:`ordered_cut`).

    .. versionchanged:: 5.0.0
       Appends :data:`ROWS_TIEBREAK` after ``order``.

    .. versionchanged:: 7.0.0
       Takes ``ties``.
    """
    where, params = narrowed.clauses(rebuilt=rebuilt)
    source, ahead = _source(narrowed)
    params = [*ahead, *params]
    sql = f"SELECT {select} {source} WHERE {where}" + ordered_cut(f"{order}, {ROWS_TIEBREAK}", limit, ties, offset=offset)
    return sql, params


def _windowed(narrowed: Narrowed, *, rebuilt: bool) -> tuple[str, list[Any]]:
    """The FROM ... WHERE of a read: the relation under every row filter, and
    under the window too when one is set - as a subquery cut to the newest or
    oldest N by date, aliased so ``pgl.`` and ``g.`` still name the columns a
    reader wrote against."""
    where, params = narrowed.clauses(rebuilt=rebuilt)
    source, ahead = _source(narrowed)
    params = [*ahead, *params]
    if narrowed.window is None:
        return f"{source} WHERE {where}", params
    order, n = narrowed.window
    direction = "DESC" if order == "recent" else "ASC"
    inner = f"SELECT pgl.* {source} WHERE {where} ORDER BY g.date {direction}, pgl.event_id LIMIT {int(n)}"
    # Re-expose the game columns under their alias so a reader's `g.` works.
    return f"FROM ({inner}) pgl JOIN games g ON g.event_id = pgl.event_id AND g.season = pgl.season", params


def aggregate_sql(narrowed: Narrowed, selects: list[str], *, rebuilt: bool = False) -> tuple[str, list[Any]]:
    """One row of aggregates over the narrowed games: averages, totals, a count,
    a record - over the window, where one is set.

    .. versionchanged:: 4.4.0
       Honors :attr:`Narrowed.window`.
    """
    source, params = _windowed(narrowed, rebuilt=rebuilt)
    return f"SELECT {', '.join(selects)} {source}", params


def games_subquery(narrowed: Narrowed, box: BoxSource) -> tuple[str, list[Any]]:
    """The narrowed games as a subquery with the result columns the condition
    readers wrap: ``won``, ``home_away``, ``team_score``, ``opponent_score``,
    the Eastern ``day`` and the raw ``stamp`` beside every ``pgl`` column.

    This is what :func:`association.query.conditions._player_games` renders
    for the readers that group a player's games by a condition (splits, a
    record above a threshold, a streak, with/without) - the same relation, the
    same guard, positional parameters instead of named ones so a
    :class:`Narrowed` can feed it. ``SELECT * REPLACE`` blanks the columns a
    rebuilt line cannot be trusted for, exactly as that reader does.

    .. versionadded:: 4.4.0

    .. versionchanged:: 4.4.0
       Honors :attr:`Narrowed.window` (step 3, C5), through the same
       :func:`_windowed` reader :func:`aggregate_sql`/:func:`grouped_sql`
       already use - a window is cut, with the played guard already applied,
       before this subquery's own columns are computed on top of it. A no-op
       for every caller that never sets ``window`` (``player_splits``,
       ``streak``, ``with_without``, ``record_when``): ``_windowed`` returns
       the identical ``FROM ... WHERE`` this function built by hand when
       ``window`` is ``None``.
    """
    source, params = _windowed(narrowed, rebuilt=box.rebuilt)
    ungated = [c for c in UNGATED_ON_REBUILD if c in box.columns]
    replacements = ", ".join(column("pgl", c, box) for c in ungated)
    blanked = f" REPLACE ({replacements})" if box.rebuilt and ungated else ""
    sql = f"""
        SELECT pgl.*{blanked}, g.date AS stamp, {eastern_date_sql("g.date")} AS day, g.winner_team_id = pgl.team_id AS won,
               CASE WHEN g.home_team_id = pgl.team_id THEN 'home' ELSE 'away' END AS home_away,
               CASE WHEN g.home_team_id = pgl.team_id THEN g.home_score ELSE g.away_score END AS team_score,
               CASE WHEN g.home_team_id = pgl.team_id THEN g.away_score ELSE g.home_score END AS opponent_score
        {source}"""
    return sql, params


def named(sql: str, params: list[Any], prefix: str = "r") -> tuple[str, dict[str, Any]]:
    """``sql`` with each positional ``?`` renamed ``$<prefix><n>``, and the
    parameters as that dict - for a reader that nests the same subquery more
    than once, or binds names of its own beside it. A ``?`` can appear only
    once in a statement; a name can be reused, and DuckDB will not mix the two
    in one statement. Only the relation's own SQL is rewritten, never a value.

    .. versionadded:: 4.4.0
    """
    parts = sql.split("?")
    if len(parts) - 1 != len(params):
        raise ValueError(f"{len(parts) - 1} placeholders for {len(params)} parameters")
    out = parts[0]
    for i, part in enumerate(parts[1:]):
        out += f"${prefix}{i}" + part
    return out, {f"{prefix}{i}": v for i, v in enumerate(params)}


def grouped_sql(
    narrowed: Narrowed,
    group_by: str,
    selects: list[str],
    *,
    having: str | None = None,
    order: str | None = None,
    limit: int | None = None,
    rebuilt: bool = False,
    ties: tuple[str, str] | None = None,
) -> tuple[str, list[Any]]:
    """Aggregates per group - a ranking of players, a split by venue or month.
    A ``limit`` here is applied after grouping (the top N groups), never to
    the rows: "top 20 by average" and "the last 20 games, averaged" are
    different questions and only the second is a row window. Groups ranked
    by a figure (``ties``) keep a tie at the limit (:func:`ordered_cut`).

    .. versionchanged:: 7.0.0
       Takes ``ties``.
    """
    source, params = _windowed(narrowed, rebuilt=rebuilt)
    sql = f"SELECT {', '.join(selects)} {source} GROUP BY {group_by}"
    if having:
        sql += f" HAVING {having}"
    if order and ties is not None and limit is not None:
        # The ranked figure is a select's alias (``"points"``), which a
        # window in the same statement would read as the column it shadows:
        # the groups are ranked from outside.
        return f"SELECT * FROM ({sql}) grouped" + ordered_cut(order, limit, ties), params
    if order:
        sql += f" ORDER BY {order}"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return sql, params


def _period_gap_selects(column: str) -> list[str]:
    """Beside a league-wide period read's per-player figures, what the plays
    miss of each player's ``column`` (:data:`PERIOD_GAPS`): his games the
    completeness table checks, those whose play-by-play does not add up, the
    bound on his total (the sum of the gaps' sizes), and the same two counts
    over every player the read keeps - the pool a refusal's share is read
    over (:data:`PERIOD_MISSING_ABOVE`)."""
    gap = f"pgl.{PERIOD_GAPS}[{PERIOD_COLUMNS.index(column) + 1}]"
    missing = f"COUNT(*) FILTER (WHERE {gap} <> 0)"
    return [
        f"COUNT(pgl.{PERIOD_GAPS}) AS checked",
        f"{missing} AS missing",
        f"COALESCE(SUM(ABS({gap})), 0) AS bound",
        f"SUM(COUNT(pgl.{PERIOD_GAPS})) OVER () AS pool_checked",
        f"SUM({missing}) OVER () AS pool_missing",
    ]


def period_ranking_sql(narrowed: Narrowed, measure: str, *, minimum: int, limit: int, rebuilt: bool) -> tuple[str, list[Any]]:
    """(name, games, total, average, then :func:`_period_gap_selects`) per
    player with ``minimum`` games or more in the period-narrowed relation,
    best average first - the league's ranking by one quarter's or half's
    ``measure``, whose played guard and covered-games rule are the
    denominator.

    .. versionadded:: 5.0.0

    .. versionchanged:: 7.0.0
       Each player's gap against his box score beside his figure.
    """
    return grouped_sql(
        narrowed,
        "pgl.athlete_id, pgl.player_name",
        ["pgl.player_name", "COUNT(*) AS games", f"SUM(pgl.{measure}) AS total", f"AVG(pgl.{measure}) AS average", *_period_gap_selects(measure)],
        having=f"COUNT(*) >= {int(minimum)}",
        order="average DESC, games DESC, pgl.player_name",
        limit=limit,
        rebuilt=rebuilt,
    )


def period_quarter_sql(narrowed: Narrowed, *, minimum: int, rebuilt: bool) -> tuple[str, list[Any]]:
    """(athlete, name, games, average points, then
    :func:`_period_gap_selects`) per player with ``minimum`` games or more
    in one quarter of the period-narrowed relation - one of the four reads
    the league's points-by-quarter table joins by player.

    .. versionadded:: 5.0.0

    .. versionchanged:: 7.0.0
       Each player's gap against his box score beside his figure.
    """
    selects = ["pgl.athlete_id", "pgl.player_name", "COUNT(*)", "AVG(pgl.points)", *_period_gap_selects("points")]
    return grouped_sql(narrowed, "pgl.athlete_id, pgl.player_name", selects, having=f"COUNT(*) >= {int(minimum)}", rebuilt=rebuilt)


def most_games_sql(narrowed: Narrowed, *, rebuilt: bool) -> tuple[str, list[Any]]:
    """The most games any one player has in the narrowed relation - what a
    pool narrowed to an opponent or a venue sets its ranking's minimum as a
    share of.

    .. versionadded:: 5.0.0
    """
    return grouped_sql(narrowed, "pgl.athlete_id", ["COUNT(*) AS games"], order="games DESC", limit=1, rebuilt=rebuilt)


def _period_values(con: duckdb.DuckDBPyConnection, statement: tuple[str, list[Any]]) -> list[tuple[Any, ...]]:
    """``statement`` - one of the league's period reads built above - run
    through the compiler's one door
    (:func:`~association.query.compose.core.values_of`), its rows by
    position. At call time: the compiler imports this module, and so does
    the season line, whose ``Statement`` the door takes."""
    from association.query.compose.core import values_of
    from association.query.season_line import Statement

    sql, params = statement
    return values_of(con, Statement(sql, params))


def period_ranking(con: duckdb.DuckDBPyConnection, narrowed: Narrowed, measure: str, *, minimum: int, limit: int, rebuilt: bool) -> list[tuple[Any, ...]]:
    """The league's ranking by one quarter's or half's ``measure``, read:
    (name, games, total, average, checked, missing, bound, pool_checked,
    pool_missing) per player with ``minimum`` games or more in the
    period-narrowed relation, best average first (:func:`period_ranking_sql`).

    .. versionadded:: 6.0.0
    """
    return _period_values(con, period_ranking_sql(narrowed, measure, minimum=minimum, limit=limit, rebuilt=rebuilt))


def period_quarter(con: duckdb.DuckDBPyConnection, narrowed: Narrowed, *, minimum: int, rebuilt: bool) -> list[tuple[Any, ...]]:
    """One quarter of the league's points-by-quarter table, read: (athlete,
    name, games, average points, checked, missing, bound, pool_checked,
    pool_missing) per player with ``minimum`` games or more in the
    quarter-narrowed relation (:func:`period_quarter_sql`).

    .. versionadded:: 6.0.0
    """
    return _period_values(con, period_quarter_sql(narrowed, minimum=minimum, rebuilt=rebuilt))


def most_games(con: duckdb.DuckDBPyConnection, narrowed: Narrowed, *, rebuilt: bool) -> int:
    """The most games any one player has in the narrowed relation
    (:func:`most_games_sql`), 0 where nobody has one - what a period
    ranking's pool narrowed to an opponent or a venue sets its minimum as a
    share of.

    .. versionadded:: 6.0.0
    """
    row = _period_values(con, most_games_sql(narrowed, rebuilt=rebuilt))
    return int(row[0][0]) if row else 0


# One game of one player's, as the relation holds it: the date, the
# opponent, the venue, the result and the score - the row a single-game
# chart's label is worded from (:mod:`association.query.game_label`). The
# relation's own join, the one ``_PLAYER_GAMES`` writes (keyed on season as
# well as the event, since the phantom 1993 shares its event ids with 1994),
# spelled out as the statement was before it moved here, with no
# played guard: the label names the game the chart was drawn for, and a
# game with no usable row is the label's to fall back on.
_GAME_LABEL_SQL = """
SELECT pgl.game_date, pgl.opponent_abbr,
       g.home_team_id = pgl.team_id AS at_home,
       g.winner_team_id = pgl.team_id AS won,
       CASE WHEN g.home_team_id = pgl.team_id THEN g.home_score ELSE g.away_score END AS team_score,
       CASE WHEN g.home_team_id = pgl.team_id THEN g.away_score ELSE g.home_score END AS opponent_score
FROM player_game_log pgl
JOIN games g ON g.event_id = pgl.event_id AND g.season = pgl.season
WHERE pgl.athlete_id = ? AND pgl.event_id = ?
LIMIT 1
"""


def game_label_row(con: duckdb.DuckDBPyConnection, athlete_id: str, event_id: str) -> tuple[Any, ...] | None:
    """One player's game, read for its label: ``(game_date, opponent_abbr,
    at_home, won, team_score, opponent_score)`` - the raw UTC stamp, the
    opponent's season-correct abbreviation, whether his team was at home and
    won, and the two scores from his team's side - or ``None`` where he has
    no row for the event. Run through the compiler's one door
    (:func:`~association.query.compose.core.values_of`). Raises
    :class:`duckdb.CatalogException` where the warehouse has no
    ``player_game_log`` or ``games``, which the label catches
    (:func:`association.query.game_label.game_label`).

    .. versionadded:: 6.0.0
    """
    from association.query.compose.core import values_of  # at call time: the compiler imports this module
    from association.query.season_line import Statement  # and so does the season line, whose Statement the door takes

    rows = values_of(con, Statement(_GAME_LABEL_SQL, [athlete_id, event_id]))
    return rows[0] if rows else None


def column(alias: str, name: str, box: BoxSource) -> str:
    """A box-score column as a reader may trust it: blanked on a rebuilt row
    when it is one the rebuild fills but was never measured for
    (:data:`association.query.conditions.UNGATED_ON_REBUILD`), so an average is
    taken over the games that carry the figure rather than over a wrong one."""
    if box.rebuilt and name in UNGATED_ON_REBUILD and name in box.columns:
        return f"CASE WHEN {alias}.reconstructed THEN NULL ELSE {alias}.{name} END AS {name}"
    return f"{alias}.{name}"


def paired_rows_sql(narrowed: Narrowed, other_id: str, select: str, *, teammates: bool = False, order: str | None = "g.date DESC, pgl.event_id", rebuilt: bool = False) -> tuple[str, list[Any]]:
    """The pair relation: games the narrowed player and ``other`` both played,
    on opposite teams - or the same, with ``teammates`` - with the other's line
    readable as ``other.<column>``. A matchup is this and nothing more: two
    reads of the relation joined on the event, both under the played guard,
    which is why "never met" can be true of two men who shared a floor for
    years (their games are teammates' games, not meetings)."""
    if narrowed.periods is not None or narrowed.period_condition is not None:
        # A meeting's period line would need the OTHER man's period line
        # too; nothing reads one yet, so this refuses rather than pairing a
        # period with a whole game.
        raise ValueError("the pair relation has no period reading")
    where, params = narrowed.clauses(rebuilt=rebuilt)
    appeared = "(other.minutes IS NOT NULL OR other.reconstructed)" if rebuilt else "other.minutes IS NOT NULL"
    side = "other.team_id = pgl.team_id" if teammates else "other.team_id <> pgl.team_id"
    sql = (
        f"SELECT {select} {_PLAYER_GAMES} JOIN player_game_log other ON other.event_id = pgl.event_id AND other.season = pgl.season "
        f"AND other.athlete_id = ? AND {side} AND NOT other.did_not_play AND {appeared} WHERE {where}"
    )
    if order:
        sql += f" ORDER BY {order}"
    return sql, [other_id, *params]


def scope_without_guard(alias: str, season: int | None, season_type: int) -> tuple[str, list[Any]]:
    """The WHERE clause for one season of box scores, or for a career of them,
    with NO played guard - for counting the rows the guard drops, never for
    answering from them.

    A career starts at the box scores' floor, and that floor is also what keeps
    1993 out: ESPN answers season=1993 with the same games as 1994 (coverage's
    phantom), so a career counted from 1993 counts every 1993-94 game twice -
    26,350 duplicate player-games.

    .. versionchanged:: 4.4.0
       Honors :data:`BOTH_SEASON_TYPES` through :func:`season_type_clause`.
    """
    type_clause, type_params = season_type_clause(f"{alias}.season_type", season_type)
    if season is None:
        return f"{alias}.season >= ? AND {type_clause}", [FLOOR, *type_params]
    return f"{alias}.season = ? AND {type_clause}", [season, *type_params]


def _tenure_clause(con: duckdb.DuckDBPyConnection, mate: Entity, season: int | None, *, side: str = "own") -> tuple[str, list[Any]]:
    """SQL keeping the games ``pgl`` played on a team ``mate`` was on at the
    time - his stints (:func:`~association.query.conditions.stints`, the one
    reading of a teammate's time on a team) - or, with ``side``
    ``"opponent"``, the games played AGAINST a team he was on at the time
    ("vs lakers without lebron": the Lakers games of his Lakers years).
    ``season`` is the one season asked about, or None for a career; the
    span's own clause keeps the games to it, so it only drops the stints
    outside it.

    .. versionchanged:: 5.0.0
       Takes ``side``.

    .. versionchanged:: 7.0.0
       Reads the stints the with/without split reads (ISSUES.md #480): a
       stint ends at the day before his first row for his next team, not at
       his last row for this one.
    """
    from association.query.conditions import stints as read_stints

    spells = read_stints(con, mate.id, COVERAGE["player_game_log"].phantom)
    if season is not None:
        spells = [spell for spell in spells if _tenure_clause_reaches(spell.first, spell.last, season)]
    if not spells:
        return "FALSE", []
    # A stint's Eastern days as the stamps ``pgl.game_date`` holds: from
    # the first day's first stamp up to, not including, the day after the last's.
    bounds = [(spell.team_id, eastern_day_utc_range(spell.first.isoformat())[0], eastern_day_utc_range(spell.last.isoformat())[1]) for spell in spells]
    rows = ", ".join("(?, ?, ?)" for _ in bounds)
    team = "pgl.team_id" if side == "own" else "pgl.opponent_team_id"
    return (
        f"EXISTS (SELECT 1 FROM (VALUES {rows}) AS stint(team_id, start_stamp, end_stamp) WHERE stint.team_id = {team} AND pgl.game_date >= stint.start_stamp AND pgl.game_date < stint.end_stamp)"
    ), [value for bound in bounds for value in bound]


def _tenure_clause_reaches(first: date, last: date, season: int) -> bool:
    """Whether a stint from ``first`` to ``last`` reaches into ``season``
    (named for the year it ends, its games from July of the year before)."""
    return first <= date(season, 6, 30) and last >= date(season - 1, 7, 1)


# Stat key -> real player_box_stats column. A whitelist, not a passthrough:
# a stat key can be model-generated text (the normalizer's stat), and this
# is the only place it can reach SQL.
# Same reasoning as metrics.EXTRA_FIELD_COLUMNS.
THRESHOLD_STAT_COLUMNS = {
    "points": "points",
    "rebounds": "rebounds",
    "assists": "assists",
    "steals": "steals",
    "blocks": "blocks",
    "turnovers": "turnovers",
    "threePointFieldGoalsMade": "threePointFieldGoalsMade",
    "fieldGoalsMade": "fieldGoalsMade",
    "freeThrowsMade": "freeThrowsMade",
    "minutes": "minutes",
    "fouls": "fouls",
}


STAT_LABELS = {
    "points": "point",
    "rebounds": "rebound",
    "assists": "assist",
    "steals": "steal",
    "blocks": "block",
    "turnovers": "turnover",
    "threePointFieldGoalsMade": "3-pointer",
    "fieldGoalsMade": "field goal",
    "freeThrowsMade": "free throw",
    "minutes": "minute",
    "fouls": "foul",
}
