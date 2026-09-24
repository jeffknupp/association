"""Tests for the intent router's validation layer - the part that decides what
the model is and is not trusted to have gotten right."""

import re
from typing import Any, cast
from unittest.mock import patch

import ollama
import pytest
from ollama import ChatResponse, Message

from association.nba.season import current_season
from association.query.prompt import estimate_tokens
from association.query.router import CODE_ASSIGNED_INTENTS, ORDER_INTENTS, ORDER_WORDS, SIDE_VALUES, Route, RouterUnavailable, route
from association.query.router_prompt import ROUTER_NUM_CTX, ROUTER_PROMPT, ROUTER_PROMPT_TOKEN_BUDGET, ROUTER_SCHEMA
from association.query.templates import TEMPLATES, TemplateContext


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
    # Which one it is comes from the question, not the model - see the scoping
    # tests at the end of this file for the measurements behind that.
    assert _ask("Who led the league in scoring in the playoffs?", '{"intent":"leaderboard","stat":"points","season_type":"playoffs"}').slots["season_type"] == 3


def test_unknown_season_type_falls_back_to_regular_season() -> None:
    assert _routed('{"intent":"leaderboard","season_type":"summer league"}').slots["season_type"] == 2


def test_a_bare_season_with_no_textual_support_is_dropped() -> None:
    """Superseded 2026-09-21 (#95): a model-supplied `season` integer used to
    be kept on the strength of the integer alone, whether or not the question
    named a year. Measured live: "show me stats for sixers when maxey scored
    20+ points" arrived with season=2023 - nothing in the text but "20+" - and
    answered a real player's real average for a season nobody asked about.
    ROUTER_PROMPT only ever asks the model to set `season` when the question
    names one (its own worked examples all have the year in the question
    text), so a value that survives with nothing in the text to back it is the
    model inventing one, not reading one - see
    test_question_text_beats_a_wrong_season_slot for the case where the text
    DOES name a year."""
    got = _route('{"intent":"threshold_count","season":2024}')
    assert got is not None and "season" not in got.slots


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


# #95, "the router invents a date or a season the question never states":
# three live examples, pinned by the router's own recorded slots
# (~/association-research/yardstick-v2/live_namerule.jsonl).
def test_router_no_longer_invents_a_season_for_a_bare_threshold_question() -> None:
    """Live: "show me stats for sixers when maxey scored 20+ points" arrived
    with season=2023 - nothing in the text but "20+" - and answered "Tyrese
    Maxey averaged 20.3 points per game ... in the 2023 regular season", a
    fluent wrong answer to a question that never named a year."""
    got = _ask(
        "show me stats for sixers when maxey scored 20+ points",
        '{"intent":"player_stat","stat":"points","player":"Maxey","season":2023,"season_type":2}',
    )
    assert "season" not in got.slots


def test_router_no_longer_invents_a_season_for_shot_distance() -> None:
    """Live: "what was steph curry's avg 3pt shot distance" arrived with
    season=2022, while two other wordings of the identical question answered
    2026 - the disagreement across phrasings was the tell that the value was
    invented rather than read."""
    got = _ask(
        "what was steph curry's avg 3pt shot distance",
        '{"intent":"shot_distance","stat":"points","player":"Stephen Curry","season":2022,"shot_value":3,"season_type":2}',
    )
    assert "season" not in got.slots


def test_router_no_longer_invents_a_date_for_a_season_named_fingerprint() -> None:
    """Live: "fingerprint maxey vs jaylen brown 2026" arrived with
    date='2026-01-01' and was refused ("not yet for a particular date") for a
    cause the question never gave - no day is named anywhere in it. The year
    IS named ("2026"), so `season` is kept; only the invented `date` drops."""
    got = _ask(
        "fingerprint maxey vs jaylen brown 2026",
        '{"intent":"fingerprint","stat":"maxey","player":"Maxey","side":"total","date":"2026-01-01","fields":["points","rebounds","assists","steals"],"season":2026,"season_type":2}',
    )
    assert "date" not in got.slots and got.slots.get("season") == 2026


def test_a_model_invented_date_with_no_calendar_day_in_the_question_is_dropped() -> None:
    """The date half of #95, on a garbage value rather than a plausible one:
    the recorded corpus row for "jamal murray career games on Tuesdays" carries
    date='TUESDAY' from the model, which is not a calendar day at all. Nothing
    in `_CALENDAR_DATE` matches the question, so the value is dropped exactly
    as a real-looking invented date is - see
    test_router_no_longer_invents_a_date_for_a_season_named_fingerprint for
    that case."""
    got = _ask("jamal murray career games on Tuesdays", '{"intent":"game_log","player":"Jamal Murray","date":"TUESDAY"}')
    assert "date" not in got.slots


def test_season_keep_cases_are_unaffected_by_the_invented_season_fix() -> None:
    """#95 changes only the branch that trusted a BARE model `season` int with
    nothing in the question to back it. These keep working exactly as before:
    a year the text names, "last season" (the model's own `season_ref`,
    resolved in code rather than read as text - see _validate_season), and an
    ordinal ("his 18th season", settled downstream by season_n once the player
    is known)."""
    # A year the question itself names.
    named = _ask("who led the league in 2024?", '{"intent":"leaderboard","season":2019}')
    assert named.slots["season"] == 2024
    # "last season".
    with patch("association.query.router.ollama.chat", return_value=_reply('{"intent":"leaderboard","stat":"ts_pct","season_ref":"previous"}')):
        last = route("m", "Best true shooting percentage last season?")
    assert last is not None and last.slots["season"] == current_season() - 1
    # An ordinal season: the misread year drops, season_n survives.
    ordinal = _ask(
        "how many 40+ points games does lebron james have in his 18th season?",
        '{"intent":"threshold_count","stat":"points","threshold":40,"player":"LeBron James","season":2018}',
    )
    assert ordinal.slots.get("season_n") == 18 and "season" not in ordinal.slots


def test_unparseable_reply_returns_none_to_fall_through() -> None:
    assert _route("not json at all") is None


def test_missing_intent_returns_none_to_fall_through() -> None:
    assert _route('{"stat":"points"}') is None


def test_an_unreachable_model_raises_rather_than_reading_as_a_bad_reply() -> None:
    """This asserted `route()` returns None for an unreachable model, on the
    rule that a router failure costs a round trip and never an answer. That
    rule still holds, one level up: `Agent._ask_inner` catches this and falls
    through exactly as it did - see
    test_a_router_that_could_not_be_asked_falls_through_saying_why.

    What changed is the sentence. Returning None here made "ollama cannot
    serve this model" indistinguishable from "the model replied with
    nonsense", and the caller reported the second: every question on a laptop
    without the router model pulled came back "the router returned no usable
    classification", which reads as a fault in the question."""
    with patch("association.query.router.ollama.chat", side_effect=ollama.ResponseError("down")), pytest.raises(RouterUnavailable):
        route("m", "q")


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
    decision; a year the QUESTION names must still override it. Rewritten
    2026-09-21 (#95) to actually name one - the original passed question="q"
    and pinned the bare `season` slot winning, which is the bug this entry
    fixes; see test_a_bare_season_with_no_textual_support_is_dropped."""
    got = _ask("who led the league in points in 2024", '{"intent":"leaderboard","stat":"points","season":2024,"season_ref":"current"}')
    assert got.slots["season"] == 2024


def test_season_ref_wins_when_the_question_names_no_year_at_all() -> None:
    """The mirror case, added alongside the #95 fix: with nothing in the text,
    the enum-bounded `season_ref` is still trusted - the model can only set it
    to "previous"/"current", never a free year, and ROUTER_PROMPT already
    instructs "current" for anything that does not say "last season" - but the
    bare `season` integer beside it is not."""
    got = _route('{"intent":"leaderboard","stat":"points","season":2024,"season_ref":"current"}')
    assert got is not None and got.slots["season"] == current_season()


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
    """Every template must be REACHABLE, by one of exactly two routes: the model
    emits its intent, or `route()` assigns it from the question text. A template
    in neither list is dead code that no question can ever reach."""
    from association.query.templates import TEMPLATES

    assert set(TEMPLATES) <= set(ROUTER_SCHEMA["properties"]["intent"]["enum"]) | CODE_ASSIGNED_INTENTS


def test_a_code_assigned_intent_is_kept_out_of_the_models_grammar() -> None:
    """The exemption above must not become a place to park intents the model
    should be emitting. These are the ones read from the question's own words,
    and adding them to the schema or the prompt would move slots on unrelated
    questions for no gain."""
    assert CODE_ASSIGNED_INTENTS.isdisjoint(ROUTER_SCHEMA["properties"]["intent"]["enum"])
    assert not any(intent in ROUTER_PROMPT for intent in CODE_ASSIGNED_INTENTS)


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


def test_the_model_slot_no_longer_applies_when_the_text_names_no_season() -> None:
    """Superseded 2026-09-21 (#95). This used to pin the opposite: "phrasings
    the parser has never seen must route as well as before", trusting the
    model's raw `season` whenever nothing in the text named one. Measured,
    that is the invented-season bug itself - "his rookie year" names no year
    in the text, exactly the shape of "show me stats for sixers when maxey
    scored 20+ points" (season=2023 from nothing but "20+"). The model's guess
    now drops and the template's own current-season default applies."""
    with patch("association.query.router.ollama.chat", return_value=_reply('{"intent":"leaderboard","season":2021}')):
        got = route("m", "who led in his rookie year?")
    assert got is not None and "season" not in got.slots


def _fingerprint(payload: str, question: str) -> Route:
    with patch("association.query.router.ollama.chat", return_value=_reply(payload)):
        got = route("m", question)
    assert got is not None
    return got


def test_question_text_beats_a_dropped_side_slot() -> None:
    """The measured failure, verbatim. `stat` is the one required slot, so a
    constrained decoder spends the adjective on stat="defensive" and omits
    `side` - 6/6 at temperature 0, for a question that appears in ROUTER_PROMPT
    as a worked example with the right answer beside it. Unset, the template
    draws the whole radar: a broader answer than the question asked for, with
    nothing saying so."""
    got = _fingerprint(
        '{"intent":"fingerprint","stat":"defensive","player":"Victor Wembanyama"}',
        "Show me Wembanyama's defensive fingerprint chart",
    )
    assert got.slots["side"] == "defense"


def test_the_offensive_half_is_recognized_too() -> None:
    got = _fingerprint('{"intent":"fingerprint","stat":"offensive","player":"Nikola Jokic"}', "plot Jokic's offensive fingerprint")
    assert got.slots["side"] == "offense"


def test_a_question_naming_neither_half_leaves_the_side_unset() -> None:
    """Absent means the whole radar, which is the template's own default - so
    this must not invent a side for a question that named none."""
    got = _fingerprint('{"intent":"fingerprint","stat":"","player":"Nikola Jokic"}', "plot Jokic's fingerprint")
    assert "side" not in got.slots


def test_a_question_naming_both_halves_leaves_the_side_unset() -> None:
    """Deliberately conservative, the same way override_nicknames is: naming
    both halves is a request for the whole radar, and guessing between them
    would be the same bug in the other direction."""
    got = _fingerprint(
        '{"intent":"fingerprint","stat":"","player":"Nikola Jokic"}',
        "compare Jokic's offensive and defensive fingerprint",
    )
    assert "side" not in got.slots


def test_the_model_side_slot_still_applies_when_the_text_names_neither() -> None:
    """Phrasings the patterns have never seen must route as well as before."""
    got = _fingerprint('{"intent":"fingerprint","stat":"","player":"Nikola Jokic","side":"defense"}', "plot Jokic's fingerprint on that end")
    assert got.slots["side"] == "defense"


def test_a_bogus_model_side_is_dropped_rather_than_passed_along() -> None:
    got = _fingerprint('{"intent":"fingerprint","stat":"","player":"Nikola Jokic","side":"sideways"}', "plot Jokic's fingerprint")
    assert "side" not in got.slots


def test_the_side_words_are_matched_whole() -> None:
    """ "Ant" matching every player with "ant" in their name is the same bug
    this file's neighbors guard against on the entity side."""
    got = _fingerprint('{"intent":"fingerprint","stat":"","player":"Cedi Osman"}', "plot Osman's fingerprint")
    assert "side" not in got.slots


def test_the_side_is_only_added_to_a_fingerprint() -> None:
    """`side` means nothing to any other template, so a defensive-sounding
    leaderboard question must not grow a slot nothing reads."""
    got = _fingerprint('{"intent":"leaderboard","stat":"rebounds"}', "who leads the league in defensive rebounds?")
    assert "side" not in got.slots


def test_the_side_values_match_the_router_schema() -> None:
    """Two hand-maintained lists of the same names is the shape that produced
    the player_compare bug - a value here the schema cannot emit would be
    unreachable, and one the schema emits that is missing here gets dropped."""
    assert set(SIDE_VALUES) == set(ROUTER_SCHEMA["properties"]["side"]["enum"])


@pytest.mark.parametrize("question", ["points per quarter for Luka", "Jokic points by quarter"])
def test_questions_no_template_computes_are_forced_to_the_agent(question: str) -> None:
    """These read like a supported shape while asking for something no template
    computes. Shot distance was here too until it earned its own template, and
    so was a named player's single quarter until `period_split` earned one -
    which is the intended lifecycle for this list. What is left here is the
    breakdown across ALL four quarters, which is a different shape."""
    with patch("association.query.router.ollama.chat", return_value=_reply('{"intent":"player_stat","player":"Stephen Curry"}')):
        got = route("m", question)
    assert got is not None and got.intent == "other"


def test_a_named_players_single_quarter_earned_its_own_template() -> None:
    """The case that used to sit in the list above. "How many points did Jokic
    score in the 3rd quarter?" was forced to the agent because nothing answered
    it; `period_split` does, by summing the value of his made shots in that
    period out of `shot_chart`."""
    with patch("association.query.router.ollama.chat", return_value=_reply('{"intent":"player_stat","player":"Nikola Jokic"}')):
        got = route("m", "How many points did Jokic score in the 3rd quarter?")
    assert got is not None and got.intent == "period_split" and got.slots["period"] == 3


def test_a_team_quarter_question_is_exempted_from_the_agent_only_override() -> None:
    """Regression: "how many points did the 76ers score in the 4th quarter
    against Boston this season?" tripped _AGENT_ONLY like any other "Nth
    quarter" question and was forced to the agent, which then spent 3 model
    calls (~150s) on SQL that filtered a nonexistent games.period column, a
    broken LAG() over play_id, and finally comparing home_team_id directly to
    an abbreviation. Unlike a PLAYER's quarter score, a TEAM's is answered
    exactly from games.home_linescores/away_linescores - templates.
    team_quarter_points - so this compound shape is exempted rather than
    routed to the agent."""
    payload = '{"intent":"team_quarter_points","team":"Philadelphia 76ers","period":4,"opponent":"Boston Celtics"}'
    with patch("association.query.router.ollama.chat", return_value=_reply(payload)):
        got = route("m", "How many points did the 76ers score in the 4th quarter against Boston this season?")
    assert got is not None and got.intent == "team_quarter_points"
    assert got.slots["team"] == "Philadelphia 76ers" and got.slots["opponent"] == "Boston Celtics"


def test_a_player_quarter_question_never_answers_from_the_teams_linescore() -> None:
    """Defensive: if the router emits team_quarter_points alongside a named
    player (it shouldn't - the prompt says that intent is never for a player),
    the override must still win rather than trust that slot combo. It now lands
    on `period_split`, which answers about the player, rather than on the
    team's linescore, which would answer about the 76ers."""
    payload = '{"intent":"team_quarter_points","team":"Philadelphia 76ers","period":4,"player":"Joel Embiid"}'
    with patch("association.query.router.ollama.chat", return_value=_reply(payload)):
        got = route("m", "How many points did Embiid score in the 4th quarter against Boston?")
    assert got is not None and got.intent == "period_split"


def test_a_dropped_player_still_reaches_period_split_over_a_team_only_reading() -> None:
    """ISSUES.md #170: "How many points did Jokic score in the 3rd quarter
    against Boston?" measured live against qwen2.5:3b came back with NO
    `player` at all - the model filled `team`/`opponent` instead
    (`team='Boston Celtics'`, `opponent='Denver Nuggets'`, neither one asked
    for by name) - which fit the "team's own half" shape exactly and routed to
    `team_quarter_points`, a template with no player column, for a question
    about one man. `scripts/check_routing.py` pinned this to `other` before
    `period_split` existed for the shape; now it answers it.

    The question's own grammar still names Jokic (`_subject_named_in`), so
    that recovers the player the same way it already does for
    `threshold_count`/`single_game_high`. Of the two team-shaped slots the
    model filled, `opponent` ('Denver Nuggets') names nobody the question
    wrote, and `team` ('Boston Celtics') does - so the surviving one lands in
    `opponent`, which `period_split` honors."""
    payload = '{"intent":"other","stat":"points","team":"Boston Celtics","opponent":"Denver Nuggets","season_ref":"current","order":"recent","limit":1}'
    got = _ask("How many points did Jokic score in the 3rd quarter against Boston?", payload)
    assert got.intent == "period_split"
    assert got.slots.get("player") == "Jokic"
    assert got.slots.get("period") == 3
    assert got.slots.get("opponent") == "Boston Celtics"
    assert "team" not in got.slots


def test_a_dropped_player_recovery_never_steals_a_teams_own_quarter_or_half() -> None:
    """The mirror check: a real team subject must not be misread as a
    "dropped player" just because it sits directly before a scoring verb -
    "did the 76ers score" fits `_subject_named_in`'s grammar exactly the way
    "did Jokic score" does, and only `_is_team_name` tells them apart."""
    payload = '{"intent":"other","team":"Philadelphia 76ers","opponent":"Boston Celtics"}'
    got = _ask("How many points did the 76ers score in the 3rd quarter against Boston?", payload)
    assert got.intent == "team_quarter_points"
    assert got.slots.get("team") == "Philadelphia 76ers"
    # The already-covered known gap above (a TEAM's half) must stay exactly as
    # it was - no grammar in "Celtics 2nd half scoring this season" reads as a
    # scoring verb, so nothing here is newly at risk of being read as a name.
    still_a_team = _ask("Celtics 2nd half scoring this season", '{"intent":"team_quarter_points","team":"Boston Celtics","period":2}')
    assert still_a_team.intent == "team_quarter_points" and still_a_team.slots.get("team") == "Boston Celtics"


def test_team_quarter_points_is_in_the_schema_enum() -> None:
    assert "team_quarter_points" in ROUTER_SCHEMA["properties"]["intent"]["enum"]


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
    """Six personal fouls is an NBA rule, not a judgment call. Confirmed live:
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


def _compare(question: str, payload: str = '{"intent":"player_compare","stat":"points","players":["Shai Gilgeous-Alexander","Joel Embiid"]}') -> Route:
    with patch("association.query.router.ollama.chat", return_value=_reply(payload)):
        got = route("m", question)
    assert got is not None
    return got


def test_a_comparison_that_named_no_stat_does_not_keep_one() -> None:
    """`stat` is required, so the decoder fills it whether or not the question
    named a stat - "compare sga and embiid" comes back with stat='points' 12
    times out of 12. Left in place it collapses the whole line player_compare
    exists to show back to one average."""
    assert "stat" not in _compare("compare sga and embiid").slots


def test_a_comparison_that_did_name_a_stat_keeps_it() -> None:
    """ "who scores more" is a question about scoring, and narrowing to it is
    the behavior that must survive."""
    assert _compare("who scores more, sga or embiid?").slots["stat"] == "points"
    assert _compare("compare sga and embiid on rebounding").slots["stat"] == "points"


def test_only_a_comparison_drops_an_unasked_stat() -> None:
    """`leaderboard` has nothing to rank by without it, and no question phrases
    every metric it means."""
    got = _compare("who led the league last season?", '{"intent":"leaderboard","stat":"points"}')
    assert got.slots["stat"] == "points"


def _asking(payload: str, question: str) -> Route:
    """_routed, but for the checks that read the question text rather than only
    the payload."""
    with patch("association.query.router.ollama.chat", return_value=_reply(payload)):
        got = route("m", question)
    assert got is not None
    return got


def test_a_router_that_could_not_be_asked_says_so_rather_than_blaming_the_question() -> None:
    """Reported from a laptop where the router model was not pulled: EVERY
    question came back "the router returned no usable classification", which
    reads as a fault in the question and sent the reader to look at it. The
    model was never asked at all. With --disable-fallthrough that sentence is
    the whole error, so it has to name the server and the model.

    Only an unusable REPLY is still "no usable classification" - that one is
    about what the model said, and route() keeps returning None for it so the
    question still falls through."""
    import json as _json

    missing = ollama.ResponseError("model 'qwen2.5:3b' not found")
    with patch("association.query.router.ollama.chat", side_effect=missing), pytest.raises(RouterUnavailable, match=re.escape("could not serve the router model 'qwen2.5:3b'")):
        route("qwen2.5:3b", "who leads the league in assists?")
    with patch("association.query.router.ollama.chat", side_effect=ConnectionError("refused")), pytest.raises(RouterUnavailable, match="ollama is not answering"):
        route("qwen2.5:3b", "who leads the league in assists?")
    with patch("association.query.router.ollama.chat", side_effect=_json.JSONDecodeError("bad", "", 0)):
        assert route("qwen2.5:3b", "who leads the league in assists?") is None


def test_question_text_beats_a_dropped_order_slot() -> None:
    """The measured failure, verbatim. ROUTER_PROMPT instructs `order` for
    game_log and shot_chart only, so a fingerprint question carries no
    instruction to fill it: "show me a fingerprint for steph curry's last game
    in 2026" came back with no `order` 3/3 at temperature 0. With the slot
    missing there was nothing for the template to refuse, so a question about
    one game was answered with the whole season's radar."""
    got = _asking('{"intent":"fingerprint","stat":"netpoints","player":"Stephen Curry","season":2026}', "show me a fingerprint for steph curry's last game in 2026")
    assert got.slots["order"] == "recent"


def test_the_other_end_of_the_season_is_recognized_too() -> None:
    got = _asking('{"intent":"fingerprint","stat":"netpoints","player":"Stephen Curry"}', "fingerprint for curry's first game of 2026")
    assert got.slots["order"] == "first"


def test_a_count_between_the_word_and_the_game_still_reads() -> None:
    got = _asking('{"intent":"game_log","player":"Stephen Curry"}', "curry's last 5 games")
    assert got.slots["order"] == "recent"


def test_a_question_about_a_whole_season_grows_no_order() -> None:
    """The cost of a false positive: this would narrow a season question to one
    game, which is the bug being fixed pointing the other way."""
    got = _asking('{"intent":"fingerprint","stat":"netpoints","player":"Stephen Curry","season":2026}', "show me a fingerprint for steph curry in 2026")
    assert "order" not in got.slots


@pytest.mark.parametrize(
    "question",
    [
        "show me last season's best game for curry",  # the best game OF last season
        "what was curry's best game in last year's playoffs",
        "how did curry do in a game last season",
    ],
)
def test_an_ordinal_attached_to_the_season_is_not_a_request_for_one_game(question: str) -> None:
    """Why the patterns cross at most a count between the word and "game".

    Allowing a word or two instead picks up four right phrasings and two wrong,
    and these are the wrong ones: the ordinal belongs to the SEASON, and
    matching it narrows a whole-season question to a single game with nothing
    saying so. The four it gives up cost only a fall back to the model's own
    slot, which fills them correctly often enough - see the test below.
    """
    got = _asking('{"intent":"game_log","player":"Stephen Curry"}', question)
    assert "order" not in got.slots


def test_the_model_order_slot_still_applies_when_the_text_uses_another_phrasing() -> None:
    """The patterns are tighter than the model's reading - "his last home game"
    is one they miss - so a slot the model filled is never overwritten or
    dropped."""
    got = _asking('{"intent":"shot_chart","player":"Stephen Curry","order":"recent"}', "curry's shot chart for his last home game")
    assert got.slots["order"] == "recent"


def test_a_bogus_model_order_is_not_trusted_as_a_phrasing_this_missed() -> None:
    got = _asking('{"intent":"game_log","player":"Stephen Curry","order":"sideways"}', "how did curry do this season")
    assert got.slots.get("order") != "sideways"


def test_a_single_game_asked_of_player_stat_carries_its_order_and_a_limit_of_one() -> None:
    """ "his last game" is one game at one end of the span: player_stat hands it
    to game_log, which needs BOTH slots - an order alone would list his last
    ten. The model emits neither reliably here, so route() sets the pair from
    the question's own words (#142)."""
    got = _asking('{"intent":"player_stat","stat":"points","player":"Stephen Curry"}', "how many points did curry score in his last game")
    assert (got.slots.get("order"), got.slots.get("limit")) == ("recent", 1)
    first = _asking('{"intent":"player_stat","stat":"points","player":"Stephen Curry"}', "curry's stats in his first game of 2026")
    assert (first.slots.get("order"), first.slots.get("limit")) == ("first", 1)
    # "last N games" is a count, not a single game: the count stays and this rule keeps out of it.
    many = _asking('{"intent":"player_stat","stat":"points","player":"Stephen Curry","limit":5}', "curry stats in his last 5 games")
    assert many.slots.get("limit") == 5


def test_a_filler_limit_on_player_stat_goes_whatever_its_size_when_the_question_names_no_count() -> None:
    """A limit on player_stat now hands the question to game_log, so a filler
    one no longer costs the answer - it answers a different question. "Portis
    vs bulls 2019-20 to 2023-24" arrived with limit=5 and became a three-game
    log where his averages were asked for. A year is not a count of games, and
    neither half of "2019-20" is."""
    got = _asking('{"intent":"player_stat","stat":"points","player":"Bobby Portis","limit":5}', "Portis vs bulls 2019-20 to 2023-24")
    assert "limit" not in got.slots
    real = _asking('{"intent":"player_stat","stat":"points","player":"Bobby Portis","limit":5}', "Portis vs bulls last 5 games")
    assert real.slots.get("limit") == 5
    # A count in another intent is that intent's business, not this rule's.
    top = _asking('{"intent":"leaderboard","stat":"points","limit":5}', "who led the league in scoring in 2024")
    assert top.slots.get("limit") == 5


def test_the_order_values_match_the_router_schema() -> None:
    """Same shape as the side check above: a value here the schema cannot emit
    would be unreachable, and one it emits that is missing here gets dropped."""
    assert set(ORDER_WORDS) == set(ROUTER_SCHEMA["properties"]["order"]["enum"])


def test_the_order_intents_are_the_ones_that_honor_order() -> None:
    """Two hand-maintained lists of the same intents, kept apart so the router
    does not import the templates. An intent honoring `order` and missing here
    keeps the bug this fixed; one listed here that does not honor it turns
    into a fall-through."""
    from association.query.router import _ORDER_ON_A_SINGLE_GAME
    from association.query.templates.common import HONORED_SCOPING

    # player_stat honors an order only beside a limit of one (a single game
    # handed to game_log), so route() sets the pair together for it rather
    # than filling order alone - see _ORDER_ON_A_SINGLE_GAME.
    assert frozenset(intent for intent, honored in HONORED_SCOPING.items() if "order" in honored) == ORDER_INTENTS | _ORDER_ON_A_SINGLE_GAME


# ---------------- scoping read from the question text ----------------
#
# Each of these existed because a real StatMuse query was answered fast, fluently
# and about something else. None is in ROUTER_SCHEMA - they are read from the
# text - so the model's answer is given here only to show it is not consulted.


def _ask(question: str, payload: str) -> Route:
    """route() on a real question, with the model's reply fixed."""
    with patch("association.query.router.ollama.chat", return_value=_reply(payload)):
        got = route("m", question)
    assert got is not None
    return got


def test_the_postseason_comes_from_the_question_not_the_model() -> None:
    """Measured both ways at temperature 0: "Sga record 36 plus points" came back
    as a playoff question, and "tatum stats in the 2024 finals" as a
    regular-season one."""
    assert _ask("Sga record 36 plus points", '{"intent":"threshold_count","season_type":"playoffs"}').slots["season_type"] == 2
    assert _ask("tatum stats in the 2024 finals", '{"intent":"player_stat","season_type":"regular"}').slots["season_type"] == 3
    assert _ask("Who led the playoffs in rebounding?", '{"intent":"leaderboard"}').slots["season_type"] == 3


@pytest.mark.parametrize(
    "question",
    [
        "Show me the Knicks last 5 games",
        "what did Nikola Jokic do in his last 5 games?",
        "Rui last ten games",
        "Total points scored by the toronto raptord in the last 10 games",
    ],
)
def test_a_last_n_games_question_naming_no_season_type_reads_both(question: str) -> None:
    """ISSUES.md, "'Last N games' means the last N regular-season games, even
    when playoff games came after": each of these answered the last N of the
    REGULAR season though the team or player went on to play in the
    postseason. `game_log` reads the signal set here to read both season
    types and merge them by date instead of silently defaulting to the
    regular season the way `_validate_season_type` does everywhere else."""
    got = _ask(question, '{"intent":"game_log","player":"Nikola Jokic","team":"Knicks","order":"recent","limit":5}')
    assert got.slots["season_type_unstated"] is True
    # season_type is still set - the template's fallback default, unused once
    # the signal is read, and every OTHER template that might see it ignores
    # season_type_unstated entirely (see check_scope's HONORED_SCOPING test).
    assert got.slots["season_type"] == 2


@pytest.mark.parametrize(
    "question",
    [
        "Knicks last 5 regular season games",
        "Knicks last 5 regular-season games",
        "Knicks last 5 playoff games",
        "Knicks last 5 postseason games",
        "Knicks last 5 games in the finals",
    ],
)
def test_a_last_n_games_question_naming_its_season_type_is_not_widened(question: str) -> None:
    """The correction the default has to leave standing: saying "regular
    season" or "playoffs" outright must keep meaning only that."""
    got = _ask(question, '{"intent":"game_log","team":"Knicks","order":"recent","limit":5}')
    assert "season_type_unstated" not in got.slots


def test_a_last_n_games_signal_requires_a_real_limit_and_the_recent_order() -> None:
    from association.query.router import _route_game_log_recent_span

    # No `order` at all: an ordinary game_log question with a limit, not "last
    # N games" - e.g. "top 5" is `order` absent, `limit` present.
    no_order: dict[str, Any] = {"limit": 5}
    _route_game_log_recent_span("game_log", no_order, "Knicks top 5 wins this season")
    assert "season_type_unstated" not in no_order
    # `order="first"` is "his first N games", the opposite end of the season -
    # a real question, but not the one this signal is for.
    first: dict[str, Any] = {"order": "first", "limit": 5}
    _route_game_log_recent_span("game_log", first, "Knicks first 5 games")
    assert "season_type_unstated" not in first
    # No real limit at all.
    no_limit: dict[str, Any] = {"order": "recent"}
    _route_game_log_recent_span("game_log", no_limit, "Knicks last games")
    assert "season_type_unstated" not in no_limit
    # A non-game_log intent never sees it, whatever else is set.
    other_intent: dict[str, Any] = {"order": "recent", "limit": 5}
    _route_game_log_recent_span("player_stat", other_intent, "Knicks last 5 games")
    assert "season_type_unstated" not in other_intent


@pytest.mark.parametrize(
    "extra_slots",
    [
        {"game_n": 4},  # one game of a KNOWN playoff series
        {"span": "career"},  # a career has no single year to mix two types within
        {"since": 2020},  # a range of seasons
        {"date": "2026-04-12"},  # one calendar day already finds its own game
    ],
)
def test_a_last_n_games_signal_defers_to_a_narrower_slot_already_set(extra_slots: dict[str, Any]) -> None:
    """Each of these already fixes which games are meant more precisely than
    "last N" does, so none of them widen to both season types."""
    from association.query.router import _route_game_log_recent_span

    slots: dict[str, Any] = {"order": "recent", "limit": 5, **extra_slots}
    _route_game_log_recent_span("game_log", slots, "Knicks last 5 games")
    assert "season_type_unstated" not in slots


@pytest.mark.parametrize(
    ("question", "venue"),
    [
        ("Knicks home record this season", "home"),
        ("Warriors record on the road", "away"),
        ("zach lavine vs nuggets last 8 games home", "home"),
        ("How far away does Wembanyama shoot from?", None),  # a distance, and a routing case
        ("Nikola Jokic home and away splits", None),  # both halves is a split, not a filter
    ],
)
def test_a_venue_is_read_from_the_question(question: str, venue: str | None) -> None:
    assert _ask(question, '{"intent":"team_record"}').slots.get("venue") == venue


def test_a_career_is_every_season_so_the_models_default_year_is_dropped() -> None:
    got = _ask("career points leaders", '{"intent":"leaderboard","stat":"points","season_ref":"current"}')
    assert got.slots["span"] == "career"
    assert "season" not in got.slots


def test_a_career_high_in_a_named_season_is_that_seasons_best() -> None:
    """ "career high this season" is a worked example of single_game_high in
    ROUTER_PROMPT - it means the season's best, and must not become a career."""
    assert "span" not in _ask("what is his career high this season", '{"intent":"single_game_high","stat":"points"}').slots
    assert _ask("Diabate career high assists", '{"intent":"single_game_high","stat":"assists"}').slots["span"] == "career"


def test_a_named_year_survives_a_career_word() -> None:
    got = _ask("most points ever scored in a game in 2024", '{"intent":"single_game_high","stat":"points"}')
    assert got.slots["span"] == "career" and got.slots["season"] == 2024


def test_all_season_type_games_reads_as_a_career_span() -> None:
    """The measured bug, verbatim (#141): "show a shot chart for steph curry
    in all playoff games" routed to season 2025 - none of _SPAN_WORDS
    ("career", "all-time", "ever", "in/of history") is in it - and drew one
    postseason presented as all of them, with nothing saying so."""
    got = _ask(
        "show a shot chart for steph curry in all playoff games",
        '{"intent":"shot_chart","player":"Stephen Curry","season_type":3,"season":2025}',
    )
    assert got.slots["span"] == "career" and "season" not in got.slots
    every = _ask(
        "show a shot chart for steph curry in every playoff game",
        '{"intent":"shot_chart","player":"Stephen Curry","season_type":3,"season":2025}',
    )
    assert every.slots["span"] == "career"
    his = _ask(
        "show a shot chart for steph curry in all his playoff games",
        '{"intent":"shot_chart","player":"Stephen Curry","season_type":3,"season":2025}',
    )
    assert his.slots["span"] == "career"


def test_since_he_joined_the_league_reads_as_a_career_span() -> None:
    """yardstick-v2 F031: "Show me luka's avg assists since he joined the
    league" routed with no ``span`` at all - none of ``_SPAN_WORDS`` is in
    it - and answered one season (8.8 apg, 2019-20) where his whole career
    (8.23 apg, 514 games, 2019-2026) was asked for. Anchored on "the league"
    so it does not fire on "since he joined the team"/"...the Mavericks",
    which name a team question, not a career one."""
    got = _ask(
        "Show me luka's avg assists since he joined the league",
        '{"intent":"player_stat","player":"Luka Doncic","stat":"assists","season":2026}',
    )
    assert got.slots["span"] == "career" and "season" not in got.slots
    team = _ask(
        "how many points has curry scored since he joined the Warriors",
        '{"intent":"player_stat","player":"Stephen Curry","stat":"points","season":2026}',
    )
    assert "span" not in team.slots


def test_all_season_type_games_does_not_fire_on_all_star() -> None:
    """The anchor is a season-TYPE word directly after "all"/"every" - "star"
    is not one, so an All-Star question keeps its own season rather than
    becoming a career (a false positive here would draw every All-Star Game
    on record for a question about one)."""
    game = _ask("curry's shot chart for the all-star game", '{"intent":"shot_chart","player":"Stephen Curry"}')
    assert "span" not in game.slots
    games = _ask("curry's shot distance in all star games this season", '{"intent":"shot_distance","player":"Stephen Curry"}')
    assert "span" not in games.slots


@pytest.mark.parametrize(
    ("question", "without"),
    [
        ("Podziemski game log without curry", ["curry"]),
        ("jalen Duren stats without Cade Cunningham this season", ["Cade Cunningham"]),
        ("Celtics record without Tatum", ["Tatum"]),
        ("most games without a turnover", None),  # names nobody
    ],
)
def test_a_missing_teammate_is_read_from_the_question(question: str, without: list[str] | None) -> None:
    assert _ask(question, '{"intent":"game_log"}').slots.get("without") == without


@pytest.mark.parametrize(
    ("question", "without"),
    [
        ("Celtics record without Tatum and Brown", ["Tatum", "Brown"]),
        ("Lakers record without Lebron and AD this season", ["Lebron", "AD"]),
        ("Celtics record without Tatum, Brown and Holiday", ["Tatum", "Brown", "Holiday"]),
        # "or" joins the same way "and" does: neither of them played either way.
        ("hornets record without brandon miller or lamelo", ["brandon miller", "lamelo"]),
        # "and" with nothing in front of it names nobody, so the phrase still
        # starts at the name that follows "without".
        ("Celtics record with and without Tatum", ["Tatum"]),
    ],
)
def test_every_name_a_without_phrase_holds_is_read(question: str, without: list[str]) -> None:
    """The measured bug: only the first name came back, so the answer covered
    the games without ONE of the players and said nothing about the other - a
    different question, answered fluently."""
    assert _ask(question, '{"intent":"with_without"}').slots.get("without") == without


def test_with_a_teammate_is_only_read_for_the_template_that_uses_it() -> None:
    """ "with" is everywhere ("games with 30+ points"), so outside with_without it
    would be noise at best."""
    assert _ask("jjj stats with ja morant last season", '{"intent":"with_without"}').slots["with_player"] == ["ja morant"]
    assert "with_player" not in _ask("jjj stats with ja morant last season", '{"intent":"player_stat"}').slots


def test_a_with_phrase_keeps_every_name_the_same_way() -> None:
    """The same defect on the other keyword: "record when A and B play" is a
    real question shape, and one name of it is a different question."""
    assert _ask("jjj stats with ja morant and desmond bane last season", '{"intent":"with_without"}').slots["with_player"] == ["ja morant", "desmond bane"]


@pytest.mark.parametrize(
    ("question", "split"),
    [
        ("Nikola Jokic home and away splits", "home_away"),
        ("Joe Ingles stats when starting vs coming off the bench", "starter_bench"),
        ("Giannis Antetokounmpo stats by month", "month"),
        ("Tatum stats in wins vs losses", "wins_losses"),
    ],
)
def test_a_split_is_read_from_the_question(question: str, split: str) -> None:
    got = _ask(question, '{"intent":"player_splits"}')
    assert got.slots["split"] == split
    assert "venue" not in got.slots


@pytest.mark.parametrize(
    ("question", "rank"),
    [
        ("which team has the fewest turnovers", "fewest"),
        ("best defense in the league", "best"),
        ("worst three point shooting team", "worst"),
        ("team with the most threes", "most"),
    ],
)
def test_a_team_ranking_reads_which_end_was_asked_for(question: str, rank: str) -> None:
    assert _ask(question, '{"intent":"team_leaderboard"}').slots["rank"] == rank


def test_a_streak_reads_whether_it_is_a_losing_one() -> None:
    assert _ask("lakers longest losing streak this season", '{"intent":"streak"}').slots["kind"] == "loss"
    assert _ask("lakers longest winning streak this season", '{"intent":"streak"}').slots["kind"] == "win"


def test_a_team_ranking_asked_as_a_player_ranking_is_rerouted() -> None:
    """Measured: "which team scores the most points per game" was answered with
    the players' scoring leaders."""
    assert _ask("which team scores the most points per game", '{"intent":"leaderboard","stat":"points"}').intent == "team_leaderboard"
    assert _ask("which team has the most threes in a playoff game", '{"intent":"single_game_high","stat":"threePointFieldGoalsMade"}').intent == "other"
    assert _ask("Top 5 scorers on the Lakers?", '{"intent":"leaderboard","stat":"points"}').intent == "leaderboard"


@pytest.mark.parametrize(
    ("question", "want"),
    [
        ("rj barrett 4th qtr log", {"period": 4}),
        ("kd q4 points last game", {"period": 4}),
        ("tatum first half stats", {"half": 1}),
        ("harrison barnes 1st q stats", {"period": 1}),
    ],
)
def test_abbreviated_quarters_and_halves_are_recognized(question: str, want: dict[str, int]) -> None:
    """ "rj barrett 4th qtr log" slipped past a pattern that only knew
    "quarter", and game_log answered with his whole last game. It used to be
    forced to the agent for want of a template; the abbreviations still have to
    be recognized, and now they route to one."""
    got = _ask(question, '{"intent":"game_log","player":"X"}')
    assert got.intent == "period_split"
    assert all(got.slots.get(k) == v for k, v in want.items()), got.slots


def test_a_team_half_is_the_two_quarters_of_its_own_linescore() -> None:
    """A team's half used to have no template and fall through: the model maps
    "first half" onto period 1, which is wrong for a team the same way it is
    for a player. It is team_quarter_points' now, summing the two quarters the
    linescore already holds - and the `half` slot, not the model's `period`,
    is what says which two."""
    got = _ask("Celtics 2nd half scoring this season", '{"intent":"team_quarter_points","team":"Boston Celtics","period":2}')
    assert got.intent == "team_quarter_points" and got.slots.get("half") == 2
    assert _ask("76ers 4th qtr points vs boston", '{"intent":"team_quarter_points","team":"Philadelphia 76ers","period":4}').intent == "team_quarter_points"
    # A half with no team and no player still has no subject, so it falls through.
    assert _ask("2nd half scoring this season", '{"intent":"team_quarter_points"}').intent == "other"


def test_a_team_asked_for_its_most_or_fewest_carries_that_rank() -> None:
    """ "Detroit Pistons most points in a first half this season" asks for ONE
    game, not the season's average. The rank words route() already reads for
    team_leaderboard say which end, and the template answers that game."""
    most = _ask("Detroit Pistons most points in a first half this season", '{"intent":"team_quarter_points","team":"Detroit Pistons","period":1,"season":2026}')
    assert most.intent == "team_quarter_points" and most.slots.get("half") == 1 and most.slots.get("rank") == "most"
    least = _ask("least points scored by the wizards in the first half this season", '{"intent":"team_quarter_points","team":"Washington Wizards","period":1,"season":2026}')
    assert least.slots.get("rank") == "fewest"
    # A plain half question carries no rank, so it still averages.
    plain = _ask("Celtics 2nd half scoring this season", '{"intent":"team_quarter_points","team":"Boston Celtics","period":2}')
    assert "rank" not in plain.slots


def test_a_rank_word_filed_as_the_team_is_dropped() -> None:
    """ISSUES.md #172: "nba team with least playoff wins since 2022" arrived
    with `team='least'` beside a correctly-read `rank='fewest'` - the same
    word, filed twice. No franchise is named "least", so `team_leaderboard`
    refused "no team matching 'least'" over a cause the question never gave,
    on a question it could otherwise answer in full. `team` is read against
    `RANK_WORDS` again rather than a new list, so the two checks cannot
    disagree about what counts as one."""
    payload = '{"intent":"team_leaderboard","stat":"playoffs_wins","team":"least","season":2022,"season_ref":"previous"}'
    got = _ask("nba team with least playoff wins since 2022", payload)
    assert got.intent == "team_leaderboard"
    assert got.slots.get("rank") == "fewest"
    assert "team" not in got.slots
    assert got.slots.get("since") == 2022
    # A real team beside a rank word is untouched - only a team slot that IS
    # one of the rank words is ever dropped.
    real_team = _ask("worst record for the Knicks since 2022", '{"intent":"team_leaderboard","team":"New York Knicks","stat":"record","season":2022,"season_ref":"previous"}')
    assert real_team.slots.get("team") == "New York Knicks" and real_team.slots.get("rank") == "worst"


@pytest.mark.parametrize(
    ("question", "playoff_round"),
    [
        ("tatum stats in the 2024 finals", "finals"),
        ("Chris Paul playoff game 7 record", None),  # "game 7" is a game of a series (game_n), not a round
        ("jokic stats in the second round", "second round"),
        ("tatum stats in the 2024 playoffs", None),  # the whole postseason is answerable
    ],
)
def test_a_playoff_round_is_read_from_the_question(question: str, playoff_round: str | None) -> None:
    """ "tatum stats in the 2024 finals" was answered with his whole 2024
    postseason - 19 games, where the Finals were 5."""
    assert _ask(question, '{"intent":"player_stat"}').slots.get("round") == playoff_round


def test_a_split_is_read_for_every_intent_so_others_can_refuse_it() -> None:
    """Measured: routed to player_stat and answered with his season minutes."""
    got = _ask("Joe Ingles stats when starting vs coming off the bench", '{"intent":"player_stat","player":"Joe Ingles","stat":"minutes"}')
    assert got.slots["split"] == "starter_bench"


@pytest.mark.parametrize(
    ("question", "since", "until"),
    [
        ("most 3 pointers made since 2020", 2020, None),
        ("most steals by bucks players 2010s", 2010, 2019),
        ("most points this season", None, None),
        # Closed ranges - both ends named. `until` used to be declared nowhere
        # and honored nowhere at all, so these read as an open "since" span
        # (AGENTS.md's own worst-failure-shape example).
        ("Portis vs bulls 2019-20 to 2023-24", 2020, 2024),
        ("Best record from 2010-11 to 2018-19 nba", 2011, 2019),
        ("between 2020 and 2024 who led in assists", 2020, 2024),
        ("knicks record by month 2024 2025", 2024, 2025),
        # "2024-2026" (non-consecutive, hyphenated, no "to"/"from") is a
        # range; "2023-2024" (consecutive) is a SINGLE season written with
        # both years spelled out, exactly as "2023-24" already means - a
        # correction to this same commit's first version, which read the
        # consecutive form as since=2023/until=2024 and silently overwrote
        # season_text._SPAN's already-correct single-season read.
        ("how many 20+ point games did SGA have 2024-2026?", 2024, 2026),
        ("sga stats in the 2023-2024 season", None, None),
    ],
)
def test_a_range_of_seasons_replaces_the_one_the_model_picked(question: str, since: int | None, until: int | None) -> None:
    """Measured: "since 2020" became season=2020, answered as one season."""
    got = _ask(question, '{"intent":"leaderboard","stat":"points","season":2020}')
    assert got.slots.get("since") == since and got.slots.get("until") == until
    if since is not None:
        assert "season" not in got.slots


def test_a_consecutive_hyphenated_year_pair_keeps_season_texts_own_reading() -> None:
    """ "the 2023-2024 season" is ONE season (2024), the way "2023-24" already
    is - `_validate_range`'s `_RANGE_HYPHEN_YEARS` used to treat ANY
    four-digit hyphenated pair as a range, so this silently became
    since=2023/until=2024 and popped the single, correct `season` slot
    `_validate_season`/`season_text._SPAN` had already set."""
    got = _ask("sga stats in the 2023-2024 season", '{"intent":"player_stat","player":"Shai Gilgeous-Alexander","stat":"points"}')
    assert got.slots.get("season") == 2024
    assert "since" not in got.slots and "until" not in got.slots


@pytest.mark.parametrize(
    ("phrase", "count"),
    [
        ("past two seasons", 2),
        ("past 3 seasons", 3),
        ("last two years", 2),
        ("last 5 years", 5),
    ],
)
def test_a_relative_season_count_becomes_a_since_span(phrase: str, count: int) -> None:
    """ "past N seasons" / "last N years" is a relative window counted back
    from NOW, not the absolute "since YYYY" or a decade name above - and not
    a count of games either (#140, below). `since` alone reaches exactly the
    window asked for: nothing is played after "now", so no `until` is needed
    to stop it at the current season."""
    got = _ask(f"jayson tatum's games against the knicks in the {phrase}", '{"intent":"game_log","player":"Jayson Tatum"}')
    assert got.slots.get("since") == current_season() - count + 1
    assert "until" not in got.slots


def test_a_past_n_seasons_count_word_does_not_become_a_limit() -> None:
    """The measured failure, verbatim (#140): "show tyrese maxey's games
    against boston in the past two seasons" routed with the model's own
    limit=2 - the "two" belongs to "seasons", not to a count of games - and
    answered his last 2 games of his CAREER where seven were asked for.
    Measured on player_game_log (games played, regular season): Maxey has 3
    games against Boston in 2025 and 4 in 2026, seven total - exactly what
    `since=2025` (current season 2026) reaches with no games left out."""
    got = _ask(
        "show tyrese maxey's games against boston in the past two seasons",
        '{"intent":"game_log","player":"Tyrese Maxey","teams":["Boston"],"limit":2,"season_type":"regular"}',
    )
    assert "limit" not in got.slots
    assert got.slots.get("since") == current_season() - 1
    assert "span" not in got.slots


def test_a_real_games_count_survives_beside_a_past_n_seasons_phrase() -> None:
    """Only the count word that modifies "seasons"/"years" is filler - a
    separate, real count of games ("last 5 games") is not this rule's
    business and keeps its limit."""
    got = _ask("tatum's last 5 games in the past two seasons", '{"intent":"game_log","player":"Jayson Tatum","limit":5}')
    assert got.slots.get("limit") == 5
    assert got.slots.get("since") == current_season() - 1


def test_a_record_asked_as_a_count_goes_to_record_when() -> None:
    """Measured: answered with the league's 30-point-game counts, Embiid dropped."""
    got = _ask("Sixers record when Embiid scores 30 points this season", '{"intent":"threshold_count","stat":"points","threshold":30}')
    assert got.intent == "record_when"
    assert _ask("most 30 point games this season", '{"intent":"threshold_count","stat":"points","threshold":30}').intent == "threshold_count"


def test_a_fingerprint_is_only_what_the_question_names() -> None:
    """Measured under two prompt revisions: "Plot Curry's threes from last
    season" came back as a fingerprint."""
    assert _ask("Plot Curry's threes from last season", '{"intent":"fingerprint","player":"Stephen Curry"}').intent == "shot_chart"
    assert _ask("how does wemby add value", '{"intent":"fingerprint","player":"Victor Wembanyama"}').intent == "other"
    assert _ask("Show me Wembanyama's defensive fingerprint chart", '{"intent":"fingerprint"}').intent == "fingerprint"


def test_a_career_high_is_a_single_game_not_an_average() -> None:
    assert _ask("Diabate career high assists", '{"intent":"player_stat","player":"Moussa Diabate","stat":"assists"}').intent == "single_game_high"


@pytest.mark.parametrize(
    ("question", "threshold"),
    [
        ("Sixers record when Embiid scores 30 points this season", 30),
        ("Sga record 36 plus points", 36),
        ("most 40 point games in a row", 40),
        ("most 3 point makes in a game", None),  # a shot type, not a threshold of three
    ],
)
def test_a_threshold_the_model_left_out_is_read_from_the_question(question: str, threshold: int | None) -> None:
    assert _ask(question, '{"intent":"record_when","stat":"points"}').slots.get("threshold") == threshold


def test_a_count_with_no_threshold_is_a_season_ranking() -> None:
    """Measured: "who has the most threes this season" arrived as threshold_count
    with no threshold, and fell through."""
    assert _ask("who has the most threes this season", '{"intent":"threshold_count","stat":"threePointFieldGoalsMade"}').intent == "leaderboard"
    assert _ask("most 30 point games this season", '{"intent":"threshold_count","stat":"points"}').intent == "threshold_count"


@pytest.mark.parametrize(("question", "rank"), [("slowest pace in the league", "fewest"), ("fastest team this season", "most")])
def test_pace_words_rank_the_right_end(question: str, rank: str) -> None:
    """Without these, "slowest pace" listed the fastest teams first."""
    assert _ask(question, '{"intent":"team_leaderboard","stat":"pace"}').slots["rank"] == rank


@pytest.mark.parametrize("question", ["Sga games with under 14 fta in his whole career", "games with less than 20 points", "most games with fewer than 5 turnovers"])
def test_a_comparison_below_a_number_is_a_scoping_slot(question: str) -> None:
    """ "under 14 fta" reached threshold_count as 14 and was answered as 14 or more."""
    assert "below" in _ask(question, '{"intent":"threshold_count","stat":"points","threshold":14}').slots


def test_a_comparison_below_a_number_keeps_the_words_that_name_the_stat() -> None:
    """The model's `stat` beside "under 14 fta" was freeThrowsMade - the nearest
    name it knows - so the phrase, words and all, is what the templates read
    the column from. Every phrase, not the first: two lines are two filters."""
    got = _ask("Sga games with under 14 fta in his whole career", '{"intent":"threshold_count","stat":"freeThrowsMade","threshold":14}')
    assert got.slots["below"] == ["under 14 fta"]
    two = _ask("mikal bridges game log with less than 15 fga and with less than 35 minutes", '{"intent":"game_log","player":"Mikal Bridges"}')
    assert two.slots["below"] == ["less than 15 fga", "less than 35 minutes"]
    assert "above" not in two.slots  # the "35 minutes" inside "less than 35 minutes" is not a floor
    cap = _ask("curry games with 30 minutes or less", '{"intent":"game_log","player":"Stephen Curry"}')
    assert cap.slots["below"] == ["30 minutes or less"] and "above" not in cap.slots and "situation" not in cap.slots


def test_a_limit_of_one_beside_a_log_asked_for_by_name_is_filler() -> None:
    """ "paul reed gamelog with 25 minutes" arrived with order='recent',
    limit=1 and answered his most recent game where his log was asked. The
    order is kept (a model order on game_log always is); the limit goes."""
    got = _asking('{"intent":"game_log","player":"Paul Reed","order":"recent","limit":1}', "paul reed gamelog with 25 minutes")
    assert "limit" not in got.slots
    kept = _asking('{"intent":"game_log","player":"Paul Reed","order":"recent","limit":1}', "paul reed's last game")
    assert kept.slots.get("limit") == 1
    counted = _asking('{"intent":"game_log","player":"Paul Reed","order":"recent","limit":1}', "paul reed game log last 1 games")
    assert counted.slots.get("limit") == 1


@pytest.mark.parametrize(
    ("question", "want"),
    [
        ("paul reed gamelog with 25 minutes", ["with 25 minutes"]),
        ("forwards with 20+ mins vs gsw log", ["with 20+ mins"]),
        ("curry stats in games with at least 30 minutes played", ["with at least 30 minutes played"]),
    ],
)
def test_a_minutes_floor_is_a_line_the_relation_filters_on(question: str, want: list[str]) -> None:
    """These used to be `situation`, which every template refuses; a minutes
    floor is a line on a box-score column, and the log answers it."""
    got = _ask(question, '{"intent":"game_log","player":"Paul Reed"}')
    assert got.slots.get("above") == want and "situation" not in got.slots


@pytest.mark.parametrize(
    "question",
    ["Celtics record on back to backs", "Lakers record in overtime this season", "76ers record in october", "Knicks record vs the east", "best record since the all-star break"],
)
def test_a_situation_no_template_filters_on_is_a_scoping_slot(question: str) -> None:
    assert "situation" in _ask(question, '{"intent":"team_record","team":"X"}').slots


@pytest.mark.parametrize(
    "question",
    [
        # Every one of these is verbatim from the 261-query StatMuse feed
        # replay, and every one was answered with the narrowing silently
        # dropped. A day of the week is 8 of the 14.
        "lebron james 2 3 pointers all-time vs jazz on tuesdays",
        "garland on mondays game log",
        "jamal murray career games on Tuesdays",
        "2024 nba stephen curry double double per game scored on fridays",
        "anthony davis stats on christmas",
        "most triple doubles before turning 27",
        "lebron ppg as an 18 year old",
        "paolo banchero since returning from injury",
    ],
)
def test_a_narrowing_the_schema_has_no_slot_for_still_reaches_check_scope(question: str) -> None:
    """The P1 from the feed replay: `check_scope` can only refuse a slot the
    router emits, and ROUTER_SCHEMA has no slot for a weekday, a holiday, an
    age, a minutes condition or "since returning from injury" - so the words
    never reached it and the template answered the un-narrowed question.

    "lebron james 2 3 pointers all-time vs jazz on tuesdays" returned his
    career average against Utah over 48 games. Read into `situation`, which no
    template lists in HONORED_SCOPING, so every one of these now refuses and
    falls through to the agent.
    """
    assert "situation" in _ask(question, '{"intent":"player_stat","player":"LeBron James"}').slots


def test_a_stat_name_before_a_second_line_is_not_a_subject() -> None:
    """ "who had the most 30+ point 10+ rebound games this year?" read "point"
    as the player of "point 10+ rebound games" and answered for Sir'Dominic
    Pointer. A stat's own name is never the subject."""
    got = _ask("who had the most 30+ point 10+ rebound games this year?", '{"intent":"threshold_count","stat":"points","threshold":30,"season":2026}')
    assert "player" not in got.slots


def test_this_postseason_names_the_current_season() -> None:
    """ "maxey's stats for game 4 against the knicks this postseason" carried a
    filler limit, and the last-meetings rule read the missing season word as
    "wherever they fall" - a career, which asked which Maxey."""
    got = _ask("show maxey's stats for game 4 against the knicks this postseason", '{"intent":"game_log","player":"Maxey","order":"recent","limit":1,"season_type":3}')
    assert "span" not in got.slots and got.slots.get("game_n") == 4


@pytest.mark.parametrize(
    ("question", "model_stat", "want"),
    [
        ("who were the top 10 in defensive netpoints / 100 possesions?", "netpoints_defense", "netpoints_defense_per_100"),  # codespell:ignore possesions - as typed
        ("who were the top 10 in adjusted defensive netpoints", "netpoints_defense", "netpoints_defense_per_100"),
        ("who were the top 10 players in offensive netpoints per 100 possessions", "netpoints_offense", "netpoints_offense_per_100"),
        ("who led the league in adjusted netpoints?", "netpoints", "netpoints_per_100"),
        ("who led the league in netpoints adjusted per possesion", "netpoints_per_100", "netpoints_per_100"),  # codespell:ignore possesion - as typed
    ],
)
def test_a_netpoints_rate_asked_by_any_name_is_the_per_100_metric(question: str, model_stat: str, want: str) -> None:
    """#152: five web-session questions ranked season totals under a question
    that asked for the rate. The only adjusted NetPoints in the data is the
    per-100 rate, so "adjusted" reads as it."""
    got = _ask(question, f'{{"intent":"leaderboard","stat":"{model_stat}","limit":10}}')
    assert got.slots["stat"] == want and "rate" not in got.slots


def test_a_filler_order_does_not_narrow_a_chart_to_one_game() -> None:
    """#153: on shot_chart, shot_distance, player_netpoints and fingerprint an
    `order` resolves to ONE game, where game_log only sorts - so a filler one
    costs the season. "a shot chart of steph curry's 2025 season for 3 point
    shots" drew a single game, 7 of 12."""
    got = _asking('{"intent":"shot_chart","player":"Stephen Curry","order":"recent","shot_value":3,"season":2025}', "show a shot chart of steph curry's 2025 season for 3 point shots")
    assert "order" not in got.slots and got.slots.get("season") == 2025
    # A game the question does name keeps it, however it is phrased.
    named = _asking('{"intent":"shot_chart","player":"Stephen Curry","order":"recent"}', "curry's shot chart for his last home game")
    assert named.slots.get("order") == "recent"
    # game_log is unaffected: there an order sorts a list rather than picking a game.
    log = _asking('{"intent":"game_log","player":"Stephen Curry","order":"recent"}', "curry game log for 2025")
    assert log.slots.get("order") == "recent"


def test_one_game_at_the_end_of_a_span_is_this_season_unless_the_question_says_otherwise() -> None:
    """#153: "steph curry's last regular season game" came back as season
    2025 - the model read "last regular season" as the season before this one
    - and the chart drew a game a year off."""
    got = _asking('{"intent":"shot_chart","player":"Stephen Curry","order":"recent","limit":1,"season":2025}', "show a shot chart of steph curry's last regular season game")
    assert "season" not in got.slots and got.slots.get("order") == "recent"
    # A year the question states wins.
    stated = _asking('{"intent":"shot_chart","player":"Stephen Curry","order":"recent","season":2025}', "steph curry's last game of 2025")
    assert stated.slots.get("season") == 2025
    # And so does "last season".
    worded = _asking('{"intent":"shot_chart","player":"Stephen Curry","order":"recent","season":2025}', "steph curry's last game of last season")
    assert worded.slots.get("season") == 2025


@pytest.mark.parametrize(
    ("question", "want"),
    [
        ("who had the most 30+ point 10+ rebound games this year?", ["30+ point", "10+ rebound"]),
        ("How many 20+ point 5+ assist games did luka have?", ["20+ point", "5+ assist"]),
        ("How many games did luka have with 20+ points and 5+ assists?", ["20+ points", "5+ assists"]),
    ],
)
def test_both_conditions_of_a_two_condition_count_are_read(question: str, want: list[str]) -> None:
    """#139: ROUTER_SCHEMA carries one `threshold`, so the second condition
    survived only as a `fields` entry the template ignores - "the most 30+
    point 10+ rebound games" answered the 30+ point leader."""
    got = _ask(question, '{"intent":"threshold_count","stat":"points","threshold":20,"fields":["rebounds"]}')
    assert got.slots.get("above") == want


def test_one_condition_is_left_to_the_threshold_slot_exactly_as_before() -> None:
    """Nothing changes for the questions that work today: a single "30+
    points" is the model's own threshold, and a count word with no plus
    ("top 10 rebound leaders") is not a condition at all - reading it as one
    would refuse a leaderboard question that answers."""
    one = _ask("who had the most 30+ point games this season?", '{"intent":"threshold_count","stat":"points","threshold":30}')
    assert "above" not in one.slots and one.slots["threshold"] == 30
    ranked = _ask("top 10 rebound leaders this season", '{"intent":"leaderboard","stat":"rebounds","limit":10}')
    assert "above" not in ranked.slots


def test_a_rate_no_metric_holds_is_refused_rather_than_ranked_by_the_wrong_unit() -> None:
    """ "/ 90" has no column; "points per 100 possessions" has no per-100 form.
    Both get a `rate` slot, and the metric is never quietly switched to one the
    warehouse does hold.

    The slot used to be honored by nothing, so `check_scope` raised and the
    question fell through to an agent with no per-90 anything to read.
    `leaderboard` declares it now and refuses in the metric's own name - see
    test_leaderboard_refuses_a_unit_the_metric_has_no_form_of, which owns that
    half. What belongs here is that the router still states the unit rather
    than dropping it, because a dropped `rate` is a per-90 question answered
    per game with nothing saying so."""
    from association.query.templates import check_scope

    per_90 = _ask("who were the top 10 in defensive netpoints / 90", '{"intent":"leaderboard","stat":"netpoints_defense","limit":10}')
    assert per_90.slots["stat"] == "netpoints_defense" and per_90.slots["rate"] == "/ 90"
    check_scope("leaderboard", per_90.slots)  # reaches the template now, which refuses by name
    points = _ask("points per 100 possessions leaders", '{"intent":"leaderboard","stat":"points"}')
    assert points.slots["stat"] == "points" and points.slots["rate"] == "per 100 possessions"
    plain = _ask("who led the league in defensive netpoints", '{"intent":"leaderboard","stat":"netpoints_defense"}')
    assert plain.slots["stat"] == "netpoints_defense" and "rate" not in plain.slots


def test_an_unscoped_count_by_a_named_player_reads_as_his_career() -> None:
    """Product decision, 2026-09-19: "how many times has embiid fouled out?"
    is 0 this season and 9 in his career, and only the second is the question.
    A season the question names still wins, and so does an ordinal one."""
    got = _ask("how many times has embiid fouled out?", '{"intent":"threshold_count","stat":"fouls","threshold":6,"season":2026}')
    assert got.slots.get("player") == "embiid" and got.slots.get("span") == "career" and "season" not in got.slots
    this = _ask("how many 30 point games does jokic have this season", '{"intent":"threshold_count","stat":"points","threshold":30,"season":2026}')
    assert "span" not in this.slots and this.slots.get("season") == 2026
    year = _ask("how many 30 point games did jokic have in 2024", '{"intent":"threshold_count","stat":"points","threshold":30,"season":2024}')
    assert "span" not in year.slots and year.slots.get("season") == 2024
    ordinal = _ask("how many 40+ points games does lebron james have in his 18th season?", '{"intent":"threshold_count","stat":"points","threshold":40,"season":2018}')
    assert "span" not in ordinal.slots and ordinal.slots.get("season_n") == 18
    # A league-wide count keeps the default season: nobody's career to read.
    league = _ask("how many 50 point games were there", '{"intent":"threshold_count","stat":"points","threshold":50,"season":2026}')
    assert "span" not in league.slots


@pytest.mark.parametrize(
    ("question", "want"),
    [
        ("how many 40+ points games does lebron james have in his 18th season?", "lebron james"),
        ("how many 30 point games does jokic have", "jokic"),
        ("how many games with 5+ blocks has wemby had", "wemby"),
        ("how many 50 point games does anyone have this season", None),
    ],
)
def test_a_count_whose_subject_sits_between_does_and_have_is_restored(question: str, want: str | None) -> None:
    """The model dropped LeBron from the first of these and answered the 2018
    league leaderboard. #148's grammars read "X games with" and "X 30-point
    games"; the subject here sits between an auxiliary and "have"."""
    from association.query.router import _subject_named_in

    assert _subject_named_in(question) == want
    got = _ask(question, '{"intent":"threshold_count","stat":"points","threshold":40}')
    assert got.slots.get("player") == want


@pytest.mark.parametrize(("question", "n"), [("how many 40+ points games does lebron james have in his 18th season?", 18), ("Most points in 15th season played", 15), ("jokic's 3rd season", 3)])
def test_a_season_named_by_its_place_in_a_career_is_an_ordinal_the_templates_settle(question: str, n: int) -> None:
    """The model reads the ordinal as a year: "his 18th season" came back as
    season 2018, with LeBron dropped entirely, and the answer was the 2018
    league leaderboard. The ordinal is kept as `season_n` and the misread year
    goes; which year it is needs the player, so the templates settle it."""
    got = _ask(question, '{"intent":"threshold_count","stat":"points","threshold":40,"player":"LeBron James","season":2018}')
    assert got.slots.get("season_n") == n and "season" not in got.slots and "situation" not in got.slots
    # A year the question names itself stays beside the ordinal (the template refuses the pair).
    named = _ask("lebron's 18th season in 2021", '{"intent":"player_stat","player":"LeBron James","season":2021}')
    assert named.slots.get("season_n") == 18 and named.slots.get("season") == 2021


@pytest.mark.parametrize(("question", "n"), [("Ayton stats in game 4 playoff games", 4), ("show maxey's stats for game 4 against the knicks this postseason", 4), ("lebron in game 7s", 7)])
def test_one_game_of_a_playoff_series_is_a_number_the_relation_finds(question: str, n: int) -> None:
    """ "Ayton stats in game 4 playoff games" answered with his whole 10-game
    postseason, then refused as a `situation`. It is the nth game by date
    between two teams in one postseason, which `real_games` can number; "game
    7" is no longer a `round` either - it is a game like the others."""
    got = _ask(question, '{"intent":"player_stat","player":"Deandre Ayton","season_type":3}')
    assert got.slots.get("game_n") == n and "situation" not in got.slots and "round" not in got.slots
    # The 4 in "game 4" is not a count of games: a filler limit beside it still goes.
    filler = _asking('{"intent":"player_stat","player":"Deandre Ayton","season_type":3,"limit":5}', "Ayton stats in game 4 playoff games")
    assert "limit" not in filler.slots


@pytest.mark.parametrize(
    ("question", "want"),
    [
        # A season fixes the year: season 2026 runs Oct 2025 - Jun 2026, so
        # January onward is 2026 and October back is 2025.
        ("Desmond bane march 17", "2026-03-17"),
        ("Bam adebeyo jan 19", "2026-01-19"),
        ("celtics record vs sixers on november 11", "2025-11-11"),
        ("Curry on Dec. 25th", "2025-12-25"),
        # A year the question states wins over the one the season implies.
        ("celtics vs sixers on november 11 2019", "2019-11-11"),
    ],
)
def test_a_calendar_day_is_resolved_rather_than_refused(question: str, want: str) -> None:
    """The year is not in the question and does not need to be: season Y runs
    October of Y-1 through June of Y, so the month fixes it. This project's own
    numbering applied to a month, not a guess.

    Worth stating why this is resolved where the other narrowings refuse: it
    produces the RIGHT answer rather than a refusal. `game_log` honors `date`,
    and given 2026-03-17 it answers "Desmond Bane, game on 2026-03-17, 16 PTS
    vs OKC" - which is the question. Before this, the date never reached a slot
    and the model's `order="recent"` answered with his most recent game, a
    month later.
    """
    assert _ask(question, '{"intent":"game_log","player":"Desmond Bane"}').slots.get("date") == want


@pytest.mark.parametrize(
    "question",
    [
        # A window, not a day - no template honors a range of dates.
        "Best NBA record since January 31st",
        "lebron points after march 1",
    ],
)
def test_a_date_that_opens_a_window_is_refused_rather_than_read_as_one_day(question: str) -> None:
    """ "since January 31" names a range. Read as a single day it would answer
    one game for a question about a span, which is the same substitution this
    whole module exists to stop."""
    got = _ask(question, '{"intent":"game_log","player":"LeBron James"}')
    assert "date" not in got.slots and "situation" in got.slots


def test_a_calendar_day_with_no_season_behind_it_refuses() -> None:
    """A career question spans twenty Octobers, so nothing fixes the year.
    `span` pops the season, and the date has to refuse rather than pick one."""
    got = _ask("lebron james march 17 all time", '{"intent":"game_log","player":"LeBron James"}')
    assert "date" not in got.slots and "situation" in got.slots


def test_an_impossible_calendar_day_is_not_a_date() -> None:
    """February 31 is not a day. `date(...)` raises rather than rolling over,
    and a slot that cannot be built is one the question keeps asking about -
    so it refuses instead of silently dropping."""
    got = _ask("curry stats on february 31", '{"intent":"game_log","player":"Stephen Curry"}')
    assert "date" not in got.slots and "situation" in got.slots


@pytest.mark.parametrize(
    ("question", "payload", "want"),
    [
        # The model fills the SINGULAR `player` plus an `opponent`, which the
        # existing `players`-list rule never sees. All verbatim from the feed.
        ("keon ellis stats vs trailblazers", '{"intent":"player_matchup","player":"Keon Ellis","opponent":"Portland Trail Blazers"}', "player_stat"),
        ("De'angelo russell vs pistons", '{"intent":"player_matchup","player":"De\'Angelo Russell","opponent":"Detroit Pistons"}', "player_stat"),
        ("Kd games vs wizards", '{"intent":"player_matchup","player":"Kevin Durant","opponent":"Washington Wizards"}', "game_log"),
        ("embid vs bucks gamelog", '{"intent":"player_matchup","player":"Joel Embiid","opponent":"Bucks"}', "game_log"),
        ("kon kneuppel log vs okc", '{"intent":"player_matchup","player":"Kon Knueppel","opponent":"Oklahoma City"}', "game_log"),
    ],
)
def test_a_player_against_a_team_is_not_a_player_matchup(question: str, payload: str, want: str) -> None:
    """`player_matchup` needs two players. Given one and a team it had nothing
    to answer with, and eight feed queries fell through to the agent where
    `player_stat` and `game_log` answer them exactly - both honor `opponent`.

    The rule for this already existed and only read the `players` LIST; the
    model routinely uses the singular `player` slot with an `opponent`
    instead, which is the same question in a different shape.
    """
    assert _ask(question, payload).intent == want


def test_a_matchup_against_another_player_stays_a_matchup() -> None:
    """The reroute is gated on the opponent being a TEAM. "jay huff game log vs
    Embiid" is a real player-versus-player question with the second player in
    the `opponent` slot, and rerouting it would answer a different one."""
    assert _ask("jay huff game log vs Embiid", '{"intent":"player_matchup","player":"Jay Huff","opponent":"Embiid"}').intent == "player_matchup"


@pytest.mark.parametrize(
    ("question", "payload"),
    [
        ("mathurin v det", '{"intent":"player_matchup","players":["Mathurin","Detroit"]}'),
        ("sam hauser v mil", '{"intent":"player_matchup","players":["Sam Hauser","Mil"]}'),
        ("pascal vs orlando", '{"intent":"player_matchup","players":["Pascal Siakam","Orlando"]}'),
        ("Amén Thomson vs toronto", '{"intent":"player_matchup","players":["Amen Thompson","Toronto"]}'),
        ("lauri markkan vs gsw last 5 games", '{"intent":"player_matchup","players":["Lauri Markkanen","gsw"]}'),
    ],
)
def test_a_team_named_by_city_or_abbreviation_is_recognized(question: str, payload: str) -> None:
    """`_is_team_name` required the LAST word to be a nickname, so a team named
    any other way read as a player and the question became a matchup between
    two people. Every case here is verbatim from the feed."""
    assert _ask(question, payload).intent in ("player_stat", "game_log")


@pytest.mark.parametrize("name", ["PJ Washington", "Allan Houston", "Magic Johnson", "Houstan", "Burks", "Hawkins", "Thornton", "Wheat"])
def test_a_player_whose_name_looks_like_a_team_is_still_a_player(name: str) -> None:
    """The reason a city is matched against the WHOLE name and never the last
    word: three players are surnamed Cleveland, Houston and Washington. The
    rest are why a near spelling is not matched at all - Burks/Bucks,
    Hawkins/Hawks, Thornton/Toronto and Wheat/Heat are all within one
    `difflib` step, and the model puts bare surnames in that slot."""
    from association.query.router import _is_team_name

    assert not _is_team_name(name)


def test_a_triple_double_abbreviation_is_not_read_as_three_pointers() -> None:
    """Not a narrowing, though the replay filed it as one: "luka td3s home" had
    its venue read and honored correctly, and answered his POINTS per game at
    home, because `td3s` became shot_value 3. Nothing counts triple-doubles for
    one player, and the season table they live on has no venue dimension, so
    this is the agent's. Spelled out, "triple double" already routes right."""
    assert _ask("luka td3s home", '{"intent":"player_stat","player":"Luka Doncic","shot_value":3}').intent == "other"


@pytest.mark.parametrize("question", ["Duncan Robison 1q log", "Devin Vassell nba player per game stats 1q"])
def test_the_short_form_of_a_quarter_is_recognized(question: str) -> None:
    """`_AGENT_ONLY` knew `q1` and not `1q`, so these were answered with a
    whole-game line - the mirror of the "4th qtr" gap that made the pattern
    grow abbreviations. Recognizing them first sent them to the agent; now that
    `period_split` exists they route to it."""
    got = _ask(question, '{"intent":"game_log","player":"Devin Vassell"}')
    assert got.intent == "period_split" and got.slots["period"] == 1


def test_a_quarter_question_about_a_group_of_players_still_falls_through() -> None:
    """ "each center 1q pts log vs nugget" names a position, not a player.
    `period_split` answers about one named player, so with the slot empty this
    keeps the old behavior rather than inventing a subject."""
    assert _ask("each center 1q pts log vs nugget", '{"intent":"game_log"}').intent == "other"


def test_a_team_line_with_no_stat_named_keeps_the_whole_line() -> None:
    """ "Knicks stats" arrived as stat='points'."""
    assert "stat" not in _ask("Knicks stats this season", '{"intent":"team_stat","team":"New York Knicks","stat":"points"}').slots
    assert _ask("Knicks pace this season", '{"intent":"team_stat","team":"New York Knicks","stat":"pace"}').slots["stat"] == "pace"


def test_a_team_streak_does_not_carry_a_stat_it_never_asked_for() -> None:
    assert "stat" not in _ask("lakers longest winning streak this season", '{"intent":"streak","team":"Lakers","stat":"points"}').slots
    assert _ask("most 40 point games in a row", '{"intent":"streak","stat":"points","threshold":40}').slots["stat"] == "points"


def test_the_last_n_meetings_reach_back_across_seasons() -> None:
    got = _ask("jaylen brown last 8 games vs pistons", '{"intent":"game_log","player":"Jaylen Brown","limit":8,"season_ref":"current"}')
    assert got.slots["span"] == "career" and "season" not in got.slots
    named = _ask("jaylen brown last 8 games vs pistons this season", '{"intent":"game_log","player":"Jaylen Brown","limit":8,"season_ref":"current"}')
    assert "span" not in named.slots


# ---------------- slots the model put in the wrong place, from the final corpus ----------------


def test_true_shooting_is_not_answered_as_three_point_percentage() -> None:
    """Measured: answered with Durant's 3-point percentage."""
    got = _ask("kevin durant true shooting percentage career", '{"intent":"player_stat","player":"Kevin Durant","stat":"threePointFieldGoalPct"}')
    assert got.slots["stat"] == "ts_pct"


# ---------------- "game score" is not answered with points per game (ISSUES.md #114) ----------------


def test_game_score_leaderboard_gets_the_leaderboard_spelling() -> None:
    """Measured: "game score nba leader" arrived at leaderboard with
    stat='points' and was answered with the points-per-game leaders - correct
    about points, not about what was asked. `leaderboard` reads this metric
    through metrics.LEADERBOARD_METRICS, keyed "avg_game_score"."""
    got = _ask("game score nba leader", '{"intent":"leaderboard","stat":"points"}')
    assert got.slots["stat"] == "avg_game_score"


def test_game_score_player_stat_gets_the_player_stat_spelling() -> None:
    """player_stat reads this metric through templates.players.ADVANCED_STATS,
    keyed "game_score" with no prefix - a different spelling than leaderboard's,
    because the two tables were built by different agents against different
    conventions."""
    got = _ask("kevin durant game score this season", '{"intent":"player_stat","player":"Kevin Durant","stat":"points"}')
    assert got.slots["stat"] == "game_score"


@pytest.mark.parametrize(
    "question",
    [
        # "score" alone means points everywhere else in basketball, and a loose
        # match on it would hijack every one of these into a Game Score
        # leaderboard - trading one fluently wrong answer for several.
        "pacers score",
        "what was the score of the game",
        "Total points scored by the toronto raptors",
        "least points scored by the wizards this season",
        # The boundary after "score" is what rejects this one: "scored" is not
        # "score" at a word boundary, even though "game" and "score" are only
        # a few characters apart.
        "2024 nba stephen curry double double per game scored on fridays",
    ],
)
def test_game_score_pattern_does_not_fire_on_ordinary_scoring_language(question: str) -> None:
    got = _ask(question, '{"intent":"leaderboard","stat":"points"}')
    assert got.slots["stat"] == "points"


def test_a_question_actually_about_points_still_emits_points() -> None:
    """The guard has to leave the ordinary case alone, not just avoid the
    near-miss ones above."""
    got = _ask("who led the league in points per game", '{"intent":"leaderboard","stat":"points"}')
    assert got.slots["stat"] == "points"


def test_game_score_is_left_alone_outside_leaderboard_and_player_stat() -> None:
    """player_compare reads a player's stat line through PLAYER_STAT_COLUMNS /
    COMPARE_STAT_LINE, never ADVANCED_STATS - a "game_score" value there would
    be silently unreadable rather than answered, so it is not set. The model's
    own (wrong) guess is left in place, same as before this fix; that is a
    pre-existing gap this task is scoped not to touch."""
    got = _ask("compare durant and lebron in game score", '{"intent":"player_compare","players":["Kevin Durant","LeBron James"],"stat":"points"}')
    assert got.slots.get("stat") == "points"


def test_an_order_the_question_never_asked_for_is_dropped() -> None:
    got = _ask("evan mobley avg against bucks", '{"intent":"player_stat","player":"Evan Mobley","stat":"points","order":"recent","limit":1}')
    assert "order" not in got.slots and "limit" not in got.slots
    kept = _ask("Top 5 scorers on the Lakers?", '{"intent":"leaderboard","stat":"points","team":"Lakers","limit":5}')
    assert kept.slots["limit"] == 5


def test_a_log_asked_of_player_stat_is_a_game_log() -> None:
    assert _ask("luka ft log", '{"intent":"player_stat","player":"Luka Doncic","stat":"freeThrowsMade"}').intent == "game_log"


def test_a_matchup_against_a_team_is_a_players_games() -> None:
    games = _ask("zach lavine vs nuggets last 8 games home", '{"intent":"player_matchup","players":["Zach LaVine","Denver Nuggets"],"limit":8}')
    assert games.intent == "game_log"
    line = _ask("how did curry do against the celtics this year", '{"intent":"player_matchup","players":["Stephen Curry","Boston Celtics"]}')
    assert line.intent == "player_stat"
    assert _ask("lebron vs kawhi head to head", '{"intent":"player_matchup","players":["LeBron James","Kawhi Leonard"]}').intent == "player_matchup"
    # "Magic" is a team word, but a first name is not a team.
    assert _ask("magic johnson vs larry bird head to head", '{"intent":"player_matchup","players":["Magic Johnson","Larry Bird"]}').intent == "player_matchup"


def test_a_comparison_of_one_player_with_a_team_is_his_games_against_it() -> None:
    """Measured: arrived as player_compare with the Celtics in `players`, and
    fell through to the agent once the Celtics became the opponent."""
    line = _ask("compare curry vs the celtics this season", '{"intent":"player_compare","players":["Stephen Curry","Boston Celtics"],"stat":"points"}')
    assert line.intent == "player_stat"
    # Still dropped: the stat is the one the decoder is forced to fill.
    assert "stat" not in line.slots
    # Two players against a team is still a comparison, refused on its opponent.
    both = _ask("compare curry and lebron vs the celtics", '{"intent":"player_compare","players":["Stephen Curry","LeBron James","Boston Celtics"]}')
    assert both.intent == "player_compare"
    assert _ask("compare magic johnson and larry bird", '{"intent":"player_compare","players":["Magic Johnson","Larry Bird"]}').intent == "player_compare"


def test_a_history_with_no_stat_named_is_the_career_line() -> None:
    got = _ask("Jokic career averages", '{"intent":"player_history","player":"Nikola Jokic","stat":"points","limit":1}')
    assert got.intent == "player_stat" and "stat" not in got.slots and "limit" not in got.slots
    assert _ask("Jokic's scoring by year", '{"intent":"player_history","player":"Nikola Jokic","stat":"points"}').intent == "player_history"


def test_a_history_against_a_team_is_the_line_against_it() -> None:
    got = _ask("derozan career points vs knicks", '{"intent":"player_history","player":"DeMar DeRozan","stat":"points","limit":5}')
    assert got.intent == "player_stat" and got.slots["stat"] == "points" and "limit" not in got.slots


def test_best_or_worst_record_ranks_the_league() -> None:
    got = _ask("worst record 2025-26", '{"intent":"team_record","team":"worst","stat":"win_pct"}')
    assert got.intent == "team_leaderboard" and got.slots["stat"] == "record" and "team" not in got.slots
    assert got.slots["rank"] == "worst"


def test_the_league_is_not_a_team() -> None:
    assert "team" not in _ask("Longest winning streak in the NBA this season", '{"intent":"streak","team":"all-NBA"}').slots


def test_a_team_metric_named_in_the_question_wins() -> None:
    from association.query.team_metrics import resolve_team_metric

    got = _ask("Lowest defensive rating by a team this season", '{"intent":"team_leaderboard","stat":"usage_pct_defense","team":"all_teams"}')
    assert resolve_team_metric(got.slots["stat"]) == "defensive_rating"
    assert "team" not in got.slots


def test_a_per_game_abbreviation_names_a_stat() -> None:
    """player_stat drops a stat the question never named; "ppg" names one."""
    got = _ask("lebron ppg this season", '{"intent":"player_stat","player":"LeBron James","stat":"points"}')
    assert got.slots["stat"] == "points"


def test_a_season_before_1990_named_in_the_question_is_kept() -> None:
    """MIN_SEASON is 1947, not 1990 (season_text.py) - a year this low must
    still survive when the QUESTION names it. Rewritten 2026-09-21 (#95): this
    used to pin the same floor for a BARE model `season` int on a question
    ("who led the league in scoring back then") that names no year at all -
    which is the invented-season bug this entry fixes, not the floor. See
    test_the_model_slot_no_longer_applies_when_the_text_names_no_season for
    that half."""
    got = _ask("who led the league in scoring in 1980", '{"intent":"leaderboard","stat":"points"}')
    assert got.slots["season"] == 1980


def test_a_single_game_high_keeps_the_player_the_question_names() -> None:
    """Measured live: "most points curry scored in a game this season" came back
    as single_game_high with no player at all, and the answer was the league's
    high - Bam Adebayo's - to a question about one man. players_named_in cannot
    restore it, since "curry" is six players and it refuses to guess, so the
    subject is read from the grammar and resolution asks which Curry."""
    got = _ask("most points curry scored in a game this season", '{"intent":"single_game_high","stat":"points","season_ref":"current"}')
    assert got.slots["player"] == "curry"
    possessive = _ask("curry's highest scoring game this season", '{"intent":"single_game_high","stat":"points","season_ref":"current"}')
    assert possessive.slots["player"] == "curry"


def test_a_threshold_count_keeps_the_player_the_question_names() -> None:
    """#138: "how many times has embiid fouled out?" came back as
    threshold_count with no player at all, and the answer was the league's
    leader in 6+-foul games (Karl-Anthony Towns) to a question about Joel
    Embiid, who has 0 such games in the 2026 season it defaulted to. Fouling
    out is code-assigned to threshold_count regardless of what the model
    said (see test_fouling_out_is_normalized_to_six_fouls), so the reply
    below need not even get the intent right."""
    got = _ask("how many times has embiid fouled out?", '{"intent":"other"}')
    assert got.intent == "threshold_count"
    assert got.slots["player"] == "embiid"
    assert got.slots["stat"] == "fouls"
    assert got.slots["threshold"] == 6


def test_a_record_when_keeps_the_player_the_question_names() -> None:
    """#144: "what was the sixers record when maxey scored 15+ points?" routed
    with the team, the stat and the threshold all correct and no `player` at
    all, so the template raised "record_when needs a player" and the agent
    spent 583 seconds failing to write the join. The name was already in the
    grammar `_SUBJECT_OF_HIGH` reads - "maxey scored" - and `record_when` was
    simply never asked. Measured on the warehouse, the answer it now reaches
    is 36-29 in the 65 games Tyrese Maxey scored 15+."""
    got = _ask(
        "what was the sixers record when maxey scored 15+ points?",
        '{"intent":"record_when","stat":"points","threshold":15,"team":"Philadelphia 76ers","season_ref":"current"}',
    )
    assert got.slots["player"] == "maxey"
    assert got.slots["threshold"] == 15


def test_a_record_when_reads_its_threshold_off_the_question() -> None:
    """Measured live (web session, build `178c21f-dirty`): "what was the sixers
    record this season when tyrese maxey had 20+ points?" came back with
    stat='wins' - "record" is what the model had to file under the required
    `stat`, whose enum has no won-lost record, so the nearest value it knew
    won - and the template refused with "needs a known stat and a positive
    threshold, got 'wins'/20" about a question that states its stat plainly.
    "20+ points" is one fact and sets both halves."""
    got = _ask(
        "what was the sixers record this season when tyrese maxey had 20+ points?",
        '{"intent":"record_when","stat":"wins","team":"Philadelphia 76ers","threshold":20,"season_ref":"current"}',
    )
    assert got.slots["stat"] == "points"
    assert got.slots["threshold"] == 20
    boards = _ask(
        "sixers record when maxey had 12+ boards",
        '{"intent":"record_when","stat":"wins","team":"Philadelphia 76ers","threshold":12,"season_ref":"current"}',
    )
    assert boards.slots["stat"] == "rebounds"


def test_two_thresholds_leave_a_record_when_alone() -> None:
    """`record_when` carries ONE threshold, so a question stating two names a
    shape it cannot answer. Picking whichever half the regex found first would
    answer a narrower question than was asked; left alone, it refuses."""
    got = _ask(
        "sixers record when maxey had 20+ points and 5+ assists",
        '{"intent":"record_when","stat":"wins","team":"Philadelphia 76ers","threshold":20,"season_ref":"current"}',
    )
    assert got.slots["stat"] == "wins"


def test_the_router_and_the_templates_agree_on_what_a_stat_word_means() -> None:
    """#164: the router kept its own copy of the vocabulary
    `templates/common.py` reads, and nothing checked that they agreed - the
    shape AGENTS.md calls "one concept, one definition", which
    `check_duplicate_names.py` cannot see because the two names differ.

    There is one definition now (`query/measures.py`), and the router derives
    its map from it, so a spelling dropped there raises at import rather than
    silently narrowing what the threshold grammar understands. This pins that
    the derivation stays a derivation."""
    from association.query.measures import MEASURE_WORDS
    from association.query.router import _THRESHOLD_SPELLINGS, _THRESHOLD_WORDS
    from association.query.templates.common import MEASURE_WORDS as TEMPLATES_MEASURE_WORDS

    assert TEMPLATES_MEASURE_WORDS is MEASURE_WORDS
    assert set(_THRESHOLD_WORDS) == set(_THRESHOLD_SPELLINGS)
    for word, means in _THRESHOLD_WORDS.items():
        assert MEASURE_WORDS[word] == means, word


def test_the_threshold_pair_words_and_their_pattern_cannot_drift() -> None:
    """The alternation is built from `_THRESHOLD_WORDS`, so every word the map
    knows is a word the pattern matches, and every word it matches has a
    meaning. They were a hand-kept list and a map before, which could disagree
    about a word with nothing to notice."""
    from association.query.router import _THRESHOLD_PAIR, _THRESHOLD_WORDS

    for word, means in _THRESHOLD_WORDS.items():
        match = _THRESHOLD_PAIR.search(f"games with 20+ {word} this season")
        assert match is not None, word
        assert match.group(2).casefold() == word
        assert _THRESHOLD_WORDS[match.group(2).casefold()] == means
    # A "+" is still required - "top 10 rebound leaders" is not a condition.
    assert _THRESHOLD_PAIR.search("top 10 rebound leaders") is None


def test_a_subject_keeps_the_first_name_the_question_gave_it(subtests: Any) -> None:
    """#165: `_SUBJECT_OF_HIGH` captured ONE word, so a possessive gave a bare
    surname. Measured over the 261-question corpus, three questions wrote the
    name out and got a clarification listing players they never mentioned -
    "bryant" is Bryant Reeves, Bryant Stith, Carter Bryant and Elijah Bryant,
    and not Kobe. The count grammars already captured a leading word; this one
    does now, gated on the same richer stopword list so "most points curry
    scored" still reads "curry" and never "points curry"."""
    from association.query.router import _subject_named_in

    for question, want in (
        ("kobe bryant's stats vs rockets in the 2009 playoffs ts% each game", "kobe bryant"),
        ("Jaden mcdaniel's vs trail blazer last 5 games", "Jaden mcdaniel"),
        ("steve adam's vs kings last 10 games", "steve adam"),
        ("most points curry scored in a game this season", "curry"),
        ("curry's highest scoring game this season", "curry"),
        # A bare "had"/"has" is a subject position only when a threshold
        # follows: "sixers record when maxey had 10+ rebounds" named nobody,
        # while the same question with "scored" answered.
        ("sixers record when maxey had 10+ rebounds", "maxey"),
        ("warriors record when curry has 30+ points", "curry"),
        # "when" is not part of the name, and a stat's own noun is not a
        # person: these three returned "when maxey", "points" and "game".
        ("Total points scored by the toronto raptors in the last 10 games", None),
        ("least points scored by the wizards in the first half this season", None),
        ("game score nba leader", None),
        ("who scored the most points this season", None),
    ):
        with subtests.test(question=question):
            assert _subject_named_in(question) == want


def test_a_record_when_about_a_team_gains_no_player() -> None:
    """The other half, and why restoring a player here is safe: a record
    question whose threshold is the TEAM's own scoring names no player, and
    must not acquire one. It is answered by `record_when`'s team branch now
    (the Celtics were 27-0 when they scored 120+ in 2026), so the two readings
    have to stay apart at this stage rather than downstream."""
    got = _ask(
        "what was the celtics record when they scored 120 points",
        '{"intent":"record_when","stat":"points","threshold":120,"team":"Boston Celtics","season_ref":"current"}',
    )
    assert "player" not in got.slots


def test_a_threshold_counts_own_player_is_not_overwritten() -> None:
    got = _ask("how many times has embiid fouled out?", '{"intent":"threshold_count","player":"Joel Embiid","stat":"fouls","threshold":6}')
    assert got.slots["player"] == "Joel Embiid"


def test_a_league_wide_threshold_count_stays_league_wide() -> None:
    """The other half: a threshold_count naming nobody must not gain a player."""
    got = _ask("most games with 30+ points this season", '{"intent":"threshold_count","stat":"points","threshold":30}')
    assert "player" not in got.slots


def test_a_threshold_count_named_by_games_with_keeps_its_subject() -> None:
    """#148: a threshold_count player named via "NAME games with ..." - no
    scoring verb, no possessive - still dropped after #138's fix, and the
    answer was the league's ranking to a question naming a real player.
    "jamal murray games with 2 threes including playoffs" routed to
    threshold_count with no player at all and answered "Julian Champagnie had
    the most games with 2+ 3-pointers in the 2026 postseason, with 19" -
    Murray, who has 58 postseason games and 426 career games with 2+
    three-pointers made, was nowhere in it. The full two-word name is kept
    (not just "murray"), because the bare surname is five players who all
    have a 2026 box score (Collin Murray-Boyles, Dejounte, Jamal, Keegan,
    Kris) and would only trade the league-ranking bug for a needless
    clarifying question."""
    got = _ask(
        "jamal murray games with 2 threes including playoffs",
        '{"intent":"threshold_count","stat":"threePointFieldGoalsMade","threshold":2,"season_type":3,"season":2026}',
    )
    assert got.slots["player"] == "jamal murray"


def test_a_threshold_count_named_by_a_bare_surname_before_games_with() -> None:
    """ "Sga games with under 14 fta in his whole career" names its subject the
    same ungrammatical way, but with a single-token nickname rather than a
    first and last name - there is no preceding word to fold in, and none
    should be invented."""
    got = _ask("Sga games with under 14 fta in his whole career", '{"intent":"threshold_count","stat":"freeThrowsAttempted","threshold":14}')
    assert got.slots["player"] == "Sga"


def test_games_with_does_not_capture_a_modifier_as_the_subject() -> None:
    """ "most games with 30+ points this season" must not read "most" as a
    name, and "bam adebayo career games in the month of march" has no verb or
    possessive either, but "career" sits directly before "games" and is not a
    name - the question is left unrestored rather than guessed at, the same
    discipline as the rest of this function."""
    assert "player" not in _ask("most games with 30+ points this season", '{"intent":"threshold_count","stat":"points","threshold":30}').slots
    got = _ask("bam adebayo career games in the month of march", '{"intent":"other"}')
    assert "player" not in got.slots


def test_a_threshold_count_named_by_a_point_games_phrase() -> None:
    """The other corpus-adjacent shape named in #148's next step: a number and
    a stat word standing in for "games with", as in "murray 30 point games"
    or "murray games of 20+ rebounds" - neither has a scoring verb or a
    possessive."""
    assert _ask("murray 30 point games this season", '{"intent":"threshold_count","stat":"points","threshold":30}').slots["player"] == "murray"
    assert _ask("murray games of 20+ rebounds", '{"intent":"threshold_count","stat":"rebounds","threshold":20}').slots["player"] == "murray"


def test_a_possessive_before_games_of_is_not_swallowed_whole() -> None:
    """The name-capture group used to be greedy, which let "murray's games of
    20+ rebounds" consume the whole "murray's" - apostrophe and all - into the
    captured word once a "games of" alternative sat in the same pattern as the
    possessive branch. The two grammars are now separate, so the possessive is
    read by `_SUBJECT_OF_HIGH`'s own `'s` branch exactly as it always was."""
    got = _ask("murray's games of 20+ rebounds", '{"intent":"threshold_count","stat":"rebounds","threshold":20}')
    assert got.slots["player"] == "murray"


def test_a_league_wide_single_game_high_stays_league_wide() -> None:
    """The other half: a question naming nobody must not gain a player. "best"
    is Travis Best, "game" is Jaron Blossomgame and "high" is Haywood
    Highsmith, so a word scan would answer these about somebody."""
    for question in (
        "What was the highest scoring game by a player this year?",
        "who had the most assists in a single game and how many did he have",
        "who scored the most points in a game this season",
    ):
        got = _ask(question, '{"intent":"single_game_high","stat":"points","season_ref":"current"}')
        assert "player" not in got.slots, question


def test_the_model_s_own_player_is_not_overwritten() -> None:
    got = _ask("most points Stephen Curry scored in a game this season", '{"intent":"single_game_high","stat":"points","player":"Stephen Curry"}')
    assert got.slots["player"] == "Stephen Curry"


@pytest.mark.parametrize(
    ("question", "want"),
    [
        # Verbatim from the feed. All three quarter spellings and both halves.
        ("Duncan Robison 1q log", {"period": 1}),
        ("rj barrett 4th qtr log", {"period": 4}),
        ("harrison barnes 1st quarter stats each game vs magic", {"period": 1}),
        ("victor wembanyama vs sacramento first half log", {"half": 1}),
        ("Kd vs clippers 2h at home gamelog", {"half": 2}),
        ("scottie barnes stats 2nd half log without rj", {"half": 2}),
    ],
)
def test_a_named_players_quarter_now_routes_to_a_template(question: str, want: dict[str, int]) -> None:
    """These were forced to the agent because no template answered them - 21 of
    261 feed queries, the largest content gap in the sample. `period_split`
    answers them now, and the period is read from the question text: `period`
    is in ROUTER_SCHEMA but only ever taught for a TEAM's quarter score, so on
    a player's question the model leaves it empty."""
    got = _ask(question, '{"intent":"game_log","player":"Duncan Robinson"}')
    assert got.intent == "period_split"
    assert all(got.slots.get(k) == v for k, v in want.items()), got.slots


@pytest.mark.parametrize("question", ["nba playerspoints by quarter average", "points per quarter for Luka", "Jokic qtrs"])
def test_a_breakdown_across_every_quarter_still_goes_to_the_agent(question: str) -> None:
    """ "by quarter" asks for all four at once, which is a different shape from
    "the third quarter". `period_split` answers one period, so a question that
    names none keeps the old behavior rather than being answered for a period
    nobody asked about."""
    assert _ask(question, '{"intent":"player_stat","player":"Nikola Jokic"}').intent == "other"


def test_a_teams_quarter_is_still_the_teams_template() -> None:
    """team_quarter_points reads the official linescore, which is exact.
    period_split sums shot values, which is not - so a TEAM question must not
    drift onto the derived path."""
    payload = '{"intent":"team_quarter_points","team":"Philadelphia 76ers","period":4,"opponent":"Boston Celtics"}'
    assert _ask("How many points did the 76ers score in the 4th quarter against Boston this season?", payload).intent == "team_quarter_points"


def test_a_quarter_question_naming_no_player_is_not_period_split() -> None:
    """`period_split` answers about a player and nothing else, so a quarter
    question with no player has no subject it can take. It is no longer a
    fall-through either: one that ranks players is period_leaderboard's."""
    got = _ask("knicks 1st quarter scoring leaders playoffs", '{"intent":"leaderboard","team":"New York Knicks"}')
    assert got.intent == "period_leaderboard" and got.slots["period"] == 1
    # A quarter question that ranks nobody and names nobody still falls through.
    assert _ask("1st quarter scoring this season", '{"intent":"leaderboard"}').intent == "other"


def test_a_quarter_ranking_beats_the_teams_own_quarter_template() -> None:
    """The Knicks question routes to team_quarter_points with the team filled
    and no player - exactly the shape the team exemption protects - and
    answering it there gives the TEAM's first quarter where its players' were
    asked for. The ranking words win, and the team narrows the ranking."""
    payload = '{"intent":"team_quarter_points","team":"New York Knicks","period":1}'
    got = _ask("knicks 1st quarter scoring leaders playoffs", payload)
    assert got.intent == "period_leaderboard" and got.slots.get("team") == "New York Knicks"
    # Without the ranking words it is still the team's own quarter.
    plain = _ask("How many points did the Knicks score in the 1st quarter this season?", payload)
    assert plain.intent == "team_quarter_points"


def test_a_period_ranking_with_a_player_named_is_still_that_players_split() -> None:
    """One named player is period_split's question however it is worded - the
    ranking reader must not take a question about one man."""
    got = _ask("who scored the most in the 1st quarter, jokic or embiid", '{"intent":"player_stat","player":"Nikola Jokic"}')
    assert got.intent == "period_split"


@pytest.mark.parametrize(
    ("question", "payload"),
    [
        ("duren v nets 1h gameloh", '{"intent":"game_log","stat":"none","player":"Jalen Duren"}'),
        ("scottie barnes stats 2nd half log", '{"intent":"game_log","stat":"minutes","player":"Scottie Barnes"}'),
    ],
)
def test_a_stat_the_question_never_named_does_not_reach_period_split(question: str, payload: str) -> None:
    """`stat` is REQUIRED in ROUTER_SCHEMA, so the model fills it on a question
    that names none - "none" and "minutes" here, both measured - and
    period_split refused both as asking for a stat it cannot give."""
    got = _ask(question, payload)
    assert got.intent == "period_split" and "stat" not in got.slots


def test_a_stat_the_question_does_name_is_kept_so_it_can_be_refused() -> None:
    """The other direction. "kd rebounds 4th quarter" really asks for
    rebounds, which no shot table holds; dropping the slot would answer his
    POINTS instead, which is the substitution this project refuses."""
    got = _ask("kd rebounds 4th quarter", '{"intent":"player_stat","stat":"rebounds","player":"Kevin Durant"}')
    assert got.intent == "period_split" and got.slots.get("stat") == "rebounds"


@pytest.mark.parametrize(("question", "per_game"), [("rj barrett 4th qtr log", True), ("vj edgecombe 1st quarter scoring by game", True), ("Devin Vassell nba player per game stats 1q", False)])
def test_a_log_is_asked_for_by_the_question_not_assumed(question: str, per_game: bool) -> None:
    """ "per game stats" is an average, and "by game" is a log - the difference
    between one line and a table."""
    got = _ask(question, '{"intent":"game_log","player":"RJ Barrett"}')
    assert bool(got.slots.get("per_game")) is per_game


def test_the_router_prompt_leaves_room_for_the_question_and_the_reply() -> None:
    """ollama truncates an over-length prompt head-first and silently, and the
    router's prompt has no per-question assembly step to raise at, the way
    `PreambleTooLarge` does for the agent. The prompt is a constant, so this
    is the guard: it fails when `ROUTER_PROMPT` plus the longest user line the
    code builds (a previous question and a long question) costs more than
    three quarters of `ROUTER_NUM_CTX`. The prompt was documented as "~430
    tokens" for months after it passed 2,400; what this asserts is measured
    at the same ~4 characters a token as the agent's budget, not with the
    model's tokenizer."""
    long_question = "what was the record of the los angeles lakers against the boston celtics at home in the 2024 regular season, and how many games did they win by ten or more points? " * 2
    user_line = f"(previous question, for context only: {long_question})\nQ: {long_question}"
    cost = estimate_tokens(ROUTER_PROMPT) + estimate_tokens(user_line)
    assert cost <= ROUTER_PROMPT_TOKEN_BUDGET, f"router prompt plus a long question is ~{cost} tokens, over the {ROUTER_PROMPT_TOKEN_BUDGET} budget: shorten ROUTER_PROMPT or raise ROUTER_NUM_CTX"
    # The quarter left over is the chat template and a reply of under 100 tokens of JSON.
    assert ROUTER_NUM_CTX - ROUTER_PROMPT_TOKEN_BUDGET >= 1024


@pytest.mark.parametrize(
    "question",
    [
        "nick nurse coaching record all-time nba in december on the road",
        "who coached the bulls in 1996",
        "how many games did doc rivers coach",
        "head coach of the lakers",
    ],
)
def test_a_coach_question_is_refused_rather_than_handed_to_the_agent(question: str) -> None:
    """Nothing here holds a coach, so the agent would query tables with no such
    column and is then free to fill the silence from its own weights - the
    failure check_coverage exists to stop. The intent is assigned from the
    question's own words, so no model reply can avoid it: the reply below asks
    for something else entirely and is overridden."""
    with patch("association.query.router.ollama.chat", return_value=_reply('{"intent":"player_stat","player":"Nick Nurse","stat":"points"}')):
        got = route("m", question)
    assert got is not None and got.intent == "coach"
    # No slots: the model's are for a question that cannot be answered, and a
    # team or player name reaching the refusal would only invite a wrong cause.
    assert got.slots == {}


@pytest.mark.parametrize(
    "question",
    [
        "how many points does embiid average",
        "what was the lakers record last season",
        "who led the league in scoring in 1996",
        # The surname alone is deliberately not enough: "nurse" and "rivers"
        # are ordinary words, and matching one would be the substring trap
        # players_named_in was written against.
        "nick nurse",
    ],
)
def test_an_ordinary_question_is_not_taken_for_a_coach_question(question: str) -> None:
    with patch("association.query.router.ollama.chat", return_value=_reply('{"intent":"player_stat","player":"Joel Embiid","stat":"points"}')):
        got = route("m", question)
    assert got is not None and got.intent != "coach"


def test_the_coach_refusal_names_the_source_rather_than_blaming_it() -> None:
    """The obvious sentence - "ESPN does not publish coaches" - was probed on
    2026-09-17 and is false: it serves two coach collections, both unusable.
    Saying the source has none would be the wrong-cause refusal this project
    keeps producing, so the sentence says what is actually wrong with them."""
    answer = TEMPLATES["coach"](cast("TemplateContext", None), {}).answer
    assert "No table here holds a coach" in answer
    assert "ESPN does publish coaches" in answer
    assert "Player and team questions are unaffected" in answer


# ---------------- 2-point percentage is not answered as overall FG% (ISSUES.md #114) ----------------


@pytest.mark.parametrize(
    "question",
    [
        "show me sga's 2pt percentage for the past 5 years",
        "lebron's 2-pt percentage over the last 10 years",
        "what is embiid's 2 point percentage this season",
        "kevin durant's two point percentage career",
        "sga 2p pct",
        "sga 2pt pct",
        "sga 2-point field goal percentage",
    ],
)
def test_two_point_percentage_overrides_the_models_guess(question: str) -> None:
    """Measured (2026-09-20 web session, build 178c21f-dirty): "show me sga's
    2pt percentage for the past 5 years" arrived at player_history with
    stat='fieldGoalPct' and answered OVERALL shooting (55.3, 51.9, 53.5, 51.0,
    45.3) under a question that asked for the 2-point split (60.2, 57.1, 57.6,
    53.3, 51.4 - measured against player_season_stats_deduped, makes and
    attempts less the threes). `stat` has no enum in ROUTER_SCHEMA, so the
    model sometimes gets "twoPointFieldGoalPct" right on its own (3 of 10
    times in that session) and sometimes substitutes the nearest one
    ROUTER_PROMPT actually teaches - fieldGoalPct here - which this overrides
    either way, the same discipline _route_game_score uses."""
    got = _ask(question, '{"intent":"player_history","stat":"fieldGoalPct"}')
    assert got.slots["stat"] == "twoPointFieldGoalPct"


def test_two_point_percentage_also_overrides_on_player_stat() -> None:
    got = _ask("sga 2pt percentage this season", '{"intent":"player_stat","player":"Shai Gilgeous-Alexander","stat":"fieldGoalPct"}')
    assert got.slots["stat"] == "twoPointFieldGoalPct"


@pytest.mark.parametrize(
    "question",
    [
        "sga's 3 point percentage this season",  # NOT two-point - three
        "sga's field goal percentage this season",  # plain FG%, no "2"/"two"
        "sga's effective field goal percentage this season",  # efg_pct, unrelated
        "sga scored 20 points in the 2nd quarter",  # "2nd" is not "2 point"
        "sga's 42 point game",  # a threshold, not a shooting split
    ],
)
def test_two_point_percentage_pattern_does_not_fire_on_near_misses(question: str) -> None:
    got = _ask(question, '{"intent":"player_history","stat":"fieldGoalPct"}')
    assert got.slots["stat"] != "twoPointFieldGoalPct"


def test_two_point_percentage_is_left_alone_outside_its_two_intents() -> None:
    """player_compare reads a player's stat line through PLAYER_STAT_COLUMNS,
    never SHOOTING_STATS - a "twoPointFieldGoalPct" value there would be
    silently unreadable, the same reason game_score is scoped away from it."""
    got = _ask("compare sga and embiid on 2pt percentage", '{"intent":"player_compare","players":["Shai Gilgeous-Alexander","Joel Embiid"],"stat":"fieldGoalPct"}')
    assert got.slots.get("stat") != "twoPointFieldGoalPct"


def test_a_question_actually_about_field_goal_percentage_still_emits_it() -> None:
    """The guard has to leave the ordinary case alone, not just avoid the
    near-miss ones above - the model's own correct answer is not overridden
    when the question never says "2"/"two"."""
    got = _ask("sga's field goal percentage this season", '{"intent":"player_history","stat":"fieldGoalPct"}')
    assert got.slots["stat"] == "fieldGoalPct"


# ---------------- a leaderboard refuses a distance ranking naming the real cause (ISSUES.md #114) ----------------


def test_leaderboard_shot_distance_gets_the_sentinel_stat_and_drops_any_player() -> None:
    """Measured: "who lead the league in avg 3 point distance" arrived with
    stat='threePointFieldGoalPct' (the nearest real metric) and answered Luke
    Kennard's 3-point PERCENTAGE, 47.8% - a real, fluently wrong number. `stat`
    is overridden to a sentinel `templates.players.leaderboard` refuses on by
    name, and any `player` the router filled is dropped with it - `leaderboard`
    never reads one for real, and a filler value ("player": "player") would
    otherwise reach override_invented_players first and refuse for the wrong
    cause."""
    got = _ask("who lead the league in avg 3 point distance", '{"intent":"leaderboard","stat":"threePointFieldGoalPct"}')
    assert got.slots["stat"] == "shot_distance"
    assert "player" not in got.slots


def test_leaderboard_shot_distance_also_fires_on_the_filler_player_shape() -> None:
    """Measured: "who lead the league in shot distance for 3 point shots"
    arrived with a filler `player: "player"` and no stat naming a real metric,
    and was refused for naming a player the question does not mention - the
    wrong cause, since no distance leaderboard exists either way."""
    got = _ask("who lead the league in shot distance for 3 point shots", '{"intent":"leaderboard","player":"player"}')
    assert got.slots["stat"] == "shot_distance"
    assert "player" not in got.slots


@pytest.mark.parametrize(
    "question",
    [
        "who led the league in scoring",
        "who lead the league in 3 point percentage",
        "how far away does wembanyama shoot from",  # a real player_stat/team_record shape, not this intent
    ],
)
def test_leaderboard_shot_distance_pattern_does_not_fire_on_ordinary_leaderboards(question: str) -> None:
    got = _ask(question, '{"intent":"leaderboard","stat":"points","player":"filler"}')
    assert got.slots.get("stat") != "shot_distance"


def test_shot_distance_sentinel_is_left_alone_outside_leaderboard() -> None:
    """The `shot_distance` INTENT is a real template about one named player
    (templates/shots.py) - this sentinel is a different thing, scoped to
    `leaderboard` only, and must never touch that intent's own player slot."""
    got = _ask("what is curry's average shot distance this season", '{"intent":"shot_distance","player":"Stephen Curry"}')
    assert got.slots.get("stat") != "shot_distance"
    assert got.slots.get("player") == "Stephen Curry"


def test_a_relative_window_is_a_season_count_where_the_limit_already_counts_seasons() -> None:
    """Found on the merged tree, where #140 (past N seasons -> `since`) and
    #114 (2pt percentage) met: a history's `limit` counts SEASONS, so "the
    past 5 years" is that limit. Handing it `since` instead gave the template
    a slot it does not honor and refused a question that answers."""
    got = _asking('{"intent":"player_history","stat":"fieldGoalPct","player":"Shai Gilgeous-Alexander","limit":5,"season_type":2}', "show me sga's 2pt percentage for the past 5 years")
    assert got.slots.get("limit") == 5 and "since" not in got.slots and got.slots["stat"] == "twoPointFieldGoalPct"
    # Every other intent still reads the window as a span.
    log = _asking('{"intent":"game_log","player":"Tyrese Maxey","limit":2,"season_type":2}', "maxey's games against boston in the past two seasons")
    assert log.slots.get("since") == current_season() - 1 and "limit" not in log.slots


@pytest.mark.parametrize(
    "question",
    [
        "show me the 76ers record when both Embiid and Paul George played",
        "show me PHI record with Embiid and Paul George",
        "PHI record when Embiid and Paul George play",
        "PHI record when Embiid with Paul George",
    ],
)
def test_a_record_when_two_players_played_is_a_with_without_question(question: str) -> None:
    """#156: four phrasings in one web session, four refusals - record_when
    divides a season by a NUMBER a player reached, and none of these names
    one. Both players are read, including from either side of "A with B",
    where reading only the far side answered about Paul George alone."""
    got = _ask(question, '{"intent":"record_when","stat":"wins","team":"Philadelphia 76ers","season":2026}')
    assert got.intent == "with_without"
    assert got.slots.get("with_player") == ["Embiid", "Paul George"]


def test_a_record_when_a_player_reaches_a_number_keeps_its_intent() -> None:
    """The control: a threshold is what record_when divides by, so a question
    that names one is untouched however the players are worded."""
    got = _ask("Sixers record when Embiid scores 30 points this season", '{"intent":"record_when","stat":"points","threshold":30,"team":"Philadelphia 76ers","season":2026}')
    assert got.intent == "record_when" and got.slots["threshold"] == 30 and "with_player" not in got.slots


def test_a_teams_record_when_a_player_reaches_a_number_is_record_when() -> None:
    """yardstick-v2 F087 "show me stats for sixers when maxey scored 20+
    points": the model files player_stat (Maxey's own average) where the
    team's record under the condition was asked. Two readers agree before it
    moves - "when <someone> scored" and a threshold in the text."""
    got = _asking('{"intent":"player_stat","stat":"points","player":"Maxey","season":2026}', "show me stats for sixers when maxey scored 20+ points")
    assert got.intent == "record_when"
    assert got.slots["threshold"] == 20
    # No "when <someone> scores" clause: the intent the model chose stands.
    stays = _asking('{"intent":"player_stat","stat":"points","player":"Maxey","season":2026}', "maxey stats in games with 20+ points")
    assert stays.intent == "player_stat"


def test_a_period_split_window_the_question_never_named_is_dropped() -> None:
    """yardstick-v2 F058/F060: "each game" and "games" questions arrived
    with a filler limit of 1 (and an order), and period_split printed one
    row under a whole-season total. The period's own ordinal is not a
    window: "first half games" keeps no limit, "last 5 games" keeps its 5."""
    each = _asking('{"intent":"period_split","player":"Harrison Barnes","opponent":"Orlando Magic","order":"recent","limit":1,"period":1}', "harrison barnes 1st quarter stats each game vs magic")
    assert "limit" not in each.slots and "order" not in each.slots
    games = _asking('{"intent":"period_split","player":"Rudy Gobert","order":"recent","limit":1,"half":1}', "Rudy gobert first half games this season")
    assert "limit" not in games.slots and "order" not in games.slots
    last5 = _asking('{"intent":"period_split","player":"Zach Collins","order":"recent","limit":5,"period":1,"split":"starter"}', "zach collins first quarter stats last 5 games as a starter")
    assert last5.slots["limit"] == 5 and last5.slots["order"] == "recent"


def test_an_opponent_that_is_the_without_list_is_dropped() -> None:
    """yardstick-v2 F158: the teammates named after "without" came back as
    the `opponent` too, and a log against no team fell through. A real team
    beside the without list is kept."""
    got = _asking(
        '{"intent":"game_log","stat":"minutes","player":"Bane","opponent":"Anthony Black, Franz Wagner","limit":10,"season":2026}', "bane game log without anthony black and franz wagner this season"
    )
    assert got.slots["without"] == ["anthony black", "franz wagner"]
    assert "opponent" not in got.slots
    kept = _asking('{"intent":"game_log","stat":"minutes","player":"Bane","opponent":"Boston Celtics","limit":10,"season":2026}', "bane game log vs boston without franz wagner this season")
    assert kept.slots["opponent"] == "Boston Celtics"
