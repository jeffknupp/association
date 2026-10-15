"""The split tagger: the one reader of the split a question divides a
player's games by - home and away, starts and the bench (or one half of
it), wins and losses, by month - from the lexicon's words
(:data:`~association.query.lexicon.SPLIT_WORDS`), with the start or the
bench phrase of a teammate the subject reading read blanked out: "maxey
points when embiid starts" is Embiid's start, not Maxey's own starter split.

One reading, before the cuts tagger that reads it (a venue beside a venue
split is the split's).

.. versionadded:: 6.0.0
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast, get_args

from association.query import lexicon
from association.query.reading import Claim, Companion, ScopeError, Split
from association.query.subject import _companion_phrases, _condition_role, _near

if TYPE_CHECKING:
    import re

OWN_READ_ROLES = frozenset({"started", "bench"})
"""The roles a companion can have that narrow the subject's OWN games as a
condition (``subject._apply_conditions``) rather than divide them into a
with/without split: "maxey points when embiid starts" is Maxey's line in
Embiid's starts.

.. versionadded:: 6.0.0
   ``parse._OWN_READ_ROLES`` until Phase 3, step 4.
"""


@dataclass(frozen=True)
class SplitRead:
    """What the tagger read: the split (``None`` for none, or for two named
    at once) and the characters it claimed.

    .. versionadded:: 6.0.0
    """

    split: Split | None
    claims: tuple[Claim, ...]


def role_phrases(conditions: tuple[Companion, ...], question: str) -> list[tuple[re.Match[str], str]]:
    """Each companion phrase of ``question`` (``subject._companion_phrases``)
    that names one of the subject reading's ``conditions``, with the role it
    gives him (``subject._condition_role``) - the reading's own phrases,
    never a second reader of them.

    .. versionadded:: 6.0.0
       ``parse._read_route_role_phrases`` until Phase 3, step 4.
    """
    phrases: list[tuple[re.Match[str], str]] = []
    for match in _companion_phrases(question):
        predicate = _condition_role(match.group(1).lower(), match.group(2))[0]
        if any(c.predicate == predicate and _near(c.player, match.group(2)) for c in conditions):
            phrases.append((match, predicate))
    return phrases


def read_split(question: str, conditions: tuple[Companion, ...] = ()) -> SplitRead:
    """The one split ``question``'s words name (:data:`~association.query.lexicon.SPLIT_WORDS`),
    narrowed to the half of the starter/bench split it names where it names
    one, read with every teammate's start or bench phrase blanked out (the
    subject reading's ``conditions``): None for none, or for two.

    .. versionadded:: 6.0.0
       ``router._route_split_slot`` and ``parse._read_route_split`` until
       Phase 3, step 4, two readers of one value.
    """
    blanked = _split_blanked(question, conditions)
    found = [(name, match) for name, pattern in lexicon.SPLIT_WORDS.items() if (match := pattern.search(blanked)) is not None]
    if len(found) != 1:
        return SplitRead(None, ())
    name, match = found[0]
    split, side = _split_side(name, blanked)
    claims = [Claim(match.start(), match.end(), "split")] + ([Claim(side.start(), side.end(), "split")] if side is not None else [])
    return SplitRead(_as_split(split), tuple(claims))


def _split_blanked(question: str, conditions: tuple[Companion, ...]) -> str:
    """``question`` with each teammate's start or bench phrase blanked to
    spaces of its length - the role's words running past the phrase through
    "the bench" (:data:`~association.query.lexicon.ROLE_TAIL`) - so every
    position stands where the question holds it."""
    blanked = question
    for match, predicate in role_phrases(conditions, question):
        if predicate not in OWN_READ_ROLES:
            continue
        tail = lexicon.ROLE_TAIL.match(question, match.end(2))
        end = tail.end() if tail is not None else match.end(2)
        blanked = blanked[: match.start()] + " " * (end - match.start()) + blanked[end:]
    return blanked


def _split_side(split: str, question: str) -> tuple[str, re.Match[str] | None]:
    """``split``, narrowed to the half the question names where it names
    one - ``"starter"`` or ``"bench"`` for a question about one half, with
    the words that named it; the category otherwise, both halves named or a
    split with no halves. "Jrue holiday last 50 games as a starter" is a log
    of his starts; "starting vs coming off the bench" is the splits
    question."""
    if split != "starter_bench":
        return split, None
    starter, bench = lexicon.STARTER_WORDS.search(question), lexicon.BENCH_WORDS.search(question)
    if starter is not None and bench is None:
        return "starter", starter
    if bench is not None and starter is None:
        return "bench", bench
    return split, None


def _as_split(split: str | None) -> Split | None:
    """A split read off the words as the filters' own literal - the reader
    writes one of the set, and one that wrote anything else is a bug said
    out loud."""
    if split is None:
        return None
    if split not in get_args(Split):
        raise ScopeError(f"split {split!r} is not one the Scope holds")
    return cast("Split", split)
