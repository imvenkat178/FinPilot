"""Deposit insurance, cash programs and collateral stress -- sections 19 and 20.

Coverage rules are versioned by effective date. The specification's own example
is the NCUA trust rule effective 1 December 2026, and the Version 4 expansion
adds the same mechanism one dimension wider so a non-US regime is an
implementation of the same interface rather than a rewrite (GX05).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from ..models import Account, AccountType, Household, ProtectionType
from ..money import Money, msum


# ---------------------------------------------------------------------------
# Versioned regime table -- GX05
# ---------------------------------------------------------------------------

@dataclass
class CoverageRegime:
    code: str
    name: str
    jurisdiction: str
    currency: str
    standard_amount: Decimal
    effective_from: date
    effective_to: Optional[date] = None
    trust_cap: Optional[Decimal] = None
    trust_beneficiary_cap: int = 5
    joint_per_owner: bool = True
    note: str = ""

    def applies_on(self, d: date) -> bool:
        return self.effective_from <= d and (self.effective_to is None or d <= self.effective_to)


REGIMES: list[CoverageRegime] = [
    CoverageRegime("FDIC", "FDIC deposit insurance", "US", "USD",
                   Decimal("250000"), date(2008, 10, 3), None,
                   trust_cap=Decimal("1250000"), trust_beneficiary_cap=5,
                   note="Single-owner deposits in the same ownership category at one "
                        "insured bank are aggregated; adding accounts does not "
                        "multiply coverage. A sole proprietorship's deposits "
                        "generally aggregate with the owner's individual deposits."),
    CoverageRegime("NCUA_PRE", "NCUA share insurance (current trust rules)", "US",
                   "USD", Decimal("250000"), date(2008, 10, 3), date(2026, 11, 30),
                   trust_cap=None,
                   note="Credit-union share insurance under the trust rules in force "
                        "before 1 December 2026."),
    CoverageRegime("NCUA_POST", "NCUA share insurance (combined trust rule)", "US",
                   "USD", Decimal("250000"), date(2026, 12, 1), None,
                   trust_cap=Decimal("1250000"), trust_beneficiary_cap=5,
                   note="From 1 December 2026 a combined trust-account category "
                        "applies, up to $250,000 per eligible beneficiary and "
                        "$1.25 million per owner at one federally insured credit union."),
    # GX05: the same interface, other jurisdictions
    CoverageRegime("FSCS", "FSCS deposit protection", "GB", "GBP",
                   Decimal("85000"), date(2017, 1, 30), date(2025, 11, 30),
                   note="Limit before the December 2025 increase."),
    CoverageRegime("FSCS_120", "FSCS deposit protection", "GB", "GBP",
                   Decimal("120000"), date(2025, 12, 1), None,
                   note="Raised to £120,000 from 1 December 2025. Temporary high "
                        "balance rules apply separately."),
    CoverageRegime("DGS", "EU deposit guarantee scheme", "EU", "EUR",
                   Decimal("100000"), date(2010, 12, 31), None,
                   note="€100,000 per depositor per bank."),
    CoverageRegime("DICGC", "Deposit Insurance and Credit Guarantee Corporation",
                   "IN", "INR", Decimal("500000"), date(2021, 2, 4), None,
                   note="₹5 lakh per depositor per bank."),
    CoverageRegime("FCS", "Financial Claims Scheme", "AU", "AUD",
                   Decimal("250000"), date(2012, 2, 1), None,
                   note="A$250,000 per account holder per ADI. Licences can be "
                        "grouped, so two brands may share one limit."),
]


def regime_for(jurisdiction: str, on: date, kind: str = "bank") -> CoverageRegime:
    candidates = [r for r in REGIMES
                  if r.jurisdiction == jurisdiction and r.applies_on(on)]
    if jurisdiction == "US":
        want = "NCUA" if kind == "credit_union" else "FDIC"
        candidates = [r for r in candidates if r.code.startswith(want)]
    if not candidates:
        raise ValueError(f"no coverage regime for {jurisdiction} on {on}")
    return sorted(candidates, key=lambda r: r.effective_from)[-1]


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

@dataclass
class DeclaredDeposit:
    """IN01. Deposits the user declares but has not connected. They consume
    capacity without authorizing transfers from them."""
    institution_id: str
    owner: str
    category: str
    amount: Money


@dataclass
class CoverageBucket:
    institution_id: str
    institution_name: str
    owner: str
    category: str
    regime: str
    limit: Money
    direct: Money
    via_sweep: Money
    declared: Money
    beneficiaries: int = 0

    @property
    def total(self) -> Money:
        return self.direct + self.via_sweep + self.declared

    @property
    def covered(self) -> Money:
        return self.total.min(self.limit)

    @property
    def uncovered(self) -> Money:
        return (self.total - self.limit).clamp_min_zero()

    def to_json(self) -> dict:
        return {"institution_id": self.institution_id,
                "institution_name": self.institution_name, "owner": self.owner,
                "category": self.category, "regime": self.regime,
                "limit": self.limit.to_json(), "direct": self.direct.to_json(),
                "via_sweep": self.via_sweep.to_json(),
                "declared": self.declared.to_json(), "total": self.total.to_json(),
                "covered": self.covered.to_json(),
                "uncovered": self.uncovered.to_json(),
                "beneficiaries": self.beneficiaries}


@dataclass
class CoverageReport:
    as_of: date
    buckets: list[CoverageBucket]
    unresolved: list[str]
    protection_breakdown: dict[str, Money]
    currency: str = "USD"

    @property
    def total_uncovered(self) -> Money:
        return msum([b.uncovered for b in self.buckets], self.currency)

    def to_json(self) -> dict:
        return {
            "as_of": self.as_of.isoformat(),
            "buckets": [b.to_json() for b in self.buckets],
            "total_uncovered": self.total_uncovered.to_json(),
            "unresolved": self.unresolved,
            "protection_breakdown": {k: v.to_json()
                                     for k, v in self.protection_breakdown.items()},
            "caveats": [
                "This is an estimate based on the ownership information available. "
                "Verify with the institution.",
                "A brand, account nickname, financial app or brokerage screen is "
                "not necessarily a separate insured institution.",
                "Deposit insurance and brokerage protection are different things. "
                "SIPC addresses missing customer assets at a failed member "
                "brokerage, subject to its limits, and does not insure market value.",
                "The application cannot create or retitle ownership to claim more "
                "coverage. An ownership change is a separate legal and banking "
                "process.",
            ],
        }


def build_coverage(hh: Household, on: Optional[date] = None,
                   declared: Optional[list[DeclaredDeposit]] = None,
                   institution_kind: Optional[dict[str, str]] = None,
                   institution_names: Optional[dict[str, str]] = None
                   ) -> CoverageReport:
    on = on or hh.as_of
    kinds = institution_kind or {}
    # an institution id is an internal key; the report shows the bank's name
    names = dict(institution_names or {})
    for a in hh.accounts.values():
        if a.institution_id and a.institution:
            names.setdefault(a.institution_id, a.institution)

    def display(inst_id: str) -> str:
        return names.get(inst_id, inst_id)
    buckets: dict[tuple, CoverageBucket] = {}
    unresolved: list[str] = []
    protection: dict[str, Money] = {}

    def bucket(inst_id: str, inst_name: str, owner: str, category: str,
               kind: str) -> CoverageBucket:
        key = (inst_id, owner, category)
        if key not in buckets:
            reg = regime_for(hh.jurisdiction, on, kind)
            limit_amount = reg.standard_amount
            if category == "joint" and reg.joint_per_owner:
                pass                       # per co-owner interest, aggregated below
            buckets[key] = CoverageBucket(
                inst_id, inst_name, owner, category, reg.code,
                Money(limit_amount, reg.currency),
                Money.zero(hh.base_currency), Money.zero(hh.base_currency),
                Money.zero(hh.base_currency))
        return buckets[key]

    for a in hh.accounts.values():
        if a.type not in (AccountType.CHECKING, AccountType.SAVINGS,
                          AccountType.MONEY_MARKET_DEPOSIT, AccountType.CD,
                          AccountType.BROKERAGE_SWEEP):
            if a.type in (AccountType.MONEY_MARKET_FUND, AccountType.BROKERAGE):
                protection["SIPC — custody protection, not market value"] = \
                    protection.get("SIPC — custody protection, not market value",
                                   Money.zero(hh.base_currency)) + a.current
            continue
        kind = kinds.get(a.institution_id, "bank")
        owner = a.entity_id or "primary"

        if a.sweep_allocations:
            # IN02: look through the program to the actual participating banks
            for inst, amt in a.sweep_allocations.items():
                b = bucket(inst, display(inst), owner, a.ownership_category,
                           kinds.get(inst, "bank"))
                b.via_sweep = b.via_sweep + amt
            allocated = msum(list(a.sweep_allocations.values()), hh.base_currency)
            if allocated < a.current:
                unresolved.append(
                    f"{a.nickname}: {(a.current - allocated)} of the balance has no "
                    "confirmed underlying bank allocation.")
        else:
            if not a.institution_id:
                unresolved.append(f"{a.nickname}: the insured institution is not identified.")
                continue
            b = bucket(a.institution_id, a.institution, owner, a.ownership_category, kind)
            b.direct = b.direct + a.current

        label = {ProtectionType.FDIC: "FDIC — bank deposits",
                 ProtectionType.NCUA: "NCUA — credit union shares",
                 ProtectionType.SIPC: "SIPC — custody protection, not market value",
                 ProtectionType.NONE: "Not covered by deposit or brokerage protection",
                 ProtectionType.UNRESOLVED: "Protection treatment unresolved"}[a.protection]
        protection[label] = protection.get(label, Money.zero(hh.base_currency)) + a.current

    for d in (declared or []):
        b = bucket(d.institution_id, display(d.institution_id), d.owner, d.category,
                   kinds.get(d.institution_id, "bank"))
        b.declared = b.declared + d.amount

    return CoverageReport(on, sorted(buckets.values(),
                                     key=lambda b: -b.total.amount),
                          unresolved, protection, hh.base_currency)


def coverage_remedy(report: CoverageReport, source_institution: str,
                    destination_institution: str,
                    destination_current: Money,
                    buffer_below_limit: Optional[Money] = None) -> dict:
    """Section 19's worked remedy: move enough to leave a stated buffer below the
    source institution's limit, and confirm the move actually reduces the source's
    underlying allocation."""
    cur = destination_current.currency
    buf = buffer_below_limit or Money(Decimal("500"), cur)
    src = next((b for b in report.buckets if b.institution_id == source_institution), None)
    if src is None:
        return {"error": f"no exposure found at {source_institution}"}
    excess = src.uncovered
    if not excess.is_positive:
        return {"institution": source_institution, "excess": excess.to_json(),
                "action": "No uncovered balance at this institution."}
    move = excess + buf
    dst = next((b for b in report.buckets if b.institution_id == destination_institution), None)
    dst_after = destination_current + move
    dst_limit = dst.limit if dst else Money(Decimal("250000"), cur)
    return {
        "source": source_institution,
        "source_total": src.total.to_json(),
        "source_limit": src.limit.to_json(),
        "excess": excess.to_json(),
        "recommended_move": move.to_json(),
        "source_after": (src.total - move).to_json(),
        "destination": destination_institution,
        "destination_after": dst_after.to_json(),
        "destination_headroom_after": (dst_limit - dst_after).clamp_min_zero().to_json(),
        "buffer_below_limit": buf.to_json(),
        "checks_required": [
            "Confirm the selected source movement actually reduces this "
            "institution's underlying allocation, not only the app's view of it.",
            "Check destination capacity, access timing and ownership before executing.",
            "Future interest and program reallocations change this figure; it is a "
            "forecast, not a guarantee of insurance eligibility.",
        ],
    }


# ---------------------------------------------------------------------------
# HN03/HN04 -- collateral stress
# ---------------------------------------------------------------------------

@dataclass
class CollateralStress:
    collateral_value: Money
    advance_rate: Decimal
    capacity: Money
    drawn: Money
    headroom: Money
    scenario: str

    def to_json(self) -> dict:
        return {"scenario": self.scenario,
                "collateral_value": self.collateral_value.to_json(),
                "advance_rate": str(self.advance_rate),
                "capacity": self.capacity.to_json(), "drawn": self.drawn.to_json(),
                "headroom": self.headroom.to_json(),
                "deficiency": (self.drawn - self.capacity).clamp_min_zero().to_json()}


def stress_collateral(collateral_value: Money, advance_rate: Decimal, drawn: Money,
                      declines: list[Decimal] = None,
                      reduced_advance_rates: list[Decimal] = None) -> dict:
    declines = declines or [Decimal("0.30")]
    reduced = reduced_advance_rates or []
    rows: list[CollateralStress] = []

    def row(val: Money, rate: Decimal, label: str) -> CollateralStress:
        cap = (val * rate).round()
        return CollateralStress(val, rate, cap, drawn,
                                (cap - drawn).clamp_min_zero(), label)

    def pct(x: Decimal) -> str:
        q = (x * 100).quantize(Decimal("0.01"))
        return f"{q:f}".rstrip("0").rstrip(".")

    rows.append(row(collateral_value, advance_rate, "Current"))
    for d in declines:
        val = (collateral_value * (Decimal(1) - d)).round()
        rows.append(row(val, advance_rate, f"{pct(d)}% market decline"))
        for r in reduced:
            rows.append(row(val, r, f"{pct(d)}% decline and advance rate cut to "
                                    f"{pct(r)}%"))
    return {
        "rows": [r.to_json() for r in rows],
        "warnings": [
            "A securities-backed line can be a demand loan with collateral calls "
            "and forced-sale consequences. Non-purpose proceeds cannot be used to "
            "buy or trade securities.",
            "An apparent yield spread does not justify an automatic new draw, "
            "increased leverage, transfer of collateral or security sale. These are "
            "outside the automatic cash-routing policy.",
            "Credit availability is not a reliable emergency reserve. Actual lender "
            "terms and account-level concentration rules determine real requirements.",
        ],
    }
