"""The measure: the one catalog of what a question can ask about
(:data:`CATALOG`, keyed by :data:`MeasureKey`), the spellings each key is
named by across the six vocabularies the slot was read against
(:data:`ALIASES`: the normalizer's keys, the measure grammar's, the
compiler's measure names, the leaderboard metrics', the team metrics'
alias texts, the box-score words), and the one tagger that reads the
family (:func:`read_measure`) from the lexicon's words
(:mod:`association.query.lexicon`) and the facts the stages settled before
it - the intent, the model's key as context, the lines read - into
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
from typing import Literal

from association.nba.netpoints import FINGERPRINT_CATEGORIES, FINGERPRINT_SIDE_LABELS
from association.query import lexicon
from association.query.reading import Claim, Measure, MeasureHow, MeasureSide, MeasureWhose

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


def column_of(spelling: str | None) -> str | None:
    """The stored game-log column ``spelling`` names (the catalog's
    ``column``), or None - what a keyed line's measure is read as.

    .. versionadded:: 6.0.0
    """
    key, whose, _side, _how, _category = key_of(spelling)
    if key is None or whose != "own":
        return None
    return CATALOG[key].column


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
    stages' reading of "fgm", "ppg", "defensive rating" under a team's
    intents) rather than by its own key: the team compiler's total reads a
    measure by its key alone, as the stages' alias text never matched a
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
_ADVANCED_STAT_INTENTS: frozenset[str] = frozenset({"player_stat", "player_compare", "player_history", "leaderboard", "game_log"})
#: Game score's spelling, by the reader: the ranking reads it through the
#: leaderboard metrics, keyed `avg_game_score` like every other per-game
#: average; a player's line through the advanced stats, keyed `game_score`.
_GAME_SCORE_BY_INTENT: dict[str, str] = {"leaderboard": "avg_game_score", "player_stat": "game_score"}
_TWO_POINT_PCT_INTENTS: frozenset[str] = frozenset({"player_history", "player_stat"})
_SHOT_VALUE_INTENTS: frozenset[str] = frozenset({"shot_distance", "shot_chart"})
#: A made column to its attempted sibling ("who attempted the most three pointers" filed the made column).
_MADE_TO_ATTEMPTED: dict[str, str] = {"threePointFieldGoalsMade": "threePointFieldGoalsAttempted", "fieldGoalsMade": "fieldGoalsAttempted", "freeThrowsMade": "freeThrowsAttempted"}
#: A NetPoints name to its per-100 form, for a rate word beside it.
_NETPOINTS_PER_100: dict[str, str] = {
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
    """What the stages settled before the measure is read, and the tagger's
    rules read beside the words: the intent they settled on; the model's
    key (``key``), CONTEXT the words may confirm and never the value's
    source where the words name one - and where they name none, the key
    stands as it did, so no answer moves; the model's ``side`` and
    ``shot_value`` where a test hands them (the normalizer files neither);
    the columns a test hands beside a ranking (``fields``, kept as given on
    any intent, as the slot was); the stat the lines tagger read beside a line (``line_stat``: "scores
    30" is points; one "N+ stat" pair under a count or a record is that
    word's column); whether a line is keyed (a record over one reads the
    games won or lost only where none is); and whether a game of ordering
    was named (a count of games with "last" is a log of one, not a count).

    .. versionadded:: 6.0.0
    """

    intent: str
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


def read_measure(question: str, context: MeasureContext) -> MeasureRead:
    """The measure ``question``'s words name, under ``context``, with the
    claims: the words' own reading over the model's key (:func:`named`),
    then each rule the stages read in order - the intent-chosen keys
    (fouling out, a games count under a line's reader, a quarter's stat
    kept only where named), the model's key dropped where the words name
    no stat on a whole-line reader, an advanced metric, game score, a
    2-point percentage, a ranking by shot distance, the shots, the
    attempts beside a make, a team metric's alias, a NetPoints rate, a
    team's total, the stat beside a line, a run's result, the side, and
    the columns beside a ranking.

    .. versionadded:: 6.0.0
    """
    claims: list[Claim] = []
    key = context.key
    worded = named(question)
    if worded is not None:
        key = worded[0]
        claims.append(worded[1])
    key = _key_by_intent(question, context, key, claims)
    key = _key_by_words(question, context, key, claims)
    key, unit = _rate(question, context, key, claims)
    how: MeasureHow | None = None
    if unit is not None:
        how = _how_of(unit)
    elif context.intent == "period_split" and _per_game_log(question, key):
        how = "per_game"
    of_wins = None
    if context.intent == "streak":
        losing = lexicon.LOSING_STREAK.search(question)
        of_wins = losing is None
        if losing is not None:
            claims.append(Claim(losing.start(), losing.end(), "measure"))
    side = _side(question, context, claims)
    shot_value = _shot_value(question, context, claims)
    beside = _beside(question, context, claims)
    # One claim per stretch read: the grammar and a stage may read the same
    # words ("ts%" is the grammar's key and the advanced metric's word).
    distinct = tuple(dict.fromkeys(claim for claim in claims if claim.end > claim.start))
    if key is None and unit is None and how is None and of_wins is None and side is None and shot_value is None and not beside:
        return MeasureRead(None, distinct)
    catalog_key, whose, keyed_side, keyed_how, category = key_of(key)
    measure = Measure(
        key=catalog_key,
        as_typed=key,
        how=how if how is not None else keyed_how,
        unit=unit,
        whose=whose,
        side=side if side is not None else keyed_side,
        shot_value=shot_value,
        category=category,
        won=of_wins,
        beside=beside,
    )
    return MeasureRead(measure, distinct)


def _key_by_intent(question: str, context: MeasureContext, key: str | None, claims: list[Claim]) -> str | None:
    """The key the intent stages wrote or dropped as they chose the intent:
    fouling out is the count's own stat; a games count on a line's reader
    is no measure (a log of his games, a line's "how many games did he
    play"); a quarter's or a half's stat is kept only where the words name
    one (the model fills the required key whether or not they do); a
    comparison's, a line's, a team's and a run's likewise."""
    intent = context.intent
    fouled = lexicon.FOULED_OUT.search(question)
    if fouled is not None and intent == "threshold_count":
        key = "fouls"
    if intent == "game_log" and key in ("games", "game"):
        key = None
    if intent == "player_stat" and key in GAMES_STATS and lexicon.HOW_MANY_GAMES.search(question) and not context.order_named:
        key = None
    if intent in ("period_split", "period_leaderboard", "player_compare", "player_stat", "streak") and not names_a_stat(question):
        key = None
    if intent == "team_stat" and not (names_a_stat(question) or lexicon.TEAM_STAT_WORDS.search(question)):
        key = None
    if lexicon.TRIPLE_DOUBLE_ABBREVIATION.search(question):
        key = "triple_double"
    return key


def _key_by_words(question: str, context: MeasureContext, key: str | None, claims: list[Claim]) -> str | None:
    """The key the words name beyond the grammar, each where a reader looks
    it up: an advanced metric, game score (by the reader's own spelling), a
    2-point percentage, a ranking by shot distance (the sentinel the ranking
    refuses by name), the attempts beside a make, a team metric's alias, and
    the stat read beside a line."""
    intent = context.intent
    if intent in _ADVANCED_STAT_INTENTS:
        for metric, pattern in lexicon.ADVANCED_STAT_WORDS:
            found = pattern.search(question)
            if found is not None:
                key = metric
                claims.append(Claim(found.start(), found.end(), "measure"))
                break
    if intent in _GAME_SCORE_BY_INTENT:
        found = lexicon.GAME_SCORE.search(question)
        if found is not None:
            key = _GAME_SCORE_BY_INTENT[intent]
            claims.append(Claim(found.start(), found.end(), "measure"))
    if intent in _TWO_POINT_PCT_INTENTS:
        found = lexicon.TWO_POINT_PCT.search(question)
        if found is not None:
            key = "twoPointFieldGoalPct"
            claims.append(Claim(found.start(), found.end(), "measure"))
    if intent == "leaderboard":
        found = lexicon.SHOT_DISTANCE_RANKED.search(question)
        if found is not None:
            key = "shot_distance"
            claims.append(Claim(found.start(), found.end(), "measure"))
    if key in _MADE_TO_ATTEMPTED:
        attempted = lexicon.ATTEMPTED.search(question)
        if attempted is not None and lexicon.MADE.search(question) is None:
            key = _MADE_TO_ATTEMPTED[key]
            claims.append(Claim(attempted.start(), attempted.end(), "measure"))
    if intent in ("team_stat", "team_leaderboard"):
        alias = team_metric_named(question)
        if alias is not None:
            key = alias[0]
            claims.append(alias[1])
    if intent == "record_when" and not context.keyed_line:
        games_won = lexicon.GAMES_WON.search(question)
        if games_won is not None:
            return "losses" if games_won.group(1).lower().startswith("los") else "wins"
    if context.line_stat is not None:
        key = context.line_stat
    return key


def _rate(question: str, context: MeasureContext, key: str | None, claims: list[Claim]) -> tuple[str | None, str | None]:
    """A unit asked for: a NetPoints rate folded into its key (the one
    adjusted form it has), a per-90 or a per-100 rate of anything else
    kept as the cell the ranking refuses by name, a team's season total."""
    unit: str | None = None
    if context.intent == "leaderboard":
        if key in ("netpoints", "netpoints_per_100", "netpoints_total"):
            # "who are the top 10 in adjusted offensive netpoints" arrived as
            # the total after the 5.0.0 prompt shrink; the side word decides.
            sides = [(name, pattern.search(question)) for name, pattern in lexicon.SIDE_WORDS.items()]
            named_sides = [(name, found) for name, found in sides if found is not None]
            if len(named_sides) == 1:
                name, found = named_sides[0]
                key = f"netpoints_{name}" + ("_per_100" if key.endswith("_per_100") else "")
                claims.append(Claim(found.start(), found.end(), "measure"))
        per_90 = lexicon.PER_90.search(question)
        if per_90 is not None:
            claims.append(Claim(per_90.start(), per_90.end(), "measure"))
            return key, per_90.group(0).casefold()
        rate = lexicon.RATE_WORDS.search(question)
        if rate is not None:
            claims.append(Claim(rate.start(), rate.end(), "measure"))
            if key in _NETPOINTS_PER_100:
                key = _NETPOINTS_PER_100[key]
            elif not (key is not None and key.endswith("_per_100")):
                unit = rate.group(0).casefold()
    if context.intent == "team_stat" and unit is None:
        total = lexicon.TEAM_TOTAL.search(question)
        if total is not None and lexicon.PER_GAME_WORDS.search(question) is None:
            unit = "total"
            claims.append(Claim(total.start(), total.end(), "measure"))
    return key, unit


def _how_of(unit: str) -> MeasureHow | None:
    """The reading of a unit's words: a total, per 90, per 100 in any spelling."""
    low = unit.casefold()
    if low == "total":
        return "total"
    if "90" in low:
        return "per_90"
    return "per_100"


def _per_game_log(question: str, key: str | None) -> bool:
    """A quarter's or half's figure asked game by game: a log, or "games"
    with no stat named (measured, 7 of the 11 questions the retired reader
    answered first said "log", "by game" or "each game" and got a total)."""
    return lexicon.LOG_WORDS.search(question) is not None or (lexicon.PERIOD_GAMES_WORDS.search(question) is not None and key is None)


def _side(question: str, context: MeasureContext, claims: list[Claim]) -> MeasureSide | None:
    """The side of the ball a fingerprint asked for: the words first
    (exactly one side named; both is the whole radar), the model's where
    the words say nothing. Dropped often enough by a model that defers to
    its required key ("Show me Wembanyama's defensive fingerprint chart"
    spent the adjective on ``stat="defensive"``, 6/6 at temperature 0)."""
    if context.intent != "fingerprint":
        return None
    found = [(name, pattern.search(question)) for name, pattern in lexicon.SIDE_WORDS.items()]
    named_sides = [(name, match) for name, match in found if match is not None]
    if len(named_sides) == 1:
        name, match = named_sides[0]
        claims.append(Claim(match.start(), match.end(), "measure"))
        return name  # type: ignore[return-value]
    return context.side if context.side in ("offense", "defense", "total") else None  # type: ignore[return-value]


def _shot_value(question: str, context: MeasureContext, claims: list[Claim]) -> Literal[1, 2, 3] | None:
    """Which shots a distance or a chart is about: the model's value where
    it filed one, else exactly one value named ("twos and threes" is
    neither); never beside "td3s", a triple-double the model read as a three."""
    if context.intent not in _SHOT_VALUE_INTENTS or lexicon.TRIPLE_DOUBLE_ABBREVIATION.search(question):
        return None
    if isinstance(context.shot_value, int) and not isinstance(context.shot_value, bool):
        return context.shot_value  # type: ignore[return-value]
    found = [(value, pattern.search(question)) for value, pattern in lexicon.SHOT_VALUE_WORDS]
    named_values = [(value, match) for value, match in found if match is not None]
    if len(named_values) != 1:
        return None
    value, match = named_values[0]
    claims.append(Claim(match.start(), match.end(), "measure"))
    return value  # type: ignore[return-value]


def _beside(question: str, context: MeasureContext, claims: list[Claim]) -> tuple[str, ...]:
    """The columns a ranking asks to see beside its measure: each stat word
    after "with their" / "alongside their" that the ranking shows
    (:data:`~association.query.metrics.EXTRA_FIELD_COLUMNS`'s six, by their
    box-score word), and "team" for the team each player plays for. Only
    for the ranking, the one reader that shows them; a word it cannot show
    is left out, and the answer is the ranking the question also asked for."""
    if context.fields:
        return tuple(context.fields)
    if context.intent != "leaderboard":
        return ()
    fields: list[str] = []
    after = lexicon.FIELDS_AFTER.search(question)
    if after is not None:
        for word in lexicon.WORD.findall(after.group("rest").lower()):
            key = lexicon.MEASURE_WORDS.get(word)
            if key in _BESIDE_KEYS and key not in fields:
                fields.append(key)
        if fields:
            claims.append(Claim(after.start(), after.end(), "measure"))
    team = lexicon.TEAM_FIELD_WORDS.search(question)
    if team is not None:
        fields.append("team")
        claims.append(Claim(team.start(), team.end(), "measure"))
    return tuple(fields)


#: The box-score averages a ranking can show beside its metric
#: (``metrics.EXTRA_FIELD_COLUMNS``'s keys, declared here on the reader's side).
_BESIDE_KEYS: frozenset[str] = frozenset({"points", "rebounds", "assists", "steals", "blocks", "minutes"})


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
