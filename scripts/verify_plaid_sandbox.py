"""Check FinPilot's bank linking against Plaid Sandbox and save the evidence (roadmap G8.1).

Needs keys from a Plaid Sandbox application. Put PLAID_CLIENT_ID and PLAID_SECRET in the git-ignored
file .local/plaid-sandbox.env, which --create-env-file writes as an empty template, or set them as
environment variables, which take precedence. PLAID_ENV must be sandbox. FINPILOT_TOKEN_KEY is
optional; the run generates a throwaway key. The check calls FinPilot's own HTTP routes in process
with a temporary SQLite database and replaces the Plaid Link browser step with
/sandbox/public_token/create. It links an item, syncs until transactions arrive, reads the imported
accounts, card and loan terms and categories, then disconnects. The evidence file holds statuses,
counts and Plaid error codes only, never keys, tokens or provider IDs.

    python scripts/verify_plaid_sandbox.py --create-env-file
    python scripts/verify_plaid_sandbox.py
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import tempfile
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cryptography.fernet import Fernet  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from finpilot.ai.llm import LLMConfig, LocalLLM  # noqa: E402
from finpilot.integrations.plaid import PlaidClient, PlaidConfig, PlaidError  # noqa: E402

# First Platypus Bank is a Plaid Sandbox test institution; pass --institution to use another.
INSTITUTION = "ins_109508"
EVIDENCE = ROOT / "validation" / "plaid-sandbox-e2e.json"
CREDENTIAL_FILE = ROOT / ".local" / "plaid-sandbox.env"
KEYS = ("PLAID_CLIENT_ID", "PLAID_SECRET", "PLAID_ENV", "FINPILOT_TOKEN_KEY")
TEMPLATE = """# Plaid Sandbox keys for scripts/verify_plaid_sandbox.py.
# This folder is git-ignored. Never commit these values or paste them into a chat.
# In the Plaid Dashboard, open Developers, then Keys, and copy the client ID and the Sandbox secret.
PLAID_CLIENT_ID=
PLAID_SECRET=
PLAID_ENV=sandbox
"""


def load_credentials(path=CREDENTIAL_FILE, environ=None):
    """Keys from a local KEY=VALUE file, overridden by environment variables. Values are never printed."""
    environ = os.environ if environ is None else environ
    found = {}
    if path is not None and Path(path).is_file():
        for raw in Path(path).read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if line.startswith("export "):
                line = line[len("export "):].strip()
            key, separator, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if not separator or key not in KEYS:
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if value:
                found[key] = value
    for key in KEYS:
        if environ.get(key):
            found[key] = environ[key]
    return found


def _transactions(client):
    return client.post("/api/transactions/search", json={"limit": 200}).json()


def _evidence(config, institution, steps, summary, errors):
    syncs = steps.get("sync") or []
    passed = bool(steps.get("register") == 201 and steps.get("link_token") == 200
                  and steps.get("exchange") == 200 and syncs and all(code == 200 for code in syncs)
                  and steps.get("disconnect") == 200 and summary.get("accounts_by_type")
                  and summary.get("transactions"))
    return {"checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "environment": config.environment, "institution": institution, "steps": steps,
            **summary, "provider_errors": errors, "passed": passed}


def run(config, *, transport=None, institution=INSTITUTION, attempts=10, wait=3.0):
    """Link, sync and disconnect one Sandbox item through FinPilot and summarize what it imported."""
    if config.environment != "sandbox":
        raise SystemExit("PLAID_ENV must be sandbox; this check never runs against production.")
    errors = []

    class RecordingClient(PlaidClient):
        """Keeps each failed endpoint and Plaid error code, which FinPilot's own responses withhold."""

        def _post(self, endpoint, payload):
            try:
                return super()._post(endpoint, payload)
            except PlaidError as exc:
                errors.append({"endpoint": endpoint, "code": exc.code})
                raise

    def factory():
        return RecordingClient(config, transport=transport)

    steps, summary = {}, {}
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        # Imported here, not at module level: importing finpilot.api.app builds its module-level app and
        # database from FINPILOT_DATABASE_URL, which a caller of load_credentials may not have set yet.
        from finpilot.api.app import create_app

        app = create_app("sqlite:///" + (Path(folder) / "sandbox.db").as_posix(), llm=LocalLLM(LLMConfig()))
        app.state.plaid_factory = factory
        with TestClient(app) as client:
            identity = client.post("/api/auth/register", json={
                "name": "Sandbox check", "email": f"sandbox-{uuid.uuid4().hex}@example.com",
                "password": secrets.token_urlsafe(18), "sample_data": False})
            steps["register"] = identity.status_code
            if identity.status_code != 201:
                return _evidence(config, institution, steps, summary, errors)
            client.headers["X-CSRF-Token"] = identity.json()["csrf_token"]
            steps["link_token"] = client.post("/api/bank/link-token").status_code
            try:
                with factory() as plaid:
                    public_token = plaid.sandbox_public_token(institution, ["transactions", "liabilities"])
                steps["sandbox_item"] = "created"
            except PlaidError as exc:
                steps["sandbox_item"] = exc.code
                return _evidence(config, institution, steps, summary, errors)
            exchange = client.post("/api/bank/exchange", json={"public_token": public_token})
            steps["exchange"] = exchange.status_code
            if exchange.status_code != 200:
                return _evidence(config, institution, steps, summary, errors)
            connection_id = exchange.json()["connection"]["id"]
            steps["sync"] = []
            for attempt in range(max(1, attempts)):
                if attempt:
                    time.sleep(wait)
                synced = client.post(f"/api/bank/sync/{connection_id}")
                steps["sync"].append(synced.status_code)
                if synced.status_code != 200 or _transactions(client)["total"]:
                    break
            workspace = client.get("/api/workspace").json()
            page = _transactions(client)
            connection = next(row for row in client.get("/api/bank/connections").json()["connections"]
                              if row["id"] == connection_id)
            summary = {
                "accounts_by_type": dict(sorted(Counter(item["type"] for item in workspace["accounts"]).items())),
                "liabilities": sorted(({"type": item["type"], "terms_complete": item["terms_complete"]}
                                       for item in workspace["liabilities"]),
                                      key=lambda item: (item["type"], item["terms_complete"])),
                "cards": len(workspace["cards"]),
                "transactions": page["total"],
                "categories": dict(Counter(tx["category"] for tx in page["transactions"]).most_common()),
                "notices": connection["notices"],
            }
            steps["disconnect"] = client.delete(f"/api/bank/connections/{connection_id}").status_code
    return _evidence(config, institution, steps, summary, errors)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--institution", default=INSTITUTION, help="Plaid Sandbox institution ID")
    parser.add_argument("--output", type=Path, default=EVIDENCE, help="where to write the evidence JSON")
    parser.add_argument("--env-file", type=Path, default=CREDENTIAL_FILE,
                        help="KEY=VALUE file with the Sandbox keys; environment variables take precedence")
    parser.add_argument("--create-env-file", action="store_true", help="write an empty keys file and exit")
    args = parser.parse_args(argv)
    if args.create_env_file:
        if args.env_file.exists():
            print(f"{args.env_file} already exists and was left unchanged.")
        else:
            args.env_file.parent.mkdir(parents=True, exist_ok=True)
            args.env_file.write_text(TEMPLATE, encoding="utf-8")
            print(f"Wrote {args.env_file}. Add the Sandbox client ID and secret, then run this script again.")
        return 0
    found = load_credentials(args.env_file)
    if not found.get("PLAID_CLIENT_ID") or not found.get("PLAID_SECRET"):
        print(f"No Plaid Sandbox keys found. Add PLAID_CLIENT_ID and PLAID_SECRET to {args.env_file}, or set them "
              "as environment variables. Create the file with --create-env-file.", file=sys.stderr)
        return 2
    environment = found.get("PLAID_ENV", "sandbox")
    if environment != "sandbox":
        print("PLAID_ENV must be sandbox; this check never runs against production.", file=sys.stderr)
        return 2
    config = PlaidConfig(found["PLAID_CLIENT_ID"], found["PLAID_SECRET"], environment,
                         found.get("FINPILOT_TOKEN_KEY") or Fernet.generate_key().decode())
    evidence = run(config, institution=args.institution)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": evidence["passed"], "steps": evidence["steps"],
                      "provider_errors": evidence["provider_errors"]}, indent=2))
    return 0 if evidence["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
