"""Summaries, descriptions and error responses for every HTTP route.

create_app applies these to the OpenAPI schema, so /docs, /redoc and docs/api-reference.md explain each
endpoint. tests/test_docs.py fails when a route has no entry here or an entry has no route.
"""
from fastapi.routing import APIRoute

ERRORS = {
    400: "The If-Match header does not contain a workspace revision.",
    401: "Not signed in, the session expired, or the MCP read token is missing, expired or revoked.",
    403: "Cross-site request, failed CSRF check, or the role or workspace type does not allow this action.",
    404: "The record does not exist in this workspace.",
    409: "The workspace revision changed, or the record's current state does not allow this action.",
    413: "The upload is too large.",
    422: "The request failed validation.",
    429: "A rate limit or capacity limit was reached; retry shortly.",
    502: "The remote MCP server returned an error.",
    503: "A dependency is unavailable, such as the database, a provider or its configuration.",
}

READ = (401,)
WRITE = (400, 401, 403, 409, 422)

ROUTES = {
    # Service
    ("GET", "/"): ("Open the web application",
        "Serves the application shell with versioned asset URLs. No authentication.", ()),
    ("GET", "/api/health"): ("Check service health",
        "Runs a database readiness query. It needs no authentication and does not wait for model discovery.", (503,)),

    # Identity
    ("POST", "/api/auth/register"): ("Create an account",
        "Creates a user, a private workspace and a session cookie. With `sample_data`, the workspace holds fictional "
        "data and payment simulation is enabled. Rate limited per client address.", (403, 409, 422, 429)),
    ("POST", "/api/auth/login"): ("Sign in",
        "Checks the email and password and sets the session cookie. Rate limited per client address and per email.",
        (401, 403, 422, 429)),
    ("GET", "/api/auth/me"): ("Get the signed-in identity",
        "Returns the user, workspace ID, role and the CSRF token to send as `X-CSRF-Token` on every write.", READ),
    ("POST", "/api/auth/logout"): ("Sign out", "Revokes the current session and clears the cookie.", (401, 403)),

    # Workspace data
    ("GET", "/api/bootstrap"): ("Load the application",
        "Returns what the web app needs on first load, including workspace data, calculations, the revision and "
        "capability flags such as bank linking and payment simulation.", READ),
    ("GET", "/api/dashboard"): ("Get dashboard calculations",
        "Returns the dashboard calculations, cached by workspace revision.", READ),
    ("GET", "/api/workspace"): ("Get workspace records", "Returns the workspace records used by management forms.", READ),
    ("POST", "/api/manage/{kind}"): ("Create a record",
        "Creates a workspace record of the given kind. Send `If-Match` with the workspace revision to detect "
        "concurrent changes.", WRITE),
    ("PATCH", "/api/manage/{kind}/{record_id}"): ("Update a record",
        "Updates a workspace record of the given kind. Send `If-Match` with the workspace revision.", WRITE + (404,)),
    ("POST", "/api/transactions/preview"): ("Preview a CSV import",
        "Parses CSV text with an optional column mapping and returns rows, duplicates and errors without saving.",
        (401, 403, 422)),
    ("POST", "/api/transactions/import"): ("Import CSV transactions",
        "Imports previewed CSV rows in one transaction and skips duplicates. Reported account balances are unchanged.",
        WRITE),
    ("PATCH", "/api/transactions/{transaction_id}"): ("Correct a transaction",
        "Corrects a transaction's description, category or kind.", WRITE + (404,)),
    ("GET", "/api/transactions"): ("List transactions",
        "Lists transactions newest first, filtered by account, text or category, with `limit` and `offset` paging.",
        (401, 422)),
    ("GET", "/api/audit"): ("List recent changes",
        "Lists recent workspace change events with the action, revision, actor and time.", READ),

    # Financial workspace
    ("GET", "/api/overview"): ("Get the money overview", "Returns the household money overview.", READ),
    ("GET", "/api/liquidity"): ("Get liquidity tiers",
        "Groups balances into tiers by how quickly the money can be used.", READ),
    ("GET", "/api/coverage"): ("Estimate deposit insurance coverage",
        "Estimates deposit insurance coverage across accounts and ownership categories.", READ),
    ("GET", "/api/bills"): ("List upcoming bills", "Lists bill occurrences due within the next `days` days.", (401, 422)),
    ("GET", "/api/forecast"): ("Forecast cash",
        "Projects dated balances for one account, or all planning accounts, over the next `days` days.", (401, 422)),
    ("GET", "/api/allowance"): ("Calculate a spending allowance",
        "Calculates how much can be spent over the next `days` days while still covering obligations and protected cash.",
        (401, 422)),
    ("GET", "/api/buffer"): ("Calculate an operating buffer", "Calculates the operating buffer for an account.", READ),
    ("GET", "/api/connections"): ("List account connections", "Lists the connections between accounts in the workspace.",
        READ),
    ("GET", "/api/recurring/activity"): ("List recurring rule activity",
        "Lists recurring rule runs within `horizon_days` and whether each will run, is paused, skipped or lacks "
        "authorization.", (401, 422)),
    ("GET", "/api/plan"): ("Get the paycheck plan",
        "Returns the paycheck allocation plan for a month, the current month by default.", (401, 422)),
    ("GET", "/api/policies"): ("Get rule automation status", "Returns the status of recurring rules and their authorization.",
        READ),
    ("POST", "/api/policies"): ("Create a fixed rule (legacy)",
        "Creates a recurring rule for an older form; new clients use `POST /api/manage/{kind}`. A saved rule has no "
        "payment authority.", WRITE),
    ("POST", "/api/policies/{policy_id}/authorize"): ("Authorize a rule for simulation",
        "Gives a rule simulation-only payment authority with a per-run cap.", WRITE + (404,)),
    ("POST", "/api/policies/{policy_id}/pause"): ("Pause or resume a rule",
        "Pauses a rule, or resumes it when `paused` is false.", WRITE + (404,)),
    ("POST", "/api/policies/{policy_id}/skip-next"): ("Skip a rule's next run", "Skips the next run of a rule.",
        WRITE + (404,)),
    ("POST", "/api/bills/{bill_id}/pay-once"): ("Draft a one-time bill payment",
        "Prepares a payment draft for a bill occurrence. Nothing is submitted.", WRITE + (404,)),
    ("GET", "/api/debt/compare"): ("Compare debt payoff strategies",
        "Compares debt payoff strategies, optionally with an extra monthly payment.", (401, 422)),
    ("GET", "/api/debt/what-if"): ("Model an extra debt payment", "Shows the effect of an extra payment toward debt.",
        (401, 422)),
    ("GET", "/api/mortgage/scenarios"): ("Compare mortgage scenarios",
        "Compares mortgage payoff with an optional lump sum and an optional extra monthly payment.", (401, 422)),
    ("GET", "/api/mortgage/biweekly"): ("Compare biweekly mortgage payments",
        "Compares a biweekly payment program with monthly payments, net of an optional annual program fee.", (401, 422)),
    ("POST", "/api/cards/choose"): ("Choose a card for a purchase",
        "Recommends a card for a purchase from rewards, category, merchant, channel, foreign fees and processing fees.",
        (401, 403, 422)),
    ("GET", "/api/cards/utilization"): ("Check utilization timing",
        "Checks statement utilization timing for a card, optionally with a planned payment.", (401, 422)),
    ("GET", "/api/tax/net-benefit"): ("Compare savings with debt paydown",
        "Compares the after-tax benefit of saving an amount with paying down debt over `horizon_days`.", (401, 422)),
    ("GET", "/api/tax/profile"): ("Get tax assumptions", "Returns the workspace tax assumptions.", READ),
    ("POST", "/api/execution/build"): ("Build payment drafts",
        "Builds payment drafts from the paycheck allocation for a month or an income event.", WRITE),
    ("POST", "/api/execution/run/{group_id}"): ("Run a simulated payment group",
        "Runs a payment group through the simulation provider. Requires `confirm_simulation` and a sample workspace.",
        WRITE + (404,)),
    ("POST", "/api/execution/recover/{leg_id}"): ("Recover a simulated payment leg",
        "Runs recovery for a simulated payment leg in a sample workspace.", WRITE + (404,)),
    ("POST", "/api/execution/pause-all"): ("Pause all payment execution",
        "Pauses payment execution for the whole workspace.", WRITE),
    ("POST", "/api/execution/resume"): ("Resume payment execution", "Resumes payment execution after a global pause.",
        WRITE),
    ("GET", "/api/execution/groups"): ("List payment groups",
        "Lists payment groups, the global pause state and reserved amounts.", READ),
    ("GET", "/api/execution/groups/{group_id}"): ("Get a payment group",
        "Returns one payment group with the preflight checks for each leg.", (401, 404)),

    # Assistant
    ("POST", "/api/ask"): ("Ask the assistant",
        "Answers a question or request inside a saved conversation. Numbers come from deterministic calculations; "
        "requested changes come back as proposals that need confirmation. Rate limited per user and by assistant capacity.",
        (401, 403, 404, 409, 422, 429)),
    ("GET", "/api/ask/history"): ("List saved answers",
        "Lists the signed-in user's recent saved answers in this workspace.", (401, 422)),
    ("GET", "/api/ask/suggestions"): ("Get example questions", "Returns example questions for the assistant.", READ),
    ("GET", "/api/llm/health"): ("Get model status", "Returns the cached status of the operator-configured model.", READ),

    # Assistant actions
    ("GET", "/api/assistant/capabilities"): ("List assistant capabilities",
        "Lists every capability with its input schema, authorization and availability, plus selectable records and limits.",
        READ),
    ("GET", "/api/assistant/proposals"): ("List proposals", "Lists the action proposals in a conversation.", (401, 404, 422)),
    ("POST", "/api/assistant/proposals"): ("Create a proposal",
        "Creates a reviewed proposal with up to four operations. Nothing changes until it is confirmed.",
        (401, 403, 404, 409, 422)),
    ("GET", "/api/assistant/proposals/{proposal_id}"): ("Get a proposal", "Returns a proposal with its preview and status.",
        (401, 404)),
    ("PATCH", "/api/assistant/proposals/{proposal_id}"): ("Edit a proposal",
        "Changes the operations of an unconfirmed proposal. Requires the current proposal version.",
        (401, 403, 404, 409, 422)),
    ("POST", "/api/assistant/proposals/{proposal_id}/cancel"): ("Cancel a proposal",
        "Cancels a proposal at the given version.", (401, 403, 404, 409, 422)),
    ("POST", "/api/assistant/proposals/{proposal_id}/confirm"): ("Confirm a proposal",
        "Applies a proposal once at the given version. Provider actions, such as bank sync, run outside the financial "
        "transaction.", (401, 403, 404, 409, 422, 502, 503)),
    ("POST", "/api/assistant/csv-preview"): ("Upload a CSV for review",
        "Uploads a UTF-8 CSV for a conversation, previews the rows and creates an import proposal when the file is valid.",
        (401, 403, 404, 413, 422)),

    # Bank connections
    ("GET", "/api/bank/connections"): ("List bank connections",
        "Lists bank connections and whether bank linking is configured and available for this workspace.", (401, 503)),
    ("POST", "/api/bank/link-token"): ("Start bank linking",
        "Creates a Plaid Link token. Owners and approvers only; sample workspaces cannot link banks.",
        (400, 401, 403, 409, 429, 503)),
    ("POST", "/api/bank/exchange"): ("Finish bank linking",
        "Exchanges a Plaid public token and imports supported accounts and transactions once.",
        (400, 401, 403, 409, 422, 429, 503)),
    ("POST", "/api/bank/sync/{connection_id}"): ("Sync a bank connection",
        "Imports new transactions and balance snapshots for a bank connection.", (400, 401, 403, 404, 409, 429, 503)),
    ("DELETE", "/api/bank/connections/{connection_id}"): ("Disconnect a bank",
        "Requests provider revocation and removes the stored token. Imported records are kept.",
        (400, 401, 403, 404, 409, 429, 503)),

    # Conversations
    ("GET", "/api/conversations"): ("List conversations", "Lists the signed-in user's saved conversations.", READ),
    ("POST", "/api/conversations"): ("Create a conversation", "Creates an empty conversation.", (401, 403, 422)),
    ("GET", "/api/conversations/{conversation_id}"): ("Get a conversation",
        "Returns a conversation's messages, sources and proposals.", (401, 404)),
    ("DELETE", "/api/conversations/{conversation_id}"): ("Delete a conversation",
        "Deletes a conversation and its saved messages.", (401, 403, 404)),

    # Documents
    ("GET", "/api/documents"): ("List documents", "Lists the documents in the private library.", READ),
    ("POST", "/api/documents"): ("Upload a document",
        "Adds a PDF, TXT or Markdown file of up to 2 MB to the private library. PDFs need selectable text.",
        (401, 403, 409, 413, 422, 429)),
    ("GET", "/api/documents/search"): ("Search documents",
        "Finds matching passages in private documents, optionally limited to selected documents.", (401, 422)),
    ("GET", "/api/documents/{document_id}"): ("Get a document", "Returns a document with its extracted excerpts.",
        (401, 404)),
    ("DELETE", "/api/documents/{document_id}"): ("Delete a document",
        "Removes a document from the library and from future retrieval.", (401, 403, 404)),

    # MCP
    ("GET", "/api/mcp/setup"): ("Get MCP setup",
        "Returns the export bridge configuration and the operator-approved servers available for imports.", (401, 503)),
    ("GET", "/api/mcp/tokens"): ("List MCP read tokens", "Lists the user's MCP read tokens without their secrets.", READ),
    ("POST", "/api/mcp/tokens"): ("Create an MCP read token",
        "Creates an expiring, read-only token for an MCP client. The secret is shown once, and only one active token "
        "is allowed.", (401, 403, 409, 422, 429)),
    ("DELETE", "/api/mcp/tokens/{token_id}"): ("Revoke an MCP read token", "Revokes an MCP read token.", (401, 403, 404)),
    ("GET", "/api/mcp/connections"): ("List MCP connections",
        "Lists the user's connections to operator-approved MCP servers.", (401, 503)),
    ("POST", "/api/mcp/connections"): ("Connect an MCP server",
        "Connects to an operator-approved MCP server, storing any bearer token encrypted. One connection at a time.",
        (401, 403, 409, 422, 429, 503)),
    ("DELETE", "/api/mcp/connections/{connection_id}"): ("Remove an MCP connection",
        "Removes an MCP connection and its stored credential.", (401, 403, 404)),
    ("GET", "/api/mcp/connections/{connection_id}/catalog"): ("Browse an MCP server",
        "Lists the approved read tools and resources available through a connection.", (401, 404, 409, 429, 502, 503)),
    ("POST", "/api/mcp/connections/{connection_id}/import"): ("Import from an MCP server",
        "Calls an approved read tool or reads an approved resource and saves the result as a private document.",
        (401, 403, 404, 409, 422, 429, 502, 503)),
    ("GET", "/api/mcp/export/tools"): ("List tools for MCP clients",
        "Lists the read-only financial tools available to an MCP client. Uses a FinPilot MCP bearer token, not the "
        "session cookie.", (401, 429)),
    ("POST", "/api/mcp/export/call"): ("Call a tool for an MCP client",
        "Runs one read-only financial tool for an MCP client authenticated with a bearer token.", (401, 403, 422, 429)),
}


def iter_api_routes(routes, context=None):
    """Yield (APIRoute, full path) pairs, including routes inside included routers.

    FastAPI 0.141 keeps each included router as a wrapper with `original_router` and `include_context`
    instead of copying its routes into `app.routes`.
    """
    for route in routes:
        if isinstance(route, APIRoute):
            yield route, (context.path_for(route) if context is not None else route.path)
        elif hasattr(route, "original_router") and hasattr(route, "include_context"):
            child = route.include_context if context is None else context.combine(route.include_context)
            yield from iter_api_routes(route.original_router.routes, child)


def apply_route_docs(app) -> list[str]:
    """Attach summaries, descriptions and error responses; return the routes that have no entry.

    Call this before the first request or OpenAPI build, because FastAPI caches the effective routes.
    """
    missing = []
    for route, path in iter_api_routes(app.routes):
        for method in sorted(route.methods):
            entry = ROUTES.get((method, path))
            if entry is None:
                missing.append(f"{method} {path}")
                continue
            summary, description, codes = entry
            route.summary, route.description = summary, description
            route.responses = {**route.responses, **{code: {"description": ERRORS[code]} for code in codes}}
    return missing
