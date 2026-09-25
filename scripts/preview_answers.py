"""Render recorded questions through the web page without the router, and screenshot each answer.

The page's renderers (``web/static/index.html``, ``RENDERERS``) draw an answer from
the ``data`` a template returns, so a rendering problem is only visible with real
data on the real page - and asking the live router for every case costs ollama a
minute each and moves slots between runs. This script skips the router: it takes a
question's RECORDED route (the ``-> (router) intent=... slots={...}`` line a live
yardstick run or a history file holds), runs the fast path exactly as ``agent.py``
would from that Route (entity repairs, coverage, the compiler, the refusals - the
model is never called), serializes the answer the way ``POST /api/ask`` does, and
writes a gallery page: ``index.html`` itself, with ``EventSource`` stubbed to answer
each question from the recorded set and the questions asked one after another on
load. Open ``gallery.html`` in a browser, or pass ``--screenshots`` to drive it in
headless Chromium (``uv run --with playwright python scripts/preview_answers.py``)
and get one PNG per answer to look at.

    uv run python scripts/preview_answers.py --from-live ~/association-research/yardstick-v2/live_day2.jsonl \\
        --match "streak" --match "2nd half" --out /tmp/preview

    uv run python scripts/preview_answers.py --from-history ~/deploy/state/.history --since 2026-09-24 --out /tmp/preview --screenshots

    uv run python scripts/preview_answers.py --case streak '{"stat": "wins", "season": 2026}' "Longest winning streak in the NBA this season" --out /tmp/preview

Runs against the warehouse at ``--db-path`` (default ``nba.duckdb`` in the current
directory), read-only. Needs no ollama and no network: a question whose fast path
gives up (the agent would have been asked) is recorded as fell-through and rendered
as such, which is itself worth seeing.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "src" / "association" / "web" / "static"
ROUTE_LINE = re.compile(r"intent='(\w+)' slots=(\{.*\})")


def recorded_routes(path: Path, matches: list[str]) -> list[tuple[str, str, dict[str, Any]]]:
    """``(question, intent, slots)`` for every row of a live-run jsonl whose
    question contains one of ``matches`` (all rows when none are given)."""
    cases = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        found = ROUTE_LINE.search(row.get("router") or "")
        if found is None:
            continue
        if matches and not any(m.lower() in row["q"].lower() for m in matches):
            continue
        try:
            cases.append((row["q"], found.group(1), ast.literal_eval(found.group(2))))
        except (ValueError, SyntaxError):
            continue
    return cases


def recorded_history(history_dir: Path, matches: list[str], since: str | None) -> list[tuple[str, str, dict[str, Any]]]:
    """``(question, intent, slots)`` from a history directory's records - the
    question line and the ``-> (router) ...`` trace line each record keeps -
    newest last; ``since`` (``YYYY-MM-DD``) keeps records modified that day
    or later. Duplicates of one question keep the newest record only."""
    import datetime as dt

    cutoff = dt.datetime.fromisoformat(since).timestamp() if since else None
    seen: dict[str, tuple[str, str, dict[str, Any]]] = {}
    for path in sorted(history_dir.glob("*.log"), key=lambda p: p.stat().st_mtime):
        if cutoff is not None and path.stat().st_mtime < cutoff:
            continue
        text = path.read_text()
        question = next((line[len("question: ") :].strip() for line in text.splitlines() if line.startswith("question: ")), None)
        found = ROUTE_LINE.search(text)
        if not question or found is None:
            continue
        if matches and not any(m.lower() in question.lower() for m in matches):
            continue
        try:
            seen[question] = (question, found.group(1), ast.literal_eval(found.group(2)))
        except (ValueError, SyntaxError):
            continue
    return list(seen.values())


def answer_without_the_router(db_path: str, out_dir: Path, question: str, intent: str, slots: dict[str, Any]) -> dict[str, Any]:
    """The wire form of the fast path's answer from a fixed Route - the model
    never called. A fall-through is reported as one rather than raised."""
    import ollama

    from association.query import agent as agent_module
    from association.query.agent import Agent
    from association.query.answer import FallthroughDisabled
    from association.query.router import Route
    from association.web.app import as_response

    def fixed_route(model: str, q: str, previous_question: str | None = None) -> Route:
        return Route(intent=intent, slots=dict(slots))

    def never(**kw: Any) -> None:
        raise AssertionError("the model must not be called: this preview runs the fast path from a recorded route")

    original_route, original_chat = agent_module.route, ollama.chat
    agent_module.route, ollama.chat = fixed_route, never  # type: ignore[assignment]
    try:
        agent = Agent("preview", db_path, out_dir, history_dir=out_dir / ".history", trace=lambda line: None, fallthrough=False)
        try:
            answer = agent.ask(question, label="preview")
        except FallthroughDisabled as exc:
            return {
                "question": question,
                "text": f"(fell through to the agent) {exc}",
                "answered_by": "agent",
                "intent": intent,
                "data": None,
                "artifacts": [],
                "timing": {"total_seconds": 0, "model_seconds": 0, "model_calls": 0, "tool_seconds": 0, "tool_calls": 0},
                "history_file": None,
            }
        return as_response(answer, history_file=answer.history_file).model_dump(mode="json")
    finally:
        agent_module.route, ollama.chat = original_route, original_chat  # type: ignore[assignment]


STUB = """
<style>
  /* The gallery grows with its answers instead of scrolling inside `main`, so
     an element screenshot of a tall answer is the whole answer. */
  html, body { height: auto !important; overflow: visible !important; }
  main { overflow: visible !important; height: auto !important; max-height: none !important; }
  .scroller { overflow-x: visible !important; }
</style>
<script>
(() => {
  const answers = window.__answers;
  const byQuestion = new Map(answers.map(a => [a.question, a]));
  window.EventSource = class {
    constructor(url) {
      this.listeners = {};
      const question = decodeURIComponent(url.split("question=")[1] || "");
      const a = byQuestion.get(question);
      setTimeout(() => {
        const missing = {text: "(no recorded answer for this question)", answered_by: "agent", intent: null,
                         timing: {total_seconds: 0}, data: null, artifacts: [], history_file: null};
        const payload = JSON.stringify(a || missing);
        (this.listeners["answer"] || []).forEach(f => f({data: payload}));
      }, 5);
    }
    addEventListener(name, f) { (this.listeners[name] = this.listeners[name] || []).push(f); }
    close() {}
  };
  const realFetch = window.fetch;
  window.fetch = (u, o) => {
    const s = String(u);
    const json = body => Promise.resolve(new Response(JSON.stringify(body), {headers: {"content-type": "application/json"}}));
    if (s.indexOf("/api/health") >= 0) return json({ready: true, model: "preview", db: "preview"});
    if (s.indexOf("/api/ping") >= 0) return json({instance: "preview", busy: false});
    if (s.indexOf("/api/coverage") >= 0) return json({tiers: []});
    if (s.indexOf("/api/notes") >= 0) return json({saved: true});
    return realFetch(u, o);
  };
  window.addEventListener("load", () => {
    const input = document.querySelector("#input");
    const form = input.closest("form");
    let i = 0;
    const next = () => {
      if (i >= answers.length) { document.body.setAttribute("data-gallery-done", "1"); return; }
      input.value = answers[i++].question;
      input.disabled = false;
      form.requestSubmit();
      setTimeout(next, 60);
    };
    setTimeout(next, 100);
  });
})();
</script>
"""


def write_gallery(out_dir: Path, answers: list[dict[str, Any]]) -> Path:
    """``index.html`` with the answers embedded and the API stubbed, asking each
    recorded question in turn on load."""
    page = (STATIC / "index.html").read_text()
    inject = f"<script>window.__answers = {json.dumps(answers)};</script>" + STUB
    gallery = page.replace("<body>", "<body>" + inject, 1)
    path = out_dir / "gallery.html"
    path.write_text(gallery)
    (out_dir / "answers.json").write_text(json.dumps(answers, indent=1))
    return path


def screenshot(gallery: Path, out_dir: Path, count: int) -> list[Path]:
    """One PNG per rendered answer, driven in headless Chromium; needs playwright."""
    from playwright.sync_api import sync_playwright

    shots = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 900, "height": 1200})
        page.goto(gallery.as_uri())
        page.wait_for_selector("body[data-gallery-done]", timeout=30_000)
        page.wait_for_timeout(300)
        turns = page.query_selector_all(".turn")
        for i, turn in enumerate(turns[:count]):
            target = out_dir / f"answer_{i + 1:02d}.png"
            turn.screenshot(path=str(target))
            shots.append(target)
        page.screenshot(path=str(out_dir / "gallery_full.png"), full_page=True)
        browser.close()
    return shots


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--from-live", type=Path, help="a live-run jsonl with recorded router lines")
    parser.add_argument("--from-history", type=Path, help="a .history directory (the web server's, or the CLI's) whose records carry router lines")
    parser.add_argument("--since", help="with --from-history: keep records modified on or after this day, YYYY-MM-DD")
    parser.add_argument("--match", action="append", default=[], help="keep only questions containing this text (repeatable)")
    parser.add_argument("--case", nargs=3, action="append", default=[], metavar=("INTENT", "SLOTS_JSON", "QUESTION"), help="an explicit case")
    parser.add_argument("--db-path", default="nba.duckdb")
    parser.add_argument("--out", type=Path, default=Path(tempfile.mkdtemp(prefix="preview-")))
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--screenshots", action="store_true", help="drive the gallery in headless Chromium and write one PNG per answer")
    args = parser.parse_args()

    cases: list[tuple[str, str, dict[str, Any]]] = []
    if args.from_live:
        cases += recorded_routes(args.from_live, args.match)
    if args.from_history:
        cases += recorded_history(args.from_history, args.match, args.since)
    for intent, slots_json, question in args.case:
        cases.append((question, intent, json.loads(slots_json)))
    if not cases:
        print("no cases: pass --from-live (with --match) or --case", file=sys.stderr)
        return 2
    cases = cases[: args.limit]
    args.out.mkdir(parents=True, exist_ok=True)
    answers = []
    for question, intent, slots in cases:
        answer = answer_without_the_router(args.db_path, args.out, question, intent, slots)
        answers.append(answer)
        print(f"{answer['answered_by']:5s} {intent:18s} {question[:70]}", flush=True)
    gallery = write_gallery(args.out, answers)
    print(f"\n{len(answers)} answers -> {gallery}")
    if args.screenshots:
        for shot in screenshot(gallery, args.out, len(answers)):
            print(f"  {shot}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
