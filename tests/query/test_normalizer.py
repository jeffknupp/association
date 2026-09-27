"""The normalizer (query/normalizer.py): the model's whole job once the
parser reads the question - names copied verbatim and one stat key."""

from __future__ import annotations

import json
import re
from unittest.mock import patch

import ollama
import pytest
from ollama import ChatResponse, Message

from association.query.normalizer import NORMALIZER_PROMPT, NORMALIZER_SCHEMA, NORMALIZER_STATS, Normalized, normalize
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
