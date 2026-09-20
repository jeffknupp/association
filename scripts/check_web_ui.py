#!/usr/bin/env python3
"""Browser check for the page's two keyboard-and-clipboard behaviors.

`web/static/index.html` carries its JavaScript inline, and pytest can only read
that file as text: `test_renderers.py` parses the renderer table out of it and
syntax-checks the script with `node --check`, which is everything that can be
done without a browser. ArrowUp recall and the per-question copy button are
neither - they are key events, a caret position and a clipboard, so they get a
real browser engine, real key presses and a real `navigator.clipboard.readText`.

    uv run --with playwright python scripts/check_web_ui.py

Deliberately NOT a pre-commit gate. Playwright is not in the `dev` extra and
its browser is a ~150MB download, while the gates here run offline in seconds;
adding it would make every commit pay for a check that two files can move. Run
it when you touch the page's script, the way check_coverage.py is run after
editing a floor.

It serves `static/` over http://127.0.0.1 rather than opening a `file://` URL,
because `navigator.clipboard` exists only in a secure context and a loopback
address is one - the same reason the copy button keeps a selection-based
fallback for the LAN address the same server also answers on.

The API is stubbed in the page (EventSource and the two startup fetches), so
this needs no ollama, no warehouse and no network.
"""

import functools
import http.server
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

STATIC = Path(__file__).resolve().parent.parent / "src" / "association" / "web" / "static"
handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(STATIC))
server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{server.server_address[1]}/index.html"

STUB = """
window.EventSource = class {
  constructor(u) { this.ls = {}; setTimeout(() => {
    const payload = JSON.stringify({text: "an answer", answered_by: "fast", intent: "leaderboard",
      timing: {total_seconds: 0.5}, data: {}, artifacts: []});
    (this.ls["answer"] || []).forEach(f => f({data: payload}));
  }, 5); }
  addEventListener(n, f) { (this.ls[n] = this.ls[n] || []).push(f); }
  close() {}
};
const realFetch = window.fetch;
window.fetch = (u, o) => {
  if (String(u).indexOf("/api/health") >= 0) return Promise.resolve(new Response(JSON.stringify({ready: true, model: "stub", db: "stub"}), {headers: {"content-type": "application/json"}}));
  if (String(u).indexOf("/api/coverage") >= 0) return Promise.resolve(new Response(JSON.stringify({}), {headers: {"content-type": "application/json"}}));
  return realFetch(u, o);
};
"""

results = []


def check(name, ok, detail=""):
    results.append((ok, name, detail))


with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(permissions=["clipboard-read", "clipboard-write"])
    page = ctx.new_page()
    page.add_init_script(STUB)
    page.goto(url)
    page.wait_for_selector("#input")

    def ask(q):
        page.fill("#input", q)
        page.press("#input", "Enter")
        page.wait_for_timeout(120)

    ask("first question")
    ask("second question")

    rows = page.query_selector_all(".turn .q-row")
    check("a copy button in every question balloon", len(rows) == 2 and all(r.query_selector("button.copy") for r in rows), f"rows={len(rows)}")
    check("the balloon still holds the question text", [q.inner_text() for q in page.query_selector_all(".q")] == ["first question", "second question"])

    first_copy = rows[0].query_selector("button.copy") if rows else None
    if first_copy is None:
        check("clicking copies that question to the clipboard", False, "no copy button to click")
        check("the button says it copied", False, "no copy button to click")
    else:
        first_copy.click()
        page.wait_for_timeout(120)
        check("clicking copies that question to the clipboard", page.evaluate("navigator.clipboard.readText()") == "first question", page.evaluate("navigator.clipboard.readText()"))
        check("the button says it copied", "done" in (first_copy.get_attribute("class") or ""))

    page.fill("#input", "half typed")
    page.press("#input", "ArrowUp")
    check("ArrowUp recalls the newest question", page.input_value("#input") == "second question", page.input_value("#input"))
    page.press("#input", "ArrowUp")
    check("ArrowUp again steps further back", page.input_value("#input") == "first question", page.input_value("#input"))
    page.press("#input", "ArrowUp")
    check("it holds at the oldest rather than wrapping", page.input_value("#input") == "first question", page.input_value("#input"))
    page.press("#input", "ArrowDown")
    check("ArrowDown steps forward again", page.input_value("#input") == "second question", page.input_value("#input"))
    page.press("#input", "ArrowDown")
    check("ArrowDown past the newest restores the draft", page.input_value("#input") == "half typed", page.input_value("#input"))

    ask("second question")
    page.fill("#input", "")
    page.press("#input", "ArrowUp")
    check("a repeat is not a second entry", page.input_value("#input") == "second question", page.input_value("#input"))
    page.press("#input", "ArrowUp")
    check("the question before it is still reachable", page.input_value("#input") == "first question", page.input_value("#input"))

    # A question typed across two lines: ArrowUp moves the caret, not history.
    page.fill("#input", "line one")
    page.press("#input", "Shift+Enter")
    page.type("#input", "line two")
    before = page.input_value("#input")
    page.press("#input", "ArrowUp")
    check("ArrowUp on a later line moves the caret, not history", page.input_value("#input") == before, repr(page.input_value("#input")))
    # ...and from the first line of that same text it IS history, starting
    # from the NEWEST again, because typing reset the cursor.
    page.press("#input", "ArrowUp")
    check("ArrowUp from the first line is history again", page.input_value("#input") == "second question", page.input_value("#input"))
    # Typing resets the cursor even after browsing to the oldest entry.
    page.fill("#input", "")
    page.type("#input", "x")
    page.press("#input", "ArrowUp")
    check("typing restarts recall from the newest", page.input_value("#input") == "second question", page.input_value("#input"))

    browser.close()

if len(results) < 14:
    # A crash mid-run would otherwise print a short, all-PASS list and exit 0.
    check("every check ran", False, f"only {len(results)} of 14 checks reported")

for ok, name, detail in results:
    print(("PASS " if ok else "FAIL ") + name + (f"  :: {detail}" if detail and not ok else ""))
print(f"\n{sum(1 for ok, _, _ in results if ok)}/{len(results)} passed")
raise SystemExit(0 if all(ok for ok, _, _ in results) else 1)
