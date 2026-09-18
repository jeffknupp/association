"""NetPoints ratings and play-type fingerprints.

.. versionadded:: 3.0.0
   Split out of the former ``association.query.templates`` module.
"""

from __future__ import annotations

from typing import Any

import duckdb

from association.nba.netpoints import FINGERPRINT_CATEGORIES
from association.nba.season import current_season
from association.nba.season import eastern_date as _eastern_date

from ..entities import Ambiguous, Availability, Entity, no_match
from ..fingerprint import FINGERPRINT_AVAILABILITY, FINGERPRINT_VIEWS, GAME_FINGERPRINT_AVAILABILITY, FingerprintUnavailable, render_for_players
from ..metrics import SEASON_TYPE_LABELS
from ..shotchart import resolve_chart_player
from .common import SEASON_TYPE_NAMES, TemplateContext, TemplateResult, TemplateUnsupported, _clarify, _defaulted_season_note, _period, _resolved_player, _table_cell

# Beyond three polygons on one radar the shapes stop being separable - and the
# palette in radar.py holds three series colors for the same reason.
MAX_FINGERPRINT_PLAYERS = 3


# player_netpoints reads the season totals AND the fingerprint, and the two
# disagree about who they hold: measured, 63 player-seasons are in the first
# only and 8 in the second only. A row in either is an answer.
_NET_POINTS = (Availability("net_points_player"), FINGERPRINT_AVAILABILITY)


_NET_POINTS_GAMES = Availability("net_points_player_game")


# espnanalytics.com's "Net Pts Fingerprint": the play-type breakdown behind a
# player's NetPoints. `total` is the summary rather than a play type, so it is
# reported on the headline line instead of as a category row.
FINGERPRINT_SUMMARY_CATEGORY = "total"


# These six partition a player's NetPoints exactly: they sum to the offensive
# and defensive totals for every player checked, to within float rounding
# (max deviation 0.005 across the league's top minute-earners). Verified
# against net_points_player.offense / .defense, which are stored separately.
FINGERPRINT_PARTITION = ("two_pt", "three_pt", "free_throw", "turnover", "rebound", "foul")


def player_netpoints(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """One player's NetPoints, with the play-type fingerprint.

    NetPoints existed only as leaderboard metrics - ways to rank the league - so
    a question about one player had nowhere to land: the guard in player_stat
    fired correctly, then the agent ranked the league and dropped the player. A
    guard that turns a wrong answer into a slow one needs something to fall
    through TO.

    .. versionchanged:: 4.1.0
       A defaulted (unnamed) season with no NetPoints for the player now
       redirects to the seasons he does have on record, when there are any,
       rather than a flat refusal that reads as though the warehouse held
       nothing of his at all. A season the question named outright is
       unaffected.
    """
    con = ctx.con
    # Settled before the name is resolved: the season is what narrows an
    # ambiguous name to the players with NetPoints in it.
    raw_season = slots.get("season")
    defaulted = not (isinstance(raw_season, int) and raw_season)
    season = raw_season or current_season()
    season_type = slots.get("season_type") or 2
    one_game = slots.get("order") in ("recent", "first")
    player = _resolved_player(con, slots.get("player"), "player_netpoints needs a player name", available=_NET_POINTS_GAMES if one_game else _NET_POINTS, season=season)
    if isinstance(player, TemplateResult):
        return player

    # "NetPoints from his LAST regular season game" was answered with the whole
    # season - 43 games - because nothing scoped it. Per-game NetPoints live in
    # their own table, with no fingerprint breakdown, so this is a different
    # answer rather than a filtered one.
    if one_game:
        return _single_game_netpoints(ctx, player, season, season_type, slots["order"])

    # net_points_player uses its OWN string season_type; filtering it with the
    # numeric one every other table uses silently matches nothing.
    label = SEASON_TYPE_LABELS.get(season_type, "Regular Season")
    headline = con.execute(
        "SELECT overall, offense, defense, overall_per_100_poss, total_minutes, games FROM net_points_player WHERE athlete_id = ? AND season = ? AND net_points_season_type = ?",
        [player.id, season, label],
    ).fetchone()

    categories = [c for c in FINGERPRINT_CATEGORIES.values() if c != FINGERPRINT_SUMMARY_CATEGORY]
    selected = ", ".join(f"{c}_o_net_pts, {c}_d_net_pts, {c}_t_net_pts" for c in categories)
    # The fingerprint table has no season_type column at all.
    fingerprint = con.execute(
        f"SELECT total_poss, {selected} FROM net_points_player_fingerprint WHERE athlete_id = ? AND season = ?",
        [player.id, season],
    ).fetchone()

    # Per 100 possessions by default: the fingerprint is for comparing players,
    # and season totals mostly rank by playing time. Totals stay in `data`, and
    # the `rate` slot asks for them.
    possessions = fingerprint[0] if fingerprint else None
    per_100 = slots.get("rate") != "total" and bool(possessions)
    scale = 100.0 / possessions if per_100 and possessions else 1.0

    breakdown = _netpoints_breakdown(fingerprint, categories, scale) if fingerprint is not None else []

    period = _period(season, season_type)
    if headline is None and not breakdown:
        answer = f"The warehouse has no {period} NetPoints for {player.name}."
        if defaulted:
            # The season was defaulted to "now", not asked for. A player with
            # NetPoints on record in other seasons is a redirect (issue #18),
            # not the flat refusal above read alone - which, for a retired
            # player, sounds like the warehouse holds nothing of his at all.
            # No "or ask for his career" here: player_netpoints has no career
            # span to offer. A season the question named keeps this plain.
            row = con.execute(
                "SELECT MIN(season), MAX(season) FROM net_points_player WHERE athlete_id = ? AND net_points_season_type = ?",
                [player.id, label],
            ).fetchone()
            redirect = (int(row[0]), int(row[1])) if row and row[0] is not None else None
            answer += _defaulted_season_note(redirect, SEASON_TYPE_NAMES.get(season_type, "regular season"), career_hint=False)
        return TemplateResult(
            data={"player": player.name, "season": season},
            answer=answer,
        )
    return TemplateResult(
        data={"player": player.name, "season": season, "headline": headline, "fingerprint": breakdown},
        answer=_phrase_netpoints(player.name, period, headline, breakdown, per_100, possessions),
    )


def _netpoints_breakdown(fingerprint: tuple[Any, ...], categories: list[str], scale: float) -> list[dict[str, Any]]:
    """The fingerprint's categories, scaled, largest total first. ``fingerprint``
    is the row read by player_netpoints: possessions, then offense, defense and
    total for each of ``categories`` in turn."""
    breakdown: list[dict[str, Any]] = []
    for index, category in enumerate(categories):
        o, d, t = fingerprint[1 + index * 3 : 1 + index * 3 + 3]
        if o is None and d is None and t is None:
            continue
        breakdown.append(
            {
                "category": category.replace("_", " "),
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


def _single_game_netpoints(ctx: TemplateContext, player: Entity, season: int, season_type: int, order: str) -> TemplateResult:
    """One game's NetPoints, from net_points_player_game.

    That table is opt-in (`data pull --include-net-points-daily`) and, unlike
    net_points_player, uses the normal numeric season_type. It carries no
    play-type split of its own - that lives in the sibling
    net_points_player_game_fingerprint, which the `fingerprint` intent draws."""
    period = _period(season, season_type)
    try:
        row = ctx.con.execute(
            "SELECT g.event_id, g.date, g.o_net_pts, g.d_net_pts, g.t_net_pts, g.o_poss, g.d_poss, g.t_wpa "
            "FROM (SELECT npg.*, gm.date FROM net_points_player_game npg JOIN games gm ON gm.event_id = npg.event_id "
            "      WHERE npg.athlete_id = ? AND npg.season = ? AND npg.season_type = ?) g "
            f"ORDER BY g.date {'ASC' if order == 'first' else 'DESC'} LIMIT 1",
            [player.id, season, season_type],
        ).fetchone()
    except duckdb.Error as exc:
        # The table only exists if the daily NetPoints fetch was run. Saying so
        # beats falling through to an agent that has no better source.
        raise TemplateUnsupported(f"per-game NetPoints unavailable: {exc}") from exc

    if row is None:
        which = "earliest" if order == "first" else "most recent"
        return TemplateResult(
            data={"player": player.name, "season": season, "game": None},
            answer=f"No per-game NetPoints on record for {player.name}'s {which} {period} game.",
        )
    event_id, date, o, d, t, o_poss, d_poss, wpa = row
    which = "first" if order == "first" else "most recent"
    game = {"event_id": event_id, "date": _eastern_date(date), "offense": o, "defense": d, "total": t}
    detail = []
    if o_poss is not None and d_poss is not None:
        detail.append(f"{o_poss:.0f} offensive and {d_poss:.0f} defensive possessions")
    if wpa is not None:
        detail.append(f"{wpa:+.3f} win probability added")
    answer = f"{player.name}, NetPoints in his {which} {period} game ({_eastern_date(date)}): {_table_cell(t)} total ({_table_cell(o)} offense, {_table_cell(d)} defense)."
    if detail:
        answer += "\n  " + ", ".join(detail) + "."
    answer += "\n  (Ask for a fingerprint of that game to see the play-type split behind it.)"
    return TemplateResult(data={"player": player.name, "game": game}, answer=answer)


def _phrase_netpoints(
    name: str,
    period: str,
    headline: tuple[Any, ...] | None,
    breakdown: list[dict[str, Any]],
    per_100: bool,
    possessions: float | None,
) -> str:
    lines = _phrase_netpoints_headline(name, period, headline)

    if not breakdown:
        lines.append("  No play-type fingerprint on record for this season.")
        return "\n".join(lines)

    units = "per 100 possessions" if per_100 else "season totals"
    scope = f" over {possessions:,.0f} possessions" if per_100 and possessions else ""
    width = max(len(row["category"]) for row in breakdown)
    partition_names = {c.replace("_", " ") for c in FINGERPRINT_PARTITION}
    # NOT `detail`: the headline block above binds that to a list of strings,
    # and reusing it here is the same shadowing that made a rate=total request
    # print under a per-100 heading.
    partition_rows = [r for r in breakdown if r["category"] in partition_names]
    detail_rows = [r for r in breakdown if r["category"] not in partition_names]
    lines += _phrase_netpoints_partition(partition_rows, units, scope, width)
    lines += _phrase_netpoints_detail(detail_rows, units, width)
    return "\n".join(lines)


def _phrase_netpoints_headline(name: str, period: str, headline: tuple[Any, ...] | None) -> list[str]:
    """The season-total line, and its minutes and games, or the note that there are none."""
    lines = []
    if headline is not None:
        # NOT `per_100`: that is the parameter saying which UNITS the
        # fingerprint is in, and unpacking over it made a rate=total request
        # print season totals under a "per 100 possessions" heading.
        overall, offense, defense, per_100_rate, minutes, games = headline
        lines.append(f"{name}, NetPoints in the {period}: {_table_cell(overall)} overall ({_table_cell(offense)} offense, {_table_cell(defense)} defense)")
        detail = []
        if per_100_rate is not None:
            detail.append(f"{per_100_rate:.2f} per 100 possessions")
        if minutes:
            detail.append(f"{int(minutes):,} minutes")
        if games:
            detail.append(f"{int(games)} games")
        if detail:
            lines.append("  " + ", ".join(detail) + ".")
    else:
        lines.append(f"{name}, NetPoints fingerprint in the {period} (no season totals on record):")
    return lines


def _phrase_netpoints_partition(partition_rows: list[dict[str, Any]], units: str, scope: str, width: int) -> list[str]:
    """The six categories that partition the total, as an offense section and a defense section."""
    lines: list[str] = []
    # Offense and defense get a section each, sorted by their OWN side. One
    # table sorted by total renders the defensive profile invisible: for SGA,
    # `turnover` carries the largest defensive value of any category and lands
    # 15th of 21 by total, below categories whose defense is ~0.
    for side, heading in (("offense", "Offense"), ("defense", "Defense")):
        ranked = sorted((r for r in partition_rows if r[side] is not None), key=lambda r: -abs(r[side]))
        if not ranked:
            continue
        lines.append("")
        lines.append(f"  {heading}, {units}{scope}:")
        # Two decimals: per-100 values are small, and one decimal collapses
        # most of the categories onto the same number.
        lines.extend("  " + row["category"].ljust(width) + f"{row[side]:.2f}".rjust(9) for row in ranked)
        # The sum is printed so the reader can check it against the headline -
        # these six really do add up, and showing it says so without asserting.
        lines.append("  " + "-" * (width + 9))
        lines.append("  " + "total".ljust(width) + f"{sum(r[side] for r in ranked):.2f}".rjust(9))
    return lines


def _phrase_netpoints_detail(detail_rows: list[dict[str, Any]], units: str, width: int) -> list[str]:
    """The overlapping play-type slices, which are shown but do not add up."""
    if not detail_rows:
        return []
    lines = [
        "",
        f"  Play-type detail, {units} (overlapping slices - a driving layup at the rim",
        "  counts in driving, layup and rim, so these do not add up):",
        "  " + "category".ljust(width) + "".join(h.rjust(9) for h in ("O", "D")),
    ]
    for row in sorted(detail_rows, key=lambda r: -abs(r["total"] or 0)):
        cells = "".join(("-" if row[k] is None else f"{row[k]:.2f}").rjust(9) for k in ("offense", "defense"))
        lines.append("  " + row["category"].ljust(width) + cells)
    return lines


def fingerprint(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """Renders one or more players' NetPoints fingerprints to a static HTML
    radar plot.

    Uses fingerprint.render_for_players, and resolves names the same best-match
    way shot_chart does: a plot titled with the resolved name shows a wrong
    match on sight, which is what makes best-match safe here and not in a
    template reporting numbers.
    """
    # "compare their fingerprints" arrives as `players`, one name as `player`.
    # Both draw one plot; two polygons on shared axes IS the comparison, so
    # this does not need a second intent.
    names = _fingerprint_names(slots.get("players"), slots.get("player"))

    # A question about one game draws that game, from the long per-game table
    # rather than the season file - see fingerprint.load_game_fingerprints for
    # why its numbers are the game's own net points and not a per-100 rate. A
    # `date` is not honored the same way: the router gives a calendar date and
    # the loader picks a player's first or last game, which are different
    # questions, so a dated request still says it cannot answer.
    order = slots.get("order") if slots.get("order") in ("recent", "first") else None
    if slots.get("date") and not order:
        message = "A fingerprint can be drawn for a player's first or most recent game of a season, but not yet for a particular date - ask for their last game instead."
        return TemplateResult(data={"message": message}, answer=message)

    # Settled before any name is resolved: the season is what narrows an
    # ambiguous name to the players who have a fingerprint in it.
    season = slots.get("season") or current_season()
    requested_type = slots.get("season_type")
    season_type = requested_type if isinstance(requested_type, int) else 2
    # A one-game plot is narrowed against the table it will actually be drawn
    # from. Availability in the season file does not imply a row per game, and
    # the season file has no season_type at all.
    availability = GAME_FINGERPRINT_AVAILABILITY if order else FINGERPRINT_AVAILABILITY

    resolved = _fingerprint_resolve_players(ctx.con, names, availability, season)
    if isinstance(resolved, TemplateResult):
        return resolved
    players, ambiguous = resolved

    # The router's word for it is `side`, which is what a question says ("his
    # defensive fingerprint"); the renderer's is `view`, because each skill
    # already carries the side it is measured on and this only picks which
    # skills are drawn.
    view = slots.get("side")
    if view not in FINGERPRINT_VIEWS:
        view = "total"
    return _fingerprint_render(ctx, players, ambiguous, season, view=view, season_type=season_type, order=order)


def _fingerprint_names(players_slot: Any, player_slot: Any) -> list[str]:
    """The player name(s) asked for, from the `players` slot (a comparison) or
    the `player` slot (one name)."""
    names = players_slot if isinstance(players_slot, list) else None
    names = [n for n in names if isinstance(n, str) and n.strip()] if names else []
    if not names:
        if not isinstance(player_slot, str) or not player_slot.strip():
            raise TemplateUnsupported("fingerprint needs a player name")
        names = [player_slot]
    return names[:MAX_FINGERPRINT_PLAYERS]


def _fingerprint_resolve_players(con: duckdb.DuckDBPyConnection, names: list[str], availability: Availability, season: int) -> tuple[list[Entity], list[str]] | TemplateResult:
    """Each name resolved against the table the plot will actually be drawn
    from, the same best-match way `shot_chart` does: a plot titled with the
    resolved name shows a wrong match on sight, which is what makes
    best-match resolution safe here and not in a template reporting numbers."""
    players: list[Entity] = []
    ambiguous: list[str] = []
    for name in names:
        found = resolve_chart_player(con, name, availability, season)
        if found is None:
            message = no_match(con, name)
            return TemplateResult(data={"message": message}, answer=message)
        if isinstance(found, Ambiguous):
            return _clarify(name, found.candidates, active=found.active)
        player, also = found
        # The same name twice would draw one polygon over itself and report a
        # comparison; deduped on the RESOLVED id, since "SGA" and "Gilgeous"
        # are two names for one player.
        if player.id not in {p.id for p in players}:
            players.append(player)
        ambiguous.extend(also)
    return players, ambiguous


def _fingerprint_render(ctx: TemplateContext, players: list[Entity], ambiguous: list[str], season: int, *, view: str, season_type: int, order: str | None) -> TemplateResult:
    """Renders the plot and builds the answer, or returns the renderer's own
    message when the table it reads has nothing to draw."""
    try:
        rendered = render_for_players(ctx.con, ctx.out_dir, players, ambiguous, season, view=view, season_type=season_type, order=order)
    except FingerprintUnavailable as exc:
        # Returned, not raised: the agent has no better source for this plot
        # than the table this just read, so falling through would only be slow.
        return TemplateResult(data={"message": str(exc)}, answer=str(exc))
    artifact = rendered.artifact
    return TemplateResult(
        data={
            "players": [p.name for p in players],
            "season": season,
            "side": view,
            "scope": "game" if order else "season",
            "path": str(artifact.path) if artifact else None,
            "message": rendered.message,
        },
        answer=rendered.message,
        artifacts=[artifact] if artifact else [],
    )
