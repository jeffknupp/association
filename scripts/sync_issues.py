"""Open a GitHub issue for each ISSUES.md entry that does not have one yet.

`ISSUES.md` is the source of truth; the issues mirror it. An entry already
carrying a `- **GitHub:** #N` line is skipped, and so is one whose title
already exists as an issue, so the script is safe to re-run. The number is
written back into the entry, which is what makes the second run a no-op.

    scripts/sync_issues.py --dry-run
    scripts/sync_issues.py --area data

See "Recording findings" in AGENTS.md.
"""

import argparse
import json
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
PRIORITY = {"P1": ("P1: wrong answer", "bug"), "P2": ("P2: misleading", "bug"), "P3": ("P3: gap", "enhancement"), "P4": ("P4: low", None)}
FOOTER = (
    "\n\n---\n`ISSUES.md` in the repository is the source of truth for this project: this issue mirrors the entry "
    "**{title}** under **{section}**. Keep the entry current there; when a fix removes it, close this issue and name the commit.\n"
)


def entries(text: str):
    section, title, body, fenced = None, None, [], False
    for line in text.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        if not fenced and line.startswith("## P"):
            if title:
                yield section, title, body
                title, body = None, []
            section = line[3:].split(":")[0]
        elif not fenced and line.startswith("### ") and section:
            if title:
                yield section, title, body
            title, body = line[4:].strip(), []
        elif title is not None:
            body.append(line)
    if title:
        yield section, title, body


def labels_for(section: str) -> list[str]:
    """The priority label, plus bug or enhancement. The area label is the caller's call (--area)."""
    name, kind = PRIORITY[section]
    return [name] if kind is None else [name, kind]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print what would be created, create nothing")
    ap.add_argument("--area", choices=("data", "query", "tooling", "documentation"), help="area label for every issue this run creates")
    args = ap.parse_args()
    path = ROOT / "ISSUES.md"
    text = path.read_text()
    existing = {
        i["title"]: i["number"]
        for i in json.loads(subprocess.run(["gh", "issue", "list", "--state", "all", "--limit", "500", "--json", "title,number"], cwd=ROOT, capture_output=True, text=True).stdout or "[]")
    }
    made, skipped = [], []
    for section, title, body_lines in entries(text):
        body = "\n".join(body_lines).strip()
        if "**GitHub:** #" in body or title in existing:
            skipped.append(title)
            continue
        labels = labels_for(section)
        if args.area:
            labels.append(args.area)
        if args.dry_run:
            made.append((section, title, labels))
            continue
        cmd = ["gh", "issue", "create", "--title", title, "--body", body + FOOTER.format(title=title, section=section)]
        for lab in labels:
            cmd += ["--label", lab]
        run = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        if run.returncode:
            print(f"FAILED {title}: {run.stderr.strip()[:160]}")
            return 1
        number = run.stdout.strip().rsplit("/", 1)[-1]
        made.append((section, title, labels, number))
        marker = f"\n- **GitHub:** #{number}"
        anchor = f"### {title}\n{body}"
        assert text.count(anchor) == 1, f"cannot anchor {title}"
        text = text.replace(anchor, anchor + marker, 1)
        path.write_text(text)
        time.sleep(0.5)
    print(f"{'would create' if args.dry_run else 'created'} {len(made)}, skipped {len(skipped)}")
    for row in made:
        print("  ", row[0], "|", ", ".join(row[2]), "|", row[1][:70], ("-> #" + row[3]) if len(row) > 3 else "")
    return 0


sys.exit(main())
