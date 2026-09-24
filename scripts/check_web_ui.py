#!/usr/bin/env python3
"""Browser check for the page's keyboard, clipboard, note-saving and live-reload behaviors.

`web/static/index.html` carries its JavaScript inline, and pytest can only read
that file as text: `test_renderers.py` parses the renderer table out of it and
syntax-checks the script with `node --check`, which is everything that can be
done without a browser. ArrowUp recall, the per-question copy button and
saving a note are none of them - they are key events, a caret position, a
clipboard and a real `fetch` to `POST /api/notes`, so they get a real browser
engine, real key presses and a real `navigator.clipboard.readText`. The
connection indicator and live reload are timers and a real `location.reload()`,
checked on a page of their own under Playwright's fake clock so ten-second
polls cost nothing.

    uv run --with playwright python scripts/check_web_ui.py

Deliberately NOT a pre-commit gate. Playwright is not in the `dev` extra and
its browser is a ~150MB download, while the gates here run offline in seconds;
adding it would make every commit pay for a check that a couple of files can
move. Run it when you touch the page's script, the way check_coverage.py is
run after editing a floor.

It serves `static/` over http://127.0.0.1 rather than opening a `file://` URL,
because `navigator.clipboard` exists only in a secure context and a loopback
address is one - the same reason the copy button keeps a selection-based
fallback for the LAN address the same server also answers on.

The API is stubbed in the page (EventSource, the two startup fetches,
`POST /api/notes`, and `GET /api/ping`, whose instance and failure the checks
set through `window.__ping`), so this needs no ollama, no warehouse and no network.
"""

import functools
import http.server
import threading
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
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
      timing: {total_seconds: 0.5}, data: {}, artifacts: [], history_file: "stub-0000000000000000.log"});
    (this.ls["answer"] || []).forEach(f => f({data: payload}));
  }, 5); }
  addEventListener(n, f) { (this.ls[n] = this.ls[n] || []).push(f); }
  close() {}
};
window.__notes = [];
window.__ping = {instance: "first", fail: false};
const realFetch = window.fetch;
window.fetch = (u, o) => {
  if (String(u).indexOf("/api/health") >= 0) return Promise.resolve(new Response(JSON.stringify({ready: true, model: "stub", db: "stub"}), {headers: {"content-type": "application/json"}}));
  if (String(u).indexOf("/api/ping") >= 0) {
    if (window.__ping.fail) return Promise.reject(new TypeError("Failed to fetch"));
    return Promise.resolve(new Response(JSON.stringify({instance: window.__ping.instance, busy: false}), {headers: {"content-type": "application/json"}}));
  }
  if (String(u).indexOf("/api/coverage") >= 0) return Promise.resolve(new Response(JSON.stringify({}), {headers: {"content-type": "application/json"}}));
  if (String(u).indexOf("/api/notes") >= 0) {
    window.__notes.push(JSON.parse(o.body));
    return Promise.resolve(new Response(JSON.stringify({saved: true}), {headers: {"content-type": "application/json"}}));
  }
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

    # Notes: saved back into the answer's own history file (POST /api/notes),
    # not merely held on the page - a real key event and a real fetch, which
    # is exactly what node --check and test_renderers.py cannot exercise.
    note_boxes = page.query_selector_all(".turn .note-box")
    check("a note control appears under every answer", len(note_boxes) == len(rows), f"note_boxes={len(note_boxes)} rows={len(rows)}")

    first_note = note_boxes[0] if note_boxes else None
    if first_note is None:
        check("saving a note posts it to /api/notes", False, "no note control to use")
        check("the button reports the saved state", False, "no note control to use")
        check("the note text stays visible after saving", False, "no note control to use")
        check("a second save appends another note rather than replacing it", False, "no note control to use")
    else:
        note_input = first_note.query_selector(".note-input")
        save_button = first_note.query_selector(".note-save")
        note_input.fill("this answer undercounts rebounds")
        save_button.click()
        page.wait_for_timeout(120)
        sent = page.evaluate("window.__notes")
        check("saving a note posts it to /api/notes", len(sent) == 1 and sent[0]["note"] == "this answer undercounts rebounds" and sent[0]["history_file"] == "stub-0000000000000000.log", sent)
        status_text = first_note.query_selector(".note-status").inner_text()
        check("the button reports the saved state", status_text == "Note saved", status_text)
        check("the note text stays visible after saving", note_input.input_value() == "this answer undercounts rebounds", note_input.input_value())

        save_button.click()
        page.wait_for_timeout(120)
        sent = page.evaluate("window.__notes")
        check("a second save appends another note rather than replacing it", len(sent) == 2, sent)

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

    # Connection indicator and live reload, on pages with a fake clock. A
    # marker set on `window` survives everything except a real reload, which
    # is how "it reloaded" and "it did not" are told apart.
    def live_page():
        page = ctx.new_page()
        page.add_init_script(STUB)
        page.clock.install()
        page.goto(url)
        page.wait_for_selector("#input")
        page.clock.run_for(100)
        page.evaluate("window.__marker = true")
        return page

    def conn(page):
        return page.inner_text("#conn-text")

    def reloaded(page):
        # A reload may still be in flight when this is called: reading the
        # marker then fails with "execution context was destroyed", which is
        # the navigation itself, so settle and read the new page instead.
        for _ in range(20):
            try:
                page.wait_for_load_state()
                page.wait_for_selector("#input")
                return page.evaluate("window.__marker") is None
            except PlaywrightError:  # noqa: PERF203 - retrying is the point: a reload in flight destroys the context being read
                page.wait_for_timeout(100)
        return False

    live = live_page()
    check("the indicator says connected once the server answers", conn(live) == "connected", conn(live))
    live.evaluate("window.__ping.fail = true")
    live.clock.run_for(10_500)
    check("one missed poll reads as reconnecting, not down", conn(live) == "reconnecting…", conn(live))
    live.clock.run_for(6_500)
    check("three missed polls read as disconnected", conn(live) == "disconnected", conn(live))
    live.evaluate("window.__ping.fail = false")
    live.clock.run_for(3_500)
    check("it says connected again when the server comes back", conn(live) == "connected", conn(live))

    live.fill("#input", "a draft in progress")
    live.evaluate("window.__ping.instance = 'second'")
    live.clock.run_for(10_500)
    check("a restart with a draft typed offers a reload instead", conn(live) == "updated · reload" and not reloaded(live), conn(live))
    live.fill("#input", "")
    live.clock.run_for(10_500)
    check("with nothing to lose it reloads itself", reloaded(live))
    live.close()

    live = live_page()
    live.fill("#input", "a question")
    live.press("#input", "Enter")
    live.clock.run_for(100)
    live.evaluate("window.__ping.instance = 'second'")
    live.clock.run_for(10_500)
    check("a restart with an answer on screen does not reload it away", conn(live) == "updated · reload" and not reloaded(live), conn(live))
    live.click("#conn")
    check("clicking the indicator reloads", reloaded(live))
    live.close()

    browser.close()

if len(results) < 27:
    # A crash mid-run would otherwise print a short, all-PASS list and exit 0.
    check("every check ran", False, f"only {len(results)} of 27 checks reported")

for ok, name, detail in results:
    print(("PASS " if ok else "FAIL ") + name + (f"  :: {detail}" if detail and not ok else ""))
print(f"\n{sum(1 for ok, _, _ in results if ok)}/{len(results)} passed")
raise SystemExit(0 if all(ok for ok, _, _ in results) else 1)
