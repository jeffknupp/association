"""Resolving a name the model produced ("Lakers", "LAL", "Curry") to a real id.

Callers want different things from an ambiguous name - a chart of the wrong
Curry is a visible mistake, a NUMBER attributed to the wrong Curry is not - so
both behaviors stay available rather than one being picked for everyone:

    find_*    - every candidate, best first. The caller decides.
    resolve_* - one entity, or Ambiguous/NotFound. Never a guess.

Templates use resolve_*, because a template's job is to be trusted with a
number. Ambiguity is returned as a value, and the template asks a clarifying
question (see :func:`clarification`) rather than guessing or falling through.
Before asking, a template narrows the candidates to those with a row where its
answer is read from, for the season it will answer about (see
:func:`resolve_player`): a question about this season's Curry is not a
question about Dell, who retired in 2002.

Charts take the third road: they narrow with :func:`narrow_to_available` first,
to the candidates who have the rows the chart would be drawn from, and only ask
when more than one survives. What made a best match defensible there was the
plot being titled with the name that won - which is no help at all when the
wrong name means no plot gets drawn."""

from __future__ import annotations

import functools
import re
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal

import duckdb

from association.nba.franchises import FRANCHISE_ERAS, FranchiseEra, season_name
from association.nba.season import current_season
from association.query import names
from association.query.notes import decided
from association.query.reading import Scope, Unsupported
from association.query.result import Clarify, Refusal

MAX_CANDIDATES = 10


def _read_table(con: duckdb.DuckDBPyConnection, sql: str) -> tuple[list[tuple[str, str]], list[tuple[Any, ...]], Exception | None]:
    """One table's columns (name and type), rows and - instead of raising it
    - the error reading it raised: a partial warehouse with no ``teams``
    still names players, and each lookup raises the error where its own SQL
    used to (:func:`_player_index`, :func:`_team_index`)."""
    try:
        cursor = con.execute(sql)
        described = [(str(column[0]), str(column[1])) for column in cursor.description or []]
        return described, cursor.fetchall(), None
    except duckdb.Error as error:
        return [], [], error


def _read_players(con: duckdb.DuckDBPyConnection) -> tuple[list[tuple[str, str]], list[tuple[Any, ...]], Exception | None]:
    """The one statement every player-name lookup is answered from
    (:class:`association.query.names.PlayerIndex`)."""
    return _read_table(con, "SELECT athlete_id, display_name FROM players")


def _read_teams(con: duckdb.DuckDBPyConnection) -> tuple[list[tuple[str, str]], list[tuple[Any, ...]], Exception | None]:
    """The one statement every team-name lookup is answered from
    (:class:`association.query.names.TeamIndex`): every column, since which
    ones exist decides what a lookup raises."""
    return _read_table(con, "SELECT * FROM teams")


def _again(error: Exception) -> Exception:
    """A fresh copy of a kept error, to raise where the SQL would have."""
    return type(error)(*error.args)


def players_of(con: duckdb.DuckDBPyConnection) -> names.PlayerIndex:
    """The players' index for ``con``, as it was read: the error reading
    ``players`` raised is kept on it, not raised (:func:`checked_players`
    raises it where a lookup needs the rows, as that lookup's SQL did).

    What a reader of the question's words is handed in place of the
    connection: the in-memory index is all name recognition reads
    (``ROADMAP.md``, contract 3), and a function that holds no connection
    cannot run a statement (``scripts/check_ratchets.py``,
    ``con_in_the_reader``).

    .. versionadded:: 5.0.0
    """
    return names.players_for(con, _read_players)


def teams_of(con: duckdb.DuckDBPyConnection) -> names.TeamIndex:
    """The teams' index for ``con``, as :func:`players_of` hands the
    players': unchecked, its error kept (:func:`team_columns` raises it).

    .. versionadded:: 5.0.0
    """
    return names.teams_for(con, _read_teams)


def checked_players(index: names.PlayerIndex) -> names.PlayerIndex:
    """``index``, for a lookup that used to read ``players`` - raising what
    reading ``players`` raised, as that lookup's SQL did.

    .. versionadded:: 5.0.0
    """
    if index.error is not None:
        raise _again(index.error)
    return index


def team_columns(index: names.TeamIndex, *columns: str) -> names.TeamIndex:
    """``index``, for a lookup whose SQL read ``columns`` of ``teams`` -
    raising what that SQL raised on a warehouse without the table
    (:class:`duckdb.CatalogException`) or without one of the columns
    (:class:`duckdb.BinderException`). Several tests build a ``teams`` with
    no ``name`` or ``location``, and callers tell those errors apart.

    .. versionadded:: 5.0.0
    """
    if index.error is not None:
        raise _again(index.error)
    missing = [column for column in columns if column not in index.columns]
    if missing:
        raise duckdb.BinderException(f'Binder Error: Referenced column "{missing[0]}" not found in FROM clause!')
    return index


def _player_index(con: duckdb.DuckDBPyConnection) -> names.PlayerIndex:
    """The players' index, for a lookup that used to read ``players`` -
    raising what reading ``players`` raised, as that lookup's SQL did."""
    return checked_players(players_of(con))


def _team_index(con: duckdb.DuckDBPyConnection, *columns: str) -> names.TeamIndex:
    """The teams' index, for a lookup whose SQL read ``columns`` of ``teams``
    (:func:`team_columns`)."""
    return team_columns(teams_of(con), *columns)


def teams_named_by_word(teams: names.TeamIndex, word: str) -> list[str]:
    """The distinct display names of the teams ``word`` is a whole word of
    ("blazers" for the Portland Trail Blazers), in table order - from the
    teams' index, as every name lookup is (:mod:`association.query.names`).
    The words are the SQL's own split of the lowercased name
    (:func:`~association.query.names.sql_words`), which this replaced:
    ``list_contains(regexp_split_to_array(lower(display_name), '[^a-z]+'), ?)``,
    one statement per word of the question.

    .. versionadded:: 5.0.0

    .. versionchanged:: 5.0.0
       Takes the teams' in-memory index (:func:`teams_of`) in place of a
       connection.
    """
    found: list[str] = []
    for team in team_columns(teams, "display_name").rows:
        name = team["display_name"]
        if name is not None and name not in found and word in names.sql_words(str(name)):
            found.append(name)
    return found


def team_abbreviations(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Each team's abbreviation, lowercased as the SQL's ``lower`` does it,
    with its display name - from the teams' index. A team with no
    abbreviation has none.

    .. versionadded:: 5.0.0
    """
    index = _team_index(con, "abbreviation", "display_name")
    return {names.sql_lower(str(team["abbreviation"])): team["display_name"] for team in index.rows if team["abbreviation"] is not None}


def _team_like(index: names.TeamIndex, team: dict[str, Any], column: str, pattern: str) -> bool:
    """``<column> ILIKE pattern`` on one ``teams`` row (:func:`_ilike`)."""
    return _ilike(team[column], pattern, column in index.ascii_columns)


def _entities(index: names.PlayerIndex, rows: list[int]) -> list[Entity]:
    """``rows`` of the players' index, as entities."""
    return [Entity(id=str(index.rows[row][0]), name=str(index.rows[row][1])) for row in rows]


# Curated shorthand -> the player it unambiguously means. NOT the prominence
# tiebreak that was measured and rejected: that ranked every candidate by
# minutes or points, which also resolved "Brown" and "Mitchell" - names no
# query can reliably carry on their own. This is an explicit, auditable list,
# and a name absent from it still gets the clarifying question.
#
# A shorthand may take a token another player owns ("melo" is also Fab Melo's
# surname, "russ" also Russ Smith's) when one player dominates that shorthand
# in ordinary use, because a question carrying only the shorthand cannot
# reliably have meant the other player either - "show me Melo's fingerprint"
# is not a plausible request for Fab Melo's. First names are here on the same
# reasoning: "luka" and "kobe" resolve, where they used to ask. What stays out
# is a shorthand that is simply someone's ordinary name ("timmy" is Timmy
# Allen's actual first name, "buck" is Buck Williams's), and a surname on its
# own, which nobody can expect to complete: "brown" still asks, across ten
# candidates, and should.
#
# Confirmed against the warehouse: every value matches exactly one row in
# `players`, and no key is a name token belonging only to someone else. Re-run
# `python scripts/check_nicknames.py` after editing.
#
# Sourced from Wikipedia's "List of nicknames in basketball", filtered to
# players the warehouse actually has - it starts at 1993-94, so Bird, Kareem
# and Dr. J are not here because they are not in `players` at all.
PLAYER_NICKNAMES = {
    # Shorthand and initialisms.
    "sga": "Shai Gilgeous-Alexander",
    "wemby": "Victor Wembanyama",
    "alien": "Victor Wembanyama",
    "kd": "Kevin Durant",
    "cp3": "Chris Paul",
    "steph": "Stephen Curry",
    "chef curry": "Stephen Curry",
    "ad": "Anthony Davis",
    "the brow": "Anthony Davis",
    "dame": "Damian Lillard",
    "pg13": "Paul George",
    "kat": "Karl-Anthony Towns",
    "bron": "LeBron James",
    "lebron": "LeBron James",
    "king james": "LeBron James",
    "the king": "LeBron James",
    "ant": "Anthony Edwards",
    "ant man": "Anthony Edwards",
    "ant-man": "Anthony Edwards",
    "jrue": "Jrue Holiday",
    "trae": "Trae Young",
    "zion": "Zion Williamson",
    "book": "Devin Booker",
    "klay": "Klay Thompson",
    "luka": "Luka Doncic",
    "russ": "Russell Westbrook",
    "dlo": "D'Angelo Russell",
    "ja": "Ja Morant",
    "melo": "Carmelo Anthony",
    "boogie": "DeMarcus Cousins",
    "jojo": "Joel Embiid",
    "the process": "Joel Embiid",
    "the beard": "James Harden",
    "the claw": "Kawhi Leonard",
    "the klaw": "Kawhi Leonard",
    "giannis": "Giannis Antetokounmpo",
    "the greek freak": "Giannis Antetokounmpo",
    "greek freak": "Giannis Antetokounmpo",
    "joker": "Nikola Jokic",
    "og": "OG Anunoby",
    # "Rui" is Hachimura's real given name, the same shape as "luka" and
    # "kobe" above - and the warehouse also holds a "Rui Betancourt" who
    # shares the token, which is exactly why this entry matters: unresolved,
    # "rui" fell to find_players' alphabetical ordering and answered about
    # Betancourt. The curated lookup is checked before that ordering ever
    # runs, so it settles the one case a plain search could not.
    "rui": "Rui Hachimura",
    # Players whose careers reach back toward the warehouse's 1993-94 floor.
    # These are the ones the router gets wrong rather than merely misses: it
    # answered "The Answer" with Klay Thompson and "The Glove" with Jayson
    # Tatum, both confidently.
    "ai": "Allen Iverson",
    "a.i.": "Allen Iverson",
    "the answer": "Allen Iverson",
    "bubba chuck": "Allen Iverson",
    "vc": "Vince Carter",
    "vinsanity": "Vince Carter",
    "air canada": "Vince Carter",
    "half man half amazing": "Vince Carter",
    "kobe": "Kobe Bryant",
    "black mamba": "Kobe Bryant",
    "the mamba": "Kobe Bryant",
    "the big fundamental": "Tim Duncan",
    "the dream": "Hakeem Olajuwon",
    "the mailman": "Karl Malone",
    "the admiral": "David Robinson",
    "the glove": "Gary Payton",
    "the worm": "Dennis Rodman",
    "the truth": "Paul Pierce",
    "big ticket": "Kevin Garnett",
    "kg": "Kevin Garnett",
    "shaq": "Shaquille O'Neal",
    "the diesel": "Shaquille O'Neal",
    "mj": "Michael Jordan",
    "air jordan": "Michael Jordan",
    "the german": "Dirk Nowitzki",
    "t-mac": "Tracy McGrady",
    "c-webb": "Chris Webber",
    "penny": "Anfernee Hardaway",
    "the matrix": "Shawn Marion",
    "the reignman": "Shawn Kemp",
    "reign man": "Shawn Kemp",
    "agent zero": "Gilbert Arenas",
    "white chocolate": "Jason Williams",
    "the glide": "Clyde Drexler",
    "zo": "Alonzo Mourning",
    "j-kidd": "Jason Kidd",
    "d-wade": "Dwyane Wade",
    "flash": "Dwyane Wade",
    "manu": "Manu Ginobili",
    "birdman": "Chris Andersen",
    "the birdman": "Chris Andersen",
}


def _fold(text: str) -> str:
    """``"dončić"`` -> ``"doncic"``: the warehouse spells every name in plain
    letters, and a question typed with the accents matched nothing - "luka
    dončić last 15 games vs. magic" lost Luka and answered the Lakers' log."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


# Words of a name or a question, split on anything that is not a letter so
# "Gilgeous-Alexander" is two words and "Jokic's" is "Jokic" and a stray "s".
# Accents are folded first (_fold), so "dončić" is the one word "doncic".
def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[^A-Za-z]+", _fold(text)) if w]


def _initials(name: str) -> str:
    """ "kat" for Karl-Anthony Towns - what a question calls a player when it
    uses neither their name nor a nickname anybody wrote down."""
    words = _words(name)
    return "".join(w[0] for w in words).casefold() if len(words) > 1 else ""


def _exact_name_span(players: names.PlayerIndex, span: list[str], limit: int = 2) -> list[Entity]:
    """Players whose ``display_name`` holds every word of ``span`` as a whole
    word - the exact-match building block the subject reading's
    :func:`~association.query.subject.players_named_in` and
    :func:`~association.query.subject.question_derived_player` both need, so
    the lookup is written once.

    ``limit`` bounds the scan; 2 is enough to tell "exactly one" from "more
    than one" without reading out a whole surname's worth of rows. Rows come
    in table order, as the SQL this replaced returned them.
    """
    index = checked_players(players)
    return _entities(index, index.with_words([w.casefold() for w in span], int(limit)))


def _fuzzy_name_span(players: names.PlayerIndex, span: list[str], limit: int) -> list[Entity]:
    """Players within :func:`_edit_budget` of every word of ``span``, each
    against its nearest word of the name - the near-spelling counterpart of
    :func:`_exact_name_span`, and the same match :func:`suggest_players`
    makes for its own last pass, in table order. The AND across tokens is
    what keeps a short span from matching everybody.
    """
    index = checked_players(players)
    near = index.near([(w, _edit_budget(w)) for w in span])
    return _entities(index, [row for row, _total in near][: int(limit)])


def misread_players(names: list[str]) -> str:
    """The sentence for names the question does not support and nothing in it
    can replace.

    Said rather than passed to the agent, which is the whole point. Measured:
    "compare fingerprints for embiid vs jokic in 2026" fell through with an
    invented name, and the agent spent 55 seconds writing a confident
    fingerprint for "Ronaldo Lopes", who does not exist - percentages and all.
    The same reasoning as ``coverage.check_coverage`` returning its refusal
    instead of raising it: nothing downstream does better here, and an agent
    with nothing to find is free to fill the silence from its own weights.

    .. versionadded:: 2.1.0
    """
    if not names:
        return "This question could not be matched to a player, so it was not answered."
    joined = (", ".join(names[:-1]) + " and " if len(names) > 1 else "") + names[-1]
    return (
        f"This was read as a question about {joined}, who the question does not mention - so it was not answered, "
        "rather than answered about the wrong player. Naming the player in full usually fixes it."
    )


def team_only_question_names_a_player(named_player: str, intent: str) -> str:
    """The refusal sentence for :func:`~association.query.subject.player_named_on_a_team_only_question`
    - names the player it read rather than answering the league or a team's
    own numbers, the wrong subject.

    .. versionadded:: 4.4.0
    """
    return (
        f"This was read as a question about {named_player}, a player, but {intent.replace('_', ' ')} has no reading for one - "
        f"it would have answered the league's or a team's own numbers instead. Ask about {named_player}'s own stats, "
        "or name a team if a team's record was meant."
    )


# What a question calls a team beyond the words of its ESPN name. Only the
# ones no word of the display name already carries: "Knicks", "Celtics" and
# "Blazers" need nothing here, "sixers" and "cavs" do.
_TEAM_NICKNAMES: dict[str, str] = {
    "sixers": "Philadelphia 76ers",
    "philly": "Philadelphia 76ers",
    "cavs": "Cleveland Cavaliers",
    "mavs": "Dallas Mavericks",
    "wolves": "Minnesota Timberwolves",
    "twolves": "Minnesota Timberwolves",
    "dubs": "Golden State Warriors",
    "clips": "LA Clippers",
    "pels": "New Orleans Pelicans",
    "nola": "New Orleans Pelicans",
    "nugs": "Denver Nuggets",
    "grizz": "Memphis Grizzlies",
    "wiz": "Washington Wizards",
    # The abbreviations everyone uses and ESPN does not. Its `teams` table
    # abbreviates these four "GS", "NO", "NY" and "SA", so the three-letter
    # forms a question actually contains resolved to NOTHING - measured live,
    # "sam hauser v mil" works and "Lauri Markkan vs GSW last 5 games" loses
    # the team entirely.
    "gsw": "Golden State Warriors",
    "nop": "New Orleans Pelicans",
    "nyk": "New York Knicks",
    "sas": "San Antonio Spurs",
    # ESPN stores the Clippers as "LA Clippers", so the full form the router
    # writes - the prompt asks for full team names, and every other Los Angeles
    # team has one - matched nothing at all.
    "los angeles clippers": "LA Clippers",
}


# Shorthand a question or the router uses for a former name.
_ERA_ALIASES: dict[str, str] = {
    "sonics": "Seattle SuperSonics",
    "seattle sonics": "Seattle SuperSonics",
    "new orleans/oklahoma city hornets": "New Orleans Hornets",
    "nj nets": "New Jersey Nets",
}


def _team_key(text: str) -> str:
    """A team name normalized for lookup: case, spacing and a leading "the"."""
    key = " ".join(text.casefold().split())
    return key[4:] if key.startswith("the ") else key


def _era_index() -> dict[str, list[FranchiseEra]]:
    index: dict[str, list[FranchiseEra]] = {}
    for era in FRANCHISE_ERAS:
        words = era.name.casefold().split()
        # The full name, its nickname, and its city. Only the last word is a
        # nickname for every name listed here ("SuperSonics", "Grizzlies").
        for key in {" ".join(words), words[-1], " ".join(words[:-1])}:
            index.setdefault(key, []).append(era)
    for alias, name in _ERA_ALIASES.items():
        index.setdefault(alias, []).extend(era for era in FRANCHISE_ERAS if era.name == name)
    # Oklahoma City was the Hornets' home for two seasons before it had a team
    # of its own - the one city key no name above produces.
    index.setdefault("oklahoma city", []).extend(era for era in FRANCHISE_ERAS if era.name == "New Orleans Hornets")
    return index


_ERAS_BY_KEY = _era_index()


def _era_name(team_id: str, season: int) -> str | None:
    """What franchise ``team_id`` was called in ``season``, if it was renamed."""
    eras = [era for era in FRANCHISE_ERAS if era.team_id == team_id]
    held = [era for era in eras if era.covers(season)]
    return held[0].name if held else None


def _named_for_season(team_id: str, current_name: str, season: int | None) -> str:
    """A ``teams`` row's name as it was in ``season``; see franchises.season_name."""
    return season_name(team_id, season if season is not None else current_season(), current_name)


def franchise_by_name(text: str, season: int | None = None) -> list[Entity] | None:
    """The franchises a renamed or relocated team's name meant in ``season``.

    None when ``text`` is no name :data:`FRANCHISE_ERAS` knows, so the caller
    carries on with the ``teams`` table. A list otherwise, and the rule for
    choosing is what the name MEANT that season:

    - A name some franchise held in ``season`` means that franchise: "Hornets"
      in 2008 is id 3, the New Orleans Hornets, and in 2026 is id 30.
    - A name nobody held that season means the franchise that ever held it:
      "Pelicans" in 2008 is still id 3, answered under its 2008 name.
    - A name two franchises held, neither of them that season, is both, and the
      caller asks: "Hornets" in 2014 was nobody, between id 3's last Hornets
      season and id 30's first.

    ``season`` None means the current season, because every template defaults
    to it and a question naming no year means now.

    .. versionadded:: 2.2.0
    """
    eras = _ERAS_BY_KEY.get(_team_key(text))
    if not eras:
        return None
    season = season if season is not None else current_season()
    held = [era for era in eras if era.covers(season)]
    ids = sorted({era.team_id for era in (held or eras)}, key=int)
    return [Entity(id=team_id, name=_era_name(team_id, season) or next(era.name for era in eras if era.team_id == team_id)) for team_id in ids]


def _franchise_in(teams: names.TeamIndex, text: str, season: int | None) -> list[Entity] | None:
    """:func:`franchise_by_name`, kept only where the warehouse agrees.

    The era table names ESPN's ids, and a franchise is only trusted to be what
    that id holds if ``teams`` files today's name under it - the same guard
    :func:`_named_for_season` applies in the other direction. None when the
    name is not a former one, or no id checks out, so the caller falls back to
    the ``teams`` table.
    """
    found = franchise_by_name(text, season)
    if found is None:
        return None
    try:
        rows = {_as_varchar(team["team_id"]): team["display_name"] for team in team_columns(teams, "team_id", "display_name").rows}
    except duckdb.Error:
        return None
    today = {era.team_id: era.name for era in FRANCHISE_ERAS if era.last_season is None}
    kept = [team for team in found if rows.get(team.id) == today.get(team.id)]
    return kept or None


def _by_nickname(teams: names.TeamIndex, text: str, season: int | None) -> Entity | None:
    """The one team a name's NICKNAME points to, when its city is garbled.

    The router expands a question's nickname into a full name, and the city it
    supplies is sometimes invented: "blazers" came back as "Portland Blazers"
    (ESPN writes Portland TRAIL Blazers) and "kings" as "Los Angeles Kings".
    Literal matching finds nothing for either.

    The nickname decides, and the city may only confirm it or be unknown - it
    may never contradict it. "Portland Blazers" resolves, since Portland is the
    Blazers' city. "Los Angeles Kings" does not, because Los Angeles is two
    OTHER teams' city and choosing between an invented city and a real nickname
    is a guess; the question's own words settle that one instead, in
    :func:`association.query.subject.apply_subject`.
    """
    try:
        return _nickname_match(teams, text, season)
    except duckdb.Error:
        # `name` and `location` are columns of the real `teams` table and not
        # of every partial one. Finding nothing is the pre-existing answer.
        return None


def _nickname_match(teams: names.TeamIndex, text: str, season: int | None) -> Entity | None:
    words = _team_key(text).split()
    for size in (2, 1):
        if len(words) <= size:
            continue
        nickname, city = " ".join(words[-size:]), " ".join(words[:-size])
        named = _franchise_in(teams, nickname, season)
        if named is None:
            index = team_columns(teams, "team_id", "display_name", "name")
            named = [Entity(id=str(t["team_id"]), name=t["display_name"]) for t in index.rows if _team_like(index, t, "name", nickname) or _team_like(index, t, "name", f"% {nickname}")]
        if len(named) != 1:
            continue
        in_city = _franchise_in(teams, city, season)
        if in_city is None:
            index = team_columns(teams, "team_id", "location")
            in_city = [Entity(id=str(t["team_id"]), name="") for t in index.rows if _team_like(index, t, "location", city)]
        if not in_city or named[0].id in {team.id for team in in_city}:
            return named[0]
    return None


def _run_together(name: str) -> set[str]:
    """Every spelling of ``name`` with a space left out: "trailblazers",
    "portlandtrail" and "portlandtrailblazers" for Portland Trail Blazers.

    Whole words in the order the name holds them, so this adds spellings OF
    the name and never a different one - "blazerstrail" is not among them, and
    neither is a fragment of a word.
    """
    words = [word.casefold() for word in _words(name)]
    return {"".join(words[start:end]) for start in range(len(words)) for end in range(start + 2, len(words) + 1)}


def _run_together_team(teams: names.TeamIndex, text: str, season: int | None) -> Entity | None:
    """The one team ``text`` names with a space left out, or None.

    "trailblazers stats last 10 games" is how people write the only NBA team
    whose nickname is two words, and nothing literal reaches it: ``teams``
    holds "Portland Trail Blazers" and the question's single token equals no
    word of it. Seven of the thirty names are exposed this way - the six
    two-word cities as well, "goldenstate" and "newyork" among them - and
    every one of them resolved to nothing.

    Still never a guess, and it needs no length floor to stay that way: the
    letters have to EQUAL a whole run of the name's own words, so "la" and
    "new" match nothing here however short they are, and a run-together form
    two teams shared would resolve to neither.
    """
    key = "".join(_words(text)).casefold()
    try:
        rows = [(t["team_id"], t["display_name"]) for t in team_columns(teams, "team_id", "display_name").rows]
    except duckdb.CatalogException:
        # A partial warehouse with no `teams`; see `_team_named`.
        return None
    found = [row for row in rows if key in _run_together(row[1])]
    if len(found) != 1:
        return None
    return Entity(id=str(found[0][0]), name=_named_for_season(str(found[0][0]), found[0][1], season))


def _team_named(teams: names.TeamIndex, text: Any, season: int | None = None) -> Entity | None:
    """The one team ``text`` names outright - an id, an abbreviation, a nickname,
    or a name match starting a word - or None. Never a substring guess: "LA"
    is two teams and stays None, which is the point.

    A name a franchise used to carry is read for ``season``; see
    :func:`franchise_by_name`."""
    if not isinstance(text, str) or not text.strip():
        return None
    historic = _franchise_in(teams, text, season)
    if historic is not None:
        return historic[0] if len(historic) == 1 else None
    text = _TEAM_NICKNAMES.get(text.strip().casefold(), text.strip())
    try:
        index = team_columns(teams, "team_id", "abbreviation", "display_name")
        rows = [
            (t["team_id"], t["display_name"])
            for t in index.rows
            if t["team_id"] == text or _team_like(index, t, "abbreviation", text) or _team_like(index, t, "display_name", f"{text}%") or _team_like(index, t, "display_name", f"% {text}%")
        ][:2]
    except duckdb.CatalogException:
        # A warehouse without `teams` (a partial load) has no team to find.
        # Everything here is best-effort: finding none leaves the slots exactly
        # as the router gave them, which is never worse than before this ran.
        return None
    if len(rows) == 1:
        return Entity(id=str(rows[0][0]), name=rows[0][1])
    if rows:
        # Two matched, which is the ambiguity this refuses to guess at.
        return None
    nicknamed = _by_nickname(teams, text, season)
    return nicknamed if nicknamed is not None else _run_together_team(teams, text, season)


def teammate_names(value: Any) -> list[str]:
    """The teammates a ``without`` or ``with_player`` slot names, in order.

    The router reads every name the phrase holds - "without Tatum and Brown" is
    two people - so the slot is a list. A bare string is still read as one
    name: slot values are advisory everywhere else in this package, and a
    reader that understood only one shape would be one stray route away from
    answering nothing.

    .. versionadded:: 2.2.0
    """
    values = value if isinstance(value, list) else [value]
    return [v.strip() for v in values if isinstance(v, str) and v.strip()]


@dataclass(frozen=True)
class Entity:
    """One resolved player or team: an opaque warehouse id and its display name."""

    id: str
    name: str


@dataclass(frozen=True)
class Ambiguous:
    """`candidates` is for telling the user what to disambiguate between - it
    is the reason this is a return value and not just None.

    ``active`` counts the candidates, from the front, who played in the season
    the answer is about. :func:`clarification` names every one of them rather
    than counting any away; zero means no season narrowed the list, and the
    ordinary cap applies.

    .. versionchanged:: 2.1.0
       Added ``active``.
    """

    query: str
    candidates: list[str]
    active: int = 0


@dataclass(frozen=True)
class NotFound:
    """Nothing matched ``query`` - distinct from :class:`Ambiguous`, where too
    much did."""

    query: str


Resolution = Entity | Ambiguous | NotFound


MAX_CLARIFY_CANDIDATES = 5
"""How many candidates a "did you mean" sentence names before it starts
counting the rest instead.

.. versionadded:: 2.1.0
"""


# What "close enough" means, per token, when nothing matched exactly. Scaled to
# the token's length because one edit is a different claim about "Jr" than
# about "Antetokounmpo": a token of three letters or fewer must match a word
# outright, and only a long one is allowed two edits. Measured against the
# 3,101 names in ``players`` - at these budgets "Jokick" offers Nikola Jokic
# alone (at two, also Nikola Jovic), and "asdf", "goat", "coach" and "the
# answer" offer nothing at all.
def _edit_budget(token: str) -> int:
    return 0 if len(token) <= 3 else 1 if len(token) <= 6 else 2


def suggest_players(con: duckdb.DuckDBPyConnection, text: str) -> list[Entity]:
    """Players ``text`` plausibly meant, when it matched none of them exactly.

    Reached only after :func:`find_players` has come back empty, so it costs
    nothing on a question that works and it replaces an answer that was going
    to fail anyway. Two passes, in order, because they answer different
    failures:

    1. **The surname alone.** The router invents the half of a name the
       question does not contain. Asked to "compare sga and embid" it emitted
       ``'Jemel Embiid'`` - the surname corrected, the given name made up - and
       since every token must match, one fabricated word buried a player the
       warehouse holds. Dropping back to the last token is exact matching, not
       fuzzy, and recovers Joel Embiid.
    2. **Near spellings.** The user's own typo, which trusting the question
       cannot fix: "embid" is not a substring of "Embiid", so ILIKE never sees
       it. Every token must still be within :func:`_edit_budget` of some word
       of the name, and it is that AND across tokens that keeps the answer
       short - "Larry Bird" suggests nobody, because no Bird in the warehouse
       has a given name near "Larry".

    Suggestions are dropped entirely when there are more than
    ``MAX_CLARIFY_CANDIDATES`` of them. A long list is not a suggestion: it
    means the name was too common to narrow anything ("Smith" is within one
    edit of 25 players), and the question is better off falling through than
    reading out a directory. Pass 1 finding too many no longer ends the
    search, though - it falls through to pass 2, which is stricter (every
    token has to be close, not just the surname) and can still land on one
    real match: six Harpers share the surname alone, and only Dylan is also
    close on the given name "Dylon" typed instead.

    A name that resolves to a real team outright is never suggested, however
    close the edit distance: "Hawks" is one edit from Spencer Hawes, and
    offering him is a wrong answer to a question about a team, not a near
    miss on a player - the same false-cause shape :func:`no_match` exists to
    avoid elsewhere.

    Returns:
        Closest first, at most ``MAX_CLARIFY_CANDIDATES`` of them; empty when
        nothing is close enough to be worth naming.

    .. versionadded:: 2.1.0
    .. versionchanged:: 4.3.0
       Falls through to the near-spelling pass when the surname alone matches
       too many players, and never suggests a name that is really a team's.
    """
    tokens = [t for t in text.split() if t]
    if not tokens:
        return []

    if _team_named(teams_of(con), text) is not None:
        # A team name is not a near miss on a player. "Hawks" is one edit
        # from Spencer Hawes, and "Most reb by a hawk player history"
        # answered "did you mean Spencer Hawes?" instead of naming the real
        # cause: the question is about a team, and this template cannot
        # answer that - the same false-cause shape the Maxey refusal note
        # above warns about, one step earlier. The caller already has `text`
        # to explain that with; guessing a person here can only mislead it.
        return []

    backed_off = _suggest_players_by_surname(con, tokens)
    if backed_off:
        return backed_off
    # Otherwise more than MAX_CLARIFY_CANDIDATES share the surname alone, or
    # none do - either way that pass answers nothing on its own, and falls
    # through to the near-spelling pass rather than giving up. That pass is
    # stricter (every token has to be close, not just the last one), which is
    # exactly what a common surname needs: "Dylon Harper" backs off to 6
    # Harpers - too many to suggest - but only one of them, Dylan, is also
    # close on the given name.
    return _suggest_players_by_spelling(con, tokens)


def _suggest_players_by_surname(con: duckdb.DuckDBPyConnection, tokens: list[str]) -> list[Entity]:
    """:func:`suggest_players`' first pass: a multi-word name backed off to its
    last word, matched exactly at a word boundary. Empty when the name is one
    word, when nobody matches, and when more than ``MAX_CLARIFY_CANDIDATES``
    do - each of which leaves the near-spelling pass to try."""
    if len(tokens) < 2 or len(tokens[-1]) <= 2:
        return []
    # Word-boundary matches only. find_players falls back to incidental
    # substring hits when nothing starts with the token, which is fine for a
    # name somebody typed and wrong for one being guessed at: backing "Nobody
    # At All" off to "All" otherwise suggests Bo Wall.
    start = re.compile(_WORD_START + re.escape(tokens[-1]), re.IGNORECASE)
    kept = [player for player in find_players(con, tokens[-1]) if start.search(player.name)]
    return kept if len(kept) <= MAX_CLARIFY_CANDIDATES else []


def _suggest_players_by_spelling(con: duckdb.DuckDBPyConnection, tokens: list[str]) -> list[Entity]:
    """:func:`suggest_players`' second pass: the players every one of whose
    ``tokens`` is within :func:`_edit_budget` of some word of the name,
    closest first - or nobody, when more than ``MAX_CLARIFY_CANDIDATES`` are."""
    index = _player_index(con)
    # Closest first, then by name; two players of one name and one distance
    # stay in table order (an ORDER BY leaves that tie to the engine).
    near = sorted(index.near([(t, _edit_budget(t)) for t in tokens]), key=lambda found: (found[1], str(index.rows[found[0]][1])))
    rows = [row for row, _total in near][: MAX_CLARIFY_CANDIDATES + 1]
    return [] if len(rows) > MAX_CLARIFY_CANDIDATES else _entities(index, rows)


def read_near_spelling(con: duckdb.DuckDBPyConnection, text: str) -> Entity | None:
    """The one player ``text`` is a near spelling of, taken as the answer and
    said so - or None, and the caller asks or refuses as it always did.

    For a name slot that matched nobody exactly. Typos are the entity index's
    job, never the router model's: once names reach resolution as the question
    typed them, "embid" arrives as "embid", and asking "did you mean Joel
    Embiid?" of every typo would turn each one into a clarification. Jeff's
    rule (AGENTS.md, "A reasonable default beats a question") allows a default
    where the value used is shown and a wording reaches the alternative, so a
    single candidate is taken and :func:`note_typo_reading` puts both in the
    answer: who the text was read as, and that spelling the name exactly asks
    about somebody else.

    Only :func:`suggest_players`' near-spelling pass can default, and only when
    it holds exactly one player. That pass needs EVERY word of ``text`` within
    :func:`_edit_budget` of the chosen name, so "Stephen Cury" and "Dylon
    Harper" are misspellings of one real player. The surname back-off is not
    a misspelling: in "Jemel Embiid" or "Larry Bird" the given name is a
    different person's, and when the surname alone lands on one player the
    text is as likely to mean somebody the warehouse does not hold (Larry
    Bird is not in ``players``) as the one it does - so it still asks. Two or
    more near spellings ("jolic": Jokic or Jovic) still ask, and a team's name
    is never read as a player's ("Hawks" is one edit from Spencer Hawes).

    Never call this on words a question merely contains. It is for a span
    already given as a NAME: fuzzy-matching the question's leftover words finds
    somebody in most questions ("season" is one edit from Tari Eason), which
    is measured and recorded in AGENTS.md.

    .. versionadded:: 5.0.0
    """
    tokens = [t for t in text.split() if t]
    if not tokens or _team_named(teams_of(con), text) is not None:
        return None
    near = _suggest_players_by_spelling(con, tokens)
    if not near and len(tokens[-1]) > 3 and tokens[-1].casefold().endswith("s"):
        # A possessive typed without its apostrophe ("joel embids fingerprint")
        # is the name plus an "s" - one edit the budget spends before the typo
        # itself is reached. Only where the spelling as typed is near nobody,
        # so it never settles an ambiguity; the parser reads the same spelling
        # (parse.classify_span), and the two must agree on who is named.
        near = _suggest_players_by_spelling(con, [*tokens[:-1], tokens[-1][:-1]])
    if len(near) != 1:
        return None
    note_typo_reading(text, near[0])
    return near[0]


def unmatched(con: duckdb.DuckDBPyConnection, text: str, kind: str = "player") -> Clarify | Refusal:
    """A name nothing matched: the question back naming its near misses
    when there are any (a :class:`~association.query.result.Clarify`), or
    the refusal saying nothing matched (``name_unmatched``) - the page
    reading the sentence as its message either way, as the charts' did.

    .. versionadded:: 2.1.0
       As ``no_match``, which returned the sentence.

    .. versionchanged:: 5.0.0
       Returns the typed outcome; the sayer words it
       (``compose.say.suggestion``).
    """
    near = tuple(player.name for player in suggest_players(con, text))
    if near:
        return Clarify(asked=text, candidates=near, why="near_spelling", shown={}, under=("message",))
    return Refusal(kind="name_unmatched", facts={"asked": text, "kind": kind})


@dataclass(frozen=True)
class Availability:
    """The table a chart's rows come from, for narrowing a name to the players
    who could actually have produced the chart being asked for.

    A declared constant next to the code that owns the table, rather than a
    string literal at the call site: the name is interpolated into SQL, so the
    set of tables that can appear there stays small, named and checkable.

    .. versionadded:: 2.1.0
    """

    table: str


GAME_LOGS = Availability("player_game_log")
"""The game logs as an availability: the table a log rests on.

.. versionadded:: 5.0.0
   Declared beside :class:`Availability` (``templates.common.GAME_LOGS`` was this).
"""

BOX_SCORES = Availability("player_box_stats")
"""The box scores as an availability: the table a box-score answer rests on.

.. versionadded:: 5.0.0
   Declared beside :class:`Availability` (``templates.common.BOX_SCORES`` was this).
"""

SHOT_AVAILABILITY = Availability("shot_chart")
"""Where a shot chart's rows live, for narrowing an ambiguous name to the
players who actually took shots in the season being charted.

.. versionadded:: 2.1.0

.. versionchanged:: 5.0.0
   Declared beside :class:`Availability` (``shotchart.SHOT_AVAILABILITY`` is this).
"""


def narrow_to_available(
    con: duckdb.DuckDBPyConnection,
    candidates: list[Entity],
    source: Availability | tuple[Availability, ...],
    season: int | None = None,
    through: int | None = None,
) -> list[Entity]:
    """The candidates with at least one row in ``source``, in the order given -
    for ``season`` when one is given, and in any season when it is not.

    ``season`` is optional because a chart's is: a request that names no season
    is drawn over a whole career, and narrowing that by one year would be
    filtering the candidates by something the question never said.

    ``through`` is for an answer that covers a span rather than one season, and
    keeps anybody with a row in a season up to and including it. A history
    anchored at 2005 reads each player's last seasons up to 2005, wherever they
    fall, so a player who retired in 2002 still has an answer there, and only
    one who had not started yet is out.

    ``source`` may be several tables, for an answer read from more than one,
    and a row in ANY of them keeps a candidate, because a row in any of them is
    an answer. ``player_netpoints`` is the case in point: its two tables
    disagree about who they hold.

    Elimination, never preference. It drops the candidates who cannot be the
    answer to the question asked; it does not choose between two who both can.
    That distinction is the whole reason this is allowed to exist where the
    prominence tiebreak recorded above ``PLAYER_NICKNAMES`` was rejected -
    ranking by minutes or points also "resolved" Brown and Mitchell, which no
    question carries on its own. "Maxey" matches Marlon (last played 1994) and
    Tyrese; only one of them has a 2026 fingerprint, and that is a fact about
    the warehouse rather than a guess about who was meant.

    Returns an empty list when none of them has data, which the caller must
    handle rather than treat as "no such player": it means the question is
    unanswerable for everybody named, which is a different sentence.

    .. versionadded:: 2.1.0
    """
    sources = source if isinstance(source, tuple) else (source,)
    if not candidates or not sources:
        return []
    where = f"athlete_id IN ({', '.join('?' for _ in candidates)})"
    params: list[Any] = [c.id for c in candidates]
    if season is not None:
        where += " AND season = ?"
        params.append(season)
    if through is not None:
        where += " AND season <= ?"
        params.append(through)
    union = " UNION ".join(f"SELECT DISTINCT athlete_id FROM {s.table} WHERE {where}" for s in sources)
    have = {str(row[0]) for row in con.execute(union, params * len(sources)).fetchall()}
    return [c for c in candidates if c.id in have]


def _exact(candidates: list[Entity], text: str, keys: tuple[str, ...] = ("name",)) -> Entity | None:
    """An exact, case-insensitive hit on a full name (or abbreviation) beats
    any number of substring hits - otherwise a real full name that happens to
    be a prefix of another ("Jaylen Brown" vs. a hypothetical "Jaylen Brown
    Jr.") would resolve as ambiguous even though the user named one exactly."""
    wanted = text.strip().casefold()
    hits = [c for c in candidates if any(getattr(c, k).casefold() == wanted for k in keys)]
    return hits[0] if len(hits) == 1 else None


# A token matches "strongly" when it starts a word in the name rather than
# landing anywhere inside it. The boundary is any non-letter, not a space, so
# the halves of a hyphenated or apostrophed name each start a word: "Alexander"
# is a strong match for Shai Gilgeous-Alexander and Nickeil Alexander-Walker,
# "Neal" for Shaquille O'Neal.
_WORD_START = "(^|[^A-Za-z])"

# The letters DuckDB's regexp_matches(..., 'i') folds onto an ASCII one beyond
# its other case: RE2 folds by Unicode's simple case folding, under which the
# Kelvin sign is a "k" and the long s an "s" - and nothing else that is not
# ASCII is any ASCII letter (U+0130, a capital I with a dot, is NOT an "i" to
# RE2, where Python's re says it is).
_RE2_FOLDS = str.maketrans({"\u212a": "k", "\u017f": "s"})


def _starts_a_word(name: str, token: str) -> bool:
    """``regexp_matches(name, _WORD_START || <token, escaped>, 'i')``, as
    DuckDB answered it: ``token`` occurs in ``name``, ignoring case, at the
    start or after a character that is not a letter. ``token`` is ASCII here
    (:func:`find_players` folds it first)."""
    folded = "".join(char.lower() if char.isascii() else char for char in name.translate(_RE2_FOLDS))
    wanted = token.lower()
    start = folded.find(wanted)
    while start != -1:
        if start == 0 or not ("a" <= folded[start - 1] <= "z"):
            return True
        start = folded.find(wanted, start + 1)
    return False


@functools.lru_cache(maxsize=4096)
def _like_pattern(pattern: str) -> re.Pattern[str]:
    """A LIKE pattern as a regex: ``%`` any run, ``_`` any one character,
    and no escape character - DuckDB's LIKE has none unless one is named."""
    return re.compile("".join(".*" if char == "%" else "." if char == "_" else re.escape(char) for char in pattern), re.DOTALL)


def _ilike(value: str | None, pattern: str, plain: bool) -> bool:
    """``value ILIKE pattern`` as DuckDB answers it, ``plain`` saying whether
    the column ``value`` comes from holds only ASCII (``PlayerIndex.ascii_only``).
    There DuckDB lowers ASCII letters alone and compares the rest byte for
    byte, so a pattern holding anything else matches no value; elsewhere both
    sides are lowered as its ``lower()`` lowers them (:func:`names.sql_lower`).
    Then the pattern is matched whole. A NULL value matches nothing."""
    if value is None or (plain and not pattern.isascii()):
        return False
    return _like_pattern(names.sql_lower(pattern)).fullmatch(names.sql_lower(value)) is not None


def _as_varchar(value: Any) -> Any:
    """``CAST(value AS VARCHAR)`` for the ids ``teams`` holds - VARCHAR
    already in every warehouse and fixture, so this is the identity there."""
    return value if value is None or isinstance(value, str) else str(value)


def find_players(con: duckdb.DuckDBPyConnection, text: str, limit: int | None = MAX_CANDIDATES) -> list[Entity]:
    """Every token must match, so "Luka Doncic" doesn't also match a player
    sharing only a first name.

    ``limit`` bounds the list for a caller that will read it out. ``None``
    returns every match, which is what anything narrowing the list has to
    see: 71 name words match more than ``MAX_CANDIDATES`` players, and
    narrowing the first page of them is choosing by alphabet.

    .. versionchanged:: 2.1.0
       Candidates matching at a word boundary rank first and, when there are
       any, are the only ones returned. Incidental substring hits used to be
       ordered among them purely by name: "Ball" answered with Cedric Ceballos.
       Takes ``limit``.
    """
    # Matched against the WHOLE query, never as a substring, so "book" resolves
    # to Devin Booker while "notebook" is untouched - and "Ant" stops matching
    # every player with "ant" in their name (Durant, Anthony, Antetokounmpo).
    text = PLAYER_NICKNAMES.get(text.strip().casefold(), _fold(text))
    tokens = [t for t in text.split() if t]
    if not tokens:
        return []
    # Word-boundary matches rank first and, when there are any, are the whole
    # answer - the same two-step find_teams uses, and for the same reason:
    # substring matching keeps a name honestly ambiguous, but an INCIDENTAL hit
    # is not a candidate a person would recognize. "Ball" offered Cedric
    # Ceballos ahead of LaMelo, "Bey" offered Mike Tobey ahead of Saddiq, and
    # "Ford" offered Al Horford ahead of Aleem Ford - each of them the pick a
    # best-match caller then drew a chart of. Ordering inside the query rather
    # than filtering after it is load-bearing: LIMIT would otherwise be free to
    # truncate the strong matches away in favor of alphabetically earlier weak
    # ones.
    #
    # Two players of one name stay in table order; ORDER BY display_name
    # left that tie to the engine, whose order for it depended on which rows
    # were being sorted.
    index = _player_index(con)
    rows = [(athlete_id, name, all(_starts_a_word(name, t) for t in tokens)) for athlete_id, name in index.rows if name is not None and all(_ilike(name, f"%{t}%", index.ascii_only) for t in tokens)]
    rows.sort(key=lambda row: (not row[2], row[1]))
    if limit is not None:
        rows = rows[: int(limit)]
    matched = [row for row in rows if row[2]] or rows
    return [Entity(id=str(r[0]), name=str(r[1])) for r in matched]


def find_teams(con: duckdb.DuckDBPyConnection, text: str, season: int | None = None) -> list[Entity]:
    """Substring matching is kept (so "LA" stays honestly ambiguous rather than
    silently resolving to whichever team is literally named "LA"), but matches
    that start a word are ranked first - otherwise "LA" offers "Atlanta Hawks"
    as a candidate, which makes a clarification look broken.

    Two readings come before and after that. First, a name a renamed or
    relocated franchise carried is read for ``season`` - "Hornets" in 2008 is
    New Orleans, not today's Charlotte team (:func:`franchise_by_name`). Last,
    when nothing matches literally, a name whose city the router garbled is
    read by its nickname (:func:`_by_nickname`).

    .. versionchanged:: 2.2.0
       Reads ``season``, resolves former franchise names, and falls back to the
       nickname when the city is wrong.
    """
    teams = teams_of(con)
    historic = _franchise_in(teams, text, season)
    if historic is not None:
        return historic
    # "Sixers" and "Cavs" are no word of any ESPN team name; the router emits
    # them verbatim often enough that they fell through to the agent.
    text = _TEAM_NICKNAMES.get(text.strip().casefold(), text)

    index = _team_index(con, "team_id", "abbreviation", "display_name")
    matched = [t for t in index.rows if t["team_id"] == text or _team_like(index, t, "abbreviation", text) or _team_like(index, t, "display_name", f"%{text}%")]
    # Best tier first, then by name (a missing name last, as DuckDB sorts a
    # NULL); two teams of one name stay in table order.
    ranked = sorted(((t["team_id"], t["display_name"], _find_teams_rank(index, t, text)) for t in matched), key=lambda r: (-r[2], r[1] is None, r[1] or ""))
    rows = ranked[:MAX_CANDIDATES]
    # Only the best tier survives, and the two steps do different jobs. Dropping
    # rank 0 keeps "LA" inside "Atlanta" out of a clarification, so it offers
    # plausible teams rather than everything the LIKE touched. Ranking an
    # ABBREVIATION above a name match settles a collision that used to be
    # offered as a real ambiguity: "ORL" is Orlando's abbreviation and also a
    # substring of "New Orleans", so it returned both, and a question naming
    # one team could be answered about another. "LA" is still honestly
    # ambiguous - no team is abbreviated that - and both Los Angeles teams sit
    # at rank 1 together.
    if not rows:
        nicknamed = _by_nickname(teams, text, season) or _run_together_team(teams, text, season)
        return [nicknamed] if nicknamed is not None else []
    best = max((r[2] for r in rows), default=0)
    return [Entity(id=str(r[0]), name=_named_for_season(str(r[0]), r[1], season)) for r in rows if r[2] == best]


def _find_teams_rank(index: names.TeamIndex, team: dict[str, Any], text: str) -> int:
    """:func:`find_teams`' tier for one matched team: 2 is the team's own id
    or abbreviation; 1 a name match that starts a word ('LA%' catches "LA
    Clippers", '% LA%' catches "Los Angeles Lakers"); 0 an incidental
    substring."""
    if team["team_id"] == text or _team_like(index, team, "abbreviation", text):
        return 2
    return 1 if _team_like(index, team, "display_name", f"{text}%") or _team_like(index, team, "display_name", f"% {text}%") else 0


def _resolve(candidates: list[Entity], text: str, exact_keys: tuple[str, ...]) -> Resolution:
    if not candidates:
        return NotFound(query=text)
    if len(candidates) == 1:
        return candidates[0]
    exact = _exact(candidates, text, exact_keys)
    return exact if exact is not None else Ambiguous(query=text, candidates=[c.name for c in candidates])


_NAME_READINGS: ContextVar[list[str] | None] = ContextVar("association_name_readings", default=None)


@contextmanager
def collect_name_readings() -> Iterator[list[str]]:
    """Collect, for one question, every sentence saying how an open name was
    read - see :func:`resolve_player`.

    A default is only allowed where it is visible and correctable, so a name
    settled by "who played most recently" has to reach the answer as words: who
    it was read as, who else matched, and what to type to get the other one.
    The reading happens several calls below the template, in 27 call sites that
    take a bare connection, so it is collected here rather than threaded
    through every signature - a ``ContextVar`` and not a module global because
    the web server and the tests run questions on more than one thread. Outside
    this context nothing is collected and resolution behaves the same.

    .. versionadded:: 4.4.0
    """
    notes: list[str] = []
    token = _NAME_READINGS.set(notes)
    try:
        yield notes
    finally:
        _NAME_READINGS.reset(token)


def note_typo_reading(text: str, chosen: Entity) -> None:
    """Say a near spelling was read as ``chosen``, where somebody is
    listening - the same visible-and-correctable discipline
    :func:`_note_name_reading` carries for a bare-surname default, applied to
    :func:`suggest_players`' OWN single-candidate result. Jeff's rule
    (AGENTS.md, "A reasonable default beats a question"): a near spelling
    with exactly one candidate is taken rather than asked about, and the
    answer says so - "'wembyanama' matches no player exactly and was read as
    Victor Wembanyama" - so a wrong guess is visible and a real ambiguity
    (more than one candidate, which :func:`suggest_players` would have to be
    asked about instead of called with) is never silently picked. The second
    half of the sentence is the correction: spelled exactly, a name that is
    somebody's reaches him, and one that is nobody's gets the plain "no player
    found" instead of this reading.

    Public, unlike :func:`_note_name_reading`: written from
    :mod:`association.query.player_relation`, which has no other way to
    reach the :data:`_NAME_READINGS` context.

    .. versionadded:: 4.4.0
    .. versionchanged:: 5.0.0
       The sentence says how to reach anybody else - it used to name the
       reading alone - and :func:`read_near_spelling` writes it for every name
       slot :func:`resolve_player` and the chart resolver settle, not only a
       teammate's.
    """
    notes = _NAME_READINGS.get()
    if notes is None:
        return
    note = f"({text!r} matches no player exactly and was read as {chosen.name}, the only near spelling on record - spell the name exactly to ask about someone else.)"
    decided("name_reading", note, field="player", before=text, chose=chosen.name, why="near_spelling")
    if note not in notes:
        notes.append(note)


def _note_name_reading(text: str, chosen: Entity, others: list[Entity], season: int, *, named_in_full: bool) -> None:
    """Say how ``text`` was read, where somebody is listening. The second
    sentence is the point: it names the wording that reaches the other player,
    because a default nobody can correct is the one case this is not allowed."""
    notes = _NAME_READINGS.get()
    if notes is None:
        return
    shown = [c.name for c in others[:MAX_CLARIFY_CANDIDATES]]
    extra = len(others) - len(shown)
    also = _joined_names(shown) + ("" if extra <= 0 else f" and {extra} more")
    verb = "matches" if len(others) == 1 else "match"
    whom, they = ("him", "he") if len(others) == 1 else ("one of them", "they")
    how = f"name a season {they} played" if named_in_full else f"use the full name, or name a season {they} played,"
    note = f"({text!r} was read as {chosen.name}, the only match who played in {season - 1}-{season % 100:02d}. {also} also {verb} - {how} to ask about {whom}.)"
    decided("name_reading", note, field="player", before=text, chose=chosen.name, instead_of=[c.name for c in others], why="namesake" if named_in_full else "only_active", season=season)
    if note not in notes:
        notes.append(note)


def _joined_names(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"


def resolve_player(
    con: duckdb.DuckDBPyConnection,
    text: str,
    available: Availability | tuple[Availability, ...] | None = None,
    season: int | None = None,
    through: int | None = None,
) -> Resolution:
    """ "Curry" is genuinely ambiguous (Seth and Stephen), and a leaderboard
    row attributed to the wrong one is indistinguishable from a right answer.

    Given ``available``, an ambiguous name is first narrowed to the candidates
    with a row there - for ``season``, or any season up to ``through``, as in
    :func:`narrow_to_available` - and only the survivors are asked about. "How
    did curry do this year" asked about Dell, Eddy, JamesOn, Michael and Seth
    and left Stephen out, because the warehouse holds six Currys and the
    sentence names five; four of the five never played in the season the
    answer would be read from. Narrowed, it asks about Seth and Stephen, who
    both did.

    It eliminates and never chooses, which is what separates it from the
    prominence tiebreak rejected above ``PLAYER_NICKNAMES``: one survivor is
    the answer because nobody else has a row to answer from, and two or more
    are asked about. Three things keep it that way:

    - Every match is narrowed, not :func:`find_players`' first
      ``MAX_CANDIDATES``. Narrow that page and "the only Johnson with a row"
      means the only one among the first ten of 47, alphabetically.
    - A name matched exactly is that player while he has a row in the seasons
      asked about. "Gary Payton career points" is the father's. Only when he
      has none and exactly one namesake does is it the namesake, said in the
      answer - see :func:`_named_in_full`.
    - When narrowing eliminates everybody, everybody is asked about, exactly
      as before. No answer to "which one?" has data then either, and picking
      one because the season is empty would be the guess this refuses.

    **A name the question leaves open means whoever still plays.** With no
    season asked about, the survivors are narrowed once more, to the last
    season of the span (now, for a career): one left is the answer. "Maxey" is
    Tyrese, not Marlon, who last played in 1994; "show maxey's games against
    boston in the past two seasons" asked which of them was meant. This is a
    default, and the rule for a default is that it is **visible and
    correctable**: :func:`collect_name_readings` carries a sentence to the
    answer saying who the name was read as, who else matched, and what to type
    to reach him. Measured on the warehouse: of 391 surnames two or more
    players share, 124 have exactly one who played in 2026 and stop asking, and
    in 67 of those a retired namesake has more games on record ("wade" is Dean
    Wade, "pippen" is Scotty Pippen Jr.) - which is why the sentence is not
    optional. It is still not the prominence tiebreak: two namesakes who both
    played ("brown", "curry") are asked about, and nothing ranks them.

    Where two or more survive, whoever played in the season the answer is
    about is listed first and counted in ``Ambiguous.active``, which
    :func:`clarification` never cuts, so Seth and Stephen are named ahead of
    Dell rather than cut behind "1 other also matches".

    .. versionchanged:: 2.1.0
       Takes ``available``, ``season`` and ``through``, and narrows an
       ambiguous name by them before asking.

    .. versionchanged:: 4.4.0
       With no season asked about, one namesake who played in the latest
       season is the answer rather than a question, and a name given in full
       yields to the one namesake with data where its owner has none. Both are
       reported through :func:`collect_name_readings`.

    .. versionchanged:: 5.0.0
       A name that matches nobody but is a near spelling of exactly one player
       is that player, reported the same way (:func:`read_near_spelling`),
       where it used to be :class:`NotFound`.
    """
    if not available:
        return _resolve_player_unnarrowed(con, find_players(con, text), text)
    everyone = find_players(con, text, limit=None)
    if len(everyone) < 2:
        return _resolve_player_unnarrowed(con, everyone, text)
    # The season a name left open is settled by: the one asked about, else the
    # last season of the span, else now.
    latest = season if season is not None else through if through is not None else current_season()
    named = _exact(everyone, text)
    if named is not None:
        return _named_in_full(con, text, named, everyone, available, season, through, latest)
    narrowed = narrow_to_available(con, everyone, available, season, through)
    if len(narrowed) == 1:
        return narrowed[0]
    if not narrowed:
        # Nobody left is the old question over the old list - the same first
        # page find_players returns - rather than every Williams on record.
        return Ambiguous(query=text, candidates=[c.name for c in everyone[:MAX_CANDIDATES]])
    current = narrowed if season is not None else narrow_to_available(con, narrowed, available, latest)
    if season is None and len(current) == 1:
        # One of them still plays. "Maxey" is Tyrese and not Marlon, who last
        # played in 1994 - said in the answer, with the way to reach Marlon.
        _note_name_reading(text, current[0], [c for c in narrowed if c.id != current[0].id], latest, named_in_full=False)
        return current[0]
    in_season = {c.id for c in current}
    ordered = current + [c for c in narrowed if c.id not in in_season]
    return Ambiguous(query=text, candidates=[c.name for c in ordered], active=len(current))


def _resolve_player_unnarrowed(con: duckdb.DuckDBPyConnection, candidates: list[Entity], text: str) -> Resolution:
    """:func:`resolve_player` with nothing to narrow by: the one candidate, a
    question about several, or - with nobody by that name - the one player it
    is a near spelling of, said in the answer ("embid" is Joel Embiid; see
    :func:`read_near_spelling`)."""
    if not candidates:
        near = read_near_spelling(con, text)
        if near is not None:
            return near
    return _resolve(candidates, text, ("name",))


def _named_in_full(
    con: duckdb.DuckDBPyConnection,
    text: str,
    named: Entity,
    everyone: list[Entity],
    available: Availability | tuple[Availability, ...],
    season: int | None,
    through: int | None,
    latest: int,
) -> Resolution:
    """A name that is somebody's whole name is that player - unless he has
    nothing in the seasons asked about and exactly one namesake does.

    "Jabari Smith" is the retired father's exact name and the son is "Jabari
    Smith Jr.", so the son's log answered "no 2026 games" - a true sentence
    about the wrong man, and the refusal naming the wrong cause this project
    ranks beside a wrong answer. Same elimination as everywhere else here: the
    father cannot be the answer to a question about 2026, one player can, and
    the answer says so and says how to ask about the father. With two namesakes
    who could be, or none, the name means what it says, as it always did:
    "Gary Payton career points" is the father's, because he has a career.
    """
    if narrow_to_available(con, [named], available, season, through):
        return named
    could_be = [c for c in narrow_to_available(con, everyone, available, season, through) if c.id != named.id]
    if len(could_be) != 1:
        return named
    _note_name_reading(text, could_be[0], [named], latest, named_in_full=True)
    return could_be[0]


def resolve_team(con: duckdb.DuckDBPyConnection, text: str, season: int | None = None) -> Resolution:
    """One team, or a refusal. See :func:`resolve_player` for why ambiguity is
    returned rather than resolved.

    .. versionchanged:: 2.2.0
       Takes the ``season`` a team name is read for.
    """
    return _resolve(find_teams(con, text, season), text, ("name", "id"))


# "record" is the whole signal that a head_to_head naming a player is a
# question about that player's games rather than about two franchises.


def clarify(text: str, candidates: list[str], kind: Literal["player", "team"] = "player", active: int = 0) -> Clarify:
    """A handled outcome, not a fall-through: the reader knows exactly what
    is ambiguous, so it asks instead of passing the problem along. The
    sentence is the sayer's (``compose.say.clarification``), because the
    chart resolution reaches the same ambiguity and has to phrase it
    identically.

    .. versionadded:: 5.0.0
       Public, for the shot relation's reader (``compose.shots``).

    .. versionchanged:: 5.0.0
       Returns a :class:`~association.query.result.Clarify` rather than its
       worded answer.
    """
    return Clarify(asked=text, candidates=tuple(candidates), kind=kind, active=active)


def resolved_player(
    con: duckdb.DuckDBPyConnection,
    text: Any,
    missing: str = "no player named",
    *,
    available: Availability | tuple[Availability, ...],
    season: int | None = None,
    through: int | None = None,
) -> Entity | Clarify:
    """One player, or a clarifying question - the player counterpart
    to resolved_team. Returning the question rather than raising it keeps
    ambiguity a handled outcome: the caller answers with the question instead of
    guessing. Callers must forward it.

    `available` is required, so no template can resolve a name without saying
    where its answer comes from: an ambiguous name is narrowed to the players
    with a row there, for `season` or any season up to `through`, before
    anybody is asked about. See entities.resolve_player - "Curry" this season
    asked about four men who never played in it and left out Stephen."""
    if not isinstance(text, str) or not text.strip():
        raise Unsupported(missing)
    try:
        resolution = resolve_player(con, text, available, season, through)
    except duckdb.CatalogException:
        # The NetPoints tables exist only if that opt-in fetch was run. With
        # nothing to narrow against the name is asked about as it always was,
        # and the template's own query reports the missing table.
        resolution = resolve_player(con, text)
    match resolution:
        case Entity() as player:
            return player
        case Ambiguous(candidates=candidates, active=active):
            return clarify(text, candidates, active=active)
        case _:
            # A near miss is answered rather than passed along, for the same
            # reason ambiguity is: the agent would resolve the same name
            # against the same table, and a name nothing matches is a fact,
            # not a shape this template happens not to cover.
            near = tuple(player.name for player in suggest_players(con, text))
            if near:
                return Clarify(asked=text, candidates=near, why="near_spelling")
            raise Unsupported(f"no player matching {text!r}")


def resolved_team(con: duckdb.DuckDBPyConnection, text: Any, season: int | None = None) -> Entity | Clarify:
    """One team, a clarifying question, or a refusal - read for ``season``,
    because a franchise's name is a fact about a season. "Hornets" is New
    Orleans in 2008 and Charlotte in 2026; see entities.franchise_by_name."""
    if not isinstance(text, str) or not text.strip():
        raise Unsupported("no team named")
    match resolve_team(con, text, season):
        case Entity() as team:
            return team
        case Ambiguous(candidates=candidates):
            return clarify(text, candidates, kind="team")
        case _:
            raise Unsupported(f"no team matching {text!r}")


def slot_season(scope: Scope) -> int | None:
    """The season a question's team names are read for: the one it named, or
    None for "now" - the same default every template applies."""
    return scope.season


def optional_team(con: duckdb.DuckDBPyConnection, text: Any, season: int | None = None) -> Entity | Clarify | None:
    """A team slot that may be empty: ``None`` for no text, else
    :func:`resolved_team`'s entity or its refusal.

    .. versionadded:: 5.0.0
       Public, as the relation's shared step.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    return resolved_team(con, text, season=season)
