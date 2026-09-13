# Working in this repository (humans and AI agents)

This file is read automatically by Claude Code (via `CLAUDE.md`), OpenAI Codex, Cursor,
GitHub Copilot, Gemini CLI and most other coding agents. If you are an AI agent, treat
everything below as binding instructions for this repository.

## The one rule that matters

**`HANDOFF.md` is the shared memory of this project.** Every agent or person who
changes anything in this repository must read it before starting and update it before
finishing. Chat history is lost between sessions and between tools; `HANDOFF.md` is not.

### At the start of a session

1. Read `HANDOFF.md` section 1 ("Last step") in full. It tells you exactly where the
   previous session stopped, what state it left the tree in, and how to resume.
2. Skim sections 2 (repository state), 4 (next work) and 6 (known limits) so you do not
   re-discover known problems or re-do finished work.
3. Run `git status --short` and compare it with what section 2 says. If they disagree,
   trust git and record the discrepancy in your session-log entry.

### Before you finish (every time, even for small changes)

1. Update section 1 ("Last step") so it describes *your* session: what happened, what
   state the tree is in, and the exact commands or steps the next agent should run first.
2. Update sections 2 to 7 wherever your work changed the facts (something done, something
   new to do, a limit removed or discovered, a decision taken).
3. Add a new entry at the **top** of section 8 ("Session log") using the template there.
   Newest entry first. Never edit or delete older entries; they are the audit trail.
4. Run the validator and fix anything it reports:

   ```powershell
   .venv/Scripts/python.exe scripts/check_handoff.py --strict
   ```

   `--strict` also fails if code changed but `HANDOFF.md` did not.
5. If you commit, include `HANDOFF.md` in the same commit as the code it describes.

### How to write a good handoff

- **Facts, not intentions.** "Ran the full pytest suite: 738 passed" is useful.
  "Tests should pass" is not. If you did not run something, say you did not run it.
- **Be honest about what is broken or unverified.** The next agent will find out anyway;
  finding out from you is cheaper.
- **Point at files and commands**, not descriptions of files. `finpilot/ai/workflows.py`
  beats "the workflow module".
- **Explain the why** for anything a future agent might be tempted to undo.
- **Keep it current, not complete.** Section 3 (done) is a ledger, not a changelog; git
  history holds the details. When the session log exceeds about 30 entries, move the
  oldest ones to `docs/handoff-archive/<year>.md` and link it.

## Project facts you need

| Item | Value |
| --- | --- |
| Product | FinPilot: hosted multi-tenant personal-finance workspace with a reviewed AI assistant |
| Stack | Python 3.12, FastAPI, SQLAlchemy, Alembic, SQLite (dev/test) or PostgreSQL (hosted); vanilla JS frontend under `finpilot/web/`; Node 24 for frontend suites |
| Virtualenv | `.venv/` (Windows: `.venv/Scripts/python.exe`) |
| Run locally | `.venv/Scripts/python.exe -m uvicorn finpilot.api.app:app --host 127.0.0.1 --port 8100` |
| Python tests | `.venv/Scripts/python.exe -m pytest -q` (full suite takes about 18 to 19 minutes; run a single file while iterating) |
| Frontend tests | Need the app running on port 8100 (or `FINPILOT_TEST_URL`). Run `node tests/<name>_frontend.mjs` per suite; see `README.md` for the list |
| Handoff check | `.venv/Scripts/python.exe scripts/check_handoff.py --strict` |
| Migrations | Alembic, `alembic/versions/`. New tables or columns need a migration and `tests/test_migrations.py` must still pass |
| Local data | `.local/` (git-ignored). Never commit it. Evidence files that docs cite live in `validation/` |
| Config | Environment variables only; see `.env.example`. Native startup does not load `.env` |

### Documentation map

| File | Purpose | Update when |
| --- | --- | --- |
| `HANDOFF.md` | Shared working memory: last step, state, backlog, limits, session log | Every session |
| `AGENTS.md` | This file: protocol and project facts for agents | The protocol or a project fact changes |
| `README.md` | User-facing overview, workflows, local run, latency numbers | A user-visible capability changes |
| `ARCHITECTURE.md` | Boundaries, consistency model, AI safety design | A boundary or design decision changes |
| `DEPLOYMENT.md` | Hosting, Postgres, Docker, bank and MCP operator setup | An operator-facing setting changes |
| `MCP.md` | MCP server/client behaviour | MCP behaviour changes |
| `AI_VALIDATION.md`, `END_TO_END_VALIDATION.md` | Dated evidence reports | Only with a new measured run; never edit numbers by hand |

## Engineering conventions that must hold

- **Tenant boundary.** Every read and write derives the household from the session.
  IDs and query parameters never grant access. New routes must follow this.
- **Money is `Decimal`**, never float. Persisted JSON is versioned and allowlisted.
  No pickle anywhere in application persistence.
- **AI never executes.** Models can plan and explain; financial numbers come from the
  engines, writes go through preview plus explicit confirmation. Do not add a path that
  lets model output change records or wording of amounts without server verification.
- **No invented evidence.** Validation documents report measured runs. If you change
  behaviour that a report covers, either re-run and update the evidence file under
  `validation/`, or state in `HANDOFF.md` that the report is now stale.
- **Payment simulation** only exists in sample workspaces (`payment_sandbox=true`).
  Real workspaces must keep rejecting simulation and settlement.
- **Web assets** are fingerprinted at process start; restart the server after editing
  anything under `finpilot/web/`.
- **Line endings.** The repo is mixed LF/CRLF today. Do not reformat whole files just to
  change endings; it hides real diffs.

## Things not to do without asking the user

- Commit or push. Leave changes in the working tree and describe them in `HANDOFF.md`
  unless the user asked for a commit.
- Delete or rewrite session-log history in `HANDOFF.md`.
- Change measured numbers in any validation document.
- Add a real provider credential, `.env` file, or anything under `.local/` to git.
