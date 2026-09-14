# Agent memory kit

A shared, validated memory for any repository where humans and AI coding agents take turns. Every agent reads the same files at the start of a session and updates them before it stops, so work survives across sessions, tools and people.

It was designed in the FinPilot repository and works with Claude Code, OpenAI Codex, Cursor, GitHub Copilot, Gemini CLI and people. It needs Python 3.8 or newer and git. pytest is optional.

## The files

| File | Role | Lifetime |
| --- | --- | --- |
| `AGENTS.md` | How to work in this repository: the protocol plus project facts. Most agents read this filename automatically. | Changes rarely |
| `CLAUDE.md` | One line, `@AGENTS.md`, so Claude Code loads the same protocol. | Changes rarely |
| `HANDOFF.md` | Where we are: the last step, repository state, current focus, known limits, decisions and an append-only session log. | Rewritten every session |
| `ROADMAP.md` | Where we are going: goals, sub-goals and tasks, each sub-goal with a horizon, priority, status and dependencies. | Grows over months |
| `scripts/roadmap.py` | Lists actionable work, prints and regenerates the roadmap summary, validates the tree, exports JSON. | Copy as is |
| `scripts/check_handoff.py` | Validates `HANDOFF.md`, `ROADMAP.md` and the links between them. `--strict` also checks git freshness. | Copy as is |
| `.agent-memory.json` | Optional. Lists the path prefixes that count as code for `--strict`. | Per repository |
| `tests/test_handoff.py`, `tests/test_roadmap.py` | Run the validators inside pytest so nobody forgets them. | Copy as is |
| `.github/PULL_REQUEST_TEMPLATE.md` | A checklist that asks for the handoff and roadmap updates. | Copy as is |

## Two kinds of memory

**Session memory lives in `HANDOFF.md`.** It answers "what just happened, and what should I do first?" It stays short because it is rewritten in place every session, except for its session log, which only grows.

**Direction memory lives in `ROADMAP.md`.** It answers "what is the whole plan, what is finished, and what is waiting on whom?" It is long-lived and structured.

They are separate because they change at different speeds. A handoff that tries to hold the whole plan becomes too long to read at the start of every session. A roadmap that tries to hold session state goes stale within a day. The handoff points into the roadmap by ID instead of copying it.

## Levels and horizons are two separate axes

**Levels describe size.** Every item has a stable ID that encodes its place in the tree.

| Level | ID | What it is | Example |
| --- | --- | --- | --- |
| Goal | `G5` | A lasting outcome that is never fully finished | Testing and quality |
| Sub-goal | `G5.1` | A verifiable result with a "Done when" test | Continuous integration |
| Task | `G5.1.2` | Roughly one session of work | Run both validators in the CI workflow |

**Horizons describe timing.** Each sub-goal carries exactly one horizon tag:

| Tag | Meaning | Typical content |
| --- | --- | --- |
| `short-term` | Needed before real users rely on the product, or before the next release | Launch blockers, verified defects, hardening |
| `mid-term` | Expected in the first months after that | Features users expect soon, operational maturity |
| `long-term` | Growth, scale and product decisions | Redesigns, new markets, business questions |

A goal therefore spans horizons. In FinPilot, the long-term goal "G5 Testing and quality" holds a short-term CI sub-goal, mid-term PostgreSQL and browser tests, and a long-term load-testing sub-goal. This answers both "what does testing still need?" and "what must happen before launch?" from the same tree.

Rewrite the three horizon definitions in the roadmap header to fit the repository, for example "before 1.0" for a library, but keep the three tags. The tools depend on them.

**Priority** ranks work inside a horizon: `P0` critical, `P1` high, `P2` normal, `P3` low.

**Status** is one of six values:

| Status | Meaning | Extra line required |
| --- | --- | --- |
| `todo` | Not started | None |
| `doing` | Someone is working on it | None |
| `blocked` | Cannot move because of something outside the tree | `Blocked by:` |
| `decision` | Only the user or owner can decide | `Decision needed:` |
| `done` | Every task is checked and the "Done when" result is true | None |
| `dropped` | Abandoned, kept for the record | Explain why in `Why:` |

**Dependencies** use `needs:` on the metadata line. `roadmap.py next` hides a sub-goal until everything it needs is `done` or `dropped`, and the validator rejects unknown IDs and cycles.

## The sub-goal block

This format is strict because the tools parse it:

```
### G5.1 Continuous integration
`short-term` `P0` `todo` needs: G5.2
Why: nothing runs the tests automatically.
Evidence: no .github/workflows directory exists (checked 2026-09-13).
Done when: every pull request runs the fast tests and both validators, and merging is blocked on failure.
- [ ] G5.1.1 Add a CI workflow.
- [x] G5.1.2 Run both validators in CI. (done 2026-09-20: workflow file ci.yml, run 1432 passed)
```

- The first non-blank line under the heading is the metadata line.
- `Done when:` is required. `Why:` and `Evidence:` are strongly encouraged.
- `Evidence:` states how strongly something was verified: "confirmed by running the app" and "inferred from defaults, not reproduced" are different facts.
- `From:` credits an outside suggestion. `Adapted from:` says it was changed to fit, and why.
- A checked task must carry `(done YYYY-MM-DD: proof)`. No proof, no checkmark.

## HANDOFF.md, section by section

| Section | Holds | Update rule |
| --- | --- | --- |
| 1. Last step | When, who, what happened, state left behind, numbered resume steps | Rewrite every session |
| 2. Repository state | Branch, last commit, uncommitted work, toolchain, last full test run, CI | Rewrite when facts change |
| 3. Done | A capability ledger, not a changelog | Add when a capability lands |
| 4. Next | The current focus as a few roadmap IDs, chosen by the last session | Every ID must exist and still be open |
| 5. Improvement areas | A pointer to the roadmap's "Ideas not yet goals" | Rarely |
| 6. Known limits | Verified limits and risks, each tagged with its roadmap ID | When a limit is found or removed |
| 7. Decisions | Date, decision, reason, so nobody re-litigates | When a decision is made |
| 8. Session log | One entry per session, newest first, never edited afterwards | Every session |

Each session-log entry has a heading `### YYYY-MM-DD | agent | title` and five bullets: **Goal**, **Changed**, **Verified**, **Not done / left broken** and **Next agent should**. Archive the oldest entries to `docs/handoff-archive/<year>.md` after about 30.

## The session protocol

`AGENTS.md` makes every agent follow the same loop.

**At the start of a session**

1. Read `HANDOFF.md` section 1 in full.
2. Skim `HANDOFF.md` sections 4 and 6.
3. Run `python scripts/roadmap.py next` to see actionable work and the decisions waiting on the owner.
4. Run `git status --short` and compare it with `HANDOFF.md` section 2. Trust git when they disagree, and say so in the log.

**While working**

- Set a sub-goal to `doing` when you start it.
- Record newly discovered work as tasks or sub-goals immediately. Do not leave it in chat.
- Ask the owner about anything marked `decision`.

**Before finishing, every time**

1. Check finished tasks with `(done YYYY-MM-DD: proof)` and update sub-goal statuses in `ROADMAP.md`.
2. Run `python scripts/roadmap.py write` to regenerate the summary.
3. Rewrite `HANDOFF.md` section 1 for your session.
4. Update `HANDOFF.md` sections 2 to 7 where facts changed.
5. Add a session-log entry at the top of section 8.
6. Run `python scripts/check_handoff.py --strict` and fix everything it reports.
7. Commit only when asked, and then include `HANDOFF.md` and `ROADMAP.md` with the code they describe.

## What the tools enforce

`scripts/roadmap.py check` rejects:

- IDs that are malformed, duplicated or filed under the wrong parent
- A sub-goal without a valid metadata line or a `Done when:` line
- `decision` without `Decision needed:`, and `blocked` without `Blocked by:`
- A checked task without a dated `(done ...)` note, or with a future date
- A `done` sub-goal with open tasks, and an open sub-goal whose tasks are all checked
- `needs:` pointing at a missing sub-goal, at itself, or into a cycle
- A summary block that no longer matches the tree

`scripts/check_handoff.py` rejects:

- Missing or reordered `HANDOFF.md` sections, and a section 1 without its five fields
- Session-log entries with a bad heading, missing bullets, future dates or the wrong order
- Any roadmap problem, any roadmap ID in `HANDOFF.md` that does not exist, and any section-4 ID that is already closed

With `--strict` it also rejects:

- Code changes without a `HANDOFF.md` change. Code means the prefixes in `.agent-memory.json`, or every non-Markdown path when that file is absent.
- A newest session entry dated before the latest commit

## Why it is built this way

- **Plain Markdown with strict line formats.** It reads well on GitHub and in any editor, diffs cleanly in review, and any agent can edit it. The strict lines still make it machine-checkable.
- **Stable IDs.** Handoff entries, commits and pull requests can cite `G5.1` precisely, and the reference never breaks because IDs are never renumbered or deleted.
- **A generated summary.** Progress counts and horizon tables are computed from the tasks, so they cannot disagree with them.
- **Evidence and dated checkmarks.** They turn "should work" into "this proves it", and they record how strongly each claim was verified.
- **A decision queue.** Agents surface vendor, pricing and product choices instead of making them silently.
- **"Considered and not adopted".** Rejected ideas stay rejected, with reasons, so each new agent does not propose them again.
- **An append-only session log.** It is an honest audit trail, including what was not done.
- **Enforcement where agents already look.** The validators run inside the test suite and belong in CI, so the protocol does not depend on anyone remembering it.
- **Cross-checks.** The handoff cannot point at work that is finished or does not exist, and code cannot change without the handoff changing.

## Install in another repository

1. Copy everything inside this kit's `templates/` folder into the new repository root. Keep the `.github/`, `scripts/` and `tests/` folders.
2. If the repository does not use pytest, delete the two files in `tests/` and run the validators in CI instead.
3. Open the repository in your coding agent and paste the bootstrap prompt below.
4. Check the result yourself:

   ```
   python scripts/roadmap.py check
   python scripts/check_handoff.py --strict
   python scripts/roadmap.py next
   ```

5. Commit the files once you are satisfied.

Use the repository's Python, such as `python3` or `.venv/bin/python`. Codex, Cursor and GitHub Copilot's coding agent read `AGENTS.md`; Claude Code reads `CLAUDE.md`, which imports it. For a tool that expects another file name, add a one-line file that points to `AGENTS.md`, or configure the tool to read it.

## Bootstrap prompt

Paste this exactly into the agent, in the new repository, after copying the templates.

````text
Set up the shared agent memory system in this repository. The kit files are already copied in: AGENTS.md, CLAUDE.md, HANDOFF.md, ROADMAP.md, scripts/roadmap.py, scripts/check_handoff.py, tests/test_handoff.py, tests/test_roadmap.py and .github/PULL_REQUEST_TEMPLATE.md. Keep every structure, heading, tag, format and rule exactly as the templates define them. Replace only placeholders and example content. Do not edit the two scripts.

1. Survey the repository before writing anything: README and other docs, directory layout, language, framework and package manager, how to install, run and test, CI configuration, the git branch, recent commits and uncommitted work.

2. AGENTS.md: keep the protocol sections word for word. Fill "Project facts you need", "Documentation map" and "Engineering conventions that must hold" with facts you verified in step 1. Remove placeholder rows that do not apply.

3. ROADMAP.md: rewrite the three horizon definitions in "How this file works" to fit this project, keeping the tags short-term, mid-term and long-term. Replace the example goals with a real goal tree:
   a. Audit the project for gaps in correctness, security and privacy, identity and accounts, legal and compliance, hosting and operations, testing and CI, build and supply chain, product features, performance and scale, and documentation. Skip areas that do not apply.
   b. Verify each gap in the code or configuration, or by running something. Write what you checked in an Evidence: line and state how strongly it is verified. Never present an assumption as a confirmed fact.
   c. Group gaps into lasting goals (G1, G2, ...). Give each sub-goal a horizon, a priority, a status, needs: dependencies where they are real, a Why: line, a Done when: line and concrete tasks sized for one session.
   d. Record capabilities that already exist as done sub-goals or checked tasks with (done YYYY-MM-DD: proof) notes, so nobody rebuilds them.
   e. Use status decision with a Decision needed: line for anything only I can decide, such as vendors, pricing, pushing, publishing or deleting. Do not decide these yourself.
   f. List suggestions you reject under "Considered and not adopted" with the reason.

4. If some non-Markdown files should not count as code for the strict check, for example generated or vendored folders, create .agent-memory.json containing {"code_paths": [...]} with the path prefixes that do count.

5. HANDOFF.md: fill sections 1 to 7 with verified facts. In section 4, list three to five open roadmap IDs to start with. Replace the example session-log entry with a real entry for this session, dated today.

6. If this project does not use pytest, delete tests/test_handoff.py and tests/test_roadmap.py.

7. Run python scripts/roadmap.py write, then python scripts/roadmap.py check, then python scripts/check_handoff.py --strict, then the two tests if they remain. Fix every problem reported.

8. Do not commit or push unless I ask. Finish by reporting the number of goals and sub-goals, the open short-term sub-goals, the decisions waiting on me, and the exact validation results.
````

## Everyday use

| When | Command or action |
| --- | --- |
| Starting any session | Tell the agent: "Follow AGENTS.md." |
| Seeing what to do next | `python scripts/roadmap.py next` |
| Seeing overall progress | `python scripts/roadmap.py status` |
| After editing `ROADMAP.md` | `python scripts/roadmap.py write` |
| Before committing | `python scripts/check_handoff.py --strict` |
| Making a decision | Tell the agent your answer. It records it in `HANDOFF.md` section 7 and moves the sub-goal forward. |
| Building a dashboard | `python scripts/roadmap.py json` exports the whole tree |
