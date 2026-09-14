# Configuration

FinPilot reads its configuration from environment variables only. Native startup does not load `.env`; Docker Compose does, starting from [`.env.example`](../.env.example). Hosting steps are in [DEPLOYMENT.md](../DEPLOYMENT.md). `tests/test_docs.py` fails when the code reads a setting that this page does not list.

## Application

| Setting | Default | Effect |
| --- | --- | --- |
| `FINPILOT_ENV` | unset, meaning development | `production` requires an HTTPS `FINPILOT_PUBLIC_ORIGIN` and PostgreSQL, accepts only that origin's host, sets secure cookies and HSTS, and stops the app from creating tables automatically. |
| `FINPILOT_PUBLIC_ORIGIN` | unset | The exact public origin, such as `https://finance.example.com`. Used for the same-origin check on writes, trusted hosts, secure cookies and Plaid Link tokens. Required in production. |
| `FINPILOT_DATABASE_URL` | `sqlite:///./.local/finpilot.db` | SQLAlchemy database URL. Hosted deployments must use PostgreSQL, for example `postgresql+psycopg://...`. |

## AI model

| Setting | Default | Effect |
| --- | --- | --- |
| `FINPILOT_LLM_BASE_URL` | unset | OpenAI-compatible endpoint ending in `/v1`. When unset, FinPilot probes local runtimes: Ollama on port 11434, llama.cpp on 8080, LM Studio on 1234 and vLLM on 8000. |
| `FINPILOT_LLM_MODEL` | unset | Model ID. When unset, a model is discovered from the endpoint's model list. |
| `FINPILOT_LLM_API_KEY` | `not-needed` | Bearer credential sent to the model endpoint. |
| `FINPILOT_LLM_TEMPERATURE` | `0.1` | Sampling temperature for model calls. |
| `FINPILOT_LLM_MAX_TOKENS` | `384` | Maximum tokens per generation, kept between 1 and 2048. |
| `FINPILOT_LLM_TIMEOUT` | `12` | Inference budget in seconds, kept between 1 and 120. |
| `FINPILOT_LLM_HEALTH_TTL` | `15` | Seconds a model health result stays cached, kept between 0 and 300. |
| `FINPILOT_LLM_FAILURE_COOLDOWN` | `5` | Seconds the model is skipped after a failure, kept between 0 and 60. |

Without a reachable model, the assistant answers in calculator mode.

## Bank linking

| Setting | Default | Effect |
| --- | --- | --- |
| `PLAID_CLIENT_ID` | unset | Plaid client ID. |
| `PLAID_SECRET` | unset | Plaid secret for the chosen environment. |
| `PLAID_ENV` | unset | `sandbox` or `production`. |

Bank linking stays unavailable until all three settings and `FINPILOT_TOKEN_KEY` are set. It supports checking, savings and money-market accounts. Sample workspaces can never link banks.

## Credential encryption

| Setting | Default | Effect |
| --- | --- | --- |
| `FINPILOT_TOKEN_KEY` | unset | Fernet key that encrypts stored Plaid access tokens and MCP credentials. Changing or losing it makes stored tokens unreadable, so keep it separate from database backups. |

## MCP

| Setting | Default | Effect |
| --- | --- | --- |
| `FINPILOT_MCP_SERVERS` | `[]` | JSON array of operator-approved servers, each with `id`, `name`, an HTTPS `url`, `read_tools` and `allow_resources`. An empty array disables external imports. See [MCP.md](../MCP.md). |
| `FINPILOT_API_URL` | `http://127.0.0.1:8100` | Set in an MCP client's environment for the stdio export bridge. Must use HTTPS except on localhost. |
| `FINPILOT_MCP_TOKEN` | unset | The once-shown FinPilot read token, set in the MCP client's environment. |

## Tracing

| Setting | Default | Effect |
| --- | --- | --- |
| `LANGCHAIN_TRACING_V2` | unset | Read by `enable_langsmith()` in `finpilot/ai/graph.py`, which the application does not call today. LangChain libraries may still read it themselves. |
| `LANGCHAIN_API_KEY` | unset | LangSmith credential for that tracing. |
| `LANGCHAIN_PROJECT` | `finpilot` when tracing is enabled | LangSmith project name. |

Leave tracing unset in production. Traces would carry prompts containing financial data to a third-party service; roadmap items `G1.4` and `G7.6` track safe tracing.

## Docker Compose only

| Setting | Default | Effect |
| --- | --- | --- |
| `FINPILOT_BIND_ADDRESS` | `127.0.0.1` | Host address the application port binds to. |
| `FINPILOT_PORT` | `8100` | Host port for the application. |
| `POSTGRES_DB` | `finpilot` | Database name for the bundled PostgreSQL service. |
| `POSTGRES_USER` | `finpilot` | Database user for that service. |
| `POSTGRES_PASSWORD` | none; required | Generated database password. URL-encode it inside `FINPILOT_DATABASE_URL`. |

## Server and tests

| Setting | Default | Effect |
| --- | --- | --- |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Uvicorn setting listing the addresses allowed to report forwarded client IPs. Behind a reverse proxy, set it to the proxy address; see roadmap item `G1.2`. |
| `FINPILOT_TEST_URL` | `http://127.0.0.1:8100` | Base URL that the Node frontend suites call. |
