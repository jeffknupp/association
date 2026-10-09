"""The Result: what the RUN stage produces for a question, as values - the
subject, the span the read covered, the narrowings it applied, the parts of
the answer (a table of rows, a figure) and the remarks the sayer has to
make, each as a kind and its facts. The sayer takes a Result and nothing
else, so everything the result carries is said or is visibly dropped
(``ROADMAP.md``, contract 5), and "identical" between two trees is a
comparison of values with the sentence left out (``ROADMAP-TYPES.md``,
"Outcome - what RUN produces").

Declared here by Phase 2's first slice (``ROADMAP.md``, "Phase 2, the
expected steps", step 0: a player's or a team's game log), as the target
types draft says a type is - by the phase that first uses it. What this
first version carries beyond the draft, on purpose and to be cut as the
sayers take it over: the narrowing's phrase and the span's words
(``Narrowing.phrase``, ``Span.years``), which the reader still takes from
the relation's own ``filters()`` and ``years()`` rather than the sayer
building them from cells.

Typed since the Phase 2 review's cleanup ("the Result is typed",
2026-10-05): what a read finds instead of an answer is a :class:`Refusal`
(a cause from :data:`RUN_CAUSES` and its facts) or a :class:`Clarify`,
never a sentence - ``empty`` too, the reason a read with no rows gives;
the cells the read applied are :data:`Cell` values on :class:`Narrowing`
and :class:`Span`; what a shape's sayer needs beside its body is one typed
record per shape (:data:`Facts`), where an untyped mapping was.

.. versionadded:: 5.0.0
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from association.query.notes import Note
from association.query.reading import CAUSES


@dataclass(frozen=True, kw_only=True)
class Span:
    """The seasons a read covered: one season and its type, or a career
    (``career``) whose first and last seasons the rows name, or one dated
    game (``date``). ``years`` is the span's own wording of a range
    ("2024-25 to 2025-26"), the relation's ``_Span.years``, carried until
    the sayer words it from ``first`` and ``last``. ``floor`` is the first
    season a span over every season could reach (the relation's floor),
    which a span with nothing in it is named by ("in any regular season on
    record (1994 onward)"). ``source`` is the relation the span was read
    over: the player's games (``"games"``), or the season line
    (``"seasons"``, one row per season: an unnarrowed line, a history, a
    comparison - :mod:`association.query.season_line`), or a team's own
    season: its line and the standings (``"team_seasons"``) or ESPN's power
    index (``"team_snapshots"``, :mod:`association.query.team_seasons`), or
    ESPN Analytics' NetPoints (``"netpoints"``, a player's season ratings
    and fingerprints, :mod:`association.query.compose.netpoints`), or one
    player's located shots (``"shots"``, the declared shot relation,
    :mod:`association.query.compose.shots`). ``since`` and ``until`` are a
    span cut by seasons the question named ("since 2015", "from 2011 to
    2019"; the draft's ``Span.seasons``), and ``ordinal`` a season named by
    its place in his career ("his 18th season").

    .. versionadded:: 5.0.0

    .. versionchanged:: 5.0.0
       ``since``, ``until`` and ``ordinal``, which ``Result.facts`` carried.
    """

    season: int | None = None
    season_type: int | None = None
    career: bool = False
    date: str | None = None
    first: int | None = None
    last: int | None = None
    years: str | None = None
    phrase: str | None = None
    floor: int | None = None
    since: int | None = None
    until: int | None = None
    ordinal: int | None = None
    source: Literal["games", "seasons", "team_seasons", "team_snapshots", "netpoints", "shots"] = "games"


@dataclass(frozen=True)
class Period:
    """The quarter or half a read sees of each game (``ROADMAP-TYPES.md``,
    ``Reading.period``): as the answer names it (``label``: "1st quarter",
    "2nd half") and by number (``periods``, where the read kept them).

    .. versionadded:: 5.0.0
    """

    label: str
    periods: tuple[int, ...] = ()


@dataclass(frozen=True)
class OnDate:
    """One named day the read was narrowed to (the draft's ``OnDate``).

    .. versionadded:: 5.0.0
    """

    day: str


@dataclass(frozen=True)
class Line:
    """A stat a game reached, missed or equaled (the draft's ``Line``): the
    box-score column, the comparison, the number, and how the answer says
    it (``label``, the relation's own words: "30+ points").

    .. versionadded:: 5.0.0
    """

    column: str | None
    op: str = ">="
    value: Any = None
    label: str = ""


@dataclass(frozen=True)
class Role:
    """How he entered the game (the draft's ``Role``): started, or came off
    the bench.

    .. versionadded:: 5.0.0
    """

    started: bool


@dataclass(frozen=True)
class GameOfSeries:
    """Game ``n`` of a series (the draft's ``GameOfSeries``).

    .. versionadded:: 5.0.0
    """

    n: int


@dataclass(frozen=True)
class Calendar:
    """A calendar narrowing (the draft's ``Calendar``): a month by number,
    or a situation as the reading named it ("on Christmas").

    .. versionadded:: 5.0.0
    """

    month: int | None = None
    situation: str | None = None


@dataclass(frozen=True)
class Companions:
    """Teammates a team's games are divided by (the draft's ``Companion``
    filters, as a with/without split's ``presence`` dimension reads them):
    their names, whether the question asked about their absence
    (``absent``: "without"), and each one's predicate as the split held it
    (``predicates``: who played, sat, started).

    .. versionadded:: 5.0.0
    """

    names: tuple[str, ...]
    absent: bool = False
    predicates: tuple[Any, ...] = ()


@dataclass(frozen=True)
class Met:
    """The games every named subject played in, on opposite sides (the
    draft's ``Met``): a matchup is a comparison with this cell - ``other``
    is the second player.

    .. versionadded:: 5.0.0
    """

    other: str


@dataclass(frozen=True)
class ShotValue:
    """Shots of one value only (the draft's ``ShotValue``): 1, 2 or 3.

    .. versionadded:: 5.0.0
    """

    value: int


Cell = Period | OnDate | Line | Role | GameOfSeries | Calendar | Companions | Met | ShotValue
"""The closed set of cells a read applies beyond the span, typed
(``ROADMAP-TYPES.md``, "Filter - one closed union in place of 39 slots" -
the minimal set the Result needed, declared by the step that moved them
off ``Result.facts``, 2026-10-05). An opponent, a venue and the teammates
absent are :class:`Narrowing`'s own fields, as they were.

.. versionadded:: 5.0.0
"""


@dataclass(frozen=True, kw_only=True)
class Narrowing:
    """The cells the read applied beyond the span, as values and as the
    phrase the relation says them with (``Narrowed.filters()``) - the
    phrase is carried, not built, until each cell has one phrase in the
    sayer (``ROADMAP.md``, contract 4). ``opponent``, ``venue`` and
    ``without`` are the cells every relation narrows by; the rest are
    ``cells``, each a :data:`Cell`, read by type
    (:meth:`cell`, :meth:`lines`).

    .. versionadded:: 5.0.0

    .. versionchanged:: 5.0.0
       ``cells`` holds what ``Result.facts`` carried as untyped keys (the
       period, a date, the lines, a role, a series game, a calendar cut,
       the companions, the pair's other player, a shot value), and
       ``period`` is the :class:`Period` cell among them.
    """

    phrase: str = ""
    opponent: str | None = None
    venue: str | None = None
    without: tuple[str, ...] = ()
    cells: tuple[Cell, ...] = ()

    def cell[C](self, kind: type[C]) -> C | None:
        """The one cell of type ``kind`` the read applied, or None."""
        return next((each for each in self.cells if isinstance(each, kind)), None)

    def lines(self) -> tuple[Line, ...]:
        """Every :class:`Line` the read kept games past, in its order."""
        return tuple(each for each in self.cells if isinstance(each, Line))

    @property
    def period(self) -> Period | None:
        """The quarter or half the read sees of each game, or None for whole games."""
        return self.cell(Period)


@dataclass(frozen=True, kw_only=True)
class Rows:
    """A table of games (or of any one-row-per-item read): ``columns`` in
    display order, ``rows`` as plain mappings, the count the window cut
    them from (``total_before_window``), the per-row figures summed or
    averaged over exactly these rows (``summary``: a log's per-game
    averages, a team log's wins and losses and its total), and how many
    came from each season type where two were merged (``by_season_type``).
    ``by`` is what the rows are in order of, the draft's "rows by date"
    and "rows by the measure": ``"date"`` for a log, or the measure a
    single-game high ranks its games by.

    .. versionadded:: 5.0.0
    """

    columns: tuple[str, ...] = ()
    rows: tuple[Mapping[str, Any], ...] = ()
    total_before_window: int | None = None
    summary: Mapping[str, Any] = field(default_factory=dict)
    by_season_type: Mapping[int, int] = field(default_factory=dict)
    by: str = "date"


@dataclass(frozen=True, kw_only=True)
class Grouped:
    """One row per value of ``by``: a record split by whether a line was
    reached, a splits table by venue, a ranking by player. Each row is a
    plain mapping with its ``key`` and its values.

    ``ranked_by`` is the measure a ranking orders its rows by, the draft's
    "order: by the measure" (``ROADMAP-TYPES.md``, "The shapes") - what tells
    two rankings by ``player`` apart: ``"games"`` for a count of games over a
    line (``threshold_count``'s league, whose rows carry ``games``), a
    season-line metric's name (``"avg_points"``, ``"ts_pct"``) for a stat
    ranking (``leaderboard``, whose rows carry ``rank`` and ``values`` by
    measure, the ranked figure under this name). ``None`` for a group that
    is no ranking (a split, a record over a line, two named subjects).

    .. versionadded:: 5.0.0
    """

    by: str
    rows: tuple[Mapping[str, Any], ...] = ()
    ranked_by: str | None = None
    #: The parameter of ``by`` (the draft's ``Dimension``: ``line(Line)``,
    #: ``presence(of: names)``): the line a split by ``threshold`` divides
    #: the games by, the teammates a split by ``presence`` divides them by.
    of: Line | Companions | None = None


@dataclass(frozen=True, kw_only=True)
class Scalar:
    """The whole narrowed set reduced to one line (``ROADMAP-TYPES.md``,
    "The shapes": ``scalar``): how many ``games`` it held, each measure's
    figure over them (``values``, by measure name - a per-game average, or a
    rate as the ratio of its sums), and the sums the line is said from
    (``sums``: a stat's total by its name, a percentage's ``made`` and
    ``attempted``, a made count's attempts by their column). Declared by the
    first scalar shape to retire its template (``player_stat``'s narrowed
    line), as the draft says a type is.

    ``how`` is how the set was reduced, the draft's ``Measure.how``: each
    measure per game beside its sums (``"per_game"``, a line), or the games
    counted (``"count"``, ``threshold_count``'s one player: ``games`` is the
    figure, ``values`` is empty, and ``sums`` holds how many of the counted
    games are rebuilt from play-by-play, ``rebuilt``), or a team's games
    won and lost (``"record"``, ``team_record``'s: ``values`` holds
    ``wins`` and ``losses`` and what the record's source says beside them),
    or - since the sayer is chosen by the body's type and this field
    (2026-10-05) - a season line read as the season stores it
    (``"season"``), an average over shots (``"per_shot"``), NetPoints
    ratings per 100 possessions (``"per_100"``) or one game's own
    (``"total"``), ESPN's projection of a team's season
    (``"projection"``), a team's record ranked among the league's
    (``"ranked"``).

    .. versionadded:: 5.0.0
    """

    games: int
    values: Mapping[str, Any] = field(default_factory=dict)
    sums: Mapping[str, Any] = field(default_factory=dict)
    how: Literal["per_game", "count", "record", "season", "per_shot", "per_100", "total", "projection", "ranked"] = "per_game"


@dataclass(frozen=True, kw_only=True)
class Run:
    """One run of consecutive games a condition held along (``ROADMAP-TYPES.md``,
    "The shapes": ``runs``): whose it is where a listing names several
    (``owner``; ``None`` for one named subject's own), how many games, the
    first and last day and season, and whether it was still going at the
    owner's last game on record (``still_open``).

    .. versionadded:: 5.0.0
    """

    owner: str | None = None
    length: int
    first: Any
    last: Any
    first_season: int
    last_season: int
    still_open: bool = False


def run_of(row: Mapping[str, Any], owner: str | None = None) -> Run:
    """One row of a run statement - the columns ``conditions._longest_runs_sql``
    and the team compiler's run read return (``length``, ``first_day``,
    ``last_day``, ``first_season``, ``last_season``, ``open``) - as a
    :class:`Run`, under ``owner`` where a listing names several.

    .. versionadded:: 5.0.0
    """
    return Run(
        owner=owner,
        length=row["length"],
        first=row["first_day"],
        last=row["last_day"],
        first_season=row["first_season"],
        last_season=row["last_season"],
        still_open=bool(row["open"]),
    )


@dataclass(frozen=True, kw_only=True)
class Runs:
    """The longest runs a read found, longest first (``ROADMAP-TYPES.md``,
    "The shapes": ``runs``) - a named subject's longest and any that tie
    it, or one per owner over the league. Declared by the first runs shape
    to retire its template (``streak``).

    .. versionadded:: 5.0.0
    """

    runs: tuple[Run, ...] = ()
    #: What the runs held along (the draft: "a ``Line`` or ``Won``"): a line
    #: each game reached, or wins (``won=True``) or losses.
    line: Line | None = None
    won: bool = True


@dataclass(frozen=True, kw_only=True)
class Chart:
    """A drawing (``ROADMAP-TYPES.md``, "The shapes": ``chart`` - "the
    artifact, and the counts it drew"): the artifact's ``kind``, the
    ``made`` and ``attempted`` counts drawn, the marks themselves
    (``marks``, one tuple per mark, as the renderer takes them), the page's
    ``title`` and ``caption`` and the ``file`` name it is written under -
    and ``path``, where it was written, which the RUN stage's draw step
    sets after the read (a reader reads; nothing it returns names a file
    that does not exist yet). No marks is a chart with nothing to draw.
    Declared by the first chart to retire its template (``shot_chart``,
    Phase 2, step 5): the draft's body, rather than a ``Rows`` of shots,
    because what the answer states is the counts and the file, never the
    rows, and the marks are the renderer's input, not a table anybody
    reads.

    .. versionadded:: 5.0.0
    """

    kind: str
    made: int = 0
    attempted: int = 0
    marks: tuple[tuple[Any, ...], ...] = ()
    title: str = ""
    caption: str = ""
    file: str = ""
    path: str | None = None


@dataclass(frozen=True, kw_only=True)
class Decided:
    """One decision the read made where the question left a field open
    (``ROADMAP-TYPES.md``, "Outcome": ``decisions``): the ``kind`` from
    :data:`~association.query.notes.DECISION_KINDS`, the ``field`` it
    settled, what the question typed (``before``), what was chosen and
    what it could have been, and the facts the sentence is made of - never
    the sentence. The sayer phrases it once and records it through
    :func:`~association.query.notes.decided`, as it does a :class:`Note`.

    .. versionadded:: 5.0.0
    """

    kind: str
    field: str
    chose: Any
    before: Any = None
    instead_of: tuple[Any, ...] = ()
    why: str = ""
    # ``dataclasses.field`` by its module: the attribute above named ``field`` shadows the import.
    facts: Mapping[str, Any] = dataclasses.field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class Part:
    """One part of an answer: its role and its body. The first part is the
    headline's.

    .. versionadded:: 5.0.0
    """

    role: Literal["answer", "summary", "detail"] = "answer"
    body: Rows | Grouped | Scalar | Runs | Chart | None = None
    notes: tuple[Note, ...] = ()


@dataclass(frozen=True, kw_only=True)
class Window:
    """The window the read applied after every other narrowing: how many
    rows it kept (``limit``), how many the question asked for (``asked``,
    ``None`` for a default), and which end of the span it took them from.

    .. versionadded:: 5.0.0
    """

    limit: int
    asked: int | None = None
    ascending: bool = False


# --- what a shape's sayer needs beside its body: one typed record per shape ---------


@dataclass(frozen=True, kw_only=True)
class CountFacts:
    """A count of games over a line or a single game's high, besides its
    body: the stat asked about (as the question named it), the first season
    box scores cover, and how many games in the span had an empty box score.

    .. versionadded:: 5.0.0
    """

    stat: str | None
    box_scores_from: int
    empty_box_scores: int


@dataclass(frozen=True, kw_only=True)
class LogFacts:
    """A log of games, besides its rows: the stat whose total a team's log
    states beneath them (``stat``), and whether both season types were
    merged (``mixed``: no season type was named for "last N games").

    .. versionadded:: 5.0.0
    """

    stat: Any = None
    mixed: bool = False


@dataclass(frozen=True, kw_only=True)
class LineFacts:
    """A player's line (over his games, or the season line's), history or
    comparison, besides its body: the stat asked about, the columns shown
    (``wanted``), how many seasons a career line sums (``season_count``)
    and the ordinal season it was read for (``season_n``).

    .. versionadded:: 5.0.0
    """

    stat: Any = None
    wanted: tuple[str, ...] = ()
    season_count: int | None = None
    season_n: int | None = None


@dataclass(frozen=True, kw_only=True)
class SplitsFacts:
    """A player's or a team's splits, besides the groups: the split asked
    for (``split``), the kinds shown, the line's names and headers, how the
    heading counts the games (``counted``) and how many, and the team - the
    one whose games they are, or the one a player's were played for.

    .. versionadded:: 5.0.0
    """

    split: str | None
    kinds: tuple[str, ...]
    line: tuple[tuple[str, str], ...]
    counted: str
    games: int
    team: str | None = None


@dataclass(frozen=True, kw_only=True)
class PeriodFacts:
    """A player's quarter or half, besides its rows or quarters: the
    measure, whether a per-game log was asked (``per_game``) and of his
    whole line (``full_line``), the relation's own words for a teammate's
    role or a calendar cut it applied (``also``), and the games read.

    .. versionadded:: 5.0.0
    """

    stat: str
    per_game: bool = False
    full_line: bool = False
    also: tuple[str, ...] = ()
    games: int = 0


@dataclass(frozen=True, kw_only=True)
class PeriodRankingFacts:
    """A ranking by a quarter or half, besides its rows: the measure, the
    games minimum applied, the most anyone played where the minimum is half
    of it, and how many players qualified.

    .. versionadded:: 5.0.0
    """

    measure: str
    minimum: int
    most: int | None = None
    qualified: int | None = None


@dataclass(frozen=True, kw_only=True)
class TeamPeriodFacts:
    """A team's quarter or half, besides its line and games: the measure,
    which extreme was asked for (``rank``) and the narrowing's own words
    without its date (``dateless``).

    .. versionadded:: 5.0.0
    """

    measure: str
    rank: str | None
    dateless: str


@dataclass(frozen=True, kw_only=True)
class RankingFacts:
    """A ranking of players by a season-line metric, besides its rows: the
    metric's label, its ratio's columns, the columns shown beside it and the
    team whose players it ranks.

    .. versionadded:: 5.0.0
    """

    label: str
    ratio: tuple[str, ...] | None = None
    fields: tuple[str, ...] = ()
    team: str | None = None


@dataclass(frozen=True, kw_only=True)
class RecordFacts:
    """A record over a line, a streak or a with/without split, besides its
    body: the teams it was counted on (``teams``), who the split is about
    (``player``), and for a streak whether it is of wins (``want_win``).

    .. versionadded:: 5.0.0
    """

    teams: tuple[str, ...] = ()
    player: str | None = None
    want_win: bool = True


@dataclass(frozen=True, kw_only=True)
class MatchupFacts:
    """Two players' meetings, besides the comparison: how many games they
    shared as teammates, and the teammate whose absence emptied them.

    .. versionadded:: 5.0.0
    """

    teammate_games: int
    absence: Mapping[str, Any] | None = None


@dataclass(frozen=True, kw_only=True)
class MeetingsFacts:
    """Two teams' meetings, besides the wins: how many games, and a span
    named as a career (``span``).

    .. versionadded:: 5.0.0
    """

    games: int
    span: str | None = None


@dataclass(frozen=True, kw_only=True)
class TeamRecordFacts:
    """A team's record from the game list, besides the tally: why there
    were no games (``none``: the season held none, or the teams never met).

    .. versionadded:: 5.0.0
    """

    none: str | None = None


@dataclass(frozen=True, kw_only=True)
class TeamStatFacts:
    """A team's season numbers, besides the metrics: the one named, the
    games behind them, and why a metric needing points allowed is blank
    (``short``).

    .. versionadded:: 5.0.0
    """

    metric: str | None
    games: int | None = None
    short: Mapping[str, Any] | None = None


@dataclass(frozen=True, kw_only=True)
class TeamRankingFacts:
    """Every team ranked by one metric, besides the rows: the metric, the
    rank word asked, which end comes first, and how many teams were ranked.

    .. versionadded:: 5.0.0
    """

    metric: str
    rank: str | None
    descending: bool
    of: int = 0


@dataclass(frozen=True, kw_only=True)
class OutlookFacts:
    """ESPN's power index for a team, besides its line and chances: the
    snapshot read (its kind, name, update stamp and team count), and every
    snapshot of the season with whether a postseason one was asked.

    .. versionadded:: 5.0.0
    """

    kind: int | None = None
    snapshot: str | None = None
    updated: str | None = None
    teams_in_snapshot: int | None = None
    snapshots: tuple[Mapping[str, Any], ...] = ()
    postseason: bool = False


@dataclass(frozen=True, kw_only=True)
class NetPointsFacts:
    """A player's NetPoints, besides the ratings: whether they are per 100
    possessions, the possessions behind them, and the game read.

    .. versionadded:: 5.0.0
    """

    per_100: bool = False
    possessions: float | None = None
    event_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class ChartFacts:
    """A drawing's settings, besides its marks: a fingerprint's view and
    scale, the span it covers in words (``when``), its players and order,
    the axis note and the league's scale the draw step needs.

    .. versionadded:: 5.0.0
    """

    view: str | None = None
    scale: str | None = None
    when: str | None = None
    players: tuple[str, ...] = ()
    order: str | None = None
    axis_note: str | None = None
    league: Mapping[str, Any] | None = None


Facts = (
    CountFacts
    | LogFacts
    | LineFacts
    | SplitsFacts
    | PeriodFacts
    | PeriodRankingFacts
    | TeamPeriodFacts
    | RankingFacts
    | RecordFacts
    | MatchupFacts
    | MeetingsFacts
    | TeamRecordFacts
    | TeamStatFacts
    | TeamRankingFacts
    | OutlookFacts
    | NetPointsFacts
    | ChartFacts
)
"""What a shape's sayer needs beside the body, the cells, the notes and the
decisions - one typed record per shape, where ``Result.facts`` was an
untyped mapping (51 constructions, about 80 keys; the inventory is in the
step's report). A key that was a cell the read applied went to
:attr:`Narrowing.cells` or :class:`Span`; one that chose the sayer is gone.

.. versionadded:: 5.0.0
"""


RUN_CAUSES: frozenset[str] = frozenset(
    {
        "season_out_of_reach",
        "no_advanced_line",
        "advanced_from_empty_box_scores",
        "name_unmatched",
        "opponent_is_absent",
        "no_such_season_n",
        "period_condition_needs_plays",
        "not_a_teammate",
        "no_game_on_date",
        "box_scores_empty",
        "no_box_scores",
        "no_games_in_span",
        "not_teammates_then",
        "none_matched",
        "listed_not_played",
        "no_player_games",
        "no_team_games",
        "team_none_matched",
        "no_games_in_season",
        "no_team_games_in",
        "free_throw_chart",
        "shot_chart_unseparable",
        "shot_distance_unseparable",
        "fingerprint_on_a_date",
        "period_untrusted",
        "period_unread",
        "team_period_unknown",
        "team_period_unread",
        "team_period_untrusted",
        "period_rank_rate",
        "period_rank_unread",
        "conference_named",
        "teammate_never_seen",
        "never_together",
        "together_outside_span",
        "team_stat_unrecorded",
        "no_team_totals",
        "metric_before_first_season",
        "no_team_record",
        "missed_postseason",
        "no_team_line",
        "no_venue_split",
        "short_of_games",
        "fingerprint_view",
        "fingerprint_scale",
        "fingerprint_pool_empty",
        "fingerprint_no_season",
        "fingerprint_none_for",
        "game_fingerprints_unpulled",
        "game_fingerprint_no_season",
        "game_fingerprint_pool_empty",
        "game_fingerprint_none_for",
    }
)
"""The closed set of causes a READ refuses by (:attr:`Refusal.kind`) - a
fact found in the warehouse, which the words alone could not have told:
a season under a table's floor, a name nothing on record matches. Disjoint
from :data:`~association.query.reading.CAUSES`, the causes a READING
refuses by, which are decided from the question's words before any table
is read and are pinned by the readings population: a kind says which stage
refused. One :class:`Refusal` type carries either, and the sayer has one
phrase per kind of the union (``compose.say.refusal_phrase``).

.. versionadded:: 5.0.0
"""


@dataclass(frozen=True, kw_only=True)
class Refusal:
    """What a read found instead of an answer (``ROADMAP-TYPES.md``,
    "Outcome": ``Refusal(cause, facts)``): the ``kind`` it refuses by - one
    of :data:`RUN_CAUSES`, or a reading's
    :data:`~association.query.reading.CAUSES` the planner hands on - and
    the plain ``facts`` its sentence is made of, never the sentence, which
    the sayer builds (``compose.say.refusal_phrase``). ``shown`` is the
    page's values beside the sentence (the answer's ``data``), and
    ``under`` the keys the page reads the sentence itself under: the two
    are what the readers' own refusals carried before the sentence moved,
    and they vary by reader, so the Refusal says them rather than the
    sayer guessing per kind.

    Some facts are still words the relation phrases (a narrowing's
    ``filters()``, a span's ``during()``), carried as ``Narrowing.phrase``
    and ``Span.years`` are, until the sayer words them from cells.

    .. versionadded:: 5.0.0
    """

    kind: str
    facts: Mapping[str, Any] = field(default_factory=dict)
    shown: Mapping[str, Any] = field(default_factory=dict)
    under: tuple[str, ...] = ("message",)

    def __post_init__(self) -> None:
        """Hold ``kind`` to the union of the two closed sets."""
        if self.kind not in RUN_CAUSES | CAUSES:
            raise ValueError(f"{self.kind!r} is not a cause a read or a reading refuses by")


@dataclass(frozen=True, kw_only=True)
class Clarify:
    """A question back (``ROADMAP-TYPES.md``, "Outcome": ``Clarify``): the
    name as typed (``asked``), the players or teams it could be
    (``candidates``, in the order they are named), what kind of name it is,
    how many of the candidates played in the span asked about (``active``,
    always named), and why it is asked - several on record by that name
    (``"ambiguous"``) or none, with near spellings (``"near_spelling"``).
    ``shown`` and ``under`` as :class:`Refusal`'s, where a reader's page
    held the sentence rather than the candidates.

    .. versionadded:: 5.0.0
    """

    asked: str
    candidates: tuple[str, ...]
    kind: Literal["player", "team"] = "player"
    active: int = 0
    why: Literal["ambiguous", "near_spelling"] = "ambiguous"
    shown: Mapping[str, Any] | None = None
    under: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class Result:
    """What one read produced. ``subject`` names who it is about (a player,
    a team), ``relation`` which relation it read; ``parts`` hold the rows
    or figures, ``notes`` what the sayer must say about the data (kinds and
    facts, never sentences), ``decisions`` what it chose where the question
    left a field open (:class:`Decided`), ``facts`` the typed record a
    shape's sayer needs beside them (:data:`Facts`: a team log's ``stat``
    for its total line). ``empty`` is the :class:`Refusal` a read with no
    rows gives its reason with; the body is there still, with nothing in
    it, so the shape says itself.

    .. versionchanged:: 5.0.0
       ``facts`` is typed per shape and ``empty`` a :class:`Refusal`.

    .. versionadded:: 5.0.0
    """

    subject: str
    relation: Literal["player", "team", "everyone"]
    span: Span = field(default_factory=Span)
    narrowing: Narrowing = field(default_factory=Narrowing)
    window: Window | None = None
    parts: tuple[Part, ...] = ()
    notes: tuple[Note, ...] = ()
    decisions: tuple[Decided, ...] = ()
    facts: Facts | None = None
    empty: Refusal | None = None

    @property
    def rows(self) -> Rows | None:
        """The first part's rows, where the answer is a table of items."""
        body = self.parts[0].body if self.parts else None
        return body if isinstance(body, Rows) else None

    @property
    def scalar(self) -> Scalar | None:
        """The first part's line, where the answer is one row reduced from the whole set."""
        body = self.parts[0].body if self.parts else None
        return body if isinstance(body, Scalar) else None

    @property
    def grouped(self) -> Grouped | None:
        """The first part's groups, where the answer is one row per group."""
        body = self.parts[0].body if self.parts else None
        return body if isinstance(body, Grouped) else None

    @property
    def chart(self) -> Chart | None:
        """The first part's drawing, where the answer is a chart."""
        body = self.parts[0].body if self.parts else None
        return body if isinstance(body, Chart) else None

    @property
    def runs(self) -> Runs | None:
        """The first part's runs, where the answer is the longest runs of consecutive games."""
        body = self.parts[0].body if self.parts else None
        return body if isinstance(body, Runs) else None


Unanswered = Refusal | Clarify
"""What a read returns in place of a :class:`Result`: a refusal naming its
cause, or a question back. ``isinstance(read, Unanswered)`` is how a caller
passes either on unworded.

.. versionadded:: 5.0.0
"""
