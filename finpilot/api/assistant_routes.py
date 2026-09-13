"""Bounded AI requests, with tenant-bound tools and persistent answers."""
from datetime import date, timezone
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from ..ai.graph import FinanceAgent, ToolChoice
from ..ai.tools import ToolRegistry
from ..conversation.queries import SCOPED_TOOLS
from ..persistence.database import ChatRow
from .dependencies import P, R, revision

router = APIRouter(prefix="/api", tags=["Assistant"])


class ViewingContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: Optional[str] = Field(None, max_length=100)
    page: str = Field("overview", max_length=40)


class AskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=3000)
    untrusted_context: str = Field("", max_length=8000)
    context: Optional[ViewingContext] = None
    tool_choice: ToolChoice = ToolChoice.ROUTER
    include_evidence: bool = True


class TenantTools(ToolRegistry):
    def __init__(self, ctx, runtime, principal, account_id=None):
        super().__init__(ctx.household, ctx.tax, ctx.execution,
                         allowed_account_ids={account_id} if account_id else None)
        self.runtime, self.principal, self.revision = runtime, principal, ctx.revision
        self.changed = False

    def call(self, name, arguments=None):
        spec = self.spec(name)
        if name not in {"pause_recurring_policy", "skip_next_occurrence", "pay_bill_once"}:
            if self.scope and name not in SCOPED_TOOLS and name != "explain_product_boundary":
                return {"error": "This calculation needs household context. Open a conversation and explicitly select Your household."}
            if self.scope and spec and "account_id" in spec.parameters.get("properties", {}):
                arguments = dict(arguments or {})
                arguments.setdefault("account_id", next(iter(self.scope)))
            return super().call(name, arguments)
        return {"error": "Open a conversation to preview and confirm this change. The legacy question endpoint cannot change financial records."}


@router.post("/ask")
def ask(body: AskIn, request: Request, p: P, r: R):
    r.auth.throttle("ask:" + p.user_id, limit=30, seconds=60)
    if not r.ai_slots.acquire(blocking=False):
        raise HTTPException(429, "The assistant is busy. Please try again shortly.")
    try:
        ctx = r.read(p)
        if body.context and body.context.account_id and body.context.account_id not in ctx.household.accounts:
            raise HTTPException(404, "Account not found in this workspace.")
        tools = TenantTools(ctx, r, p, body.context.account_id if body.context else None)
        context = body.untrusted_context
        if body.context:
            import json
            context = json.dumps({"viewing": body.context.model_dump(), "untrusted_text": context})
        result = FinanceAgent(tools, r.llm, compile_graph=False).ask(body.question, context, body.tool_choice).to_json()
        result["viewing"] = {"page": body.context.page if body.context else "overview",
            "name": ctx.household.accounts[body.context.account_id].nickname
            if body.context and body.context.account_id else "Your household"}
        result["revision"] = tools.revision
        result["workspace_changed"] = tools.changed
        result["answer_id"] = r.record_answer(p, tools.revision, result)
        request.state.revision = tools.revision
        if not body.include_evidence:
            result.pop("evidence", None)
        return result
    finally:
        r.ai_slots.release()


@router.get("/ask/history")
def history(p: P, r: R, limit: int = Query(20, ge=1, le=50)):
    with r.db.sessions() as session:
        rows = session.execute(select(ChatRow).where(ChatRow.household_id == p.household_id,
            ChatRow.user_id == p.user_id).order_by(ChatRow.at.desc()).limit(limit)).scalars()
        return {"answers": [{"id": row.id, "at": row.at.replace(tzinfo=timezone.utc).isoformat(), "revision": row.revision,
                             **row.result} for row in rows]}


@router.get("/ask/suggestions")
def suggestions(p: P):
    return {"suggestions": ["How should I split my next paycheck?", "How much can I spend this week?",
                            "Which of my cards should I use for an $80 dinner?"]}


@router.get("/llm/health")
def llm_health(p: P, r: R):
    # Configuration belongs to the operator; users cannot change a shared URL.
    return r.model_status()
