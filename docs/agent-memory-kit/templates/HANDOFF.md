# Project handoff

> Shared working memory for everyone (human or AI) who works on this repository.
> The protocol for using this file is in [AGENTS.md](AGENTS.md).
> Validate your edits with `python scripts/check_handoff.py --strict`.
>
> Sections 1 to 7 describe the **current** truth and are rewritten in place.
> Section 8 is an **append-only** log, newest entry first.

## 1. Last step (read this first)

- **When:** 2026-01-01
- **Who:** <agent or person>
- **What happened:** <what the last session did, stated as facts>
- **State left behind:** <committed or not, branch, anything half-done>
- **Resume by:**
  1. `git status --short` and compare it with section 2.
  2. `python scripts/roadmap.py next` to see actionable work and pending decisions.
  3. <the first concrete step for the next session>

## 2. Repository state

| Item | Current value |
| --- | --- |
| Branch | <branch and remote> |
| Last commit | <short hash, date and subject> |
| Uncommitted | <files, or none> |
| Toolchain verified here | <language and tool versions> |
| Last full test run | <date, command and result, or never> |
| CI | <workflow and its status, or none> |

## 3. Done (capability ledger)

Working and covered by tests unless noted. Git history holds the details.

- **<capability>:** <what works and where the proof is>

## 4. Next (current focus)

The complete goal tree lives in [ROADMAP.md](ROADMAP.md): goals, sub-goals and tasks with horizon, priority, status and dependencies. Run `python scripts/roadmap.py next` for actionable work and pending decisions. Every roadmap ID below must exist and still be open; `scripts/check_handoff.py` enforces this.

Current focus, chosen by the last session:

1. `G1.2` <why it comes first>

## 5. Improvement areas (ideas, not commitments)

Record new ideas in the "Ideas not yet goals" section of `ROADMAP.md`.

## 6. Known limits and risks (do not re-discover)

- <verified limit or risk, how it was verified, and the roadmap ID that tracks it>

## 7. Decisions (do not re-litigate without a reason)

| Date | Decision | Why |
| --- | --- | --- |
| 2026-01-01 | Session memory lives in `HANDOFF.md` and the goal tree in `ROADMAP.md`, both checked by scripts | One source of truth for every person and agent |

## 8. Session log (append newest first)

<!--
Template. Copy it and keep the heading format exactly: date | agent | one-line title.

### YYYY-MM-DD | <agent or person> | <short title>
- **Goal:** what was asked.
- **Changed:** files or areas touched, in one or two lines each.
- **Verified:** commands run and their real results. Say "not run" when not run.
- **Not done / left broken:** anything incomplete, failing, or skipped, and why.
- **Next agent should:** the first concrete thing to do.
-->

### 2026-01-01 | <agent or person> | Adopted the agent memory system
- **Goal:** Give every person and agent one shared memory for this repository.
- **Changed:** Added `AGENTS.md`, `CLAUDE.md`, `HANDOFF.md`, `ROADMAP.md`, `scripts/roadmap.py`, `scripts/check_handoff.py`, the two validator tests and the pull request template.
- **Verified:** <the validator and test commands you ran, with their real results>
- **Not done / left broken:** <anything left for later, or nothing>
- **Next agent should:** <the first concrete step>
