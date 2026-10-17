"""The parser: a question, read into one :class:`~association.query.reading.Reading`.

What the model contributes arrives as data -
the names it copied out of the question and the stat key it chose
(:func:`read_route` takes them as arguments, from
:func:`~association.query.normalizer.normalize`) - and everything else is
read from the words here, in grammar
tables: the kind and the parent shape the words ask (:data:`PARENT_GRAMMAR`), then the
grammar's rules (:func:`~association.query.grammar.settle`), the taggers,
one per family (:func:`_tagged`) and the point
(:func:`~association.query.point.read_point`), with who the question is
about read once (:func:`~association.query.subject.read_subject`) -
into the Reading :func:`read_route` returns. Nothing carries a route
between the words and the Reading: what the words ask is the grammars'
and the grammar's rules', and the filters are the taggers'.

Nothing here reaches a model, and nothing here trusts a name it was given:
a span that is no player's and no team's is dropped, never made a subject
(ISSUES.md #236).

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from association.query import lexicon, measure, names
from association.query.calendar import HOLIDAYS
from association.query.cuts import CutsContext, CutsRead, own_alignment, read_cuts
from association.query.decisions import Decision
from association.query.entities import _edit_budget, _words, find_players, find_teams, former_franchise, suggest_players, team_abbreviations
from association.query.grammar import Asked, absent, settle
from association.query.line import LinesNamed, LinesRead, lines_named, read_lines, read_period_line
from association.query.measure import BOOLEAN_KEYS, MeasureContext, named, read_measure
from association.query.measures import PERIOD_COLUMNS, PERIOD_RATE_STATS, TEAM_PERIOD_COLUMNS
from association.query.period import read_period, respelled
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
    PLAYERS_COUNTED,
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
    Minimum,
    PointRefused,
    PointShape,
    Reading,
    Span,
    Unsupported,
    Window,
    key_path,
)
from association.query.reading import Subject as ReadSubject
from association.query.span import SpanContext, SpanRead, needed, read_by, read_span
from association.query.span import claimed as claimed_once
from association.query.split import OWN_READ_ROLES, read_split, role_phrases
from association.query.subject import (
    Subject,
    _edit_distance,
    apply_subject,
    child_named,
    compared_but_unmatched,
    is_team_name,
    nicknames_in,
    opponent_read,
    player_named_on_a_team_only_question,
    question_derived_player,
    question_supports,
    read_subject,
    settle_subject,
    subject_claims,
    team_named_in,
    team_not_read,
    versus_word_unread,
)
from association.query.window import MinimumRead, WindowContext, contradicting_ends, own_seasons_window, per_season_window, read_minimum, read_window

ANY = frozenset({"player", "pair", "team", "teams", "team_players", "position", "everyone", "player+companions", "team+companions"})
"""Every subject kind, for a grammar row that applies whatever the kind."""

_AGAINST = lexicon.stands("opponent_against", "opponent_versus")
"""A team set against the subject, led by a versus word, as the subject
reading typed it (the words "against boston" are its, not a row's)."""

_PAIR_MEETING: lexicon.GrammarRow = lexicon.AllOf((lexicon.NoneOf((lexicon.COMPARE_VERB_ROW,)), lexicon.AnyOf((lexicon.MEETING_ROW, _AGAINST))))
"""A pair meeting - "vs", "against", a matchup, or an opponent typed with
a versus word - unless a compare verb owns the pair."""

_PLAYER_LOG: lexicon.GrammarRow = lexicon.AnyOf(
    (
        lexicon.PLAYER_LOG_ROW,
        # A player's games against a team: "games" with the opponent the
        # subject reading typed right after it, its versus word the opponent's.
        lexicon.StandsAfter(lexicon.GAMES_BEFORE_A_VALUE, frozenset({"opponent_against"})),
        # A window of games ("last 10 games", "his first game", "most recent
        # 5 games") and a range opened on a dated day ("since 1/26/20"), as
        # the window tagger and the cuts tagger typed them: the row reads that
        # one stands, and its words are the taggers' to claim (slice (c)).
        lexicon.stands("window"),
        # A month or a date - unless a per-game word stands beside it, where
        # the line over those games is asked ("how many points did fox
        # average in November 2023", "tatum averages since 12/11/2023" - never
        # the last 10 games listed, ISSUES.md #412).
        lexicon.AllOf((lexicon.AnyOf((lexicon.PLAYER_LOG_DATE_ROW, lexicon.stands("date"))), lexicon.NoneOf((lexicon.PER_GAME_ROW,)))),
    )
)
"""A player's games rather than his line: a log word, a window, one game, a
date with no per-game word beside it."""

_LEAGUE_TEAMS_RANKED = lexicon.AllOf((lexicon.LEAGUE_TEAMS_RANKED_ROW, lexicon.AnyOf((lexicon.TEAM_WORD_ROW, lexicon.NoneOf((lexicon.LINE_OF_GAMES_ROW,))))))
"""The league's teams ranked: a team word, or "nba" beside wins or losses,
with no games kept by a line unless a team word is written (ISSUES.md #394).
"nba" alone names the players' ranking: where the words name no measure it
reads, the ranking refuses by name (``ranking_needs_stat``, ISSUES.md #421),
where it was held the teams' refusal for fear of the points default."""

PARENT_GRAMMAR: tuple[tuple[frozenset[str], lexicon.GrammarRow, PointShape], ...] = (
    # (kinds the row applies to, the row's predicate over the grammar's
    # text, the parent shape) - first match wins.
    (ANY, lexicon.FINGERPRINT_ROW, NETPOINTS_FINGERPRINT),
    (ANY, lexicon.SHOT_CHART_ROW, SHOT_CHART),
    (frozenset({"team", "teams"}), lexicon.PERIOD_ROW, TEAM_PERIOD_TOTAL),
    (frozenset({"everyone"}), lexicon.PERIOD_ROW, PERIOD_RANKING),
    (ANY, lexicon.PERIOD_ROW, PERIOD_LOG),
    (ANY, lexicon.COACH_ROW, TEAM_COACH),
    (frozenset({"pair"}), _PAIR_MEETING, PLAYER_MEETINGS),
    (frozenset({"pair"}), lexicon.ANY_WORDS_ROW, PLAYER_COMPARISON),
    (frozenset({"teams"}), lexicon.ANY_WORDS_ROW, TEAM_MEETINGS),
    (frozenset({"team_players"}), lexicon.ANY_WORDS_ROW, PLAYER_RANKING),
    (frozenset({"team"}), lexicon.TEAM_PLAYERS_RANKED_ROW, PLAYER_RANKING),
    # A presence word, or a companion's phrase led by one as the subject
    # reading typed it.
    (frozenset({"team+companions"}), lexicon.AnyOf((lexicon.PRESENCE_ROW, lexicon.stands("companion_with"))), PRESENCE_SPLIT),
    # A log word, or a window of games as the window tagger typed it ("last
    # 10 games", "the season opener") - or the window words the row read
    # before the window was typed, for a form the tagger does not type,
    # never a count of seasons - or its games against an opponent over its
    # last N seasons (ISSUES.md #372).
    (frozenset({"team"}), lexicon.AnyOf((lexicon.TEAM_LOG_ROW, lexicon.stands("window"), lexicon.TEAM_GAMES_OVER_SEASONS_ROW)), PLAYER_LOG),
    (frozenset({"team"}), lexicon.STREAK_ROW, TEAM_RECORD),
    (frozenset({"team"}), lexicon.TEAM_RECORD_ROW, TEAM_RECORD),
    (frozenset({"team"}), lexicon.TEAM_OUTLOOK_ROW, TEAM_OUTLOOK),
    # A team's triple-doubles are its PLAYERS' (a boolean player line), which
    # the players' ranking reads under the team - not a team box-score total.
    (frozenset({"team"}), lexicon.TEAM_DOUBLES_ROW, PLAYER_RANKING),
    # A team's players ranked by games played (ISSUES.md #408).
    (frozenset({"team"}), lexicon.TEAM_GAMES_PLAYED_ROW, PLAYER_RANKING),
    (frozenset({"team"}), lexicon.ANY_WORDS_ROW, TEAM_LINE),
    (frozenset({"player"}), lexicon.NETPOINTS_ROW, NETPOINTS_RATINGS),
    # A player's own record is the W-L of HIS games, which player_splits
    # answers (F088, "Embiid's record against Boston this year"; ISSUES.md
    # #231) - never the team's with/without split, which needs a companion.
    # Not with a line stated with a plus in it, in words or as the lines
    # tagger typed it: "Sga record 36 plus points" is record_when, a child
    # player_stat's reading assigns. Before the log row, whose window and
    # date words ("since 1/26/20", "last 10 games") narrow a record as much
    # as a log - but not over a log word: a game log is its games.
    (
        frozenset({"player"}),
        lexicon.AllOf((lexicon.RECORD_ROW, lexicon.NoneOf((lexicon.PLUS_LINE_WORDS_ROW, lexicon.stands("line_plus"))), lexicon.NoneOf((lexicon.LOG_ROW,)))),
        PLAYER_SPLITS,
    ),
    (frozenset({"player"}), _PLAYER_LOG, PLAYER_LOG),
    # A player's split by a companion - unless a versus word sets him against
    # a team, where "maxey points vs boston without embiid" is his own games
    # narrowed (the absence a condition, on whichever side the
    # name resolves to), not a two-sided split.
    (
        frozenset({"player+companions"}),
        lexicon.AllOf((lexicon.AnyOf((lexicon.PRESENCE_ROW, lexicon.stands("companion_with"))), lexicon.NoneOf((lexicon.VERSUS_WORD_ROW, _AGAINST)))),
        PRESENCE_SPLIT,
    ),
    (frozenset({"player"}), lexicon.ANY_WORDS_ROW, PLAYER_LINE),
    (frozenset({"position"}), lexicon.POSITION_LOG_ROW, PLAYER_LOG),
    (frozenset({"position"}), lexicon.ANY_WORDS_ROW, PLAYER_RANKING),
    # Not over games kept by a line, with no team word: "most 40 point games
    # in nba history" is the players' count of 40-point games, which "nba"
    # beside "most" read as the teams' ranking (ISSUES.md #394).
    (frozenset({"everyone"}), _LEAGUE_TEAMS_RANKED, TEAM_RANKING),
    # A record with no player in it is a team's: "worst record 2025-26" read
    # as the league's scorers, the everyone row below.
    (frozenset({"everyone"}), lexicon.LEAGUE_RECORD_ROW, TEAM_RANKING),
    # A win percentage with no player in it is the teams' too: "worst win
    # percentage" ranked the league's scorers (ISSUES.md #428).
    (frozenset({"everyone"}), lexicon.LEAGUE_WIN_PCT_ROW, TEAM_RANKING),
    (frozenset({"everyone"}), lexicon.STREAK_ROW, TEAM_RECORD),
    # "game log", or a log after a window of games as the window tagger typed it ("last game log").
    (frozenset({"everyone"}), lexicon.AnyOf((lexicon.LEAGUE_LOG_ROW, lexicon.StandsBefore(frozenset({"window"}), lexicon.LOG_AFTER_A_VALUE))), PLAYER_LOG),
    # How many players a season line holds (ISSUES.md #390): the ranking, counted.
    (frozenset({"everyone"}), lexicon.PLAYERS_COUNTED_ROW, PLAYERS_COUNTED),
    (frozenset({"everyone"}), lexicon.ANY_WORDS_ROW, PLAYER_RANKING),
)
"""The parent grammar: the first row whose kinds hold the subject's kind
and whose predicate holds over the grammar's text names the parent - what
the words ask, as the point it means bare
(:class:`~association.query.reading.PointShape`: its relation, shape and
``by``). A row reads the words (the lexicon's, :data:`~association.query.lexicon.GrammarRow`)
and the values the taggers typed where they stand
(:class:`~association.query.lexicon.GrammarText`) - a window of games, an
opponent with its versus word, a companion's phrase, a line - asked by
value, never by a character written into the question. The children
(:data:`~association.query.subject.KIND_ASSIGNED_ASKS`) are assigned under
it by the subject reading.

.. versionadded:: 5.0.0

.. versionchanged:: 6.0.0
   Each row names a :class:`~association.query.reading.PointShape` (the
   grammar's key), where it named an intent (Phase 3, step 4).

.. versionchanged:: 6.0.0
   Each row is a predicate over the grammar's text, where it was a regex
   over the question with each typed value written into it as a mark
   (Phase 4, step 6).
"""

# The measure grammar - the stat a question names in its own words, read
# before the normalizer's key - is the lexicon's (lexicon.MEASURE_GRAMMAR)
# and the measure tagger's one reading (measure.named); the grammar's rules
# take it as their context (grammar.settle).


def parent_asked(question: str | lexicon.GrammarText, kind: str, companions: bool = False) -> PointShape | None:
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

    .. versionchanged:: 6.0.0
       Takes the grammar's text (:class:`~association.query.lexicon.GrammarText`)
       as well as a plain question (Phase 4, step 6).
    """
    text = lexicon.GrammarText.of(question)
    for kinds, predicate, parent in PARENT_GRAMMAR:
        if (kind in kinds or (companions and f"{kind}+companions" in kinds)) and predicate.holds(text):
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
    # A single near spelling is that player - the typo policy Jeff settled:
    # the index defaults, visibly, where exactly one player is within the
    # edit budget; two or more ask. "Embid"
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
    surname no player has). The cut is the parser's, made once, on the
    model's own names, before the reading respells
    them - which is what keeps a typo'd surname ("Bam Adeyebu", read as Bam
    Adebayo) from being cut back to the ambiguous "Bam"."""
    words = _words(name)
    asked = {word.casefold() for word in _words(question)}
    held = [word for word in words if word.casefold() in asked]
    if not held or len(held) == len(words) or name in nicknames_in(question) or is_team_name(name):
        # A team's name the model copied is no player's completion: cut,
        # "Philadelphia 76ers" (whose "76ers" splits to "ers") read as a
        # player named "ers".
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
    players = [n if classify_span(index, n) == "player" else _slots_from_names_shared(index, n) for n in names if n not in teams]
    players = [n for n in players if n]
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


def _slots_from_names_shared(index: names.NameIndex, name: str) -> str | None:
    """A possessive on a surname two or more players share, with the
    possessive taken off - ``"Curry"`` for ``"Curry's"`` - or None. Such a
    span is nobody's as typed ("Curry's" holds no word "s", and six Currys
    are no single near spelling), so it was dropped and "Curry's stats by
    month 2016" ranked the league's 2016 scorers (ISSUES.md #268); the
    question's own words cannot settle a name it shares either, so the span
    is handed on for the reader to ask which. A possessive on a name one
    player holds is left to the words, which read him."""
    stripped = name.strip()
    for suffix in ("'s", "'S", "\u2019s", "\u2019S"):
        if stripped.endswith(suffix) and len(find_players(index.players, stripped[: -len(suffix)])) >= 2:
            return stripped[: -len(suffix)]
    return None


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


def _two_teams(subject: Subject, question: str | lexicon.GrammarText, slots: dict[str, Any]) -> Subject:
    """Two teams meeting (ISSUES.md #235): a team subject set against a
    second team, no player named, and a meeting word between them - the
    ``teams`` kind the reading only gives under ``TEAM_MEETINGS`` today.
    Read over the question's words, or over the grammar's text, where a
    window of games and an opponent's versus word stand as the taggers
    typed them."""
    if subject.kind != "team" or subject.players or not subject.teams:
        return subject
    other = subject.opponent or (slots.get("opponent") if isinstance(slots.get("opponent"), str) else None)
    text = lexicon.GrammarText.of(question)
    one_teams_games = lexicon.TWO_TEAMS_LOG_WORDS.search(text.text) or (lexicon.LOG_OR_WINDOW_WORDS.holds(text) and not lexicon.TWO_TEAMS_RECORD_WORDS.search(text.text))
    if not other or other == subject.teams[0] or not lexicon.MEETING_WORDS.holds(text) or one_teams_games:
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
    "lebron" is LeBron James and "embid" Joel Embiid) - and that the question
    holds at all: a span the reading lists as invented (no word of the
    question supports it) rides on :attr:`Reading.misread
    <association.query.reading.Reading.misread>` alone, never among the
    players, so it changes neither the subject's kind nor the key the
    grammar names ("points per quarter for Luka" with an invented Stephen
    Curry is Luka's quarter breakdown, not the league's ranking; ISSUES.md
    #373)."""
    read = (*subject.players, *subject.companions)
    typed = [slots["player"]] if isinstance(slots.get("player"), str) else list(slots.get("players") or [])
    return [*subject.players, *(span for span in typed if span not in subject.invented and not any(question_supports(name, span) for name in read))]


# The columns asked for beside a ranking ("top 5 scorers with their rebounds
# and assists", F017) are the measure tagger's reading (``Measure.beside``,
# over lexicon.FIELDS_AFTER and TEAM_FIELD_WORDS).

# A quarter or half used as a CONDITION on which games count is the lines
# tagger's reading (``line.read_period_line``), made here before the
# grammar's rules so its words are blanked out of the question they read.


def _reached_spans(settled: Asked, companions: tuple[Companion, ...], phrases: tuple[tuple[int, int, str], ...]) -> tuple[tuple[int, int], ...]:
    """The spans of the phrases whose companion REACHED a line, where such a
    companion stands beside the subject on the reading: a "N stat" pair
    inside one is his line, never the subject's (ISSUES.md #359, "maxey
    record when embiid scores 20+ points"). A companion the rules restored
    as the subject himself ("how many games with 40+ points did Lebron James
    have") hands no span: the line is his own."""
    own = settled.subject.player
    if not any(c.line is not None and c.predicate == "reached" and not (own and question_supports(c.player, own)) for c in companions):
        return ()
    return tuple((start, end) for start, end, predicate in phrases if predicate == "reached")


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
# lexicon's patterns ("2010's" is a decade, "n't" a denial, a possessive
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

Jeff's rule: short or nonsensical questions are refused with a generic
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
    straight one - the fold at the parser's door, for :func:`read_route`,
    over the question and the names the model copied out of it."""
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
    # The choosers read the typed values the taggers read, never their
    # words: the parent grammar reads the grammar's text - the question with
    # each line's number, the opponent the subject reading read with its
    # versus word, each companion's phrase, each window of games and each
    # range opened on a dated day held apart from the words as the value
    # typed there (lexicon.GrammarText) - so a row reads "a window of games
    # stands here" by value and claims only its own words around it; the
    # values' words are their readers' to claim.
    named_lines = lines_named(question)
    prepared = grammar_text(
        question,
        lines=named_lines,
        opponent_at=tuple((c.start, c.end) for c in opponent_read(index.teams, question, opponent)),
        companions_at=tuple((start, end) for start, end, _ in read.companion_phrases),
        opponent=opponent,
        conditions=read.conditions,
    )
    parent = parent_asked(prepared, subject.kind, beside)
    # What the grammar read: the words whose deletion names another parent
    # (span.read_by) - a row's own words ("fingerprint", "record"), a word
    # that rules an earlier row out ("compare" keeps a pair off the matchup
    # row), the meeting word that makes two teams the kind it reads ("how
    # many times did the 76ers play boston"); not the "game" of "game log",
    # which "log" names alone.
    parent_claims = read_by(prepared, "asked", lambda probed: parent_asked(probed, _two_teams(read, probed, slots).kind, beside))
    # The rules are handed what the phrases typed, never their words: a
    # record "when <someone> scored N" is a reached companion led by "when".
    reached_when = any(predicate == "reached" and question[start:end].lower().startswith("when") for start, end, predicate in read.companion_phrases)
    settled_ask, decisions, words, child_claims = _read_route_staged(question, stat or None, parent, read, who, opponent, lines=named_lines, reached_when=reached_when)
    final = settled_ask.asked
    scope, tagger_claims, tagger_decisions = (
        _tagged(
            question,
            settled_ask,
            read.conditions,
            lines=period_lines,
            model_stat=stat or None,
            minimum=(stated_minimum, unblanked),
            phrases=read.companion_phrases,
            inactive=_inactive(index, settled_ask.subject.players),
        )
        if not settled_ask.coach
        else (Reading(), (), ())
    )
    decisions = (*decisions, *tagger_decisions)
    # The one reading, settled under the key the words end with: what the
    # parser's last step and everything after it answer from.
    settled = settle_subject(read, final, parent=parent, words=words)
    # The companions' phrases are the subject reading's claims, the line in
    # a quarter the parser's; the rules' and the taggers' read first.
    claims = claimed_once([*claimed_once([*settled_ask.claims, *tagger_claims]), *child_claims, *period_claims, *read.claims, *parent_claims])
    return _reading(index, asked, final, scope, settled, decisions=decisions, claims=claims, model_names=given, model_stat=stat)


def _inactive(index: names.NameIndex, players: tuple[str, ...]) -> bool:
    """Whether every player named is inactive - no player his name reaches
    has a box score in the latest season on record
    (:attr:`~association.query.names.NameIndex.active`): the span tagger
    reads his career where no season is named (decision D13: "kobe bryant
    playoff stats"). A name that reaches nobody, or an active set that could
    not be read, is no such reading: the season stays the default, and the
    answer says what it finds there."""
    if not players:
        return False
    active = index.active
    if active.season is None or active.error is not None:
        return False
    for player in players:
        found = find_players(index.players, player, limit=None)
        if not found or any(each.id in active.ids for each in found):
            return False
    return True


def _blanked(question: str, claims: tuple[Claim, ...]) -> str:
    """``question`` with each claim's characters written over by spaces of
    equal length, not cut, so every claim read after it is where the real
    question holds it."""
    for claim in claims:
        question = question[: claim.start] + " " * (claim.end - claim.start) + question[claim.end :]
    return question


def grammar_text(
    question: str,
    *,
    lines: LinesNamed | None = None,
    opponent_at: tuple[tuple[int, int], ...] = (),
    companions_at: tuple[tuple[int, int], ...] = (),
    opponent: str | None = None,
    conditions: tuple[Companion, ...] = (),
) -> lexicon.GrammarText:
    """``question`` as the parent grammar reads it (:func:`parent_asked`):
    each line's number the lines tagger read (``lines``,
    :meth:`~association.query.line.LinesNamed.numbered`), the opponent with
    its versus word (``opponent_at``, by the word that leads it), each
    companion's phrase (``companions_at``, by whether a presence word is in
    it; a line inside it stays the line), each window of games the window
    tagger reads (:meth:`~association.query.window.WindowCandidates.of_games`)
    and each range opened on a dated day the cuts tagger reads
    (:meth:`~association.query.cuts.CutsRead.dated`) - each typed over the
    one before it, a later one over an earlier. Called with the question
    alone it types the windows and the days, which need no name: a test of
    the grammar's rows over words alone goes through here.

    .. versionadded:: 6.0.0
    """
    text = lines.numbered(question) if lines is not None else lexicon.GrammarText.of(question)
    text = text.typed_over(tuple((start, end, lexicon.opponent_kind(text.text[start:end])) for start, end in opponent_at))
    text = text.typed_over(tuple((start, end, lexicon.companion_kind(text.text[start:end])) for start, end in companions_at), under=lexicon.LINE_KINDS)
    text = text.typed_over(tuple((claim.start, claim.end, "window") for claim in read_window(question, WindowContext()).of_games()))
    return text.typed_over(tuple((claim.start, claim.end, "date") for claim in read_cuts(question, CutsContext(opponent=opponent, without=absent(conditions))).dated(question)))


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
    2,082 wordings outside the corpus), where running them under the
    parent and again under each child would be 1.7 runs a question on
    average, up to four.

    Returns what the rules settled, the decisions made getting to it (the
    key moving off the parent, and why - :attr:`Reading.decisions
    <association.query.reading.Reading.decisions>`), the words that named
    the child, if one stands, and the words the child grammars read. The
    child grammars read the grammar's text with each line the lines tagger
    reads typed whole, the rules with each line's number typed and the
    typed ``lines`` beside it (:class:`~association.query.line.LinesNamed`,
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
    child_claims = read_by(marked, "asked", lambda probed: child_named(read, parent, probed))
    if named is not None:
        child, words = named
        staged = settle(numbered, child, subject=who, opponent=opponent, companions=companions, stat=stat, lines=lines, reached_when=reached_when)
        # A team's record under a companion's line names no words, and the
        # rules' own settling of it stands. The decisions record the moves
        # by their keys, as the trace prints them (``asked``,
        # ``relation/shape/by``).
        if staged.asked == child or words is None:
            decisions.append(Decision("parser", "asked", key_path(parent), key_path(child), _why_named(child, words)))
            if staged.asked != child:
                decisions.append(Decision("parser", "asked", key_path(child), key_path(staged.asked), "the grammar's rules settle it from the question's words"))
            return staged, tuple(decisions), words, child_claims
        decisions.append(Decision("parser", "asked", key_path(child), key_path(parent), f"the words {words!r} name {key_path(child)}, and the grammar's rules declined it"))
    staged = settle(numbered, parent, subject=who, opponent=opponent, companions=companions, stat=stat, lines=lines, reached_when=reached_when)
    if staged.asked != parent:
        decisions.append(Decision("parser", "asked", key_path(parent), key_path(staged.asked), "the grammar's rules settle it from the question's words"))
    return staged, tuple(decisions), None, child_claims


def _why_named(child: PointShape, words: str | None) -> str:
    """The reason a child stands, as :attr:`~association.query.subject.Subject.asked_reason` says it."""
    return f"the words {words!r} name {key_path(child)}" if words is not None else "a team's record in the games a player named beside it reached a line"


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
    phrases: tuple[tuple[int, int, str], ...] = (),
    inactive: bool = False,
) -> tuple[Reading, tuple[Claim, ...], tuple[Decision, ...]]:
    """The taggers, each the one reader of its family, over the key and the
    names the grammar's rules settled (``settled``), in the order each reads
    what the ones before it wrote: the split (the cuts tagger reads it), the
    lines (the measure tagger reads the stat a line names), the measure (the
    window tagger reads whether the stat is a yes/no one), the period, the
    cuts (the span tagger reads a date, a series game, an ordinal season and
    a dated range's year), the window (the span tagger reads it), the span
    last. ``companions`` are who the subject reading found beside the
    subject (an absent teammate is no opponent; a companion's line is his -
    ``phrases``, the subject reading's companion phrases with their spans,
    keep a pair inside a reached companion's phrase off the subject's
    lines, :func:`_reached_spans`);
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
    reached = _reached_spans(settled, companions, phrases)
    read = lines_under(asked, read_lines(question), beside_line=beside_line, reached=reached)
    measure_context = _tagged_measure_context(settled, question, read, side=side, shot_value=shot_value, fields=fields)
    measure = measure_under(asked, read_measure(question), measure_context)
    period = period_under(asked, read_period(question))
    cuts_context = CutsContext(opponent=settled.opponent, without=absent(companions))
    cuts = cuts_under(asked, read_cuts(question, cuts_context), split=split.split)
    window_context = WindowContext(boolean_stat=measure.measure is not None and measure.measure.key in BOOLEAN_KEYS)

    window = window_under(asked, read_window(_tagged_window_words(question, asked, cuts_context, split.split), window_context))
    implied = _SpanImplied.of(companions, cuts, kept.minimum, inactive=inactive, teams=settled.subject.teams)
    span = span_under(asked, read_span(question, _tagged_span_context(settled, question, window=window.window, cuts=cuts, implied=implied)))
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
        *needed(question, window.claims, lambda q: window_under(asked, read_window(_tagged_window_words(q, asked, cuts_context, split.split), window_context)).window, outside="window", gives_up=True),
        # The qualifier's over the question as typed, its words blanked in the rest.
        *needed(typed, kept.claims, lambda q: minimum_under(asked, read_minimum(q)).minimum, outside="minimum", gives_up=True),
        *needed(question, read.claims, lambda q: _tagged_lines(lines_under(asked, read_lines(q), beside_line=beside_line, reached=reached)), outside="line", gives_up=True),
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
        *needed(question, span.claims, lambda q: _tagged_span(asked, q, settled, window=window.window, cuts=cuts, implied=implied), outside="span", gives_up=True),
    )
    return scope, claims, _tagged_decisions(span, question, scope)


def _tagged_decisions(span: SpanRead, question: str, scope: Reading) -> tuple[Decision, ...]:
    """The taggers' decisions the trace records, and the answer says: the
    career the words imply where no season is named (decision D13), and a
    near spelling of "quarter" read as the word where a quarter stands on
    the Reading (ISSUES.md #457)."""
    found: list[Decision] = []
    if span.implied:
        found.append(Decision("parser", "span", None, "career", "no season named, and the words imply the career (decision D13)"))
    near = respelled(question)
    if near is not None and (scope.period is not None or any(line.period is not None for line in scope.lines)):
        found.append(Decision("parser", "period", near, "quarter", f"{near!r} is one edit from 'quarter', after an ordinal"))
    return tuple(found)


def _tagged_window_words(question: str, asked: PointShape | None, context: CutsContext, split: str | None) -> str:
    """``question`` as the window tagger reads it: a situation nothing reads
    is no other family's value, so its words are blanked ("vs top 3 teams"
    is no window of the last 3 games, ISSUES.md #400)."""
    return _blanked(question, cuts_under(asked, read_cuts(question, context), split=split).unread_situation())


def _tagged_worded_key(question: str, model_key: Any) -> Any:
    """The measure key the taggers read beside the words: the one the words
    name (:func:`~association.query.measure.named`), else the model's."""
    worded = named(question)
    return worded[0] if worded is not None else model_key


def _tagged_cuts(read: CutsRead) -> tuple[Cuts, int | None, int | None, bool]:
    """What the cuts tagger writes into the reading: the cuts, and the
    seasons a dated range opens and ends at, which the span tagger reads."""
    return read.cuts, read.dated_since, read.dated_until, read.dated_both


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


def _tagged_span(asked: PointShape | None, question: str, settled: Asked, *, window: Window, cuts: CutsRead, implied: _SpanImplied) -> tuple[Span, bool]:
    """What the span tagger writes into the reading, under the key: its
    span (:func:`~association.query.point.span_under`) and whether the
    words implied the career, which the parser's decision records - so a
    career word a career the words also imply stands beside is still the
    word the span was read by ("demar derozan points career vs
    clippers")."""
    read = span_under(asked, read_span(question, _tagged_span_context(settled, question, window=window, cuts=cuts, implied=implied)))
    return read.span, bool(read.implied)


@dataclass(frozen=True)
class _SpanImplied:
    """What the span tagger reads beside the words for the careers they
    imply (decision D13; :data:`~association.query.span.IMPLIED_CAREERS`),
    each a value another family typed or the subject reading settled: an
    opponent the cuts read, a companion who played, a holiday, every
    player named inactive in the latest season, a ranking's floor of
    games."""

    opponent: bool = False
    companion_played: bool = False
    companion_reached: bool = False
    companion_absent: bool = False
    annual_day: bool = False
    inactive: bool = False
    minimum_games: int | None = None
    former_era: bool = False

    @classmethod
    def of(cls, companions: tuple[Companion, ...], cuts: CutsRead, minimum: Minimum | None, *, inactive: bool, teams: tuple[str, ...]) -> _SpanImplied:
        """The facts from the typed companions, cuts, minimum and teams."""
        situation = cuts.cuts.situation
        calendar = situation.calendar if situation is not None else None
        eras = (former_franchise(name) for name in (*teams, cuts.cuts.tenure, cuts.cuts.opponent) if name)
        return cls(
            former_era=any(era is not None and era.last_season is not None for era in eras),
            opponent=cuts.cuts.opponent is not None or any(c.side == "opponent" for c in companions),
            companion_played=any(c.predicate not in ("absent", "reached") for c in companions),
            companion_reached=any(c.predicate == "reached" for c in companions),
            companion_absent=any(c.predicate == "absent" for c in companions),
            annual_day=calendar is not None and calendar in HOLIDAYS.values(),
            inactive=inactive,
            minimum_games=minimum.count if minimum is not None and minimum.unit == "games" else None,
        )


def _tagged_span_context(settled: Asked, question: str, *, window: Window, cuts: CutsRead, implied: _SpanImplied) -> SpanContext:
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
        window_of=window.of,
        date=cuts.cuts.date,
        game_n=cuts.cuts.game_n,
        season_n=cuts.cuts.season_n,
        dated_since=cuts.dated_since,
        dated_until=cuts.dated_until,
        dated_both=cuts.dated_both,
        opponent=implied.opponent,
        companion_played=implied.companion_played,
        companion_reached=implied.companion_reached,
        companion_absent=implied.companion_absent,
        annual_day=implied.annual_day,
        inactive=implied.inactive,
        pair=len(settled.subject.players) >= 2,
        minimum_games=implied.minimum_games,
        former_era=implied.former_era,
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
        subject=subject,
        decisions=(*_subject_decisions(subject), *decisions, *applied.decisions),
        misread=tuple(applied.dropped),
        claims=claimed_once(found),
    )
    pointed = with_point(question, reading)
    # What the words name that nothing reads, read once, here: a refusal
    # said before any reader runs, and the shapes recognized and read by
    # nothing, said where the answer side declines (the question as it was
    # asked, unfolded).
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
    # An unread word that carries a meaning the reading
    # defaulted past is a refusal by name, and the refusal claims its words
    # (so the ledger reads them, and the deletion probe agrees: without the
    # word there is no refusal).
    read_by_model = (*model_names, *_reading_from_route_names(applied.scope, subject, applied.asked))
    defaulted, defaulted_claims = _reading_from_route_defaulted(asked, pointed, [(c.start, c.end) for c in found], read_by_model, model_stat)
    if defaulted is not None and defaulted.kind == "ranking_needs_stat" and unsupported:
        # What the words name that nothing reads (an age) is the refusal the
        # answering loop says where the ranking declines; it is the cause.
        defaulted = None
    found = list(claimed_once([*found, *defaulted_claims]))
    return replace(
        pointed,
        refused=refused,
        unsupported=unsupported,
        left_out=left_out,
        defaulted=defaulted,
        claims=tuple(found),
        # The words nothing claimed, by the claims ledger's rule, over the
        # question as it was asked (the fold keeps every position).
        unread=lexicon.unread_words(asked, [(c.start, c.end) for c in found], read_by_model, model_stat),
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
    tenure, read off the typed values."""
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
    The parser's last step's (``_reading``); public for a caller that builds
    a Reading by hand (a test handing the compiler a subject of its own)
    rather than from the question's words.

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
# A championship, a player named on a team's question, and the shapes
# nothing reads are readings of the words, made here, once, and carried on
# the Reading as reading.Cause values the planner says through the one
# phrase table (compose.plan.refusal_result, say.refusal_phrase) - nothing
# after the parser reads the question for them.


def _reading_from_route_refused(players: names.PlayerIndex, teams: names.TeamIndex, question: str, reading: Reading) -> Cause | None:
    """The refusal the words come to that no point answers past, said
    before any reader runs - the Reading's
    :attr:`~association.query.reading.Reading.refused`:

    - a championship (``championship``): "Show which team won the nba
      championship for the past 10 years" read as a team ranking would rank
      regular-season records since 2017.
      The warehouse holds every playoff game and no table of titles; a
      champion is derivable (the winner of a postseason's last game) and
      nothing derives it yet. "Title odds" is ``TEAM_OUTLOOK``'s projection
      and is left alone. Was ``refusals.by_question``.
    - a player named on a question whose shape has no reading for one
      (``no_player_reading``, :data:`~association.query.reading.TEAM_ONLY_ASKS`):
      "alperen şengün alltime record" read as ``TEAM_RANKING`` and
      answered the league standings, Sengun never read (yardstick-v2 F111) -
      the one player the words name and no real team
      (:func:`~association.query.subject.player_named_on_a_team_only_question`).
      Was the answering loop's own check.
    - both ends of the span at once (``window_ends_contradict``): "lebron
      james last 5 first games stats" answered his first 10 games, "last"
      and "5" unread (ISSUES.md #378) - no end is right over the other, so
      the refusal names both
      (:func:`~association.query.window.contradicting_ends`).
    - a fact about a player or a game no table holds, or nothing reads
      (``fact_not_read``, :data:`~association.query.lexicon.ABSENT_FACTS`):
      "3rd nba overall draft picks", "players that wear number 5 jersey
      active" and "6 ft 6 inches nba players" each answered the 2026 points
      leaders, and "NBA players born on August 1" was refused as a game on
      August 1 (ISSUES.md #389) - the words fell to the parent grammar's
      default reading, the fact unread; and an award or an All-Star
      selection ("nba mvps in 2010's", "1999 sixth man of the year",
      ISSUES.md #146), answered as a points ranking.
      Points scored one way that no reader reads (``fact_not_read``,
      :data:`~association.query.lexicon.PLAY_TYPE_POINTS`): "toronto
      raptors points off turnovers" answered the Raptors' turnovers (ISSUES.md
      #456).
    - a coach named by "under" and his name, with no "coach" said
      (``no_coach_table``, with the ``words``;
      :data:`~association.query.lexicon.COACH_UNDER`): "Name the top 10
      players by games played under Lon Kruger" ranked the league's 2026
      season (ISSUES.md #431).
    - a window of games taken in each season (``window_per_season``):
      "warriors first 10 game record every season" answered the first 10
      games of 2026, "every season" unread (ISSUES.md #379) - nothing reads
      a window per season yet
      (:func:`~association.query.window.per_season_window`).
    - a count of a career's own seasons by their place
      (``own_seasons_window``): "lebron james stats through their first 9
      seasons" answered his 2026 line (ISSUES.md #392) - which seasons are
      his first nine is a fact of his career the reader does not hold
      (:func:`~association.query.window.own_seasons_window`).

    - a conference or division a ranking's subject belongs to
      (``own_alignment``): "top scorers in the western conference this
      season" ranked every player's games against the West (ISSUES.md #339)
      - nothing ranks a conference's own yet
      (:func:`~association.query.cuts.own_alignment`); said only where the
      words name nothing else that nothing reads (bench points), whose
      cause is said where the answer side declines, as before.

    The championship first, as the answering loop asked them, then a fact
    nothing reads, then a coach named by "under", then the window's ends, then a window per season, then a
    career's own seasons, then a ranking's own conference."""
    if lexicon.CHAMPIONSHIP.search(question) and not lexicon.TITLE_ODDS.search(question):
        return Cause(kind="championship", facts={"shape": reading.asked})
    fact = _refused_fact_not_read(question) or _refused_play_type_points(question, reading)
    if fact is not None:
        return fact
    coach = _refused_coach_under(teams, question)
    if coach is not None:
        return coach
    ends = contradicting_ends(question)
    if ends:
        return Cause(kind="window_ends_contradict", facts={"ends": [question[claim.start : claim.end] for claim in ends]})
    per_season = per_season_window(question)
    if per_season:
        return Cause(kind="window_per_season", facts={"window": question[per_season[0].start : per_season[0].end], "per": question[per_season[1].start : per_season[1].end]})
    own = own_seasons_window(question)
    if own:
        return Cause(kind="own_seasons_window", facts={"seasons": [question[claim.start : claim.end] for claim in own]})
    aligned = own_alignment(reading.asked, reading.cuts)
    if aligned is not None and not _reading_from_route_unsupported(question, reading):
        # Where the words name something else nothing reads ("bench points
        # allowed in the west"), that cause is said, as it was.
        return Cause(kind="own_alignment", facts={"shape": reading.asked, "alignment": aligned})
    denied = _reading_from_route_denied(question, reading)
    if denied is not None:
        return denied
    unread_versus = versus_word_unread(players, teams, question, reading)
    if unread_versus is not None:
        return Cause(kind="opponent_not_read", facts={"words": unread_versus, "shape": reading.asked})
    unplaced = team_not_read(teams, question, reading)
    if unplaced is not None:
        # ISSUES.md #436: a team named beside a player that
        # no read placed - his tenure, the opponent - is refused by name,
        # never answered over every game.
        return Cause(kind="team_not_read", facts={"words": unplaced[0], "team": unplaced[1], "shape": reading.asked})
    if reading.asked not in TEAM_ONLY_ASKS:
        return None
    player = player_named_on_a_team_only_question(players, teams, question, reading.who, reading.span.season)
    return Cause(kind="no_player_reading", facts={"player": player, "shape": reading.asked}) if player is not None else None


def _refused_fact_not_read(question: str) -> Cause | None:
    """The first fact :data:`~association.query.lexicon.ABSENT_FACTS` names
    in ``question``, as the ``fact_not_read`` cause with the words that
    name it - or None."""
    for fact, pattern in lexicon.ABSENT_FACTS:
        match = pattern.search(question)
        if match is not None:
            return Cause(kind="fact_not_read", facts={"fact": fact, "words": match.group(0)})
    return None


def _refused_play_type_points(question: str, reading: Reading) -> Cause | None:
    """Points scored one way (:data:`~association.query.lexicon.PLAY_TYPE_POINTS`:
    off turnovers, on second chances, on the fast break, in the paint) where
    the measure read is not the team metric that reads them, as the
    ``fact_not_read`` cause with the words (ISSUES.md #456): "toronto
    raptors points off turnovers" answered the Raptors' turnovers and
    "lebron points in the paint" his points. A team's points in the paint
    and fast-break points are read, and pass."""
    read = reading.measure.key if reading.measure is not None else None
    for metric, pattern in lexicon.PLAY_TYPE_POINTS:
        match = pattern.search(question)
        if match is not None and (metric is None or metric != read):
            return Cause(kind="fact_not_read", facts={"fact": "points by play", "words": match.group(0)})
    return None


def _refused_coach_under(teams: names.TeamIndex, question: str) -> Cause | None:
    """A coach named by "under" and two or three words of a name, with no
    "coach" said (:data:`~association.query.lexicon.COACH_UNDER`, ISSUES.md
    #431): "Name the top 10 players by games played under Lon Kruger"
    ranked the league's 2026 season. Refused by the coach cause, naming the
    words - no table here holds a coach, so no tenure narrows anything. The
    name ends at the first word no name holds - a stopword, a number or a
    stat word, a team's word - and two words must be left."""
    for match in lexicon.COACH_UNDER.finditer(question):
        start = match.start("name")
        end = start
        kept = 0
        for piece in lexicon.NAME_PIECES.finditer(match.group("name")):
            word = piece.group(0).lower()
            if _refused_coach_under_stops(teams, word):
                break
            kept += 1
            end = start + piece.end()
        if kept >= 2:
            return Cause(kind="no_coach_table", facts={"unanswerable": "coach", "words": question[match.start() : end]})
    return None


def _refused_coach_under_stops(teams: names.TeamIndex, word: str) -> bool:
    """Whether ``word`` ends a coach's name after "under" (:func:`_refused_coach_under`)."""
    if word in lexicon.NAME_STOPWORDS or word in lexicon.UNDER_NOT_A_COACH or word in lexicon.COUNT_NUMBERS:
        return True
    return named(word) is not None or bool(find_teams(teams, word))


def _reading_from_route_denied(question: str, reading: Reading) -> Cause | None:
    """A teammate's start or bench role the words deny - "maxey game log
    when embiid doesn't start" - which the role reader reads as the role
    itself, so a filter on it narrows to the very games the question
    excludes (ISSUES.md #248: his games WITH Embiid starting were listed):
    refused by a cause naming the words, on every key but the with/without
    split, which shows both halves and keeps it (:func:`_read_route_beside`)."""
    if reading.asked == PRESENCE_SPLIT:
        return None
    roles = tuple(c for c in reading.companions if c.predicate in ("started", "bench"))
    for match, predicate in role_phrases(roles, question):
        if lexicon.DENIED_ROLE.search(match.group(2)):
            player = next(c.player for c in roles if c.predicate == predicate)
            return Cause(
                kind="denied_role", facts={"player": player, "words": question[match.start() : match.end()].strip(), "role": "start" if predicate == "started" else "bench", "shape": reading.asked}
            )
    return None


_DEFAULTED_ORDER: tuple[str, ...] = ("number", "stat", "window", "date")
"""The classes of a word the reading defaulted past, in the order one is
said where a question holds more than one: a number first (the sharpest
loss - "last 5 games" read as the season), then a stat, a window word, a
date."""


def _reading_from_route_defaulted(question: str, reading: Reading, claimed: list[tuple[int, int]], names: tuple[str, ...], stat: str) -> tuple[Cause | None, list[Claim]]:
    """A word of ``question`` that carries a meaning the reading defaulted
    past (:attr:`~association.query.reading.Reading.defaulted`):
    among the content words nothing ``claimed`` (the ledger's own rule,
    :func:`~association.query.lexicon.content_words`), a number that is no
    rate's unit, no season the span read and no read line's or period's
    value (``unread_number``); a stat word the measure tagger reads to a key
    other than the one the reading took (``unread_stat``); a window word
    where the window read no count and no end (``unread_window``); a month,
    or a slashed day after a word that leads a date, where the cuts hold no
    calendar narrowing and no date (``unread_date``). One cause, the first
    class by :data:`_DEFAULTED_ORDER`, its facts the words of every class
    found, and a claim over each such word, since the refusal reads them.
    A word that restates the shape ("record", "stats", "most") is in no
    class and stays unread. Measured on the populations
    (``~/association-research/stages/unread_meaning.py``): no question
    answered through a reader carries one."""
    unread = [(start, end, word) for start, end, word in lexicon.content_words(question, names, stat) if not any(first < end and last > start for first, last in claimed)]
    context = _DefaultedContext.of(reading)
    classes: dict[str, list[str]] = {}
    claims: list[Claim] = []
    names_read: dict[str, str] = {}
    for start, end, word in unread:
        lower = word.lower()
        before = question[:start].split()[-1].lower() if question[:start].split() else ""
        rest = question[end:]
        after = rest.split()[0].lower().strip("?,.!") if rest.split() else ""
        which = _defaulted_class(lower, before, after, context, names_read)
        if which is None:
            continue
        classes.setdefault(which, []).append(word)
        claims.append(Claim(start, end, "defaulted"))
        if which == "window" and after:
            # The word that made it a stretch ("games" of "recent games") is
            # read too: without it there is no refusal, and the ledger's
            # deletion probe reads what the refusal depends on.
            offset = end + len(rest) - len(rest.lstrip())
            claims.append(Claim(offset, offset + len(rest.split()[0].rstrip("?,.!")), "defaulted"))
    cause = _defaulted_cause(reading, classes, names_read, context.key)
    if cause is None:
        return _defaulted_ranking(reading), []
    return cause, claims


def _defaulted_ranking(reading: Reading) -> Cause | None:
    """The players' ranking by points per game where neither the words nor
    the model named any measure (ISSUES.md #421): the ranking's default
    answered "best shooting percentages", "most wins" and "efficiency
    rating by year" as the league's scorers, fluently. A cause the planner
    says where the ranking would answer, as every defaulted class is - so a
    decline or a refusal the planning comes to first keeps its sentence."""
    point = reading.point
    if point is None or point.read_as not in (PLAYER_RANKING, PointShape("player_games", "ranking", "player")) or point.predicates or reading.lines:
        # (A line names the ranking's stat: "players who averaged 25 ppg".)
        return None
    if reading.measure is not None and reading.measure.key is not None:
        return None
    return Cause(kind="ranking_needs_stat", facts={"shape": PLAYER_RANKING})


@dataclass(frozen=True)
class _DefaultedContext:
    """What the reading read, against which an unread word is classed
    (:func:`_reading_from_route_defaulted`): the measure's key and its own
    words, the seasons the span read, the values the lines and the period
    read, the window and the cuts."""

    key: str | None
    read_words: frozenset[str]
    years: frozenset[str]
    values: frozenset[str]
    window: Window
    cuts: Cuts
    #: What the point is read as: the stat class applies on a shape that
    #: reads ONE stat (a ranking, a count, a single game's high), never on a
    #: log or a line, which show the whole line ("luka doncic points rebounds
    #: assists last 5 games" is a correct log).
    shape: PointShape | None

    @classmethod
    def of(cls, reading: Reading) -> _DefaultedContext:
        """The context ``reading`` gives."""
        key = reading.measure.key if reading.measure is not None else None
        # The read measure's own words - its catalog label and the words it
        # was typed as - so a leftover of it ("point" of "point differential",
        # read whole by the model) names no other stat.
        typed = (reading.measure.as_typed or "") if reading.measure is not None else ""
        label = measure.CATALOG[key].label if key in measure.CATALOG else ""
        # The words split at every non-letter ("3-pointers made" is "pointers"
        # and "made"), so "point" of "three points made" is a leftover of
        # the 3-pointers the tagger read, by its stem (:func:`_leftover`).
        read_words = lexicon.letter_words(f"{typed} {label}")
        span = reading.span
        years = frozenset(str(year) for year in (span.season, span.since, span.until) if year is not None)
        values = {str(line.value).removesuffix(".0") for line in reading.lines}
        if reading.period is not None and reading.period.number is not None:
            # "4th quarter stats 4th quarter": the second names the period read.
            values |= {str(reading.period.number), *lexicon.ordinal_forms(reading.period.number)}
        shape = reading.point.read_as if reading.point is not None else reading.asked
        return cls(key, read_words, years, frozenset(values), reading.window, reading.cuts, shape)


def _defaulted_class(lower: str, before: str, after: str, context: _DefaultedContext, names_read: dict[str, str]) -> str | None:
    """Which class one unread word falls in (:func:`_reading_from_route_defaulted`),
    or None where it carries no meaning the reading defaulted past. Narrow,
    so a class refuses no correct answer: a stat word counts only on a shape that reads
    one stat (:func:`_one_stat_shape`); a window word only beside a count or
    a game word ("top 5 games", "last game" - not "top scorers", "past or
    present"); "may" only in a date ("in may", "may 10" - not "who may
    lead"); and a token holding a digit that is no number as written
    ("3ot", "20222-23") is an unread number too."""
    named = measure.named(lower)
    if named is not None and named[0] != context.key and not _leftover(lower, context.read_words):
        if not _one_stat_shape(context.shape):
            return None
        names_read[lower] = measure.CATALOG[named[0]].label if named[0] in measure.CATALOG else named[0]
        return "stat"
    if lexicon.MEANING_NUMBER.fullmatch(lower):
        return "number" if _defaulted_number(lower, before, context) else None
    if named is None and lexicon.MEANING_GLUED_DIGITS.fullmatch(lower) and not lexicon.MEANING_SEASON_FORM.fullmatch(lower) and lower.strip("'") not in context.years:
        return "number"
    if lower in lexicon.MEANING_WINDOW_WORDS and context.window.count is None and context.window.order is None and _beside_a_stretch(after):
        return "window"
    if _defaulted_date(lower, before, after, context):
        return "date"
    return None


def _one_stat_shape(shape: PointShape | None) -> bool:
    """Whether the point reads one stat, so a second stat word asks for
    another answer: a ranking, a count, a single game's high or a total; a
    log, a line, a split, a history or a comparison show the whole line."""
    if shape is None:
        return False
    return shape.shape == "ranking" or (shape.shape == "rows" and shape.by == "measure") or (shape.shape == "scalar" and shape.by in ("count", "total"))


def _beside_a_stretch(after: str) -> bool:
    """Whether the word after a window word makes it a stretch of games:
    a count ("top 5", "last 10"), an ordinal ("last 1st quarter"), or a
    word of :data:`lexicon.MEANING_WINDOW_NEXT` - a game word ("last game",
    "past seasons"), a time word ("last time", "last week"), a yes/no
    measure's word ("last triple double") or a period word ("last first
    quarter")."""
    return bool(lexicon.MEANING_NUMBER.fullmatch(after)) or after in lexicon.MEANING_WINDOW_NEXT or after.rstrip("s") in lexicon.MEANING_WINDOW_NEXT


def _leftover(lower: str, read_words: frozenset[str]) -> bool:
    """Whether an unread stat word is a leftover of the measure the reading
    took: its stem is one of the measure's own words or a prefix of one
    ("point" of "point differential"; "point" of "three points made", whose
    label is "3-pointers made"), four letters or more so "ast" and "assists"
    do not pair by accident."""
    stem = lower.rstrip("s")
    return any(stem == word.rstrip("s") or (len(stem) >= 4 and word.startswith(stem)) or (len(word) >= 4 and stem.startswith(word.rstrip("s"))) for word in read_words)


def _defaulted_number(lower: str, before: str, context: _DefaultedContext) -> bool:
    """Whether a number the reading did not read counts: not a rate's unit,
    not a season the span read, not a read line's or period's value, not
    a year, and not the day of a date after a month ("september 11th")."""
    number = lower.rstrip("%+").replace(",", "")
    if number in lexicon.MEANING_UNIT_NUMBERS or number in context.years or lower in context.values or number in context.values:
        return False
    if len(number) == 4 and number.isdigit() and 1946 <= int(number) <= 2030:
        return False
    return before not in lexicon.MEANING_MONTH_WORDS


def _defaulted_date(lower: str, before: str, after: str, context: _DefaultedContext) -> bool:
    """Whether an unread word is a month or a day the cuts did not narrow
    by: a month's name, or a slashed pair after a word that leads a date
    ("on 1/26", "since 7/14"; "went 7/14 from three" is a figure). "may" is
    the month only in a date's place - after a date lead or before a day
    ("in may", "may 10"); "who may lead the league" is the verb."""
    if context.cuts.situation is not None or context.cuts.date is not None:
        return False
    if lower == "may":
        return before in lexicon.MEANING_DATE_LEADS or bool(lexicon.MEANING_NUMBER.fullmatch(after))
    return lower in lexicon.MEANING_MONTH_WORDS or bool(lexicon.MEANING_SLASHED_DAY.fullmatch(lower) and before in lexicon.MEANING_DATE_LEADS)


def _defaulted_cause(reading: Reading, classes: dict[str, list[str]], names_read: dict[str, str], key: str | None) -> Cause | None:
    """The one cause the classes found give, the first by :data:`_DEFAULTED_ORDER`,
    its facts the words by class; a stat's names what the word names and
    what was read instead."""
    for which in _DEFAULTED_ORDER:
        if which not in classes:
            continue
        facts: dict[str, Any] = {"shape": reading.asked, "words": tuple(classes[which]), "classes": {name: tuple(words) for name, words in classes.items()}}
        if which == "stat":
            facts["names"] = names_read.get(classes["stat"][0].lower(), classes["stat"][0])
            facts["read"] = measure.CATALOG[key].label if key in measure.CATALOG else None
        return Cause(kind=f"unread_{which}", facts=facts)
    return None


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
    Measured (``~/association-research/stages/refusal_sites.py``): 6 of the
    628 recorded and 157 of the 2,082 feed questions are recognized by one
    of these, and no question by two."""
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
    return Cause(kind="playoff_round", facts={"shape": reading.asked, "round": playoff_round})


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
    return Cause(kind="non_calendar_situation", facts={"shape": reading.asked, "situation": situation.text, "reads_as": reads_as})


def _unsupported_period_stat(reading: Reading) -> Cause | None:
    """A stat a quarter's or half's line does not rebuild, on a period
    shape: the per-period figures are rebuilt from the shots and plays
    (:data:`~association.query.measures.PERIOD_COLUMNS`), and play-by-play
    carries no minutes, plus-minus or advanced rate per quarter; a field
    goal, 3-point or free throw percentage IS read (``PERIOD_RATE_STATS``).
    The period and whether it is a half are the sentence's."""
    scope = reading
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
    return Cause(kind="period_stat", facts={"shape": reading.asked, "stat": measure.as_typed, "period": quarter, "half": half})


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
    return Cause(kind="period_as_condition", facts={"shape": reading.asked})


def _unsupported_team_period_stat(reading: Reading) -> Cause | None:
    """A team's stat by quarter or half that neither the linescore (its
    points) nor the period's rebuilt line
    (:data:`~association.query.measures.TEAM_PERIOD_COLUMNS`) holds:
    minutes, plus-minus, points in the paint."""
    measure = reading.measure
    if reading.asked != TEAM_PERIOD_TOTAL or measure is None or measure.as_typed is None or measure.as_typed in ("points", "pts"):
        return None
    key = measure.key if measure.whose == "own" else None
    if key in TEAM_PERIOD_COLUMNS or key in PERIOD_RATE_STATS:
        return None
    return Cause(kind="team_period_stat", facts={"shape": reading.asked, "stat": measure.as_typed})


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
    return Cause(kind="bench_points", facts={"shape": reading.asked}) if named and lexicon.BENCH_WORD.search(question) else None


def _unsupported_team_boolean_count(reading: Reading) -> Cause | None:
    """A team's count of its players' triple-doubles or double-doubles, as a
    ranking with the team filed: "oklahoma city thunder all-time triple
    doubles vs west" reads as ``PLAYER_RANKING`` with ``team`` and ``stat:
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
    return Cause(kind="team_boolean_count", facts={"shape": reading.asked, "stat": measure.as_typed})


def _reading_from_route_left_out(players: names.PlayerIndex, question: str, reading: Reading) -> LeftOut | None:
    """A fingerprint's "vs" the reading holds one side of - the names the
    words name beyond the players it holds (``Reading.left_out``), which the
    answer says beside the one polygon it draws
    (:func:`~association.query.subject.compared_but_unmatched`). Read for a
    fingerprint alone, the one shape that says it."""
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
