"""Transactional application runtime, independent of HTTP and browser state."""
from __future__ import annotations

import copy
import os
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select, update, delete
from sqlalchemy.exc import OperationalError

from .persistence.codec import decode, encode
from .persistence.database import (Database, HouseholdRow, MembershipRow, AuditRow,
                                   ChatRow, TransactionRow, utcnow)
from .services.auth import AuthService, AuthError
from .ai.llm import LocalLLM, LLMConfig
from .ai.graph import FinanceAgent
from .ai.wording_cache import WordingCache
from .ai.tools import ToolRegistry
from .execution.engine import ExecutionEngine, SimulatedProvider
from .api.workspace import _encode, workspace_data


class RevisionConflict(ValueError):
    pass


@dataclass
class WorkspaceContext:
    household: object
    tax: object
    execution: ExecutionEngine
    revision: int

    @property
    def registry(self):
        return ToolRegistry(self.household, self.tax, self.execution)

    def snapshot(self):
        ex = self.execution
        return encode({"household": self.household, "tax": self.tax,
            "groups": ex.groups, "reservations": ex.reservations, "paused": ex.paused,
            "provider_submitted": ex.provider.submitted,
            "provider_faults": ex.provider.faults, "provider_calls": ex.provider.calls[-1000:]})

    @classmethod
    def from_row(cls, row):
        value = decode(row.snapshot)
        if getattr(value["household"], "live_dates", False):
            value["household"].as_of = date.today()
        provider = SimulatedProvider()
        provider.submitted = value.get("provider_submitted", {})
        provider.faults = value.get("provider_faults", {})
        provider.calls = value.get("provider_calls", [])
        ex = ExecutionEngine(value["household"], provider)
        ex.groups = value.get("groups", {})
        ex.reservations = value.get("reservations", {})
        ex.paused = value.get("paused", False)
        return cls(value["household"], value["tax"], ex, row.revision)


class Runtime:
    def __init__(self, database=None, llm=None):
        self.db = database or Database()
        self.auth = AuthService(self.db)
        self.llm = llm or LocalLLM(LLMConfig())
        self._provided_llm = llm is not None
        self._guard = threading.RLock()
        self._locks = [threading.RLock() for _ in range(128)]
        self._cache = OrderedDict()
        # Verified model wording and validated plans, never financial results.
        self.wording_cache = WordingCache()
        self._bootstrap_cache = OrderedDict()
        self._ready = False
        self._model_started = False
        self._closed = False
        self.ai_slots = threading.BoundedSemaphore(4)

    def initialize(self):
        with self._guard:
            if self._ready:
                return
            if os.getenv("FINPILOT_ENV") == "production" and self.db.engine.dialect.name != "postgresql":
                raise RuntimeError("Hosted production requires FINPILOT_DATABASE_URL for PostgreSQL.")
            if os.getenv("FINPILOT_ENV") != "production":
                self.db.initialize()
            self._ready = True

    def start_model(self):
        """Run slow local-model discovery outside the financial request path."""
        with self._guard:
            if self._model_started:
                return
            self._model_started = True
        def warm():
            candidate = self.llm if self._provided_llm else LocalLLM()
            with self._guard:
                if self._closed:
                    candidate.close()
                    return
                previous, self.llm = self.llm, candidate
            if previous is not candidate:
                previous.close()
            candidate.health()
        threading.Thread(target=warm, name="finpilot-model-health", daemon=True).start()

    def close(self):
        with self._guard:
            self._closed = True
            model = self.llm
        close = getattr(model, "close", None)
        if close:
            close()
        self.db.close()

    def _lock(self, household_id):
        return self._locks[hash(household_id) % len(self._locks)]

    def read(self, principal):
        self.initialize()
        with self.db.sessions() as session:
            row = session.execute(select(HouseholdRow).join(MembershipRow,
                MembershipRow.household_id == HouseholdRow.id).where(
                HouseholdRow.id == principal.household_id,
                MembershipRow.user_id == principal.user_id)).scalar_one_or_none()
            if not row:
                raise AuthError("Workspace not found.", 404)
            return WorkspaceContext.from_row(row)

    @contextmanager
    def transaction(self, principal, action, expected_revision=None):
        self.initialize()
        with self._lock(principal.household_id), self.db.sessions.begin() as session:
            # PostgreSQL serializes a household's writers across processes;
            # the revision predicate is an additional stale-client safeguard.
            record = session.execute(select(HouseholdRow, MembershipRow.role).join(MembershipRow,
                MembershipRow.household_id == HouseholdRow.id).where(
                HouseholdRow.id == principal.household_id,
                MembershipRow.user_id == principal.user_id).with_for_update(
                    of=(HouseholdRow, MembershipRow))).one_or_none()
            if not record:
                raise AuthError("Workspace not found.", 404)
            row, current_role = record
            # A principal can outlive a role change while waiting for model or
            # bank I/O. Authority is checked at commitment under the same lock.
            if current_role not in ("owner", "approver"):
                raise AuthError("This workspace role cannot make changes.", 403)
            if expected_revision is not None and row.revision != expected_revision:
                raise RevisionConflict("Your workspace changed in another tab. Refresh and review your change again.")
            ctx = WorkspaceContext.from_row(row)
            ctx.db_session = session
            previous_revision = row.revision
            # Transaction fields are immutable scalars, dates, enums and Money.
            # Capture cheap values so in-place provider corrections are detected
            # without fetching or serializing the whole SQL projection.
            original_transactions = {tx.id: vars(tx).copy() for tx in ctx.household.transactions}
            yield ctx
            snapshot = ctx.snapshot()
            result = session.execute(update(HouseholdRow).where(
                HouseholdRow.id == row.id, HouseholdRow.revision == previous_revision).values(
                name=ctx.household.name, snapshot=snapshot,
                revision=previous_revision + 1, updated_at=utcnow()))
            if result.rowcount != 1:
                raise RevisionConflict("This workspace was updated concurrently. Refresh and retry.")
            self._sync_transactions(session, ctx.household, original_transactions)
            session.add(AuditRow(id=uuid.uuid4().hex, household_id=row.id,
                actor_id=principal.user_id, action=action, revision=previous_revision + 1))
            ctx.revision = previous_revision + 1
        # A failed commit never invalidates or publishes a new version.
        with self._guard:
            for cache in (self._cache, self._bootstrap_cache):
                for key in list(cache):
                    if key[0] == principal.household_id:
                        cache.pop(key, None)

    def _sync_transactions(self, session, hh, original):
        changed = [tx for tx in hh.transactions if original.get(tx.id) != vars(tx)]
        removed = set(original) - {tx.id for tx in hh.transactions}
        # Bound SQL parameter counts for SQLite as well as PostgreSQL.
        for offset in range(0, len(removed), 500):
            session.execute(delete(TransactionRow).where(TransactionRow.household_id == hh.id,
                TransactionRow.id.in_(list(removed)[offset:offset + 500])))
        for offset in range(0, len(changed), 500):
            batch = changed[offset:offset + 500]
            existing = {x.id: x for x in session.execute(select(TransactionRow).where(
                TransactionRow.household_id == hh.id,
                TransactionRow.id.in_([tx.id for tx in batch]))).scalars()}
            for tx in batch:
                payload = _encode(tx)
                row = existing.get(tx.id)
                if row and row.payload == payload:
                    continue
                if row is None:
                    row = TransactionRow(household_id=hh.id, id=tx.id)
                    session.add(row)
                row.account_id, row.posted_on = tx.account_id, tx.date.isoformat()
                row.category, row.description, row.payload = tx.category or "", tx.description or "", payload

    def bootstrap(self, principal):
        """Validate membership/revision before reusing an immutable wire view.

        A cache hit never loads the large aggregate JSON. Cross-process writes
        are visible through the database revision; calendar rollover changes the
        key even when a live household has no writes overnight.
        """
        self.initialize()
        with self.db.sessions() as session:
            revision = session.execute(select(HouseholdRow.revision).join(MembershipRow,
                MembershipRow.household_id == HouseholdRow.id).where(
                HouseholdRow.id == principal.household_id,
                MembershipRow.user_id == principal.user_id)).scalar_one_or_none()
        if revision is None:
            raise AuthError("Workspace not found.", 404)
        today = date.today().isoformat()
        key = (principal.household_id, revision, today)
        with self._guard:
            cached = self._bootstrap_cache.get(key)
            if cached is not None:
                self._bootstrap_cache.move_to_end(key)
                data = copy.deepcopy(cached)
            else:
                data = None
        if data is None:
            ctx = self.read(principal)
            data = {"revision": ctx.revision, "dashboard": self.dashboard(principal, ctx),
                    "workspace": self.workspace(ctx)}
            # read() may have observed a newer commit than the cheap revision read.
            key = (principal.household_id, ctx.revision, today)
            data["dashboard"].pop("llm", None)
            with self._guard:
                self._bootstrap_cache[key] = copy.deepcopy(data)
                while len(self._bootstrap_cache) > 128:
                    self._bootstrap_cache.popitem(last=False)
        data["dashboard"]["llm"] = self.model_status()
        return data

    def dashboard(self, principal, ctx=None):
        ctx = ctx or self.read(principal)
        key = (principal.household_id, ctx.revision, str(ctx.household.as_of))
        with self._guard:
            cached = self._cache.get(key)
            if cached:
                self._cache.move_to_end(key)
                data = copy.deepcopy(cached)
                data["llm"] = self.model_status()
                return data
        reg, hh = ctx.registry, ctx.household
        y, m = hh.as_of.year, hh.as_of.month
        data = {"household": hh.name, "as_of": hh.as_of.isoformat(),
            "revision": ctx.revision, "overview": reg.get_money_overview(),
            "plan": reg.get_paycheck_plan(y, m), "forecast": reg.get_cash_forecast(days=45),
            "bills": reg.get_upcoming_obligations(days=30),
            "allowance": reg.get_spending_allowance(days=14), "buffer": reg.get_buffer(),
            "debt": reg.compare_debt_strategies(), "coverage": reg.get_deposit_coverage(),
            "liquidity": reg.get_liquidity_tiers(), "automation": reg.get_automation_status(),
            "connections": reg.get_account_connections(),
            "recurring_activity": reg.get_recurring_activity(horizon_days=45)}
        with self._guard:
            self._cache[key] = copy.deepcopy(data)
            while len(self._cache) > 128:
                self._cache.popitem(last=False)
        data["llm"] = self.model_status()
        return data

    def model_status(self):
        status = self.llm.status() if hasattr(self.llm, "status") else {"reachable": self.llm.available}
        return {k: v for k, v in status.items() if k in (
            "reachable", "model", "runtime", "reason", "checked_at", "cached", "stale")}

    def workspace(self, ctx):
        data = workspace_data(ctx.household, ctx.tax, transaction_limit=100)
        data["revision"] = ctx.revision
        count = len(ctx.household.transactions)
        data["transaction_count"] = count
        data["transactions_truncated"] = count > 100
        return data

    def record_answer(self, principal, revision, result):
        answer_id = "ans_" + uuid.uuid4().hex
        # Financial snapshot locks are never held during model inference.
        with self.db.sessions.begin() as session:
            session.add(ChatRow(id=answer_id, household_id=principal.household_id,
                user_id=principal.user_id, revision=revision, result=result))
        return answer_id
