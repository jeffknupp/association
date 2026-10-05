"""What a shot is worth, and whose shots a chart is drawn for: the shot
value read from the table's columns (:data:`SHOT_VALUE_SQL`), the seasons
whose twos and threes cannot be told apart or are derived, and the chart's
player resolution (:func:`resolve_chart_player`).

.. versionchanged:: 5.0.0
   The rendering entry points (``render_shot_chart`` and
   ``render_for_player``) are gone: their read is the shot relation's
   reader (:mod:`association.query.compose.shots`, ``read_shot_chart``),
   the drawing its draw step over
   :func:`association.query.court.render_court_html`, and the message the
   sayer's (``compose.say.say_shot_chart``) - Phase 2, step 5.
"""

from __future__ import annotations

import duckdb

from .court import BEYOND_THE_ARC_SQL, HAS_POSITION_SQL
from .entities import MAX_CANDIDATES, Ambiguous, Availability, Entity, find_players, narrow_to_available, read_near_spelling
from .entities import SHOT_AVAILABILITY as SHOT_AVAILABILITY

UNSEPARABLE_SHOT_VALUES: dict[int, str] = {
    2002: (
        "ESPN did not label 2002's shots as twos or threes, and unlike every later season its shot descriptions do not name every three - counted against the "
        "box score they miss about one in sixty - so threes cannot be reliably separated from twos"
    ),
}
"""Seasons where nothing reliably says whether a shot was a two or a three, by
season, with the reason. A question filtered to twos or threes in one of them
is refused rather than answered from whatever subset happens to be labeled.

.. versionadded:: 2.1.0
"""

DERIVED_SHOT_VALUES: dict[int, str] = {
    2003: ("ESPN did not label 2003's shots as twos or threes, so they are read from each shot's description, whose count of threes matches the box score's to within 0.01%"),
    2022: (
        "ESPN labeled only 4% of 2022's shots as twos or threes, so the rest are read from the description where it says and otherwise from where the shot "
        "was taken against the three-point line, which counts 0.2% more threes than the box score does"
    ),
}
"""Seasons whose shot values are mostly derived rather than labeled by ESPN,
with the caveat an answer filtered to twos or threes carries.

.. versionadded:: 2.1.0
"""

TEXT_NAMES_EVERY_THREE_UNTIL = 2012
"""The last season whose shot descriptions name every three.

Through 2012 "three point" is in the description of every shot ESPN labels a
three and of no shot it labels a two - 100.00% agreement in each of 2004-2012.
From 2013 step-backs and pull-ups stop saying so (99.66% in 2013, 95.45% by
2024), and only the shot's position is left to decide.

.. versionadded:: 2.1.0
"""

_UNSEPARABLE = ", ".join(str(s) for s in sorted(UNSEPARABLE_SHOT_VALUES))

SHOT_VALUE_SQL = f"""(CASE
    WHEN shot_type ILIKE '%free throw%' THEN 1
    WHEN points_attempted IN (2, 3) THEN points_attempted
    WHEN description ILIKE '%three point%' THEN 3
    WHEN description ILIKE '%two point%' THEN 2
    WHEN season IN ({_UNSEPARABLE}) THEN NULL
    WHEN season <= {TEXT_NAMES_EVERY_THREE_UNTIL} THEN 2
    WHEN NOT {HAS_POSITION_SQL} THEN NULL
    WHEN {BEYOND_THE_ARC_SQL} THEN 3
    ELSE 2
END)"""
"""A ``shot_chart`` row's value - 1, 2 or 3 - as SQL, or NULL where nothing
establishes it. The one definition every shot-value filter reads.

``points_attempted`` cannot be filtered on directly, because **0 there means
unlabeled, not zero points**: every shot of 2002 and 2003, 96% of 2022's, and
about a quarter of each season's from 2004 to 2012 - every one of those a miss,
so "his twos" answered from the labels alone came out at a 72% field goal
percentage. So the label is used where there is one, and otherwise, in order:

- ``shot_type`` for free throws, which are unlabeled in 2002, 2003 and 2022;
- the description, where it says "three point" or "two point" - a label ESPN
  wrote in prose, which disagrees with its numeric label on at most 3 shots a
  season;
- through :data:`TEXT_NAMES_EVERY_THREE_UNTIL`, a two, since those seasons'
  descriptions name every three;
- after it, the shot's position against the three-point line
  (:data:`association.query.court.BEYOND_THE_ARC_SQL`), which matches ESPN's
  own labels on 99.83-99.94% of shots in every season it labeled.

Counted against the box score's three-point attempts per player-game, the
result matches in 99.3-100% of games in every season but 2002 - which is
:data:`UNSEPARABLE_SHOT_VALUES`, and NULL here for any shot neither labeled nor
described.

.. versionadded:: 2.1.0
"""


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
    candidates = find_players(con, player_name, limit=None)
    if not candidates:
        # One near spelling is that player, and the reading is said in the
        # answer - the same default resolve_player takes. Not narrowed to who
        # has chart rows: a single candidate has nobody to be eliminated in
        # favor of, and the renderer's own message says what he lacks.
        near = read_near_spelling(con, player_name)
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
