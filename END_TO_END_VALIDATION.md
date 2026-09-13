# End-to-end validation — September 13, 2026

Current verification of the local application, including the changes below. Llama fallback and exact interpretation are counted separately. These results do not establish hosted production performance.

## Changes verified

- Smaller request-specific planner schemas keep relevant capabilities, explicit decimal values, percentage units and record references. Required compound task slots prevent silently dropping a requested task. Unrequested fields are omitted; words inside record names cannot silently change recurrence. Duplicate names require explicit choices; guessed IDs cannot bypass clarification.
- Deterministic compound reads and explicit renames use no model calls. Workflow results use authoritative server rendering without a generic second generation call. Legacy calculator/document explanation checks remain active.
- Planner context reads only relevant entity kinds. MCP tool/source allowlists are checked before preview and before claiming a provider action; known precondition failures remain unexecuted.
- Payment lists, calendar rows and account details preserve bill cents. Completed receipts no longer retain a contradictory Drafted status badge.

## Automated application checks

Python: **738 passed, 1 warning in 1110.08s (0:18:30)**. Seven frontend suites passed. [Python log](validation/python-e2e.txt), [frontend log](validation/frontend-e2e.txt).

The 80-capability manifest exercises authenticated chat, preview, explicit confirmation, persisted changes, refreshed bootstrap and restored receipts. Additional tests cover cancellation, stale/expired proposals, duplicate and concurrent confirmation, role/tenant boundaries, provider failures, CSV imports, document/MCP injection, conversation restoration, and account context changes. Model doubles in those tests prove application behavior, not Llama understanding.

Local PostgreSQL verification passed migration upgrade/repeat/downgrade/re-upgrade with metadata agreement, compound chat, two-worker exactly-once confirmation, conflicting-preview rejection, cross-tenant denial, cancellation, and an actual MCP SDK fixture import. The test used a new disposable database, then removed it and stopped its isolated server. [PostgreSQL evidence](validation/postgres-e2e.json).

## Live local Llama

Model: **llama3.2:latest**, Ollama, CPU, Q4_K_M. Each case uses the normal 12-second inference budget, sequentially; no cloud substitute. The matrix forces planning even for phrases normally handled without inference. It includes every capability plus two extra paraphrases for each of 30 action families.

| Measurement | Historical baseline | Current complete run |
| --- | ---: | ---: |
| Exact interpretations | 90/140 | 115/140 |
| Capabilities with an accepted interpretation | 57/80 | 77/80 |
| Model calls | 171 | 140 |
| Reported completed-call tokens | 63924 | 33201 |
| Confirmed fictional actions | 57 | 67 |
| Restored receipts | 57 | 67 |
| Median API time | 5.32s | 7.38s |
| p95 API time | 11.92s | 12.19s |
| Maximum API time | 12.08s | 12.56s |

Call reduction measures avoided generation work. Token totals are provider-reported for completed calls; timeouts can consume unreported work. Local compute/electricity cost was not measured, and no cloud billing savings are claimed. Other active model processes shared the CPU, so these are observed local timings, not a controlled hardware benchmark or SLA. The full Llama run used fixed planner prompts and schemas; a final duplicate-name execution guard was verified separately in the final application suite and does not change those interpretation inputs.

[Complete current matrix](validation/ai-workflows-e2e-final.json) records exact expected/actual arguments, planner status, fallback, model calls, preview, confirmation and persistence evidence. [Comparison data](validation/inference-comparison.json).

### Remaining interpretation limits

**25 of 140 cases remain limited.** A fallback is not a successful interpretation. Wrong interpretations are never confirmed by the evaluator. Exact previews and user confirmation remain required in the application.

| Capability / variant | Observed outcome |
| --- | --- |
| get_mortgage_scenarios / 0 | 12-second model timeout (12.11s) |
| compare_card_vs_bank_for_bill / 0 | 12-second model timeout (12.45s) |
| create_bill / 0 | 12-second model timeout (12.09s) |
| update_goal / 0 | 12-second model timeout (12.12s) |
| create_rule / 0 | 12-second model timeout (12.12s) |
| resume_rule / 0 | 12-second model timeout (12.19s) |
| skip_rule / 0 | 12-second model timeout (12.31s) |
| authorize_rule / 0 | 12-second model timeout (12.11s) |
| draft_bill_payment / 0 | 12-second model timeout (12.56s) |
| build_payment_drafts / 0 | 12-second model timeout (12.14s) |
| simulate_payment / 0 | 12-second model timeout (12.23s) |
| create_account / 1 | 12-second model timeout (12.30s) |
| create_account / 2 | 12-second model timeout (12.19s) |
| update_account / 1 | 12-second model timeout (12.17s) |
| update_account / 2 | 12-second model timeout (12.08s) |
| create_income / 1 | 12-second model timeout (12.09s) |
| create_income / 2 | 12-second model timeout (12.11s) |
| update_income / 1 | 12-second model timeout (12.23s) |
| update_bill / 1 | 12-second model timeout (12.11s) |
| update_bill / 2 | 12-second model timeout (12.08s) |
| create_goal / 1 | 12-second model timeout (12.17s) |
| create_goal / 2 | 12-second model timeout (12.12s) |
| update_goal / 1 | 12-second model timeout (12.19s) |
| update_goal / 2 | 12-second model timeout (12.12s) |
| create_rule / 1 | 12-second model timeout (12.14s) |

No accepted interpretation in this complete run for: get_mortgage_scenarios, compare_card_vs_bank_for_bill, update_goal. Their typed chat controls and application execution paths are covered by the passing application suite.

### Compound and memory journeys

- accounts_and_transactions: accepted, 11.16s.
- documents_and_calculator: accepted, 11.61s.
- charges_and_bill: limited, 12.11s.
- income_event: accepted, 9.17s.
- Conversation follow-up with checking → protected-savings context: authoritative evidence/guard checks passed; model explanation fell back, 12.06s.

[Compound results](validation/ai-workflow-scenarios-e2e.json), [memory result](validation/ai-memory-scope-e2e.json). No 60-second diagnostic is counted as normal-budget acceptance.

## Application latency without inference

Twelve repetitions on disposable SQLite workspaces through authenticated in-process ASGI. These timings include application requests and exclude browser/network/TLS. They do not measure Llama understanding.

| Workflow step | Median | p95 |
| --- | ---: | ---: |
| compound records and history | 158.69 ms | 371.21 ms |
| transaction search | 127.66 ms | 170.04 ms |
| bill records | 131.50 ms | 187.73 ms |
| review preview | 241.26 ms | 578.69 ms |
| confirmation | 105.15 ms | 168.50 ms |
| refreshed bootstrap | 180.38 ms | 460.68 ms |
| receipt restoration | 248.62 ms | 534.76 ms |
| whole reviewed workflow | 937.19 ms | 1627.00 ms |

The reviewed-action benchmark verifies the exact database value, refreshed revision, saved receipt and zero model calls on every repetition. [Workflow timings](validation/workflow-benchmark-e2e.json).

The separate 5,000-row benchmark checks cold/warm reads, indexed history, create/update and that financial reads/writes finish while inference is deliberately held. Cold clears application caches; database/OS caches remain warm. [Complete API benchmark](validation/api-benchmark-e2e.json).

## Browser verification

Verified the ten financial sections, a tax comparison, compound account/history chat, an exact bill change, confirmation, refreshed Payments, persisted receipt after reload, shared drawer/workspace history, and restoration of the original document conversation. Desktop and 390 × 844 compact layouts were checked; compact confirmation completed without horizontal overflow. Captured console errors/warnings were empty. The temporary bill change was restored to its original amount. [Browser check record](validation/browser-e2e.json).

The cents-rounding defect was observed in the real Payments list, fixed, and rechecked. Destructive, credential, provider-failure and injection cases run in isolated automated fixtures. The prior CSV browser upload journey is documented in the historical AI audit; it was not repeated during this pass.

## Practical limits

- Hosted multi-user load, TLS/network latency, live bank/provider behavior and external MCP OAuth remain unverified. The PostgreSQL checks are local, not a hosted deployment test.
- Real payments/transfers remain excluded; simulator tests use fictional funds. Uncertain external outcomes require inspection and are not automatically retried.
- Document retrieval is lexical search over text-bearing PDF/TXT/Markdown, without OCR or embeddings. Large aggregate snapshots still incur decode/write costs as history grows.
- Earlier iterations, including a stopped load-contended run that exposed an APY-unit defect, are retained in .local. They are not merged into the current matrix to inflate coverage.

## Reproduce

```powershell
.venv/Scripts/python.exe -m pytest -q
Get-ChildItem tests/*frontend.mjs | ForEach-Object { node $_.FullName }
.venv/Scripts/python.exe -m scripts.evaluate_workflows --paraphrases --require-coverage --output .local/llama-workflows-e2e-final.json
.venv/Scripts/python.exe -m scripts.evaluate_workflow_scenarios --output .local/llama-workflow-scenarios-e2e.json
.venv/Scripts/python.exe scripts/evaluate_memory_scope.py --output .local/ai-memory-scope-e2e.json
.venv/Scripts/python.exe -m scripts.benchmark --rows 5000 --runs 12 --output .local/api-benchmark-e2e.json
.venv/Scripts/python.exe -m scripts.benchmark_workflows --output .local/workflow-benchmark-e2e.json
.venv/Scripts/python.exe -m scripts.verify_postgres --maintenance-url postgresql+psycopg://finpilotqa@127.0.0.1:55439/postgres
.venv/Scripts/python.exe -m scripts.report_e2e_validation
```

PostgreSQL verification requires a separate local test instance and creates/drops only its own randomly named database. The Llama coverage gate exits nonzero for any failed exact interpretation or missing receipt. Structured output uses Ollama's documented [response_format support](https://docs.ollama.com/capabilities/structured-outputs); schema validity alone does not prove semantic correctness.
