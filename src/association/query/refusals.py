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
from typing import Any

import duckdb

from association.query.calendar import parse_situation
from association.query.entities import find_players, find_teams
from association.query.templates.common import PLAYER_INTENTS, TemplateResult

#: Intents whose ``opponent`` is a team the subject played against. A PLAYER
#: in that slot is the pair relation's question - "lebron vs kawhi head to
#: head", "jay huff game log vs embiid" - which `player_matchup` answers
#: from two reads of the relation joined on the event; the router filed the
#: second player as an opponent, and a genuine two-player matchup refuses
#: `opponent`, so both fell through (yardstick-v2 F081, F142).
_PAIRABLE_INTENTS: frozenset[str] = frozenset({"player_matchup", "game_log", "player_stat", "threshold_count", "single_game_high", "streak", "record_when", "player_splits"})


_BENCH_POINTS = re.compile(r"\bbench\s+(?:points?|scoring|pts)\b", re.IGNORECASE)
_AGE = re.compile(r"\b(?:\d+\s+years?\s+old|(?:before|after|by|at)\s+(?:turning|age)\s+\d+|age\s+\d+)\b", re.IGNORECASE)
_CONFERENCE_OR_DIVISION = re.compile(r"\b(?:east(?:ern)?|west(?:ern)?|conference|division|atlantic|central|southeast|northwest|pacific|southwest)\b", re.IGNORECASE)


def unanswerable(con: duckdb.DuckDBPyConnection, intent: str, slots: dict[str, Any], question: str) -> TemplateResult | None:
    """The refusal for a question shape nothing here reads, or None where the
    agent should have its turn. Called only after the template refused and
    the compiler declined, so an answerable question never reaches it.

    .. versionadded:: 4.4.0
    """
    for check in (_playoff_round, _non_calendar_situation, _period_stat, _team_period_stat, _bench_points, _team_where_a_player_belongs):
        message = check(con, intent, slots, question)
        if message is not None:
            return TemplateResult(data={"message": message, "refused": check.__name__.lstrip("_"), "intent": intent}, answer=message)
    return None


def _playoff_round(con: duckdb.DuckDBPyConnection, intent: str, slots: dict[str, Any], question: str) -> str | None:
    """A named round: the games carry no round or series label (ISSUES #10)."""
    playoff_round = slots.get("round")
    if not isinstance(playoff_round, str) or not playoff_round.strip():
        return None
    return (
        f"The games are not labeled by playoff round, so '{playoff_round}' cannot pick them out yet. "
        "Name the two teams and the season instead - a series is their postseason meetings, and those are read."
    )


def _non_calendar_situation(con: duckdb.DuckDBPyConnection, intent: str, slots: dict[str, Any], question: str) -> str | None:
    """A ``situation`` that names no calendar: an age (no birth dates on
    record), a conference or division (in the standings, not yet read), or
    anything else the games are not read by."""
    situation = slots.get("situation")
    if not isinstance(situation, str) or not situation.strip() or parse_situation(situation) is not None:
        return None
    if _AGE.search(situation):
        return f"'{situation}' needs a birth date, and the player records here carry none - so no answer can be narrowed by age. Ask by season instead (the season he turned that age)."
    if _CONFERENCE_OR_DIVISION.search(situation):
        return f"'{situation}' narrows by conference or division, which are not read from the standings yet - name the teams instead, or ask without the narrowing."
    return f"'{situation}' is not something the games are read by - a weekday, a month, a holiday or \"since <day>\" is. Ask without it, or with one of those."


def _period_stat(con: duckdb.DuckDBPyConnection, intent: str, slots: dict[str, Any], question: str) -> str | None:
    """A stat other than points by quarter or half: the per-period figures
    are rebuilt from the scoring plays, so points is the only one."""
    stat = slots.get("stat")
    if intent != "period_split" or not isinstance(stat, str) or stat in ("points", "pts", ""):
        return None
    period = slots.get("period") or slots.get("half")
    where = f"the {period}{'st' if period == 1 else 'nd' if period == 2 else 'rd' if period == 3 else 'th'} {'half' if slots.get('half') else 'quarter'}" if isinstance(period, int) else "a period"
    return f"By quarter or half, only points are on record - {stat!r} is not split by period. Ask for points in {where}, or for {stat} over whole games."


def _team_where_a_player_belongs(con: duckdb.DuckDBPyConnection, intent: str, slots: dict[str, Any], question: str) -> str | None:
    """A team in the ``player`` slot of a template that answers for one
    player: ask which player was meant, or send the team's own question to
    the team templates."""
    player = slots.get("player")
    if intent not in PLAYER_INTENTS or not isinstance(player, str) or not player.strip():
        return None
    if find_players(con, player) or not find_teams(con, player):
        return None
    return f"'{player}' is a team, and this was read as a question about one player's {slots.get('stat') or 'stats'}. Name a player, or ask for the team's own record or stats."


def pair_from_opponent(con: duckdb.DuckDBPyConnection, intent: str, slots: dict[str, Any]) -> str | None:
    """Rewrite a player filed as the ``opponent`` into the second of two
    ``players``, so :func:`~association.query.templates.games.player_matchup`
    answers the meetings; returns the opponent's name when it did, else None.
    Only where the opponent matches a player and no team - a team opponent,
    or a name matching nothing, is left for the template to read or refuse.

    Not a refusal: this module's one repair, kept here beside the refusal it
    replaced (a "not read yet" that was false - the pair relation is read).

    .. versionadded:: 4.4.0
    """
    opponent, player = slots.get("opponent"), slots.get("player")
    if intent not in _PAIRABLE_INTENTS or not isinstance(opponent, str) or not opponent.strip() or not isinstance(player, str) or not player.strip():
        return None
    if find_teams(con, opponent) or not find_players(con, opponent):
        return None
    slots["players"] = [player, opponent]
    slots.pop("player", None)
    slots.pop("opponent", None)
    return opponent


def _team_period_stat(con: duckdb.DuckDBPyConnection, intent: str, slots: dict[str, Any], question: str) -> str | None:
    """A team's stat other than points by quarter or half: the linescore
    holds each team's points per period and nothing else (yardstick-v2 F065,
    "trailblazers ... 3 point average 1st quarter")."""
    stat = slots.get("stat")
    if intent != "team_quarter_points" or not isinstance(stat, str) or stat in ("points", "pts", ""):
        return None
    return (
        f"A team's quarter or half holds points only - the linescore is the one per-period figure on record, and {stat!r} is not split by period. "
        f"Ask for the team's points in that period, or for {stat} over whole games."
    )


def _bench_points(con: duckdb.DuckDBPyConnection, intent: str, slots: dict[str, Any], question: str) -> str | None:
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
