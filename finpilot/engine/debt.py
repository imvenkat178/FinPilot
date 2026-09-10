"""Multi-debt repayment simulator -- spec sections 7, 8 and 18.

The simulator's conventions are fixed by section 8 so its results are
reproducible:

    "The simulator charges interest monthly at the annual rate divided by 12,
     rounds interest to cents, then makes payments. Required payments remain
     fixed while a debt exists; all freed amounts roll into the same total
     repayment budget. There are no new purchases, taxes, escrow charges,
     mortgage insurance, penalties, promotions, or fees. The final payment is
     limited to the remaining amount due."

Every strategy runs against the SAME budget and the SAME starting snapshot, so
the comparison is honest (section 24: "Every debt comparison uses the same
stated budget and starting snapshot").
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Callable, Optional

from ..models import Liability, RateType
from ..money import Money, allocate as split_evenly, msum


class Strategy(str, Enum):
    HIGHEST_RATE = "highest_rate"           # cost-minimising benchmark
    SMALLEST_BALANCE = "smallest_balance"   # motivation ordering
    EQUAL_SHARES = "equal_shares"
    CASH_FLOW_RELIEF = "cash_flow_relief"   # free the largest required payment first
    PRESERVE_LIQUIDITY = "preserve_liquidity"
    PROMO_DEADLINE_FIRST = "promo_deadline_first"
    CUSTOM = "custom"


@dataclass
class DebtSnapshot:
    id: str
    name: str
    balance: Money
    apr: Decimal
    minimum: Money
    rate_type: RateType = RateType.FIXED
    promo_expires: Optional[date] = None
    deductible_interest: bool = False

    @classmethod
    def from_liability(cls, lia: Liability) -> "DebtSnapshot":
        return cls(id=lia.id, name=lia.name, balance=lia.balance, apr=lia.apr,
                   minimum=lia.minimum_payment, rate_type=lia.rate_type,
                   deductible_interest=lia.tax_deductible_interest)


@dataclass
class MonthRow:
    month: int
    debt_id: str
    opening: Money
    interest: Money
    payment: Money
    closing: Money


@dataclass
class PayoffEvent:
    debt_id: str
    name: str
    month: int


@dataclass
class SimulationResult:
    strategy: Strategy
    label: str
    months_to_clear: int
    total_interest: Money
    total_paid: Money
    payoffs: list[PayoffEvent]
    schedule: list[MonthRow]
    residual_balance: Money
    first_month_allocation: dict[str, Money] = field(default_factory=dict)
    truncated: bool = False
    assumptions: list[str] = field(default_factory=list)

    @property
    def first_payoff(self) -> Optional[PayoffEvent]:
        return self.payoffs[0] if self.payoffs else None

    def to_json(self) -> dict:
        return {
            "strategy": self.strategy.value, "label": self.label,
            "months_to_clear": self.months_to_clear,
            "total_interest": self.total_interest.to_json(),
            "total_paid": self.total_paid.to_json(),
            "residual_balance": self.residual_balance.to_json(),
            "truncated": self.truncated,
            "first_payoff": ({"debt": self.first_payoff.name,
                              "month": self.first_payoff.month}
                             if self.first_payoff else None),
            "payoffs": [{"debt": p.name, "month": p.month} for p in self.payoffs],
            "first_month_allocation": {k: v.to_json()
                                       for k, v in self.first_month_allocation.items()},
            "assumptions": self.assumptions,
        }


def _order_key(strategy: Strategy) -> Callable[[DebtSnapshot], tuple]:
    if strategy == Strategy.HIGHEST_RATE:
        return lambda d: (-d.apr, d.balance.amount)
    if strategy == Strategy.SMALLEST_BALANCE:
        return lambda d: (d.balance.amount, -d.apr)
    if strategy == Strategy.CASH_FLOW_RELIEF:
        return lambda d: (-(d.minimum.amount / (d.balance.amount or Decimal(1))),
                          d.balance.amount)
    if strategy == Strategy.PROMO_DEADLINE_FIRST:
        return lambda d: (d.promo_expires or date.max, -d.apr)
    return lambda d: (d.balance.amount, -d.apr)


class DebtSimulator:
    """Runs a repayment plan month by month under the section 8 conventions."""

    MAX_MONTHS = 720

    def __init__(self, debts: list[DebtSnapshot], monthly_budget: Money,
                 currency: str = "USD"):
        self.debts = debts
        self.budget = monthly_budget
        self.cur = currency

    @property
    def required_total(self) -> Money:
        return msum([d.minimum for d in self.debts], self.cur)

    @property
    def extra_budget(self) -> Money:
        return (self.budget - self.required_total).clamp_min_zero()

    # ------------------------------------------------------------------
    def run(self, strategy: Strategy, horizon_months: Optional[int] = None,
            custom_targets: Optional[list[str]] = None,
            reserve_for: Optional[tuple[str, Money]] = None) -> SimulationResult:
        """Simulate one strategy.

        `reserve_for` implements the hybrid in section 7: reserve a defined
        amount to clear a named debt, then apply the remaining extra-payment
        budget to the cost-focused plan.
        """
        state = {d.id: Money(d.balance.amount, d.balance.currency) for d in self.debts}
        meta = {d.id: d for d in self.debts}
        alive = [d.id for d in self.debts if state[d.id].is_positive]

        total_interest = Money.zero(self.cur)
        total_paid = Money.zero(self.cur)
        payoffs: list[PayoffEvent] = []
        schedule: list[MonthRow] = []
        first_alloc: dict[str, Money] = {}
        month = 0
        limit = horizon_months or self.MAX_MONTHS

        while alive and month < limit:
            month += 1
            opening = {i: state[i] for i in alive}

            # 1. accrue interest, rounded to cents, then capitalise
            interest_this_month: dict[str, Money] = {}
            for i in alive:
                d = meta[i]
                rate = Decimal("0") if d.rate_type == RateType.PROMOTIONAL_ZERO else d.apr
                intr = (state[i] * (rate / Decimal("12"))).round()
                interest_this_month[i] = intr
                state[i] = state[i] + intr
                total_interest = total_interest + intr

            # 2. required payments, capped at the balance
            paid_this_month: dict[str, Money] = {i: Money.zero(self.cur) for i in alive}
            budget_left = self.budget
            for i in alive:
                due = meta[i].minimum.min(state[i]).min(budget_left)
                paid_this_month[i] = paid_this_month[i] + due
                state[i] = state[i] - due
                budget_left = budget_left - due

            # 3. extra, by strategy
            targets = [i for i in alive if state[i].is_positive]
            if targets and budget_left.is_positive:
                if reserve_for and reserve_for[0] in targets:
                    tid, amt = reserve_for
                    give = amt.min(state[tid]).min(budget_left)
                    paid_this_month[tid] = paid_this_month[tid] + give
                    state[tid] = state[tid] - give
                    budget_left = budget_left - give
                    targets = [i for i in alive if state[i].is_positive]

                if budget_left.is_positive and targets:
                    if strategy == Strategy.EQUAL_SHARES:
                        shares = split_evenly(budget_left,
                                              [Decimal(1)] * len(targets))
                        # a share bigger than the balance spills to the others
                        leftovers = Money.zero(self.cur)
                        for i, share in zip(targets, shares):
                            give = share.min(state[i])
                            leftovers = leftovers + (share - give)
                            paid_this_month[i] = paid_this_month[i] + give
                            state[i] = state[i] - give
                        budget_left = leftovers
                        for i in sorted(targets, key=_order_key(Strategy.SMALLEST_BALANCE)
                                        and (lambda x: state[x].amount)):
                            if budget_left.is_zero:
                                break
                            give = budget_left.min(state[i])
                            paid_this_month[i] = paid_this_month[i] + give
                            state[i] = state[i] - give
                            budget_left = budget_left - give
                    elif strategy == Strategy.PRESERVE_LIQUIDITY:
                        pass                       # required payments only
                    else:
                        if strategy == Strategy.CUSTOM and custom_targets:
                            ordered = [i for i in custom_targets if i in targets]
                            ordered += [i for i in sorted(
                                targets, key=lambda x: _order_key(Strategy.HIGHEST_RATE)(meta[x]))
                                if i not in ordered]
                        else:
                            ordered = sorted(
                                targets,
                                key=lambda x: _order_key(strategy)(
                                    DebtSnapshot(x, meta[x].name, state[x],
                                                 meta[x].apr, meta[x].minimum,
                                                 meta[x].rate_type,
                                                 meta[x].promo_expires)))
                        for i in ordered:
                            if budget_left.is_zero:
                                break
                            give = budget_left.min(state[i])
                            paid_this_month[i] = paid_this_month[i] + give
                            state[i] = state[i] - give
                            budget_left = budget_left - give

            for i in alive:
                total_paid = total_paid + paid_this_month[i]
                schedule.append(MonthRow(month, i, opening[i],
                                         interest_this_month[i],
                                         paid_this_month[i], state[i]))
            if month == 1:
                first_alloc = {meta[i].name: paid_this_month[i] for i in alive}

            cleared = [i for i in alive if not state[i].is_positive]
            for i in cleared:
                payoffs.append(PayoffEvent(i, meta[i].name, month))
            alive = [i for i in alive if state[i].is_positive]

        residual = msum([state[i] for i in state], self.cur)
        return SimulationResult(
            strategy=strategy, label=STRATEGY_LABEL[strategy],
            months_to_clear=month, total_interest=total_interest,
            total_paid=total_paid, payoffs=payoffs, schedule=schedule,
            residual_balance=residual, first_month_allocation=first_alloc,
            truncated=bool(alive),
            assumptions=[
                "Interest accrues monthly at the annual rate divided by twelve and "
                "is rounded to cents before payment.",
                "Required payments stay fixed while a debt exists; freed amounts "
                "roll into the same total budget.",
                "No new purchases, escrow, insurance, penalties, promotions or fees "
                "are modelled. Real contracts often differ and require their own model.",
            ])


STRATEGY_LABEL = {
    Strategy.HIGHEST_RATE: "Highest rate first",
    Strategy.SMALLEST_BALANCE: "Smallest balance first",
    Strategy.EQUAL_SHARES: "Equal extra shares",
    Strategy.CASH_FLOW_RELIEF: "Monthly payment relief",
    Strategy.PRESERVE_LIQUIDITY: "Preserve liquidity",
    Strategy.PROMO_DEADLINE_FIRST: "Promotional deadline first",
    Strategy.CUSTOM: "Custom combination",
}


@dataclass
class Comparison:
    budget: Money
    required_total: Money
    extra: Money
    results: list[SimulationResult]

    @property
    def benchmark(self) -> SimulationResult:
        return next(r for r in self.results if r.strategy == Strategy.HIGHEST_RATE)

    def premium_over_benchmark(self, r: SimulationResult) -> Money:
        return r.total_interest - self.benchmark.total_interest

    def to_json(self) -> dict:
        b = self.benchmark
        return {
            "budget": self.budget.to_json(),
            "required_total": self.required_total.to_json(),
            "extra": self.extra.to_json(),
            "objective_note": (
                "Minimising remaining borrowing cost is a different objective from "
                "reaching the first payoff sooner or reducing the required monthly "
                "payment. All three are shown."),
            "results": [
                {**r.to_json(),
                 "interest_premium_vs_lowest_cost":
                     (r.total_interest - b.total_interest).to_json()}
                for r in self.results],
        }


def compare_strategies(debts: list[DebtSnapshot], monthly_budget: Money,
                       strategies: Optional[list[Strategy]] = None,
                       horizon_months: Optional[int] = None) -> Comparison:
    strategies = strategies or [Strategy.HIGHEST_RATE, Strategy.SMALLEST_BALANCE,
                                Strategy.EQUAL_SHARES]
    sim = DebtSimulator(debts, monthly_budget)
    results = [sim.run(s, horizon_months=horizon_months) for s in strategies]
    return Comparison(monthly_budget, sim.required_total, sim.extra_budget, results)


# ---------------------------------------------------------------------------
# Mortgage-specific modelling -- section 18
# ---------------------------------------------------------------------------

def amortized_payment(principal: Money, apr: Decimal, months: int) -> Money:
    i = apr / Decimal("12")
    if i == 0:
        return (principal / months).round()
    factor = (Decimal(1) + i) ** months
    return (principal * (i * factor / (factor - Decimal(1)))).round()


@dataclass
class MortgageScenario:
    name: str
    monthly_principal_interest: Money
    months_remaining: int
    lifetime_interest: Money
    upfront_cash: Money
    cash_retained: Money
    note: str

    def to_json(self) -> dict:
        return {"name": self.name,
                "monthly_principal_interest": self.monthly_principal_interest.to_json(),
                "months_remaining": self.months_remaining,
                "lifetime_interest": self.lifetime_interest.to_json(),
                "upfront_cash": self.upfront_cash.to_json(),
                "cash_retained": self.cash_retained.to_json(),
                "note": self.note}


def _run_off(principal: Money, apr: Decimal, payment: Money,
             extra: Money = None, cap_months: int = 720) -> tuple[int, Money]:
    bal = principal
    interest = Money.zero(principal.currency)
    extra = extra or Money.zero(principal.currency)
    m = 0
    while bal.is_positive and m < cap_months:
        m += 1
        i = (bal * (apr / Decimal("12"))).round()
        interest = interest + i
        bal = bal + i
        pay = (payment + extra).min(bal)
        bal = bal - pay
    return m, interest


def mortgage_scenarios(principal: Money, apr: Decimal, months_remaining: int,
                       current_payment: Money, lump_sum: Money = None,
                       extra_monthly: Money = None,
                       available_cash: Money = None,
                       recast_fee: Money = None) -> list[MortgageScenario]:
    """Section 18 requires at least four choices to be modelled side by side."""
    cur = principal.currency
    lump = lump_sum or Money.zero(cur)
    extra = extra_monthly or Money.zero(cur)
    cash = available_cash or Money.zero(cur)
    fee = recast_fee or Money.zero(cur)
    out: list[MortgageScenario] = []

    m, i = _run_off(principal, apr, current_payment)
    out.append(MortgageScenario(
        "Continue scheduled payments", current_payment, m, i,
        Money.zero(cur), cash,
        "The baseline. Principal and interest only; escrow and insurance are "
        "tracked separately and are unchanged by any of these choices."))

    if extra.is_positive:
        m2, i2 = _run_off(principal, apr, current_payment, extra)
        out.append(MortgageScenario(
            f"Add {extra} of principal every month", current_payment + extra, m2, i2,
            Money.zero(cur), cash,
            "The required installment does not fall. Confirm the servicer applies "
            "the extra amount to principal rather than holding it in suspense."))

    if lump.is_positive:
        reduced = (principal - lump).clamp_min_zero()
        m3, i3 = _run_off(reduced, apr, current_payment)
        out.append(MortgageScenario(
            f"Pay {lump} to principal, keep the same installment",
            current_payment, m3, i3, lump, (cash - lump).clamp_min_zero(),
            "Retires the debt sooner. The required payment is unchanged, so the "
            "monthly cash commitment does not improve."))

        remaining_term = months_remaining
        new_pi = amortized_payment(reduced, apr, remaining_term)
        m4, i4 = _run_off(reduced, apr, new_pi)
        out.append(MortgageScenario(
            f"Pay {lump} then request a recast",
            new_pi, m4, i4, lump + fee, (cash - lump - fee).clamp_min_zero(),
            "A scenario until the servicer confirms availability, required "
            "curtailment, fee, eligibility and effective date. Keep the required "
            "payment unchanged until revised terms are verified. If you keep "
            "paying the former amount after a recast, that extra must be modelled "
            "explicitly rather than assumed."))
    return out


def biweekly_comparison(principal: Money, apr: Decimal, monthly_payment: Money,
                        program_fee_per_year: Money = None) -> dict:
    """Section 18: 26 half-payments contribute the equivalent of 13 monthly
    payments. Equalise the annual budget before attributing any saving to
    cadence."""
    cur = principal.currency
    fee = program_fee_per_year or Money.zero(cur)
    half = (monthly_payment / 2).round()
    annual_biweekly = half * 26
    annual_monthly = monthly_payment * 12
    extra_per_year = annual_biweekly - annual_monthly
    equalising_extra = (extra_per_year / 12).round()

    m_plain, i_plain = _run_off(principal, apr, monthly_payment)
    m_equal, i_equal = _run_off(principal, apr, monthly_payment, equalising_extra)

    return {
        "half_payment": half.to_json(),
        "payments_per_year": 26,
        "annual_paid_biweekly": annual_biweekly.to_json(),
        "annual_paid_monthly": annual_monthly.to_json(),
        "extra_paid_per_year": extra_per_year.to_json(),
        "equalising_monthly_extra": equalising_extra.to_json(),
        "program_fee_per_year": fee.to_json(),
        "monthly_only": {"months": m_plain, "interest": i_plain.to_json()},
        "monthly_plus_equal_extra": {"months": m_equal, "interest": i_equal.to_json()},
        "finding": (
            "Once the annual budget is equalised, the two plans are close. Most of "
            "the advertised saving comes from paying more money, not from the "
            "cadence. If the servicer holds partial payments until a full "
            "installment accumulates, the biweekly saving does not occur at all."),
    }


def promo_payoff_reserve(promo_balance: Money, paychecks_remaining: int,
                         expected_other_reduction: Money = None) -> dict:
    """Section 18's promotional payoff reserve."""
    cur = promo_balance.currency
    other = expected_other_reduction or Money.zero(cur)
    need = (promo_balance - other).clamp_min_zero()
    if paychecks_remaining <= 0:
        return {"error": "no eligible paychecks remain before the deadline",
                "amount_outstanding": need.to_json()}
    per = (need / paychecks_remaining).round()
    return {
        "promotional_balance": promo_balance.to_json(),
        "paychecks_remaining": paychecks_remaining,
        "per_paycheck": per.to_json(),
        "total_reserved": (per * paychecks_remaining).to_json(),
        "note": ("Use an internal deadline earlier than the contractual expiration "
                 "by the verified transfer and posting allowance. Minimum payments "
                 "continue while the reserve grows, and the reserve is unavailable "
                 "for sweeps or other extra debt payments. Recompute if a minimum "
                 "payment reduces this same bucket or a fee posts."),
    }
