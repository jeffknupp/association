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

from collections.abc import Sequence
from typing import Any

from association.query.conditions import _SPLIT_TITLES, _cell, _margin, _split_cells, _split_label, _table, _win_pct
from association.query.notes import Note, decided, note
from association.query.player_games import PERIOD_LOG_COLUMNS, _joined, period_columns
from association.query.reading import DEFAULT_GAME_LOG_LIMIT, _clamp_limit, ordinal_word
from association.query.result import Decided, Grouped, Result, Rows, Run, Scalar, Span
from association.query.season_line import NETPOINTS_COMPARE_ROWS
from association.query.shotchart import UNSEPARABLE_SHOT_VALUES
from association.query.templates.common import HISTORY_COLUMNS, PLAYER_STAT_COLUMNS, SEASON_TYPE_NAMES, STAT_LABELS, TemplateResult, count_games, format_value, season_label, season_phrase, table_cell
from association.query.templates.players import ADVANCED_STATS, MADE_STAT_ATTEMPTS, SHOOTING_STATS

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
    if facts.get("term") == "overtime_excluded":
        return "Overtime is no quarter and is not counted."
    if facts.get("term") == "played":
        return "Played means he appeared in the game, and W-L is his team's record in those games."
    if facts.get("term") == "months_eastern":
        return "Months go by the US Eastern date of the game."
    if facts.get("term") == "pool":
        return f"Over the {facts['games']} games he played; a game he missed is in neither row."
    if facts.get("term") == "streak_rule":
        return _say_streak_rule(facts)
    if facts.get("term") == "most_recent_team":
        # Its own line beneath a ranking's table, the way a table's other notes follow it.
        return "\nTeam is each player's most recent team that season."
    if facts.get("term") == "unseen_ends_run":
        return " A game with no box score in the warehouse ends a run rather than being carried across, since it cannot be checked."
    if facts.get("term") != "without":
        raise ValueError(f"no phrase for the definition of {facts.get('term')!r}")
    names = list(facts["names"])
    who = "he did not play" if len(names) == 1 else ("neither of them played" if len(names) == 2 else "none of them played")
    return f"Without {_joined(names)} means games {who} while on the same team - a did-not-play entry, or no line in the box score at all, which is how most injuries appear."


#: What a run is counted over, by who has it - the rule beneath a streak.
_STREAK_RULES: dict[str, str] = {
    "team_within_season": "Streaks are counted within one season.",
    "league_team_within_season": "Each team's longest in a season, counted within that season.",
}


def _say_streak_rule(facts: dict[str, Any]) -> str:
    what = facts["what"]
    if what == "player_games_played":
        return "Only games he played count: a game he missed neither extends the run nor ends it" + (", and a run carries on from one season into the next." if facts["across_seasons"] else ".")
    if what == "league_player_games_played":
        return "Each player's longest run, counting only games he played" + (", carried across seasons." if facts["across_seasons"] else ".")
    return _STREAK_RULES[what]


def _say_lines_rebuilt(facts: dict[str, Any]) -> str:
    if facts.get("what") == "counted":
        return _say_lines_rebuilt_counted(facts)
    if facts.get("what") == "single_game":
        return " That game has no box score from ESPN - the figure is rebuilt from its play-by-play, so treat it as close rather than exact."
    shown = facts["games"]
    whose = "their" if shown != 1 else "its"
    return f"{shown} of these game{'s have' if shown != 1 else ' has'} no box score from ESPN: {whose} figures are rebuilt from play-by-play, and minutes cannot be recovered at all."


def _say_lines_rebuilt_counted(facts: dict[str, Any]) -> str:
    """The count's leader's rebuilt games: "52 of those 52 games" is true
    and reads as a bug, so where EVERY counted game was rebuilt - Anthony
    Davis's whole 2015, every Chicago and New Orleans season from 2013 to
    2018 - it says so outright; two sentences rather than one with a
    swapped subject, which agreed "HAVE" with the count instead of with its
    own subject."""
    rebuilt_shown, counted, whose_name = facts["games"], facts["total"], facts["whose"]
    named = whose_name is None
    whose = "those" if named else f"{whose_name}'s"
    plural = rebuilt_shown != 1
    if rebuilt_shown == counted:
        lead = f"None of {whose} {counted} games has a box score from ESPN" if plural else f"{'That' if named else whose + ' only'} game has no box score from ESPN"
    else:
        lead = f"{rebuilt_shown} of {whose} {counted} games {'have' if plural else 'has'} no box score from ESPN"
    return f" {lead} - {'those figures are' if plural else 'that figure is'} rebuilt from play-by-play, so treat the count as close rather than exact."


def _say_stat_withheld(facts: dict[str, Any]) -> str:
    """A stat outside the rebuilt set, with rebuilt lines in scope: a
    DECISION, not a gap - a count says it was not counted, a single-game
    high (no ``stat`` fact) that it was not read."""
    errors = "rebuilt fouls are wrong in about one game in six, and turnovers in one in thirteen, against one in sixty for points."
    if "stat" in facts:
        return f"{facts['games']:,} of the games in that span were rebuilt from play-by-play, but a {facts['label']} is not counted from a rebuilt line: {errors}"
    return f"{facts['games']:,} of them were rebuilt from play-by-play, but a {facts['label']} is not read from a rebuilt line: {errors}"


def _say_empty_box_scores(facts: dict[str, Any], consequence: str) -> str:
    """Games in scope whose box score is empty, by count and years, and what
    that does to the answer (``consequence``, the answer's own words)."""
    count, first, last, name = facts["games"], facts["first"], facts["last"], facts["whose"]
    whose = f"{count:,} of {name}'s games" if name else f"{count:,} {'game' if count == 1 else 'games'}"
    between = f"in {season_label(first)}" if first == last else f"between {season_label(first)} and {season_label(last)}"
    return f" {whose} {between} {'has' if count == 1 else 'have'} an empty box score in this warehouse, so {consequence}."


def _say_games_unseen(facts: dict[str, Any]) -> str:
    count = facts["games"]
    if facts.get("what") == "meetings":
        return f" {count} game{'' if count == 1 else 's'} between their teams while both were playing for them {'has' if count == 1 else 'have'} no box score, so a meeting there is not counted."
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
    what = facts.get("what")
    if what == "career_began_earlier":
        # Said FIRST, before the number (a count's or a high's preface).
        kind = SEASON_TYPE_NAMES.get(facts["season_type"], "regular season")
        return f"Box scores here begin in {season_label(first)}, and {facts['whose']}'s {kind} career began in {season_label(facts['earliest'])}, so his whole career is not in them. "
    if what == "league_counts":
        return f"Box scores begin in {season_label(first)}, so these are not all-time counts: a career that began earlier is counted only from {season_label(first)}."
    if what == "league_record":
        return f" Box scores begin in {season_label(first)}, so this is not an all-time record: earlier games are not in this warehouse."
    if "earliest" in facts:
        return f"Box scores begin with the {season_label(first)} season, so his {facts['earliest']}-{first - 1} seasons are not counted."
    return f" Box scores start with the {first} {facts['what']}; anything earlier is not counted."


def decision_phrase(each: Decided, **said_with: Any) -> str:
    """ONE phrase per decision kind (:data:`~association.query.notes.DECISION_KINDS`),
    recorded through :func:`~association.query.notes.decided` as it is
    said - the decision counterpart of :func:`note_phrase`. ``said_with``
    is what the sentence needs from the answer around it (a count the
    heading also states), never a fact the decision should carry itself.

    .. versionadded:: 5.0.0
    """
    if each.kind == "minimum":
        text = f" (minimum {each.chose:,} {each.facts['of']})"
    elif each.kind == "season_fallback":
        text = f"No games this season, so these are his most recent {said_with['games']}{said_with['at']}, from the {said_with['season_label']}."
    elif each.kind == "season_redirected":
        first, last, kind = each.facts["first"], each.facts["last"], each.facts["what"]
        # Singular for one season, plural for a range - the same rule _Span.years
        # uses, so "he last appears in 2010" is never followed by "his 2010
        # seasons" for a player on record in exactly one. ``career_hint`` is
        # the answer's: a chart has no career to ask for.
        seasons = f"{first} {kind}" if first == last else f"{first}-{last} {kind}s"
        tail = ", or ask for his career." if said_with.get("career_hint", True) else "."
        text = f" He last appears in {last}. The warehouse holds his {seasons}; name one{tail}"
    else:
        raise ValueError(f"no phrase for decision kind {each.kind!r}")
    return decided(each.kind, text, field=each.field, chose=each.chose, before=each.before, instead_of=each.instead_of, why=each.why, **each.facts)


def note_phrase(each: Note, *, narrowing: str = "", consequence: str = "", listed: bool = False) -> str:
    """The one sentence a note of ``each.kind`` is said with, from its facts.
    ``narrowing`` is the read's own phrase for what it was narrowed to
    (``Result.narrowing.phrase``), which a window note follows a count
    with; ``consequence`` is what empty box scores do to the answer around
    the note ("the count may be low"), which only that answer can say.
    ``listed`` says the note keys a table whose open rows are starred
    (a run still going, beneath the league's listing).

    .. versionadded:: 5.0.0
    """
    facts = dict(each.facts)
    if each.kind == "still_open":
        return " * still going at the last game on record." if listed else " It was still going at the last game on record."
    if each.kind == "window_short":
        return _say_window_short(facts, narrowing)
    if each.kind == "definition":
        return _say_definition(facts)
    if each.kind == "lines_rebuilt":
        return _say_lines_rebuilt(facts)
    if each.kind == "stat_withheld":
        return _say_stat_withheld(facts)
    if each.kind == "games_unseen" and "first" in facts:
        return _say_empty_box_scores(facts, consequence)
    if each.kind == "games_unseen":
        return _say_games_unseen(facts)
    if each.kind == "stat_blank":
        return _say_stat_blank(facts)
    if each.kind == "floor" and facts.get("table") == "box_scores":
        return _say_floor(facts)
    if each.kind == "floor" and facts.get("what") == "career_pool":
        return f"Careers that ended before {season_label(facts['first'])} are not in this warehouse, so this is not an all-time list."
    if each.kind == "rebuilt_agreement" and facts.get("what") in ("period_points_from_shots", "period_rebuilt"):
        return _say_period_agreement(facts)
    if each.kind == "seasons_missing":
        return _say_seasons_missing(facts)
    if each.kind == "no_data_for" and "period" in facts:
        return f"({', '.join(facts['names'])} has no {facts['period']} numbers in the warehouse.)"
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


def _say_grouped(result: Result) -> TemplateResult | None:
    """A grouped body worded by what it is grouped by - two named subjects, a
    ranking by player (a count of games over a line, ``ranked_by="games"``,
    or a stat over the season line), a record over a line, splits, a
    quarter - or ``None`` for any other body. Split out of :func:`say` for
    the complexity gate, in its order."""
    body = result.grouped
    if body is None:
        return None
    if body.by == "player":
        return say_threshold_count(result) if body.ranked_by == "games" else say_leaderboard(result)
    sayers = {"subject": say_player_matchup, "threshold": say_record_when, "split": say_splits, "period": say_period_by_quarter}
    sayer = sayers.get(body.by)
    return sayer(result) if sayer is not None else None


def say(result: Result) -> TemplateResult:
    """``result`` worded by its shape: a team's rows, a player's rows, a
    record grouped by a line, splits, or a player's line.

    .. versionadded:: 5.0.0
    """
    if result.span.source == "seasons":
        return _say_season_line(result)
    if result.scalar is not None and result.scalar.how == "count":
        return say_threshold_count(result)
    if result.runs is not None:
        return say_streak(result)
    if result.scalar is not None:
        return say_player_stat(result)
    grouped = _say_grouped(result)
    if grouped is not None:
        return grouped
    if result.rows is not None and result.rows.by != "date":
        return say_single_game_high(result)
    if result.rows is not None and "period" in result.facts:
        return say_period_split(result)
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


# --- a player's quarter or half ------------------------------------------------------

_PERIOD_WORDS: dict[str, str] = {
    "fieldGoalsAttempted": "field goal attempt",
    "threePointFieldGoalsAttempted": "3-point attempt",
    "freeThrowsAttempted": "free throw attempt",
    "offensiveRebounds": "offensive rebound",
    "defensiveRebounds": "defensive rebound",
}

PERIOD_RATE_WORDS: dict[str, str] = {"fg_pct": "field goal percentage", "three_pct": "3-point percentage", "ft_pct": "free throw percentage"}
"""A period rate's own name (:data:`~association.query.player_games.PERIOD_RATES`).

.. versionadded:: 5.0.0
"""

PERIOD_RATE_SHOTS: dict[str, str] = {"fg_pct": "field goals", "three_pct": "3-pointers", "ft_pct": "free throws"}
"""What a period rate's shots are called ("shot 4 of 7 on 3-pointers").

.. versionadded:: 5.0.0
"""


def period_noun(measure: str, n: int) -> str:
    """``"rebound"``/``"rebounds"`` - the word a period answer says a column
    in; a rate's own name ("free throw percentage"), which has no plural.

    .. versionadded:: 5.0.0
    """
    if measure in PERIOD_RATE_WORDS:
        return PERIOD_RATE_WORDS[measure]
    word = STAT_LABELS.get(measure) or _PERIOD_WORDS.get(measure, measure)
    return word if n == 1 else f"{word}s"


def period_columns_noun(measure: str) -> str:
    """ "rebounds", or for a rate the two columns it divides - "free throws
    and free throw attempts" - for a sentence about how those columns were
    rebuilt rather than about the percentage.

    .. versionadded:: 5.0.0
    """
    return " and ".join(period_noun(column, 2) for column in period_columns(measure))


def period_rate_said(made: int, attempted: int, pct: float | None, measure: str) -> str:
    """ "shot 4 of 7 (57.1%) on 3-pointers", or "attempted no free throws".

    .. versionadded:: 5.0.0
    """
    shots = PERIOD_RATE_SHOTS[measure]
    if pct is None:
        return f"attempted no {shots}"
    return f"shot {made} of {attempted} ({pct:.1f}%) on {shots}"


def _say_period_agreement(facts: dict[str, Any]) -> str:
    season = facts["season"]
    if facts["what"] == "period_points_from_shots":
        return (
            f"(Summed from shot data rather than an official per-quarter box score. In {season} that sum matches ESPN's own "
            f"quarter scores {facts['pct']:.0f}% of the time, so treat a single game as approximate.)"
        )
    said = ", ".join(f"{period_noun(column, 2)} {pct:.0f}%" for column, pct in zip(facts["columns"], facts["pct"], strict=True))
    return (
        f"(Rebuilt from play-by-play rather than an official per-quarter box score. In {season} a game's figures rebuilt this way match its box score "
        f"this often: {said} - treat a single game as approximate.)"
    )


def period_caveat(notes: list[Note]) -> str:
    """A period answer's agreement caveats (``player_games.period_agreement_notes``),
    phrased and recorded, each on its own indented line - the text the answer
    appends, empty where the season needs none.

    .. versionadded:: 5.0.0
    """
    return "".join("\n  " + note(each.kind, note_phrase(each), **each.facts) for each in notes)


def say_period_refusal(facts: dict[str, Any]) -> TemplateResult:
    """The refusal for a season whose per-period figures cannot be trusted
    (``player_games.period_distrust``'s facts): a shot's value the season
    does not carry, points that disagree with ESPN's own quarter scores, or
    a column whose rebuilt figure disagrees with the box score.

    .. versionadded:: 5.0.0
    """
    season, column, agreement = facts["season"], facts["column"], facts["agreement"]
    if facts["unseparable"]:
        message = f"Per-quarter scoring cannot be answered for {season}: {UNSEPARABLE_SHOT_VALUES[season]}."
    elif column == "points":
        message = f"Per-quarter scoring cannot be answered for {season}: its per-period points agree with ESPN's own quarter scores only {agreement:.0f}% of the time."
    else:
        noun = period_noun(column, 2)
        message = f"Per-quarter {noun} cannot be answered for {season}: rebuilt from play-by-play, a game's {noun} match its box score only {agreement:.0f}% of the time."
    return TemplateResult(data={"season": season, "message": message}, answer=message)


def say_period_unread(measure: str) -> TemplateResult:
    """The refusal where a period's ``measure`` could not be rebuilt at all:
    a warehouse loaded without play-by-play leaves every plays column NULL.

    .. versionadded:: 5.0.0
    """
    message = f"Per-quarter {period_columns_noun(measure)} cannot be answered here: they are rebuilt from play-by-play, and this warehouse holds none."
    return TemplateResult(data={"message": message}, answer=message)


def _period_where_said(result: Result) -> str:
    """What a period answer says it narrowed to, after the player and the
    period - the venue, the starter/bench half, the teammates absent, the
    lines on a box-score column, a single date, the game of a series - in
    the words :meth:`~association.query.player_games.Narrowed.filters` uses
    for the same narrowings. Said in the answer, like every other narrowing
    here: a total over his starts, headed as though it covered every game,
    is the silent narrowing ``check_scope`` exists to stop."""
    facts, narrowing = result.facts, result.narrowing
    venue, started, mates, measures = narrowing.venue, facts["started"], list(narrowing.without), list(facts["measures"])
    said = f" at {'home' if venue == 'home' else 'away'}" if venue else ""
    said += "" if started is None else (" as a starter" if started else " off the bench")
    said += f" without {_joined(mates)}" if mates else ""
    said += f" with {_joined(measures)}" if measures else ""
    date = facts.get("date", result.span.date)
    said += f" on {date}" if date else ""
    # One opponent makes it "the" series, a whole postseason "each".
    series_game = facts["series_game"]
    said += f" in game {series_game} of {'the' if narrowing.opponent is not None else 'each'} series" if series_game is not None else ""
    return said + "".join(f" {phrase}" for phrase in facts["also"])


def _period_none_found(result: Result, season_label: str, vs: str, at: str) -> str:
    """No games under the narrowing. A date names its own day (``at`` says
    it), and no season was read for it: "No 2026 regular season games found
    for Anthony Davis on 2015-01-10" named a season the date is not in."""
    dated = result.facts.get("date", result.span.date)
    return f"No games found for {result.subject}{vs}{at}." if dated else f"No {season_label} games found for {result.subject}{vs}{at}."


def _period_about(result: Result) -> dict[str, Any]:
    """The plain values a period answer's page renders from, before its games."""
    return {
        "player": result.subject,
        **({"period": result.facts["period"]} if "period" in result.facts else {}),
        "stat": result.facts["stat"],
        "season": result.span.season,
        "opponent": result.narrowing.opponent,
        "venue": result.narrowing.venue,
        "started": result.facts["started"],
        "measures": list(result.facts["measures"]),
    }


# The period's whole line as a log shows it: each column's heading.
_PERIOD_LOG_HEADINGS: tuple[str, ...] = ("PTS", "REB", "AST", "STL", "BLK", "TO", "PF")


def _period_cell(value: Any) -> str:
    return "-" if value is None else str(value)


def _period_side(game: dict[str, Any]) -> str:
    return f"{'vs' if game['home_away'] == 'home' else '@ '} {game['opponent'] or '?':<24}"


def _period_log(games: list[dict[str, Any]], period_label: str, label: str, measure: str, *, full_line: bool) -> str:
    """The log beneath a period answer: one column (the stat asked about),
    or - where no stat was named - the period's whole line, with its field
    goals and free throws as made-attempted, the way a box score prints them.
    A column the warehouse cannot rebuild (no play-by-play) prints "-"."""
    if measure in PERIOD_RATE_WORDS:
        made_column, attempted_column = period_columns(measure)
        rows = []
        for g in games:
            pct = "-" if g[measure] is None else f"{g[measure]:.1f}%"
            rows.append(f"  {g['date']}  {_period_side(g)} {_period_cell(g[made_column])}-{_period_cell(g[attempted_column]):<4} {pct:>6}")
        return f"  {period_label} {PERIOD_RATE_SHOTS[measure]} made-attempted, {label}:\n" + "\n".join(rows)
    if not full_line:
        rows = [f"  {g['date']}  {_period_side(g)} {_period_cell(g[measure]):>3}" for g in games]
        return f"  {period_label} {period_noun(measure, 2)}, {label}:\n" + "\n".join(rows)
    heading = f"  {'date':<10}  {'':<27} {'FG':>5} {'FT':>5} " + " ".join(f"{name:>3}" for name in _PERIOD_LOG_HEADINGS)
    rows = []
    for g in games:
        line = g["line"]
        fg = f"{_period_cell(line['fieldGoalsMade'])}-{_period_cell(line['fieldGoalsAttempted'])}"
        ft = f"{_period_cell(line['freeThrowsMade'])}-{_period_cell(line['freeThrowsAttempted'])}"
        figures = " ".join(f"{_period_cell(line[column]):>3}" for column in PERIOD_LOG_COLUMNS)
        rows.append(f"  {g['date']}  {_period_side(g)} {fg:>5} {ft:>5} {figures}")
    return f"  {period_label} line, {label}:\n{heading}\n" + "\n".join(rows)


def _period_header(result: Result, games: list[dict[str, Any]], season_label: str, vs: str, at: str) -> str:
    """The headline: one game's own wording when there is only one, the
    recent-games log appended when the question asked for one ("log", "by
    game", "each game"), or the plain season figure otherwise. The figure
    stays over EVERY game; the log is the newest N (or the first N, for
    ``order`` "first"), and says which."""
    facts, summary = result.facts, result.rows.summary if result.rows is not None else {}
    measure, period_label, subject = facts["stat"], facts["period"], result.subject
    plural = "game" if len(games) == 1 else "games"
    total = summary["total"]
    if measure in PERIOD_RATE_WORDS:
        did = period_rate_said(total, summary["attempted"], summary["average"], measure)
        header = f"{subject} {did} in the {period_label} over {len(games)} {plural} of the {season_label}{vs}{at}."
    else:
        did = f"scored {total} points" if measure == "points" else f"had {total} {period_noun(measure, total)}"
        header = f"{subject} {did} in the {period_label} over {len(games)} {plural} of the {season_label}{vs}{at}, averaging {summary['average']:.1f}."
    if len(games) == 1:
        g = games[0]
        against = f"the {g['opponent']}" if g["opponent"] else "their opponent"
        return f"{subject} {did} in the {period_label} {'vs' if g['home_away'] == 'home' else 'at'} {against} on {g['date']} ({season_label})."
    if facts["per_game"]:
        count = _clamp_limit(facts["limit"], default=DEFAULT_GAME_LOG_LIMIT)
        earliest = facts["order"] == "first"
        shown = games[:count] if earliest else games[-count:]
        label = "every game" if len(shown) == len(games) else f"the {len(shown)} {'earliest' if earliest else 'most recent'}"
        header += "\n" + _period_log(shown if earliest else list(reversed(shown)), period_label, label, measure, full_line=facts["full_line"])
    return header


def say_period_split(result: Result) -> TemplateResult:
    """A player's figure in one quarter or half, worded: the heading over
    every game (one game said its own way), the log beneath where one was
    asked for, the season the games were found in when none were this
    season, and the season's measured agreement.

    .. versionadded:: 5.0.0
    """
    body = result.rows
    assert body is not None and result.span.season is not None
    season_label = season_phrase(result.span.season, result.span.season_type or 2)
    vs = f" against the {result.narrowing.opponent}" if result.narrowing.opponent else ""
    at = _period_where_said(result)
    games = [dict(g) for g in body.rows]
    data: dict[str, Any] = {**_period_about(result), "games": games, "games_played": len(games)}
    if not games:
        message = _period_none_found(result, season_label, vs, at)
        return TemplateResult(data={**data, "message": message, "headline": message}, answer=message)
    data |= dict(body.summary)
    header = _period_header(result, games, season_label, vs, at)
    extra = None
    for each in result.decisions:
        extra = decision_phrase(each, games=len(games), at=at, season_label=season_label)
    caveat = period_caveat(list(result.notes))
    data["headline"] = header.split("\n")[0]
    data["notes"] = [*([extra] if extra else []), *([caveat.strip()] if caveat else [])]
    return TemplateResult(data=data, answer=header + (f"\n{extra}" if extra else "") + caveat)


def _period_quarter_cells(values: list[str]) -> str:
    return "".join(f"{value:>9}" for value in values[:-1]) + f"{values[-1]:>12}"


def _period_quarter_table(result: Result, quarters: list[dict[str, Any]], season_label: str, vs: str, at: str) -> tuple[str, list[str]]:
    """The four quarters' header and two-row table: per game and total (a
    rate: percentage and made-attempted) in each quarter, then in
    regulation - the four together."""
    measure, games = result.facts["stat"], result.facts["games"]
    heads = _period_quarter_cells([f"Q{q['quarter']}" for q in quarters] + ["regulation"])
    if measure in PERIOD_RATE_WORDS:
        made, attempted = sum(q["made"] for q in quarters), sum(q["attempted"] for q in quarters)

        def _pct(pct: float | None) -> str:
            return "-" if pct is None else f"{pct:.1f}%"

        pct_row = _period_quarter_cells([_pct(q["pct"]) for q in quarters] + [_pct(made * 100.0 / attempted if attempted else None)])
        made_row = _period_quarter_cells([f"{q['made']}-{q['attempted']}" for q in quarters] + [f"{made}-{attempted}"])
        header = f"{result.subject}, {PERIOD_RATE_WORDS[measure]} by quarter in the {season_label}{vs}{at} ({games} games):"
        return header, [f"  {'':<10}{heads}", f"  {'percentage':<10}{pct_row}", f"  {'made-att':<10}{made_row}"]

    def _avg(average: float | None) -> str:
        return "-" if average is None else f"{average:.1f}"

    noun = "points" if measure == "points" else period_noun(measure, 2)
    per_game = _period_quarter_cells([_avg(q["average"]) for q in quarters] + [_avg(sum(q["average"] or 0.0 for q in quarters))])
    totals = _period_quarter_cells([str(q["total"]) for q in quarters] + [str(sum(q["total"] for q in quarters))])
    header = f"{result.subject}, {noun} per game by quarter in the {season_label}{vs}{at} ({games} games):"
    return header, [f"  {'':<10}{heads}", f"  {'per game':<10}{per_game}", f"  {'total':<10}{totals}"]


def say_period_by_quarter(result: Result) -> TemplateResult:
    """A player's four quarters side by side, worded: the header naming the
    games (the same in every quarter, said once), the per-game and total
    rows, that overtime is no quarter, and the season's measured agreement.

    .. versionadded:: 5.0.0
    """
    groups = result.grouped
    assert groups is not None and result.span.season is not None
    facts = result.facts
    season_label = season_phrase(result.span.season, result.span.season_type or 2)
    vs = f" against the {result.narrowing.opponent}" if result.narrowing.opponent else ""
    at = _period_where_said(result)
    if facts["window"] is not None:
        # The compiler's window cut these games (the same N in every
        # quarter): `Narrowed.filters(windowed=True)`'s own phrase.
        order, n = facts["window"]
        at += f" over his {'last' if order == 'recent' else 'first'} {n} game{'s' if n != 1 else ''}"
    quarters = [dict(row) for row in groups.rows]
    data: dict[str, Any] = {**_period_about(result), "games_played": facts["games"], "quarters": quarters}
    if not facts["games"]:
        message = _period_none_found(result, season_label, vs, at)
        return TemplateResult(data={**data, "message": message, "headline": message}, answer=message)
    header, table = _period_quarter_table(result, quarters, season_label, vs, at)
    overtime, *agreement = result.notes
    said = note(overtime.kind, note_phrase(overtime), **overtime.facts)
    caveat = period_caveat(agreement)
    data |= {"headline": header.rstrip(":"), "notes": [said, *([caveat.strip()] if caveat else [])]}
    return TemplateResult(data=data, answer="\n".join([header, *table, f"  {said}"]) + caveat)


# --- a count of games over a line, and a single game's high ---------------------------


def _counted_span_words(result: Result) -> tuple[str, str, str]:
    """How an answer read from box scores names the games it covers, from
    the span's values: the clause that follows a verb ("in the 2026 regular
    season", "in his regular season career (2018-19 through 2025-26)"), the
    caption the page shows, and the games' own name for "no ... in the
    warehouse". A career that began before the box scores, and the
    league's, are named from the box scores' first season; a named career
    from his own first and last."""
    span, facts = result.span, result.facts
    # Not ``or 2``: 0 is both season types at once, a value of its own.
    season_type = 2 if span.season_type is None else span.season_type
    kind = SEASON_TYPE_NAMES.get(season_type, "regular season")
    since = season_label(facts["box_scores_from"])
    ordinal = facts.get("ordinal")
    if span.season is not None:
        period = season_phrase(span.season, season_type)
        if ordinal is not None:
            return f"in his {ordinal_word(ordinal)} season ({period})", f"{ordinal_word(ordinal)} season, {period}", f"{period} games"
        return f"in the {period}", period, f"{period} games"
    if result.relation == "everyone" or (span.first is not None and span.first < facts["box_scores_from"]):
        return f"in the {kind} since {since}", f"{kind} since {since}", f"{kind} games since {since}"
    years = f" ({season_label(span.first)} through {season_label(span.last)})" if span.first is not None and span.last is not None else ""
    return f"in his {kind} career{years}", f"{kind} career{years}", f"{kind} games"


def _threshold_count_phrase(rows: list[tuple[Any, int, int]], scope: str, when: str, player: str | None) -> str:
    """Always names the season outright rather than echoing "this season" back.
    The original failure answered for 2024 while the user meant the current
    season, and said nothing about it - so the season is stated, every time.
    ``when`` is that statement: one season, or a career and where it starts."""
    label = f"games with {scope}"
    if player is not None:
        games = rows[0][1] if rows else 0
        one = f"game with {scope}"  # "had 1 game", not "1 games"
        return f"{player} had {games} {one if games == 1 else label} {when}." if games else f"{player} had no {label} {when}."
    if not rows:
        return f"No player had a game with {scope} {when}."
    top = rows[0][1]
    tied = [name for name, games, _ in rows if games == top]
    if len(tied) > 1:
        leaders = ", ".join(tied[:-1]) + f" and {tied[-1]}"
        sentence = f"{leaders} tied for the most {label} {when}, with {top} each."
    else:
        sentence = f"{rows[0][0]} had the most {label} {when}, with {top}."
    rest = [f"{name} ({games})" for name, games, _ in rows if games != top]
    return sentence + (f" Next: {', '.join(rest)}." if rest else "")


def _threshold_count_rows(result: Result) -> list[tuple[Any, int, int]]:
    """The count as ``(name, games, rebuilt games among them)``, most first:
    the named player's one row where he has any, else the league's."""
    line, groups = result.scalar, result.grouped
    if line is not None:
        return [(result.subject, line.games, int(line.sums.get("rebuilt") or 0))] if line.games else []
    assert groups is not None
    return [(row["key"], int(row["games"]), int(row["rebuilt"])) for row in groups.rows]


def say_threshold_count(result: Result) -> TemplateResult:
    """How many games cleared a line, worded - a named player's count, or
    the league's leaders with "Next: ..." - with the floor said first where
    his career began before the box scores, and the notes after it in the
    retired template's order: a league career is not all-time, a withheld
    stat (else the empty box scores), the leader's rebuilt games.

    .. versionadded:: 5.0.0
    """
    facts = result.facts
    named = result.relation == "player"
    stat = facts["stat"]
    label = STAT_LABELS.get(stat or "", stat or "")
    counted = facts["counted"]
    scope_text = " and ".join(([f"{counted}+ {label}s"] if counted else []) + list(facts["lines"]))
    when, caption, _games = _counted_span_words(result)
    rows = _threshold_count_rows(result)
    preface, notes = "", []
    for each in result.notes:
        said = note(each.kind, note_phrase(each, consequence="the count may be low" if named else "these counts may be low"), **each.facts)
        if each.kind == "floor" and each.facts.get("what") == "career_began_earlier":
            preface = said
        else:
            notes.append(said.strip() if each.kind in ("games_unseen", "lines_rebuilt") else said)
    phrase = preface + _threshold_count_phrase(rows, scope_text, when, result.subject if named else None)
    season = result.span.season
    data = {
        "question_shape": f"games with {scope_text}, {caption}",
        "season": season,
        "span": "career" if season is None else None,
        "leaders": [{"player": name, "games": games} for name, games, _ in rows],
        "empty_box_scores": facts["empty_box_scores"],
        "rebuilt_games": rows[0][2] if rows else 0,
        # The trailing "Next: ..." restates the table in prose - not the headline.
        "headline": phrase.split(" Next: ")[0],
        "notes": notes,
    }
    return TemplateResult(data=data, answer=" ".join([phrase, *notes]))


def _single_game_high_phrase(result: Result, games: list[dict[str, Any]], label: str, when: str, games_said: str, withheld: str) -> str:
    """The high in one sentence - a named player's top game, or the league's
    leader with any tie said as a tie and "Next: ..." - or, with no games,
    which games are missing: none at all, none with a box score (the empty
    box scores' note then gives the count and the years), or a stat
    withheld from the rebuilt lines that hold them (``withheld``, its note,
    already phrased)."""
    named = result.subject if result.relation == "player" else None
    if not games:
        who = f"{named} has" if named else "There are"
        # A stat outside the rebuilt set with rebuilt lines in scope is a
        # DECISION, not a gap; "no games" said of a player who played 68 of
        # them with empty box scores is the wrong-cause refusal.
        if withheld:
            return f"{who} no {games_said} with a box score in the warehouse. " + withheld
        if result.facts["empty_box_scores"]:
            return f"{who} no {games_said} with a box score in the warehouse."
        return f"{who} no {games_said} in the warehouse."
    top = games[0]
    where = f" vs {top['opponent']}" if top["opponent"] else ""
    if named:
        return f"{named}'s highest {label} total in a single game {when} was {top['value']}, on {top['date']}{where}."
    tied = [g for g in games if g["value"] == top["value"]]
    if len(tied) > 1:
        names = ", ".join(g["player"] for g in tied[:-1]) + f" and {tied[-1]['player']}"
        sentence = f"{names} tied for the most {label}s in a single game {when}, with {top['value']} each."
    else:
        sentence = f"{top['player']} had the most {label}s in a single game {when}: {top['value']}, on {top['date']}{where}."
    rest = [f"{g['player']} ({g['value']})" for g in games if g["value"] != top["value"]]
    return sentence + (f" Next: {', '.join(rest)}." if rest else "")


def say_single_game_high(result: Result) -> TemplateResult:
    """A single game's high, worded - the retired ``single_game_high``
    template's sentence: the floor first where a career began before the
    box scores, the high, then a league career's floor, the empty box
    scores and a rebuilt top game, and the defaulted season's redirect on
    the same line (and in ``data["notes"]``, since the page's caption is
    ``question_shape`` and has no other way to reach it).

    .. versionadded:: 5.0.0
    """
    body = result.rows
    assert body is not None
    games = [dict(g) for g in body.rows]
    stat = result.facts["stat"]
    label = STAT_LABELS.get(stat or "", stat or "")
    when, caption, games_said = _counted_span_words(result)
    said: dict[str, str] = {}
    for each in result.notes:
        consequence = "a bigger game may be missing" if games else "there is no per-game high to read from them"
        text = note(each.kind, note_phrase(each, consequence=consequence), **each.facts)
        said["preface" if each.kind == "floor" and each.facts.get("what") == "career_began_earlier" else each.kind] = text
    headline = (
        said.get("preface", "")
        + _single_game_high_phrase(result, games, label, when, games_said, said.get("stat_withheld", ""))
        + said.get("floor", "")
        + said.get("games_unseen", "")
        + said.get("lines_rebuilt", "")
    )
    redirect = "".join(decision_phrase(each) for each in result.decisions)
    named = result.subject if result.relation == "player" else None
    season = result.span.season
    data = {
        "question_shape": f"most {label}s in a single game" + (f", {named}" if named else "") + f", {caption}",
        "season": season,
        "span": "career" if season is None else None,
        "stat": stat,
        "games": games,
        "empty_box_scores": result.facts["empty_box_scores"],
        "headline": headline,
        "notes": [redirect.strip()] if redirect else [],
    }
    return TemplateResult(data=data, answer=headline + redirect)


# --- runs ------------------------------------------------------------------------------


def streak_result(want_win: bool) -> str:
    """What a run of results is called: "winning streak" or "losing streak".

    .. versionadded:: 5.0.0
    """
    return "winning streak" if want_win else "losing streak"


def say_one_run(subject: str, label: str, runs: Sequence[Run], rule: str, *, season: int | None, still_open: bool, who: dict[str, Any]) -> TemplateResult:
    """One named player's or team's longest run, with any run that ties it,
    under ``subject`` ("Nikola Jokic's longest run of consecutive games with
    20+ points") and ``label`` (the span): its length and days, the season
    it fell in where the span covers several, and - ``still_open`` - that
    it was still going at the last game on record. ``rule`` is the
    definition beneath it, already phrased. A team's streak (``streak``'s
    team branch, ``templates.splits._streak_team_answer``) is said here
    too, until the team slice.

    .. versionadded:: 5.0.0
    """
    top = runs[0]
    ties = [r for r in runs[1:] if r.length == top.length]
    in_season = f" (the {top.first_season} season)" if season is None and top.first_season == top.last_season else ""
    games = "game" if top.length == 1 else "games"
    answer = f"{subject}, {label}: {top.length} {games}, {top.first} to {top.last}{in_season}."
    if ties:
        answer += " Matched by " + ", ".join(f"{r.first} to {r.last}" for r in ties) + "."
    if still_open:
        still = Note("still_open")
        answer += note(still.kind, note_phrase(still))
    streaks = [{"length": r.length, "from": str(r.first), "to": str(r.last), "open": r.still_open} for r in [top, *ties]]
    return TemplateResult(data={**who, "span": label, "streaks": streaks, "headline": answer, "notes": [rule.strip()]}, answer=f"{answer}\n{rule}")


def say_run_listing(runs: Sequence[Run], what: str, rule: str, label: str, where: str, *, by_stat: bool, stat: Any, threshold: Any, unit: str, want_win: bool) -> TemplateResult:
    """The league's longest runs with nobody named: one per player (a stat
    streak) or one per team-season (a win/loss streak, the team relation's,
    ``compose.present._present_team_streak``), each under its ``owner``, a
    tie reported as a tie, and the rule and the open-run footnote under the
    table - carried in ``data["notes"]`` too, so the web page shows them
    under the rendered table (rendered, the "*" beside a run had no key
    anywhere on screen - Jeff's note, 2026-09-24). ``where`` names a span
    with no run in it ("in the 2026 regular season").

    .. versionadded:: 5.0.0
    """
    if not runs:
        nobody = f"No player had a game with {threshold}+ {unit}" if by_stat else "No team has a game with a result"
        message = f"{nobody} {where}."
        return TemplateResult(data={"span": label, "streaks": [], "headline": message}, answer=message)
    streaks = [{"name": r.owner, "season": r.first_season if not by_stat else None, "length": r.length, "from": str(r.first), "to": str(r.last), "open": r.still_open} for r in runs]
    longest = runs[0].length
    top = [r for r in runs if r.length == longest]
    leaders = " and ".join(str(r.owner) for r in top)
    headline = f"{leaders} {'shared' if len(top) > 1 else 'had'} the longest {what} of the {label}: {longest} {'game' if longest == 1 else 'games'}."
    rows = [(str(r.owner), [str(r.length), str(r.first), str(r.last) + (" *" if r.still_open else "")]) for r in runs]
    still = Note("still_open")
    footnote = note(still.kind, note_phrase(still, listed=True)) if any(r.still_open for r in runs) else ""
    answer = f"{headline}\n" + _table(f"Longest, {label}:", ["games", "from", "to"], rows) + f"\n{rule}{footnote}"
    data = {
        "span": label,
        "stat": stat if by_stat else None,
        "threshold": threshold if by_stat else None,
        "kind": None if by_stat else ("win" if want_win else "loss"),
        "streaks": streaks,
        "headline": headline,
        "notes": [rule.strip(), *([footnote.strip()] if footnote else [])],
    }
    return TemplateResult(data=data, answer=answer)


def _where_in_span(span: Span) -> str:
    """A span with no run in it, in words: the season, or every season of
    its type from the relation's floor on."""
    return f"in the {span.phrase}" if span.season is not None else f"in any {SEASON_TYPE_NAMES.get(span.season_type or 2, 'regular season')} on record ({span.floor} onward)"


def say_streak(result: Result) -> TemplateResult:
    """A player's longest run, or the league's longest one per player,
    worded: the run (and any that tie it) under the player's name and the
    span, or the listing under its leader, then the rule beneath - only
    games he played count, and a game with no box score ends a run - and
    whether a run is still going.

    .. versionadded:: 5.0.0
    """
    body = result.runs
    assert body is not None and result.span.phrase is not None
    facts = result.facts
    stat, threshold, by_stat, want_win = facts["stat"], facts["threshold"], facts["by_stat"], facts["want_win"]
    unit = _unit(stat)
    label = result.span.phrase
    rule = "".join(note(each.kind, note_phrase(each), **each.facts) for each in result.notes if each.kind == "definition")
    if result.relation == "everyone":
        what = f"run of consecutive games with {threshold}+ {unit}"
        return say_run_listing(body.runs, what, rule, label, _where_in_span(result.span), by_stat=True, stat=stat, threshold=threshold, unit=unit, want_win=want_win)
    filters = result.narrowing.phrase
    if not body.runs:
        never = f"never had a game with {threshold}+ {unit}" if by_stat else f"never {'won' if want_win else 'lost'} a game he played"
        message = f"{result.subject} {never}{filters} in the {label}."
        return TemplateResult(data={"player": result.subject, "span": label, "streaks": [], "headline": message}, answer=message)
    what = f"consecutive games with {threshold}+ {unit}" if by_stat else f"{streak_result(want_win)} in games he played"
    subject = (f"{result.subject}'s longest run of {what}" if by_stat else f"{result.subject}'s longest {what}") + filters
    still_open = any(each.kind == "still_open" for each in result.notes)
    return say_one_run(subject, label, body.runs, rule, season=result.span.season, still_open=still_open, who={"player": result.subject})


# --- two players' meetings -------------------------------------------------------------

#: The comparison's rows beneath the record: each player's per-game figure over the meetings, by key and header.
_MATCHUP_LINE: tuple[tuple[str, str], ...] = (("minutes", "minutes"), ("points", "points"), ("rebounds", "rebounds"), ("assists", "assists"), ("fg_pct", "FG%"))


def _matchup_absence_said(absence: dict[str, Any] | None) -> str:
    """How often the two met with a teammate's absence dropped, where it
    emptied the meetings - the reading the question probably meant."""
    if absence is None:
        return ""
    names, met, player = _joined(list(absence["names"])), absence["met"], absence["player"]
    return (
        f" Over {absence['first']}-{absence['last']} they met {met} time{'s' if met != 1 else ''} in all, {absence['beside']} of them with {names} playing beside {player}; "
        f"'without {names}' counts only the games he missed while on {player}'s team, and there were none among their meetings."
    )


def _matchup_none(result: Result, caveat: str) -> TemplateResult:
    """Two players who never met in scope, said by what the read narrowed:
    "never played against each other", or no meetings in the first
    player's games narrowed that way, and the games they shared as
    teammates where every shared game was one."""
    a, b, together = result.subject, result.facts["other"], result.facts["teammate_games"]
    where = _where_in_span(result.span)
    teammates = f" - they were teammates in all {together} games they both played" if together else ""
    said = caveat + _matchup_absence_said(result.facts.get("absence"))
    narrowing = result.narrowing.phrase
    # With a narrowing, "never played against each other" would be false of
    # two players who met whenever the narrowing was not in force.
    head = f"No meetings between {a} and {b} in {a}'s games{narrowing}" if narrowing else f"{a} and {b} never played against each other"
    message = f"{head} {where}{teammates}.{said}"
    return TemplateResult(data={"players": [a, b], "meetings": 0, "teammate_games": together, "headline": message}, answer=message)


def say_player_matchup(result: Result) -> TemplateResult:
    """Two players' meetings, worded: the head-to-head record and each
    one's averages side by side under a heading naming how many times they
    met and how often the first one's team won, then the newest meetings
    (each team as it was abbreviated that season, both players'
    points/rebounds/assists), then the games no box score shows.

    .. versionadded:: 5.0.0
    """
    groups = result.grouped
    assert groups is not None and result.span.phrase is not None
    caveat = "".join(_said(result))
    if not groups.rows:
        return _matchup_none(result, caveat)
    a, b = groups.rows
    detail = result.parts[1].body
    assert isinstance(detail, Rows) and detail.total_before_window is not None
    count, wins = detail.total_before_window, a["wins"]
    names = [a["key"], b["key"]]
    title = f"{names[0]} vs {names[1]}{result.narrowing.phrase}, {result.span.phrase}: {count} meeting{'' if count == 1 else 's'}, {names[0]}'s team won {wins}."
    summary = [("wins", [str(wins), str(count - wins)])] + [(header, [_cell(line[key]) for line in (a, b)]) for key, header in _MATCHUP_LINE]
    answer = _table(title, names, summary)
    log = [
        (str(m["day"]), [f"{m['team']} {m['team_score']}-{m['opponent_score']} {m['opponent']}", *(f"{m[side]['points']}/{m[side]['rebounds']}/{m[side]['assists']}" for side in ("a", "b"))])
        for m in detail.rows
    ]
    answer += "\n\n" + _table(f"Most recent {len(detail.rows)} of {count} (points/rebounds/assists):", ["score", *names], log)
    answer += f"\n{caveat.strip()}" if caveat else ""
    games = [{"date": str(m["day"]), "won": m["won"], "team_score": m["team_score"], "opponent_score": m["opponent_score"], names[0]: m["a"], names[1]: m["b"]} for m in detail.rows]
    averages = {line["key"]: {k: v for k, v in line.items() if k not in ("key", "wins")} for line in (a, b)}
    data = {
        "players": names,
        "span": result.span.phrase,
        "meetings": count,
        "wins": {names[0]: wins, names[1]: count - wins},
        "averages": averages,
        "games": games,
        "headline": title,
        "notes": [caveat.strip()] if caveat else [],
    }
    return TemplateResult(data=data, answer=answer)


# --- a ranking over the season line ------------------------------------------------


def _leaderboard_rows(body: Grouped) -> list[dict[str, Any]]:
    """A ranking's rows as the answer's ``leaders`` carry them: the name, the
    ranked figure as ``value``, then the row's other columns in order."""
    measure = body.ranked_by or ""
    return [{"display_name": row["key"], "value": row["values"][measure], **{k: v for k, v in row["values"].items() if k != measure}} for row in body.rows]


def _leader_value(row: dict[str, Any], ratio: Sequence[str] | None, *, short: bool = False) -> str:
    """A ranked value as a reader expects it: a percentage as one, with the
    makes and attempts behind it - the "out of how many?" a bare percentage
    always draws - and a count with its thousands separated."""
    value = row.get("value")
    if value is None:
        return "-"
    if ratio:
        made, attempted = row.get(ratio[0]), row.get(ratio[1])
        text = f"{value * 100:.1f}%"
        return text if short or made is None or attempted is None else f"{text} ({int(made):,} of {int(attempted):,})"
    if isinstance(value, int):
        return f"{value:,}"
    # ESPN's averages carry one decimal, and "4" beside "3.8" reads as a count.
    return f"{value:.1f}" if isinstance(value, float) and value == round(value, 1) and abs(value) >= 1 else format_value(value)


def _leaderboard_minimum(result: Result) -> tuple[Decided | None, Any]:
    """The ranking's qualifier decision and the minimum it applied (``None``
    where the metric applies none)."""
    minimum = next((each for each in result.decisions if each.kind == "minimum"), None)
    return minimum, (minimum.chose if minimum is not None else None)


def _leaderboard_qualifier(minimum: Decided | None) -> str:
    """ " (minimum 200 3-point attempts)", or nothing. Shown because it answers
    "why isn't X here?" before it is asked - and makes an empty early-season
    board say why it is empty."""
    if minimum is None or not minimum.chose:
        return ""
    return decision_phrase(minimum)


def _phrase_leaderboard(rows: list[dict[str, Any]], label: str, where: str, period: str, qualifier: str, ratio: Sequence[str] | None) -> str:
    if not rows:
        return f"No players qualified for {label} in {where} in the {period}{qualifier}."
    top = rows[0]
    sentence = f"{top['display_name']} led {where} in {label} in the {period}{qualifier}, at {_leader_value(top, ratio)}."
    rest = [f"{r['display_name']} ({_leader_value(r, ratio, short=True)})" for r in rows[1:]]
    return sentence + (f" Next: {', '.join(rest)}." if rest else "")


def _tabulate_leaderboard(rows: list[dict[str, Any]], label: str, where: str, period: str, fields: list[str], header_note: str, ratio: Sequence[str] | None) -> str:
    """A table once extra columns are asked for - a sentence carrying three
    numbers per player across ten players is unreadable, and the qualifying
    minimum belongs on screen so "why isn't X here?" has a visible answer."""
    if not rows:
        return f"No players qualified for {label} in {where} in the {period}{header_note}."
    columns = [(label, "value")] + [(f, f) for f in fields]
    name_width = max(len(r["display_name"]) for r in rows)
    # The ranked metric keeps its own precision (9.91, not 9.9); the extra
    # box-score columns are per-game averages, where one decimal is the norm.
    cell = lambda row, key: _leader_value(row, ratio, short=True) if key == "value" else table_cell(row.get(key))  # noqa: E731
    widths = [max(len(title), *(len(cell(r, key)) for r in rows)) for title, key in columns]
    lines = [f"{label}, {where}, {period}{header_note}:"]
    lines.append(" " * name_width + "  " + "  ".join(t.rjust(w) for (t, _), w in zip(columns, widths, strict=True)))
    for row in rows:
        cells = "  ".join(cell(row, key).rjust(w) for (_, key), w in zip(columns, widths, strict=True))
        lines.append(f"{row['display_name'].ljust(name_width)}  {cells}")
    return "\n".join(lines)


def _leaderboard_headline(answer: str) -> str:
    """The answer's first line, without the trailing "Next: ..." list - the
    same way the page's own fallback reads it (``firstLine``,
    ``web/static/index.html``)."""
    return answer.split("\n")[0].rstrip(":").split(" Next: ")[0]


def say_leaderboard(result: Result) -> TemplateResult:
    """A ranking over the season line, worded as the retired template
    worded it (``templates.players._leaderboard_ranking``): the leader and
    the next names in a sentence, or a table once "also" columns were asked
    for, the qualifier in either, a career's pool said every time, and the
    most-recent-team remark on its own line beneath a team column.

    .. versionadded:: 5.0.0
    """
    body = result.grouped
    assert body is not None
    if result.span.career:
        return _say_career_leaderboard(result, body)
    facts = result.facts
    label, fields, ratio = facts["label"], list(facts["fields"]), facts["ratio"]
    rows = _leaderboard_rows(body)
    season = result.span.season
    assert season is not None
    period = season_phrase(season, result.span.season_type or 2)
    where = f"the {facts['team']}" if facts["team"] else "the league"
    # The remark beneath the table is written before the table's qualifier,
    # in the order the template wrote them.
    trade_note = "".join(note(each.kind, note_phrase(each), **each.facts) for each in result.notes)
    minimum, applied = _leaderboard_minimum(result)
    qualifier = _leaderboard_qualifier(minimum)
    answer = _tabulate_leaderboard(rows, label, where, period, fields, qualifier, ratio) if fields else _phrase_leaderboard(rows, label, where, period, qualifier, ratio)
    data = {
        "question_shape": f"{label}, {period}",
        "season": season,
        "fields": fields,
        "min_sample": applied,
        "leaders": rows,
        "headline": _leaderboard_headline(answer),
        "notes": [trade_note.strip()] if trade_note else [],
    }
    return TemplateResult(data=data, answer=answer + trade_note)


def _say_career_leaderboard(result: Result, body: Grouped) -> TemplateResult:
    """A career ranking. Says whose careers, every time: the pool is every
    player active in 1993-94 or later, counted over his whole career, and
    nobody whose career ended before it - Kareem Abdul-Jabbar is not in the
    warehouse at all - so presenting it as "all-time" would be the
    unrepresentative ranking ``nba/coverage.py``'s second floor exists to
    refuse."""
    rows = _leaderboard_rows(body)
    ratio = result.facts["ratio"]
    kind = SEASON_TYPE_NAMES.get(result.span.season_type or 2, "regular season")
    label = f"career {result.facts['label'].removeprefix('total ')}"
    pool = result.span.first
    assert pool is not None
    since = season_label(pool)
    minimum, applied = _leaderboard_minimum(result)
    qualifier = _leaderboard_qualifier(minimum)
    gap = "".join(note(each.kind, note_phrase(each), **each.facts) for each in result.notes)
    if not rows:
        answer = f"No player qualified for {label} in the {kind}{qualifier}. {gap}"
    else:
        top = rows[0]
        years = f"{season_label(top['first_season'])} through {season_label(top['last_season'])}"
        detail = f", over {int(top['games']):,} games ({years})" if top.get("games") else ""
        sentence = f"Among players active in {since} or later, {top['display_name']} leads in {label} in the {kind}{qualifier}: {_leader_value(top, ratio)}{detail}."
        rest = [f"{r['display_name']} ({_leader_value(r, ratio, short=True)})" for r in rows[1:]]
        answer = " ".join([sentence, *([f"Next: {', '.join(rest)}."] if rest else []), gap])
    return TemplateResult(
        data={
            "question_shape": f"{label}, {kind}, players active since {since}",
            "season": None,
            "span": "career",
            "pool_first_season": pool,
            "fields": [],
            "min_sample": applied,
            "leaders": rows,
            "headline": _leaderboard_headline(answer),
        },
        answer=answer,
    )


# --- the season line: a player's line, a history, a comparison ------------------------


def _say_seasons_missing(facts: dict[str, Any]) -> str:
    """Seasons an advanced stat's career could not see. Silence here would
    be the failure this project keeps producing: a precise number over a
    span it does not actually cover, printed as fluently as a complete one.
    The seasons are not scattered games - ESPN serves whole team-seasons of
    empty box scores from 2013 to 2018 (``DATA.md``), and a player who
    spent them on Chicago or New Orleans has nothing at all for those
    years."""
    missing = facts["seasons"]
    subject = "season in that span is" if missing == 1 else "seasons in that span are"
    them = "it" if missing == 1 else "them"
    return f" {missing} {subject} not counted: ESPN's box scores for {them} are empty, so no {facts['label']} can be computed from {them}."


def _advanced_value(percentage: bool, value: Any) -> str:
    """One advanced figure as the sentence prints it. A percentage is
    printed as a three-decimal fraction (``.622``), the way a shooting line
    reads, because these are stored as 0-1 fractions; usage is already a
    0-100 rate and game score is a raw composite, so both take one
    decimal."""
    if percentage:
        return f"{float(value):.3f}".lstrip("0")
    return f"{float(value):.1f}"


def _say_player_line_advanced(result: Result, line: Scalar) -> TemplateResult:
    """A computed advanced stat's line: the figure, its volume - a rate
    without it is the thing people ask "out of how many?" about - the
    games, and the seasons it could not see."""
    stat, span, name = result.facts["stat"], result.span, result.subject
    spec = ADVANCED_STATS[stat]
    if not line.values:
        message = f"{name} has no {spec.label} on record {span.phrase} - it is computed from box scores, which start in 1994."
        return TemplateResult(data={"player": name, "stat": stat, "stats": {}}, answer=message)
    value, volume, games = line.values[stat], line.sums["volume"], line.games
    printed = _advanced_value(spec.percentage, value)
    behind = f" on {int(volume):,} {spec.volume}" if volume is not None and spec.volume else ""
    about: dict[str, Any] = {"span": "career", "seasons": [span.first, span.last], "season_count": result.facts["season_count"]} if span.career else {"season": span.first}
    sentence = f"{name} has a {printed} {spec.label} {span.phrase}{behind}, in {int(games):,} games." if games else f"{name} has a {printed} {spec.label} {span.phrase}{behind}."
    data = {"player": name, "stat": stat, **about, "stats": {spec.column: value, "games_played": int(games) if games is not None else None}, "seasons_missing": line.sums["seasons_missing"]}
    return TemplateResult(data=data, answer=sentence + "".join(_said(result)))


def _player_line_values(result: Result, line: Scalar, wanted: list[str]) -> tuple[dict[str, Any], Any]:
    """The line as ``data["stats"]`` holds it, by column: the games, each
    wanted stat's per-game figure and total, a made count's attempts - as
    stored for a season, rounded as the retired template rounded a summed
    career - and those attempts, for the sentence."""
    career = result.span.career
    values: dict[str, Any] = {"gamesPlayed": line.games}
    for stat in wanted:
        per_game_col, total_col, _ = PLAYER_STAT_COLUMNS[stat]
        values[per_game_col] = rounded(line.values[stat]) if career else line.values[stat]
        if total_col and stat in line.sums:
            values[total_col] = round(line.sums[stat]) if career else line.sums[stat]
    attempted_col = MADE_STAT_ATTEMPTS.get(wanted[0]) if len(wanted) == 1 else None
    attempted = line.sums.get(attempted_col) if attempted_col else None
    if attempted_col and attempted_col in line.sums:
        values[attempted_col] = int(line.sums[attempted_col]) if career else line.sums[attempted_col]
    return values, attempted


def _player_line_empty(result: Result) -> TemplateResult:
    """No line on record: the season's or the career's sentence, and where
    the season was defaulted, the seasons he IS on record for."""
    span, name = result.span, result.subject
    if span.career:
        kind = SEASON_TYPE_NAMES.get(span.season_type or 2, "regular season")
        return TemplateResult(data={"player": name, "span": "career", "stats": {}}, answer=f"{name} has no {kind} numbers in the warehouse.")
    assert span.season is not None
    answer = f"{name} has no {season_phrase(span.season, span.season_type or 2)} numbers in the warehouse." + "".join(decision_phrase(each) for each in result.decisions)
    return TemplateResult(data={"player": name, "season": span.season, "stats": {}}, answer=answer)


def _say_season_line(result: Result) -> TemplateResult:
    """The season line's own shapes, by body: a player's line, a history by
    season, a comparison of players."""
    if result.scalar is not None:
        return say_player_line(result)
    if result.grouped is not None and result.grouped.by == "season":
        return say_player_history(result)
    assert result.grouped is not None and result.grouped.by == "subject", "a season-line result with no season-line shape"
    return say_player_compare(result)


def say_player_line(result: Result) -> TemplateResult:
    """A player's unnarrowed line from the season line, worded - one
    season's ("averaged 27.7 points per game in 70 games in the 2026
    regular season"), a career's ("over his career (8 regular seasons,
    2019-2026)"), a percentage with its makes and attempts, or a computed
    advanced stat - the retired ``player_stat`` template's words for the
    season line.

    .. versionadded:: 5.0.0
    """
    line = result.scalar
    assert line is not None
    if result.facts["stat"] in ADVANCED_STATS:
        return _say_player_line_advanced(result, line)
    if not line.values and not line.sums:
        return _player_line_empty(result)
    span, name, wanted = result.span, result.subject, list(result.facts["wanted"])
    if span.career:
        seasons, kind = result.facts["season_count"], SEASON_TYPE_NAMES.get(span.season_type or 2, "regular season")
        plural = "" if seasons == 1 else "s"
        when = f"over his career ({seasons} {kind}{plural}, {span.first}-{span.last})" if span.first != span.last else f"over his career (the {span.first} {kind})"
        about: dict[str, Any] = {"span": "career", "seasons": [span.first, span.last], "season_count": seasons}
        period = f"career {kind}s"
    else:
        assert span.season is not None and span.phrase is not None
        when, about, period = span.phrase, {"season": span.season, "season_n": result.facts["season_n"]}, season_phrase(span.season, span.season_type or 2)
    stat = result.facts["stat"]
    if stat is not None:
        shooting = SHOOTING_STATS[stat]
        values = {"gamesPlayed": line.games, shooting.made: line.sums["made"], shooting.attempted: line.sums["attempted"]}
        return shooting_result(name, about, values, shooting, when=when)
    values, attempted = _player_line_values(result, line, wanted)
    answer = phrase_player_stat(name, period, values, wanted, when=when, attempted=attempted)
    return TemplateResult(data={"player": name, **about, "stats": values, "labels": stat_value_labels(wanted)}, answer=answer)


def _history_table(name: str, label: str, period: str, history: list[dict[str, Any]], columns: list[tuple[str, str, str]], *, career: bool) -> str:
    """The seasons, newest first, under a heading naming the stat, the
    season type and the seasons shown."""
    if not history:
        return f"The warehouse has no {period} seasons on record for {name}."
    headers = ["season", "G"] + [h for _, h, _ in columns]
    keys = ["season", "games"] + [k for _, _, k in columns]
    widths = [max(len(h), *(len(table_cell(row.get(k))) for row in history)) for h, k in zip(headers, keys, strict=True)]
    newest, oldest = history[0]["season"], history[-1]["season"]
    years = f"{oldest}" if oldest == newest else f"{oldest}-{newest}"
    shown = f"career, {years}" if career else years
    lines = [f"{name}, {label} by {period}, {shown} (most recent first):", "  ".join(h.rjust(w) for h, w in zip(headers, widths, strict=True))]
    lines.extend("  ".join(table_cell(row.get(k)).rjust(w) for k, w in zip(keys, widths, strict=True)) for row in history)
    return "\n".join(lines)


def _history_career_line(name: str, label: str, summary: Scalar) -> str:
    """The career line beneath a career's history: the games-weighted
    percentage from the makes and attempts summed across every season shown
    (never a mean of means, F041), or the plain career total."""
    if "total" in summary.sums:
        return f"{name}'s career total: {round(summary.sums['total']):,} {label.removesuffix(' per game')}."
    made, attempted = summary.sums["made"], summary.sums["attempted"]
    return f"{name}'s career {label}: {100.0 * made / attempted:.1f}% ({int(made):,} of {int(attempted):,})."


def say_player_history(result: Result) -> TemplateResult:
    """A player's stat season by season, worded: the aligned table under its
    heading, and under a career the career line - the retired
    ``player_history`` template's words. ``data["seasons"]`` keys each row
    by the STABLE name (``HISTORY_COLUMNS``' third element), never the SQL a
    column reads through, and ``data["labels"]`` carries each column's
    printed header, so the page need not guess one back out of a key.

    .. versionadded:: 5.0.0
    """
    groups = result.grouped
    assert groups is not None
    stat, name, span = result.facts["stat"], result.subject, result.span
    label, columns = HISTORY_COLUMNS[stat]
    period = SEASON_TYPE_NAMES.get(span.season_type or 2, "regular season")
    history = [{"season": row["key"], **{k: v for k, v in row.items() if k != "key"}} for row in groups.rows]
    answer = _history_table(name, label, period, history, columns, career=span.career)
    summary = result.parts[1].body if len(result.parts) > 1 else None
    if isinstance(summary, Scalar):
        answer += f"\n{_history_career_line(name, label, summary)}"
    labels = {k: h for _, h, k in columns}
    return TemplateResult(data={"player": name, "stat": stat, "span": "career" if span.career else None, "seasons": history, "labels": labels}, answer=answer)


def _signed_cell(value: Any) -> str:
    """A NetPoints cell. Signed, because the sign is the whole reading of it -
    an unmarked "0.42" beside "-1.10" loses which one helped their team."""
    return "-" if value is None else f"{value:+.2f}"


def _compare_table(rows: dict[str, dict[str, Any]], wanted: list[str], period: str, netpoints: dict[str, dict[str, Any]], missing_note: str) -> str:
    """A fixed-width table rather than prose. Comparisons are the one shape
    where a sentence actively hurts - the agent's prose version stated that a
    player with 0.4 steals led one with 1.6."""
    names = list(rows)
    # A fixed decimal in every cell, not format_value: in an aligned column a
    # trailing-zero-stripped "25" next to "27.7" reads as a different unit.
    entries: list[tuple[str, list[str]]] = [("games", [table_cell(rows[name].get("gamesPlayed")) for name in names])]
    for stat in wanted:
        column, _, label = PLAYER_STAT_COLUMNS[stat]
        entries.append((label, [table_cell(rows[name].get(column)) for name in names]))
    # Shown only when somebody has a row: an empty NetPoints block under a
    # comparison of two 1990s players would read as "both contributed nothing"
    # rather than "this season predates the data".
    if any(netpoints.get(name) for name in names):
        entries.append(("", ["" for _ in names]))
        for label, column in NETPOINTS_COMPARE_ROWS:
            entries.append((label, [_signed_cell(netpoints.get(name, {}).get(column)) for name in names]))
    label_width = max(len(label) for label, _ in entries)
    name_width = max(len(text) for text in (*names, *(cell for _, cells in entries for cell in cells)))
    # rstripped so the blank separator row is an empty line rather than a line
    # of spaces, which shows up as trailing whitespace wherever this is stored.
    lines = [f"{' vs '.join(names)}, {period}:", (f"{' ' * label_width}  " + "  ".join(name.rjust(name_width) for name in names)).rstrip()]
    lines += [(f"{label.ljust(label_width)}  " + "  ".join(cell.rjust(name_width) for cell in cells)).rstrip() for label, cells in entries]
    if missing_note:
        lines.append(missing_note)
    return "\n".join(lines)


def say_player_compare(result: Result) -> TemplateResult:
    """Two or more players' season lines side by side, worded: a table of
    the games and each stat per game, the NetPoints summary beneath where
    anybody has a row, and which players have no line that season - the
    retired ``player_compare`` template's words.

    .. versionadded:: 5.0.0
    """
    groups = result.grouped
    assert groups is not None and result.span.season is not None
    wanted = list(result.facts["wanted"])
    columns = {stat: PLAYER_STAT_COLUMNS[stat][0] for stat in wanted}
    rows = {line["key"]: ({"gamesPlayed": line["games"], **{columns[stat]: line[stat] for stat in wanted}} if "games" in line else {}) for line in groups.rows}
    detail = result.parts[1].body if len(result.parts) > 1 else None
    netpoints = {line["key"]: {k: v for k, v in line.items() if k != "key"} for line in detail.rows} if isinstance(detail, Grouped) else {}
    missing_note = "".join(_said(result))
    answer = _compare_table(rows, wanted, season_phrase(result.span.season, result.span.season_type or 2), netpoints, missing_note)
    data = {"season": result.span.season, "players": rows, "netpoints": netpoints, "headline": answer.split("\n")[0].rstrip(":"), "notes": [missing_note] if missing_note else []}
    return TemplateResult(data=data, answer=answer)
