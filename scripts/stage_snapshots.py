#!/usr/bin/env python3
"""Write, and compare, each stage's output for every recorded question.

ROADMAP.md, Phase 0: the proof of a change to the query pipeline. ``run``
answers each recorded question through the whole ``Agent`` with the
normalizer's RECORDED reply in place of the model (no ollama, no network) and
writes one JSON line per question holding what each stage produced - the
reading, the planned query, the result's values and the answer
(``association.query.stages.snapshot``). ``compare`` reads two such files and
reports, per question, the FIRST stage a difference appears in.

    PYTHONPATH=<tree>/src uv run python scripts/stage_snapshots.py run before.jsonl
    PYTHONPATH=<tree>/src uv run python scripts/stage_snapshots.py run after.jsonl
    uv run python scripts/stage_snapshots.py compare before.jsonl after.jsonl

``run`` pins what would otherwise move an answer between two runs of the same
code: the date (``ASSOCIATION_TODAY``, from ``--today``), DuckDB's thread
count (``--threads 1``: a parallel SUM is not bit-reproducible) and the
directory charts are written to (masked as ``<out>``). Its first line says
which copy of the code it read, the build and the warehouse - a script run
from a worktree resolves the INSTALLED package unless ``PYTHONPATH`` says
otherwise, and a green comparison of a tree against itself proves nothing -
and the versions of Python, DuckDB and ollama with the model's digest: the
recorded replies this run stands in for the model with were said by one
ollama build on one machine (53 of 628 moved between two, 2026-10-02), and
a lowering or a sort can move with the interpreter or the engine.
``compare`` warns when two runs differ in any of them.

``run --feed`` answers the fourth population instead (ISSUES.md #332): the
2,082 questions the readings population (``reader_pop.py``) reads beyond
the corpus - the StatMuse feed's 2,067 distinct questions of three or more
words and 15 franchise and companion wordings - from the replies
``~/association-research/stages/feed_replies.py`` writes (no names and no
stat for the feed, which no model was asked: tree against tree on equal
input, not production's answer), about four minutes a tree. The record is
the same snapshot, so ``compare`` reads it unchanged.

``compare-calls`` is the same comparison over the second population a
change is proven on: every call the unit tests make across a stage boundary,
recorded by running the suite with ``ASSOCIATION_STAGE_CALLS=<dir>``
(``tests/stage_calls.py``) on each tree.

``remarks`` reads one run: how many answers carry a note or a stated
decision, the count of each kind (``association.query.notes``), and every
remark that was written and did not reach its answer.

``compare`` exits 1 on any difference, and 2 when the two runs share
nothing to compare (a wrong path must not read as a clean run). ``--values-only`` leaves the
sentences out (``stages.WORDING``): for a change allowed to reword an answer
but not to move a number. ``--ignore <stage.path>`` (repeatable) leaves one
field out of the comparison and lists its values on each side by count -
only for a field the commit names as added to, or deleted from, a record
(``reading.point.by``, Phase 3, step 1); everything else stays compared
whole. Run the baseline twice and compare it with itself before reading
anything into a difference.

The recorded replies are jsonl rows ``{"q": question, "out": {"names": [...],
"stat": ...}}``; by default the three files of the 628-question corpus under
``~/association-research/parser-greenfield`` (the 277 yardstick wordings, the
75 hold-out questions and the 276 paraphrases).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

RECORDED_DIR = Path.home() / "association-research" / "parser-greenfield"
DEFAULT_RECORDED = tuple(RECORDED_DIR / name for name in ("normalizer_qwen2.5_3b.jsonl", "normalizer_corpus_qwen2.5_3b.jsonl", "normalizer_paraphrases_qwen2.5_3b.jsonl"))
DEFAULT_TODAY = "2026-09-30"
FEED_RECORDED = Path.home() / "association-research" / "stages" / "feed_recorded.jsonl"


def _recorded_replies(paths: list[Path]) -> dict[str, dict[str, Any]]:
    """Each question's recorded normalizer reply, in file order, first one kept."""
    replies: dict[str, dict[str, Any]] = {}
    for path in paths:
        for line in path.read_text().splitlines():
            row = json.loads(line)
            replies.setdefault(row["q"], row["out"])
    return replies


def _build(root: Path) -> str:
    """The tree's ``git describe``, or "unknown" outside a checkout."""
    described = subprocess.run(["git", "-C", str(root), "describe", "--always", "--dirty"], capture_output=True, text=True, check=False)
    return described.stdout.strip() or "unknown"


def _ollama(model: str) -> dict[str, Any]:
    """The ollama server's version and ``model``'s digest, or what stood in
    the way - the run asks no model, so an unreachable server is a fact to
    record, not an error."""
    import ollama

    try:
        client = ollama.Client()
        digest = next((each.digest or "" for each in client.list().models if each.model == model), "not pulled")
        version = subprocess.run(["ollama", "--version"], capture_output=True, text=True, check=False).stdout.strip().removeprefix("ollama version is ")
        return {"version": version or "unknown", "model": model, "digest": digest[:12]}
    except Exception as exc:  # noqa: BLE001 - whatever kept the server from answering is the record
        return {"version": "unreachable", "model": model, "digest": f"{type(exc).__name__}"}


def run(args: argparse.Namespace) -> int:
    """Answer every recorded question and write its stages, one line each."""
    # Before the package is imported: the date is read at call time, but a
    # pin set after the first answer would split the run in two.
    os.environ["ASSOCIATION_TODAY"] = args.today
    import duckdb

    import association
    import association.query.normalizer as normalizer
    from association.query.agent import Agent
    from association.query.models import DEFAULT_ROUTER_MODEL
    from association.query.stages import snapshot

    replies = _recorded_replies(args.recorded)
    questions = [q for q in replies if not args.match or any(word.lower() in q.lower() for word in args.match)]

    def recorded(_model: str, question: str) -> Any:
        out = replies.get(question)
        if out is None or "error" in out:
            return None
        names = [name.strip() for name in out.get("names") or [] if isinstance(name, str) and name.strip()]
        return normalizer.Normalized(names, out.get("stat") if out.get("stat") in normalizer.NORMALIZER_STATS else "")

    normalizer.normalize = recorded  # type: ignore[assignment]
    # The agent's chart output and history go to a scratch directory removed
    # when the run ends, however it ends: a bare mkdtemp left one behind per
    # run - 663 of them, 748 MB on the shared /tmp by 2026-10-05 (#335).
    with tempfile.TemporaryDirectory(prefix="stages-") as scratch_name:
        scratch = Path(scratch_name)
        agent = Agent(str(args.db_path), scratch / "out", history_dir=scratch / "history", trace=lambda _line: None)
        agent.con.execute(f"SET threads = {int(args.threads)}")
        mask = {str(scratch / "out"): "<out>"}
        code = Path(association.__file__).resolve()
        meta = {
            "code": str(code),
            "build": _build(code.parents[2]),
            "db": str(args.db_path.resolve()),
            "today": args.today,
            "threads": args.threads,
            "questions": len(questions),
            "python": sys.version.split()[0],
            "duckdb": duckdb.__version__,
            "ollama": _ollama(DEFAULT_ROUTER_MODEL),
        }
        print(json.dumps(meta), flush=True)
        started = time.monotonic()
        with args.out.open("w") as out:
            out.write(json.dumps({"meta": meta}) + "\n")
            for index, question in enumerate(questions, 1):
                try:
                    answer = agent.ask(question)
                    record = snapshot(agent.reading, answer, planned=agent.planned, unanswered=agent.unanswered if answer.answered_by == "refused" else None, unsaid=agent.unsaid, mask=mask)
                except Exception as exc:  # noqa: BLE001 - one bad question must not end the run, and a crash is itself a result to compare
                    record = {"question": question, "error": f"{type(exc).__name__}: {exc}"}
                out.write(json.dumps(record, sort_keys=True) + "\n")
                if index % 100 == 0:
                    print(f"{index}/{len(questions)} {time.monotonic() - started:.0f}s", flush=True)
        print(f"wrote {len(questions)} questions to {args.out} in {time.monotonic() - started:.0f}s", flush=True)
    return 0


def _load(path: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """A snapshot file's meta line and its records by question."""
    meta: dict[str, Any] = {}
    records: dict[str, dict[str, Any]] = {}
    for line in path.read_text().splitlines():
        row = json.loads(line)
        if "meta" in row:
            meta = row["meta"]
        else:
            records[row["question"]] = row
    return meta, records


def _question_differences(before: dict[str, Any], after: dict[str, Any], args: argparse.Namespace) -> list[Any]:
    """One question's differences; a crash on either side is one difference
    in a stage of its own, named first."""
    from association.query.stages import Difference, differences, value_differences

    if "error" in before or "error" in after:
        if before.get("error") == after.get("error"):
            return []
        return [Difference("error", "", "changed", before.get("error"), after.get("error"))]
    found = differences(before, after, tolerance=args.tolerance, wording=not args.values_only)
    # What the answer said beside its numbers, as kinds and facts: compared
    # in either mode wherever both runs recorded it - it is what holds a
    # caveat in place while a sentence is reworded. A run from before the
    # remarks were recorded has none to compare.
    if "remarks" in before and "remarks" in after and not args.ignore_remarks:
        found.extend(value_differences("remarks", before["remarks"], after["remarks"], tolerance=args.tolerance))
    # An ignored field is left out whole - a list's items with it ("reading.unread[2]"), as
    # reader_cmp.py leaves them out - so a field both runs hold and a commit moves is compared
    # by its values' counts alone.
    return [each for each in found if f"{each.stage}.{each.path}".split("[")[0] not in args.ignore]


def _ignored_values(records: dict[str, dict[str, Any]], ignored: list[str]) -> dict[str, Counter[str]]:
    """Per ignored ``stage.path``, the values the run holds there by count
    (``<absent>`` where the record has no such field), so a field a commit
    adds is listed, not only left out."""
    found: dict[str, Counter[str]] = {path: Counter() for path in ignored}
    for record in records.values():
        for path in ignored:
            value: Any = record
            for key in path.split("."):
                value = value.get(key) if isinstance(value, dict) else None
                if value is None:
                    break
            found[path][repr(value) if value is not None else "<absent>"] += 1
    return found


def compare(args: argparse.Namespace) -> int:
    """Report every question whose stages differ between two runs; 1 if any."""
    before_meta, before = _load(args.before)
    after_meta, after = _load(args.after)
    print(f"before: {before_meta.get('build')} {before_meta.get('code')}")
    print(f"after:  {after_meta.get('build')} {after_meta.get('code')}")
    for pinned in ("db", "today", "threads", "python", "duckdb", "ollama"):
        if before_meta.get(pinned) != after_meta.get(pinned):
            print(f"WARNING: the runs differ in {pinned}: {before_meta.get(pinned)!r} against {after_meta.get(pinned)!r}")
    only = sorted(before.keys() ^ after.keys())
    for question in only:
        print(f"ONLY IN {'before' if question in before else 'after'}: {question}")
    first_stage: Counter[str] = Counter()
    kinds: Counter[str] = Counter()
    moved: list[tuple[str, list[Any]]] = []
    for question in before:
        if question not in after:
            continue
        found = _question_differences(before[question], after[question], args)
        if found:
            moved.append((question, found))
            first_stage[found[0].stage] += 1
            kinds.update(each.kind for each in found)
    _print_moved(moved, args)
    compared = len(before.keys() & after.keys())
    scope = "values only" if args.values_only else "values and wording"
    for side, records in (("before", before), ("after", after)):
        for path, values in _ignored_values(records, args.ignore).items():
            print(f"IGNORED {path} ({side}): " + ", ".join(f"{value} {count}" for value, count in values.most_common()))
    ignored = f"{len(args.ignore)} field(s) ignored"
    print(f"\n{compared} questions compared ({scope}, tolerance {args.tolerance}, {ignored}): {compared - len(moved)} identical, {len(moved)} differ, {len(only)} in one run only")
    if moved:
        print("first stage that differs: " + ", ".join(f"{stage} {count}" for stage, count in first_stage.most_common()))
        print("kinds of difference: " + ", ".join(f"{kind} {count}" for kind, count in kinds.most_common()))
    if not compared:
        print("NOTHING COMPARED: the two runs share no question - an empty comparison is not a clean one")
        return 2
    return 1 if moved or only else 0


def _print_moved(moved: list[tuple[str, list[Any]]], args: argparse.Namespace) -> None:
    """The first ``--show`` moved questions, ``--lines`` differences each."""
    for question, found in moved[: args.show]:
        print(f"\n{question}")
        for each in found[: args.lines]:
            print(f"    {each.line()}")
        if len(found) > args.lines:
            print(f"    ... and {len(found) - args.lines} more")
    if len(moved) > args.show:
        print(f"\n... and {len(moved) - args.show} more questions (--show)")


def remarks(args: argparse.Namespace) -> int:
    """What a run's answers said beside their numbers: each kind's count,
    and every remark written and not said. 1 if any was not said."""
    _meta, records = _load(args.run)
    kinds: Counter[str] = Counter()
    carrying = 0
    dropped: list[tuple[str, list[str]]] = []
    for question, record in records.items():
        held = record.get("remarks") or {}
        found = [each["kind"] for each in (*held.get("notes", []), *held.get("decisions", []))]
        kinds.update(found)
        carrying += bool(found)
        if held.get("unsaid"):
            dropped.append((question, held["unsaid"]))
    print(f"{len(records)} questions: {carrying} answers carry {sum(kinds.values())} remarks of {len(kinds)} kinds")
    for kind, count in kinds.most_common():
        print(f"  {count:4d}  {kind}")
    for question, unsaid in dropped[: args.show]:
        print(f"WRITTEN AND NOT SAID ({', '.join(unsaid)}): {question}")
    print(f"{len(dropped)} answers have a remark that was written and not said")
    return 1 if dropped else 0


def _load_calls(directory: Path) -> tuple[set[str], dict[tuple[str, int], dict[str, Any]]]:
    """The copies of the code a recorded suite run read, and every call it
    made (``tests/stage_calls.py``) by the test that made it and its position
    among that test's calls."""
    code: set[str] = set()
    calls: dict[tuple[str, int], dict[str, Any]] = {}
    for path in sorted(directory.glob("calls-*.jsonl")):
        for line in path.read_text().splitlines():
            row = json.loads(line)
            if "meta" in row:
                code.add(row["meta"]["code"])
            else:
                calls[(row.pop("test"), row.pop("call"))] = row
    return code, calls


def compare_calls(args: argparse.Namespace) -> int:
    """Report every unit-test call whose arguments or outcome differ between
    two recorded suite runs; 1 if any, or if a call is in one run only."""
    from association.query.stages import value_differences

    before_code, before = _load_calls(args.before)
    after_code, after = _load_calls(args.after)
    print(f"before: {', '.join(sorted(before_code)) or 'unknown'}")
    print(f"after:  {', '.join(sorted(after_code)) or 'unknown'}")
    only = sorted(before.keys() ^ after.keys())
    tests_only = sorted({test for test, _ in only})
    for test in tests_only[: args.show]:
        print(f"CALLS IN ONE RUN ONLY: {test}")
    moved: list[tuple[tuple[str, int], list[Any]]] = []
    for key in sorted(before.keys() & after.keys()):
        found = value_differences(before[key]["boundary"], before[key], after[key], tolerance=args.tolerance)
        if found:
            moved.append((key, found))
    for (test, call), found in moved[: args.show]:
        print(f"\n{test} call {call}")
        for each in found[: args.lines]:
            print(f"    {each.line()}")
        if len(found) > args.lines:
            print(f"    ... and {len(found) - args.lines} more")
    compared = len(before.keys() & after.keys())
    boundaries = Counter(row["boundary"].split(":")[0] if row["boundary"].startswith("template:") else row["boundary"] for row in before.values())
    print(f"\n{compared} calls compared (tolerance {args.tolerance}): {compared - len(moved)} identical, {len(moved)} differ, {len(only)} in one run only ({len(tests_only)} tests)")
    print("calls by boundary (before): " + ", ".join(f"{name} {count}" for name, count in boundaries.most_common()))
    if not compared:
        print("NOTHING COMPARED: the two runs share no call - an empty comparison is not a clean one")
        return 2
    return 1 if moved or only else 0


def main() -> int:
    """Parse the command line and run the subcommand."""
    from association.query.stages import FLOAT_TOLERANCE

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run", help="answer every recorded question and write each stage's output")
    run_parser.add_argument("out", type=Path)
    run_parser.add_argument("--recorded", type=Path, nargs="+", default=list(DEFAULT_RECORDED), help="recorded normalizer replies (jsonl)")
    run_parser.add_argument("--db-path", type=Path, default=Path("nba.duckdb"))
    run_parser.add_argument("--today", default=DEFAULT_TODAY, help="the date the run answers as (ASSOCIATION_TODAY)")
    run_parser.add_argument("--threads", type=int, default=1)
    run_parser.add_argument("--match", action="append", help="only questions containing this text (repeatable)")
    run_parser.add_argument("--feed", action="store_const", dest="recorded", const=[FEED_RECORDED], help=f"answer the 2,082 feed questions instead ({FEED_RECORDED})")
    run_parser.set_defaults(func=run)
    compare_parser = commands.add_parser("compare", help="report the first stage each question differs in between two runs")
    compare_parser.add_argument("before", type=Path)
    compare_parser.add_argument("after", type=Path)
    compare_parser.add_argument("--values-only", action="store_true", help="leave the sentences out (stages.WORDING)")
    compare_parser.add_argument("--ignore-remarks", action="store_true", help="do not compare the notes and stated decisions: only for a change whose whole point is to record more of them")
    compare_parser.add_argument(
        "--ignore", action="append", default=[], metavar="STAGE.PATH", help="leave one field out and list its values by count: only for a field the commit names as added or deleted (repeatable)"
    )
    compare_parser.add_argument("--tolerance", type=float, default=FLOAT_TOLERANCE)
    compare_parser.add_argument("--show", type=int, default=20, help="questions to print")
    compare_parser.add_argument("--lines", type=int, default=6, help="differences to print per question")
    compare_parser.set_defaults(func=compare)
    remarks_parser = commands.add_parser("remarks", help="count a run's notes and stated decisions by kind, and list any written and not said")
    remarks_parser.add_argument("run", type=Path)
    remarks_parser.add_argument("--show", type=int, default=40, help="questions to print")
    remarks_parser.set_defaults(func=remarks)
    calls_parser = commands.add_parser("compare-calls", help="compare two recorded suite runs (ASSOCIATION_STAGE_CALLS), call by call")
    calls_parser.add_argument("before", type=Path)
    calls_parser.add_argument("after", type=Path)
    calls_parser.add_argument("--tolerance", type=float, default=FLOAT_TOLERANCE)
    calls_parser.add_argument("--show", type=int, default=20, help="calls to print")
    calls_parser.add_argument("--lines", type=int, default=6, help="differences to print per call")
    calls_parser.set_defaults(func=compare_calls)
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
