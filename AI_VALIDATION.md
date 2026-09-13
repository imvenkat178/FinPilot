> Historical implementation baseline. See [END_TO_END_VALIDATION.md](END_TO_END_VALIDATION.md) for the subsequent fixes and current end-to-end results.

# AI workflow validation

Current implementation audit: September 13, 2026. The workflow controls and reviewed executor are implemented. Complete natural-language coverage is **not established** unless every capability and required paraphrase passes interpretation; fallbacks never count as model understanding.

## Automated application checks

The final Python result is recorded in [python-suite.txt](validation/python-suite.txt). Seven frontend suites cover main screens, management, execution, bank linking, assistant history/sources and reviewed workflows; see [frontend-suites.txt](validation/frontend-suites.txt).

The executable [capability manifest](tests/chat_capability_manifest.py) covers all 80 catalog entries. Each write case runs request, preview, explicit confirmation, database/revision verification, refreshed bootstrap, duplicate confirmation and saved receipt checks. Additional tests cover edits, cancellation, expired/stale proposals, separate workers, conflicting confirmations, roles/tenants, CSV mappings and duplicates, private attachments, interrupted providers, compound reads/changes, clarification restoration, credential rejection and injected document/MCP instructions. Paycheck event creation/editing, regenerated expectations, received history, card terms and cleared rule mandates have dedicated assertions.

SQLite migration upgrade/repeat/downgrade/upgrade matches SQLAlchemy metadata. PostgreSQL DDL compiles. A live PostgreSQL server and hosted load/concurrency checks were unavailable; SQLite checks do not replace them.

## Local Llama results: normal 12-second budget

Provider: local Ollama, model: llama3.2:latest (3.2B Q4_K_M, CPU). No cloud model substituted. Calls are sequential, with one planning call and at most one explanation sharing the inference budget. The typed planner constrains operation names and argument fields, followed by full server validation. The HTTP/API duration includes additional local work.

| Measurement | Result |
| --- | --- |
| Exact canonical interpretations | 90/140 |
| Distinct capabilities with at least one accepted interpretation | 57/80 |
| Reviewed fictional actions confirmed | 57 |
| Confirmed receipts restored from conversations | 57 |
| Accepted model introductions | 2/31 attempted |
| Median / p95 / maximum API duration | 5.32s / 11.92s / 12.08s |

The matrix forces model planning even for requests that normally take a deterministic route, so it measures planner understanding rather than overall application success. The later compound shortlist correction leaves the candidate lists unchanged for all 80 base requests; supplemental scenario runs exercise that correction.

The matrix includes one base question for each capability and two additional paraphrases for each of 30 action families. Each case compares the exact canonical operation and arguments, not merely successful text generation or schema acceptance. Only a correctly interpreted fictional proposal is confirmed. The evidence records expected/actual arguments, planner status, model calls, explanation acceptance, fallback, preview, receipt, revision and persistence separately.

Financial calculations and consequential numbers remain server-rendered. A workflow model introduction cannot replace the calculator wording; numerical introductions are rejected. Ordinary deterministic calculator routes retain their established answer checks. These guards do not prove complete semantic correctness.

All MCP tests use the actual Python SDK with fictional ASGI/Streamable HTTP fixtures. Bank calls use mocked adapters. No live bank credentials or real funds were used. Interactive browser checks were performed separately from heavy regression testing; other machine workload, prompt caching and CPU contention are not controlled production benchmarks.

[Complete normal-budget evidence](validation/ai-workflows-normal.json)

## Capability interpretation matrix

Base is the exact manifest request. Variants shows accepted additional paraphrases; a dash means that read/handoff capability has no action-family variants.

| Capability | Base | Additional variants |
| --- | --- | --- |
| get_money_overview | Pass | - |
| get_paycheck_plan | Pass | - |
| get_spending_allowance | Limited | - |
| get_cash_forecast | Pass | - |
| get_upcoming_obligations | Pass | - |
| compare_debt_strategies | Limited | - |
| what_if_extra_payment | Limited | - |
| get_mortgage_scenarios | Pass | - |
| compare_biweekly_mortgage | Pass | - |
| plan_promotional_payoff | Pass | - |
| choose_card | Limited | - |
| compare_card_vs_bank_for_bill | Pass | - |
| compare_booking_channels | Limited | - |
| check_utilization_timing | Pass | - |
| get_buffer | Limited | - |
| assess_sweep_move | Pass | - |
| get_liquidity_tiers | Pass | - |
| compare_payment_timing | Pass | - |
| get_tax_profile | Limited | - |
| compare_savings_vs_debt | Pass | - |
| check_interest_deduction | Pass | - |
| get_deposit_coverage | Pass | - |
| plan_coverage_remedy | Pass | - |
| stress_collateral_line | Pass | - |
| get_automation_status | Pass | - |
| get_account_connections | Limited | - |
| get_recurring_activity | Pass | - |
| explain_transfer_outcome | Limited | - |
| stress_income_loss | Pass | - |
| explain_product_boundary | Pass | - |
| create_account | Pass | 0/2 |
| update_account | Pass | 2/2 |
| create_income | Limited | 0/2 |
| update_income | Pass | 2/2 |
| create_bill | Pass | 1/2 |
| update_bill | Pass | 2/2 |
| create_goal | Pass | 0/2 |
| update_goal | Pass | 1/2 |
| create_rule | Pass | 0/2 |
| update_rule | Pass | 1/2 |
| update_tax | Limited | 0/2 |
| pause_rule | Pass | 2/2 |
| resume_rule | Pass | 1/2 |
| skip_rule | Pass | 2/2 |
| authorize_rule | Limited | 0/2 |
| draft_bill_payment | Limited | 0/2 |
| build_payment_drafts | Pass | 1/2 |
| simulate_payment | Pass | 1/2 |
| recover_payment | Pass | 2/2 |
| pause_execution | Pass | 2/2 |
| resume_execution | Pass | 1/2 |
| correct_transaction | Pass | 1/2 |
| import_transactions | Pass | 2/2 |
| upload_csv | Pass | - |
| list_records | Limited | - |
| search_transactions | Limited | - |
| list_audit | Limited | - |
| list_payment_groups | Pass | - |
| list_documents | Pass | - |
| search_documents | Pass | - |
| read_document | Pass | - |
| list_conversations | Pass | - |
| list_connections | Pass | - |
| browse_mcp | Pass | - |
| delete_document | Limited | 0/2 |
| delete_conversation | Pass | 2/2 |
| disconnect_mcp | Limited | 2/2 |
| revoke_mcp_access | Pass | 2/2 |
| sync_bank | Pass | 2/2 |
| disconnect_bank | Pass | 2/2 |
| import_mcp | Limited | 0/2 |
| connect_bank | Pass | - |
| connect_mcp | Pass | - |
| create_mcp_access | Limited | - |
| upload_document | Pass | - |
| select_documents | Limited | - |
| new_conversation | Limited | - |
| open_conversation | Pass | - |
| navigate | Limited | - |
| explain_capabilities | Limited | - |

## Supplemental normal-budget scenarios

- accounts_and_transactions: limited, 11.05s.
- documents_and_calculator: limited, 9.67s.
- charges_and_bill: limited, 12.11s.
- income_event: limited, 6.47s.

[Compound and paycheck-event evidence](validation/ai-workflows-scenarios.json)

## Separate 60-second diagnostics

These are diagnostic runs, not normal-budget acceptance. 0/4 exact interpretations passed. See [diagnostic evidence](validation/ai-workflows-diagnostic.json) for individual latency, planner, explanation and persistence results.

Compound/paycheck-event diagnostic results (60 seconds each, separate from normal acceptance):

- accounts_and_transactions: limited, 9.42s.
- documents_and_calculator: limited, 10.06s.
- charges_and_bill: limited, 5.97s.
- income_event: limited, 3.73s.

[Supplemental diagnostic evidence](validation/ai-workflows-scenario-diagnostic.json)

## Browser journeys

Verified on the actual local application at desktop 1366 x 900 and compact 390 x 844:

- Create a fictional bill using chat controls, inspect preview, confirm, see it on Payments, reopen the drawer, and reload its saved receipt.
- Prepare an amount change through chat, edit its preview inline, then cancel; no change is saved.
- Upload a fictional CSV, inspect the exact row, confirm its import and restore the receipt after reload.
- Resume the existing benefits conversation, restore selected documents and inspect the cited original passage.
- No horizontal page overflow on compact layout and no captured console errors/warnings.

The file chooser stalled during the CSV journey; the eventual upload, preview, confirmation and restoration succeeded. This browser-tool delay is separate from the measured API model latencies. Destructive source tests and credential/access tests use isolated automated fixtures.

## Limits and release status

- Some local-model interpretations/timeouts remain limited. Structured workflow controls and calculator fallbacks preserve application access; they are not evidence of successful model understanding.
- A diagnostic success cannot establish the default 12-second target. No hosted latency SLA or full production readiness is claimed.
- Real payments and transfers are excluded. Sample execution is confined to fictional sample workspaces.
- Live provider credentials, institutional behavior, external MCP OAuth and production PostgreSQL deployment/load remain unverified.
- Retrieval is private lexical search over text-bearing PDF/TXT/Markdown, without OCR or semantic embeddings.
- The existing aggregate snapshot grows with financial history; the application remains a modular monolith, not an independently scaled ledger platform.
- Provider outcomes may remain unknown after external failures. No automatic retry or claim of a completed external operation is made.

## Reproduce

    .venv/Scripts/python.exe -m pytest -q
    Get-ChildItem tests/*frontend.mjs | ForEach-Object { node $_.FullName }
    .venv/Scripts/python.exe -m scripts.evaluate_workflows --paraphrases --require-coverage --output .local/llama-workflows-constrained-normal.json
    .venv/Scripts/python.exe -m scripts.evaluate_workflow_scenarios
    .venv/Scripts/python.exe -m scripts.report_workflow_validation

The --require-coverage flag exits nonzero for any failed interpretation or missing confirmed receipt; generating a report does not override that gate. The evaluator uses disposable fictional workspaces and disables only fixture request throttling. Production throttles and confirmation checks remain enabled.

Structured output uses the OpenAI-compatible response_format supported by [Ollama's official documentation](https://docs.ollama.com/capabilities/structured-outputs). Schemas constrain syntax; the evaluator separately checks interpretation.
