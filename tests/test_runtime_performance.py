"""Cache validity, bounded projection work and inference isolation contracts."""
from datetime import date, timedelta

import pytest
from sqlalchemy import event

from finpilot.ai.llm import LocalLLM, LLMConfig
from finpilot.models import TxState
from finpilot.persistence.database import Database, MembershipRow
from finpilot.runtime import Runtime
from finpilot.services.auth import AuthError
from scripts.benchmark import HeldModel, inference_isolation
from tests.test_hosted import hosted, register


def test_warm_bootstrap_avoids_snapshot_decode_and_returns_private_data(hosted, monkeypatch):
    client, runtime, _ = hosted
    register(client)
    principal = runtime.auth.authenticate(client.cookies.get("finpilot_session"))
    first = runtime.bootstrap(principal)
    expected_name = first["workspace"]["household"]["name"]
    first["workspace"]["household"]["name"] = "Mutated local result"
    def unexpected_read(*args, **kwargs):
        raise AssertionError("A warm bootstrap should read the revision, not decode all history")
    monkeypatch.setattr(runtime, "read", unexpected_read)
    assert runtime.bootstrap(principal)["workspace"]["household"]["name"] == expected_name


def test_bootstrap_cache_observes_other_worker_commits_and_membership_removal(hosted):
    client, runtime, url = hosted
    identity = register(client)
    principal = runtime.auth.authenticate(client.cookies.get("finpilot_session"))
    before = runtime.bootstrap(principal)
    other_worker = Runtime(Database(url), llm=LocalLLM(LLMConfig()))
    try:
        with other_worker.transaction(principal, "test.other-worker") as ctx:
            ctx.household.name = "Committed by another worker"
        changed = runtime.bootstrap(principal)
        assert changed["revision"] == before["revision"] + 1
        assert changed["workspace"]["household"]["name"] == "Committed by another worker"
        with runtime.db.sessions.begin() as session:
            session.delete(session.get(MembershipRow, (identity["user"]["id"], identity["household_id"])))
        with pytest.raises(AuthError):
            runtime.bootstrap(principal)
    finally:
        other_worker.close()


def test_live_bootstrap_cache_rolls_over_at_calendar_change(hosted, monkeypatch):
    import finpilot.runtime as runtime_module
    client, runtime, _ = hosted
    register(client, sample=False)
    principal = runtime.auth.authenticate(client.cookies.get("finpilot_session"))
    before = runtime.bootstrap(principal)
    next_date = date.fromisoformat(before["dashboard"]["as_of"]) + timedelta(days=1)
    class Tomorrow(date):
        @classmethod
        def today(cls):
            return next_date
    monkeypatch.setattr(runtime_module, "date", Tomorrow)
    assert runtime.bootstrap(principal)["dashboard"]["as_of"] == next_date.isoformat()


def test_projection_reads_only_changed_transactions_and_keeps_reversals_atomic(hosted):
    client, runtime, _ = hosted
    register(client)
    response = client.post("/api/transactions/import", json={"account_id": "acc_checking",
        "csv": "Date,Description,Amount\n2026-09-08,Coffee,-4.85\n2026-09-09,Lunch,-12.00\n",
        "mapping": {"date": "Date", "description": "Description", "amount": "Amount"}})
    assert response.status_code == 200
    statements = []
    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())
    event.listen(runtime.db.engine, "before_cursor_execute", capture)
    try:
        assert client.patch("/api/manage/accounts/acc_checking", json={"nickname": "Renamed"}).status_code == 200
        assert not any("from transactions" in sql for sql in statements)
    finally:
        event.remove(runtime.db.engine, "before_cursor_execute", capture)
    principal = runtime.auth.authenticate(client.cookies.get("finpilot_session"))
    with runtime.transaction(principal, "test.provider-correction") as ctx:
        changed = ctx.household.transactions[0]
        changed.state = TxState.REVERSED
        changed.description = "Provider reversed"
        changed_id = changed.id
        # A canonical deletion must remove the SQL projection in the same commit.
        ctx.household.transactions = [changed]
    rows = client.post("/api/transactions/search", json={}).json()
    assert rows["total"] == 1
    assert rows["transactions"][0]["id"] == changed_id
    assert rows["transactions"][0]["state"] == "reversed"
    assert rows["transactions"][0]["counts_as_spending"] is False


def test_held_mock_inference_does_not_block_financial_read_or_write(hosted):
    client, runtime, _ = hosted
    register(client)
    held = HeldModel()
    original = runtime.llm
    runtime.llm = held.client
    try:
        revision = client.get("/api/bootstrap").json()["revision"]
        result = inference_isolation(client, held, revision)
        assert result["both_completed_before_model_release"]
        assert result["mock_model_calls"] == 1
    finally:
        held.release.set()
        held.client.close()
        runtime.llm = original
