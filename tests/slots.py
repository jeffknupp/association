"""The slot door, kept for the tests: a slot dict - the shape a router's
model filed a question in, and the shape a test still spells a hand-built
reading in - turned into the typed filters a Reading and a query hold
(``reading.Filtered``).

Until Phase 3, step 4, slice (b) this was ``reading.Scope.from_slots`` and
the families' doors beside it (``Span.from_slots``, ``Window.from_slots``,
``Cuts.from_slots``, ``Period.from_slots``, ``Line.from_period_slot``,
``Companion.from_slot``): the package's one door for a slot dict, which
nothing in it calls once the router's stages went - the parser writes the
typed values from the words - so it lives with its only callers, moved
whole. ``Subject.from_slots`` and ``Measure.from_slots`` stay in the
package: the parser reads the model's names and stat through them.

- :func:`filters` is the old ``Scope.from_slots``, returning the typed
  values by field name;
- :func:`slots_scope` is a Reading holding nothing but those filters (what
  a test handed a reader as a ``Scope``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date
from typing import TYPE_CHECKING, Any

from association.query.reading import (
    _FILTER_FIELDS,
    Companion,
    Cuts,
    Line,
    Measure,
    Period,
    Reading,
    ScopeError,
    Span,
    Subject,
    Window,
    situation_of,
)

if TYPE_CHECKING:
    pass


def slots_scope(slots: Mapping[str, Any]) -> Reading:
    """A Reading holding nothing but the filters ``slots`` names
    (:func:`filters`) - what a test handed a reader as a ``Scope``."""
    return Reading(**filters(slots))


def filters(slots: Mapping[str, Any]) -> dict[str, Any]:
    """The typed filters a slot dict names, by field name (``who`` for the
    subject; ``subject`` is taken as its old name). An empty value (None, "", an empty
    list, False) is the slot absent; a key nothing here types, or a value
    of the wrong type or outside its closed set, raises - a slot this
    could only drop is a narrowing the answer would silently leave out.

    .. versionadded:: 5.0.0
    """
    unknown = sorted(
        set(slots)
        - _SCOPE_FIELDS
        - frozenset(_SUBJECT_SLOT_NAMES)
        - _SPAN_SLOT_NAMES
        - _WINDOW_SLOT_NAMES
        - _CUT_SLOT_KEYS
        - _PERIOD_SLOT_NAMES
        - _LINE_SLOT_NAMES
        - _COMPANION_SLOT_NAMES
        - _MEASURE_SLOT_NAMES
    )
    if unknown:
        raise ScopeError(f"no scope field for slot(s) {unknown}")
    values: dict[str, Any] = {}
    typed_slots: dict[str, dict[str, Any]] = {"subject": {}, "span": {}, "window": {}, "cuts": {}, "period": {}, "lines": {}, "companions": {}, "measure": {}}
    for name, raw in slots.items():
        # A blank string is the slot absent too: the model files " " for
        # an opponent it has none of, and every template read it as
        # nobody (`_optional_team`), where the relation's resolver read
        # it as a team named nothing and refused.
        if raw is None or raw is False or (isinstance(raw, (str, list, tuple)) and not raw) or (isinstance(raw, str) and not raw.strip()):
            continue
        family, whole = _typed_family(name, raw)
        if whole:
            values[name] = raw
        elif family is None:
            values[name] = _CHECKS[name](name, raw)
        else:
            typed_slots[family][name] = _CHECKS[name](name, raw)
    for family, door in (
        ("subject", Subject.from_slots),
        ("measure", Measure.from_slots),
        ("span", span_from_slots),
        ("window", window_from_slots),
        ("cuts", cuts_from_slots),
        ("period", period_from_slots),
        ("lines", lines_from_slots),
        ("companions", companions_from_slots),
    ):
        if typed_slots[family]:
            if family in values:
                raise ScopeError(f"a typed {family} and the slot(s) {sorted(typed_slots[family])} at once")
            # The lines' door reads the keyed line's stat off the measure.
            values[family] = door({**typed_slots[family], **({"stat": typed_slots["measure"].get("stat")} if family == "lines" else {})})
    if "subject" in values:
        values["who"] = values.pop("subject")
    return values


def lines_from_slots(slots: Mapping[str, Any]) -> tuple[Line, ...]:
    """The lines four slot values name (:meth:`Scope.from_slots` reads them
    off a slot dict): the ``above`` and ``below`` phrases as the lines their
    words read as (through the phrase reader every reader of them went
    through), a ``threshold`` beside the ``stat`` as the shape's own keyed
    line, and the ``period_condition``. A phrase whose words name no stat is
    a line on no measure, which the relation refuses by its words as it did;
    a phrase carrying the keyed line's own number beside it is read as the
    count's reader reads the pair (the phrase IS the count)."""
    from association.query.lines import phrase_line  # the reader of a phrase's words, beside the lexicon

    lines: list[Line] = [phrase_line(str(phrase), below=True) for phrase in _as_list(slots.get("below"))]
    lines += [phrase_line(str(phrase), below=False) for phrase in _as_list(slots.get("above"))]
    threshold = slots.get("threshold")
    if threshold is not None:
        from association.query.measure import column_of  # the catalog; `measure` imports this module's types

        stat = slots.get("stat")
        lines.insert(0, Line(measure=column_of(stat) if isinstance(stat, str) else None, value=threshold, as_typed=f"{threshold} {stat}" if isinstance(stat, str) else str(threshold), keyed=True))
    in_period = slots.get("period_condition")
    if in_period is not None:
        lines.append(line_from_period_slot(in_period))
    return tuple(lines)


def _as_list(raw: Any) -> list[Any]:
    if raw is None:
        return []
    return [raw] if isinstance(raw, str) else list(raw)


def companions_from_slots(slots: Mapping[str, Any]) -> tuple[Companion, ...]:
    """The companions three slot values name (:meth:`Scope.from_slots` reads
    them off a slot dict): the ``conditions`` entries as typed, the
    ``without`` names as absent teammates, the ``with_player`` names as
    teammates who played (beside a role the same name holds in
    ``conditions``, as the two slots held them: the split divides by the
    one and narrows by the other) - the other side's first, as the slots
    held them."""
    found: list[Companion] = [companion_from_slot(entry) for entry in _as_list(slots.get("conditions"))]
    found += [Companion(player=name, predicate="absent") for name in _as_list(slots.get("without"))]
    found += [Companion(player=name, predicate="played") for name in _as_list(slots.get("with_player"))]
    other_side = [c for c in found if c.side == "opponent"]
    return (*other_side, *(c for c in found if c.side == "own"))


def _typed_family(name: str, raw: Any) -> tuple[str | None, bool]:
    """Which typed value the slot ``name`` belongs to at the door, and
    whether ``raw`` IS that value whole: ``("span", True)`` for a
    :class:`Span` under ``span``, ``("span", False)`` for the span's slot
    ``season`` (and ``span``, the slot, as a string), the window's and the
    cuts' and the period's the same, ``(None, False)`` for a field of the
    Scope's own."""
    whole = _WHOLE_VALUES.get(name)
    if whole is not None and isinstance(raw, whole):
        return name, True
    if name in ("lines", "companions") and isinstance(raw, (tuple, list)) and all(isinstance(each, Line if name == "lines" else Companion) for each in raw):
        return name, True
    for slot_names, family in _FAMILY_SLOT_NAMES:
        if name in slot_names:
            return family, False
    return None, False


def _text(name: str, raw: Any) -> str:
    if not isinstance(raw, str):
        raise ScopeError(f"scope {name}={raw!r} is not text")
    return raw


def _texts(name: str, raw: Any) -> tuple[str, ...]:
    # A bare string is one item: the readers have always taken it so
    # (``entities.teammate_names``: slot values are advisory, and a
    # reader of one shape would be one stray route from answering nothing).
    if isinstance(raw, str):
        return (raw,)
    if not isinstance(raw, (list, tuple)) or not all(isinstance(item, str) for item in raw):
        raise ScopeError(f"scope {name}={raw!r} is not a list of text")
    return tuple(raw)


def _iso_day(name: str, raw: Any) -> str:
    # One calendar day as the cuts tagger writes it (cuts._day): an
    # ISO date and nothing else - "last night" would pass a text check and
    # then fail in whichever reader got it first.
    try:
        valid = isinstance(raw, str) and len(raw) == 10 and date.fromisoformat(raw).isoformat() == raw
    except ValueError:
        valid = False
    if not valid:
        raise ScopeError(f"scope {name}={raw!r} is not a calendar day (YYYY-MM-DD)")
    return raw


def _whole(name: str, raw: Any) -> int:
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise ScopeError(f"scope {name}={raw!r} is not a whole number")
    return raw


def _flag(name: str, raw: Any) -> bool:
    if not isinstance(raw, bool):
        raise ScopeError(f"scope {name}={raw!r} is not a flag")
    return raw


def _conditions(name: str, raw: Any) -> list[Any]:
    if not isinstance(raw, (list, tuple)):
        raise ScopeError(f"scope {name}={raw!r} is not a list of conditions")
    return list(raw)


def _one_of(*allowed: object) -> Callable[[str, Any], Any]:
    def check(name: str, raw: Any) -> Any:
        """``raw`` when it is one of ``allowed``, else the reason it is not."""
        if isinstance(raw, bool) or raw not in allowed:
            raise ScopeError(f"scope {name}={raw!r} is not one of {allowed}")
        return raw

    return check


_CHECKS: dict[str, Callable[[str, Any], Any]] = {
    **dict.fromkeys(("player", "team", "opponent", "own_team", "stat", "ranked_by", "rate", "kind", "situation", "round"), _text),
    "date": _iso_day,
    **dict.fromkeys(("players", "teams", "with_player", "without", "above", "below", "fields"), _texts),
    **dict.fromkeys(("threshold", "season", "since", "until", "game_n", "season_n", "period", "limit"), _whole),
    **dict.fromkeys(("per_game", "season_type_unstated"), _flag),
    "conditions": _conditions,
    "period_condition": lambda name, raw: raw,
    "side": _one_of("offense", "defense", "total"),
    "shot_value": _one_of(1, 2, 3),
    "rank": _one_of("most", "fewest", "best", "worst"),
    "season_type": _one_of(2, 3),
    "span": _one_of("career"),
    "split": _one_of("home_away", "starter_bench", "wins_losses", "month", "starter", "bench"),
    "half": _one_of(1, 2),
    "venue": _one_of("home", "away"),
    "order": _one_of("recent", "first"),
}
_SCOPE_FIELDS = frozenset({*_FILTER_FIELDS, "subject"})
#: The four slot names the subject was carried as until Phase 3, step 2 -
#: the names the subject's door still takes (:meth:`Subject.from_slots`);
#: the reader's module held them until slice (c) deleted the projection.
_SUBJECT_SLOT_NAMES = ("player", "players", "team", "teams")
#: The seven slot names the measure was carried as until Phase 3, step 2 -
#: the names the measure's door still takes (:func:`_measure_from_slots`).
_MEASURE_SLOT_NAMES = frozenset({"stat", "fields", "per_game", "rate", "side", "shot_value", "kind"})
#: The six slot names the span's door still takes (:meth:`Span.from_slots`).
_SPAN_SLOT_NAMES = frozenset({"season", "season_type", "season_type_unstated", "span", "since", "until"})
#: The four slot names the window's door still takes (:meth:`Window.from_slots`).
_WINDOW_SLOT_NAMES = frozenset({"order", "limit", "rank", "ranked_by"})
#: The slot each cut was declared under until Phase 3, step 2 (``own_team``
#: the tenure's, the rest their own): the door's own copy, since the reader
#: says a cell by its own name (``reading.cell_facts``) and keeps no slot names.
_CUT_SLOT_NAMES: dict[str, str] = {
    "opponent": "opponent",
    "tenure": "own_team",
    "date": "date",
    "situation": "situation",
    "game_n": "game_n",
    "season_n": "season_n",
    "round": "round",
    "venue": "venue",
}
#: The eight slot names the cuts' door still takes (:meth:`Cuts.from_slots`).
_CUT_SLOT_KEYS = frozenset(_CUT_SLOT_NAMES.values())
#: The two slot names the period's door still takes (:meth:`Period.from_slots`).
_PERIOD_SLOT_NAMES = frozenset({"period", "half"})
#: The four slot names the lines' door still takes (:func:`_lines_from_slots`).
_LINE_SLOT_NAMES = frozenset({"threshold", "above", "below", "period_condition"})
#: The three slot names the companions' door still takes (:func:`_companions_from_slots`).
_COMPANION_SLOT_NAMES = frozenset({"with_player", "without", "conditions"})

#: Each typed value's field, and the type a whole value under it has.
_WHOLE_VALUES: dict[str, type] = {"subject": Subject, "who": Subject, "span": Span, "window": Window, "cuts": Cuts, "period": Period, "measure": Measure}
#: Each family's slot names, and the family they pass the door into.
_FAMILY_SLOT_NAMES: tuple[tuple[frozenset[str], str], ...] = (
    (frozenset(_SUBJECT_SLOT_NAMES), "subject"),
    (_SPAN_SLOT_NAMES, "span"),
    (_WINDOW_SLOT_NAMES, "window"),
    (_CUT_SLOT_KEYS, "cuts"),
    (_PERIOD_SLOT_NAMES, "period"),
    (_LINE_SLOT_NAMES, "lines"),
    (_COMPANION_SLOT_NAMES, "companions"),
    (_MEASURE_SLOT_NAMES, "measure"),
)


def span_from_slots(slots: Mapping[str, Any]) -> Span:
    """The Span six slot values name (:meth:`Scope.from_slots` reads
    them off a slot dict): ``season``, ``season_type``,
    ``season_type_unstated``, ``span``, ``since``, ``until``."""
    return Span(
        season=slots.get("season"),
        season_type=slots.get("season_type"),
        both=bool(slots.get("season_type_unstated")),
        career=slots.get("span") == "career",
        since=slots.get("since"),
        until=slots.get("until"),
    )


def window_from_slots(slots: Mapping[str, Any]) -> Window:
    """The Window four slot values name (:meth:`Scope.from_slots` reads
    them off a slot dict): ``order``, ``limit``, ``rank``, ``ranked_by``.
    A slot dict says nothing about what a count counts, so ``of`` is
    games; the tagger is the one writer of a count of seasons."""
    return Window(order=slots.get("order"), count=slots.get("limit"), rank=slots.get("rank"), by=slots.get("ranked_by"))


def cuts_from_slots(slots: Mapping[str, Any]) -> Cuts:
    """The Cuts eight slot values name (:meth:`Scope.from_slots` reads
    them off a slot dict): ``opponent``, ``own_team``, ``venue``,
    ``date``, ``situation`` (its words, parsed here), ``round``,
    ``game_n``, ``season_n``."""
    situation = slots.get("situation")
    return Cuts(
        opponent=slots.get("opponent"),
        tenure=slots.get("own_team"),
        venue=slots.get("venue"),
        date=slots.get("date"),
        situation=situation_of(situation) if isinstance(situation, str) else None,
        round=slots.get("round"),
        game_n=slots.get("game_n"),
        season_n=slots.get("season_n"),
    )


def period_from_slots(slots: Mapping[str, Any]) -> Period:
    """The Period the two slot values name (:meth:`Scope.from_slots`
    reads them off a slot dict): ``period`` or ``half``, never both -
    the words name one or the other, and a half beside a quarter was
    the model-era slot under the stages' half."""
    if "period" in slots and "half" in slots:
        raise ScopeError(f"scope period={slots['period']!r} and half={slots['half']!r} at once")
    if "half" in slots:
        return Period(number=slots["half"], half=True)
    return Period(number=slots["period"])


def line_from_period_slot(entry: Any) -> Line:
    """One ``period_condition`` slot value, a dict, as a line in a quarter
    or half - raising on a shape the relation could not read."""
    if isinstance(entry, Line):
        return entry
    if not isinstance(entry, Mapping) or set(entry) - {"stat", "threshold", "op", "period", "half"} or "stat" not in entry or "threshold" not in entry:
        raise ScopeError(f"scope period_condition {entry!r} is not a stat, a threshold and a period or half")
    period, half = entry.get("period"), entry.get("half")
    if (period is None) == (half is None):
        raise ScopeError(f"scope period_condition {entry!r} needs exactly one of period and half")
    where = Period(number=_whole("period_condition period", period)) if period is not None else Period(number=_one_of(1, 2)("period_condition half", half), half=True)
    return Line(
        measure=_text("period_condition stat", entry["stat"]),
        op=_one_of(">=", "=")("period_condition op", entry.get("op", ">=")),
        value=_whole("period_condition threshold", entry["threshold"]),
        period=where,
    )


def companion_from_slot(entry: Any) -> Companion:
    """One ``conditions`` slot entry, a dict, as a typed companion -
    raising on a shape the relation could not read."""
    if isinstance(entry, Companion):
        return entry
    if not isinstance(entry, Mapping) or set(entry) - {"player", "side", "predicate", "stat", "threshold"}:
        raise ScopeError(f"scope condition {entry!r} is not a player, side, predicate and line")
    player = entry.get("player")
    if not isinstance(player, str) or not player.strip():
        raise ScopeError(f"scope condition {entry!r} names no player")
    predicate = _one_of("played", "absent", "started", "bench", "reached")("condition predicate", entry.get("predicate", "played"))
    stat, threshold = entry.get("stat"), entry.get("threshold")
    line = None
    if predicate == "reached":
        # A reached role's line, as the slot carried it: its stat may be a
        # word no column answers to (the relation refuses such a line by
        # name), but a line with no stat or no number is no line at all.
        if stat is None or threshold is None:
            raise ScopeError(f"scope condition {entry!r} reaches a line with no stat or no threshold")
        line = Line(measure=_text("condition stat", stat), value=_whole("condition threshold", threshold))
    return Companion(player=player, side=_one_of("own", "opponent")("condition side", entry.get("side", "own")), predicate=predicate, line=line)
