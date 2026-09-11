"""Payment execution and operational recovery -- spec section 21.

The lifecycle is durable and explicit:

    draft -> awaiting_authorization -> authorized -> scheduled -> validating ->
    submitted -> processing -> funds_available -> credited_by_biller ->
    reconciled, with canceled, failed, returned and outcome_unknown as terminal
    or recoverable branches.

"Provider acceptance is not proof that a creditor received or correctly applied
a payment, and initial settlement is not a promise that no later return is
possible."

Two invariants are enforced mechanically rather than by convention:
  * an idempotency key means a retry never creates a second payment; and
  * an outcome_unknown leg blocks any replacement payment until it resolves.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Callable, Optional

from ..models import (Account, AuthorizationMode, Capability, Household,
                      Mandate, PolicyPurpose, _id, now)
from ..money import Money, msum


class LegState(str, Enum):
    DRAFT = "draft"
    AWAITING_AUTHORIZATION = "awaiting_authorization"
    AUTHORIZED = "authorized"
    SCHEDULED = "scheduled"
    VALIDATING = "validating"
    SUBMITTED = "submitted"
    PROCESSING = "processing"
    FUNDS_AVAILABLE = "funds_available"
    CREDITED_BY_BILLER = "credited_by_biller"
    RECONCILED = "reconciled"
    CANCELED = "canceled"
    FAILED = "failed"
    RETURNED = "returned"
    OUTCOME_UNKNOWN = "outcome_unknown"


TERMINAL = {LegState.RECONCILED, LegState.CANCELED, LegState.FAILED}
BLOCKS_REPLACEMENT = {LegState.SUBMITTED, LegState.PROCESSING,
                      LegState.OUTCOME_UNKNOWN}

ALLOWED: dict[LegState, set[LegState]] = {
    LegState.DRAFT: {LegState.AWAITING_AUTHORIZATION, LegState.AUTHORIZED,
                     LegState.CANCELED},
    LegState.AWAITING_AUTHORIZATION: {LegState.AUTHORIZED, LegState.CANCELED},
    LegState.AUTHORIZED: {LegState.SCHEDULED, LegState.VALIDATING, LegState.CANCELED},
    LegState.SCHEDULED: {LegState.VALIDATING, LegState.CANCELED},
    LegState.VALIDATING: {LegState.SUBMITTED, LegState.FAILED, LegState.CANCELED},
    LegState.SUBMITTED: {LegState.PROCESSING, LegState.FAILED,
                         LegState.OUTCOME_UNKNOWN},
    LegState.PROCESSING: {LegState.FUNDS_AVAILABLE, LegState.FAILED,
                          LegState.RETURNED, LegState.OUTCOME_UNKNOWN},
    LegState.FUNDS_AVAILABLE: {LegState.CREDITED_BY_BILLER, LegState.RECONCILED,
                               LegState.RETURNED},
    LegState.CREDITED_BY_BILLER: {LegState.RECONCILED, LegState.RETURNED},
    LegState.RECONCILED: {LegState.RETURNED},   # a return can still arrive
    LegState.OUTCOME_UNKNOWN: {LegState.PROCESSING, LegState.FUNDS_AVAILABLE,
                               LegState.FAILED, LegState.RETURNED,
                               LegState.CREDITED_BY_BILLER, LegState.RECONCILED},
    LegState.RETURNED: {LegState.RECONCILED},
    LegState.FAILED: set(),
    LegState.CANCELED: set(),
}


class LegKind(str, Enum):
    INTERNAL_TRANSFER = "internal_transfer"
    BILL_PAYMENT = "bill_payment"
    CARD_STATEMENT = "card_statement"
    EXTRA_PRINCIPAL = "extra_principal"
    INVESTMENT_FUNDING = "investment_funding"
    RESERVE_FUNDING = "reserve_funding"


@dataclass
class AuditEvent:
    at: datetime
    state: LegState
    detail: str
    provider_ref: Optional[str] = None

    def to_json(self) -> dict:
        return {"at": self.at.isoformat(), "state": self.state.value,
                "detail": self.detail, "provider_ref": self.provider_ref}


@dataclass
class PaymentLeg:
    id: str = field(default_factory=lambda: _id("leg"))
    group_id: str = ""
    kind: LegKind = LegKind.INTERNAL_TRANSFER
    source_account_id: str = ""
    destination_account_id: str = ""
    destination_label: str = ""
    amount: Money = field(default_factory=lambda: Money.zero())
    purpose: PolicyPurpose = PolicyPurpose.GOAL
    scheduled_for: Optional[date] = None
    entity_id: Optional[str] = None
    mandate: Optional[Mandate] = None
    depends_on: list[str] = field(default_factory=list)
    optional: bool = False
    fee: Money = field(default_factory=lambda: Money.zero())
    idempotency_key: str = ""
    provider_ref: Optional[str] = None
    state: LegState = LegState.DRAFT
    audit: list[AuditEvent] = field(default_factory=list)
    failure_reason: Optional[str] = None
    principal_only: bool = False

    def __post_init__(self):
        if not self.idempotency_key:
            self.idempotency_key = self.compute_key()
        if not self.audit:
            self.audit.append(AuditEvent(now(), LegState.DRAFT, "created"))

    def compute_key(self) -> str:
        payload = json.dumps({
            "group": self.group_id, "src": self.source_account_id,
            "dst": self.destination_account_id or self.destination_label,
            "amt": str(self.amount.amount), "cur": self.amount.currency,
            "when": self.scheduled_for.isoformat() if self.scheduled_for else "",
            "purpose": self.purpose.value, "kind": self.kind.value,
        }, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:24]

    def transition(self, to: LegState, detail: str,
                   provider_ref: Optional[str] = None) -> None:
        if to not in ALLOWED[self.state]:
            raise ValueError(
                f"illegal transition {self.state.value} -> {to.value} for leg {self.id}")
        self.state = to
        if provider_ref:
            self.provider_ref = provider_ref
        self.audit.append(AuditEvent(now(), to, detail, provider_ref))

    @property
    def blocks_replacement(self) -> bool:
        return self.state in BLOCKS_REPLACEMENT

    @property
    def money_is_out(self) -> bool:
        return self.state in (LegState.SUBMITTED, LegState.PROCESSING,
                              LegState.FUNDS_AVAILABLE,
                              LegState.CREDITED_BY_BILLER, LegState.RECONCILED,
                              LegState.OUTCOME_UNKNOWN)

    def to_json(self) -> dict:
        return {
            "id": self.id, "group_id": self.group_id, "kind": self.kind.value,
            "source_account_id": self.source_account_id,
            "destination": self.destination_account_id or self.destination_label,
            "amount": self.amount.to_json(), "purpose": self.purpose.value,
            "scheduled_for": self.scheduled_for.isoformat() if self.scheduled_for else None,
            "state": self.state.value, "optional": self.optional,
            "depends_on": self.depends_on, "fee": self.fee.to_json(),
            "idempotency_key": self.idempotency_key,
            "provider_ref": self.provider_ref,
            "failure_reason": self.failure_reason,
            "principal_only": self.principal_only,
            "audit": [a.to_json() for a in self.audit],
        }


@dataclass
class TransferGroup:
    """Section 21: 'Treat multi-account splits as a group of independently
    tracked legs, not an atomic bank transaction.'"""
    id: str = field(default_factory=lambda: _id("grp"))
    label: str = ""
    legs: list[PaymentLeg] = field(default_factory=list)
    created: datetime = field(default_factory=now)

    def leg(self, leg_id: str) -> Optional[PaymentLeg]:
        return next((l for l in self.legs if l.id == leg_id), None)

    def summary(self) -> dict:
        by_state: dict[str, int] = {}
        for l in self.legs:
            by_state[l.state.value] = by_state.get(l.state.value, 0) + 1
        return {"group_id": self.id, "label": self.label,
                "legs": len(self.legs), "by_state": by_state,
                "unresolved": [l.id for l in self.legs
                               if l.state == LegState.OUTCOME_UNKNOWN]}

    def to_json(self) -> dict:
        return {**self.summary(), "detail": [l.to_json() for l in self.legs]}


# ---------------------------------------------------------------------------
# Provider simulator -- lets the recovery paths be exercised deterministically
# ---------------------------------------------------------------------------

class ProviderFault(str, Enum):
    NONE = "none"
    INSUFFICIENT_FUNDS = "insufficient_funds"
    TIMEOUT = "timeout"
    REJECTED = "rejected"
    RETURN_AFTER_SETTLEMENT = "return_after_settlement"
    DUPLICATE_CALLBACK = "duplicate_callback"
    CLOSED_ACCOUNT = "closed_account"
    UNSUPPORTED_PRINCIPAL_ONLY = "unsupported_principal_only"


@dataclass
class ProviderResponse:
    accepted: bool
    provider_ref: Optional[str]
    fault: ProviderFault
    message: str


class SimulatedProvider:
    """Stands in for an approved payment provider. Production requires a real
    provider arrangement; a sandbox success is not production approval."""

    def __init__(self):
        self.faults: dict[str, ProviderFault] = {}     # leg_id -> fault
        self.submitted: dict[str, str] = {}            # idempotency_key -> ref
        self.calls: list[dict] = []

    def inject(self, leg_id: str, fault: ProviderFault) -> None:
        self.faults[leg_id] = fault

    def submit(self, leg: PaymentLeg) -> ProviderResponse:
        self.calls.append({"leg": leg.id, "key": leg.idempotency_key,
                           "amount": str(leg.amount)})
        # idempotency: the same key never creates a second payment
        if leg.idempotency_key in self.submitted:
            return ProviderResponse(True, self.submitted[leg.idempotency_key],
                                    ProviderFault.NONE,
                                    "existing submission returned for this key")
        fault = self.faults.get(leg.id, ProviderFault.NONE)
        if fault == ProviderFault.TIMEOUT:
            return ProviderResponse(False, None, fault,
                                    "no response from provider within the timeout")
        if fault in (ProviderFault.REJECTED, ProviderFault.CLOSED_ACCOUNT,
                     ProviderFault.INSUFFICIENT_FUNDS,
                     ProviderFault.UNSUPPORTED_PRINCIPAL_ONLY):
            return ProviderResponse(False, None, fault, fault.value.replace("_", " "))
        ref = f"prv_{leg.idempotency_key[:12]}"
        self.submitted[leg.idempotency_key] = ref
        return ProviderResponse(True, ref, fault, "accepted")

    def query(self, leg: PaymentLeg) -> ProviderResponse:
        """Status recovery after a timeout -- section 21: 'A network timeout
        triggers status recovery; it must not automatically create another
        payment.'"""
        if leg.idempotency_key in self.submitted:
            return ProviderResponse(True, self.submitted[leg.idempotency_key],
                                    ProviderFault.NONE, "found: processing")
        return ProviderResponse(False, None, ProviderFault.NONE,
                                "no submission exists for this key")


# ---------------------------------------------------------------------------
# Execution engine
# ---------------------------------------------------------------------------

@dataclass
class PreflightResult:
    ok: bool
    checks: dict[str, bool]
    blockers: list[str]

    def to_json(self) -> dict:
        return {"ok": self.ok, "checks": self.checks, "blockers": self.blockers}


class ExecutionEngine:
    def __init__(self, household: Household, provider: Optional[SimulatedProvider] = None):
        self.hh = household
        self.provider = provider or SimulatedProvider()
        self.groups: dict[str, TransferGroup] = {}
        self.reservations: dict[str, Money] = {}       # account_id -> reserved
        self.paused = False

    # -- construction ---------------------------------------------------
    def build_group_from_allocation(self, allocation, label: str = "") -> TransferGroup:
        """Turn a PaycheckAllocation into independently tracked legs."""
        from ..engine.allocator import AllocationStatus
        grp = TransferGroup(label=label or f"Paycheck {allocation.pay_date}")
        for a in allocation.allocations:
            if not a.amount.is_positive or a.policy_id == "floor":
                continue
            policy = self.hh.policies.get(a.policy_id)
            kind = {
                PolicyPurpose.REQUIRED_DEBT: LegKind.BILL_PAYMENT,
                PolicyPurpose.CARD_STATEMENT: LegKind.CARD_STATEMENT,
                PolicyPurpose.BILL: LegKind.BILL_PAYMENT,
                PolicyPurpose.EXTRA_PRINCIPAL: LegKind.EXTRA_PRINCIPAL,
                PolicyPurpose.INVESTMENT_CASH: LegKind.INVESTMENT_FUNDING,
            }.get(a.purpose, LegKind.RESERVE_FUNDING)
            leg = PaymentLeg(
                group_id=grp.id, kind=kind,
                source_account_id=policy.source_account_id if policy else "",
                destination_account_id=a.destination_account_id,
                destination_label=a.name, amount=a.amount, purpose=a.purpose,
                scheduled_for=a.due_date or allocation.pay_date,
                entity_id=policy.entity_id if policy else None,
                mandate=policy.mandate if policy else None,
                optional=not (a.urgency.value in ("due_before_next_income",
                                                  "protected_floor")),
                principal_only=(a.purpose == PolicyPurpose.EXTRA_PRINCIPAL))
            grp.legs.append(leg)
        self.groups[grp.id] = grp
        return grp

    def build_group_from_bill(self, bill_id: str, label: str = "") -> TransferGroup:
        """A one-time payment (spec's Release-2 'one-time payments' capability,
        Bills-and-calendar screen): pay a specific bill now, independent of
        any paycheck allocation. Returns a draft leg -- building it never
        moves money; `preflight`/`run_group` still gate everything the
        allocation-sourced path gates (authorization, funds, capability)."""
        bill = self.hh.bills.get(bill_id)
        if bill is None:
            raise KeyError(f"unknown bill: {bill_id}")
        policy = next((p for p in self.hh.policies.values()
                      if bill.payee_account_id
                      and p.destination_account_id == bill.payee_account_id), None)
        grp = TransferGroup(label=label or f"One-time payment: {bill.name}")
        leg = PaymentLeg(
            group_id=grp.id, kind=LegKind.BILL_PAYMENT,
            source_account_id=bill.funding_account_id,
            destination_account_id=bill.payee_account_id or "",
            destination_label=bill.name, amount=bill.amount,
            purpose=PolicyPurpose.BILL,
            scheduled_for=bill.due_date,
            entity_id=policy.entity_id if policy else None,
            mandate=policy.mandate if policy else None,
            optional=False)
        grp.legs.append(leg)
        self.groups[grp.id] = grp
        return grp

    # -- preflight ------------------------------------------------------
    def preflight(self, leg: PaymentLeg) -> PreflightResult:
        """Section 21: recheck immediately before submission."""
        checks: dict[str, bool] = {}
        blockers: list[str] = []

        checks["not_globally_paused"] = not self.paused
        if self.paused:
            blockers.append("All future automation is paused by the user.")

        mandate_ok = False
        if leg.mandate:
            mandate_ok, why = leg.mandate.authorizes(leg.amount)
            if not mandate_ok:
                blockers.append(f"Authorization: {why}")
        else:
            blockers.append("No mandate attached to this leg.")
        checks["authorization"] = mandate_ok

        src = self.hh.accounts.get(leg.source_account_id)
        checks["source_exists"] = src is not None
        if src is None:
            blockers.append("Source account not found.")
        else:
            can_send = src.can(Capability.SEND_TRANSFER)
            checks["source_capability"] = can_send
            if not can_send:
                blockers.append(
                    f"{src.nickname} is visible for planning but does not support "
                    "sending transfers. Account data access does not prove payment "
                    "support.")
            reserved = self.reservations.get(src.id, Money.zero(src.currency))
            executable = (src.available - reserved).clamp_min_zero()
            enough = executable >= leg.amount
            checks["executable_funds"] = enough
            if not enough:
                blockers.append(
                    f"Executable balance on {src.nickname} is {executable} after "
                    f"existing reservations; this leg needs {leg.amount}.")
            checks["connection_fresh"] = src.connection_healthy
            if not src.connection_healthy:
                blockers.append(f"{src.nickname} data is stale; refresh before executing.")

        dst = self.hh.accounts.get(leg.destination_account_id)
        if dst is not None:
            if leg.kind in (LegKind.BILL_PAYMENT, LegKind.CARD_STATEMENT,
                            LegKind.EXTRA_PRINCIPAL):
                ok = dst.can(Capability.PAY_BILLER)
                checks["destination_capability"] = ok
                if not ok:
                    blockers.append(f"{dst.nickname} does not support creditor payments.")
            if leg.principal_only:
                ok = dst.can(Capability.PRINCIPAL_ONLY)
                checks["principal_only_supported"] = ok
                if not ok:
                    blockers.append(
                        "The provider does not support a principal-only instruction "
                        "for this account. An ordinary installment will not be "
                        "submitted in its place.")

        # duplicate prevention against existing autopay and in-flight legs
        dup = self._find_duplicate(leg)
        checks["no_duplicate"] = dup is None
        if dup is not None:
            blockers.append(
                f"An existing payment for this obligation is already {dup.state.value} "
                f"(leg {dup.id}). A replacement is withheld until it resolves.")

        for dep_id in leg.depends_on:
            dep = self._find_leg(dep_id)
            ready = dep is not None and dep.state in (
                LegState.FUNDS_AVAILABLE, LegState.CREDITED_BY_BILLER,
                LegState.RECONCILED)
            checks[f"dependency_{dep_id}"] = ready
            if not ready:
                blockers.append(
                    f"Waits on confirmed usable funding from leg {dep_id} "
                    f"(currently {dep.state.value if dep else 'missing'}).")

        return PreflightResult(not blockers, checks, blockers)

    def _find_leg(self, leg_id: str) -> Optional[PaymentLeg]:
        for g in self.groups.values():
            l = g.leg(leg_id)
            if l:
                return l
        return None

    def _find_duplicate(self, leg: PaymentLeg) -> Optional[PaymentLeg]:
        for g in self.groups.values():
            for other in g.legs:
                if other.id == leg.id:
                    continue
                if other.idempotency_key == leg.idempotency_key and other.money_is_out:
                    return other
                if (other.destination_account_id == leg.destination_account_id
                        and other.purpose == leg.purpose
                        and other.scheduled_for == leg.scheduled_for
                        and other.blocks_replacement):
                    return other
        return None

    # -- execution ------------------------------------------------------
    def authorize(self, leg: PaymentLeg, mandate: Mandate) -> None:
        leg.mandate = mandate
        if leg.state == LegState.DRAFT:
            leg.transition(LegState.AWAITING_AUTHORIZATION, "authorization requested")
        leg.transition(LegState.AUTHORIZED,
                       f"authorized under {mandate.mode.value} mandate {mandate.id}")

    def execute(self, leg: PaymentLeg) -> PaymentLeg:
        if leg.state in (LegState.DRAFT, LegState.AWAITING_AUTHORIZATION):
            leg.failure_reason = "not authorized"
            return leg
        if leg.state == LegState.AUTHORIZED:
            leg.transition(LegState.VALIDATING, "preflight checks")
        elif leg.state == LegState.SCHEDULED:
            leg.transition(LegState.VALIDATING, "preflight checks at due time")
        elif leg.state != LegState.VALIDATING:
            return leg

        pf = self.preflight(leg)
        if not pf.ok:
            leg.failure_reason = "; ".join(pf.blockers)
            leg.transition(LegState.FAILED, leg.failure_reason)
            return leg

        # reserve in the internal ledger at the moment of commitment
        src = self.hh.accounts[leg.source_account_id]
        self.reservations[src.id] = self.reservations.get(
            src.id, Money.zero(src.currency)) + leg.amount

        resp = self.provider.submit(leg)
        if not resp.accepted:
            if resp.fault == ProviderFault.TIMEOUT:
                leg.transition(LegState.SUBMITTED, "dispatched; awaiting response")
                leg.transition(LegState.OUTCOME_UNKNOWN,
                               "provider did not respond within the timeout. Status "
                               "recovery is required; no replacement payment will be "
                               "created.")
                return leg
            self._release(src.id, leg.amount)
            leg.failure_reason = resp.message
            leg.transition(LegState.FAILED, f"provider: {resp.message}")
            return leg

        leg.transition(LegState.SUBMITTED, "accepted by provider", resp.provider_ref)
        leg.transition(LegState.PROCESSING, "provider processing")
        return leg

    def _release(self, account_id: str, amount: Money) -> None:
        cur = self.reservations.get(account_id)
        if cur:
            self.reservations[account_id] = (cur - amount).clamp_min_zero()

    def recover_unknown(self, leg: PaymentLeg) -> PaymentLeg:
        """Query the provider with the ORIGINAL identity. Never resubmit blindly."""
        if leg.state != LegState.OUTCOME_UNKNOWN:
            return leg
        resp = self.provider.query(leg)
        if resp.accepted:
            leg.transition(LegState.PROCESSING,
                           "status recovery found the original submission",
                           resp.provider_ref)
        else:
            self._release(leg.source_account_id, leg.amount)
            leg.failure_reason = "no submission existed; safe to retry"
            leg.transition(LegState.FAILED,
                           "status recovery confirmed no payment was created")
        return leg

    def settle(self, leg: PaymentLeg, credited: bool = True) -> PaymentLeg:
        if leg.state == LegState.PROCESSING:
            leg.transition(LegState.FUNDS_AVAILABLE, "funds available at destination")
            src = self.hh.accounts.get(leg.source_account_id)
            if src:
                src.available = src.available - leg.amount
                src.current = src.current - leg.amount
                self._release(src.id, leg.amount)
            dst = self.hh.accounts.get(leg.destination_account_id)
            if dst:
                dst.available = dst.available + leg.amount
                dst.current = dst.current + leg.amount
        if credited and leg.state == LegState.FUNDS_AVAILABLE:
            leg.transition(LegState.CREDITED_BY_BILLER,
                           "creditor confirmed application of the payment")
            leg.transition(LegState.RECONCILED,
                           "matched to bank activity and creditor application")
        return leg

    def apply_return(self, leg: PaymentLeg, reason: str = "returned by the bank"
                     ) -> dict:
        """SC55: a funding transfer returned after initial settlement invalidates
        later allocations. Rebuild from actual status; never reverse completed
        legs automatically."""
        if leg.state not in (LegState.FUNDS_AVAILABLE, LegState.CREDITED_BY_BILLER,
                             LegState.RECONCILED, LegState.PROCESSING,
                             LegState.OUTCOME_UNKNOWN):
            return {"error": f"leg is {leg.state.value}; a return does not apply"}
        leg.transition(LegState.RETURNED, reason)
        src = self.hh.accounts.get(leg.source_account_id)
        dst = self.hh.accounts.get(leg.destination_account_id)
        if dst:
            dst.available = dst.available - leg.amount
            dst.current = dst.current - leg.amount
        if src:
            src.available = src.available + leg.amount
            src.current = src.current + leg.amount

        grp = self.groups.get(leg.group_id)
        invalidated: list[str] = []
        if grp:
            for other in grp.legs:
                if leg.id in other.depends_on and other.state not in TERMINAL:
                    if other.state in (LegState.AUTHORIZED, LegState.SCHEDULED,
                                       LegState.DRAFT):
                        other.transition(LegState.CANCELED,
                                         f"funding leg {leg.id} was returned")
                        invalidated.append(other.id)
        return {
            "leg": leg.id, "state": leg.state.value, "reason": reason,
            "dependent_legs_canceled": invalidated,
            "reopened_obligations": [leg.destination_label],
            "recovery": (
                "Cash availability and dependent legs have been rebuilt from actual "
                "status. The affected obligation is reopened. Completed legs are not "
                "reversed automatically and the plan is not marked entirely unpaid."),
        }

    def pause_all(self) -> dict:
        """EX09's pause-all control. Section 21: revocation stops new app-originated
        actions and initiates supported cancellation for pending items; it cannot
        guarantee reversal of money already sent."""
        self.paused = True
        cancellable, uncancellable = [], []
        for g in self.groups.values():
            for l in g.legs:
                if l.state in (LegState.DRAFT, LegState.AUTHORIZED, LegState.SCHEDULED,
                               LegState.AWAITING_AUTHORIZATION):
                    l.transition(LegState.CANCELED, "user paused all future automation")
                    cancellable.append(l.id)
                elif l.money_is_out:
                    uncancellable.append({"leg": l.id, "state": l.state.value,
                                          "amount": str(l.amount),
                                          "destination": l.destination_label})
        return {
            "paused": True,
            "canceled": cancellable,
            "already_sent_cannot_be_stopped": uncancellable,
            "note": ("Future eligible runs are stopped. Payments already submitted "
                     "cannot be stopped through this application; a recall or "
                     "investigation must be raised with the provider and is not "
                     "guaranteed."),
        }

    def run_group(self, group: TransferGroup, settle: bool = True) -> dict:
        """Required legs first; optional legs only when funding still allows.
        A failure in one leg does not fail the group."""
        ordered = sorted(group.legs, key=lambda l: (l.optional, l.scheduled_for or date.max))
        results = []
        for leg in ordered:
            if leg.state == LegState.DRAFT and leg.mandate and leg.mandate.active:
                leg.transition(LegState.AUTHORIZED, "covered by an existing standing mandate")
            self.execute(leg)
            if settle and leg.state == LegState.PROCESSING:
                self.settle(leg)
            results.append({"leg": leg.id, "label": leg.destination_label,
                            "amount": str(leg.amount), "state": leg.state.value,
                            "reason": leg.failure_reason})
        return {"group": group.id, "results": results, **group.summary()}
