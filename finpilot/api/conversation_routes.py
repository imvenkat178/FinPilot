"""Chat is an authenticated application surface, not a privileged API bypass."""
from fastapi import APIRouter, HTTPException, Query, Request

from ..conversation.contracts import ActionBatch, ConfirmIn, MessageIn, NewConversation
from ..conversation.service import ConversationService
from .dependencies import P, R

router = APIRouter(prefix="/api/conversations", tags=["Conversations"])


class ProposalIn(MessageIn):
    batch: ActionBatch


@router.post("", status_code=201)
def create(body: NewConversation, p: P, r: R):
    r.auth.throttle("chat.create:" + p.user_id, limit=20, seconds=60)
    return ConversationService(r, p).create(body)


@router.get("")
def conversations(p: P, r: R, limit: int = Query(30, ge=1, le=100)):
    return ConversationService(r, p).list(limit)


@router.get("/{conversation_id}")
def conversation(conversation_id: str, p: P, r: R, limit: int = Query(50, ge=1, le=100),
                 before: int | None = Query(None, ge=1)):
    return ConversationService(r, p).get(conversation_id, limit, before)


@router.post("/{conversation_id}/messages")
def message(conversation_id: str, body: MessageIn, request: Request, p: P, r: R):
    r.auth.throttle("chat.message:" + p.user_id, limit=30, seconds=60)
    if not r.ai_slots.acquire(blocking=False):
        raise HTTPException(429, "The assistant is busy. Please try again shortly.")
    try:
        result = ConversationService(r, p).message(conversation_id, body)
        request.state.revision = result["workspace_revision"]
        return result
    finally:
        r.ai_slots.release()


@router.post("/{conversation_id}/proposals")
def proposal(conversation_id: str, body: ProposalIn, request: Request, p: P, r: R):
    r.auth.throttle("chat.preview:" + p.user_id, limit=30, seconds=60)
    result = ConversationService(r, p).message(conversation_id, body, explicit_batch=body.batch)
    request.state.revision = result["workspace_revision"]
    return result


@router.post("/{conversation_id}/proposals/{proposal_id}/confirm")
def confirm(conversation_id: str, proposal_id: str, body: ConfirmIn, request: Request, p: P, r: R):
    result = ConversationService(r, p).confirm(conversation_id, proposal_id, body)
    request.state.revision = result["workspace_revision"]
    return result


@router.post("/{conversation_id}/proposals/{proposal_id}/cancel")
def cancel(conversation_id: str, proposal_id: str, p: P, r: R):
    return ConversationService(r, p).cancel(conversation_id, proposal_id)
