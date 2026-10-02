"""Render recorded questions through the web page without a model, and screenshot each answer.

The page's renderers (``web/static/index.html``, ``RENDERERS``) draw an answer from
the ``data`` a template returns, so a rendering problem is only visible with real
data on the real page - and asking the live model for every case costs ollama a
minute each and moves its reply between runs. This script asks no model: it takes
the normalizer's RECORDED reply (the ``-> (normalizer) names=[...] stat='...'`` line
a live yardstick run or a history file holds) and answers the question through the
whole ``Agent`` with that reply in the model's place, as ``scripts/stage_snapshots.py``
does - the parser reads the question, so the answer is the one the page would give
today. It serializes the answer the way ``POST /api/ask`` does, and
writes a gallery page: ``index.html`` itself, with ``EventSource`` stubbed to answer
each question from the recorded set and the questions asked one after another on
load. Open ``gallery.html`` in a browser, or pass ``--screenshots`` to drive it in
headless Chromium (``uv run --with playwright python scripts/preview_answers.py``)
and get one PNG per answer to look at.

    uv run python scripts/preview_answers.py --from-live ~/association-research/yardstick-v2/live_day2.jsonl \\
        --match "streak" --match "2nd half" --out /tmp/preview

    uv run python scripts/preview_answers.py --from-history ~/deploy/state/.history --since 2026-09-24 --out /tmp/preview --screenshots

    uv run python scripts/preview_answers.py --case "Longest winning streak in the NBA this season" '[]' wins --out /tmp/preview

Runs against the warehouse at ``--db-path`` (default ``nba.duckdb`` in the current
directory), read-only. Needs no ollama and no network: a question whose fast path
gives up is answered with the refusal naming why, as the real page shows it, and rendered
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
REPLY_LINE = re.compile(r"\(normalizer\) names=(\[.*?\]) stat='([^']*)'")


def recorded_replies(path: Path, matches: list[str]) -> list[tuple[str, list[str], str]]:
    """``(question, names, stat)`` - the normalizer's recorded reply - for
    every row of a live-run jsonl whose question contains one of ``matches``
    (all rows when none are given). A row with no normalizer line (a run
    from before the parser read the question) is skipped."""
    cases = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        found = REPLY_LINE.search("\n".join(row.get("trace") or []))
        if found is None:
            continue
        if matches and not any(m.lower() in row["q"].lower() for m in matches):
            continue
        try:
            cases.append((row["q"], list(ast.literal_eval(found.group(1))), found.group(2)))
        except ValueError, SyntaxError:
            continue
    return cases


def recorded_history(history_dir: Path, matches: list[str], since: str | None) -> list[tuple[str, list[str], str]]:
    """``(question, names, stat)`` from a history directory's records - the
    question line and the ``-> (normalizer) ...`` trace line each record keeps -
    newest last; ``since`` (``YYYY-MM-DD``) keeps records modified that day
    or later. Duplicates of one question keep the newest record only."""
    import datetime as dt

    cutoff = dt.datetime.fromisoformat(since).timestamp() if since else None
    seen: dict[str, tuple[str, list[str], str]] = {}
    for path in sorted(history_dir.glob("*.log"), key=lambda p: p.stat().st_mtime):
        if cutoff is not None and path.stat().st_mtime < cutoff:
            continue
        text = path.read_text()
        question = next((line[len("question: ") :].strip() for line in text.splitlines() if line.startswith("question: ")), None)
        found = REPLY_LINE.search(text)
        if not question or found is None:
            continue
        if matches and not any(m.lower() in question.lower() for m in matches):
            continue
        try:
            seen[question] = (question, list(ast.literal_eval(found.group(1))), found.group(2))
        except ValueError, SyntaxError:
            continue
    return list(seen.values())


def answer_as_recorded(db_path: str, out_dir: Path, question: str, names: list[str], stat: str) -> dict[str, Any]:
    """``question``'s answer through the whole ``Agent`` with the
    normalizer's recorded reply (``names``, ``stat``) in the model's place,
    serialized as ``POST /api/ask`` serializes it. Until 5.0.0's last change
    this replayed a recorded ROUTE (``Agent.ask(route=...)``), a door the
    agent no longer has: the parser reads every question it answers."""
    import association.query.normalizer as normalizer
    from association.query.agent import Agent
    from association.web.app import as_response

    reply = normalizer.Normalized([name for name in names if name.strip()], stat if stat in normalizer.NORMALIZER_STATS else "")
    real = normalizer.normalize
    normalizer.normalize = lambda _model, _question: reply  # type: ignore[assignment]
    try:
        agent = Agent(db_path, out_dir, history_dir=out_dir / ".history", trace=lambda line: None)
        answer = agent.ask(question, label="preview")
    finally:
        normalizer.normalize = real
    return as_response(answer, history_file=answer.history_file).model_dump(mode="json")


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
    parser.add_argument("--from-live", type=Path, help="a live-run jsonl whose rows carry the normalizer's trace line")
    parser.add_argument("--from-history", type=Path, help="a .history directory (the web server's, or the CLI's) whose records carry the normalizer's trace line")
    parser.add_argument("--since", help="with --from-history: keep records modified on or after this day, YYYY-MM-DD")
    parser.add_argument("--match", action="append", default=[], help="keep only questions containing this text (repeatable)")
    parser.add_argument(
        "--case", nargs=3, action="append", default=[], metavar=("QUESTION", "NAMES_JSON", "STAT"), help="an explicit case: the question, and the normalizer's reply to stand in for the model"
    )
    parser.add_argument("--db-path", default="nba.duckdb")
    parser.add_argument("--out", type=Path, default=Path(tempfile.mkdtemp(prefix="preview-")))
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--screenshots", action="store_true", help="drive the gallery in headless Chromium and write one PNG per answer")
    args = parser.parse_args()

    cases: list[tuple[str, list[str], str]] = []
    if args.from_live:
        cases += recorded_replies(args.from_live, args.match)
    if args.from_history:
        cases += recorded_history(args.from_history, args.match, args.since)
    for question, names_json, stat in args.case:
        cases.append((question, list(json.loads(names_json)), stat))
    if not cases:
        print("no cases: pass --from-live (with --match) or --case", file=sys.stderr)
        return 2
    cases = cases[: args.limit]
    args.out.mkdir(parents=True, exist_ok=True)
    answers = []
    for question, names, stat in cases:
        answer = answer_as_recorded(args.db_path, args.out, question, names, stat)
        answers.append(answer)
        print(f"{answer['answered_by']:5s} {answer.get('intent') or '-':18s} {question[:70]}", flush=True)
    gallery = write_gallery(args.out, answers)
    print(f"\n{len(answers)} answers -> {gallery}")
    if args.screenshots:
        for shot in screenshot(gallery, args.out, len(answers)):
            print(f"  {shot}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
