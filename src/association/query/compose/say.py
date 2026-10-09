"""The sayer: a :class:`~association.query.result.Result` worded as the
answer - a heading, a table, the remarks beneath it - and the plain
values the page renders from. It takes a Result and nothing else: no
connection, no relation, no question (``ROADMAP.md``, contract 3; the
import contract "The compiler's sentence reads no warehouse" holds this
module as it holds :mod:`association.query.compose.sentence`). Each note
kind has ONE phrase, here (:func:`note_phrase`), so rewording a note is
editing that phrase; the templates that still write their own sentences
phrase their notes through it too (``compose.core._box_notes``).

Phase 2's first slice (``ROADMAP.md``, "Phase 2, the expected steps",
step 0): the game log's words, taken from the retired template's body
(``templates.games._player_game_log`` and ``team_game_log``) and
reproduced from the Result word for word.

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from association.nba.coverage import unavailable
from association.query.answer import Artifact, Reply
from association.query.conditions import _SPLIT_TITLES, _cell, _margin, _split_cells, _split_label, _table, _win_pct
from association.query.entities import MAX_CLARIFY_CANDIDATES
from association.query.measures import PERIOD_COLUMNS
from association.query.metrics import LEADERBOARD_METRICS
from association.query.notes import Note, decided, note
from association.query.player_games import PERIOD_LOG_COLUMNS, PERIOD_RATES, STAT_LABELS, _joined, period_columns
from association.query.reading import LeftOut, ordinal_word
from association.query.result import (
    Calendar,
    Chart,
    ChartFacts,
    Clarify,
    Companions,
    CountFacts,
    Decided,
    GameOfSeries,
    Grouped,
    Line,
    LineFacts,
    LogFacts,
    MatchupFacts,
    MeetingsFacts,
    Met,
    NetPointsFacts,
    OnDate,
    OutlookFacts,
    PeriodFacts,
    PeriodRankingFacts,
    RankingFacts,
    RecordFacts,
    Refusal,
    Result,
    Role,
    Rows,
    Run,
    Runs,
    Scalar,
    ShotValue,
    Span,
    SplitsFacts,
    TeamPeriodFacts,
    TeamRankingFacts,
    TeamRecordFacts,
    TeamStatFacts,
)
from association.query.season_line import ADVANCED_STATS, HISTORY_COLUMNS, MADE_STAT_ATTEMPTS, NETPOINTS_COMPARE_ROWS, PLAYER_STAT_COLUMNS, SEASON_TOTAL_OF, SHOOTING_STATS
from association.query.season_text import MONTH_NAMES, SEASON_TYPE_NAMES, season_label, season_phrase
from association.query.shotchart import DERIVED_SHOT_VALUES, UNSEPARABLE_SHOT_VALUES
from association.query.team_metrics import RATING_NOTE, TEAM_METRICS, TeamMetric

from .logs import LOG_PERCENTAGES, log_key
from .sentence import LABELS

# --- notes: one phrase per kind -----------------------------------------------


def _snapshot_described(each: Mapping[str, Any]) -> str:
    """A power-index snapshot as an answer names it: its kind, date and team count."""
    teams = each["teams"]
    return f"a {each['kind']} snapshot ({each['date']}, {teams} team{'s' if teams != 1 else ''})"


#: What a team-season answer leaves blank, and why (a ``value_withheld`` note, by ``what``).
_TEAM_SEASON_WITHHELD: dict[str, str] = {
    "points_allowed": "A '-' needs points allowed, and ESPN's game list does not hold all of this team's games that season.",
    "rank": "A rank is left out where ESPN's game list is short for other teams that season.",
}


def _say_team_season_note(kind: str, facts: dict[str, Any]) -> str | None:
    """The phrase for a note a team-season answer makes - about its power-index
    snapshot, a figure it leaves blank, or another question to ask - or
    ``None`` for any other note."""
    what = facts.get("what")
    if kind in ("floor", "standings_short", "game_list_disagrees") and facts.get("table", "games") in ("standings", "games"):
        return _say_record_note(kind, facts)
    if kind == "snapshot" and what == "postseason_substitute":
        return "  (No pre-playoff snapshot for that season holds them, so this is the postseason one.)"
    if kind == "snapshot" and what == "stamped_after_season":
        return f"  (ESPN stamps this snapshot {facts['date']}, after the {facts['season']} season ended, so it may not reflect any one moment of it.)"
    if kind == "snapshot" and what == "other_snapshots":
        return f"  ESPN's power index for {facts['season']} also has {_joined([_snapshot_described(each) for each in facts['snapshots']])}."
    if kind == "value_withheld" and what == "bpi":
        return f"  no BPI rating in this snapshot - ESPN left it empty for all {facts['teams']} teams, though the record and projections below are its own"
    if kind == "value_withheld" and what in _TEAM_SEASON_WITHHELD:
        return _TEAM_SEASON_WITHHELD[what]
    if kind == "hint" and what == "regular_season":
        return f" The {facts['team']} are only in {_joined([_snapshot_described(each) for each in facts['snapshots']])} - ask about the regular season to see it."
    return None


def _say_window_short(facts: dict[str, Any], narrowing: str) -> str:
    count = facts["found"]
    plural = "s" if count != 1 else ""
    if facts.get("season_type") == [2, 3]:
        return f"Only {count} game{plural}{narrowing} found across the regular season and postseason."
    season = facts.get("season")
    found_in = "in his box scores" if season is None else f"in the {season_phrase(season, facts['season_type'])} - ask about his career to reach earlier seasons"
    return f"Only {count} game{plural}{narrowing} {found_in}."


def _say_neutral_site(games: int, placed: str) -> str:
    """The neutral-site games a home/road record leaves out, as the record
    places them: inside a season's line (``"season"``), after a career's
    (``"career"``), or on its own line beneath a tally (``"tally"``)."""
    if placed == "tally":
        return "\n  Neutral-site games count as neither home nor away."
    plural, verb = ("" if games == 1 else "s"), ("s" if games == 1 else "")
    if placed == "career":
        return f" {games} neutral-site game{plural} count{verb} as neither."
    return f" ({games} neutral-site game{plural} count{verb} as neither home nor away)"


#: The definitions said the same whatever their facts, by term.
_FIXED_DEFINITIONS: dict[str, str] = {
    "overtime_excluded": "Overtime is no quarter and is not counted.",
    "months_eastern": "Months go by the US Eastern date of the game.",
    # Its own line beneath a ranking's table, the way a table's other notes follow it.
    "most_recent_team": "\nTeam is each player's most recent team that season.",
    "rank_meaning": "Rank 1st is the best in the league (for pace, the fastest).",
    "unseen_ends_run": " A game with no box score in the warehouse ends a run rather than being carried across, since it cannot be checked.",
    # A postseason question over ESPN's per-season split (ISSUES.md #299).
    "fingerprint_per_season": " - ESPN publishes the play-type split per season, with no postseason breakout, so this is the whole season's",
    # Inside the heading of NetPoints' play-type detail, which it splits over two lines.
    "netpoints_overlap": "overlapping slices - a driving layup at the rim\n  counts in driving, layup and rim, so these do not add up",
}


def _say_definition(facts: dict[str, Any], about: str = "", placed: str = "") -> str:
    fixed = _FIXED_DEFINITIONS.get(facts.get("term") or "")
    if fixed is not None:
        return fixed
    if facts.get("term") == "neutral_site":
        return _say_neutral_site(facts["games"], placed)
    if facts.get("term") == "played" and "names" in facts:
        return _say_presence_played(list(facts["names"]))
    if facts.get("term") == "played":
        return "Played means he appeared in the game, and W-L is his team's record in those games."
    if facts.get("term") == "out":
        return f"Out means none of {_joined(list(facts['names']))} appeared in the game - a DNP or no box-score row at all; the other row is every game at least one of them played."
    if facts.get("term") == "tenure_counted":
        return _say_tenure_counted(facts, about)
    if facts.get("term") == "columns":
        return f"G, W-L and margin are the team's; Played counts {facts['whose']}'s games, and his averages are over those."
    if facts.get("term") == "pool" and facts.get("what") == "games_with_a_result":
        return f"Over the {facts['games']} games with a result."
    if facts.get("term") == "pool":
        return f"Over the {facts['games']} games he played; a game he missed is in neither row."
    if facts.get("term") == "streak_rule":
        return _say_streak_rule(facts)
    if facts.get("term") == "rating_formula":
        return RATING_NOTE
    if facts.get("term") == "netpoints_units":
        # What a NetPoints fingerprint's numbers are, in its section headings.
        possessions = facts["possessions"]
        return f"{facts['units']}" + (f" over {possessions:,.0f} possessions" if possessions else "")
    if facts.get("term") != "without":
        raise ValueError(f"no phrase for the definition of {facts.get('term')!r}")
    names = list(facts["names"])
    who = "he did not play" if len(names) == 1 else ("neither of them played" if len(names) == 2 else "none of them played")
    return f"Without {_joined(names)} means games {who} while on the same team - a did-not-play entry, or no line in the box score at all, which is how most injuries appear."


def _say_presence_played(names: list[str]) -> str:
    """What "played" means on a with/without split: one teammate's
    appearance, or every one of several teammates'."""
    if len(names) == 1:
        return f"Played means {names[0]} appeared in the game; out is a DNP or no box-score row at all."
    return f"Played means every one of {_joined(names)} appeared in the game; the other row is every game at least one of them missed - a DNP or no box-score row at all."


def _say_tenure_counted(facts: dict[str, Any], about: str) -> str:
    """The games a with/without split counted: inside ``about`` (whose time
    on the team, which only the answer's heading names), each stint spelled
    out - under its team's name where the stints span several teams."""
    stints = list(facts["stints"])
    several = len({stint["team"] for stint in stints}) > 1
    spelled = "; ".join(f"{stint['team']} {stint['from']} to {stint['to']}" if several else f"{stint['from']} to {stint['to']}" for stint in stints)
    return f"Counted: games inside {about} ({spelled}), which runs from the first box score that lists {'him' if len(facts['names']) == 1 else 'them'} there to the last."


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
    if facts.get("what") in ("unread", "unread_ranked"):
        return _say_lines_unread(facts)
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


def _say_lines_unread(facts: dict[str, Any]) -> str:
    """The games the answer's measures could not read: rebuilt lines carry
    no figure for a column the rebuild never measured (a rate over attempts,
    turnovers, minutes), so the figure is over the fetched games while the
    games beside it count every one (ISSUES.md #327). A ranking's
    (``unread_ranked``) says each player's count is beside his figure
    (``compose.sentence``) and that the minimum counts only those."""
    skipped, total = facts["games"], facts["total"]
    labels = [LABELS.get(column, column) for column in facts["columns"]]
    plural = skipped != 1
    whose = "the listed players'" if facts["what"] == "unread_ranked" else "these"
    have, their = ("have", "their") if plural else ("has", "its")
    lead = f"{skipped:,} of {whose} {total:,} games {have} no box score from ESPN: {their} figures are rebuilt from play-by-play, which leaves no {_joined(labels, 'or')}"
    if facts["what"] == "unread_ranked":
        verb = "are" if len(labels) != 1 else "is"
        return f"{lead}, so a player's {_joined(labels)} {verb} read over his other games, counted beside the figure, and the minimum counts only those."
    return f"{lead}, so {'those are' if len(labels) != 1 else 'that is'} read over the other {total - skipped:,}."


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


def _say_games_unseen(facts: dict[str, Any], consequence: str = "") -> str:
    count = facts["games"]
    if facts.get("why") == "no_box_score" and consequence:
        # Games inside a with/without split's time with no box score: the
        # answer says what that does to its two rows.
        return f"{count} game{'' if count == 1 else 's'} inside that time {'has' if count == 1 else 'have'} no box score, so {consequence}."
    if facts.get("why") == "no_play_by_play":
        return f"{count} of the {facts['of']} games have no play-by-play and are not counted."
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
    whose = "their" if facts.get("whose") == "team" else "his"
    return f" {facts['games']} of {whose} games in that span have no {unit} figure on record, so they are in neither row."


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


def _say_record_note(kind: str, facts: dict[str, Any]) -> str:
    """A team record's remarks about its sources: where the standings or the
    game list start, as the span's words end, and the seasons either falls
    short of the team's own totals."""
    if kind == "standings_short":
        parts = _joined([f"{s['season']} ({s['held']} of {s['played']} games)" for s in facts["seasons"]])
        return f"Note: ESPN's standings do not cover the {possessive(facts['team'])} whole season in {parts}, so this record is short by those games."
    if kind == "game_list_disagrees":
        parts = _joined([f"{s['season']} ({s['listed']} listed, {s['played']} played)" for s in facts["seasons"]])
        # A tally of some of a season's games cannot say the missing games
        # are among them (ISSUES.md #298: an October road record was "off by"
        # a 2000 game that may have been neither).
        claim = "those games may or may not be among the ones tallied here" if facts.get("narrowed") else "so this tally is off by those games"
        return f"Note: ESPN's game list and the {possessive(facts['team'])} season totals disagree on how many {facts['what']} games they played in {parts}, {claim}."
    if facts["table"] == "standings" and facts.get("what") == "home_road_split":
        return f" - ESPN's standings carry no home/road split before {season_label(facts['first'])}"
    if facts["table"] == "standings":
        return "the warehouse's standings begin there, so this is not the franchise's whole history"
    if facts.get("earliest") is not None:
        # A since-bounded span that starts before the list does (#300).
        left_out = f"{facts['earliest']}" if facts["first"] - facts["earliest"] == 1 else f"{facts['earliest']}-{facts['first'] - 1}"
        held = f"the {facts['first']} playoffs" if facts.get("what") == "postseason" else f"the {season_label(facts['first'])} season"
        return f"Seasons {left_out} are left out: the warehouse's game list holds every {facts['what'].replace(' ', '-')} game from {held} on."
    if facts.get("what") == "postseason":
        return f" - the warehouse's game list starts with the {facts['first']} playoffs"
    return " - the first the warehouse holds every game of"


def decision_phrase(each: Decided, **said_with: Any) -> str:
    """ONE phrase per decision kind (:data:`~association.query.notes.DECISION_KINDS`),
    recorded through :func:`~association.query.notes.decided` as it is
    said - the decision counterpart of :func:`note_phrase`. ``said_with``
    is what the sentence needs from the answer around it (a count the
    heading also states), never a fact the decision should carry itself.

    .. versionadded:: 5.0.0
    """
    if each.kind == "minimum":
        # A pool narrowed to an opponent or a venue says what its minimum is
        # half of (``qualifier``, the answer's: the most anyone played in it).
        text = f" (minimum {each.chose:,} {each.facts['of']}{said_with.get('qualifier', '')})"
    elif each.kind == "cut":
        text = f"{each.facts['total']} players qualified; the top {each.chose} are shown."
    elif each.kind == "season_default":
        text = season_phrase(int(each.chose), int(each.facts["season_type"]))
    elif each.kind == "season_fallback":
        text = f"No games this season, so these are his most recent {said_with['games']}{said_with['at']}, from the {said_with['season_label']}."
    elif each.kind == "also_matched":
        # The best match was drawn: the others are named (ISSUES.md #308: this
        # printed the list itself, brackets and quotes).
        text = f". Note: other players also matched: {', '.join(each.instead_of)}"
    elif each.kind == "name_left_out":
        # A "vs" fingerprint that drew one polygon: the name the question
        # also holds, or - where it names nobody else - a spelling to check.
        dropped = each.facts.get("names")
        if dropped:
            text = f"Note: the question also names {' and '.join(dropped)}, who was not included in this answer."
        else:
            text = "Note: the question compares two players, but only one of them matches anybody in the warehouse - check the spelling of the other."
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


def say_left_out(left_out: LeftOut) -> str:
    """What a fingerprint that drew one polygon of a "vs" question says
    beside it (:class:`~association.query.reading.LeftOut`), as the
    ``name_left_out`` decision - the players it drew chosen, the names the
    question also holds (``dropped``), or none matching (``unmatched``).

    .. versionadded:: 6.0.0
       ``entities.compared_but_unmatched``'s sentence, which read the
       question in the answering loop.
    """
    names = list(left_out.names)
    return decision_phrase(Decided(kind="name_left_out", field="players", chose=list(left_out.held), why="dropped" if names else "unmatched", facts={"names": names} if names else {}))


def note_phrase(each: Note, *, narrowing: str = "", consequence: str = "", listed: bool = False, about: str = "", placed: str = "") -> str:
    """The one sentence a note of ``each.kind`` is said with, from its facts.
    ``narrowing`` is the read's own phrase for what it was narrowed to
    (``Result.narrowing.phrase``), which a window note follows a count
    with; ``consequence`` is what empty box scores do to the answer around
    the note ("the count may be low"), which only that answer can say.
    ``listed`` says the note keys a table whose open rows are starred
    (a run still going, beneath the league's listing). ``about`` is whose
    time on a team a with/without split counted, as its heading names it.
    ``placed`` is where a team's record puts its neutral-site games: inside
    a season's line, after a career's, or beneath a tally.

    .. versionadded:: 5.0.0
    """
    facts = dict(each.facts)
    if each.kind == "still_open":
        return " * still going at the last game on record." if listed else " It was still going at the last game on record."
    if each.kind == "window_short":
        return _say_window_short(facts, narrowing)
    if each.kind == "definition":
        return _say_definition(facts, about, placed)
    if each.kind == "lines_rebuilt":
        return _say_lines_rebuilt(facts)
    if each.kind == "stat_withheld":
        return _say_stat_withheld(facts)
    if each.kind == "games_unseen" and "first" in facts:
        return _say_empty_box_scores(facts, consequence)
    if each.kind == "games_unseen":
        return _say_games_unseen(facts, consequence)
    if each.kind == "stat_blank":
        return _say_stat_blank(facts)
    if each.kind == "floor" and facts.get("table") == "box_scores":
        return _say_floor(facts)
    said = _say_shot_note(each.kind, facts, about)
    if said is None:
        said = _say_season_note(each.kind, facts)
    if said is None:
        said = _say_team_season_note(each.kind, facts)
    if said is None:
        said = _say_netpoints_note(each.kind, facts)
    if said is None:
        raise ValueError(f"no phrase for a {each.kind!r} note with {sorted(facts)}")
    return said


def _say_shot_note(kind: str, facts: dict[str, Any], about: str) -> str | None:
    """The phrase for a note about the shot table - a season whose twos and
    threes are derived rather than labeled, shots no value can be read for,
    and where a career sits against the shot floor (``about`` is whose
    career) - or ``None``; split out of :func:`note_phrase` for the
    complexity gate."""
    if kind == "shot_values_derived":
        return DERIVED_SHOT_VALUES[facts["season"]]
    if kind == "shots_unlabeled":
        why = "; ".join(UNSEPARABLE_SHOT_VALUES.get(s, f"nothing records their value in {s}") for s in facts["seasons"])
        return f"left out {facts['shots']:,} {'shot' if facts['shots'] == 1 else 'shots'} that cannot be told apart as twos or threes: {why}"
    if kind != "floor" or facts.get("table") != "shots":
        return None
    kind_words = SEASON_TYPE_NAMES.get(facts["season_type"], "regular season")
    first, earliest, last = facts["first"], facts["earliest"], facts["last"]
    if facts["what"] == "career_before_floor":
        return f" {about}'s {kind_words} career ({earliest}-{last}) ends before shot data begins, in {first}, so none of it can be shown."
    if facts["what"] == "career_clipped":
        return f" Shot data begins with the {first} season, so his {earliest}-{first - 1} {kind_words}s are not shown."
    return f" Covers his whole {kind_words} career on record ({earliest}-{last})."


def _say_season_note(kind: str, facts: dict[str, Any]) -> str | None:
    """The phrase for a note about a span of seasons - a career pool, a
    quarter's agreement with the plays, seasons missing, a player with no
    quarter's numbers - or ``None``; split out of :func:`note_phrase` for
    the complexity gate, in its order."""
    if kind == "floor" and facts.get("what") == "career_pool":
        return f"Careers that ended before {season_label(facts['first'])} are not in this warehouse, so this is not an all-time list."
    if kind == "rebuilt_agreement" and facts.get("what") in ("period_points_from_shots", "period_rebuilt", "team_period_rebuilt"):
        return _say_period_agreement(facts)
    if kind == "seasons_missing":
        return _say_seasons_missing(facts)
    if kind == "no_data_for" and "period" in facts:
        return f"({', '.join(facts['names'])} has no {facts['period']} numbers in the warehouse.)"
    return None


def _facts[F](result: Result, kind: type[F]) -> F:
    """``result``'s facts, as the record its shape's sayer reads - a shape
    said with another's record is a bug, never an empty answer."""
    facts = result.facts
    if not isinstance(facts, kind):
        raise TypeError(f"a {kind.__name__} sayer was handed {type(facts).__name__}")
    return facts


def _empty_said(result: Result) -> str:
    """Why a read with no rows found none: its :attr:`Result.empty` refusal, said."""
    assert result.empty is not None
    return refusal_phrase(result.empty.kind, result.empty.facts)


def _said(result: Result) -> list[str]:
    """Every note on ``result``, phrased and recorded (:func:`~association.query.notes.note`)."""
    return [note(each.kind, note_phrase(each, narrowing=result.narrowing.phrase), **each.facts) for each in result.notes]


# --- refusals: one phrase per cause ---------------------------------------------


def _no_ranking_for(stat: str) -> str:
    """The refusal for a ranking by a stat this relation has no measure for."""
    return f"No ranking reads {stat!r} on the player-games relation - it only ranks the box-score measures it knows, not a NetPoints or other outside figure."


def _ranking_floor_unit(unit: str, count: int) -> str:
    """The refusal for a ranking floor in a unit no ranking applies (F056:
    "... with at least 100 attempts"). The sentence names the floor that IS
    applied, so the question can be re-asked with it."""
    return f"A minimum of {count} {unit} is not a floor this ranking can apply yet - only a minimum number of games is. Ask with 'at least N games', or without the floor."


def _ranking_unit(metric: str, rate: Any) -> str:
    """The refusal for a ranking in a unit the metric has no form of,
    naming the forms THIS metric has ("who were the top 10 in defensive
    netpoints / 90": per 90 minutes is a football unit, and nothing in the
    warehouse is stored in it). Only the three NetPoints metrics have a
    per-100 sibling, so a generic list of units would be the
    refusal-with-the-wrong-cause shape. The retired leaderboard template's
    sentence, word for word (``compose.rankings._leaderboard_no_such_rate``
    until the point reader carried the cause)."""
    forms = ["as a season total" if metric.startswith("total_") else "per game"]
    if metric in SEASON_TOTAL_OF:
        forms.append("as a season total")
    # `netpoints_total`'s per-100 sibling is `netpoints_per_100`, not
    # `netpoints_total_per_100`, so the suffix comes off before looking.
    if f"{metric.removeprefix('avg_').removesuffix('_total')}_per_100" in LEADERBOARD_METRICS:
        forms.append("per 100 possessions")
    asked = "per 90 minutes" if "90" in str(rate) else str(rate).replace("_", " ")
    return f"No leaderboard ranks {metric.replace('_', ' ')} {asked} - the warehouse stores it only {' or '.join(forms)}."


#: How each shape over a line is named in its refusal, by the intent the
#: cause carries.
_LINE_SHAPES: dict[str, str] = {
    "single_game_high": "a single-game high",
    "record_when": "a record in the games over a line",
    "threshold_count": "a count of games over a line",
    "streak": "a streak",
    "game_log": "keeping only the games past a number",
}

#: What the stat is for, in each shape's refusal.
_STAT_FOR: dict[str, str] = {
    "single_game_high": "to rank games by",
    "record_when": "the line is on",
    "threshold_count": "the line is on",
    "streak": "each game has to reach",
    "game_log": "the games have to reach it in",
}


#: A shape over a line by the number its games reach, for a number read
#: with no stat.
_REACHING: dict[str, str] = {
    "game_log": "Keeping only the games past {}",
    "streak": "A streak of games reaching {}",
    "record_when": "A record in the games reaching {}",
    "threshold_count": "A count of games reaching {}",
}


def _stat_words(stat: Any) -> str:
    """A stat column as the answer names it, plural: "points", "3-pointers"."""
    label = STAT_LABELS.get(stat)
    return f"{label}s" if label else str(stat)


def _cause_sentence(kind: str, facts: Mapping[str, Any]) -> str | None:
    """The sentence each cause of a shape over a line - and the other
    declines slices (i) and (ii) gave a user as "Nothing here answers this
    question" (Phase 2, step 3) - is said with, naming the fact that is
    missing; ``None`` for a kind said elsewhere. Moved from
    ``compose.plan``, which said a reading's causes until the run's joined
    them here."""
    shape = _LINE_SHAPES.get(facts.get("intent", ""), "this answer")
    sentences = {
        "needs_stat": lambda: f"{shape.capitalize()} needs a stat {_STAT_FOR.get(facts.get('intent', ''), 'to read')}, and none was read.",
        "unknown_stat": lambda: _unknown_stat(facts.get("intent", ""), facts["stat"], shape),
        "needs_threshold": lambda: f"{shape.capitalize()} needs the number of {_stat_words(facts['stat'])} each game has to reach, and none was read.",
        "threshold_needs_stat": lambda: f"{_REACHING.get(facts.get('intent', ''), 'Games reaching {}').format(facts['threshold'])} needs the stat they reach it in, and none was read.",
        "threshold_counts_every_game": lambda: f"A threshold of {facts['threshold']} counts every game - there is no line there to keep games past.",
        "line_names_no_stat": lambda: f"{facts['phrase']!r} names no box-score stat a game can be kept {facts['side']}.",
        "needs_line": lambda: "A count of games across the league needs the line it counts - a stat and a number, as in '40-point games' - and none could be read from the question.",
        "career_place_needs_player": lambda: f"The {ordinal_word(facts['season_n'])} season is a place in one player's career, and no player was named.",
        "needs_subject": lambda: f"{shape.capitalize()} needs a player or a team to read it for, and neither was named.",
        "team_streak_of_stat": lambda: f"A team's streak is of wins or losses - a run of games reaching a number of {_stat_words(facts['stat'])} is read for a player, not a team.",
        "matchup_needs_two": lambda: _matchup_needs_two(list(facts["names"])),
        "no_coach_table": lambda: COACH_REFUSAL,
        "too_short": lambda: f"I couldn't understand your question, '{facts['asked']}'. Please try re-phrasing it.",
        "no_period_stat": lambda: (
            f"A quarter or half has no per-period {facts['stat']!r} - the period's line rebuilds {', '.join(PERIOD_COLUMNS)} from the plays, "
            "and a field goal, 3-point or free throw percentage is a ratio of those; nothing else."
        ),
    }
    phrase = sentences.get(kind)
    return phrase() if phrase is not None else None


#: What a coach question is answered with, and why it is a refusal naming the
#: source rather than one naming only the intent.
#:
#: No table here holds a coach - 20 base tables and 6 views, zero columns named
#: anything like it - so a reader has nothing to find. Left to fall through,
#: the retired SQL agent spent a slow round trip and was then free to fill the
#: silence from its own weights, which is the failure ``check_coverage`` exists
#: to stop: an agent with nothing to read writes a confident answer. So the
#: reading refuses (``point._read_point``: the ``no_coach_table`` cause), and
#: this names which fact is missing.
#:
#: The sentence says what it says because the obvious reading - "ESPN does not
#: publish coaches" - was checked on 2026-09-17 and is false. ESPN serves two
#: coach collections, and neither is usable: the league-wide one ignores the
#: season it is asked for (1977 answers with today's staff, Doug Christie and
#: JJ Redick among them), and the team-scoped one covers 12 of 30 teams in
#: 1996, never names two coaches for a team-season - so no mid-season change
#: exists in it - and is wrong about Detroit for every season sampled from
#: 1994 to 2026. Telling somebody the source has no coaches would be the
#: wrong-cause refusal this project keeps producing; telling them it has an
#: unusable one is true. See DATA.md, "ESPN publishes coaches, and the
#: collection that looks league-wide is not historical".
COACH_REFUSAL: str = (
    "No table here holds a coach, so nothing about one can be answered - not a record, not a tenure, not a game. "
    "ESPN does publish coaches, but not in a form worth storing: the season-by-season list it serves ignores the season asked for and returns the current staff, "
    "and its per-team list covers 12 of 30 teams in 1996, never shows a mid-season change, and names the wrong coach for some franchises outright. "
    "Player and team questions are unaffected."
)
"""The sentence a coach question is answered with. See above.

.. versionadded:: 4.0.0

.. versionchanged:: 5.0.0
   Moved from ``templates.teams``, with the ``coach`` template it was the
   whole answer of: the reading refuses by the ``no_coach_table`` cause,
   and :func:`refusal_phrase` says it (``compose.plan`` until the run's
   refusals joined the reading's here).
"""


def _unknown_stat(words: str, stat: Any, shape: str) -> str:
    """The refusal for a stat with no per-game column, in the shape's words
    (``words``: the cause's ``intent`` fact, the reading's name for them)."""
    if words == "single_game_high":
        return f"A single-game high cannot rank games by {stat!r} - only by a box-score stat each game has a number for."
    if words == "game_log":
        return f"A game log has no per-game column for {stat!r}."
    return f"{shape.capitalize()} cannot be read over {stat!r} - it has no per-game box-score column."


def _matchup_needs_two(names: list[str]) -> str:
    """A matchup's refusal for the count of players read: none, one, or more than two."""
    if not names:
        return "A matchup needs two players, and none was read."
    if len(names) == 1:
        return f"A matchup needs two players, and only {names[0]} was read."
    return f"A matchup is between two players, and {len(names)} were read: {', '.join(names)}."


def _say_shot_distance_ranking() -> str:
    """The refusal for a shot-distance ranking, naming the real cause (ISSUES.md
    #114): no leaderboard metric ranks distance, and the nearest real one is
    a percentage."""
    return "Shot distance is not ranked league-wide yet - ask about one named player's average shot distance instead."


def clarification(text: str, candidates: Sequence[str], kind: str = "player", active: int = 0) -> str:
    """The "did you mean" sentence for an ambiguous name - one phrasing, so
    the same ambiguity does not read two ways depending on which reader
    asked it.

    Names the first ``MAX_CLARIFY_CANDIDATES`` in the order given and counts
    the rest - except the first ``active``, the candidates who played in the
    season asked about, who are always named. Counting them away is how
    "Curry" hid Stephen: six Currys sorted by name and cut at five named four
    men who never played in the season asked about, plus Seth. A caller with a
    season in hand narrows and orders first (see
    :func:`~association.query.entities.resolve_player`) and passes
    ``Ambiguous.active`` on, so the count only ever stands for players who
    could not be the answer. The most one season holds under one name is
    15 Williamses, in 1998 and 1999; 2026 holds 14.

    .. versionadded:: 2.1.0

    .. versionchanged:: 5.0.0
       Moved from :mod:`association.query.entities`: a read returns a
       :class:`~association.query.result.Clarify`, and the sayer words it.
    """
    shown = list(candidates[: max(MAX_CLARIFY_CANDIDATES, active)])
    extra = len(candidates) - len(shown)
    rest = "" if extra <= 0 else " (1 other also matches)" if extra == 1 else f" ({extra} others also match)"
    joined = ", ".join(shown[:-1]) + f" or {shown[-1]}" + rest
    return f"{text!r} matches more than one {kind} - did you mean {joined}?"


def suggestion(text: str, candidates: Sequence[str], kind: str = "player") -> str:
    """The sentence for a name nothing matched, naming the near spellings
    :func:`~association.query.entities.suggest_players` found, if any.

    .. versionadded:: 2.1.0

    .. versionchanged:: 5.0.0
       Moved from :mod:`association.query.entities`, as :func:`clarification` was.
    """
    if not candidates:
        return f"No {kind} found matching {text!r}."
    joined = (", ".join(candidates[:-1]) + " or " if len(candidates) > 1 else "") + candidates[-1]
    return f"No {kind} found matching {text!r} - did you mean {joined}?"


def _say_no_such_season_n(facts: Mapping[str, Any]) -> str:
    """An ordinal season past the player's career on record."""
    on_record = facts["on_record"]
    have = f"{facts['seasons']} seasons on record ({on_record[0]}-{on_record[1]})" if on_record else "no season on record"
    return f"{facts['player']} has {have}, so there is no {ordinal_word(facts['season_n'])} season to answer for."


def _say_period_condition_needs_plays(facts: Mapping[str, Any]) -> str:
    """A quarter's line used as a condition, in a warehouse with no plays to rebuild it from."""
    noun = STAT_LABELS.get(facts["stat"], facts["stat"])
    return f"Games with {facts['threshold']}+ {noun}s in the {facts['period']} cannot be picked out here: a period's {noun}s are rebuilt from play-by-play, and this warehouse holds none."


def _say_no_games_in_span(facts: Mapping[str, Any]) -> str:
    """No games in the span with a box score - and, where the season was a
    default, where his games are (a ``season_redirected`` decision)."""
    redirect = facts["redirect"]
    return f"No {facts['span']} games found for {facts['player']}." + defaulted_season_note(tuple(redirect) if redirect else None, facts["kind"])


def _say_player_listed(facts: Mapping[str, Any]) -> str:
    """A player the box scores list in the span but never as playing, or not at all."""
    for_team = f" for the {facts['team']}" if facts["team"] else ""
    count = facts["games"]
    if not count:
        return f"{facts['player']} has no games{for_team} {facts['where']} in the warehouse."
    which = "it" if count == 1 else "any of them"
    return f"{facts['player']} was listed in {count} box score{'' if count == 1 else 's'}{for_team} {facts['where']} but did not play in {which}."


def _fingerprint_who(facts: Mapping[str, Any]) -> str:
    """The players a fingerprint was asked for, or "any player"."""
    return ", ".join(facts["players"]) if facts["players"] else "any player"


#: Why no fingerprint can be drawn (``fingerprint.FingerprintUnavailable``'s causes).
_FINGERPRINT_PHRASES: dict[str, Callable[[Mapping[str, Any]], str]] = {
    "fingerprint_view": lambda facts: f"view must be one of {facts['views']} - got {facts['view']!r}.",
    "fingerprint_scale": lambda facts: f"scale must be one of {facts['scales']} - got {facts['scale']!r}.",
    "fingerprint_pool_empty": lambda facts: f"No player reached {facts['min_minutes']} minutes in season {facts['season']}, so there is nothing to compare against.",
    "fingerprint_no_season": lambda facts: f"The warehouse has no NetPoints fingerprint data for season {facts['season']}.",
    "fingerprint_none_for": lambda facts: (
        f"No NetPoints fingerprint on record for {_fingerprint_who(facts)} in season {facts['season']}, which has {facts['held']} player{'' if facts['held'] == 1 else 's'} on record."
    ),
    "game_fingerprints_unpulled": lambda facts: f"Per-game NetPoints fingerprints are not in this warehouse - pull them with `data pull --include-net-points-daily`. ({facts['error']})",
    "game_fingerprint_no_season": lambda facts: f"The warehouse has no per-game NetPoints fingerprint data for season {facts['season']}.",
    "game_fingerprint_pool_empty": lambda facts: f"No game in season {facts['season']} reached {facts['min_possessions']} possessions, so there is nothing to compare against.",
    "game_fingerprint_none_for": lambda facts: (
        f"No per-game NetPoints fingerprint on record for {_fingerprint_who(facts)}'s {'earliest' if facts['order'] == 'first' else 'most recent'} game of season {facts['season']}."
    ),
}


def _say_fingerprint_unavailable(kind: str, facts: Mapping[str, Any]) -> str:
    """Why no fingerprint was drawn - and, where the name was read best-match,
    the other players it also matched (the ``also_matched`` decision): the
    plot is titled with the name that won, which is what is missing when
    nothing is drawn, so the runners-up are named on the failure too."""
    said = _FINGERPRINT_PHRASES[kind](facts)
    if facts.get("also"):
        said += decided("also_matched", f" Note: other players also matched: {', '.join(facts['also'])}.", field="player", chose=facts["chose"], instead_of=facts["also"])
    return said


def _say_never_together(facts: Mapping[str, Any]) -> str:
    """A with/without split's subject and teammates (and, if named, a team)
    never on the same roster together, as the box scores show it."""
    team, all_of, named = facts["team"], facts["all_of"], list(facts["teammates"])
    on = f" the {team}" if team else ""
    if facts["player"] is None and len(named) == 1:
        return f"{all_of} never appeared in a box score for{on}, so there are no {team or ''} games with or without him to count."
    whom = _joined([facts["player"], *named]) if facts["player"] is not None else all_of
    played_phrase = "he played" if len(named) == 1 else "they all played"
    return f"{whom} were never on{on or ' the same team'} together in the box scores on record, so there are no games to divide by whether {played_phrase}."


def _say_together_outside_span(facts: Mapping[str, Any]) -> str:
    """A with/without split's time together falling outside the span asked
    about - or inside it, with no box score for any of its games."""
    named, all_of = list(facts["teammates"]), facts["all_of"]
    whose = f"{all_of}'s time" if facts["player"] is None else f"The time {_joined([facts['player'], *named])} spent together"
    if facts["unseen"]:
        return (
            f"All {facts['unseen']} games inside {whose[0].lower() + whose[1:]} on the team in the {facts['span']} have no box score in the warehouse, so whether {all_of} played them cannot be told."
        )
    return f"{whose} on the team, as the box scores show it ({facts['stints']}), falls outside the {facts['span'] if facts['season_named'] else 'seasons on record'}."


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
    elif span.date or span.since is not None or span.until is not None:
        # One day, or a span the question cut ("the past two seasons"): the
        # seasons alone, since a cut span is not his career (ISSUES.md #285).
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
        "span": "career" if span.career and not _facts(result, LogFacts).mixed else None,
        "opponent": narrowing.opponent,
        "venue": narrowing.venue,
        "without": list(narrowing.without),
    }


def say_player_log(result: Result) -> Reply:
    """A player's log, worded: the heading, the aligned table with its
    per-game averages, the notes - and the plain values beneath them.

    .. versionadded:: 5.0.0
    """
    about = _player_about(result)
    body = result.rows
    if body is None or not body.rows:
        empty = _empty_said(result)
        return Reply(data={**about, "games": [], "message": empty}, answer=empty)
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
    return Reply(data=data, answer="\n".join([header, *table, *notes]))


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


def say_team_log(result: Result) -> Reply:
    """A team's log, worded: the heading with its record, one line per game,
    the total the question asked for beneath them.

    .. versionadded:: 5.0.0
    """
    body = result.rows
    if body is None or not body.rows:
        return Reply(data={"team": result.subject, "games": []}, answer=_empty_said(result))
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
    stat = _facts(result, LogFacts).stat
    data = {"team": result.subject, "wins": wins, "losses": losses, "games": games, "headline": header.rstrip(":"), **_team_total_data(games, stat)}
    return Reply(data=data, answer="\n".join([header, *lines]) + _team_total_line(games, stat))


def _say_rows(result: Result) -> Reply:
    """Rows by date: a team's log, a player's quarter or half game by game
    (the read's :class:`~association.query.result.Period` cell), or his
    log."""
    if result.relation == "team":
        return say_team_log(result)
    return say_period_split(result) if result.narrowing.period is not None else say_player_log(result)


def _say_line(result: Result) -> Reply:
    """A line over games, per game: a team's quarter or half, or a player's line."""
    return say_team_quarter_points(result) if result.relation == "team" else say_player_stat(result)


def _say_ranking(result: Result) -> Reply:
    """A ranking by player: of games over a line (``ranked_by="games"``), by
    a quarter or half (the read's :class:`~association.query.result.Period`
    cell, or a column per quarter), or by a season-line metric."""
    body = result.grouped
    assert body is not None
    if body.ranked_by == "games":
        return say_threshold_count(result)
    if result.narrowing.period is not None or body.ranked_by == "quarters":
        return say_period_leaderboard(result)
    return say_leaderboard(result)


def _say_comparison(result: Result) -> Reply:
    """Named subjects side by side: in the games they met in (the read's
    :class:`~association.query.result.Met` cell), or their season lines."""
    return say_player_matchup(result) if result.narrowing.cell(Met) is not None else say_player_compare(result)


def _say_teams(result: Result) -> Reply:
    """Teams side by side: ranked by a metric, or the two that met."""
    body = result.grouped
    assert body is not None
    return say_team_leaderboard(result) if body.ranked_by is not None else say_head_to_head(result)


def say(result: Result | Refusal | Clarify) -> Reply:
    """``result`` worded by its shape (:func:`_sayer`): the headline part's
    body and what it is by - or a refusal by its cause, or a question
    back.

    .. versionadded:: 5.0.0

    .. versionchanged:: 5.0.0
       Takes a :class:`~association.query.result.Refusal` or a
       :class:`~association.query.result.Clarify` too: a read's outcome,
       whichever it was.

    .. versionchanged:: 5.0.0
       Chooses the sayer from the body alone (2026-10-05), where it read
       the span's source and the relation as well.
    """
    if isinstance(result, Refusal):
        return say_refusal(result)
    if isinstance(result, Clarify):
        return say_clarify(result)
    return _sayer(result)(result)


# --- a record over a line ----------------------------------------------------------


def say_record_when(result: Result) -> Reply:
    """A player's team's record when he reached a line, worded: the three-row
    table (reached, fell short, all his games) under its heading, then the
    pool, the floor and the caveats in the retired template's order.

    .. versionadded:: 5.0.0
    """
    groups = result.grouped
    assert groups is not None and result.span.phrase is not None
    if result.relation == "team":
        return _say_team_record_when(result, groups)
    line = groups.of
    assert isinstance(line, Line)
    stat, threshold, teams = line.column, line.value, list(_facts(result, RecordFacts).teams)
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
    return Reply(data=data, answer=f"{table}\n{trailer}")


def _say_team_record_when(result: Result, groups: Grouped) -> Reply:
    """A team's record when its own figure reached a line, worded as the
    retired template's team branch said it: the three rows under the team's
    heading, then the pool, the floor and the games with no figure."""
    line = groups.of
    assert isinstance(line, Line)
    stat, threshold = line.column, line.value
    unit = _unit(stat)
    by_key = {row["key"]: {k: v for k, v in row.items() if k != "key"} for row in groups.rows}
    reached, short, every = by_key["reached"], by_key["short"], by_key["all"]
    title = f"{result.subject} record when they had {threshold}+ {unit}{result.narrowing.phrase}, {result.span.phrase}:"
    rows = [(f"{threshold}+ {unit}", reached), (f"under {threshold} {unit}", short), ("all their games", every)]
    table = _table(title, ["G", "W-L", "Win%", "Margin"], [(name, [str(g["games"]), f"{g['wins']}-{g['losses']}", _win_pct(g["wins"], g["games"]), _margin(g["avg_margin"])]) for name, g in rows])
    # Recorded in the Result's order (the template wrote the caveat first),
    # said in the heading's: the pool, the floor, the caveat.
    said = {each.kind: text for each, text in zip(result.notes, _said(result), strict=True)}
    trailer = said.get("definition", "") + said.get("floor", "") + said.get("stat_blank", "")
    data = {"team": result.subject, "stat": stat, "threshold": threshold, "span": result.span.phrase, "reached": reached, "fell_short": short, "headline": title.rstrip(":"), "notes": [trailer]}
    return Reply(data=data, answer=f"{table}\n{trailer}")


# --- splits ----------------------------------------------------------------------------


def _splits_about(result: Result, facts: SplitsFacts) -> dict[str, Any]:
    """The page's values a splits answer opens with: a team's own, or a
    player's and his narrowing's."""
    narrowing = result.narrowing
    if result.relation == "team":
        return {"player": None, "team": facts.team, "venue": narrowing.venue, "opponent": narrowing.opponent}
    return {"player": result.subject, "team": facts.team, **_narrowed_values(result)}


def say_splits(result: Result) -> Reply:
    """A player's or a team's splits, worded: the shared table over whichever
    subject was read - one or all four splits, each split's rows under its
    group label, a blank line between splits - and the notes that qualify
    them, in the retired template's order: what the words mean, the floor,
    the caveat.

    .. versionadded:: 5.0.0
    """
    groups = result.grouped
    assert groups is not None and result.span.phrase is not None
    facts = _facts(result, SplitsFacts)
    split, kinds, counted = facts.split, list(facts.kinds), facts.counted
    line = [(name, header, "") for name, header in facts.line]
    by_kind: dict[str, list[dict[str, Any]]] = {kind: [] for kind in kinds}
    for row in groups.rows:
        by_kind[row["split"]].append({k: v for k, v in row.items() if k != "split"})
    rows: list[tuple[str, list[str]]] = []
    for kind in kinds:
        if rows:
            rows.append(("", []))
        rows += [(_split_label(kind, entry), _split_cells(entry, line)) for entry in by_kind[kind]]
    what = _SPLIT_TITLES[split] if split else "splits"
    team = result.relation == "team"
    subject = result.subject + (f" for the {facts.team}" if facts.team and not team else "") + result.narrowing.phrase
    headline = f"{subject}, {what}, {result.span.phrase} ({counted}):"
    answer = _table(headline, ["G", "W-L", *(header for _, header, _ in line)], rows)
    said = list(zip(result.notes, _said(result), strict=True))
    notes = (
        [text for each, text in said if each.kind == "definition"]
        + [text.strip() for each, text in said if each.kind == "floor"]
        + [text.strip() for each, text in said if each.kind in ("games_unseen", "lines_rebuilt", "stat_blank")]
    )
    answer += "\n" + " ".join(notes)
    data = {**_splits_about(result, facts), "span": result.span.phrase, "games": facts.games, "splits": by_kind, "headline": headline.rstrip(":"), "notes": notes}
    return Reply(data=data, answer=answer.strip())


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


def shooting_result(name: str, scope: dict[str, Any], values: dict[str, Any], shooting: Any, *, when: str, games_note: str = "") -> Reply:
    """A percentage with the makes and attempts behind it - "out of how many?"
    is the first thing anybody asks of a percentage without them.

    ``shooting`` is the stat's entry in ``season_line.SHOOTING_STATS``.
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
    return Reply(data={"player": name, **scope, "stats": stats, "labels": labels}, answer=answer)


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


def _narrowed_values(result: Result) -> dict[str, Any]:
    """A player's narrowing as the page's plain values: the opponent, the
    venue, the teammates absent, his role, the lines' words and the game of
    a series - read off the Result's cells."""
    narrowing = result.narrowing
    role, game = narrowing.cell(Role), narrowing.cell(GameOfSeries)
    return {
        "opponent": narrowing.opponent,
        "venue": narrowing.venue,
        "without": list(narrowing.without),
        "started": role.started if role is not None else None,
        "measures": [line.label for line in narrowing.lines()],
        "series_game": game.n if game is not None else None,
    }


def _player_stat_values(result: Result, line: Scalar) -> tuple[dict[str, Any], Any]:
    """The line as ``data["stats"]`` holds it: the games, then each wanted
    stat's per-game figure and total, then a made count's attempts - and
    those attempts, for the sentence."""
    wanted = list(_facts(result, LineFacts).wanted)
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


def say_player_stat(result: Result) -> Reply:
    """A player's line over the games a question narrowed to, worded: one
    sentence (a percentage with its makes and attempts, or each stat per
    game with the total of one stat alone), the remarks after it, and
    against one opponent the newest meetings beneath - the retired
    ``player_stat`` template's words.

    .. versionadded:: 5.0.0
    """
    line = result.scalar
    assert line is not None
    span, facts = result.span, _facts(result, LineFacts)
    about = {"season": span.season, "date": span.date, "span": "career" if span.career else None, **_narrowed_values(result)}
    if not line.games:
        return Reply(data={"player": result.subject, **about, "games": 0, "stats": {}}, answer=_empty_said(result))
    notes = _said(result)
    scope = {**about, "seasons": [span.first, span.last]}
    when, games_note = span.phrase or "", result.narrowing.phrase
    stat = facts.stat
    if stat is not None:
        shooting = SHOOTING_STATS[stat]
        said = shooting_result(
            result.subject, scope, {"gamesPlayed": line.games, shooting.made: line.sums["made"], shooting.attempted: line.sums["attempted"]}, shooting, when=when, games_note=games_note
        )
    else:
        wanted = list(facts.wanted)
        values, attempted = _player_stat_values(result, line)
        answer = phrase_player_stat(result.subject, season_phrase(span.first or 0, span.season_type or 2), values, wanted, games_note=games_note, when=when, attempted=attempted)
        said = Reply(data={"player": result.subject, **scope, "stats": values, "labels": stat_value_labels(wanted)}, answer=answer)
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
    if facts["what"] == "team_period_rebuilt":
        said = ", ".join(f"{pct:.1f}% of the time in {season}" for season, pct in zip(facts["seasons"], facts["pct"], strict=True))
        return (
            f"Rebuilt from play-by-play rather than an official per-quarter box score: a team-game's {period_columns_noun(facts['stat'])} rebuilt this way "
            f"match its box score {said} - treat a single game as approximate."
        )
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


def _say_period_untrusted(facts: Mapping[str, Any]) -> str:
    """The refusal for a season whose per-period figures cannot be trusted
    (``player_games.period_distrust``'s facts): a shot's value the season
    does not carry, points that disagree with ESPN's own quarter scores, or
    a column whose rebuilt figure disagrees with the box score."""
    season, column, agreement = facts["season"], facts["column"], facts["agreement"]
    if facts["unseparable"]:
        message = f"Per-quarter scoring cannot be answered for {season}: {UNSEPARABLE_SHOT_VALUES[season]}."
    elif column == "points":
        message = f"Per-quarter scoring cannot be answered for {season}: its per-period points agree with ESPN's own quarter scores only {agreement:.0f}% of the time."
    else:
        noun = period_noun(column, 2)
        message = f"Per-quarter {noun} cannot be answered for {season}: rebuilt from play-by-play, a game's {noun} match its box score only {agreement:.0f}% of the time."
    return message


def _say_period_unread(facts: Mapping[str, Any]) -> str:
    """The refusal where a period's ``measure`` could not be rebuilt at all:
    a warehouse loaded without play-by-play leaves every plays column NULL."""
    return f"Per-quarter {period_columns_noun(facts['measure'])} cannot be answered here: they are rebuilt from play-by-play, and this warehouse holds none."


def _period_where_said(result: Result) -> str:
    """What a period answer says it narrowed to, after the player and the
    period - the venue, the starter/bench half, the teammates absent, the
    lines on a box-score column, a single date, the game of a series - in
    the words :meth:`~association.query.player_games.Narrowed.filters` uses
    for the same narrowings. Said in the answer, like every other narrowing
    here: a total over his starts, headed as though it covered every game,
    is the silent narrowing the scoping cells exist to stop."""
    facts, narrowing = _facts(result, PeriodFacts), result.narrowing
    role, series = narrowing.cell(Role), narrowing.cell(GameOfSeries)
    venue, started, mates, measures = narrowing.venue, role.started if role is not None else None, list(narrowing.without), [line.label for line in narrowing.lines()]
    said = f" at {'home' if venue == 'home' else 'away'}" if venue else ""
    said += "" if started is None else (" as a starter" if started else " off the bench")
    said += f" without {_joined(mates)}" if mates else ""
    said += f" with {_joined(measures)}" if measures else ""
    date = result.span.date
    said += f" on {date}" if date else ""
    # One opponent makes it "the" series, a whole postseason "each".
    said += f" in game {series.n} of {'the' if narrowing.opponent is not None else 'each'} series" if series is not None else ""
    return said + "".join(f" {phrase}" for phrase in facts.also)


def _period_none_found(result: Result, season_label: str, vs: str, at: str) -> str:
    """No games under the narrowing. A date names its own day (``at`` says
    it), and no season was read for it: "No 2026 regular season games found
    for Anthony Davis on 2015-01-10" named a season the date is not in."""
    dated = result.span.date
    return f"No games found for {result.subject}{vs}{at}." if dated else f"No {season_label} games found for {result.subject}{vs}{at}."


def _period_about(result: Result) -> dict[str, Any]:
    """The plain values a period answer's page renders from, before its games."""
    narrowing, role = result.narrowing, result.narrowing.cell(Role)
    return {
        "player": result.subject,
        **({"period": narrowing.period.label} if narrowing.period is not None else {}),
        "stat": _facts(result, PeriodFacts).stat,
        "season": result.span.season,
        "opponent": narrowing.opponent,
        "venue": narrowing.venue,
        "started": role.started if role is not None else None,
        "measures": [line.label for line in narrowing.lines()],
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
    facts, summary, period, window = _facts(result, PeriodFacts), result.rows.summary if result.rows is not None else {}, result.narrowing.period, result.window
    assert period is not None and window is not None
    measure, period_label, subject = facts.stat, period.label, result.subject
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
    if facts.per_game:
        count = window.limit
        earliest = window.ascending
        shown = games[:count] if earliest else games[-count:]
        label = "every game" if len(shown) == len(games) else f"the {len(shown)} {'earliest' if earliest else 'most recent'}"
        header += "\n" + _period_log(shown if earliest else list(reversed(shown)), period_label, label, measure, full_line=facts.full_line)
    return header


def say_period_split(result: Result) -> Reply:
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
        return Reply(data={**data, "message": message, "headline": message}, answer=message)
    data |= dict(body.summary)
    header = _period_header(result, games, season_label, vs, at)
    extra = None
    for each in result.decisions:
        extra = decision_phrase(each, games=len(games), at=at, season_label=season_label)
    caveat = period_caveat(list(result.notes))
    data["headline"] = header.split("\n")[0]
    data["notes"] = [*([extra] if extra else []), *([caveat.strip()] if caveat else [])]
    return Reply(data=data, answer=header + (f"\n{extra}" if extra else "") + caveat)


def _period_quarter_cells(values: list[str]) -> str:
    return "".join(f"{value:>9}" for value in values[:-1]) + f"{values[-1]:>12}"


def _period_quarter_table(result: Result, quarters: list[dict[str, Any]], season_label: str, vs: str, at: str) -> tuple[str, list[str]]:
    """The four quarters' header and two-row table: per game and total (a
    rate: percentage and made-attempted) in each quarter, then in
    regulation - the four together."""
    facts = _facts(result, PeriodFacts)
    measure, games = facts.stat, facts.games
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


def say_period_by_quarter(result: Result) -> Reply:
    """A player's four quarters side by side, worded: the header naming the
    games (the same in every quarter, said once), the per-game and total
    rows, that overtime is no quarter, and the season's measured agreement.

    .. versionadded:: 5.0.0
    """
    groups = result.grouped
    assert groups is not None and result.span.season is not None
    facts, window = _facts(result, PeriodFacts), result.window
    season_label = season_phrase(result.span.season, result.span.season_type or 2)
    vs = f" against the {result.narrowing.opponent}" if result.narrowing.opponent else ""
    at = _period_where_said(result)
    if window is not None:
        # The compiler's window cut these games (the same N in every
        # quarter): `Narrowed.filters(windowed=True)`'s own phrase.
        n = window.limit
        at += f" over his {'first' if window.ascending else 'last'} {n} game{'s' if n != 1 else ''}"
    quarters = [dict(row) for row in groups.rows]
    data: dict[str, Any] = {**_period_about(result), "games_played": facts.games, "quarters": quarters}
    if not facts.games:
        message = _period_none_found(result, season_label, vs, at)
        return Reply(data={**data, "message": message, "headline": message}, answer=message)
    header, table = _period_quarter_table(result, quarters, season_label, vs, at)
    overtime, *agreement = result.notes
    said = note(overtime.kind, note_phrase(overtime), **overtime.facts)
    caveat = period_caveat(agreement)
    data |= {"headline": header.rstrip(":"), "notes": [said, *([caveat.strip()] if caveat else [])]}
    return Reply(data=data, answer="\n".join([header, *table, f"  {said}"]) + caveat)


# --- a count of games over a line, and a single game's high ---------------------------


def _counted_span_words(result: Result) -> tuple[str, str, str]:
    """How an answer read from box scores names the games it covers, from
    the span's values: the clause that follows a verb ("in the 2026 regular
    season", "in his regular season career (2018-19 through 2025-26)"), the
    caption the page shows, and the games' own name for "no ... in the
    warehouse". A career that began before the box scores, and the
    league's, are named from the box scores' first season; a named career
    from his own first and last."""
    span, facts = result.span, _facts(result, CountFacts)
    # Not ``or 2``: 0 is both season types at once, a value of its own.
    season_type = 2 if span.season_type is None else span.season_type
    kind = SEASON_TYPE_NAMES.get(season_type, "regular season")
    since = season_label(facts.box_scores_from)
    ordinal = span.ordinal
    if span.season is not None:
        period = season_phrase(span.season, season_type)
        if ordinal is not None:
            return f"in his {ordinal_word(ordinal)} season ({period})", f"{ordinal_word(ordinal)} season, {period}", f"{period} games"
        return f"in the {period}", period, f"{period} games"
    if result.relation == "everyone" or (span.first is not None and span.first < facts.box_scores_from):
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


def say_threshold_count(result: Result) -> Reply:
    """How many games cleared a line, worded - a named player's count, or
    the league's leaders with "Next: ..." - with the floor said first where
    his career began before the box scores, and the notes after it in the
    retired template's order: a league career is not all-time, a withheld
    stat (else the empty box scores), the leader's rebuilt games.

    .. versionadded:: 5.0.0
    """
    facts = _facts(result, CountFacts)
    named = result.relation == "player"
    stat = facts.stat
    label = STAT_LABELS.get(stat or "", stat or "")
    scope_text = " and ".join(line.label or f"{line.value}+ {label}s" for line in result.narrowing.lines())
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
        "empty_box_scores": facts.empty_box_scores,
        "rebuilt_games": rows[0][2] if rows else 0,
        # The trailing "Next: ..." restates the table in prose - not the headline.
        "headline": phrase.split(" Next: ")[0],
        "notes": notes,
    }
    return Reply(data=data, answer=" ".join([phrase, *notes]))


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
        if _facts(result, CountFacts).empty_box_scores:
            return f"{who} no {games_said} with a box score in the warehouse."
        return f"{who} no {games_said} in the warehouse."
    top = games[0]
    where = f" vs {top['opponent']}" if top["opponent"] else ""
    if named:
        return f"{named}'s highest {label} total in a single game {when} was {top['value']}, on {top['date']}{where}."
    tied = [g for g in games if g["value"] == top["value"]]
    players = list(dict.fromkeys(g["player"] for g in tied))
    if len(players) > 1:
        # A player with two tied games is named once, with how many times
        # (ISSUES.md #262: "Stephen Curry and Stephen Curry tied for ...").
        tied_names = [f"{name}{_times_phrase(sum(g['player'] == name for g in tied))}" for name in players]
        names = ", ".join(tied_names[:-1]) + f" and {tied_names[-1]}"
        sentence = f"{names} tied for the most {label}s in a single game {when}, with {top['value']} each."
    elif len(tied) > 1:
        # One player, several games at the top: his games, by date.
        dates = " and ".join(f"on {g['date']}" + (f" vs {g['opponent']}" if g["opponent"] else "") for g in tied)
        sentence = f"{top['player']} had the most {label}s in a single game {when}: {top['value']}, {_count_times(len(tied))} - {dates}."
    else:
        sentence = f"{top['player']} had the most {label}s in a single game {when}: {top['value']}, on {top['date']}{where}."
    rest = [f"{g['player']} ({g['value']})" for g in games if g["value"] != top["value"]]
    return sentence + (f" Next: {', '.join(rest)}." if rest else "")


def _count_times(count: int) -> str:
    """``"twice"``, ``"3 times"``."""
    return "twice" if count == 2 else f"{count} times"


def _times_phrase(count: int) -> str:
    """`` (twice)`` beside a name tied with itself as well as with others; nothing for one game."""
    return f" ({_count_times(count)})" if count > 1 else ""


def say_single_game_high(result: Result) -> Reply:
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
    stat = _facts(result, CountFacts).stat
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
        "empty_box_scores": _facts(result, CountFacts).empty_box_scores,
        "headline": headline,
        "notes": [redirect.strip()] if redirect else [],
    }
    return Reply(data=data, answer=headline + redirect)


# --- runs ------------------------------------------------------------------------------


def streak_result(want_win: bool) -> str:
    """What a run of results is called: "winning streak" or "losing streak".

    .. versionadded:: 5.0.0
    """
    return "winning streak" if want_win else "losing streak"


def say_one_run(subject: str, label: str, runs: Sequence[Run], rule: str, *, season: int | None, still_open: bool, who: dict[str, Any]) -> Reply:
    """One named player's or team's longest run, with any run that ties it,
    under ``subject`` ("Nikola Jokic's longest run of consecutive games with
    20+ points") and ``label`` (the span): its length and days, the season
    it fell in where the span covers several, and - ``still_open`` - that
    it was still going at the last game on record. ``rule`` is the
    definition beneath it, already phrased. A team's streak is said here
    too (:func:`say_streak`).

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
    return Reply(data={**who, "span": label, "streaks": streaks, "headline": answer, "notes": [rule.strip()]}, answer=f"{answer}\n{rule}")


def say_run_listing(runs: Sequence[Run], what: str, rule: str, label: str, where: str, *, by_stat: bool, stat: Any, threshold: Any, unit: str, want_win: bool) -> Reply:
    """The league's longest runs with nobody named: one per player (a stat
    streak) or one per team-season (a win/loss streak, the team relation's,
    ``compose.runs.read_team_streak``), each under its ``owner``, a
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
        return Reply(data={"span": label, "streaks": [], "headline": message}, answer=message)
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
    return Reply(data=data, answer=answer)


def _where_in_span(span: Span) -> str:
    """A span with no run in it, in words: the season, or every season of
    its type from the relation's floor on."""
    return f"in the {span.phrase}" if span.season is not None else f"in any {SEASON_TYPE_NAMES.get(span.season_type or 2, 'regular season')} on record ({span.floor} onward)"


def say_streak(result: Result) -> Reply:
    """A player's longest run, or the league's longest one per player,
    worded: the run (and any that tie it) under the player's name and the
    span, or the listing under its leader, then the rule beneath - only
    games he played count, and a game with no box score ends a run - and
    whether a run is still going.

    .. versionadded:: 5.0.0
    """
    body = result.runs
    assert body is not None and result.span.phrase is not None
    line, want_win = body.line, body.won
    by_stat = line is not None
    stat, threshold = (line.column, line.value) if line is not None else (None, None)
    unit = _unit(stat)
    label = result.span.phrase
    rule = "".join(note(each.kind, note_phrase(each), **each.facts) for each in result.notes if each.kind == "definition")
    if result.relation == "everyone":
        # The league's runs: one per player along a line, or one per
        # team-season of wins or losses.
        what = f"run of consecutive games with {threshold}+ {unit}" if by_stat else streak_result(want_win)
        return say_run_listing(body.runs, what, rule, label, _where_in_span(result.span), by_stat=by_stat, stat=stat, threshold=threshold, unit=unit, want_win=want_win)
    filters = result.narrowing.phrase
    still_open = any(each.kind == "still_open" for each in result.notes)
    if result.relation == "team":
        return _say_team_run(result, body, rule, want_win=want_win, still_open=still_open)
    if not body.runs:
        never = f"never had a game with {threshold}+ {unit}" if by_stat else f"never {'won' if want_win else 'lost'} a game he played"
        message = f"{result.subject} {never}{filters} in the {label}."
        return Reply(data={"player": result.subject, "span": label, "streaks": [], "headline": message}, answer=message)
    what = f"consecutive games with {threshold}+ {unit}" if by_stat else f"{streak_result(want_win)} in games he played"
    subject = (f"{result.subject}'s longest run of {what}" if by_stat else f"{result.subject}'s longest {what}") + filters
    return say_one_run(subject, label, body.runs, rule, season=result.span.season, still_open=still_open, who={"player": result.subject})


def _say_team_run(result: Result, body: Runs, rule: str, *, want_win: bool, still_open: bool) -> Reply:
    """A named team's longest run of wins or losses (and any that tie it),
    or that it never won (or lost) a game in the span narrowed so -
    ``streak``'s retired team branch, word for word."""
    team, label, filters = result.subject, result.span.phrase or "", result.narrowing.phrase
    if not body.runs:
        message = f"The {team} did not {'win' if want_win else 'lose'} a game{filters} in the {label}."
        return Reply(data={"team": team, "span": label, "streaks": [], "headline": message}, answer=message)
    what = streak_result(want_win)
    subject = (f"The {team}' longest {what}" if team.endswith("s") else f"The {team}'s longest {what}") + filters
    return say_one_run(subject, label, body.runs, rule, season=result.span.season, still_open=still_open, who={"team": team})


# --- two players' meetings -------------------------------------------------------------

#: The comparison's rows beneath the record: each player's per-game figure over the meetings, by key and header.
_MATCHUP_LINE: tuple[tuple[str, str], ...] = (("minutes", "minutes"), ("points", "points"), ("rebounds", "rebounds"), ("assists", "assists"), ("fg_pct", "FG%"))


def _matchup_absence_said(absence: Mapping[str, Any] | None) -> str:
    """How often the two met with a teammate's absence dropped, where it
    emptied the meetings - the reading the question probably meant."""
    if absence is None:
        return ""
    names, met, player = _joined(list(absence["names"])), absence["met"], absence["player"]
    return (
        f" Over {absence['first']}-{absence['last']} they met {met} time{'s' if met != 1 else ''} in all, {absence['beside']} of them with {names} playing beside {player}; "
        f"'without {names}' counts only the games he missed while on {player}'s team, and there were none among their meetings."
    )


def _matchup_none(result: Result, caveat: str) -> Reply:
    """Two players who never met in scope, said by what the read narrowed:
    "never played against each other", or no meetings in the first
    player's games narrowed that way, and the games they shared as
    teammates where every shared game was one."""
    facts, met = _facts(result, MatchupFacts), result.narrowing.cell(Met)
    assert met is not None
    a, b, together = result.subject, met.other, facts.teammate_games
    where = _where_in_span(result.span)
    teammates = f" - they were teammates in all {together} games they both played" if together else ""
    said = caveat + _matchup_absence_said(facts.absence)
    narrowing = result.narrowing.phrase
    # With a narrowing, "never played against each other" would be false of
    # two players who met whenever the narrowing was not in force.
    head = f"No meetings between {a} and {b} in {a}'s games{narrowing}" if narrowing else f"{a} and {b} never played against each other"
    message = f"{head} {where}{teammates}.{said}"
    return Reply(data={"players": [a, b], "meetings": 0, "teammate_games": together, "headline": message}, answer=message)


def say_player_matchup(result: Result) -> Reply:
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
    return Reply(data=data, answer=answer)


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


def say_leaderboard(result: Result) -> Reply:
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
    facts = _facts(result, RankingFacts)
    label, fields, ratio = facts.label, list(facts.fields), facts.ratio
    rows = _leaderboard_rows(body)
    season = result.span.season
    assert season is not None
    period = season_phrase(season, result.span.season_type or 2)
    for each in result.decisions:
        if each.kind == "season_default":
            # The heading's own span phrase, written through the decision so
            # the answer records that the season was chosen, not asked.
            period = decision_phrase(each)
    where = f"the {facts.team}" if facts.team else "the league"
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
    return Reply(data=data, answer=answer + trade_note)


def _say_career_leaderboard(result: Result, body: Grouped) -> Reply:
    """A career ranking. Says whose careers, every time: the pool is every
    player active in 1993-94 or later, counted over his whole career, and
    nobody whose career ended before it - Kareem Abdul-Jabbar is not in the
    warehouse at all - so presenting it as "all-time" would be the
    unrepresentative ranking ``nba/coverage.py``'s second floor exists to
    refuse."""
    rows = _leaderboard_rows(body)
    ratio = _facts(result, RankingFacts).ratio
    kind = SEASON_TYPE_NAMES.get(result.span.season_type or 2, "regular season")
    label = f"career {_facts(result, RankingFacts).label.removeprefix('total ')}"
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
    return Reply(
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


def _say_player_line_advanced(result: Result, line: Scalar) -> Reply:
    """A computed advanced stat's line: the figure, its volume - a rate
    without it is the thing people ask "out of how many?" about - the
    games, and the seasons it could not see."""
    facts = _facts(result, LineFacts)
    stat, span, name = facts.stat, result.span, result.subject
    spec = ADVANCED_STATS[stat]
    if not line.values:
        message = f"{name} has no {spec.label} on record {span.phrase} - it is computed from box scores, which start in 1994."
        return Reply(data={"player": name, "stat": stat, "stats": {}}, answer=message)
    value, volume, games = line.values[stat], line.sums["volume"], line.games
    printed = _advanced_value(spec.percentage, value)
    behind = f" on {int(volume):,} {spec.volume}" if volume is not None and spec.volume else ""
    about: dict[str, Any] = {"span": "career", "seasons": [span.first, span.last], "season_count": facts.season_count} if span.career else {"season": span.first}
    sentence = f"{name} has a {printed} {spec.label} {span.phrase}{behind}, in {int(games):,} games." if games else f"{name} has a {printed} {spec.label} {span.phrase}{behind}."
    data = {"player": name, "stat": stat, **about, "stats": {spec.column: value, "games_played": int(games) if games is not None else None}, "seasons_missing": line.sums["seasons_missing"]}
    return Reply(data=data, answer=sentence + "".join(_said(result)))


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


def _player_line_empty(result: Result) -> Reply:
    """No line on record: the season's or the career's sentence, and where
    the season was defaulted, the seasons he IS on record for."""
    span, name = result.span, result.subject
    if span.career:
        kind = SEASON_TYPE_NAMES.get(span.season_type or 2, "regular season")
        return Reply(data={"player": name, "span": "career", "stats": {}}, answer=f"{name} has no {kind} numbers in the warehouse.")
    assert span.season is not None
    answer = f"{name} has no {season_phrase(span.season, span.season_type or 2)} numbers in the warehouse." + "".join(decision_phrase(each) for each in result.decisions)
    return Reply(data={"player": name, "season": span.season, "stats": {}}, answer=answer)


def say_player_line(result: Result) -> Reply:
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
    facts = _facts(result, LineFacts)
    if facts.stat in ADVANCED_STATS:
        return _say_player_line_advanced(result, line)
    if not line.values and not line.sums:
        return _player_line_empty(result)
    span, name, wanted = result.span, result.subject, list(facts.wanted)
    if span.career:
        seasons, kind = facts.season_count, SEASON_TYPE_NAMES.get(span.season_type or 2, "regular season")
        plural = "" if seasons == 1 else "s"
        when = f"over his career ({seasons} {kind}{plural}, {span.first}-{span.last})" if span.first != span.last else f"over his career (the {span.first} {kind})"
        about: dict[str, Any] = {"span": "career", "seasons": [span.first, span.last], "season_count": seasons}
        period = f"career {kind}s"
    else:
        assert span.season is not None and span.phrase is not None
        when, about, period = span.phrase, {"season": span.season, "season_n": facts.season_n}, season_phrase(span.season, span.season_type or 2)
    stat = facts.stat
    if stat is not None:
        shooting = SHOOTING_STATS[stat]
        values = {"gamesPlayed": line.games, shooting.made: line.sums["made"], shooting.attempted: line.sums["attempted"]}
        return shooting_result(name, about, values, shooting, when=when)
    values, attempted = _player_line_values(result, line, wanted)
    answer = phrase_player_stat(name, period, values, wanted, when=when, attempted=attempted)
    return Reply(data={"player": name, **about, "stats": values, "labels": stat_value_labels(wanted)}, answer=answer)


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


def say_player_history(result: Result) -> Reply:
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
    stat, name, span = _facts(result, LineFacts).stat, result.subject, result.span
    label, columns = HISTORY_COLUMNS[stat]
    period = SEASON_TYPE_NAMES.get(span.season_type or 2, "regular season")
    history = [{"season": row["key"], **{k: v for k, v in row.items() if k != "key"}} for row in groups.rows]
    answer = _history_table(name, label, period, history, columns, career=span.career)
    summary = result.parts[1].body if len(result.parts) > 1 else None
    if isinstance(summary, Scalar):
        answer += f"\n{_history_career_line(name, label, summary)}"
    labels = {k: h for _, h, k in columns}
    return Reply(data={"player": name, "stat": stat, "span": "career" if span.career else None, "seasons": history, "labels": labels}, answer=answer)


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


def say_player_compare(result: Result) -> Reply:
    """Two or more players' season lines side by side, worded: a table of
    the games and each stat per game, the NetPoints summary beneath where
    anybody has a row, and which players have no line that season - the
    retired ``player_compare`` template's words.

    .. versionadded:: 5.0.0
    """
    groups = result.grouped
    assert groups is not None and result.span.season is not None
    wanted = list(_facts(result, LineFacts).wanted)
    columns = {stat: PLAYER_STAT_COLUMNS[stat][0] for stat in wanted}
    rows = {line["key"]: ({"gamesPlayed": line["games"], **{columns[stat]: line[stat] for stat in wanted}} if "games" in line else {}) for line in groups.rows}
    detail = result.parts[1].body if len(result.parts) > 1 else None
    netpoints = {line["key"]: {k: v for k, v in line.items() if k != "key"} for line in detail.rows} if isinstance(detail, Grouped) else {}
    missing_note = "".join(_said(result))
    answer = _compare_table(rows, wanted, season_phrase(result.span.season, result.span.season_type or 2), netpoints, missing_note)
    data = {"season": result.span.season, "players": rows, "netpoints": netpoints, "headline": answer.split("\n")[0].rstrip(":"), "notes": [missing_note] if missing_note else []}
    return Reply(data=data, answer=answer)


# --- a team's own season: the power index ------------------------------------------


def record_pct(value: float) -> str:
    """Basketball convention for a winning percentage: .646, not 0.646.

    .. versionadded:: 5.0.0
       ``templates.teams._record_pct``, for the team-season sayers.
    """
    text = f"{value:.3f}"
    return text[1:] if text.startswith("0") else text


def _team_outlook_record_line(kind: int | None, wins: Any, losses: Any, proj_w: Any, proj_l: Any) -> str:
    """The record line: for a postseason snapshot, the finished regular
    season plus any playoff games added on top - a team whose record still
    equals the projection played none (the 2026 Hornets, out in the
    play-in) - or the record so far with a projection otherwise."""
    played = int(wins) + int(losses)
    regular = (round(proj_w), round(proj_l)) if proj_w is not None and proj_l is not None else None
    if kind == 3:
        # In a postseason snapshot the "projection" is the finished regular
        # season, and the record adds the playoff games to it.
        if regular and played > sum(regular):
            return f"  record {int(wins)}-{int(losses)} including the playoffs; {regular[0]}-{regular[1]} in the regular season"
        return f"  record {int(wins)}-{int(losses)}, no playoff games"
    projection = f", projected {regular[0]}-{regular[1]}" if regular else ""
    return f"  record {int(wins)}-{int(losses)}{projection}" if played else f"  no games played yet{projection}"


def _team_outlook_noted(result: Result, *kinds_of: str) -> list[str]:
    """The Result's notes whose ``what`` is one of ``kinds_of``, phrased and
    recorded, in the order the read made them."""
    return [note(each.kind, note_phrase(each), **each.facts) for each in result.notes if each.facts.get("what") in kinds_of]


def _team_outlook_missing(result: Result) -> Reply:
    """No snapshot of the kind asked for holds the team: which snapshots the
    season has, and which the team is missing from - "no data" would send
    the reader to the wrong place - and, where one holds it, the hint."""
    season, team = result.span.season, result.subject
    facts = _facts(result, OutlookFacts)
    snapshots = facts.snapshots
    listing = [_snapshot_described(each) for each in snapshots]
    if not snapshots:
        message = f"ESPN's power index has no {season} snapshot in the warehouse."
        return Reply(data={"team": team, "season": season, "message": message}, answer=message)
    if facts.postseason and not any(each["kind"] == "postseason" for each in snapshots):
        gap = "and no postseason snapshot"
    elif facts.postseason:
        gap = f"and the {team} are not in its postseason snapshot"
    else:
        gap = f"and the {team} are {'not in it' if len(listing) == 1 else 'in neither' if len(listing) == 2 else 'in none of them'}"
    message = f"ESPN's power index for {season} has {_joined(listing)}, {gap}." + "".join(_team_outlook_noted(result, "regular_season"))
    return Reply(data={"team": team, "season": season, "snapshots": listing, "message": message}, answer=message)


def _team_outlook_bpi_line(result: Result, line: Scalar) -> str:
    """The BPI line, or the note that the snapshot carries no rating - never
    dropped, since the power index IS this answer's headline: ESPN's 2026
    regular-season snapshot is the live case, all 30 of its teams with a
    NULL ``bpi`` while their records, projections, chances and SOS are
    populated (measured 2026-09-18)."""
    withheld = _team_outlook_noted(result, "bpi")
    if withheld:
        return withheld[0]
    values = line.values
    offense, defense = values["bpi_offense"], values["bpi_defense"]
    detail = f" (offense {offense:+.1f}, defense {defense:+.1f})" if offense is not None and defense is not None else ""
    return f"  BPI {values['bpi']:+.1f}{detail}, {ordinal_word(int(values['higher']) + 1)} of the {_facts(result, OutlookFacts).teams_in_snapshot} teams in the snapshot"


def _team_outlook_lines(result: Result, line: Scalar, chances: Grouped) -> tuple[list[str], str, str | None, str | None]:
    """The answer's lines in order, and the three the page shows beneath its
    card: the BPI line, the strength of schedule and the other snapshots."""
    facts, values = _facts(result, OutlookFacts), line.values
    kind = facts.kind
    lines = [f"ESPN's power index for the {result.subject}, {result.span.season} {facts.snapshot} snapshot (updated {(facts.updated or '')[:10]}, {facts.teams_in_snapshot} teams):"]
    lines += _team_outlook_noted(result, "postseason_substitute", "stamped_after_season")
    bpi_line = _team_outlook_bpi_line(result, line)
    lines.append(bpi_line)
    wins, losses = values["wins"], values["losses"]
    if wins is not None and losses is not None:
        lines.append(_team_outlook_record_line(kind, wins, losses, values["projected_wins"], values["projected_losses"]))
    odds = [f"{row['key']} {row['chance']:.1f}%" for row in chances.rows if row["chance"] is not None]
    if odds:
        lines.append("  chances: " + ", ".join(odds))
    sos, sos_rank = values["strength_of_schedule"], values["strength_of_schedule_rank"]
    sos_line = None
    if sos is not None and 0 < sos < 1:
        # ESPN's schedule-strength rank is a league-wide rank only from 2022;
        # the values before it (7,909 to 59,238) are not ranks.
        rank_note = f", {ordinal_word(int(sos_rank))} hardest in the league" if sos_rank is not None and 1 <= sos_rank <= 30 else ""
        sos_line = f"  strength of schedule {record_pct(sos)}{rank_note}"
        lines.append(sos_line)
    others = _team_outlook_noted(result, "other_snapshots")
    others_line = others[0] if others else None
    if others_line is not None:
        lines.append(others_line)
    return lines, bpi_line, sos_line, others_line


def say_team_outlook(result: Result) -> Reply:
    """A team's ESPN power index, worded: the snapshot it was read from (its
    kind, date and size) and what is odd about it, the BPI line, the record
    and its projection, the chances, the strength of schedule and the
    season's other snapshots - or, where no snapshot of the kind asked for
    holds the team, which snapshots exist. ``templates.teams.team_outlook``'s
    words, from the Result.

    .. versionadded:: 5.0.0
    """
    line = result.scalar
    chances = result.parts[1].body if len(result.parts) > 1 else None
    if line is None or not line.values or not isinstance(chances, Grouped):
        return _team_outlook_missing(result)
    facts, values = _facts(result, OutlookFacts), line.values
    lines, bpi_line, sos_line, others_line = _team_outlook_lines(result, line, chances)
    data: dict[str, Any] = {
        "team": result.subject,
        "season": result.span.season,
        "snapshot": facts.snapshot,
        "updated": (facts.updated or "")[:10],
        "teams_in_snapshot": facts.teams_in_snapshot,
        "bpi": values["bpi"],
        "bpi_offense": values["bpi_offense"],
        "bpi_defense": values["bpi_defense"],
        "position": int(values["higher"]) + 1,
        "wins": values["wins"],
        "losses": values["losses"],
        # In a postseason snapshot ESPN's "projection" columns hold the
        # finished regular season (see _team_outlook_record_line), so they
        # are filed under that name: the page drew "PROJECTED 45-37" beside
        # "RECORD 49-44" for a team whose season was over (Jeff's session,
        # 2026-09-24).
        **(
            {"regular_season_wins": values["projected_wins"], "regular_season_losses": values["projected_losses"]}
            if facts.kind == 3
            else {"projected_wins": values["projected_wins"], "projected_losses": values["projected_losses"]}
        ),
        "chances": {row["key"]: row["chance"] for row in chances.rows},
        "strength_of_schedule": values["strength_of_schedule"],
    }
    # The page draws its own BPI/record/chances card from the typed values
    # above (RENDERERS.team_outlook, web/static/index.html), so its notes
    # are only the lines the card does NOT carry: the BPI's offense/defense
    # split, the strength-of-schedule RANK, and the other snapshots. The
    # record and chances lines restate the card's own boxes and were shown
    # beneath them, twice over (Jeff's session, 2026-09-24); the CLI's text
    # keeps every line.
    data["headline"] = lines[0].rstrip(":")
    data["notes"] = [each.strip() for each in (bpi_line, sos_line, others_line) if each is not None]
    return Reply(data=data, answer="\n".join(lines))


# --- a team's own season: its line ---------------------------------------------------


def tally(wins: int, losses: int) -> str:
    """A record and its percentage: "53-29 (.646)", or "0-0".

    .. versionadded:: 5.0.0
       ``templates.teams._tally``, for the team-season sayers.
    """
    return f"{wins:,}-{losses:,} ({record_pct(wins / (wins + losses))})" if wins + losses else "0-0"


def possessive(name: str) -> str:
    """ "the Knicks'" but "the Thunder's" - a team name is plural only sometimes.

    .. versionadded:: 5.0.0
       ``templates.teams._possessive``, for the team-season sayers.
    """
    return f"{name}'" if name.endswith("s") else f"{name}'s"


def metric_cell(metric: TeamMetric, value: float) -> str:
    """A team metric's value as a table shows it: one decimal, a percentage marked.

    .. versionadded:: 5.0.0
       ``templates.teams._metric_cell``.
    """
    return f"{value:.1f}%" if metric.percent else f"{value:.1f}"


def short_of_games_said(metric: TeamMetric, period: str, short: dict[str, Any]) -> str:
    """Why an opponent-based metric has no value: ESPN's game list holds
    fewer of a team's games than its season totals count, and points allowed
    over fewer games than everything else would make the figure wrong
    without looking wrong.

    .. versionadded:: 5.0.0
       ``templates.teams._incomplete_opponents``' sentence.
    """
    others = short["others"]
    return (
        f"{metric.label.capitalize()} can't be given for the {period}: ESPN's game list holds "
        f"{short['listed']} of the {possessive(short['team'])} {short['games']} games"
        + (f", and is short for {others} other team{'s' if others != 1 else ''}" if others else "")
        + " - points allowed over fewer games than everything else would make the figure wrong without looking wrong."
    )


def _team_stat_single(result: Result, period: str, stats: dict[str, dict[str, Any]], row: dict[str, Any]) -> Reply:
    """One named metric: its value and rank, or why points allowed leave it blank."""
    team, season, facts = result.subject, result.span.season, _facts(result, TeamStatFacts)
    metric = TEAM_METRICS[row["key"]]
    if row["value"] is None:
        assert facts.short is not None
        answer = short_of_games_said(metric, period, dict(facts.short))
        return Reply(data={"team": team, "season": season, "stats": stats, "message": answer}, answer=answer)
    where = ""
    if row["rank"] is not None:
        order = "best" if metric.lower_is_better is not None else "highest"
        where = f", {ordinal_word(row['rank'])}-{order} of {row['of']} teams"
    answer = f"The {possessive(team)} {metric.label} was {metric_cell(metric, row['value'])} in the {period} ({facts.games} games){where}."
    for each in result.notes:
        answer += f" {note(each.kind, note_phrase(each), **each.facts)}"
    # `headline` matches the page's own firstLine(text) fallback exactly (the
    # whole thing - this answer is one line even with the rating note glued
    # on) rather than the note-free sentence alone: the renderer's own
    # `caption` is `firstLine(text)`, and a shorter headline here would make
    # the two disagree and print the caption a second time, duplicating the
    # note (measured on the rendered page, 2026-09-24). No separate `notes`
    # entry either, for the same reason - the note is already inside
    # `headline`, and `notes` has no way here to say it is the same text.
    return Reply(data={"team": team, "season": season, "games": facts.games, "stats": stats, "headline": answer, "notes": []}, answer=answer)


def _team_stat_table(result: Result, period: str, stats: dict[str, dict[str, Any]], rows: Sequence[dict[str, Any]]) -> Reply:
    """The compact line: a table of value and rank, with the notes beneath."""
    games = _facts(result, TeamStatFacts).games
    label_width = max(len(label) for label in stats)
    cells = {TEAM_METRICS[row["key"]].label: "-" if row["value"] is None else metric_cell(TEAM_METRICS[row["key"]], row["value"]) for row in rows}
    value_width = max(5, *(len(c) for c in cells.values()))
    lines = [f"{result.subject}, {period} ({games} games):", f"{' ' * label_width}  {'value'.rjust(value_width)}  rank"]
    for label, entry in stats.items():
        rank_cell = f"{ordinal_word(entry['rank'])} of {entry['of']}" if entry["rank"] is not None else "-"
        lines.append(f"{label.ljust(label_width)}  {cells[label].rjust(value_width)}  {rank_cell}")
    notes = _said(result)
    return Reply(
        data={"team": result.subject, "season": result.span.season, "games": games, "stats": stats, "headline": lines[0].rstrip(":"), "notes": notes},
        answer="\n".join([*lines, *notes]),
    )


def say_team_stat(result: Result) -> Reply:
    """One team's season numbers, worded: a record and its rank, one metric's
    value and rank (with the rating formula beneath a rating or the pace),
    or the compact line as a table with what a rank means and what was left
    out - or, with nothing to give, which fact is missing.
    ``templates.teams.team_stat``'s words, from the Result.

    .. versionadded:: 5.0.0
    """
    assert result.span.season is not None and result.span.season_type is not None
    period = season_phrase(result.span.season, result.span.season_type)
    line = result.scalar
    if line is not None:
        values = line.values
        answer = f"The {result.subject} were {tally(values['wins'], values['losses'])} in the {period}, the {ordinal_word(values['rank'])}-best record of {values['of']} teams."
        return Reply(data={"team": result.subject, "season": result.span.season, **values}, answer=answer)
    body = result.grouped
    assert body is not None
    stats = {TEAM_METRICS[row["key"]].label: {"value": row["value"], "rank": row["rank"], "of": row["of"]} for row in body.rows}
    if _facts(result, TeamStatFacts).metric is not None:
        return _team_stat_single(result, period, stats, dict(body.rows[0]))
    return _team_stat_table(result, period, stats, [dict(row) for row in body.rows])


# --- a team's own season: every team ranked -------------------------------------------

#: How a ranking's venue is said in its title.
_VENUE_WORDS = {"home": "at home", "away": "on the road"}


def _team_leaderboard_period(span: Span) -> str:
    """The span a ranking covers: "seasons 2011-2019", "seasons since 2022",
    or one season's own name."""
    if span.last is not None:
        return f"seasons {span.first}-{span.last}"
    if span.first is not None:
        return f"seasons since {span.first}"
    assert span.season is not None and span.season_type is not None
    return season_phrase(span.season, span.season_type)


def _team_leaderboard_end(metric: TeamMetric, key: str, rank_word: str | None, descending: bool) -> str:
    """Which end the ranking lists first - said, because a list of the
    fastest teams under a question about the slowest would otherwise look
    perfectly right."""
    if metric.lower_is_better is None or rank_word in ("most", "fewest"):
        end = "highest first" if descending else "lowest first"
    else:
        best_first = descending == (metric.lower_is_better is False)
        end = ("best first" if best_first else "worst first") + (" (highest)" if descending else " (lowest)")
    if key in ("record", "losses"):
        end = "best record first" if (key == "record") == descending else "worst record first"
    return end


def say_team_leaderboard(result: Result) -> Reply:
    """Every team ranked by one metric, worded: the title (the metric, a venue,
    the span), which end comes first, how many teams were ranked, the rows
    shown (a named team's own past a "..."), and the rating formula beneath a
    rating or the pace - or why nothing could be ranked.
    ``templates.teams.team_leaderboard``'s words, from the Result.

    .. versionadded:: 5.0.0
    """
    facts = _facts(result, TeamRankingFacts)
    key = facts.metric
    metric = TEAM_METRICS[key]
    period = _team_leaderboard_period(result.span)
    venue = result.narrowing.venue
    title = f"{metric.label.capitalize()}{f' {_VENUE_WORDS[venue]}' if venue else ''}, {period}"
    season = None if result.span.first is not None else result.span.season
    body = result.grouped
    assert body is not None
    if not body.rows:
        answer = f"The warehouse has no {period} numbers to rank teams by {metric.label}."
        return Reply(data={"question_shape": title, "season": season, "teams": [], "headline": answer}, answer=answer)
    end = _team_leaderboard_end(metric, key, facts.rank, facts.descending)
    display = {row["key"]: tally(row["wins"], row["losses"]) if metric.expression is None else metric_cell(metric, row["value"]) for row in body.rows}
    name_width = max(len(row["key"]) for row in body.rows)
    value_width = max(len(cell) for cell in display.values())
    lines: list[str] = []
    for row in body.rows:
        if row["beyond"] and not any(each.startswith("    ...") for each in lines):
            lines.append("    ...")
        lines.append(f"{row['rank']:>2}  {row['key'].ljust(name_width)}  {display[row['key']].rjust(value_width)}")
    headline = f"{title} - {end}, of {facts.of} teams:"
    notes = _said(result)
    teams = [{"rank": row["rank"], "team": row["key"], "value": row["value"], "display": display[row["key"]]} for row in body.rows]
    return Reply(data={"question_shape": title, "season": season, "order": end, "teams": teams, "headline": headline.rstrip(":"), "notes": notes}, answer="\n".join([headline, *lines, *notes]))


# --- a team with and without named teammates ---------------------------------


def _with_without_verbs(predicates: list[Any]) -> tuple[str, str]:
    """How the two rows name their side: "played"/"out" for appearances,
    "started"/"did not start" for starts, "came off the bench"/"started or
    out" for the bench, "had 20+ points"/"did not" for a line - the words
    the question used, never "played" for a start it asked about."""
    kinds = {p for p, _ in predicates}
    if kinds == {"started"}:
        return "started", "did not start"
    if kinds == {"bench"}:
        return "came off the bench", "started or out"
    if kinds == {"reached"}:
        lines = [f"{line[1]}+ {STAT_LABELS.get(line[0], line[0])}s" for _, line in predicates if line is not None]
        return f"had {_joined(sorted(set(lines)))}", "did not"
    if kinds == {"played"}:
        return "played", "out"
    return "met the condition", "did not"


def _with_without_heading(result: Result, named: list[str], all_of: str) -> tuple[str, list[str], str]:
    """The table's title and headers, and the phrase naming whose time
    together is counted - with a player subject's own columns added to the
    headers. An opponent the question named goes in the TITLE: a record
    over one opponent's games, headed as though it covered every game, is
    the silent narrowing the split exists to stop."""
    facts = _facts(result, RecordFacts)
    subject, counted_teams, label = facts.player, ", ".join(facts.teams), result.span.phrase
    headers = ["G", "W-L", "Win%", "Margin"]
    versus = result.narrowing.phrase
    if subject is None:
        whose = f"{all_of}'s time with the team" if len(named) == 1 else f"the time {all_of} were on the team together"
        return f"{counted_teams} with and without {all_of}{versus}, {label}:", headers, whose
    whose = f"the time {subject} and {all_of} were both on the team" if len(named) == 1 else f"the time {_joined([subject, *named])} were on the team together"
    return f"{subject} with and without {all_of} ({counted_teams}){versus}, {label}:", [*headers, "Played", "MIN", "PTS", "REB", "AST", "FG%"], whose


def say_with_without(result: Result) -> Reply:
    """A team's record with and without named teammates, worded as the
    retired ``with_without`` template said it: the two rows side by side
    per team (the question's own side first), with the subject's averages
    where a player is named, under a title naming the teams, the opponent
    and the span; then what was counted, what "played" (or "out") means,
    the games no box score shows and a player subject's columns.

    .. versionadded:: 5.0.0
    """
    groups = result.grouped
    assert groups is not None
    facts, companions = _facts(result, RecordFacts), groups.of
    assert isinstance(companions, Companions)
    named = list(companions.names)
    all_of, any_of = _joined(named), _joined(named, "or")
    subject, asked_without, teams = facts.player, companions.absent, list(facts.teams)
    verbs = _with_without_verbs(list(companions.predicates))
    rows: list[tuple[str, list[str]]] = []
    for group in groups.rows:
        played = group["teammate_played"]
        cells = [str(group["games"]), f"{group['wins']}-{group['losses']}", _win_pct(group["wins"], group["games"]), _margin(group["avg_margin"])]
        if subject is not None:
            cells += [str(group["player_games"]), *(_cell(group[k]) for k in ("minutes", "points", "rebounds", "assists", "fg_pct"))]
        prefix = f"{group['team']}, " if len(teams) > 1 else ""
        # "A and B out" against "A or B played": the row label says which
        # of the two it is, since with two names they are not opposites.
        whom = (any_of if asked_without else all_of) if played else (all_of if asked_without else any_of)
        rows.append((f"{prefix}{whom} {verbs[0] if played else verbs[1]}", cells))
    title, headers, whose = _with_without_heading(result, named, all_of)
    # "he" for one teammate, "they" for several (ISSUES.md #313, item 2).
    unknown = f"whether {'they' if len(named) > 1 else 'he'} played is unknown; they are on neither side"
    notes = [note(each.kind, note_phrase(each, about=whose, consequence=unknown), **each.facts) for each in result.notes]
    tenure = next(list(each.facts["stints"]) for each in result.notes if each.facts.get("term") == "tenure_counted")
    data = {
        "teammate": all_of,
        "teammates": named,
        "player": subject,
        "teams": teams,
        "span": result.span.phrase,
        "groups": list(groups.rows),
        "tenure": tenure,
        "headline": title.rstrip(":"),
        "notes": notes,
    }
    return Reply(data=data, answer=_table(title, headers, rows) + "\n" + " ".join(notes))


# --- two teams' meetings ------------------------------------------------------------


def _head_to_head_series(lead: str, a: str, b: str, a_wins: int, b_wins: int, *, won: str = "won the series") -> str:
    """The meetings' lead, then who won them: split evenly, or the leader's
    margin."""
    if a_wins == b_wins:
        return f"{lead}, splitting them {a_wins}-{b_wins}."
    leader, trailing = (a, f"{a_wins}-{b_wins}") if a_wins > b_wins else (b, f"{b_wins}-{a_wins}")
    return f"{lead}; the {leader} {won} {trailing}."


def _head_to_head_where(result: Result, a: str) -> str:
    """Where a venue or a date narrowed the meetings: "on 2026-01-05", or
    "in the Lakers' home games of the 2026 regular season" (since a season,
    from one through another, or on record, over a span)."""
    span, facts = result.span, _facts(result, MeetingsFacts)
    if span.date:
        return f"on {span.date}"
    # "Lakers'", not "Lakers's" - most team names end in "s".
    possessive = f"{a}'" if a.endswith("s") else f"{a}'s"
    since, until = span.since, span.until
    if since is not None and until is not None:
        within = f"from {since} through {until}"
    elif since is not None:
        within = f"since {since}"
    elif facts.span == "career":
        within = "on record"
    else:
        assert span.season is not None
        within = f"of the {season_phrase(span.season, span.season_type or 2)}"
    return f"in the {possessive} {'home' if result.narrowing.venue == 'home' else 'road'} games {within}"


def _head_to_head_sentence(result: Result, a: str, b: str, a_wins: int, b_wins: int) -> str:
    """The meetings' one sentence: narrowed by a venue or a date, over a
    since-bounded or whole-career span, or one season's."""
    games = _facts(result, MeetingsFacts).games
    span = result.span
    times = "once" if games == 1 else f"{games} times"
    if result.narrowing.venue or span.date:
        where = _head_to_head_where(result, a)
        if games == 0:
            return f"The warehouse has no games between the {a} and the {b} {where}."
        return _head_to_head_series(f"The {a} and the {b} met {times} {where}", a, b, a_wins, b_wins)
    if span.career:
        if games == 0:
            since, until = span.since, span.until
            when = f"from {since} through {until}" if since is not None and until is not None else f"since {since}" if since is not None else "on record"
            return f"The {a} and the {b} have not played each other {when}."
        return _head_to_head_series(f"The {a} and the {b} have met {times} {span.phrase}", a, b, a_wins, b_wins, won="lead the all-time series")
    assert span.season is not None
    period = season_phrase(span.season, span.season_type or 2)
    if games == 0:
        return f"The warehouse has no {period} games between the {a} and the {b}."
    return _head_to_head_series(f"The {a} and the {b} met {times} in the {period}", a, b, a_wins, b_wins)


def say_head_to_head(result: Result) -> Reply:
    """Two teams' meetings, worded: how many times they met in the span (one
    season, one date, a venue's games, a since-bounded or whole-career span)
    and who won them - the retired ``head_to_head`` template's words.

    .. versionadded:: 5.0.0
    """
    groups = result.grouped
    assert groups is not None
    (first, second) = groups.rows
    a, b = str(first["key"]), str(second["key"])
    a_wins, b_wins = int(first["wins"]), int(second["wins"])
    answer = _head_to_head_sentence(result, a, b, a_wins, b_wins)
    facts = _facts(result, MeetingsFacts)
    data: dict[str, Any] = {"teams": [a, b], "games": facts.games, "wins": {a: a_wins, b: b_wins}, "venue": result.narrowing.venue, "date": result.span.date}
    if result.span.career:
        data.update({"since": result.span.since, "until": result.span.until, "span": facts.span})
    data["headline"] = answer
    return Reply(data=data, answer=answer)


# --- a team's quarter or half ---------------------------------------------------------

#: Above this many games, a full per-game breakdown is unreadable rather than
#: informative - it only fires when no opponent narrows the season down.
_QUARTER_BREAKDOWN_LIMIT = 12


def _say_team_period_unknown(facts: Mapping[str, Any]) -> str:
    """The refusal for a team's stat nothing splits by period: the linescore
    holds the score, and the plays rebuild only the period line's columns."""
    from association.query.team_games import TEAM_PERIOD_COLUMNS

    stat = facts["stat"]
    held = _joined(["points", *(period_noun(c, 2) for c in TEAM_PERIOD_COLUMNS if c != "points"), "the field goal, 3-point and free throw percentages from them"])
    label = f"{STAT_LABELS[stat]}s" if stat in STAT_LABELS else str(stat)
    return f"A team's {label} by quarter is not on record: ESPN's linescore holds only the score, and play-by-play rebuilds only {held} - not {stat}."


def _say_team_period_unread(facts: Mapping[str, Any]) -> str:
    """The refusal where none of a team's games has the play-by-play its
    period ``measure`` is rebuilt from."""
    return f"No play-by-play is on record for these {facts['team']} games, and a team's {period_columns_noun(facts['measure'])} by quarter are rebuilt from it (it starts in 2002)."


def _say_team_period_untrusted(facts: Mapping[str, Any]) -> str:
    """The refusal for seasons whose team-games rebuild a period ``measure``
    right too seldom to answer, each with its measured agreement."""
    said = ", ".join(f"{season} ({pct:.0f}%)" for season, pct in facts["weak"])
    measure = facts["measure"]
    columns = period_columns_noun(measure)
    return f"A team's {facts['period']} {period_noun(measure, 2)} cannot be answered for {said}: rebuilt from play-by-play, a team-game's {columns} match its box score that seldom."


def _period_label(result: Result) -> str:
    """The quarter or half a read saw of each game, as the answer names it."""
    period = result.narrowing.period
    assert period is not None
    return period.label


def _team_period_about(result: Result, games: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The keys every team quarter answer's data opens with."""
    period = result.narrowing.period
    assert period is not None
    periods = list(period.periods)
    return {"team": result.subject, "opponent": result.narrowing.opponent, "period": periods[0] if len(periods) == 1 else None, "period_label": period.label, "games": list(games)}


def _team_period_rate(result: Result, played: list[Mapping[str, Any]], line: Scalar) -> Reply:
    """A team's shooting percentage in the period, over the games that
    reached it: one game said its own way, many listed beneath the figure as
    made-attempted. A "most"/"fewest" question is refused rather than
    ranked: one quarter's best percentage is whichever game went 2 for 2."""
    measure, team, label, span_words = _facts(result, TeamPeriodFacts).measure, result.subject, _period_label(result), result.span.phrase or ""
    made, attempted, pct = line.sums["made"], line.sums["attempted"], line.values[measure]
    word = PERIOD_RATE_WORDS[measure]
    data: dict[str, Any] = {**_team_period_about(result, played), "stat": measure, "total": made, "attempted": attempted, "average": pct}
    if _facts(result, TeamPeriodFacts).rank in ("most", "fewest"):
        message = (
            f"The {team}'s best or worst single {label} by {word} is not ranked: over a few attempts the extreme is whichever game went 2 for 2. "
            f"Ask for their {word} in the {label} over the span instead."
        )
        return Reply(data={**data, "message": message, "headline": message}, answer=message)
    vs = f" against the {result.narrowing.opponent}" if result.narrowing.opponent else ""
    did = period_rate_said(made, attempted, pct, measure)
    if len(played) == 1:
        g = played[0]
        paren = f" ({span_words})" if span_words else ""
        answer = f"The {team} {did} in the {label} against the {g['opponent']} on {g['date']}{_facts(result, TeamPeriodFacts).dateless}{paren}."
        return Reply(data={**data, "headline": answer}, answer=answer)
    header = f"The {team} {did} in the {label} across {len(played)} {span_words} games{vs}{result.narrowing.phrase}"
    if len(played) > _QUARTER_BREAKDOWN_LIMIT:
        return Reply(data={**data, "headline": header}, answer=header + ".")
    made_column, attempted_column = PERIOD_RATES[measure]
    lines = [f"  {g['date']}  {g[made_column]}-{g[attempted_column]}  {'-' if g[measure] is None else f'{g[measure]:.1f}%'}  vs {g['opponent']}" for g in played]
    return Reply(data={**data, "headline": header}, answer="\n".join([header + ":", *lines]))


def _team_period_extreme(result: Result, played: list[Mapping[str, Any]], data: dict[str, Any], line: Scalar) -> Reply:
    """The single game a "most"/"fewest" question asks for - the team
    counterpart of a single game's high - ties named together rather than
    resolved by whichever row sorted first."""
    facts = _facts(result, TeamPeriodFacts)
    measure, rank = facts.measure, facts.rank
    assert rank is not None
    best = line.values[rank]
    tied = [g for g in played if g[measure] == best]
    where = " and ".join(f"vs the {g['opponent']} on {g['date']}" for g in tied)
    figure = f"scored {best}" if measure == "points" else f"had {best} {period_noun(measure, best)}"
    vs = f" against the {result.narrowing.opponent}" if result.narrowing.opponent else ""
    answer = f"The {result.subject} {figure} in the {_period_label(result)} {where}, their {rank} in the {result.span.phrase or ''}{vs}{_facts(result, TeamPeriodFacts).dateless}."
    return Reply(data={**data, "rank": rank, "extreme": best, "extreme_games": tied, "headline": answer}, answer=answer)


def _team_period_sentence(result: Result, played: list[Mapping[str, Any]], total: int) -> str:
    """The figure over the games that reached the period: one game, many
    summed (or averaged, for a column other than points), or each listed
    under the total."""
    measure, team, label, span_words = _facts(result, TeamPeriodFacts).measure, result.subject, _period_label(result), result.span.phrase or ""
    extra, dateless = result.narrowing.phrase, _facts(result, TeamPeriodFacts).dateless
    vs = f" against the {result.narrowing.opponent}" if result.narrowing.opponent else ""
    if len(played) == 1:
        g = played[0]
        paren = f" ({span_words})" if span_words else ""
        figure = f"scored {g['points']} points" if measure == "points" else f"had {g[measure]} {period_noun(measure, g[measure])}"
        return f"The {team} {figure} in the {label} against the {g['opponent']} on {g['date']}{dateless}{paren}."
    avg = total / len(played)
    if measure == "points":
        if len(played) > _QUARTER_BREAKDOWN_LIMIT:
            return f"The {team} scored {total} total points in the {label} across {len(played)} {span_words} games{vs}{extra}, averaging {avg:.1f} per game."
        header = f"The {team}, {label} scoring{vs}{extra}, {span_words} ({len(played)} games, {total} total):"
        return "\n".join([header, *(f"  {g['date']}  {g['points']}  vs {g['opponent']}" for g in played)])
    noun = period_noun(measure, 2)
    if len(played) > _QUARTER_BREAKDOWN_LIMIT:
        return f"The {team} averaged {avg:.1f} {noun} in the {label} across {len(played)} {span_words} games{vs}{extra} ({total} in all)."
    header = f"The {team} averaged {avg:.1f} {noun} in the {label}{vs}{extra}, {span_words} ({len(played)} games, {total} in all):"
    return "\n".join([header, *(f"  {g['date']}  {g[measure]}  vs {g['opponent']}" for g in played)])


def _team_period_body(result: Result) -> Reply:
    """The answer before its rebuilt-column notes: no games, none reaching
    the period, a rate, the extreme a "most"/"fewest" asks for, or the
    figure and the games beneath it."""
    line, detail = result.parts[0].body, result.parts[1].body
    assert isinstance(line, Scalar) and isinstance(detail, Rows)
    games = list(detail.rows)
    team, label, measure = result.subject, _period_label(result), _facts(result, TeamPeriodFacts).measure
    vs = f" against the {result.narrowing.opponent}" if result.narrowing.opponent else ""
    if not games:
        answer = f"The warehouse has no {result.span.phrase or ''} games for the {team}{vs}{result.narrowing.phrase}."
        return Reply(data={"team": team, "opponent": result.narrowing.opponent, "games": [], "headline": answer}, answer=answer)
    played = [g for g in games if g["points"] is not None]
    if not played:
        plural = "game" if len(games) == 1 else "games"
        answer = f"None of the {team}'s {len(games)} {result.span.phrase or ''} {plural}{vs}{result.narrowing.phrase} went to the {label}."
        return Reply(data={"team": team, "opponent": result.narrowing.opponent, "games": games, "headline": answer}, answer=answer)
    if measure in PERIOD_RATES:
        return _team_period_rate(result, played, line)
    total = line.sums[measure]
    data = {**_team_period_about(result, played), "total": total}
    if measure != "points":
        data["stat"] = measure
    if _facts(result, TeamPeriodFacts).rank in ("most", "fewest"):
        return _team_period_extreme(result, played, data, line)
    answer = _team_period_sentence(result, played, total)
    return Reply(data={**data, "average": round(total / len(played), 2), "headline": answer.split("\n")[0].rstrip(":")}, answer=answer)


def say_team_quarter_points(result: Result) -> Reply:
    """A team's figure in one quarter or half, worded: the games' total or
    average and the games beneath it, one game, the extreme a "most" or
    "fewest" asks for, a shooting percentage, or why there is nothing to
    say - and, on its own line, how a rebuilt column was read (the games
    with no play-by-play, each season's measured agreement) - the retired
    ``team_quarter_points`` template's words.

    .. versionadded:: 5.0.0
    """
    said = _team_period_body(result)
    notes = " ".join(f"({note(each.kind, note_phrase(each), **each.facts)})" for each in result.notes)
    if not notes or not said.answer:
        return said
    data = dict(said.data)
    data["notes"] = [*data.get("notes", []), notes]
    return Reply(data=data, answer=f"{said.answer}\n  {notes}")


# --- the league's ranking by a quarter or half -------------------------------------------


def _say_period_rank_rate(facts: Mapping[str, Any]) -> str:
    """The refusal for a ranking by a shooting percentage in a quarter: a
    games-played qualifier says nothing about attempts."""
    word = PERIOD_RATE_WORDS[facts["measure"]]
    return (
        f"Players are not ranked by {word} in a quarter or half: the per-game qualifier every period ranking uses says nothing about attempts, "
        f"and a percentage over a few of them ranks noise. Ask for one player's {word} in that period."
    )


def _period_leaderboard_where(result: Result) -> str:
    """The opponent and the venue the pool was narrowed to, as the headline
    says them after the season: " vs the Boston Celtics at home"."""
    said = f" vs the {result.narrowing.opponent}" if result.narrowing.opponent is not None else ""
    if result.narrowing.venue:
        said += " at home" if result.narrowing.venue == "home" else " on the road"
    return said


def _period_leaderboard_minimum(result: Result) -> str:
    """The minimum the ranking applied, phrased and recorded."""
    most = _facts(result, PeriodRankingFacts).most
    return decision_phrase(result.decisions[0], qualifier=f", half of the {most} anyone played" if most is not None else "")


def _say_period_by_quarter_league(result: Result, rows: list[dict[str, Any]]) -> Reply:
    """Points by quarter for every qualifying player, ranked by the four
    together, with what the table leaves out (overtime) and how many it
    shows."""
    team = result.subject or None
    among = f" for the {team}" if team else ""
    where = _period_leaderboard_where(result)
    season_label = season_phrase(int(result.span.season or 0), result.span.season_type or 2)
    if not rows:
        message = f"No player{among} played the {_facts(result, PeriodRankingFacts).minimum} games needed to rank points by quarter in the {season_label}{where}."
        return Reply(data={"season": result.span.season, "team": team, "narrowing": where.strip(), "leaders": [], "message": message}, answer=message)
    headline = f"Points per game by quarter{among} in the {season_label}{where}{_period_leaderboard_minimum(result)}, ranked by the four quarters together:"
    table = [f"  {'player':<26} {'G':>3} {'Q1':>6} {'Q2':>6} {'Q3':>6} {'Q4':>6} {'total':>6}"]
    table += [f"  {row['player']:<26} {row['games']:>3} {row['q1']:>6.2f} {row['q2']:>6.2f} {row['q3']:>6.2f} {row['q4']:>6.2f} {row['total']:>6.2f}" for row in rows]
    overtime, *agreement = result.notes
    notes = [f"{note(overtime.kind, note_phrase(overtime), **overtime.facts)} {decision_phrase(result.decisions[1])}"]
    caveat = period_caveat(agreement)
    data = {
        "season": result.span.season,
        "team": team,
        "narrowing": where.strip(),
        "minimum_games": _facts(result, PeriodRankingFacts).minimum,
        "leaders": rows,
        "qualified": _facts(result, PeriodRankingFacts).qualified,
        "headline": headline.rstrip(":"),
        "notes": [*notes, *([caveat.strip()] if caveat else [])],
    }
    return Reply(data=data, answer="\n".join([headline, *table, *notes]) + caveat)


def say_period_leaderboard(result: Result) -> Reply:
    """The league's (or a team's) players ranked by a stat in one quarter or
    half, per game - the leader in a sentence, the next four after him, the
    qualifier it applied and the season's accuracy caveat - or, with no
    period named, their points in each quarter side by side: the retired
    ``period_leaderboard`` template's words.

    .. versionadded:: 5.0.0
    """
    body = result.grouped
    assert body is not None
    rows = [dict(row) for row in body.rows]
    if body.ranked_by == "quarters":
        return _say_period_by_quarter_league(result, rows)
    team = result.subject or None
    measure, label = _facts(result, PeriodRankingFacts).measure, _period_label(result)
    led = f"the {team}" if team else "the league"
    among = f" for the {team}" if team else ""
    noun = period_noun(measure, 2)
    where = _period_leaderboard_where(result)
    scope = season_phrase(int(result.span.season or 0), result.span.season_type or 2)
    if not rows:
        message = f"No player{among} played the {_facts(result, PeriodRankingFacts).minimum} games needed to rank {label} {'scoring' if measure == 'points' else noun} in the {scope}{where}."
        empty: dict[str, Any] = {"period": label, "season": result.span.season, "team": team, "narrowing": where.strip(), "leaders": [], "message": message}
        return Reply(data=empty, answer=message)
    top = rows[0]
    rest = ", ".join(f"{row['player']} ({row['average']})" for row in rows[1:])
    headline = f"{top['player']} led {led} in {label} {noun} per game in the {scope}{where}{_period_leaderboard_minimum(result)}, at {top['average']} over {top['games']} games."
    answer = headline + (f" Next: {rest}." if rest else "")
    caveat = period_caveat(list(result.notes))
    data = {
        "period": label,
        "stat": measure,
        "season": result.span.season,
        "team": team,
        "narrowing": where.strip(),
        "minimum_games": _facts(result, PeriodRankingFacts).minimum,
        "leaders": rows,
        "headline": headline,
        "notes": [caveat.strip()] if caveat else [],
    }
    return Reply(data=data, answer=answer + caveat)


# --- a team's record --------------------------------------------------------------------


def _say_conference_named(facts: Mapping[str, Any]) -> str:
    """The refusal for a team slot that names a conference or a division:
    a record or a line here is one team's, and nothing adds a conference's
    or a division's teams together yet (ISSUES.md #25). Not "no
    membership": ``team_alignment`` has held every team's conference and
    division since 2026-09-24, and a player's games against one are read."""
    return (
        f"{facts['named']!r} is a conference or a division, not a team: a record or a line here is read for one team, "
        "and nothing adds up a conference's or a division's teams yet. Name a team instead."
    )


def _said_record_notes(result: Result, placed: str = "") -> dict[str, str]:
    """Each remark of a record, phrased and recorded in order, by kind."""
    return {each.kind: note(each.kind, note_phrase(each, placed=placed), **each.facts) for each in result.notes}


def _say_standings_season(result: Result, line: Scalar) -> Reply:
    """One season's standings line, or its home or road record."""
    team, season, venue = result.subject, result.span.season, result.narrowing.venue
    if not line.values:
        return Reply(data={"team": team, "season": season}, answer=f"There are no {season} standings for the {team} in the warehouse.")
    said = _said_record_notes(result, "season")
    neutral_note, gap = said.get("definition", ""), said.get("standings_short")
    v = line.values
    w, lost, win_pct = v["wins"], v["losses"], v["win_pct"]
    detail = result.parts[1].body if len(result.parts) > 1 else None
    split = ((detail.rows[0]["wins"], detail.rows[0]["losses"]), (detail.rows[1]["wins"], detail.rows[1]["losses"])) if isinstance(detail, Grouped) else None
    data: dict[str, Any] = {"team": team, "season": season, "wins": w, "losses": lost, "win_pct": win_pct}
    if venue is not None:
        return _say_standings_venue(result, line, split, data, neutral_note, gap)
    return _say_standings_line(result, line, split, data, neutral_note, gap)


def _say_standings_venue(result: Result, line: Scalar, split: Any, data: dict[str, Any], neutral_note: str, gap: str | None) -> Reply:
    """A season's home or road record from the standings' own split, beside
    the season's; the record card carries the record asked for."""
    team, season, venue, v = result.subject, result.span.season, result.narrowing.venue, line.values
    w, lost, win_pct = v["wins"], v["losses"], v["win_pct"]
    assert venue is not None
    if split is None:
        message = f"ESPN's {season} standings carry no home/road split for the {team} (it reads 0-0 before 1993-94), and the warehouse has no full game list for that season to tally one from."
        return Reply(data={**data, "message": message}, answer=message)
    vw, vl = split[0] if venue == "home" else split[1]
    headline = f"The {team} were {tally(vw, vl)} {_VENUE_WORDS[venue]} in the {season} regular season, {w}-{lost} overall{neutral_note}."
    data.update(
        {
            "wins": vw,
            "losses": vl,
            "win_pct": vw / (vw + vl) if vw + vl else 0.0,
            "season_wins": w,
            "season_losses": lost,
            "season_win_pct": win_pct,
            "venue": venue,
            "venue_wins": vw,
            "venue_losses": vl,
            "neutral_site_games": v["neutral"],
            "headline": headline,
            "notes": [gap] if gap else [],
        }
    )
    return Reply(data=data, answer=f"{headline} {gap}" if gap else headline)


def _say_standings_line(result: Result, line: Scalar, split: Any, data: dict[str, Any], neutral_note: str, gap: str | None) -> Reply:
    """A season's standings line: the record, seed and streak, then home and
    road, the last ten and games back, then points for and against."""
    team, season, v = result.subject, result.span.season, line.values
    w, lost, win_pct = v["wins"], v["losses"], v["win_pct"]
    answer = f"The {team} were {w}-{lost} in the {season} regular season"
    if win_pct is not None:
        answer += f" ({record_pct(win_pct)})"
    seed, streak = v["seed"], v["streak"]
    extras = []
    if seed:
        extras.append(f"{ordinal_word(int(seed))} seed")
    if streak:
        extras.append(f"{'won' if streak > 0 else 'lost'} {abs(int(streak))} straight")
    answer += f", {', '.join(extras)}." if extras else "."
    headline = answer
    answer += _standings_season_detail(split, neutral_note, v["last_ten"], v["games_behind"])
    points_for, points_against, differential = v["points_for"], v["points_against"], v["differential"]
    if points_for is not None and points_against is not None:
        answer += f"\n  {points_for:.1f} points per game, {points_against:.1f} allowed ({(differential if differential is not None else points_for - points_against):+.1f})."
    data.update(
        {
            "home": split[0] if split else None,
            "road": split[1] if split else None,
            "last_ten": v["last_ten"],
            "games_behind": v["games_behind"],
            "points_for": points_for,
            "points_against": points_against,
            # Carried either way: None reads as "not seeded"/"no active streak".
            "seed": int(seed) if seed else None,
            "streak": int(streak) if streak else None,
            "headline": headline,
            "notes": [gap] if gap else [],
        }
    )
    return Reply(data=data, answer=f"{answer}\n  {gap}" if gap else answer)


def _standings_season_detail(split: tuple[tuple[int, int], tuple[int, int]] | None, neutral_note: str, last_ten: Any, behind: Any) -> str:
    """The line under a season's record: home and road, the last ten games, and games back."""
    detail = []
    if split:
        detail.append(f"Home {'-'.join(map(str, split[0]))}, road {'-'.join(map(str, split[1]))}{neutral_note}")
    if isinstance(last_ten, str) and last_ten.strip():
        detail.append(f"last 10: {last_ten}")
    if behind:
        detail.append(f"{format_value(float(behind))} game{'s' if behind != 1 else ''} back")
    return "\n  " + "; ".join(detail) + "." if detail else ""


def _say_standings_career(result: Result, line: Scalar) -> Reply:
    """Every season's standings added up, overall or at home or on the road."""
    team, venue = result.subject, result.narrowing.venue
    if not line.values:
        return Reply(data={"team": team}, answer=f"The warehouse has no standings at all for the {team}.")
    if venue is not None and not line.values["seasons"]:
        message = f"ESPN's standings carry no home/road split for the {team} in any season the warehouse holds."
        return Reply(data={"team": team, "message": message}, answer=message)
    said = _said_record_notes(result, "career")
    gap = said.get("standings_short")
    wins, losses, seasons = line.values["wins"], line.values["losses"], line.values["seasons"]
    first, last = result.span.first, result.span.last
    assert isinstance(first, int) and isinstance(last, int)
    across = f"across the {seasons} regular seasons from {season_label(first)} through {season_label(last)}"
    if venue is None:
        start = said.get("floor", "the first season the warehouse holds for them")
        headline = f"The {team} are {tally(wins, losses)} {across} - {start}."
    else:
        headline = f"The {team} are {tally(wins, losses)} {_VENUE_WORDS[venue]} {across}{said.get('floor', '')}." + said.get("definition", "")
    data: dict[str, Any] = {"team": team, **({"venue": venue} if venue is not None else {})}
    data.update({"wins": wins, "losses": losses, "win_pct": wins / (wins + losses) if wins + losses else 0.0, "first_season": first, "last_season": last, "seasons": seasons, "headline": headline})
    data["notes"] = [gap] if gap else []
    return Reply(data=data, answer=f"{headline} {gap}" if gap else headline)


def _record_span_words(result: Result, floor: str) -> str:
    """The span a tally names: one season, a since-bounded range, every
    postseason on record, or every regular season on record - the last two
    with where the game list starts (``floor``)."""
    span, since, until = result.span, result.span.since, result.span.until
    season_type = span.season_type or 2
    if span.season is not None:
        return f"the {season_phrase(span.season, season_type)}"
    if since is not None:
        kind = "postseasons" if season_type == 3 else "regular seasons"
        bound = f"from {since} through {until}" if until is not None else f"since {since}"
        return f"the {kind} {bound}"
    if season_type == 3:
        return f"every postseason from 1989 through the latest{floor}"
    return f"the regular seasons from {season_label(1994)} on{floor}"


def _record_none(result: Result) -> str:
    """Why a tally found nothing - the warehouse has no games that season at
    all, the team played none, or the two teams did not meet - narrowed to
    what the question asked, so a team with games elsewhere is not told it
    has none."""
    team, opponent, facts = result.subject, result.narrowing.opponent, _facts(result, TeamRecordFacts)
    season, season_type = result.span.season, result.span.season_type or 2
    kind = "postseason" if season_type == 3 else "regular-season"
    cut, series = result.narrowing.cell(Calendar) or Calendar(), result.narrowing.cell(GameOfSeries)
    month, calendar = cut.month, cut.situation
    where = f" {calendar}" if calendar else (f" in {MONTH_NAMES[month - 1]}" if month is not None else "")
    since, until, game_n = result.span.since, result.span.until, series.n if series is not None else None
    since_phrase = "" if since is None else (f" from {since} through {until}" if until is not None else f" since {since}")
    game_n_phrase = f" in game {game_n} of {'the' if opponent is not None else 'each'} series" if game_n else ""
    if season is None:
        if opponent is None:
            return f"The warehouse holds no {kind} games for the {team}{since_phrase}{game_n_phrase}{where}."
        return f"The warehouse holds no {kind} games between the {team} and the {opponent}{since_phrase}{game_n_phrase}{where}."
    period = season_phrase(season, season_type)
    if facts.none == "season":
        return f"The warehouse holds no {period} games for any team."
    if facts.none == "not_met":
        return f"The {team} and the {opponent} did not meet{game_n_phrase}{where} in the {period}."
    return f"The {team} played no games{game_n_phrase}{where} in the {period}."


def _record_split(games: Sequence[Mapping[str, Any]]) -> str:
    """The home, away and neutral-site records within a tally."""
    home = [g for g in games if g["venue"] == "home"]
    away = [g for g in games if g["venue"] == "away"]
    split = f"Home {sum(g['won'] for g in home)}-{sum(not g['won'] for g in home)}, away {sum(g['won'] for g in away)}-{sum(not g['won'] for g in away)}"
    if len(games) - len(home) - len(away):
        split += f", neutral site {sum(g['won'] for g in games if g['venue'] == 'neutral')}-{sum(not g['won'] for g in games if g['venue'] == 'neutral')}"
    return f"\n  {split}."


def _say_games_record(result: Result, line: Scalar) -> Reply:
    """A record tallied from the game list, its home/away split, a season's
    meetings with one team, any NBA Cup final they met in, and the seasons
    the list and the team's own totals disagree on."""
    team, opponent, venue, span = result.subject, result.narrowing.opponent, result.narrowing.venue, result.span
    games_body = result.parts[1].body
    assert isinstance(games_body, Rows)
    games = [dict(g) for g in games_body.rows]
    shown = [g for g in games if venue is None or g["venue"] == venue]
    wins, losses = line.values["wins"], line.values["losses"]
    cut = result.narrowing.cell(Calendar)
    month = cut.month if cut is not None else None
    season_type = span.season_type or 2
    data: dict[str, Any] = {
        "team": team,
        "opponent": opponent,
        "season": span.season,
        "season_type": "postseason" if season_type == 3 else "regular season",
        "venue": venue,
        "month": MONTH_NAMES[month - 1] if month is not None else None,
        "wins": wins,
        "losses": losses,
        # Read by the web page's record card, like the standings paths' own.
        "win_pct": wins / (wins + losses) if wins + losses else 0.0,
        "first_season": span.first,
        "last_season": span.last,
    }
    if not games:
        none_message = _record_none(result)
        return Reply(data={**data, "games": [], "headline": none_message}, answer=none_message)
    said = _said_record_notes(result, "tally")
    span_words = _record_span_words(result, said.get("floor", ""))
    against = f" against the {opponent}" if opponent else ""
    where_played = f" {_VENUE_WORDS[venue]}" if venue else ""
    month_phrase = f" in {MONTH_NAMES[month - 1]}" if month is not None else ""
    verb = "went" if span.season is not None else "are"
    answer = f"The {team} {verb} {tally(wins, losses)}{month_phrase}{where_played}{against}{result.narrowing.phrase} in {span_words}."
    if venue is None:
        answer += _record_split(games)
    else:
        answer += said.get("definition", "")
    answer += _record_meetings(result, shown, data)
    gap = said.get("game_list_disagrees")
    if gap:
        answer += f"\n  {gap}"
    data["games"] = shown
    data["headline"] = answer.split("\n")[0]
    data["notes"] = [gap] if gap else []
    return Reply(data=data, answer=answer)


def _record_meetings(result: Result, shown: list[dict[str, Any]], data: dict[str, Any]) -> str:
    """A season's meetings with one team, listed - few enough to list, and
    what "vs" questions usually want next - and any NBA Cup final the two
    met in, which counts in no standings (its rows put in ``data`` too)."""
    opponent, span = result.narrowing.opponent, result.span
    answer = ""
    if opponent is not None and span.season is not None and shown:
        answer += "\n" + "\n".join(
            f"  {g['date']}  {'W' if g['won'] else 'L'} {g['team_score']}-{g['opponent_score']}  {'at' if g['venue'] == 'away' else 'vs'} {g['opponent']}"
            + (" (neutral site)" if g["venue"] == "neutral" else "")
            for g in shown
        )
    if len(result.parts) > 2:
        cups = result.parts[2].body
        assert isinstance(cups, Rows)
        answer += "".join(
            f"\n  They also met in the NBA Cup final on {c['date']}, which counts in no standings: {'won' if c['won'] else 'lost'} {c['team_score']}-{c['opponent_score']}." for c in cups.rows
        )
        data["cup_final"] = [dict(c) for c in cups.rows]
    return answer


def _combined_from(first_season: Any, *, playoff: bool) -> str:
    """ " from 1993-94" or " from 1989", or "" where a half carries no first
    season (one named season, where the halves start together) - #204. A
    postseason is dated by the calendar year it was played in."""
    if not isinstance(first_season, int):
        return ""
    return f" from {first_season if playoff else season_label(first_season)}"


def _say_combined_record(result: Result, line: Scalar) -> Reply:
    """Both season types' records summed - the total, and each type's own
    record and first season beside it, with where each half's source starts
    said after it, and each half's "Note:" beneath."""
    halves = result.parts[1].body
    assert isinstance(halves, Grouped)
    regular, playoff = halves.rows
    team, opponent, venue = result.subject, result.narrowing.opponent, result.narrowing.venue
    # Each half's remarks, phrased and recorded in the order the halves read
    # them: the floors after the half they bound, a neutral-site count after
    # the sentence, a "Note:" on its own line.
    inline: list[str] = ["", ""]
    after: list[str] = []
    tails: list[str] = []
    for index, each in enumerate(result.notes):
        half = 0 if index < regular["notes"] else 1
        said = note(each.kind, note_phrase(each, placed=(regular, playoff)[half]["placed"]), **each.facts)
        if each.kind == "floor":
            inline[half] += said if said.startswith(" ") else f" - {said}"
        elif each.kind == "definition" and (regular, playoff)[half]["placed"] == "season":
            inline[half] += said
        elif each.kind == "definition":
            after.append(said)
        else:
            tails.append(said)
    wins, losses = line.values["wins"], line.values["losses"]
    against = f" against the {opponent}" if opponent else ""
    where_played = f" {_VENUE_WORDS[venue]}" if venue else ""
    r_from = _combined_from(regular["first_season"], playoff=False) + inline[0]
    p_from = _combined_from(playoff["first_season"], playoff=True) + inline[1]
    answer = (
        f"The {team} are {tally(wins, losses)} combined{where_played}{against}, including the playoffs "
        f"({tally(regular['wins'], regular['losses'])} regular season{r_from}, {tally(playoff['wins'], playoff['losses'])} playoffs{p_from})." + "".join(after)
    )
    if tails:
        answer += "\n  " + "\n  ".join(tails)
    data = {
        "team": team,
        "opponent": opponent,
        "venue": venue,
        "wins": wins,
        "losses": losses,
        "win_pct": wins / (wins + losses) if wins + losses else 0.0,
        "regular_season": {"wins": regular["wins"], "losses": regular["losses"], "first_season": regular["first_season"]},
        "postseason": {"wins": playoff["wins"], "losses": playoff["losses"], "first_season": playoff["first_season"]},
    }
    return Reply(data=data, answer=answer)


def say_team_record(result: Result) -> Reply:
    """A team's record, worded by its parts: both season types summed (a
    detail by season type), a tally of its games (the games beneath), or a
    standings season or career - the retired
    ``team_record`` template's words.

    .. versionadded:: 5.0.0
    """
    line = result.scalar
    assert line is not None
    detail = result.parts[1].body if len(result.parts) > 1 else None
    if isinstance(detail, Grouped) and detail.by == "season_type":
        return _say_combined_record(result, line)
    if isinstance(detail, Rows):
        # A tally of its games, with the games beneath.
        return _say_games_record(result, line)
    return _say_standings_career(result, line) if result.span.career else _say_standings_season(result, line)


def say_team_record_by_month(result: Result) -> Reply:
    """A team's record broken out by calendar month, as one table, or one
    per season of a since-bounded span - the retired ``team_record``
    template's words.

    .. versionadded:: 5.0.0
    """
    body = result.grouped
    assert body is not None
    team, opponent, venue, span = result.subject, result.narrowing.opponent, result.narrowing.venue, result.span
    since = result.span.since
    months = [dict(row) for row in body.rows]
    if not months:
        message = _record_none(result)
        data: dict[str, Any] = {"team": team, "months": []} if since is not None else {"team": team, "season": span.season, "months": []}
        return Reply(data=data, answer=message)
    against = f" against the {opponent}" if opponent else ""
    where_played = f" {_VENUE_WORDS[venue]}" if venue else ""
    season_type = span.season_type or 2
    by_season: dict[Any, list[dict[str, Any]]] = {}
    for row in months:
        by_season.setdefault(row["season"], []).append(row)
    floor = "".join(note(each.kind, note_phrase(each), **each.facts) for each in result.notes)
    tables = []
    for season, rows in by_season.items():
        span_words = f"the {season_phrase(season, season_type)}" if since is not None else _record_span_words(result, floor)
        title = f"The {team}, record by month{where_played}{against}, {span_words}:"
        tables.append(_table(title, ["G", "W-L"], [(row["month"], [str(row["games"]), f"{row['wins']}-{row['losses']}"]) for row in rows]))
    answer = "\n\n".join(tables)
    if since is not None and floor:
        # The seasons the list could not reach, before the tables (#300).
        answer = f"{floor}\n\n{answer}"
    if since is not None:
        data = {"team": team, "opponent": opponent, "venue": venue, "months": months, "since": since, "until": result.span.until, "headline": tables[0].split("\n")[0].rstrip(":")}
    else:
        data = {"team": team, "season": span.season, "opponent": opponent, "venue": venue, "months": months, "headline": answer.split("\n")[0].rstrip(":")}
    return Reply(data=data, answer=answer)


# --- a player's NetPoints ------------------------------------------------------------


def _say_netpoints_note(kind: str, facts: dict[str, Any]) -> str | None:
    """The phrase for a note a NetPoints answer makes - a part with nothing on
    record, the fingerprint a game's NetPoints come with, a player drawn
    against a pool he is not in, or one with no fingerprint at all - or
    ``None``."""
    what = facts.get("what")
    if kind == "part_missing" and what == "season_totals":
        return "(no season totals on record)"
    if kind == "part_missing" and what == "fingerprint":
        return "  No play-type fingerprint on record for this season."
    if kind == "below_pool":
        short = f"under {facts['threshold']} possessions in that game" if facts["of"] == "possessions" else f"under {facts['threshold']} minutes"
        return f". Note: {', '.join(facts['names'])} played {short}, so they are plotted against a pool they are not in"
    if kind == "no_data_for" and what == "fingerprint":
        return f". No fingerprint on record for: {', '.join(facts['names'])}"
    if kind == "hint" and what == "fingerprint_of_that_game":
        return "\n  (Ask for a fingerprint of that game to see the play-type split behind it.)"
    return None


def _netpoints_said(result: Result, kind: str, **match: Any) -> str:
    """``result``'s note of ``kind`` whose facts hold ``match``, phrased and
    recorded where the answer says it."""
    each = next(each for each in result.notes if each.kind == kind and all(each.facts.get(k) == v for k, v in match.items()))
    return note(each.kind, note_phrase(each), **each.facts)


def say_player_netpoints(result: Result) -> Reply:
    """One player's NetPoints worded: one game's (a windowed Result), a
    season's line and the table of its play-type split, or the refusal
    naming the season and the player that has nothing - with the seasons
    he does have where the season was defaulted.

    .. versionadded:: 5.0.0
       ``templates.netpoints.player_netpoints``' words.
    """
    if result.window is not None:
        return _say_netpoints_game(result)
    season, period, facts = result.span.season, result.span.phrase, _facts(result, NetPointsFacts)
    line = next((part.body for part in result.parts if isinstance(part.body, Scalar) and part.body.values), None)
    split = next((part.body for part in result.parts if isinstance(part.body, Grouped)), None)
    if line is None and split is None:
        answer = f"The warehouse has no {period} NetPoints for {result.subject}."
        # No "or ask for his career" here: player_netpoints has no career
        # span to offer. A season the question named keeps this plain.
        answer += "".join(decision_phrase(each, career_hint=False) for each in result.decisions)
        return Reply(data={"player": result.subject, "season": season, "headline": answer}, answer=answer)
    totals = dict(line.values) if line is not None else None
    breakdown = [{"category": row["key"], **{k: v for k, v in row.items() if k != "key"}} for row in split.rows] if split is not None else []
    per_100, possessions = facts.per_100, facts.possessions
    units = "per 100 possessions" if per_100 else "season totals"
    scope = f" over {possessions:,.0f} possessions" if per_100 and possessions else ""
    answer = _say_netpoints_season(result, totals, breakdown)
    return Reply(
        data={
            "player": result.subject,
            "season": season,
            # `totals` is a stable dict, not the raw SQL row `headline` used to
            # be: the page's own `data["headline"]` is the display sentence
            # every other renderer reads verbatim (ISSUES.md, "`player_netpoints`
            # still renders as a `<pre>` block").
            "totals": _netpoints_totals(totals),
            "fingerprint": breakdown,
            "per_100": per_100,
            "possessions": possessions,
            "headline": answer.split("\n")[0],
            "notes": _netpoints_page_notes(totals, breakdown, units, scope),
        },
        answer=answer,
    )


def _netpoints_totals(totals: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The season line as a stable dict for the page, or ``None`` with no
    season totals on record."""
    if totals is None:
        return None
    minutes, games = totals["total_minutes"], totals["games"]
    return {
        "overall": totals["overall"],
        "offense": totals["offense"],
        "defense": totals["defense"],
        "per_100": totals["overall_per_100_poss"],
        "minutes": int(minutes) if minutes else None,
        "games": int(games) if games else None,
    }


def _netpoints_line_detail(totals: Mapping[str, Any] | None) -> list[str]:
    """The minutes/games/per-100-rate clauses beside the season line - shared
    by the printed sentence and ``data["notes"]``, so the two read the same
    numbers off one place."""
    if totals is None:
        return []
    per_100_rate, minutes, games = totals["overall_per_100_poss"], totals["total_minutes"], totals["games"]
    detail = []
    if per_100_rate is not None:
        detail.append(f"{per_100_rate:.2f} per 100 possessions")
    if minutes:
        detail.append(f"{int(minutes):,} minutes")
    if games:
        detail.append(f"{int(games)} games")
    return detail


def _netpoints_page_notes(totals: Mapping[str, Any] | None, breakdown: list[dict[str, Any]], units: str, scope: str) -> list[str]:
    """The lines a table- or card-rendered page needs beside the numbers
    rather than in them: the season line's own detail, what units the
    category rows are in, and the play-type overlap disclaimer - the same
    three facts the answer's prose carries."""
    page_notes = []
    detail = _netpoints_line_detail(totals)
    if detail:
        page_notes.append(", ".join(detail) + ".")
    if breakdown:
        page_notes.append(f"Categories are {units}{scope}.")
    if any(not row["partition"] for row in breakdown):
        page_notes.append("Play-type detail is overlapping slices - a driving layup at the rim counts in driving, layup and rim, so these do not add up.")
    return page_notes


def _say_netpoints_season(result: Result, totals: Mapping[str, Any] | None, breakdown: list[dict[str, Any]]) -> str:
    """The season line, then a table of the six partition categories
    (offense and defense sections) and one of the overlapping play-type
    detail rows."""
    lines = _say_netpoints_headline(result, totals)
    if not breakdown:
        lines.append(_netpoints_said(result, "part_missing", what="fingerprint"))
        return "\n".join(lines)
    width = max(len(row["category"]) for row in breakdown)
    lines += _say_netpoints_partition(result, [r for r in breakdown if r["partition"]], width)
    lines += _say_netpoints_detail(result, [r for r in breakdown if not r["partition"]], width)
    return "\n".join(lines)


def _say_netpoints_headline(result: Result, totals: Mapping[str, Any] | None) -> list[str]:
    """The season-total line, and its minutes and games, or the note that there are none."""
    name, period = result.subject, result.span.phrase
    per_season = next((each for each in result.notes if each.kind == "definition" and each.facts.get("term") == "fingerprint_per_season"), None)
    if per_season is not None:
        # A postseason question over the per-season split (#299): the season
        # named as a whole, and why.
        period = f"{result.span.season} season"
    if totals is None:
        heading = f"{name}, NetPoints fingerprint in the {period} {_netpoints_said(result, 'part_missing', what='season_totals')}"
        return [heading + (note(per_season.kind, note_phrase(per_season), **per_season.facts) if per_season is not None else "") + ":"]
    lines = [f"{name}, NetPoints in the {period}: {table_cell(totals['overall'])} overall ({table_cell(totals['offense'])} offense, {table_cell(totals['defense'])} defense)"]
    detail = _netpoints_line_detail(totals)
    if detail:
        lines.append("  " + ", ".join(detail) + ".")
    return lines


def _say_netpoints_partition(result: Result, partition_rows: list[dict[str, Any]], width: int) -> list[str]:
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
        lines.append(f"  {heading}, {_netpoints_said(result, 'definition', term='netpoints_units')}:")
        # Two decimals: per-100 values are small, and one decimal collapses
        # most of the categories onto the same number.
        lines.extend("  " + row["category"].ljust(width) + f"{row[side]:.2f}".rjust(9) for row in ranked)
        # The sum is printed so the reader can check it against the headline -
        # these six really do add up, and showing it says so without asserting.
        lines.append("  " + "-" * (width + 9))
        lines.append("  " + "total".ljust(width) + f"{sum(r[side] for r in ranked):.2f}".rjust(9))
    return lines


def _say_netpoints_detail(result: Result, detail_rows: list[dict[str, Any]], width: int) -> list[str]:
    """The overlapping play-type slices, which are shown but do not add up."""
    if not detail_rows:
        return []
    units = "per 100 possessions" if _facts(result, NetPointsFacts).per_100 else "season totals"
    said = _netpoints_said(result, "definition", term="netpoints_overlap")
    lines = [
        "",
        *f"  Play-type detail, {units} ({said}):".split("\n"),
        "  " + "category".ljust(width) + "".join(h.rjust(9) for h in ("O", "D")),
    ]
    for row in sorted(detail_rows, key=lambda r: -abs(r["total"] or 0)):
        cells = "".join(("-" if row[k] is None else f"{row[k]:.2f}").rjust(9) for k in ("offense", "defense"))
        lines.append("  " + row["category"].ljust(width) + cells)
    return lines


def _say_netpoints_game(result: Result) -> Reply:
    """One game's NetPoints: the line and its possessions and win probability
    added, and the fingerprint that holds its play-type split - or that he
    has no game on record at that end of the season."""
    window, period, line = result.window, result.span.phrase, result.scalar
    assert window is not None and line is not None
    if not line.games:
        which = "earliest" if window.ascending else "most recent"
        answer = f"No per-game NetPoints on record for {result.subject}'s {which} {period} game."
        return Reply(data={"player": result.subject, "season": result.span.season, "game": None, "headline": answer}, answer=answer)
    which = "first" if window.ascending else "most recent"
    o, d, t = line.values["offense"], line.values["defense"], line.values["total"]
    o_poss, d_poss, wpa = line.sums["o_poss"], line.sums["d_poss"], line.sums["wpa"]
    game = {"event_id": _facts(result, NetPointsFacts).event_id, "date": result.span.date, "offense": o, "defense": d, "total": t}
    detail = []
    if o_poss is not None and d_poss is not None:
        detail.append(f"{o_poss:.0f} offensive and {d_poss:.0f} defensive possessions")
    if wpa is not None:
        detail.append(f"{wpa:+.3f} win probability added")
    headline = f"{result.subject}, NetPoints in his {which} {period} game ({result.span.date}): {table_cell(t)} total ({table_cell(o)} offense, {table_cell(d)} defense)."
    answer = headline
    if detail:
        answer += "\n  " + ", ".join(detail) + "."
    answer += _netpoints_said(result, "hint", what="fingerprint_of_that_game")
    return Reply(data={"player": result.subject, "game": game, "headline": headline, "notes": [", ".join(detail) + "."] if detail else []}, answer=answer)


def say_fingerprint(result: Result) -> Reply:
    """One or more players' fingerprints worded: the page drawn
    (``compose.netpoints.draw_fingerprint`` wrote it; ``path`` is where),
    whom for, the season or the games it covers and its scale - then the
    players plotted against a pool they are not in, those with no
    fingerprint, and the other names that matched.

    .. versionadded:: 5.0.0
       ``fingerprint.render_for_players``' message.
    """
    chart = result.chart
    assert chart is not None and chart.path is not None
    facts = _facts(result, ChartFacts)
    view, order = facts.view, facts.order
    message = f"Rendered NetPoints fingerprint ({view}) for {chart.title} ({facts.when}, {facts.scale} scale) to {chart.path}"
    message += "".join(note(each.kind, note_phrase(each), **each.facts) for each in result.notes)
    message += "".join(decision_phrase(each) for each in result.decisions)
    data = {"players": list(facts.players), "season": result.span.season, "side": view, "scope": "game" if order else "season", "path": chart.path, "message": message}
    return Reply(data=data, answer=message, artifacts=[Artifact("fingerprint", Path(chart.path))])


# --- one player's shots ------------------------------------------------------------


def say_shot_distance(result: Result) -> Reply:
    """One player's average shot distance worded, as ``shot_distance``'s
    retired template said it: the average and the attempts it is over, or
    that there were none, over the period or the games a narrowing or a
    window pinned it to - then a derived season's caveat and the career's
    floor, in that order.

    .. versionadded:: 5.0.0
    """
    line = result.scalar
    assert line is not None
    name, period = result.subject, result.span.phrase
    value, day = result.narrowing.cell(ShotValue), result.narrowing.cell(OnDate)
    shot_value = value.value if value is not None else None
    kind = {2: "2-point ", 3: "3-point "}.get(shot_value or 0, "")
    # One game a window alone reached keeps its own words; any other set of
    # games is said as the span and the narrowing (Narrowing.phrase).
    first = result.window is not None and result.window.ascending
    game_note = result.narrowing.phrase or (f" in his {'first' if first else 'most recent'} game ({day.day})" if day is not None else "")
    average, attempts = line.values["avg_feet"], line.sums["attempts"]
    if not attempts or average is None:
        answer = f"No {kind}shots with recorded coordinates for {name}{game_note} in the {period}."
    else:
        answer = f"{name}'s average {kind}shot distance{game_note or f' in the {period}'} was {average:.1f} feet, over {attempts:,} attempts with recorded coordinates."
    for each in result.notes:
        said = note(each.kind, note_phrase(each, about=name), **each.facts)
        answer += f" Note: {said}." if each.kind == "shot_values_derived" else said
    # A defaulted season with no shots: the seasons he is on record for.
    answer += "".join(decision_phrase(each) for each in result.decisions)
    return Reply(
        data={"player": name, "season": result.span.season, "shot_value": shot_value, "avg_feet": average, "attempts": attempts, "headline": answer},
        answer=answer,
    )


def say_shot_chart(result: Result) -> Reply:
    """One player's shot chart worded, as ``shot_chart``'s retired template
    and its renderer said it: the file drawn, whom for (and which game or
    window, by the narrowing's phrase), made of attempted - or that nothing
    was drawn, and why (the refusal's sentence, or no shots found) - then
    the other names that matched, the shots a value filter left out or
    derived, and the career's floor or the seasons a defaulted one redirects
    to. The artifact is the file the draw step wrote (``compose.shots.draw_shot_chart``).

    .. versionadded:: 5.0.0
    """
    chart = result.chart
    assert chart is not None
    name = result.subject
    said = [note(each.kind, note_phrase(each, about=name), **each.facts) for each in result.notes if each.kind != "floor"]
    floor = "".join(note(each.kind, note_phrase(each, about=name), **each.facts) for each in result.notes if each.kind == "floor")
    if chart.path is not None:
        context = result.narrowing.phrase
        who = f"{name} ({context})" if context else name
        message = f"Rendered shot chart for {who} ({chart.made}/{chart.attempted} made, {chart.made / chart.attempted:.1%}) to {chart.path}"
        message += "".join(decision_phrase(each) for each in result.decisions if each.kind == "also_matched")
        message += "".join(f". Note: {text}" for text in said)
    else:
        base = _empty_said(result) if result.empty is not None else f"No shots found for {name} with the given filters."
        message = " Note: ".join([base, *said]) + ("." if said else "")
    message += floor
    message += "".join(decision_phrase(each, career_hint=False) for each in result.decisions if each.kind == "season_redirected")
    artifacts = [Artifact(chart.kind, Path(chart.path))] if chart.path is not None else []
    return Reply(data={"message": message, "player": name, "path": chart.path}, answer=message, artifacts=artifacts)


def table_cell(value: Any) -> str:
    """A value in an aligned column: a fixed decimal, never trailing-zero
    stripped - "25" next to "27.7" reads as a different unit.

    .. versionadded:: 5.0.0
       Public, as the sayer's phrase helper.
    """
    if value is None:
        return "-"
    return f"{value:.1f}" if isinstance(value, float) else str(value)


def format_value(value: Any) -> str:
    """A figure as an answer prints it: a float to two places (three below
    one), trailing zeros dropped; anything else as itself.

    .. versionadded:: 5.0.0
       Public, as the sayer's phrase helper.
    """
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".") if abs(value) < 1 else f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


def count_games(count: int) -> str:
    """``"1 game"``, ``"1,200 games"``.

    .. versionadded:: 5.0.0
       Public, as the sayer's phrase helper.
    """
    return f"{count:,} game{'' if count == 1 else 's'}"


def defaulted_season_note(season_range: tuple[int, int] | None, kind: str, *, career_hint: bool = True) -> str:
    """The sentence a defaulted-season refusal appends when
    :func:`~association.query.season_line.season_redirect` found something to point at - empty with nothing on record at all, which
    leaves the plain refusal standing: that is a genuine gap, not a wrong
    default, and there is nothing here to redirect toward.

    Never substitutes an answer, only names where to ask again - the same
    discipline `entities.suggest_players` follows for a near-miss name.

    .. versionadded:: 5.0.0
       Public, the sayer's (``templates.common._defaulted_season_note`` until then).
    """
    if season_range is None:
        return ""
    first, last = season_range
    redirect = Decided(kind="season_redirected", field="season", chose=None, why="the season read by default holds nothing for him", facts={"first": first, "last": last, "what": kind})
    return decision_phrase(redirect, career_hint=career_hint)


# --- refusals: the phrase table ------------------------------------------------


#: The run's own causes (:data:`~association.query.result.RUN_CAUSES`), one
#: phrase each, from the Refusal's facts.
_RUN_PHRASES: dict[str, Callable[[Mapping[str, Any]], str | None]] = {
    "season_out_of_reach": lambda facts: unavailable(tuple(facts["tables"]), facts["season"], facts["season_type"], ranking=facts["ranking"]),
    "name_unmatched": lambda facts: suggestion(facts["asked"], (), facts.get("kind", "player")),
    "opponent_is_absent": lambda facts: (
        f"{facts['opponent']} is both the player {facts['player']} is matched against and the teammate named as absent - no game can be both. Name the opponent team, or drop 'without'."
    ),
    "no_such_season_n": _say_no_such_season_n,
    "period_condition_needs_plays": _say_period_condition_needs_plays,
    "not_a_teammate": lambda facts: f"No player matching {facts['asked']!r} was {facts['player']}'s teammate {facts['during']}.",
    "no_game_on_date": lambda facts: f"No {facts['kind']} game on {facts['date']} found for {facts['player']}{facts['narrowing']}.",
    "box_scores_empty": lambda facts: (
        f"{facts['player']} played {count_games(facts['games'])} {facts['during']}, but the box score is empty for all of them - ESPN served no minutes or stats for any."
    ),
    "no_box_scores": lambda facts: f"{facts['player']} has no {facts['kind']} box scores in the warehouse, which begin with the {season_label(facts['first'])} season.",
    "no_games_in_span": _say_no_games_in_span,
    "not_teammates_then": lambda facts: f"{facts['teammate']} was not {facts['player']}'s teammate in any of his {count_games(facts['games'])} {facts['during']}.",
    "none_matched": lambda facts: f"{facts['player']} played {count_games(facts['games'])} {facts['during']}, none of them{facts['narrowing']}.",
    "listed_not_played": _say_player_listed,
    "no_player_games": _say_player_listed,
    "no_team_games": lambda facts: f"The warehouse has no games with a result for the {facts['team']} {facts['where']}.",
    "no_games_in_season": lambda facts: f"{facts['player']} has no games recorded in the {facts['season']} season{facts['narrowing']}.",
    "no_team_games_in": lambda facts: f"No {facts['span']} games found for the {facts['team']}{facts['narrowing']}.",
    "free_throw_chart": lambda _facts: "Free throws are all taken from the same line and carry no court position worth drawing, so there is no free-throw chart to render.",
    "shot_chart_unseparable": lambda facts: f"{UNSEPARABLE_SHOT_VALUES[facts['season']]}. A chart of {facts['player']}'s {facts['kind']} in {facts['season']} cannot be drawn.",
    "shot_distance_unseparable": lambda facts: (
        f"{UNSEPARABLE_SHOT_VALUES[facts['season']]}. {facts['player']}'s average {facts['kind']}shot distance in the {facts['period']} cannot be given; his average over all shots can."
    ),
    "fingerprint_on_a_date": lambda _facts: "A fingerprint can be drawn for a player's first or most recent game of a season, but not yet for a particular date - ask for their last game instead.",
    "period_untrusted": _say_period_untrusted,
    "period_unread": _say_period_unread,
    "team_period_unknown": _say_team_period_unknown,
    "team_period_unread": _say_team_period_unread,
    "team_period_untrusted": _say_team_period_untrusted,
    "period_rank_rate": _say_period_rank_rate,
    "period_rank_unread": lambda facts: f"Per-quarter {period_noun(facts['measure'], 2)} cannot be ranked here: they are rebuilt from play-by-play, and this warehouse holds none.",
    "conference_named": _say_conference_named,
    "teammate_never_seen": lambda facts: f"{facts['teammate']} has no box-score appearance in the warehouse, so there is no time on a team to count games in.",
    "never_together": _say_never_together,
    "together_outside_span": _say_together_outside_span,
    "team_stat_unrecorded": lambda facts: (
        f"The warehouse has {facts['games']} game{'' if facts['games'] == 1 else 's'} with a result for the {facts['team']}{facts['narrowing']} {facts['where']}, "
        f"but no {_unit(facts['stat'])} figure on record for {'it' if facts['games'] == 1 else 'any of them'}."
    ),
    "no_team_totals": lambda facts: f"The warehouse has no {facts['season']} team totals for the {facts['team']}.",
    "metric_before_first_season": lambda facts: f"{TEAM_METRICS[facts['metric']].label.capitalize()} can't be given for {facts['season']}: {TEAM_METRICS[facts['metric']].first_season_reason}.",
    "no_team_record": lambda facts: f"The {facts['team']} have no {season_phrase(facts['season'], facts['season_type'])} record in the warehouse.",
    "missed_postseason": lambda facts: f"The {facts['team']} did not play in the {season_phrase(facts['season'], facts['season_type'])}.",
    "no_team_line": lambda facts: f"The warehouse has no {season_phrase(facts['season'], facts['season_type'])} team stats for the {facts['team']}.",
    "no_venue_split": lambda facts: f"ESPN's {facts['season']} standings carry no home/road split (it reads 0-0 for every team before 1993-94).",
    "short_of_games": lambda facts: short_of_games_said(TEAM_METRICS[facts["metric"]], season_phrase(facts["season"], facts["season_type"]), facts["short"]),
    "team_none_matched": lambda facts: f"The {facts['team']} played {facts['games']:,} games {facts['during']}, none of them{facts['narrowing']}.",
}


CHAMPIONSHIP_REFUSAL: str = (
    "Championships are not on record as such - the warehouse holds every playoff game and no table of titles, and nothing derives a champion from a postseason's last series yet. "
    "Ask for a team's postseason record in a season, or two teams' playoff meetings, to see who won a series."
)
"""The sentence a championship question is refused with, before any reader
runs (the reading's ``championship`` cause): "Show which team won the nba
championship for the past 10 years" was read as a team ranking and ranked
regular-season records since 2017.

.. versionadded:: 6.0.0
   ``refusals.by_question``'s sentence.
"""


def _say_no_player_reading(facts: Mapping[str, Any]) -> str:
    """A player named on a question whose shape has no reading for one
    ("alperen şengün alltime record" read as a team ranking): names the
    player it read rather than answering the league's or a team's own
    numbers, the wrong subject. Was ``entities.team_only_question_names_a_player``."""
    player, shape = facts["player"], str(facts["intent"]).replace("_", " ")
    return (
        f"This was read as a question about {player}, a player, but {shape} has no reading for one - "
        f"it would have answered the league's or a team's own numbers instead. Ask about {player}'s own stats, "
        "or name a team if a team's record was meant."
    )


def _say_non_calendar_situation(facts: Mapping[str, Any]) -> str:
    """A situation nothing reads, by what the reader recognized in it: an
    age (no birth dates on record), a conference or division word in a
    shape the alignment reader does not take, or anything else."""
    situation, reads_as = facts["situation"], facts["reads_as"]
    if reads_as == "age":
        return f"'{situation}' needs a birth date, and the player records here carry none - so no answer can be narrowed by age. Ask by season instead (the season he turned that age)."
    if reads_as == "alignment":
        return f'\'{situation}\' names a conference or division, but not in a shape this reads - try "vs the west", "against eastern conference teams" or "vs the southeast division".'
    return f"'{situation}' is not something the games are read by - a weekday, a month, a holiday, \"since <day>\", a conference or a division is. Ask without it, or with one of those."


def _say_period_stat(facts: Mapping[str, Any]) -> str:
    """A stat a quarter's or half's rebuilt line does not hold, naming the
    columns that ARE rebuilt and the period asked about."""
    stat, half = facts["stat"], facts["half"]
    period = facts["period"] or half
    where = f"the {period}{'st' if period == 1 else 'nd' if period == 2 else 'rd' if period == 3 else 'th'} {'half' if half else 'quarter'}" if isinstance(period, int) else "a period"
    return (
        f"By quarter or half, a line is rebuilt from the play-by-play - points, field goals, free throws, rebounds, assists, steals, blocks, turnovers and fouls, "
        f"and the field goal, 3-point and free throw percentages from them - and {stat!r} is not among them. Ask for one of those in {where}, or for {stat} over whole games."
    )


def _say_team_boolean_count(facts: Mapping[str, Any]) -> str:
    """A team's total of its players' triple-doubles or double-doubles."""
    label = "triple-doubles" if facts["stat"] == "triple_double" else "double-doubles"
    return f"A team's total of its players' {label} is not read yet - one player's {label} are (ask '<player> triple doubles this season'), and so is the team's own record. Ask one of those."


#: What the question's words name that nothing reads, and the refusals the
#: words come to before any reader runs - the reading's causes Phase 3's
#: first step moved off the answering loop (``refusals.by_question``,
#: ``refusals.unanswerable``'s checks, the team-only player), each said by
#: the sentence it was said by there, word for word.
_WORDS_PHRASES: dict[str, Callable[[Mapping[str, Any]], str]] = {
    "championship": lambda _facts: CHAMPIONSHIP_REFUSAL,
    "no_player_reading": _say_no_player_reading,
    "playoff_round": lambda facts: (
        f"The games are not labeled by playoff round, so '{facts['round']}' cannot pick them out yet. "
        "Name the two teams and the season instead - a series is their postseason meetings, and those are read."
    ),
    "non_calendar_situation": _say_non_calendar_situation,
    "period_stat": _say_period_stat,
    "period_as_condition": lambda _facts: (
        "A quarter or a half here reads as a condition on which games count, and no line could be read from it - "
        'a number, a stat the period\'s line rebuilds and the period, as in "after making one three in the first quarter" or "in games where he scored 10+ points in the first half". '
        "Ask with the line worded that way, for the stat in that period, or for it over whole games."
    ),
    "team_period_stat": lambda facts: (
        f"A team's quarter or half holds its points (the linescore), the box-score counts play-by-play rebuilds and the shooting percentages from them, and {facts['stat']!r} is none of those. "
        f"Ask for the team's points, threes, rebounds, turnovers or free throw percentage in that period, or for {facts['stat']} over whole games."
    ),
    "bench_points": lambda _facts: (
        "Bench points are not read yet - the box score flags starters, so a bench total could be built, but no template or the compiler adds one up today. "
        "Ask for a named player's points, or a team's points, instead."
    ),
    "team_boolean_count": _say_team_boolean_count,
}


#: A reading's causes said by a phrase of their own rather than the shape
#: table in :func:`_cause_sentence`.
_RANKING_PHRASES: dict[str, Callable[[Mapping[str, Any]], str | None]] = {
    "shot_distance_ranking": lambda _facts: _say_shot_distance_ranking(),
    "no_ranking_measure": lambda facts: _no_ranking_for(facts["stat"]),
    "ranking_floor_unit": lambda facts: _ranking_floor_unit(facts["unit"], facts["count"]),
    "ranking_unit": lambda facts: _ranking_unit(facts["metric"], facts["rate"]),
}


def refusal_phrase(kind: str, facts: Mapping[str, Any]) -> str:
    """ONE sentence per refusal cause - a reading's
    (:data:`~association.query.reading.CAUSES`) or a read's
    (:data:`~association.query.result.RUN_CAUSES`) - from its facts, naming
    the fact that is missing (AGENTS.md, "A refusal names the missing
    thing, never only the slot"). The refusal counterpart of
    :func:`note_phrase` and :func:`decision_phrase`.

    .. versionadded:: 5.0.0
    """
    if kind in _FINGERPRINT_PHRASES:
        return _say_fingerprint_unavailable(kind, facts)
    phrase = _RUN_PHRASES.get(kind) or _RANKING_PHRASES.get(kind) or _WORDS_PHRASES.get(kind)
    said = phrase(facts) if phrase is not None else _cause_sentence(kind, facts)
    if said is None:
        raise ValueError(f"no sentence for the cause {kind!r}")
    return said


def say_refusal(refusal: Refusal) -> Reply:
    """A :class:`~association.query.result.Refusal` worded: its cause's one
    sentence (:func:`refusal_phrase`), under the keys the page reads it by,
    beside the page's values.

    .. versionadded:: 5.0.0
    """
    message = refusal_phrase(refusal.kind, refusal.facts)
    return Reply(data={**refusal.shown, **dict.fromkeys(refusal.under, message)}, answer=message)


def say_clarify(asked: Clarify) -> Reply:
    """A :class:`~association.query.result.Clarify` worded: the question back,
    naming the candidates - those on record by the name, or the near
    spellings of one nothing matched.

    .. versionadded:: 5.0.0
    """
    candidates = list(asked.candidates)
    if asked.why == "near_spelling":
        message = suggestion(asked.asked, candidates, asked.kind)
        shown = {"unmatched": asked.asked, "suggestions": candidates} if asked.shown is None else dict(asked.shown)
    else:
        message = clarification(asked.asked, candidates, asked.kind, asked.active)
        shown = {"ambiguous": asked.asked, "candidates": candidates} if asked.shown is None else dict(asked.shown)
    return Reply(data={**shown, **dict.fromkeys(asked.under, message)}, answer=message)


# --- the sayer table: the body decides -----------------------------------------


#: A Scalar body's sayer, by how it was reduced (:attr:`Scalar.how <association.query.result.Scalar.how>`).
_SCALAR_SAYERS: dict[str, Callable[[Result], Reply]] = {
    "per_game": _say_line,
    "count": say_threshold_count,
    "record": say_team_record,
    "season": say_player_line,
    "per_shot": say_shot_distance,
    "per_100": say_player_netpoints,
    "total": say_player_netpoints,
    "projection": say_team_outlook,
    "ranked": say_team_stat,
}

#: A Grouped body's sayer, by what one row is (:attr:`Grouped.by <association.query.result.Grouped.by>`).
_GROUPED_SAYERS: dict[str, Callable[[Result], Reply]] = {
    "player": _say_ranking,
    "subject": _say_comparison,
    "team": _say_teams,
    "threshold": say_record_when,
    "split": say_splits,
    "period": say_period_by_quarter,
    "presence": say_with_without,
    "month": say_team_record_by_month,
    "metric": say_team_stat,
    "season": say_player_history,
    "category": say_player_netpoints,
}

#: A Chart body's sayer, by what it draws (:attr:`Chart.kind <association.query.result.Chart.kind>`).
_CHART_SAYERS: dict[str, Callable[[Result], Reply]] = {
    "fingerprint": say_fingerprint,
    "shot_chart": say_shot_chart,
}


def _sayer(result: Result) -> Callable[[Result], Reply]:
    """The sayer of ``result``'s headline part, by the body's type and the
    one field that says what it is: a scalar by how it was reduced, a group
    by what one row is, rows by what they are in order of, a chart by what
    it draws - and nothing else (``ROADMAP-TYPES.md``, "The shapes": "a
    sayer may branch on the shape and on nothing else"). A sayer reached
    so may still tell its subject's kind or a cell it says (a team's log
    from a player's, a quarter's), which the draft's rule reads too."""
    body = result.parts[0].body if result.parts else None
    if isinstance(body, Scalar):
        return _SCALAR_SAYERS[body.how]
    if isinstance(body, Grouped):
        return _GROUPED_SAYERS[body.by]
    if isinstance(body, Chart):
        return _CHART_SAYERS[body.kind]
    if isinstance(body, Runs):
        return say_streak
    if isinstance(body, Rows):
        return _say_rows if body.by == "date" else say_single_game_high
    raise ValueError(f"no sayer for a {type(body).__name__} body")
