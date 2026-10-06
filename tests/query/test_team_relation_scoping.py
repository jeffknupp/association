"""Step 3, C4b: the team-games relation gets one scoping declaration too.

The team counterpart of the two C3 gates in ``test_templates.py``
(``test_templates_on_the_relation_declare_no_scoping_of_their_own`` and
``test_templates_on_the_relation_do_not_narrow_it_themselves``), over
``TEAM_RELATION_SCOPING``/``TEAM_RELATION_SCOPING_EXCLUDED``/``team_relation_scoping``
instead of the player relation's own three. Kept in its own file rather than
added to ``test_templates.py``, which another agent owns this round.
"""

from __future__ import annotations

import inspect
import re
from typing import Any

from association.query.compose.meetings import read_head_to_head
from association.query.compose.periods import read_team_quarter_points
from association.query.compose.plan import words_stated
from association.query.team_relation import TEAM_RELATION_SCOPING, TEAM_RELATION_SCOPING_EXCLUDED, team_relation_scoping


def test_team_templates_declare_scoping_through_the_shared_helper() -> None:
    """Every template on the team-games relation honors exactly
    ``TEAM_RELATION_SCOPING``, plus whatever it names as ``extra`` (a cell
    that is its own, not the relation's - ``situation``/``split`` for
    ``team_record``'s calendar-month reading), less its own exclusions - and
    every exclusion in ``TEAM_RELATION_SCOPING_EXCLUDED`` names a real cell
    with a written reason. A template that listed its own frozenset instead
    of going through ``team_relation_scoping`` would fail this the same way
    a player-relation template would fail the C3 gate: the two declarations
    would silently drift out of step with the code.
    """
    # (intent, extra cells that are the template's own rather than the
    # relation's - see each HONORED_SCOPING entry's own comment for why).
    # `situation` moved from `team_record`'s own extra into `TEAM_RELATION_SCOPING`
    # itself (step 3, K1): every team template now honors it through the base
    # set, `team_record` included, so it is no longer listed as its own here.
    # The team shapes' readers declare in compose.plan.words_stated (Phase 2, slice (iv)).
    on_the_relation = {"team_record": {"split", "season_type_unstated"}, "head_to_head": set(), "team_quarter_points": {"period", "half"}}
    for intent, extra in on_the_relation.items():
        excluded = TEAM_RELATION_SCOPING_EXCLUDED.get(intent, {})
        for slot, reason in excluded.items():
            assert slot in TEAM_RELATION_SCOPING, f"{intent} excludes {slot!r}, which is not a team-relation cell at all"
            assert reason.strip(), f"{intent} excludes {slot!r} without a reason"
        assert words_stated(intent) == (TEAM_RELATION_SCOPING | extra) - set(excluded), f"{intent} declares scoping of its own rather than through team_relation_scoping"


def test_team_relation_scoping_helper_matches_the_declared_dict() -> None:
    """``team_relation_scoping`` is the only thing that may build a
    HONORED_SCOPING entry for a template on this relation - proven by
    reconstructing each one from the helper directly, the way the test above
    checks the dict but this checks the FUNCTION agrees with itself."""
    assert team_relation_scoping("team_record", "split", "season_type_unstated") == words_stated("team_record")
    # team_leaderboard's reader (compose.team_stats, Phase 2, step 4) declares through the same helper.
    assert team_relation_scoping("team_leaderboard") == words_stated("team_leaderboard")
    assert team_relation_scoping("head_to_head") == words_stated("head_to_head")
    assert team_relation_scoping("team_quarter_points", "period", "half") == words_stated("team_quarter_points")


def _source_with_private_steps(handler: Any) -> str:
    """A template's source plus every private function of its module it
    reaches, transitively - the team-relation counterpart of
    ``test_templates.py``'s own ``_source_with_private_steps``, kept as a
    separate copy for the reason every shared step in this port is: a change
    to one relation's walker must not silently reach the other's tests.

    Stops on its own at ``team_games`` and ``scoped_team`` (and every other
    shared step): the regex only follows a name that STARTS WITH an
    underscore, and neither of those does, by design - see their own module
    docstrings.
    """
    module = inspect.getmodule(handler)
    assert module is not None
    seen, todo, out = set(), [handler], []
    while todo:
        fn = todo.pop()
        if fn in seen or not inspect.isfunction(fn):
            continue
        seen.add(fn)
        src = inspect.getsource(fn)
        out.append(src)
        for name in set(re.findall(r"\b(_[a-z][a-z0-9_]*)\(", src)):
            step = getattr(module, name, None)
            if step is not None:
                todo.append(step)
    return "\n".join(out)


# ``tg.opponent_id = ?`` / ``tg.side = ?`` / ``tg.eastern_date = ?`` are the
# clauses ``common.team_games`` itself writes for opponent/venue/date; a
# template that wrote one of these three literally would be narrowing the
# relation itself instead of going through the shared step - the team
# counterpart of the C3 gate's own forbidden tokens
# (``pgl.opponent_team_id = ?`` and friends) over the player relation.
_FORBIDDEN_TOKENS = ("tg.opponent_id = ?", "tg.side = ?", "tg.eastern_date = ?")

# Templates that read every one of their cells through `common.team_games` -
# the only ones this gate can hold to "never write these tokens itself".
# `head_to_head` and `team_quarter_points` are both fully on the relation
# (step 3, C4b for the second; C4 already had the first). `team_record` and
# `team_leaderboard` are NOT here, and each is exempted for a different,
# written reason rather than silently dropped - see the test below.
_FULLY_ON_THE_SHARED_STEP = {"head_to_head": read_head_to_head, "team_quarter_points": read_team_quarter_points}


def test_team_templates_on_the_shared_step_do_not_narrow_it_themselves() -> None:
    """The other half of the pair: a template that reads every cell through
    ``common.team_games`` never writes the relation's own opponent/venue/date
    clauses by hand - the narrowing lives in one place, so a new cell reaches
    every template that calls it at once. Watched to fail: removing the
    ``opponent=False`` guard on ``TeamNarrowed.filters`` (so
    ``team_quarter_points`` built its own ``" vs the X"`` string) does not
    trip this - the forbidden tokens are the WHERE-clause text
    ``team_games`` writes, never the answer's prose - but writing
    ``narrowed.narrow("tg.opponent_id = ?", ...)`` directly inside
    ``team_quarter_points`` does.
    """
    for intent, reader in _FULLY_ON_THE_SHARED_STEP.items():
        source = _source_with_private_steps(reader)
        for token in _FORBIDDEN_TOKENS:
            assert token not in source, f"{intent} narrows the relation itself ({token!r}); use common.team_games"


def test_team_record_is_exempted_from_the_shared_step_for_a_written_reason() -> None:
    """``team_record`` is deliberately NOT in ``_FULLY_ON_THE_SHARED_STEP``: its
    own season-record path (``_record_narrowed``, over
    ``team_metrics.games_scope``) excludes the NBA Cup final from a
    regular-season record, which ``common.team_games`` does not do (a plain
    game list or a head-to-head count is not a record - see both modules'
    own docstrings) - so it narrows an opponent
    (``compose.team_records._record_games``' ``narrowed.narrow("tg.opponent_id = ?", ...)``)
    on its own, bespoke ``TeamNarrowed``, never through ``common.team_games``.
    This is watched by asserting the token IS present, so a future refactor
    that quietly moves ``team_record`` onto the shared step (fixing this
    exemption for real) fails this assertion and is a signal to delete it,
    not a false pass.
    """
    from association.query.compose.team_records import read_team_record

    source = _source_with_private_steps(read_team_record)
    assert "tg.opponent_id = ?" in source, "team_record no longer narrows opponent by hand - delete this exemption and add it to _FULLY_ON_THE_SHARED_STEP"


def _team_leaderboard_source() -> str:
    """The team ranking's reader and the relation's statements it runs
    (``compose.team_stats``, ``team_seasons``; Phase 2, step 4)."""
    import association.query.compose.team_stats as reader
    import association.query.team_seasons as relation

    return inspect.getsource(reader) + inspect.getsource(relation)


def test_team_leaderboard_never_narrows_opponent_or_date() -> None:
    """``team_leaderboard`` never reaches ``common.team_games`` at all: its
    ordinary path reads the season line's and the standings' statements
    (``team_seasons.team_lines_statement``/``team_records_statement``), and
    its ``since`` path (``team_since_records_statement``) builds a
    LEAGUE-WIDE ``TeamNarrowed`` with no team named. ``opponent`` and
    ``date`` are excluded from what its words state
    (``TEAM_RELATION_SCOPING_EXCLUDED``) precisely because nothing here
    narrows to either. ``venue`` is checked separately below: unlike the
    other two, team_leaderboard DOES honor it, and does so by hand,
    predating this port."""
    source = _team_leaderboard_source()
    for token in ("tg.opponent_id = ?", "tg.eastern_date = ?"):
        assert token not in source, f"team_leaderboard narrows the relation itself ({token!r})"


def test_team_leaderboard_venue_narrowing_is_a_pre_existing_exemption() -> None:
    """``team_leaderboard``'s postseason venue split (the record metrics,
    ``team_seasons.team_venue_records_statement``) writes ``tg.side = ?``
    directly rather than through ``common.team_games`` - a real,
    hand-narrowed exemption like ``team_record``'s opponent, and one this
    port did not create. Watched the same way ``team_record``'s own
    exemption is: if a future change moves this onto the shared step, this
    assertion starts failing and says to delete the exemption rather than
    silently keep exempting nothing.
    """
    assert "tg.side = ?" in _team_leaderboard_source(), "team_leaderboard no longer narrows venue by hand - delete this exemption"
