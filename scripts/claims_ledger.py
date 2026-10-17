#!/usr/bin/env python3
"""Which words of each recorded question does the reading depend on?

ROADMAP.md, contract 2: each word of a question is read once, and a content
word nothing read is recorded, never silently dropped - the dropped word is
how a narrower question gets answered fluently ("players with 30 points in
games where ..." answered without the 30). The reader claims spans as it
reads; this measures the same thing from OUTSIDE the reader, so the number
means the same across any rewrite of it:

    a content word is UNREAD when deleting it from the question leaves the
    reading and the planned query exactly as they were.

    PYTHONPATH=<tree>/src uv run python scripts/claims_ledger.py run ledger.jsonl
    uv run python scripts/claims_ledger.py report ledger.jsonl

``run`` reads each recorded question (no model: the normalizer's recorded
reply, the date pinned, as ``scripts/stage_snapshots.py``), then once more
per content word with that word deleted, and writes one line per question
with its unread words. ``report`` prints the count and the most common
unread words, held in ``scripts/ledger_held.json`` ("not grown").

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

The rule is the package's
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
FEED_RECORDED = Path.home() / "association-research" / "stages" / "feed_recorded.jsonl"
HELD_PATH = Path(__file__).resolve().with_name("ledger_held.json")
"""The counts the report holds each whole population to (``corpus``, the
628 recorded questions; ``feed``, the 2,070 the feed's answered population
reads, ``scripts/stage_snapshots.py run --feed``): the unread content words,
the questions holding an unread NUMBER (a word with a digit), and for the
feed the questions whose Reading states other unread words than the
deletion finds and the joined claims by kind - the 628's are
:data:`HELD_DISAGREEMENTS` and :data:`HELD_JOINS`, each held with Jeff's
ruling. A count that grows, or a question new to a list, fails the report;
one that fell or left a list fails it too, on a run of the whole
population, until ``report --shrink`` lowers the file with the change (it
only removes and lowers, as ``scripts/check_ratchets.py --shrink``)."""
POPULATIONS = {"corpus": 628, "feed": 2070}
"""How many questions each held population holds: a run of fewer is part
of one (``--match``) and is held only to what it can show grew."""


def population_of(recorded: list[Path], match: list[str] | None) -> str:
    """Which population a run reads: ``corpus`` (the three recorded files),
    ``feed`` (:data:`FEED_RECORDED`), or ``other`` - which nothing holds."""
    if match:
        return "other"
    paths = [path.resolve() for path in recorded]
    if paths == [path.resolve() for path in DEFAULT_RECORDED]:
        return "corpus"
    return "feed" if paths == [FEED_RECORDED.resolve()] else "other"


def has_number(word: str) -> bool:
    """A word holding a digit - "30", "2nd", "11/29/24", "under40.0%": an
    unread one is a number the reading does not depend on, which is a bug
    unless it restates what the words already read ("100" of "per 100
    possessions")."""
    return any(char.isdigit() for char in word)


def without(question: str, start: int, end: int) -> str:
    """The question with one word deleted and the space it left closed."""
    return re.sub(r"\s+", " ", question[:start] + " " + question[end:]).strip()


def names_read(whole: dict[str, Any]) -> list[str]:
    """Every name the reading settled on - the typed subject's players and
    teams, the cuts' opponent and tenure, the companions named (the absent
    ones; the teammates who played, on the with/without
    split alone and only where none is absent) and the subject reading's
    own players, teams, opponent and tenure (``parse._reading_from_route_names``,
    the same list from the Reading) - so a word of one counts as read where
    the model copied only part of the name ("steph" for "steph curry", which
    the nickname table completes), read off the typed record."""
    reading = whole.get("reading") or {}
    who = reading.get("who") or {}
    cuts = reading.get("cuts") or {}
    found: list[str] = [*who.get("players", []), *who.get("teams", []), *(name for name in (cuts.get("opponent"), cuts.get("tenure")) if isinstance(name, str))]
    companions = reading.get("companions") or []
    absent = [c["player"] for c in companions if c.get("predicate") == "absent" and c.get("side") == "own"]
    # The with/without split, by the key the words asked.
    if reading.get("asked") == {"relation": "team_games", "shape": "split", "by": "presence"} and not absent:
        found.extend(c["player"] for c in companions if c.get("side") == "own" and c.get("predicate") == "played")
    found.extend(absent)
    subject = reading.get("subject") or {}
    found.extend((*subject.get("players", []), *subject.get("teams", []), *(name for name in (subject.get("opponent"), subject.get("own_team")) if isinstance(name, str))))
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
    """The Reading's own unread words, where its record holds them, or
    None."""
    reading = whole.get("reading")
    return list(reading["unread"]) if isinstance(reading, dict) and "unread" in reading else None


def _reader(db_path: Path) -> Any:
    """A function reading one question into its ``reading`` and ``query``
    records, or the refusal it stopped at - the parser's two steps, inside
    the season the Agent answers in."""
    from association.nba.season import season_on_record
    from association.query.compose.plan import plan_point
    from association.query.connection import connect_read_only, latest_season_on_record
    from association.query.entities import names_of
    from association.query.names import loaded
    from association.query.parse import read_route
    from association.query.reading import ScopeError
    from association.query.stages import read_stages

    con = connect_read_only(str(db_path))
    con.execute("SET threads = 1")
    on_record = latest_season_on_record(con)

    def read(question: str, names: list[str], stat: str, joined: list[str] | None = None) -> dict[str, Any]:
        # As Agent.ask reads: inside the season on record, and with the
        # players' and teams' names loaded once for the question. ``joined``
        # collects the ``what`` of each claim the reading joined from two
        # (``span.claimed``), for the held count (:data:`HELD_JOINS`).
        with season_on_record(on_record), loaded():
            try:
                reading = read_route(names_of(con), question, names, stat)
                if joined is not None:
                    joined.extend(claim.what for claim in reading.claims if "+" in claim.what)
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
    items = list(replies.items())
    if args.workers > 1 and not args.shard:
        # The feed pass is run at every push (decision D12), so it is
        # sharded across processes as the stage snapshots are - each
        # question is probed on its own, so N workers write the same rows
        # in the same order in about 1/N the time.
        return _run_sharded(args, items)
    if args.shard:
        which, of = (int(part) for part in args.shard.split("/"))
        items = items[which::of]
    read = _reader(args.db_path)
    meta = {
        "code": str(Path(association.__file__).resolve()),
        "db": str(args.db_path.resolve()),
        "today": args.today,
        "questions": len(replies),
        "population": population_of(args.recorded, args.match),
        **({"shard": args.shard} if args.shard else {}),
    }
    print(json.dumps(meta), flush=True)
    started = time.monotonic()
    with args.out.open("w") as out:
        # The population the ledger was read over, which the report holds
        # it to (HELD_PATH): the 628 recorded questions, the 2,082 feed
        # questions, or neither.
        out.write(json.dumps({"meta": meta}) + "\n")
        for index, (question, reply) in enumerate(items, 1):
            if "error" in reply or (args.match and not any(word.lower() in question.lower() for word in args.match)):
                continue
            out.write(json.dumps(_row(read, question, reply, NORMALIZER_STATS)) + "\n")
            if index % 100 == 0:
                print(f"{index}/{len(replies)} {time.monotonic() - started:.0f}s", flush=True)
    print(f"wrote {args.out} in {time.monotonic() - started:.0f}s", flush=True)
    return 0


def _row(read: Any, question: str, reply: dict[str, Any], stats: Any) -> dict[str, Any]:
    """One question's ledger row (:func:`run`): read whole, then with each
    content word deleted; the words whose deletion leaves the reading and the
    planned query unchanged are unread, beside the Reading's own list."""
    names = [name.strip() for name in reply.get("names") or [] if isinstance(name, str) and name.strip()]
    stat = reply.get("stat") if reply.get("stat") in stats else ""
    joined: list[str] = []
    whole = read(question, names, stat, joined)
    words = content_words(question, [*names, *names_read(whole)], stat)
    probed = _probed(whole)
    unread = [word for start, end, word in words if _probed(read(without(question, start, end), names, stat)) == probed]
    row: dict[str, Any] = {"question": question, "content_words": [word for _, _, word in words], "unread": unread}
    stated = stated_unread(whole)
    if stated is not None:
        row["reading_unread"] = stated
        row["joined"] = joined
    return row


def _run_sharded(args: argparse.Namespace, items: list[tuple[str, dict[str, Any]]]) -> int:
    """``run`` over ``args.workers`` processes, each reading every
    ``workers``-th question (``--shard K/N``) into its own file, the shards
    then joined into ``args.out`` in the questions' own order under one
    meta line - the file a single process would have written."""
    import subprocess

    started = time.monotonic()
    shard_paths = [args.out.with_name(f"{args.out.name}.shard{which}") for which in range(args.workers)]
    command = [sys.executable, str(Path(__file__).resolve()), "run", "--db-path", str(args.db_path), "--today", args.today, "--recorded", *(str(path) for path in args.recorded)]
    for word in args.match or []:
        command += ["--match", word]
    procs = [subprocess.Popen([*command, "--shard", f"{which}/{args.workers}", str(path)], stdout=subprocess.DEVNULL) for which, path in enumerate(shard_paths)]
    failed = [which for which, proc in enumerate(procs) if proc.wait() != 0]
    if failed:
        print(f"shard(s) {failed} failed", flush=True)
        return 1
    meta: dict[str, Any] = {}
    rows: dict[str, str] = {}
    for path in shard_paths:
        for line in path.read_text().splitlines():
            row = json.loads(line)
            if "meta" in row:
                meta = meta or {key: value for key, value in row["meta"].items() if key != "shard"}
            else:
                rows[row["question"]] = line
        path.unlink()
    meta["workers"] = args.workers
    print(json.dumps(meta), flush=True)
    with args.out.open("w") as out:
        out.write(json.dumps({"meta": meta}) + "\n")
        for question, _reply in items:
            if question in rows:
                out.write(rows[question] + "\n")
    print(f"wrote {args.out} in {time.monotonic() - started:.0f}s ({args.workers} workers)", flush=True)
    return 0


def report(args: argparse.Namespace) -> int:
    """The count of unread content words, and the most common ones; 1 where a
    disagreement, a join or a held count (:data:`HELD_PATH`) fails."""
    lines = [json.loads(line) for line in args.ledger.read_text().splitlines()]
    meta = next((line["meta"] for line in lines if "meta" in line), {})
    rows = [line for line in lines if "meta" not in line]
    population = meta.get("population", "other")
    _print_unread(rows, args.top, args.show)
    # The held counts first: under --shrink they lower the file, and the
    # feed's held lists below are read from it as lowered.
    counts = _report_held(rows, population, shrink=getattr(args, "shrink", False))
    if population == "feed":
        held = _held().get("feed", {})
        disagreements = _report_held_disagreements(rows, held)
        joins = _report_joins(rows, held.get("joins", {}), POPULATIONS["feed"])
    else:
        disagreements = _report_disagreements(rows)
        joins = _report_joins(rows)
    return 1 if disagreements or joins or counts else 0


def _print_unread(rows: list[dict[str, Any]], top: int, show: int) -> None:
    """The unread content words, the most common of them, and the questions
    holding an unread number, with ``show`` of each listed."""
    content = sum(len(row["content_words"]) for row in rows)
    unread = Counter(word for row in rows for word in row["unread"])
    with_unread = sum(1 for row in rows if row["unread"])
    print(f"{len(rows)} questions, {content} content words: {sum(unread.values())} unread ({sum(unread.values()) / max(content, 1):.1%}) in {with_unread} questions")
    print("most common: " + ", ".join(f"{word} {count}" for word, count in unread.most_common(top)))
    for row in rows[:show] if show else []:
        if row["unread"]:
            print(f"  {row['question']}  ->  {' '.join(row['unread'])}")
    numbers = [row for row in rows if any(has_number(word) for word in row["unread"])]
    print(f"questions with an unread number: {len(numbers)}")
    for row in numbers[:show] if show else []:
        print(f"  {row['question']}  ->  {[word for word in row['unread'] if has_number(word)]}")


def _held() -> dict[str, Any]:
    """The held counts by population (:data:`HELD_PATH`)."""
    return dict(json.loads(HELD_PATH.read_text())) if HELD_PATH.exists() else {}


def _held_questions(rows: list[dict[str, Any]], what: str, now: set[str], held: set[str], *, whole: bool) -> tuple[int, int, set[str]]:
    """``now`` (the questions of a list found in this run) against ``held``:
    whether a question is new to the list, whether one held that this run
    asked is off it now (it leaves the list with its fix), and the list as
    it may be lowered to."""
    asked = {row["question"] for row in rows}
    new = sorted(now - held)
    gone = sorted((held & asked) - now)
    for question in new:
        print(f"  NEW {what}: {question!r}")
    for question in gone:
        print(f"  GONE {what}: {question!r} - remove it with the change that cleared it (report --shrink)")
    return (1 if new else 0), (1 if gone else 0), (held - set(gone) if whole else held)


def _report_held(rows: list[dict[str, Any]], population: str, *, shrink: bool = False) -> int:
    """A whole population's unread words and unread-number questions
    against :data:`HELD_PATH`: 1 on a grown count or a new question, and -
    on a run of the whole population - on a count that fell or a question
    gone, until ``--shrink`` lowers the file (it never raises a count, and
    a growth still fails under it)."""
    if population not in POPULATIONS:
        return 0
    held_file = _held()
    held = held_file.get(population, {})
    whole = len(rows) >= POPULATIONS[population]
    unread = sum(len(row["unread"]) for row in rows)
    held_unread = int(held.get("unread_words", 0))
    grew, fell = _held_count(f"held ({population}): unread words", unread, held_unread, whole=whole)
    numbers = {row["question"] for row in rows if any(has_number(word) for word in row["unread"])}
    print(f"held ({population}): questions with an unread number {len(numbers)} (held {len(held.get('unread_numbers', []))})")
    new, gone, numbers_lowered = _held_questions(rows, "unread number", numbers, set(held.get("unread_numbers", [])), whole=whole)
    grew, fell = grew | new, fell | gone
    lowered = {**held, "unread_words": min(unread, held_unread) if whole else held_unread, "unread_numbers": sorted(numbers_lowered)}
    if population == "feed" and whole:
        # The feed's disagreements and joins are reported (and fail) beside
        # this; here they are only lowered with the rest under --shrink.
        lowered.update(_feed_lowered(rows, held))
    if not shrink:
        return grew | fell
    if not whole:
        print(f"  --shrink needs a run of the whole population ({POPULATIONS[population]} questions); nothing written")
        return 1
    held_file[population] = lowered
    # The questions as typed, never escaped: the file is read by people and
    # merged by hand, and an escaped "Dončić" reads as a change where none is.
    HELD_PATH.write_text(json.dumps(held_file, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    print(f"  wrote {HELD_PATH.name}: {population} lowered to this run where it fell")
    return grew


def _held_count(what: str, now: int, held: int, *, whole: bool) -> tuple[int, int]:
    """A held count against this run's: whether it grew, and - on a run of
    the whole population - whether it fell (lowered with the change)."""
    print(f"{what} {now} (held {held})")
    if now > held:
        print(f"  GREW: {what.split(': ', 1)[-1]} {held} -> {now}")
        return 1, 0
    if now < held and whole:
        print(f"  FELL: {what.split(': ', 1)[-1]} {held} -> {now} - lower it with the change (report --shrink)")
        return 0, 1
    return 0, 0


def _feed_lowered(rows: list[dict[str, Any]], held: dict[str, Any]) -> dict[str, Any]:
    """The feed's held disagreements and joins as a whole run lowers them:
    a held question still differing, a held kind's count at most this run's."""
    differ = {row["question"] for row in rows if "reading_unread" in row and row["reading_unread"] != row["unread"]}
    joins = Counter(what for row in rows for what in row.get("joined", []))
    return {
        "disagreements": sorted(set(held.get("disagreements", [])) & differ),
        "joins": {what: min(count, joins[what]) for what, count in held.get("joins", {}).items() if joins.get(what)},
    }


def _report_held_disagreements(rows: list[dict[str, Any]], held: dict[str, Any]) -> int:
    """The feed's questions whose Reading states other unread words than
    the deletion finds, against the held list (:data:`HELD_PATH`): the
    subject reading's claims are declared, never probed (``ROADMAP-TYPES.md``,
    "Still open" 16), and the feed's disagreements were measured, not
    examined one by one - so they are held by question, and one new fails."""
    stated = [row for row in rows if "reading_unread" in row]
    differ = {row["question"] for row in stated if row["reading_unread"] != row["unread"]}
    print(f"the Reading's own unread words: {len(stated) - len(differ)} of {len(stated)} questions agree with the deletion, {len(differ)} differ (held {len(held.get('disagreements', []))})")
    new, gone, _ = _held_questions(rows, "disagreement", differ, set(held.get("disagreements", [])), whole=len(rows) >= POPULATIONS["feed"])
    return new | gone


#: The questions whose Reading states other unread words than the deletion
#: finds, held by Jeff's ruling, each with why: every one is the subject
#: reading's, which is read once and never asked again for its claims
#: (``ROADMAP-TYPES.md``, "Still open" 16). ``report`` exits 0 while the
#: questions that differ are exactly these, and 1 on any other question - or
#: on one of these that no longer differs, which is removed from the list with
#: the change that cleared it (``scripts/check_ratchets.py``'s GONE rule).
HELD_DISAGREEMENTS: dict[str, str] = {
    "lebron vs kawhi head to head": (
        '"head" stops the subject reading\'s spelling reader completing "kawhi" (the window "kawhi head" names nobody); '
        'deleting it lets "kawhi" be spelled Kawhi Leonard, so the deletion counts it read, while the reading carries nothing for it'
    ),
    "How many points did the 76ers score in the 4th quarter against Boston this season?": (
        '"against" is claimed with the opponent it stands before, and the second team named is the opponent without it: the subject reading declares its claims, so it claims the versus word it read'
    ),
    "This season, how many times did the 76ers play against the Celtics?": (
        '"against" is claimed with the opponent it stands before, and "play" makes the two teams meet without it: the subject reading declares its claims, so it claims the versus word it read'
    ),
}


def _report_disagreements(rows: list[dict[str, Any]]) -> int:
    """Every question whose Reading states other unread words than the
    deletion finds, each held one (:data:`HELD_DISAGREEMENTS`) marked, and 1
    where another differs or a held one no longer does (0 where the ledger
    was run on a tree whose Reading states none)."""
    stated = [row for row in rows if "reading_unread" in row]
    if not stated:
        print("the Reading's own unread words: not recorded by this tree")
        return 0
    differ = [row for row in stated if row["reading_unread"] != row["unread"]]
    print(f"the Reading's own unread words: {len(stated) - len(differ)} of {len(stated)} questions agree with the deletion, {len(differ)} differ")
    for row in differ:
        held = " (held)" if row["question"] in HELD_DISAGREEMENTS else " (NEW)"
        print(f"  {row['question']}  ->  Reading {row['reading_unread']}  deletion {row['unread']}{held}")
    asked = {row["question"] for row in stated}
    cleared = [question for question in HELD_DISAGREEMENTS if question in asked and question not in {row["question"] for row in differ}]
    for question in cleared:
        print(f"  GONE: {question!r} agrees now - remove it from HELD_DISAGREEMENTS with the change that cleared it")
    new = [row for row in differ if row["question"] not in HELD_DISAGREEMENTS]
    return 1 if new or cleared else 0


#: Every claim the 628's readings join from two that overlap without one
#: holding the other (``span.claimed``: one claim named for both readings),
#: by its ``what``, held as a count (Jeff's ruling): a new kind or a grown
#: count fails ``report``, and a kind that vanishes or a count that falls
#: is lowered here with the change (the ratchets' GONE rule). None is held:
#: a join on the 628 fails the report outright, since a word two rules of
#: one tagger read is a bug.
HELD_JOINS_QUESTIONS = 628
"""The recorded questions :data:`HELD_JOINS` was counted over."""
HELD_JOINS: dict[str, int] = {}


def _report_joins(rows: list[dict[str, Any]], table: dict[str, int] | None = None, questions: int | None = None) -> int:
    """The joined claims by kind against :data:`HELD_JOINS` (or the feed's
    held table, ``table`` over ``questions``), and 1 on a new kind, a grown
    count, or - on a run of every question held - a count that fell or a
    kind gone (lowered or removed with the change); 0 where the ledger was
    run on a tree that records none."""
    if not any("joined" in row for row in rows):
        return 0
    held_joins = HELD_JOINS if table is None else table
    # A run of part of the corpus (``--match``, a test's) can grow a count
    # and cannot vouch for one that fell: only a run of every recorded
    # question is held to the table's falls.
    whole = len(rows) >= (HELD_JOINS_QUESTIONS if questions is None else questions)
    found = Counter(what for row in rows for what in row.get("joined", []))
    print("joined claims: " + (", ".join(f"{what} {count}" for what, count in found.most_common()) or "none"))
    failed = 0
    for what in sorted(set(found) | set(held_joins)):
        held, now = held_joins.get(what, 0), found.get(what, 0)
        if now > held:
            print(f"  {'NEW' if not held else 'GREW'}: {what} {held} -> {now}")
            failed = 1
        elif now < held and whole:
            print(f"  {'GONE' if not now else 'FELL'}: {what} {held} -> {now} - lower the held joins with the change")
            failed = 1
    return failed


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
    run_parser.add_argument("--feed", action="store_const", dest="recorded", const=[FEED_RECORDED], help=f"read the 2,082 feed questions instead ({FEED_RECORDED})")
    run_parser.add_argument("--workers", type=int, default=1, help="read in this many processes, each its own shard, joined into one file in the questions' order (D12)")
    run_parser.add_argument("--shard", default="", metavar="K/N", help=argparse.SUPPRESS)
    run_parser.set_defaults(func=run)
    report_parser = commands.add_parser("report", help="print the count of unread content words and the most common")
    report_parser.add_argument("ledger", type=Path)
    report_parser.add_argument("--top", type=int, default=40)
    report_parser.add_argument("--show", type=int, default=0, help="also print this many questions with their unread words")
    report_parser.add_argument("--shrink", action="store_true", help=f"lower {HELD_PATH.name} to a whole population's run where a count fell or a question left a list; never raises one")
    report_parser.set_defaults(func=report)
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
