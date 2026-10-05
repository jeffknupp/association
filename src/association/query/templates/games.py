"""What is left of the game-level templates: whether a team slot beside a
named player is his opponent (``compose.core`` reads it). The game log,
two teams' meetings, a team's quarter and the league's ranking by one went
to readers and the sayer (Phase 2: ``compose.logs``, ``compose.meetings``,
``compose.periods``).

.. versionadded:: 3.0.0
   Split out of the former ``association.query.templates`` module.
"""

from __future__ import annotations

from typing import Any

import duckdb

from association.query.measures import PERIOD_RATE_STATS as PERIOD_RATE_STATS
from association.query.reading import DEFAULT_GAME_LOG_LIMIT as DEFAULT_GAME_LOG_LIMIT

from ..entities import Entity, resolve_team


def _played_for(con: duckdb.DuckDBPyConnection, player: Entity, team: Entity) -> bool:
    """Whether ``player`` has ever suited up for ``team``, anywhere in
    ``player_game_log``.

    The only question this answers is "is this team the SUBJECT, not the
    opponent" - so it deliberately looks across his whole career rather than
    the season in scope: a team slot naming a season he was not on it is still
    not an opponent, and treating it as one would file a real former team as
    though the two had played each other.
    """
    row = con.execute("SELECT 1 FROM player_game_log WHERE athlete_id = ? AND team_id = ? LIMIT 1", [player.id, team.id]).fetchone()
    return row is not None


def _team_slot_for_player(con: duckdb.DuckDBPyConnection, player: Entity, team_text: str, *, season: int | None, opponent: Any) -> Any:
    """What a ``team`` slot means once ``player`` is named - see #147.

    An ``opponent`` already named wins outright: a ``team`` slot beside it is
    the same noise the router routinely fills alongside an already-correct
    opponent, not a second fact to reconcile - measured on the filed corpus
    rows, it is Payton Pritchard's invented "Phoenix Suns" beside a correct
    "Philadelphia 76ers" opponent, and Kobe Bryant's own "Los Angeles Lakers"
    beside a correct "Houston Rockets" one. Comparing the two and refusing
    when they disagreed was tried first and was wrong for exactly this shape:
    "Phoenix Suns" is a real, resolvable team, so a naive conflict check
    refused Pritchard's question rather than answering it.

    With no ``opponent`` already named, his own team narrows nothing, so it is
    dropped; a different, real team is his opponent (the Curry shape); and a
    name nothing resolves to - the router inventing a team the way it
    sometimes invents a player, see AGENTS.md's "the router invents names" -
    or an ambiguous one, is dropped rather than guessed at or asked about: the
    player, not the team, is what the question is about.
    """
    if isinstance(opponent, str) and opponent.strip():
        return opponent
    match resolve_team(con, team_text, season):
        case Entity() as team:
            return opponent if _played_for(con, player, team) else team_text
        case _:
            # NotFound or Ambiguous - dropped either way, since `opponent` is
            # not set here for either to fill.
            return opponent
