#!/usr/bin/env python3
"""Watch a guard fail, without the harness lying to you about it.

    python scripts/perturb.py --test tests/query/test_templates.py \\
        --edit src/association/query/templates.py \\
        --replace 'REBUILT_STATS: frozenset[str] = frozenset({"points"' \\
        --with     'REBUILT_STATS: frozenset[str] = frozenset({"fouls", "points"'

`AGENTS.md` has said "a guard is worth nothing until you have watched it fail"
for a long time, and the discipline is easy to follow in letter while missing
the thing that actually breaks: **the harness reporting CAUGHT for a reason
unrelated to the perturbation.** Every one of these has happened here, and each
looks exactly like a green run from the outside:

- **A red baseline.** One unrelated failing test makes *every* perturbation
  exit non-zero, so all of them read CAUGHT whether the guard works or was
  deleted. This refuses to run until the baseline is green.
- **A filtered run.** ``-k "rebuilt"`` does not match a test named
  ``..._rebuild_counted``. Three guards sat out a whole sweep that was then
  reported on. This runs the whole file, and takes no ``-k``.
- **A retyped anchor.** Hand-escaping a string with apostrophes matches zero
  times; the edit silently does nothing and the suite stays green, which reads
  as MISSED - a *weak guard* - when the truth is that nothing was perturbed.
  This asserts the anchor matches exactly once and that the file changed on
  disk, and says HARNESS FAILED rather than MISSED.
- **A stale ``.pyc``.** A same-size edit inside one second is served from
  cache. This sets ``PYTHONDONTWRITEBYTECODE=1`` for the child runs.

It restores from a byte copy taken before the edit, never ``git checkout``,
because the working tree usually holds other uncommitted work - and it verifies
the restore by hash before exiting.

Exit status is 0 when the perturbation was CAUGHT, 1 when it was MISSED or the
harness could not apply it. A MISSED guard is not a failure of this script; it
is the finding, and it means the test asserts something true under both
branches.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile


def _digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(test_path: str) -> tuple[int, str]:
    """Run one test file, whole, with the bytecode cache disabled."""
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run(
        ["uv", "run", "pytest", test_path, "-q"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return proc.returncode, proc.stdout + proc.stderr


def _failed_tests(output: str) -> list[str]:
    return [line.split(" ")[1].split("::", 1)[-1] for line in output.splitlines() if line.startswith("FAILED ")]


def main() -> int:
    """Apply one perturbation, run the tests, restore, and report."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--edit", required=True, help="Source file to perturb")
    parser.add_argument("--test", required=True, help="Test file to run, whole (no -k: see the module docstring)")
    parser.add_argument("--replace", required=True, help="Exact text to replace. Must match exactly once")
    parser.add_argument("--with", dest="replacement", required=True, help="What to replace it with")
    parser.add_argument("--name", default="", help="Label for the report line")
    args = parser.parse_args()

    source = pathlib.Path(args.edit)
    label = args.name or f"{args.replace[:40]}..."
    if not source.exists():
        print(f"HARNESS FAILED  no such file: {source}")
        return 1

    # 1. The baseline must be green, or CAUGHT means nothing.
    code, output = _run(args.test)
    if code != 0:
        print(f"HARNESS FAILED  baseline is RED - {len(_failed_tests(output))} test(s) already failing.")
        print("                Every perturbation would report CAUGHT regardless of the guard.")
        for name in _failed_tests(output)[:5]:
            print(f"                  {name}")
        return 1
    print(f"baseline green: {output.strip().splitlines()[-1]}")

    original = source.read_text()
    before = _digest(source)
    with tempfile.TemporaryDirectory() as tmp:
        backup = pathlib.Path(tmp) / source.name
        shutil.copy2(source, backup)

        # 2. The anchor must match exactly once, or nothing was perturbed.
        count = original.count(args.replace)
        if count != 1:
            print(f"HARNESS FAILED  anchor matched {count} times, expected 1.")
            print("                Slice the anchor out of the file rather than retyping it.")
            return 1

        source.write_text(original.replace(args.replace, args.replacement))
        if _digest(source) == before:
            print("HARNESS FAILED  file is unchanged after the edit.")
            shutil.copy2(backup, source)
            return 1

        try:
            code, output = _run(args.test)
        finally:
            shutil.copy2(backup, source)

    # 3. The restore has to be verified, not assumed.
    if _digest(source) != before:
        print("HARNESS FAILED  could not restore the original file - check it before continuing.")
        return 1

    if code != 0:
        failed = _failed_tests(output)
        print(f"CAUGHT  {label}")
        for name in failed[:5]:
            print(f"          {name}")
        if len(failed) > 5:
            print(f"          ... and {len(failed) - 5} more")
        return 0

    print(f"MISSED  {label}")
    print("        The suite is green with the behavior reverted, so the test asserts")
    print("        something true under both branches. Rewrite it or delete the guard.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
