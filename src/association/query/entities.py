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
from typing import Any

import duckdb

from association.nba.franchises import FRANCHISE_ERAS, FranchiseEra, season_name
from association.nba.season import current_season
from association.query import names
from association.query.notes import decided

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


def _player_index(con: duckdb.DuckDBPyConnection) -> names.PlayerIndex:
    """The players' index, for a lookup that used to read ``players`` -
    raising what reading ``players`` raised, as that lookup's SQL did."""
    index = names.players_for(con, _read_players)
    if index.error is not None:
        raise _again(index.error)
    return index


def _team_index(con: duckdb.DuckDBPyConnection, *columns: str) -> names.TeamIndex:
    """The teams' index, for a lookup whose SQL read ``columns`` of ``teams``
    - raising what that SQL raised on a warehouse without the table
    (:class:`duckdb.CatalogException`) or without one of the columns
    (:class:`duckdb.BinderException`). Several tests build a ``teams`` with
    no ``name`` or ``location``, and callers tell those errors apart."""
    index = names.teams_for(con, _read_teams)
    if index.error is not None:
        raise _again(index.error)
    missing = [column for column in columns if column not in index.columns]
    if missing:
        raise duckdb.BinderException(f'Binder Error: Referenced column "{missing[0]}" not found in FROM clause!')
    return index


_LETTER_RUN = re.compile(r"[a-zA-Z']+")


def team_named_in(con: duckdb.DuckDBPyConnection, question: str) -> str | None:
    """The one team the question itself names, by a whole word of it (or a
    curated nickname) - the team counterpart of
    :func:`players_named_in`, kept deliberately
    minimal: single words only, since no franchise name has an internal
    ambiguity a span needs to resolve the way a player's first/last name
    does ("Portland Trail Blazers" is found by "blazers" alone; nothing
    named "Trail" collides with it). Never a guess between two candidates -
    only an exact single match counts, and the first match wins, read left
    to right the way a question states its subject first.

    Used to restore a team the router dropped entirely (F127, ISSUES.md:
    "how many 3 pointers have the magic made" routed with no ``team`` slot
    at all) - the same repair :func:`players_named_in`
    already makes for a dropped player.

    .. versionadded:: 4.4.0

    .. versionchanged:: 5.0.0
       Lives here, beside the player's reader: it was ``compose.team``'s,
       which the subject reading and the parser imported from the answer
       side to read a team word (``ROADMAP.md``, Phase 1).

    .. versionchanged:: 5.0.0
       A possessive ("the Sixers' record") names the team as the bare word does.
    """
    for found in _LETTER_RUN.findall(question.lower()):
        # "the Sixers' record", "the Knicks' last 5 games": the possessive
        # is the question's, not the name's (ISSUES.md #232 - 11 of 277
        # paraphrases read no team at all).
        word = found.removesuffix("'s").rstrip("'")
        if len(word) < 4:
            continue
        nickname = _TEAM_NICKNAMES.get(word)
        if nickname:
            return nickname
        named = teams_named_by_word(con, word)
        if len(named) == 1:
            return str(named[0])
    return None


def teams_named_by_word(con: duckdb.DuckDBPyConnection, word: str) -> list[str]:
    """The distinct display names of the teams ``word`` is a whole word of
    ("blazers" for the Portland Trail Blazers), in table order - from the
    teams' index, as every name lookup is (:mod:`association.query.names`).
    The words are the SQL's own split of the lowercased name
    (:func:`~association.query.names.sql_words`), which this replaced:
    ``list_contains(regexp_split_to_array(lower(display_name), '[^a-z]+'), ?)``,
    one statement per word of the question.

    .. versionadded:: 5.0.0
    """
    found: list[str] = []
    for team in _team_index(con, "display_name").rows:
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

# Built once: an alternation of every nickname, longest first so "greek freak"
# wins over a hypothetical "greek". Word boundaries are spelled as lookarounds
# rather than \b because several keys end in a non-word character ("a.i."),
# where \b asserts the opposite of what is wanted.
_NICKNAME_RE = re.compile(
    r"(?<![\w])(" + "|".join(re.escape(k) for k in sorted(PLAYER_NICKNAMES, key=len, reverse=True)) + r")(?![\w])",
    re.IGNORECASE,
)


def nicknames_in(question: str) -> list[str]:
    """Player names for every nickname appearing as a whole word in ``question``,
    in the order they appear, without repeats.

    Matched against the user's own words, which is the only place a nickname
    still exists: by the time a model has filled a slot it has usually
    rewritten the nickname, and when it rewrites one wrongly there is nothing
    downstream to notice.

    .. versionadded:: 2.1.0
    """
    seen: list[str] = []
    for match in _NICKNAME_RE.finditer(question):
        name = PLAYER_NICKNAMES[match.group(1).casefold()]
        if name not in seen:
            seen.append(name)
    return seen


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


def players_named_in(con: duckdb.DuckDBPyConnection, question: str) -> list[str]:
    """Players the question itself names, in the order it names them.

    The generalization of :func:`nicknames_in` from the curated table to the
    whole roster, and the same idea: the question is the only place a name the
    user actually typed still exists. Spans of three words down to one are
    tried left to right, longest first, so "karl anthony towns" is read as one
    name rather than three.

    Deliberately strict about what counts as naming somebody, because this is
    used to overrule the router. A span matches only if it is a nickname key or
    if every word of it equals a whole word of exactly one player's name -
    substring matching would read "What was the highest scoring game" as naming
    Jaron Blossomgame, and word-boundary matching would read "with" as naming
    Jeff Withey. Single words shorter than three letters are ignored for the
    same reason: the possessive left behind by "Jokic's" is an "s", which is a
    whole word of "John S. Williams".

    .. versionadded:: 2.1.0
    """
    words = _words(question)
    found: list[str] = []
    index = 0
    while index < len(words):
        for size in (3, 2, 1):
            if index + size > len(words):
                continue
            span = words[index : index + size]
            nickname = PLAYER_NICKNAMES.get(" ".join(span).casefold())
            if nickname is not None:
                found.append(nickname)
                index += size
                break
            if any(len(w) < 3 for w in span):
                continue
            rows = _exact_name_span(con, span)
            if len(rows) == 1:
                found.append(rows[0].name)
                index += size
                break
        else:
            index += 1
    seen: list[str] = []
    for name in found:
        if name not in seen:
            seen.append(name)
    return seen


def _exact_name_span(con: duckdb.DuckDBPyConnection, span: list[str], limit: int = 2) -> list[Entity]:
    """Players whose ``display_name`` holds every word of ``span`` as a whole
    word - the exact-match building block :func:`players_named_in` and
    :func:`_question_derived_player` both need, so the query is written once.

    ``limit`` bounds the scan; 2 is enough to tell "exactly one" from "more
    than one" without reading out a whole surname's worth of rows. Rows come
    in table order, as the SQL this replaced returned them.
    """
    index = _player_index(con)
    return _entities(index, index.with_words([w.casefold() for w in span], int(limit)))


def _fuzzy_name_span(con: duckdb.DuckDBPyConnection, span: list[str], limit: int) -> list[Entity]:
    """Players within :func:`_edit_budget` of every word of ``span``, each
    against its nearest word of the name - the near-spelling counterpart of
    :func:`_exact_name_span`, and the same match :func:`suggest_players`
    makes for its own last pass, in table order. The AND across tokens is
    what keeps a short span from matching everybody.
    """
    index = _player_index(con)
    near = index.near([(w, _edit_budget(w)) for w in span])
    return _entities(index, [row for row, _total in near][: int(limit)])


# players_named_in's own cap: a name is never longer than three words once
# _words has split a hyphenated one into halves.
_SPAN_MAX_WORDS = 3


def _anchor_word_position(q_words: list[str], lowered_q: list[str], word: str) -> int | None:
    """Where ``word`` (one word of the router's name) turns up in the
    question - exactly, or the nearest near spelling within
    :func:`_edit_budget` - or ``None`` when it turns up nowhere at all."""
    exact = next((i for i, w in enumerate(lowered_q) if w == word.casefold()), None)
    if exact is not None:
        return exact
    if len(word) < 3:
        return None  # a near spelling of a word this short is a different word
    # Measured as DuckDB's damerau_levenshtein(lower(q), word) measured it,
    # when this asked the warehouse to do the arithmetic.
    row = [names.distance(names.sql_lower(q), word.casefold()) for q in q_words]
    budget = _edit_budget(word)
    near = [i for i, d in enumerate(row) if d <= budget and len(lowered_q[i]) >= 3]
    return min(near, key=lambda i: row[i]) if near else None


def _resolve_word_span(con: duckdb.DuckDBPyConnection, span: list[str]) -> Entity | None:
    """``span``, resolved to one player - exact words before near ones, the
    same order :func:`players_named_in` tries - or ``None``. A single word is
    never handed to the fuzzy pass; see :func:`_question_derived_player`."""
    if any(len(w) < 3 for w in span):
        return None
    matches = _exact_name_span(con, span)
    if len(matches) != 1 and len(span) > 1:
        matches = _fuzzy_name_span(con, span, limit=2)
    return matches[0] if len(matches) == 1 else None


def _question_derived_player(con: duckdb.DuckDBPyConnection, question: str, name: str) -> Entity | None:
    """The one player a window of the question's OWN words - anchored to
    wherever ``name`` (the router's guess, right or wrong) itself appears -
    plausibly names, when that is confident enough to act on.

    Two faults this repairs, both structural, and both invisible to the
    "any one word is enough" check a name is otherwise held to
    (:func:`~association.query.subject.question_supports`), because that check
    is deliberately generous: the router TRUNCATES a name the question spells in
    full ("dennis schröder", typed correctly, arrived as just ``'Dennis'`` -
    grounded, and a 7-way surname), and it FABRICATES a word next to a real
    one ("tatum rec home" arrived as ``'Jaylen Tatum'``, the league's only
    Tatum with an invented given name bolted on; "Grady dick" arrived as
    ``'Grady Dickinson'``, grounded by its own typo'd given name). Neither
    ever reaches a repair, because grounding already says yes.

    So this reads the question instead of trusting the router's spelling: it
    anchors each of ``name``'s own words to where it turns up nearby - exactly,
    or within :func:`_edit_budget`, since the router silently corrects typos
    and a near spelling still marks the spot - and resolves the question's
    literal words there against the roster, never the router's spelling.

    Deliberately anchored, never a sentence-wide scan: fuzzy-matching a
    question's leftover words was measured and rejected elsewhere in this
    module ("season" is one edit from Tari Eason, wherever it turns up), and
    what keeps this safe is that nothing is tried unless it sits next to a
    word the router already pointed at - a name with no anchor at all is left
    for the ungrounded path below to report, exactly as before.

    Two things measured against real corpus rows keep this from trusting too
    little of a coincidence:

    - **Two or more of the router's own words anchoring is answered from
      within exactly that range of the question, narrowed a word at a time -
      but never down to one word alone.** "kareem stats vs bob lanier"
      anchors both "bob" and "lanier" (the question's own words, spelled
      exactly), and that pair names nobody: Bob Lanier retired before the
      warehouse's 1993-94 floor. Falling back to "lanier" alone then named
      Chaz Lanier, a real but wholly unrelated player - the same false-cause
      shape the Maxey example in the module docstring warns about, arrived at
      through this function instead of a nickname. The range still shrinks
      rather than being tried whole-or-nothing, because the router's own
      spelling can itself be the mismatch: "de'angelo russell" anchors "de",
      "angelo" and "russell" all exactly, and the full three-word span fails
      only because the roster spells the first of them "d", not "de" -
      dropping it and resolving "angelo russell" alone is what recovers
      D'Angelo Russell. What is never tried is the SINGLE remaining word once
      the range is down to it: two anchored words failing together, with no
      narrower range above one word left to try, is the answer, not an
      invitation to trust one of them alone.
    - **With exactly one anchor, a WINDOW around it (two words or more) is
      trusted regardless of where the anchor sits** - "Grady dick" anchors
      only on the given name "Grady", and the window's "grady dick" is what
      resolves it to Gradey Dick. What is trusted only when that anchor is
      the LAST of the router's words, the position a surname sits in, is
      falling all the way back to the anchor word ALONE, with nothing else
      corroborating it. "Jaylen Tatum" anchors only on "Tatum", its last
      word, and the league's only Tatum is trustworthy alone. "Kareem
      Abdul-Jabbar" anchors only on "Kareem", its FIRST word, and "Kareem"
      alone is exactly as unrelated a near-miss as "lanier" alone above, for
      the identical reason - Kareem Abdul-Jabbar is the other player these
      two corpus rows have in common, and neither he nor Bob Lanier has a
      row in `players` at all. A router-supplied given name with nothing
      else in the question corroborating it is left alone here, for the
      "any one word" check to judge as it always has.

    Returns:
        The one player the question's own words resolve to, or ``None`` when
        nothing anchors at all, when two or more anchored words do not
        resolve together, when a single anchor resolves only alone and is
        not the last of the router's words, or when what is left resolves to
        more than one player (left for :func:`resolve_player` to ask about)
        or to none.
    """
    q_words = _words(question)
    name_words = [w for w in _words(name) if w]
    if not q_words or not name_words:
        return None

    lowered_q = [w.casefold() for w in q_words]
    positions = [_anchor_word_position(q_words, lowered_q, word) for word in name_words]
    anchors = {p for p in positions if p is not None}
    if not anchors:
        return None

    bounds = _question_derived_player_multi_anchor_window(q_words, anchors) if len(anchors) >= 2 else _question_derived_player_single_anchor_window(q_words, name_words, positions, anchors)
    if bounds is None:
        return None
    window, min_size = bounds

    try:
        return _question_derived_player_search(con, window, min_size)
    except duckdb.CatalogException:
        # A warehouse without `players` (a partial load, or a test double) has
        # nothing here to resolve against - the same best-effort rule
        # `_team_named` follows: finding nothing leaves the slots exactly as
        # the router gave them, which is never worse than before this ran.
        return None


def _question_derived_player_multi_anchor_window(q_words: list[str], anchors: set[int]) -> tuple[list[str], int] | None:
    """The window and size floor for two or more anchored words - resolved
    from exactly that range of the question, one word narrower at a time,
    but never down to a single word alone. See
    :func:`_question_derived_player`'s docstring's "kareem ... bob lanier"
    measurement for why: falling back that far is what named Chaz Lanier.
    "de'angelo russell" is why the range still shrinks at all - the router's
    own spelling of "De" does not match the roster's "D", and dropping it
    lets "angelo russell" resolve on its own. ``None`` when the anchors are
    too spread out to be one coherent name."""
    if max(anchors) - min(anchors) > 4:
        return None
    return q_words[min(anchors) : max(anchors) + 1], 2


def _question_derived_player_single_anchor_window(q_words: list[str], name_words: list[str], positions: list[int | None], anchors: set[int]) -> tuple[list[str], int]:
    """The window and size floor for exactly one anchored word: a small
    window around it, never the whole question - what keeps
    :func:`_question_derived_player` from becoming the rejected
    leftover-word scan. Two or more words of THIS window agreeing is trusted
    regardless of where the anchor sits ("Grady dick" anchors on the given
    name "Grady", and the window's "grady dick" is what resolves it) - what
    is trusted only from the surname position (the floor of 1 rather than 2)
    is falling all the way back to the anchor WORD ALONE, with nothing else
    corroborating it; see the docstring's "Kareem" measurement."""
    anchor_index = next(i for i, p in enumerate(positions) if p is not None)
    anchor = next(iter(anchors))
    window = q_words[max(0, anchor - 1) : min(len(q_words), anchor + 2)]
    min_size = 1 if anchor_index == len(name_words) - 1 else 2
    return window, min_size


def _question_derived_player_search(con: duckdb.DuckDBPyConnection, window: list[str], min_size: int) -> Entity | None:
    """``window``, tried longest span first down to ``min_size``, exact
    words before near ones - the shared search both
    :func:`_question_derived_player_multi_anchor_window` and
    :func:`_question_derived_player_single_anchor_window` resolve into."""
    for size in range(min(_SPAN_MAX_WORDS, len(window)), min_size - 1, -1):
        found = [resolved for start in range(len(window) - size + 1) if (resolved := _resolve_word_span(con, window[start : start + size])) is not None]
        unique_ids = {c.id for c in found}
        if len(unique_ids) == 1:
            return found[0]
    return None


# The question saying it compares things at all. Restoring a dropped player
# needs this, because players_named_in is strict but not infallible: "best" is
# Travis Best and "boston" is Brandon Boston Jr., so "plot jokic's fingerprint
# from his best season" names two players by its rules and would otherwise
# have drawn Travis Best a polygon.
_COMPARISON = re.compile(r"\b(?:vs\.?|versus|compare[ds]?|comparing|comparison)\b", re.IGNORECASE)

# "X vs Y", the one structural signal that two SUBJECTS were meant. Tighter
# than _COMPARISON on purpose: "compare Jokic's fingerprint to last season"
# compares seasons, and a note claiming a player is missing there would be
# noise. Matched whole so a surname containing "vs" does not count.
_VERSUS = re.compile(r"\b(?:vs\.?|versus)\b", re.IGNORECASE)


def compared_but_unmatched(con: duckdb.DuckDBPyConnection, question: str, held: list[str]) -> str | None:
    """The caveat for a "vs" fingerprint question that drew only one polygon,
    or None where none applies.

    A misspelling nothing can repair - "generate fingerprints for embiid vs
    jolic" drew Joel Embiid alone, because "jolic" matches no player and is not
    close enough to exactly one to guess at. Recovering it was measured and
    rejected: a near-spelling search over a question's leftover words finds a
    spurious player in 29 of 51 corpus questions ("season" is one edit from
    Tari Eason, "most" from Quinten Post), and it does not find Nikola Jokic
    here either. That case still gets the spelling note.

    But one polygon on a "vs" question has a second cause that is not a
    misspelling at all: "show a fingerprint for maxey vs jaylen brown in 2026"
    said "only one of them matches anybody in the warehouse - check the
    spelling of the other" about Jaylen Brown, whom ``players`` holds and
    ``net_points_player_fingerprint`` has a 2026 row for - he was simply never
    carried into the answer. That is the mirror-image bug AGENTS.md calls out
    under "a refusal that names the wrong cause", so before blaming a
    spelling, the leftover name is resolved against the roster - and where
    it resolves, the sentence says the player was dropped, never that he is
    missing from the warehouse.

    .. versionadded:: 2.1.0
    .. versionchanged:: 4.4.0
       Takes ``con`` and returns the caveat text (or ``None``) rather than a
       bool, so a name that resolves against the roster gets a true sentence
       instead of being folded into the same claim as one that does not.
    """
    if len(held) >= 2 or not _VERSUS.search(question):
        return None
    dropped = [name for name in players_named_in(con, question) if not any(_shares_word(name, k) for k in held)]
    if dropped:
        return decided("name_left_out", f"Note: the question also names {' and '.join(dropped)}, who was not included in this answer.", field="players", chose=list(held), why="dropped", names=dropped)
    said = "Note: the question compares two players, but only one of them matches anybody in the warehouse - check the spelling of the other."
    return decided("name_left_out", said, field="players", chose=list(held), why="unmatched")


def misread_players(names: list[str]) -> str:
    """The sentence for names the question does not support and nothing in it
    can replace.

    Said rather than passed to the agent, which is the whole point. Measured:
    "compare fingerprints for embiid vs jokic in 2026" fell through with an
    invented name, and the agent spent 55 seconds writing a confident
    fingerprint for "Ronaldo Lopes", who does not exist - percentages and all.
    The same reasoning as ``templates.check_coverage`` returning its refusal
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


def _has_a_real_team(con: duckdb.DuckDBPyConnection, slots: dict[str, Any], question: str) -> bool:
    """Whether ``slots`` carries a ``team``/``teams`` value that actually
    resolves to a real franchise - not merely a non-empty string. The router
    invents a team the way it invents a player (AGENTS.md, "the router
    invents names"): "alperen şengün alltime record" arrived one run with no
    `team` at all, and another with `team='Alperen Şengün'` - the player's
    own name, filed as though it were a franchise, which
    :func:`~association.query.templates.teams.team_record`-shaped
    templates then refuse as "no team matching", the wrong cause. A team
    slot nothing resolves is functionally the same as no team slot at all.

    So is one that resolves to a franchise the question never names:
    "towns home rec including playoffs since 1/26/20 vs spurs" (yardstick-v2
    F110) arrived with ``team='Toronto Raptors'`` - a real team, and no word
    of it in the question - and would have answered the Raptors' record
    about a question naming Karl-Anthony Towns (:func:`_team_grounded`)."""
    season = slots.get("season") if isinstance(slots.get("season"), int) else None
    teams = slots.get("teams")
    texts = [slots.get("team"), *(teams if isinstance(teams, list) else [teams])]
    for text in texts:
        team = _team_named(con, text, season) if isinstance(text, str) and text.strip() else None
        if team is not None and _team_grounded(con, question, team):
            return True
    return False


def player_named_on_a_team_only_question(con: duckdb.DuckDBPyConnection, question: str, slots: dict[str, Any]) -> str | None:
    """The one player a question names, when the routed intent has no
    reading for him at all and the question names no REAL team either - or
    None.

    yardstick-v2 F111: "alperen şengün alltime record" routed to
    ``team_leaderboard`` - no player slot exists on that intent at all, and
    no ``team`` slot that resolves to a real franchise was filled either -
    and answered the league standings, entirely off the subject the
    question named. Diacritics are already handled: ``players_named_in``
    folds "şengün" to "sengun" (:func:`_fold`) before matching, the same way
    it reads "dončić". See :func:`_has_a_real_team` for why an invented
    ``team`` value (the player's own name, filed as though it were a
    franchise - measured live, a second run of this exact question) counts
    as no team at all rather than stopping this check.

    Caller-gated to :data:`~association.query.templates.common.TEAM_ONLY_INTENTS`
    (this function does not check the intent itself, the same shape the
    subject reading's intent sets take in
    :func:`association.query.subject.apply_subject`): a REAL team already named there is the
    real subject, and a player coincidentally named beside it changes no
    answer - the same reasoning that keeps a stray name on ``head_to_head``
    from being refused elsewhere in this module. A word that names only a
    team ("magic" in "magic vs nets" is the Orlando Magic, not Magic
    Johnson - :func:`_named_only_by_a_team_word`) or only a common English
    word that collides with a surname ("best" is Travis Best -
    :func:`_named_only_by_a_common_word`) is excluded the same way
    the subject reading already excludes both.

    .. versionadded:: 4.4.0
    """
    if _has_a_real_team(con, slots, question):
        return None
    named = [name for name in players_named_in(con, question) if not _named_only_by_a_team_word(con, question, name) and not _named_only_by_a_common_word(question, name)]
    return named[0] if len(named) == 1 else None


def team_only_question_names_a_player(named_player: str, intent: str) -> str:
    """The refusal sentence for :func:`player_named_on_a_team_only_question`
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


def _franchise_in(con: duckdb.DuckDBPyConnection, text: str, season: int | None) -> list[Entity] | None:
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
        rows = {_as_varchar(team["team_id"]): team["display_name"] for team in _team_index(con, "team_id", "display_name").rows}
    except duckdb.Error:
        return None
    today = {era.team_id: era.name for era in FRANCHISE_ERAS if era.last_season is None}
    kept = [team for team in found if rows.get(team.id) == today.get(team.id)]
    return kept or None


def _by_nickname(con: duckdb.DuckDBPyConnection, text: str, season: int | None) -> Entity | None:
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
        return _nickname_match(con, text, season)
    except duckdb.Error:
        # `name` and `location` are columns of the real `teams` table and not
        # of every partial one. Finding nothing is the pre-existing answer.
        return None


def _nickname_match(con: duckdb.DuckDBPyConnection, text: str, season: int | None) -> Entity | None:
    words = _team_key(text).split()
    for size in (2, 1):
        if len(words) <= size:
            continue
        nickname, city = " ".join(words[-size:]), " ".join(words[:-size])
        named = _franchise_in(con, nickname, season)
        if named is None:
            index = _team_index(con, "team_id", "display_name", "name")
            named = [Entity(id=str(t["team_id"]), name=t["display_name"]) for t in index.rows if _team_like(index, t, "name", nickname) or _team_like(index, t, "name", f"% {nickname}")]
        if len(named) != 1:
            continue
        in_city = _franchise_in(con, city, season)
        if in_city is None:
            index = _team_index(con, "team_id", "location")
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


def _run_together_team(con: duckdb.DuckDBPyConnection, text: str, season: int | None) -> Entity | None:
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
        rows = [(t["team_id"], t["display_name"]) for t in _team_index(con, "team_id", "display_name").rows]
    except duckdb.CatalogException:
        # A partial warehouse with no `teams`; see `_team_named`.
        return None
    found = [row for row in rows if key in _run_together(row[1])]
    if len(found) != 1:
        return None
    return Entity(id=str(found[0][0]), name=_named_for_season(str(found[0][0]), found[0][1], season))


# "vs", "versus", "against" or "v" and whatever follows. Whether what follows is
# a team is decided against the teams table, not here: "lebron vs kawhi" is two
# players and must stay a comparison.
_AGAINST = re.compile(r"\b(?:vs\.?|versus|against|v\.?)\s+(?:the\s+)?(.+)", re.IGNORECASE)


def _team_named(con: duckdb.DuckDBPyConnection, text: Any, season: int | None = None) -> Entity | None:
    """The one team ``text`` names outright - an id, an abbreviation, a nickname,
    or a name match starting a word - or None. Never a substring guess: "LA"
    is two teams and stays None, which is the point.

    A name a franchise used to carry is read for ``season``; see
    :func:`franchise_by_name`."""
    if not isinstance(text, str) or not text.strip():
        return None
    historic = _franchise_in(con, text, season)
    if historic is not None:
        return historic[0] if len(historic) == 1 else None
    text = _TEAM_NICKNAMES.get(text.strip().casefold(), text.strip())
    try:
        index = _team_index(con, "team_id", "abbreviation", "display_name")
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
    nicknamed = _by_nickname(con, text, season)
    return nicknamed if nicknamed is not None else _run_together_team(con, text, season)


def _team_after_versus(con: duckdb.DuckDBPyConnection, question: str, season: int | None = None) -> Entity | None:
    """The team a question sets a subject AGAINST ("jaylen brown last 8 games vs
    pistons"), or None. Spans of three words down to one are tried, so "vs new
    york" is the Knicks rather than an ambiguous "new"."""
    for match in _AGAINST.finditer(question):
        words = _words(match.group(1))[:3]
        for size in (3, 2, 1):
            if size <= len(words) and len(" ".join(words[:size])) >= 2:
                team = _team_named(con, " ".join(words[:size]), season)
                if team is not None:
                    return team
    return None


# "for", "with the" and whatever follows - a player's OWN team, unlike
# _AGAINST's opponent. Loose on purpose, the same way _AGAINST is: a false
# match ("stats for this season") tries "this season" against the teams
# table and simply fails to find one, which costs nothing - the DB lookup
# is the real gate, not the regex. "with" alone is not read here: "westbrook
# stats with the clippers" and "westbrook stats vs the clippers" mean
# different things, but a bare "with" also introduces `without`'s own
# teammate phrasing ("stats with steph curry on the floor" names a
# TEAMMATE, not a team), so only "with the" - which a teammate's name never
# takes - is read as this shape.
_FOR_TEAM = re.compile(r"\bfor\s+(?:the\s+)?(.+)|\bwith\s+the\s+(.+)", re.IGNORECASE)


def _team_after_for(con: duckdb.DuckDBPyConnection, question: str, season: int | None = None) -> tuple[Entity, int] | None:
    """The player's OWN team a question names with "for"/"with the"
    ("lebron stats as a starter for Miami", yardstick-v2 F166) with where
    in the question the phrase starts, or None. Spans of three words down
    to one are tried, the same as :func:`_team_after_versus`.

    .. versionadded:: 4.4.0
    """
    for match in _FOR_TEAM.finditer(question):
        span_text = match.group(1) or match.group(2) or ""
        words = _words(span_text)[:3]
        for size in (3, 2, 1):
            if size <= len(words) and len(" ".join(words[:size])) >= 2:
                if size == 1 and words[0].casefold() in _COMMON_WORDS_THAT_NAME_TEAMS:
                    # "for me" is the asker, not the Memphis Grizzlies.
                    continue
                team = _team_named(con, " ".join(words[:size]), season)
                if team is not None:
                    return team, match.start()
    return None


#: Ordinary English words that collide with a real NBA team abbreviation,
#: found the same way :data:`_COMMON_WORDS_THAT_NAME_PLAYERS` was - measured
#: against the full routing corpus.
#: :func:`_team_named` matches an abbreviation with no length floor of its
#: own (`abbreviation ILIKE ?`), so a bare three-letter word run through it
#: directly can resolve to a team nobody meant: "was" is the Washington
#: Wizards ("What was the highest scoring game by a player this year?"),
#: "min" is the Minnesota Timberwolves (a plausible box-score "20+ min"),
#: and "me" is the Memphis Grizzlies, whose name starts with it ("show kat's
#: average points for me" read Memphis as his own team, and answered that he
#: never played for them - plan item 6, step (d), part 3c, where the day10
#: paraphrases found it once nothing rewrote "kat" to a name the question
#: does not hold). Unlike the player list, this one is checked against the SPAN actually
#: tried rather than the team's own name, because a team's matched span is
#: often not a word of its display name at all (an abbreviation matches
#: nothing in "Washington Wizards" except by lookup).
#:
#: .. versionadded:: 4.4.0
_COMMON_WORDS_THAT_NAME_TEAMS: frozenset[str] = frozenset({"was", "min", "me"})


def _team_grounded(con: duckdb.DuckDBPyConnection, question: str, team: Entity) -> bool:
    """Whether the question shows any trace of ``team`` - a word of its name,
    its abbreviation, or a nickname: the team counterpart of the check a
    player's name is held to (:func:`~association.query.subject.question_supports`)."""
    row = next(((t["abbreviation"], t["display_name"]) for t in _team_index(con, "abbreviation", "display_name", "team_id").rows if t["team_id"] == team.id), None)
    if row is None:
        return True  # nothing to check it against; leave it alone
    asked = {word.casefold() for word in _words(question)}
    carried = {word.casefold() for word in _words(row[1])} | {str(row[0]).casefold()}
    carried |= {nickname for nickname, name in _TEAM_NICKNAMES.items() if name == row[1]}
    # A name written with a space left out is a trace of the team as plainly as
    # its own words are: "trailblazers stats last 10 games" was dropped as a
    # team the question never mentioned, and refused for naming no team at all.
    carried |= _run_together(row[1])
    if carried & asked:
        return True
    # A clipped word is a trace too: "cav vs celtic last 10games" names both
    # teams, and the router's expansion of "cav" to the Cavaliers is its job,
    # not an invention. Three letters, so "la" and "no" ground nothing.
    return any(len(word) >= 3 and any(name.startswith(word) for name in carried) for word in asked)


#: Ordinary English words that also happen to be an NBA player's whole
#: surname - measured against the full routing corpus
#: (the routing check's cases, retired in 5.0.0, plus
#: ``/home/jeff/association-research/statmuse-2026-09/feed_queries.txt``, 380
#: questions) before :data:`~association.query.templates.common.SUBJECT_RESTORABLE_INTENTS`
#: shipped: "Best true shooting percentage last season?" and "Best record
#: from 2010-11 to 2018-19 nba" both named Travis Best, and "Celtics vs Bulls
#: head to head record" named Luther Head - three genuine team/league
#: questions with no player intended at all. The narrowest gate that removes
#: them, per AGENTS.md's own instruction for this exact trap: not a general
#: dictionary (environment-dependent, and this project's other word lists -
#: ``_COUNT_SUBJECT_WORDS``, ``_NAME_STOPWORDS`` - are hand-curated for the
#: same reason), grown from what a corpus measurement actually finds.
#:
#: .. versionadded:: 4.4.0
_COMMON_WORDS_THAT_NAME_PLAYERS: frozenset[str] = frozenset({"best", "head"})


def _named_only_by_a_common_word(question: str, player: str) -> bool:
    """Whether every word of ``player``'s name the question holds is ALSO an
    ordinary English word known to collide with a real surname
    (:data:`_COMMON_WORDS_THAT_NAME_PLAYERS`) - the same shape
    :func:`_named_only_by_a_team_word` checks for a team name, applied to a
    plain word instead of a team's.

    .. versionadded:: 4.4.0
    """
    asked = {w.casefold() for w in _words(question)}
    supporting = [w for w in _words(player) if w.casefold() in asked]
    return bool(supporting) and all(w.casefold() in _COMMON_WORDS_THAT_NAME_PLAYERS for w in supporting)


def _named_only_by_a_team_word(con: duckdb.DuckDBPyConnection, question: str, player: str) -> bool:
    """Whether every word of ``player``'s name the question holds also names a
    team - "magic" in "magic vs nets" is the Orlando Magic, not Magic Johnson,
    and "boston" is the Celtics before it is Brandon Boston Jr."""
    asked = {w.casefold() for w in _words(question)}
    supporting = [w for w in _words(player) if w.casefold() in asked]
    return bool(supporting) and all(_team_named(con, w) is not None for w in supporting)


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


def _shares_word(one: str, other: str) -> bool:
    return bool({w.casefold() for w in _words(one) if len(w) >= 3} & {w.casefold() for w in _words(other) if len(w) >= 3})


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


def clarification(text: str, candidates: list[str], kind: str = "player", active: int = 0) -> str:
    """The "did you mean" sentence for an ambiguous name.

    Lives here rather than in :mod:`association.query.templates` because both
    halves of the query path ask it now: a template returns it as its answer,
    and a chart's rendering entry point returns it as a message. One phrasing,
    so the same ambiguity does not read two ways depending on which path the
    router happened to take.

    Names the first ``MAX_CLARIFY_CANDIDATES`` in the order given and counts
    the rest - except the first ``active``, the candidates who played in the
    season asked about, who are always named. Counting them away is how
    "Curry" hid Stephen: six Currys sorted by name and cut at five named four
    men who never played in the season asked about, plus Seth. A caller with a
    season in hand narrows and orders first (see :func:`resolve_player`) and
    passes ``Ambiguous.active`` on, so the count only ever stands for players
    who could not be the answer. The most one season holds under one name is
    15 Williamses, in 1998 and 1999; 2026 holds 14.

    .. versionadded:: 2.1.0
    """
    shown = candidates[: max(MAX_CLARIFY_CANDIDATES, active)]
    extra = len(candidates) - len(shown)
    rest = "" if extra <= 0 else " (1 other also matches)" if extra == 1 else f" ({extra} others also match)"
    joined = ", ".join(shown[:-1]) + f" or {shown[-1]}" + rest
    return f"{text!r} matches more than one {kind} - did you mean {joined}?"


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

    if _team_named(con, text) is not None:
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
    if not tokens or _team_named(con, text) is not None:
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


def no_match(con: duckdb.DuckDBPyConnection, text: str, kind: str = "player") -> str:
    """The sentence for a name nothing matched, naming near misses when there
    are any.

    The counterpart to :func:`clarification`, and here for the same reason:
    four entry points reach a name that matched nothing, and a person asking
    the same question twice should not get two different sentences depending on
    which one answered it.

    .. versionadded:: 2.1.0
    """
    return suggestion(text, [player.name for player in suggest_players(con, text)], kind)


def suggestion(text: str, candidates: list[str], kind: str = "player") -> str:
    """The sentence itself, given names :func:`suggest_players` already found.

    Split from :func:`no_match` for the one caller that has the candidates in
    hand and would otherwise search for them twice.

    .. versionadded:: 2.1.0
    """
    if not candidates:
        return f"No {kind} found matching {text!r}."
    joined = (", ".join(candidates[:-1]) + " or " if len(candidates) > 1 else "") + candidates[-1]
    return f"No {kind} found matching {text!r} - did you mean {joined}?"


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
   Declared beside :class:`Availability` (``templates.common.GAME_LOGS`` is this).
"""

BOX_SCORES = Availability("player_box_stats")
"""The box scores as an availability: the table a box-score answer rests on.

.. versionadded:: 5.0.0
   Declared beside :class:`Availability` (``templates.common.BOX_SCORES`` is this).
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
    historic = _franchise_in(con, text, season)
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
        nicknamed = _by_nickname(con, text, season) or _run_together_team(con, text, season)
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
    :mod:`association.query.templates.common`, which has no other way to
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
