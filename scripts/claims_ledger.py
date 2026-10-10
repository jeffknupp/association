#!/usr/bin/env python3
"""Which words of each recorded question does the reading depend on?

ROADMAP.md, contract 2: each word of a question is read once, and a content
word nothing read is recorded, never silently dropped - the dropped word is
how a narrower question gets answered fluently ("players with 30 points in
games where ..." answered without the 30). The new reader will claim spans
as it reads; this measures the same thing from OUTSIDE the reader, so the
number means the same before, during and after the reader is replaced:

    a content word is UNREAD when deleting it from the question leaves the
    reading and the planned query exactly as they were.

    PYTHONPATH=<tree>/src uv run python scripts/claims_ledger.py run ledger.jsonl
    uv run python scripts/claims_ledger.py report ledger.jsonl

``run`` reads each recorded question (no model: the normalizer's recorded
reply, the date pinned, as ``scripts/stage_snapshots.py``), then once more
per content word with that word deleted, and writes one line per question
with its unread words. ``report`` prints the count and the most common
unread words; the count on the day Phase 0 measured it is the baseline the
roadmap's "done" table holds ("not grown").

Three kinds of word are not counted, each for a reason:

- a function word (``CONTENT_STOPWORDS``): "the", "did", "of" carry no narrowing.
- a word of a name the model copied out of the question, or of a name the
  reading settled on: the recorded reply stands in for the model, and it
  still holds the name after the word is deleted, so the deletion cannot
  be seen. The model read it.
- a word that names the stat the model picked (``MODEL_STAT_WORDS``), for the
  same reason.

So the instrument is blind to a word only the model could have dropped.
The baseline is a number to hold, not a list of bugs; the report's list is
where to look for them.

Since Phase 3, step 3 the rule is the package's
(:mod:`association.query.lexicon`: ``content_words``, ``CONTENT_STOPWORDS``,
``MODEL_STAT_WORDS``), and the Reading states its own unread words by it -
the content words no reader rule claimed (``Reading.unread``). This is the
check from outside that the two agree: ``run`` records the Reading's list
beside the deletion's, and ``report`` names every question where they
differ and exits 1 on one. They measure one thing two ways, so a
disagreement is a finding - a rule that claimed a word the reading does not
depend on, or a word a stage reads without claiming it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

RECORDED_DIR = Path.home() / "association-research" / "parser-greenfield"
DEFAULT_RECORDED = tuple(RECORDED_DIR / name for name in ("normalizer_qwen2.5_3b.jsonl", "normalizer_corpus_qwen2.5_3b.jsonl", "normalizer_paraphrases_qwen2.5_3b.jsonl"))
DEFAULT_TODAY = "2026-09-30"


def without(question: str, start: int, end: int) -> str:
    """The question with one word deleted and the space it left closed."""
    return re.sub(r"\s+", " ", question[:start] + " " + question[end:]).strip()


#: The slots of a reading that hold a name.
NAME_SLOTS = ("player", "players", "team", "teams", "opponent", "own_team", "with_player", "without")


def names_read(whole: dict[str, Any]) -> list[str]:
    """Every name the reading settled on - the scope's and the subject's -
    so a word of one counts as read where the model copied only part of the
    name ("steph" for "steph curry", which the nickname table completes)."""
    reading = whole.get("reading") or {}
    found: list[str] = []
    for holder in (reading.get("scope") or {}, reading.get("subject") or {}):
        for slot in NAME_SLOTS:
            value = holder.get(slot)
            found.extend(value if isinstance(value, list) else [value] if isinstance(value, str) else [])
    return found


def content_words(question: str, names: list[str], stat: str) -> list[tuple[int, int, str]]:
    """The words the reader itself has to account for: not a function word,
    and not read by the model (``names`` is the model's names and the names
    the reading settled on) - the package's rule (``lexicon.content_words``),
    which the Reading's own ``unread`` is counted by."""
    from association.query.lexicon import content_words as rule

    return rule(question, names, stat)


def _probed(record: dict[str, Any]) -> dict[str, Any]:
    """A read's record as the deletion probe compares it: without the
    Reading's own unread words, which a deleted word always moves."""
    reading = record.get("reading")
    if not isinstance(reading, dict) or "unread" not in reading:
        return record
    return {**record, "reading": {key: value for key, value in reading.items() if key != "unread"}}


def stated_unread(whole: dict[str, Any]) -> list[str] | None:
    """The Reading's own unread words, where its record holds them (a tree
    since Phase 3, step 3), or None."""
    reading = whole.get("reading")
    return list(reading["unread"]) if isinstance(reading, dict) and "unread" in reading else None


def _reader(db_path: Path) -> Any:
    """A function reading one question into its ``reading`` and ``query``
    records, or the refusal it stopped at - the parser's two steps, inside
    the season the Agent answers in."""
    from association.nba.season import season_on_record
    from association.query.compose.plan import plan_point
    from association.query.connection import connect_read_only, latest_season_on_record
    from association.query.names import loaded
    from association.query.parse import read_route, reading_from_route
    from association.query.reading import ScopeError
    from association.query.stages import read_stages

    con = connect_read_only(str(db_path))
    con.execute("SET threads = 1")
    on_record = latest_season_on_record(con)

    def read(question: str, names: list[str], stat: str) -> dict[str, Any]:
        # As Agent.ask reads: inside the season on record, and with the
        # players' and teams' names loaded once for the question.
        with season_on_record(on_record), loaded():
            try:
                routed, _subject, _parent = read_route(con, question, names, stat)
                reading = reading_from_route(con, question, routed)
                return read_stages(reading, planned=plan_point(reading))
            except ScopeError as exc:
                return {"refused": str(exc)}

    return read


def run(args: argparse.Namespace) -> int:
    """Read every recorded question whole and with each content word deleted."""
    os.environ["ASSOCIATION_TODAY"] = args.today
    import association
    from association.query.normalizer import NORMALIZER_STATS

    replies: dict[str, dict[str, Any]] = {}
    for path in args.recorded:
        for line in path.read_text().splitlines():
            row = json.loads(line)
            replies.setdefault(row["q"], row["out"])
    read = _reader(args.db_path)
    print(json.dumps({"code": str(Path(association.__file__).resolve()), "db": str(args.db_path.resolve()), "today": args.today, "questions": len(replies)}), flush=True)
    started = time.monotonic()
    with args.out.open("w") as out:
        for index, (question, reply) in enumerate(replies.items(), 1):
            if "error" in reply or (args.match and not any(word.lower() in question.lower() for word in args.match)):
                continue
            names = [name.strip() for name in reply.get("names") or [] if isinstance(name, str) and name.strip()]
            stat = reply.get("stat") if reply.get("stat") in NORMALIZER_STATS else ""
            whole = read(question, names, stat)
            words = content_words(question, [*names, *names_read(whole)], stat)
            probed = _probed(whole)
            unread = [word for start, end, word in words if _probed(read(without(question, start, end), names, stat)) == probed]
            row = {"question": question, "content_words": [word for _, _, word in words], "unread": unread}
            stated = stated_unread(whole)
            if stated is not None:
                row["reading_unread"] = stated
            out.write(json.dumps(row) + "\n")
            if index % 100 == 0:
                print(f"{index}/{len(replies)} {time.monotonic() - started:.0f}s", flush=True)
    print(f"wrote {args.out} in {time.monotonic() - started:.0f}s", flush=True)
    return 0


def report(args: argparse.Namespace) -> int:
    """The count of unread content words, and the most common ones."""
    rows = [json.loads(line) for line in args.ledger.read_text().splitlines()]
    content = sum(len(row["content_words"]) for row in rows)
    unread = Counter(word for row in rows for word in row["unread"])
    with_unread = sum(1 for row in rows if row["unread"])
    print(f"{len(rows)} questions, {content} content words: {sum(unread.values())} unread ({sum(unread.values()) / max(content, 1):.1%}) in {with_unread} questions")
    print("most common: " + ", ".join(f"{word} {count}" for word, count in unread.most_common(args.top)))
    for row in rows[: args.show] if args.show else []:
        if row["unread"]:
            print(f"  {row['question']}  ->  {' '.join(row['unread'])}")
    return _report_disagreements(rows)


def _report_disagreements(rows: list[dict[str, Any]]) -> int:
    """Every question whose Reading states other unread words than the
    deletion finds, and 1 if there is one (0 where the ledger was run on a
    tree whose Reading states none)."""
    stated = [row for row in rows if "reading_unread" in row]
    if not stated:
        print("the Reading's own unread words: not recorded (a tree before Phase 3, step 3)")
        return 0
    differ = [row for row in stated if row["reading_unread"] != row["unread"]]
    print(f"the Reading's own unread words: {len(stated) - len(differ)} of {len(stated)} questions agree with the deletion, {len(differ)} differ")
    for row in differ:
        print(f"  {row['question']}  ->  Reading {row['reading_unread']}  deletion {row['unread']}")
    return 1 if differ else 0


def main() -> int:
    """Parse the command line and run the subcommand."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run", help="measure which words each recorded question's reading depends on")
    run_parser.add_argument("out", type=Path)
    run_parser.add_argument("--recorded", type=Path, nargs="+", default=list(DEFAULT_RECORDED), help="recorded normalizer replies (jsonl)")
    run_parser.add_argument("--db-path", type=Path, default=Path("nba.duckdb"))
    run_parser.add_argument("--today", default=DEFAULT_TODAY, help="the date the run reads as (ASSOCIATION_TODAY)")
    run_parser.add_argument("--match", action="append", help="only questions containing this text (repeatable)")
    run_parser.set_defaults(func=run)
    report_parser = commands.add_parser("report", help="print the count of unread content words and the most common")
    report_parser.add_argument("ledger", type=Path)
    report_parser.add_argument("--top", type=int, default=40)
    report_parser.add_argument("--show", type=int, default=0, help="also print this many questions with their unread words")
    report_parser.set_defaults(func=report)
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
