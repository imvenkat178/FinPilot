"""Tax-adjusted savings versus debt comparison -- spec section 17.

The user-facing name is "net benefit comparison". A positive quoted APY-minus-APR
spread is not guaranteed arbitrage.

Worked example (section 17): $10,000 for one year at 5% APY produces $500 gross
interest; at a simplified 24% federal and 5% state marginal rate the estimated
net interest is $355. Against that baseline:

    reduce a nondeductible 4% interest-only debt by $10,000  ->  $400 (+$45)
    reduce a nondeductible 3% interest-only debt by $10,000  ->  $300 (-$55)
    reduce 4% debt with a fully usable 24% interest deduction -> $304 (-$51)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from ..models import Confidence
from ..money import Money, msum


@dataclass
class TaxProfile:
    """TX01. Stored per tax year with visible verification and approximation
    labels. A simple marginal-rate estimate must never imply a prepared return."""
    tax_year: int = 2026
    jurisdiction: str = "US"
    state: str = "NY"
    filing_status: str = "single"
    federal_marginal: Decimal = Decimal("0.24")
    state_marginal: Decimal = Decimal("0.05")
    itemizes: bool = False
    niit_applies: bool = False
    niit_rate: Decimal = Decimal("0.038")
    verified: bool = False
    entity_id: Optional[str] = None

    @property
    def combined_marginal(self) -> Decimal:
        r = self.federal_marginal + self.state_marginal
        if self.niit_applies:
            r += self.niit_rate
        return r

    def rate_on_interest(self, treasury: bool = False) -> Decimal:
        """Treasury interest is subject to federal income tax but exempt from
        state and local income taxes."""
        r = self.federal_marginal
        if not treasury:
            r += self.state_marginal
        if self.niit_applies:
            r += self.niit_rate
        return r

    def to_json(self) -> dict:
        return {"tax_year": self.tax_year, "jurisdiction": self.jurisdiction,
                "state": self.state, "filing_status": self.filing_status,
                "federal_marginal": str(self.federal_marginal),
                "state_marginal": str(self.state_marginal),
                "combined_marginal": str(self.combined_marginal),
                "itemizes": self.itemizes, "niit_applies": self.niit_applies,
                "verified": self.verified,
                "label": ("verified" if self.verified else
                          "approximate — entered by you and not verified against a "
                          "prepared return")}


@dataclass
class NetBenefitRow:
    label: str
    one_year_net_benefit: Money
    versus_baseline: Money
    liquidity_retained: Money
    residual_debt: Money
    note: str
    debt_applied: Money = field(default_factory=Money.zero)
    debt_interest_benefit: Money = field(default_factory=Money.zero)
    retained_cash_interest: Money = field(default_factory=Money.zero)
    selected_debt_remaining: Optional[Money] = None

    def to_json(self) -> dict:
        return {"label": self.label,
                "one_year_net_benefit": self.one_year_net_benefit.to_json(),
                "versus_baseline": self.versus_baseline.to_json(),
                "liquidity_retained": self.liquidity_retained.to_json(),
                "residual_debt": self.residual_debt.to_json(), "note": self.note,
                "debt_applied": self.debt_applied.to_json(),
                "debt_interest_benefit": self.debt_interest_benefit.to_json(),
                "retained_cash_interest": self.retained_cash_interest.to_json(),
                "selected_debt_remaining": (self.selected_debt_remaining.to_json()
                                             if self.selected_debt_remaining is not None else None)}


@dataclass
class NetBenefitComparison:
    amount: Money
    horizon_days: int
    profile: TaxProfile
    baseline: NetBenefitRow
    rows: list[NetBenefitRow]
    break_even_cash_yield: Optional[Decimal]
    confidence: Confidence
    assumptions: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "amount": self.amount.to_json(), "horizon_days": self.horizon_days,
            "tax_profile": self.profile.to_json(),
            "baseline": self.baseline.to_json(),
            "rows": [r.to_json() for r in self.rows],
            "break_even_cash_yield": (str(round(self.break_even_cash_yield, 5))
                                      if self.break_even_cash_yield is not None else None),
            "confidence": self.confidence.value,
            "assumptions": self.assumptions,
        }


def after_tax_interest(gross: Money, rate: Decimal) -> Money:
    """Approximation: gross interest multiplied by one minus the incremental rate.
    A full comparison computes taxes with and without the action when deductions,
    thresholds or caps make this unreliable."""
    return (gross * (Decimal(1) - rate)).round()


def compare_savings_vs_debt(amount: Money, cash_apy: Decimal,
                            debt_options: list[tuple[str, Decimal, bool, Money] | tuple[str, Decimal, bool]],
                            profile: TaxProfile, horizon_days: int = 365,
                            treasury: bool = False,
                            existing_debt_balance: Optional[Money] = None
                            ) -> NetBenefitComparison:
    """Compare the same starting cash against paying each existing debt.

    Options contain (label, annual_rate, interest_is_deductible, current_balance).
    Legacy triples require an explicit existing_debt_balance; no unknown balance
    is inferred from the amount the user wants to compare.
    """
    cur = amount.currency
    years = Decimal(horizon_days) / Decimal(365)
    gross = (amount * cash_apy * years).round()
    tax_rate = profile.rate_on_interest(treasury=treasury)
    net_interest = after_tax_interest(gross, tax_rate)
    options = []
    for option in debt_options:
        if len(option) == 4:
            label, rate, deductible, balance = option
        elif len(option) == 3 and existing_debt_balance is not None:
            label, rate, deductible = option
            balance = existing_debt_balance
        else:
            raise ValueError("A current balance is required for each debt comparison")
        if balance.currency != cur:
            raise ValueError("Debt and starting cash must use the same currency")
        if balance.is_positive:
            options.append((label, rate, deductible, balance))
    total_debt = (existing_debt_balance if existing_debt_balance is not None else
                  msum([balance for _, _, _, balance in options], cur))

    baseline = NetBenefitRow(
        "Retain the cash in the existing deposit account", net_interest,
        Money.zero(cur), amount, total_debt,
        f"Gross interest {gross} less an estimated {tax_rate * 100}% incremental "
        "tax. Cash retained also preserves liquidity.",
        debt_applied=Money.zero(cur), debt_interest_benefit=Money.zero(cur),
        retained_cash_interest=net_interest)

    rows: list[NetBenefitRow] = []
    break_even_rates = []
    for label, rate, deductible, balance in options:
        applied = min(amount, balance)
        retained = amount - applied
        retained_interest = after_tax_interest((retained * cash_apy * years).round(), tax_rate)
        avoided = (applied * rate * years).round()
        debt_benefit = avoided
        note = f"Avoided interest of {avoided} at {rate * 100}%."
        if deductible and profile.itemizes:
            lost_deduction = (avoided * profile.federal_marginal).round()
            debt_benefit = avoided - lost_deduction
            note = (f"Avoided interest of {avoided}, reduced by the {lost_deduction} "
                    "tax benefit lost when that interest is no longer paid. This "
                    "assumes the deduction is fully usable in your actual case, "
                    "which can be zero.")
        elif deductible and not profile.itemizes:
            note += (" The interest is of a type that can qualify, but your profile "
                     "does not itemize, so no tax benefit is modelled here.")
        note += (f" Apply {applied} to the recorded {balance} balance and retain "
                 f"{retained} in the same deposit account, earning an estimated "
                 f"{retained_interest} after tax over this period. "
                 "Principal repaid usually cannot be recovered without a new "
                 "borrowing or sale decision.")
        benefit = debt_benefit + retained_interest
        rows.append(NetBenefitRow(
            f"Reduce {label} by {applied}; retain {retained} in cash", benefit,
            benefit - net_interest, retained, (total_debt - applied).clamp_min_zero(),
            note, debt_applied=applied, debt_interest_benefit=debt_benefit,
            retained_cash_interest=retained_interest,
            selected_debt_remaining=(balance - applied).clamp_min_zero()))
        # Retained cash earns the same yield on both sides and cancels. Only
        # the amount diverted to debt enters the break-even yield calculation.
        if applied.is_positive and years > 0 and tax_rate < 1:
            break_even_rates.append(debt_benefit.amount /
                ((Decimal(1) - tax_rate) * applied.amount * years))
    break_even = max(break_even_rates, default=None)

    conf = Confidence.EXACT if profile.verified else Confidence.BOUNDED
    return NetBenefitComparison(
        amount=amount, horizon_days=horizon_days, profile=profile,
        baseline=baseline, rows=rows, break_even_cash_yield=break_even,
        confidence=conf,
        assumptions=[
            "Both sides start with the same cash, the same required payments and "
            "the same horizon. A debt payment is capped at its recorded balance; "
            "any remainder stays in the same deposit account and earns the same cash yield.",
            "Debt rows assume the principal difference persists for the whole "
            "period with simple annual interest. Actual amortising loans require "
            "the paired payment schedules from the repayment engine.",
            "The contractual note rate is used for loan interest, not a disclosure "
            "APR that contains already-paid origination costs.",
            "Previously paid fees are sunk for a new extra-payment decision; newly "
            "incurred transfer, prepayment or refinance costs remain relevant.",
            ("Marginal rates are approximate and entered by you. This is not the "
             "preparation of a tax return." if not profile.verified else
             "Marginal rates are from your verified profile."),
        ])


def interest_deduction_check(loan_type: str, profile: TaxProfile) -> dict:
    """TX03. Never assume interest is deductible because of what the loan is
    called. Section 17 is explicit about each of these."""
    rules = {
        "mortgage": (
            "conditional",
            "Requires qualified debt plus applicable itemization, use-of-proceeds "
            "and limit checks. The benefit is the incremental reduction in tax in "
            "your actual case, which can be zero. Being secured by a primary home "
            "is not sufficient."),
        "student_loan": (
            "conditional",
            "Can qualify as an adjustment to income without itemizing, subject to "
            "eligibility and limits. Absence of itemization alone is not "
            "disqualifying."),
        "credit_card": (
            "no",
            "Personal credit-card interest is generally nondeductible."),
        "auto_loan": (
            "conditional",
            "Avoid a blanket rule. Current law includes a conditional deduction for "
            "certain qualifying passenger-vehicle loans for tax years 2025 through "
            "2028. Tax-year-specific eligibility and evidence are required."),
        "investment": (
            "conditional",
            "Investment-interest treatment depends on qualifying use and "
            "limitations, not simply on the pledged collateral."),
    }
    status, note = rules.get(loan_type, ("unknown",
                                         "No verified rule for this loan type."))
    return {"loan_type": loan_type, "deductible": status, "explanation": note,
            "itemizes": profile.itemizes, "tax_year": profile.tax_year,
            "requires_verification": True, "confidence": Confidence.BOUNDED.value}
