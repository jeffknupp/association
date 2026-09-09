"""Tests for serving a rendered chart over HTTP.

The route reads out of the directory the CLI writes charts to. That directory
belongs to the person running the server, not to this program - they can put
anything in it, and a name that arrives over HTTP decides which file comes
back. So the interesting tests here are all about what a name is allowed to be.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from association.query.answer import Answer, Timing
from association.web.app import INDEX_HTML, artifact_path, create_app


class Idle:
    """An answerer for a server that is only ever asked for files. Local to
    this module rather than shared: nothing here asks a question."""

    busy = False
    ready = True

    def ask(self, question: str, label: str, trace: Callable[[str], None] = lambda line: None) -> Answer:
        return Answer(question=question, text="", answered_by="fast", timing=Timing(0.0, 0.0, 0, 0.0, 0))

# Every shape a name could take to escape the output directory, and a couple of
# ordinary-looking ones that are still not chart names.
HOSTILE_NAMES = [
    "../secret.html",
    "../../etc/passwd",
    "..%2f..%2fsecret.html",
    "..",
    ".",
    "",
    "/etc/passwd",
    "%2Fetc%2Fpasswd",
    "sub/chart.html",
    "chart.html/../../secret.html",
    ".hidden.html",
    "chart.txt",
    "chart.html.txt",
    "chart",
]


@pytest.fixture
def served(tmp_path: Path) -> tuple[TestClient, Path]:
    """A client over an output directory holding one real chart, with a file
    the server must never serve sitting one level above it."""
    out_dir = tmp_path / "query_output"
    out_dir.mkdir()
    (out_dir / "shotchart_stephen_curry_401811054.html").write_text("<!doctype html><title>chart</title>")
    (tmp_path / "secret.html").write_text("<!doctype html>NOT YOURS")
    app = create_app(Idle(), db_path=str(tmp_path / "nba.duckdb"), out_dir=out_dir, model="m", router_model="r")
    return TestClient(app), out_dir


def test_a_chart_is_served_by_the_name_the_answer_reported(served: tuple[TestClient, Path]) -> None:
    """Answer.artifacts carries `name`, and this is what makes that name useful
    to a browser rather than just informative."""
    client, _ = served
    response = client.get("/api/artifacts/shotchart_stephen_curry_401811054.html")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<title>chart</title>" in response.text


@pytest.mark.parametrize("name", HOSTILE_NAMES)
def test_a_name_that_is_not_a_chart_name_gets_nothing(name: str, served: tuple[TestClient, Path]) -> None:
    """404 for all of them, including the ones rejected for being *shaped* like
    a path: a distinct status for "not allowed" would answer the question the
    probing was asking.

    Measured, not assumed: with `artifact_path`'s checks removed, most of these
    still 404 because the router never matches a path parameter containing a
    separator. This test pins the endpoint's behavior end to end; the one below
    pins the guard, which is what would still be holding if the route were ever
    declared as `{name:path}`.
    """
    client, _ = served
    response = client.get(f"/api/artifacts/{name}")

    assert response.status_code == 404, f"{name!r} returned {response.status_code}"
    assert "NOT YOURS" not in response.text


@pytest.mark.parametrize("name", HOSTILE_NAMES)
def test_the_guard_itself_rejects_every_one_of_those(name: str, tmp_path: Path) -> None:
    """Independently of the router, which is doing some of the work above."""
    assert artifact_path(tmp_path, name) is None


def test_a_symlink_out_of_the_directory_is_not_followed(served: tuple[TestClient, Path]) -> None:
    """The one attack the name check cannot see. `evil.html` is a perfectly
    valid chart name; only resolving it and looking at where it landed catches
    this."""
    client, out_dir = served
    (out_dir / "evil.html").symlink_to(out_dir.parent / "secret.html")

    response = client.get("/api/artifacts/evil.html")

    assert response.status_code == 404
    assert "NOT YOURS" not in response.text


def test_a_symlink_within_the_directory_is_fine(served: tuple[TestClient, Path]) -> None:
    """Rejecting every symlink would be simpler and wrong - one pointing at a
    chart beside it resolves inside the directory and is an ordinary file."""
    client, out_dir = served
    (out_dir / "latest.html").symlink_to(out_dir / "shotchart_stephen_curry_401811054.html")

    assert client.get("/api/artifacts/latest.html").status_code == 200


def test_a_name_with_no_file_behind_it_is_a_miss_not_an_error(served: tuple[TestClient, Path]) -> None:
    """A chart written by an earlier run and since deleted."""
    client, _ = served
    assert client.get("/api/artifacts/fingerprint_nobody_2025_total_percentile.html").status_code == 404


def test_a_directory_named_like_a_chart_is_not_served(tmp_path: Path) -> None:
    """`is_file()`, not `exists()` - FileResponse on a directory is a 500."""
    out_dir = tmp_path / "out"
    (out_dir / "chart.html").mkdir(parents=True)
    assert artifact_path(out_dir, "chart.html") is None


def test_the_output_directory_not_existing_yet_is_a_miss(tmp_path: Path) -> None:
    """Nothing has been rendered yet, which is the state every fresh install
    is in - it must not raise on the way to saying so."""
    assert artifact_path(tmp_path / "never_created", "chart.html") is None


def test_health_and_charts_agree_about_the_output_directory(served: tuple[TestClient, Path]) -> None:
    """Both come from the one `--out-dir` the process was started with, so a
    chart is servable from the directory health reports."""
    client, out_dir = served
    assert client.get("/api/health").json()["output_dir"] == str(out_dir)


def test_the_page_asks_for_charts_at_the_route_that_serves_them() -> None:
    """Two hand-maintained halves again: the page builds this URL as a string
    and the server declares it as a route, and a rename on either side would
    show up only as an empty frame."""
    assert '"/api/artifacts/" + encodeURIComponent(' in INDEX_HTML.read_text()


def test_a_chart_is_framed_with_scripts_disabled() -> None:
    """The directory being served is one a person can drop files into, and the
    charts themselves have never contained a script - so allowing them costs
    nothing to remove."""
    page = INDEX_HTML.read_text()
    assert '"sandbox", "allow-same-origin"' in page
    assert "allow-scripts" not in page


def test_the_chart_renderers_still_emit_no_scripts() -> None:
    """The premise of the sandbox above. If a renderer ever grows one, the
    frame silently stops working and this says why."""
    from association.query import court, radar

    for module in (court, radar):
        source = Path(module.__file__ or "").read_text()
        assert "<script" not in source, f"{module.__name__} now emits a script - the artifact iframe blocks it"
