"""The parser: a question, read into one :class:`~association.query.reading.Reading`
with no router.

ROADMAP plan item 6, step (b). What the model contributes arrives as data -
the names it copied out of the question and the stat key it chose
(:func:`read_route` takes them as arguments, from
:func:`~association.query.normalizer.normalize`) - and everything else is
read from the words here, in grammar
tables: the kind and the parent intent (:data:`PARENT_GRAMMAR`), then the
scope, the window and the point through the readers the pipeline already
has (:func:`~association.query.subject.read_subject`,
:func:`~association.query.router.settle`,
:func:`~association.query.point.read_point`). Those readers are the
source material the tables absorb one at a time; each table is measured on
the day10 wordings and the held-out paraphrases before the next
(``~/association-research/parser-greenfield/measure.py``).

Nothing here reaches a model, and nothing here trusts a name it was given:
a span that is no player's and no team's is dropped, never made a subject
(ISSUES.md #236).

.. versionadded:: 5.0.0
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Literal, cast, get_args

import duckdb

from association.query.decisions import Decision
from association.query.entities import _edit_budget, _words, find_players, find_teams, players_of, suggest_players, team_abbreviations, teams_of
from association.query.measures import MEASURE_WORDS, PERIOD_COLUMNS
from association.query.metrics import EXTRA_FIELD_COLUMNS, TEAM_FIELD_WORDS
from association.query.point import read_point
from association.query.reading import ConditionSpec, PeriodCondition, PointRefused, Reading, Scope, ScopeError, Split, Unsupported
from association.query.router import Route, _period_asked, _route_calendar_slots_split, settle
from association.query.subject import (
    TEAM_SINGULARS,
    Subject,
    _companion_phrases,
    _condition_role,
    _edit_distance,
    _near,
    apply_subject,
    beside,
    child_named,
    nicknames_in,
    question_derived_player,
    question_supports,
    read_subject,
    settle_subject,
    team_named_in,
)

_PAIR_MEETING = (
    r"(?!.*\b(compare|compared|comparing|contrast|evaluate|who scores more|who is better|who was better)\b)"
    r"(?=.*\b(head.to.head|matchup|when they play|meet|vs\.?|versus|against)\b)"
)
"""A pair meeting - "vs", "against", a matchup - unless a compare verb owns the pair."""

# Every count a question can spell, one to ninety-nine and a hundred, in one
# table every count pattern here is built from: the words were written out
# three times, and "last twelve games" read as a season line while "last 12
# games" read the log (the package review, 2026-09-27; word2number is
# unmaintained, and text2num's rewrite of the whole question would move
# every other reading).
_ONES = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine")
_TEENS = ("ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen")
_TENS = ("twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
_NUMBERS: dict[str, int] = {
    **{word: n for n, word in enumerate(_ONES, 1)},
    **{word: n for n, word in enumerate(_TEENS, 10)},
    **{word: n * 10 for n, word in enumerate(_TENS, 2)},
    **{f"{ten} {one}": t * 10 + o for t, ten in enumerate(_TENS, 2) for o, one in enumerate(_ONES, 1)},
    "hundred": 100,
    "a hundred": 100,
}
_COUNT = r"(\d{1,3}|" + "|".join(re.escape(w).replace(r"\ ", r"[\s-]+") for w in sorted(_NUMBERS, key=len, reverse=True)) + ")"


_PLAYER_LOG = (
    r"\b(game ?log|gamelog|logs?|last " + _COUNT + r" games|each game|game by game|box scores?|(?<!per )games? (with|where|in which|against|vs)"
    r"|how many (games|times)|highest|most .* in a game|career high|best game|single game"
    r"|first game|last game|(most recent|latest|previous|final) (\d+ )?games?|first \d+ games|game \d|month of|\d+/\d+|march|january|february|april|december|november|october)\b"
)
"""A player's games rather than his line: a log word, a window, one game, a date."""

ANY = frozenset({"player", "pair", "team", "teams", "team_players", "position", "everyone", "player+companions", "team+companions"})
"""Every subject kind, for a grammar row that applies whatever the kind."""

PARENT_GRAMMAR: tuple[tuple[frozenset[str], str, str], ...] = (
    # (kinds the row applies to, the words, the parent intent) - first match wins.
    (ANY, r"\bfingerprint", "fingerprint"),
    # "plot" and the shots in either order: "threes by Plot Curry" is "plot curry's threes" reworded.
    (ANY, r"\bshot (chart|map|plot)|\bplot\b.*\b(shots?|threes)\b|\b(shots?|threes)\b.*\bplot\b|\bshots?\b.*\b(chart|plot|map)\b|\bwhere .* shoot", "shot_chart"),
    (frozenset({"team", "teams"}), r"\b(1st|2nd|3rd|4th|first|second|third|fourth)[\s-](quarter|half)|\bquarter\b|\b[1-4]h\b|\b[1-4]q\b|\bovertime\b|\bclutch\b", "team_quarter_points"),
    (frozenset({"everyone"}), r"\b(1st|2nd|3rd|4th|first|second|third|fourth)[\s-](quarter|half)|\bquarter\b|\b[1-4]h\b|\b[1-4]q\b|\bovertime\b|\bclutch\b", "period_leaderboard"),
    (ANY, r"\b(1st|2nd|3rd|4th|first|second|third|fourth)[\s-](quarter|half)|\bquarter\b|\b[1-4]h\b|\b[1-4]q\b|\bovertime\b|\bclutch\b", "period_split"),
    (ANY, r"\bcoach", "coach"),
    (frozenset({"pair"}), _PAIR_MEETING, "player_matchup"),
    (frozenset({"pair"}), r".", "player_compare"),
    (frozenset({"teams"}), r".", "head_to_head"),
    (frozenset({"team_players"}), r".", "leaderboard"),
    (frozenset({"team"}), r"(?=.*\b(top \d+|scorers?|rebounders?|passers?|leaders?|players?)\b)(?=.*\b(top|most|best|leaders?)\b)", "leaderboard"),
    (frozenset({"team+companions"}), r"\b(with|without|when|while)\b", "with_without"),
    (
        frozenset({"team"}),
        r"\b(game ?log|(last|past|previous|most recent) " + _COUNT + r"\b|first \d+ games|each game|game by game|differential"
        r"|(first|opening|last|latest|most recent|final) game|(season )?opener)",
        "game_log",
    ),
    (frozenset({"team"}), r"\bstreak", "team_record"),
    (frozenset({"team"}), r"(?=.*\b(record|standings?|wins?|losses|w-?l|win.loss|won|lost|rec)\b)(?!.*\b(most|fewest|least|best|worst|top|rank)\b)", "team_record"),
    (frozenset({"team"}), r"\b(outlook|projections?|projected|on pace|schedule|odds|chances|(vs\.?|versus|against|compared (to|with)) other)\b", "team_outlook"),
    # A team's triple-doubles are its PLAYERS' (a boolean player line), which
    # the players' ranking reads under the team - not a team box-score total.
    (frozenset({"team"}), r"\b(triple|double)[ -]?doubles?\b", "leaderboard"),
    (frozenset({"team"}), r".", "team_stat"),
    (frozenset({"player"}), r"\bnet ?po?i?nts?\b|\bnetpts\b", "player_netpoints"),
    # A player's own record is the W-L of HIS games, which player_splits
    # answers (F088, "Embiid's record against Boston this year"; ISSUES.md
    # #231) - never the team's with/without split, which needs a companion.
    # Not with a line in it: "Sga record 36 plus points" is record_when, a
    # child player_stat's reading assigns. Before the log row, whose window
    # and date words ("since 1/26/20", "last 10 games") narrow a record as
    # much as a log - but not over a log word: a game log is its games.
    (
        frozenset({"player"}),
        r"(?=.*\b(record|rec|w-?l|win.loss|splits?)\b)(?!.*\b\d{1,3}[\s-]*(\+|plus\b|or more\b))(?!.*\b(game ?log|gamelog|logs?|each game|game by game|box scores?)\b)",
        "player_splits",
    ),
    (frozenset({"player"}), _PLAYER_LOG, "game_log"),
    # A player's split by a companion - unless a versus word sets him against
    # a team, where "maxey points vs boston without embiid" is his own games
    # narrowed (ROADMAP step 3: the absence a condition, on whichever side the
    # name resolves to), not a two-sided split.
    (frozenset({"player+companions"}), r"(?=.*\b(with|without|while|when)\b)(?!.*\b(?:vs\.?|versus|against)\b)", "with_without"),
    (frozenset({"player"}), r".", "player_stat"),
    (frozenset({"position"}), r"\b(log|game ?log)\b", "game_log"),
    (frozenset({"position"}), r".", "leaderboard"),
    (
        frozenset({"everyone"}),
        r"(?=.*\b(team|teams|franchise|nba)\b)(?!.*\bplayers?\b)(?=.*\b(record|wins|best|worst|most|fewest|per team|allowed)\b)(?!.*\b(leaders?|points|assists|rebounds|netpoints|netpts)\b)",
        "team_leaderboard",
    ),
    # A record with no player in it is a team's: "worst record 2025-26" read
    # as the league's scorers, the everyone row below.
    (frozenset({"everyone"}), r"(?=.*\b(record|standings?|w-?l)\b)(?!.*\b(players?|who scored|scorers?)\b)", "team_leaderboard"),
    (frozenset({"everyone"}), r"\bstreak", "team_record"),
    (frozenset({"everyone"}), r"\b(finals|game ?log)\b", "game_log"),
    (frozenset({"everyone"}), r".", "leaderboard"),
)
"""The parent-intent grammar: the first row whose kinds hold the subject's
kind and whose words the question matches names the parent. The children
(:data:`~association.query.subject.KIND_ASSIGNED_INTENTS`) are assigned
under it by the subject reading, as they are on the router's parent today.

.. versionadded:: 5.0.0
"""

MEASURE_GRAMMAR: tuple[tuple[str, str], ...] = (
    # (the words, the measure key) - first match wins; the netpoints family before its parts.
    (
        r"\b(defensive|def|defense)\b.{0,20}\b(net ?po?i?nts?|netpts)\b.{0,30}\b(per 100|/ ?100|adjusted|per possession)\b|\b(net ?po?i?nts?"
        r"|netpts)\b.{0,20}\b(defensive|def|defense)\b.{0,30}\b(per 100|/ ?100|adjusted|per possession)\b"
        r"|\badjusted\b.{0,12}\bdefensive\b.{0,12}\b(net ?po?i?nts?|netpts)\b|\bdefensive\b.{0,12}\b(netpts|net ?po?i?nts?)\s*/\s*100\b",
        "netpoints_defense_per_100",
    ),
    (
        r"\b(offensive|off|offense)\b.{0,20}\b(net ?po?i?nts?|netpts)\b.{0,30}\b(per 100|/ ?100|adjusted|per possession)\b|\b(net ?po?i?nts?"
        r"|netpts)\b.{0,20}\b(offensive|off|offense)\b.{0,30}\b(per 100|/ ?100|adjusted|per possession)\b"
        r"|\badjusted\b.{0,12}\boffensive\b.{0,12}\b(net ?po?i?nts?|netpts)\b",
        "netpoints_offense_per_100",
    ),
    (r"\b(defensive|def|defense)\b.{0,20}\b(net ?po?i?nts?|netpts)\b|\b(net ?po?i?nts?|netpts)\b.{0,20}\b(defensive|def|defense)\b", "netpoints_defense"),
    (r"\b(offensive|off|offense)\b.{0,20}\b(net ?po?i?nts?|netpts)\b|\b(net ?po?i?nts?|netpts)\b.{0,20}\b(offensive|off|offense)\b", "netpoints_offense"),
    (r"\b(net ?po?i?nts?|netpts)\b.{0,30}\b(per 100|/ ?100|adjusted|per possession)\b|\badjusted\b.{0,12}\b(net ?po?i?nts?|netpts)\b", "netpoints_per_100"),
    (r"\b(net ?po?i?nts?|netpts)\b", "netpoints"),
    (r"\bpoints? differential\b|\bpoint diff\b|\bdifferential\b", "points_differential"),
    (r"\bplus[ /-]?minus\b|\+/-", "plus_minus"),
    (r"\bts ?%|\btrue shooting\b", "ts_pct"),
    (r"\befg\b|\beffective field goal", "efg_pct"),
    (r"\busage\b|\busg\b", "usage_pct"),
    (r"\bgame score\b", "game_score"),
    (r"\btriple[ -]?doubles?\b|\btd3s?\b|\btds\b", "triple_double"),
    (r"\bdouble[ -]?doubles?\b|\bdd\b", "double_double"),
    # "%" is not a word character, so it takes no \b: "who had the highest
    # 3pt % this season" read as 3-pointers made while it did.
    (r"\b(3|three)[ -]?(pt|point|pointer)s?\b.{0,12}(\bpercentage\b|\bpct\b|%)|\b3p%|\b3pt%", "threePointFieldGoalPct"),
    (r"\b(2|two)[ -]?(pt|point|pointer)s?\b.{0,12}(\bpercentage\b|\bpct\b|%)|\b2p%|\b2pt%", "twoPointFieldGoalPct"),
    (r"\bfg ?%|\bfg percentage\b|\bfield goal percentage\b", "fieldGoalPct"),
    (r"\bft ?%|\bfree throw percentage\b", "freeThrowPct"),
    # What a team gives up is the opponent's line, never its own: "rebounds
    # allowed per team" read as the teams' own rebounds and was answered with
    # them, best first. Points allowed is a team metric; the rest name a key
    # no template ranks, which refuses rather than answering the team's own.
    (r"\b(points?|pts)\b.{0,12}\b(allowed|given up|conceded)\b|\b(allowed|gave up|conceded)\b.{0,12}\b(points?|pts)\b|\bopponents?'? (points|ppg)\b", "points allowed"),
    (r"\b(rebounds?|boards)\b.{0,12}\b(allowed|given up|conceded)\b|\b(allowed|gave up|conceded)\b.{0,12}\b(rebounds?|boards)\b", "rebounds allowed"),
    (r"\bassists?\b.{0,12}\b(allowed|given up|conceded)\b", "assists allowed"),
    (r"\b(threes|3s|(3|three)[ -]?(pt|point|pointer)s?)\b.{0,12}\b(allowed|given up|conceded)\b", "threes allowed"),
    # A three is its own column: "3 point stats", "three points made" and
    # "3-point average" are threes made (a percentage is read above), never
    # the "point" in them read as points.
    # Both asked for: the made line already says "585 of 1,727", and no
    # per-game line reads the attempted column alone.
    (r"(?=.*\b(3|three)[ -]?(pt|point|pointer)s?\b)(?=.*\b(attempts?|attempted|tries|3pa)\b)(?=.*\b(made|makes|mad|hit)\b)", "threePointFieldGoalsMade"),
    (r"\b(3|three)[ -]?(pt|point|pointer)s?\b.{0,12}\b(attempts?|attempted|tries)\b|\b3pa\b", "threePointFieldGoalsAttempted"),
    (r"\b(3|three)[ -]?(pt|point|pointer)s?\b(?!.{0,20}\b(distance|range|shots?)\b)", "threePointFieldGoalsMade"),
    (r"\bscorers?\b|\bscores\b|\bscoring\b", "points"),
)
"""The measure grammar: the stat a question names in its own words, read
before the normalizer's key so a phrase the closed vocabulary holds never
depends on the model - the NetPoints family above all (the 3B misses most
of it), then the derived rates and the words :data:`~association.query.measures.MEASURE_WORDS`
does not hold.

.. versionadded:: 5.0.0
"""


_MEASURE_ORDINARY_WORDS = frozenset({"to", "min"})
"""Abbreviations in :data:`~association.query.measures.MEASURE_WORDS` that are
also ordinary words ("compared to other teams" is no turnover count): read
only where the question writes them in capitals ("TO", "MIN")."""


def measure(question: str) -> str | None:
    """The measure :data:`MEASURE_GRAMMAR` or :data:`~association.query.measures.MEASURE_WORDS` names in ``question``, or ``None``."""
    for pattern, key in MEASURE_GRAMMAR:
        if re.search(pattern, question, re.IGNORECASE):
            return key
    words = " " + re.sub(r"[^a-z0-9%/+]+", " ", question.lower()) + " "
    hits = [(w, c) for w, c in MEASURE_WORDS.items() if f" {w} " in words and (w not in _MEASURE_ORDINARY_WORDS or re.search(rf"\b{w.upper()}S?\b", question))]
    return max(hits, key=lambda x: len(x[0]))[1] if hits else None


WINDOW_GRAMMAR: tuple[tuple[str, str | None, int | None], ...] = (
    # (the words, the order, the limit - 0 means "the number in the words") - first match wins.
    (rf"\b(last|past|previous|most recent|latest|final)\s+{_COUNT}\s+((home|road|away|regular[- ]season|playoff|postseason)\s+){{0,2}}(games?|outings?|contests?|starts?)\b", "recent", 0),
    # A bare count closing the question is games: "magic vs nets last 10".
    (rf"\b(last|past|previous)\s+{_COUNT}\s*[?.!]*\s*\Z", "recent", 0),
    (rf"\bfirst\s+{_COUNT}\s+games?\b", "first", 0),
    (r"\b(last|most recent|latest|final)\s+(regular[- ]season\s+|postseason\s+|playoff\s+)?game\b", "recent", 1),
    (r"\b(first|opening)\s+(regular[- ]season\s+|postseason\s+|playoff\s+)?game\b|\b(season\s+)?opener\b", "first", 1),
    (rf"\b(top|bottom)\s+{_COUNT}\b", None, 0),
    # Deliberately no "who led the league in ..." -> 1: a ranking with no
    # limit already leads with the one asked about and adds "Next: ..."
    # (leaderboard, threshold_count, single_game_high), and a limit of 1
    # cost those answers their runners-up (the lead's offline run of the
    # agent, 2026-09-27) - though the router's references hold it.
)
"""The window grammar: the count and the end of the rows a question asks
for, read from its own words ("last 10 games", "top 5", "his last game")
where the router used to fill them in.

.. versionadded:: 5.0.0
"""

_LOG_OR_WINDOW_WORDS = re.compile(r"\b(log|gamelog|game log|last \d+|past \d+|first \d+)\b", re.IGNORECASE)
# A window over two teams meeting is still their meetings when a record is
# asked for ("lakers vs mavs record last 10 home games"); a log word never is.
_TWO_TEAMS_LOG_WORDS = re.compile(r"\b(log|gamelog|game log)\b", re.IGNORECASE)
_TWO_TEAMS_RECORD_WORDS = re.compile(r"\b(record|rec|w-?l|win.loss)\b", re.IGNORECASE)


def _count(word: str) -> int:
    return int(word) if word.isdigit() else _NUMBERS[" ".join(word.lower().replace("-", " ").split())]


def _window_read(question: str) -> tuple[str | None, int | None] | None:
    """The window :data:`WINDOW_GRAMMAR` reads off ``question`` - the order
    and the limit its first matching row names, either ``None`` where the row
    names none - or ``None`` where no row matches."""
    for pattern, order, limit in WINDOW_GRAMMAR:
        match = re.search(pattern, question, re.IGNORECASE)
        if match is None:
            continue
        if limit == 0:
            number = next((g for g in match.groups() if g and re.fullmatch(_COUNT, g, re.IGNORECASE)), None)
            return order or None, _count(number) if number is not None else None
        return order or None, limit or None
    return None


def window(question: str, slots: dict[str, Any]) -> dict[str, Any]:
    """``slots`` - the model's names and stat, before the stages - with the
    window :data:`_window_read` reads, where nothing set it; a limit already
    set stands. The stages read the window beside the season type they
    decide, so it is read before them."""
    if isinstance(slots.get("limit"), int) and not isinstance(slots.get("limit"), bool):
        return slots
    read = _window_read(question)
    if read is None:
        return slots
    order, limit = read
    out = dict(slots)
    if order and not out.get("order"):
        out["order"] = order
    if limit is not None:
        out["limit"] = limit
    return out


def _as_order(order: str | None) -> Literal["recent", "first"] | None:
    """A window row's order as the Scope's own literal - the grammar's rows
    write one of the two, and a row that wrote anything else is a bug said
    out loud, as the Scope's door says it for a slot dict."""
    if order == "recent":
        return "recent"
    if order == "first":
        return "first"
    if order:
        raise ScopeError(f"window order {order!r} is not 'recent' or 'first'")
    return None


def _as_half(half: int | None) -> Literal[1, 2] | None:
    """A half of a game as the Scope's own literal (:func:`_as_order`'s
    reason)."""
    if half == 1:
        return 1
    if half == 2:
        return 2
    if half is not None:
        raise ScopeError(f"half {half!r} is not 1 or 2")
    return None


def _as_split(split: str | None) -> Split | None:
    """A split read off the words as the Scope's own literal
    (:func:`_as_order`'s reason)."""
    if split is None:
        return None
    if split not in get_args(Split):
        raise ScopeError(f"split {split!r} is not one the Scope holds")
    return cast("Split", split)


def window_scope(question: str, scope: Scope) -> Scope:
    """:func:`window`, over the typed Scope the stages settled: the window
    read again where the stages left it unset. A limit they set stands.

    .. versionadded:: 5.0.0
    """
    if scope.limit is not None:
        return scope
    read = _window_read(question)
    if read is None:
        return scope
    order, limit = read
    return replace(scope, order=scope.order or _as_order(order), limit=limit if limit is not None else scope.limit)


_MEETING = re.compile(r"\b(vs\.?|versus|against|play(?:ed|s)?|meet|met|head.to.head|matchup|face[ds]?|beat(?:en)?)\b", re.IGNORECASE)


# A row written as lookaheads describes the whole question, so it is anchored
# at its start: searched from every position, "(?!.*evaluate)" would simply
# skip past the word it excludes.
_PARENT_ROWS: tuple[tuple[frozenset[str], re.Pattern[str], str], ...] = tuple(
    (kinds, re.compile((r"\A" if pattern.startswith("(?") else "") + pattern, re.IGNORECASE | re.DOTALL), parent) for kinds, pattern, parent in PARENT_GRAMMAR
)


def parent_intent(question: str, kind: str, companions: bool = False) -> str:
    """The parent intent :data:`PARENT_GRAMMAR` names for ``question`` read
    as a subject of ``kind``; ``companions`` says the reading found a player
    named beside the subject with a role, which the ``<kind>+companions``
    rows require ("when playing away" and "when he started" name nobody)."""
    for kinds, pattern, parent in _PARENT_ROWS:
        if (kind in kinds or (companions and f"{kind}+companions" in kinds)) and pattern.search(question):
            return parent
    return "other"


#: Spans the normalizer may emit that name nobody here: a conference ("vs
#: west" - David, Delonte, Doug and Mario West are players, so the index alone
#: reads it as one) and the indefinite pronouns ("someone" is one near
#: spelling from Simone Fontecchio, and a single near spelling defaults).
_NEVER_A_NAME: frozenset[str] = frozenset(
    {"west", "east", "western", "eastern", "someone", "somebody", "anyone", "anybody", "everyone", "everybody", "nobody", "no one", "who", "whoever", "player", "players"}
)


def classify_span(con: duckdb.DuckDBPyConnection, text: str) -> str | None:
    """``"team"``, ``"player"`` or ``None`` for a span the model copied out of
    the question: a team's word, nickname or name first; then a name some
    player holds as whole words; else nothing (a division, a typo, the word
    "team" - none of them a subject, ISSUES.md #236)."""
    low = text.lower().strip().removesuffix("'s").rstrip("'")
    if low.removeprefix("the ") in _NEVER_A_NAME:
        return None
    if low in TEAM_SINGULARS or team_named_in(teams_of(con), low) is not None or _classify_span_abbreviation(con, low):
        return "team"
    teams = find_teams(con, text)
    players = find_players(con, text)
    if teams and not players:
        return "team"
    if players:
        return "player"
    # A single near spelling is that player - the typo policy Jeff settled
    # (2026-09-26): the index defaults, visibly, where exactly one player is
    # within the edit budget; two or more ask, as they always did. "Embid"
    # is Joel Embiid; "jolic" (Jokic or Jovic) is nobody's here.
    for spelling in dict.fromkeys((text, text.removesuffix("s"), text.removesuffix("'s"))):
        if spelling and len(suggest_players(con, spelling)) == 1:
            return "player"
    return None


def _classify_span_abbreviation(con: duckdb.DuckDBPyConnection, low: str) -> bool:
    """Whether ``low`` is a team's abbreviation exactly ("phi", "gsw"): "PHI"
    is also inside Phil Handy's name, and a span that IS a team's code names
    the team."""
    if not 2 <= len(low) <= 4 or not low.isalpha():
        return False
    return low in team_abbreviations(con)


def _as_typed(question: str, name: str) -> str:
    """``name`` as the question spells it. The model is told to copy names
    exactly and mostly does (299 of 302 measured), but it corrects a typo now
    and then - "how many points does embid average" came back as "embiid" -
    and a correction nothing shows is the model deciding who a name is, which
    is the entity index's job, said in the answer
    (:func:`~association.query.entities.read_near_spelling`). So a name the
    question does not hold is put back to the one run of the question's own
    words that is a near spelling of it, word for word; with none, or with
    two, the model's spelling stands (an expansion, "sga" as Shai
    Gilgeous-Alexander, is the nickname reading's to check)."""
    if name.casefold() in question.casefold():
        return name
    wanted = [w.casefold() for w in _AS_TYPED_WORD.findall(name)]
    words = _AS_TYPED_WORD.findall(question)
    runs = _as_typed_runs(words, wanted)
    if not runs and len(wanted) > 1 and wanted[-1] not in {w.casefold() for w in words}:
        # Completed AND corrected: "webanyama" came back "Victor Wembanyama".
        # The surname's own near spelling is what the question typed; a
        # surname the question holds as typed is a completion, which
        # `_as_typed_part` reads, not a correction.
        runs = _as_typed_runs(words, wanted[-1:])
    return " ".join(runs[0]) if wanted and len(runs) == 1 else name


def _as_typed_part(con: duckdb.DuckDBPyConnection, question: str, name: str) -> str:
    """``name`` cut back to the part the question holds, where the model
    COMPLETED a name the question gives only part of and that part names
    more than one player. "who is better, tatum or brown" came back with
    "Jaylen Brown": "brown" is ten players, and the model choosing Jaylen is
    the prominence tiebreak this project measured and rejected (above
    :data:`~association.query.entities.PLAYER_NICKNAMES`), arriving through a
    guess nothing downstream can see. Cut back, normal resolution decides -
    by who still plays, said in the answer, or by asking.

    Only where the part is ambiguous: completing "jokic" or "embiid" changes
    no answer. Left alone besides: a name the question spells in full, a
    nickname the question used (the curated table's resolution, "steph
    curry" as Stephen), and a name the question's own span resolves to
    (:func:`~association.query.subject.question_derived_player` - "Dylon
    harper" typos the given name, and the corrected "Dylan" is not a word
    the question lacks). A completion that resolves to nobody is cut back
    whatever the part reaches ("derozan" came back "Derozan Valenčić", a
    surname no player has). The router-era repair
    ``entities.undo_name_completion`` made this cut after every stage; it is
    the parser's now, on the model's own names, before the reading respells
    them - which is what keeps a typo'd surname ("Bam Adeyebu", read as Bam
    Adebayo) from being cut back to the ambiguous "Bam"."""
    words = _words(name)
    asked = {word.casefold() for word in _words(question)}
    held = [word for word in words if word.casefold() in asked]
    if not held or len(held) == len(words) or name in nicknames_in(question):
        return name
    derived = question_derived_player(players_of(con), question, name)
    if derived is not None and derived.name.casefold() == name.casefold():
        return name
    part = " ".join(held)
    return part if len(find_players(con, part)) > 1 or not find_players(con, name) else name


def _as_typed_runs(words: list[str], wanted: list[str]) -> list[list[str]]:
    """Every run of ``words`` that is ``wanted`` word for word, each within
    the entity index's edit budget (three letters or more)."""
    return [
        words[i : i + len(wanted)]
        for i in range(len(words) - len(wanted) + 1)
        if all(len(t) >= 3 and _as_typed_close(t.casefold(), w) for t, w in zip(words[i : i + len(wanted)], wanted, strict=True))
    ]


def _as_typed_close(typed: str, wanted: str) -> bool:
    """``typed`` is ``wanted`` within the entity index's edit budget - read
    with its possessive "s" as well as without it, the way the index reads
    a name (:func:`~association.query.entities.read_near_spelling`): "show
    me embids 3pt percentage" came back "embiid", two edits from "embids"
    and one from "embid", and with no run to put it back to, the name the
    question does hold read as one it never named (ISSUES.md #180)."""
    budget = _edit_budget(wanted)
    return _edit_distance(typed, wanted) <= budget or (len(typed) > 3 and typed.endswith("s") and _edit_distance(typed[:-1], wanted) <= budget)


_AS_TYPED_WORD = re.compile(r"[\w'.-]+")


def _slots_from_names(con: duckdb.DuckDBPyConnection, names: list[str], stat: str) -> dict[str, Any]:
    """The names as the slot shape the readers take today: ``player`` /
    ``players``, ``team`` and a second team as ``opponent``, ``stat``."""
    teams = [n for n in names if classify_span(con, n) == "team"]
    players = [n for n in names if n not in teams and classify_span(con, n) == "player"]
    slots: dict[str, Any] = {}
    if len(players) == 1:
        slots["player"] = players[0]
    elif players:
        slots["players"] = players
    if teams:
        slots["team"] = teams[0]
    if len(teams) > 1:
        slots["opponent"] = teams[1]
    if stat:
        slots["stat"] = stat
    return slots


def _with_measure(question: str, slots: dict[str, Any]) -> dict[str, Any]:
    """``slots`` with the stat the words name (:func:`measure`) over the
    normalizer's key: the words are the question's own; the key is a guess."""
    named = measure(question)
    return {**slots, "stat": named} if named else slots


def _two_teams(subject: Subject, question: str, slots: dict[str, Any]) -> Subject:
    """Two teams meeting (ISSUES.md #235): a team subject set against a
    second team, no player named, and a meeting word between them - the
    ``teams`` kind the reading only gives under ``head_to_head`` today."""
    if subject.kind != "team" or subject.players or not subject.teams:
        return subject
    other = subject.opponent or (slots.get("opponent") if isinstance(slots.get("opponent"), str) else None)
    one_teams_games = _TWO_TEAMS_LOG_WORDS.search(question) or (_LOG_OR_WINDOW_WORDS.search(question) and not _TWO_TEAMS_RECORD_WORDS.search(question))
    if not other or other == subject.teams[0] or not _MEETING.search(question) or one_teams_games:
        return subject
    return replace(subject, kind="teams", teams=(subject.teams[0], other), opponent=None)


def _read_route_names(subject: Subject, slots: dict[str, Any]) -> dict[str, Any]:
    """The name slots as the subject reading read them from the question's
    own words - its players (never a companion: "without joel embiid" is a
    narrowing), the team it is about or plays for, the opponent - in place of
    the model's spans, which are only where the reading started. A player
    span the reading did not settle on and that is none of its names is kept
    as typed ("brown" beside Tatum is ten players; the template asks), so a
    name the model found is never lost; a name the model DROPPED that the
    question holds is the reading's ("Nikola Jokic" with ``names=[]``).
    A reading that settled on no one leaves the slots as they were."""
    if not (subject.players or subject.teams or subject.opponent or subject.own_team or subject.companions):
        return slots
    out = {key: value for key, value in slots.items() if key not in ("player", "players", "team", "opponent")}
    players = _read_route_players(subject, slots)
    if len(players) == 1:
        out["player"] = players[0]
    elif players:
        out["players"] = players
    # A player's own team stays the subject stage's to write, as for a routed
    # question: the templates read it with the span it implies ("lebron as a
    # starter for Miami" is his Heat years, not this season). A team the
    # reading placed nowhere stays as the model filed it.
    placed = subject.opponent or subject.own_team
    team = subject.teams[0] if subject.teams and subject.kind in ("team", "teams", "team_players", "everyone", "position") else (None if placed else slots.get("team"))
    if team:
        out["team"] = team
    opponent = subject.teams[1] if subject.kind == "teams" and len(subject.teams) > 1 else subject.opponent
    if opponent:
        out["opponent"] = opponent
    return out


def _read_route_players(subject: Subject, slots: dict[str, Any]) -> list[str]:
    """The subject's players, then each player span of the model's that is
    none of the reading's names or companions (``question_supports``, so
    "lebron" is LeBron James and "embid" Joel Embiid)."""
    read = (*subject.players, *subject.companions)
    typed = [slots["player"]] if isinstance(slots.get("player"), str) else list(slots.get("players") or [])
    return [*subject.players, *(span for span in typed if not any(question_supports(name, span) for name in read))]


# Columns asked for beside a ranking - "top 5 scorers with their rebounds and
# assists", "... and the team they play for" (yardstick-v2 F017): the
# leaderboard's `fields`, a slot the router's model filled from the words.
_FIELDS_AFTER = re.compile(r"\b(?:with|alongside|and|plus|including)\s+(?:their|his|the)\s+(?P<rest>.+)$", re.IGNORECASE)


def _read_route_fields(intent: str, scope: Scope, question: str) -> Scope:
    """The columns a leaderboard question asks to see beside its ranking:
    each stat word after "with their" / "alongside their" that the
    leaderboard shows (:data:`~association.query.metrics.EXTRA_FIELD_COLUMNS`),
    and "team" for the team each player plays for. Only for the leaderboard,
    the one template that reads them; a word it cannot show is left out, and
    the answer is the ranking the question also asked for."""
    if intent != "leaderboard" or scope.fields:
        return scope
    fields: list[str] = []
    after = _FIELDS_AFTER.search(question)
    if after:
        for word in re.findall(r"[a-z]+", after.group("rest").lower()):
            key = MEASURE_WORDS.get(word)
            if key in EXTRA_FIELD_COLUMNS and key not in fields:
                fields.append(key)
    if TEAM_FIELD_WORDS.search(question):
        fields.append("team")
    return replace(scope, fields=tuple(fields)) if fields else scope


# A quarter or half used as a CONDITION on which games count: the verb, the
# number, the stat and the period, in the shapes questions use - "after
# making one three in first quarter" (yardstick-v2 F062), "in games where he
# scored 10+ points in the first half", "when he makes a three in the 4th".
_PERIOD_CONDITION = re.compile(
    r"\b(?:after\s+|(?:in|for|over)\s+(?:the\s+)?games?\s+(?:(?:where|when|in\s+which)\s+)?(?:(?:he|she|they)\s+)?|when(?:ever)?\s+(?:he|she|they)\s+|if\s+(?:he|she|they)\s+)"
    r"(?:making|scoring|hitting|getting|having|recording|grabbing|made|scored|hit|got|had|recorded|grabbed|makes|scores|hits|gets|has|records|grabs)\s+"
    r"(?P<least>at\s+least\s+)?(?P<n>\d{1,3}|an?|one|two|three|four|five|six|seven|eight|nine|ten)\s*(?P<more>\+|\s+or\s+more)?\s+"
    r"(?P<stat>[a-z0-9 -]+?)\s+in\s+(?:the\s+)?(?P<period>(?:first|second|third|fourth|1st|2nd|3rd|4th)[\s-]+(?:quarter|half)|q[1-4]|[1-4]q|[12]h)\b",
    re.IGNORECASE,
)
_CONDITION_NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
# The singular a line's words take after "one" - the columns the period's
# line rebuilds (MEASURE_WORDS holds the plurals and the abbreviations).
_CONDITION_STAT_WORDS: dict[str, str] = {
    "three": "threePointFieldGoalsMade",
    "3": "threePointFieldGoalsMade",
    "three pointer": "threePointFieldGoalsMade",
    "3 pointer": "threePointFieldGoalsMade",
    "3-pointer": "threePointFieldGoalsMade",
    "three-pointer": "threePointFieldGoalsMade",
    "triple": "threePointFieldGoalsMade",
    "point": "points",
    "rebound": "rebounds",
    "board": "rebounds",
    "assist": "assists",
    "steal": "steals",
    "block": "blocks",
    "turnover": "turnovers",
    "foul": "fouls",
    "free throw": "freeThrowsMade",
    "field goal": "fieldGoalsMade",
    "basket": "fieldGoalsMade",
    "shot": "fieldGoalsMade",
}


def read_period_condition(question: str) -> tuple[PeriodCondition, tuple[int, int]] | None:
    """The quarter or half a question uses as a condition on which games
    count, as a :class:`~association.query.reading.PeriodCondition` with
    the span of the words that said it - or None where the question uses
    none, or words one whose stat the period's line does not rebuild
    (:data:`~association.query.measures.PERIOD_COLUMNS`; a refusal names
    that, ``refusals._period_as_condition``). Read from the text alone, the
    way every slot but the names and the stat is (ROADMAP plan item 6).
    "At least N", "N+", "N or more" and "a"/"an" are at-least lines; a bare
    number ("one three", "10 points") is exactly that many - the reading
    yardstick-v2 F062's key takes (31 games with exactly one first-quarter
    three, not the 36 with one or more) - and the answer says "exactly", so
    the other reading is one word away (Jeff's rule: a visible default that
    can be corrected).

    .. versionadded:: 5.0.0
    """
    m = _PERIOD_CONDITION.search(question)
    if m is None:
        return None
    number = m.group("n").lower()
    threshold = int(number) if number.isdigit() else _CONDITION_NUMBERS[number]
    words = re.sub(r"\s+", " ", m.group("stat").lower().strip())
    stat = _CONDITION_STAT_WORDS.get(words) or MEASURE_WORDS.get(words) or MEASURE_WORDS.get(f"{words}s")
    if stat is None or stat not in PERIOD_COLUMNS or threshold < 1:
        return None
    asked = _period_asked(m.group("period"))
    if asked is None:
        return None
    op: Literal[">=", "="] = ">=" if m.group("least") or m.group("more") or number in ("a", "an") else "="
    return PeriodCondition(stat=stat, threshold=threshold, op=op, period=asked.get("period"), half=_as_half(asked.get("half"))), m.span()


def _read_route_versus(subject: Subject, scope: Scope, intent: str) -> Scope:
    """The players the reading put on the OTHER side of the subject's games
    ("most points by curry vs lebron", ROADMAP step 3), written into the
    route's ``conditions`` here rather than left for the reading's second
    pass (:func:`reading_from_route`, which reads the route and not the
    model's names): a name two players share ("curry") is read as a player
    only through the model's span, so a second pass without it would lose
    him - and with him lost, "giannis points vs lebron and curry" was two
    subjects again. Whatever the intent: a condition the answer cannot
    honor is refused by name, never dropped."""
    versus = [c for c in subject.conditions if c.side == "opponent"]
    if not versus:
        return scope
    return replace(scope, conditions=(*scope.conditions, *(ConditionSpec(player=c.name, side="opponent", predicate="played") for c in versus)))


def _read_route_period(intent: str, scope: Scope, question: str) -> Scope:
    """A team's quarter or half from the words ("first quarter", "2nd
    half") - a slot the router's model filled and the stages only read for
    the intents they assign themselves."""
    if intent != "team_quarter_points" or scope.period is not None or scope.half is not None:
        return scope
    asked = _period_asked(question)
    return replace(scope, period=asked.get("period"), half=_as_half(asked.get("half"))) if asked else scope


#: The roles a companion can have that narrow the subject's OWN games as a
#: condition (``subject._apply_conditions``) rather than divide them into a
#: with/without split: "maxey points when embiid starts" is Maxey's line in
#: Embiid's starts.
_OWN_READ_ROLES = frozenset({"started", "bench"})

# A start or a bench role the phrase denies - "when embiid doesn't start" -
# reads as `started` to the role reader, which has no predicate for "did not":
# as a condition that would narrow to the very games the question excludes.
# The with/without split shows both halves, so a denied role stays there.
_DENIED_ROLE = re.compile(r"(?:\bnot|n'?t|\bnever)\s+(?:be\s+|been\s+|get\s+|gets\s+)?(?:start|come|came|coming)", re.IGNORECASE)

# "off" ends a companion phrase before "the bench" ("the" stops it), so the
# role's words run past the phrase by that much.
_ROLE_TAIL = re.compile(r"\s*the\s+(?:bench|pine)\b", re.IGNORECASE)


def _read_route_role_phrases(subject: Subject, question: str) -> list[tuple[re.Match[str], str]]:
    """Each companion phrase of ``question`` (``subject._companion_phrases``)
    that names one of the reading's companions, with the role it gives him
    (``subject._condition_role``) - the reading's own phrases, never a second
    reader of them."""
    phrases: list[tuple[re.Match[str], str]] = []
    for match in _companion_phrases(question):
        predicate = _condition_role(match.group(1).lower(), match.group(2))[0]
        if any(c.predicate == predicate and _near(c.name, match.group(2)) for c in subject.conditions):
            phrases.append((match, predicate))
    return phrases


def _read_route_beside(subject: Subject, question: str) -> bool:
    """Whether a companion stands beside the subject for the ``<kind>+companions``
    rows of :data:`PARENT_GRAMMAR` - the with/without split. Not a player's
    teammate whose only role is a start or the bench: that narrows the
    player's own games ("maxey points when embiid starts", "in games embiid
    started"), so the player's own row names the parent (``player_stat``,
    ``game_log``, a shot template) and the role is a ``conditions`` entry on
    it - where the split's ``when`` row sent it to ``with_without``, and a
    missing "when" to a pair of players compared."""
    if not subject.conditions:
        return False
    if subject.kind != "player" or any(c.predicate not in _OWN_READ_ROLES for c in subject.conditions):
        return True
    return any(_DENIED_ROLE.search(match.group(2)) for match, _ in _read_route_role_phrases(subject, question))


def _read_route_split(subject: Subject, question: str, intent: str, scope: Scope) -> Scope:
    """``scope`` with the split read again from ``question`` with every
    teammate's start or bench phrase blanked out: "maxey points when embiid
    starts" filed Embiid's start as Maxey's own starter split, which
    ``with_without`` refused, and "stephen curry shot chart when draymond
    green starts" drew Curry's starts. The subject's own split still reads
    ("maxey points as a starter when embiid comes off the bench").

    The teammate's role is written in its place, as a condition, whatever
    the intent (``subject._apply_conditions``): until 5.0.0's last change
    this and that were gated on whether the answering template honored
    conditions, so "sixers first quarter points when embiid starts" kept
    the misread split, and the split is what refused it - a reader that
    read differently for what would answer (``ROADMAP.md``, Phase 1). Now
    the condition is written, and the planner or the template refuses it
    by name."""
    phrases = [match for match, predicate in _read_route_role_phrases(subject, question) if predicate in _OWN_READ_ROLES]
    if not phrases:
        return scope
    blanked = question
    for match in phrases:
        tail = _ROLE_TAIL.match(question, match.end(2))
        end = tail.end() if tail is not None else match.end(2)
        blanked = blanked[: match.start()] + " " * (end - match.start()) + blanked[end:]
    split = _route_calendar_slots_split(blanked)
    out = replace(scope, split=_as_split(split))
    if split == "home_away" and intent == "player_splits":
        out = replace(out, venue=None)  # a split over venues is not a filter to one (router._route_intent_slots)
    return out


# The apostrophe a phone keyboard types (U+2019, and U+2018, its opening
# twin) is a straight one to every reader after the parser's door: the
# router's patterns ("2010's" is a decade, "n't" a denial, a possessive
# "'s"), the name tokens, and the warehouse's own names - 32 players carry a
# straight apostrophe and none a typographic one. Typed with U+2019, "most
# points in the 2010's" answered the 2010 season alone, and "D'Angelo
# Russell game against the Timberwolves" lost its player (ISSUES.md #259).
# Folded here, once, in the question and the names the model copied out of
# it together, so a copied "De'Aaron" typed with one still anchors to its
# question - and never before the normalizer is asked, since the model
# copies the names from the question as typed and its recorded replies are
# keyed on it.
_READ_ROUTE_APOSTROPHES = str.maketrans({"\u2019": "'", "\u2018": "'"})


def _read_route_folded(text: str) -> str:
    """``text`` with each typographic apostrophe (U+2019, U+2018) read as a
    straight one - the fold at the parser's door, for :func:`read_route`
    and :func:`reading_from_route` alike."""
    return text.translate(_READ_ROUTE_APOSTROPHES)


def read_route(con: duckdb.DuckDBPyConnection, question: str, names: list[str] | None = None, stat: str = "") -> tuple[Route, Subject, str]:
    """The route the parser settles on for ``question`` - the intent and the
    slots a template reads, in the router's own shape - beside the subject
    it was read about and the parent the words named. ``names`` are the
    spans the normalizer copied out of the question and ``stat`` its stat
    key, both checked here, never trusted. This is what the agent answers
    from when the parser reads the question in place of the router (step c):
    the typed Scope the stages settled, exactly as a routed question's. A
    typographic apostrophe (U+2019, U+2018) in the question or in a name is
    read as a straight one, so "the 2010's" typed with one is a decade, and
    "D'Angelo Russell" a player.

    .. versionadded:: 5.0.0
    """
    question = _read_route_folded(question)
    names = [_read_route_folded(name) for name in names or []]
    slots = _with_measure(question, _slots_from_names(con, [_as_typed_part(con, question, _as_typed(question, name)) for name in names], stat))
    # THE reading of who the question is about: everything after this
    # settles it (subject.settle_subject), nothing reads the names again.
    read = read_subject(con, question, "other", Scope.from_slots(slots))
    subject = _two_teams(read, question, slots)
    slots = _read_route_names(subject, slots)
    # A quarter or half used as a condition on which games count is read
    # here and its words taken out of the question the grammar and the
    # stages see (#275): left in, "after making one three in first quarter"
    # is the period relation's question and "one three" a line on nothing.
    condition = read_period_condition(question)
    if condition is not None:
        start, end = condition[1]
        question = f"{question[:start]} {question[end:]}"
    parent = parent_intent(question, subject.kind, _read_route_beside(subject, question))
    # The window before the stages: they read ``order``/``limit`` as the
    # model's (a bare "last 10 games" reads both season types only beside
    # them, ``_route_game_log_recent_span``).
    staged, decisions, words = _read_route_staged(question, slots, parent, read)
    final = staged.intent
    scope = _read_route_fields(final, _read_route_period(final, window_scope(question, staged.scope), question), question)
    # A teammate's start is his, never the subject's own split: the stages
    # read the split from the whole question.
    scope = _read_route_split(subject, question, final, scope)
    if condition is not None:
        scope = replace(scope, period_condition=condition[0])
    scope = _read_route_versus(subject, scope, final)
    # The one reading, settled under the intent the route ends with: what
    # the parser's last step and everything after it answer from.
    settled = settle_subject(read, final, parent=parent, words=words)
    subject = replace(
        subject,
        intent=final,
        intent_reason=settled.intent_reason,
        teams=subject.teams if subject.kind == "teams" else settled.teams,
        opponent=subject.opponent if subject.kind == "teams" else settled.opponent,
    )
    return Route(final, scope, decisions, settled), subject, parent


def _read_route_staged(question: str, slots: dict[str, Any], parent: str, read: Subject) -> tuple[Route, tuple[Decision, ...], str | None]:
    """The stages, run ONCE: under the child the subject's shape and the
    question's words name for ``parent``
    (:func:`~association.query.subject.child_named` - a count of 30+ point
    games under a game log, a history over the past 4 seasons under a
    player's line, a team's record under a companion's line), or under the
    parent itself. The stages may decline a child (a count with no
    threshold in the text is a ranking), and then run again under the
    parent - the one case of a second run, measured at 4 of 628 recorded
    questions, every one a history the stages read as a line (and 59 of
    2,082 wordings outside the corpus). Until
    5.0.0's last change they ran under the parent first and again under
    each child to see whether it held, and once more under the one that
    did: 1.7 runs a question on average, up to four.

    Returns the route, the decisions made getting to it
    (:attr:`~association.query.router.Route.decisions`: the intent moving
    off the parent, and why) and the words that named the child, if one
    stands."""
    named = child_named(read, parent, question)
    companions = beside(read.conditions)
    decisions: list[Decision] = []
    if named is not None:
        child, words = named
        staged = settle(child, window(question, slots), question, companions)
        # A team's record under a companion's line names no words, and the
        # stages' own settling of it stands, as the route's did.
        if staged.intent == child or words is None:
            decisions.append(Decision("parser", "intent", parent, child, _why_named(child, words)))
            if staged.intent != child:
                decisions.append(Decision("parser", "intent", child, staged.intent, "the stages settle it from the question's words"))
            return staged, tuple(decisions), words
        decisions.append(Decision("parser", "intent", child, parent, f"the words {words!r} name {child}, and the stages declined it"))
    staged = settle(parent, window(question, slots), question, companions)
    if staged.intent != parent:
        decisions.append(Decision("parser", "intent", parent, staged.intent, "the stages settle it from the question's words"))
    return staged, tuple(decisions), None


def _why_named(child: str, words: str | None) -> str:
    """The reason a child stands, as :attr:`~association.query.subject.Subject.intent_reason` says it."""
    return f"the words {words!r} name {child}" if words is not None else "a team's record in the games a player named beside it reached a line"


def reading_from_route(con: duckdb.DuckDBPyConnection, question: str, route: Route) -> Reading:
    """The parser's last step: the :class:`~association.query.reading.Reading`
    everything after the parser answers from. ``route`` is what
    :func:`read_route` settled, carrying the one reading of who the question
    is about (:attr:`~association.query.router.Route.subject`); that
    reading is settled under the route's intent
    (:func:`~association.query.subject.settle_subject`) and written into the
    typed scope - the players it names, in the route's own shape, the one
    player a template that needs one was left without, a player's own team
    and the tenure it implies, a position group, the companions' roles
    (:func:`~association.query.subject.apply_subject`). Nothing after this
    writes a slot: the agent consumes the Reading (ROADMAP plan item 6, step
    (d), part 3 - one writer).

    The Reading carries what the reading decided, as values
    (:attr:`~association.query.reading.Reading.decisions`) - who the question
    is about, then the parser's own moves of the intent that ``route``
    carries, then what the subject wrote into the scope - and the model's
    names the question never held that nothing in it could replace
    (:attr:`~association.query.reading.Reading.misread`), which the agent
    refuses by name rather than answer about somebody the question never
    mentioned. The route's scope is the typed
    Scope already (:class:`~association.query.router.Route`), its names and
    phrases folded by :func:`read_route`; a typographic apostrophe in the
    question is read as a straight one here too.

    .. versionadded:: 5.0.0
    """
    scope = route.scope
    question = _read_route_folded(question)
    # The subject is read once, by read_route, settled there under the
    # intent the route ends with, and rides on the route. A route with none
    # is a caller's mistake, said here: until 5.0.0's last change the
    # subject was read again for one, a second reading that ran on a
    # different scope than the first and disagreed with it (a team's old
    # name read without the season; ROADMAP.md, Phase 1).
    if route.subject is None:
        raise ValueError("reading_from_route needs the route's subject - who the question is about, as read_route read it")
    subject = route.subject
    applied = apply_subject(subject, scope, intent=route.intent)
    reading = Reading(
        scope=applied.scope,
        intent=applied.intent,
        subject=subject,
        decisions=(*_subject_decisions(subject), *route.decisions, *applied.decisions),
        misread=tuple(applied.dropped),
    )
    return with_point(con, question, reading)


def with_point(con: duckdb.DuckDBPyConnection, question: str, reading: Reading) -> Reading:
    """``reading`` with the compiler's point read from the question's words
    (:func:`~association.query.point.read_point`) - or with why
    there is none: no reading of the point (``point_declined``), or a
    refusal the reading itself comes to (``point_refusal``: its cause, which
    the planner says). Read here,
    once, so nothing after the parser reads the question; the point is
    PLANNED after the parser, once, by the answering loop
    (:func:`~association.query.compose.plan.plan_point`), which is where a
    narrowing the relation cannot honor is refused.
    :func:`reading_from_route`'s last step; public for a caller that builds
    a Reading by hand (a test handing the compiler a subject of its own)
    rather than from a route.

    .. versionadded:: 5.0.0

    .. versionchanged:: 5.0.0
       Reads the point and no longer plans it (``ROADMAP.md``, Phase 1: the
       planner runs once, outside the parser).
    """
    try:
        point = read_point(reading, question)
    except PointRefused as exc:
        # Before the decline: a refusal with a cause is a kind of decline.
        return replace(reading, point_refusal=exc.cause)
    except Unsupported as exc:
        return replace(reading, point_declined=str(exc))
    return replace(reading, point=point)


def _subject_decisions(subject: Subject) -> tuple[Decision, ...]:
    """Who the question was read to be about, as decisions: the kind, with
    what the reading rested on, then each name the reading found."""
    found = (
        ("players", subject.players),
        ("teams", subject.teams),
        ("opponent", subject.opponent),
        ("own_team", subject.own_team),
        ("companions", subject.companions),
        ("position", subject.position),
    )
    return (
        Decision("subject", "kind", None, subject.kind, "; ".join(subject.evidence)),
        *(Decision("subject", name, None, list(value) if isinstance(value, tuple) else value, "from the question's own words") for name, value in found if value),
    )
