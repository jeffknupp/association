"""Tests for the intent router's validation layer - the part that decides what
the model is and is not trusted to have gotten right."""

import re
from typing import Any
from unittest.mock import patch

import ollama
import pytest
from ollama import ChatResponse, Message

from association.query.router import ORDER_INTENTS, ORDER_WORDS, ROUTER_PROMPT, ROUTER_SCHEMA, SIDE_VALUES, Route, route
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
    # Which one it is comes from the question, not the model - see the scoping
    # tests at the end of this file for the measurements behind that.
    assert _ask("Who led the league in scoring in the playoffs?", '{"intent":"leaderboard","stat":"points","season_type":"playoffs"}').slots["season_type"] == 3


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


def test_a_player_quarter_question_still_forces_the_agent_even_if_misrouted() -> None:
    """Defensive: if the router ever emits team_quarter_points alongside a
    named player (it shouldn't - the prompt says this intent is never for a
    player), the override must still win rather than trust that slot combo."""
    payload = '{"intent":"team_quarter_points","team":"Philadelphia 76ers","period":4,"player":"Joel Embiid"}'
    with patch("association.query.router.ollama.chat", return_value=_reply(payload)):
        got = route("m", "How many points did Embiid score in the 4th quarter against Boston?")
    assert got is not None and got.intent == "other"


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


def test_the_order_is_only_added_where_a_template_honours_it() -> None:
    """check_scope REFUSES a scoping slot the template cannot honour, so adding
    `order` to a player_stat question would not sharpen the answer - it would
    cost one, by sending a question that works today to the agent instead."""
    got = _asking('{"intent":"player_stat","stat":"points","player":"Stephen Curry"}', "how many points did curry score in his last game")
    assert "order" not in got.slots


def test_the_order_values_match_the_router_schema() -> None:
    """Same shape as the side check above: a value here the schema cannot emit
    would be unreachable, and one it emits that is missing here gets dropped."""
    assert set(ORDER_WORDS) == set(ROUTER_SCHEMA["properties"]["order"]["enum"])


def test_the_order_intents_are_the_ones_that_honour_order() -> None:
    """Two hand-maintained lists of the same intents, kept apart so the router
    does not import the templates. An intent honouring `order` and missing here
    keeps the bug this fixed; one listed here that does not honour it turns
    into a fall-through."""
    from association.query.templates import HONORED_SCOPING

    assert ORDER_INTENTS == frozenset(intent for intent, honored in HONORED_SCOPING.items() if "order" in honored)


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


@pytest.mark.parametrize(
    ("question", "without"),
    [
        ("Podziemski game log without curry", "curry"),
        ("jalen Duren stats without Cade Cunningham this season", "Cade Cunningham"),
        ("Celtics record without Tatum", "Tatum"),
        ("most games without a turnover", None),  # names nobody
    ],
)
def test_a_missing_teammate_is_read_from_the_question(question: str, without: str | None) -> None:
    assert _ask(question, '{"intent":"game_log"}').slots.get("without") == without


def test_with_a_teammate_is_only_read_for_the_template_that_uses_it() -> None:
    """ "with" is everywhere ("games with 30+ points"), so outside with_without it
    would be noise at best."""
    assert _ask("jjj stats with ja morant last season", '{"intent":"with_without"}').slots["with_player"] == "ja morant"
    assert "with_player" not in _ask("jjj stats with ja morant last season", '{"intent":"player_stat"}').slots


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


@pytest.mark.parametrize("question", ["rj barrett 4th qtr log", "kd q4 points last game", "tatum first half stats", "harrison barnes 1st q stats"])
def test_abbreviated_quarters_and_halves_go_to_the_agent(question: str) -> None:
    """ "rj barrett 4th qtr log" slipped past a pattern that only knew "quarter",
    and game_log answered with his whole last game."""
    assert _ask(question, '{"intent":"game_log","player":"X"}').intent == "other"


def test_a_team_half_is_not_a_quarter_either() -> None:
    assert _ask("Celtics 2nd half scoring this season", '{"intent":"team_quarter_points","team":"Boston Celtics","period":2}').intent == "other"
    assert _ask("76ers 4th qtr points vs boston", '{"intent":"team_quarter_points","team":"Philadelphia 76ers","period":4}').intent == "team_quarter_points"


@pytest.mark.parametrize(
    ("question", "playoff_round"),
    [
        ("tatum stats in the 2024 finals", "finals"),
        ("Chris Paul playoff game 7 record", "game 7"),
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
    ],
)
def test_a_range_of_seasons_replaces_the_one_the_model_picked(question: str, since: int | None, until: int | None) -> None:
    """Measured: "since 2020" became season=2020, answered as one season."""
    got = _ask(question, '{"intent":"leaderboard","stat":"points","season":2020}')
    assert got.slots.get("since") == since and got.slots.get("until") == until
    if since is not None:
        assert "season" not in got.slots


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


@pytest.mark.parametrize(
    "question",
    ["Celtics record on back to backs", "Lakers record in overtime this season", "76ers record in october", "Knicks record vs the east", "best record since the all-star break"],
)
def test_a_situation_no_template_filters_on_is_a_scoping_slot(question: str) -> None:
    assert "situation" in _ask(question, '{"intent":"team_record","team":"X"}').slots


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
