"""Real HTTP/auth/UoW boundaries with an entirely mocked Plaid transport."""
from contextlib import contextmanager
from copy import deepcopy
from decimal import Decimal
import json

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import select

from finpilot.api.app import create_app
from finpilot.ai.llm import LocalLLM, LLMConfig
from finpilot.integrations.plaid import PlaidClient, PlaidConfig, PlaidError, provider_id
from finpilot.persistence.database import BankConnectionRow, TransactionRow, HouseholdRow, MembershipRow
from finpilot.models import TxState
from finpilot.runtime import RevisionConflict

TOKEN = "access-sandbox-synthetic-test-token"


def account(account_id="bank-checking", **balances):
    return {"account_id": account_id, "type": "depository", "subtype": "checking",
        "name": "Checking", "mask": "1234", "balances": {
            "current": 1100.10, "available": 1000.10, "iso_currency_code": "USD", **balances}}


def transaction(tx_id="posted-1", **changes):
    return {"transaction_id": tx_id, "account_id": "bank-checking", "amount": 12.34,
        "iso_currency_code": "USD", "date": "2026-09-10", "pending": False,
        "name": "Coffee", "personal_finance_category": {"primary": "FOOD_AND_DRINK"}, **changes}


def batch(cursor="cursor-1", *, added=(), modified=(), removed=(), more=False):
    return {"next_cursor": cursor, "has_more": more, "added": list(added),
            "modified": list(modified), "removed": list(removed)}


class Provider:
    def __init__(self):
        self.accounts = [account()]
        self.updates = [batch(added=[transaction()])]
        self.calls = []
        self.fail = None
        self.on_sync = None

    def __call__(self, request):
        path, body = request.url.path, json.loads(request.content)
        self.calls.append((path, body))
        if self.fail == path:
            return httpx.Response(400, json={"error_code": "ITEM_LOGIN_REQUIRED",
                "error_message": "synthetic private provider message " + TOKEN})
        if path == "/link/token/create":
            result = {"link_token": "link-sandbox-test", "expiration": "2026-09-11T23:00:00Z"}
        elif path == "/item/public_token/exchange":
            result = {"access_token": TOKEN, "item_id": "item-test"}
        elif path == "/accounts/get":
            result = {"accounts": self.accounts, "item": {"institution_id": "ins-test"}}
        elif path == "/institutions/get_by_id":
            result = {"institution": {"name": "Test bank"}}
        elif path == "/transactions/sync":
            if self.on_sync:
                callback, self.on_sync = self.on_sync, None
                callback()
            result = self.updates.pop(0) if len(self.updates) > 1 else self.updates[0]
            if result.get("error_code"):
                return httpx.Response(400, json=result)
        elif path == "/item/remove":
            result = {"request_id": "removed-test"}
        else:
            raise AssertionError("Unexpected provider endpoint: " + path)
        return httpx.Response(200, json=deepcopy(result))


def register(client, email="bank-owner@example.com", *, sample=False):
    result = client.post("/api/auth/register", json={"name": "Bank owner", "email": email,
        "password": "synthetic-bank-test-password", "sample_data": sample})
    assert result.status_code == 201, result.text
    identity = result.json()
    client.headers["X-CSRF-Token"] = identity["csrf_token"]
    return identity


@pytest.fixture
def bank(tmp_path, monkeypatch):
    monkeypatch.delenv("FINPILOT_ENV", raising=False)
    monkeypatch.delenv("FINPILOT_PUBLIC_ORIGIN", raising=False)
    app = create_app("sqlite:///" + (tmp_path / "bank.db").as_posix(), llm=LocalLLM(LLMConfig()))
    mock = Provider()
    config = PlaidConfig("synthetic-client", "synthetic-secret", "sandbox", Fernet.generate_key().decode())
    app.state.plaid_factory = lambda: PlaidClient(config, transport=httpx.MockTransport(mock))
    with TestClient(app) as client:
        yield client, app.state.runtime, mock, config


def link(client):
    response = client.post("/api/bank/exchange", json={"public_token": "public-test"})
    assert response.status_code == 200, response.text
    return response.json()["connection"]["id"]


def saved(r):
    with r.db.sessions() as session:
        return session.scalar(select(BankConnectionRow))


def context(client, r):
    return r.read(r.auth.authenticate(client.cookies.get("finpilot_session")))


def test_link_encryption_real_balances_and_read_only_capabilities(bank):
    client, r, mock, config = bank
    register(client)
    token_response = client.post("/api/bank/link-token")
    assert token_response.status_code == 200
    payload = mock.calls[-1][1]
    assert payload["products"] == ["transactions"]
    assert payload["account_filters"]["depository"]["account_subtypes"] == ["checking", "savings", "money market"]
    connection_id = link(client)
    row, ctx = saved(r), context(client, r)
    assert TOKEN not in row.encrypted_access_token
    assert Fernet(config.token_key.encode()).decrypt(row.encrypted_access_token.encode()).decode() == TOKEN
    public = client.get("/api/bank/connections").text
    assert TOKEN not in public and "encrypted_access_token" not in public and "item-test" not in public
    account_id = row.account_mapping["bank-checking"]
    a = ctx.household.accounts[account_id]
    assert a.available.amount == Decimal("1000.10") and a.current.amount == Decimal("1100.10")
    assert sorted(c.value for c in a.capabilities) == ["view_balance", "view_transactions"]
    assert a.provenance.as_of is None and a.ownership_category == "unverified"
    assert a.protection.value == "unresolved" and a.institution_id == ""
    tx = ctx.household.transactions[0]
    assert str(tx.amount.amount) == "-12.34" and tx.balance_already_reflected
    assert connection_id == row.id and row.cursor == "cursor-1"
    assert client.get("/api/bank/connections").json()["connections"][0]["last_synced_at"].endswith("+00:00")
    with r.db.sessions() as session:
        assert session.scalar(select(TransactionRow)).payload["amount"]["amount"] == "-12.34"


def test_unconfigured_and_sample_workspaces_never_call_provider(bank):
    client, r, mock, config = bank
    register(client, sample=True)
    assert client.post("/api/bank/link-token").status_code == 409
    assert client.post("/api/bank/exchange", json={"public_token": "public-test"}).status_code == 409
    assert not mock.calls
    assert not client.get("/api/bank/connections").json()["provider"]["configured"]
    other = TestClient(client.app)
    register(other, "empty@example.com")
    client.app.state.plaid_factory = lambda: PlaidClient(PlaidConfig(), transport=httpx.MockTransport(mock))
    assert other.post("/api/bank/link-token").status_code == 503
    assert not other.get("/api/bank/connections").json()["provider"]["configured"]
    assert not mock.calls


def test_tenant_auth_csrf_and_viewer_boundaries(bank):
    client, r, mock, _ = bank
    assert client.get("/api/bank/connections").status_code == 401
    owner = register(client)
    connection_id = link(client)
    other = TestClient(client.app)
    second = register(other, "other@example.com")
    before = len(mock.calls)
    assert other.get("/api/bank/connections").json()["connections"] == []
    assert other.post(f"/api/bank/sync/{connection_id}").status_code == 404
    assert other.delete(f"/api/bank/connections/{connection_id}").status_code == 404
    assert len(mock.calls) == before
    del client.headers["X-CSRF-Token"]
    assert client.post(f"/api/bank/sync/{connection_id}").status_code == 403
    client.headers["X-CSRF-Token"] = owner["csrf_token"]
    assert client.post(f"/api/bank/sync/{connection_id}", headers={"Origin": "https://other.invalid"}).status_code == 403
    with r.db.sessions.begin() as session:
        membership = session.scalar(select(MembershipRow).where(MembershipRow.user_id == second["user"]["id"]))
        membership.role = "viewer"
    assert other.post("/api/bank/link-token").status_code == 403
    assert len(mock.calls) == before


def test_repeated_exchange_and_sync_do_not_duplicate_records(bank):
    client, r, mock, _ = bank
    register(client)
    connection_id = link(client)
    assert link(client) == connection_id
    for _ in range(2):
        assert client.post(f"/api/bank/sync/{connection_id}").status_code == 200
    ctx = context(client, r)
    assert len(ctx.household.accounts) == 1 and len(ctx.household.transactions) == 1
    with r.db.sessions() as session:
        assert len(session.scalars(select(BankConnectionRow)).all()) == 1
        assert len(session.scalars(select(TransactionRow)).all()) == 1


def test_pending_to_posted_across_pages_keeps_audit_without_double_counting(bank):
    client, r, mock, _ = bank
    register(client)
    mock.updates = [batch(added=[transaction("pending-1", pending=True)])]
    connection_id = link(client)
    mock.updates = [batch("page-1", removed=[{"transaction_id": "pending-1"}], more=True),
        batch("cursor-2", added=[transaction("posted-2", pending_transaction_id="pending-1")])]
    result = client.post(f"/api/bank/sync/{connection_id}")
    assert result.status_code == 200, result.text
    txs = {tx.id: tx for tx in context(client, r).household.transactions}
    pending = txs[provider_id(connection_id, "pending-1", "tx_plaid_")]
    posted = txs[provider_id(connection_id, "posted-2", "tx_plaid_")]
    assert pending.state == TxState.REVERSED and not pending.counts_as_spending
    assert posted.linked_tx_id == pending.id and posted.counts_as_spending
    assert saved(r).cursor == "cursor-2"


def test_bad_provider_batch_rolls_back_balances_cursor_and_projection(bank):
    client, r, mock, _ = bank
    register(client)
    connection_id = link(client)
    before = context(client, r)
    mock.accounts = [account(available=3, current=4)]
    mock.updates = [batch("bad-cursor", added=[transaction("bad-currency", iso_currency_code="EUR")])]
    result = client.post(f"/api/bank/sync/{connection_id}")
    assert result.status_code == 502, result.text
    after = context(client, r)
    assert after.revision == before.revision
    assert list(after.household.accounts.values())[0].available == list(before.household.accounts.values())[0].available
    assert saved(r).cursor == "cursor-1" and saved(r).version == 1
    with r.db.sessions() as session:
        assert len(session.scalars(select(TransactionRow)).all()) == 1


def test_provider_failure_and_disconnect_failure_do_not_claim_success(bank):
    client, r, mock, _ = bank
    register(client)
    connection_id = link(client)
    mock.fail = "/transactions/sync"
    response = client.post(f"/api/bank/sync/{connection_id}")
    assert response.status_code == 502 and TOKEN not in response.text and "private" not in response.text
    assert saved(r).cursor == "cursor-1"
    mock.fail = "/item/remove"
    assert client.delete(f"/api/bank/connections/{connection_id}").status_code == 502
    assert saved(r).status == "active" and saved(r).encrypted_access_token
    mock.fail = None
    assert client.delete(f"/api/bank/connections/{connection_id}").status_code == 200
    row, ctx = saved(r), context(client, r)
    assert row.status == "disconnected" and row.encrypted_access_token is None
    assert len(ctx.household.accounts) == 1 and len(ctx.household.transactions) == 1
    a = next(iter(ctx.household.accounts.values()))
    assert not a.connection_healthy and a.provenance.verification.value == "stale"
    calls = len(mock.calls)
    assert client.post(f"/api/bank/sync/{connection_id}").status_code == 409
    assert client.delete(f"/api/bank/connections/{connection_id}").status_code == 200
    assert len(mock.calls) == calls


def test_paginated_mutation_restarts_whole_batch_at_saved_cursor(bank):
    client, r, mock, _ = bank
    register(client)
    connection_id = link(client)
    mock.updates = [batch("discard-page", added=[transaction("discard-me")], more=True),
        {"error_code": "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION"},
        batch("cursor-2", added=[transaction("keep-me")])]
    assert client.post(f"/api/bank/sync/{connection_id}").status_code == 200
    cursors = [body.get("cursor", "") for path, body in mock.calls if path == "/transactions/sync"]
    assert cursors[-3:] == ["cursor-1", "discard-page", "cursor-1"]
    ids = {tx.id for tx in context(client, r).household.transactions}
    assert provider_id(connection_id, "discard-me", "tx_plaid_") not in ids
    assert provider_id(connection_id, "keep-me", "tx_plaid_") in ids


def test_concurrent_cursor_winner_blocks_stale_sync_commit(bank):
    client, r, mock, _ = bank
    register(client)
    connection_id = link(client)
    principal = r.auth.authenticate(client.cookies.get("finpilot_session"))
    def winning_writer():
        # This callback runs during provider I/O. It can acquire a UoW because
        # the bank route has not kept a database transaction open over the call.
        with r.transaction(principal, "test.concurrent-bank-writer") as ctx:
            row = ctx.db_session.scalar(select(BankConnectionRow).where(BankConnectionRow.id == connection_id))
            row.version += 1
            row.cursor = "winning-cursor"
    mock.on_sync = winning_writer
    mock.updates = [batch("losing-cursor", added=[transaction("losing-tx")])]
    result = client.post(f"/api/bank/sync/{connection_id}")
    assert result.status_code == 409, result.text
    assert saved(r).cursor == "winning-cursor"
    assert len(context(client, r).household.transactions) == 1


def test_concurrent_disconnect_prevents_inflight_sync_from_restoring_token(bank):
    client, r, mock, _ = bank
    register(client)
    connection_id = link(client)
    def revoke_while_syncing():
        response = client.delete(f"/api/bank/connections/{connection_id}")
        assert response.status_code == 200
    mock.on_sync = revoke_while_syncing
    mock.updates = [batch("late-cursor", added=[transaction("late-tx")])]
    assert client.post(f"/api/bank/sync/{connection_id}").status_code == 409
    assert saved(r).status == "disconnected" and saved(r).encrypted_access_token is None
    assert len(context(client, r).household.transactions) == 1


def test_stale_exchange_cannot_revoke_concurrently_saved_same_item(bank, monkeypatch):
    client, r, mock, config = bank
    identity = register(client)
    principal = r.auth.authenticate(client.cookies.get("finpilot_session"))
    baseline = r.read(principal).revision
    client.headers["If-Match"] = str(baseline)
    original = r.transaction
    intercepted = False
    @contextmanager
    def concurrent_exchange(p, action, expected_revision=None):
        nonlocal intercepted
        if action == "bank.link" and not intercepted:
            intercepted = True
            with original(p, "test.winning-link") as winner:
                winner.db_session.add(BankConnectionRow(id="bank-winning", household_id=p.household_id,
                    item_id="item-test", environment="sandbox", institution_name="Test bank",
                    encrypted_access_token=Fernet(config.token_key.encode()).encrypt(TOKEN.encode()).decode(),
                    cursor="", version=1, status="active", account_mapping={}))
        with original(p, action, expected_revision) as ctx:
            yield ctx
    monkeypatch.setattr(r, "transaction", concurrent_exchange)
    response = client.post("/api/bank/exchange", json={"public_token": "public-test"})
    assert response.status_code == 409, response.text
    assert saved(r).id == "bank-winning" and saved(r).encrypted_access_token
    assert not any(path == "/item/remove" for path, _ in mock.calls)


def test_database_failure_rolls_back_cursor_and_domain_changes(bank, monkeypatch):
    client, r, mock, _ = bank
    register(client)
    connection_id = link(client)
    before = context(client, r)
    mock.updates = [batch("rollback-cursor", added=[transaction("rollback-tx")])]
    def fail_projection(*args):
        raise RuntimeError("synthetic storage failure")
    monkeypatch.setattr(r, "_sync_transactions", fail_projection)
    with pytest.raises(RuntimeError, match="synthetic storage failure"):
        client.post(f"/api/bank/sync/{connection_id}")
    assert saved(r).cursor == "cursor-1"
    assert context(client, r).revision == before.revision
    assert len(context(client, r).household.transactions) == 1


@pytest.mark.parametrize("snapshot", [[account(available=None)], [], [dict(account(), type="credit", subtype="credit card")]])
def test_omitted_or_incomplete_account_snapshot_marks_existing_data_stale(bank, snapshot):
    client, r, mock, _ = bank
    register(client)
    connection_id = link(client)
    mock.accounts = snapshot
    mock.updates = [batch("cursor-2")]
    assert client.post(f"/api/bank/sync/{connection_id}").status_code == 200
    a = next(iter(context(client, r).household.accounts.values()))
    assert not a.connection_healthy and a.provenance.verification.value == "stale"
    assert a.available.amount == Decimal("1000.10")


def test_missing_balances_and_credit_terms_are_not_invented(bank):
    client, r, mock, _ = bank
    register(client)
    mock.accounts = [account(available=None), dict(account("credit"), type="credit", subtype="credit card")]
    connection_id = link(client)
    ctx = context(client, r)
    assert not ctx.household.accounts and not ctx.household.cards and not ctx.household.liabilities
    assert len(saved(r).notices) == 2
    assert client.get("/api/bank/connections").json()["connections"][0]["account_ids"] == []


def test_environment_mismatch_and_unreadable_token_prevent_provider_calls(bank):
    client, r, mock, config = bank
    register(client)
    connection_id = link(client)
    before = len(mock.calls)
    other = PlaidConfig(config.client_id, config.secret, "production", config.token_key)
    client.app.state.plaid_factory = lambda: PlaidClient(other, transport=httpx.MockTransport(mock))
    assert client.post(f"/api/bank/sync/{connection_id}").status_code == 409
    other = PlaidConfig(config.client_id, config.secret, "sandbox", Fernet.generate_key().decode())
    client.app.state.plaid_factory = lambda: PlaidClient(other, transport=httpx.MockTransport(mock))
    assert client.post(f"/api/bank/sync/{connection_id}").status_code == 503
    assert len(mock.calls) == before


def test_failed_exchange_never_revokes_an_ambiguous_remote_grant(bank):
    client, r, mock, _ = bank
    register(client)
    mock.fail = "/accounts/get"
    response = client.post("/api/bank/exchange", json={"public_token": "public-test"})
    assert response.status_code == 502
    assert "could not be saved" in response.text and "authorization may need review" in response.text
    assert TOKEN not in response.text
    assert saved(r) is None
    assert not context(client, r).household.accounts
    assert not any(path == "/item/remove" for path, _ in mock.calls)
