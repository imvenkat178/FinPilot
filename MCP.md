# FinPilot MCP connections

FinPilot supports two explicit data flows. The assistant itself cannot use either flow to move money or change accounts.

## Connect an MCP client to FinPilot

Create a **read token** in the assistant's MCP settings. The secret is shown once; the database stores only its SHA-256 hash. Each token belongs to its creator and workspace, expires within 30 days, and can be revoked immediately. Membership and expiry are rechecked on every read.

Configure a desktop MCP client with the official Python SDK stdio bridge:

```json
{
  "mcpServers": {
    "finpilot": {
      "command": "/absolute/path/to/finpilot/.venv/bin/python",
      "args": ["-m", "finpilot.integrations.mcp_server"],
      "env": {
        "PYTHONPATH": "/absolute/path/to/finpilot",
        "FINPILOT_API_URL": "https://your-finpilot.example",
        "FINPILOT_MCP_TOKEN": "COPY_THE_ONCE_SHOWN_READ_TOKEN"
      }
    }
  }
}
```

On Windows, use the absolute `.venv\Scripts\python.exe` path (escape backslashes in JSON). Install the project's Python dependencies on the machine running this bridge. HTTPS is required for a hosted API; HTTP is permitted only for loopback development. Set `PYTHONPATH` to your local FinPilot checkout so the bridge also launches when your MCP client uses a different working directory. The assistant now builds this configuration for you: enter your local Python executable and project directory, then copy the JSON. These are paths on the computer running the MCP client, not paths on the hosted server. The once-shown token stays on this screen until you leave it; it is not put in browser storage.

The bridge implements MCP initialize, tools/list and tools/call over stdio. Its financial tools use the same deterministic calculators and verified workspace scope as the application. Only registry tools explicitly marked read-only are advertised and accepted. Calculator errors are returned as MCP errors. The token grants financial read access to the whole workspace, so keep the client configuration private. It grants no document-library access.

This is an MCP **stdio integration with an application read grant**, not an OAuth authorization server or a publicly exposed Streamable HTTP endpoint. No client-supplied tenant ID can override the token scope. The latest saved workspace data is read on each call; imported bank data remains a cached snapshot.

## Import data from another MCP server

The Connections screen reports missing operator setup before asking for provider credentials. An authenticated `GET /api/mcp/setup` returns separate read-only export and external-import readiness; it never returns a secret or probes external servers.

An operator first sets two environment variables:

- `FINPILOT_TOKEN_KEY`: a Fernet key used to encrypt provider credentials (the same managed key can serve bank and MCP connections). Preserve the key across deployments; replacing it requires users to reconnect.
- `FINPILOT_MCP_SERVERS`: a JSON array containing exact approved HTTPS MCP endpoints and their approved **read-only** tools.

Example configuration (the endpoint is illustrative):

```json
[
  {
    "id": "statements",
    "name": "Statement provider",
    "url": "https://provider.example/mcp",
    "read_tools": ["list_statements", "get_statement"],
    "allow_resources": true
  }
]
```

The operator must confirm that each allowed tool is actually read-only. FinPilot does not infer authority from a remote server's tool annotations. Empty allowlists make every tool unavailable. `allow_resources` separately permits MCP resource reads.

A signed-in user can choose an approved server, enter that provider's bearer token, discover its approved tools/resources, and explicitly import a result into their private document library. Servers that require interactive OAuth discovery/consent are not connected automatically; this version supports operator-approved endpoints with bearer credentials or no authentication. Credentials are per user and encrypted at rest together with their approved endpoint. Changing an endpoint under the same server ID requires reconnecting; the saved credential is never silently forwarded to the new destination. Removing the connection erases its saved credential; already imported documents remain until the user deletes them.

Imports use the official SDK's Streamable HTTP transport. Endpoint DNS is resolved, restricted to public addresses, and pinned for the connection while preserving the original TLS hostname. Redirects, proxy environment variables and compressed responses are refused. Each request has a 15-second deadline, a one-megabyte response limit, and a 48,000-character imported-text limit. DNS resolution respects the request deadline and is limited to eight active lookups even when an operating-system lookup outlives cancellation. SDK diagnostics are sanitized at record creation, including validation exceptions and root-logger calls, so raw provider payloads cannot reach application logging handlers. Catalogs are bounded to five pages and 100 visible tools/resources. Sampling, model roots and elicitation callbacks are not provided. MCP resource URIs are opaque server identifiers and are never independently fetched as URLs by FinPilot.

Imported content includes its provider, tool/resource identity and import timestamp. It remains untrusted source text: retrieval can cite what a document says, but cannot treat the text as permission to execute an action or as an update to an account balance. A fresh import is needed when remote data changes. Credentials and argument payloads are not copied into document provenance.

## Verification

Run:

```sh
python -m pytest tests/test_mcp_integration.py tests/test_mcp_setup.py tests/test_mcp_bridge_launch.py tests/test_migrations.py -q
```

The suite verifies tenant privacy, hashed and revocable grants, encrypted credentials, removal of membership, rejected mutations, document imports and network bounds. It also runs the official MCP SDK through both an in-memory protocol session and an actual stdio subprocess connected to an ephemeral authenticated HTTP application, plus Streamable HTTP discovery, tool calls and resource reads against an SDK fixture server. The portable-launch check runs from an unrelated directory containing spaces and verifies that the read token, rather than another browser user's session, determines the returned workspace. Setup tests check missing/invalid keys and allowlists, API-origin selection and secret-free status responses. External provider credentials are not required for these tests.
