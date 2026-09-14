# FinPilot deployment

Every environment variable, with its default and effect, is listed in [docs/configuration.md](docs/configuration.md).

The repository includes a nonroot container, a PostgreSQL Compose service, and reviewed Alembic migrations. No deployment is performed by these files. The supported hosted configuration is PostgreSQL behind HTTPS; native development can use SQLite.

## Native development on Windows

Use Python 3.12 or newer. From the repository root:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
$env:FINPILOT_ENV = 'development'
$env:FINPILOT_DATABASE_URL = 'sqlite:///./.local/finpilot.db'
$env:FINPILOT_PUBLIC_ORIGIN = 'http://127.0.0.1:8100'
.venv/Scripts/python.exe -m uvicorn finpilot.api.app:app --host 127.0.0.1 --port 8100
```

Open `http://127.0.0.1:8100` and create an account. Local startup initializes the development schema. The optional sample-data choice creates a household belonging to that account; it is not a shared anonymous workspace.

The application reads process environment variables. It does not automatically load `.env` during a native Python launch. Use `--env-file .env` with Uvicorn only when the file contains settings intended for that native process. The provided `.env.example` targets Docker Compose.

## Local PostgreSQL with Docker Compose

1. Install Docker with Compose support.
2. Copy `.env.example` to `.env`.
3. Generate a PostgreSQL password. Set `POSTGRES_PASSWORD` and put its URL-encoded equivalent into `FINPILOT_DATABASE_URL`. The database host in the Compose network is `db`.
4. Leave `FINPILOT_ENV=development` and `FINPILOT_PUBLIC_ORIGIN=http://localhost:8100` for this local run.
5. Start the stack:

```powershell
docker compose up --build -d
docker compose ps
docker compose logs --tail 100 app
```

The blank password in the sample intentionally prevents startup until configured. The `migrate` service waits for PostgreSQL, runs `alembic upgrade head`, and exits. The app starts only after that job succeeds. PostgreSQL has a named data volume and no published host port. The app binds to the host's loopback interface on port 8100 by default.

Open `http://localhost:8100`. Use that exact origin: the session and origin checks intentionally reject mismatched origins. Stopping the stack preserves the named volume. Do not remove that volume when preserving financial records matters.

The image runs as UID/GID 10001. Source files, migrations, and static assets are copied into the image; local databases, virtual environments, prototype files, and `.env` secrets are excluded from its build context. The application health check supplies the configured public Host header so hosted trusted-host checks remain enabled.

## Hosted environment

Set these values in the deployment platform's secret/environment store:

| Variable | Hosted value |
| --- | --- |
| `FINPILOT_ENV` | `production` |
| `FINPILOT_PUBLIC_ORIGIN` | The exact public HTTPS origin, such as `https://finance.example.com`. |
| `FINPILOT_DATABASE_URL` | A private PostgreSQL connection URL using `postgresql+psycopg://...`; include provider-required TLS settings. |
| `FINPILOT_LLM_BASE_URL` | Optional private OpenAI-compatible model endpoint ending in `/v1`. |
| `FINPILOT_LLM_MODEL` | Optional explicit model ID; otherwise discover one from the configured endpoint. |
| `FINPILOT_LLM_API_KEY` | The model endpoint credential, if required. |

The application refuses hosted startup with SQLite or without an HTTPS public origin. Use a reverse proxy or platform ingress to terminate TLS and preserve the original public Host header. The application container listens on port 8000. Keep the database and model endpoint on private networks. When configuring forwarded headers, trust only the actual proxy addresses; do not indiscriminately trust client-supplied forwarding headers.

Before starting application workers, run one migration/release job with the same database settings:

```powershell
python -m alembic upgrade head
python -m alembic current
```

Run migrations once per release, not simultaneously from every worker. Then start the app container or its Uvicorn command. `GET /api/health` performs a database readiness check; it does not wait for model discovery. Normal browser operation uses a cached model status.

Take and verify a database backup before applying new production migrations. Prefer a forward repair migration for deployed data. Migration downgrades remove the corresponding application tables and data; its automated round-trip test runs only against disposable SQLite databases.

If an existing development database was created before Alembic, do not blindly run or stamp the initial revision. Compare its schema to the current metadata and back it up first. The simplest development route is a fresh database. Hosted environments should start with Alembic-managed schemas.

## Optional Plaid bank linking

The bank adapter remains disabled until all four settings are supplied through process environment variables or the deployment secret store:

| Variable | Value |
| --- | --- |
| `PLAID_CLIENT_ID` | The operator's Plaid application client ID. |
| `PLAID_SECRET` | Secret for the selected Plaid environment. |
| `PLAID_ENV` | Exactly `sandbox` or `production`. |
| `FINPILOT_TOKEN_KEY` | A valid Fernet key used to encrypt access tokens in the database. |

Generate the encryption key locally with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` and store the result as a secret. Keep it separate from database backups and retain it securely: changing or losing it makes existing stored tokens unreadable. Rotation needs a controlled decrypt/re-encrypt migration; simply replacing the key is not a rotation procedure.

Run Alembic to head, including `20260911_0002_bank_connections`, before enabling credentials. For hosted Link OAuth, register the exact HTTPS public origin plus `/` as an allowed Plaid redirect URI. The frontend loads the [official Plaid Link SDK](https://plaid.com/docs/transactions/add-to-app/) only after configured linking is requested, and resumes OAuth for the same signed-in browser session. Allow the official SDK and provider's frames if adding a stricter deployment CSP.

Start with operator-controlled Plaid Sandbox testing; Sandbox is visibly labeled and uses test institutions. Production requires the operator's enabled Plaid application and appropriate institution/product access. The app requests Transactions and accepts checking, savings, and money-market deposit accounts; cards, loan terms, investments, money movement, and ownership verification are not provided by this adapter. Existing manual account/card/loan features remain available.

Create an empty workspace for bank linking. A workspace created with sample data is permanently a payment simulation sandbox and cannot link banks. Conversely, payment simulation run/recover endpoints reject real workspaces. Bank linking grants balance and transaction reads only; it cannot send payments.

Use Connections → Sync to refresh a linked bank. Data from `/accounts/get` is a cached snapshot, with retrieval time shown separately; it is not a live balance verification. The first transaction sync can be empty while the provider prepares history. There are no automatic/webhook refreshes yet. Do not link the same account again to refresh it, because distinct provider connections are not automatically merged by name or account mask.

Disconnect requests provider revocation, removes the encrypted token, and retains saved financial records. A failed or interrupted exchange/revocation can leave an uncertain remote grant, since provider and database commits are separate; review the bank/Plaid connection before retrying. Institution reconnect/update mode and token-key recovery require operator attention. No successful connection is invented when credentials or provider access are unavailable.

Bank integration tests use a mocked HTTP transport and synthetic tokens only. They verify tenant/role/CSRF boundaries, encryption, sample isolation, duplicate ingestion, pending/posting transitions, pagination restart, cursor races, rollback, missing balances, and disconnect failures. No live Plaid Sandbox/Production call or bank account was used, so live institutional behavior and OAuth must be checked in the operator's configured environment before rollout.

Email/password sign-in is available. Email verification, MFA, self-service password recovery, and recovery emails are not implemented; deployment does not provision those capabilities.

## Optional local AI runtime

The application works with calculator answers when no model is available. On Docker Desktop, `host.docker.internal` can address a runtime on the host, provided that runtime is reachable from the container. On Linux, the Compose file adds the host-gateway mapping. The host runtime must be configured to accept connections from that private network; exposing it publicly is unnecessary.

| Setting | Default | Bounds / effect |
| --- | --- | --- |
| `FINPILOT_LLM_TIMEOUT` | `12` seconds | 1-120 seconds; shared remaining inference budget across agent model calls. |
| `FINPILOT_LLM_MAX_TOKENS` | `384` | 1-2,048 output tokens; lower values can lead to calculator fallback for incomplete explanations. |
| `FINPILOT_LLM_HEALTH_TTL` | `15` seconds | 0-300 seconds; cached reachability observation. |
| `FINPILOT_LLM_FAILURE_COOLDOWN` | `5` seconds | 0-60 seconds; skips repeated failing completions while retaining calculator answers. |

Configure model credentials at the operator layer. User accounts cannot change the shared model endpoint. Model inference is not instantaneous: budget exhaustion, malformed output, or failed financial checks return the deterministic answer instead. Financial figures still come from the same calculators.

## Validation supplied

```powershell
.venv/Scripts/python.exe -m pytest tests/test_migrations.py tests/test_bank_linking.py tests/test_llm_runtime.py tests/test_ai.py -q
node tests/bank_frontend.mjs
node tests/assistant_frontend.mjs
```

Migration tests cover SQLite upgrade, repeat upgrade, downgrade, second upgrade, exact comparison against the SQLAlchemy metadata, and PostgreSQL SQL compilation without a server. AI tests cover model health coalescing, nonblocking status reads, client shutdown, timeout/cooldown fallback, mocked-response verification, routing, and guarded execution parity.

The scaffolding has not been deployed. Docker is not installed in the current workspace environment, so the image build and Compose startup have not been executed here. PostgreSQL DDL was compiled offline; a live PostgreSQL migration and restore rehearsal remain deployment checks. Test the image and actual target database before routing hosted user traffic to this deployment.


## Conversations, documents and MCP

Run `python -m alembic upgrade head` before deploying this release to a hosted database. Revisions `20260912_0003`, `20260912_0004` and `20260912_0005` add the document library, conversation generations and MCP credentials/grants. Development SQLite initialization creates missing tables automatically. Existing financial and answer records are preserved.

Install the updated requirements (official MCP SDK, pypdf and python-multipart). Keep request upload limits at 2 MB or higher at the reverse proxy; FinPilot enforces its own 2 MB file limit. PDF processing requires permission to launch the same Python executable in an isolated worker process. Scanned PDFs need OCR outside this release. Database backups must include document text and conversation tables, and must be protected as private financial data.

External MCP sources require `FINPILOT_MCP_SERVERS` and `FINPILOT_TOKEN_KEY`; the default source allowlist is empty. Follow [MCP.md](MCP.md) to configure exact approved HTTPS endpoints and read tools. Each user supplies their own provider credential. Interactive OAuth consent is not included; bearer-token and unauthenticated approved sources are supported. The FinPilot export bridge uses stdio and revocable application read grants. No external source or credentials have been provisioned by this code change.

Verify a deployment with the normal regression suite plus `node tests/knowledge_frontend.mjs`. For local inference validation, run `python scripts/evaluate_knowledge.py` and `python scripts/evaluate_mcp_knowledge.py` against Ollama with `llama3.2:latest`; these use fictional, disposable workspaces. The second script tests actual SDK transport to a fixture provider and a real local-model answer, without an external provider account.


## Reviewed workflow release

Run the Alembic migration job through head before starting updated workers. Revisions 0006, 0007 and 0008 add durable action proposals/receipts, pending workflow state and conversation-owned CSV attachments. The release keeps existing financial APIs and conversations compatible. Do not run schema creation from production application workers.

Include the full Python suite and all seven frontend suites in release validation. The workflow tests cover exact-version confirmation, stale previews, expiry, cancellation, separate workers, tenant/role checks, action receipts, private source ownership, actual MCP SDK fixtures and mocked bank failures. SQLite concurrency and compiled PostgreSQL DDL are local evidence only; run migration and concurrent-confirmation checks against the deployment PostgreSQL service before a hosted rollout.

The model uses structured output through the configured local OpenAI-compatible endpoint. The default shared inference budget remains 12 seconds. Do not increase it merely to present diagnostic Llama results as normal-budget acceptance; consult AI_VALIDATION.md for separate measurements. Provider actions with outcome_unknown require inspection instead of an automatic retry. Real payments and transfers remain outside this release.
