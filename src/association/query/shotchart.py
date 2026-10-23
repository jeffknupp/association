"""Whose shots a chart is drawn for: the chart's player resolution
(:func:`resolve_chart_player`). What a shot is worth is
:mod:`association.nba.shot_value`'s.

.. versionchanged:: 5.0.0
   The rendering entry points (``render_shot_chart`` and
   ``render_for_player``) are gone: their read is the shot relation's
   reader (:mod:`association.query.compose.shots`, ``read_shot_chart``),
   the drawing its draw step over
   :func:`association.query.court.render_court_html`, and the message the
   sayer's (``compose.say.say_shot_chart``) - Phase 2, step 5.

.. versionchanged:: 7.0.0
   The shot value (``SHOT_VALUE_SQL``) and the seasons it cannot tell or
   derives moved to :mod:`association.nba.shot_value`, which the fetch path
   reads too.
"""

from __future__ import annotations

import duckdb

from .entities import MAX_CANDIDATES, Ambiguous, Availability, Entity, find_players, names_of, narrow_to_available, players_of, read_near_spelling
from .entities import SHOT_AVAILABILITY as SHOT_AVAILABILITY

ChartResolution = tuple[Entity, list[str]] | Ambiguous | None
"""What resolving a chart's player can come to: the player and any other names
that matched, a question about which of several was meant, or None for a name
nothing matched.

.. versionadded:: 2.1.0
"""


def resolve_chart_player(con: duckdb.DuckDBPyConnection, player_name: str, available: Availability, season: int | None = None) -> ChartResolution:
    """The player a chart is drawn for, a clarifying question, or None.

    Narrowed by data before it is decided, which is what lets a chart keep
    answering a bare surname without guessing. Of the candidates a name
    matches, only those with a row in ``available`` for ``season`` could have
    produced the chart being asked for; dropping the rest is a fact about the
    warehouse rather than a preference between people. Exactly one left is the
    answer. Two or more are a real question, and it gets asked.

    Anything needing the athlete_id alongside a chart - scoping to one game, say
    - must resolve through this once and read with the player it returns, NOT
    resolve separately. Two independent resolutions of the same name can pick
    different players, which would scope the chart to a game the other one
    played.

    .. versionadded:: 1.2.0

    .. versionchanged:: 2.1.0
       Takes ``available`` and ``season``, and may return
       :class:`association.query.entities.Ambiguous`. It previously took the
       best match unconditionally, on the reasoning that a chart of the wrong
       Curry is obvious on sight because the plot is titled with the resolved
       name - which holds only when a plot is drawn. "Maxey" resolved to Marlon
       Maxey, who last played in 1994, and the answer was a false claim that
       the warehouse had no fingerprint data for the season. Measured over the
       566 players with a 2026 fingerprint, 319 have a surname that matches
       somebody else and 221 surnames league-wide put a player with no
       fingerprint ahead of one who has it.

       Every match is narrowed, not the first ``MAX_CANDIDATES``. Narrowing a
       page cut by name drew Anthony Davis for "Davis" while JD Davison, Nigel
       Hayes-Davis and Trayce Jackson-Davis also had 2026 shots, and 22 more
       names did the same that season.

    .. versionchanged:: 5.0.0
       A name that matches nobody but is a near spelling of exactly one player
       resolves to him rather than None, and the reading is reported through
       :func:`association.query.entities.collect_name_readings`. See
       :func:`association.query.entities.read_near_spelling`.
    """
    # Every match, not find_players' first page. Narrowing a page cut
    # alphabetically chooses by name rather than eliminating: Anthony Davis was
    # the only "Davis" on the first page with 2026 shots, and the three more
    # who had them sorted past it.
    candidates = find_players(players_of(con), player_name, limit=None)
    if not candidates:
        # One near spelling is that player, and the reading is said in the
        # answer - the same default resolve_player takes. Not narrowed to who
        # has chart rows: a single candidate has nobody to be eliminated in
        # favor of, and the renderer's own message says what he lacks.
        near = read_near_spelling(names_of(con), player_name)
        return None if near is None else (near, [])
    if len(candidates) > 1:
        narrowed = narrow_to_available(con, candidates, available, season)
        # Narrowing that eliminates EVERYBODY is not a reason to ask which one
        # was meant: no answer to that question draws a chart either, so the
        # renderer's own message - which names the player it tried and what the
        # season does hold - explains more than the question would. So it
        # narrows only where it discriminates, and otherwise leaves the old
        # best match in place to fail loudly.
        if len(narrowed) > 1:
            # Every survivor played in the season charted, so none may be
            # counted away - unless no season was given, and nobody did.
            return Ambiguous(query=player_name, candidates=[c.name for c in narrowed], active=0 if season is None else len(narrowed))
        if narrowed:
            return narrowed[0], []
    # The runners-up a failure message mentions are the page find_players has
    # always returned, rather than every Williams in the warehouse.
    shown = candidates[:MAX_CANDIDATES]
    return shown[0], [c.name for c in shown[1:]]
