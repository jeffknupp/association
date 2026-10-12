"""The parser: a question, read into one :class:`~association.query.reading.Reading`
with no router.

ROADMAP plan item 6, step (b). What the model contributes arrives as data -
the names it copied out of the question and the stat key it chose
(:func:`read_route` takes them as arguments, from
:func:`~association.query.normalizer.normalize`) - and everything else is
read from the words here, in grammar
tables: the kind and the parent shape the words ask (:data:`PARENT_GRAMMAR`), then the
grammar's rules (:func:`~association.query.grammar.settle`), the taggers,
one per family (:func:`_tagged`) and the point
(:func:`~association.query.point.read_point`), with who the question is
about read once (:func:`~association.query.subject.read_subject`) -
into the Reading :func:`read_route` returns. Since Phase 3, step 4,
slice (b) there is no router: its stages are the grammar's rules and the
split's tagger, and nothing carries a route between the words and the
Reading.

Nothing here reaches a model, and nothing here trusts a name it was given:
a span that is no player's and no team's is dropped, never made a subject
(ISSUES.md #236).

.. versionadded:: 5.0.0
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from association.query import lexicon, names
from association.query.cuts import CutsContext, CutsRead, read_cuts
from association.query.decisions import Decision
from association.query.entities import _edit_budget, _words, find_players, find_teams, suggest_players, team_abbreviations
from association.query.grammar import Asked, absent, settle
from association.query.lexicon import COUNT, LOG_OR_WINDOW_WORDS
from association.query.line import LinesNamed, LinesRead, lines_named, read_lines, read_period_line
from association.query.measure import BOOLEAN_KEYS, MeasureContext, named, read_measure
from association.query.measures import PERIOD_COLUMNS, PERIOD_RATE_STATS, TEAM_PERIOD_COLUMNS
from association.query.period import read_period
from association.query.point import cuts_under, lines_under, measure_under, minimum_under, period_under, read_point, span_under, window_under
from association.query.reading import (
    NETPOINTS_FINGERPRINT,
    NETPOINTS_RATINGS,
    PERIOD_LOG,
    PERIOD_RANKING,
    PLAYER_COMPARISON,
    PLAYER_LINE,
    PLAYER_LOG,
    PLAYER_MEETINGS,
    PLAYER_RANKING,
    PLAYER_SPLITS,
    PRESENCE_SPLIT,
    SHOT_CHART,
    TEAM_COACH,
    TEAM_LINE,
    TEAM_MEETINGS,
    TEAM_ONLY_ASKS,
    TEAM_OUTLOOK,
    TEAM_PERIOD_TOTAL,
    TEAM_RANKING,
    TEAM_RECORD,
    Cause,
    Claim,
    Companion,
    Cuts,
    Filtered,
    LeftOut,
    Line,
    Measure,
    PointRefused,
    PointShape,
    Reading,
    Unsupported,
    Window,
    asked_label,
)
from association.query.reading import Subject as ReadSubject
from association.query.span import SpanContext, needed, read_by, read_span
from association.query.span import claimed as claimed_once
from association.query.split import OWN_READ_ROLES, read_split, role_phrases
from association.query.subject import (
    Subject,
    _edit_distance,
    apply_subject,
    child_named,
    compared_but_unmatched,
    nicknames_in,
    opponent_read,
    player_named_on_a_team_only_question,
    question_derived_player,
    question_supports,
    read_subject,
    settle_subject,
    subject_claims,
    team_named_in,
)
from association.query.window import MinimumRead, WindowContext, contradicting_ends, read_minimum, read_window

_PAIR_MEETING = (
    r"(?!.*\b(compare|compared|comparing|contrast|evaluate|who scores more|who is better|who was better)\b)"
    r"(?=.*(\b(head.to.head|matchup|when they play|meet|vs\.?|versus|against)\b|[\ue005\ue006]))"
)
"""A pair meeting - "vs", "against", a matchup - unless a compare verb owns the pair."""

# Every count a question can spell is the lexicon's (`COUNT`), one table
# every count pattern here is built from.
_COUNT = COUNT


_PLAYER_LOG = (
    r"\b(game ?log|gamelog|logs?|last " + _COUNT + r" games|each game|game by game|box scores?|(?<!per )games? (with|where|in which|against|vs)"
    r"|how many (games|times)|highest|most .* in a game|career high|best game|single game"
    r"|first game|last game|(most recent|latest|previous|final) (\d+ )?games?|first \d+ games|game \d|month of|\d+/\d+|march|january|february|april|december|november|october)\b"
    # A player's games against a team: "games" with the opponent the
    # subject reading read right after it (lexicon.mark_opponent), its
    # versus word the opponent's.
    r"|(?<!per )\bgames? \ue005"
    # A window of games ("last 10 games", "his first game", "most recent 5
    # games") and a range opened on a dated day ("since 1/26/20"), each as
    # the window tagger and the cuts tagger typed it (lexicon.WINDOW_MARK,
    # DATE_MARK): the row reads that one stands here, and its words are the
    # taggers' to claim (slice (c)). The window words above stay for a form
    # the tagger does not type ("final games"); a typed one is marked and
    # the words cannot see it.
    r"|\ue00a|\ue00b"
)
"""A player's games rather than his line: a log word, a window, one game, a date."""

ANY = frozenset({"player", "pair", "team", "teams", "team_players", "position", "everyone", "player+companions", "team+companions"})
"""Every subject kind, for a grammar row that applies whatever the kind."""

PARENT_GRAMMAR: tuple[tuple[frozenset[str], str, PointShape], ...] = (
    # (kinds the row applies to, the words, the parent shape) - first match wins.
    (ANY, r"\bfingerprint", NETPOINTS_FINGERPRINT),
    # "plot" and the shots in either order: "threes by Plot Curry" is "plot curry's threes" reworded.
    (ANY, r"\bshot (chart|map|plot)|\bplot\b.*\b(shots?|threes)\b|\b(shots?|threes)\b.*\bplot\b|\bshots?\b.*\b(chart|plot|map)\b|\bwhere .* shoot", SHOT_CHART),
    (frozenset({"team", "teams"}), lexicon.PERIOD_INTENT_WORDS, TEAM_PERIOD_TOTAL),
    (frozenset({"everyone"}), lexicon.PERIOD_INTENT_WORDS, PERIOD_RANKING),
    (ANY, lexicon.PERIOD_INTENT_WORDS, PERIOD_LOG),
    (ANY, r"\bcoach", TEAM_COACH),
    (frozenset({"pair"}), _PAIR_MEETING, PLAYER_MEETINGS),
    (frozenset({"pair"}), r".", PLAYER_COMPARISON),
    (frozenset({"teams"}), r".", TEAM_MEETINGS),
    (frozenset({"team_players"}), r".", PLAYER_RANKING),
    (frozenset({"team"}), r"(?=.*\b(top \d+|scorers?|rebounders?|passers?|leaders?|players?)\b)(?=.*\b(top|most|best|leaders?)\b)", PLAYER_RANKING),
    # A companion's phrase led by a presence word, as the subject reading
    # typed it (lexicon.COMPANION_MARK_PRESENCE), or such a word anywhere else.
    (frozenset({"team+companions"}), r"\b(with|without|when|while)\b|\ue008", PRESENCE_SPLIT),
    # A log word, a window of games as the window tagger typed it
    # (lexicon.WINDOW_MARK: "last 10 games", "the season opener"), or the
    # window words the row read before the mark, for a form the tagger
    # does not type ("last 10 match ups").
    (
        frozenset({"team"}),
        r"\b(game ?log|(last|past|previous|most recent) " + _COUNT + r"\b|first \d+ games|each game|game by game|differential"
        r"|(first|opening|last|latest|most recent|final) game|(season )?opener)|\ue00a",
        PLAYER_LOG,
    ),
    (frozenset({"team"}), r"\bstreak", TEAM_RECORD),
    (frozenset({"team"}), r"(?=.*\b(record|standings?|wins?|losses|w-?l|win.loss|won|lost|rec)\b)(?!.*\b(most|fewest|least|best|worst|top|rank)\b)", TEAM_RECORD),
    (frozenset({"team"}), r"\b(outlook|projections?|projected|on pace|schedule|odds|chances|(vs\.?|versus|against|compared (to|with)) other)\b", TEAM_OUTLOOK),
    # A team's triple-doubles are its PLAYERS' (a boolean player line), which
    # the players' ranking reads under the team - not a team box-score total.
    (frozenset({"team"}), r"\b(triple|double)[ -]?doubles?\b", PLAYER_RANKING),
    (frozenset({"team"}), r".", TEAM_LINE),
    (frozenset({"player"}), r"\bnet ?po?i?nts?\b|\bnetpts\b", NETPOINTS_RATINGS),
    # A player's own record is the W-L of HIS games, which player_splits
    # answers (F088, "Embiid's record against Boston this year"; ISSUES.md
    # #231) - never the team's with/without split, which needs a companion.
    # Not with a line in it: "Sga record 36 plus points" is record_when, a
    # child player_stat's reading assigns. Before the log row, whose window
    # and date words ("since 1/26/20", "last 10 games") narrow a record as
    # much as a log - but not over a log word: a game log is its games.
    (
        frozenset({"player"}),
        r"(?=.*\b(record|rec|w-?l|win.loss|splits?)\b)(?!.*(\b\d{1,3}[\s-]*(\+|plus\b|or more\b)|\ue000))(?!.*\b(game ?log|gamelog|logs?|each game|game by game|box scores?)\b)",
        PLAYER_SPLITS,
    ),
    (frozenset({"player"}), _PLAYER_LOG, PLAYER_LOG),
    # A player's split by a companion - unless a versus word sets him against
    # a team, where "maxey points vs boston without embiid" is his own games
    # narrowed (ROADMAP step 3: the absence a condition, on whichever side the
    # name resolves to), not a two-sided split.
    (frozenset({"player+companions"}), r"(?=.*(\b(with|without|while|when)\b|\ue008))(?!.*(\b(?:vs\.?|versus|against)\b|[\ue005\ue006]))", PRESENCE_SPLIT),
    (frozenset({"player"}), r".", PLAYER_LINE),
    (frozenset({"position"}), r"\b(log|game ?log)\b", PLAYER_LOG),
    (frozenset({"position"}), r".", PLAYER_RANKING),
    (
        frozenset({"everyone"}),
        r"(?=.*\b(team|teams|franchise|nba)\b)(?!.*\bplayers?\b)(?=.*\b(record|wins|best|worst|most|fewest|per team|allowed)\b)(?!.*\b(leaders?|points|assists|rebounds|netpoints|netpts)\b)",
        TEAM_RANKING,
    ),
    # A record with no player in it is a team's: "worst record 2025-26" read
    # as the league's scorers, the everyone row below.
    (frozenset({"everyone"}), r"(?=.*\b(record|standings?|w-?l)\b)(?!.*\b(players?|who scored|scorers?)\b)", TEAM_RANKING),
    (frozenset({"everyone"}), r"\bstreak", TEAM_RECORD),
    # "game log", or a log after a window of games as the window tagger typed it ("last game log", lexicon.WINDOW_MARK).
    (frozenset({"everyone"}), r"\b(finals|game ?log)\b|\ue00a log\b", PLAYER_LOG),
    (frozenset({"everyone"}), r".", PLAYER_RANKING),
)
"""The parent grammar: the first row whose kinds hold the subject's kind
and whose words the question matches names the parent - what the words ask,
as the point it means bare (:class:`~association.query.reading.PointShape`:
its relation, shape and ``by``). The children
(:data:`~association.query.subject.KIND_ASSIGNED_ASKS`) are assigned under
it by the subject reading, as they are on the router's parent today.

.. versionadded:: 5.0.0

.. versionchanged:: 6.0.0
   Each row names a :class:`~association.query.reading.PointShape` (the
   grammar's key), where it named an intent (Phase 3, step 4).
"""

# The measure grammar - the stat a question names in its own words, read
# before the normalizer's key - is the lexicon's (lexicon.MEASURE_GRAMMAR)
# and the measure tagger's one reading (measure.named) since Phase 3, step
# 2; the grammar's rules take it as their context (grammar.settle).


# A row written as lookaheads describes the whole question, so it is anchored
# at its start: searched from every position, "(?!.*evaluate)" would simply
# skip past the word it excludes.
_PARENT_ROWS: tuple[tuple[frozenset[str], re.Pattern[str], PointShape], ...] = tuple(
    (kinds, re.compile((r"\A" if pattern.startswith("(?") else "") + pattern, re.IGNORECASE | re.DOTALL), parent) for kinds, pattern, parent in PARENT_GRAMMAR
)


def parent_asked(question: str, kind: str, companions: bool = False) -> PointShape | None:
    """The parent :data:`PARENT_GRAMMAR` names for ``question`` read as a
    subject of ``kind`` - what the words ask, or ``None`` where no row names
    a shape (every kind's rows end in one that matches any words, so only a
    kind no row holds); ``companions`` says the reading found a player
    named beside the subject with a role, which the ``<kind>+companions``
    rows require ("when playing away" and "when he started" name nobody).
    What the grammar read is claimed by its decision
    (:func:`~association.query.span.read_by`, in :func:`read_route`): a row
    written as lookaheads reads the whole question for the words it wants
    and the words that rule it out.

    .. versionadded:: 6.0.0
       ``parent_intent``, which returned an intent's name, until Phase 3, step 4.
    """
    for kinds, pattern, parent in _PARENT_ROWS:
        if (kind in kinds or (companions and f"{kind}+companions" in kinds)) and pattern.search(question):
            return parent
    return None


def classify_span(index: names.NameIndex, text: str) -> str | None:
    """``"team"``, ``"player"`` or ``None`` for a span the model copied out of
    the question: a team's word, nickname or name first; then a name some
    player holds as whole words; else nothing (a division, a typo, the word
    "team" - none of them a subject, ISSUES.md #236).

    .. versionchanged:: 6.0.0
       Takes the name index (:func:`~association.query.entities.names_of`)
       in place of a connection.
    """
    low = text.lower().strip().removesuffix("'s").rstrip("'")
    if low.removeprefix("the ") in lexicon.NEVER_A_NAME:
        return None
    if low in lexicon.TEAM_SINGULARS or team_named_in(index.teams, low) is not None or _classify_span_abbreviation(index.teams, low):
        return "team"
    teams = find_teams(index.teams, text)
    players = find_players(index.players, text)
    if teams and not players:
        return "team"
    if players:
        return "player"
    # A single near spelling is that player - the typo policy Jeff settled
    # (2026-09-26): the index defaults, visibly, where exactly one player is
    # within the edit budget; two or more ask, as they always did. "Embid"
    # is Joel Embiid; "jolic" (Jokic or Jovic) is nobody's here.
    for spelling in dict.fromkeys((text, text.removesuffix("s"), text.removesuffix("'s"))):
        if spelling and len(suggest_players(index, spelling)) == 1:
            return "player"
    return None


def _classify_span_abbreviation(teams: names.TeamIndex, low: str) -> bool:
    """Whether ``low`` is a team's abbreviation exactly ("phi", "gsw"): "PHI"
    is also inside Phil Handy's name, and a span that IS a team's code names
    the team."""
    if not 2 <= len(low) <= 4 or not low.isalpha():
        return False
    return low in team_abbreviations(teams)


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
    wanted = [w.casefold() for w in lexicon.AS_TYPED_WORD.findall(name)]
    words = lexicon.AS_TYPED_WORD.findall(question)
    runs = _as_typed_runs(words, wanted)
    if not runs and len(wanted) > 1 and wanted[-1] not in {w.casefold() for w in words}:
        # Completed AND corrected: "webanyama" came back "Victor Wembanyama".
        # The surname's own near spelling is what the question typed; a
        # surname the question holds as typed is a completion, which
        # `_as_typed_part` reads, not a correction.
        runs = _as_typed_runs(words, wanted[-1:])
    return " ".join(runs[0]) if wanted and len(runs) == 1 else name


def _as_typed_part(players: names.PlayerIndex, question: str, name: str) -> str:
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
    derived = question_derived_player(players, question, name)
    if derived is not None and derived.name.casefold() == name.casefold():
        return name
    part = " ".join(held)
    return part if len(find_players(players, part)) > 1 or not find_players(players, name) else name


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


def _slots_from_names(index: names.NameIndex, names: list[str], stat: str) -> dict[str, Any]:
    """The names as the slot shape the readers take today: ``player`` /
    ``players``, ``team`` and a second team as ``opponent``, ``stat``."""
    teams = [n for n in names if classify_span(index, n) == "team"]
    players = [n for n in names if n not in teams and classify_span(index, n) == "player"]
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


def _model_reading(slots: dict[str, Any]) -> Reading:
    """The model's spans as a reading of the words the subject reading
    starts from (:func:`~association.query.subject.read_subject`): the
    players and the team it copied (:meth:`~association.query.reading.Subject.from_slots`),
    a second team as the opponent, its stat as the measure - nothing more,
    since the model names nothing else."""
    opponent = slots.get("opponent")
    stat = slots.get("stat")
    return Reading(
        who=ReadSubject.from_slots({key: slots[key] for key in ("player", "players", "team") if key in slots}),
        cuts=Cuts(opponent=opponent if isinstance(opponent, str) and opponent.strip() else None),
        measure=Measure.from_slots({"stat": stat}) if isinstance(stat, str) and stat.strip() else None,
    )


def _two_teams(subject: Subject, question: str, slots: dict[str, Any]) -> Subject:
    """Two teams meeting (ISSUES.md #235): a team subject set against a
    second team, no player named, and a meeting word between them - the
    ``teams`` kind the reading only gives under ``head_to_head`` today."""
    if subject.kind != "team" or subject.players or not subject.teams:
        return subject
    other = subject.opponent or (slots.get("opponent") if isinstance(slots.get("opponent"), str) else None)
    one_teams_games = lexicon.TWO_TEAMS_LOG_WORDS.search(question) or (LOG_OR_WINDOW_WORDS.search(question) and not lexicon.TWO_TEAMS_RECORD_WORDS.search(question))
    if not other or other == subject.teams[0] or not lexicon.MEETING_WORDS.search(question) or one_teams_games:
        return subject
    return replace(subject, kind="teams", teams=(subject.teams[0], other), opponent=None)


def _read_route_names(subject: Subject, slots: dict[str, Any]) -> tuple[ReadSubject, str | None]:
    """Who the grammar's rules and the taggers are handed: the typed subject
    as the subject reading read it from the question's own words - its
    players (never a companion: "without joel embiid" is a narrowing), the
    team it is about or plays for - and the team it is set against, in place
    of the model's spans, which are only where the reading started. A player
    span the reading did not settle on and that is none of its names is kept
    as typed ("brown" beside Tatum is ten players; the reader asks), so a
    name the model found is never lost; a name the model DROPPED that the
    question holds is the reading's ("Nikola Jokic" with ``names=[]``). A
    reading that settled on no one hands the model's spans as they were,
    with the reading's kind and position group.

    .. versionchanged:: 6.0.0
       Returns the typed subject and the opponent; it built a
       ``router.Named`` until Phase 3, step 4, slice (b), which also carried
       the grammar's subject and the team words the rules now read from the
       words themselves (:mod:`association.query.grammar`).
    """
    if not (subject.players or subject.teams or subject.opponent or subject.own_team or subject.companions):
        model = ReadSubject.from_slots({key: slots[key] for key in ("player", "players", "team") if key in slots})
        opponent = slots.get("opponent")
        return replace(model, kind=subject.kind, position=subject.position), opponent if isinstance(opponent, str) and opponent else None
    players = _read_route_players(subject, slots)
    # A player's own team stays the subject stage's to write, as for a routed
    # question: the readers read it with the span it implies ("lebron as a
    # starter for Miami" is his Heat years, not this season). A team the
    # reading placed nowhere stays as the model filed it.
    placed = subject.opponent or subject.own_team
    team = subject.teams[0] if subject.teams and subject.kind in ("team", "teams", "team_players", "everyone", "position") else (None if placed else slots.get("team"))
    opponent = subject.teams[1] if subject.kind == "teams" and len(subject.teams) > 1 else subject.opponent
    who = ReadSubject(kind=subject.kind, players=tuple(players), teams=(team,) if team else (), position=subject.position)
    return who, opponent or None


def _read_route_players(subject: Subject, slots: dict[str, Any]) -> list[str]:
    """The subject's players, then each player span of the model's that is
    none of the reading's names or companions (``question_supports``, so
    "lebron" is LeBron James and "embid" Joel Embiid)."""
    read = (*subject.players, *subject.companions)
    typed = [slots["player"]] if isinstance(slots.get("player"), str) else list(slots.get("players") or [])
    return [*subject.players, *(span for span in typed if not any(question_supports(name, span) for name in read))]


# The columns asked for beside a ranking ("top 5 scorers with their rebounds
# and assists", F017) are the measure tagger's reading since Phase 3, step 2
# (``Measure.beside``, over lexicon.FIELDS_AFTER and TEAM_FIELD_WORDS).

# A quarter or half used as a CONDITION on which games count is the lines
# tagger's reading (``line.read_period_line``), made here before the
# grammar's rules so its words are blanked out of the question they read.


def _read_route_beside(subject: Subject, question: str) -> bool:
    """Whether a companion stands beside the subject for the ``<kind>+companions``
    rows of :data:`PARENT_GRAMMAR` - the with/without split. Not a player's
    teammate whose only role is a start or the bench: that narrows the
    player's own games ("maxey points when embiid starts", "in games embiid
    started"), so the player's own row names the parent (his line, his log,
    a shot chart) and the role is a ``conditions`` entry on it - where the
    split's ``when`` row sent it to the with/without split, and a missing
    "when" to a pair of players compared."""
    if not subject.conditions:
        return False
    if subject.kind != "player" or any(c.predicate not in OWN_READ_ROLES for c in subject.conditions):
        return True
    return any(lexicon.DENIED_ROLE.search(match.group(2)) for match, _ in role_phrases(subject.conditions, question))


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
MIN_QUESTION_WORDS = 3
"""A question with fewer words than this is refused unread.

Jeff, 2026-09-29: short or nonsensical questions are refused with a generic
sentence, and no effort is spent on them - most of the StatMuse feed's
two-word rows ("Tatum rec", "bam stats", "jaylen brown") are a user hitting
enter before the question was typed, and a system that guesses at them
answered "Tatum rec" with his splits. Measured before choosing the line: 215
of the large feed's 2,285 questions have one or two words, almost all bare
names; in the 175-question yardstick only "Tatum rec" has under three, and
every three-word question ("76ers away record", "luka td3s home") answers.

.. versionadded:: 5.0.0

.. versionchanged:: 6.0.0
   The parser's: it was ``refusals.MIN_QUESTION_WORDS``.
"""


def too_short(question: str) -> Cause | None:
    """The parser's first reading of the words: a question of fewer than
    :data:`MIN_QUESTION_WORDS` words is refused unread, by the
    ``too_short`` :class:`~association.query.reading.Cause` (the words as
    typed, which its generic sentence quotes), or None. Decided from the
    words alone, before the normalizer is asked, so a short question costs
    no model call; the answering loop refuses by this verdict and the
    planner says it (:func:`~association.query.compose.plan.refusal_result`).

    .. versionadded:: 5.0.0

    .. versionchanged:: 6.0.0
       The parser's, returning the cause: it was ``refusals.too_short``,
       which returned the sentence.
    """
    if len(question.split()) >= MIN_QUESTION_WORDS:
        return None
    return Cause(kind="too_short", facts={"asked": question.strip()})


_READ_ROUTE_APOSTROPHES = str.maketrans({"\u2019": "'", "\u2018": "'"})


def _read_route_folded(text: str) -> str:
    """``text`` with each typographic apostrophe (U+2019, U+2018) read as a
    straight one - the fold at the parser's door, for :func:`read_route`
    and :func:`reading_from_route` alike."""
    return text.translate(_READ_ROUTE_APOSTROPHES)


def read_route(index: names.NameIndex, question: str, names: list[str] | None = None, stat: str = "") -> Reading:
    """The :class:`~association.query.reading.Reading` of ``question``:
    everything after the parser answers from it. ``names`` are the spans the
    normalizer copied out of the question and ``stat`` its stat key, both
    checked here, never trusted; the subject is read once from the words
    (:func:`~association.query.subject.read_subject`), the parent grammar and
    a child grammar name what the words ask, the grammar's rules settle it
    (:func:`~association.query.grammar.settle`), the taggers read the
    filters in the order each reads what the ones before it settled
    (:func:`_tagged`), and the parser's last step writes the Reading. A
    typographic apostrophe (U+2019, U+2018) in the question or in a name is
    read as a straight one, so "the 2010's" typed with one is a decade, and
    "D'Angelo Russell" a player.

    .. versionadded:: 5.0.0

    .. versionchanged:: 6.0.0
       Takes the name index (:class:`~association.query.names.NameIndex`,
       :func:`~association.query.entities.names_of`) in place of a
       connection: the reader runs no statement.

    .. versionchanged:: 6.0.0
       Returns the Reading (Phase 3, step 4, slice (b)): it returned a
       ``router.Route`` beside the subject and the parent's label until
       then, and ``reading_from_route`` - folded in here - wrote the Reading
       from the Route.
    """
    # The model's reply as given: the words it accounts for are no unread
    # words (Reading.unread, by the claims ledger's rule).
    given = tuple(names or ())
    asked = question
    question = _read_route_folded(question)
    names = [_read_route_folded(name) for name in names or []]
    slots = _slots_from_names(index, [_as_typed_part(index.players, question, _as_typed(question, name)) for name in names], stat)
    # THE reading of who the question is about: everything after this
    # settles it (subject.settle_subject), nothing reads the names again -
    # not even to ask which words it read (its claims are its own, below).
    read = read_subject(index, question, None, _model_reading(slots))
    subject = _two_teams(read, question, slots)
    # A quarter or half used as a condition on which games count is read
    # here and its words blanked out of the question the grammar and the
    # taggers see (#275): left in, "after making one three in first quarter"
    # is the period relation's question and "one three" a line on nothing.
    # Blanked to spaces of equal length, not cut, so every claim after it
    # is where the real question holds it (the period slice's P4).
    in_period = read_period_line(question)
    period_lines: tuple[Line, ...] = ()
    period_claims: tuple[Claim, ...] = ()
    if in_period is not None:
        claim = in_period[1]
        question = question[: claim.start] + " " * (claim.end - claim.start) + question[claim.end :]
        period_lines, period_claims = (in_period[0],), (claim,)
    # A ranking's qualifier as stated ("minimum 100 games played", "with at
    # least 1000 fg attempted") is read here and blanked the same way: left
    # in, its number is a season to the span ("minimum 2000 attempts") and
    # its "attempts" the measure's ("3pt percent minimum 20 attempts" ranked
    # 3-point attempts). The key decides below whether it stands
    # (point.minimum_under; ISSUES.md #377).
    unblanked = question
    stated_minimum = read_minimum(question)
    question = _blanked(question, stated_minimum.claims)
    # Who the grammar's rules and the taggers are handed: the names the
    # reading read, typed.
    who, opponent = _read_route_names(subject, slots)
    beside = _read_route_beside(subject, question)
    # The choosers read the lines as the lines tagger types them, never by
    # their words: each line's number and stat written over by its mark
    # (lexicon.mark_lines), so a grammar's row reads "a line stands here"
    # and claims only its own words around it - the line's are the lines
    # tagger's (Phase 3, step 4, slice (b): until it the grammars read the
    # numbers themselves, and each claim joined the line's).
    named_lines = lines_named(question)
    numbered = named_lines.numbered(question)
    # The parent grammar reads the opponent as the subject reading typed
    # it, the same way: "games against boston" is a player's games with a
    # team set against him, and "against boston" the opponent's words.
    opposed = lexicon.mark_opponent(numbered, tuple((c.start, c.end) for c in opponent_read(index.teams, question, opponent)))
    # And the companions' phrases, the windows of games and the calendar
    # days the subject reading, the window tagger and the cuts tagger read,
    # the same way (slice (c)): a row reads "a companion stands here", "a
    # window of games here", "a day here", and claims only its own words
    # around them - the phrase's, the window's and the day's are their
    # readers' to claim, so no claim is joined from two readings of one word.
    companions_at = tuple((start, end) for start, end, _ in read.companion_phrases)
    prepared = lexicon.mark_companions(opposed, companions_at)
    prepared = lexicon.mark_spans(prepared, _window_spans(question), lexicon.WINDOW_MARK)
    prepared = lexicon.mark_spans(prepared, _date_spans(question, opponent, read.conditions), lexicon.DATE_MARK)
    parent = parent_asked(prepared, subject.kind, beside)
    # What the grammar read: the words whose deletion names another parent
    # (span.read_by) - a row's own words ("fingerprint", "record"), a word
    # that rules an earlier row out ("compare" keeps a pair off the matchup
    # row), the meeting word that makes two teams the kind it reads ("how
    # many times did the 76ers play boston"); not the "game" of "game log",
    # which "log" names alone.
    parent_claims = read_by(prepared, "intent", lambda probed: parent_asked(probed, _two_teams(read, probed, slots).kind, beside))
    # The rules are handed what the phrases typed, never their words: a
    # record "when <someone> scored N" is a reached companion led by "when".
    reached_when = any(predicate == "reached" and question[start:end].lower().startswith("when") for start, end, predicate in read.companion_phrases)
    settled_ask, decisions, words, child_claims = _read_route_staged(question, stat or None, parent, read, who, opponent, lines=named_lines, reached_when=reached_when)
    final = settled_ask.asked
    scope, tagger_claims = (
        _tagged(question, settled_ask, read.conditions, lines=period_lines, model_stat=stat or None, minimum=(stated_minimum, unblanked)) if not settled_ask.coach else (Reading(), ())
    )
    # The one reading, settled under the key the words end with: what the
    # parser's last step and everything after it answer from.
    settled = settle_subject(read, final, parent=parent, words=words)
    # The companions' phrases are the subject reading's claims, the line in
    # a quarter the parser's; the rules' and the taggers' read first.
    claims = claimed_once([*claimed_once([*settled_ask.claims, *tagger_claims]), *child_claims, *period_claims, *read.claims, *parent_claims])
    return _reading(index, asked, final, scope, settled, decisions=decisions, claims=claims, model_names=given, model_stat=stat)


def _blanked(question: str, claims: tuple[Claim, ...]) -> str:
    """``question`` with each claim's characters written over by spaces of
    equal length, not cut, so every claim read after it is where the real
    question holds it."""
    for claim in claims:
        question = question[: claim.start] + " " * (claim.end - claim.start) + question[claim.end :]
    return question


def marked_for_grammar(question: str) -> str:
    """``question`` as :func:`read_route` hands it to the parent grammar
    (:func:`parent_asked`) where no name is read from it: each window of
    games and each range opened on a dated day written over by its mark
    (:func:`_window_spans`, :func:`_date_spans`). The opponent's and the
    companions' marks need the subject reading, which only ``read_route``
    runs; a test of the grammar's rows over words alone goes through here.

    .. versionadded:: 6.0.0
    """
    prepared = lexicon.mark_spans(question, _window_spans(question), lexicon.WINDOW_MARK)
    return lexicon.mark_spans(prepared, _date_spans(question, None, ()), lexicon.DATE_MARK)


def _window_spans(question: str) -> tuple[tuple[int, int], ...]:
    """Where the window tagger reads a window of GAMES from - the grammar's
    end and count, and the looser end words
    (:func:`~association.query.window.read_window`) - which the parent
    grammar reads as :data:`~association.query.lexicon.WINDOW_MARK`. A
    ranking's "top 5" names a count and no end, and is a word the ranking
    rows read, so it stays words; so does a count of seasons, which a
    history's child grammar reads; and so does one game named as the
    subject's own ("his last game" - the grammar's "last game" inside it
    is marked), whose stretch reaches over words other rows read ("barnes's
    first quarter game": the period's)."""
    found = read_window(question, WindowContext())
    spans: list[tuple[int, int]] = []
    order, _count, claim = found.grammar
    if order is not None and claim is not None:
        spans.append((claim.start, claim.end))
    spans.extend((claim.start, claim.end) for _, claim in found.order_words)
    return tuple(spans)


_SLASHED_DAY = re.compile(r"\d+/\d+")


def _date_spans(question: str, opponent: str | None, companions: tuple[Companion, ...]) -> tuple[tuple[int, int], ...]:
    """Where the cuts tagger reads a range opened on a dated day from
    ("since 1/26/20", a situation with the day in it) - which the parent
    grammar reads as :data:`~association.query.lexicon.DATE_MARK`. A
    calendar day, a month and a weekday stay words: the log row reads a
    month's name and a slashed day itself, as it did."""
    found = read_cuts(question, CutsContext(opponent=opponent, without=absent(companions)))
    return tuple((c.start, c.end) for c in found.claims if c.what == "situation" and _SLASHED_DAY.search(question[c.start : c.end]) is not None)


def _read_route_staged(
    question: str,
    stat: str | None,
    parent: PointShape | None,
    read: Subject,
    who: ReadSubject,
    opponent: str | None,
    *,
    lines: LinesNamed | None = None,
    reached_when: bool = False,
) -> tuple[Asked, tuple[Decision, ...], str | None, tuple[Claim, ...]]:
    """The grammar's rules (:func:`~association.query.grammar.settle`), run
    ONCE: under the child the subject's shape and the question's words name
    for ``parent`` (:func:`~association.query.subject.child_named` - a count
    of 30+ point games under a game log, a history over the past 4 seasons
    under a player's line, a team's record under a companion's line), or
    under the parent itself. The rules may decline a child (a count with no
    threshold in the text is a ranking), and then run again under the
    parent - the one case of a second run, measured at 4 of 628 recorded
    questions, every one a history the rules read as a line (and 59 of
    2,082 wordings outside the corpus). Until 5.0.0's last change they ran
    under the parent first and again under each child to see whether it
    held, and once more under the one that did: 1.7 runs a question on
    average, up to four.

    Returns what the rules settled, the decisions made getting to it (the
    key moving off the parent, and why - :attr:`Reading.decisions
    <association.query.reading.Reading.decisions>`), the words that named
    the child, if one stands, and the words the child grammars read. The
    child grammars read the question with each line the lines tagger reads
    written over by its mark, the rules with each line's number marked and
    the typed ``lines`` beside it (:class:`~association.query.line.LinesNamed`,
    read here where none is handed); the words that named a child are the
    question's own, where they stand."""
    lines = lines if lines is not None else lines_named(question)
    marked, numbered = lines.marked(question), lines.numbered(question)
    named = child_named(read, parent, marked, original=question)
    companions = read.conditions
    decisions: list[Decision] = []
    # The words the child grammars read, whether a child stands or the
    # rules decline it (the decision records them either way): the words
    # whose deletion names another child, or its words otherwise (a child's
    # words are said in its reason) - span.read_by.
    child_claims = read_by(marked, "intent", lambda probed: child_named(read, parent, probed))
    if named is not None:
        child, words = named
        staged = settle(numbered, child, subject=who, opponent=opponent, companions=companions, stat=stat, lines=lines, reached_when=reached_when)
        # A team's record under a companion's line names no words, and the
        # rules' own settling of it stands. The decisions record the moves
        # by their labels, as the trace prints them.
        if staged.asked == child or words is None:
            decisions.append(Decision("parser", "intent", asked_label(parent), asked_label(child), _why_named(child, words)))
            if staged.asked != child:
                decisions.append(Decision("parser", "intent", asked_label(child), asked_label(staged.asked), "the stages settle it from the question's words"))
            return staged, tuple(decisions), words, child_claims
        decisions.append(Decision("parser", "intent", asked_label(child), asked_label(parent), f"the words {words!r} name {asked_label(child)}, and the stages declined it"))
    staged = settle(numbered, parent, subject=who, opponent=opponent, companions=companions, stat=stat, lines=lines, reached_when=reached_when)
    if staged.asked != parent:
        decisions.append(Decision("parser", "intent", asked_label(parent), asked_label(staged.asked), "the stages settle it from the question's words"))
    return staged, tuple(decisions), None, child_claims


def _why_named(child: PointShape, words: str | None) -> str:
    """The reason a child stands, as :attr:`~association.query.subject.Subject.intent_reason` says it."""
    return f"the words {words!r} name {asked_label(child)}" if words is not None else "a team's record in the games a player named beside it reached a line"


def _tagged(
    question: str,
    settled: Asked,
    companions: tuple[Companion, ...],
    *,
    lines: tuple[Line, ...] = (),
    model_stat: Any = None,
    side: str | None = None,
    shot_value: int | None = None,
    fields: tuple[str, ...] = (),
    minimum: tuple[MinimumRead, str] | None = None,
) -> tuple[Reading, tuple[Claim, ...]]:
    """The taggers, each the one reader of its family, over the key and the
    names the grammar's rules settled (``settled``), in the order each reads
    what the ones before it wrote: the split (the cuts tagger reads it), the
    lines (the measure tagger reads the stat a line names), the measure (the
    window tagger reads whether the stat is a yes/no one), the period, the
    cuts (the span tagger reads a date, a series game, an ordinal season and
    a dated range's year), the window (the span tagger reads it), the span
    last. ``companions`` are who the subject reading found beside the
    subject (an absent teammate is no opponent; a companion's line is his);
    ``lines`` the lines read before the rules (a line in a quarter, whose
    words the parser blanks), which join the tagger's; ``model_stat`` the
    model's stat key (the measure tagger's claims are cut against it, the
    words' key read again from each probe); ``side``,
    ``shot_value`` and ``fields`` a test's model-era context for the measure
    (the normalizer files none). A ranking's stated qualifier
    (:func:`~association.query.window.read_minimum`) is read first and its
    words blanked out of what every other tagger reads ("minimum 2000
    attempts" is no season, its "attempts" no measure); ``minimum`` is that
    reading and the question as typed where the parser read it already,
    before the grammar (:func:`read_route`). Returns the typed scope and
    each tagger's claims, cut to the words its reading depends on
    (:func:`~association.query.span.needed`).

    .. versionadded:: 6.0.0
       ``router._settle``'s taggers until Phase 3, step 4, slice (b), run
       after the stages over their slot dict.
    """
    asked = settled.asked
    stated, typed = minimum if minimum is not None else (read_minimum(question), question)
    question = _blanked(question, stated.claims)
    kept = minimum_under(asked, stated)
    # Each family is read twice over: the tagger reads the words alone, and
    # the key the words ask decides what of it stands (point.*_under) - the
    # point reader's reading of the words under its key, written here where
    # the parser writes the Reading, never a tagger's own.
    split = read_split(question, companions)
    beside_line = any(c.line is not None for c in companions)
    read = lines_under(asked, read_lines(question), beside_line=beside_line)
    measure_context = _tagged_measure_context(settled, question, read, side=side, shot_value=shot_value, fields=fields)
    measure = measure_under(asked, read_measure(question), measure_context)
    period = period_under(asked, read_period(question))
    cuts_context = CutsContext(opponent=settled.opponent, without=absent(companions))
    cuts = cuts_under(asked, read_cuts(question, cuts_context), split=split.split)
    window_context = WindowContext(boolean_stat=measure.measure is not None and measure.measure.key in BOOLEAN_KEYS)
    window = window_under(asked, read_window(question, window_context))
    span = span_under(asked, read_span(question, _tagged_span_context(settled, question, window=window.window, cuts=cuts)))
    scope = Reading(
        who=settled.subject, split=split.split, lines=(*lines, *read.lines), measure=measure.measure, period=period.period, cuts=cuts.cuts, window=window.window, span=span.span, minimum=kept.minimum
    )
    # Each family's claims cut to the words it could not do without, asked
    # of the tagger and the key's reading together (span.needed), with the
    # context its own words give it read again from the same words (the
    # measure the words name, an order word, the span's guard words); and
    # every other word its reading turns on claimed beside them, under the
    # family's name - the possessive of "curry's last game", the "record"
    # of a matchup's career.
    claims = (
        *needed(question, split.claims, lambda q: read_split(q, companions).split, outside="split", gives_up=True),
        *needed(question, period.claims, lambda q: period_under(asked, read_period(q)).period, outside="period", gives_up=True),
        *needed(question, cuts.claims, lambda q: _tagged_cuts(cuts_under(asked, read_cuts(q, cuts_context), split=split.split)), outside="cuts", gives_up=True),
        *needed(question, window.claims, lambda q: window_under(asked, read_window(q, window_context)).window, outside="window", gives_up=True),
        # The qualifier's over the question as typed, its words blanked in the rest.
        *needed(typed, kept.claims, lambda q: minimum_under(asked, read_minimum(q)).minimum, outside="minimum", gives_up=True),
        *needed(question, read.claims, lambda q: _tagged_lines(lines_under(asked, read_lines(q), beside_line=beside_line)), outside="line", gives_up=True),
        *needed(
            question,
            measure.claims,
            lambda q: (
                measure_under(
                    asked, read_measure(q), _tagged_measure_context(replace(settled, stat=_tagged_worded_key(q, model_stat)), q, read, side=side, shot_value=shot_value, fields=fields)
                ).measure
            ),
            outside="measure",
            gives_up=True,
        ),
        *needed(question, span.claims, lambda q: span_under(asked, read_span(q, _tagged_span_context(settled, q, window=window.window, cuts=cuts))).span, outside="span", gives_up=True),
    )
    return scope, claims


def _tagged_worded_key(question: str, model_key: Any) -> Any:
    """The measure key the taggers read beside the words: the one the words
    name (:func:`~association.query.measure.named`), else the model's."""
    worded = named(question)
    return worded[0] if worded is not None else model_key


def _tagged_cuts(read: CutsRead) -> tuple[Cuts, int | None]:
    """What the cuts tagger writes into the reading: the cuts, and the year
    a dated range opened at, which the span tagger reads."""
    return read.cuts, read.dated_since


def _tagged_lines(read: LinesRead) -> tuple[tuple[Line, ...], str | None]:
    """What the lines tagger writes into the reading: the lines, and the
    stat it read beside a threshold, which the measure tagger reads."""
    return read.lines, read.stat


def _tagged_measure_context(settled: Asked, question: str, read: LinesRead, *, side: str | None, shot_value: int | None, fields: tuple[str, ...]) -> MeasureContext:
    """What the measure tagger reads beside the words
    (:class:`~association.query.measure.MeasureContext`): the settled key,
    the measure key the rules read (the words' over the model's; a test's
    side, shot value and columns beside it), the stat the lines tagger read
    beside a line and whether a line is keyed, and whether a game of
    ordering was named."""
    stat = settled.stat
    return MeasureContext(
        key=stat if isinstance(stat, str) and stat.strip() else None,
        side=side,
        shot_value=shot_value if isinstance(shot_value, int) and not isinstance(shot_value, bool) else None,
        fields=tuple(fields),
        line_stat=read.stat,
        keyed_line=any(line.keyed for line in read.lines),
        order_named=any(pattern.search(question) for pattern in lexicon.ORDER_WORDS.values()),
    )


def _tagged_span_context(settled: Asked, question: str, *, window: Window, cuts: CutsRead) -> SpanContext:
    """What the span tagger reads beside the words
    (:class:`~association.query.span.SpanContext`): the settled key, the
    typed window, the typed cuts (a date, a game of a series, an ordinal
    season, and the year a range opened on a dated day starts the seasons
    at), and the guard words of other families it keeps until their slices
    move them to the lexicon."""
    return SpanContext(
        player_named=settled.subject.player is not None,
        window_named=window.count is not None,
        versus=lexicon.VERSUS_OR_AGAINST.search(question) is not None,
        how_many=lexicon.HOW_MANY_GAMES.search(question) is not None,
        record=lexicon.RECORD_WORD.search(question) is not None,
        order=window.order,
        limit=window.count,
        date=cuts.cuts.date,
        game_n=cuts.cuts.game_n,
        season_n=cuts.cuts.season_n,
        dated_since=cuts.dated_since,
    )


def _reading(
    index: names.NameIndex,
    question: str,
    asked_key: PointShape | None,
    scope: Filtered,
    subject: Subject,
    *,
    decisions: tuple[Decision, ...] = (),
    claims: tuple[Claim, ...] = (),
    model_names: tuple[str, ...] = (),
    model_stat: str = "",
) -> Reading:
    """The parser's last step, over what the words were read as: the key
    they ask, the typed scope the taggers wrote, the subject reading
    settled under that key, the parser's decisions and the claims of every
    rule that read them, and the model's reply (its names and stat, which no
    unread word counts) - written into the Reading through the subject's one
    writer (:func:`~association.query.subject.apply_subject`), with the
    point read and what the words name that nothing reads. A test that
    hands the answer side a reading of its own words builds it here
    (``tests/routed.py``)."""
    asked = question
    question = _read_route_folded(question)
    applied = apply_subject(subject, scope, asked=asked_key)
    # The subject reading's claims on the names it settled and their
    # position group, beside the taggers' and the companions' the words
    # carry. The subject is read once, so its claims are the ones it makes
    # where it reads - never found by reading it again.
    found = [*claims, *subject_claims(index.teams, question, applied.scope)]
    reading = Reading(
        **applied.scope.filters(),
        asked=applied.asked,
        intent=asked_label(applied.asked),
        subject=subject,
        decisions=(*_subject_decisions(subject), *decisions, *applied.decisions),
        misread=tuple(applied.dropped),
        claims=claimed_once(found),
    )
    pointed = with_point(question, reading)
    # What the words name that nothing reads, read once, here: a refusal
    # said before any reader runs, and the shapes recognized and read by
    # nothing, said where the answer side declines (the question as it was
    # asked, unfolded, as the answering loop read it until Phase 3, step 0).
    players, teams = index.players, index.teams
    refused = _reading_from_route_refused(players, teams, asked, pointed)
    unsupported = _reading_from_route_unsupported(asked, pointed)
    left_out = _reading_from_route_left_out(players, asked, pointed)
    # What the point reader's word tables and the refusals read, by their
    # decisions (span.read_by): "most", "at least 400 attempts", "per game",
    # a championship. The point reader reads its tables over the whole
    # question, as the grammars do.
    found += read_by(question, "point", lambda probed: _reading_from_route_point(with_point(probed, reading)))
    found += read_by(
        asked,
        "refused",
        lambda probed: (_reading_from_route_refused(players, teams, probed, pointed), _reading_from_route_unsupported(probed, pointed), _reading_from_route_left_out(players, probed, pointed)),
    )
    found = list(claimed_once(found))
    return replace(
        pointed,
        refused=refused,
        unsupported=unsupported,
        left_out=left_out,
        claims=tuple(found),
        # The words nothing claimed, by the claims ledger's rule, over the
        # question as it was asked (the fold keeps every position).
        unread=lexicon.unread_words(asked, [(c.start, c.end) for c in found], (*model_names, *_reading_from_route_names(applied.scope, subject, applied.asked)), model_stat),
    )


def _reading_from_route_point(reading: Reading) -> tuple[Reading | None, str | None, Cause | None]:
    """What the point reader wrote: the point, or why there is none."""
    return reading.point, reading.point_declined, reading.point_refusal


def _reading_from_route_names(scope: Filtered, subject: Subject, asked: PointShape | None) -> tuple[str, ...]:
    """Every name the reading settled on - the scope's and the subject's -
    whose words no reader has to claim
    (:func:`~association.query.lexicon.content_words`): the words of every
    name are the reading's own, which the claims ledger counts as read
    (``names_read``) - a word of "Stephen Curry" the model copied only
    "steph" of, which the nickname table completes. The names are the typed
    subject's players and teams, the cuts' opponent and tenure, the
    companions the slot era carried by name (the absent ones; the teammates
    who played, on the with/without split alone and only where none is
    absent) and the subject reading's own players, teams, opponent and
    tenure - what the slot-era projection listed until Phase 3, step 4,
    slice (c), read off the typed values."""
    found: list[str] = [*scope.who.players, *scope.who.teams, *(name for name in (scope.cuts.opponent, scope.cuts.tenure) if name is not None)]
    absent = [c.player for c in scope.companions if c.absent]
    if asked == PRESENCE_SPLIT and not absent:
        found.extend(c.player for c in scope.companions if c.side == "own" and c.predicate == "played")
    found.extend(absent)
    found.extend((*subject.players, *subject.teams, *(name for name in (subject.opponent, subject.own_team) if name is not None)))
    return tuple(found)


def with_point(question: str, reading: Reading) -> Reading:
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

    .. versionchanged:: 6.0.0
       Takes no connection: the point reader reads the Reading and the words.
    """
    try:
        point = read_point(reading, question)
    except PointRefused as exc:
        # Before the decline: a refusal with a cause is a kind of decline.
        return replace(reading, point_refusal=exc.cause)
    except Unsupported as exc:
        return replace(reading, point_declined=str(exc))
    return replace(reading, point=point)


# --- What the words name that nothing reads ------------------------------------
#
# Phase 3, step 0: the answering loop re-read the question after the parser
# had settled it, in three places - refusals.by_question (a championship),
# entities.player_named_on_a_team_only_question (a player named on a team's
# question) and refusals.unanswerable (eight shapes nothing reads). Each was a
# reading of the words the reader should make; it is made here, once, and
# carried on the Reading as reading.Cause values the planner says through the
# one phrase table (compose.plan.refusal_result, say.refusal_phrase).

_CHAMPIONSHIP = re.compile(r"\b(?:championships?|champions?|nba\s+titles?|won\s+the\s+(?:title|finals)|title\s+winners?|finals\s+winners?)\b", re.IGNORECASE)
"""A championship or a title won: no table holds titles, and a team
ranking would answer the question fluently and wrongly."""
_TITLE_ODDS = re.compile(r"\btitle\s+odds\b|\bchampionship\s+odds\b", re.IGNORECASE)
""""Title odds": a regular-season projection ``team_outlook`` answers, not a
championship."""
_BENCH_WORD = re.compile(r"\bbench\b", re.IGNORECASE)


def _reading_from_route_refused(players: names.PlayerIndex, teams: names.TeamIndex, question: str, reading: Reading) -> Cause | None:
    """The refusal the words come to that no point answers past, said
    before any reader runs - the Reading's
    :attr:`~association.query.reading.Reading.refused`:

    - a championship (``championship``): "Show which team won the nba
      championship for the past 10 years" (Jeff's session, 2026-09-24) was
      read as a team ranking and ranked regular-season records since 2017.
      The warehouse holds every playoff game and no table of titles; a
      champion is derivable (the winner of a postseason's last game) and
      nothing derives it yet. "Title odds" is ``team_outlook``'s projection
      and is left alone. Was ``refusals.by_question``.
    - a player named on a question whose intent has no reading for one
      (``no_player_reading``, :data:`~association.query.reading.TEAM_ONLY_ASKS`):
      "alperen şengün alltime record" read as ``team_leaderboard`` and
      answered the league standings, Sengun never read (yardstick-v2 F111) -
      the one player the words name and no real team
      (:func:`~association.query.subject.player_named_on_a_team_only_question`).
      Was the answering loop's own check.
    - both ends of the span at once (``window_ends_contradict``): "lebron
      james last 5 first games stats" answered his first 10 games, "last"
      and "5" unread (ISSUES.md #378) - no end is right over the other, so
      the refusal names both
      (:func:`~association.query.window.contradicting_ends`).

    The championship first, as the answering loop asked them, then the
    window's ends."""
    if _CHAMPIONSHIP.search(question) and not _TITLE_ODDS.search(question):
        return Cause(kind="championship", facts={"intent": reading.intent})
    ends = contradicting_ends(question)
    if ends:
        return Cause(kind="window_ends_contradict", facts={"ends": [question[claim.start : claim.end] for claim in ends]})
    if reading.asked not in TEAM_ONLY_ASKS:
        return None
    player = player_named_on_a_team_only_question(players, teams, question, reading.who, reading.span.season)
    return Cause(kind="no_player_reading", facts={"player": player, "intent": reading.intent}) if player is not None else None


def _reading_from_route_unsupported(question: str, reading: Reading) -> tuple[Cause, ...]:
    """What the words name that nothing here reads - ``ROADMAP-TYPES.md``'s
    ``Unsupported(what, as_typed)`` filter, carried as causes (the kind is
    the ``what``; the facts hold the words as typed) - in the order the
    answering loop asked ``refusals.unanswerable``'s checks, which these
    are: a playoff round, a situation nothing reads (an age, an unread
    conference phrase, anything else), a stat a quarter's line does not
    rebuild, a quarter as a condition with no line, a team's quarter of a
    stat nothing holds, bench points, a team's total of its players'
    triple-doubles. Recognized whatever answers: the answering loop says
    the first only where the answer side declined and the coverage floor
    did not refuse - a question the compiler answers is answered (the
    Reading's :attr:`~association.query.reading.Reading.unsupported`).
    Measured before the move (``~/association-research/stages/refusal_sites.py``,
    on ``87cc782``): every question one of these recognizes was refused by
    it - 6 of the 628 recorded and 157 of the 2,082 feed questions - and no
    question was recognized by two."""
    found = (
        _unsupported_playoff_round(reading),
        _unsupported_situation(reading),
        _unsupported_period_stat(reading),
        _unsupported_period_as_condition(question, reading),
        _unsupported_team_period_stat(reading),
        _unsupported_bench_points(question, reading),
        _unsupported_team_boolean_count(reading),
    )
    return tuple(cause for cause in found if cause is not None)


def _unsupported_playoff_round(reading: Reading) -> Cause | None:
    """A named round: the games carry no round or series label (ISSUES #10)."""
    playoff_round = reading.cuts.round
    if not isinstance(playoff_round, str) or not playoff_round.strip():
        return None
    return Cause(kind="playoff_round", facts={"intent": reading.intent, "round": playoff_round})


def _unsupported_situation(reading: Reading) -> Cause | None:
    """A ``situation`` that names neither a calendar narrowing nor a
    conference or division (:class:`~association.query.reading.Situation`,
    parsed once where the words were read, by the same two readers the
    relations' own shared steps apply): an age (no birth dates on record),
    a conference or division word in a shape the alignment reader does not
    take ("the Central Division these days" - the words are right and only
    the phrasing is not, a different sentence), or anything else the games
    are not read by (``reads_as``: ``age``, ``alignment``, ``other``)."""
    situation = reading.cuts.situation
    if situation is None or situation.read:
        return None
    reads_as = "age" if lexicon.AGE_WORDS.search(situation.text) else "alignment" if lexicon.CONFERENCE_OR_DIVISION.search(situation.text) else "other"
    return Cause(kind="non_calendar_situation", facts={"intent": reading.intent, "situation": situation.text, "reads_as": reads_as})


def _unsupported_period_stat(reading: Reading) -> Cause | None:
    """A stat a quarter's or half's line does not rebuild, on a period
    shape: the per-period figures are rebuilt from the shots and plays
    (:data:`~association.query.measures.PERIOD_COLUMNS`), and play-by-play
    carries no minutes, plus-minus or advanced rate per quarter; a field
    goal, 3-point or free throw percentage IS read (``PERIOD_RATE_STATS``).
    The period and whether it is a half are the sentence's."""
    scope, intent = reading, reading.intent
    asked = reading.asked
    measure = scope.measure
    if asked not in (PERIOD_LOG, PERIOD_RANKING) or measure is None or measure.as_typed is None or measure.as_typed in ("pts", "all"):
        return None
    key = measure.key if measure.whose == "own" else None
    if key in PERIOD_COLUMNS or key in PERIOD_RATE_STATS:
        return None
    period = scope.period
    quarter = period.number if period is not None and not period.half else None
    half = period.number if period is not None and period.half else None
    return Cause(kind="period_stat", facts={"intent": intent, "stat": measure.as_typed, "period": quarter, "half": half})


def _unsupported_period_as_condition(question: str, reading: Reading) -> Cause | None:
    """A quarter or half used as a CONDITION on which games count - "three
    points made per game after making one three in first quarter"
    (yardstick-v2 F062) - where no line could be read from the words: the
    parser reads a readable one as a line in that period
    (:func:`~association.query.line.read_period_line`), which the relation applies; a wording
    it cannot read is kept off the period shapes
    (``lexicon.PERIOD_AS_CONDITION``), and a period read of it would answer
    his first-quarter threes, fluently and wrongly."""
    if any(line.period is not None for line in reading.lines) or not (lexicon.PERIOD_AS_CONDITION.search(question) and lexicon.PERIOD_WORD.search(question)):
        return None
    return Cause(kind="period_as_condition", facts={"intent": reading.intent})


def _unsupported_team_period_stat(reading: Reading) -> Cause | None:
    """A team's stat by quarter or half that neither the linescore (its
    points) nor the period's rebuilt line
    (:data:`~association.query.measures.TEAM_PERIOD_COLUMNS`) holds:
    minutes, plus-minus, points in the paint."""
    measure, intent = reading.measure, reading.intent
    if reading.asked != TEAM_PERIOD_TOTAL or measure is None or measure.as_typed is None or measure.as_typed in ("points", "pts"):
        return None
    key = measure.key if measure.whose == "own" else None
    if key in TEAM_PERIOD_COLUMNS or key in PERIOD_RATE_STATS:
        return None
    return Cause(kind="team_period_stat", facts={"intent": intent, "stat": measure.as_typed})


def _unsupported_bench_points(question: str, reading: Reading) -> Cause | None:
    """Bench points: derivable (the non-starters' points in the box score,
    which flags starters) and read by nothing yet - a gap of ours, named as
    one, never "no data" (yardstick-v2 F106, "most opponent bench points
    allowed ...")."""
    # The measure as the words named it (the typed measure, with the
    # measure tagger's claim on the words: a key the model's reply alone
    # stands for names none - "stats off the bench" asks no bench points),
    # beside the one word this reads (slice (c): it read "bench points" as
    # words, and the measure's "points" twice over).
    named = reading.measure is not None and reading.measure.key == "points" and any(c.what == "measure" for c in reading.claims)
    return Cause(kind="bench_points", facts={"intent": reading.intent}) if named and _BENCH_WORD.search(question) else None


def _unsupported_team_boolean_count(reading: Reading) -> Cause | None:
    """A team's count of its players' triple-doubles or double-doubles, as a
    ranking with the team filed: "oklahoma city thunder all-time triple
    doubles vs west" reads as ``leaderboard`` with ``team`` and ``stat:
    triple_double``, and the ranking's decline would name the wrong cause
    ("no ranking reads triple_double") - one player's triple-doubles ARE
    counted; what is not read is the team's aggregate of them."""
    scope, subject = reading, reading.subject
    measure = scope.measure
    if (
        reading.asked != PLAYER_RANKING
        or measure is None
        or measure.key not in ("triple_double", "double_double")
        or subject is None
        or subject.kind not in ("team", "team_players")
        or scope.who.team is None
    ):
        return None
    return Cause(kind="team_boolean_count", facts={"intent": reading.intent, "stat": measure.as_typed})


def _reading_from_route_left_out(players: names.PlayerIndex, question: str, reading: Reading) -> LeftOut | None:
    """A fingerprint's "vs" the reading holds one side of - the names the
    words name beyond the players it holds (``Reading.left_out``), which the
    answer says beside the one polygon it draws
    (:func:`~association.query.subject.compared_but_unmatched`). Read for a
    fingerprint alone, the one shape that says it; until Phase 3, step 0
    the answering loop re-read the question for it."""
    if reading.asked != NETPOINTS_FINGERPRINT:
        return None
    scope = reading
    held = list(scope.who.players)
    return compared_but_unmatched(players, question, held)


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
