"""Validate HANDOFF.md, the shared working memory for agents on this repository.

Usage:
    python scripts/check_handoff.py            # structure checks only
    python scripts/check_handoff.py --strict   # also fail when code changed but HANDOFF.md did not

Structure checks (always):
  * every numbered section 1..8 is present, in order
  * section 1 has the five required "Last step" fields
  * section 8 has at least one session entry, each with the five required bullets
  * session entries are dated (YYYY-MM-DD), newest first, and no entry is in the future

Freshness checks (--strict, needs git):
  * if tracked files under the code roots are modified/added/deleted in the working tree
    or index, HANDOFF.md must also be modified or untracked (i.e. it was touched too)
  * the newest session entry must not predate the HEAD commit

Exit code 0 when everything passes, 1 when a check fails, 2 on usage errors.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HANDOFF = REPO_ROOT / "HANDOFF.md"

SECTIONS = [
    "## 1. Last step",
    "## 2. Repository state",
    "## 3. Done",
    "## 4. Next",
    "## 5. Improvement areas",
    "## 6. Known limits",
    "## 7. Decisions",
    "## 8. Session log",
]
LAST_STEP_FIELDS = ["**When:**", "**Who:**", "**What happened:**", "**State left behind:**", "**Resume by:**"]
ENTRY_FIELDS = ["**Goal:**", "**Changed:**", "**Verified:**", "**Not done / left broken:**", "**Next agent should:**"]
ENTRY_HEADING = re.compile(r"^### (\d{4}-\d{2}-\d{2}) \| (.+?) \| (.+?)\s*$")

# Changes under these roots count as "code changed" for --strict.
CODE_ROOTS = ("finpilot/", "tests/", "scripts/", "alembic/", "Dockerfile", "compose.yaml", "requirements.txt")
# Changes only to these never require a handoff update on their own.
EXEMPT = {"HANDOFF.md", "AGENTS.md", "CLAUDE.md"}


def _strip_html_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)


def _section_body(text: str, index: int) -> str:
    """Return the text of SECTIONS[index] up to the next section heading."""
    start = text.find(SECTIONS[index])
    if start < 0:
        return ""
    end = len(text)
    if index + 1 < len(SECTIONS):
        nxt = text.find(SECTIONS[index + 1], start)
        if nxt >= 0:
            end = nxt
    return text[start:end]


def check_structure(text: str) -> list[str]:
    problems: list[str] = []
    text = _strip_html_comments(text)

    last = -1
    for heading in SECTIONS:
        pos = text.find(heading)
        if pos < 0:
            problems.append(f"missing section heading starting with '{heading}'")
        elif pos < last:
            problems.append(f"section '{heading}' is out of order")
        else:
            last = pos
    if problems:
        return problems  # later checks depend on sections existing

    last_step = _section_body(text, 0)
    for field in LAST_STEP_FIELDS:
        if field not in last_step:
            problems.append(f"section 1 is missing the field {field}")

    log = _section_body(text, 7)
    lines = log.splitlines()
    entries: list[tuple[dt.date, str, int]] = []
    current_start = None
    for i, line in enumerate(lines):
        if line.startswith("### "):
            m = ENTRY_HEADING.match(line)
            if not m:
                problems.append(
                    f"session-log heading is not 'YYYY-MM-DD | agent | title': {line.strip()!r}"
                )
                continue
            try:
                date = dt.date.fromisoformat(m.group(1))
            except ValueError:
                problems.append(f"session-log heading has an invalid date: {line.strip()!r}")
                continue
            entries.append((date, line.strip(), i))
    if not entries:
        problems.append("section 8 has no session-log entries")
        return problems

    today = dt.date.today()
    for idx, (date, heading, start) in enumerate(entries):
        end = entries[idx + 1][2] if idx + 1 < len(entries) else len(lines)
        body = "\n".join(lines[start + 1 : end])
        for field in ENTRY_FIELDS:
            if field not in body:
                problems.append(f"entry {heading!r} is missing the bullet {field}")
        if date > today:
            problems.append(f"entry {heading!r} is dated in the future")
        if idx + 1 < len(entries) and date < entries[idx + 1][0]:
            problems.append(
                f"entry {heading!r} is older than the entry below it; newest entries go first"
            )
    return problems


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def check_freshness(text: str) -> list[str]:
    problems: list[str] = []
    try:
        status = _git("status", "--porcelain", "--untracked-files=all")
        head_iso = _git("log", "-1", "--format=%cs").strip()
    except (RuntimeError, FileNotFoundError) as exc:
        return [f"--strict needs git: {exc}"]

    changed: set[str] = set()
    for raw in status.splitlines():
        if len(raw) < 4:
            continue
        path = raw[3:].strip().strip('"')
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        changed.add(path.replace("\\", "/"))

    code_changed = sorted(
        p for p in changed if p not in EXEMPT and p.startswith(CODE_ROOTS)
    )
    handoff_touched = "HANDOFF.md" in changed
    if code_changed and not handoff_touched:
        sample = ", ".join(code_changed[:5]) + (" ..." if len(code_changed) > 5 else "")
        problems.append(
            f"{len(code_changed)} code path(s) changed ({sample}) but HANDOFF.md was not updated; "
            "add a session-log entry and refresh section 1"
        )

    entries = [
        dt.date.fromisoformat(m.group(1))
        for m in (ENTRY_HEADING.match(l) for l in _strip_html_comments(text).splitlines())
        if m
    ]
    if entries and head_iso:
        try:
            head_date = dt.date.fromisoformat(head_iso)
        except ValueError:
            head_date = None
        if head_date and max(entries) < head_date:
            problems.append(
                f"newest session entry ({max(entries)}) predates the HEAD commit ({head_date}); "
                "the last session did not record its work"
            )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--strict", action="store_true", help="also check git freshness")
    parser.add_argument("--path", type=Path, default=HANDOFF, help="handoff file to validate")
    args = parser.parse_args(argv)

    if not args.path.exists():
        print(f"check_handoff: {args.path} does not exist", file=sys.stderr)
        return 2
    text = args.path.read_text(encoding="utf-8")

    problems = check_structure(text)
    if args.strict:
        problems += check_freshness(text)

    if problems:
        print(f"check_handoff: {len(problems)} problem(s) in {args.path.name}")
        for p in problems:
            print(f"  - {p}")
        print("See AGENTS.md for the handoff protocol.")
        return 1
    print(f"check_handoff: {args.path.name} OK" + (" (strict)" if args.strict else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
