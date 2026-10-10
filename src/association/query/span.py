"""The span tagger: the one reader of the seasons a question covers and the
season type it reads (:class:`~association.query.reading.Span`), from the
lexicon's words (:mod:`association.query.lexicon`) and the few facts of the
question the stages settled before it - the intent, whether a player and a
window were named, a date, a game of a series, an ordinal season, a range
opened on a dated day. It claims the characters it read
(:class:`~association.query.reading.Claim`), each once.

Phase 3, step 2's first slice: until it, six stages of
:mod:`association.query.router` wrote the six slots this replaces, each
with its own regexes (``_route_season_slots``, the span half of
``_route_filter_slots``, ``_route_season_range`` and ``_route_since_dated``
inside the calendar stage, a pair's career in ``_route_relation_intent_slots``,
a log's and a count's career in ``_route_subject_slots``,
``_route_game_log_recent_span``), in an order the measurement held to
(``~/association-research/stages/span_family.py``): the words first, then
a range opened on a date, then the range's own forms (which win), then the
intent's implied careers, then both types for a bare "last N games".

.. versionadded:: 6.0.0
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Iterable
from dataclasses import dataclass, fields, is_dataclass

from association.nba.season import current_season
from association.query import lexicon
from association.query.reading import Claim, SeasonType, Span

LIMIT_COUNTS_SEASONS: frozenset[str] = frozenset({"player_history"})
"""Intents whose ``limit`` counts SEASONS rather than games, so a relative
span ("the past 5 years") is that count (``router._route_relative_window``)
and not a range of seasons.

.. versionadded:: 6.0.0
"""


@dataclass(frozen=True, kw_only=True)
class SpanContext:
    """What the stages settled before the span is read, and the tagger's
    rules read beside the words: the intent they settled on; whether the
    words name a player (a count of his games with no season is his career),
    a window (a log's "last N vs the Pistons" with no season is every
    meeting), "vs", "how many" and "record"; the window as settled
    (``order``, ``limit``: a bare "last N games" log reads both season
    types); a date, a game of a series and an ordinal season (each a
    narrower question than "last N", so none widens the type); and the
    year of a range the calendar stage opened on a dated day ("since
    1/26/20"), which starts the seasons there.

    .. versionadded:: 6.0.0
    """

    intent: str
    player_named: bool = False
    window_named: bool = False
    versus: bool = False
    how_many: bool = False
    record: bool = False
    order: str | None = None
    limit: int | None = None
    date: str | None = None
    game_n: int | None = None
    season_n: int | None = None
    dated_since: int | None = None


@dataclass(frozen=True)
class SpanRead:
    """What the tagger read: the :class:`~association.query.reading.Span`
    and the characters it claimed.

    .. versionadded:: 6.0.0
    """

    span: Span
    claims: tuple[Claim, ...]


def read_span(question: str, context: SpanContext) -> SpanRead:
    """The span ``question``'s words name, under ``context``.

    .. versionadded:: 6.0.0
    """
    claims: list[Claim] = []
    season, season_words = _season(question)
    claims.extend(season_words)
    season_type, both, type_claims = _season_type(question)
    claims.extend(type_claims)
    career, career_claim = career_named(question)
    if career_claim is not None:
        claims.append(career_claim)
    since, until, range_claims, ranged = _range(question, context, career)
    claims.extend(range_claims)
    if ranged:
        # A range the words name replaces the season: "most 3 pointers made
        # since 2020" became season=2020 before this, and was answered as the
        # 2020 regular season's games alone.
        season = None
    if _implied_career(question, context, career):
        career, season = True, None
    span = Span(season=season, season_type=season_type, both=both or _reads_both_types(question, context, career, since), career=career, since=since, until=until)
    return SpanRead(span, claimed(claims))


def _season(question: str) -> tuple[int | None, list[Claim]]:
    """The one season the words name, read in code rather than by a model:
    relative-date arithmetic ("last season") is arithmetic, not language."""
    named = lexicon.season_named(question)
    if named is None:
        return None, []
    season, spans = named
    return season, [Claim(start, end, "season") for start, end in spans]


def _season_type(question: str) -> tuple[SeasonType, bool, list[Claim]]:
    """The season type the question asks about: the postseason only when it
    says so, and both at once where it says that outright - "including the
    playoffs", "regular season and playoffs". Both is read APART from the
    postseason's own words, which it contains: read alone they narrowed
    "warriors all-time record including playoff record at away" to the
    playoff road record (51-52). The type itself stays the regular season
    beside it (never the postseason) so nothing reading the type alone
    narrows to the postseason ALONE - the specific wrong-cause shape this
    exists to stop."""
    both = lexicon.BOTH_SEASON_TYPES_WORDS.search(question)
    if both is not None:
        return 2, True, [Claim(both.start(), both.end(), "both")]
    playoffs = lexicon.PLAYOFF_WORDS.search(question)
    if playoffs is not None:
        return 3, False, [Claim(playoffs.start(), playoffs.end(), "season_type")]
    regular = lexicon.REGULAR_SEASON_WORDS.search(question)
    return 2, False, [] if regular is None else [Claim(regular.start(), regular.end(), "season_type")]


def career_named(question: str) -> tuple[bool, Claim | None]:
    """Whether the words ask about more than one season's worth of games at
    once - "career", "all time", "ever", "in history", "all his playoff
    games", "since he joined the league" - and the characters that said so.
    Measured before this existed: "career points leaders" and "Jokic career
    averages" were both answered with one season, fluently. "Career high"
    is the exception: with a season named ("career high this season") it
    means that season's best, so it is blanked before the words are read.

    .. versionadded:: 6.0.0
       ``router._validate_span`` was this, without the claim.
    """
    text = question
    if lexicon.CAREER_HIGH.search(text) and (lexicon.season_from_text(question) is not None or lexicon.SEASON_WORDS.search(text)):
        text = lexicon.CAREER_HIGH.sub(lambda m: " " * len(m.group(0)), text)
    for pattern in (lexicon.CAREER_WORDS, lexicon.CAREER_ALL_GAMES_WORDS, lexicon.CAREER_JOINED_LEAGUE_WORDS):
        match = pattern.search(text)
        if match is not None:
            return True, Claim(match.start(), match.end(), "career")
    return False, None


def _range(question: str, context: SpanContext, career: bool) -> tuple[int | None, int | None, list[Claim], bool]:
    """The first and last season of the range the words name, the
    characters that named it, and whether a range was read at all (which
    drops a named season) - in the order the stages read them: a range
    opened on a dated day first ("since 1/26/20" starts the seasons at the
    date's own year - unless a career word stands), then the words' own
    closed and open forms, which replace it, then a relative "past N
    seasons" (which a history reads as its count of seasons instead, since
    its window counts seasons rather than games, and which drops the
    season either way)."""
    since: int | None = None if career else context.dated_since
    ranged = since is not None
    found = _validate_range(question)
    if found is not None:
        first, last, range_claims = found
        return first, last, range_claims, True
    relative = _relative_since(question)
    if relative is None:
        return since, None, [], ranged
    if context.intent in LIMIT_COUNTS_SEASONS:
        return since, None, [relative[1]], True
    return relative[0], None, [relative[1]], True


def _validate_range(question: str) -> tuple[int, int | None, list[Claim]] | None:
    """The first and last season a range covers - (first, None) for an open
    one - with the characters that named it. Four CLOSED forms beside the
    open "since 2020": a season-hyphenated span joined by "to"/"through",
    "between YYYY and YYYY", a bare "YYYY-YYYY" (or "2015-18", "00-02") and
    two adjacent season numbers with nothing joining them; then "since
    2000-01", "since 2020", and a decade. Each closed form fills the last
    season as well as the first - see AGENTS.md, "a season range", for why
    an unfilled ``until`` is this project's worst failure shape rather than
    a missing nicety.

    .. versionadded:: 6.0.0
       ``router._validate_range`` was this, without the claims.
    """
    to_spans = _validate_range_to(question)
    if to_spans is not None:
        return to_spans
    between = lexicon.RANGE_BETWEEN.search(question)
    if between is not None:
        first, last = int(between.group(1)), int(between.group(2))
        return *((first, last) if first <= last else (last, first)), [Claim(between.start(), between.end(), "range")]
    for span in lexicon.season_spans(question):
        if span.is_range:
            return span.first, span.season, [Claim(span.start, span.end, "range")]
    bare_years = lexicon.RANGE_BARE_YEARS.search(question)
    if bare_years is not None and int(bare_years.group(2)) == int(bare_years.group(1)) + 1:
        return int(bare_years.group(1)), int(bare_years.group(2)), [Claim(bare_years.start(), bare_years.end(), "range")]
    for span in lexicon.season_spans(question):
        before = lexicon.SINCE_SPAN.search(question, 0, span.start)
        if before is not None:
            # "since 2000-01": the season ending in the later year; a span
            # that is not one season's two years is no range here, and the
            # open forms below have their turn.
            if span.season == span.first + 1:
                return span.season, None, [Claim(before.start(), span.end, "range")]
            break
    since = lexicon.SINCE_YEAR.search(question)
    if since is not None:
        return int(since.group(1)), None, [Claim(since.start(), since.end(), "range")]
    decade = lexicon.DECADE.search(question)
    if decade is not None:
        first = int(decade.group(1) + "0")
        return first, first + 9, [Claim(decade.start(), decade.end(), "range")]
    return None


def range_named(question: str) -> tuple[int, int | None] | None:
    """The first and last season of the range the words name - (first,
    None) for an open one - or None (:func:`_validate_range`, without the
    claims): what the stages ask where a range changes another family's
    reading (two players "vs" over a range are the pair relation's).

    .. versionadded:: 6.0.0
    """
    found = _validate_range(question)
    return None if found is None else (found[0], found[1])


def _validate_range_to(question: str) -> tuple[int, int, list[Claim]] | None:
    """The first and last season of two seasons written as spans with only
    "to" or "through" between them - "2019-20 to 2023-24", "from 02-03 to
    06-07" - or ``None`` where the question writes no such pair. The spans
    are :func:`~association.query.lexicon.season_spans`', so each end is
    the season it names alone, or a range's own first and last ("1999-00 to
    2001-06" ends in 2006)."""
    for left, right in itertools.pairwise(lexicon.season_spans(question)):
        if lexicon.RANGE_TO_WORDS.fullmatch(question, left.end, right.start):
            first = left.first if left.is_range else left.season
            claim = [Claim(left.start, right.end, "range")]
            return (first, right.season, claim) if first <= right.season else (right.season, first, claim)
    return None


def _relative_since(question: str) -> tuple[int, Claim] | None:
    """The first season "past/last N seasons" (or "...years") reaches, counted
    back from the current one - "past two seasons" from season 2026 is 2025
    and 2026, so a range starting at 2025 alone already names exactly those two."""
    match = lexicon.PAST_N_SEASONS.search(question)
    if match is None:
        return None
    word = match.group(1).lower()
    count = lexicon.PAST_N_SEASONS_COUNT_WORDS.get(word) or int(word)
    return current_season() - count + 1, Claim(match.start(), match.end(), "range")


def relative_seasons(question: str) -> int | None:
    """How many seasons "past/last N seasons" names, or None - what a
    history's window counts (``router._route_relative_window``).

    .. versionadded:: 6.0.0
    """
    found = _relative_since(question)
    return None if found is None else current_season() - found[0] + 1


def _implied_career(question: str, context: SpanContext, career: bool) -> bool:
    """A career the question implies without a career word, each a measured
    rule: a pair's record with no season named is their meetings across
    their careers ("steph curry record vs lebron regular season without kd",
    yardstick-v2 F114: the model's default season held no such teammate);
    a log's last N meetings with an opponent, or every meeting over both
    season types, with no season named is wherever they fall ("last 8 games
    vs pistons" answered from the current season found four and said so);
    and a count asked of one player with no season in sight is his career
    ("how many times has embiid fouled out?" is 0 this season and 9 in his
    career, and only the second is the question - product decision,
    2026-09-19). A season the question names, this one included, still wins."""
    if lexicon.season_from_text(question) is not None or lexicon.SEASON_WORDS.search(question):
        return False
    if context.intent == "player_matchup" and context.record:
        return True
    if context.intent == "game_log" and context.versus and (context.window_named or lexicon.LAST_WORD.search(question) or lexicon.BOTH_SEASON_TYPES_WORDS.search(question)):
        return True
    return context.intent == "threshold_count" and context.player_named and context.how_many and not career and not context.season_n


def _reads_both_types(question: str, context: SpanContext, career: bool, since: int | None) -> bool:
    """A "last N games" log naming no season type reads both and takes the
    newest N by date, rather than silently defaulting to the regular season
    ("Show me the Knicks last 5 games" used to list games through
    2026-04-12 while their real last five were the 2026 Finals). Only for
    the shape: a newest-first window with a real count, and nothing already
    fixing which games are meant - a game of a KNOWN playoff series, a
    career (no single year to mix two types within), a range of seasons,
    one calendar day - and no type named either way."""
    if context.intent != "game_log" or context.order != "recent":
        return False
    if not isinstance(context.limit, int) or context.limit < 1:
        return False
    if context.game_n or career or since is not None or context.date:
        return False
    return not (lexicon.PLAYOFF_WORDS.search(question) or lexicon.REGULAR_SEASON_WORDS.search(question))


def needed(question: str, claims: Iterable[Claim], reads: Callable[[str], object], *, outside: str | None = None, gives_up: bool = False) -> tuple[Claim, ...]:
    """``claims`` cut to the words the rule that made them could not do
    without (Phase 3, step 3): each content word a claim touches is deleted
    from the question in turn (:func:`~association.query.lexicon.without_word`,
    the claims ledger's own probe) and the rule asked again - ``reads``, its
    reading of a question, everything it writes into the reading - and a
    word whose deletion leaves that reading as it was is one the rule did
    not need: its claim stops short of it. "games" in "last 10 games" (ten
    games either way), the "100" of "netpoints per 100 possessions" (the
    rate reads per 100 without it), "regular season" where the season type
    read is the default anyway. A function word
    (:data:`~association.query.lexicon.CONTENT_STOPWORDS`) between two words
    kept stays inside the claim; one at its edge goes. A claim keeps its
    ``what``, split where a word between two kept ones was not needed.

    With ``outside``, every other content word is asked too, and a run of
    the ones the rule's reading turns on is claimed under that name: a word
    the rule read without claiming it - the possessive that makes "curry's
    last game" one game of his, the "record" that makes a matchup his
    career, a "contrast" that keeps "with sga" from naming a companion.

    With ``gives_up``, a word outside every claim whose deletion only ADDS
    to the reading (:func:`_gives_up`: every field the rule set stays as it
    was, and one it left unset is set) is not claimed: its presence made the
    rule read less, and the words it suppressed are words nothing read.
    "2024 and 2025" names two seasons, so neither year is THE season, and
    the span reads none - the years are unread, and say so, where claiming
    them as the span's would hide that the question's seasons were dropped.

    So the claims and the ledger measure one thing - which words the
    reading depends on - from inside each rule and from outside the whole
    reading, and the Reading's unread words (:attr:`Reading.unread
    <association.query.reading.Reading.unread>`) agree with the ledger's
    wherever the reading of a word is one rule's.

    .. versionadded:: 6.0.0
    """
    claims = tuple(claims)
    if not claims and outside is None:
        return ()
    held = reads(question)
    tokens = lexicon.content_tokens(question)
    verdicts: dict[tuple[int, int], bool | None] = {}

    def verdict(start: int, end: int, word: str) -> bool | None:
        """Whether the word at ``start``-``end`` was needed, asked once -
        None for a function word, kept only between two words kept."""
        if (start, end) not in verdicts:
            verdicts[(start, end)] = None if not word or lexicon.without_possessive(word) in lexicon.CONTENT_STOPWORDS else reads(lexicon.without_word(question, start, end)) != held
        return verdicts[(start, end)]

    kept: list[Claim] = []
    for claim in claims:
        touched = [(index, max(start, claim.start), min(end, claim.end), verdict(start, end, word)) for index, (start, end, word) in enumerate(tokens) if start < claim.end and end > claim.start]
        kept.extend(_needed_runs(touched, claim.what))
    if outside is not None:
        untouched = [(index, start, end, verdict(start, end, word)) for index, (start, end, word) in enumerate(tokens) if not any(start < claim.end and end > claim.start for claim in claims)]
        if gives_up:
            untouched = [(index, start, end, False if keep and _gives_up(held, reads(lexicon.without_word(question, start, end))) else keep) for index, start, end, keep in untouched]
        kept.extend(_needed_runs(untouched, outside))
    return tuple(kept)


def _gives_up(held: object, probed: object) -> bool:
    """Whether ``probed`` - a rule's reading with one word deleted - only
    adds to ``held``, its reading of the whole question: each part ``held``
    set is the same, and a part it left unset (None, False, empty) is set.
    Parts are a typed value's fields, a tuple's items in order."""
    if held == probed:
        return False
    return _gives_up_parts(held, probed)


def _gives_up_parts(held: object, probed: object) -> bool:
    """:func:`_gives_up` without the equality, part by part."""
    if held is None or held is False or held == () or held == "":
        return True
    if is_dataclass(held) and not isinstance(held, type) and type(held) is type(probed):
        return all(getattr(held, f.name) == getattr(probed, f.name) or _gives_up_parts(getattr(held, f.name), getattr(probed, f.name)) for f in fields(held))
    if isinstance(held, tuple) and isinstance(probed, tuple) and len(held) == len(probed):
        return all(a == b or _gives_up_parts(a, b) for a, b in zip(held, probed, strict=True))
    return False


def read_by(question: str, what: str, reads: Callable[[str], object]) -> tuple[Claim, ...]:
    """The words of ``question`` a rule that reads the whole question
    decided by - a grammar of rows written as lookaheads, which look for the
    words they want and the words that rule them out anywhere in it - as
    claims named ``what``: each word whose deletion changes the rule's
    decision (``reads``; :func:`needed` over no claim, every word
    ``outside``). What such a rule consumed is no stretch of characters its
    match spans, so what it read is what its decision turned on.

    .. versionadded:: 6.0.0
    """
    return needed(question, (), reads, outside=what)


def _needed_runs(pieces: list[tuple[int, int, int, bool | None]], what: str) -> list[Claim]:
    """One claim per run of kept words in ``pieces`` (each the word's place
    in the question, its stretch, and whether it was needed - None for a
    function word, kept only between two kept ones), in order, each named
    ``what``: a run ends at a word not needed, and where the next piece is
    not the question's next word."""
    out: list[Claim] = []
    run: list[tuple[int, int]] = []
    pending: list[tuple[int, int]] = []
    last = -2
    for index, start, end, keep in pieces:
        if index != last + 1:
            out.extend(_needed_claim(run, what))
            run, pending = [], []
        last = index
        if keep is None:
            pending.append((start, end))
        elif keep:
            run.extend([*pending, (start, end)] if run else [(start, end)])
            pending = []
        else:
            out.extend(_needed_claim(run, what))
            run, pending = [], []
    out.extend(_needed_claim(run, what))
    return out


def _needed_claim(run: list[tuple[int, int]], what: str) -> list[Claim]:
    """One claim over a run of kept words, or none for an empty run."""
    return [Claim(run[0][0], run[-1][1], what)] if run else []


def claimed(claims: list[Claim]) -> tuple[Claim, ...]:
    """``claims`` in the question's order, one per stretch of characters: a
    claim inside another's characters is the same reading (the season span
    a range was read from, the "playoffs" inside "including the playoffs")
    and folds into it; two claims that overlap without one holding the
    other share a word two readings both need, and become one claim over
    both stretches, named for both (``"window+season_type"``): "his last
    game 7" is one game at the end of his span AND the seventh game of a
    series, and "in march 24 2018" the month and the day in it. Until
    2026-10-09 such an overlap raised, as two rules reading one word - and
    "lebron's last game 7" raised out of the reader to the user, since the
    window's "last game" and the postseason's "game 7" are both right.

    .. versionadded:: 6.0.0

    .. versionchanged:: 6.0.0
       A partial overlap is one claim named for both readings, not a
       ``ValueError``; two overlapping stretches of one reading keep its name.
    """
    kept: list[Claim] = []
    for claim in sorted(claims, key=lambda c: (c.start, -c.end)):
        if kept and claim.start < kept[-1].end:
            if claim.end <= kept[-1].end:
                continue  # inside the last claim: the same reading
            last = kept.pop()
            # Two stretches of ONE reading ("netpoints per 100" and "per 100
            # possessions", both the measure's) are one claim of it.
            kept.append(Claim(last.start, claim.end, last.what if last.what == claim.what else f"{last.what}+{claim.what}"))
            continue
        kept.append(claim)
    return tuple(kept)
