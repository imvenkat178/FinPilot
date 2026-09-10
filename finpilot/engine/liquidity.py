"""Dynamic buffer, liquidity tiers, sweep economics, bill timing.

Spec sections 15 and 16. The buffer worked example (section 15):

    available checking $8,000; unreflected scheduled bills $3,500; projected
    cash spending $600; operating floor $1,000; uncertainty allowance $400
    -> required retained cash $5,500, leaving at most $2,500 for a sweep.

And the daily-value example (section 16): at 5% APY, $10,000 held for 10 days
earns about $13.38; at a combined 29% tax rate the net is about $9.50. Delaying
a $10,000 loan reduction at 24% simple interest on a 365-day basis for the same
10 days costs $65.75, so paying earlier is about $56.25 better.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from ..dates import Calendar, DEFAULT_CALENDAR
from ..models import (Account, AccountType, Confidence, Household,
                      LiquidityTier, Reserve)
from ..money import Money, msum


# ---------------------------------------------------------------------------
# LQ01 -- dynamic buffer
# ---------------------------------------------------------------------------

@dataclass
class BufferResult:
    account_id: str
    account_name: str
    available: Money
    scheduled_outflows: Money
    projected_spending: Money
    operating_floor: Money
    bank_minimum: Money
    uncertainty_allowance: Money
    required_retained: Money
    sweepable: Money
    replenishment_window_days: int
    qualifying_inflow: Money
    assumptions: list[str] = field(default_factory=list)
    confidence: Confidence = Confidence.EXACT

    def to_json(self) -> dict:
        return {
            "account_id": self.account_id, "account_name": self.account_name,
            "available": self.available.to_json(),
            "scheduled_outflows": self.scheduled_outflows.to_json(),
            "projected_spending": self.projected_spending.to_json(),
            "operating_floor": self.operating_floor.to_json(),
            "bank_minimum": self.bank_minimum.to_json(),
            "uncertainty_allowance": self.uncertainty_allowance.to_json(),
            "required_retained": self.required_retained.to_json(),
            "sweepable": self.sweepable.to_json(),
            "replenishment_window_days": self.replenishment_window_days,
            "qualifying_inflow": self.qualifying_inflow.to_json(),
            "assumptions": self.assumptions, "confidence": self.confidence.value,
        }


def compute_buffer(available: Money, scheduled_outflows: Money,
                   projected_spending: Money, operating_floor: Money,
                   uncertainty_allowance: Money,
                   bank_minimum: Optional[Money] = None,
                   qualifying_inflow: Optional[Money] = None,
                   window_days: int = 5,
                   account_id: str = "", account_name: str = "") -> BufferResult:
    cur = available.currency
    bank_min = bank_minimum or Money.zero(cur)
    inflow = qualifying_inflow or Money.zero(cur)

    drawdown = scheduled_outflows + projected_spending - inflow
    drawdown = drawdown.clamp_min_zero()
    floor = operating_floor.max(bank_min)
    required = drawdown + floor + uncertainty_allowance
    sweepable = (available - required).clamp_min_zero()

    return BufferResult(
        account_id=account_id, account_name=account_name, available=available,
        scheduled_outflows=scheduled_outflows, projected_spending=projected_spending,
        operating_floor=operating_floor, bank_minimum=bank_min,
        uncertainty_allowance=uncertainty_allowance, required_retained=required,
        sweepable=sweepable, replenishment_window_days=window_days,
        qualifying_inflow=inflow,
        assumptions=[
            "Assumes none of these outflows is already deducted from the stated "
            "available balance; obligations inside the drawdown are not counted twice.",
            "The higher of your operating floor and any bank minimum is applied, "
            "plus a separately stated uncertainty allowance.",
            f"The replenishment window is {window_days} days, covering non-business "
            "days, cutoffs and holds on the return path.",
            "Existing reservations and provider limits can reduce the executable "
            "amount further.",
        ])


class BufferEngine:
    def __init__(self, household: Household, calendar_: Calendar = DEFAULT_CALENDAR):
        self.hh = household
        self.cal = calendar_

    def for_account(self, account_id: str, window_days: int = 5,
                    uncertainty_pct: Decimal = Decimal("0.10"),
                    horizon_days: int = 30) -> BufferResult:
        from .ledger import LedgerEngine
        acct = self.hh.accounts[account_id]
        cur = acct.currency
        led = LedgerEngine(self.hh)
        fc = led.forecast(account_id, days=horizon_days)

        outflows = msum([-e.amount for d in fc.days for e in d.entries
                         if e.amount.is_negative and e.kind in ("bill", "pending")], cur)
        spending = msum([-e.amount for d in fc.days for e in d.entries
                         if e.amount.is_negative and e.kind == "optional_bill"], cur)
        # only inflows that meet the reliability standard count
        inflow = Money.zero(cur)
        for d in fc.days[:window_days + 1]:
            for e in d.entries:
                if e.amount.is_positive and e.confirmed:
                    inflow = inflow + e.amount

        floor = Money.zero(cur)
        for r in self.hh.reserves.values():
            if r.account_id == account_id and r.purpose.value == "operating_floor":
                floor = floor + r.target
        uncertainty = ((outflows + spending) * uncertainty_pct).round()

        return compute_buffer(acct.available, outflows, spending, floor,
                              uncertainty, acct.minimum_balance, inflow,
                              window_days, acct.id, acct.nickname)


# ---------------------------------------------------------------------------
# LQ02 -- liquidity classification
# ---------------------------------------------------------------------------

TIER_FOR_TYPE = {
    AccountType.CHECKING: LiquidityTier.IMMEDIATE,
    AccountType.CASH: LiquidityTier.IMMEDIATE,
    AccountType.SAVINGS: LiquidityTier.SAME_DAY,
    AccountType.MONEY_MARKET_DEPOSIT: LiquidityTier.SAME_DAY,
    AccountType.BROKERAGE_SWEEP: LiquidityTier.ONE_TO_THREE_DAYS,
    AccountType.MONEY_MARKET_FUND: LiquidityTier.INVESTMENT,
    AccountType.CD: LiquidityTier.DATED,
    AccountType.BROKERAGE: LiquidityTier.INVESTMENT,
    AccountType.RETIREMENT: LiquidityTier.INVESTMENT,
    AccountType.SBLOC: LiquidityTier.CONTINGENT_BORROWING,
    AccountType.MARGIN: LiquidityTier.CONTINGENT_BORROWING,
    AccountType.ESTIMATED_ASSET: LiquidityTier.ESTIMATED,
}

TIER_NOTE = {
    LiquidityTier.IMMEDIATE: "First source for near-term obligations.",
    LiquidityTier.SAME_DAY: "Eligible reserve or sweep destination when return timing fits the bills.",
    LiquidityTier.ONE_TO_THREE_DAYS: "Needs separate account access, coverage and funding-path checks.",
    LiquidityTier.DATED: "A dated resource. Excluded from immediate liquidity unless verified accessible.",
    LiquidityTier.INVESTMENT: "Investment holding. Not automatically equivalent to a bank deposit, and never sold automatically to fund a bill.",
    LiquidityTier.CONTINGENT_BORROWING: "Contingent borrowing capacity, never ordinary cash. Credit availability is not a reliable emergency reserve.",
    LiquidityTier.ESTIMATED: "An estimated value you entered. It contributes to net worth and never to payment capacity.",
}

TIER_LABEL = {
    LiquidityTier.IMMEDIATE: "available now",
    LiquidityTier.SAME_DAY: "same day",
    LiquidityTier.ONE_TO_THREE_DAYS: "1-3 days",
    LiquidityTier.DATED: "on a date",
    LiquidityTier.INVESTMENT: "investment",
    LiquidityTier.CONTINGENT_BORROWING: "borrowing capacity",
    LiquidityTier.ESTIMATED: "estimated",
}


def classify_liquidity(hh: Household) -> list[dict]:
    rows = []
    for a in hh.accounts.values():
        if a.type in (AccountType.CREDIT_CARD, AccountType.PERSONAL_LOAN,
                      AccountType.AUTO_LOAN, AccountType.STUDENT_LOAN,
                      AccountType.MORTGAGE, AccountType.BNPL):
            continue
        tier = a.liquidity_tier or TIER_FOR_TYPE.get(a.type, LiquidityTier.INVESTMENT)
        # for anything past same-day, the meaningful figure is the balance, not
        # an "available" number that reads as spendable
        shown = a.available if tier in (LiquidityTier.IMMEDIATE,
                                        LiquidityTier.SAME_DAY) else a.current
        rows.append({
            "account_id": a.id, "name": a.nickname, "type": a.type.value,
            "balance": a.current.to_json(), "available": shown.to_json(),
            "tier": tier.value, "tier_label": TIER_LABEL[tier],
            "protection": a.protection.value,
            "treatment": TIER_NOTE[tier],
            "counts_as_near_term_cash": tier in (LiquidityTier.IMMEDIATE,
                                                 LiquidityTier.SAME_DAY),
        })
    order = {t: i for i, t in enumerate(LiquidityTier)}
    return sorted(rows, key=lambda r: order[LiquidityTier(r["tier"])])


# ---------------------------------------------------------------------------
# LQ05 -- sweep economics
# ---------------------------------------------------------------------------

def apy_growth(principal: Money, apy: Decimal, days: int) -> Money:
    """Effective daily growth implied by an APY. Section 16 is explicit that this
    is an estimate for comparison, not proof that a bank credits a withdrawable
    amount every day."""
    daily = (Decimal(1) + apy) ** (Decimal(days) / Decimal(365)) - Decimal(1)
    return (principal * daily).round()


def simple_interest(principal: Money, apr: Decimal, days: int,
                    day_count: int = 365) -> Money:
    return (principal * apr * Decimal(days) / Decimal(day_count)).round()


@dataclass
class SweepAssessment:
    amount: Money
    destination: str
    hold_days: int
    gross_benefit: Money
    tax: Money
    net_benefit: Money
    transfer_fee: Money
    economic: bool
    reason: str

    def to_json(self) -> dict:
        return {"amount": self.amount.to_json(), "destination": self.destination,
                "hold_days": self.hold_days,
                "gross_benefit": self.gross_benefit.to_json(),
                "tax": self.tax.to_json(), "net_benefit": self.net_benefit.to_json(),
                "transfer_fee": self.transfer_fee.to_json(),
                "economic": self.economic, "reason": self.reason}


def assess_sweep(amount: Money, source_apy: Decimal, dest_apy: Decimal,
                 hold_days: int, marginal_tax_rate: Decimal,
                 transfer_fee: Optional[Money] = None,
                 minimum_benefit: Optional[Money] = None,
                 destination: str = "savings") -> SweepAssessment:
    cur = amount.currency
    fee = transfer_fee or Money.zero(cur)
    floor_benefit = minimum_benefit or Money(Decimal("1.00"), cur)

    gain = apy_growth(amount, dest_apy, hold_days) - apy_growth(amount, source_apy, hold_days)
    tax = (gain * marginal_tax_rate).round()
    net = gain - tax - fee
    economic = net >= floor_benefit
    if not economic:
        if fee.is_positive and net + fee >= floor_benefit:
            reason = (f"The {fee} transfer fee outweighs the {gain - tax} after-tax "
                      "gain. Suppressed.")
        else:
            reason = (f"The after-tax gain of {net} does not clear your minimum "
                      f"useful benefit of {floor_benefit}. Suppressed to avoid "
                      "repeated uneconomic transfers.")
    else:
        reason = (f"Moving {amount} for {hold_days} days nets {net} after tax and "
                  "fees, above your minimum useful benefit.")
    return SweepAssessment(amount, destination, hold_days, gain, tax, net, fee,
                           economic, reason)


# ---------------------------------------------------------------------------
# LQ04 -- bill timing
# ---------------------------------------------------------------------------

@dataclass
class TimingDecision:
    obligation: str
    due_date: date
    latest_safe_initiation: date
    hold_days: int
    cash_earnings_after_tax: Money
    avoided_borrowing_cost: Money
    net_of_waiting: Money
    recommendation: str
    rationale: str
    credit_utilization_note: Optional[str] = None

    def to_json(self) -> dict:
        return {"obligation": self.obligation, "due_date": self.due_date.isoformat(),
                "latest_safe_initiation": self.latest_safe_initiation.isoformat(),
                "hold_days": self.hold_days,
                "cash_earnings_after_tax": self.cash_earnings_after_tax.to_json(),
                "avoided_borrowing_cost": self.avoided_borrowing_cost.to_json(),
                "net_of_waiting": self.net_of_waiting.to_json(),
                "recommendation": self.recommendation, "rationale": self.rationale,
                "credit_utilization_note": self.credit_utilization_note}


def latest_safe_initiation(due: date, rail_days: int = 2, biller_cutoff_days: int = 1,
                           safety_days: int = 1,
                           calendar_: Calendar = DEFAULT_CALENDAR) -> date:
    """Work backward through cutoff, rail timing and a delay allowance. Section 16:
    'Never shorten a tested funding window merely to claim a few more cents of
    yield.'"""
    d = calendar_.adjust(due, __import__("finpilot.dates", fromlist=["BusinessDayRule"]).BusinessDayRule.PRECEDING)
    return calendar_.subtract_business_days(d, rail_days + biller_cutoff_days + safety_days)


def compare_timing(obligation: str, amount: Money, due: date, today: date,
                   cash_apy: Decimal, debt_apr: Decimal,
                   marginal_tax_rate: Decimal,
                   accrues_daily: bool = True,
                   rail_days: int = 2,
                   utilization_relevant: bool = False,
                   statement_close: Optional[date] = None) -> TimingDecision:
    """Compare holding cash against the cost of delaying payment.

    Adds the CR02 objective from the Version 4 expansion: reported credit
    utilization is a snapshot at statement close, not at the due date, so the
    same dated decision has a third consequence the original engine could not see.
    """
    cur = amount.currency
    latest = latest_safe_initiation(due, rail_days=rail_days)
    hold_days = max(0, (latest - today).days)

    gross = apy_growth(amount, cash_apy, hold_days)
    after_tax = gross - (gross * marginal_tax_rate).round()
    avoided = simple_interest(amount, debt_apr, hold_days) if accrues_daily \
        else Money.zero(cur)
    net = after_tax - avoided

    util_note = None
    if utilization_relevant and statement_close:
        if statement_close < latest:
            util_note = (
                f"Reported utilization is measured at statement close on "
                f"{statement_close.isoformat()}, not at the due date. Paying before "
                "that date lowers the balance the issuer reports while still "
                "preserving the grace period; paying at the due date does not.")

    if net.is_negative:
        rec = "Pay now"
        rationale = (f"Holding the cash for {hold_days} days earns {after_tax} after "
                     f"tax, while the balance accrues {avoided} over the same days. "
                     f"Paying earlier is {(-net)} better under these terms.")
    elif net.is_zero:
        rec = "Either date is equivalent"
        rationale = ("The after-tax cash earnings and the avoided borrowing cost "
                     "are the same over these dates.")
    else:
        rec = f"Initiate by {latest.isoformat()}"
        rationale = (f"There is no interest cost to waiting, so the cash earns "
                     f"{after_tax} after tax over {hold_days} days. Initiate by the "
                     "latest conservative date that still credits on time. A late-fee "
                     "grace interval is not a target payment date.")
    return TimingDecision(obligation, due, latest, hold_days, after_tax, avoided,
                          net, rec, rationale, util_note)
