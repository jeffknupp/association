"""The NetPoints relation's readers: a player's NetPoints (``player_netpoints``)
and his play-type fingerprint (``fingerprint``), read from ESPN Analytics'
NetPoints tables into a :class:`~association.query.result.Result` -
Phase 2's slice (v) (``ROADMAP.md``, step 5, and the decision "Charts are
declared shapes": each keeps its own reader and renderer, declared with its
relation, and is not ported onto a NetPoints relation before Phase 3).

``fingerprint`` is a chart (:class:`~association.query.result.Chart`):
one or more players' play-type fingerprints - a season's, from
``net_points_player_fingerprint``, or a first or last game's, from
``net_points_player_game_fingerprint`` - read by the chart's own reader,
:mod:`association.query.fingerprint` (``load_for_players``: the percentile
pool and the polygons come out of one read), and drawn by its own renderer
(:mod:`association.query.radar`) in :func:`draw_fingerprint`, the step
between this reader and the sayer that writes the page.

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

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

import duckdb

from association.nba.netpoints import FINGERPRINT_CATEGORIES, FINGERPRINT_PARTITION
from association.nba.season import current_season, eastern_date
from association.query.entities import Ambiguous, Availability, Entity, resolved_player, unmatched
from association.query.fingerprint import (
    FINGERPRINT_AVAILABILITY,
    FINGERPRINT_MIN_GAME_POSSESSIONS,
    FINGERPRINT_MIN_MINUTES,
    FINGERPRINT_SUMMARY_CATEGORY,
    GAME_FINGERPRINT_AVAILABILITY,
    PER_100_POSSESSIONS,
    PER_GAME,
    FingerprintUnavailable,
    LeagueScale,
    fingerprint_captions,
    fingerprint_file,
    fingerprint_page,
    load_for_players,
)
from association.query.metrics import SEASON_TYPE_LABELS
from association.query.notes import Note
from association.query.reading import Scope, Unsupported
from association.query.result import Chart, ChartFacts, Clarify, Decided, Grouped, NetPointsFacts, Part, Refusal, Result, Scalar, Span, Unanswered, Window
from association.query.season_line import Statement, season_redirect
from association.query.season_text import SEASON_TYPE_NAMES, season_phrase
from association.query.shotchart import resolve_chart_player

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


def read_player_netpoints(con: duckdb.DuckDBPyConnection, q: NetPointsQuery) -> Result | Unanswered | None:
    """One player's NetPoints (``player_netpoints``' point): a season's line
    and the play-type split behind it, in the units the scope asks for (per
    100 possessions, or season totals where no possession count is on
    record), or one
    game's NetPoints where ``order`` names his first or most recent. A
    :class:`~association.query.result.Refusal` or :class:`~association.query.result.Clarify` back is the
    relation's refusal (an ambiguous name); ``None`` where the point is not
    a scalar.

    NetPoints existed only as leaderboard metrics - ways to rank the league -
    so a question about one player had nowhere to land until the retired
    template, and a guard that turns a wrong answer into a slow one needs
    something to fall through TO.

    Raises ``Unsupported`` with no player named, and where the
    per-game table a game's NetPoints come from is missing.

    .. versionadded:: 5.0.0
       ``templates.netpoints.player_netpoints``, moved whole.

    .. versionchanged:: 6.0.0
       Takes no ``stated``: the answer side checks the point's cells against
       its shape's row before asking (``compose.plan.cells_unhonored``,
       Phase 3, step 2's closing slice).
    """
    scope = q.scope
    if q.shape != "scalar":
        return None
    # Settled before the name is resolved: the season is what narrows an
    # ambiguous name to the players with NetPoints in it.
    defaulted = not scope.span.season
    season = scope.span.season or current_season()
    season_type = scope.span.season_type or 2
    # A named order ("recent", "first") is one game.
    order = scope.window.order
    player = resolved_player(con, scope.subject.player, "player_netpoints needs a player name", available=_NETPOINTS_GAMES if order else _NETPOINTS_TABLES, season=season)
    if isinstance(player, Unanswered):
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
    facts = NetPointsFacts(per_100=per_100, possessions=possessions)
    if totals_row is None and not breakdown:
        # The season's line with nothing in it: the shape, said by what is missing.
        empty = (Part(body=Scalar(games=0, how="per_100")),)
        return Result(subject=player.name, relation="player", span=span, parts=empty, decisions=_netpoints_redirect(con, player, season_type) if defaulted else (), facts=facts)
    parts: list[Part] = []
    notes: list[Note] = []
    if totals_row is not None:
        parts.append(Part(body=Scalar(games=int(totals_row[5]) if totals_row[5] else 0, values=dict(zip(_NETPOINTS_TOTALS, totals_row, strict=True)), how="per_100")))
    else:
        notes.append(Note("part_missing", {"what": "season_totals"}))
    if breakdown and season_type == 3:
        # The fingerprint table has no season type: its split is the whole
        # season's, and a postseason answer says so rather than labeling it
        # the playoffs' (ISSUES.md #299).
        notes.append(Note("definition", {"term": "fingerprint_per_season"}))
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
        raise Unsupported(f"per-game NetPoints unavailable: {exc}") from exc
    span = Span(season=season, season_type=season_type, phrase=season_phrase(season, season_type), source="netpoints")
    window = Window(limit=1, ascending=order == "first")
    if row is None:
        return Result(subject=player.name, relation="player", span=span, window=window, parts=(Part(body=Scalar(games=0, how="total")),), facts=NetPointsFacts())
    event_id, date, o, d, t, o_poss, d_poss, wpa = row
    scalar = Scalar(games=1, values={"offense": o, "defense": d, "total": t}, sums={"o_poss": o_poss, "d_poss": d_poss, "wpa": wpa}, how="total")
    return Result(
        subject=player.name,
        relation="player",
        span=Span(season=season, season_type=season_type, date=eastern_date(date), phrase=span.phrase, source="netpoints"),
        window=window,
        parts=(Part(body=scalar),),
        notes=(Note("hint", {"what": "fingerprint_of_that_game"}),),
        facts=NetPointsFacts(event_id=event_id),
    )


# --- a player's fingerprint ------------------------------------------------------

MAX_FINGERPRINT_PLAYERS = 3
"""The most polygons one radar draws: beyond three the shapes stop being
separable - and the palette in :mod:`association.query.radar` holds three
series colors for the same reason.

.. versionadded:: 5.0.0
   Moved from ``association.query.templates.netpoints``.
"""


def read_fingerprint(con: duckdb.DuckDBPyConnection, q: NetPointsQuery) -> Result | Unanswered | None:
    """One or more players' fingerprints (``fingerprint``'s point), read for
    one radar: a :class:`~association.query.result.Chart` whose marks are
    the polygons. "compare their fingerprints" arrives as ``players``, one
    name as ``player``: both draw one plot, since two polygons on shared
    axes IS the comparison. A
    :class:`~association.query.result.Refusal` or :class:`~association.query.result.Clarify` back is the
    relation's own refusal - a name nobody matches, an ambiguous one, a
    date, or nothing on record to draw; ``None`` where the point is not a
    chart.

    Names are resolved best-match, as a shot chart's are
    (:func:`~association.query.shotchart.resolve_chart_player`): a plot
    titled with the resolved name shows a wrong match on sight, which is
    what makes best-match safe here and not in an answer reporting numbers.

    Raises ``Unsupported`` with no player named.

    .. versionadded:: 5.0.0
       ``templates.netpoints.fingerprint``, moved whole; its drawing is
       :func:`draw_fingerprint` and its words the sayer's.

    .. versionchanged:: 6.0.0
       Takes no ``stated``: the answer side checks the point's cells against
       its shape's row before asking (``compose.plan.cells_unhonored``,
       Phase 3, step 2's closing slice).
    """
    scope = q.scope
    if q.shape != "chart":
        return None
    names = _fingerprint_names(scope.subject.players)
    # A question about one game draws that game, from the long per-game table
    # rather than the season file - see fingerprint.load_game_fingerprints for
    # why its numbers are the game's own net points and not a per-100 rate. A
    # `date` is not honored the same way: the router gives a calendar date and
    # the loader picks a player's first or last game, which are different
    # questions, so a dated request still says it cannot answer.
    order = scope.window.order
    if scope.cuts.date and not order:
        return Refusal(kind="fingerprint_on_a_date")
    # Settled before any name is resolved: the season is what narrows an
    # ambiguous name to the players who have a fingerprint in it.
    season = scope.span.season or current_season()
    season_type = scope.span.season_type or 2
    # A one-game plot is narrowed against the table it will actually be drawn
    # from. Availability in the season file does not imply a row per game, and
    # the season file has no season_type at all.
    availability = GAME_FINGERPRINT_AVAILABILITY if order else FINGERPRINT_AVAILABILITY
    resolved = _fingerprint_resolve_players(con, names, availability, season)
    if isinstance(resolved, Unanswered):
        return resolved
    players, ambiguous = resolved
    # The reading's word for it is `side`, which is what a question says
    # ("his defensive fingerprint"); the renderer's is `view`, because each
    # skill already carries the side it is measured on and this only picks
    # which skills are drawn. The Reading's door refuses any side but the
    # renderer's three, so only an absent one needs the default.
    side = scope.measure.side if scope.measure is not None else None
    return fingerprint_result(con, players, ambiguous, season, view=side or "total", season_type=season_type, order=order)


def _fingerprint_names(players: tuple[str, ...]) -> list[str]:
    """The player name(s) asked for - two or more a comparison, one a
    polygon of his own - no more than one radar draws."""
    if not players:
        raise Unsupported("fingerprint needs a player name")
    return list(players[:MAX_FINGERPRINT_PLAYERS])


def _fingerprint_resolve_players(con: duckdb.DuckDBPyConnection, names: list[str], availability: Availability, season: int) -> tuple[list[Entity], list[str]] | Unanswered:
    """Each name resolved against the table the plot will actually be drawn
    from, best-match: the players, and the other names that also matched -
    or the refusal (nobody matches) or the question back (two or more with
    a fingerprint)."""
    players: list[Entity] = []
    ambiguous: list[str] = []
    for name in names:
        found = resolve_chart_player(con, name, availability, season)
        if found is None:
            return unmatched(con, name)
        if isinstance(found, Ambiguous):
            # The question back, in the sentence every chart and template asks it with.
            return Clarify(asked=name, candidates=tuple(found.candidates), active=found.active)
        player, also = found
        # The same name twice would draw one polygon over itself and report a
        # comparison; deduped on the RESOLVED id, since "SGA" and "Gilgeous"
        # are two names for one player.
        if player.id not in {p.id for p in players}:
            players.append(player)
        ambiguous.extend(also)
    return players, ambiguous


def fingerprint_result(
    con: duckdb.DuckDBPyConnection,
    players: list[Entity],
    ambiguous: list[str],
    season: int,
    *,
    view: str = "total",
    scale: str = "percentile",
    season_type: int = 2,
    order: str | None = None,
    min_minutes: int = FINGERPRINT_MIN_MINUTES,
) -> Result | Unanswered:
    """Already-resolved players' fingerprints as a chart for one radar, or
    the loader's own sentence where nothing can be drawn (a season with no
    rows, players with none in it, a game nobody qualified in) - returned,
    not raised: nothing has a better source for this plot than the table
    just read, and a raise would refuse for the wrong cause.

    The marks are one ``(fingerprint, game)`` per player drawn - his
    :class:`~association.query.fingerprint.PlayerFingerprint` and the game
    it came from (``None`` for a season). A player with no row is named,
    never drawn as a zero polygon, which would read as "played and
    contributed nothing" (a ``no_data_for`` note); one under the pool's
    floor is drawn and said to be (``below_pool``); the other names that
    matched are the ``also_matched`` decision. Its
    :class:`~association.query.result.ChartFacts` carry the view, the
    scale, the span the plot covers (``when``), the axis note and the
    league scale the draw step needs.

    .. versionadded:: 5.0.0
       ``fingerprint.render_for_players``' read and the template's around it.
    """
    try:
        fingerprints, league, games, _unit = load_for_players(con, players, ambiguous, season, view=view, scale=scale, min_minutes=min_minutes, season_type=season_type, order=order)
    except FingerprintUnavailable as exc:
        return Refusal(kind=exc.kind, facts=exc.facts)
    drawn = {f.athlete_id for f in fingerprints}
    missing = [p.name for p in players if p.id not in drawn]
    title, subtitle, axis_note, when = fingerprint_captions(fingerprints, games, season, view=view, scale=scale, order=order, league=league, min_minutes=min_minutes)
    notes: list[Note] = []
    unqualified = [f.name for f in fingerprints if not f.qualified]
    if unqualified:
        threshold, of = (FINGERPRINT_MIN_GAME_POSSESSIONS, "possessions") if order else (min_minutes, "minutes")
        notes.append(Note("below_pool", {"names": unqualified, "threshold": threshold, "of": of}))
    if missing:
        notes.append(Note("no_data_for", {"names": missing, "what": "fingerprint"}))
    decisions = (Decided(kind="also_matched", field="player", chose=[f.name for f in fingerprints], instead_of=tuple(ambiguous)),) if ambiguous else ()
    chart = Chart(
        kind="fingerprint",
        marks=tuple((f, games.get(f.athlete_id)) for f in fingerprints),
        title=title,
        caption=subtitle,
        file=fingerprint_file(fingerprints, season, order, view, scale),
    )
    return Result(
        subject=title,
        relation="player",
        span=Span(season=season, season_type=season_type, source="netpoints"),
        parts=(Part(body=chart),),
        notes=tuple(notes),
        decisions=decisions,
        facts=ChartFacts(
            players=tuple(p.name for p in players),
            view=view,
            scale=scale,
            order=order,
            when=when,
            axis_note=axis_note,
            league={"best": league.best, "worst": league.worst, "pool_size": league.pool_size},
        ),
    )


def draw_fingerprint(result: Result, out_dir: Path) -> Result:
    """``result``'s chart drawn: the radar page written to ``out_dir`` under
    the chart's file name (:func:`~association.query.fingerprint.fingerprint_page`,
    over :mod:`association.query.radar`), and the chart's ``path`` set to
    where it was written. The step between the reader and the sayer: a
    reader reads and names no file that does not exist yet, and the sayer
    takes the Result and nothing else, so neither writes the page.

    .. versionadded:: 5.0.0
    """
    chart = result.chart
    assert chart is not None
    facts = result.facts
    assert isinstance(facts, ChartFacts) and facts.league is not None and facts.scale is not None
    fingerprints = [mark[0] for mark in chart.marks]
    unit = PER_GAME if facts.order else PER_100_POSSESSIONS
    html = fingerprint_page(chart.title, chart.caption, facts.axis_note or "", fingerprints, LeagueScale(**facts.league), scale=facts.scale, unit=unit)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / chart.file
    out_path.write_text(html)
    return replace(result, parts=(Part(body=replace(chart, path=str(out_path))), *result.parts[1:]))
