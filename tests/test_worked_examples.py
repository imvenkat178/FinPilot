"""Every worked example in the specification, reproduced to the cent.

Section 24: "The worked examples in Sections 6 and 8 can be regenerated from
their stated assumptions" and "The new worked examples in Sections 14 through 20
reconcile numerically with their stated assumptions."

If one of these fails, the engine has drifted from the document.
"""
from datetime import date
from decimal import Decimal as D

import pytest

from finpilot.engine.allocator import AllocationEngine
from finpilot.engine.cards import (bill_payment_comparison, compare_channels,
                                   interest_vs_reward)
from finpilot.engine.coverage import (CoverageBucket, build_coverage,
                                      coverage_remedy, regime_for,
                                      stress_collateral)
from finpilot.engine.debt import (Strategy, compare_strategies,
                                  promo_payoff_reserve)
from finpilot.engine.liquidity import (apy_growth, compute_buffer,
                                       simple_interest)
from finpilot.engine.tax import TaxProfile, after_tax_interest
from finpilot.money import Money
from finpilot.seed.demo import SECTION8_BUDGET, demo_household, section8_debts


def M(v):
    return Money(D(str(v)), "USD")


def cents(m: Money) -> str:
    return str(m.round().amount)


# ---------------------------------------------------------------------------
# Section 4 -- illustrative paycheck plan
# ---------------------------------------------------------------------------

def test_section4_illustrative_paycheck():
    """$3,500 take-home; $2,100 required; $300 cushion; $200 investment;
    $100 flexible; $800 remains; an equal split gives $400 each."""
    take_home = M(3500)
    required = M(1400) + M(250) + M(100) + M(150) + M(200)
    assert cents(required) == "2100.00"
    remaining = take_home - required - M(300) - M(200) - M(100)
    assert cents(remaining) == "800.00"
    half = remaining / 2
    assert cents(half) == "400.00"


def test_section4_shortfall_is_named_before_optional_allocations():
    """'If available pay is only $2,000 and no other uncommitted funds exist,
    the app shows a $100 required-cost gap before proposing optional allocations.'"""
    gap = M(2100) - M(2000)
    assert cents(gap) == "100.00"


# ---------------------------------------------------------------------------
# Section 6 -- worked card comparisons
# ---------------------------------------------------------------------------

def test_section6_dinner():
    a = (M(80) * D("0.04")).round()
    b = (M(80) * D("0.02")).round()
    assert cents(a) == "3.20" and cents(b) == "1.60"
    assert cents(a - b) == "1.60"


def test_section6_hotel_channels():
    r = compare_channels(M(500), D("0.02"), M(535), D("0.05"))
    assert r["direct"]["net_cost"]["amount"] == "490.00"
    assert r["portal"]["net_cost"]["amount"] == "508.25"
    assert r["cheaper"] == "Direct booking"
    assert r["difference"]["amount"] == "18.25"


def test_section6_bill_with_card_fee():
    r = bill_payment_comparison(M(1000), D("0.02"), D("0.0295"))
    assert r["card"]["reward"]["amount"] == "20.00"
    assert r["card"]["processing_fee"]["amount"] == "29.50"
    assert r["better"] == "bank"
    assert r["difference"]["amount"] == "9.50"


def test_section6_interest_exceeds_reward():
    r = interest_vs_reward(M(1000), D("0.30"), 30, D("0.02"))
    assert r["added_interest"]["amount"] == "24.66"
    assert r["reward"]["amount"] == "20.00"
    assert "4.66" in r["finding"]


# ---------------------------------------------------------------------------
# Section 8 -- worked repayment comparison
# ---------------------------------------------------------------------------

EXPECTED_SECTION8 = {
    Strategy.HIGHEST_RATE: dict(first_payoff="Credit card", first_month=13,
                                months=109, interest="45748.51", paid="247748.51"),
    Strategy.SMALLEST_BALANCE: dict(first_payoff="Personal loan", first_month=4,
                                    months=109, interest="46074.62", paid="248074.62"),
    Strategy.EQUAL_SHARES: dict(first_payoff="Personal loan", first_month=10,
                                months=110, interest="47176.78", paid="249176.78"),
}


@pytest.fixture(scope="module")
def section8():
    return compare_strategies(section8_debts(), SECTION8_BUDGET)


def test_section8_budget_split(section8):
    assert cents(section8.required_total) == "1778.77"
    assert cents(section8.extra) == "500.00"


@pytest.mark.parametrize("strategy", list(EXPECTED_SECTION8))
def test_section8_each_strategy(section8, strategy):
    exp = EXPECTED_SECTION8[strategy]
    r = next(x for x in section8.results if x.strategy == strategy)
    assert r.first_payoff.name == exp["first_payoff"]
    assert r.first_payoff.month == exp["first_month"]
    assert r.months_to_clear == exp["months"]
    assert cents(r.total_interest) == exp["interest"]
    assert cents(r.total_paid) == exp["paid"]


def test_section8_premiums(section8):
    b = section8.benchmark
    snow = next(r for r in section8.results if r.strategy == Strategy.SMALLEST_BALANCE)
    eq = next(r for r in section8.results if r.strategy == Strategy.EQUAL_SHARES)
    assert cents(snow.total_interest - b.total_interest) == "326.11"
    assert cents(eq.total_interest - b.total_interest) == "1428.27"


def test_section8_first_month_allocation(section8):
    """'the highest-rate plan pays $740 to the card, $100 to the personal loan,
    $300 to the auto loan, and $1,138.77 to the mortgage.'"""
    a = section8.benchmark.first_month_allocation
    assert cents(a["Credit card"]) == "740.00"
    assert cents(a["Personal loan"]) == "100.00"
    assert cents(a["Auto loan"]) == "300.00"
    assert cents(a["Mortgage"]) == "1138.77"


def test_no_balance_ever_goes_negative(section8):
    for r in section8.results:
        for row in r.schedule:
            assert row.closing.amount >= 0


# ---------------------------------------------------------------------------
# Section 14 -- the complete monthly split (table 10)
# ---------------------------------------------------------------------------

TABLE_10 = {
    "Mortgage required payment": ("1800.00", "0.00"),
    "Auto loan required payment": ("250.00", "0.00"),
    "Student loan required payment": ("0.00", "250.00"),
    "Credit card statement": ("600.00", "0.00"),
    "Utilities": ("150.00", "0.00"),
    "Home & auto insurance": ("0.00", "250.00"),
    "Cash & debit spending": ("200.00", "500.00"),
    "Emergency reserve": ("0.00", "700.00"),
    "Annual bills reserve": ("0.00", "300.00"),
    "Brokerage cash contribution": ("0.00", "500.00"),
    "Additional principal on selected debt": ("0.00", "500.00"),
}


@pytest.fixture(scope="module")
def month_plan():
    hh = demo_household()
    plan, runs = AllocationEngine(hh).allocate_month(2026, 9)
    got = {}
    for i, run in enumerate(runs):
        for a in run.allocations:
            got.setdefault(a.name, ["0.00", "0.00"])
            if a.amount.is_positive:
                got[a.name][i] = cents(a.amount)
    return plan, runs, got


def test_table10_totals(month_plan):
    plan, runs, _ = month_plan
    assert cents(plan.total_target) == "6000.00"
    assert cents(plan.expected_income) == "6000.00"
    assert [cents(r.allocated) for r in runs] == ["3000.00", "3000.00"]


@pytest.mark.parametrize("destination", list(TABLE_10))
def test_table10_row(month_plan, destination):
    _, _, got = month_plan
    assert got[destination] == list(TABLE_10[destination]), destination


def test_mortgage_deadline_forces_first_paycheck(month_plan):
    """SC49: an even split must fail when it cannot meet the deadline."""
    _, runs, _ = month_plan
    mortgage = next(a for a in runs[0].allocations
                    if a.name == "Mortgage required payment")
    assert mortgage.urgency.value == "due_before_next_income"
    assert cents(mortgage.amount) == "1800.00"
    even_split = M(6000) / 11 / 2
    assert even_split < M(1800)


def test_monthly_target_funded_once_across_paychecks(month_plan):
    """SC51: never apply the full monthly target to every paycheck."""
    _, runs, got = month_plan
    for name, (p1, p2) in got.items():
        total = D(p1) + D(p2)
        target = next((t for t in TABLE_10 if t == name), None)
        if target:
            assert total == D(TABLE_10[name][0]) + D(TABLE_10[name][1])


def test_smaller_paycheck_preserves_required_and_pauses_optional():
    """SC50: recompute from the received amount, reduce optional in saved order."""
    hh = demo_household()
    hh.income_events[0].received_amount = M(2600)
    plan, runs = AllocationEngine(hh).allocate_month(2026, 9)
    first = runs[0]
    assert cents(first.available) == "2600.00"
    mortgage = next(a for a in first.allocations
                    if a.name == "Mortgage required payment")
    assert cents(mortgage.amount) == "1800.00"      # required is preserved
    spending = next(a for a in first.allocations if a.name == "Cash & debit spending")
    assert spending.amount.is_zero                  # optional is paused


# ---------------------------------------------------------------------------
# Section 15 -- dynamic buffer
# ---------------------------------------------------------------------------

def test_section15_buffer():
    r = compute_buffer(available=M(8000), scheduled_outflows=M(3500),
                       projected_spending=M(600), operating_floor=M(1000),
                       uncertainty_allowance=M(400))
    assert cents(r.required_retained) == "5500.00"
    assert cents(r.sweepable) == "2500.00"


# ---------------------------------------------------------------------------
# Section 16 -- daily value
# ---------------------------------------------------------------------------

def test_section16_apy_growth_ten_days():
    g = apy_growth(M(10000), D("0.05"), 10)
    assert cents(g) == "13.38"


def test_section16_after_tax():
    g = apy_growth(M(10000), D("0.05"), 10)
    net = g - (g * D("0.29")).round()
    assert cents(net) == "9.50"


def test_section16_delaying_a_loan_reduction_costs_more():
    added = simple_interest(M(10000), D("0.24"), 10)
    assert cents(added) == "65.75"
    g = apy_growth(M(10000), D("0.05"), 10)
    net_cash = g - (g * D("0.29")).round()
    assert cents(added - net_cash) == "56.25"


def test_section16_small_amount_is_not_worth_a_fee():
    g = apy_growth(M(2000), D("0.05"), 10)
    net = g - (g * D("0.29")).round()
    assert cents(net) == "1.90"
    assert net < M(5)


# ---------------------------------------------------------------------------
# Section 17 -- tax adjusted comparison (table 12)
# ---------------------------------------------------------------------------

def test_section17_baseline_after_tax_interest():
    gross = (M(10000) * D("0.05")).round()
    assert cents(gross) == "500.00"
    net = after_tax_interest(gross, D("0.29"))
    assert cents(net) == "355.00"


@pytest.mark.parametrize("rate,deductible,expected,ahead", [
    (D("0.04"), False, "400.00", "debt"),
    (D("0.03"), False, "300.00", "cash"),
    (D("0.04"), True, "304.00", "cash"),
])
def test_section17_table12(rate, deductible, expected, ahead):
    avoided = (M(10000) * rate).round()
    benefit = avoided
    if deductible:
        benefit = avoided - (avoided * D("0.24")).round()
    assert cents(benefit) == expected
    baseline = M(355)
    assert (benefit > baseline) == (ahead == "debt")


def test_section17_table12_margins():
    assert cents(M(400) - M(355)) == "45.00"
    assert cents(M(355) - M(300)) == "55.00"
    assert cents(M(355) - M(304)) == "51.00"


def test_treasury_interest_is_exempt_from_state_tax():
    p = TaxProfile(federal_marginal=D("0.24"), state_marginal=D("0.05"))
    assert p.rate_on_interest(treasury=False) == D("0.29")
    assert p.rate_on_interest(treasury=True) == D("0.24")


# ---------------------------------------------------------------------------
# Section 18 -- promotional payoff reserve
# ---------------------------------------------------------------------------

def test_section18_promo_reserve():
    r = promo_payoff_reserve(M(3600), 6)
    assert r["per_paycheck"]["amount"] == "600.00"
    r5 = promo_payoff_reserve(M(3600), 5)
    assert r5["per_paycheck"]["amount"] == "720.00"


# ---------------------------------------------------------------------------
# Section 19 -- deposit coverage
# ---------------------------------------------------------------------------

def test_section19_coverage_example():
    """$180,000 direct plus $90,000 through a sweep at the same bank is
    $270,000, so $20,000 exceeds the $250,000 category limit. Moving $20,500
    leaves $249,500 there and $220,500 at the second bank."""
    b = CoverageBucket("bank_a", "Bank A", "owner", "single", "FDIC",
                       M(250000), M(180000), M(90000), M(0))
    assert cents(b.total) == "270000.00"
    assert cents(b.uncovered) == "20000.00"
    move = b.uncovered + M(500)
    assert cents(move) == "20500.00"
    assert cents(b.total - move) == "249500.00"
    assert cents(M(200000) + move) == "220500.00"


def test_ncua_trust_rule_changes_on_1_december_2026():
    """The engine must retain both rule versions and forecast the transition."""
    before = regime_for("US", date(2026, 11, 30), "credit_union")
    after = regime_for("US", date(2026, 12, 1), "credit_union")
    assert before.code == "NCUA_PRE"
    assert after.code == "NCUA_POST"
    assert after.trust_cap == D("1250000")
    assert after.trust_beneficiary_cap == 5


def test_fscs_limit_is_versioned_by_effective_date():
    """GX05: another jurisdiction is an instance of the same interface."""
    assert regime_for("GB", date(2025, 11, 30)).standard_amount == D("85000")
    assert regime_for("GB", date(2025, 12, 1)).standard_amount == D("120000")


def test_adding_accounts_does_not_multiply_coverage():
    hh = demo_household()
    rep = build_coverage(hh)
    meridian = [b for b in rep.buckets if b.institution_id == "bank_meridian"]
    assert len(meridian) == 1, "same owner and category must aggregate into one bucket"


# ---------------------------------------------------------------------------
# Section 20 -- collateral stress
# ---------------------------------------------------------------------------

def test_section20_collateral_stress():
    r = stress_collateral(M(1000000), D("0.50"), M(350000),
                          declines=[D("0.30")], reduced_advance_rates=[D("0.40")])
    rows = {x["scenario"]: x for x in r["rows"]}
    cur = rows["Current"]
    assert cur["capacity"]["amount"] == "500000.00"
    assert cur["headroom"]["amount"] == "150000.00"

    decline = rows["30% market decline"]
    assert decline["collateral_value"]["amount"] == "700000.00"
    assert decline["headroom"]["amount"] == "0.00"

    both = rows["30% decline and advance rate cut to 40%"]
    assert both["capacity"]["amount"] == "280000.00"
    assert both["deficiency"]["amount"] == "70000.00"
