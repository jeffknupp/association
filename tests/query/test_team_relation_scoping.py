"""The team-games relation's one scoping declaration.

The team counterpart of the two source-reading tests in ``test_readers.py``
(a reader on the relation declares no scoping of its own, and does not
narrow the relation itself), over
``TEAM_RELATION_SCOPING``/``TEAM_RELATION_SCOPING_EXCLUDED`` instead of the
player relation's own two.
"""

from __future__ import annotations

import inspect
import re
from typing import Any

from association.query.compose.meetings import read_head_to_head
from association.query.compose.periods import read_team_quarter_points
from association.query.compose.plan import cells_stated
from association.query.reading import (
    TEAM_MEETINGS,
    TEAM_PERIOD_TOTAL,
    TEAM_RANKING,
    TEAM_RECORD,
    DeclineReason,
    PointShape,
)
from association.query.team_relation import TEAM_RELATION_SCOPING, TEAM_RELATION_SCOPING_EXCLUDED


def test_team_readers_declare_scoping_through_the_shared_table() -> None:
    """Every reader on the team-games relation honors exactly
    ``TEAM_RELATION_SCOPING``, plus what its shape's row takes beyond it (a
    cell that is its own, not the relation's - ``split`` for ``team_record``'s
    calendar-month reading), less its row's exclusions - and every exclusion
    names a team-relation cell with a written reason. A reader that listed
    its own frozenset would fail this the same way a player-relation reader
    fails its own source-reading test: the two declarations would silently
    drift out of step with the code.
    """
    on_the_relation: dict[PointShape, set[str]] = {TEAM_RECORD: {"split"}, TEAM_MEETINGS: set(), TEAM_PERIOD_TOTAL: set(), TEAM_RANKING: {"rate"}}
    for words, extra in on_the_relation.items():
        row = TEAM_RELATION_SCOPING_EXCLUDED[words]
        assert row.taken == extra, words
        for cell, reason in row.unstated.items():
            assert cell in TEAM_RELATION_SCOPING | row.taken, f"{words} excludes {cell!r}, which is not a team-relation cell at all"
            assert isinstance(reason, DeclineReason), f"{words} excludes {cell!r} without a reason"
        assert cells_stated(words) == (TEAM_RELATION_SCOPING | extra) - set(row.unstated), f"{words} declares scoping of its own rather than through its row"
    # What each reader's words state.
    assert cells_stated(TEAM_RECORD) == (TEAM_RELATION_SCOPING | {"split"}) - {"date", "window", "period"}
    assert cells_stated(TEAM_RANKING) == {"venue", "range"}
    assert cells_stated(TEAM_MEETINGS) == TEAM_RELATION_SCOPING - {"game_n", "situation", "period"}
    assert cells_stated(TEAM_PERIOD_TOTAL) == TEAM_RELATION_SCOPING


def _source_with_private_steps(handler: Any) -> str:
    """A reader's source plus every private function of its module it
    reaches, transitively - the team-relation counterpart of
    ``test_readers.py``'s own ``_source_with_private_steps``, kept as a
    separate copy so that a change to one relation's walker cannot silently
    reach the other's tests.

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
# clauses ``team_relation.team_games`` itself writes for opponent/venue/date;
# a reader that wrote one of these three literally would be narrowing the
# relation itself instead of going through the shared step - the team
# counterpart of the player relation's forbidden tokens
# (``pgl.opponent_team_id = ?`` and friends).
_FORBIDDEN_TOKENS = ("tg.opponent_id = ?", "tg.side = ?", "tg.eastern_date = ?")

# Readers that read every one of their cells through
# ``team_relation.team_games`` - the only ones this gate can hold to "never
# write these tokens itself": two teams' meetings and a team's period total.
# A team's record and the team ranking are NOT here, and each is exempted for
# a different, written reason rather than silently dropped - see the tests
# below.
_FULLY_ON_THE_SHARED_STEP = {TEAM_MEETINGS: read_head_to_head, TEAM_PERIOD_TOTAL: read_team_quarter_points}


def test_team_readers_on_the_shared_step_do_not_narrow_it_themselves() -> None:
    """The other half of the pair: a reader that reads every cell through
    ``team_relation.team_games`` never writes the relation's own
    opponent/venue/date clauses by hand - the narrowing lives in one place,
    so a new cell reaches every reader that calls it at once. Watched to
    fail: removing the ``opponent=False`` guard on ``TeamNarrowed.filters``
    (so ``read_team_quarter_points`` built its own ``" vs the X"`` string)
    does not trip this - the forbidden tokens are the WHERE-clause text
    ``team_games`` writes, never the answer's prose - but writing
    ``narrowed.narrow("tg.opponent_id = ?", ...)`` directly inside
    ``read_team_quarter_points`` does.
    """
    for shape, reader in _FULLY_ON_THE_SHARED_STEP.items():
        source = _source_with_private_steps(reader)
        for token in _FORBIDDEN_TOKENS:
            assert token not in source, f"{shape} narrows the relation itself ({token!r}); use team_relation.team_games"


def test_team_record_is_exempted_from_the_shared_step_for_a_written_reason() -> None:
    """A team's record is deliberately NOT in ``_FULLY_ON_THE_SHARED_STEP``: its
    own season-record path (``_record_narrowed``, over
    ``team_metrics.games_scope``) excludes the NBA Cup final from a
    regular-season record, which ``team_relation.team_games`` does not do (a plain
    game list or a head-to-head count is not a record - see both modules'
    own docstrings) - so it narrows an opponent
    (``compose.team_records._record_games``' ``narrowed.narrow("tg.opponent_id = ?", ...)``)
    on its own, bespoke ``TeamNarrowed``, never through ``team_relation.team_games``.
    This is watched by asserting the token IS present, so a refactor that
    quietly moves the record onto the shared step (ending this exemption for
    real) fails this assertion and is a signal to delete it, not a false
    pass.
    """
    from association.query.compose.team_records import read_team_record

    source = _source_with_private_steps(read_team_record)
    assert "tg.opponent_id = ?" in source, "a team's record does not narrow opponent by hand - delete this exemption and add it to _FULLY_ON_THE_SHARED_STEP"


def _team_ranking_source() -> str:
    """The team ranking's reader and the relation's statements it runs
    (``compose.team_stats``, ``team_seasons``)."""
    import association.query.compose.team_stats as reader
    import association.query.team_seasons as relation

    return inspect.getsource(reader) + inspect.getsource(relation)


def test_the_team_ranking_never_narrows_opponent_or_date() -> None:
    """The team ranking never reaches ``team_relation.team_games`` at all: its
    ordinary path reads the season line's and the standings' statements
    (``team_seasons.team_lines_statement``/``team_records_statement``), and
    its ``since`` path (``team_since_records_statement``) builds a
    LEAGUE-WIDE ``TeamNarrowed`` with no team named. ``opponent`` and
    ``date`` are excluded from what its words state
    (``TEAM_RELATION_SCOPING_EXCLUDED``) precisely because nothing here
    narrows to either. ``venue`` is checked separately below: unlike the
    other two, the team ranking DOES honor it, and does so by hand."""
    source = _team_ranking_source()
    for token in ("tg.opponent_id = ?", "tg.eastern_date = ?"):
        assert token not in source, f"the team ranking narrows the relation itself ({token!r})"


def test_the_team_rankings_venue_narrowing_is_a_written_exemption() -> None:
    """The team ranking's postseason venue split (the record metrics,
    ``team_seasons.team_venue_records_statement``) writes ``tg.side = ?``
    directly rather than through ``team_relation.team_games`` - a real,
    hand-narrowed exemption like a team's record's opponent. Watched the
    same way that exemption is: if a change moves this onto the shared
    step, this assertion starts failing and says to delete the exemption
    rather than silently keep exempting nothing.
    """
    assert "tg.side = ?" in _team_ranking_source(), "the team ranking does not narrow venue by hand - delete this exemption"
