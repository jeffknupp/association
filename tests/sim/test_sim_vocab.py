"""The play-type vocabulary covers every type ESPN has used, and names shots
the way the measured rule does."""

import csv
from pathlib import Path

from association.sim import vocab

_TYPES_BY_SEASON = Path(__file__).resolve().parents[2] / "scripts" / "sim_phase0" / "results" / "types_by_season.csv"


def test_every_measured_type_is_classified() -> None:
    """Phase 0 check 0.1 listed the 164 types in the warehouse; a type missing
    here would be counted as unmapped by every build."""
    with _TYPES_BY_SEASON.open(newline="") as f:
        measured = {row["type"].replace("\\n", "\n") for row in csv.DictReader(f)}
    assert measured, "the Phase 0 vocabulary file is empty"
    assert measured - set(vocab.PLAY_KINDS) == set()
    assert set(vocab.PLAY_KINDS) - measured == set(), "a type classified that the warehouse has never served"


def test_every_kind_is_one_of_the_declared_kinds() -> None:
    assert {k.kind for k in vocab.PLAY_KINDS.values()} <= vocab.KINDS
    # A kind the build derives is never assigned to a type directly.
    assert "unknown_score" not in {k.kind for k in vocab.PLAY_KINDS.values()}


def test_the_shot_list_agrees_with_the_positive_rule() -> None:
    """``reconstructed_box``'s rule (named words, minus free throws, turnovers,
    fouls and blocks) scored 99.5% on attempts; the explicit list must name
    exactly the same types, or one of them is wrong."""
    assert vocab.shot_types_match_the_positive_rule() == (set(), set())


def test_the_text_resolved_types_are_in_the_table_and_the_flags_are_where_measured() -> None:
    assert set(vocab.PLAY_KINDS) >= vocab.TEXT_RESOLVED
    assert vocab.kind_of("Technical Foul") == vocab.PlayKind("foul_technical", bench_possible=True)
    assert vocab.kind_of("Defensive 3-Seconds Technical") == vocab.PlayKind("foul_technical")  # an on-floor call
    assert vocab.kind_of("Bad Pass\nTurnover") == vocab.PlayKind("turnover", live_ball=True)
    assert vocab.kind_of("Traveling") == vocab.PlayKind("turnover", live_ball=False)
    assert vocab.kind_of("Shooting Block") == vocab.PlayKind("foul_personal")
    assert vocab.kind_of("A Type ESPN Invents Next Year") is None
    assert vocab.kind_of(None) is None
