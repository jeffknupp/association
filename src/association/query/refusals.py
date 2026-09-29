"""What nothing here can answer, refused fast and with its cause.

The pipeline's last step before the SQL-writing agent. A template that cannot
honor a question raises ``TemplateUnsupported``; the compiler gets one try;
and then, before the agent, this module asks whether the shape is one the
agent has no better source for either. Where it is, a refusal naming the
missing thing IS the answer - the same reasoning as
:func:`association.query.templates.common.check_coverage`, which returns a
floor refusal rather than raising it: the agent would query the same tables,
take a minute over it, and is then free to fill the silence from its own
weights (AGENTS.md, "Refusing beats falling through wherever the agent has
nothing to read"). Measured on the yardstick's fall-throughs, 2026-09-23: a
playoff round, an age, a conference, and a stat other than points by quarter
each took 30-120 seconds to reach an agent
answer that was wrong or never came.

Every shape here is one the templates already refuse and the warehouse has no
column for; ``tests/query/test_refusals.py`` checks the first half of that
for each, so a shape that gains a template stops being refused here the day
it does. A cause has to be the RIGHT one - "players carry no birth date" for
an age, not "no data" - because a refusal naming the wrong cause reads as
honest and sends the reader somewhere useless (AGENTS.md, "a refusal that
names the wrong cause").

.. versionadded:: 4.4.0
"""

from __future__ import annotations

import re

import duckdb

from association.query.calendar import parse_alignment, parse_situation
from association.query.entities import find_teams
from association.query.player_games import PERIOD_COLUMNS
from association.query.reading import Reading, Scope
from association.query.subject import Subject
from association.query.templates.common import PLAYER_INTENTS, TemplateResult

_CHAMPIONSHIP = re.compile(r"\b(?:championships?|champions?|nba\s+titles?|won\s+the\s+(?:title|finals)|title\s+winners?|finals\s+winners?)\b", re.IGNORECASE)
_BENCH_POINTS = re.compile(r"\bbench\s+(?:points?|scoring|pts)\b", re.IGNORECASE)
_AGE = re.compile(r"\b(?:\d+\s+years?\s+old|(?:before|after|by|at)\s+(?:turning|age)\s+\d+|age\s+\d+)\b", re.IGNORECASE)
_CONFERENCE_OR_DIVISION = re.compile(r"\b(?:east(?:ern)?|west(?:ern)?|conference|division|atlantic|central|southeast|northwest|pacific|southwest)\b", re.IGNORECASE)


def unanswerable(con: duckdb.DuckDBPyConnection, reading: Reading, question: str) -> TemplateResult | None:
    """The refusal for a question shape nothing here reads, or None where the
    agent should have its turn. Called only after the template refused and
    the compiler declined, so an answerable question never reaches it.
    ``reading`` is the parser's (:func:`~association.query.parse.reading_from_route`):
    the checks read its intent, its typed scope and the subject it read.

    .. versionadded:: 4.4.0

    .. versionchanged:: 5.0.0
       Takes the :class:`~association.query.reading.Reading` in place of an
       intent, a slot dict and a subject read here for a caller with none
       (ROADMAP plan item 6, step (f)).
    """
    subject = reading.subject
    if subject is None:
        # Who the question is about is read once, by the parser; a Reading
        # with none is a caller's mistake, said here rather than as an
        # AttributeError inside a check.
        raise ValueError("unanswerable needs the reading's subject - who the question is about, as the parser read it")
    for check in (_playoff_round, _non_calendar_situation, _period_stat, _team_period_stat, _bench_points, _team_where_a_player_belongs, _team_boolean_count):
        message = check(con, reading.intent, reading.scope, question, subject)
        if message is not None:
            return TemplateResult(data={"message": message, "refused": check.__name__.lstrip("_"), "intent": reading.intent}, answer=message)
    return None


def _playoff_round(con: duckdb.DuckDBPyConnection, intent: str, scope: Scope, question: str, subject: Subject) -> str | None:
    """A named round: the games carry no round or series label (ISSUES #10)."""
    playoff_round = scope.round
    if not isinstance(playoff_round, str) or not playoff_round.strip():
        return None
    return (
        f"The games are not labeled by playoff round, so '{playoff_round}' cannot pick them out yet. "
        "Name the two teams and the season instead - a series is their postseason meetings, and those are read."
    )


def _non_calendar_situation(con: duckdb.DuckDBPyConnection, intent: str, scope: Scope, question: str, subject: Subject) -> str | None:
    """A ``situation`` that names neither a calendar narrowing nor a
    conference or division (:func:`association.query.calendar.parse_situation`/
    :func:`~association.query.calendar.parse_alignment`, the same two readers
    the relations' own shared steps try): an age (no birth dates on record),
    or anything else the games are not read by.

    A conference/division WORD in a shape ``parse_alignment`` does not
    recognize ("the Central Division these days") gets its own message naming
    the shape that is read, rather than the generic one - the words are
    right and only the phrasing is not, which is a different sentence than
    "not something the games are read by".

    .. versionchanged:: 4.4.0
       A conference or division IS read now (K3-2), by value - this used to
       refuse every one outright ("not read from the standings yet").
    """
    situation = scope.situation
    if not isinstance(situation, str) or not situation.strip():
        return None
    if parse_situation(situation) is not None or parse_alignment(situation) is not None:
        return None
    if _AGE.search(situation):
        return f"'{situation}' needs a birth date, and the player records here carry none - so no answer can be narrowed by age. Ask by season instead (the season he turned that age)."
    if _CONFERENCE_OR_DIVISION.search(situation):
        return f'\'{situation}\' names a conference or division, but not in a shape this reads - try "vs the west", "against eastern conference teams" or "vs the southeast division".'
    return f"'{situation}' is not something the games are read by - a weekday, a month, a holiday, \"since <day>\", a conference or a division is. Ask without it, or with one of those."


def _period_stat(con: duckdb.DuckDBPyConnection, intent: str, scope: Scope, question: str, subject: Subject) -> str | None:
    """A stat the period's line does not rebuild, by quarter or half: the
    per-period figures are rebuilt from the shots and plays
    (:data:`~association.query.player_games.PERIOD_COLUMNS`), and play-by-play
    carries no minutes, plus-minus or rate per quarter.

    .. versionchanged:: 5.0.0
       Names the columns that ARE rebuilt (plan item 4): until the period
       relation, points were the only one, and this said so of rebounds and
       assists too - a refusal naming a cause that is no longer true.
    """
    stat = scope.stat
    if intent not in ("period_split", "period_leaderboard") or not isinstance(stat, str) or stat in ("pts", "", "all") or stat in PERIOD_COLUMNS:
        return None
    period = scope.period or scope.half
    where = f"the {period}{'st' if period == 1 else 'nd' if period == 2 else 'rd' if period == 3 else 'th'} {'half' if scope.half else 'quarter'}" if isinstance(period, int) else "a period"
    return (
        f"By quarter or half, a line is rebuilt from the play-by-play - points, field goals, free throws, rebounds, assists, steals, blocks, turnovers and fouls - "
        f"and {stat!r} is not among them. Ask for one of those in {where}, or for {stat} over whole games."
    )


def _team_where_a_player_belongs(con: duckdb.DuckDBPyConnection, intent: str, scope: Scope, question: str, subject: Subject) -> str | None:
    """A team in the ``player`` slot of a template that answers for one
    player - the reading says the subject is the team and names no player:
    ask which player was meant, or send the team's own question to the team
    templates.

    Only where the slot does name a team, by the team index: a team's record
    split by a PLAYER beside it ("celtics record when jayson tatum scores")
    reads as a team subject with Tatum in the ``player`` slot, and was
    refused as "'jayson tatum' is a team". There the fact missing is the line
    the record is split by, and the refusal says that
    (:func:`_team_where_a_player_belongs_line`) - or nothing, for a shape it
    has no sentence for. A word that is a team's and a player's both
    ("magic") is the team here: the reading already chose it over the
    player."""
    player = scope.player
    if intent not in PLAYER_INTENTS or not isinstance(player, str) or not player.strip():
        return None
    if subject.kind not in ("team", "team_players") or subject.players:
        return None
    if not find_teams(con, player):
        return _team_where_a_player_belongs_line(intent, scope, player)
    return f"'{player}' is a team, and this was read as a question about one player's {scope.stat or 'stats'}. Name a player, or ask for the team's own record or stats."


def _team_where_a_player_belongs_line(intent: str, scope: Scope, player: str) -> str | None:
    """The refusal for a player named beside a team where ``record_when`` has
    no line to split the team's games by: the number and the stat together,
    which is what the template needs and the question did not give."""
    threshold = scope.threshold
    if intent != "record_when" or (isinstance(threshold, int) and not isinstance(threshold, bool) and threshold >= 1 and scope.stat):
        return None
    team = f" {scope.team}" if isinstance(scope.team, str) and scope.team.strip() else " team's"
    return (
        f"A record split by '{player}' needs a line - a number and a stat, as in \"when {player} scores 30+ points\" - and this question gives none it can read. "
        f"Ask with the line, or for the{team} record with and without {player}."
    )


def _team_boolean_count(con: duckdb.DuckDBPyConnection, intent: str, scope: Scope, question: str, subject: Subject) -> str | None:
    """A team's count of its players' triple-doubles or double-doubles, as a
    ranking with the team filed: "oklahoma city thunder all-time triple
    doubles vs west" arrives as ``leaderboard`` with ``team`` and
    ``stat: triple_double`` (day5, after the 5.0.0 prompt shrink), and the
    compiler's decline said "no ranking reads triple_double" - the wrong
    cause, since one player's triple-doubles ARE counted; what is not read
    is the team's aggregate of them."""
    stat = scope.stat
    if intent != "leaderboard" or stat not in ("triple_double", "double_double") or subject.kind not in ("team", "team_players") or not scope.team:
        return None
    label = "triple-doubles" if stat == "triple_double" else "double-doubles"
    return f"A team's total of its players' {label} is not read yet - one player's {label} are (ask '<player> triple doubles this season'), and so is the team's own record. Ask one of those."


def _team_period_stat(con: duckdb.DuckDBPyConnection, intent: str, scope: Scope, question: str, subject: Subject) -> str | None:
    """A team's stat other than points by quarter or half: the linescore
    holds each team's points per period and nothing else (yardstick-v2 F065,
    "trailblazers ... 3 point average 1st quarter")."""
    stat = scope.stat
    if intent != "team_quarter_points" or not isinstance(stat, str) or stat in ("points", "pts", ""):
        return None
    return (
        f"A team's quarter or half holds points only - the linescore is the one per-period figure on record, and {stat!r} is not split by period. "
        f"Ask for the team's points in that period, or for {stat} over whole games."
    )


def _bench_points(con: duckdb.DuckDBPyConnection, intent: str, scope: Scope, question: str, subject: Subject) -> str | None:
    """Bench points: derivable (the non-starters' points in the box score,
    which flags starters) but read by nothing yet - a gap of ours, named as
    one, not "no data" (yardstick-v2 F106, "most opponent bench points
    allowed ...")."""
    if not _BENCH_POINTS.search(question):
        return None
    return (
        "Bench points are not read yet - the box score flags starters, so a bench total could be built, but no template or the compiler adds one up today. "
        "Ask for a named player's points, or a team's points, instead."
    )


def by_question(question: str, intent: str | None) -> TemplateResult | None:
    """A refusal decided from the question's own words BEFORE any template
    runs - for a shape a template would otherwise answer fluently and wrongly.
    "Show which team won the nba championship for the past 10 years" (Jeff's
    session, 2026-09-24) routed ``team_leaderboard`` and ranked regular-season
    records since 2017. The warehouse holds every playoff game and no table of
    titles; a champion is derivable (the winner of a postseason's last game)
    and nothing derives it yet, so the refusal names that and the question
    that works.

    .. versionadded:: 4.4.0
    """
    if not _CHAMPIONSHIP.search(question):
        return None
    if re.search(r"\btitle\s+odds\b|\bchampionship\s+odds\b", question, re.IGNORECASE):
        return None  # "title odds" is a regular-season projection team_outlook answers
    message = (
        "Championships are not on record as such - the warehouse holds every playoff game and no table of titles, and nothing derives a champion from a postseason's last series yet. "
        "Ask for a team's postseason record in a season, or two teams' playoff meetings, to see who won a series."
    )
    return TemplateResult(data={"message": message, "refused": "championship", "intent": intent}, answer=message)
