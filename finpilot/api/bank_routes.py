"""Authenticated bank reads: external calls are never inside a financial UoW."""
from contextlib import contextmanager
from datetime import timezone
import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from .dependencies import P, R, revision
from ..integrations.plaid import PlaidClient, PlaidError, apply_accounts, apply_liabilities, apply_transactions
from ..persistence.database import BankConnectionRow, utcnow
from ..runtime import RevisionConflict
from ..models import Verification

router = APIRouter(prefix="/api/bank", tags=["Bank connections"])


class ExchangeBody(BaseModel):
    public_token: str = Field(min_length=1, max_length=2000)


@contextmanager
def provider(request):
    factory = getattr(request.app.state, "plaid_factory", PlaidClient)
    try:
        with factory() as client:
            yield client
    except PlaidError as exc:
        raise HTTPException(exc.status, str(exc)) from None


def owned(session, principal, connection_id, *, lock=False):
    query = select(BankConnectionRow).where(BankConnectionRow.id == connection_id,
        BankConnectionRow.household_id == principal.household_id)
    row = session.execute(query.with_for_update() if lock else query).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Bank connection not found.")
    return row


def public_connection(row):
    return {"id": row.id, "institution": row.institution_name, "environment": row.environment,
        "status": row.status, "account_ids": list((row.account_mapping or {}).values()),
        "last_synced_at": row.last_synced_at.replace(tzinfo=row.last_synced_at.tzinfo or timezone.utc).isoformat() if row.last_synced_at else None,
        "transactions_ready": bool(row.cursor), "notices": row.notices or [],
        "balance_source": "Cached bank snapshot; not a realtime balance check."}


def preflight(r, p, request, *, linking=False):
    if p.role not in ("owner", "approver"):
        raise HTTPException(403, "This workspace role cannot manage bank connections.")
    ctx = r.read(p)
    expected = revision(request)
    if expected is not None and ctx.revision != expected:
        raise RevisionConflict("Your workspace changed. Refresh before updating bank connections.")
    if linking and getattr(ctx.household, "payment_sandbox", False):
        raise HTTPException(409, "Sample workspaces cannot connect real banks. Create an empty workspace to link an account.")
    r.auth.throttle("bank:" + p.user_id, limit=30, seconds=60)
    return expected


def require_active(client, row):
    client.require_configured()
    if row.status != "active" or not row.encrypted_access_token:
        raise HTTPException(409, "This bank connection has been disconnected.")
    if row.environment != client.config.environment:
        raise HTTPException(409, "This connection belongs to a different bank-provider environment.")


@router.get("/connections")
def connections(request: Request, r: R, p: P):
    with provider(request) as client, r.db.sessions() as session:
        rows = session.execute(select(BankConnectionRow).where(
            BankConnectionRow.household_id == p.household_id).order_by(BankConnectionRow.created_at)).scalars()
        status = client.config.public_status()
        if getattr(r.read(p).household, "payment_sandbox", False):
            status.update(configured=False, reason="Sample workspaces cannot connect banks. Create an empty workspace to link an account.")
        return {"provider": status, "connections": [public_connection(row) for row in rows]}


@router.post("/link-token")
def link_token(request: Request, r: R, p: P):
    preflight(r, p, request, linking=True)
    with provider(request) as client:
        return client.create_link_token(p.user_id, request.app.state.public_origin)


@router.post("/exchange")
def exchange(body: ExchangeBody, request: Request, r: R, p: P):
    expected = preflight(r, p, request, linking=True)
    with provider(request) as client:
        access_token, item_id = client.exchange(body.public_token)
        try:
            # A repeated exchange response must never duplicate financial records.
            with r.db.sessions() as session:
                existing = session.execute(select(BankConnectionRow).where(
                    BankConnectionRow.household_id == p.household_id,
                    BankConnectionRow.environment == client.config.environment,
                    BankConnectionRow.item_id == item_id)).scalar_one_or_none()
                if existing:
                    return {"connection": public_connection(existing), "revision": r.read(p).revision}
            accounts, institution_id, institution_name = client.account_snapshot(access_token)
            liabilities = client.fetch_liabilities(access_token, accounts)
            updates = client.fetch_updates(access_token, "")
            now = utcnow()
            row = BankConnectionRow(id="bank_" + uuid.uuid4().hex,
                household_id=p.household_id, item_id=item_id, environment=client.config.environment,
                institution_id=institution_id, institution_name=institution_name,
                encrypted_access_token=client.encrypt(access_token), cursor=updates["cursor"],
                version=1, status="active", account_mapping={}, notices=[], last_synced_at=now)
            with r.transaction(p, "bank.link", expected) as ctx:
                # Protect the sample boundary even if a future workspace mutator is added.
                if getattr(ctx.household, "payment_sandbox", False):
                    raise HTTPException(409, "Sample workspaces cannot connect banks.")
                duplicate = ctx.db_session.execute(select(BankConnectionRow).where(
                    BankConnectionRow.household_id == p.household_id,
                    BankConnectionRow.environment == client.config.environment,
                    BankConnectionRow.item_id == item_id)).scalar_one_or_none()
                if duplicate:
                    row, imported = duplicate, 0
                else:
                    ctx.db_session.add(row)
                    apply_accounts(ctx.household, row, accounts, now)
                    apply_liabilities(ctx.household, row, accounts, liabilities, now)
                    imported = apply_transactions(ctx.household, row, updates["changes"])
            request.state.revision = ctx.revision
            return {"connection": public_connection(row), "imported": imported, "revision": ctx.revision}
        except Exception as exc:
            # A read-then-remove compensation could revoke the same item after
            # another worker commits it. Only explicit disconnect revokes grants.
            status = (exc.status if isinstance(exc, PlaidError) else
                      409 if isinstance(exc, RevisionConflict) else
                      exc.status_code if isinstance(exc, HTTPException) else 503)
            raise HTTPException(status, "This bank link attempt could not be saved. "
                "Refresh Connections before retrying; the bank/provider authorization may need review.") from None


@router.post("/sync/{connection_id}")
def sync(connection_id: str, request: Request, r: R, p: P):
    expected = preflight(r, p, request)
    from ..services.bank_operations import sync as sync_bank
    with provider(request) as client:
        row, imported, current_revision = sync_bank(r, p, client, connection_id, expected)
    request.state.revision = current_revision
    return {"connection": public_connection(row), "imported": imported, "revision": current_revision}


@router.delete("/connections/{connection_id}")
def disconnect(connection_id: str, request: Request, r: R, p: P):
    expected = preflight(r, p, request)
    from ..services.bank_operations import disconnect as disconnect_bank
    with provider(request) as client:
        row, current_revision = disconnect_bank(r, p, client, connection_id, expected)
    request.state.revision = current_revision
    return {"connection": public_connection(row), "revision": current_revision}
