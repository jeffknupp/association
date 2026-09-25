"""One sentence per skeleton, carrying what the templates' sentences carry -
the subject, the span, every narrowing the relation applied
(:meth:`~association.query.player_games.Narrowed.filters`), the measure names,
and the count of games the numbers rest on. Not the templates' exact wording;
the shape a reader needs to see what was answered and correct it with a
follow-up (`AGENTS.md`, "a default that chooses reasonably ...").

.. versionadded:: 4.4.0
"""

from __future__ import annotations

from typing import Any

from association.query.player_games import BOTH_SEASON_TYPES

from .core import Query
from .team import TeamQuery, TeamResult

#: A measure name, as an answer prints it.
LABELS: dict[str, str] = {
    "points": "points",
    "rebounds": "rebounds",
    "assists": "assists",
    "minutes": "minutes",
    "steals": "steals",
    "blocks": "blocks",
    "turnovers": "turnovers",
    "fouls": "fouls",
    "fieldGoalsMade": "FGM",
    "fieldGoalsAttempted": "FGA",
    "threePointFieldGoalsMade": "3PM",
    "threePointFieldGoalsAttempted": "3PA",
    "freeThrowsMade": "FTM",
    "freeThrowsAttempted": "FTA",
    "plusMinus": "+/-",
    "ts_pct": "TS%",
    "efg_pct": "eFG%",
    "fg_pct": "FG%",
    "three_pct": "3P%",
    "ft_pct": "FT%",
    "usage_pct": "usage",
    "game_score": "game score",
    "pra": "PRA",
    "triple_double": "triple-doubles",
    "double_double": "double-doubles",
    "won": "wins",
    "fouled_out": "foul-outs",
}
"""A measure name, mapped to the word an answer prints for it.

.. versionadded:: 4.4.0
"""

#: A boolean predicate, as the words after "with" name it.
PREDICATE_WORDS: dict[str, str] = {"won": "won", "triple_double": "with a triple-double", "double_double": "with a double-double", "fouled_out": "fouled out"}
"""A boolean predicate's name, mapped to how an answer states it.

.. versionadded:: 4.4.0
"""


def _span_phrase(span: Any, player_seasons: tuple[int, int] | None = None) -> str:
    """The span an answer names: a season, or a career (from its first season
    on, or between the two seasons a closed range named).

    ``player_seasons`` - a named player's own first and last season on record
    (:func:`~association.query.compose.core._player_own_seasons`) - names a
    *plain* career by them instead of the relation's floor: "(1994 on)" is
    true of every player and names nothing about the one asked about, where a
    question naming its own ``since``/``until`` already says exactly what it
    covers and keeps that wording (ISSUES.md, "The compiler's career span
    says '(1994 on)' where the template named the player's own seasons").

    .. versionchanged:: 4.4.0
       A closed range says "(2020-2022)" rather than "(2020 on)" - the numbers
       already stopped at ``until`` (`_span_of` bounds the read), so the
       sentence saying otherwise was a stated scope that did not match the
       count (yardstick-v2 F036). A both-season-types span is named as such.

    .. versionchanged:: 4.5.0
       Takes ``player_seasons``.
    """
    kind = {2: "regular season", 3: "postseason", BOTH_SEASON_TYPES: "regular season and postseason"}.get(getattr(span, "season_type", 2), "regular season")
    season = getattr(span, "season", None)
    if season is None:
        first: int | None
        until: int | None
        if player_seasons is not None:
            first, until = player_seasons
        else:
            first = getattr(span, "first", None)
            until = getattr(span, "until", None)
        if first and until:
            return f"{kind} career ({first})" if first == until else f"{kind} career ({first}-{until})"
        return f"{kind} career" + (f" ({first} on)" if first else "")
    return f"{season} {kind}"


_FRACTION_COLUMNS = frozenset({"ts_pct", "efg_pct", "usage_pct"})


def _fmt(v: Any, name: str) -> str:
    """One cell of an answer's table: a fixed decimal for a float, a percent sign for a rate."""
    if v is None:
        return "-"
    if isinstance(v, float):
        if name in _FRACTION_COLUMNS:
            # The view stores these as fractions (0.57); the derived rates
            # (fg_pct, three_pct, ft_pct) are already scaled to percent.
            return f"{v * 100:.1f}%"
        return f"{v:.1f}" if not name.endswith("_pct") else f"{v:.1f}%"
    return str(v)


def _predicates(q: Query) -> str:
    """ " with a triple-double" / " won and rebounds >= 10" - the predicates on a point, as a trailing phrase."""
    parts = []
    for name, op, value in q.predicates:
        if name in PREDICATE_WORDS and value is True:
            parts.append(PREDICATE_WORDS[name])
        else:
            parts.append(f"{LABELS.get(name, name)} {op} {value}")
    return (" " + " and ".join(parts)) if parts else ""


def _rows_sentence(q: Query, out: dict[str, Any]) -> str:
    """A ``rows`` read: a log, or the top game(s) by a measure."""
    rows = out["rows"]
    who = out["player"]
    where = out["narrowing"]
    span = _span_phrase(out["span"], out.get("player_seasons"))
    if not rows:
        return f"No games for {who}{where} in the {span}{_predicates(q)}."
    head = f"{who}{where}, {span}{_predicates(q)}"
    if q.order == "measure":
        head += f" - top {len(rows)} by {LABELS.get(q.measures[0], q.measures[0])}"
    elif q.limit:
        head += f" - {'first' if q.direction == 'asc' else 'last'} {len(rows)} games"
    named = max((len(str(r.get("player") or "")) for r in rows), default=0)
    lines = [
        f"  {r['day']}  "
        + (f"{r['player']:{named}s}  " if r.get("player") else "")
        + f"{'vs' if r['home'] else '@'} {r['opponent']:4s} {'W' if r['won'] else 'L'}  "
        + "  ".join(f"{LABELS.get(c, c)} {_fmt(r.get(c), c)}" for c in q.measures)
        for r in rows
    ]
    return head + ":\n" + "\n".join(lines)


def _scalar_sentence(q: Query, out: dict[str, Any]) -> str:
    """A ``scalar`` read: an average, a total, a count, or a record."""
    rows = out["rows"]
    who = out["player"]
    where = out["narrowing"]
    span = _span_phrase(out["span"], out.get("player_seasons"))
    r = rows[0] if rows else {}
    games = r.get("games") or 0
    if q.aggregate == "count":
        return f"{who} had {games} games{_predicates(q)}{where} in the {span}."
    if q.aggregate == "record":
        return f"{who}'s team went {r.get('wins', 0)}-{r.get('losses', 0)} in the {games} games{_predicates(q)}{where} in the {span}."
    if not games:
        return f"No games for {who}{where} in the {span}."
    stats = ", ".join(f"{_fmt(r.get(m), m)} {LABELS.get(m, m)}" for m in q.measures)
    how = "per game" if q.aggregate == "per_game" else q.aggregate
    if q.aggregate == "per_game":
        return f"{who} averaged {stats} {how} over {games} games{where} in the {span}."
    return f"{who}: {stats} ({how}) over {games} games{where} in the {span}."


def _grouped_sentence(q: Query, out: dict[str, Any]) -> str:
    """A ``grouped`` read: a split by venue or starter/bench, or a ranking of players."""
    rows = out["rows"]
    who = out["player"]
    where = out["narrowing"]
    span = _span_phrase(out["span"], out.get("player_seasons"))
    if not rows:
        return f"No games for {who}{where} in the {span}."
    head = f"{who}{where}, {span}{_predicates(q)}, by {q.group}"
    if q.measures:
        head += f" ({LABELS.get(q.measures[0], q.measures[0])} {'per game' if q.aggregate == 'per_game' else q.aggregate}" + (f", minimum {q.minimum_games} games" if q.minimum_games else "") + ")"
    lines = []
    for r in rows:
        cells = [f"{r.get('games')} G"]
        if "wins" in r:
            cells.append(f"{r.get('wins')}-{r.get('losses')}")
        cells += [f"{LABELS.get(m, m)} {_fmt(r.get(m), m)}" for m in q.measures]
        lines.append(f"  {r['group']!s:24s} " + "  ".join(cells))
    if q.aggregate == "count" and q.group == "player" and len(rows) > 1 and out.get("total") is not None:
        # A count by player is usually asked for its total too ("thunder
        # all-time triple doubles": 193, then who had them) - the whole
        # count, not the listed rows' (core._grouped_total).
        listed = " listed" if out["total"] != sum(int(r.get("games") or 0) for r in rows) else ""
        lines.append(f"  {'Total':24s} {out['total']} G" + (f" ({len(rows)} players{listed} of more)" if listed else ""))
    return head + ":\n" + "\n".join(lines)


#: A team measure name, as an answer prints it - the team counterpart of
#: :data:`LABELS`, over :mod:`association.query.compose.team`'s own columns.
TEAM_LABELS: dict[str, str] = {
    "points": "points",
    "points_allowed": "points allowed",
    "differential": "point differential",
    "rebounds": "rebounds",
    "assists": "assists",
    "steals": "steals",
    "blocks": "blocks",
    "turnovers": "turnovers",
    "threePointFieldGoalsMade": "3-pointers made",
    "fieldGoalsMade": "field goals made",
    "freeThrowsMade": "free throws made",
    "fouls": "fouls",
}
"""A team measure name, mapped to the phrase an answer prints for it.

.. versionadded:: 4.4.0
"""


def team_sentence(q: TeamQuery, result: TeamResult) -> str:
    """The answer for a :class:`~association.query.compose.team.TeamQuery` -
    the team subject's own sentence, carrying the same things a template's
    does: the value, the span it covers, the games it rests on, and the
    narrowing that produced it, so a wrong scope is visible and correctable
    (`AGENTS.md`, "a default that chooses reasonably ...").

    .. versionadded:: 4.4.0
    """
    label = TEAM_LABELS.get(q.measure, q.measure)
    span = _span_phrase(result.span)
    if result.value is None:
        return f"The warehouse has no {label} on record for the {result.team.name} in the {span}."
    if result.from_season_line:
        return f"The {result.team.name} had {result.value:,.0f} {label} over the complete {span} ({result.games} games).{result.note}{result.coverage_note}"
    value = f"{result.value:+,.0f}" if q.measure == "differential" else f"{result.value:,.0f}"
    record = f" ({result.wins}-{result.losses})" if result.wins is not None else ""
    per_game = f" ({result.value / result.games:+.2f} per game)" if q.measure == "differential" and result.games else ""
    # `narrowed_text` already says "over their last N games" when a window
    # narrowed the read (TeamNarrowed.filters()) - saying the count again
    # here would read as "over 10 games over their last 10 games".
    games_phrase = "" if "game" in result.narrowed_text else f" over {result.games} games"
    return f"The {result.team.name} {'are' if label == 'point differential' else 'had'} {value} {label}{per_game}{games_phrase}{result.narrowed_text}{record}.{result.coverage_note}"


def sentence(q: Query, out: dict[str, Any]) -> str:
    """The answer for a compiled :class:`~association.query.compose.core.Query`
    and its rows: one skeleton, one sentence shape, carrying the subject, the
    span, and every narrowing the relation applied.

    .. versionadded:: 4.4.0
    """
    if q.skeleton == "rows":
        return _rows_sentence(q, out)
    if q.skeleton == "scalar":
        return _scalar_sentence(q, out)
    if q.skeleton == "grouped":
        return _grouped_sentence(q, out)
    return f"{out['player']}{out['narrowing']}: {len(out['rows'])} rows"
