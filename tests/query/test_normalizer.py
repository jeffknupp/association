"""The normalizer (query/normalizer.py): the model's whole job once the
parser reads the question - names copied verbatim and one stat key."""

from __future__ import annotations

import json
import re
from unittest.mock import patch

import ollama
import pytest
from ollama import ChatResponse, Message

from association.query.normalizer import NORMALIZER_NUM_CTX, NORMALIZER_PROMPT, NORMALIZER_SCHEMA, NORMALIZER_STATS, Normalized, normalize
from association.query.prompt import estimate_tokens
from association.query.router import RouterUnavailable


def _normalize(payload: str) -> Normalized | None:
    reply = ChatResponse(model="m", message=Message(role="assistant", content=payload))
    with patch("association.query.normalizer.ollama.chat", return_value=reply):
        return normalize("m", "q")


def test_names_come_back_as_typed_and_the_stat_as_the_key() -> None:
    assert _normalize('{"names": ["embid", " Maxey "], "stat": "points"}') == Normalized(["embid", "Maxey"], "points")


def test_a_stat_outside_the_list_and_blank_names_are_dropped() -> None:
    """The enum holds under constrained decoding; this is the check for a
    server that did not honor it, so a key nothing reads never arrives."""
    assert _normalize('{"names": ["", 3, "sga"], "stat": "ppg"}') == Normalized(["sga"], "")


def test_an_unusable_reply_is_none_and_an_absent_server_says_so() -> None:
    assert _normalize("not json") is None
    assert _normalize('["embiid"]') is None
    with patch("association.query.normalizer.ollama.chat", side_effect=ollama.ResponseError("down")), pytest.raises(RouterUnavailable, match="could not serve"):
        normalize("m", "q")
    with patch("association.query.normalizer.ollama.chat", side_effect=ConnectionError("refused")), pytest.raises(RouterUnavailable, match="not answering"):
        normalize("m", "q")


def test_the_prompt_and_the_schema_agree() -> None:
    """Every example's stat is one the schema can emit - the router's prompt
    and schema disagreeing is how ``player_compare`` became unreachable."""
    assert NORMALIZER_SCHEMA["properties"]["stat"]["enum"] == list(NORMALIZER_STATS)
    examples = [json.loads(found) for found in re.findall(r"-> (\{.*\})", NORMALIZER_PROMPT)]
    assert len(examples) == 5
    assert all(example["stat"] in NORMALIZER_STATS and set(example) == {"names", "stat"} for example in examples)


def test_the_schema_asks_for_the_names_and_the_stat_and_requires_both() -> None:
    """A slot the schema does not require is one the decoder may never
    consider, and no prompt wording fixes that (AGENTS.md) - so both are
    required. And nothing else is asked for: no intent and no slot a grammar
    reads from the words, so there is no enum an intent could be parked in
    and no slot that crowds out another, the two lessons the router's schema
    taught (its tests went with it in 4.5.0)."""
    assert set(NORMALIZER_SCHEMA["properties"]) == {"names", "stat"}
    assert NORMALIZER_SCHEMA["required"] == ["names", "stat"]


def test_the_prompt_leaves_room_for_the_question_and_the_reply() -> None:
    """ollama truncates an over-length prompt head-first and silently, and this
    prompt, like the router's before it, has no per-question assembly step to
    raise at the way the agent's ``PreambleTooLarge`` does. It is a constant,
    so this is the guard: the prompt plus a long question may cost no more
    than three quarters of ``NORMALIZER_NUM_CTX``, measured at the same ~4
    characters a token as the agent's budget; the quarter left holds the chat
    template and a reply of a few dozen tokens of JSON."""
    long_question = "what was the record of the los angeles lakers against the boston celtics at home in the 2024 regular season, and how many games did they win by ten or more points? " * 2
    cost = estimate_tokens(NORMALIZER_PROMPT) + estimate_tokens(f"Q: {long_question}")
    assert cost <= NORMALIZER_NUM_CTX * 3 // 4, f"the normalizer's prompt plus a long question is ~{cost} tokens, over three quarters of NORMALIZER_NUM_CTX ({NORMALIZER_NUM_CTX})"
