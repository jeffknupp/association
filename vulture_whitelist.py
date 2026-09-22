"""Names vulture reports as unused that are in fact used - by a framework, or by callers outside this repo.

Vulture reads this file as source (``[tool.vulture]`` in ``pyproject.toml``)
and never runs it: each bare name below counts as a use of every symbol with
that name. So every entry says where the symbol lives and who really calls it,
which is what lets an entry whose caller has gone be recognized and deleted.
Do not add a name just to quiet a finding - a dead symbol hidden here is
exactly what the check exists to find.
"""

# ruff: noqa: B018, F821 - bare, unbound names are the whole mechanism

# association/cli/commands.py: registered by Click's @data.command / @cli.command.
data_pull
data_load
data_check
web

# association/web/app.py, inside create_app: registered by FastAPI's @app.get.
health
ask_stream

# association/web/app.py, HealthResponse: pydantic response fields, serialized
# by FastAPI and read as JSON by the page.
warehouse_ready
output_dir
models
ollama_ready

# association/web/app.py, TierResponse (#71): pydantic response fields,
# serialized by FastAPI and read as JSON by the page's coverage pills.
partial_seasons
phantom_seasons

# association/query/answer.py: public API (versionadded 2.0.0) for callers that
# dispatch on Artifact.kind; nothing in this repo needs to.
ARTIFACT_KINDS

# scripts/check_docs_markup.py, _ProseText: html.parser.HTMLParser
# hooks, called by the base class. ``attrs`` is the base signature's parameter.
handle_starttag
handle_endtag
handle_data
attrs

# association/query/templates/common.py: scoped_team, the team relation's
# counterpart to scoped_player (step 3, C4). game_log's team half and
# head_to_head narrow through team_games() directly, which does not need a
# name resolved ahead of a span the way an ambiguous PLAYER name does, so
# neither calls scoped_team yet - it is the contract the two ports that
# follow C4 (player_splits/streak's team branches and with_without, currently
# on conditions._team_games) are meant to settle their team and span through,
# the same order scoped_player already establishes for a player.
scoped_team
