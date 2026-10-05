"""NetPoints play-type fingerprints.

.. versionadded:: 3.0.0
   Split out of the former ``association.query.templates`` module.

.. versionchanged:: 5.0.0
   ``player_netpoints`` is gone: a player's NetPoints are read by
   :mod:`association.query.compose.netpoints` and worded by
   :mod:`association.query.compose.say` (Phase 2, step 5).
"""

from __future__ import annotations

import duckdb

from association.nba.season import current_season
from association.query.reading import Reading

from ..entities import Ambiguous, Availability, Entity, no_match
from ..fingerprint import FINGERPRINT_AVAILABILITY, GAME_FINGERPRINT_AVAILABILITY, FingerprintUnavailable, render_for_players
from ..shotchart import resolve_chart_player
from .common import TemplateContext, TemplateResult, TemplateUnsupported, _clarify

# Beyond three polygons on one radar the shapes stop being separable - and the
# palette in radar.py holds three series colors for the same reason.
MAX_FINGERPRINT_PLAYERS = 3


def fingerprint(ctx: TemplateContext, reading: Reading) -> TemplateResult:
    """Renders one or more players' NetPoints fingerprints to a static HTML
    radar plot.

    Uses fingerprint.render_for_players, and resolves names the same best-match
    way shot_chart does: a plot titled with the resolved name shows a wrong
    match on sight, which is what makes best-match safe here and not in a
    template reporting numbers.
    """
    scope = reading.scope
    # "compare their fingerprints" arrives as `players`, one name as `player`.
    # Both draw one plot; two polygons on shared axes IS the comparison, so
    # this does not need a second intent.
    names = _fingerprint_names(scope.players, scope.player)

    # A question about one game draws that game, from the long per-game table
    # rather than the season file - see fingerprint.load_game_fingerprints for
    # why its numbers are the game's own net points and not a per-100 rate. A
    # `date` is not honored the same way: the router gives a calendar date and
    # the loader picks a player's first or last game, which are different
    # questions, so a dated request still says it cannot answer.
    order = scope.order
    if scope.date and not order:
        message = "A fingerprint can be drawn for a player's first or most recent game of a season, but not yet for a particular date - ask for their last game instead."
        return TemplateResult(data={"message": message}, answer=message)

    # Settled before any name is resolved: the season is what narrows an
    # ambiguous name to the players who have a fingerprint in it.
    season = scope.season or current_season()
    season_type = scope.season_type or 2
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
    # skills are drawn. A side is one of the renderer's views - offense,
    # defense or total - by the time it is read here: the Reading's door
    # refuses any other, so only an absent one needs the default.
    view = scope.side or "total"
    return _fingerprint_render(ctx, players, ambiguous, season, view=view, season_type=season_type, order=order)


def _fingerprint_names(players_slot: tuple[str, ...], player_slot: str | None) -> list[str]:
    """The player name(s) asked for, from the `players` slot (a comparison) or
    the `player` slot (one name)."""
    names = [n for n in players_slot if n.strip()]
    if not names:
        if player_slot is None or not player_slot.strip():
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
        # Returned, not raised: nothing has a better source for this plot than
        # the table this just read, and a raise would refuse for the wrong cause.
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
