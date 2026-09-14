# Frontend

The web application is plain JavaScript modules and CSS in `finpilot/web/`, with no build step or framework. The server serves `index.html` at `/` and rewrites `/assets/` URLs to a prefix derived from a hash of every asset, so a browser never mixes old and new modules. The hash is computed when the server starts: restart it after editing anything in `finpilot/web/`.

## Modules

| Module | Role | Imports |
| --- | --- | --- |
| `app.js` | Entry point (`load`). Wires every section, drawer and form together. | Every other module |
| `auth.js` | Account creation, sign-in and sign-out screens (`initializeAuth`, `signOut`). | none |
| `core.js` | Shared state `S`, the `sections` list, formatting (`money`, `pct`, `dateLabel`), HTML helpers such as `esc`, `panel` and `table`, and `api`, which adds the `X-CSRF-Token` header to every write. | `icons.js` |
| `pages.js` | The main sections: `overview`, `accountsPage`, `paycheckPage`, `billsPage`, `cashflowPage`, `goalsPage`, `debtPage`, `rulesPage`, `protectionPage` and `assistantWorkspacePage`. | `core.js`, `execution.js`, `manage.js` |
| `accounts.js` | The account detail page (`accountPage`). | `core.js`, `manage.js`, `pages.js` |
| `details.js` | Detail panel content, including workspace settings, payment drafts and calculator results. | `core.js`, `execution.js`, `manage.js` |
| `manage.js` | Create and edit forms, including the transaction CSV import. | none |
| `execution.js` | Payment drafts and the sample-workspace payment simulation. | none |
| `connect.js` | Bank linking through Plaid Link. | `core.js` |
| `assistant.js` | The assistant drawer and AI workspace: asking, conversation history, resuming and deleting conversations. | `core.js`, `knowledge.js`, `workflows.js` |
| `workflows.js` | Review cards for assistant proposals, receipts and the CSV upload form in chat. | none |
| `knowledge.js` | The document library, MCP connections and MCP access tokens. | `core.js` |
| `icons.js` | Inline SVG icons (`icon`). | none |

Styles live in `base.css`, `theme.css`, `workspace.css` and `assistant.css`.

## Sections

`core.js` defines the navigation order: Overview, Accounts, Paycheck plan, Bills & payments, Cash flow, Savings goals, Debt & credit, Recurring rules, Tax & protection and AI workspace.

## Data and state

- `S` holds the session, the workspace revision, the loaded data and workspace records, and the current page, account and tab.
- Data comes from the HTTP API described in [api-reference.md](api-reference.md). The session cookie authenticates reads, and `api` adds the CSRF token to writes.

## Conventions

- Pages are built from HTML template strings. Pass any user-supplied or server-supplied text through `esc` before it enters markup.
- Show money with the formatting helpers in `core.js`, which keep the exact cents that the server returned.
- Keep interactive elements keyboard accessible and labelled; existing pages use ARIA roles such as `tab`, `status` and `alert`.

## Tests

The Node suites in `tests/*_frontend.mjs` import these modules directly and check rendering, escaping and workflows. Run each with `node tests/<name>_frontend.mjs`. Only `tests/frontend.mjs` calls a running server, on port 8100 or at `FINPILOT_TEST_URL`.
