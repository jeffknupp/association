"""Tests for the bind-and-print half of `association web`.

Nothing here starts a server. What is worth testing is the part the plan
settled deliberately: no default port, and a URL a terminal can turn into a
link.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from association.web.serve import INDEX_HTML, bind, serve, url_for


def test_the_default_port_is_a_free_one_the_caller_did_not_choose() -> None:
    """Settled in the 2.0 plan: a fixed default collides with whatever else is
    running and has to be explained. An ephemeral one never does."""
    sock = bind("127.0.0.1", 0)
    try:
        assert sock.getsockname()[1] > 0
    finally:
        sock.close()


def test_an_explicit_port_is_honored() -> None:
    """--port stays available for anyone who wants to bookmark one."""
    scratch = socket.socket()
    scratch.bind(("127.0.0.1", 0))
    wanted = scratch.getsockname()[1]
    scratch.close()

    sock = bind("127.0.0.1", wanted)
    try:
        assert sock.getsockname()[1] == wanted
    finally:
        sock.close()


def test_the_printed_url_carries_the_port_that_was_actually_bound() -> None:
    """The whole point of binding before serving: with an ephemeral port, a URL
    printed from the *requested* port would say 0."""
    sock = bind("127.0.0.1", 0)
    try:
        assert url_for(sock) == f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        sock.close()


def test_a_wildcard_bind_prints_an_address_a_browser_can_open() -> None:
    """0.0.0.0 is what the socket says and not somewhere a browser can go."""
    sock = bind("0.0.0.0", 0)
    try:
        assert url_for(sock).startswith("http://127.0.0.1:")
    finally:
        sock.close()


def test_a_missing_page_stops_startup_rather_than_serving_404s(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """This project has shipped a wheel missing a file before. A server that
    starts and then serves a blank page hides that; refusing to start does not."""
    monkeypatch.setattr("association.web.serve.INDEX_HTML", tmp_path / "gone.html")
    with pytest.raises(SystemExit, match="missing from the installed package"):
        serve("127.0.0.1", 0, str(tmp_path / "nba.duckdb"), tmp_path, model="m", router_model="r")


def test_the_page_this_module_points_at_exists_in_the_source_tree() -> None:
    """Guards the path itself, which the test above monkeypatches away."""
    assert INDEX_HTML.is_file()
