"""Workspace commands and bounded, tenant-scoped read projections."""
from datetime import timezone
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, func
from ..persistence.database import AuditRow, TransactionRow
from ..services.workspace import WorkspaceService
from ..integrations.plaid import PlaidConfig
from .dependencies import P, R, revision

router = APIRouter(prefix="/api", tags=["Workspace data"])


@router.get("/bootstrap")
def bootstrap(request: Request, p: P, r: R):
    data = r.bootstrap(p)
    request.state.revision = data["revision"]
    sandbox = data["workspace"]["household"]["payment_sandbox"]
    data["capabilities"] = {"manual_accounts": True, "csv_import": True,
                            "payment_provider": "simulation", "payment_sandbox": sandbox,
                            "bank_linking": PlaidConfig.from_env().configured and not sandbox}
    return data


@router.get("/dashboard")
def dashboard(request: Request, p: P, r: R):
    data = r.dashboard(p)
    request.state.revision = data["revision"]
    return data


@router.get("/workspace")
def workspace(request: Request, p: P, r: R):
    ctx = r.read(p)
    request.state.revision = ctx.revision
    return r.workspace(ctx)


@router.post("/manage/{kind}", status_code=201)
def create_record(kind: str, payload: dict, request: Request, p: P, r: R):
    with r.transaction(p, f"{kind}.create", revision(request)) as ctx:
        result = WorkspaceService(ctx.household, ctx.tax).upsert(kind, payload)
    request.state.revision = ctx.revision
    return {**result, "revision": ctx.revision}


@router.patch("/manage/{kind}/{record_id}")
def update_record(kind: str, record_id: str, payload: dict, request: Request, p: P, r: R):
    with r.transaction(p, f"{kind}.update", revision(request)) as ctx:
        result = WorkspaceService(ctx.household, ctx.tax).upsert(kind, payload, record_id)
    request.state.revision = ctx.revision
    return {**result, "revision": ctx.revision}


@router.post("/transactions/preview")
def preview_transactions(payload: dict, p: P, r: R):
    ctx = r.read(p)
    return WorkspaceService(ctx.household, ctx.tax).preview_transactions(payload)


@router.post("/transactions/import")
def import_transactions(payload: dict, request: Request, p: P, r: R):
    with r.transaction(p, "transactions.import", revision(request)) as ctx:
        result = WorkspaceService(ctx.household, ctx.tax).import_transactions(payload)
    request.state.revision = ctx.revision
    return {**result, "revision": ctx.revision}


@router.patch("/transactions/{transaction_id}")
def update_transaction(transaction_id: str, payload: dict, request: Request, p: P, r: R):
    with r.transaction(p, "transaction.correct", revision(request)) as ctx:
        result = WorkspaceService(ctx.household, ctx.tax).update_transaction(transaction_id, payload)
    request.state.revision = ctx.revision
    return {**result, "revision": ctx.revision}


# Search filters travel in the JSON body so search text never appears in URLs or request logs.
class TransactionSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str | None = Field(None, max_length=200)
    q: str = Field("", max_length=200)
    category: str | None = Field(None, max_length=100)
    limit: int = Field(50, ge=1, le=200)
    offset: int = Field(0, ge=0, le=1000000)


@router.post("/transactions/search")
def transactions(body: TransactionSearch, p: P, r: R):
    filters = [TransactionRow.household_id == p.household_id]
    if body.account_id:
        filters.append(TransactionRow.account_id == body.account_id)
    if body.q:
        filters.append(TransactionRow.description.icontains(body.q, autoescape=True))
    if body.category:
        filters.append(TransactionRow.category == body.category)
    with r.db.sessions() as session:
        total = session.scalar(select(func.count()).select_from(TransactionRow).where(*filters))
        rows = session.execute(select(TransactionRow).where(*filters).order_by(
            TransactionRow.posted_on.desc(), TransactionRow.id.desc()).offset(body.offset).limit(body.limit)).scalars()
        return {"transactions": [row.payload for row in rows], "total": total, "limit": body.limit, "offset": body.offset}


@router.get("/audit")
def audit(p: P, r: R, limit: int = Query(50, ge=1, le=200)):
    with r.db.sessions() as session:
        rows = session.execute(select(AuditRow).where(AuditRow.household_id == p.household_id)
            .order_by(AuditRow.at.desc()).limit(limit)).scalars()
        return {"events": [{"id": row.id, "action": row.action, "revision": row.revision,
                           "actor_id": row.actor_id, "at": row.at.replace(tzinfo=timezone.utc).isoformat()} for row in rows]}
