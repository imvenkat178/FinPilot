"""Bounded AI requests, with tenant-bound tools and persistent answers."""
from datetime import date, timezone
import re
from typing import Optional, Literal
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from ..ai.graph import FinanceAgent, ToolChoice
from ..ai.tools import ToolRegistry
from ..ai.router import route
from ..ai.llm import start_inference_metrics, inference_metrics, stop_inference_metrics
from ..ai.guardrails import scope_check
from ..ai.knowledge import resolve_followup, memory_context, document_question, memory_question, source_answer
from ..services.conversations import ConversationService
from ..services.documents import DocumentService
from ..persistence.database import ChatRow
from .dependencies import P, R, revision
from .action_routes import OperationIn
from ..ai.input_safety import reject_credentials
from ..ai.workflows import run_workflow, mutating_request

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
    conversation_id: Optional[str] = Field(None, max_length=40)
    document_ids: Optional[list[str]] = Field(None, max_length=10)
    workflow_mode: Literal["auto", "model"] = "auto"
    workflow_input: Optional[list[OperationIn]] = Field(None, min_length=1, max_length=4)


class TenantTools(ToolRegistry):
    def __init__(self, ctx, runtime, principal):
        super().__init__(ctx.household, ctx.tax, ctx.execution)
        self.runtime, self.principal, self.revision = runtime, principal, ctx.revision
        self.changed = False

    def call(self, name, arguments=None):
        spec = self.spec(name)
        if not spec or not spec.read_only:
            return {"error": "Chat changes require a reviewed proposal.", "_tool": name}
        return super().call(name, arguments)


@router.post("/ask")
def ask(body: AskIn, request: Request, p: P, r: R):
    reject_credentials(body.question)
    reject_credentials(body.untrusted_context)
    if body.workflow_input:
        reject_credentials([x.model_dump() for x in body.workflow_input])
    r.auth.throttle("ask:" + p.user_id, limit=30, seconds=60)
    if not r.ai_slots.acquire(blocking=False):
        raise HTTPException(429, "The assistant is busy. Please try again shortly.")
    conversations, documents = ConversationService(r.db), DocumentService(r.db)
    generation_id = conversation_id = None
    metric_token = start_inference_metrics()
    try:
        ctx = r.read(p)
        saved = conversations.get(p, body.conversation_id) if body.conversation_id else {}
        viewing = body.context.model_dump() if body.context else (saved.get('last_context') or {'page':'overview','account_id':None})
        account_id = viewing.get('account_id')
        if account_id and account_id not in ctx.household.accounts:
            raise HTTPException(404, "Account not found in this workspace.")
        document_ids = body.document_ids if body.document_ids is not None else saved.get('document_ids', [])
        # Deleted selections are dropped when resuming; explicit foreign IDs fail closed.
        if body.document_ids is None:
            available = {d['id'] for d in documents.list(p)}
            document_ids = [d for d in document_ids if d in available]
        document_ids = documents.validate_ids(p, document_ids)
        conversation_id = body.conversation_id or conversations.create(p)['id']
        generation_id, memory = conversations.begin(p, conversation_id, body.question,
            {'viewing':viewing, 'document_ids':document_ids}, expected_version=saved.get('version'))
        tools = TenantTools(ctx, r, p)
        override = resolve_followup(body.question, memory, tools)
        sources = []
        is_document = document_question(body.question, document_ids) and not scope_check(body.question)
        pure_source = is_document and not mutating_request(body.question) and not re.search(r"\b(and|compare|versus|against)\b", body.question, re.I)
        pure_memory = memory_question(body.question) or bool(re.match(r"^remember\b", body.question, re.I))
        workflow = None
        if body.workflow_input or body.workflow_mode == "model" or not (pure_source or pure_memory):
            workflow = run_workflow(body.question, ctx, r, p, request, viewing, document_ids,
                conversation_id, generation_id, force_model=body.workflow_mode == "model",
                input_operations=[x.model_dump() for x in body.workflow_input] if body.workflow_input else None)
        if workflow is not None:
            result = {"question": body.question, **workflow}
        elif re.match(r"^remember(?:\s+that\b|\s*[:,])", body.question, re.I) and not scope_check(body.question):
            result = {'question':body.question, 'answer':"I will keep this in the current conversation. You can return to it from Conversations.",
                'state':'informational','intent':'memory_saved','tools_called':[], 'used_model':False,
                'grounding':{'ok':True,'method':'saved_user_context'},'guard':{},'assumptions':[],
                'confidence':'saved context','latency_ms':0,'trace':[], 'evidence':[]}
        elif is_document:
            search_question = body.question
            if memory and len(body.question.split()) < 8:
                search_question = memory[-1]['question'] + ' ' + body.question
            sources = documents.retrieve(p, search_question, document_ids=document_ids or None, limit=6)
            if not sources and document_ids and re.search(r"\b(summarize|summarise|summary|overview)\b", body.question, re.I):
                sources = documents.overview(p, document_ids, limit=6)
            result = source_answer(body.question, sources, r.llm)
        elif memory and memory_question(body.question) and not scope_check(body.question):
            memory_sources = [{'id':'memory', 'title':'Your prior message','page':None,
                'chunk_id':str(i), 'text':t['question']} for i,t in enumerate(memory[-6:])]
            result = source_answer(body.question, memory_sources, r.llm, kind='memory')
        else:
            import json
            context = json.dumps({'viewing': viewing, 'untrusted_text': body.untrusted_context})
            resolved = override or route(body.question)
            # The graph adds current viewing identifiers to an override in place.
            # Persist the language intent before that happens, not a stale account scope.
            remembered_route = {**resolved.to_json(), 'arguments': dict(resolved.arguments)}
            result = FinanceAgent(tools, r.llm, compile_graph=False, default_account_id=account_id,
                conversation_context=memory_context(memory), route_override=override
            ).ask(body.question, context, body.tool_choice).to_json()
            # Preserve only authorized read intent/arguments for later follow-up resolution.
            spec = tools.spec(resolved.tool)
            if spec and spec.read_only:
                result['resolved_route'] = remembered_route
            if override:
                result['resolved_question'] = f"Follow-up to: {memory[-1]['question']}"
        result['document_sources'] = result.get('document_sources', [])
        result['conversation_id'] = conversation_id
        result['answer_id'] = generation_id
        result['memory_turns'] = len(memory)
        result['document_ids'] = document_ids
        result["model_status"] = r.model_status()
        result["viewing"] = {"page": viewing['page'], "name": ctx.household.accounts[account_id].nickname
                             if account_id else "Your household"}
        result["revision"] = tools.revision
        result["workspace_changed"] = tools.changed
        result["generation"] = {"id":generation_id, "model":r.llm.config.model or None, "calls":inference_metrics()}
        conversations.finish(p, conversation_id, generation_id, result, tools.revision)
        request.state.revision = tools.revision
        if not body.include_evidence:
            result.pop("evidence", None)
        return result
    except Exception:
        if generation_id:
            conversations.fail(p, conversation_id, generation_id)
        raise
    finally:
        stop_inference_metrics(metric_token)
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
