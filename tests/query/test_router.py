"""Tests for the intent router's validation layer - the part that decides what
the model is and is not trusted to have gotten right."""

import re
from typing import Any
from unittest.mock import patch

import ollama
import pytest
from ollama import ChatResponse, Message

from association.query.router import ROUTER_PROMPT, ROUTER_SCHEMA, Route, route
from association.season import current_season


def _reply(payload: str) -> ChatResponse:
    return ChatResponse(model="m", message=Message(role="assistant", content=payload))


def _route(payload: str, **kwargs: Any) -> Route | None:
    with patch("association.query.router.ollama.chat", return_value=_reply(payload)):
        return route("m", "q", **kwargs)


def _routed(payload: str, **kwargs: Any) -> Route:
    """_route for the cases that must produce a Route - asserts rather than
    leaving every caller to narrow away the None."""
    got = _route(payload, **kwargs)
    assert got is not None
    return got


def test_parses_intent_and_slots() -> None:
    got = _route('{"intent":"threshold_count","stat":"points","threshold":30}')
    assert got == Route(intent="threshold_count", slots={"stat": "points", "threshold": 30, "season_type": 2})


def test_season_type_defaults_to_regular_season() -> None:
    assert _routed('{"intent":"leaderboard","stat":"points"}').slots["season_type"] == 2


def test_playoffs_maps_to_the_numeric_season_type_every_table_uses() -> None:
    # Without this a playoff question silently answers for the regular season.
    assert _routed('{"intent":"leaderboard","stat":"points","season_type":"playoffs"}').slots["season_type"] == 3


def test_unknown_season_type_falls_back_to_regular_season() -> None:
    assert _routed('{"intent":"leaderboard","season_type":"summer league"}').slots["season_type"] == 2


def test_explicit_season_year_is_kept() -> None:
    got = _route('{"intent":"threshold_count","season":2024}')
    assert got is not None and got.slots["season"] == 2024


def test_nonsense_season_is_dropped_not_passed_to_sql() -> None:
    # Confirmed live: "last season" once produced season=20222023, which as a
    # SQL filter would silently match nothing and answer with an empty result.
    got = _route('{"intent":"threshold_count","season":20222023}')
    assert got is not None and "season" not in got.slots


def test_season_ref_is_resolved_in_code_not_by_the_model() -> None:
    assert _routed('{"intent":"threshold_count","season_ref":"current"}').slots["season"] == current_season()
    assert _routed('{"intent":"threshold_count","season_ref":"previous"}').slots["season"] == current_season() - 1


def test_season_ref_never_leaks_through_as_a_slot() -> None:
    got = _route('{"intent":"threshold_count","season_ref":"current"}')
    assert got is not None and "season_ref" not in got.slots


def test_bad_season_falls_back_to_season_ref() -> None:
    got = _route('{"intent":"threshold_count","season":20222023,"season_ref":"previous"}')
    assert got is not None and got.slots["season"] == current_season() - 1


def test_unparseable_reply_returns_none_to_fall_through() -> None:
    assert _route("not json at all") is None


def test_missing_intent_returns_none_to_fall_through() -> None:
    assert _route('{"stat":"points"}') is None


def test_unreachable_model_returns_none_rather_than_raising() -> None:
    with patch("association.query.router.ollama.chat", side_effect=ollama.ResponseError("down")):
        assert route("m", "q") is None


def test_previous_question_is_passed_as_context_for_repl_followups() -> None:
    with patch("association.query.router.ollama.chat", return_value=_reply('{"intent":"other"}')) as chat:
        route("m", "what about 2025?", previous_question="who led in points?")
    user_message = chat.call_args.kwargs["messages"][1]["content"]
    assert "who led in points?" in user_message and "what about 2025?" in user_message


def test_schema_constrains_intent_to_the_known_set() -> None:
    assert "other" in ROUTER_SCHEMA["properties"]["intent"]["enum"]
    assert "intent" in ROUTER_SCHEMA["required"]


@pytest.mark.parametrize("payload", ['{"intent":"other"}', '{"intent":"leaderboard","limit":10}'])
def test_unported_intents_still_parse_cleanly(payload: str) -> None:
    assert _route(payload) is not None


def test_blank_required_stat_is_dropped_rather_than_passed_along() -> None:
    """`stat` is required in the schema so the decoder actually considers it;
    a question with no stat answers with "", which must not reach a template."""
    got = _route('{"intent":"player_stat","stat":"","player":"Nikola Jokic"}')
    assert got is not None and "stat" not in got.slots and got.slots["player"] == "Nikola Jokic"


def test_schema_requires_stat_so_the_decoder_emits_it() -> None:
    assert "stat" in ROUTER_SCHEMA["required"]
    assert ROUTER_SCHEMA["additionalProperties"] is False


def test_explicit_year_still_wins_over_a_required_season_ref() -> None:
    """`season_ref` is required so the model always makes a relative-season
    decision; a named year must still override it."""
    got = _route('{"intent":"leaderboard","stat":"points","season":2024,"season_ref":"current"}')
    assert got is not None and got.slots["season"] == 2024


def test_schema_requires_only_the_slot_that_pays_for_itself() -> None:
    """Requiring season_ref as well was measured and reverted - it crowded out
    other slots and started dropping explicitly named years."""
    assert ROUTER_SCHEMA["required"] == ["intent", "stat"]


def test_every_intent_the_prompt_describes_is_emittable() -> None:
    """Regression: player_compare was added to the prompt and given a slot, but
    not to the schema enum - so constrained decoding could never emit it, and
    every comparison silently routed to player_stat instead."""
    # An intent line is `  name  - description`; wrapped continuation lines are
    # indented further and must not be mistaken for intent names.
    described = set(re.findall(r"^  (\w+)\s+- ", ROUTER_PROMPT, re.MULTILINE))
    assert described, "no intents parsed out of the prompt"
    assert described <= set(ROUTER_SCHEMA["properties"]["intent"]["enum"])


def test_every_ported_template_has_an_intent_in_the_schema() -> None:
    from association.query.templates import TEMPLATES

    assert set(TEMPLATES) <= set(ROUTER_SCHEMA["properties"]["intent"]["enum"])


def test_array_slots_are_bounded() -> None:
    """An unbounded array is a generation-length hazard under constrained
    decoding: the grammar permits "one more item" forever, and at ~10 tok/s on
    CPU a looping array stalls a call for minutes (confirmed live)."""
    for name in ("players", "fields"):
        schema = ROUTER_SCHEMA["properties"][name]
        assert schema["type"] == "array"
        assert schema.get("maxItems"), f"{name} has no maxItems"


def test_question_text_beats_a_dropped_season_slot() -> None:
    """The model omits the season on "...last season" often enough that
    deferring to it silently answered for the current season."""
    got = _route('{"intent":"leaderboard","stat":"ts_pct"}')
    assert got is not None and "season" not in got.slots
    with patch("association.query.router.ollama.chat", return_value=_reply('{"intent":"leaderboard","stat":"ts_pct"}')):
        got = route("m", "Best true shooting percentage last season?")
    assert got is not None and got.slots["season"] == current_season() - 1


def test_question_text_beats_a_wrong_season_slot() -> None:
    with patch("association.query.router.ollama.chat", return_value=_reply('{"intent":"leaderboard","season":2019}')):
        got = route("m", "who led the league in 2024?")
    assert got is not None and got.slots["season"] == 2024


def test_the_model_slot_still_applies_when_the_text_names_no_season() -> None:
    """Phrasings the parser has never seen must route as well as before."""
    with patch("association.query.router.ollama.chat", return_value=_reply('{"intent":"leaderboard","season":2021}')):
        got = route("m", "who led in his rookie year?")
    assert got is not None and got.slots["season"] == 2021


@pytest.mark.parametrize(
    "question",
    [
        "How many points did Jokic score in the 3rd quarter?",
        "points per quarter for Luka",
    ],
)
def test_questions_no_template_computes_are_forced_to_the_agent(question: str) -> None:
    """These read like a supported shape while asking for something no template
    computes. Shot distance was here too until it earned its own template,
    which is the intended lifecycle for this list."""
    with patch("association.query.router.ollama.chat", return_value=_reply('{"intent":"player_stat","player":"Stephen Curry"}')):
        got = route("m", question)
    assert got is not None and got.intent == "other"


def test_an_ordinary_question_is_not_forced_to_the_agent() -> None:
    with patch("association.query.router.ollama.chat", return_value=_reply('{"intent":"player_stat","player":"Stephen Curry"}')):
        got = route("m", "how many points does Curry average?")
    assert got is not None and got.intent == "player_stat"


@pytest.mark.parametrize(
    "question",
    [
        "How many times has Wembanyama fouled out of a game",
        "how often does Embiid foul out?",
        "games where Jokic fouled out",
    ],
)
def test_fouling_out_is_normalized_to_six_fouls(question: str) -> None:
    """Six personal fouls is an NBA rule, not a judgement call. Confirmed live:
    the router got the shape right but emitted stat "fouls committed" with
    threshold 1, and the question then hung in the agent until it was aborted."""
    with patch("association.query.router.ollama.chat", return_value=_reply('{"intent":"player_stat","stat":"fouls committed","threshold":1}')):
        got = route("m", question)
    assert got is not None and got.intent == "threshold_count"
    assert got.slots["stat"] == "fouls" and got.slots["threshold"] == 6


def test_an_ordinary_foul_question_is_not_rewritten() -> None:
    with patch("association.query.router.ollama.chat", return_value=_reply('{"intent":"leaderboard","stat":"fouls"}')):
        got = route("m", "who commits the most fouls?")
    assert got is not None and got.intent == "leaderboard" and "threshold" not in got.slots
