"""The Plaid Sandbox check drives FinPilot's bank routes end to end; here Plaid itself is mocked."""
import importlib.util
import json
import logging
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet

from finpilot.integrations.plaid import PlaidClient, PlaidConfig, PlaidError
from tests.test_bank_aggregation import credit_account, reported_liabilities
from tests.test_bank_linking import TOKEN, Provider

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("verify_plaid_sandbox", ROOT / "scripts" / "verify_plaid_sandbox.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["verify_plaid_sandbox"] = module
    spec.loader.exec_module(module)
    return module


def test_sandbox_check_records_link_sync_terms_and_disconnect(monkeypatch):
    monkeypatch.delenv("FINPILOT_ENV", raising=False)
    monkeypatch.delenv("FINPILOT_PUBLIC_ORIGIN", raising=False)
    tool = _load()
    mock = Provider()
    mock.accounts = [mock.accounts[0], credit_account()]
    mock.liabilities = reported_liabilities()
    config = PlaidConfig("synthetic-client", "synthetic-secret", "sandbox", Fernet.generate_key().decode())
    evidence = tool.run(config, transport=httpx.MockTransport(mock), wait=0)
    assert evidence["passed"], evidence
    assert evidence["steps"]["sandbox_item"] == "created" and evidence["steps"]["sync"] == [200]
    assert evidence["accounts_by_type"] == {"checking": 1, "credit_card": 1}
    assert evidence["liabilities"] == [{"type": "credit_card", "terms_complete": True}]
    assert evidence["cards"] == 1 and evidence["transactions"] == 1 and evidence["provider_errors"] == []
    paths = [path for path, _ in mock.calls]
    assert "/sandbox/public_token/create" in paths and paths[-1] == "/item/remove"
    text = json.dumps(evidence)
    assert TOKEN not in text and "synthetic" not in text


def test_rejected_keys_are_recorded_by_endpoint_and_code(monkeypatch):
    monkeypatch.delenv("FINPILOT_ENV", raising=False)
    monkeypatch.delenv("FINPILOT_PUBLIC_ORIGIN", raising=False)
    tool = _load()

    def rejects(request):
        return httpx.Response(400, json={"error_code": "INVALID_API_KEYS", "error_message": "synthetic rejection"})

    config = PlaidConfig("synthetic-client", "synthetic-secret", "sandbox", Fernet.generate_key().decode())
    evidence = tool.run(config, transport=httpx.MockTransport(rejects), wait=0)
    assert not evidence["passed"] and evidence["steps"]["sandbox_item"] == "INVALID_API_KEYS"
    assert {"endpoint": "/sandbox/public_token/create", "code": "INVALID_API_KEYS"} in evidence["provider_errors"]
    assert "synthetic" not in json.dumps(evidence)


def test_sandbox_check_never_runs_against_production():
    tool = _load()
    with pytest.raises(SystemExit):
        tool.run(PlaidConfig("client", "secret", "production", Fernet.generate_key().decode()))


def test_keys_come_from_a_local_file_and_environment_variables_win(tmp_path):
    tool = _load()
    path = tmp_path / "plaid.env"
    path.write_text("# PLAID_CLIENT_ID=commented-out\nexport PLAID_CLIENT_ID=\"file-client\"\n"
                    "PLAID_SECRET='file-secret'\nPLAID_ENV=sandbox\nOTHER_SETTING=ignored\n", encoding="utf-8")
    found = tool.load_credentials(path, environ={"PLAID_SECRET": "env-secret"})
    assert found == {"PLAID_CLIENT_ID": "file-client", "PLAID_SECRET": "env-secret", "PLAID_ENV": "sandbox"}


def test_missing_keys_explain_the_next_step_without_calling_plaid(tmp_path, monkeypatch, capsys):
    tool = _load()
    for key in tool.KEYS:
        monkeypatch.delenv(key, raising=False)
    path = tmp_path / "plaid.env"
    assert tool.main(["--env-file", str(path), "--create-env-file"]) == 0
    assert "PLAID_CLIENT_ID=\n" in path.read_text(encoding="utf-8")
    assert tool.main(["--env-file", str(path), "--create-env-file"]) == 0
    evidence = tmp_path / "evidence.json"
    assert tool.main(["--env-file", str(path), "--output", str(evidence)]) == 2
    assert "No Plaid Sandbox keys found" in capsys.readouterr().err
    assert not evidence.exists()


def test_importing_the_check_does_not_build_the_app():
    # finpilot.api.app creates its database from FINPILOT_DATABASE_URL at import, so a launcher that
    # imports load_credentials before setting that variable would silently use .local/finpilot.db.
    code = ("import runpy, sys; runpy.run_path('scripts/verify_plaid_sandbox.py', run_name='check'); "
            "print('finpilot.api.app' in sys.modules)")
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"


def test_failed_plaid_calls_log_endpoint_and_code_without_keys_or_messages(caplog):
    def rejects(request):
        return httpx.Response(400, json={"error_type": "INVALID_INPUT", "error_code": "INVALID_API_KEYS",
            "error_message": "synthetic message " + TOKEN, "request_id": "req-Synthetic1"})

    config = PlaidConfig("synthetic-client", "synthetic-secret", "production", Fernet.generate_key().decode())
    with caplog.at_level(logging.WARNING, logger="finpilot.integrations.plaid"):
        with PlaidClient(config, transport=httpx.MockTransport(rejects)) as plaid:
            with pytest.raises(PlaidError) as failure:
                plaid.create_link_token("user-1")
    assert failure.value.code == "INVALID_API_KEYS"
    assert "The bank connection could not be updated" in str(failure.value)
    logged = caplog.text
    assert "production /link/token/create" in logged and "error_code=INVALID_API_KEYS" in logged
    assert "error_type=INVALID_INPUT" in logged and "request_id=req-Synthetic1" in logged
    assert "synthetic" not in logged and TOKEN not in logged
