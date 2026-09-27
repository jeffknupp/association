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

.. versionadded:: 4.5.0
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
from association.query.router import Route, _period_asked, settle
from association.query.subject import KIND_ASSIGNED_INTENTS, TEAM_SINGULARS, Subject, question_supports, read_subject

_PAIR_MEETING = (
    r"(?!.*\b(compare|compared|comparing|contrast|evaluate|who scores more|who is better|who was better)\b)"
    r"(?=.*\b(head.to.head|matchup|when they play|meet|vs\.?|versus|against)\b)"
)
"""A pair meeting - "vs", "against", a matchup - unless a compare verb owns the pair."""

_PLAYER_LOG = (
    r"\b(game ?log|gamelog|logs?|last (\d+|ten|five) games|each game|game by game|box scores?|games? (with|where|in which|against|vs)"
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
    (frozenset({"team"}), r"\b(outlook|projection|on pace|schedule|(vs\.?|versus|against|compared (to|with)) other)\b", "team_outlook"),
    # A team's triple-doubles are its PLAYERS' (a boolean player line), which
    # the players' ranking reads under the team - not a team box-score total.
    (frozenset({"team"}), r"\b(triple|double)[ -]?doubles?\b", "leaderboard"),
    (frozenset({"team"}), r".", "team_stat"),
    (frozenset({"player"}), r"\bnet ?po?i?nts?\b|\bnetpts\b", "player_netpoints"),
    (frozenset({"player"}), _PLAYER_LOG, "game_log"),
    (frozenset({"player+companions"}), r"\b(with|without|while|when)\b", "with_without"),
    # A player's own record is the W-L of HIS games, which player_splits
    # answers (F088, "Embiid's record against Boston this year"; ISSUES.md
    # #231) - never the team's with/without split, which needs a companion.
    # Not with a line in it: "Sga record 36 plus points" is record_when, a
    # child player_stat's reading assigns.
    (frozenset({"player"}), r"(?=.*\b(record|splits?)\b)(?!.*\b\d{1,3}[\s-]*(\+|plus\b|or more\b))", "player_splits"),
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

.. versionadded:: 4.5.0
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
    (r"\b(3|three)[ -]?(pt|point|pointer)s?\b.{0,12}\b(attempts?|attempted|tries)\b|\b3pa\b", "threePointFieldGoalsAttempted"),
    (r"\b(3|three)[ -]?(pt|point|pointer)s?\b(?!.{0,20}\b(distance|range|shots?)\b)", "threePointFieldGoalsMade"),
    (r"\bscorers?\b|\bscores\b|\bscoring\b", "points"),
)
"""The measure grammar: the stat a question names in its own words, read
before the normalizer's key so a phrase the closed vocabulary holds never
depends on the model - the NetPoints family above all (the 3B misses most
of it), then the derived rates and the words :data:`~association.query.measures.MEASURE_WORDS`
does not hold.

.. versionadded:: 4.5.0
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
    (rf"\b(last|past|previous|most recent|latest|final)\s+{_COUNT}\s+((home|road|away|regular[- ]season|playoff|postseason)\s+){{0,2}}(games?|outings?|contests?|starts?)\b", "recent", 0),
    # A bare count closing the question is games: "magic vs nets last 10".
    (rf"\b(last|past|previous)\s+{_COUNT}\s*[?.!]*\s*\Z", "recent", 0),
    (rf"\bfirst\s+{_COUNT}\s+games?\b", "first", 0),
    (r"\b(last|most recent|latest|final)\s+(regular[- ]season\s+|postseason\s+|playoff\s+)?game\b", "recent", 1),
    (r"\bfirst\s+(regular[- ]season\s+|postseason\s+|playoff\s+)?game\b", "first", 1),
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

.. versionadded:: 4.5.0
"""

_LOG_OR_WINDOW_WORDS = re.compile(r"\b(log|gamelog|game log|last \d+|past \d+|first \d+)\b", re.IGNORECASE)
# A window over two teams meeting is still their meetings when a record is
# asked for ("lakers vs mavs record last 10 home games"); a log word never is.
_TWO_TEAMS_LOG_WORDS = re.compile(r"\b(log|gamelog|game log)\b", re.IGNORECASE)
_TWO_TEAMS_RECORD_WORDS = re.compile(r"\b(record|rec|w-?l|win.loss)\b", re.IGNORECASE)


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
    if low in TEAM_SINGULARS or team_named_in(con, low) is not None or _classify_span_abbreviation(con, low):
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
    return con.execute("SELECT count(*) FROM teams WHERE lower(abbreviation) = ?", [low]).fetchone() != (0,)


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


def _read_route_period(intent: str, slots: dict[str, Any], question: str) -> dict[str, Any]:
    """A team's quarter or half from the words ("first quarter", "2nd
    half") - a slot the router's model filled and the stages only read for
    the intents they assign themselves."""
    if intent != "team_quarter_points" or "period" in slots or "half" in slots:
        return slots
    asked = _period_asked(question)
    return {**slots, **asked} if asked else slots


def read_route(con: duckdb.DuckDBPyConnection, question: str, names: list[str] | None = None, stat: str = "") -> tuple[Route, Subject, str]:
    """The route the parser settles on for ``question`` - the intent and the
    slots a template reads, in the router's own shape - beside the subject
    it was read about and the parent the words named. ``names`` are the
    spans the normalizer copied out of the question and ``stat`` its stat
    key, both checked here, never trusted. This is what the agent answers
    from when the parser reads the question in place of the router (step c):
    the slots before the compiler's own repairs, exactly as a routed
    question's are.

    .. versionadded:: 4.5.0
    """
    slots = _with_measure(question, _slots_from_names(con, list(names or []), stat))
    subject = _two_teams(read_subject(con, question, "other", dict(slots)), question, slots)
    slots = _read_route_names(subject, slots)
    parent = parent_intent(question, subject.kind, bool(subject.conditions))
    # The window before the stages: they read ``order``/``limit`` as the
    # model's (a bare "last 10 games" reads both season types only beside
    # them, ``_route_game_log_recent_span``).
    route = settle(parent, window(question, slots), question)
    settled = read_subject(con, question, route.intent, dict(route.slots))
    final = settled.intent or route.intent
    point_slots = dict(route.slots)
    if final != route.intent and final in KIND_ASSIGNED_INTENTS:
        again = settle(final, dict(route.slots), question)
        final, point_slots = again.intent, dict(again.slots)
    point_slots = _read_route_period(final, window(question, point_slots), question)
    subject = replace(subject, intent=final, teams=subject.teams if subject.kind == "teams" else settled.teams, opponent=subject.opponent if subject.kind == "teams" else settled.opponent)
    return Route(final, point_slots), subject, parent


def parse(con: duckdb.DuckDBPyConnection, question: str, names: list[str] | None = None, stat: str = "") -> Reading:
    """``question`` as a :class:`~association.query.reading.Reading`: the
    route :func:`read_route` settles, read into the compiler's point.

    .. versionadded:: 4.5.0
    """
    route, subject, parent = read_route(con, question, names, stat)
    try:
        reading = read_point(con, route.intent, dict(route.slots), question, subject)
    except (Unsupported, Refused):
        reading = Reading(dict(route.slots), intent=route.intent, subject=subject)
    return replace(reading, intent=route.intent, subject=subject, evidence=(*reading.evidence, f"parent {parent!r} from the words under kind {subject.kind!r}"))
