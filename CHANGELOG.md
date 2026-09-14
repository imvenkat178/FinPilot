# Changelog

FinPilot has no tagged releases yet, so changes are grouped by date. Commit hashes point to the details in the git history.

## 2026-09-14

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
