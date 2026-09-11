"""Resolving a name the model produced ("Lakers", "LAL", "Curry") to a real id.

Callers want different things from an ambiguous name - a chart of the wrong
Curry is a visible mistake, a NUMBER attributed to the wrong Curry is not - so
both behaviours stay available rather than one being picked for everyone:

    find_*    - every candidate, best first. The caller decides.
    resolve_* - one entity, or Ambiguous/NotFound. Never a guess.

Templates use resolve_*, because a template's job is to be trusted with a
number. Ambiguity is returned as a value, and the template asks a clarifying
question (see :func:`clarification`) rather than guessing or falling through.

Charts take the third road: they narrow with :func:`narrow_to_available` first,
to the candidates who have the rows the chart would be drawn from, and only ask
when more than one survives. What made a best match defensible there was the
plot being titled with the name that won - which is no help at all when the
wrong name means no plot gets drawn."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import duckdb

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
    names = nicknames_in(question)
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


# Words of a name or a question, split on anything that is not a letter so
# "Gilgeous-Alexander" is two words and "Jokic's" is "Jokic" and a stray "s".
def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[^A-Za-z]+", text) if w]


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


def override_invented_players(con: duckdb.DuckDBPyConnection, question: str, slots: dict[str, Any]) -> tuple[list[tuple[str, str]], list[str]]:
    """Replace router-supplied player names the question does not support, or
    report the ones that cannot be replaced. Mutates ``slots``.

    :func:`override_nicknames` fixes a nickname the router rewrote wrongly.
    This is the same failure without the nickname: asked to "compare sga and
    embiid" the 3B router emitted ``['Shai Gilgeous-Alexander', 'Jusuf
    Nurkic']`` and the answer was a fluent, correct-looking table of two real
    players, one of whom the question never mentioned. Nothing downstream could
    notice - "Jusuf Nurkic" resolves perfectly.

    So every name is checked against the question before a template reads it,
    and a name with no trace there is not answered about. Where the question
    names somebody nothing else claims, that player takes its place; where it
    does not, the name is reported and the caller falls through to the agent,
    which at least reads the question. Guessing is not on the list.

    Returns:
        The ``(was, now)`` pairs replaced, and the ungrounded names that could
        not be replaced. A non-empty second element means the slots are not
        safe to answer from, even though the first may also be non-empty.

    .. versionadded:: 2.1.0
    """
    slot = "players" if isinstance(slots.get("players"), list) else "player"
    values = slots.get(slot)
    named = [v for v in (values if isinstance(values, list) else [values]) if isinstance(v, str) and v.strip()]
    if not named:
        return [], []

    ungrounded = [name for name in named if not _grounded(con, question, name)]
    if not ungrounded:
        return [], []

    # Only the names nothing already accounts for are available as
    # replacements: "compare sga and embiid" names two players, and one of them
    # is the slot that came through fine.
    kept = [name for name in named if name not in ungrounded]
    spare = [name for name in players_named_in(con, question) if not any(_shares_word(name, k) for k in kept)]
    if len(spare) != len(ungrounded):
        return [], ungrounded

    changed = []
    replacement = dict(zip(ungrounded, spare, strict=True))
    if slot == "players" and isinstance(values, list):
        for i, was in enumerate(values):
            if was in replacement:
                values[i] = replacement[was]
                changed.append((str(was), replacement[was]))
    else:
        slots[slot] = replacement[named[0]]
        changed.append((named[0], replacement[named[0]]))
    return changed, []


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
    """
    if not _COMPARISON.search(question):
        return None
    named = players_named_in(con, question)
    listed = slots.get("players")
    raw = listed if isinstance(listed, list) else [slots.get("player")]
    held = [name for name in raw if isinstance(name, str) and name.strip()]
    if len(named) <= len(held):
        return None
    slots["players"] = named
    slots.pop("player", None)
    return " and ".join(held) or "nobody", " and ".join(named)


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


def compared_but_unmatched(question: str, held: list[str]) -> bool:
    """Whether the question pits players against each other and only one of
    them could be matched to the warehouse.

    A misspelling nothing can repair - "generate fingerprints for embiid vs
    jolic" drew Joel Embiid alone, because "jolic" matches no player and is not
    close enough to exactly one to guess at. Recovering it was measured and
    rejected: a near-spelling search over a question's leftover words finds a
    spurious player in 29 of 51 corpus questions ("season" is one edit from
    Tari Eason, "most" from Quinten Post), and it does not find Nikola Jokic
    here either.

    So the second player stays lost, and the answer says so. That is the whole
    point: one polygon where two were asked for is the project's oldest failure
    shape, and it is only a failure while nothing mentions it.

    .. versionadded:: 2.1.0
    """
    return len(held) < 2 and bool(_VERSUS.search(question))


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
    guess instead of through code, where nothing downstream can see it. A bare
    surname is the canonical thing this project asks about, and it stopped
    asking as soon as the router started completing it.

    So a name the question carries only PART of is cut back to that part, and
    normal resolution decides: ``find_players`` applies the nickname table
    first, so "luka" still answers Luka Doncic, and :func:`resolve_player`
    returns :class:`Ambiguous` for "brown", which is the question being asked.

    Only where the part is ambiguous, which is what keeps this from undoing the
    router's useful work. Completing "jokic", "embiid" or "wembanyama" changes
    no answer, and a name the question spells in full is not a part at all.
    Measured over the ``check_routing.py`` corpus, no slot moves.

    Returns:
        The ``(was, now)`` pairs cut back, for the trace.

    .. versionadded:: 2.1.0
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
}

# "vs", "versus", "against" or "v" and whatever follows. Whether what follows is
# a team is decided against the teams table, not here: "lebron vs kawhi" is two
# players and must stay a comparison.
_AGAINST = re.compile(r"\b(?:vs\.?|versus|against|v\.?)\s+(?:the\s+)?(.+)", re.IGNORECASE)


def _team_named(con: duckdb.DuckDBPyConnection, text: Any) -> Entity | None:
    """The one team ``text`` names outright - an id, an abbreviation, a nickname,
    or a name match starting a word - or None. Never a substring guess: "LA"
    is two teams and stays None, which is the point."""
    if not isinstance(text, str) or not text.strip():
        return None
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
    return Entity(id=str(rows[0][0]), name=rows[0][1]) if len(rows) == 1 else None


def _team_after_versus(con: duckdb.DuckDBPyConnection, question: str) -> Entity | None:
    """The team a question sets a subject AGAINST ("jaylen brown last 8 games vs
    pistons"), or None. Spans of three words down to one are tried, so "vs new
    york" is the Knicks rather than an ambiguous "new"."""
    for match in _AGAINST.finditer(question):
        words = _words(match.group(1))[:3]
        for size in (3, 2, 1):
            if size <= len(words) and len(" ".join(words[:size])) >= 2:
                team = _team_named(con, " ".join(words[:size]))
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
    return bool(carried & asked)


def scope_from_question(con: duckdb.DuckDBPyConnection, question: str, slots: dict[str, Any], *, reads_player: bool, needs_player: bool = False) -> list[str]:
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
    is left exactly as it was.

    Restoring a player follows :func:`override_invented_players`' discipline:
    only when the question names exactly one player, and only for a template
    that reads one (``reads_player``). A player the router simply left out is
    restored only where the template cannot answer without one
    (``needs_player``): "Sga record 36 plus points" came back with no player
    at all, and an optional player slot left empty means "the league".

    .. versionadded:: 2.1.0
    """
    notes: list[str] = []
    versus = _team_after_versus(con, question)
    has_player = bool(slots.get("player")) or bool(slots.get("players"))
    team = _team_named(con, slots.get("team"))

    def only_player() -> str | None:
        """The one player the question names, or None if it names none or several."""
        named = players_named_in(con, question)
        return named[0] if len(named) == 1 else None

    if team is not None and not has_player and reads_player:
        # The team in `team` is the opponent, or a team the question never
        # mentioned (the router's guess at the player's own): either way the
        # player it displaced is the subject.
        displaced = versus is not None and team.id == versus.id
        if displaced or not _team_grounded(con, question, team):
            player = only_player()
            if player is not None:
                slots.pop("team", None)
                slots["player"] = player
                notes.append(f"{team.name!r} was {'the opponent' if displaced else 'not in the question'}; the subject is {player!r}")
                team = None

    listed = slots.get("players")
    if versus is not None and isinstance(listed, list):
        kept = [name for name in listed if not ((found := _team_named(con, name)) is not None and found.id == versus.id)]
        if len(kept) != len(listed):
            notes.append(f"{versus.name!r} is a team, not a player to compare")
            if len(kept) == 1:
                slots.pop("players", None)
                slots["player"] = kept[0]
            else:
                slots["players"] = kept

    if needs_player and not slots.get("player") and not slots.get("players"):
        player = only_player()
        if player is not None:
            slots["player"] = player
            notes.append(f"player {player!r} (from the question; the router left it out)")

    if versus is not None and not slots.get("opponent"):
        carried = [slots.get("team"), *(slots.get("teams") or [])]
        if not any((found := _team_named(con, name)) is not None and found.id == versus.id for name in carried):
            slots["opponent"] = versus.name
            notes.append(f"opponent {versus.name!r} (from the question)")
    return notes


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
    is the reason this is a return value and not just None."""

    query: str
    candidates: list[str]


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


def clarification(text: str, candidates: list[str], kind: str = "player") -> str:
    """The "did you mean" sentence for an ambiguous name.

    Lives here rather than in :mod:`association.query.templates` because both
    halves of the query path ask it now: a template returns it as its answer,
    and a chart's rendering entry point returns it as a message. One phrasing,
    so the same ambiguity does not read two ways depending on which path the
    router happened to take.

    .. versionadded:: 2.1.0
    """
    shown, extra = candidates[:MAX_CLARIFY_CANDIDATES], len(candidates) - MAX_CLARIFY_CANDIDATES
    joined = ", ".join(shown[:-1]) + f" or {shown[-1]}" + (f" ({extra} others also match)" if extra > 0 else "")
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
    reading out a directory.

    Returns:
        Closest first, at most ``MAX_CLARIFY_CANDIDATES`` of them; empty when
        nothing is close enough to be worth naming.

    .. versionadded:: 2.1.0
    """
    tokens = [t for t in text.split() if t]
    if not tokens:
        return []

    if len(tokens) > 1 and len(tokens[-1]) > 2:
        # Word-boundary matches only. find_players falls back to incidental
        # substring hits when nothing starts with the token, which is fine for
        # a name somebody typed and wrong for one being guessed at: backing
        # "Nobody At All" off to "All" otherwise suggests Bo Wall.
        start = re.compile(_WORD_START + re.escape(tokens[-1]), re.IGNORECASE)
        kept = [player for player in find_players(con, tokens[-1]) if start.search(player.name)]
        if kept:
            return kept if len(kept) <= MAX_CLARIFY_CANDIDATES else []

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


def narrow_to_available(con: duckdb.DuckDBPyConnection, candidates: list[Entity], source: Availability, season: int | None = None) -> list[Entity]:
    """The candidates with at least one row in ``source``, in the order given -
    for ``season`` when one is given, and in any season when it is not.

    ``season`` is optional because a chart's is: a request that names no season
    is drawn over a whole career, and narrowing that by one year would be
    filtering the candidates by something the question never said.

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
    if not candidates:
        return []
    placeholders = ", ".join("?" for _ in candidates)
    where = f"athlete_id IN ({placeholders})" + ("" if season is None else " AND season = ?")
    rows = con.execute(
        f"SELECT DISTINCT athlete_id FROM {source.table} WHERE {where}",
        [*(c.id for c in candidates)] + ([] if season is None else [season]),
    ).fetchall()
    have = {str(row[0]) for row in rows}
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


def find_players(con: duckdb.DuckDBPyConnection, text: str) -> list[Entity]:
    """Every token must match, so "Luka Doncic" doesn't also match a player
    sharing only a first name.

    .. versionchanged:: 2.1.0
       Candidates matching at a word boundary rank first and, when there are
       any, are the only ones returned. Incidental substring hits used to be
       ordered among them purely by name: "Ball" answered with Cedric Ceballos.
    """
    # Matched against the WHOLE query, never as a substring, so "book" resolves
    # to Devin Booker while "notebook" is untouched - and "Ant" stops matching
    # every player with "ant" in their name (Durant, Anthony, Antetokounmpo).
    text = PLAYER_NICKNAMES.get(text.strip().casefold(), text)
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
    rows = con.execute(
        f"SELECT athlete_id, display_name, ({strong}) AS strong FROM players WHERE {where} ORDER BY strong DESC, display_name LIMIT {MAX_CANDIDATES}",
        [_WORD_START + re.escape(t) for t in tokens] + [f"%{t}%" for t in tokens],
    ).fetchall()
    matched = [row for row in rows if row[2]] or rows
    return [Entity(id=str(r[0]), name=r[1]) for r in matched]


def find_teams(con: duckdb.DuckDBPyConnection, text: str) -> list[Entity]:
    """Substring matching is kept (so "LA" stays honestly ambiguous rather than
    silently resolving to whichever team is literally named "LA"), but matches
    that start a word are ranked first - otherwise "LA" offers "Atlanta Hawks"
    as a candidate, which makes a clarification look broken."""
    rows = con.execute(
        "SELECT team_id, display_name, "
        "  (team_id = ? OR abbreviation ILIKE ? OR display_name ILIKE ? OR display_name ILIKE ?) AS strong "
        "FROM teams WHERE team_id = ? OR abbreviation ILIKE ? OR display_name ILIKE ? "
        f"ORDER BY strong DESC, display_name LIMIT {MAX_CANDIDATES}",
        # strong = an id/abbreviation hit, or a name match that starts a word:
        # 'LA%' catches "LA Clippers", '% LA%' catches "Los Angeles Lakers".
        [text, text, f"{text}%", f"% {text}%", text, text, f"%{text}%"],
    ).fetchall()
    strong = [Entity(id=str(r[0]), name=r[1]) for r in rows if r[2]]
    # Incidental substring hits ("LA" inside "Atlanta") are dropped whenever a
    # word-boundary match exists, so a clarification offers plausible teams
    # rather than everything the LIKE happened to touch.
    return strong or [Entity(id=str(r[0]), name=r[1]) for r in rows]


def _resolve(candidates: list[Entity], text: str, exact_keys: tuple[str, ...]) -> Resolution:
    if not candidates:
        return NotFound(query=text)
    if len(candidates) == 1:
        return candidates[0]
    exact = _exact(candidates, text, exact_keys)
    return exact if exact is not None else Ambiguous(query=text, candidates=[c.name for c in candidates])


def resolve_player(con: duckdb.DuckDBPyConnection, text: str) -> Resolution:
    """ "Curry" is genuinely ambiguous (Seth and Stephen), and a leaderboard
    row attributed to the wrong one is indistinguishable from a right answer."""
    return _resolve(find_players(con, text), text, ("name",))


def resolve_team(con: duckdb.DuckDBPyConnection, text: str) -> Resolution:
    """One team, or a refusal. See :func:`resolve_player` for why ambiguity is
    returned rather than resolved."""
    return _resolve(find_teams(con, text), text, ("name", "id"))
