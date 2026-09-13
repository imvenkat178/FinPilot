# FinPilot implementation plan

September 13, 2026 · Companion to [CONVERSATIONAL_FINANCE.md](CONVERSATIONAL_FINANCE.md)

## 1. Start with the branch already implemented

Review `codex/conversational-finance` against baseline `3a2bdc9b01a1c860ff086236308fad57966336d7`. It adds the primary chat surface, persistent conversations, scoped evidence and charts, inline setup, reviewed commands, durable confirmations and sample execution. Retain the existing finance engines, authentication, tenant boundary and account screens.

The immediate deliverable is a reviewable conversation foundation. It does not implement live transfers or a background scheduler. The following milestones make the rest of the requested product concrete; complete their acceptance checks before representing them as shipped.

## 2. Run it locally

Use Python 3.12 for the tested environment and Node 24 for frontend contracts. Native startup reads process environment variables and does not automatically load `.env`. Use a fresh disposable database when exploring sample payments.

On Windows PowerShell, after fetching the branch:

```powershell
git fetch origin
git switch codex/conversational-finance
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
$env:FINPILOT_ENV = 'development'
$env:FINPILOT_DATABASE_URL = 'sqlite:///./.local/finpilot-chat.db'
$env:FINPILOT_PUBLIC_ORIGIN = 'http://127.0.0.1:8100'
.venv/Scripts/python.exe -m uvicorn finpilot.api.app:app --host 127.0.0.1 --port 8100
```

On Linux with Python 3.12:

```bash
git fetch origin
git switch codex/conversational-finance
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
export FINPILOT_ENV=development
export FINPILOT_DATABASE_URL=sqlite:///./.local/finpilot-chat.db
export FINPILOT_PUBLIC_ORIGIN=http://127.0.0.1:8100
.venv/bin/python -m uvicorn finpilot.api.app:app --host 127.0.0.1 --port 8100
```

`requirements.lock` is the exact installed Linux/Python 3.12 dependency snapshot used for this verification and the container/CI build. It is not a cross-platform solver lock or a claim of a dependency security audit. `requirements.txt` remains the portable dependency input; resolve and validate separately on Windows and when upgrading dependencies.

Open [the local application](http://127.0.0.1:8100), register and choose a fictional sample workspace to inspect the full calculator and simulation flow. An empty workspace supports manual setup and configured bank reads. The workspace's sample/real distinction is permanent.

If using an Alembic-managed database, run one release migration before starting the new app:

```bash
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m alembic current
```

The expected head is `20260912_0003_conversations`, following `20260911_0002_bank_connections`. It adds conversation, turn and action-proposal tables. Back up existing data first. Do not stamp or downgrade a live database just to bypass a schema mismatch. See [DEPLOYMENT.md](../DEPLOYMENT.md) for PostgreSQL, Compose, HTTPS and legacy development-schema handling.

For the optional private model, set `FINPILOT_LLM_BASE_URL`, `FINPILOT_LLM_MODEL` and its credential in the process environment. Set `FINPILOT_LLM_MAX_TOKENS=1024` initially for typed plans, and evaluate the chosen model's actual JSON reliability. The new planner requests at most 1,024 output tokens and at most twelve seconds; the configured limits may make either smaller. No particular model's language quality has been established by the mocked tests. Provider credentials and bank passwords must never be pasted into chat.

## 3. Try these chat journeys

The account names below belong to the fictional sample workspace. Substitute exact names for an empty workspace. Avoid assuming a full institution name is a canonical identifier across separately linked items.

| Journey | Instructions | Expected result |
| --- | --- | --- |
| Account analysis | Ask “Summarize Everyday Checking.” Then “Show spending last month,” followed by “Show it as a chart.” | Only that account is analyzed. The follow-up retains the earlier period. Sources and the workspace date are visible. |
| Platform analysis | Select First Meridian Bank in the scope control, then ask “Show spending by account.” | Only recorded accounts at that platform are included. |
| Paycheck planning | Select Your household and ask “How should I split my next paycheck?” | A calculated monthly funding plan, paycheck allocations and reasons. It has not sent money. |
| Create monthly allocations | Ask “Split $200 to Emergency & Annual Savings and $100 to Brokerage Cash from Everyday Checking monthly.” | One preview containing two rules. Confirm saves both; a failing operation saves neither. |
| Edit in conversation | Ask “Create a recurring rule,” complete the form, review and confirm. Then ask “Make it $300.” | A new review refers to the saved rule. It does not create a duplicate or carry forward old payment authority. |
| Unsupported per-deposit request | Ask for the same split “every paycheck.” | The app explains the current monthly-target behavior and asks for a monthly target instead of pretending to implement fixed amounts per deposit. |
| Bill setup | Ask “Add a bill,” enter the source, amount and schedule, review and confirm. | The saved obligation appears in the application; future live execution is not implied. |
| Draft and sample run | Ask “Build paycheck payments.” Confirm the draft; use Review simulation on a returned group. Check the sample-simulation box before confirming. | Each leg reports its actual simulated status. A missing mandate or failed preflight remains blocked. |
| Retry and resume | Refresh or sign out/back in, reopen the thread and inspect its confirmed preview. | The durable receipt and applied state remain. Reconfirming the same proposal cannot repeat the financial change. |
| Stale preview | Prepare a rule edit; change financial data elsewhere; then confirm the old proposal. | The app asks for a fresh preview and applies nothing from the stale one. |

Bank connection starts from chat but uses the provider's secure Link UI. That is an intentional part of the journey, not a request for the model to collect banking credentials.

## 4. Milestone A — verify and finish the conversational foundation

Complete this before adding new financial engines.

| Task | Files/boundary | Definition of done |
| --- | --- | --- |
| Browser acceptance | `web/assistant.js`, `conversation-renderer.js`, `conversation.css`, `manage.js` | Exercise the table above in a real browser at desktop and mobile widths; verify refresh, double-click, back navigation, session expiration, keyboard focus, form errors and signed chart values. |
| PostgreSQL concurrency | `runtime.py`, `conversation/service.py`, migrations/tests | Run against a disposable PostgreSQL service using independent sessions/processes: duplicate confirms, concurrent messages, revoked membership, expired proposals and failure rollback. Confirm one financial effect and one receipt. |
| Live-model evaluation | `conversation/planner.py`, `ai/llm.py`, proposed `tests/evals/` | Evaluate paraphrases, ambiguous nicknames, compound edits, negation, date changes, missing amounts, out-of-scope IDs and malicious record text against the intended model. Report measured pass rates and latency. |
| Planning-language precision | Planner, inline rule forms and allocator documentation | Every displayed fixed or income-percentage rule names its monthly basis. Unknown phrasing yields clarification. Do not label a monthly target as an amount guaranteed on each paycheck. |
| Complete chat workflow coverage | `conversation/contracts.py`, `actions.py`, existing command services | Add typed paths for transaction import preview, received-income recording, connection sync/recovery and exact occurrence choices. Reuse existing services and return explicit receipts. These workflows are not all native conversational actions yet. |
| CI and review gate | `.github/workflows/verify.yml` | Require successful Verify FinPilot checks and review before merging. Add a PostgreSQL integration job after its tests exist; compilation alone does not validate row-lock behavior. |

Track clarification quality and task completion, not just intent classification. A valid JSON plan targeting the wrong rule is still a failed interaction. Release criteria should include zero unauthorized effects in the test corpus and deterministic rejection of invented or cross-tenant identifiers; language pass-rate targets should be set from a representative corpus rather than invented as achieved metrics.

The workflow uses official checkout, Python and Node setup actions. Maintain the action versions and dependency snapshot deliberately. [GitHub Python build/test guidance](https://docs.github.com/en/actions/tutorials/build-and-test-code/python), [setup-node](https://github.com/actions/setup-node).

## 5. Milestone B — exact paycheck splits and durable recurring plans

This is the next feature to implement after the conversation review because it directly completes the user's request to split each paycheck.

Add an explicit `amount_basis` to a versioned recurring-policy contract:

| Basis | Meaning | Example |
| --- | --- | --- |
| `monthly_target` | A total for the calendar/planning month, reduced by funding already applied. | $600 total this month, allocated across eligible paychecks. |
| `fixed_per_deposit` | A fixed amount once per eligible received deposit, subject to caps and available funds. | $200 after each verified paycheck. |
| `percent_of_eligible_deposit` | A fraction of one identified received deposit; its base must exclude unrelated transfers/refunds. | 10% of this $2,000 paycheck is $200. |
| `surplus_share` | A fraction of eligible remaining funds after named commitments and the protected floor. | Half the remaining eligible surplus after bills. |

Implementation order:

1. Extend `models.py`, the versioned persistence codec and `WorkspaceService` validation. Migrate old rules to their actual monthly semantics; do not silently reinterpret stored `NET_PAYCHECK` records.
2. Add a durable income occurrence identity tied to source account, provider/source event, amount, status and effective date. Separate an expected deposit from a received and eligible deposit.
3. Extend `engine/allocator.py` so each basis has a separate tested calculation and accounting path. Record reservations and applied funding per policy revision and occurrence. Use unique constraints to prevent duplicate allocation on webhook retries.
4. Define priority, minimum buffer, maximum per run/month, shortfall handling, carry-forward and rounding. Reject incompatible percentages/caps or return a visible shortfall. Do not silently scale user-approved amounts.
5. Add schedule preview in chat: show the next occurrences and an example deposit, then save the exact basis and mandate parameters after review.
6. Add a worker that creates drafts for eligible occurrences. Keep real submission disabled until Milestone D; sample or draft-only automation must be clearly labeled.

Required tests: two versus three paychecks; multiple employers; amount changed before posting; pending-to-posted transition; duplicate provider events; refunds and own transfers excluded; a paycheck received after month end; 31st-day and holiday rules; split rounding preserves cents; a one-time mandate is consumed once; pause/resume/skip targets the correct occurrence; material edits invalidate authority; partial funding and returns do not double-count progress.

Acceptance example using synthetic data: a $2,000 deposit and a $2,500 deposit, with a $200 fixed-per-deposit rule and a 10% rule, produce $200/$200 and $200/$250 respectively before applying explicit affordability constraints. A separate $300 monthly target cannot become $300 on both deposits. A repeated notification for either deposit produces no second allocation.

## 6. Milestone C — trustworthy financial observations

Add `balance_observations`, `term_observations` and canonical provider-account mappings. Store source, retrieval time, effective time if supplied, confidence, currency and revision separately. Missing APR/APY is an unknown value, not zero. Preserve the raw source reference without exposing secrets to prompts or logs.

Extend the existing bank adapter incrementally: scheduled cursor sync, authenticated provider webhooks, idempotent ingestion, Link update-mode recovery, liability/card terms where licensed and supported, and investment/cash observations where supported. Keep institution/product capabilities explicit. An aggregator does not guarantee every institution supplies every rate, liability term or payment function.

Add statement import as a reviewable fallback: upload into isolated storage, validate file type/size, extract candidate fields, show the source page and let the user confirm corrections. Untrusted statement text must not be added to the model's instruction channel. Account merging must use provider/ownership evidence and user review; matching the last four digits alone is insufficient.

Definition of done: no double-counted pending/posted records, reconnect cannot restore a revoked connection, incomplete provider responses retain stale provenance, rate changes carry effective dates, sync retries preserve cursor correctness, and a stale balance blocks any action whose preflight requires freshness.

## 7. Milestone D — real money movement

Choose one supported US account-to-account use case and payment provider first. Establish the provider's enabled products, ownership/identity requirements, permitted funds flow, operating responsibilities and commercial arrangement. Resolve the applicable legal and consumer-protection obligations with the provider and qualified counsel for that concrete design. This is a product integration gate, not something a model prompt or sample mandate can satisfy.

| Engineering task | Required result |
| --- | --- |
| Payment-intent model | Immutable source, destination/payee, amount, currency, fee bound, earliest/latest dates, mandate version and actor. |
| Reservations | Authoritative pending outgoing amounts are counted once across concurrent groups and forecasts. |
| Transactional outbox | Intent, reservation, audit and job commit together. Worker claims have leases and retry limits. |
| Provider adapter | Typed authorize, submit, lookup, cancel-if-supported and normalize-event operations; stable idempotency key per payment intent. |
| Webhook inbox | Verify signatures according to the provider; unique event key; tolerate duplicates and out-of-order delivery. |
| Unknown-outcome recovery | Timeout preserves an uncertain state. Look up the original request before attempting another submission. |
| Reconciliation | Match external status and bank observations to internal legs; preserve returns and compensating events. |
| Conversation receipts | Show “submitted,” “processing,” “settled,” “returned” or “failed” with a timestamp and next action. Never infer settlement from a successful HTTP call. |
| Operations | Global/provider/account pause, limits, controlled retries, exception queue, audit and incident recovery. |

Start with provider Sandbox using synthetic funds, then a restricted production pilot when the concrete integration is authorized and verified. Prove restart recovery, crash after provider acceptance, duplicate events, insufficient funds, authorization revocation, partial group failure and later returns. No public production enablement is included in this branch.

Loan, mortgage and credit-card payments require payee/servicer-specific support. Bank transfers into a brokerage require destination eligibility; investments and payroll elections have different constraints. Do not make a generic transfer endpoint promise all those workflows.

## 8. Milestone E — broad, reliable conversation behavior

Create a typed `workflow_run` for compound tasks with slots such as source account, destinations, amounts, basis, dates, constraints and chosen strategy. The planner should ask one useful missing-input question, retain validated answers, recalculate after edits and show a new preview when the plan changes.

Add named workflow handlers for onboarding, monthly planning, debt payoff, card selection, windfall allocation, connection recovery and payment support. These can be modules in the monolith. Use a single orchestrator to select a bounded workflow; do not create a network of autonomous money-moving agents.

Introduce event streaming only after event identities, replay and permission behavior are defined. Suggested events are `input_required`, `calculation_ready`, `preview_ready`, `action_status` and `response_complete`. Stream progress and validated components; final financial numbers come from completed calculators. Reconnecting a stream must not rerun a payment.

Add explicitly saved preferences, approved aliases and pinned scenarios. Memory writes require a visible user-approved change when they influence future allocations. Store “prefers a larger emergency buffer” separately from an actual monetary reserve rule. A prior conversation cannot silently establish standing authority.

## 9. Milestone F — complete rewards, debt, tax and HNW journeys

Deliver one vertical workflow at a time through the same chat/query/preview/action protocol:

| Workflow | Implementation detail | Acceptance criterion |
| --- | --- | --- |
| Owned-card advisor | Versioned issuer terms, MCC/merchant rules, credits, caps, grace-period facts, point-value assumptions and source dates. | A higher gross reward loses when verified fees/interest make its net benefit lower; unknown terms produce conditions or a question. |
| Card usage integration | Supported merchant/wallet/biller adapter with tokenization and explicit user selection. | Changing the recommended card alone never claims the merchant's payment setting changed. |
| Debt cost minimization | Contract-specific daily accrual, fees, minimums, promo buckets, payment allocation and post-tax assumptions. | Compare feasible schedules over the same horizon, preserve minimums and show truncation/unknown terms. |
| Mortgage scenarios | Principal/escrow separation, servicer recast eligibility, prepayment rules and payment-credit dates. | Lower recast payment and shorter payoff term are represented separately; unsupported servicer actions remain unavailable. |
| Cash routing | Current owned destinations, restrictions, cutoffs, net yield, buffer forecast and sweep hysteresis. | Tiny yield changes do not cause repeated transfers; foreseeable bill shortfalls prevent a sweep. |
| Coverage | Insured-bank identity, category, legal owner, beneficiaries when required and sweep allocations. | Accounts at different brands of one bank are not automatically treated as separately insured. Unknown ownership does not produce a guaranteed coverage claim. |
| HNW liquidity | Verified entity authority, collateral terms, borrowing-base stress and contribution restrictions. | No cross-entity move or asset sale is inferred from household membership; a stress scenario never submits a borrowing request. |
| Curated education | Reviewed source/timestamp metadata and concise, contextual retrieval. | Educational content cannot override calculators, ownership, user preferences or action authority. |

Extend the scenario catalog in the architecture guide into fixtures with expected inputs, allowed tools, required clarifications, numerical outputs and prohibited effects. Keep finance-engine golden tests distinct from language-model evaluations.

## 10. Data and operational scaling

Keep PostgreSQL as the source of truth. Use normalized immutable transaction/payment events and bounded projections when history size makes aggregate decoding expensive. Migrate with reconciliation totals and a compatibility version; do not split the ledger and snapshot across services without a consistency design.

A single database-backed worker is sufficient initially. Add a managed durable workflow engine or queue when job volume and operating needs justify it. Redis may support shared caching or admission, but a cache lock is not the source of payment idempotency. Unique database/provider keys and durable states must survive cache loss.

For HNW and household collaboration, add explicit ownership and authorization edges to relational tables before considering a graph database. Most early queries are scoped account lookups, bounded ownership checks, schedule selection and event reconciliation. Keep delegation scope and expiry inspectable.

Measure per-route latency, model latency/tokens, clarification rate, workflow completion, stale-preview rejection, duplicate suppression, sync lag, outbox age and reconciliation exceptions. Exclude raw questions, account numbers and tokens from routine telemetry. Add retention/export/deletion policies for conversations and financial evidence; deletion must account for required operational records rather than silently erasing payment history.

## 11. Verification completed and still required

Local verification of this branch:

| Check | Result |
| --- | --- |
| Existing baseline backend suite | 357 passed before the conversation changes. |
| Full backend suite after changes | 388 passed, including 31 new conversation tests. One existing Starlette/AnyIO test-client deprecation warning. |
| Web contract suites | All five passed: main frontend, management, execution, bank and conversation. |
| Conversation behavior | Scope/filter follow-ups, observed spending, auth/CSRF, private threads, invalid model output, stale/expired/tampered proposals, restart persistence, membership revocation, concurrent confirmation, batch rollback and sample/real boundaries. |
| Migrations | Disposable SQLite round trip and schema comparison; PostgreSQL SQL compilation. |

Run the same checks:

```bash
.venv/bin/python -m pytest tests -q
.venv/bin/python scripts/test_web.py
```

The web script starts its own disposable authenticated local API and runs Node render contracts. It does not contact a bank or real model and does not constitute browser interaction testing. In this review environment the browser could not reach the isolated local server, so no completed visual or click-through review is claimed. Live PostgreSQL concurrency, Docker/Compose startup, live model quality and actual Plaid/provider behavior remain environment-specific checks.

## 12. Suggested engineering handoff

Use this instruction for the next implementation task:

> Continue from the reviewed conversational-finance branch. Implement explicit allocation bases and per-received-deposit accounting as described in Milestone B. Preserve all existing monthly-target behavior through a versioned migration. Extend the existing Python domain services, typed conversation contracts and inline previews. Use verified occurrence identity, decimal arithmetic, caps, shortfall reporting and idempotent accounting. Keep payment simulation and real workspaces separate. Do not add live payment calls or claim a scheduled real transfer is available. Deliver the code, migration, behavioral tests, chat acceptance examples and updated capability/status documentation in a separate reviewable PR.

After that, complete financial observations and the provider-backed execution protocol. Ship a small number of end-to-end chat journeys with accurate outcomes before adding more unsupported commands to the prompt.
