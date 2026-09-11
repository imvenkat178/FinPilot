"""The paycheck and monthly allocation engine -- spec sections 4 and 14.

This is the centre of the product. Three rules govern it and everything else
follows from them:

1.  The monthly plan is the target ledger; paychecks are its funding events.
    "If a monthly goal is $700 and $400 has already been funded, the next run
    can allocate at most the remaining $300."  (Section 14)

2.  A deadline that falls before the next paycheck must be funded from *this*
    one.  "The mortgage is due on the fifth and the card statement on the
    twelfth, so those amounts must be funded by the first paycheck.  Dividing
    every destination equally across two paychecks would make the mortgage plan
    infeasible."  (Section 14 / SC49)

3.  Required obligations and protected reserves take precedence over optional
    percentages, and an overcommitted policy displays the conflict rather than
    producing negative allocations.  (Section 14)

The engine never divides a paycheck by fixed percentages. It builds a dated
ledger, asks what must be true by each date, and funds backwards from there.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from enum import Enum
from typing import Optional

from ..dates import Cadence, day_in_month, DayRule
from ..models import (Bill, Confidence, Household, IncomeEvent, PercentBase,
                      PolicyMethod, PolicyPurpose, RecurringPolicy)
from ..money import Money, allocate as split_evenly, msum


class AllocationStatus(str, Enum):
    FUNDED = "funded"
    PARTIAL = "partial"
    DEFERRED = "deferred"           # legitimately waits for a later paycheck
    UNFUNDED_SHORTFALL = "unfunded_shortfall"
    PAUSED = "paused"
    ALREADY_FUNDED = "already_funded"
    BLOCKED = "blocked"             # no authority or no capability


class Urgency(str, Enum):
    DUE_BEFORE_NEXT_INCOME = "due_before_next_income"
    PROTECTED_FLOOR = "protected_floor"
    DUE_THIS_PERIOD = "due_this_period"
    OPTIONAL = "optional"


@dataclass
class Allocation:
    policy_id: str
    name: str
    purpose: PolicyPurpose
    destination_account_id: str
    amount: Money
    status: AllocationStatus
    urgency: Urgency
    reason: str
    due_date: Optional[date] = None
    monthly_target: Optional[Money] = None
    funded_before: Optional[Money] = None
    requested: Optional[Money] = None
    liability_id: Optional[str] = None
    reserve_id: Optional[str] = None

    def to_json(self) -> dict:
        return {
            "policy_id": self.policy_id, "name": self.name,
            "purpose": self.purpose.value,
            "destination_account_id": self.destination_account_id,
            "amount": self.amount.to_json(), "status": self.status.value,
            "urgency": self.urgency.value, "reason": self.reason,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "monthly_target": self.monthly_target.to_json() if self.monthly_target else None,
            "funded_before": self.funded_before.to_json() if self.funded_before else None,
            "requested": self.requested.to_json() if self.requested else None,
            "liability_id": self.liability_id, "reserve_id": self.reserve_id,
        }


@dataclass
class Shortfall:
    name: str
    needed: Money
    funded: Money
    due_date: Optional[date]
    consequence: str

    @property
    def gap(self) -> Money:
        return (self.needed - self.funded).clamp_min_zero()

    def to_json(self) -> dict:
        return {"name": self.name, "needed": self.needed.to_json(),
                "funded": self.funded.to_json(), "gap": self.gap.to_json(),
                "due_date": self.due_date.isoformat() if self.due_date else None,
                "consequence": self.consequence}


@dataclass
class PaycheckAllocation:
    income_event_id: str
    pay_date: date
    next_income_date: Optional[date]
    available: Money
    allocations: list[Allocation] = field(default_factory=list)
    shortfalls: list[Shortfall] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    confidence: Confidence = Confidence.EXACT

    @property
    def allocated(self) -> Money:
        return msum([a.amount for a in self.allocations], self.available.currency)

    @property
    def unallocated(self) -> Money:
        return (self.available - self.allocated).clamp_min_zero()

    @property
    def has_shortfall(self) -> bool:
        return any(s.gap.is_positive for s in self.shortfalls)

    def by_purpose(self) -> dict[str, Money]:
        out: dict[str, Money] = {}
        for a in self.allocations:
            if a.amount.is_zero:
                continue
            k = a.purpose.value
            out[k] = out.get(k, Money.zero(self.available.currency)) + a.amount
        return out

    def to_json(self) -> dict:
        return {
            "income_event_id": self.income_event_id,
            "pay_date": self.pay_date.isoformat(),
            "next_income_date": self.next_income_date.isoformat() if self.next_income_date else None,
            "available": self.available.to_json(),
            "allocated": self.allocated.to_json(),
            "unallocated": self.unallocated.to_json(),
            "allocations": [a.to_json() for a in self.allocations],
            "shortfalls": [s.to_json() for s in self.shortfalls],
            "warnings": self.warnings, "assumptions": self.assumptions,
            "confidence": self.confidence.value,
            "by_purpose": {k: v.to_json() for k, v in self.by_purpose().items()},
        }


@dataclass
class MonthlyTarget:
    policy_id: str
    name: str
    purpose: PolicyPurpose
    target: Money
    destination_account_id: str
    due_date: Optional[date]
    priority: int
    required: bool
    protected: bool
    funded: Money
    liability_id: Optional[str] = None
    reserve_id: Optional[str] = None

    @property
    def remaining(self) -> Money:
        return (self.target - self.funded).clamp_min_zero()

    def to_json(self) -> dict:
        return {"policy_id": self.policy_id, "name": self.name,
                "purpose": self.purpose.value, "target": self.target.to_json(),
                "funded": self.funded.to_json(), "remaining": self.remaining.to_json(),
                "destination_account_id": self.destination_account_id,
                "due_date": self.due_date.isoformat() if self.due_date else None,
                "priority": self.priority, "required": self.required,
                "protected": self.protected}


@dataclass
class MonthlyPlan:
    year: int
    month: int
    targets: list[MonthlyTarget]
    expected_income: Money
    currency: str = "USD"

    @property
    def total_target(self) -> Money:
        return msum([t.target for t in self.targets], self.currency)

    @property
    def required_target(self) -> Money:
        return msum([t.target for t in self.targets if t.required], self.currency)

    @property
    def is_overcommitted(self) -> bool:
        return self.total_target > self.expected_income

    def to_json(self) -> dict:
        return {"year": self.year, "month": self.month,
                "expected_income": self.expected_income.to_json(),
                "total_target": self.total_target.to_json(),
                "required_target": self.required_target.to_json(),
                "overcommitted": self.is_overcommitted,
                "targets": [t.to_json() for t in self.targets]}


CONSEQUENCE = {
    PolicyPurpose.REQUIRED_DEBT: "late fee, and after 30 days a delinquency may be reported",
    PolicyPurpose.CARD_STATEMENT: "grace period lost; new purchases accrue interest from their transaction dates",
    PolicyPurpose.BILL: "late fee, service interruption or a reported delinquency depending on the biller",
    PolicyPurpose.TAX_RESERVE: "underpayment exposure at filing",
}


class AllocationEngine:
    """Builds monthly targets and allocates each income event against them."""

    def __init__(self, household: Household):
        self.hh = household
        self.cur = household.base_currency

    # ------------------------------------------------------------------
    # Monthly plan
    # ------------------------------------------------------------------
    def month_income_dates(self, year: int, month: int
                           ) -> list[tuple[date, Money, Optional[IncomeEvent]]]:
        """Expected income events in a month, in date order.

        Returns each event's own identity alongside its date and amount --
        two events can share a date (a salary and a same-day bonus), and a
        caller that re-looks an event up by date alone would find only the
        first match for both and silently process it twice."""
        first = date(year, month, 1)
        last = date(year, month, calendar.monthrange(year, month)[1])
        events: list[tuple[date, Money, Optional[IncomeEvent]]] = []
        for ev in self.hh.income_events:
            d = ev.received_date or ev.expected_date
            if first <= d <= last:
                events.append((d, ev.effective_amount, ev))
        if not events:
            for src in self.hh.income_sources.values():
                if src.schedule:
                    for d in src.schedule.occurrences(first, last):
                        projected = IncomeEvent(id=f"scheduled:{src.id}:{d.isoformat()}",
                                                source_id=src.id, expected_date=d,
                                                expected_amount=src.net_amount)
                        events.append((d, src.net_amount, projected))
        return sorted(events, key=lambda t: t[0])

    def _policy_monthly_target(self, p: RecurringPolicy, monthly_income: Money,
                               committed: Money) -> Money:
        """Resolve a policy's monthly target. Percentages must name their base."""
        if p.method == PolicyMethod.FIXED:
            return p.monthly_target or p.amount
        if p.method == PolicyMethod.PERCENT_OF_INCOME:
            base = {
                PercentBase.MONTHLY_INCOME: monthly_income,
                PercentBase.NET_PAYCHECK: monthly_income,
                PercentBase.SURPLUS_AFTER_COMMITMENTS: (monthly_income - committed).clamp_min_zero(),
            }[p.percent_base]
            return (base * p.percent).round()
        if p.method == PolicyMethod.TARGET_BALANCE:
            res = self.hh.reserves.get(p.destination_reserve_id or "")
            if res:
                return res.remaining
            acct = self.hh.accounts.get(p.destination_account_id)
            if acct and p.target_balance:
                return (p.target_balance - acct.available).clamp_min_zero()
            return Money.zero(self.cur)
        if p.method == PolicyMethod.TARGET_BY_DATE:
            res = self.hh.reserves.get(p.destination_reserve_id or "")
            remaining = res.remaining if res else (p.target_balance or Money.zero(self.cur))
            if not p.target_date:
                return remaining
            months = max(1, (p.target_date.year - self.hh.as_of.year) * 12
                         + (p.target_date.month - self.hh.as_of.month))
            return (remaining / months).round()
        if p.method == PolicyMethod.SURPLUS_SHARE:
            surplus = (monthly_income - committed).clamp_min_zero()
            return (surplus * p.percent).round()
        return Money.zero(self.cur)

    def _due_date_for(self, p: RecurringPolicy, year: int, month: int) -> Optional[date]:
        # a bill attached to this policy's destination or liability
        for b in self.hh.bills.values():
            if p.liability_id and b.payee_account_id == p.liability_id:
                return day_in_month(year, month, b.due_date.day, DayRule.LAST_DAY_OF_MONTH)
            if b.id == p.destination_reserve_id:
                return day_in_month(year, month, b.due_date.day, DayRule.LAST_DAY_OF_MONTH)
        if p.liability_id and p.liability_id in self.hh.liabilities:
            lia = self.hh.liabilities[p.liability_id]
            return day_in_month(year, month, lia.due_day, DayRule.LAST_DAY_OF_MONTH)
        if p.schedule and p.schedule.day_of_month:
            return day_in_month(year, month, p.schedule.day_of_month, p.schedule.day_rule)
        return None

    def build_monthly_plan(self, year: int, month: int) -> MonthlyPlan:
        income_events = self.month_income_dates(year, month)
        monthly_income = msum([a for _, a, _ in income_events], self.cur)

        # committed = required + protected, needed as the base for surplus rules
        committed = Money.zero(self.cur)
        for p in self.hh.policies_sorted():
            if p.paused:
                continue
            if p.is_required or p.is_protected_reserve:
                committed = committed + self._policy_monthly_target(
                    p, monthly_income, Money.zero(self.cur))

        targets: list[MonthlyTarget] = []
        for p in self.hh.policies_sorted():
            if p.paused:
                continue
            tgt = self._policy_monthly_target(p, monthly_income, committed)
            if tgt.is_zero:
                continue
            due_date = self._due_date_for(p, year, month)
            funded = p.funding_for_period(date(year, month, 1), self.hh.as_of)
            bill = self.hh.bill_for_policy(p)
            if bill and due_date:
                funded = funded.max(self.hh.bill_occurrence(bill, due_date).funded)
            targets.append(MonthlyTarget(
                policy_id=p.id, name=p.name, purpose=p.purpose, target=tgt,
                destination_account_id=p.destination_account_id,
                due_date=due_date,
                priority=p.priority, required=p.is_required,
                protected=p.is_protected_reserve, funded=funded,
                liability_id=p.liability_id, reserve_id=p.destination_reserve_id))
        return MonthlyPlan(year, month, targets, monthly_income, self.cur)

    # ------------------------------------------------------------------
    # Paycheck allocation
    # ------------------------------------------------------------------
    def allocate_paycheck(self, event: IncomeEvent, plan: MonthlyPlan,
                          next_income_date: Optional[date] = None,
                          carry_in_cash: Optional[Money] = None,
                          checking_floor: Optional[Money] = None
                          ) -> PaycheckAllocation:
        pay_date = event.received_date or event.expected_date
        available = event.effective_amount
        if carry_in_cash:
            available = available + carry_in_cash

        if next_income_date is None:
            later = [d for d, _, _ in self.month_income_dates(plan.year, plan.month)
                     if d > pay_date]
            next_income_date = later[0] if later else None

        result = PaycheckAllocation(
            income_event_id=event.id, pay_date=pay_date,
            next_income_date=next_income_date, available=available)

        if not event.is_received:
            result.assumptions.append(
                f"Uses the expected deposit of {available}; the plan is "
                "recalculated when the money actually posts.")
            result.confidence = Confidence.BOUNDED
        if carry_in_cash and carry_in_cash.is_positive:
            result.assumptions.append(
                f"Includes {carry_in_cash} of uncommitted cash already on hand.")

        remaining = available
        remaining_paychecks_after = self._paychecks_after(plan, pay_date)

        # -- pass 1: anything due before the next paycheck -----------------
        urgent = [t for t in plan.targets
                  if t.remaining.is_positive and (t.required or t.protected)
                  and self._is_urgent(t, pay_date, next_income_date)]
        urgent.sort(key=lambda t: (t.due_date or date.max, t.priority))

        for t in urgent:
            remaining = self._fund(t, remaining, result,
                                   Urgency.DUE_BEFORE_NEXT_INCOME,
                                   self._urgent_reason(t, next_income_date))

        # -- pass 2: protect the operating floor ---------------------------
        floor = checking_floor or self._floor_from_policies(plan)
        if floor.is_positive:
            hold = floor.min(remaining)
            result.allocations.append(Allocation(
                policy_id="floor", name="Protected checking floor",
                purpose=PolicyPurpose.BUFFER, destination_account_id="",
                amount=hold, status=AllocationStatus.FUNDED,
                urgency=Urgency.PROTECTED_FLOOR,
                reason=f"Held in checking so the account cannot go below {floor} "
                       "before the next deposit.",
                monthly_target=floor, requested=floor))
            remaining = remaining - hold
            if hold < floor:
                result.warnings.append(
                    f"The checking floor of {floor} could only be funded to {hold}.")

        # -- pass 3: required items due later in the period ----------------
        later_required = [t for t in plan.targets
                          if t.remaining.is_positive and t.required
                          and not self._is_urgent(t, pay_date, next_income_date)]
        later_required.sort(key=lambda t: (t.due_date or date.max, t.priority))
        for t in later_required:
            if remaining_paychecks_after > 0:
                # a later paycheck can carry it; fund nothing now unless spare
                result.allocations.append(Allocation(
                    policy_id=t.policy_id, name=t.name, purpose=t.purpose,
                    destination_account_id=t.destination_account_id,
                    amount=Money.zero(self.cur), status=AllocationStatus.DEFERRED,
                    urgency=Urgency.DUE_THIS_PERIOD,
                    reason=f"Due {t.due_date.isoformat() if t.due_date else 'later this month'}, "
                           f"after the next deposit on {next_income_date}. Funded from that paycheck.",
                    due_date=t.due_date, monthly_target=t.target,
                    funded_before=t.funded, requested=t.remaining,
                    liability_id=t.liability_id, reserve_id=t.reserve_id))
                continue
            remaining = self._fund(t, remaining, result, Urgency.DUE_THIS_PERIOD,
                                   "Required this period and this is the last "
                                   "deposit before it is due.")

        # -- pass 4: everything else, in the user's saved priority order -----
        #
        # Section 14 says fixed commitments and protected reserves take precedence
        # "over optional percentages". That sentence is about percentage-of-income
        # and surplus-share rules specifically, so those are forced behind every
        # protected reserve regardless of their priority number. Fixed-amount
        # targets -- household spending, for instance -- keep the position the
        # user gave them, which is what "the saved priority order" in SC50 means.
        done = {a.policy_id for a in result.allocations}
        rest = [t for t in plan.targets
                if t.remaining.is_positive and t.policy_id not in done]

        def order(t: MonthlyTarget) -> tuple:
            pol = self.hh.policies.get(t.policy_id)
            elastic = bool(pol and pol.method in (PolicyMethod.PERCENT_OF_INCOME,
                                                  PolicyMethod.SURPLUS_SHARE)
                           and not t.protected and not t.required)
            return (1 if elastic else 0, t.priority, t.name)

        rest.sort(key=order)
        for t in rest:
            urgency = Urgency.DUE_THIS_PERIOD if (t.required or t.protected) \
                else Urgency.OPTIONAL
            if remaining.is_zero:
                result.allocations.append(Allocation(
                    policy_id=t.policy_id, name=t.name, purpose=t.purpose,
                    destination_account_id=t.destination_account_id,
                    amount=Money.zero(self.cur),
                    status=AllocationStatus.UNFUNDED_SHORTFALL, urgency=urgency,
                    reason=("Paused: obligations due before the next deposit "
                            "consumed this paycheck. It resumes on the next one."),
                    monthly_target=t.target, funded_before=t.funded,
                    requested=t.remaining, liability_id=t.liability_id,
                    reserve_id=t.reserve_id))
                continue
            share = self._share_for_this_paycheck(t, remaining_paychecks_after)
            reason = ("Protected reserve, funded ahead of elastic percentage rules."
                      if t.protected else
                      "Funded in your saved priority order, spread across the "
                      "deposits left in the period.")
            remaining = self._fund(t, remaining, result, urgency, reason, cap=share)

        # -- shortfall register --------------------------------------------
        for a in result.allocations:
            if a.status in (AllocationStatus.PARTIAL, AllocationStatus.UNFUNDED_SHORTFALL) \
                    and a.urgency in (Urgency.DUE_BEFORE_NEXT_INCOME, Urgency.PROTECTED_FLOOR) \
                    or (a.status != AllocationStatus.DEFERRED
                        and a.requested and a.amount < a.requested
                        and a.purpose in CONSEQUENCE):
                result.shortfalls.append(Shortfall(
                    name=a.name, needed=a.requested or a.amount, funded=a.amount,
                    due_date=a.due_date,
                    consequence=CONSEQUENCE.get(a.purpose,
                                                "this target will not reach its monthly amount")))

        if result.has_shortfall:
            result.warnings.insert(0,
                "This deposit does not cover every required obligation. Optional "
                "transfers are paused and the gap is shown below. A partial "
                "payment does not satisfy a full contractual obligation.")
        return result

    # ------------------------------------------------------------------
    def allocate_month(self, year: int, month: int,
                       checking_floor: Optional[Money] = None
                       ) -> tuple[MonthlyPlan, list[PaycheckAllocation]]:
        """Run every income event in a month against one shared target ledger.

        Funding accumulates across paychecks: this is what stops the same
        monthly target being applied to every deposit (SC51)."""
        plan = self.build_monthly_plan(year, month)
        events = self.month_income_dates(year, month)
        runs: list[PaycheckAllocation] = []
        for i, (d, amt, ev) in enumerate(events):
            # `ev` is this exact event's own identity, from month_income_dates
            # itself -- never re-looked-up by date, which would silently
            # collapse two same-day events (e.g. a salary and a same-day
            # bonus) onto whichever one happens to sort first.
            if ev is None:
                ev = IncomeEvent(source_id="", expected_date=d, expected_amount=amt)
            nxt = events[i + 1][0] if i + 1 < len(events) else None
            run = self.allocate_paycheck(ev, plan, next_income_date=nxt,
                                         checking_floor=checking_floor)
            # commit funding into the shared ledger
            for a in run.allocations:
                for t in plan.targets:
                    if t.policy_id == a.policy_id and a.amount.is_positive:
                        t.funded = t.funded + a.amount
            runs.append(run)
        return plan, runs

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _is_urgent(self, t: MonthlyTarget, pay_date: date,
                   next_income_date: Optional[date]) -> bool:
        if t.protected and t.purpose == PolicyPurpose.BUFFER:
            return True
        if t.due_date is None:
            return False
        if next_income_date is None:
            return True                       # last deposit before period end
        return t.due_date < next_income_date

    def _urgent_reason(self, t: MonthlyTarget, next_income_date: Optional[date]) -> str:
        if t.due_date and next_income_date:
            return (f"Due {t.due_date.isoformat()}, before the next deposit on "
                    f"{next_income_date.isoformat()}. Splitting this across both "
                    "paychecks would leave the due date unfunded.")
        if t.due_date:
            return f"Due {t.due_date.isoformat()} and this is the last deposit before it."
        return "Required before the next deposit."

    def _paychecks_after(self, plan: MonthlyPlan, pay_date: date) -> int:
        return len([d for d, _, _ in self.month_income_dates(plan.year, plan.month)
                    if d > pay_date])

    def _share_for_this_paycheck(self, t: MonthlyTarget, after: int) -> Money:
        """Spread a non-deadline target evenly across the deposits that remain."""
        if after <= 0:
            return t.remaining
        return (t.remaining / (after + 1)).round()

    def _floor_from_policies(self, plan: MonthlyPlan) -> Money:
        for t in plan.targets:
            if t.purpose == PolicyPurpose.BUFFER:
                return t.target
        return Money.zero(self.cur)

    def _fund(self, t: MonthlyTarget, remaining: Money, result: PaycheckAllocation,
              urgency: Urgency, reason: str, cap: Optional[Money] = None) -> Money:
        policy = self.hh.policies.get(t.policy_id)
        trigger_date = t.due_date if policy and policy.schedule and t.due_date else result.pay_date
        if policy and self.hh.policy_skipped_on(policy, trigger_date):
            result.allocations.append(Allocation(
                policy_id=t.policy_id, name=t.name, purpose=t.purpose,
                destination_account_id=t.destination_account_id,
                amount=Money.zero(self.cur), status=AllocationStatus.PAUSED,
                urgency=urgency, reason="This dated occurrence was skipped by the user.",
                due_date=t.due_date, monthly_target=t.target, funded_before=t.funded,
                requested=t.remaining, liability_id=t.liability_id, reserve_id=t.reserve_id))
            return remaining
        want = t.remaining if cap is None else t.remaining.min(cap)
        if want.is_zero:
            return remaining
        give = want.min(remaining)
        if give.is_zero:
            status = AllocationStatus.UNFUNDED_SHORTFALL
        elif give < want:
            status = AllocationStatus.PARTIAL
        else:
            status = AllocationStatus.FUNDED
        result.allocations.append(Allocation(
            policy_id=t.policy_id, name=t.name, purpose=t.purpose,
            destination_account_id=t.destination_account_id, amount=give,
            status=status, urgency=urgency, reason=reason, due_date=t.due_date,
            monthly_target=t.target, funded_before=t.funded, requested=want,
            liability_id=t.liability_id, reserve_id=t.reserve_id))
        return remaining - give
