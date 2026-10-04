"""The sayer: a :class:`~association.query.result.Result` worded as the
answer - a heading, a table, the remarks beneath it - and the plain
values the page renders from. It takes a Result and nothing else: no
connection, no relation, no question (``ROADMAP.md``, contract 3; the
import contract "The compiler's sentence reads no warehouse" holds this
module as it holds :mod:`association.query.compose.sentence`). Each note
kind has ONE phrase, here (:func:`note_phrase`), so rewording a note is
editing that phrase; the templates that still write their own sentences
phrase their notes through it too (``templates.common._box_score_notes``).

Phase 2's first slice (``ROADMAP.md``, "Phase 2, the expected steps",
step 0): the game log's words, taken from the retired template's body
(``templates.games._player_game_log`` and ``team_game_log``) and
reproduced from the Result word for word.

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from typing import Any

from association.query.conditions import _SPLIT_TITLES, _margin, _split_cells, _split_label, _table, _win_pct
from association.query.notes import Note, note
from association.query.player_games import _joined
from association.query.result import Result, Rows, Scalar
from association.query.templates.common import PLAYER_STAT_COLUMNS, SEASON_TYPE_NAMES, STAT_LABELS, TemplateResult, count_games, format_value, season_label, season_phrase
from association.query.templates.players import MADE_STAT_ATTEMPTS, SHOOTING_STATS

from .logs import LOG_PERCENTAGES, log_key

# --- notes: one phrase per kind -----------------------------------------------


def _say_window_short(facts: dict[str, Any], narrowing: str) -> str:
    count = facts["found"]
    plural = "s" if count != 1 else ""
    if facts.get("season_type") == [2, 3]:
        return f"Only {count} game{plural}{narrowing} found across the regular season and postseason."
    season = facts.get("season")
    found_in = "in his box scores" if season is None else f"in the {season_phrase(season, facts['season_type'])} - ask about his career to reach earlier seasons"
    return f"Only {count} game{plural}{narrowing} {found_in}."


def _say_definition(facts: dict[str, Any]) -> str:
    if facts.get("term") == "played":
        return "Played means he appeared in the game, and W-L is his team's record in those games."
    if facts.get("term") == "months_eastern":
        return "Months go by the US Eastern date of the game."
    if facts.get("term") == "pool":
        return f"Over the {facts['games']} games he played; a game he missed is in neither row."
    if facts.get("term") != "without":
        raise ValueError(f"no phrase for the definition of {facts.get('term')!r}")
    names = list(facts["names"])
    who = "he did not play" if len(names) == 1 else ("neither of them played" if len(names) == 2 else "none of them played")
    return f"Without {_joined(names)} means games {who} while on the same team - a did-not-play entry, or no line in the box score at all, which is how most injuries appear."


def _say_lines_rebuilt(facts: dict[str, Any]) -> str:
    shown = facts["games"]
    whose = "their" if shown != 1 else "its"
    return f"{shown} of these game{'s have' if shown != 1 else ' has'} no box score from ESPN: {whose} figures are rebuilt from play-by-play, and minutes cannot be recovered at all."


def _say_games_unseen(facts: dict[str, Any]) -> str:
    count = facts["games"]
    if facts.get("why") == "no_box_score":
        whose = facts.get("whose", "his team's")
        return f" The warehouse has no box score for {count} of {whose} games in that span - ESPN lacks about one game in eight from 2013 to 2018 - so any of them he played are not counted."
    return f"Not counted: {count} game{'s' if count != 1 else ''} in this span whose box score lists him with no minutes and no stats."


def _say_stat_blank(facts: dict[str, Any]) -> str:
    if "columns" in facts:
        return f" Rebounds, assists, 3-pointers and FG% are missing from {facts['games']} of those games' box scores and are averaged over the rest."
    unit = _unit(facts["stat"])
    return f" {facts['games']} of his games in that span have no {unit} figure on record, so they are in neither row."


def _unit(stat: Any) -> str:
    """The unit a line is said in: the stat's label with an "s", as the
    record template pluralized it ("point" -> "points")."""
    return f"{STAT_LABELS.get(stat or '', stat or '')}s"


def _say_floor(facts: dict[str, Any]) -> str:
    first = facts["first"]
    if "earliest" in facts:
        return f"Box scores begin with the {season_label(first)} season, so his {facts['earliest']}-{first - 1} seasons are not counted."
    return f" Box scores start with the {first} {facts['what']}; anything earlier is not counted."


def note_phrase(each: Note, *, narrowing: str = "") -> str:
    """The one sentence a note of ``each.kind`` is said with, from its facts.
    ``narrowing`` is the read's own phrase for what it was narrowed to
    (``Result.narrowing.phrase``), which a window note follows a count
    with.

    .. versionadded:: 5.0.0
    """
    facts = dict(each.facts)
    if each.kind == "window_short":
        return _say_window_short(facts, narrowing)
    if each.kind == "definition":
        return _say_definition(facts)
    if each.kind == "lines_rebuilt":
        return _say_lines_rebuilt(facts)
    if each.kind == "games_unseen":
        return _say_games_unseen(facts)
    if each.kind == "stat_blank":
        return _say_stat_blank(facts)
    if each.kind == "floor" and facts.get("table") == "box_scores":
        return _say_floor(facts)
    raise ValueError(f"no phrase for a {each.kind!r} note with {sorted(facts)}")


def _said(result: Result) -> list[str]:
    """Every note on ``result``, phrased and recorded (:func:`~association.query.notes.note`)."""
    return [note(each.kind, note_phrase(each, narrowing=result.narrowing.phrase), **each.facts) for each in result.notes]


# --- the player's log -----------------------------------------------------------


def _log_cell(header: str, value: Any, *, average: bool = False) -> str:
    if value is None:
        return "-"
    if header == "+/-":
        return f"{value:+.1f}" if average else f"{int(value):+d}"
    if header in LOG_PERCENTAGES or average:
        return f"{value:.1f}"
    return str(int(value))


def _aligned(titles: list[str], rows: list[list[str]], left: int) -> list[str]:
    """Rows under titles, the first ``left`` columns left-aligned and the rest
    right-aligned, indented like every other listing here."""
    widths = [max(len(title), *(len(row[i]) for row in rows)) for i, title in enumerate(titles)]

    def _line(cells: list[str]) -> str:
        return ("  " + "  ".join(cell.ljust(width) if i < left else cell.rjust(width) for i, (cell, width) in enumerate(zip(cells, widths, strict=True)))).rstrip()

    return [_line(titles), *(_line(row) for row in rows)]


def _scope_words(count: int, ascending: bool, date: str | None) -> str:
    if date:
        return f"on {date}"
    if count == 1:
        return "first game" if ascending else "most recent game"
    return f"first {count} games" if ascending else f"last {count} games"


def mixed_where(season: int, counts: dict[int, int]) -> str:
    """The clause a mixed-type "last N games" heading adds after the count of
    games - "of the 2026 postseason" where every kept game is one type, or
    "(2 regular season and 3 postseason)" where they are not, naming the
    default actually used the way AGENTS.md's "a reasonable default beats a
    question" requires.

    .. versionadded:: 5.0.0
    """
    if len(counts) == 1:
        (season_type,) = counts
        return f" of the {season_phrase(season, season_type)}"
    parts = [f"{count} {SEASON_TYPE_NAMES[season_type]}" for season_type, count in sorted(counts.items())]
    return f" ({_joined(parts)})"


def _player_log_header(result: Result, body: Rows) -> str:
    """The listing's headline: how many games, over what span, filtered how -
    naming how many games the window cut from ("last 10 of 49 games")
    whenever the count before the window exceeds what is listed (F149)."""
    span, window = result.span, result.window
    assert window is not None
    count = len(body.rows)
    if body.by_season_type:
        return f"{result.subject}{result.narrowing.phrase}, {_scope_words(count, False, None)}{mixed_where(span.season or 0, dict(body.by_season_type))}:"
    total = body.total_before_window
    cut = total is not None and total > count
    if span.date:
        scope_text = f"{'game' if count == 1 else 'games'} on {span.date}"
    elif count == 1:
        scope_text = f"{'first' if window.ascending else 'most recent'} of {total} games" if cut else ("first game" if window.ascending else "most recent game")
    else:
        word = "first" if window.ascending else "last"
        scope_text = f"{word} {count} of {total} games" if cut else f"{word} {count} games"
    if span.season is not None:
        where_text = f" of the {season_phrase(span.season, span.season_type or 2)}"
    elif span.date:
        where_text = f" ({span.years})"
    else:
        where_text = f" of his career ({span.years})"
    return f"{result.subject}{result.narrowing.phrase}, {scope_text}{where_text}:"


def _player_log_table(headers: list[str], games: list[dict[str, Any]], averages: dict[str, Any]) -> list[str]:
    """The aligned date/opponent/result/stat table, its last row the per-game
    averages."""
    titles = ["date", "opp", "W/L", *headers]
    body = [[g["date"], f"{'vs' if g['home_away'] == 'home' else '@'} {g['opponent']}", g["result"] or "-", *(_log_cell(h, g[log_key(h)]) for h in headers)] for g in games]
    body.append(["per game", "", "", *(_log_cell(h, averages[log_key(h)], average=True) for h in headers)])
    return _aligned(titles, body, left=3)


def _player_about(result: Result) -> dict[str, Any]:
    span, narrowing = result.span, result.narrowing
    return {
        "player": result.subject,
        "season": span.season,
        "span": "career" if span.career and not result.facts.get("mixed") else None,
        "opponent": narrowing.opponent,
        "venue": narrowing.venue,
        "without": list(narrowing.without),
    }


def say_player_log(result: Result) -> TemplateResult:
    """A player's log, worded: the heading, the aligned table with its
    per-game averages, the notes - and the plain values beneath them.

    .. versionadded:: 5.0.0
    """
    about = _player_about(result)
    body = result.rows
    if body is None or not body.rows:
        assert result.empty is not None
        return TemplateResult(data={**about, "games": [], "message": result.empty}, answer=result.empty)
    headers = list(body.columns)
    games = [dict(g) for g in body.rows]
    averages = dict(body.summary)
    header = _player_log_header(result, body)
    table = _player_log_table(headers, games, averages)
    notes = _said(result)
    data: dict[str, Any] = {**about, "columns": headers, "games": games, "averages": averages}
    if not body.by_season_type:
        data["qualifying_games"] = body.total_before_window
    data.update({"headline": header.rstrip(":"), "notes": notes})
    return TemplateResult(data=data, answer="\n".join([header, *table, *notes]))


# --- the team's log ----------------------------------------------------------------

#: A ``stat`` slot's own word, mapped to the total a team's log states
#: beneath its games - "total points" (F128) and "point differential"
#: (F129), both narrowed correctly by the log already but never STATED
#: until this existed: the games were right and the question's own number
#: was still missing.
TEAM_LOG_STAT_TOTALS: dict[str, str] = {
    "points": "points",
    "pointsDifference": "differential",
    # The model's other spellings of the same thing, seen live once the
    # router's prompt shrank (day6, F129).
    "points_differential": "differential",
    "point_differential": "differential",
    "differential": "differential",
}
"""``stat`` -> which total a team's log states beneath its games.

.. versionadded:: 5.0.0
"""


def _team_total_line(games: list[dict[str, Any]], stat: Any) -> str:
    """The total or differential line a team's log states when ``stat`` asks
    for one it can compute from the games listed, or nothing."""
    kind = TEAM_LOG_STAT_TOTALS.get(stat) if isinstance(stat, str) else None
    if kind is None or not games:
        return ""
    if kind == "points":
        return f"\n  Total points: {sum(g['team_score'] for g in games):,}."
    diff = sum(g["team_score"] - g["opponent_score"] for g in games)
    return f"\n  Point differential: {diff:+,} ({diff / len(games):+.2f} per game)."


def _team_total_data(games: list[dict[str, Any]], stat: Any) -> dict[str, Any]:
    """The same total or differential as plain values, for the page."""
    kind = TEAM_LOG_STAT_TOTALS.get(stat) if isinstance(stat, str) else None
    if kind is None or not games:
        return {}
    if kind == "points":
        return {"total_points": sum(g["team_score"] for g in games)}
    diff = sum(g["team_score"] - g["opponent_score"] for g in games)
    return {"differential": diff, "differential_per_game": round(diff / len(games), 2)}


def say_team_log(result: Result) -> TemplateResult:
    """A team's log, worded: the heading with its record, one line per game,
    the total the question asked for beneath them.

    .. versionadded:: 5.0.0
    """
    body = result.rows
    if body is None or not body.rows:
        assert result.empty is not None
        return TemplateResult(data={"team": result.subject, "games": []}, answer=result.empty)
    games = [dict(g) for g in body.rows]
    wins, losses, unknown = body.summary["wins"], body.summary["losses"], body.summary["unknown"]
    record = f"{wins}-{losses}" + (f", {unknown} with no recorded result" if unknown else "")
    span, window = result.span, result.window
    assert window is not None
    if body.by_season_type:
        where = mixed_where(span.season or 0, dict(body.by_season_type))
        scope_text = _scope_words(len(games), False, None)
    else:
        where = f" of the {season_phrase(span.season, span.season_type or 2)}" if span.season is not None else (f" ({span.years})" if span.date else f" (all-time, {span.years})")
        scope_text = _scope_words(len(games), window.ascending, span.date)
    header = f"{result.subject}{result.narrowing.phrase}, {scope_text}{where} ({record}):"
    mark = {True: "W", False: "L", None: "?"}
    lines = [f"  {g['date']}  {mark[g['won']]} {g['team_score']}-{g['opponent_score']}  {'vs' if g['home_away'] == 'home' else 'at'} {g['opponent']}" for g in games]
    stat = result.facts.get("stat")
    data = {"team": result.subject, "wins": wins, "losses": losses, "games": games, "headline": header.rstrip(":"), **_team_total_data(games, stat)}
    return TemplateResult(data=data, answer="\n".join([header, *lines]) + _team_total_line(games, stat))


def say(result: Result) -> TemplateResult:
    """``result`` worded by its shape: a team's rows, a player's rows, a
    record grouped by a line, splits, or a player's line.

    .. versionadded:: 5.0.0
    """
    if result.scalar is not None:
        return say_player_stat(result)
    if result.grouped is not None and result.grouped.by == "threshold":
        return say_record_when(result)
    if result.grouped is not None and result.grouped.by == "split":
        return say_splits(result)
    return say_team_log(result) if result.relation == "team" else say_player_log(result)


# --- a record over a line ----------------------------------------------------------


def say_record_when(result: Result) -> TemplateResult:
    """A player's team's record when he reached a line, worded: the three-row
    table (reached, fell short, all his games) under its heading, then the
    pool, the floor and the caveats in the retired template's order.

    .. versionadded:: 5.0.0
    """
    groups = result.grouped
    assert groups is not None and result.span.phrase is not None
    stat, threshold, teams = result.facts["stat"], result.facts["threshold"], list(result.facts["teams"])
    unit = _unit(stat)
    by_key = {row["key"]: row for row in groups.rows}
    reached, short, every = by_key["reached"], by_key["short"], by_key["all"]
    whose = f"{teams[0]} record" if len(teams) == 1 else f"Record of {result.subject}'s teams ({', '.join(teams)})"
    title = f"{whose} when {result.subject} had {threshold}+ {unit}{result.narrowing.phrase}, {result.span.phrase}:"
    rows = [(f"{threshold}+ {unit}", reached), (f"under {threshold} {unit}", short), ("all his games", every)]
    table = _table(title, ["G", "W-L", "Win%", "Margin"], [(name, [str(g["games"]), f"{g['wins']}-{g['losses']}", _win_pct(g["wins"], g["games"]), _margin(g["avg_margin"])]) for name, g in rows])
    # Recorded in the Result's order (the template wrote the caveats first),
    # said in the heading's: the pool, the floor, the caveats.
    said = {each.kind: text for each, text in zip(result.notes, _said(result), strict=True)}
    trailer = said.get("definition", "") + said.get("floor", "") + said.get("games_unseen", "") + said.get("stat_blank", "")
    data = {
        "player": result.subject,
        "teams": teams,
        "stat": stat,
        "threshold": threshold,
        "span": result.span.phrase,
        "reached": {k: v for k, v in reached.items() if k != "key"},
        "fell_short": {k: v for k, v in short.items() if k != "key"},
        "headline": title.rstrip(":"),
        "notes": [trailer],
    }
    return TemplateResult(data=data, answer=f"{table}\n{trailer}")


# --- splits ----------------------------------------------------------------------------


def say_splits(result: Result) -> TemplateResult:
    """A player's or a team's splits, worded: the shared table over whichever
    subject was read - one or all four splits, each split's rows under its
    group label, a blank line between splits - and the notes that qualify
    them, in the retired template's order: what the words mean, the floor,
    the caveat.

    .. versionadded:: 5.0.0
    """
    groups = result.grouped
    assert groups is not None and result.span.phrase is not None
    facts = result.facts
    split, kinds, counted = facts["split"], list(facts["kinds"]), facts["counted"]
    line = [(name, header, "") for name, header in facts["line"]]
    by_kind: dict[str, list[dict[str, Any]]] = {kind: [] for kind in kinds}
    for row in groups.rows:
        by_kind[row["split"]].append({k: v for k, v in row.items() if k != "split"})
    rows: list[tuple[str, list[str]]] = []
    for kind in kinds:
        if rows:
            rows.append(("", []))
        rows += [(_split_label(kind, entry), _split_cells(entry, line)) for entry in by_kind[kind]]
    what = _SPLIT_TITLES[split] if split else "splits"
    subject = result.subject + (f" for the {facts['for_team']}" if facts.get("for_team") else "") + result.narrowing.phrase
    headline = f"{subject}, {what}, {result.span.phrase} ({counted}):"
    answer = _table(headline, ["G", "W-L", *(header for _, header, _ in line)], rows)
    said = list(zip(result.notes, _said(result), strict=True))
    notes = (
        [text for each, text in said if each.kind == "definition"]
        + [text.strip() for each, text in said if each.kind == "floor"]
        + [text.strip() for each, text in said if each.kind in ("games_unseen", "stat_blank")]
    )
    answer += "\n" + " ".join(notes)
    data = {**facts["about"], "span": result.span.phrase, "games": facts["games"], "splits": by_kind, "headline": headline.rstrip(":"), "notes": notes}
    return TemplateResult(data=data, answer=answer.strip())


# --- a player's line ---------------------------------------------------------------


def rounded(value: Any) -> float | None:
    """A computed per-game figure to one decimal, as ESPN's stored ones are -
    "21.33 points" next to a stored "27.7" reads as a different unit.

    .. versionadded:: 5.0.0
       The sayer's, from ``templates.players._rounded``.
    """
    return None if value is None else round(float(value), 1)


def stat_value_labels(wanted: list[str]) -> dict[str, str]:
    """``data["stats"]``' own labels for a multi-stat ``player_stat`` line -
    the page's static abbreviation table (``LABELS``,
    ``web/static/index.html``) maps both the per-game column
    (``avgPoints``) and the season total (``points``) to the same short
    word ("PTS"), so a line naming both prints two tiles labeled identically
    with no way to tell the average from the total apart (seen live on the
    rendered page, 2026-09-24: "PTS" twice, one of them a season sum in the
    hundreds sitting next to a per-game figure under 30). Every wanted
    stat's own English label (``PLAYER_STAT_COLUMNS``' third element)
    names both explicitly rather than leaving the page to guess a pair apart
    that its own lookup collapses to one string.

    .. versionadded:: 4.4.0

    .. versionchanged:: 5.0.0
       The sayer's, from ``templates.players._stat_value_labels``.
    """
    labels: dict[str, str] = {}
    for name in wanted:
        per_game, total, label = PLAYER_STAT_COLUMNS[name]
        labels[per_game] = f"{label} per game"
        if total:
            labels[total] = f"{label} total"
    return labels


def shooting_result(name: str, scope: dict[str, Any], values: dict[str, Any], shooting: Any, *, when: str, games_note: str = "") -> TemplateResult:
    """A percentage with the makes and attempts behind it - "out of how many?"
    is the first thing anybody asks of a percentage without them.

    ``shooting`` is the stat's entry in ``templates.players.SHOOTING_STATS``.
    ``values`` carries the makes and attempts keyed by ``shooting.made``/
    ``shooting.attempted`` - the SQL each was read through, which for every
    stat but ``twoPointFieldGoalPct`` is already a real column name. The
    ``data["stats"]`` this returns is keyed by ``shooting.made_key``/
    ``attempted_key`` instead - the stable names, identical to the SQL for
    three of the four stats and the whole fix for the fourth - with
    ``data["labels"]`` carrying the short label a page prints beside each
    one.

    .. versionadded:: 4.4.0

    .. versionchanged:: 5.0.0
       The sayer's, from ``templates.players._shooting_result``.
    """
    made_col, attempted_col, how, noun = shooting.made, shooting.attempted, shooting.how, shooting.noun
    made, attempted, games = values.get(made_col), values.get(attempted_col), values.get("gamesPlayed")
    played = f" in {count_games(games)}{games_note}" if games else games_note
    if attempted is None or made is None:
        answer = f"{name} has no {noun} on record{played} {when}."
        pct = None
    elif not attempted:
        answer = f"{name} attempted no {noun}{played} {when}."
        pct = None
    else:
        pct = 100.0 * made / attempted
        answer = f"{name} shot {pct:.1f}% {how} ({made:,} of {attempted:,}){played} {when}."
    stats = {k: v for k, v in values.items() if k not in (made_col, attempted_col)}
    stats[shooting.made_key] = made
    stats[shooting.attempted_key] = attempted
    stats["pct"] = pct
    labels = {shooting.made_key: shooting.made_label, shooting.attempted_key: shooting.attempted_label, "pct": shooting.pct_label}
    return TemplateResult(data={"player": name, **scope, "stats": stats, "labels": labels}, answer=answer)


def phrase_player_stat(name: str, period: str, values: dict[str, Any], wanted: list[str], *, games_note: str = "", when: str | None = None, attempted: Any = None) -> str:
    """A player's line in one sentence: each wanted stat per game, the games
    and the span, and - for one stat alone - its total, or a made count's
    makes of its attempts.

    .. versionadded:: 5.0.0
       The sayer's, from ``templates.players._phrase_player_stat``.
    """
    games = values.get("gamesPlayed")
    parts = []
    for stat in wanted:
        per_game_col, _, label = PLAYER_STAT_COLUMNS[stat]
        per_game = values.get(per_game_col)
        if per_game is not None:
            parts.append(f"{format_value(per_game)} {label}")
    if not parts:
        return f"{name} has no {period} numbers in the warehouse."
    body = ", ".join(parts[:-1]) + f" and {parts[-1]}" if len(parts) > 1 else parts[0]
    if games == 1 and when and when.startswith("on "):
        # One game on one date is a line, not an average: "had 33 points, 3
        # rebounds and 6 assists on 2026-03-01", and no total to add.
        return f"{name} had {body}{games_note} {when}."
    played = f" in {count_games(games)}{games_note}" if games else games_note
    sentence = f"{name} averaged {body} per game{played} {when or f'in the {period}'}."
    # The season total goes in its own clause rather than inline, and only when
    # a single stat was asked for - inline it read as "33.5 points (2143 total)
    # per game", which says something false.
    if len(wanted) == 1:
        total_col = PLAYER_STAT_COLUMNS[wanted[0]][1]
        total = values.get(total_col) if total_col else None
        if total is not None:
            # A made-count stat with its attempted total on hand (F051,
            # ISSUES.md): "90 of 228 (39.5%)" is the makes and attempts, and
            # the percentage they make - the same reading a bare percentage
            # answer always carries, since a made-count with no attempts
            # beside it is the thing a reader immediately asks "out of how
            # many?" about. Falls back to the plain total when the stat has
            # no attempted sibling (points, rebounds, assists...).
            if attempted:
                sentence += f" That is {round(total):,} of {round(attempted):,} ({100.0 * total / attempted:.1f}%)."
            else:
                sentence += f" That is {total:,} in total."
    return sentence


def _player_stat_values(result: Result, line: Scalar) -> tuple[dict[str, Any], Any]:
    """The line as ``data["stats"]`` holds it: the games, then each wanted
    stat's per-game figure and total, then a made count's attempts - and
    those attempts, for the sentence."""
    wanted = list(result.facts["wanted"])
    values: dict[str, Any] = {"gamesPlayed": line.games}
    for stat in wanted:
        per_game_col, total_col, _ = PLAYER_STAT_COLUMNS[stat]
        values[per_game_col] = rounded(line.values[stat])
        if total_col and line.sums.get(stat) is not None:
            values[total_col] = int(line.sums[stat])
    attempted_col = MADE_STAT_ATTEMPTS.get(wanted[0]) if len(wanted) == 1 else None
    attempted = line.sums.get(attempted_col) if attempted_col else None
    if attempted_col and attempted is not None:
        values[attempted_col] = int(attempted)
    return values, attempted


def _player_stat_meetings(meetings: Rows) -> list[str]:
    """The newest meetings behind an average against one opponent, as the
    lines that end the answer."""
    recent = meetings.rows
    shown, total = len(recent), meetings.total_before_window or 0
    if total > shown:
        title = f"Most recent {shown} of the {total} meetings:"
    elif shown == 1:
        title = "The only meeting:"
    else:
        title = f"All {shown} meetings:"

    def _count(value: Any) -> str:
        return "-" if value is None else str(int(value))

    return [title] + [
        f"  {g['date']}  {'vs' if g['home_away'] == 'home' else '@'} {g['opponent']}  {g['result'] or '-'}  {_count(g['points'])} PTS, {_count(g['rebounds'])} REB, {_count(g['assists'])} AST"
        for g in recent
    ]


def say_player_stat(result: Result) -> TemplateResult:
    """A player's line over the games a question narrowed to, worded: one
    sentence (a percentage with its makes and attempts, or each stat per
    game with the total of one stat alone), the remarks after it, and
    against one opponent the newest meetings beneath - the retired
    ``player_stat`` template's words.

    .. versionadded:: 5.0.0
    """
    line = result.scalar
    assert line is not None
    about, span = dict(result.facts["about"]), result.span
    if not line.games:
        assert result.empty is not None
        return TemplateResult(data={"player": result.subject, **about, "games": 0, "stats": {}}, answer=result.empty)
    notes = _said(result)
    scope = {**about, "seasons": [span.first, span.last]}
    when, games_note = span.phrase or "", result.narrowing.phrase
    stat = result.facts["stat"]
    if stat is not None:
        shooting = SHOOTING_STATS[stat]
        said = shooting_result(
            result.subject, scope, {"gamesPlayed": line.games, shooting.made: line.sums["made"], shooting.attempted: line.sums["attempted"]}, shooting, when=when, games_note=games_note
        )
    else:
        wanted = list(result.facts["wanted"])
        values, attempted = _player_stat_values(result, line)
        answer = phrase_player_stat(result.subject, season_phrase(span.first or 0, span.season_type or 2), values, wanted, games_note=games_note, when=when, attempted=attempted)
        said = TemplateResult(data={"player": result.subject, **scope, "stats": values, "labels": stat_value_labels(wanted)}, answer=answer)
    if notes:
        said.answer = " ".join([said.answer, *notes])
    if len(result.parts) > 1 and isinstance(result.parts[1].body, Rows):
        meetings = result.parts[1].body
        said.data["recent"] = [dict(g) for g in meetings.rows]
        said.answer = "\n".join([said.answer, *_player_stat_meetings(meetings)])
    return said
