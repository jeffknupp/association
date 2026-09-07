"""Tests for per-question prompt assembly and the preamble budget check.

The bug these exist for: SYSTEM_PROMPT + TOOLS reached 10,295 tokens against a
NUM_CTX of 8192, ollama truncated head-first and silently, and only 4,098
tokens reached the model - discarding the schema summary, both standing rules,
and the first ~15 KNOWLEDGE_BASE entries. It stayed silent for four commits.
"""

import json

import pytest

from association.query.prompt import (
    ALWAYS_ON_TOPICS,
    KNOWLEDGE_BASE,
    MAX_SELECTED_ENTRIES,
    NUM_CTX,
    PREAMBLE_TOKEN_BUDGET,
    TOOLS,
    PreambleTooLarge,
    build_system_prompt,
    estimate_tokens,
    select_knowledge,
)


def _topics(question: str) -> list[str]:
    return [e["topic"] for e in select_knowledge(question)]


def test_every_assembled_prompt_fits_the_budget() -> None:
    questions = [
        "",
        "How many points did Jokic score in the 3rd quarter against Boston?",
        "Compare Luka and SGA this season",
        "How far was Curry's average three pointer?",
        "What was the Lakers record on April 12?",
        "Who had the most triple doubles among qualified players?",
    ]
    for question in questions:
        total = estimate_tokens(build_system_prompt(question)) + estimate_tokens(json.dumps(TOOLS))
        assert total <= PREAMBLE_TOKEN_BUDGET, f"{question!r} assembled to ~{total} tokens"


def test_budget_leaves_real_room_for_the_conversation() -> None:
    # A prompt under num_ctx is evaluated in full; one over it is cut to about
    # half, silently. The preamble must leave room for tool results on top.
    assert PREAMBLE_TOKEN_BUDGET < NUM_CTX // 2


def test_an_oversized_preamble_raises_instead_of_being_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("association.query.prompt.PREAMBLE_TOKEN_BUDGET", 10)
    with pytest.raises(PreambleTooLarge, match="truncate"):
        build_system_prompt("who leads the league in points?")


def test_always_on_entries_are_present_for_any_question() -> None:
    prompt = build_system_prompt("anything at all")
    for topic in ALWAYS_ON_TOPICS:
        assert topic in prompt


def test_always_on_entries_are_never_double_included() -> None:
    for topic in ALWAYS_ON_TOPICS:
        assert topic not in _topics("season current run_sql ids aliases player team")


def test_selection_picks_the_entry_a_question_actually_needs() -> None:
    assert "Points scored in a specific quarter/period" in _topics("How many points did Jokic score in the 3rd quarter?")
    assert "Shot distance / shot location math" in _topics("How far away was Curry's average three?")
    assert "Filtering by an exact calendar date" in _topics("What happened on April 12?")
    assert "Double-double / triple-double definitions" in _topics("Who had the most triple doubles?")


def test_selection_is_capped() -> None:
    assert len(select_knowledge(" ".join(e["topic"] for e in KNOWLEDGE_BASE))) <= MAX_SELECTED_ENTRIES


def test_an_unrelated_question_selects_nothing_rather_than_filler() -> None:
    assert select_knowledge("hello") == []


def test_selection_is_deterministic() -> None:
    question = "How many points did Jokic score in the 3rd quarter against Boston?"
    assert _topics(question) == _topics(question)


def test_the_schema_summary_survives_assembly() -> None:
    # This is what truncation discarded first, leaving the model with the tool
    # schemas and no schema at all.
    assert "player_box_stats" in build_system_prompt("anything")
    assert "one row per player PER GAME" in build_system_prompt("anything")


def test_keywords_are_never_the_only_content_of_an_entry() -> None:
    for entry in KNOWLEDGE_BASE:
        assert entry["note"], entry["topic"]
        assert isinstance(entry.get("keywords", []), list)
