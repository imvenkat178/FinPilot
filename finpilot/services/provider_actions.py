"""Durable claim around external work; uncertain outcomes are never auto-retried."""
from datetime import timedelta
from sqlalchemy import update
from starlette.concurrency import run_in_threadpool
from ..persistence.action_models import ActionProposalRow
from ..persistence.database import MembershipRow,utcnow
from ..runtime import RevisionConflict
from ..services.auth import AuthError
from .proposals import Replay, public

def claim(service,p,proposal_id,version):
    with service.r._lock(p.household_id),service.r.db.sessions.begin() as s:
        row=service.owned(s,p,proposal_id,True)
        service.check(s,p,row,version)
        op=row.operations[0]
        if op["capability"] in {"sync_bank","disconnect_bank"}:
            membership=s.get(MembershipRow,(p.user_id,p.household_id))
            if not membership or membership.role not in {"owner","approver"}:
                raise AuthError("This workspace role cannot manage bank connections.",403)
        changed=s.execute(update(ActionProposalRow).where(ActionProposalRow.id==row.id,
            ActionProposalRow.version==version,ActionProposalRow.status=="executing").values(status="running",expires_at=utcnow()+timedelta(minutes=3)))
        if not changed.rowcount:
            raise RevisionConflict("This action has already started.")
        return public(row)

def complete(service,p,proposal_id,result,revision):
    with service.r.db.sessions.begin() as s:
        row=service.owned(s,p,proposal_id,True)
        receipt=service.receipt(s,row,[result],revision)
        return public(row,receipt)

def uncertain(service,p,proposal_id):
    with service.r.db.sessions.begin() as s:
        row=service.owned(s,p,proposal_id,True)
        row.status="outcome_unknown"

async def confirm_provider(service,p,proposal_id,version,request):
    initial=await run_in_threadpool(service.get,p,proposal_id)
    if initial["receipt"]:
        if initial["version"]!=version:
            raise RevisionConflict("Review the confirmed proposal version.")
        return initial
    current=await run_in_threadpool(service.r.read,p)
    if initial["revision"]!=current.revision:
        raise RevisionConflict("Your workspace changed. Refresh the proposal before confirming.")
    op=initial["operations"][0]
    if op["capability"]=="import_mcp":
        # A revoked allowlist is a known precondition failure, not an uncertain
        # remote outcome. Check before acquiring the execution identity.
        await run_in_threadpool(service.validate_mcp_import,p,op["arguments"])
    try:
        await run_in_threadpool(claim,service,p,proposal_id,version)
    except Replay as replay:
        return replay.result
    op=initial["operations"][0];args=op["arguments"]
    try:
        if op["capability"] in {"sync_bank","disconnect_bank"}:
            result=await run_in_threadpool(bank_call,service,p,op,request,initial)
            revision=result["revision"]
        elif op["capability"]=="import_mcp":
            from ..api.mcp_routes import ImportBody, import_result
            body=ImportBody.model_validate({k:v for k,v in args.items() if k!="record_id"})
            result=await import_result(args["record_id"],body,request,service.r,p)
            revision=current.revision
        else:
            raise ValueError("Unknown provider capability")
        return await run_in_threadpool(complete,service,p,proposal_id,result,revision)
    except Exception:
        await run_in_threadpool(uncertain,service,p,proposal_id)
        raise

def bank_call(service,p,op,request,initial):
    from ..api.bank_routes import provider,public_connection
    from .bank_operations import owned,sync,disconnect
    target=initial["preview"]["entries"][0].get("target") or {}
    with service.r.db.sessions() as s:
        current=owned(s,p,op["arguments"]["record_id"])
        if current.version!=target.get("version"):
            raise RevisionConflict("This bank connection changed. Review it again.")
    with provider(request) as client:
        if op["capability"]=="sync_bank":
            row,imported,revision=sync(service.r,p,client,op["arguments"]["record_id"],initial["revision"])
            return {"connection":public_connection(row),"imported":imported,"revision":revision}
        row,revision=disconnect(service.r,p,client,op["arguments"]["record_id"],initial["revision"],target.get("version"))
        return {"connection":public_connection(row),"revision":revision}