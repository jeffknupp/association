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

# association/web/app.py, inside create_app: registered by FastAPI's @app.get
# / @app.post.
health
ping
ask_stream
add_note

# association/web/app.py, HealthResponse: pydantic response fields, serialized
# by FastAPI and read as JSON by the page.
warehouse_ready
output_dir
models
ollama_ready

# association/web/app.py, PingResponse: a pydantic response field, serialized
# by FastAPI and read as JSON by the page's connection indicator.
instance

# association/web/app.py, NoteResponse: a pydantic response field, serialized
# by FastAPI and read as JSON by the page's note control.
saved

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

# association/query/compose/__init__.py: the compose package's whole public
# surface, called by association/query/agent.py through a call-time module
# lookup (`from . import compose; compose.answer(...)`, so tests can
# monkeypatch it), which vulture cannot see as a call.
answer

# association/query/reading.py, Scope: a field read by name, never by
# attribute - a marker that makes check_scope refuse (it reads every name in
# SCOPING_SLOTS with getattr), so the compiler answers a triple-double
# ranking instead (compose.core.COMPILER_SLOTS).
ranked_by
