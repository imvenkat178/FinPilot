# Changelog

FinPilot has no tagged releases yet, so changes are grouped by date. Commit hashes point to the details in the git history.

## 2026-09-20

- Continuous integration: `.github/workflows/tests.yml` runs the Python suite, the generated-documentation check and the six frontend suites that need no server, and `.github/workflows/agent-memory.yml` runs the roadmap and handoff validators, the branch check and the memory tests, on every push and pull request.

- Client addresses behind a reverse proxy: `FORWARDED_ALLOW_IPS` is documented and passed through in `compose.yaml`, the `Dockerfile` records its default, and FinPilot warns at startup in production while only loopback is trusted, so sign-up and sign-in limits stop counting every user under the proxy's address. Reproduced and re-checked with an nginx container by `scripts/verify_proxy_client_addresses.py` (`validation/proxy-client-addresses.json`).

## 2026-09-19

- A failed Plaid call now logs a warning with the environment, endpoint, HTTP status, Plaid error type, error code and request ID, without messages, keys or tokens, so a rejected secret shows up as `INVALID_API_KEYS` instead of only a 502.
- Importing `scripts/verify_plaid_sandbox.py` no longer builds FinPilot's app and database, so a launcher that reuses its key loader can still choose `FINPILOT_DATABASE_URL`.

## 2026-09-15

- Model wording never supplies a number. The reference answer reaches the model with every figure replaced by a token such as `[F1]`; the server inserts the exact figures and falls back to calculator wording when a draft types or invents a figure, adds investment, product, eligibility or legal advice, or claims an action happened.
- Unrelated requests, such as a poem or the weather, get a fixed reply without planning or a model call, and requests the router does not recognize get fixed guidance instead of model wording.
- Answers link the records behind them (accounts, bills, cards, debts, goals, rules, income sources and transactions), show which result field each figure came from and carry a high, medium or low evidence confidence with its reasons. These fields are checked against a JSON Schema before they are saved or shown.
- Model wording that passed every check, and validated workflow plans, are reused from an in-process cache keyed by household, model and exact prompt. Cached wording holds tokens, so a repeat shows current figures and is checked again.
- `scripts/measure_figure_tokens.py` measures token wording and cache reuse on a local model. At the 12-second budget `llama3.2:latest` kept model wording on 5 of 10 demo questions, and every accepted answer came from the cache when asked again; details for `llama3.2:latest` and `llama3.1:8b-instruct-q4_K_M` are in `AI_VALIDATION.md`.

## 2026-09-14

- Plaid Sandbox verification passed: FinPilot linked, synced and disconnected a Sandbox item, imported card and loan terms where Plaid reported them and mapped every transaction to a FinPilot category (`validation/plaid-sandbox-e2e.json`).
- Automated aggregation through Plaid: credit card, auto loan, personal loan, mortgage and student loan accounts link alongside cash accounts. Plaid Liabilities supplies card and loan terms without guessing missing ones, debts without reported terms stay in totals but out of payoff models, bank categories map onto one FinPilot vocabulary, CSV import is refused for bank-synced accounts, and `scripts/verify_plaid_sandbox.py` checks the whole flow against Plaid Sandbox with keys from a git-ignored local file.
- Request logs are private under any Uvicorn setup. Each request writes one `finpilot.requests` line without query strings, the container image turns off the Uvicorn access log, and FinPilot strips query strings when that log is on. Calculator amounts and transaction and document search text moved from URLs into JSON request bodies: `POST` replaces `GET` for `/api/debt/compare`, `/api/debt/what-if`, `/api/mortgage/scenarios`, `/api/mortgage/biweekly`, `/api/cards/utilization` and `/api/tax/net-benefit`, and `POST /api/transactions/search` and `POST /api/documents/search` replace `GET /api/transactions` and `GET /api/documents/search`.
- `scripts/roadmap_artifact.py` builds the owner's private roadmap page from `ROADMAP.md` plus the current focus, last step and recent sessions in `HANDOFF.md`, and a local Claude Code Stop hook asks for a republish whenever those files change.
- Reference documentation: a user guide, calculation methods, an API reference with its OpenAPI schema, an AI capability reference, a data model reference, a configuration reference and frontend notes, all listed in the README. Generated references are checked by `tests/test_docs.py`.
- Every HTTP endpoint has a summary, a description and its error responses in the API schema served at `/docs`.
- `SECURITY.md`, `CONTRIBUTING.md` and this changelog.
- The README verification commands list all seven frontend test suites.
- Generated pages for the roadmap and the agent memory kit guide, kept current by staleness checks, and a pre-push hook that runs the memory checks (`5a04ad8`).

## 2026-09-13

- Reviewed AI workflows: 80 typed capabilities with previews, explicit confirmation and receipts; saved conversations; a private document library with citations; an MCP client and a read-only MCP server; validation evidence (`76f95b1`).
- Shared agent memory: the agent protocol, the handoff file and its validator (`76f95b1`, `31424ba`), then the roadmap, the portable agent memory kit and word-for-word protocol alignment (`7389d2f`).
- A branch check for handoff updates and a CI workflow template in the kit (`2d92256`).

## 2026-09-11

- Hosted multi-tenant release: authentication, PostgreSQL persistence, bank linking and Docker deployment (`3a2bdc9`).
- Account connections, recurring activity and one-time payments (`e3b7ad9`).
- Fixes for seven defects found in an external review (`8c71832`).

## 2026-09-10

- First implementation of FinPilot, a personal finance application with an AI assistant (`80015ba`).
- AI wiring hardened for a real local Llama model, with a fast diagnostic (`80adc7c`).
