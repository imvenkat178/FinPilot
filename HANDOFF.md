# FinPilot handoff

> Shared working memory for everyone (human or AI) who works on this repository.
> The protocol for using this file is in [AGENTS.md](AGENTS.md).
> Validate your edits with `.venv/Scripts/python.exe scripts/check_handoff.py --strict`.
>
> Sections 1 to 7 describe the **current** truth and are rewritten in place.
> Section 8 is an **append-only** log, newest entry first.

## 1. Last step (read this first)

- **When:** 2026-09-13
- **Who:** Claude Sonnet 5 (Claude Code)
- **What happened:** Committed the accumulated September 12 to 13 work (the reviewed AI workflow layer, conversations, documents, MCP, sample payment actions, and their validation evidence) together with the handoff system added earlier the same day. Commit `76f95b1` on `main`. Checked the staged `.env.example` diff and scanned the diff for secrets before committing; the one hit was a test fixture asserting credential-injection requests are rejected, not a real credential.
- **State left behind:** Working tree is clean except `prototype/`, which was deliberately excluded because it carries its own nested `.git` (adding it with `git add -A` would create a broken gitlink, not real content). `main` is one commit ahead of `origin/main` and has not been pushed.
- **Resume by:**
  1. `git status --short` — should show only `?? prototype/`.
  2. Decide with the user whether/how to push `main`, and what to do with `prototype/` (import its files without the nested repo, add as a real git submodule, or leave it untracked).
  3. Then pick from section 4.

## 2. Repository state

| Item | Current value |
| --- | --- |
| Branch | `main`, one commit ahead of `origin/main` (https://github.com/imvenkat178/FinPilot.git); not pushed |
| Last commit | `76f95b1` (2026-09-13) "feat: reviewed AI workflow layer, documents, MCP, and shared agent handoff system" |
| Uncommitted | Only `prototype/` (untracked, contains its own nested `.git`; excluded on purpose, see section 4) |
| What the last commit contains | The reviewed AI workflow layer (80 typed capabilities, preview and confirm, receipts), saved conversations, document library with citations, MCP client and server, sample payment actions, their validation evidence, and the handoff system itself (`AGENTS.md`, `CLAUDE.md`, `HANDOFF.md`, `scripts/check_handoff.py`, `tests/test_handoff.py`, `.github/PULL_REQUEST_TEMPLATE.md`). Described in `END_TO_END_VALIDATION.md` and `ARCHITECTURE.md` |
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

## 4. Next (prioritized backlog)

| Pri | Item | Why | Where to start |
| --- | --- | --- | --- |
| P0 | Push `main` to `origin` | Commit `76f95b1` exists only locally | `git push origin main`; ask the user first if this is a shared/protected branch |
| P0 | Decide what to do with `prototype/` | It has its own nested `.git`, so it cannot be `git add -A`'d as-is; currently untracked and excluded from every commit | `git -C prototype log --oneline`; options are: copy files in without the nested repo, add as a real `git submodule`, or leave permanently untracked and add to `.gitignore` |
| P1 | Close the 25/140 Llama interpretation gaps | All 25 are 12 s timeouts; `get_mortgage_scenarios`, `compare_card_vs_bank_for_bill`, `update_goal` have no accepted interpretation at all | `END_TO_END_VALIDATION.md` "Remaining interpretation limits"; `finpilot/ai/planner_context.py`, `finpilot/ai/workflows.py`; rerun `scripts/evaluate_workflows.py` |
| P1 | Validate hosted deployment | Docker image, live PostgreSQL service, TLS, multi-process load have never been exercised | `DEPLOYMENT.md`; `scripts/verify_postgres.py` |
| P1 | Add CI | Nothing runs the suites automatically; an 18 minute suite will drift | `.github/workflows/`; consider a fast tier (see section 5) |
| P2 | Identity gaps | No email verification, password recovery, MFA, household invitations | `finpilot/api/app.py` auth routes; needs an email provider decision |
| P2 | Bank linking beyond mocks | Scheduled sync, webhooks, institution update mode, cross-connection account merge are not implemented; live Plaid sandbox never used | `finpilot/services/bank_operations.py`, `ARCHITECTURE.md` "Bank connection" |
| P2 | Document retrieval quality | Lexical only; no OCR, no embeddings | `finpilot/services/document_extract.py`, `finpilot/ai/knowledge.py` |
| P2 | Scaling boundary | Aggregate snapshot decode/write grows with history; dashboard, model-health and AI admission caches are per process | `ARCHITECTURE.md` "Operations and current boundaries"; `finpilot/persistence/database.py` |
| P3 | Faster feedback loop for tests | Full pytest is 18 to 19 min; frontend suites need a running server | pytest markers or a `-m fast` tier; a script that starts the server and runs the Node suites |
| P3 | Line-ending hygiene | Git warns about LF/CRLF on several files | Add `.gitattributes`; do not mass-reformat |

## 5. Improvement areas (ideas, not commitments)

- **Test tiers.** Mark the long AI and migration tests so a sub-minute tier can run on every change and the full suite nightly.
- **One-command frontend verification.** A script that boots uvicorn on a free port, runs all `tests/*_frontend.mjs`, and shuts down.
- **Planner latency.** The 25 remaining Llama failures are all timeouts, not misreadings. Options: smaller per-capability schemas, fewer few-shot tokens, a larger or quantization-faster model, or a GPU host. Measure before and after with `scripts/evaluate_workflows.py`.
- **Evidence regeneration.** `scripts/report_*` produce the validation markdown; a single entry point would stop reports drifting from their JSON.
- **Ledger split.** Move transaction history out of the mutable aggregate snapshot so large households stop paying decode cost on every write.
- **Shared inference gateway.** Needed before running more than one application process against one local model.

## 6. Known limits and risks (do not re-discover)

- Hosted multi-user load, TLS and network latency, live bank behaviour and external MCP OAuth are **unverified**. PostgreSQL checks were local and disposable.
- Real money movement is **not implemented** and must stay that way without an explicit product decision. Simulation is sample-workspace only.
- The Llama evidence is CPU-bound and shared the machine with other processes; treat timings as observations, not benchmarks.
- Frontend suites hit a live server and register throwaway users; they need port 8100 free or `FINPILOT_TEST_URL` set.
- `.local/` holds earlier evaluation runs that are deliberately not merged into the current matrix. Do not cite them as current.
- Web asset fingerprints are set at process start; a stale server serves stale JS.
- The application does not load `.env` on native startup; set variables in the shell or use compose.

## 7. Decisions (do not re-litigate without a reason)

| Date | Decision | Why |
| --- | --- | --- |
| 2026-09-13 | Handoff lives in `HANDOFF.md` with the protocol in `AGENTS.md`; `CLAUDE.md` only imports `AGENTS.md` | One file every agent framework reads; one place to update |
| 2026-09-13 | Validator (`scripts/check_handoff.py`) runs inside pytest as `tests/test_handoff.py` | Any agent that runs the suite is reminded automatically |
| 2026-09-13 | Session log is append-only; archive to `docs/handoff-archive/` past about 30 entries | Keep the audit trail without bloating the file |
| 2026-09-13 | `prototype/` is excluded from commits (nested `.git`, not a submodule) until someone decides how to bring it in | `git add -A` would otherwise write a broken gitlink pointing at an untracked repo |
| 2026-09-11 | Per-operation household snapshot and SQLAlchemy session; no cross-request caching of financial answers except by household revision | Correctness under concurrency; see `ARCHITECTURE.md` |
| 2026-09-12 | Model output never executes; every write goes through preview and explicit confirmation | Financial safety; see `ARCHITECTURE.md` "Reviewed AI workflows" |

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

### 2026-09-13 | Claude Sonnet 5 (Claude Code) | Committed the accumulated work and the handoff system
- **Goal:** Commit the repository's pending work at the user's request ("commit").
- **Changed:** Staged and committed everything except `prototype/` (28 modified files, about 90 new files including the handoff system from the previous entry). Commit `76f95b1` on `main`. Updated this file's sections 1, 2, 4 and 7 to reflect the commit.
- **Verified:** Reviewed the staged `.env.example` diff by hand (only adds commented `FINPILOT_MCP_SERVERS` guidance, no real values). Scanned the full staged diff for secret-shaped strings; the only hit was a test fixture (`test_credentials_rejected_before_storage_or_model`) asserting that credential-injection attempts get HTTP 422, not a real credential. `git status --short` after commit shows only `?? prototype/`. `scripts/check_handoff.py --strict` passes on the updated file.
- **Not done / left broken:** `main` was not pushed to `origin`. `prototype/` remains untracked, its own nested git repo, undecided. Test suite was not re-run after the commit since the commit only staged already-written, already-tested files; no code was newly authored in this session.
- **Next agent should:** Confirm with the user whether to push `main`, then resolve `prototype/` per the P0 item in section 4.
