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

- a function word (``STOPWORDS``): "the", "did", "of" carry no narrowing.
- a word of a name the model copied out of the question, or of a name the
  reading settled on: the recorded reply stands in for the model, and it
  still holds the name after the word is deleted, so the deletion cannot
  be seen. The model read it.
- a word that names the stat the model picked (``STAT_WORDS``), for the
  same reason.

So the instrument is blind to a word only the model could have dropped.
It overcounts too, in one known way: a word a rule matched but did not need
("games" in "last 10 games", which reads as ten games without it) is
reported unread. The baseline is a number to hold, not a list of bugs;
the report's list is where to look for them.
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

#: Words that carry no narrowing of their own. Deliberately short: a word
#: left off this list is counted when unread, and a word wrongly on it hides.
STOPWORDS = frozenset(
    """a an the of in on at to for from by with and or is are was were be been do does did has have had his her their its he she they it
    this that these those what who which how many much me my i show give tell list get find display create generate please can you us nba s vs versus""".split()
) | {"whats", "whos"}  # codespell:ignore whats,whos - the question words as typed with no apostrophe

#: The words that name each stat key the model may pick - what the model's
#: reply stands for in the question. A key missing here claims nothing.
STAT_WORDS: dict[str, str] = {
    "points": r"points?|pts|ppg|scor\w*",
    "rebounds": r"rebounds?|reb|rebs|rpg|boards?",
    "assists": r"assists?|ast|asts|apg|dimes?",
    "steals": r"steals?|stl|stls|spg",
    "blocks": r"blocks?|blk|blks|bpg",
    "turnovers": r"turnovers?|tov|tovs|to",
    "fouls": r"fouls?|pf|pfs",
    "minutes": r"minutes?|mins?|mpg",
    "fieldGoalsMade": r"field|goals?|fgm|fg|made|makes?",
    "fieldGoalsAttempted": r"field|goals?|fga|attempts?|attempted|shots?",
    "fieldGoalPct": r"field|goals?|fg%?|percentage|percent|pct|shooting|%",
    "threePointFieldGoalsMade": r"threes?|3s|3'?s|3pm|3pts?|3-?pt|3-?pointers?|three-?pointers?|pointers?|triples|made|makes?|3|three|point",
    "threePointFieldGoalsAttempted": r"threes?|3s|3pa|3pts?|3-?pt|3-?pointers?|three-?pointers?|pointers?|attempts?|attempted|3|three|point",
    "threePointFieldGoalPct": r"threes?|3s|3p%?|3pt%?|3-?point|three-?point|percentage|percent|pct|shooting|%|3|three|point",
    "twoPointFieldGoalPct": r"twos?|2s|2p%?|2pt%?|2-?pt|2-?point|two-?point|percentage|percent|pct|%|2|two|point",
    "freeThrowsMade": r"free|throws?|ftm|ft|fts|made|makes?",
    "freeThrowsAttempted": r"free|throws?|fta|ft|fts|attempts?|attempted",
    "freeThrowPct": r"free|throws?|ft%?|percentage|percent|pct|%",
    "offensiveRebounds": r"offensive|rebounds?|oreb|orebs|boards?",
    "defensiveRebounds": r"defensive|rebounds?|dreb|drebs|boards?",
    "ts_pct": r"true|shooting|ts%?|percentage|percent|pct|%",
    "efg_pct": r"effective|efg%?|field|goals?|percentage|percent|pct|%",
    "usage_pct": r"usage|usg%?|rate|percentage|pct|%",
    "double_double": r"double-?doubles?|doubles?|dd2?s?",
    "triple_double": r"triple-?doubles?|triples?|doubles?|td3?s?",
    "netpoints": r"net|points?|netpoints?",
    "netpoints_per_100": r"net|points?|netpoints?|per|100|possessions?",
    "netpoints_offense": r"net|points?|netpoints?|offensive|offense",
    "netpoints_defense": r"net|points?|netpoints?|defensive|defense",
    "netpoints_offense_per_100": r"net|points?|netpoints?|offensive|offense|per|100|possessions?",
    "netpoints_defense_per_100": r"net|points?|netpoints?|defensive|defense|per|100|possessions?",
    "wins": r"wins?|won|winning",
    "losses": r"loss(es)?|lost|losing",
    "record": r"record|w-l|wins?|loss(es)?",
    "games_played": r"games?|played|gp",
    "shot_distance": r"shots?|distance|feet|ft|far",
    "points_allowed": r"points?|allowed|allow|opponents?|against|defense",
    "point_differential": r"points?|differential|diff|margin|\+/-|plus-?minus",
}

_TOKEN = re.compile(r"\S+")
_EDGE = re.compile(r"^[^\w%+']+|[^\w%+']+$")


def tokens(question: str) -> list[tuple[int, int, str]]:
    """Each whitespace-separated word as ``(start, end, the word lowercased
    with its leading and trailing punctuation dropped)``."""
    return [(found.start(), found.end(), _EDGE.sub("", found.group()).lower()) for found in _TOKEN.finditer(question)]


def without(question: str, start: int, end: int) -> str:
    """The question with one word deleted and the space it left closed."""
    return re.sub(r"\s+", " ", question[:start] + " " + question[end:]).strip()


def _possessive(word: str) -> str:
    """A word without its possessive ending, typed with either apostrophe."""
    return re.sub("['\u2019]s$", "", word)


def read_by_the_model(word: str, names: list[str], stat: str) -> bool:
    """Whether the model's recorded reply accounts for ``word``: it is part
    of a name the model copied out, or it names the stat the model picked."""
    bare = _possessive(word)
    for name in names:
        if any(bare == _possessive(_EDGE.sub("", part).lower()) for part in name.split()):
            return True
    pattern = STAT_WORDS.get(stat)
    return bool(pattern and re.fullmatch(pattern, bare))


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
    the reading settled on)."""
    return [(start, end, word) for start, end, word in tokens(question) if word and _possessive(word) not in STOPWORDS and not read_by_the_model(word, names, stat)]


def _reader(db_path: Path) -> Any:
    """A function reading one question into its ``reading`` and ``query``
    records, or the refusal it stopped at - the parser's two steps, inside
    the season the Agent answers in."""
    from association.nba.season import season_on_record
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
        with season_on_record(on_record), loaded(con):
            try:
                routed, _subject, _parent = read_route(con, question, names, stat)
                return read_stages(reading_from_route(con, question, routed))
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
            unread = [word for start, end, word in words if read(without(question, start, end), names, stat) == whole]
            out.write(json.dumps({"question": question, "content_words": [word for _, _, word in words], "unread": unread}) + "\n")
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
    return 0


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
