"""The grammar's rules after its rows: what the question's own words move
the key they ask to, once the parent grammar (:data:`~association.query.parse.PARENT_GRAMMAR`)
and a child grammar (:func:`~association.query.subject.child_named`) have
named it - a coach, a quarter or a half, a team where a player ranking was
named, a record, a fingerprint named or not, a line with no number, a
career high - and the names the rules read under the key they settle.

Phase 3, step 4, slice (b): these were the stages of ``router.py``, run by
``router.settle`` over a raw slot dict before the taggers and carried to
the parser's last step on a ``router.Route``. The router is gone. The rules
read the typed subject the subject reading hands them, the companions it
read and the model's stat as context, and return what they settle
(:class:`Asked`); the taggers read the words after them
(``parse._tagged``), and the parser writes the Reading. Each rule reads the
words alone; a name it settles - the subject a count's, a high's or a
record's grammar names (:func:`~association.query.subject.subject_named_in`),
a team's quarter from its one nickname, a quarter's player read back - is
one the subject reading read from the words, written on the typed subject
under the key the rules settle (``ROADMAP-TYPES.md``, Still open 15).

Measured before the move (``~/association-research/stages/route_family.py``,
the 2,710 readings): the rules move the key on 115 readings (the period
rules 22, a team or a fingerprint where a player was named 26, a line or a
record 34, a count with no line 28, one player's line 6; 63 children moved
back to the parent), write a name on 84 (the grammar's subject 30, a team's
nickname 24, ...) and run twice on 63 readings, a child they declined.

.. versionadded:: 6.0.0
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from association.query import lexicon, reading
from association.query.lexicon import BY_QUARTER, GAMES_WORDS, HALF_WORDS, ORDER_WORDS, PAST_N_SEASONS, PERIOD_AS_CONDITION, PERIOD_LEADERS, PERIOD_TOP, QUARTER_WORDS, WHO_RANKS
from association.query.line import LinesNamed, lines_named
from association.query.measure import GAMES_STATS, named, names_a_stat
from association.query.period import which_period
from association.query.reading import (
    GAME_HIGHS,
    GAMES_COUNTED,
    LINE_RECORD,
    NETPOINTS_FINGERPRINT,
    NETPOINTS_RATINGS,
    PERIOD_LOG,
    PERIOD_RANKING,
    PLAYER_COMPARISON,
    PLAYER_LINE,
    PLAYER_LOG,
    PLAYER_MEETINGS,
    PLAYER_RANKING,
    PRESENCE_SPLIT,
    SEASON_HISTORY,
    SHOT_CHART,
    TEAM_COACH,
    TEAM_LINE,
    TEAM_MEETINGS,
    TEAM_PERIOD_TOTAL,
    TEAM_RANKING,
    TEAM_RECORD,
    Claim,
    Companion,
    Period,
    PointShape,
)
from association.query.span import range_named, read_by
from association.query.subject import is_team_name, subject_named_in, team_named_in_text, team_words_in

CODE_ASSIGNED_ASKS: frozenset[PointShape] = frozenset({TEAM_COACH, PERIOD_LOG, PERIOD_RANKING})
"""What the words ask that no model could emit (a coach, a player's quarter,
players ranked by one), because the grammar's rules assign it from the
question's own text.

Kept out of the normalizer's schema and prompt on purpose. Both are
load-bearing on every other question: a new enum value changes the decoding
grammar and a new prompt line changes slots on unrelated questions - adding
one reproducibly flipped "What was the Lakers record last season?" from
``team`` "Lakers" to "Los Angeles Lakers". A period is legible from the
question ("1q", "4th qtr", "first half") with no help from the model, so it
costs nothing to read it here and nothing to route on it.

.. versionadded:: 2.2.0

.. versionchanged:: 6.0.0
   ``router.CODE_ASSIGNED_INTENTS``, the three intents' names, until Phase
   3, step 4; the grammar's since slice (b), when the router went.
"""


@dataclass(frozen=True)
class Asked:
    """What the grammar's rules settle for a question (:func:`settle`): the
    key the words ask (``None`` for no shape), whether the question is a
    coach's (refused whatever else it asks, so nothing else is read), the
    typed subject with the names the rules read under that key, the team the
    subject is set against, the measure key the words name over the model's
    (the measure tagger's context), and the words the rules' decisions
    turned on (:func:`~association.query.span.read_by`, named ``intent``).

    .. versionadded:: 6.0.0
    """

    asked: PointShape | None
    coach: bool
    subject: reading.Subject
    opponent: str | None
    stat: Any
    claims: tuple[Claim, ...]


@dataclass
class _Working:
    """The rules' working reading of one question, rewritten in place as
    each rule settles it: what the words ask, who it is about, the team set
    against, the measure key - and the two readings of the words a name is
    settled from under the key (the grammar's subject, the team nicknames),
    read once from the question the rules read."""

    asked: PointShape | None
    subject: reading.Subject
    opponent: str | None
    stat: Any
    grammar: str | None
    team_words: tuple[str, ...]
    #: The lines the words state, as the lines tagger typed them
    #: (:class:`~association.query.line.LinesNamed`): the rules read the
    #: typed values, never the line's words, which the grammar's text they
    #: read holds apart (:meth:`~association.query.line.LinesNamed.numbered`).
    lines: LinesNamed
    #: Whether a companion reached a line in a clause led by "when" ("sixers
    #: record when maxey scored 20+ points"), as the subject reading typed
    #: it - the words ``lexicon.WHEN_REACHES`` read until slice (c), kept
    #: for a clause with no companion in it ("record when he scored 30").
    reached_when: bool = False


def settle(
    question: str | lexicon.GrammarText,
    asked: PointShape | None,
    *,
    subject: reading.Subject | None = None,
    opponent: str | None = None,
    companions: tuple[Companion, ...] = (),
    stat: Any = None,
    lines: LinesNamed | None = None,
    reached_when: bool = False,
) -> Asked:
    """What the grammar's rules settle ``question`` to, under the key a
    grammar named (``asked``): the subject reading's typed ``subject`` and
    ``opponent`` (nobody, where none is given), the ``companions`` it read
    beside the subject (a record "when X and Y played" is the with/without
    split), and the model's ``stat`` - context the words override where they
    name a measure (:func:`~association.query.measure.named`), read by the
    rules that turn on a count of games. The rules may settle on another key
    than ``asked`` - a count with no line is a ranking, a "when X and Y
    played" record the with/without split - and the caller reads the one
    returned.

    .. versionadded:: 6.0.0
       ``router.settle`` until Phase 3, step 4, which took a slot dict and
       the names typed (``router.Named``) and ran the taggers after the
       rules, returning a ``router.Route``.

    .. versionchanged:: 6.0.0
       Reads the grammar's text (:class:`~association.query.lexicon.GrammarText`:
       each line's number typed apart from the words) as well as a plain
       question (Phase 4, step 6).
    """
    who = subject if subject is not None else reading.Subject()
    text = lexicon.GrammarText.of(question)
    typed = lines if lines is not None else lines_named(text.text)
    # The team nicknames the words hold, read ONCE by the subject reading's
    # reader and handed to the rules as the typed subject is (slice (c)):
    # a rule that writes one as the team claims its words below, where
    # the rules' probe read them again and joined the claim with the
    # opponent's.
    team_words = team_words_in(text.text)
    working = _working(text, asked, who, opponent, stat, typed, team_words, reached_when)
    coach = _settle_rules(working, text.text, companions)
    # What the rules read: the words whose deletion changes the key they
    # settle or a name they write (span.read_by) - the rules are over the
    # whole question ("which team", "as a starter", "log"), so what they
    # read is what their decision turned on (Phase 3, step 3).
    claims = read_by(text, "intent", lambda probed: _settle_read(probed, asked, who, opponent, companions, stat, typed, team_words, reached_when))
    return Asked(
        asked=working.asked, coach=coach, subject=working.subject, opponent=working.opponent, stat=working.stat, claims=(*claims, *_team_word_claims(text.text, who, working.subject, team_words))
    )


def _team_word_claims(question: str, handed: reading.Subject, settled: reading.Subject, team_words: tuple[str, ...]) -> tuple[Claim, ...]:
    """The characters of each team nickname a rule wrote as the subject's
    team (:func:`_team_named`), where the subject reading handed none:
    the rule's reading of the word, claimed as the ``team`` the subject
    reading's own claims name a team under."""
    written = [team for team in settled.teams if team not in handed.teams and team in team_words]
    if not written:
        return ()
    return tuple(Claim(m.start(), m.end(), "team") for m in lexicon.TEAM_NICKNAME.finditer(question.lower()) if m.group(0) in written)


def _working(
    question: lexicon.GrammarText,
    asked: PointShape | None,
    subject: reading.Subject,
    opponent: str | None,
    stat: Any,
    lines: LinesNamed,
    team_words: tuple[str, ...],
    reached_when: bool = False,
) -> _Working:
    """The rules' working reading of ``question`` before any rule has run:
    the key and the names as handed, the grammar's subject and the team
    nicknames the words hold (:func:`~association.query.subject.subject_named_in`,
    :func:`~association.query.subject.team_words_in`)."""
    return _Working(asked=asked, subject=subject, opponent=opponent, stat=stat, grammar=subject_named_in(question), team_words=team_words, lines=lines, reached_when=reached_when)


def _settle_read(
    question: lexicon.GrammarText,
    asked: PointShape | None,
    subject: reading.Subject,
    opponent: str | None,
    companions: tuple[Companion, ...],
    stat: Any,
    lines: LinesNamed,
    team_words: tuple[str, ...],
    reached_when: bool,
) -> tuple[Any, ...]:
    """What the rules settle over ``question`` - the key, whether it is a
    coach's, the subject and the opponent - as one value, the reading the
    words they read are claimed by (:func:`settle`). The measure key is the
    tagger's context, not the rules' reading."""
    working = _working(question, asked, subject, opponent, stat, lines, team_words, reached_when)
    coach = _settle_rules(working, question.text, companions)
    return working.asked, coach, working.subject, working.opponent


def _settle_rules(working: _Working, question: str, companions: tuple[Companion, ...]) -> bool:
    """The rules, in order, over ``working`` (rewritten in place): whether
    the question is a coach's (refused whatever the words ask; nothing else
    is read)."""
    # A coach question is refused whatever else it asks, and carries no
    # reading, so it short-circuits before any rule below runs.
    if _coach(working, question):
        return True
    # The measure the words name stands over the model's key as the rules'
    # CONTEXT (the keys that turn on a games count read it); the measure
    # tagger reads the family itself once the key is settled.
    worded = named(question)
    if worded is not None:
        working.stat = worded[0]
    # The rules run in this order because each reads what the ones before it
    # rewrote: the keys code assigns decide which names are read, and a
    # count whose words carry no line turns back into a ranking before any
    # name of its own is settled.
    _period(working, question)
    _triple_double_abbreviation(working, question)
    _team_and_player(working, question, companions)
    _line_and_record(working, question, companions)
    _count(working, question, companions)
    _shot_distance_subject(working, question)
    _restored_subject(working, companions)
    return False


def _coach(working: _Working, question: str) -> bool:
    """True when the question is about a coach, which nothing here can
    answer (:data:`~association.query.lexicon.COACH_WORDS`). Runs before
    every other rule and stops them: a coach question is refused whatever
    the model made of it, and reading its names would only risk a team or a
    player reaching the refusal."""
    if not lexicon.COACH_WORDS.search(question):
        return False
    working.asked = TEAM_COACH
    return True


# A TEAM's quarter score (no player named) is exempted below: linescores
# answer it exactly. A PLAYER's quarter or half is the period log's, read
# from the shots and plays.
def _team_quarter_total(working: _Working) -> bool:
    """A team's quarter asked, with no player named."""
    return working.asked == TEAM_PERIOD_TOTAL and working.subject.player is None


def _names_a_period_subject(working: _Working) -> bool:
    """Whether the question's own grammar names a PLAYER as the scorer ("did
    Jokic score in the 3rd quarter") - the #170 shape, which the model files
    as the team's quarter with no player at all, and which the exemption for
    a team's own quarter must not cover: measured after the 5.0.0 prompt
    shrink, "How many points did Jokic score in the 3rd quarter against
    Boston?" arrived as ``team_quarter_points`` for the Nuggets and the
    exemption kept it there. The same reader and the same team guard as
    :func:`_period_subject`, so "did the 76ers score" stays the team's."""
    return working.grammar is not None and not is_team_name(working.grammar)


def _period(working: _Working, question: str) -> None:
    """Fouling out, and a quarter or half: keys code assigns from the
    question's own words."""
    low = question.lower()
    if lexicon.FOULED_OUT.search(low):
        # The count of a player's foul-outs: the line itself (fouls at six)
        # is the lines tagger's, the stat the measure tagger's.
        working.asked = GAMES_COUNTED
    if not (QUARTER_WORDS.search(low) or HALF_WORDS.search(low)):
        # No quarter or half named: nothing below applies - and the team a
        # rank word beside no quarter would have named is left unwritten
        # (until slice (c) "gafford v warriors most recent 5 games" had the
        # Warriors written as his team by the check below, run for nothing).
        return
    ranks_players = PERIOD_LEADERS.search(low) is not None or (PERIOD_TOP.search(low) is not None and not _team_named(working))
    if (QUARTER_WORDS.search(low) and (ranks_players or not _team_quarter_total(working) or _names_a_period_subject(working))) or HALF_WORDS.search(low):
        # A named player's quarter or half is the period log's - but only
        # where the question names one and the period is legible, since it
        # answers about a player and nothing else.
        period = which_period(question)
        if period is not None and PERIOD_AS_CONDITION.search(low):
            working.asked = None
            return
        # No second team-quarter check: it means "this key, and NO player",
        # so it can never be true where a player is named. The team's own
        # quarter is already exempted by the outer condition.
        named_player = working.subject.player is not None
        subject = _period_subject(working, period, named_player, ranks_players)
        _period_choose(working, question, period, named_player or subject is not None, ranks_players, subject)


def _period_subject(working: _Working, period: tuple[Period, Claim] | None, named_player: bool, ranks_players: bool) -> str | None:
    """The player a quarter or half question names when the model's reply
    dropped him - ISSUES.md #170: "How many points did Jokic score in the
    3rd quarter against Boston?" arrived with NO player at all and the
    teams filled instead, which reads exactly like a team's own half and
    answered Boston's quarter to a question about Jokic. The question's own
    grammar still names him (the grammar's subject the subject reading
    read), so he gets a turn before the team branch does - but only where
    nothing ranks players (that wins over a recovered subject the same way
    it wins over the model's own player) and the recovered "subject" is not
    itself a team's name: "did the 76ers score" fits the same grammar and
    stays the team's own question."""
    if period is None or named_player or ranks_players:
        return None
    candidate = working.grammar
    if candidate is not None and not is_team_name(candidate):
        working.subject = replace(working.subject, players=(candidate,))
        return candidate
    return None


def _period_log_names(working: _Working, question: str, subject: str | None) -> None:
    """A recovered quarter's player's team and opponent, kept only where the
    question's own words say them: the player came from the text, not from
    the model, so its team and opponent are no more trustworthy than the
    player it dropped - "Boston Celtics" is a word the Jokic question used,
    "Denver Nuggets" is a word it never wrote."""
    if subject is None:
        return
    opponent = team_named_in_text(question, working.subject.team) or team_named_in_text(question, working.opponent)
    working.subject = replace(working.subject, teams=())
    working.opponent = opponent


def _period_choose(working: _Working, question: str, period: tuple[Period, Claim] | None, named_player: bool, ranks_players: bool, subject: str | None) -> None:
    """Which period key a quarter or half question is, once its period, its
    player and whether it ranks are known. The period itself is the
    tagger's to write (:func:`~association.query.period.read_period`, run
    once the key is settled); the rule only chooses."""
    low = question.lower()
    if period is not None and not named_player and ranks_players:
        # Players ranked by a quarter or a half. A team may still be named
        # ("knicks 1st quarter scoring leaders"), where it narrows the
        # ranking to that team's players rather than becoming the subject.
        working.asked = PERIOD_RANKING
    elif period is not None and not named_player and _team_named(working):
        # A TEAM's half (a team's quarter is exempted above, but a half
        # always reaches here: the linescore holds both quarters). The team
        # is the subject's, or the one nickname the question holds -
        # "Celtics 2nd half scoring this season", "least points scored by
        # the wizards in the first half" (yardstick-v2 F064).
        working.asked = TEAM_PERIOD_TOTAL
    elif period is not None and named_player:
        working.asked = PERIOD_LOG
        _period_log_names(working, question, subject)
    elif period is None and named_player and BY_QUARTER.search(low):
        # A named player's four quarters side by side (#162): the period
        # log with no period, which its point reads as the breakdown.
        working.asked = PERIOD_LOG
        _period_log_names(working, question, subject)
    elif period is None and not named_player and BY_QUARTER.search(low) and not _team_named(working):
        # Every player's four quarters side by side - the league's; a team's
        # players' breakdown ("knicks points by quarter") reads as the
        # TEAM's by quarter, which is not built, and asks nothing.
        working.asked = PERIOD_RANKING
    else:
        working.asked = None


def _triple_double_abbreviation(working: _Working, question: str) -> None:
    """ "td3s" is the stat ``triple_double``, never a shot value, and one
    named player's count of them is his line, which the compiler counts.
    "luka td3s home" is the one recorded question holding the word; the
    model filed it as nothing asked, and since the 5.0.0 prompt shrink as a
    chart of his twos, so only those two are moved - a ranking or a count
    keeps its key, and a chart stays one where the words ask to draw it."""
    if not lexicon.TRIPLE_DOUBLE_ABBREVIATION.search(question):
        return
    if working.asked in (None, SHOT_CHART) and working.subject.player is not None and not lexicon.DRAW_WORDS.search(question):
        working.asked = PLAYER_LINE


def _team_named(working: _Working) -> bool:
    """Whether a team is named - the one team the subject reading handed the
    rules, or failing that exactly one nickname the question holds, which is
    then the subject's team, as typed. Two nicknames name a matchup, not a
    subject, and file nothing."""
    if working.subject.team is not None:
        return True
    if len(set(working.team_words)) != 1:
        return False
    working.subject = replace(working.subject, teams=working.team_words[:1])
    return True


#: What the grammar names a ranking of players by, where the words may ask
#: for teams (:data:`~association.query.lexicon.TEAM_SUBJECT`).
_PLAYER_RANKING_ASKS = frozenset({PLAYER_RANKING, GAME_HIGHS, GAMES_COUNTED})
#: What a "<team> when <player> reaches N" question can arrive as, each of
#: which would answer the player's own line instead of the team's record
#: under the condition.
_WHEN_REACHES_REROUTABLE = frozenset({PLAYER_LINE, GAMES_COUNTED, PLAYER_LOG, TEAM_LINE, TEAM_RECORD, PRESENCE_SPLIT, None})
#: What the fingerprint's own name moves to the fingerprint.
_FINGERPRINT_REROUTABLE = frozenset({PLAYER_COMPARISON, PLAYER_LINE, NETPOINTS_RATINGS, None, PLAYER_LOG})


def _team_and_player(working: _Working, question: str, companions: tuple[Companion, ...]) -> None:
    """A team where a player ranking was named, a record, a fingerprint with
    no fingerprint words, and a player measured against a team."""
    if working.asked in _PLAYER_RANKING_ASKS and lexicon.TEAM_SUBJECT.search(question):
        # A team ranking has a reader; a team's single-game record and a
        # count of team games do not, and answering either with players is
        # the substitution this exists to stop.
        working.asked = TEAM_RANKING if working.asked == PLAYER_RANKING else None
    if working.asked == GAMES_COUNTED and lexicon.RECORD_WORD.search(question):
        working.asked = LINE_RECORD
    _team_and_player_when_reaches(working, question, companions)
    if working.asked == NETPOINTS_FINGERPRINT and not lexicon.FINGERPRINT_WORDS.search(question):
        working.asked = SHOT_CHART if lexicon.SHOT_WORDS.search(question) else None
    if working.asked in _FINGERPRINT_REROUTABLE and lexicon.FINGERPRINT_NAMED.search(question):
        # The reverse: "compare fingerprints for embiid vs jokic in 2026"
        # arrived as a comparison after the 5.0.0 prompt shrink, and a table
        # of averages is not the radar the word asks for.
        working.asked = NETPOINTS_FINGERPRINT
    listed = _listed(working)
    if working.asked == PLAYER_COMPARISON and sum(map(is_team_name, listed)) == 1 and len(listed) == 2:
        # One player compared with a team is his games against it:
        # "compare curry vs the celtics this season" arrived as a comparison
        # with the Celtics among the players; a comparison cannot honor the
        # opponent the team becomes, and his line answers it exactly. Two
        # players and a team stay a comparison, and refuse the opponent.
        working.asked = PLAYER_LINE
    _one_player(working, question, listed)
    _matchup_against_team(working, question, listed)


def _team_and_player_when_reaches(working: _Working, question: str, companions: tuple[Companion, ...]) -> None:
    """A team's games divided by a NUMBER someone reached ("stats for
    sixers when maxey scored 20+ points") is the record over a line,
    whatever was named first - :func:`_team_and_player`'s step for it."""
    reached = working.reached_when or (lexicon.WHEN_REACHES.search(question) is not None and not any(c.predicate == "reached" for c in companions))
    # The with/without split only where nobody beside the subject reached a
    # line himself: "76ers record when they score 120 points when embiid
    # starts" is the team's line (ISSUES.md #246), "maxey points when
    # embiid scores 30" the split over Embiid's 30.
    split_held = working.asked == PRESENCE_SPLIT and any(c.predicate == "reached" for c in companions)
    if working.asked in _WHEN_REACHES_REROUTABLE and reached and working.lines.threshold is not None and not split_held:
        # "stats for sixers when maxey scored 20+ points" (yardstick-v2
        # F087): a team's games divided by a NUMBER a player reached is the
        # record over a line whatever was named first - it was answered as
        # Maxey's own average, then his career average with the 76ers. Two
        # readers agree before it moves: the "when <someone> scores/has/gets"
        # clause and a threshold in the text.
        working.asked = LINE_RECORD


def _pair_over_seasons(working: _Working, question: str, listed: list[str]) -> None:
    """ "jokic vs cade since 2022": two players "vs" over a span of seasons,
    with no compare word, is the pair relation's meetings (which honor a
    range; a comparison does not, day5). "Luka vs Giannis this year" stays a
    comparison."""
    if working.asked != PLAYER_COMPARISON or len(listed) != 2 or not lexicon.VERSUS_OR_AGAINST.search(question) or lexicon.COMPARE_WORDS.search(question):
        return
    if range_named(question) is not None or PAST_N_SEASONS.search(question):
        working.asked = PLAYER_MEETINGS


def _one_player(working: _Working, question: str, listed: list[str]) -> None:
    """A line that is a log, a log that is a count of games, and a line with
    no player that ranks the league."""
    if working.asked == PLAYER_LINE and lexicon.LOG_WORDS.search(question):
        # A game log asked for by name: "luka ft log" was answered with a
        # season average.
        working.asked = PLAYER_LOG
    _pair_over_seasons(working, question, listed)
    if working.asked == PLAYER_LOG and lexicon.HOW_MANY_GAMES.search(question) and working.stat in GAMES_STATS and not any(p.search(question) for p in ORDER_WORDS.values()):
        # "how many games did embid play" arrived as a log of his most
        # recent game after the 5.0.0 prompt shrink; the count is the
        # line's ("... in 38 games"), and a log of one game states nothing
        # of the kind. The games key is no measure: the measure tagger
        # drops it.
        working.asked = PLAYER_LINE
    if working.asked == PLAYER_LINE and not working.subject.players and WHO_RANKS.search(question):
        # No player named and "who ... the most": the league's ranking, not
        # one player's line - "who attempted the most three pointers this
        # season?" arrived as a line after the 5.0.0 prompt shrink.
        working.asked = PLAYER_RANKING


def _listed(working: _Working) -> list[str]:
    """The players handed as a list - two or more, a comparison or a pair;
    none for one player."""
    players = working.subject.players
    return list(players) if len(players) >= 2 else []


def _matchup_against_team(working: _Working, question: str, listed: list[str]) -> None:
    """Two players' meetings whose second "player" is a team: his games
    against it, a log where the words ask for games."""
    if working.asked == PLAYER_MEETINGS and any(map(is_team_name, listed)):
        working.asked = PLAYER_LOG if lexicon.LOG_WORDS.search(question) or GAMES_WORDS.search(question) else PLAYER_LINE
    if working.asked == PLAYER_MEETINGS and len(listed) < 2 and working.subject.player is not None:
        # The same question, arriving with one player and an opponent:
        # "keon ellis stats vs trailblazers", "Kd games vs wizards" - two
        # players were needed and one was had, where his line and his log
        # answer them exactly, both honoring the opponent. An opponent that
        # is NOT a team is left alone: "jay huff game log vs Embiid" really
        # is two players' meetings.
        teams = working.subject.teams
        against = working.opponent or next(iter(teams if len(teams) >= 2 else ()), None)
        if isinstance(against, str) and is_team_name(against):
            working.asked = PLAYER_LOG if lexicon.LOG_WORDS.search(question) or GAMES_WORDS.search(question) else PLAYER_LINE


#: What a record "when X and Y played" can arrive as, which the with/without
#: split answers.
_PLAYED_TOGETHER_REROUTABLE = frozenset({TEAM_MEETINGS, TEAM_RECORD, TEAM_LINE, PLAYER_LOG, None})


def _line_and_record(working: _Working, question: str, companions: tuple[Companion, ...]) -> None:
    """A history that is really a line, a record ranking, and a career high."""
    if working.asked == SEASON_HISTORY and (not names_a_stat(question) or (lexicon.VERSUS_OR_AGAINST.search(question) and working.team_words)):
        # A season-by-season history of one stat is neither "career
        # averages" (no stat named - the whole line) nor a career against
        # one team: "Jokic career averages" answered with points by season,
        # "derozan career points vs knicks" refused on its opponent.
        working.asked = PLAYER_LINE
    if working.asked == PRESENCE_SPLIT and not absent(companions) and not _played(companions) and any(pattern.search(question) for pattern in ORDER_WORDS.values()):
        # No teammate named, and "the last 7 games": a team's log, not a
        # split. "KNICKS point differential over the last 7 games" arrived as
        # the split after the 5.0.0 prompt shrink (day5) and answered the
        # last seven REGULAR-season games where the last seven were the
        # Finals - a log reads both types for "last N".
        working.asked = PLAYER_LOG
    if working.asked in _PLAYED_TOGETHER_REROUTABLE and lexicon.RECORD_WORD.search(question) and working.lines.threshold is None and (_played(companions) or _absent_by_phrase(question, companions)):
        # "PHI record when Embiid and Paul George play" arrived as two teams
        # meeting, the Pacers invented as the opponent, after the 5.0.0
        # prompt shrink; with no threshold it is the with/without split
        # (#156). "record with Embiid out" is the same split, from the
        # other side.
        working.asked = PRESENCE_SPLIT
    if working.asked == TEAM_RECORD and lexicon.BEST_WORST_RECORD.search(question) and not working.team_words:
        # The ranking's metric, the record, is the measure tagger's reading
        # of the same words; a ranking of every team is about no one team.
        working.asked = TEAM_RANKING
        working.subject = replace(working.subject, teams=())
    if working.asked == PLAYER_LINE and lexicon.CAREER_HIGH.search(question):
        # A career high is one game's total, which a line never reports:
        # "Diabate career high assists" was answered with his assists per
        # game.
        working.asked = GAME_HIGHS


def _played(companions: tuple[Companion, ...]) -> tuple[str, ...]:
    """The teammates who PLAYED beside the subject, by name (an own-side
    ``played`` companion) - what a with/without split is decided by."""
    return tuple(c.player for c in companions if c.side == "own" and c.predicate == "played")


def absent(companions: tuple[Companion, ...]) -> tuple[str, ...]:
    """The teammates who sat the games out, by name - what a with/without
    split is decided by, and what the cuts tagger reads an "opponent" that
    is the absent list again by.

    .. versionadded:: 6.0.0
    """
    return tuple(c.player for c in companions if c.absent)


def _absent_by_phrase(question: str, companions: tuple[Companion, ...]) -> bool:
    """Whether a player named beside the subject sat out by an absence
    phrase - "with Embiid out", "when Embiid is injured" - rather than by a
    plain "without Embiid": the wording that makes a record a with/without
    split."""
    return bool(absent(companions)) and lexicon.ABSENT_NAMED.search(question) is not None


def _count(working: _Working, question: str, companions: tuple[Companion, ...]) -> None:
    """A count or a record whose words carry no line: what each becomes.
    The lines tagger reads every line (:func:`~association.query.line.read_lines`);
    the rule asks only whether the words name one
    (:func:`~association.query.line.threshold_named`)."""
    line = working.lines.threshold
    # A record "when X and Y played" is the with/without split - the games
    # they were all in, beside the ones they were not - and not a record
    # over a line, which divides a season by a NUMBER a player reached. With
    # no threshold there is no number to divide by (#156). A question that
    # names one keeps its key: "Sixers record when Embiid scores 30 points".
    if working.asked == LINE_RECORD and line is None and (_played(companions) or _absent_by_phrase(question, companions)):
        working.asked = PRESENCE_SPLIT
    if working.asked == GAMES_COUNTED and working.stat in ("games", "game") and line is None:
        # "bam adebayo career games in the month of march" (yardstick-v2
        # F096) arrived as a count of games over no line on the stat
        # "games", which nothing reads. A player's games with no line on
        # them are his game log, which states how many there were - and the
        # count's subject is settled as a count's would be (the model
        # dropped Bam here).
        working.asked = PLAYER_LOG
        _grammar_subject(working)
    if working.asked == GAMES_COUNTED and line is None and not working.lines.below:
        # A count of games needs a threshold. Without one, "who has the most
        # threes" is a season ranking. A ceiling IS the count's line ("Sga
        # games with under 14 fta"), so a count stated as one keeps its key.
        working.asked = PLAYER_RANKING


def _shot_distance_subject(working: _Working, question: str) -> None:
    """A ranking by shot distance names no player: any player the model
    filled, filler or real, is dropped - a ranking never reads one (a named
    player is refused separately), and a filler value ("player" on a
    question that names nobody) would otherwise refuse for the WRONG cause
    before the ranking's own refusal (``shot_distance_ranking``) runs."""
    if working.asked != PLAYER_RANKING or not lexicon.SHOT_DISTANCE_RANKED.search(question):
        return
    if working.subject.player is not None:
        working.subject = replace(working.subject, players=())


# The keys whose missing subject is read back out of the question's grammar.
# A record over a line joined them for a question that cost 583 seconds and
# produced nothing: "what was the sixers record when maxey scored 15+
# points?" was read with the team, the stat and the threshold all right and
# no player, though the name sat in the grammar ("maxey scored"). Nothing
# here widens what counts as a subject: a record over a line with no player
# is unanswerable, not the league's, so restoring the subject can only turn
# a refusal into an answer, never a league ranking into one man's.
_SUBJECT_RESTORED_ASKS = (GAME_HIGHS, GAMES_COUNTED, LINE_RECORD)


def _restored_subject(working: _Working, companions: tuple[Companion, ...]) -> None:
    """A single-game high's, a threshold count's or a line record's missing
    subject, settled from the question's own grammar - only where the
    reader reads one player: a ranking with no player IS the league's, and
    settling the subject only where the grammar names one (#138) never
    turns a league question into one about somebody it only appears to
    name. A line record's player is the companion who reached the line,
    as the subject reading typed him ("sixers record when maxey scored
    20+ points": Maxey), read from the grammar's words until slice (c)."""
    if working.asked == LINE_RECORD and not working.subject.players:
        reached = next((c.player for c in companions if c.predicate == "reached" and c.side == "own"), None)
        if reached is not None:
            working.subject = replace(working.subject, players=(reached,))
            return
    if working.asked in _SUBJECT_RESTORED_ASKS:
        _grammar_subject(working)


def _grammar_subject(working: _Working) -> None:
    """The player the question's grammar names, as the one player the
    reading is about - where nobody is named yet."""
    if not working.subject.players and working.grammar is not None:
        working.subject = replace(working.subject, players=(working.grammar,))
