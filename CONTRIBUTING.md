# Contributing to FinPilot

People and AI coding agents follow the same process. [AGENTS.md](AGENTS.md) is the full protocol; this page is the short version.

## Set up

On Windows, from the project directory:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m uvicorn finpilot.api.app:app --host 127.0.0.1 --port 8100
git config core.hooksPath .githooks
```

On macOS or Linux, use `.venv/bin/python` instead of `.venv/Scripts/python.exe`. The last command turns on the pre-push check for your clone. Settings are described in [docs/configuration.md](docs/configuration.md).

## Before you change anything

1. Read `HANDOFF.md` section 1 to see where the last session stopped.
2. Run `python scripts/roadmap.py next` and pick an open item, or add the work you plan to `ROADMAP.md` as a task.

## While you work

- Keep the rules in the "Engineering conventions that must hold" section of `AGENTS.md`: the tenant boundary, `Decimal` money, AI that never executes on its own, and no invented evidence.
- Add or update tests with every change in behaviour.
- Update the documents that describe what you changed. The documentation map in [README.md](README.md) lists them.

## Before you open a pull request

```powershell
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe scripts/build_docs.py
python scripts/roadmap.py write
python scripts/check_handoff.py --strict
```

- The full Python suite took about 80 seconds on 2026-09-14; run single files while iterating.
- `build_docs.py` regenerates the API, capability and data model references. Run it after changing routes, assistant capabilities or database models.
- Run the frontend suites with `node tests/<name>_frontend.mjs`; `README.md` lists every suite. Only `tests/frontend.mjs` needs the server running.
- Fill in the pull request checklist, which asks for the roadmap and handoff updates.
- GitHub Actions runs the same checks on every push and pull request: `.github/workflows/tests.yml` runs the Python suite, `build_docs.py --check` and the frontend suites that need no server, and `.github/workflows/agent-memory.yml` runs both memory validators, the branch check and the memory tests.

## Commits

- Say what changed and why, for example `fix: keep query strings out of request logs`. Existing history uses prefixes such as `feat:` and `docs:`.
- Include the matching `HANDOFF.md` and `ROADMAP.md` updates in the same commit as the code they describe.
- Never commit `.env` files, anything under `.local/`, or real credentials.

## Security issues

Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md), never in a public issue.
