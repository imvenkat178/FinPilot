# FinPilot

A hosted personal-finance workspace with separate accounts and detail screens, paycheck planning, cash-flow forecasts, goals, debt and card analysis, and an assistant grounded in financial calculations. The approved Studio interface is used throughout the application.

The application is a modular Python service with authenticated tenant boundaries, persistent data, and short database transactions. PostgreSQL is required for hosted production. SQLite supports development and tests.

## Run locally

From the project directory on Windows:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m uvicorn finpilot.api.app:app --host 127.0.0.1 --port 8100
```

Open [FinPilot](http://127.0.0.1:8100). Create an account to start an empty private workspace. The optional sample-data choice creates a separate fictional workspace where payment simulation is available. An existing sample workspace cannot connect real banks.

Local data is saved to `.local/finpilot.db` by default and survives application restarts. No real credentials or sample user passwords are embedded in the application. Environment variables configure the database, public origin, AI endpoint, and optional bank provider; native startup does not automatically load `.env`.

For hosting, use the nonroot Docker image, PostgreSQL, Alembic migrations, and HTTPS configuration described in [DEPLOYMENT.md](DEPLOYMENT.md). Docker and a live PostgreSQL service were unavailable during local implementation; the image and target database still require validation in the deployment environment.

## Available workflows

| Area | Working behavior |
| --- | --- |
| Identity | Sign up, sign in, revocable sessions, logout, private workspace per user. |
| Accounts | Create/edit cash, card, loan, investment and asset records; source dates; separate summary, activity, analytics and details tabs. |
| Transactions | CSV mapping and preview, atomic import, duplicate detection, paginated history, search and corrections. Imports preserve reported balances. |
| Income | Sources, recurring schedules, expected deposits, and received-income records. |
| Payments | Bills, dated recurrences, funding accounts, exact-occurrence draft checks, and persisted activity. |
| Paychecks | Deadline-aware allocations, saved priorities, monthly targets and shortfalls. |
| Goals | Targets, assigned funds, dates and protected reserves inside existing account balances. |
| Rules | Fixed, percentage and target rules; explicit bill binding; authorization; pause, resume and skip-next. |
| Cash flow | Dated projections, protected cash, spending allowance and operating-buffer calculations. |
| Credit and loans | Debt strategy comparisons, extra payments, mortgage scenarios, utilization and reward calculations. |
| Tax and protection | Editable assumptions, savings/debt comparisons, liquidity tiers and coverage calculations. |
| Assistant | Contextual questions, calculator evidence, saved recent conversations, optional model explanations and calculator fallback. |
| Bank linking | Configurable Plaid Link for supported US checking, savings and money-market accounts; encrypted tokens, cursor sync and disconnect. |
| Sample execution | Reviewed, explicitly confirmed payment simulation, current preflight checks, independent payment legs, idempotency, recovery and audit records. |

Bank linking stays unavailable until the operator configures provider credentials and a token-encryption key. It imports cached bank snapshots, not guaranteed realtime balances. The adapter has mocked integration tests; live bank linking has not been exercised here. Credit/loan terms can be entered manually. Live money movement is not implemented: real workspaces cannot execute simulated settlements against their recorded balances.

Email/password authentication does not include email verification, password recovery, MFA, or household invitations. Those require additional identity/product integrations before a public rollout that depends on them.

## Architecture and latency

[ARCHITECTURE.md](ARCHITECTURE.md) describes the boundaries and tradeoffs in detail.

- FastAPI routes validate requests and derive the household from the session. Account IDs and query parameters do not grant access.
- The runtime gives each operation its own household snapshot and SQLAlchemy session. PostgreSQL row locks, live membership checks, revision checks, ledger projection updates and audit events commit together.
- Versioned allowlisted JSON preserves Decimal amounts, recurrence history, shared mandates and payment lifecycle state. No pickle is used for application persistence.
- The initial screen uses one consistent bootstrap response. Dashboard calculations cache by household revision; transaction history uses an indexed, bounded read endpoint.
- Model discovery and inference stay outside financial transaction locks. Status reads perform no network calls. Inference has a bounded admission limit, request budget, pooled client and failure cooldown.
- Financial engines supply numbers and action states. Model wording is checked against those results; failures use calculator wording. These checks do not establish financial suitability or guarantee every generated interpretation.

The current aggregate snapshot includes history, so large histories still increase decode/write work. The benchmark records that cost rather than claiming unlimited scale or a hosted latency guarantee.

## Local latency measurements

Run the repeatable benchmark with synthetic data and a mocked model:

```powershell
.venv/Scripts/python.exe scripts/benchmark.py --rows 5000 --runs 12 --output .local/benchmark.json
```

Measured on Windows 11 / Python 3.12 with disposable SQLite WAL databases and the real authenticated ASGI request stack (12 repetitions, September 11, 2026):

| Request | Sample median | Sample + 5,000 transactions median / p95 |
| --- | --- | --- |
| Bootstrap, empty application caches | 32 ms | 132 / 210 ms |
| Bootstrap, populated application caches | 13 ms | 16 / 17 ms |
| Indexed account history, first 50 | 4 ms | 6 / 7 ms |
| Update an account | 11 ms | 262 / 314 ms |
| Create an account | 12 ms | 317 / 354 ms |

The 5,000-row CSV import took 595 ms. A held mock inference request remained pending while a financial read and write completed in 265 ms and 351 ms; the benchmark asserts both finish before the mock model is released. Cache validity tests cover another worker's commit, membership removal and calendar rollover.

These are local request timings, not a hosted latency guarantee. They exclude browser rendering, network/TLS and PostgreSQL contention. “Cold” clears application caches while database and operating-system caches remain warm. Full snapshot decoding and writes still scale with transaction history; the bounded SQL history endpoint and warm bootstrap avoid that work. Account deletion is not an exposed operation, so the benchmark measures supported create/read/update routes.

## Project layout

```text
finpilot/
  api/            Authenticated HTTP routes and UI projections
  services/       Identity and validated workspace commands
  persistence/    SQLAlchemy records and versioned codec
  runtime.py      Transactions, snapshot reads, cache and AI admission
  models.py       Financial records, dated occurrences and authority
  engine/         Allocation, cash flow, debt, cards, tax and coverage
  execution/      Sample payment lifecycle and recovery
  integrations/   Optional read-only bank adapter
  ai/             Intent routing, model client and answer checks
  web/            Studio pages, forms, account details and assistant
alembic/          Reviewed database migrations
tests/            Domain, hosted, integration and frontend contracts
```

The `prototype/` directory retains the earlier design artifact. The running application is served by `finpilot.api.app:app`.

## Verification

```powershell
.venv/Scripts/python.exe -m pytest -q
node tests/management_frontend.mjs
node tests/execution_frontend.mjs
# Requires the local server; creates an isolated sample QA account:
node tests/frontend.mjs
```

Tests cover financial examples, payment retries and returns, occurrence accounting, authentication and CSRF, tenant isolation, stale revisions, concurrent writers, persistence across restarts, imports, assistant history, model fallback and migrations. Bank tests use mocked provider responses and synthetic tokens. Browser verification also exercises the new forms against the actual local API and database.

The existing Starlette/AnyIO deprecation warning comes from the installed test-client dependency; it does not fail the suite.
