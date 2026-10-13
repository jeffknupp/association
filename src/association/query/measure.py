"""The measure: the one catalog of what a question can ask about
(:data:`CATALOG`, keyed by :data:`MeasureKey`), the spellings each key is
named by across the six vocabularies the slot was read against
(:data:`ALIASES`: the normalizer's keys, the measure grammar's, the
compiler's measure names, the leaderboard metrics', the team metrics'
alias texts, the box-score words), and the one tagger that reads the
family (:func:`read_measure`) from the lexicon's words
(:mod:`association.query.lexicon`) alone, and the key's reading of them
(:func:`~association.query.point.measure_under`, since Phase 3, step 4,
slice (b)) over what the words ask, the model's key as context and the
lines read (:class:`MeasureContext`) - into
:class:`~association.query.reading.Measure`, claiming the characters it
read.

Phase 3, step 2's sixth slice. Until it, the measure had SEVEN slots -
``stat``, ``rate``, ``per_game``, ``side``, ``shot_value``, ``fields``,
``kind`` - written by the parser's grammar over the normalizer's key and
by sixteen stages after it (the advanced metrics' words, game score, a
2-point percentage, a ranking by shot distance, the attempts beside a
make, a team metric's alias, a NetPoints rate folded into its key, a team's
total, the side, the shots, a run's kind, the stat the words named beside a
line, and the model's required key dropped where the words named none), and
read on the answer side against six tables that spelled one measure four
ways (``threePointFieldGoalPct``, ``three_pct``, ``three_pt_pct``,
``three_point_pct``). Measured first
(``~/association-research/stages/measure_family.py``, the seven slots as
each stage set them on all 2,710 readings): a stat on 1,004 readings in 69
spellings, 23 the model's and 37 the words', the rest the team metrics'
alias texts; the words and the model's key disagreeing on 58, the words
winning every one; no stage moving a slot between the route and the
reading; a key no answer-side vocabulary held on 24 (``shot_distance`` on
15 - the ranking's refusal, by name - ``games_played`` on 6, which nothing
reads, "rebounds allowed" and "assists allowed", refused by name, and the
grammar's ``points_differential``, which the team reader reads from the
words instead); the family's refusals ``needs_stat`` 7, ``shot_distance_ranking``
6, ``unknown_stat`` 6, ``line_names_no_stat`` 4, ``ranking_unit`` 2,
``no_ranking_measure`` 2, and the planner's "cannot honor ['rate']" on 4.

The catalog's facets are the answer side's vocabularies as they stood:
for each key's own spelling, what ``stat_measure``, ``stat_column``,
``resolve_metric`` and ``resolve_team_metric`` returned
(``tests/query/measure_spellings.json`` holds every reachable spelling's
resolution on the tree before this slice, and a test holds the catalog to
it on the facets each vocabulary reached). Where those four disagreed
between two spellings of one key - a team metric's alias text had no game
column, a MEASURE_WORDS abbreviation no ranking metric - the key's own
spelling decides, since the alias texts reached only the team readers and
the abbreviations only the line readers.

.. versionadded:: 6.0.0
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from association.nba.netpoints import FINGERPRINT_CATEGORIES, FINGERPRINT_SIDE_LABELS
from association.query import lexicon
from association.query.reading import (
    Claim,
    Measure,
    MeasureHow,
    MeasureSide,
    MeasureWhose,
)

if TYPE_CHECKING:
    import re

# --- The catalog ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class MeasureSpec:
    """One measure: what it is called and how each relation reads it.

    ``measure`` is the compiler's name for it over the games relation (a
    game-log column or a derived measure; ``stat_measure`` until Phase 3,
    step 2), ``column`` the stored game-log column alone (``stat_column``:
    a percentage has none, it is made over attempted). ``metric`` is the
    ranking's metric for a bare name (the record books' reading: a scoring
    title is per game, a make is a season count), ``career_metric`` the
    same over a career (a list of totals), ``total_metric`` the season
    total asked for outright, ``per_game_metric`` the per-game figure asked
    for outright. ``team`` is the team metric the team-season relation ranks
    and lists it by, ``team_opponent`` the metric for what a team gives up
    of it ("points allowed").

    .. versionadded:: 6.0.0
    """

    label: str
    measure: str | None = None
    column: str | None = None
    metric: str | None = None
    career_metric: str | None = None
    total_metric: str | None = None
    per_game_metric: str | None = None
    team: str | None = None
    team_opponent: str | None = None


def _box(key: str, label: str, *, metric: str, career: str, total: str | None, team: str | None, team_opponent: str | None = None) -> MeasureSpec:
    """A box-score column: its own name on the games relation, ranked per game or as a total."""
    per_game = metric if metric.startswith("avg_") else None
    return MeasureSpec(label=label, measure=key, column=key, metric=metric, career_metric=career, total_metric=total, per_game_metric=per_game, team=team, team_opponent=team_opponent)


def _pct(label: str, measure: str, metric: str, team: str) -> MeasureSpec:
    """A shooting percentage: made over attempted, never a stored column."""
    return MeasureSpec(label=label, measure=measure, metric=metric, career_metric=metric, team=team)


MeasureKey = Literal[
    "points", "rebounds", "assists", "steals", "blocks", "turnovers", "fouls", "minutes",
    "fieldGoalsMade", "fieldGoalsAttempted", "fieldGoalPct", "threePointFieldGoalsMade", "threePointFieldGoalsAttempted", "threePointFieldGoalPct",
    "twoPointFieldGoalPct", "freeThrowsMade", "freeThrowsAttempted", "freeThrowPct", "offensiveRebounds", "defensiveRebounds",
    "ts_pct", "efg_pct", "usage_pct", "game_score", "plusMinus", "double_double", "triple_double", "fouled_out", "pra", "netpoints",
    "wins", "losses", "record", "games_played", "shot_distance", "point_differential", "pace", "offensive_rating", "defensive_rating", "net_rating",
    "points_in_paint", "fast_break_points",
]  # fmt: skip
"""The closed set of measure keys - :data:`CATALOG`'s keys, which a test holds
equal (the catalog is keyed by ``str`` so a Measure's ``key`` indexes it).

.. versionadded:: 6.0.0
"""

CATALOG: dict[str, MeasureSpec] = {
    "points": _box("points", "points", metric="avg_points", career="total_points", total="total_points", team="points", team_opponent="opponent_points"),
    "rebounds": _box("rebounds", "rebounds", metric="avg_rebounds", career="total_rebounds", total="total_rebounds", team="rebounds"),
    "assists": _box("assists", "assists", metric="avg_assists", career="total_assists", total="total_assists", team="assists"),
    "steals": _box("steals", "steals", metric="avg_steals", career="total_steals", total="total_steals", team="steals"),
    "blocks": _box("blocks", "blocks", metric="avg_blocks", career="total_blocks", total="total_blocks", team="blocks"),
    "turnovers": _box("turnovers", "turnovers", metric="avg_turnovers", career="total_turnovers", total="total_turnovers", team="turnovers"),
    # Minutes and fouls have no career-total metric, so a career ranking stays per game.
    "fouls": _box("fouls", "fouls", metric="avg_fouls", career="avg_fouls", total=None, team="fouls"),
    "minutes": _box("minutes", "minutes", metric="avg_minutes", career="avg_minutes", total=None, team=None),
    # A make is a season COUNT by name ("most threes this season" is the 402-three kind of record).
    "fieldGoalsMade": _box("fieldGoalsMade", "field goals", metric="total_field_goals_made", career="total_field_goals_made", total="total_field_goals_made", team="field_goals_made"),
    "fieldGoalsAttempted": MeasureSpec(label="field goal attempts", measure="fieldGoalsAttempted", column="fieldGoalsAttempted"),
    "fieldGoalPct": _pct("field-goal percentage", "fg_pct", "fg_pct", "field_goal_pct"),
    "threePointFieldGoalsMade": _box(
        "threePointFieldGoalsMade", "3-pointers", metric="total_three_pointers_made", career="total_three_pointers_made", total="total_three_pointers_made", team="three_pointers_made"
    ),
    "threePointFieldGoalsAttempted": MeasureSpec(label="3-point attempts", measure="threePointFieldGoalsAttempted", column="threePointFieldGoalsAttempted", team="three_pointers_attempted"),
    "threePointFieldGoalPct": _pct("3-point percentage", "three_pct", "three_pt_pct", "three_point_pct"),
    # No stored 2-point column anywhere: the season line computes it from the
    # field goals less the threes (season_line.HISTORY_COLUMNS, SHOOTING_STATS),
    # and the games relation has no reading of it by name yet (the compiler's
    # `two_pct` is reached from the words alone) - so no measure, no metric.
    "twoPointFieldGoalPct": MeasureSpec(label="2-point percentage"),
    "freeThrowsMade": _box("freeThrowsMade", "free throws", metric="total_free_throws_made", career="total_free_throws_made", total="total_free_throws_made", team="free_throws_made"),
    "freeThrowsAttempted": MeasureSpec(label="free throw attempts", measure="freeThrowsAttempted", column="freeThrowsAttempted", team="free_throws_attempted"),
    "freeThrowPct": _pct("free-throw percentage", "ft_pct", "ft_pct", "free_throw_pct"),
    "offensiveRebounds": MeasureSpec(label="offensive rebounds", measure="offensiveRebounds", column="offensiveRebounds", team="offensive_rebounds"),
    "defensiveRebounds": MeasureSpec(label="defensive rebounds", measure="defensiveRebounds", column="defensiveRebounds", team="defensive_rebounds"),
    # The computed advanced stats: their own table on the season line, a
    # per-game column the compiler averages, and a ranking metric each.
    "ts_pct": MeasureSpec(label="true shooting percentage", measure="ts_pct", column="ts_pct", metric="ts_pct", career_metric="ts_pct", team="true_shooting_pct"),
    "efg_pct": MeasureSpec(label="effective field goal percentage", measure="efg_pct", column="efg_pct", metric="efg_pct", career_metric="efg_pct", team="effective_fg_pct"),
    "usage_pct": MeasureSpec(label="usage rate", measure="usage_pct", column="usage_pct", metric="usage_pct", career_metric="usage_pct"),
    # Game score ranks only by its per-game name (`avg_game_score`), the one
    # the stages wrote under a ranking; the bare key names a player's line.
    "game_score": MeasureSpec(label="game score", measure="game_score", column="game_score", per_game_metric="avg_game_score"),
    # A team's plus-minus is its point differential.
    "plusMinus": MeasureSpec(label="plus-minus", measure="plusMinus", column="plusMinus", team="point_differential"),
    "double_double": MeasureSpec(label="double-doubles", measure="double_double", metric="double_doubles", career_metric="double_doubles"),
    "triple_double": MeasureSpec(label="triple-doubles", measure="triple_double", metric="triple_doubles", career_metric="triple_doubles"),
    "fouled_out": MeasureSpec(label="foul-outs", measure="fouled_out"),
    "pra": MeasureSpec(label="points, rebounds and assists", measure="pra"),
    # NetPoints: one key, the side and the rate on the Measure, the metric
    # by the pair (NETPOINTS_METRICS); no game column.
    "netpoints": MeasureSpec(label="NetPoints", metric="netpoints_total", career_metric="netpoints_total"),
    # A team's wins: a game's `won` flag on the games relation, the record on the team's.
    "wins": MeasureSpec(label="wins", measure="won", team="record"),
    "losses": MeasureSpec(label="losses", team="losses"),
    "record": MeasureSpec(label="record", team="record"),
    "games_played": MeasureSpec(label="games played"),
    # A sentinel no relation reads: the ranking refuses it by name (shot_distance_ranking).
    "shot_distance": MeasureSpec(label="shot distance"),
    "point_differential": MeasureSpec(label="point differential", team="point_differential"),
    "pace": MeasureSpec(label="pace", team="pace"),
    "offensive_rating": MeasureSpec(label="offensive rating", team="offensive_rating"),
    "defensive_rating": MeasureSpec(label="defensive rating", team="defensive_rating"),
    "net_rating": MeasureSpec(label="net rating", team="net_rating"),
    "points_in_paint": MeasureSpec(label="points in the paint", team="points_in_paint"),
    "fast_break_points": MeasureSpec(label="fast-break points", team="fast_break_points"),
}
"""Every measure a question can ask about, by key (:data:`MeasureKey`).

.. versionadded:: 6.0.0
"""


NETPOINTS_METRICS: dict[tuple[MeasureSide | None, MeasureHow | None], str] = {
    (None, None): "netpoints_total",
    ("offense", None): "netpoints_offense",
    ("defense", None): "netpoints_defense",
    (None, "per_100"): "netpoints_per_100",
    ("offense", "per_100"): "netpoints_offense_per_100",
    ("defense", "per_100"): "netpoints_defense_per_100",
}
"""The ranking metric a NetPoints measure names, by its side and rate.

.. versionadded:: 6.0.0
"""

#: The ranking metric a NetPoints metric name folds, by name: the spelling
#: the stages wrote (and the normalizer's keys) to the measure's side and rate.
_NETPOINTS_NAMES: dict[str, tuple[MeasureSide | None, MeasureHow | None]] = {metric: pair for pair, metric in NETPOINTS_METRICS.items()}
_NETPOINTS_NAMES["netpoints"] = (None, None)

Alias = tuple[str, MeasureWhose, MeasureSide | None, MeasureHow | None, str | None]
"""What a spelling resolves to: the key, whose figure, a side, a rate and a
NetPoints category folded into the name.

.. versionadded:: 6.0.0
"""

_SIDE_CODES: dict[str, str] = {label: code for code, label in FINGERPRINT_SIDE_LABELS.items()}
#: A fingerprint metric's name (``assist_o_net_pts``) to its category and
#: side - the 66 names the ranking lists, each a NetPoints measure of one
#: play type on one side of the ball.
_FINGERPRINT_NAMES: dict[str, tuple[str, MeasureSide]] = {
    f"{prefix}_{code}_net_pts": (prefix, label)  # type: ignore[misc]
    for prefix in FINGERPRINT_CATEGORIES.values()
    for code, label in FINGERPRINT_SIDE_LABELS.items()
}

#: Each team metric's key to the measure it is of, and whose.
_TEAM_METRIC_MEASURES: dict[str, tuple[str, MeasureWhose]] = {
    "record": ("record", "own"),
    "losses": ("losses", "own"),
    "points": ("points", "own"),
    "opponent_points": ("points", "opponent"),
    "point_differential": ("point_differential", "own"),
    "pace": ("pace", "own"),
    "offensive_rating": ("offensive_rating", "own"),
    "defensive_rating": ("defensive_rating", "own"),
    "net_rating": ("net_rating", "own"),
    "field_goal_pct": ("fieldGoalPct", "own"),
    "three_point_pct": ("threePointFieldGoalPct", "own"),
    "free_throw_pct": ("freeThrowPct", "own"),
    "true_shooting_pct": ("ts_pct", "own"),
    "effective_fg_pct": ("efg_pct", "own"),
    "rebounds": ("rebounds", "own"),
    "offensive_rebounds": ("offensiveRebounds", "own"),
    "defensive_rebounds": ("defensiveRebounds", "own"),
    "assists": ("assists", "own"),
    "turnovers": ("turnovers", "own"),
    "steals": ("steals", "own"),
    "blocks": ("blocks", "own"),
    "fouls": ("fouls", "own"),
    "three_pointers_made": ("threePointFieldGoalsMade", "own"),
    "three_pointers_attempted": ("threePointFieldGoalsAttempted", "own"),
    "field_goals_made": ("fieldGoalsMade", "own"),
    "free_throws_made": ("freeThrowsMade", "own"),
    "free_throws_attempted": ("freeThrowsAttempted", "own"),
    "points_in_paint": ("points_in_paint", "own"),
    "fast_break_points": ("fast_break_points", "own"),
}


def _aliases() -> dict[str, Alias]:
    """Every spelling a measure is named by, to what it names - built once."""
    found: dict[str, Alias] = {key: (key, "own", None, None, None) for key in CATALOG}
    # The box-score words (the words after a line's number, and the parser's
    # word table), each to its column.
    for word, column in lexicon.MEASURE_WORDS.items():
        found[word] = (column, "own", None, None, None)
    # The team metrics' alias texts, each to the measure its metric is of.
    for team_key, texts in lexicon.TEAM_METRIC_WORDS.items():
        of_key, of_whose = _TEAM_METRIC_MEASURES[team_key]
        for text in texts:
            found.setdefault(text, (of_key, of_whose, None, None, None))
    # The NetPoints family: the normalizer's keys and the grammar's, one key
    # with the side and the rate on the Measure; a fingerprint metric's name
    # with its category.
    for name, (side, how) in _NETPOINTS_NAMES.items():
        found[name] = ("netpoints", "own", side, how, None)
    for name, (category, side) in _FINGERPRINT_NAMES.items():
        found[name] = ("netpoints", "own", side, None, category)
    # The grammar's own spellings, the normalizer's, the compiler's derived
    # names, the ranking's metric names where the model or a stage wrote one.
    found.update(
        {
            "points_allowed": ("points", "opponent", None, None, None),
            "points allowed": ("points", "opponent", None, None, None),
            "rebounds allowed": ("rebounds", "opponent", None, None, None),
            "assists allowed": ("assists", "opponent", None, None, None),
            "threes allowed": ("threePointFieldGoalsMade", "opponent", None, None, None),
            "points_differential": ("point_differential", "own", None, None, None),
            "plus_minus": ("plusMinus", "own", None, None, None),
            "avg_game_score": ("game_score", "own", None, "per_game", None),
            "true_shooting": ("ts_pct", "own", None, None, None),
            "usage": ("usage_pct", "own", None, None, None),
            "won": ("wins", "own", None, None, None),
            "fg_pct": ("fieldGoalPct", "own", None, None, None),
            "three_pct": ("threePointFieldGoalPct", "own", None, None, None),
            "three_pt_pct": ("threePointFieldGoalPct", "own", None, None, None),
            "three_point_pct": ("threePointFieldGoalPct", "own", None, None, None),
            "ft_pct": ("freeThrowPct", "own", None, None, None),
            "two_pct": ("twoPointFieldGoalPct", "own", None, None, None),
            "margin": ("point_differential", "own", None, None, None),
            "double_doubles": ("double_double", "own", None, None, None),
            "triple_doubles": ("triple_double", "own", None, None, None),
            "points_per_game": ("points", "own", None, "per_game", None),
            "rebounds_per_game": ("rebounds", "own", None, "per_game", None),
            "assists_per_game": ("assists", "own", None, "per_game", None),
            "games": ("games_played", "own", None, None, None),
            "game": ("games_played", "own", None, None, None),
            "gp": ("games_played", "own", None, None, None),
            "gamesPlayed": ("games_played", "own", None, None, None),
        }
    )
    for name, spec in CATALOG.items():
        if spec.per_game_metric:
            found[spec.per_game_metric] = (name, "own", None, "per_game", None)
        if spec.total_metric:
            found[spec.total_metric] = (name, "own", None, "total", None)
    # A team metric's word qualified as given up ("scoring allowed", the
    # tagger's "<alias> allowed") names a measure no vocabulary held: the
    # four the grammar writes are above, and the rest are refused by name,
    # as they were.
    return found


ALIASES: dict[str, Alias] = _aliases()
"""Every spelling a measure is named by - across the six vocabularies - to
the key it names, whose figure, and a side or rate the name folds in.

.. versionadded:: 6.0.0
"""


def key_of(spelling: str | None) -> Alias | tuple[None, MeasureWhose, None, None, None]:
    """What ``spelling`` names: the catalog key, whose figure, a side, a
    rate and a category folded into the name - or no key, where no
    vocabulary holds it (a refusal then names the spelling).

    .. versionadded:: 6.0.0
    """
    if spelling is None or not spelling.strip():
        return (None, "own", None, None, None)
    found = ALIASES.get(spelling)
    if found is None:
        found = ALIASES.get(spelling.strip().casefold())
    if found is None:
        # A camelCase key the team metrics spelled apart ("fieldGoalsMade"
        # read as "field goals made"), or an underscored one.
        found = ALIASES.get(" ".join(lexicon.CAMEL_CASE_BREAK.sub(" ", spelling).replace("_", " ").replace("-", " ").casefold().split()))
    return found if found is not None else (None, "own", None, None, None)


def measure_of(spelling: str, **fields: object) -> Measure:
    """The :class:`~association.query.reading.Measure` ``spelling`` names,
    through the catalog - a test's and a caller's way to build one from a
    key or an alias; ``fields`` are the Measure's other fields.

    .. versionadded:: 6.0.0
    """
    if not spelling.strip():
        return Measure(**fields)  # type: ignore[arg-type]
    key, whose, side, how, category = key_of(spelling)
    values: dict[str, object] = {"key": key, "as_typed": spelling, "whose": whose, "side": side, "how": how, "category": category, **fields}
    return Measure(**values)  # type: ignore[arg-type]


def spec_of(measure: Measure | None) -> MeasureSpec | None:
    """``measure``'s catalog entry, or None for no measure, one no catalog
    holds, or the opponent's figure of one (which no player-side facet reads).

    .. versionadded:: 6.0.0
    """
    if measure is None or measure.key is None:
        return None
    return CATALOG[measure.key]


def measure_name(measure: Measure | None) -> str | None:
    """The compiler's name for ``measure`` over the games relation - a
    game-log column or a derived measure - or None (``stat_measure`` until
    Phase 3, step 2).

    .. versionadded:: 6.0.0
    """
    spec = spec_of(measure)
    return spec.measure if spec is not None and measure is not None and measure.whose == "own" else None


_BY_MEASURE_NAME: dict[str, str] = {spec.measure: key for key, spec in CATALOG.items() if spec.measure is not None}
# The catalog's keys by their compiler name over the games relation: one key
# each (a test holds the names distinct), so a name reads back to one Measure.


def measured(name: str) -> Measure:
    """The catalog's :class:`~association.query.reading.Measure` the
    compiler names ``name`` over the games relation (:func:`measure_name`'s
    inverse: ``"fg_pct"`` is ``fieldGoalPct``, ``"won"`` is ``wins``, a
    box-score column its own key) - how the point reader states the measures
    its point reads, typed, from the words and lines it reads them by
    (``Reading.measures``, Phase 4, step 3). A name no key reads by
    (``"margin"``, a label of a game) raises: a point reads only what a
    question can ask about.

    .. versionadded:: 6.0.0
    """
    key = _BY_MEASURE_NAME.get(name)
    if key is None:
        raise KeyError(f"no catalog measure is read as {name!r} on the games relation")
    return Measure(key=key)


def column_name(measure: Measure | None) -> str | None:
    """The stored game-log column ``measure`` is read from, or None
    (``stat_column`` until Phase 3, step 2).

    .. versionadded:: 6.0.0
    """
    spec = spec_of(measure)
    return spec.column if spec is not None and measure is not None and measure.whose == "own" else None


def metric_name(measure: Measure | None, *, career: bool = False) -> str | None:
    """The ranking metric ``measure`` names (``resolve_metric`` until Phase
    3, step 2): a NetPoints measure by its side and rate
    (:data:`NETPOINTS_METRICS`); a figure asked per game or as a total by
    that form; a bare name by the record books' reading - per game for a
    scoring, rebounding, assist, steal or block title, a season count for a
    make - and over a ``career`` by the career list's (totals). A metric
    name spelled outright keeps its own form ("avg_points" over a career is
    still the average).

    .. versionadded:: 6.0.0
    """
    spec = spec_of(measure)
    if spec is None or measure is None or measure.whose != "own":
        return None
    if measure.key == "netpoints":
        if measure.category is not None and measure.side is not None:
            return f"{measure.category}_{_SIDE_CODES[measure.side]}_net_pts"
        # The per-100 form is the one rate NetPoints has; any other unit asked
        # is the ranking's refusal by name, over the metric the key names.
        return NETPOINTS_METRICS.get((measure.side, "per_100" if measure.how == "per_100" else None))
    bare = spec.career_metric if career else spec.metric
    if measure.how == "per_game":
        return spec.per_game_metric or bare
    if measure.how == "total":
        # A season total asked of a metric with no total form (fouls per
        # game) keeps the form it has, as the ranking's reader read it.
        return spec.total_metric or bare
    return bare


def team_metric_name(measure: Measure | None) -> str | None:
    """The team metric ``measure`` names (``resolve_team_metric`` until
    Phase 3, step 2): the measure's own, or the given-up form for the
    opponent's figure - None for no measure and for one the team-season
    relation has no metric for alike.

    .. versionadded:: 6.0.0
    """
    spec = spec_of(measure)
    if spec is None or measure is None:
        return None
    return spec.team if measure.whose == "own" else spec.team_opponent


def named_by_a_team_metric(measure: Measure | None) -> bool:
    """Whether ``measure`` was written as a team metric's alias text (the
    reading of "fgm", "ppg", "defensive rating" under a team's keys) rather
    than by its own key: the team compiler's total reads a measure by its
    key alone, as the router stages' alias text never matched a
    column name (ISSUES.md, "A team total named by a metric's alias").

    .. versionadded:: 6.0.0
    """
    return measure is not None and measure.as_typed is not None and measure.as_typed in lexicon.STAT_ALIASES and measure.as_typed not in CATALOG


# --- The words: what a question names ---------------------------------------------------------


def named(question: str) -> tuple[str, Claim] | None:
    """The measure ``question``'s own words name - the grammar's spelling
    (:data:`~association.query.lexicon.MEASURE_GRAMMAR`, first match wins)
    or the longest box-score word (:data:`~association.query.lexicon.MEASURE_WORDS`)
    - with the claim of the characters it read, or None. Read before the
    normalizer's key, which is a guess the words correct.

    .. versionadded:: 6.0.0
       ``parse.measure`` until Phase 3, step 2.
    """
    for pattern, key in lexicon.MEASURE_GRAMMAR:
        match = pattern.search(question)
        if match is not None:
            return key, Claim(match.start(), match.end(), "measure")
    low = question.lower()
    words = " " + lexicon.MEASURE_WORD_SPLIT.sub(" ", low) + " "
    hits = [(w, c) for w, c in lexicon.MEASURE_WORDS.items() if f" {w} " in words and (w not in lexicon.MEASURE_ORDINARY_WORDS or _in_capitals(question, w))]
    if not hits:
        return None
    word, column = max(hits, key=lambda x: len(x[0]))
    found = lexicon.MEASURE_WORD_AT[word].search(low)
    if found is None:
        # A word the split found between characters its pattern breaks on
        # ("3pt/3pa"): read, with no characters to claim for it.
        return column, Claim(0, 0, "measure")
    return column, Claim(found.start(), found.end(), "measure")


def _in_capitals(question: str, word: str) -> bool:
    """Whether ``word`` (an abbreviation that is also an ordinary word) is written in capitals in ``question``."""
    upper = word.upper()
    return any(token in (upper, upper + "S") for token in lexicon.MEASURE_TOKEN.findall(question))


def names_a_stat(question: str) -> bool:
    """Whether the question asked about a particular stat at all
    (:data:`~association.query.lexicon.STAT_WORDS`) - the test the model's
    required key is dropped by where the words name none: "compare sga and
    embiid" comes back with ``stat='points'`` 12 times out of 12.

    .. versionadded:: 6.0.0
       ``router._named_a_stat`` until Phase 3, step 2.
    """
    return lexicon.STAT_WORDS.search(question) is not None


def team_metric_named(question: str) -> tuple[str, Claim] | None:
    """The longest team-metric alias the question names ("defensive
    rating"), with its claim - or None. An alias the question qualifies as
    given up comes back as asked ("rebounds allowed"), for the reader to
    refuse by name (:data:`~association.query.lexicon.GIVEN_UP`).

    .. versionadded:: 6.0.0
       ``router._team_metric_in`` until Phase 3, step 2.
    """
    text = question.casefold()
    for alias, pattern in lexicon.TEAM_METRIC_NAMED:
        found = pattern.search(text)
        if found is None:
            continue
        given_up = lexicon.GIVEN_UP.match(text, found.end())
        if given_up is not None:
            return f"{alias} allowed", Claim(found.start(), given_up.end(), "measure")
        return alias, Claim(found.start(), found.end(), "measure")
    return None


# --- The tagger ----------------------------------------------------------------------------------

#: The readers that look an advanced metric up by its words.
#: A made column to its attempted sibling ("who attempted the most three pointers" filed the made column).
MADE_TO_ATTEMPTED: dict[str, str] = {"threePointFieldGoalsMade": "threePointFieldGoalsAttempted", "fieldGoalsMade": "fieldGoalsAttempted", "freeThrowsMade": "freeThrowsAttempted"}
#: A NetPoints name to its per-100 form, for a rate word beside it.
NETPOINTS_PER_100: dict[str, str] = {
    "netpoints": "netpoints_per_100",
    "netpoints_total": "netpoints_per_100",
    "netpoints_offense": "netpoints_offense_per_100",
    "netpoints_defense": "netpoints_defense_per_100",
}
#: The model's spellings of "games played", a count no measure reads.
GAMES_STATS: frozenset[str] = frozenset({"games", "game", "games_played", "gamesPlayed", "gp"})
"""What the model files for a count of games - no measure, and dropped where a line's reader would read it as one.

.. versionadded:: 6.0.0
   ``router._GAMES_STATS`` until Phase 3, step 2.
"""
#: The stats that are a yes/no about a game, which a ranking counts per
#: player; ranking THOSE GAMES by another measure is the window's `by`.
BOOLEAN_KEYS: frozenset[str] = frozenset({"triple_double", "double_double", "fouled_out"})
"""The boolean measures, by key.

.. versionadded:: 6.0.0
   ``router._BOOLEAN_STATS`` until Phase 3, step 2.
"""


@dataclass(frozen=True, kw_only=True)
class MeasureContext:
    """What the key's reading of the measure reads beside the words
    (:func:`~association.query.point.measure_under`; the key itself is its
    argument since Phase 3, step 4, slice (b)): the model's
    key (``key``), CONTEXT the words may confirm and never the value's
    source where the words name one - and where they name none, the key
    stands as it did, so no answer moves; the model's ``side`` and
    ``shot_value`` where a test hands them (the normalizer files neither);
    the columns a test hands beside a ranking (``fields``, kept as given
    whatever the words ask, as the slot was); the stat the lines tagger read beside a line (``line_stat``: "scores
    30" is points; one "N+ stat" pair under a count or a record is that
    word's column); whether a line is keyed (a record over one reads the
    games won or lost only where none is); and whether a game of ordering
    was named (a count of games with "last" is a log of one, not a count).

    .. versionadded:: 6.0.0
    """

    key: str | None = None
    side: str | None = None
    shot_value: int | None = None
    fields: tuple[str, ...] = ()
    line_stat: str | None = None
    keyed_line: bool = False
    order_named: bool = False


@dataclass(frozen=True)
class MeasureRead:
    """What the tagger read: the :class:`~association.query.reading.Measure`
    (None where the question says nothing about one) and the characters it
    claimed.

    .. versionadded:: 6.0.0
    """

    measure: Measure | None
    claims: tuple[Claim, ...]


@dataclass(frozen=True)
class MeasureWords:
    """Every word the measure is read by, found with no key: the measure the
    words name (:func:`named`) and each pattern a rule of the key's reading
    turns on (:func:`~association.query.point.measure_under`) - fouling out,
    "how many" and a stat word, a team stat word, "td3", an advanced
    metric, game score, a 2-point percentage, a ranking by shot distance,
    "attempted" and "made", a team metric's alias, games won or lost, the
    sides of the ball, per 90 and the rate words, a team's total and "per
    game", a log's and a quarter's games words, a losing streak, the shot
    values, and the columns asked for beside a ranking.

    .. versionadded:: 6.0.0
    """

    worded: tuple[str, Claim] | None
    fouled: bool
    how_many: bool
    names_a_stat: bool
    team_stat: bool
    td3: bool
    advanced: tuple[str, re.Match[str]] | None
    game_score: re.Match[str] | None
    two_point: re.Match[str] | None
    shot_distance: re.Match[str] | None
    attempted: re.Match[str] | None
    made: bool
    team_alias: tuple[str, Claim] | None
    games_won: re.Match[str] | None
    sides: tuple[tuple[str, re.Match[str]], ...]
    per_90: re.Match[str] | None
    rate: re.Match[str] | None
    team_total: re.Match[str] | None
    per_game: bool
    log: bool
    period_games: bool
    losing: re.Match[str] | None
    shot_values: tuple[tuple[int, re.Match[str]], ...]
    beside: tuple[tuple[str, ...], tuple[Claim, ...]]


def read_measure(question: str) -> MeasureWords:
    """Every word ``question`` names its measure by, with no key
    (:class:`MeasureWords`); the measure under the key the words ask is
    :func:`~association.query.point.measure_under`'s.

    .. versionadded:: 6.0.0

    .. versionchanged:: 6.0.0
       Reads no key (Phase 3, step 4, slice (b)): it took a
       ``MeasureContext`` holding what the stages settled the words to ask
       and read the measure under it; the rules that read the key are
       :func:`~association.query.point.measure_under`'s, over these words.
    """
    advanced = next(((metric, found) for metric, pattern in lexicon.ADVANCED_STAT_WORDS if (found := pattern.search(question)) is not None), None)
    return MeasureWords(
        worded=named(question),
        fouled=lexicon.FOULED_OUT.search(question) is not None,
        how_many=lexicon.HOW_MANY_GAMES.search(question) is not None,
        names_a_stat=names_a_stat(question),
        team_stat=lexicon.TEAM_STAT_WORDS.search(question) is not None,
        td3=lexicon.TRIPLE_DOUBLE_ABBREVIATION.search(question) is not None,
        advanced=advanced,
        game_score=lexicon.GAME_SCORE.search(question),
        two_point=lexicon.TWO_POINT_PCT.search(question),
        shot_distance=lexicon.SHOT_DISTANCE_RANKED.search(question),
        attempted=lexicon.ATTEMPTED.search(question),
        made=lexicon.MADE.search(question) is not None,
        team_alias=team_metric_named(question),
        games_won=lexicon.GAMES_WON.search(question),
        sides=tuple((name, found) for name, pattern in lexicon.SIDE_WORDS.items() if (found := pattern.search(question)) is not None),
        per_90=lexicon.PER_90.search(question),
        rate=lexicon.RATE_WORDS.search(question),
        team_total=lexicon.TEAM_TOTAL.search(question),
        per_game=lexicon.PER_GAME_WORDS.search(question) is not None,
        log=lexicon.LOG_WORDS.search(question) is not None,
        period_games=lexicon.PERIOD_GAMES_WORDS.search(question) is not None,
        losing=lexicon.LOSING_STREAK.search(question),
        shot_values=tuple((value, found) for value, pattern in lexicon.SHOT_VALUE_WORDS if (found := pattern.search(question)) is not None),
        beside=_beside_words(question),
    )


def _beside_words(question: str) -> tuple[tuple[str, ...], tuple[Claim, ...]]:
    """The columns the words ask to see beside a ranking's measure: each
    stat word after "with their" / "alongside their" that a ranking shows
    (:data:`BESIDE_KEYS`, by its box-score word), and "team" for the team
    each player plays for, with the characters read - kept on a ranking
    alone (the key's reading); a word it cannot show is left out."""
    fields: list[str] = []
    claims: list[Claim] = []
    after = lexicon.FIELDS_AFTER.search(question)
    if after is not None:
        for word in lexicon.WORD.findall(after.group("rest").lower()):
            key = lexicon.MEASURE_WORDS.get(word)
            if key in BESIDE_KEYS and key not in fields:
                fields.append(key)
        if fields:
            claims.append(Claim(after.start(), after.end(), "measure"))
    team = lexicon.TEAM_FIELD_WORDS.search(question)
    if team is not None:
        fields.append("team")
        claims.append(Claim(team.start(), team.end(), "measure"))
    return tuple(fields), tuple(claims)


def how_of(unit: str) -> MeasureHow | None:
    """The reading of a unit's words: a total, per 90, per 100 in any spelling.

    .. versionadded:: 6.0.0
    """
    low = unit.casefold()
    if low == "total":
        return "total"
    if "90" in low:
        return "per_90"
    return "per_100"


BESIDE_KEYS: frozenset[str] = frozenset({"points", "rebounds", "assists", "steals", "blocks", "minutes"})
"""The box-score averages a ranking can show beside its metric, by key
(``metrics.EXTRA_FIELD_COLUMNS``'s keys, declared here on the reader's side).

.. versionadded:: 6.0.0
"""


def spelled(measure: Measure | None) -> str | None:
    """The measure as the reader wrote it (``Measure.as_typed``), or None -
    what the answer side keys a label, a facts record or a refusal's words
    on, where the slot's spelling was read.

    .. versionadded:: 6.0.0
    """
    return measure.as_typed if measure is not None else None


def keyed(measure: Measure | None) -> str | None:
    """The catalog key of the subject's own measure, or None - for no
    measure, one no catalog holds, and the opponent's figure alike.

    .. versionadded:: 6.0.0
    """
    return measure.key if measure is not None and measure.whose == "own" else None


def won(measure: Measure | None) -> bool:
    """Whether a run is of wins: True unless the words said losses
    (``Measure.won`` False) - the ``kind != "loss"`` reading every run's
    reader made.

    .. versionadded:: 6.0.0
    """
    return measure is None or measure.won is not False
