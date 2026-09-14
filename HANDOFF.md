# FinPilot handoff

> Shared working memory for everyone (human or AI) who works on this repository.
> The protocol for using this file is in [AGENTS.md](AGENTS.md).
> Validate your edits with `python scripts/check_handoff.py --strict`.
>
> Sections 1 to 7 describe the **current** truth and are rewritten in place.
> Section 8 is an **append-only** log, newest entry first.

## 1. Last step (read this first)

- **When:** 2026-09-13
- **Who:** Claude Opus 5 (Claude Code)
- **What happened:** The user asked to implement the agent memory kit in this repository. The system was already installed here, so the session ran the kit's own install checklist against FinPilot and removed every difference from `docs/agent-memory-kit/templates/`. `AGENTS.md` now carries the kit's opening, protocol and "Things not to do" text word for word, while keeping FinPilot's facts, documentation map and conventions, plus a memory-scripts row, a code-paths row and a secrets convention. `ROADMAP.md` uses the kit's rules header with FinPilot's horizon meanings. `CLAUDE.md` is identical to the kit's. Memory commands now use `python`, which works because both scripts need only the standard library. `tests/test_agent_memory_kit.py` fails if the protocol text, roadmap rules or `CLAUDE.md` drift from the kit. No application code changed.
- **State left behind:** Uncommitted, as listed in section 2. Nothing is committed or pushed.
- **Resume by:**
  1. `git status --short` and confirm it matches section 2.
  2. `python scripts/roadmap.py next`.
  3. Ask the user about committing this work and the short-term decisions in section 4, then start `G1.1`.
  4. To change the protocol, edit the kit template first and copy it here; `tests/test_agent_memory_kit.py` enforces the match.

## 2. Repository state

| Item | Current value |
| --- | --- |
| Branch | `main`, 2 commit(s) ahead of the last-fetched `origin/main` (https://github.com/imvenkat178/FinPilot.git); not pushed |
| Last commit | `31424ba` (2026-09-13) "docs: record the commit outcome in HANDOFF.md"; the feature work itself is in `76f95b1` |
| Uncommitted | New `ROADMAP.md`, `scripts/roadmap.py`, `tests/test_roadmap.py`, `tests/test_agent_memory_kit.py`, `docs/agent-memory-kit/`, `.agent-memory.json`, `conftest.py`; modified `AGENTS.md`, `CLAUDE.md`, `HANDOFF.md`, `scripts/check_handoff.py`, `.github/PULL_REQUEST_TEMPLATE.md`; `prototype/` untracked (nested `.git`, see `G6.2`) |
| What `76f95b1` contains | The reviewed AI workflow layer (80 typed capabilities, preview and confirm, receipts), saved conversations, document library with citations, MCP client and server, sample payment actions, their validation evidence, and the handoff system itself (`AGENTS.md`, `CLAUDE.md`, `HANDOFF.md`, `scripts/check_handoff.py`, `tests/test_handoff.py`, `.github/PULL_REQUEST_TEMPLATE.md`). Described in `END_TO_END_VALIDATION.md` and `ARCHITECTURE.md` |
| Toolchain verified here | Python 3.12.14 in `.venv/`, Node v24.19.0, Windows 11 |
| Last full test run | 2026-09-13, before this commit: pytest 738 passed, 1 warning, 18 min 30 s (`validation/python-e2e.txt`); all seven frontend suites passed (`validation/frontend-e2e.txt`). Not re-run after the commit (no application code changed by the commit itself, only staging of already-written files) |
| CI | None. There is no `.github/workflows/` and no pre-commit configuration |
| Local model used for AI evidence | Ollama, `llama3.2:latest` (3.2B Q4_K_M, CPU), 12 s inference budget |

## 3. Done (capability ledger)

Working and covered by tests unless noted. Details live in `README.md` ("Available workflows") and the validation reports.

- **Identity and tenancy:** sign up, sign in, revocable sessions, household boundary derived from the session on every route.
- **Finance core:** accounts, CSV import with mapping and dedupe, income, bills and recurrences, paycheck allocation, goals, rules, cash-flow projection, debt and card analysis, tax and protection calculators.
- **Persistence:** versioned allowlisted JSON snapshots with `Decimal` amounts, PostgreSQL row locks and revision checks, Alembic migrations 0001 to 0008 (upgrade, repeat, downgrade, re-upgrade verified on SQLite and a local disposable PostgreSQL).
- **AI assistant:** deterministic router plus bounded capability planner, 80 typed workflow controls, compound reads, preview then explicit confirmation for every write, receipts restored from conversations, calculator-wording fallback, numeric grounding guards, injection checks on documents and MCP content.
- **Conversations and documents:** server-side saved conversations with URLs, PDF/TXT/Markdown library (2 MB, selectable text only), lexical retrieval with clickable citations.
- **MCP:** read-only MCP server with scoped expiring tokens; MCP client that imports approved source data as documents.
- **Bank linking:** Plaid Link adapter with encrypted tokens, cursor sync, disconnect. Exercised only with mocked provider responses.
- **Sample payment simulation:** preflight, independent legs, idempotency, recovery, audit; restricted to sample workspaces.
- **Deployment assets:** nonroot Dockerfile, `compose.yaml`, `DEPLOYMENT.md`. Docker image not built or run here.
- **Evidence:** benchmark scripts and dated JSON/text evidence under `validation/`; Llama interpretation matrix 115/140 exact, 77/80 capabilities with an accepted interpretation.
- **Agent coordination:** `AGENTS.md` protocol, `HANDOFF.md` validated by `scripts/check_handoff.py`, and `ROADMAP.md` validated and summarized by `scripts/roadmap.py`; both validators run inside pytest. A portable, tested copy for other repositories lives in `docs/agent-memory-kit/`.

## 4. Next (current focus)

The complete goal tree lives in [ROADMAP.md](ROADMAP.md): goals, sub-goals and tasks with horizon, priority, status and dependencies. Run `python scripts/roadmap.py next` for actionable work and pending decisions. Every roadmap ID below must exist and still be open; `scripts/check_handoff.py` enforces this.

Current focus, chosen by the last session:

1. `G1.1` Privacy-safe request logging. A verified defect with a small fix.
2. `G1.2` Correct client addresses behind a proxy. Reproduce behind a proxy first.
3. `G5.2` Fast test tier, which unblocks `G5.1` continuous integration.
4. Ask the user to decide `G6.1` (push), `G7.2` (production model host), `G2.1` (email provider) and `G6.2` (prototype directory).

## 5. Improvement areas (ideas, not commitments)

Moved into `ROADMAP.md` on 2026-09-13: test tiers (`G5.2`), one-command frontend verification (`G5.3`), planner latency (`G7.3`), evidence regeneration (`G5.7`), ledger split (`G10.1`) and shared inference admission (`G4.7`). Record new ideas in the "Ideas not yet goals" section of `ROADMAP.md`.

## 6. Known limits and risks (do not re-discover)

- Hosted multi-user load, TLS and network latency, live bank behaviour and external MCP OAuth are **unverified**. PostgreSQL checks were local and disposable.
- Real money movement is **not implemented** and must stay that way without an explicit product decision. Simulation is sample-workspace only.
- The Llama evidence is CPU-bound and shared the machine with other processes; treat timings as observations, not benchmarks.
- Frontend suites hit a live server and register throwaway users; they need port 8100 free or `FINPILOT_TEST_URL` set.
- `.local/` holds earlier evaluation runs that are deliberately not merged into the current matrix. Do not cite them as current.
- Web asset fingerprints are set at process start; a stale server serves stale JS.
- The application does not load `.env` on native startup; set variables in the shell or use compose.
- Request logging in the shipped image is not what `ARCHITECTURE.md` describes. Under the default uvicorn logging config (the `Dockerfile` CMD), `finpilot.requests` INFO lines are dropped, and the uvicorn access log prints full paths with query strings, for example `GET /api/transactions?q=therapy-copay&category=medical`. Amount-bearing reads such as `/api/debt/what-if?amount=` and `/api/tax/net-benefit?amount=` are logged the same way. Verified 2026-09-13 by running the app locally. Tracked as `G1.1`.
- uvicorn 0.52.4 trusts `X-Forwarded-For` only from `127.0.0.1` unless `FORWARDED_ALLOW_IPS` is set. The IP-keyed throttles in `finpilot/api/auth_routes.py` (register 8 per 5 min, login 30 per 5 min) therefore key on the proxy address behind any proxy that is not on loopback. Tracked as `G1.2`.
- There is no background worker. Bank sync, rule runs and payment simulation only happen on a user request. Nothing sends reminders or notifications, and there is no email capability. Tracked as `G9.1`, `G9.2` and `G2.1`.
- Identity has no recovery or second factor: no password reset, email verification, MFA, password change or session management. Sessions last a fixed 12 hours. Tracked as `G2.2` to `G2.7`.
- Users cannot delete their account or export their data in the app. The MCP read bridge offers read access, not a download. Tracked as `G2.4` and `G2.5`.
- Totals skip accounts whose currency differs from the household base currency, and bank linking is US only. Tracked as `G10.3`.
- In the Claude Code sandbox on this machine, pytest cannot create its default temp or cache directories. Add `-p no:cacheprovider --basetemp <writable dir>`; verified 2026-09-13 with `tests/test_roadmap.py`.

## 7. Decisions (do not re-litigate without a reason)

| Date | Decision | Why |
| --- | --- | --- |
| 2026-09-13 | Handoff lives in `HANDOFF.md` with the protocol in `AGENTS.md`; `CLAUDE.md` only imports `AGENTS.md` | One file every agent framework reads; one place to update |
| 2026-09-13 | Validator (`scripts/check_handoff.py`) runs inside pytest as `tests/test_handoff.py` | Any agent that runs the suite is reminded automatically |
| 2026-09-13 | Session log is append-only; archive to `docs/handoff-archive/` past about 30 entries | Keep the audit trail without bloating the file |
| 2026-09-13 | `prototype/` is excluded from commits (nested `.git`, not a submodule) until someone decides how to bring it in | `git add -A` would otherwise write a broken gitlink pointing at an untracked repo |
| 2026-09-11 | Per-operation household snapshot and SQLAlchemy session; no cross-request caching of financial answers except by household revision | Correctness under concurrency; see `ARCHITECTURE.md` |
| 2026-09-12 | Model output never executes; every write goes through preview and explicit confirmation | Financial safety; see `ARCHITECTURE.md` "Reviewed AI workflows" |
| 2026-09-13 | Long-term goals live in `ROADMAP.md` as goal, sub-goal and task IDs with horizon, priority, status and dependencies; `scripts/roadmap.py write` generates its summary; section 4 here lists only the current focus by ID | One goal tree that every agent reads; generated rollups cannot drift; the handoff stays short |
| 2026-09-13 | Suggestions from the production-readiness list the user supplied were merged into `ROADMAP.md`; ones FinPilot already had are marked done, and seven that conflict with existing design are under "Considered and not adopted" | Keeps revocable server sessions, verified-only answers and revision-keyed caching unless the user decides otherwise |
| 2026-09-13 | The memory system ships as a portable kit in `docs/agent-memory-kit/`; its script, test and PR-template copies must stay identical to this repository's, enforced by `tests/test_agent_memory_kit.py`, and repository-specific settings live in `.agent-memory.json` | Other repositories get exactly the tested system, and FinPilot cannot drift from it |
| 2026-09-13 | FinPilot is the reference installation of the agent memory kit: the `AGENTS.md` protocol text, the `ROADMAP.md` rules header and `CLAUDE.md` must match the kit templates exactly, enforced by `tests/test_agent_memory_kit.py` | The same exact instructions run here and in every repository that installs the kit |

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

### 2026-09-13 | Claude Opus 5 (Claude Code) | Installed the agent memory kit here word for word
- **Goal:** Implement the agent memory kit in this repository exactly as it is meant to be installed elsewhere.
- **Changed:** Aligned `AGENTS.md`, the `ROADMAP.md` rules header and intro command, `CLAUDE.md` and this file's commands with the kit templates. Extended `tests/test_agent_memory_kit.py` with protocol, roadmap-rules and `CLAUDE.md` checks. No application code.
- **Verified:** Compared each live file with its template first: the differences were wording and interpreter paths, while the scripts, their tests and the PR template were already identical. `python --version` reports Python 3.12.10 on PATH. After aligning, `python scripts/roadmap.py check` and `python scripts/check_handoff.py --strict` pass, the three memory test files gave 16 passed with `-p no:cacheprovider --basetemp <scratchpad>`, and full collection is 754 tests. Full suite not run because no application code changed.
- **Not done / left broken:** Nothing committed or pushed. Running the validators in continuous integration is still open as `G5.1`.
- **Next agent should:** Ask the user about committing this work, then start `G1.1`.

### 2026-09-13 | Claude Opus 5 (Claude Code) | Portable agent memory kit for other repositories
- **Goal:** Explain how the shared memory is designed and make it reusable, unchanged, in other repositories.
- **Changed:** Added `docs/agent-memory-kit/` (guide and templates), `.agent-memory.json`, root `conftest.py` and `tests/test_agent_memory_kit.py`. Updated `scripts/check_handoff.py` (config-driven code paths, cache paths ignored, no bytecode when loading the roadmap tool) and `AGENTS.md` (accurate tool list, kit in the documentation map). No application code.
- **Verified:** Installed the templates into a fresh git repository in the scratchpad. The strict check passed on a clean install, pytest there gave 12 passed, the strict check still passed with only `__pycache__` folders untracked, changing `src/app.py` without touching `HANDOFF.md` failed with exactly one problem, and updating `HANDOFF.md` made it pass. An earlier run of the same scenario counted `scripts/__pycache__` as code, which led to the cache fix. In FinPilot, `check_handoff.py --strict` passes, the three memory test files gave 14 passed with `-p no:cacheprovider --basetemp <scratchpad>`, and full collection is 752 tests with `docs/` excluded. Full suite not run because no application code changed.
- **Not done / left broken:** Nothing committed. The kit has not yet been installed in a real second repository.
- **Next agent should:** Ask the user about committing this work, then start `G1.1`.

### 2026-09-13 | Claude Opus 5 (Claude Code) | Roadmap with long-term goals, sub-goals and tasks
- **Goal:** Add long-term goals with sub-goals and tasks, classified into short-term, mid-term and long-term, covering the earlier gap review and the production-readiness list the user pasted.
- **Changed:** Added `ROADMAP.md`, `scripts/roadmap.py` and `tests/test_roadmap.py`. Updated `scripts/check_handoff.py` so it validates the roadmap and the roadmap IDs in this file, plus `AGENTS.md`, `CLAUDE.md`, `.github/PULL_REQUEST_TEMPLATE.md` and sections 1 to 8 here. No application code.
- **Verified:** Checked the pasted suggestions against the code before classifying them: Fernet credential encryption exists; input safety rejects credentials but masks no account numbers or SSNs; LangSmith tracing is opt-in without redaction; audit rows are insert-only in code but not enforced by the database; CSV import detects duplicates and defaults categories to `uncategorized`; `engine/recurring.py` tracks rule occurrences, not subscriptions; document extraction runs inside the upload request; `engine/ledger.py` is a forecast ledger, not double-entry; no pytest markers exist. `scripts/roadmap.py check` passes. `pytest tests/test_roadmap.py tests/test_handoff.py -p no:cacheprovider --basetemp <scratchpad>` gave 12 passed; without `--basetemp`, two tests errored on a sandbox `PermissionError`. A copy of this file with a closed ID and an unknown ID in section 4 failed validation as intended. Full suite not run because no application code changed.
- **Not done / left broken:** Nothing committed. Thirteen roadmap sub-goals wait on user decisions. Horizons and priorities are proposals for the user to adjust.
- **Next agent should:** Ask the user about committing this work and the short-term decisions, then start `G1.1`.

### 2026-09-13 | Claude Opus 5 (Claude Code) | Product-readiness gap review
- **Goal:** Answer what is missing to make FinPilot a complete, launchable product.
- **Changed:** `HANDOFF.md` only: sections 1, 2, 4 and 6 plus this entry. No application code.
- **Verified:** Read the auth service, app middleware, all routes, the Plaid adapter, domain models, `Dockerfile`, `compose.yaml` and `DEPLOYMENT.md`. Searched for schedulers, email, MFA, account deletion, export, metrics, error tracking, billing, legal text, budgets, OCR, admin views and PWA assets; none exist. Ran the app with uvicorn 0.52.4 defaults on port 8765 against a scratchpad SQLite database: `/api/health` returned 200, zero `finpilot.requests` lines were printed, and the access log printed `GET /api/transactions?q=therapy-copay&category=medical`. Read `uvicorn.config.Config.__init__`: `FORWARDED_ALLOW_IPS` defaults to `127.0.0.1`. Test suite not run because no code changed.
- **Not done / left broken:** No fixes applied, since the user asked a question. The proxy finding comes from uvicorn defaults plus missing configuration, not from a run behind a real proxy. Notes about privacy law are general, not legal advice.
- **Next agent should:** Confirm priorities with the user, then fix request logging and forwarded-IP trust first.

### 2026-09-13 | Claude Sonnet 5 (Claude Code) | Committed the accumulated work and the handoff system
- **Goal:** Commit the repository's pending work at the user's request ("commit").
- **Changed:** Staged and committed everything except `prototype/` (28 modified files, about 90 new files including the handoff system from the previous entry). Commit `76f95b1` on `main`. Updated this file's sections 1, 2, 4 and 7 to reflect the commit.
- **Verified:** Reviewed the staged `.env.example` diff by hand (only adds commented `FINPILOT_MCP_SERVERS` guidance, no real values). Scanned the full staged diff for secret-shaped strings; the only hit was a test fixture (`test_credentials_rejected_before_storage_or_model`) asserting that credential-injection attempts get HTTP 422, not a real credential. `git status --short` after commit shows only `?? prototype/`. `scripts/check_handoff.py --strict` passes on the updated file.
- **Not done / left broken:** `main` was not pushed to `origin`. `prototype/` remains untracked, its own nested git repo, undecided. Test suite was not re-run after the commit since the commit only staged already-written, already-tested files; no code was newly authored in this session.
- **Next agent should:** Confirm with the user whether to push `main`, then resolve `prototype/` per the P0 item in section 4.
