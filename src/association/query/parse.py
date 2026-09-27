"""The parser: a question, read into one :class:`~association.query.reading.Reading`
with no router.

ROADMAP plan item 6, step (b). What the model contributes arrives as data -
the names it copied out of the question and the stat key it chose
(:func:`parse` takes them as arguments; the normalizer that produces them is
step (c)) - and everything else is read from the words here, in grammar
tables: the kind and the parent intent (:data:`PARENT_GRAMMAR`), then the
scope, the window and the point through the readers the pipeline already
has (:func:`~association.query.subject.read_subject`,
:func:`~association.query.router.settle`,
:func:`~association.query.compose.move.read_point`). Those readers are the
source material the tables absorb one at a time; each table is measured on
the day10 wordings and the held-out paraphrases before the next
(``~/association-research/parser-greenfield/measure.py``).

Nothing here reaches a model, and nothing here trusts a name it was given:
a span that is no player's and no team's is dropped, never made a subject
(ISSUES.md #236).

.. versionadded:: 4.6.0
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

import duckdb

from association.query.compose.core import Refused, Unsupported
from association.query.compose.move import read_point
from association.query.compose.team import team_named_in
from association.query.entities import find_players, find_teams, suggest_players
from association.query.measures import MEASURE_WORDS
from association.query.reading import Reading
from association.query.router import settle
from association.query.subject import KIND_ASSIGNED_INTENTS, TEAM_SINGULARS, Subject, read_subject

_PAIR_MEETING = (
    r"(?!.*\b(compare|compared|comparing|contrast|evaluate|who scores more|who is better|who was better)\b)"
    r"(?=.*\b(head.to.head|matchup|when they play|meet|vs\.?|versus|against)\b)"
)
"""A pair meeting - "vs", "against", a matchup - unless a compare verb owns the pair."""

_PLAYER_LOG = (
    r"\b(game ?log|gamelog|logs?|last (\d+|ten|five) games|each game|game by game|box scores?|games? (with|where|in which|against|vs)"
    r"|how many (games|times)|highest|most .* in a game|career high|best game|single game"
    r"|first game|last game|first \d+ games|game \d|month of|\d+/\d+|march|january|february|april|december|november|october)\b"
)
"""A player's games rather than his line: a log word, a window, one game, a date."""

ANY = frozenset({"player", "pair", "team", "teams", "team_players", "position", "everyone", "player+companions", "team+companions"})
"""Every subject kind, for a grammar row that applies whatever the kind."""

PARENT_GRAMMAR: tuple[tuple[frozenset[str], str, str], ...] = (
    # (kinds the row applies to, the words, the parent intent) - first match wins.
    (ANY, r"\bfingerprint", "fingerprint"),
    (ANY, r"\bshot (chart|map|plot)|\bplot\b.*\b(shots?|threes)\b|\bshots?\b.*\b(chart|plot|map)\b|\bwhere .* shoot", "shot_chart"),
    (frozenset({"team", "teams"}), r"\b(1st|2nd|3rd|4th|first|second|third|fourth) (quarter|half)|\bquarter\b|\b[1-4]h\b|\b[1-4]q\b|\bovertime\b|\bclutch\b", "team_quarter_points"),
    (frozenset({"everyone"}), r"\b(1st|2nd|3rd|4th|first|second|third|fourth) (quarter|half)|\bquarter\b|\b[1-4]h\b|\b[1-4]q\b|\bovertime\b|\bclutch\b", "period_leaderboard"),
    (ANY, r"\b(1st|2nd|3rd|4th|first|second|third|fourth) (quarter|half)|\bquarter\b|\b[1-4]h\b|\b[1-4]q\b|\bovertime\b|\bclutch\b", "period_split"),
    (ANY, r"\bcoach", "coach"),
    (frozenset({"pair"}), _PAIR_MEETING, "player_matchup"),
    (frozenset({"pair"}), r".", "player_compare"),
    (frozenset({"teams"}), r".", "head_to_head"),
    (frozenset({"team_players"}), r".", "leaderboard"),
    (frozenset({"team"}), r"(?=.*\b(top \d+|scorers?|rebounders?|passers?|leaders?|players?)\b)(?=.*\b(top|most|best|leaders?)\b)", "leaderboard"),
    (frozenset({"team+companions"}), r"\b(with|without|when|while)\b", "with_without"),
    (frozenset({"team"}), r"\b(game ?log|(last|past|previous|most recent) (\d+|ten|five)\b|first \d+ games|each game|game by game|differential)", "game_log"),
    (frozenset({"team"}), r"\bstreak", "team_record"),
    (frozenset({"team"}), r"(?=.*\b(record|standings?|wins?|losses|w-?l|win.loss|won|lost|rec)\b)(?!.*\b(most|fewest|least|best|worst|top|rank)\b)", "team_record"),
    (frozenset({"team"}), r"\b(outlook|projection|on pace|schedule|vs other)\b", "team_outlook"),
    (frozenset({"team"}), r".", "team_stat"),
    (frozenset({"player"}), r"\bnet ?po?i?nts?\b|\bnetpts\b", "player_netpoints"),
    (frozenset({"player"}), _PLAYER_LOG, "game_log"),
    (frozenset({"player"}), r"\b(record|splits?)\b", "with_without"),
    (frozenset({"player+companions"}), r"\b(with|without|while|when)\b", "with_without"),
    (frozenset({"player"}), r".", "player_stat"),
    (frozenset({"position"}), r"\b(log|game ?log)\b", "game_log"),
    (frozenset({"position"}), r".", "leaderboard"),
    (
        frozenset({"everyone"}),
        r"(?=.*\b(team|teams|franchise|nba)\b)(?!.*\bplayers?\b)(?=.*\b(record|wins|best|worst|most|fewest|per team|allowed)\b)(?!.*\b(leaders?|points|assists|rebounds|netpoints|netpts)\b)",
        "team_leaderboard",
    ),
    (frozenset({"everyone"}), r"\bstreak", "team_record"),
    (frozenset({"everyone"}), r"\b(finals|game ?log)\b", "game_log"),
    (frozenset({"everyone"}), r".", "leaderboard"),
)
"""The parent-intent grammar: the first row whose kinds hold the subject's
kind and whose words the question matches names the parent. The children
(:data:`~association.query.subject.KIND_ASSIGNED_INTENTS`) are assigned
under it by the subject reading, as they are on the router's parent today.

.. versionadded:: 4.6.0
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
    (r"\b(3|three)[ -]?(pt|point|pointer)s?\b.{0,12}\b(percentage|pct|%)|\b3p%|\b3pt%", "threePointFieldGoalPct"),
    (r"\b(2|two)[ -]?(pt|point|pointer)s?\b.{0,12}\b(percentage|pct|%)|\b2p%|\b2pt%", "twoPointFieldGoalPct"),
    (r"\bfg ?%|\bfg percentage\b|\bfield goal percentage\b", "fieldGoalPct"),
    (r"\bft ?%|\bfree throw percentage\b", "freeThrowPct"),
    (r"\bscorers?\b|\bscores\b|\bscoring\b", "points"),
)
"""The measure grammar: the stat a question names in its own words, read
before the normalizer's key so a phrase the closed vocabulary holds never
depends on the model - the NetPoints family above all (the 3B misses most
of it), then the derived rates and the words :data:`~association.query.measures.MEASURE_WORDS`
does not hold.

.. versionadded:: 4.6.0
"""


def measure(question: str) -> str | None:
    """The measure :data:`MEASURE_GRAMMAR` or :data:`~association.query.measures.MEASURE_WORDS` names in ``question``, or ``None``."""
    for pattern, key in MEASURE_GRAMMAR:
        if re.search(pattern, question, re.IGNORECASE):
            return key
    words = " " + re.sub(r"[^a-z0-9%/+]+", " ", question.lower()) + " "
    hits = [(w, c) for w, c in MEASURE_WORDS.items() if f" {w} " in words]
    return max(hits, key=lambda x: len(x[0]))[1] if hits else None


_COUNT = r"(\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten|fifteen|twenty|twenty-five|thirty|fifty|hundred)"
_NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "fifteen": 15,
    "twenty": 20,
    "twenty-five": 25,
    "thirty": 30,
    "fifty": 50,
    "hundred": 100,
}
WINDOW_GRAMMAR: tuple[tuple[str, str | None, int | None], ...] = (
    # (the words, the order, the limit - 0 means "the number in the words") - first match wins.
    (rf"\b(last|past|previous|most recent|latest)\s+{_COUNT}\s+(games?|outings?|contests?|starts?)\b", "recent", 0),
    (rf"\bfirst\s+{_COUNT}\s+games?\b", "first", 0),
    (r"\b(last|most recent|latest|final)\s+(regular[- ]season\s+|postseason\s+|playoff\s+)?game\b", "recent", 1),
    (r"\bfirst\s+(regular[- ]season\s+|postseason\s+|playoff\s+)?game\b", "first", 1),
    (rf"\b(top|bottom)\s+{_COUNT}\b", None, 0),
    (
        r"\b(who|which (player|team|guard|forward|center))\b.*\b(led|leads|lead|has|had|have|is|was|were)\b.*\b(most|highest|best|fewest|least|lowest|top|worst"
        r"|longest|biggest|largest)\b|\b(who|which (player|team))\b.*\b(led|leads|lead)\s+(the\s+)?(league|nba|team|\w+)\s+in\b",
        None,
        1,
    ),
)
"""The window grammar: the count and the end of the rows a question asks
for, read from its own words ("last 10 games", "top 5", "his last game",
"who led the league in ...") where the router used to fill them in.

.. versionadded:: 4.6.0
"""

_LOG_OR_WINDOW_WORDS = re.compile(r"\b(log|gamelog|game log|last \d+|past \d+|first \d+)\b", re.IGNORECASE)


def _count(word: str) -> int:
    return int(word) if word.isdigit() else _NUMBERS[word.lower()]


def window(question: str, slots: dict[str, Any]) -> dict[str, Any]:
    """``slots`` with the window :data:`WINDOW_GRAMMAR` reads, where the
    stages left it unset; a limit the stages set stands."""
    if isinstance(slots.get("limit"), int) and not isinstance(slots.get("limit"), bool):
        return slots
    for pattern, order, limit in WINDOW_GRAMMAR:
        match = re.search(pattern, question, re.IGNORECASE)
        if match is None:
            continue
        out = dict(slots)
        if order and not out.get("order"):
            out["order"] = order
        if limit == 0:
            number = next((g for g in match.groups() if g and re.fullmatch(_COUNT, g, re.IGNORECASE)), None)
            if number is not None:
                out["limit"] = _count(number)
        elif limit:
            out["limit"] = limit
        return out
    return slots


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


def classify_span(con: duckdb.DuckDBPyConnection, text: str) -> str | None:
    """``"team"``, ``"player"`` or ``None`` for a span the model copied out of
    the question: a team's word, nickname or name first; then a name some
    player holds as whole words; else nothing (a division, a typo, the word
    "team" - none of them a subject, ISSUES.md #236)."""
    low = text.lower().strip().removesuffix("'s").rstrip("'")
    if low in TEAM_SINGULARS or team_named_in(con, low) is not None:
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
    if not other or other == subject.teams[0] or not _MEETING.search(question) or _LOG_OR_WINDOW_WORDS.search(question):
        return subject
    return replace(subject, kind="teams", teams=(subject.teams[0], other), opponent=None)


def parse(con: duckdb.DuckDBPyConnection, question: str, names: list[str] | None = None, stat: str = "") -> Reading:
    """``question`` as a :class:`~association.query.reading.Reading`. ``names``
    are the spans the normalizer copied out of the question and ``stat`` its
    stat key - both checked here, never trusted.

    .. versionadded:: 4.6.0
    """
    slots = _with_measure(question, _slots_from_names(con, list(names or []), stat))
    subject = _two_teams(read_subject(con, question, "other", dict(slots)), question, slots)
    parent = parent_intent(question, subject.kind, bool(subject.conditions))
    route = settle(parent, dict(slots), question)
    settled = read_subject(con, question, route.intent, dict(route.slots))
    final = settled.intent or route.intent
    point_slots = dict(route.slots)
    if final != route.intent and final in KIND_ASSIGNED_INTENTS:
        again = settle(final, dict(route.slots), question)
        final, point_slots = again.intent, dict(again.slots)
    point_slots = window(question, point_slots)
    subject = replace(subject, intent=final, teams=subject.teams if subject.kind == "teams" else settled.teams, opponent=subject.opponent if subject.kind == "teams" else settled.opponent)
    try:
        reading = read_point(con, final, point_slots, question, subject)
    except (Unsupported, Refused):
        reading = Reading(point_slots, intent=final, subject=subject)
    return replace(reading, intent=final, subject=subject, evidence=(*reading.evidence, f"parent {parent!r} from the words under kind {subject.kind!r}"))
