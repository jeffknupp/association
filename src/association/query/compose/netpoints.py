"""The NetPoints relation's readers: a player's NetPoints (``player_netpoints``)
and his play-type fingerprint (``fingerprint``), read from ESPN Analytics'
NetPoints tables into a :class:`~association.query.result.Result` -
Phase 2's slice (v) (``ROADMAP.md``, step 5, and the decision "Charts are
declared shapes": each keeps its own reader and renderer, declared with its
relation, and is not ported onto a NetPoints relation before Phase 3).

``player_netpoints`` is a scalar and a split by play type
(``ROADMAP-TYPES.md``, "Still open" 6): a season's ratings from
``net_points_player`` and the categories behind them from
``net_points_player_fingerprint`` (a :class:`~association.query.result.Scalar`
and a :class:`~association.query.result.Grouped` by ``category``), or one
game's from ``net_points_player_game``. The retired template's three
statements are built here and executed through the compiler's one door
(:func:`~association.query.compose.core.values_of`). The sayer
(:mod:`association.query.compose.say`) words each.

Three tables, three encodings of the season type (``DATA.md``):
``net_points_player`` keeps its own STRING (``net_points_season_type``,
"Regular Season"), the per-game tables the numeric 2/3, and the season
fingerprint has none at all - filtering one with another's silently
matches nothing.

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import duckdb

from association.nba.netpoints import FINGERPRINT_CATEGORIES, FINGERPRINT_PARTITION
from association.nba.season import current_season, eastern_date
from association.query.entities import Availability, Entity
from association.query.fingerprint import FINGERPRINT_AVAILABILITY, FINGERPRINT_SUMMARY_CATEGORY
from association.query.metrics import SEASON_TYPE_LABELS
from association.query.notes import Note
from association.query.reading import Scope
from association.query.result import Decided, Grouped, Part, Result, Scalar, Span, Window
from association.query.season_line import Statement, season_redirect
from association.query.templates.common import SEASON_TYPE_NAMES, TemplateResult, TemplateUnsupported, resolved_player, season_phrase, unhonored_scoping

from .core import values_of


@dataclass(frozen=True)
class NetPointsQuery:
    """A point on the NetPoints relation, planned: the scope the question
    settled and the shape the reader names - one player's NetPoints
    (``"scalar"``: a season's ratings and the split by play type behind
    them, or one game's) or his fingerprint (``"chart"``). The reader reads
    the player, the season, its type, the unit, the side and a first or
    last game from the scope, as the retired templates read their slots.

    .. versionadded:: 5.0.0
    """

    scope: Scope
    shape: Literal["scalar", "chart"]
    relation: Literal["netpoints"] = "netpoints"
    #: Whose the point is - one or more named players - for the stage
    #: record, which names a planned query's subject.
    subject: Literal["player"] = "player"


# --- a player's NetPoints --------------------------------------------------------

# player_netpoints reads the season totals AND the fingerprint, and the two
# disagree about who they hold: measured, 63 player-seasons are in the first
# only and 8 in the second only. A row in either is an answer.
_NETPOINTS_TABLES = (Availability("net_points_player"), FINGERPRINT_AVAILABILITY)

_NETPOINTS_GAMES = Availability("net_points_player_game")

#: The fingerprint's play-type categories, the summary (``total``) aside: it
#: is reported on the headline line rather than as a category row.
_NETPOINTS_CATEGORIES = tuple(c for c in FINGERPRINT_CATEGORIES.values() if c != FINGERPRINT_SUMMARY_CATEGORY)

#: ``net_points_player``'s season line, in the order the Result reads it.
_NETPOINTS_TOTALS = ("overall", "offense", "defense", "overall_per_100_poss", "total_minutes", "games")


def netpoints_totals_statement(athlete_id: str, season: int, season_type: int) -> Statement:
    """One player's season NetPoints line (:data:`_NETPOINTS_TOTALS`).
    ``net_points_player`` uses its OWN string season_type; filtering it with
    the numeric one every other table uses silently matches nothing.

    .. versionadded:: 5.0.0
       ``templates.netpoints.player_netpoints``' first statement.
    """
    label = SEASON_TYPE_LABELS.get(season_type, "Regular Season")
    return Statement(f"SELECT {', '.join(_NETPOINTS_TOTALS)} FROM net_points_player WHERE athlete_id = ? AND season = ? AND net_points_season_type = ?", [athlete_id, season, label])


def netpoints_fingerprint_statement(athlete_id: str, season: int) -> Statement:
    """One player's season fingerprint: his possessions, then offense,
    defense and total for each of :data:`_NETPOINTS_CATEGORIES` in turn. The
    fingerprint table has no season_type column at all.

    .. versionadded:: 5.0.0
       ``templates.netpoints.player_netpoints``' second statement.
    """
    selected = ", ".join(f"{c}_o_net_pts, {c}_d_net_pts, {c}_t_net_pts" for c in _NETPOINTS_CATEGORIES)
    return Statement(f"SELECT total_poss, {selected} FROM net_points_player_fingerprint WHERE athlete_id = ? AND season = ?", [athlete_id, season])


def netpoints_game_statement(athlete_id: str, season: int, season_type: int, order: str) -> Statement:
    """One player's first or most recent game's NetPoints, from
    ``net_points_player_game`` - opt-in (``data pull
    --include-net-points-daily``) and, unlike ``net_points_player``, on the
    normal numeric season_type - dated from ``games``.

    .. versionadded:: 5.0.0
       ``templates.netpoints._single_game_netpoints``' statement.
    """
    return Statement(
        "SELECT g.event_id, g.date, g.o_net_pts, g.d_net_pts, g.t_net_pts, g.o_poss, g.d_poss, g.t_wpa "
        "FROM (SELECT npg.*, gm.date FROM net_points_player_game npg JOIN games gm ON gm.event_id = npg.event_id "
        "      WHERE npg.athlete_id = ? AND npg.season = ? AND npg.season_type = ?) g "
        f"ORDER BY g.date {'ASC' if order == 'first' else 'DESC'} LIMIT 1",
        [athlete_id, season, season_type],
    )


def _first(rows: list[tuple[Any, ...]]) -> tuple[Any, ...] | None:
    """A statement's first row, as ``fetchone`` gave the retired reads."""
    return rows[0] if rows else None


def read_player_netpoints(con: duckdb.DuckDBPyConnection, q: NetPointsQuery, *, stated: frozenset[str]) -> Result | TemplateResult | None:
    """One player's NetPoints (``player_netpoints``' point): a season's line
    and the play-type split behind it, in the units the scope asks for (per
    100 possessions, or season totals where no possession count is on
    record), or one
    game's NetPoints where ``order`` names his first or most recent. A
    :class:`~association.query.templates.common.TemplateResult` back is the
    relation's refusal (an ambiguous name); ``None`` where the scope sets a
    narrowing the retired template's words do not state.

    NetPoints existed only as leaderboard metrics - ways to rank the league -
    so a question about one player had nowhere to land until the retired
    template, and a guard that turns a wrong answer into a slow one needs
    something to fall through TO.

    Raises ``TemplateUnsupported`` with no player named, and where the
    per-game table a game's NetPoints come from is missing.

    .. versionadded:: 5.0.0
       ``templates.netpoints.player_netpoints``, moved whole.
    """
    scope = q.scope
    if q.shape != "scalar" or unhonored_scoping("player_netpoints", scope, stated):
        return None
    # Settled before the name is resolved: the season is what narrows an
    # ambiguous name to the players with NetPoints in it.
    defaulted = not scope.season
    season = scope.season or current_season()
    season_type = scope.season_type or 2
    # A named order ("recent", "first") is one game.
    order = scope.order
    player = resolved_player(con, scope.player, "player_netpoints needs a player name", available=_NETPOINTS_GAMES if order else _NETPOINTS_TABLES, season=season)
    if isinstance(player, TemplateResult):
        return player
    # "NetPoints from his LAST regular season game" was answered with the
    # whole season - 43 games - because nothing scoped it. Per-game NetPoints
    # live in their own table, with no fingerprint breakdown, so this is a
    # different answer rather than a filtered one.
    if order:
        return _netpoints_game(con, player, season, season_type, order)
    return _netpoints_season(con, player, season, season_type, defaulted=defaulted)


def _netpoints_season(con: duckdb.DuckDBPyConnection, player: Entity, season: int, season_type: int, *, defaulted: bool) -> Result:
    """A season's NetPoints: the line (a :class:`Scalar`, absent with no row
    in ``net_points_player``) and the categories behind it (a
    :class:`Grouped` by ``category``, absent with no fingerprint row). With
    neither, an empty Result, and - where the season was defaulted, not
    asked for - the seasons the player IS on record for, as a decision."""
    totals_row = _first(values_of(con, netpoints_totals_statement(player.id, season, season_type)))
    fingerprint = _first(values_of(con, netpoints_fingerprint_statement(player.id, season)))
    # Per 100 possessions: the fingerprint is for comparing players, and
    # season totals mostly rank by playing time. Totals stay in each row, and
    # are what a season with no possession count is said in.
    possessions = fingerprint[0] if fingerprint else None
    per_100 = bool(possessions)
    scale = 100.0 / possessions if per_100 and possessions else 1.0
    breakdown = _netpoints_breakdown(fingerprint, scale) if fingerprint is not None else []
    span = Span(season=season, season_type=season_type, phrase=season_phrase(season, season_type), source="netpoints")
    facts = {"season": season, "per_100": per_100, "possessions": possessions}
    if totals_row is None and not breakdown:
        return Result(subject=player.name, relation="player", span=span, decisions=_netpoints_redirect(con, player, season_type) if defaulted else (), facts=facts)
    parts: list[Part] = []
    notes: list[Note] = []
    if totals_row is not None:
        parts.append(Part(body=Scalar(games=int(totals_row[5]) if totals_row[5] else 0, values=dict(zip(_NETPOINTS_TOTALS, totals_row, strict=True)))))
    else:
        notes.append(Note("part_missing", {"what": "season_totals"}))
    if breakdown:
        parts.append(Part(role="detail" if parts else "answer", body=Grouped(by="category", rows=tuple(breakdown))))
        units = "per 100 possessions" if per_100 else "season totals"
        notes.append(Note("definition", {"term": "netpoints_units", "units": units, "possessions": possessions if per_100 else None}))
        if any(not row["partition"] for row in breakdown):
            notes.append(Note("definition", {"term": "netpoints_overlap"}))
    else:
        notes.append(Note("part_missing", {"what": "fingerprint"}))
    return Result(subject=player.name, relation="player", span=span, parts=tuple(parts), notes=tuple(notes), facts=facts)


def _netpoints_redirect(con: duckdb.DuckDBPyConnection, player: Entity, season_type: int) -> tuple[Decided, ...]:
    """The seasons a player with no NetPoints in a defaulted season IS on
    record for (issue #18): a redirect, not the flat refusal read alone -
    which, for a retired player, sounds like the warehouse holds nothing of
    his at all. A season the question named keeps the refusal plain."""
    label = SEASON_TYPE_LABELS.get(season_type, "Regular Season")
    on_record = season_redirect(con, player.id, label, "net_points_player", season_type_column="net_points_season_type")
    if on_record is None:
        return ()
    first, last = on_record
    kind = SEASON_TYPE_NAMES.get(season_type, "regular season")
    return (Decided(kind="season_redirected", field="season", chose=None, why="the season read by default holds nothing for him", facts={"first": first, "last": last, "what": kind}),)


def _netpoints_breakdown(fingerprint: tuple[Any, ...], scale: float) -> list[dict[str, Any]]:
    """The fingerprint's categories, scaled, largest total first: one row per
    category with a value on record, keyed by its name (``key``).
    ``partition`` marks the six categories that sum to the season total
    (:data:`~association.nba.netpoints.FINGERPRINT_PARTITION`) - the flag the
    printed table and the page's renderer both split sections on, so the two
    read one fact rather than each recomputing the category set."""
    partition_names = {c.replace("_", " ") for c in FINGERPRINT_PARTITION}
    breakdown: list[dict[str, Any]] = []
    for index, category in enumerate(_NETPOINTS_CATEGORIES):
        o, d, t = fingerprint[1 + index * 3 : 1 + index * 3 + 3]
        if o is None and d is None and t is None:
            continue
        name = category.replace("_", " ")
        breakdown.append(
            {
                "key": name,
                "partition": name in partition_names,
                "offense": None if o is None else o * scale,
                "defense": None if d is None else d * scale,
                "total": None if t is None else t * scale,
                "offense_season_total": o,
                "defense_season_total": d,
                "total_season_total": t,
            }
        )
    breakdown.sort(key=lambda row: -abs(row["total"] or 0))
    return breakdown


def _netpoints_game(con: duckdb.DuckDBPyConnection, player: Entity, season: int, season_type: int, order: str) -> Result:
    """One game's NetPoints, from ``net_points_player_game``: a
    :class:`Scalar` of one game (its offense, defense and total, and the
    possessions and win probability added beside them), windowed to the
    first or most recent game - or a scalar of none where he has no row.
    It carries no play-type split of its own - that lives in the sibling
    ``net_points_player_game_fingerprint``, which the ``fingerprint`` intent
    draws, and the answer says so."""
    try:
        row = _first(values_of(con, netpoints_game_statement(player.id, season, season_type, order)))
    except duckdb.Error as exc:
        # The table only exists if the daily NetPoints fetch was run. Saying
        # so beats a refusal that names only the intent.
        raise TemplateUnsupported(f"per-game NetPoints unavailable: {exc}") from exc
    span = Span(season=season, season_type=season_type, phrase=season_phrase(season, season_type), source="netpoints")
    window = Window(limit=1, ascending=order == "first")
    if row is None:
        return Result(subject=player.name, relation="player", span=span, window=window, parts=(Part(body=Scalar(games=0)),), facts={"season": season})
    event_id, date, o, d, t, o_poss, d_poss, wpa = row
    scalar = Scalar(games=1, values={"offense": o, "defense": d, "total": t}, sums={"o_poss": o_poss, "d_poss": d_poss, "wpa": wpa})
    return Result(
        subject=player.name,
        relation="player",
        span=Span(season=season, season_type=season_type, date=eastern_date(date), phrase=span.phrase, source="netpoints"),
        window=window,
        parts=(Part(body=scalar),),
        notes=(Note("hint", {"what": "fingerprint_of_that_game"}),),
        facts={"season": season, "event_id": event_id},
    )
