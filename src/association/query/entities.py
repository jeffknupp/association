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

from .season_text import season_from_text

MAX_CANDIDATES = 10

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
    still exists: by the time the router has filled a slot it has usually
    rewritten the nickname, and when it rewrites one wrongly there is nothing
    downstream to notice - see :func:`override_nicknames`.

    .. versionadded:: 2.1.0
    """
    seen: list[str] = []
    for match in _NICKNAME_RE.finditer(question):
        name = PLAYER_NICKNAMES[match.group(1).casefold()]
        if name not in seen:
            seen.append(name)
    return seen


def override_nicknames(question: str, slots: dict[str, Any]) -> list[tuple[str, str]]:
    """Replace router-supplied player slots with what the question's nicknames
    actually mean. Mutates ``slots``; returns the ``(was, now)`` pairs changed.

    The router rewrites a nickname it recognizes, and *invents* one it does not:
    measured against qwen2.5:3b, "The Answer" became `player='Klay Thompson'`
    and "The Glove" became `player='Jayson Tatum'` - each a real player, each
    resolving cleanly, each producing a confident answer about the wrong person.
    Nothing after the router can catch that, because the nickname is already
    gone. So the question itself is the authority, and this runs before any
    template sees the slots.

    Deliberately conservative, because a wrong override is the same class of bug
    as the one being fixed. A single ``player`` slot is only overridden when the
    question names exactly one nickname; a ``players`` list only when it names
    exactly as many as the list holds, and then positionally, since nothing else
    says which name belongs to which slot. Anything else is left alone.

    .. versionadded:: 2.1.0
    """
    names = [name for name in nicknames_in(question) if name not in _players_other_slots_hold(slots)]
    if not names:
        return []

    changed: list[tuple[str, str]] = []
    players = slots.get("players")
    if isinstance(players, list) and players:
        if len(names) != len(players):
            return []
        for i, (was, now) in enumerate(zip(players, names, strict=True)):
            if was != now:
                players[i] = now
                changed.append((str(was), now))
        return changed

    if len(names) != 1:
        return []
    was = slots.get("player")
    if isinstance(was, str) and was.strip() and was != names[0]:
        slots["player"] = names[0]
        changed.append((was, names[0]))
    return changed


def _players_other_slots_hold(slots: dict[str, Any]) -> set[str]:
    """The players named by every slot but the subject's, as the nickname
    table reads them - "giannis" in ``without`` is Giannis Antetokounmpo.

    A nickname the router has already put somewhere else is spoken for, and
    not the subject: "myles turner bucks stats without giannis last 10" routed
    to ``player='Myles Turner', without=['giannis']``, and the override, seeing
    exactly one nickname in the question, rewrote the subject to Giannis - who
    then could not play without himself. The question named its subject in
    full; the nickname was the teammate.
    """
    held: set[str] = set()
    for key, value in slots.items():
        if key in ("player", "players"):
            continue
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, str):
                held.add(PLAYER_NICKNAMES.get(item.casefold(), item))
    return held


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
            where = " AND ".join(["list_contains(regexp_split_to_array(lower(display_name), '[^a-z]+'), ?)"] * size)
            rows = con.execute(f"SELECT display_name FROM players WHERE {where} LIMIT 2", [w.casefold() for w in span]).fetchall()
            if len(rows) == 1:
                found.append(rows[0][0])
                index += size
                break
        else:
            index += 1
    seen: list[str] = []
    for name in found:
        if name not in seen:
            seen.append(name)
    return seen


def _grounded(con: duckdb.DuckDBPyConnection, question: str, name: str) -> bool:
    """Whether ``question`` shows any trace of ``name`` at all.

    Any one word is enough, because half a name is how a question usually
    carries one ("Jokic", "Luka"), and the router is expected to supply the
    other half. What this catches is a name with no half in the question at
    all.

    Four ways to leave a trace, all of them things the router legitimately does
    to a name: the word itself, a near spelling of it (the user's typo, which
    the router often silently corrects), a nickname, or the initials.
    """
    asked = _words(question)
    lowered = {w.casefold() for w in asked}
    if _initials(name) in lowered or name in nicknames_in(question):
        return True
    for word in _words(name):
        if word.casefold() in lowered:
            return True
        if len(word) < 3:
            continue  # a near spelling of a word this short is a different word
        nearest = con.execute("SELECT list_min(list_transform(?::VARCHAR[], q -> damerau_levenshtein(lower(q), ?)))", [asked, word.casefold()]).fetchone()
        if nearest is not None and nearest[0] is not None and nearest[0] <= _edit_budget(word):
            return True
    return False


def _exact_name_span(con: duckdb.DuckDBPyConnection, span: list[str], limit: int = 2) -> list[Entity]:
    """Players whose ``display_name`` holds every word of ``span`` as a whole
    word - the exact-match building block :func:`players_named_in` and
    :func:`_question_derived_player` both need, so the query is written once.

    ``limit`` bounds the scan; 2 is enough to tell "exactly one" from "more
    than one" without reading out a whole surname's worth of rows.
    """
    where = " AND ".join(["list_contains(regexp_split_to_array(lower(display_name), '[^a-z]+'), ?)"] * len(span))
    rows = con.execute(f"SELECT athlete_id, display_name FROM players WHERE {where} LIMIT {int(limit)}", [w.casefold() for w in span]).fetchall()
    return [Entity(id=str(r[0]), name=r[1]) for r in rows]


def _fuzzy_name_span(con: duckdb.DuckDBPyConnection, span: list[str], limit: int) -> list[Entity]:
    """Players within :func:`_edit_budget` of every word of ``span``, each
    against its nearest word of the name - the near-spelling counterpart of
    :func:`_exact_name_span`, and the same query :func:`suggest_players`
    already runs for its own last pass. The AND across tokens is what keeps a
    short span from matching everybody.
    """
    gaps = ", ".join(f"{_NEAREST_WORD} AS gap{i}" for i in range(len(span)))
    where = " AND ".join(f"gap{i} <= ?" for i in range(len(span)))
    rows = con.execute(
        f"SELECT athlete_id, display_name FROM (SELECT athlete_id, display_name, {gaps} FROM players) WHERE {where} LIMIT {int(limit)}",
        [*span, *(_edit_budget(w) for w in span)],
    ).fetchall()
    return [Entity(id=str(r[0]), name=r[1]) for r in rows]


# players_named_in's own cap: a name is never longer than three words once
# _words has split a hyphenated one into halves.
_SPAN_MAX_WORDS = 3


def _anchor_word_position(con: duckdb.DuckDBPyConnection, q_words: list[str], lowered_q: list[str], word: str) -> int | None:
    """Where ``word`` (one word of the router's name) turns up in the
    question - exactly, or the nearest near spelling within
    :func:`_edit_budget` - or ``None`` when it turns up nowhere at all."""
    exact = next((i for i, w in enumerate(lowered_q) if w == word.casefold()), None)
    if exact is not None:
        return exact
    if len(word) < 3:
        return None  # a near spelling of a word this short is a different word
    distances = con.execute(
        "SELECT list_transform(?::VARCHAR[], q -> damerau_levenshtein(lower(q), ?))",
        [q_words, word.casefold()],
    ).fetchone()
    row = distances[0] if distances else None
    if row is None:
        return None
    budget = _edit_budget(word)
    near = [i for i, d in enumerate(row) if d is not None and d <= budget and len(lowered_q[i]) >= 3]
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

    Two faults this repairs, both structural, and both invisible to
    :func:`_grounded`'s "any one word is enough" check because that check is
    deliberately generous: the router TRUNCATES a name the question spells in
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
      else in the question corroborating it is left alone here, for
      :func:`_grounded` to judge as it always has.

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
    positions = [_anchor_word_position(con, q_words, lowered_q, word) for word in name_words]
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


def override_invented_players(con: duckdb.DuckDBPyConnection, question: str, slots: dict[str, Any]) -> tuple[list[tuple[str, str]], list[str]]:
    """Replace router-supplied player names the question does not support, or
    report the ones that cannot be replaced. Mutates ``slots``.

    :func:`override_nicknames` fixes a nickname the router rewrote wrongly.
    This is the same failure without the nickname: asked to "compare sga and
    embiid" the 3B router emitted ``['Shai Gilgeous-Alexander', 'Jusuf
    Nurkic']`` and the answer was a fluent, correct-looking table of two real
    players, one of whom the question never mentioned. Nothing downstream could
    notice - "Jusuf Nurkic" resolves perfectly.

    So every name is checked against the question before a template reads it.
    First against the question's OWN span (:func:`_question_derived_player`),
    which repairs a name confidently even where it is already "grounded" by
    the looser check below - a truncated or partly-fabricated name the router
    produced. Second, for whatever that leaves untouched, against the
    generous word-or-nickname-or-initials check that is :func:`_grounded`: a
    name with no trace there at all is not answered about. Where the question
    names somebody nothing else claims, that player takes its place; where it
    does not, the name is reported and the caller says so rather than handing
    the question to the agent: measured, the agent filled that silence with a
    player who does not exist. Guessing is not on the list.

    Returns:
        The ``(was, now)`` pairs replaced, and the ungrounded names that could
        not be replaced. A non-empty second element means the slots are not
        safe to answer from, even though the first may also be non-empty.

    .. versionchanged:: 4.3.0
       Tries :func:`_question_derived_player` first, so a name the question's
       own words resolve confidently is corrected even when it was already
       "grounded" by the older, looser check - the shape that let a truncated
       "dennis schröder" (typed correctly, arrived as ``'Dennis'``) stand as a
       7-way clarification the question never should have asked.
    """
    slot = "players" if isinstance(slots.get("players"), list) else "player"
    raw_list, is_list = _override_invented_players_slot(slots, slot)
    positions = [i for i, v in enumerate(raw_list) if isinstance(v, str) and v.strip()]
    if not positions:
        return [], []

    changed, still_ungrounded = _override_invented_players_derive(con, question, slots, slot, raw_list, is_list, positions)
    if not still_ungrounded:
        return changed, []

    # Only the names nothing already accounts for are available as
    # replacements: "compare sga and embiid" names two players, and one of
    # them is the slot that came through fine (or was already repaired above).
    kept = [raw_list[i] for i in positions if i not in still_ungrounded]
    ungrounded_names = [raw_list[i] for i in still_ungrounded]
    spare = [name for name in players_named_in(con, question) if not any(_shares_word(name, k) for k in kept)]
    if len(spare) != len(ungrounded_names):
        return changed, ungrounded_names

    replacement = dict(zip(ungrounded_names, spare, strict=True))
    for i in still_ungrounded:
        was = raw_list[i]
        _override_invented_players_write(slots, slot, raw_list, is_list, i, replacement[was])
        changed.append((was, replacement[was]))
    return changed, []


def _override_invented_players_slot(slots: dict[str, Any], slot: str) -> tuple[list[Any], bool]:
    """The list :func:`override_invented_players` mutates, and whether it is
    the real ``slots["players"]`` list rather than a throwaway wrapper around
    the single ``slots["player"]`` value - what
    :func:`_override_invented_players_write` needs to know before it can
    write a repair back."""
    values = slots.get(slot)
    if slot == "players" and isinstance(values, list):
        return values, True
    return [values], False


def _override_invented_players_write(slots: dict[str, Any], slot: str, raw_list: list[Any], is_list: bool, i: int, new_value: str) -> None:
    """Write ``new_value`` at position ``i`` of ``raw_list``, and back into
    ``slots`` too when ``raw_list`` is only a throwaway wrapper around
    ``slots[slot]`` rather than that same list object."""
    raw_list[i] = new_value
    if not is_list:
        slots[slot] = new_value


def _override_invented_players_derive(
    con: duckdb.DuckDBPyConnection, question: str, slots: dict[str, Any], slot: str, raw_list: list[Any], is_list: bool, positions: list[int]
) -> tuple[list[tuple[str, str]], list[int]]:
    """The first pass over every named position: repair it from the
    question's own span where :func:`_question_derived_player` resolves that
    confidently, and collect whatever is left that :func:`_grounded` still
    calls ungrounded."""
    changed: list[tuple[str, str]] = []
    still_ungrounded: list[int] = []
    for i in positions:
        name = raw_list[i]
        derived = _question_derived_player(con, question, name)
        if derived is not None and derived.name.casefold() != name.strip().casefold():
            changed.append((name, derived.name))
            _override_invented_players_write(slots, slot, raw_list, is_list, i, derived.name)
        elif not _grounded(con, question, name):
            still_ungrounded.append(i)
    return changed, still_ungrounded


def restore_dropped_players(con: duckdb.DuckDBPyConnection, question: str, slots: dict[str, Any]) -> tuple[str, str] | None:
    """Put back players a fingerprint's slots lost. Mutates ``slots``.

    A fingerprint draws as many polygons as it is given, so a question naming
    two players and a slot holding one is not a narrower question - it is half
    of the one asked, answered without saying so. Measured: "compare
    fingerprints for embiid vs jokic in 2026" came back as a single
    ``player`` slot, and "generate fingerprints for embiid vs jolic in 2026"
    rendered Joel Embiid alone with the second name simply gone.

    Gated on the question saying it compares something at all, which is what
    keeps players_named_in's few false positives out of a radar: "plot jokic's
    fingerprint from his best season" names Travis Best by its rules, and
    without the gate would have drawn him a polygon. The cost is a question
    that compares without saying so - "plot jokic and embiid fingerprints" -
    still losing the second name, which is what it did before this existed.

    Only for the fingerprint intent, and only in this direction. Two polygons
    on shared axes IS the comparison there, whereas turning a ``player_stat``
    question into a comparison because the question mentioned somebody else
    would be answering a different question.

    Returns:
        The ``(was, now)`` pair, or None when the slots already carry every
        player the question names.

    .. versionadded:: 2.1.0
    .. versionchanged:: 4.4.0
       Compares NAMES rather than counts. "show a fingerprint for maxey vs
       jaylen brown in 2026" held one name (``"Maxey"``) and
       :func:`players_named_in` found one too (``"Jaylen Brown"``) - equal
       counts read as nothing to restore, while the two names are different
       people. "Maxey" alone is ambiguous - ``players`` holds both Tyrese
       Maxey and Marlon Maxey - so :func:`players_named_in`'s own strictness
       (a span counts only when it names EXACTLY one player) drops it, and
       the surviving count no longer means what the count comparison
       assumed. Now a held name is kept only when :func:`_grounded` finds a
       trace of it in the question at all - the same test
       :func:`override_invented_players` uses for a router invention - and
       whatever :func:`players_named_in` finds that shares no word with a
       kept name is added rather than used to replace the whole list, so an
       already-correct name is never swapped for a mere spelling of itself.
    """
    if not _COMPARISON.search(question):
        return None
    named = players_named_in(con, question)
    listed = slots.get("players")
    raw = listed if isinstance(listed, list) else [slots.get("player")]
    held = [name for name in raw if isinstance(name, str) and name.strip()]
    # A held name the question shows no trace of at all is the router's own
    # invention (the same shape override_invented_players guards against) and
    # is dropped rather than kept; `named`, read straight from the question,
    # can replace it outright. A held name WITH a trace is kept, even where
    # players_named_in itself could not confirm it (an ambiguous bare
    # surname), so restoring one dropped player never costs another his
    # already-correct slot.
    kept = [name for name in held if _grounded(con, question, name)]
    spare = [name for name in named if not any(_shares_word(name, k) for k in kept)]
    restored = kept + spare
    if not restored or restored == held:
        return None
    slots["players"] = restored
    slots.pop("player", None)
    return " and ".join(held) or "nobody", " and ".join(restored)


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
    spelling, the leftover name is resolved against the roster the same way
    :func:`restore_dropped_players` itself resolves one - and where it
    resolves, the sentence says the player was dropped, never that he is
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
        return f"Note: the question also names {' and '.join(dropped)}, who was not included in this answer."
    return "Note: the question compares two players, but only one of them matches anybody in the warehouse - check the spelling of the other."


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


def undo_name_completion(con: duckdb.DuckDBPyConnection, question: str, slots: dict[str, Any]) -> list[tuple[str, str]]:
    """Give back the ambiguity the router resolved on its own. Mutates ``slots``.

    "Who is better, tatum or brown" routed to ``['Jayson Tatum', 'Jaylen
    Brown']``. Tatum is one player and that completion is free; "brown" is ten,
    and the router choosing Jaylen is exactly the prominence tiebreak measured
    and rejected above :data:`PLAYER_NICKNAMES` - arriving through the model's
    guess instead of through code, where nothing downstream can see it. Which
    Brown is meant is for :func:`resolve_player` to settle - by who still
    plays, said in the answer, or by asking when more than one does - and it
    never got the chance once the router started completing the name.

    So a name the question carries only PART of is cut back to that part, and
    normal resolution decides: ``find_players`` applies the nickname table
    first, so "luka" still answers Luka Doncic, and :func:`resolve_player`
    returns :class:`Ambiguous` for "brown", which is the question being asked.

    Only where the part is ambiguous, which is what keeps this from undoing the
    router's useful work. Completing "jokic", "embiid" or "wembanyama" changes
    no answer, and a name the question spells in full is not a part at all.
    Measured over the ``check_routing.py`` corpus, no slot moves.

    A name :func:`_question_derived_player` already resolved is the same kind
    of settled case as a nickname, for the same reason: "Dylon harper" typos
    the given name, and matching ``asked`` exactly (this function's own job,
    everywhere else) reads the CORRECTED "Dylan" as absent from the question
    - not typo'd, invented - and would cut a right answer back to the
    ambiguous "Harper" over a misspelling nobody asked to have undone.

    Deliberately not extended to trust a bare fragment's own exact
    uniqueness the way :func:`_question_derived_player` does: "Kon" and
    "Gui" are each, by themselves, an exact and globally unique match
    ("Kon Knueppel", "Gui Santos") the same way "Kareem" is ("Kareem Rush") -
    and "Kareem" is a coincidence, not an answer, measured directly against
    this function (`test_two_anchored_words_that_fail_together_do_not_fall_back_to_one`'s
    shape, reached through here instead of :func:`_question_derived_player`,
    named Kareem Rush for "Kareem Abdul-Jabbar" before this line existed).
    Nothing here can tell a real player's mangled surname from a fabricated
    one apart from the position check :func:`_question_derived_player`
    already applies, so a bare matched fragment is left exactly as
    ambiguous as :func:`find_players` says it is.

    Returns:
        The ``(was, now)`` pairs cut back, for the trace.

    .. versionchanged:: 4.3.0
       Also leaves alone a name :func:`_question_derived_player` resolves to
       from the question's own span - see above.
    """
    asked = {word.casefold() for word in _words(question)}
    nicknamed = nicknames_in(question)

    def part_only(name: str) -> str | None:
        """The part of ``name`` the question carries, when that part is all it
        carries and it reaches more than one player."""
        words = _words(name)
        matched = [word for word in words if word.casefold() in asked]
        # A nickname the question actually used is a resolution the curated
        # table made, not one the router guessed: "steph curry" is Stephen.
        if not matched or len(matched) == len(words) or name in nicknamed:
            return None
        derived = _question_derived_player(con, question, name)
        if derived is not None and derived.name.casefold() == name.casefold():
            return None
        fragment = " ".join(matched)
        return fragment if len(find_players(con, fragment)) > 1 else None

    changed: list[tuple[str, str]] = []
    listed = slots.get("players")
    if isinstance(listed, list):
        for index, value in enumerate(listed):
            fragment = part_only(value) if isinstance(value, str) else None
            if fragment is not None:
                listed[index] = fragment
                changed.append((str(value), fragment))
        return changed
    value = slots.get("player")
    fragment = part_only(value) if isinstance(value, str) else None
    if fragment is not None:
        slots["player"] = fragment
        changed.append((value if isinstance(value, str) else "", fragment))
    return changed


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
        rows = dict(con.execute("SELECT CAST(team_id AS VARCHAR), display_name FROM teams").fetchall())
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
    :func:`scope_from_question`.
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
            rows = con.execute("SELECT team_id, display_name FROM teams WHERE name ILIKE ? OR name ILIKE ?", [nickname, f"% {nickname}"]).fetchall()
            named = [Entity(id=str(r[0]), name=r[1]) for r in rows]
        if len(named) != 1:
            continue
        in_city = _franchise_in(con, city, season)
        if in_city is None:
            rows = con.execute("SELECT team_id FROM teams WHERE location ILIKE ?", [city]).fetchall()
            in_city = [Entity(id=str(r[0]), name="") for r in rows]
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
        rows = con.execute("SELECT team_id, display_name FROM teams").fetchall()
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
        rows = con.execute(
            "SELECT team_id, display_name FROM teams WHERE team_id = ? OR abbreviation ILIKE ? OR display_name ILIKE ? OR display_name ILIKE ? LIMIT 2",
            [text, text, f"{text}%", f"% {text}%"],
        ).fetchall()
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


def _team_after_for(con: duckdb.DuckDBPyConnection, question: str, season: int | None = None) -> Entity | None:
    """The player's OWN team a question names with "for"/"with the"
    ("lebron stats as a starter for Miami", yardstick-v2 F166), or None.
    Spans of three words down to one are tried, the same as
    :func:`_team_after_versus`.

    .. versionadded:: 4.4.0
    """
    for match in _FOR_TEAM.finditer(question):
        span_text = match.group(1) or match.group(2) or ""
        words = _words(span_text)[:3]
        for size in (3, 2, 1):
            if size <= len(words) and len(" ".join(words[:size])) >= 2:
                team = _team_named(con, " ".join(words[:size]), season)
                if team is not None:
                    return team
    return None


def _team_grounded(con: duckdb.DuckDBPyConnection, question: str, team: Entity) -> bool:
    """Whether the question shows any trace of ``team`` - a word of its name,
    its abbreviation, or a nickname. The team counterpart of :func:`_grounded`."""
    row = con.execute("SELECT abbreviation, display_name FROM teams WHERE team_id = ?", [team.id]).fetchone()
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
#: (``scripts/check_routing.py``'s cases plus
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


def _scope_from_question_only_player(con: duckdb.DuckDBPyConnection, question: str) -> str | None:
    """The one player the question names, or None if it names none or several.
    A name held only by a word that names a team the question is about does
    not count: "luka dončić last 15 games vs. magic" names Luka, not Luka and
    Magic Johnson. Nor does a name held only by an ordinary English word that
    happens to collide with a surname - "best"/"head" - count either.

    .. versionchanged:: 4.4.0
       Also excludes a name held only by :data:`_COMMON_WORDS_THAT_NAME_PLAYERS`.
    """
    named = [name for name in players_named_in(con, question) if not _named_only_by_a_team_word(con, question, name) and not _named_only_by_a_common_word(question, name)]
    return named[0] if len(named) == 1 else None


def _scope_from_question_player_in_team_slot(con: duckdb.DuckDBPyConnection, question: str, slots: dict[str, Any], notes: list[str]) -> bool:
    """Move a player's name out of ``team`` and into ``player``. Returns whether it did.

    Two ways a player ends up there. Measured: "Podziemski game log without
    curry" arrived as team='Podziemski', player='Curry' - the subject in the
    team slot and the absent teammate in the player slot - which
    :func:`find_players` settles outright, since "Podziemski" alone names one
    person.

    The other is a router that kept only a bare fragment of a name the
    question spells in full: "Will Riley last 5 game s" arrived as
    team='Riley', and "Riley" alone is three players - Eric, Minix and Will -
    so :func:`find_players` cannot settle it. :func:`players_named_in` can,
    the same way it settles a dropped or invented player elsewhere in this
    module: a whole name, not a guess. Gated on the fragment actually
    sharing a word with what it finds, so a garbled `team` next to some
    OTHER player named later in a long question cannot borrow that name -
    the same discipline :func:`override_invented_players` applies to a name
    the router invented outright.
    """
    team_text = slots.get("team")
    if not (isinstance(team_text, str) and team_text.strip()):
        return False
    as_player = find_players(con, team_text)
    held, without = slots.get("player"), teammate_names(slots.get("without"))
    name: str | None = None
    if len(as_player) == 1 and (not held or (isinstance(held, str) and any(held.casefold() == n.casefold() for n in without))):
        name = as_player[0].name
    elif not held:
        found = _scope_from_question_only_player(con, question)
        if found is not None and _shares_word(found, team_text):
            name = found
    if name is None:
        return False
    slots.pop("team", None)
    slots["player"] = name
    notes.append(f"{team_text!r} is a player, not a team; the subject is {name!r}")
    return True


def _scope_from_question_displaced_player(con: duckdb.DuckDBPyConnection, question: str, slots: dict[str, Any], slot_notes: list[str], team: Entity, versus: Entity | None) -> Entity | None:
    """Put back the player a team in ``team`` displaced, and put the team
    where it belongs. Returns the team still in the slot, or None once it has gone.

    The team in ``team`` is the opponent (the team after "vs"), or a team the
    question never mentioned (the router's guess at the player's own). Either
    way the player it displaced is the subject, and:

    - the opponent stays an opponent. "karl towns stats vs netslast 5 games"
      arrived as ``team='Brooklyn Nets'`` - the router's reading of a garbled
      "vs nets" - and dropping the team with the player restored answered his
      last five games against anybody, with nothing saying the Nets had gone.
    - a word that names a team the question is about is not a player. "magic
      vs nets last 10" named Magic Johnson by its one word "magic", and the
      Magic's log became his. With nobody named, "X vs Y" is a team's log
      against another, and the two are put back in order.
    - a team the question never names goes even when nobody was found:
      "stating centers vs phoenix suns log" arrived as the Lakers, whose log
      it then was. A subject the question does not name is for the template
      to refuse, not for the router's guess to supply.
    """
    displaced = versus is not None and team.id == versus.id
    if not displaced and _team_grounded(con, question, team):
        return team
    player = _scope_from_question_only_player(con, question)
    if player is not None and _named_only_by_a_team_word(con, question, player):
        player = None
    slots.pop("team", None)
    if player is not None:
        slots["player"] = player
        slot_notes.append(f"{team.name!r} was {'the opponent' if displaced else 'not in the question'}; the subject is {player!r}")
        if displaced or (versus is None and "opponent" not in slots and _AGAINST.search(question)):
            # The team the question plays against - resolved from the text,
            # or the router's reading of a word nothing here resolves.
            slots["opponent"] = team.name
        return None
    before = _team_named(con, slots.get("opponent")) if displaced else None
    if before is not None and before.id != team.id and _team_grounded(con, question, before):
        # "magic vs nets": the router filed the sides backwards. The team
        # before "vs" is the subject, the one after it the opponent.
        slots["team"], slots["opponent"] = before.name, team.name
        slot_notes.append(f"{before.name!r} is the subject and {team.name!r} the opponent, as the question orders them")
        return before
    if displaced:
        slots.setdefault("opponent", team.name)
        slot_notes.append(f"{team.name!r} is the team the question plays against; the question names no subject")
    else:
        slot_notes.append(f"{team.name!r} is not in the question, and the question names no player; dropped")
    return None


def _named_only_by_a_team_word(con: duckdb.DuckDBPyConnection, question: str, player: str) -> bool:
    """Whether every word of ``player``'s name the question holds also names a
    team - "magic" in "magic vs nets" is the Orlando Magic, not Magic Johnson,
    and "boston" is the Celtics before it is Brandon Boston Jr."""
    asked = {w.casefold() for w in _words(question)}
    supporting = [w for w in _words(player) if w.casefold() in asked]
    return bool(supporting) and all(_team_named(con, w) is not None for w in supporting)


def _scope_from_question_team_in_players(con: duckdb.DuckDBPyConnection, slots: dict[str, Any], notes: list[str], versus: Entity | None, season: int | None) -> None:
    """Take the team the question plays against out of ``players``."""
    listed = slots.get("players")
    if versus is not None and isinstance(listed, list):
        kept = [name for name in listed if not ((found := _team_named(con, name, season)) is not None and found.id == versus.id)]
        if len(kept) != len(listed):
            notes.append(f"{versus.name!r} is a team, not a player to compare")
            if len(kept) == 1:
                slots.pop("players", None)
                slots["player"] = kept[0]
            else:
                slots["players"] = kept


def _scope_from_question_restore_player(con: duckdb.DuckDBPyConnection, question: str, slots: dict[str, Any], notes: list[str]) -> None:
    """Put back the one player the question names, where the router left the player out."""
    player = _scope_from_question_only_player(con, question)
    if player is not None:
        slots["player"] = player
        notes.append(f"player {player!r} (from the question; the router left it out)")


def _scope_from_question_own_team(con: duckdb.DuckDBPyConnection, question: str, slots: dict[str, Any], notes: list[str]) -> None:
    """Put back a player's OWN team, where "for <team>"/"with the <team>"
    names one and the router filed neither a ``team`` nor an ``opponent``
    slot at all - yardstick-v2 F166, "lebron stats as a starter for Miami".

    Written to ``own_team``, never ``team`` - a router-supplied ``team``
    beside an already-correct ``opponent`` is documented noise elsewhere in
    this module (:func:`_team_slot_for_player` in ``templates/games.py``:
    "Phoenix Suns" beside a real "Philadelphia 76ers" opponent, "Los Angeles
    Lakers" beside a real "Houston Rockets" one), and a template that read
    ``slots["team"]`` directly here would occasionally trust that same noise
    - measured on the step 3 golden set, "lebron james 2 3 pointers all-time
    vs jazz on tuesdays" carries a recorded ``team='Los Angeles Lakers'``
    (his own, current, and entirely redundant beside a real
    ``opponent='Utah Jazz'``) and silently narrowed 9 real meetings down to 4
    before ``own_team`` existed. ``own_team`` is a name only this function
    and :data:`~association.query.templates.common.OWN_TEAM_RESTORABLE_INTENTS`'
    one caller (``player_stat``) ever read or write, so nothing else can hand
    it noise.

    A historical team names a TENURE, not "now": with no season NAMED IN
    THE QUESTION - read with :func:`~association.query.season_text.season_from_text`,
    not ``slots["season"]``, since that slot already carries the router's own
    "current season" default (``season_ref: "current"``) on a question that
    named no year at all, and trusting it would read every "for <team>"
    question as though "this season" had been said - ``span`` becomes
    "career" too, the same reading
    :data:`~association.query.router._SPAN_JOINED_LEAGUE_WORDS` gives "since
    he joined the league", for the same reason ("for Miami" 15 years into a
    Lakers career is not asking about this season). A season the question
    DID name still wins, exactly as every other span reading here does.

    .. versionadded:: 4.4.0
    """
    if slots.get("team") or slots.get("opponent") or slots.get("own_team"):
        return
    named_season = season_from_text(question)
    team = _team_after_for(con, question, named_season)
    if team is None:
        return
    slots["own_team"] = team.name
    notes.append(f"own_team {team.name!r} (from the question; the router left it out)")
    if named_season is None and not slots.get("span"):
        slots["span"] = "career"
        slots.pop("season", None)
        notes.append("span 'career' (a team named with no season is a tenure, not \"now\")")


def _scope_from_question_drop_opposing_team(slots: dict[str, Any], notes: list[str], team: Entity) -> None:
    """Drop the team the question plays against from ``team``, where it sits beside a player."""
    # The team the question plays AGAINST, filed as the subject's team
    # beside a player the router kept. Left there, nothing reads it: the
    # check below leaves a team already in `team` alone, which is how
    # head_to_head carries its own side, so no opponent was set and
    # check_scope had nothing to refuse. Measured: "compare curry and
    # lebron vs the celtics" came back with team='Boston Celtics'.
    slots.pop("team", None)
    notes.append(f"{team.name!r} is the team the question plays against, not the subject's")


def _scope_from_question_unheld_opponent(con: duckdb.DuckDBPyConnection, slots: dict[str, Any], notes: list[str], versus: Entity, season: int | None, *, carries_player: bool) -> None:
    """Fill an empty ``opponent`` with the team the question plays against, unless the slots already carry that team as a side."""
    listed_teams = [name for name in slots.get("teams") or [] if isinstance(name, str)]
    if carries_player and any((found := _team_named(con, name, season)) is not None and found.id == versus.id for name in listed_teams):
        # A player is the subject, so the team after "vs" is his opponent even
        # when the router filed it in `teams`. The "already carried" rule
        # below exists for head_to_head, which reads `teams` as its two sides;
        # player_stat and game_log never read that slot, so leaving it there
        # answers every opponent. "Keyonte George against blazers" arrived as
        # teams=["Portland Blazers"] and was answered correctly only while
        # that name failed to resolve - resolving it answered his whole
        # 54-game season instead of his 2 games against Portland.
        kept = [name for name in listed_teams if not ((found := _team_named(con, name, season)) is not None and found.id == versus.id)]
        if kept:
            slots["teams"] = kept
        else:
            slots.pop("teams", None)
        slots["opponent"] = versus.name
        notes.append(f"opponent {versus.name!r} (the question plays against it; the router filed it as a team)")
        return
    carried = [slots.get("team"), *(slots.get("teams") or [])]
    if not any((found := _team_named(con, name, season)) is not None and found.id == versus.id for name in carried):
        slots["opponent"] = versus.name
        notes.append(f"opponent {versus.name!r} (from the question)")


def _scope_from_question_opponent(con: duckdb.DuckDBPyConnection, question: str, slots: dict[str, Any], notes: list[str], versus: Entity | None, season: int | None, *, carries_player: bool) -> None:
    """Set ``opponent`` to the team the question plays against, where the slots do not already carry it.
    ``carries_player`` is whether a player-reading template has a player as its subject."""
    held = slots.get("opponent")
    held_team = _team_named(con, held, season) if held else None
    if versus is not None and held and (held_team is None or (held_team.id != versus.id and not _team_grounded(con, question, held_team))):
        # The router filled `opponent`, and what it wrote is either no team at
        # all or a team the question never mentions - while the question names
        # one outright. The question wins. This used to fill the slot only when
        # it was EMPTY, so an unresolvable string beat a team the question
        # named: "duren v nets 1h gameloh" arrived as opponent="New Jersey
        # Nets", this read "nets" as the Brooklyn Nets correctly, and threw it
        # away. Same rule as override_invented_players, for the other entity.
        slots["opponent"] = versus.name
        notes.append(f"opponent {held!r} -> {versus.name!r} (the question names it; the router's {'resolves to no team' if held_team is None else 'is not in the question'})")
    elif versus is not None and not held:
        _scope_from_question_unheld_opponent(con, slots, notes, versus, season, carries_player=carries_player)


def scope_from_question(
    con: duckdb.DuckDBPyConnection, question: str, slots: dict[str, Any], *, reads_player: bool, needs_player: bool = False, restore_subject: bool = False, restore_team: bool = False
) -> list[str]:
    """Put a team the question plays AGAINST where a template will see it.
    Mutates ``slots``; returns a line per change, for the trace.

    The router has an ``opponent`` slot but is only ever taught it for team
    quarter scoring, so on every other shape the opposing team either vanishes
    or lands in the wrong slot. Measured against real StatMuse queries, the
    single most common shape there is (a third of the feed):

    - "jaylen brown last 8 games vs pistons" routed to ``game_log`` with
      ``team='Boston Celtics'`` - his team, which the question never names -
      and no player. The answer was the Celtics' last eight games.
    - "Luka Doncic game log vs Lakers" put the Lakers in ``team`` and dropped
      Luka: the Lakers' log.
    - "how did curry do against the celtics" put the Celtics in ``players``,
      where "boston" resolves to Brandon Boston Jr.

    So the question is read for the team after "vs"/"against", and each of
    those is undone. The resulting ``opponent`` is a scoping slot: a template
    that cannot restrict to one refuses it (templates.check_scope) instead of
    answering about every opponent. A team the slots already carry - both
    sides of ``head_to_head``, the opponent ``team_quarter_points`` was given -
    is left exactly as it was, with one exception: the team the question plays
    against, filed in ``team`` beside a player a ``reads_player`` template
    reads, is that player's opponent and moves there.

    Restoring a player follows :func:`override_invented_players`' discipline:
    only when the question names exactly one player, and only for a template
    that reads one (``reads_player``). A player the router simply left out is
    restored only where the template cannot answer without one
    (``needs_player``): "Sga record 36 plus points" came back with no player
    at all, and an optional player slot left empty means "the league".

    ``restore_subject`` is the same restore for a template where an empty
    slot has a real, different answer (the league) rather than none at all -
    :data:`~association.query.templates.common.SUBJECT_RESTORABLE_INTENTS`.
    "kawhi most threes in a game" used to answer the league's single-game
    3PM leaders, Kawhi Leonard's own 7 never mentioned, because no grammar in
    ``router.py`` covers "NAME most/highest STAT" (yardstick-v2 F093). Kept
    apart from ``needs_player`` because the two read differently even though
    they call the same restore: ``needs_player`` means the template errors
    without a name, ``restore_subject`` means it would answer something else
    entirely correct and unmarked as narrower than it looks.

    ``restore_team`` puts back the player's OWN team, into ``own_team`` -
    never ``team``, which a router-supplied noise value already sits in
    often enough to make trusting it directly unsafe (see
    :func:`_scope_from_question_own_team`'s own docstring) - where "for
    <team>"/"with the <team>" names one beside him and the router left both
    ``team`` and ``opponent`` empty (yardstick-v2 F166,
    :data:`~association.query.templates.common.OWN_TEAM_RESTORABLE_INTENTS`).
    Requires a player already in the slots - a bare "for <team>" with no
    player is a team question, not this one.

    .. versionadded:: 2.1.0

    .. versionchanged:: 4.4.0
       Takes ``restore_subject``.

    .. versionchanged:: 4.4.0
       Takes ``restore_team``.
    """
    notes: list[str] = []
    season = slots.get("season") if isinstance(slots.get("season"), int) else None
    versus = _team_after_versus(con, question, season)
    has_player = bool(slots.get("player")) or bool(slots.get("players"))
    team = _team_named(con, slots.get("team"), season)

    if reads_player and team is None:
        has_player = _scope_from_question_player_in_team_slot(con, question, slots, notes) or has_player

    if team is not None and not has_player and reads_player:
        team = _scope_from_question_displaced_player(con, question, slots, notes, team, versus)

    _scope_from_question_team_in_players(con, slots, notes, versus, season)

    if (needs_player or restore_subject) and not slots.get("player") and not slots.get("players"):
        _scope_from_question_restore_player(con, question, slots, notes)

    if reads_player and has_player and team is not None and versus is not None and team.id == versus.id and not slots.get("opponent"):
        _scope_from_question_drop_opposing_team(slots, notes, team)

    _scope_from_question_opponent(con, question, slots, notes, versus, season, carries_player=reads_player and has_player)
    _scope_from_question_opponent_is_a_subject(con, slots, notes)
    _scope_from_question_own_team_if_asked(con, question, slots, notes, restore_team=restore_team, has_player=has_player)
    return notes


def _scope_from_question_own_team_if_asked(con: duckdb.DuckDBPyConnection, question: str, slots: dict[str, Any], notes: list[str], *, restore_team: bool, has_player: bool) -> None:
    """The last step of :func:`scope_from_question` - split out to keep that
    function's own branch count under the complexity gate. Requires a player
    already in the slots (``has_player``, or one a later step of
    ``scope_from_question`` itself just wrote): a bare "for <team>" with no
    player is a team question, not this one."""
    if restore_team and (has_player or slots.get("player")):
        _scope_from_question_own_team(con, question, slots, notes)


def _scope_from_question_opponent_is_a_subject(con: duckdb.DuckDBPyConnection, slots: dict[str, Any], notes: list[str]) -> None:
    """Drop an ``opponent`` that names a player the slots already ask about.

    "sga vs tyrese maxey fingerprint" put Maxey in ``opponent``;
    :func:`restore_dropped_players` then correctly rebuilt the pair, and
    ``check_scope`` refused the leftover slot, so a question the system
    answers under other words ("compare sga and tyrese maxey fingerprint")
    had no answer at all.

    Narrow on purpose. The slot goes only when it names no team AND the
    person it names is already a subject - the router filing one of the
    question's own players twice, which narrows nothing. An ``opponent`` that
    names a player the slots do NOT carry is left exactly where it is, so a
    template that cannot honor it still refuses rather than silently widening
    to every opponent, which is the trade this module exists to make.
    """
    held = slots.get("opponent")
    if not (isinstance(held, str) and held.strip()) or _team_named(con, held) is not None:
        return
    subjects = [name for name in [slots.get("player"), *(slots.get("players") or [])] if isinstance(name, str) and name.strip()]
    if any(_shares_word(subject, held) for subject in subjects):
        slots.pop("opponent", None)
        notes.append(f"opponent {held!r} is a player the question already asks about, not a team")


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


# The distance from one query token to the NEAREST word of a display name,
# split on non-letters so the halves of "Gilgeous-Alexander" are two words.
_NEAREST_WORD = "list_min(list_transform(regexp_split_to_array(display_name, '[^A-Za-z]+'), w -> damerau_levenshtein(lower(w), lower(?))))"


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

    if len(tokens) > 1 and len(tokens[-1]) > 2:
        # Word-boundary matches only. find_players falls back to incidental
        # substring hits when nothing starts with the token, which is fine for
        # a name somebody typed and wrong for one being guessed at: backing
        # "Nobody At All" off to "All" otherwise suggests Bo Wall.
        start = re.compile(_WORD_START + re.escape(tokens[-1]), re.IGNORECASE)
        kept = [player for player in find_players(con, tokens[-1]) if start.search(player.name)]
        if kept and len(kept) <= MAX_CLARIFY_CANDIDATES:
            return kept
        # Otherwise more than MAX_CLARIFY_CANDIDATES share the surname alone,
        # or none do - either way this pass answers nothing on its own, and
        # falls through to the near-spelling pass below rather than giving up.
        # That pass is stricter (every token has to be close, not just the
        # last one), which is exactly what a common surname needs: "Dylon
        # Harper" backs off to 6 Harpers here - too many to suggest - but only
        # one of them, Dylan, is also close on the given name.

    gaps = ", ".join(f"{_NEAREST_WORD} AS gap{i}" for i in range(len(tokens)))
    where = " AND ".join(f"gap{i} <= ?" for i in range(len(tokens)))
    order = " + ".join(f"gap{i}" for i in range(len(tokens)))
    rows = con.execute(
        f"SELECT athlete_id, display_name FROM (SELECT athlete_id, display_name, {gaps} FROM players) WHERE {where} ORDER BY {order}, display_name LIMIT {MAX_CLARIFY_CANDIDATES + 1}",
        [*tokens, *(_edit_budget(t) for t in tokens)],
    ).fetchall()
    return [] if len(rows) > MAX_CLARIFY_CANDIDATES else [Entity(id=str(r[0]), name=r[1]) for r in rows]


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
    where = " AND ".join(["display_name ILIKE ?"] * len(tokens))
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
    strong = " AND ".join(["regexp_matches(display_name, ?, 'i')"] * len(tokens))
    bound = "" if limit is None else f" LIMIT {int(limit)}"
    rows = con.execute(
        f"SELECT athlete_id, display_name, ({strong}) AS strong FROM players WHERE {where} ORDER BY strong DESC, display_name{bound}",
        [_WORD_START + re.escape(t) for t in tokens] + [f"%{t}%" for t in tokens],
    ).fetchall()
    matched = [row for row in rows if row[2]] or rows
    return [Entity(id=str(r[0]), name=r[1]) for r in matched]


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
    rows = con.execute(
        "SELECT team_id, display_name, "
        "  CASE WHEN team_id = ? OR abbreviation ILIKE ? THEN 2 "
        "       WHEN display_name ILIKE ? OR display_name ILIKE ? THEN 1 ELSE 0 END AS rank "
        "FROM teams WHERE team_id = ? OR abbreviation ILIKE ? OR display_name ILIKE ? "
        f"ORDER BY rank DESC, display_name LIMIT {MAX_CANDIDATES}",
        # rank 2 is the team's own id or abbreviation; rank 1 a name match that
        # starts a word ('LA%' catches "LA Clippers", '% LA%' catches "Los
        # Angeles Lakers"); rank 0 an incidental substring.
        [text, text, f"{text}%", f"% {text}%", text, text, f"%{text}%"],
    ).fetchall()
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
    answer says so - "'wembyanama' was read as Victor Wembanyama" - so a
    wrong guess is visible and a real ambiguity (more than one candidate,
    which :func:`suggest_players` would have to be asked about instead of
    called with) is never silently picked.

    Public, unlike :func:`_note_name_reading`: written from
    :mod:`association.query.templates.common`, which has no other way to
    reach the :data:`_NAME_READINGS` context.

    .. versionadded:: 4.4.0
    """
    notes = _NAME_READINGS.get()
    if notes is None:
        return
    note = f"({text!r} was read as {chosen.name} - a near spelling with no other match.)"
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
    """
    if not available:
        return _resolve(find_players(con, text), text, ("name",))
    everyone = find_players(con, text, limit=None)
    if len(everyone) < 2:
        return _resolve(everyone, text, ("name",))
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
_RECORD_ASKED = re.compile(r"\brecords?\b", re.IGNORECASE)


def _player_record_subject(con: duckdb.DuckDBPyConnection, question: str, slots: dict[str, Any], opponent: Entity) -> str | None:
    """The player a record-against-a-team question is about, or None.

    Two shapes, both measured (``ISSUES.md`` #163). The router either files
    the player in ``teams`` beside the real opponent - "Embiid career record
    vs boston" arrives as ``teams: ['Joel Embiid', 'Boston Celtics']`` - or
    replaces him with his own team, leaving two real franchises and the
    player's name only in the question: "Show Embiid's career record against
    Boston" arrives as ``teams: ['Philadelphia 76ers', 'Boston Celtics']``.
    """
    listed = slots.get("teams")
    for name in listed if isinstance(listed, list) else []:
        # A name in the TEAM list that names no team and does name exactly one
        # player. `_team_named` first, so "Boston" stays a team.
        if isinstance(name, str) and name.strip() and _team_named(con, name) is None and len(find_players(con, name)) == 1:
            return name
    named = _scope_from_question_only_player(con, question)
    # `_scope_from_question_only_player` already drops a name held only by a
    # word that names a team, which is what keeps "boston" from naming Brandon
    # Boston Jr. here; this also refuses the opponent's own name outright.
    return named if named is not None and not _shares_word(named, opponent.name) else None


def player_record_against_a_team(con: duckdb.DuckDBPyConnection, question: str, intent: str, slots: dict[str, Any]) -> str | None:
    """The intent a "PLAYER's record against TEAM" question really wants, or None.

    ``head_to_head`` is two franchises meeting, and the router sends a
    player's record against one of them there as well - which is a different
    question, since it counts every meeting including the ones he sat out.
    Both of the shapes it arrives in are described in
    :func:`_player_record_subject`, and both fell through (``ISSUES.md``
    #163): 76ers 13-15 in the 28 regular-season games Joel Embiid played
    against Boston, against a 76ers-Celtics record covering far more.

    ``with_without`` is what answers it - a team's record in the games one
    player played against the ones he missed, narrowed to one opponent - so
    the slots are rewritten for it and the new intent returned. The subject
    goes in ``without`` because that is the slot the template splits BY; it
    infers his team itself, which is why none is passed.

    Returns None for every other question, including a real head-to-head, so
    a question that works today cannot move.

    .. versionadded:: 4.4.0
    """
    if intent != "head_to_head" or not _RECORD_ASKED.search(question):
        return None
    season = slots.get("season") if isinstance(slots.get("season"), int) else None
    opponent = _team_after_versus(con, question, season)
    if opponent is None:
        return None
    player = _player_record_subject(con, question, slots, opponent)
    if player is None:
        return None
    # Only the slots that still mean the same thing for the new intent. The
    # team slots are exactly what must not survive: they are the reading being
    # replaced.
    kept = {key: value for key, value in slots.items() if key in ("season", "season_type", "span", "venue")}
    slots.clear()
    slots.update(kept)
    slots["without"] = [player]
    slots["opponent"] = opponent.name
    return "with_without"
