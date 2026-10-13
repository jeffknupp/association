"""The team-games relation's reads a team reader takes, called over the team
fixture warehouse (``test_team_templates.team_ctx``): the two checks a
team's record makes (``compose.team_records``) and the team compiler's
narrowed total (``compose.team``), each built AND run on the relation's
shared steps (:mod:`association.query.team_relation`) and handed back as
rows (Phase 4, step 5: the readers ran each statement themselves until
then).
"""

from __future__ import annotations

from test_team_templates import S, team_ctx  # noqa: F401 - the fixture, by name

from association.query.answer import AnswerContext
from association.query.team_games import TeamNarrowed
from association.query.team_metrics import games_scope
from association.query.team_relation import team_game_list_gaps, team_games_total, team_season_game_counts


def test_season_game_counts_tell_no_games_from_none_played(team_ctx: AnswerContext) -> None:  # noqa: F811 - the fixture
    scope, params = games_scope(2, S)
    # Five regular-season games for the league, the Cup final not among them; three are the Knicks'.
    assert team_season_game_counts(team_ctx.con, scope, params, "18") == (10, 3)
    assert team_season_game_counts(team_ctx.con, scope, params, "30") == (10, 0)
    scope, params = games_scope(2, 2015)
    assert team_season_game_counts(team_ctx.con, scope, params, "18") == (0, 0)


def test_game_list_gaps_name_the_seasons_the_tally_is_short(team_ctx: AnswerContext) -> None:  # noqa: F811 - the fixture
    def gaps(team_id: str) -> list[tuple[int, int, int]]:
        # A postseason is counted by the calendar year it was played in.
        narrowed = TeamNarrowed(base=["tg.team_id = ?", "tg.season_type = 3", "year(tg.eastern_date) = ?"], base_params=[team_id, S])
        return [(int(season), int(listed), int(played)) for season, listed, played in team_game_list_gaps(team_ctx.con, narrowed, team_id, 3, season=S, since=None, until=None, floor=1994)]

    # Four postseason games listed against the Celtics' own five; the Knicks' four agree.
    assert gaps("2") == [(S, 4, 5)]
    assert gaps("18") == []


def test_a_games_total_sums_the_column_with_its_games_and_record(team_ctx: AnswerContext) -> None:  # noqa: F811 - the fixture
    narrowed = TeamNarrowed(base=["tg.team_id = ?", "tg.season_type = 3", "tg.season = ?"], base_params=["18", S])
    assert team_games_total(team_ctx.con, narrowed, "tg.team_score") == (434, 4, 3, 1)
    assert team_games_total(team_ctx.con, narrowed, "tg.opponent_score") == (406, 4, 3, 1)
    nothing = TeamNarrowed(base=["tg.team_id = ?", "tg.season_type = 3", "tg.season = ?"], base_params=["30", S])
    assert team_games_total(team_ctx.con, nothing, "tg.team_score") == (None, 0, None, None)
