"""Every play ``type`` ESPN has used, classified into one ``kind``.

The 164 types below are the complete vocabulary of ``plays`` from 2002 to
2026, measured 2026-10-11 (``SIMULATION_PLAN.md`` section 0b, check 0.1;
``scripts/sim_phase0/results/types_by_season.csv`` has them per season, and
``tests/sim/test_vocab.py`` holds this table to that file). Every type is
either classified or explicitly ``ignore``d; a type ESPN adds later maps to
nothing, is counted by the build, and fails it above
:data:`UNMAPPED_LIMIT` (``plays.build_plays``), so a new type is noticed
rather than silently dropped.

Three rules the classification follows, each measured in the main project
and recorded in ``DATA.md`` ("A play's type does not always say what the box
score counts it as"):

- **A shot is named positively**, by the list of shot types, never by
  "not a free throw and not a foul": several real shot types contain no word
  "Shot" (``Driving Finger Roll Layup``, ``Layup Driving Reverse``,
  ``Dunk Putback Slam``), and a ``'%Shot%'`` filter scored 88.3% on attempts
  against 99.5% for the positive list (``fetch/repairs/reconstructed_box``).
  :func:`shot_types_match_the_positive_rule` is the cross-check a test runs.
- **Fouls whose type never says foul** - ``Shooting Block`` (2005-2018),
  ``Personal Block`` (2011-2017), ``Offensive Charge``, the take fouls - and
  ``Traveling``, a turnover whose type never says turnover, are classified by
  what they are, as ``query.player_games._PERIOD_FOUL`` and
  ``_PERIOD_TURNOVER`` already do.
- **Four types mean different things in different rows**, and only the
  text or the scoring flag says which: ``Rebound`` (generic, 2013-2017),
  ``No Turnover`` (a real turnover in 2018-2020, an overturned call in
  2013-2017), ``No Ejection`` (the text says "ejected") and ``Not Available``
  (a foul, a turnover, a rebound, or 2016's 1,334 unclassifiable scoring
  plays). They are :data:`TEXT_RESOLVED` here and resolved row by row in
  ``plays.build_plays``'s SQL.

Two flags ride with the kind. ``bench_possible`` marks a type a player can
appear in while NOT on the floor, which the lineup walk (Phase 2) must not
read as presence: measured as touches by a player with no minutes in the box
score (check 0.5), the technical, ejection and delay types are the only
ones above 0.1%, and none is above 2.2%, so the flag errs on the side of
not trusting them. ``live_ball`` is set on turnover types where the words
settle it: a bad pass or a lost ball is live (the possession can be run
out), everything typed out of bounds, a violation or an offensive foul is
dead; ``None`` where the type does not say.

.. versionadded:: 6.0.0
"""

from __future__ import annotations

from dataclasses import dataclass

UNMAPPED_LIMIT = 0.0005
"""The share of a season's plays that may map to no kind before the build
fails: 0.05%, about 300 plays of a 600,000-play season.

.. versionadded:: 6.0.0
"""

KINDS: frozenset[str] = frozenset(
    {
        "shot",
        "free_throw",
        "offensive_rebound",
        "defensive_rebound",
        "rebound",  # the generic 2013-2017 type whose text names neither side
        "turnover",
        "foul_personal",
        "foul_shooting",
        "foul_offensive",
        "foul_technical",
        "foul_flagrant",
        "substitution",
        "timeout",
        "jump_ball",
        "violation",
        "ejection",
        "period_start",
        "period_end",
        "game_end",
        "review",
        "unknown_score",  # a scoring play no shot or free-throw type names (derived, never mapped directly)
        "ignore",
    }
)
"""Every kind a play can be. ``unknown_score`` is written only by the build,
for a scoring play whose type is not a shot or a free throw.

.. versionadded:: 6.0.0
"""


@dataclass(frozen=True)
class PlayKind:
    """One type's classification: its kind and the two flags.

    .. versionadded:: 6.0.0
    """

    kind: str
    bench_possible: bool = False
    live_ball: bool | None = None


_SHOT_TYPES = (
    "Jump Shot",
    "Layup Shot",
    "Driving Layup Shot",
    "Pullup Jump Shot",
    "Step Back Jump Shot",
    "Dunk Shot",
    "Driving Floating Jump Shot",
    "Tip Shot",
    "Running Jump Shot",
    "Hook Shot",
    "Running Layup Shot",
    "Floating Jump Shot",
    "Driving Finger Roll Layup",
    "Fade Away Jump Shot",
    "Turnaround Jump Shot",
    "Cutting Layup Shot",
    "Layup Shot Putback",
    "Turnaround Fade Away Jump Shot",
    "Driving Dunk Shot",
    "Driving Floating Bank Jump Shot",
    "Cutting Dunk Shot",
    "Running Pullup Jump Shot",
    "Turnaround Hook Shot",
    "Layup Driving Reverse",
    "Running Dunk Shot",
    "Reverse Layup Shot",
    "Alley Oop Dunk Shot",
    "Driving Hook Shot",
    "Jump Shot Bank",
    "Running Finger Roll Layup",
    "Alley Oop Layup Shot",
    "Driving Jump Shot Bank",
    "Putback Dunk Shot",
    "Tip Dunk Shot",
    "Finger Roll Layup",
    "Cutting Finger Roll Layup Shot",
    "Pullup Bank Jump Shot",
    "Layup Running Reverse",
    "Turnaround Bank Jump Shot",
    "Running Alley Oop Dunk Shot",
    "Hook Driving Bank",
    "Hook Turnaround Bank",
    "Fade Away Bank Jump Shot",
    "Hook Shot Bank",
    "Reverse Dunk Shot",
    "Running Alley Oop Layup Shot",
    "Heave Jump Shot",  # 2026 only; a shot in the vocabulary, a heave by geometry and clock in sim/zones.py
    "Turnaround Fadeaway Bank Jump Shot",
    "Step Back Bank Jump Shot",
    "Dunk Putback Slam",
    "Driving Reverse Dunk Shot",
    "Running Reverse Dunk Shot",
    "Hook Jump Bank",
    "Hook Running Bank",
    "No Shot (Default Shot)",  # "Kyle Anderson makes 16-foot shot": a shot ESPN could not type
    "Dunk Putback Reverse",
)

_FREE_THROW_TYPES = (
    "Free Throw - 1 of 1",
    "Free Throw - 1 of 2",
    "Free Throw - 2 of 2",
    "Free Throw - 1 of 3",
    "Free Throw - 2 of 3",
    "Free Throw - 3 of 3",
    "Free Throw - Technical",
    "Free Throw - Flagrant 1 of 1",
    "Free Throw - Flagrant 1 of 2",
    "Free Throw - Flagrant 2 of 2",
    "Free Throw - Flagrant 1 of 3",
    "Free Throw - Flagrant 2 of 3",
    "Free Throw - Flagrant 3 of 3",
    "Free Throw - Clear Path 1 of 2",
    "Free Throw - Clear Path 2 of 2",
)

# A bad pass or a lost ball is a live-ball turnover unless the type says it
# went out of bounds; the "(X steals)" second participant confirms the live
# ones, which Phase 3 reads.
_LIVE_TURNOVER_TYPES = ("Bad Pass\nTurnover", "Lost Ball Turnover")

_DEAD_TURNOVER_TYPES = (
    "Offensive Foul Turnover",  # the turnover half of an offensive foul, logged beside `Offensive Foul`
    "Out of Bounds - Lost Ball Turnover",
    "Out of Bounds - Bad Pass Turnover",
    "Out of Bounds - Step Turnover",
    "Traveling",
    "Shot Clock Turnover",
    "3-Second Turnover",
    "Back Court Turnover",
    "Offensive Goaltending Turnover",
    "Disc Dribble Turnover",
    "Double Dribble Turnover",
    "Palming Turnover",
    "Lane Violation Turnover",
    "Inbound Turnover",
    "Kicked Ball Turnover",
    "5-Second Turnover",
    "8-Second Turnover",
    "10 Second Turnover",
    "5-Second Back to the Basket Turnover",
    "Illegal Screen Turnover",
    "Illegal Assist Turnover",
    "Jumpball Violation Turnover",
    "Illegal Pick Turnover",
    "Basket from Below Turnover",
    "Excess Timeout Turnover",
    "Punched Ball Turnover",
    "Swinging Elbows Turnover",
    "Too Many Players Turnover",
    "Opposite Basket Turnover",
    "Isolation Violation Turnover",
)

_PERSONAL_FOUL_TYPES = (
    "Personal Foul",
    "Loose Ball Foul",
    "Personal Take Foul",
    "Away from Play Foul",
    "Inbound Foul",
    "Clear Path Foul",
    "Transition Take Foul",
    "Shooting Block",  # "Ryan Hollins foul (Kevin Garnett draws the foul)", 2005-2018: a foul, DATA.md
    "Personal Block",  # 2011-2017, the same
    "Punching Foul",
    "Elbow Foul",
    "Double Personal Foul",
)

_OFFENSIVE_FOUL_TYPES = ("Offensive Foul", "Offensive Charge")

# bench_possible: a technical can be called on a player on the bench, and
# often is. `Defensive 3-Seconds Technical` is an illegal-defense call on a
# player who is on the floor, so it is not flagged.
_TECHNICAL_FOUL_TYPES = (
    "Technical Foul",
    "Double Technical Foul",
    "Delay Technical",
    "Hanging Technical Foul",
    "Taunting Technical Foul",
    "Non-Unsportsmanlike Technical",
    "Excess Timeout Technical",
    "Too Many Players Technical",
    "Flopping Technical",
)

_FLAGRANT_FOUL_TYPES = ("Flagrant Foul Type 1", "Flagrant Foul Type 2")

_TIMEOUT_TYPES = ("Full Timeout", "Short Timeout", "Official Time Out", "OfficialTVTimeOut")

_VIOLATION_TYPES = ("Kicked Ball", "Defensive Goaltending", "Lane", "Double Lane")

_REVIEW_TYPES = (
    "Coach's Challenge (Overturned)",
    "Coach's Challenge (Stands)",
    "Coach's Challenge (Supported)",
    "Coach's Challenge (replaycenter)",
    "Challenge",
    "Ref-Initiated Review (Supported)",
    "Ref-Initiated Review (Overturned)",
    "Ref-Initiated Review (Stands)",
)

# Types that record no event of play: a call that was not made, or a marker
# ESPN writes for its own display.
_IGNORED_TYPES = (
    "No Foul",  # 256 rows, 2018-2026, text "foul": too few and too ambiguous to count
    "No Timeout",
    "No Violation",
)

TEXT_RESOLVED: frozenset[str] = frozenset({"Rebound", "No Turnover", "No Ejection", "Not Available"})
"""Types whose kind depends on the row's text or scoring flag; the build's SQL
resolves each (``plays.TEXT_RESOLUTION_SQL``). The provisional kind in
:data:`PLAY_KINDS` is what the type most often is.

.. versionadded:: 6.0.0
"""


def _table() -> dict[str, PlayKind]:
    kinds: dict[str, PlayKind] = {}
    for types, play_kind in (
        (_SHOT_TYPES, PlayKind("shot")),
        (_FREE_THROW_TYPES, PlayKind("free_throw")),
        (("Offensive Rebound",), PlayKind("offensive_rebound")),
        (("Defensive Rebound",), PlayKind("defensive_rebound")),
        (("Rebound",), PlayKind("rebound")),
        (_LIVE_TURNOVER_TYPES, PlayKind("turnover", live_ball=True)),
        (_DEAD_TURNOVER_TYPES, PlayKind("turnover", live_ball=False)),
        (("No Turnover",), PlayKind("turnover")),
        (("Shooting Foul",), PlayKind("foul_shooting")),
        (_PERSONAL_FOUL_TYPES, PlayKind("foul_personal")),
        (_OFFENSIVE_FOUL_TYPES, PlayKind("foul_offensive")),
        (_TECHNICAL_FOUL_TYPES, PlayKind("foul_technical", bench_possible=True)),
        (("Defensive 3-Seconds Technical",), PlayKind("foul_technical")),
        (_FLAGRANT_FOUL_TYPES, PlayKind("foul_flagrant")),
        (("Substitution",), PlayKind("substitution")),
        (_TIMEOUT_TYPES, PlayKind("timeout")),
        (("Jump Ball", "Jumpball"), PlayKind("jump_ball")),
        (_VIOLATION_TYPES, PlayKind("violation")),
        (("Delay of Game",), PlayKind("violation", bench_possible=True)),
        (("Ejection", "No Ejection"), PlayKind("ejection", bench_possible=True)),
        (("Start Period",), PlayKind("period_start")),
        (("End Period",), PlayKind("period_end")),
        (("End Game",), PlayKind("game_end")),
        (_REVIEW_TYPES, PlayKind("review")),
        (("Not Available",), PlayKind("ignore")),
        (_IGNORED_TYPES, PlayKind("ignore")),
    ):
        for type_ in types:
            if type_ in kinds:
                raise ValueError(f"play type classified twice: {type_!r}")
            kinds[type_] = play_kind
    return kinds


PLAY_KINDS: dict[str, PlayKind] = _table()
"""Every known play type and its classification.

.. versionadded:: 6.0.0
"""


def kind_of(type_: str | None) -> PlayKind | None:
    """The classification of a play type, or ``None`` for one not in the
    vocabulary (which the build counts as unmapped).

    .. versionadded:: 6.0.0
    """
    if type_ is None:
        return None
    return PLAY_KINDS.get(type_)


def shot_types_match_the_positive_rule() -> tuple[set[str], set[str]]:
    """Cross-check the shot list against ``reconstructed_box``'s rule over
    every type in the vocabulary: the types the rule names that this table
    does not call a shot, and the ones this table calls a shot that the rule
    does not. Both sets are empty when the two agree; a test holds them so.

    .. versionadded:: 6.0.0
    """

    def _rule(t: str) -> bool:
        low = t.lower()
        named = any(w in low for w in ("shot", "layup", "dunk", "jumper", "hook", "tip", "finger roll"))
        excluded = low.startswith("free throw") or any(w in low for w in ("turnover", "foul", "block"))
        return named and not excluded

    by_rule = {t for t in PLAY_KINDS if _rule(t)}
    by_table = {t for t, k in PLAY_KINDS.items() if k.kind == "shot"}
    return by_rule - by_table, by_table - by_rule
