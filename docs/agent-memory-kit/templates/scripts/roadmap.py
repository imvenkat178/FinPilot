"""Validate, summarize and query ROADMAP.md, the shared goal tree. See AGENTS.md.

Usage:
    python scripts/roadmap.py next     # actionable open work, highest priority first
    python scripts/roadmap.py status   # print the generated summary
    python scripts/roadmap.py write    # regenerate the summary block inside ROADMAP.md
    python scripts/roadmap.py check    # validate structure and summary freshness (exit 1 on problems)
    python scripts/roadmap.py json     # machine-readable goal tree

Structure: goal "## G1. Title" -> sub-goal "### G1.1 Title" -> task "- [ ] G1.1.1 Title".
The first non-blank line under a sub-goal heading must be its metadata, for example
    `short-term` `P0` `todo` needs: G2.1, G4.1
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ROADMAP = REPO_ROOT / "ROADMAP.md"
START = "<!-- roadmap:summary:start -->"
END = "<!-- roadmap:summary:end -->"
HORIZONS = ("short-term", "mid-term", "long-term")
PRIORITIES = ("P0", "P1", "P2", "P3")
STATUSES = ("todo", "doing", "blocked", "decision", "done", "dropped")
CLOSED = ("done", "dropped")

GOAL_RE = re.compile(r"^## (G\d+)\. (.+?)\s*$")
SUB_RE = re.compile(r"^### (G\d+\.\d+) (.+?)\s*$")
META_RE = re.compile(
    r"^`(short-term|mid-term|long-term)` `(P[0-3])` `(todo|doing|blocked|decision|done|dropped)`"
    r"(?:\s+needs:\s*(G\d+\.\d+(?:\s*,\s*G\d+\.\d+)*))?\s*$"
)
TASK_RE = re.compile(r"^- \[([ xX])\] (G\d+\.\d+\.\d+) (.+?)\s*$")
TASKLIKE_RE = re.compile(r"^- \[[^\]]*\] G\d")
DONE_NOTE_RE = re.compile(r"\(done (\d{4}-\d{2}-\d{2})[:,)]")


@dataclass
class Task:
    id: str
    title: str
    done: bool
    line: int


@dataclass
class SubGoal:
    id: str
    title: str
    line: int
    horizon: str = ""
    priority: str = ""
    status: str = ""
    needs: list[str] = field(default_factory=list)
    body: list[str] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)

    def field_text(self, name: str) -> str:
        prefix = name + ":"
        for line in self.body:
            if line.startswith(prefix):
                return line[len(prefix):].strip()
        return ""


@dataclass
class Goal:
    id: str
    title: str
    line: int
    subgoals: list[SubGoal] = field(default_factory=list)


def order(identifier: str) -> tuple[int, ...]:
    return tuple(int(part) for part in identifier[1:].split("."))


def parse(text: str) -> tuple[list[Goal], list[str]]:
    goals: list[Goal] = []
    problems: list[str] = []
    goal: Goal | None = None
    sub: SubGoal | None = None
    pending_meta = in_summary = in_fence = False

    for number, line in enumerate(text.replace("\r\n", "\n").split("\n"), start=1):
        stripped = line.strip()
        if stripped == START:
            in_summary = True
            continue
        if stripped == END:
            in_summary = False
            continue
        if in_summary:
            continue
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        if pending_meta and stripped:
            pending_meta = False
            meta = META_RE.match(stripped)
            if not meta:
                problems.append(f"line {number}: {sub.id} needs a metadata line such as `short-term` `P1` `todo`")
            else:
                sub.horizon, sub.priority, sub.status = meta.group(1), meta.group(2), meta.group(3)
                sub.needs = [n.strip() for n in meta.group(4).split(",")] if meta.group(4) else []
                continue

        if line.startswith("## "):
            match = GOAL_RE.match(line)
            goal = Goal(match.group(1), match.group(2), number) if match else None
            if goal:
                goals.append(goal)
            sub = None
            continue
        if line.startswith("### "):
            match = SUB_RE.match(line)
            if not match:
                if goal:
                    problems.append(f"line {number}: headings inside a goal must be sub-goals like '### G1.2 Title'")
                sub = None
                continue
            if goal is None:
                problems.append(f"line {number}: sub-goal {match.group(1)} is not inside a goal")
                sub = None
                continue
            if not match.group(1).startswith(goal.id + "."):
                problems.append(f"line {number}: sub-goal {match.group(1)} is filed under {goal.id}")
            sub = SubGoal(match.group(1), match.group(2), number)
            goal.subgoals.append(sub)
            pending_meta = True
            continue

        task = TASK_RE.match(line)
        if task:
            if sub is None:
                problems.append(f"line {number}: task {task.group(2)} is not inside a sub-goal")
            else:
                if not task.group(2).startswith(sub.id + "."):
                    problems.append(f"line {number}: task {task.group(2)} is filed under {sub.id}")
                sub.tasks.append(Task(task.group(2), task.group(3), task.group(1) != " ", number))
            continue
        if TASKLIKE_RE.match(line):
            problems.append(f"line {number}: task checkbox must be '- [ ]' or '- [x]' followed by its ID")
            continue
        if sub is not None and stripped:
            sub.body.append(stripped)

    if pending_meta and sub is not None:
        problems.append(f"line {sub.line}: {sub.id} needs a metadata line such as `short-term` `P1` `todo`")
    return goals, problems + validate(goals)


def validate(goals: list[Goal]) -> list[str]:
    problems: list[str] = []
    seen: dict[str, int] = {}
    subs = [s for g in goals for s in g.subgoals]
    today = dt.date.today()

    def claim(identifier: str, line: int):
        if identifier in seen:
            problems.append(f"line {line}: {identifier} duplicates the ID on line {seen[identifier]}")
        else:
            seen[identifier] = line

    for goal in goals:
        claim(goal.id, goal.line)
        if not goal.subgoals:
            problems.append(f"line {goal.line}: goal {goal.id} has no sub-goals")
    for sub in subs:
        claim(sub.id, sub.line)
        for task in sub.tasks:
            claim(task.id, task.line)
            if task.done:
                note = DONE_NOTE_RE.search(task.title)
                if not note:
                    problems.append(f"line {task.line}: checked task {task.id} needs '(done YYYY-MM-DD: evidence)'")
                else:
                    try:
                        if dt.date.fromisoformat(note.group(1)) > today:
                            problems.append(f"line {task.line}: task {task.id} is marked done in the future")
                    except ValueError:
                        problems.append(f"line {task.line}: task {task.id} has an invalid done date")
        if not sub.status:
            continue
        if not sub.field_text("Done when"):
            problems.append(f"line {sub.line}: {sub.id} needs a 'Done when:' line")
        if sub.status == "decision" and not sub.field_text("Decision needed"):
            problems.append(f"line {sub.line}: {sub.id} has status decision but no 'Decision needed:' line")
        if sub.status == "blocked" and not sub.field_text("Blocked by"):
            problems.append(f"line {sub.line}: {sub.id} has status blocked but no 'Blocked by:' line")
        open_tasks = [t.id for t in sub.tasks if not t.done]
        if sub.status == "done" and open_tasks:
            problems.append(f"line {sub.line}: {sub.id} is done but tasks are open: {', '.join(open_tasks)}")
        if sub.status not in CLOSED and sub.tasks and not open_tasks:
            problems.append(f"line {sub.line}: every task in {sub.id} is checked; set its status to done")

    by_id = {s.id: s for s in subs}
    for sub in subs:
        for need in sub.needs:
            if need == sub.id:
                problems.append(f"line {sub.line}: {sub.id} cannot need itself")
            elif need not in by_id:
                problems.append(f"line {sub.line}: {sub.id} needs unknown sub-goal {need}")

    state: dict[str, int] = {}

    def visit(identifier: str, path: list[str]):
        if state.get(identifier) == 2 or identifier not in by_id:
            return
        if state.get(identifier) == 1:
            cycle = path[path.index(identifier):] + [identifier]
            problems.append("dependency cycle: " + " -> ".join(cycle))
            return
        state[identifier] = 1
        for need in by_id[identifier].needs:
            visit(need, path + [identifier])
        state[identifier] = 2

    for identifier in sorted(by_id, key=order):
        visit(identifier, [])
    return problems


def _cell(value: str) -> str:
    return value.replace("|", "\\|")


def summary(goals: list[Goal]) -> str:
    subs = [s for g in goals for s in g.subgoals]
    tasks = [t for s in subs for t in s.tasks]
    counts = {status: sum(s.status == status for s in subs) for status in STATUSES}
    lines = [
        START,
        "_Generated by `python scripts/roadmap.py write`. Do not edit this block by hand._",
        "",
        f"**Progress:** {sum(t.done for t in tasks)} of {len(tasks)} tasks done. "
        f"{counts['done']} of {len(subs)} sub-goals done.",
        "",
        "**Sub-goal status:** " + ", ".join(f"{status} {counts[status]}" for status in STATUSES) + ".",
        "",
        "| Goal | Short-term | Mid-term | Long-term | Tasks done |",
        "| --- | --- | --- | --- | --- |",
    ]
    for goal in goals:
        cells = []
        for horizon in HORIZONS:
            group = [s for s in goal.subgoals if s.horizon == horizon]
            cells.append(f"{sum(s.status == 'done' for s in group)}/{len(group)}" if group else "-")
        goal_tasks = [t for s in goal.subgoals for t in s.tasks]
        lines.append(f"| {goal.id}. {_cell(goal.title)} | " + " | ".join(cells)
                     + f" | {sum(t.done for t in goal_tasks)}/{len(goal_tasks)} |")
    lines += ["", "Horizon cells count finished sub-goals out of all sub-goals in that horizon.", ""]

    def sort_key(s: SubGoal):
        return (PRIORITIES.index(s.priority) if s.priority in PRIORITIES else 9, order(s.id))

    for horizon in HORIZONS:
        group = sorted((s for s in subs if s.horizon == horizon and s.status not in CLOSED), key=sort_key)
        lines += [f"### Open {horizon} sub-goals", ""]
        if not group:
            lines += ["None.", ""]
            continue
        lines += ["| ID | Sub-goal | Priority | Status | Needs |", "| --- | --- | --- | --- | --- |"]
        lines += [f"| {s.id} | {_cell(s.title)} | {s.priority} | {s.status} | {', '.join(s.needs) or '-'} |"
                  for s in group]
        lines.append("")
    for title, status, name in (("Decisions needed from the user", "decision", "Decision needed"),
                                ("Blocked", "blocked", "Blocked by")):
        group = sorted((s for s in subs if s.status == status),
                       key=lambda s: (HORIZONS.index(s.horizon), sort_key(s)))
        lines += [f"### {title}", ""]
        if not group:
            lines += ["None.", ""]
            continue
        lines += [f"| ID | {name} | Horizon |", "| --- | --- | --- |"]
        lines += [f"| {s.id} | {_cell(s.field_text(name))} | {s.horizon} |" for s in group]
        lines.append("")
    lines.append(END)
    return "\n".join(lines)


def _block_bounds(text: str) -> tuple[int, int]:
    start = text.find(START)
    end = text.find(END)
    if start < 0 or end < start:
        raise ValueError(f"ROADMAP.md must contain {START} and {END}")
    return start, end + len(END)


def load(path: Path = ROADMAP) -> tuple[str, list[Goal], list[str]]:
    text = path.read_text(encoding="utf-8")
    goals, problems = parse(text)
    return text, goals, problems


def check(path: Path = ROADMAP) -> list[str]:
    text, goals, problems = load(path)
    try:
        start, end = _block_bounds(text.replace("\r\n", "\n"))
        if text.replace("\r\n", "\n")[start:end] != summary(goals):
            problems.append("the summary block is stale; run: python scripts/roadmap.py write")
    except ValueError as exc:
        problems.append(str(exc))
    return problems


def write(path: Path = ROADMAP) -> list[str]:
    raw = path.read_bytes().decode("utf-8")
    crlf = "\r\n" in raw
    text = raw.replace("\r\n", "\n")
    goals, problems = parse(text)
    start, end = _block_bounds(text)
    text = text[:start] + summary(goals) + text[end:]
    path.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))
    return problems


def actionable(goals: list[Goal], horizon: str | None = None) -> list[SubGoal]:
    subs = [s for g in goals for s in g.subgoals]
    by_id = {s.id: s for s in subs}
    ready = [s for s in subs if s.status in ("todo", "doing")
             and all(by_id[n].status in CLOSED for n in s.needs if n in by_id)
             and (horizon is None or s.horizon == horizon)]
    return sorted(ready, key=lambda s: (HORIZONS.index(s.horizon), PRIORITIES.index(s.priority),
                                        s.status != "doing", order(s.id)))


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("next", "status", "write", "check", "json"))
    parser.add_argument("--path", type=Path, default=ROADMAP)
    parser.add_argument("--horizon", choices=HORIZONS)
    parser.add_argument("--limit", type=int, default=8)
    args = parser.parse_args(argv)
    if not args.path.exists():
        print(f"roadmap: {args.path} does not exist", file=sys.stderr)
        return 2

    if args.command == "check":
        problems = check(args.path)
        if problems:
            print(f"roadmap: {len(problems)} problem(s) in {args.path.name}")
            for problem in problems:
                print(f"  - {problem}")
            return 1
        print(f"roadmap: {args.path.name} OK")
        return 0
    if args.command == "write":
        problems = write(args.path)
        print("roadmap: summary written" + (f"; {len(problems)} problem(s) remain, run check" if problems else ""))
        return 1 if problems else 0

    _, goals, problems = load(args.path)
    if args.command == "json":
        print(json.dumps([asdict(g) for g in goals], indent=2))
    elif args.command == "status":
        print(summary(goals).split("\n", 1)[1].rsplit("\n", 1)[0])
    else:
        ready = actionable(goals, args.horizon)
        print("Actionable now (dependencies finished), highest priority first:")
        for sub in ready[: args.limit]:
            print(f"\n{sub.id} {sub.title}  [{sub.horizon} {sub.priority} {sub.status}]")
            for task in [t for t in sub.tasks if not t.done][:3]:
                print(f"    - {task.id} {task.title}")
        waiting = [s for g in goals for s in g.subgoals if s.status == "decision"]
        if waiting:
            print("\nWaiting on a user decision:")
            for sub in sorted(waiting, key=lambda s: (HORIZONS.index(s.horizon), PRIORITIES.index(s.priority))):
                print(f"    {sub.id} [{sub.horizon} {sub.priority}] {sub.field_text('Decision needed')}")
    if problems:
        print(f"\nroadmap: {len(problems)} structural problem(s); run check", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
