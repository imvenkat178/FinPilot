"""Conversation persistence, optimistic turn ordering, and atomic confirmations."""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import timedelta, timezone

from sqlalchemy import select

from ..persistence.database import (ActionProposalRow, ConversationRow, ConversationTurnRow,
                                    MembershipRow, utcnow)
from ..runtime import RevisionConflict
from ..services.auth import AuthError
from .actions import apply_operation, prepare_actions
from .contracts import ActionBatch, Plan, Scope
from .planner import infer_scope, make_plan
from .queries import read_query, resolve_scope, scope_json


def identifier():
    return uuid.uuid4().hex


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def timestamp(value):
    return value.replace(tzinfo=timezone.utc).isoformat()


def proposal_digest(row):
    return digest({"contract_version": 1, "id": row.id, "conversation_id": row.conversation_id,
                   "household_id": row.household_id, "user_id": row.user_id,
                   "workspace_revision": row.workspace_revision, "payload": row.payload, "preview": row.preview,
                   "expires_at": timestamp(row.expires_at)})


def proposal_json(row, revision=None):
    state = row.state
    if state == "pending":
        if row.expires_at.replace(tzinfo=timezone.utc) <= utcnow():
            state = "expired"
        elif revision is not None and revision != row.workspace_revision:
            state = "stale"
    return {"id": row.id, "digest": row.digest, "state": state,
            "workspace_revision": row.workspace_revision, "expires_at": timestamp(row.expires_at),
            "operations": row.payload["batch"]["operations"], "preview": row.preview,
            "requires_simulation_confirmation": any(o["op"] == "simulate_group" for o in row.payload["batch"]["operations"]),
            "receipt": row.receipt}


class AlreadyApplied(Exception):
    def __init__(self, receipt):
        self.receipt = receipt


class ConversationService:
    def __init__(self, runtime, principal):
        self.r, self.p = runtime, principal

    def _thread(self, session, thread_id, *, lock=False):
        if lock:
            # Confirmations enter with membership already locked by the financial
            # UoW. Message commits must acquire it before the thread too: locking
            # both through a join leaves row-lock order to the query plan and can
            # deadlock against a confirmation in another PostgreSQL worker.
            membership = session.execute(select(MembershipRow).where(
                MembershipRow.household_id == self.p.household_id,
                MembershipRow.user_id == self.p.user_id).with_for_update()).scalar_one_or_none()
            if membership is None:
                raise AuthError("Conversation not found.", 404)
        statement = select(ConversationRow).join(MembershipRow,
            MembershipRow.household_id == ConversationRow.household_id).where(
            ConversationRow.id == thread_id, ConversationRow.household_id == self.p.household_id,
            ConversationRow.user_id == self.p.user_id, MembershipRow.user_id == self.p.user_id)
        if lock:
            statement = statement.with_for_update(of=ConversationRow)
        row = session.execute(statement).scalar_one_or_none()
        if row is None:
            raise AuthError("Conversation not found.", 404)
        return row

    def _proposal(self, session, thread_id, proposal_id, *, lock=False):
        statement = select(ActionProposalRow).where(ActionProposalRow.id == proposal_id,
            ActionProposalRow.conversation_id == thread_id,
            ActionProposalRow.household_id == self.p.household_id, ActionProposalRow.user_id == self.p.user_id)
        if lock:
            statement = statement.with_for_update()
        row = session.execute(statement).scalar_one_or_none()
        if row is None:
            raise AuthError("Action preview not found.", 404)
        return row

    @staticmethod
    def summary(row):
        return {"id": row.id, "title": row.title, "revision": row.revision,
                "scope": row.context.get("scope", {}), "updated_at": timestamp(row.updated_at)}

    def create(self, body):
        ctx = self.r.read(self.p)
        resolve_scope(ctx.household, body.scope)
        with self.r.db.sessions.begin() as session:
            row = ConversationRow(id=identifier(), household_id=self.p.household_id, user_id=self.p.user_id,
                title=body.title, revision=0, context={"scope": body.scope.model_dump()},
                created_at=utcnow(), updated_at=utcnow())
            session.add(row)
        return self.summary(row)

    def list(self, limit=30):
        self.r.read(self.p)
        with self.r.db.sessions() as session:
            rows = session.execute(select(ConversationRow).where(ConversationRow.household_id == self.p.household_id,
                ConversationRow.user_id == self.p.user_id).order_by(ConversationRow.updated_at.desc()).limit(limit)).scalars()
            return {"conversations": [self.summary(row) for row in rows]}

    def _hydrate(self, session, response, revision):
        response = dict(response)
        if response.get("proposal"):
            row = self._proposal(session, response["conversation_id"], response["proposal"]["id"])
            response["proposal"] = proposal_json(row, revision)
        return response

    def get(self, thread_id, limit=50, before=None):
        ctx = self.r.read(self.p)
        with self.r.db.sessions() as session:
            row = self._thread(session, thread_id)
            query = select(ConversationTurnRow).where(ConversationTurnRow.conversation_id == row.id)
            if before is not None:
                query = query.where(ConversationTurnRow.sequence < before)
            turns = list(session.execute(query.order_by(ConversationTurnRow.sequence.desc()).limit(limit + 1)).scalars())
            return {**self.summary(row), "scope": scope_json(ctx.household, Scope.model_validate(row.context["scope"])),
                    "has_more": len(turns) > limit,
                    "turns": [{"id": t.id, "sequence": t.sequence, "question": t.question, "at": timestamp(t.at),
                               "response": self._hydrate(session, t.response, ctx.revision)} for t in reversed(turns[:limit])]}

    def _new_proposal(self, thread_id, ctx, batch):
        normalized, preview = prepare_actions(ctx, batch, self.p.user_id)
        row = ActionProposalRow(id=identifier(), conversation_id=thread_id,
            household_id=self.p.household_id, user_id=self.p.user_id, workspace_revision=ctx.revision,
            payload={"batch": normalized.model_dump(mode="json"), "as_of": ctx.household.as_of.isoformat()},
            preview=preview, state="pending", receipt=None, created_at=utcnow(),
            expires_at=utcnow() + timedelta(minutes=15))
        row.digest = proposal_digest(row)
        return row

    def message(self, thread_id, body, *, explicit_batch=None):
        ctx = self.r.read(self.p)
        request_hash = digest({"question": body.question, "scope": body.scope.model_dump() if body.scope else None,
                               "batch": explicit_batch.model_dump(mode="json") if explicit_batch else None})
        with self.r.db.sessions() as session:
            thread = self._thread(session, thread_id)
            existing = session.execute(select(ConversationTurnRow).where(ConversationTurnRow.conversation_id == thread_id,
                ConversationTurnRow.client_message_id == body.client_message_id)).scalar_one_or_none()
            if existing:
                if existing.request_digest != request_hash:
                    raise RevisionConflict("This message identifier was already used for a different request")
                return self._hydrate(session, existing.response, ctx.revision)
            version, context = thread.revision, dict(thread.context)
            if body.conversation_revision is not None and body.conversation_revision != version:
                raise RevisionConflict("This conversation changed. Reload it before sending another message")
            history = list(session.execute(select(ConversationTurnRow).where(ConversationTurnRow.conversation_id == thread_id)
                .order_by(ConversationTurnRow.sequence.desc()).limit(6)).scalars())
            history = [{"question": t.question, "answer": t.response.get("answer", "")[:1200]} for t in reversed(history)]
        if version >= 2000:
            raise ValueError("Start a new conversation to continue; this conversation reached its turn limit")
        active = body.scope or Scope.model_validate(context.get("scope", {}))
        resolve_scope(ctx.household, active)
        # Action source/destination names are command parameters, not automatic
        # account-context changes. Only read questions infer a viewing scope.
        context["scope"] = active.model_dump()
        if explicit_batch:
            plan, used_model = Plan(kind="action", action=explicit_batch), False
        else:
            plan, used_model = make_plan(ctx, body.question, context, history, self.r.llm)
        if plan.kind == "read":
            active, ambiguity = infer_scope(ctx.household, body.question, active)
            if ambiguity:
                plan = Plan(kind="clarify", clarification=ambiguity)
        response = {"contract_version": 1, "conversation_id": thread_id, "conversation_revision": version + 1,
                    "workspace_revision": ctx.revision, "question": body.question,
                    "scope": scope_json(ctx.household, active), "used_model": used_model,
                    "components": [], "state": "informational"}
        proposal = None
        try:
            if plan.kind == "read":
                response.update(read_query(ctx, active, plan.query))
            elif plan.kind == "action":
                proposal = self._new_proposal(thread_id, ctx, plan.action)
                plan.action = ActionBatch.model_validate(proposal.payload["batch"])
                response.update(answer="Review this exact change. Nothing has been applied. Confirm below to apply it, or tell me what to change.",
                                state="awaiting_confirmation", proposal=proposal_json(proposal))
            elif plan.kind == "form":
                collection = {"income": "income_sources"}.get(plan.form_collection, plan.form_collection)
                if plan.record_id and collection != "tax" and plan.record_id not in getattr(ctx.household, collection):
                    raise ValueError("That record is not in this workspace")
                response.update(answer="Fill in the missing details here. I will show a preview before saving.",
                                state="needs_input", components=[{"type": "form", "collection": plan.form_collection, "record_id": plan.record_id}])
            elif plan.kind == "capability":
                messages = {"connect_bank": "Connect a bank using its secure sign-in. This imports supported balances and transactions; it does not enable payments.",
                            "live_payments": "Live transfers are not configured in this release. You can save rules, prepare payment drafts, and run simulated payments in a sample workspace.",
                            "help": "Ask about an account, compare spending, visualize a paycheck split, or create and change records here. Changes always have a preview and a separate confirmation."}
                response.update(answer=messages[plan.capability], components=[{"type": "capability", "name": plan.capability}])
            else:
                response.update(answer=plan.clarification, state="needs_input")
                if context.get("pending_proposal_id"):
                    with self.r.db.sessions() as session:
                        pending = self._proposal(session, thread_id, context["pending_proposal_id"])
                        response["proposal"] = proposal_json(pending, ctx.revision)
        except (ValueError, KeyError) as exc:
            # A failed plan is a durable clarification, never a success receipt.
            proposal = None
            response.update(answer=str(exc).strip("'"), state="needs_input", components=[])
        with self.r._lock(self.p.household_id), self.r.db.sessions.begin() as session:
            row = self._thread(session, thread_id, lock=True)
            replay = session.execute(select(ConversationTurnRow).where(ConversationTurnRow.conversation_id == thread_id,
                ConversationTurnRow.client_message_id == body.client_message_id)).scalar_one_or_none()
            if replay:
                if replay.request_digest != request_hash:
                    raise RevisionConflict("This message identifier was already used for a different request")
                return self._hydrate(session, replay.response, ctx.revision)
            if row.revision != version:
                raise RevisionConflict("Another message finished first. Reload the conversation and try again")
            if proposal:
                prior_id = context.get("pending_proposal_id")
                if prior_id:
                    prior = self._proposal(session, thread_id, prior_id, lock=True)
                    if prior.state == "pending":
                        prior.state = "superseded"
                session.add(proposal)
                context["pending_proposal_id"] = proposal.id
            context["scope"] = active.model_dump()
            if response["state"] != "needs_input" and plan.kind in {"read", "action"}:
                context["last_plan"] = plan.model_dump(mode="json")
            row.context, row.revision, row.updated_at = context, version + 1, utcnow()
            if version == 0 and row.title == "New conversation":
                row.title = body.question[:120]
            session.add(ConversationTurnRow(id=identifier(), conversation_id=thread_id,
                client_message_id=body.client_message_id, request_digest=request_hash,
                sequence=version + 1, question=body.question, response=response))
        return response

    def confirm(self, thread_id, proposal_id, body):
        # Financial mutation, confirmation consumption, audit and receipt commit
        # together. PostgreSQL household/proposal row locks work across workers.
        try:
            with self.r.transaction(self.p, "conversation.confirm") as ctx:
                thread = self._thread(ctx.db_session, thread_id, lock=True)
                row = self._proposal(ctx.db_session, thread_id, proposal_id, lock=True)
                if not hmac.compare_digest(row.digest, body.digest) or not hmac.compare_digest(row.digest, proposal_digest(row)):
                    raise ValueError("The preview fingerprint does not match; reload and review it")
                if row.state == "applied":
                    raise AlreadyApplied(row.receipt)
                if row.state != "pending":
                    raise RevisionConflict("This preview is no longer pending. Request a fresh preview")
                if row.expires_at.replace(tzinfo=timezone.utc) <= utcnow():
                    raise RevisionConflict("This preview expired. Request a fresh preview")
                if row.workspace_revision != ctx.revision or row.payload["as_of"] != ctx.household.as_of.isoformat():
                    raise RevisionConflict("Your financial data changed. Request a fresh preview before confirming")
                batch = ActionBatch.model_validate(row.payload["batch"])
                if any(o.op == "simulate_group" for o in batch.operations) and not body.confirm_simulation:
                    raise ValueError("Explicitly confirm the sample payment simulation")
                results = [apply_operation(ctx, o, self.p.user_id) for o in batch.operations]
                context = dict(thread.context)
                if context.get("pending_proposal_id") == row.id:
                    operations = batch.model_dump(mode="json")["operations"]
                    for operation, result in zip(operations, results):
                        if operation["op"] == "upsert" and operation["record_id"] is None:
                            operation["record_id"] = result["id"]
                    context["last_plan"] = Plan(kind="action", action=ActionBatch(operations=operations)).model_dump(mode="json")
                    context.pop("pending_proposal_id", None)
                    thread.context = context
                # A confirmation changes typed conversational references too.
                # In-flight messages must not overwrite those new bindings.
                thread.revision += 1
                thread.updated_at = utcnow()
                receipt = {"id": identifier(), "proposal_id": row.id, "state": "applied",
                           "conversation_revision": thread.revision,
                           "workspace_revision": ctx.revision + 1, "at": timestamp(utcnow()),
                           "results": results, "operations": batch.model_dump(mode="json")["operations"],
                           "payment_mode": "simulation" if ctx.household.payment_sandbox else "not_connected",
                           "note": "The reviewed commands were processed. Individual payment results carry their own status; no live payment provider is connected."}
                row.state, row.receipt = "applied", receipt
            return receipt
        except AlreadyApplied as replay:
            # Exception rolls back the no-op transaction; no revision increment.
            return replay.receipt

    def cancel(self, thread_id, proposal_id):
        self.r.read(self.p)
        with self.r._lock(self.p.household_id), self.r.db.sessions.begin() as session:
            self._thread(session, thread_id, lock=True)
            row = self._proposal(session, thread_id, proposal_id, lock=True)
            if row.state == "applied":
                raise RevisionConflict("This change has already been applied; request a separate change to amend it")
            row.state = "canceled"
            return proposal_json(row)
