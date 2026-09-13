"""Debt alternatives spend the same starting cash and cannot overpay a balance."""
from decimal import Decimal as D

import pytest

from finpilot.ai.tools import ToolRegistry
from finpilot.engine.tax import TaxProfile, compare_savings_vs_debt
from finpilot.money import Money
from finpilot.persistence.codec import encode
from finpilot.seed.demo import demo_household


def m(value):
    return Money(str(value))


def test_excess_cash_stays_in_savings_and_contributes_to_total_benefit():
    comparison = compare_savings_vs_debt(m(10000), D(".05"),
        [("Card", D(".24"), False, m(2000))], TaxProfile())
    row = comparison.rows[0]
    assert comparison.baseline.one_year_net_benefit == m(355)
    assert row.debt_applied == m(2000)
    assert row.liquidity_retained == m(8000)
    assert row.debt_applied + row.liquidity_retained == comparison.amount
    assert row.debt_interest_benefit == m(480)
    assert row.retained_cash_interest == m(284)
    assert row.one_year_net_benefit == m(764)
    assert row.versus_baseline == m(409)
    assert row.selected_debt_remaining == m(0)
    assert row.residual_debt == m(0)
    assert "retain $8,000.00" in row.label
    assert comparison.to_json()["break_even_cash_yield"] == "0.33803"


def test_payment_below_balance_preserves_the_remaining_principal():
    comparison = compare_savings_vs_debt(m(1000), D(".04"),
        [("Loan", D(".10"), False, m(5000))], TaxProfile(federal_marginal=D(0), state_marginal=D(0)))
    row = comparison.rows[0]
    assert row.debt_applied == m(1000)
    assert row.liquidity_retained.is_zero
    assert row.retained_cash_interest.is_zero
    assert row.one_year_net_benefit == m(100)
    assert row.versus_baseline == m(60)
    assert row.selected_debt_remaining == m(4000)


def test_each_debt_scenario_uses_its_own_balance_and_retains_other_debts():
    comparison = compare_savings_vs_debt(m(1000), D(".10"), [
        ("Card", D(".20"), False, m(500)), ("Loan", D(".05"), False, m(2000))],
        TaxProfile(federal_marginal=D(0), state_marginal=D(0)))
    card, loan = comparison.rows
    assert comparison.baseline.residual_debt == m(2500)
    assert card.debt_applied == m(500) and card.liquidity_retained == m(500)
    assert card.one_year_net_benefit == m(150) and card.residual_debt == m(2000)
    assert loan.debt_applied == m(1000) and loan.liquidity_retained.is_zero
    assert loan.one_year_net_benefit == m(50) and loan.residual_debt == m(1500)


def test_lost_deduction_is_limited_to_the_principal_actually_repaid():
    comparison = compare_savings_vs_debt(m(10000), D(".05"),
        [("Eligible loan", D(".04"), True, m(2000))], TaxProfile(itemizes=True))
    row = comparison.rows[0]
    assert row.debt_interest_benefit == m("60.80")
    assert row.retained_cash_interest == m(284)
    assert row.one_year_net_benefit == m("344.80")
    assert row.versus_baseline == m("-10.20")


def test_existing_explicit_balance_api_retains_worked_example():
    comparison = compare_savings_vs_debt(m(10000), D(".05"),
        [("Loan", D(".04"), False)], TaxProfile(), existing_debt_balance=m(10000))
    assert comparison.rows[0].one_year_net_benefit == m(400)
    assert comparison.rows[0].versus_baseline == m(45)
    with pytest.raises(ValueError, match="current balance"):
        compare_savings_vs_debt(m(10000), D(".05"), [("Unknown loan", D(".04"), False)], TaxProfile())


def test_zero_after_tax_cash_yield_does_not_divide_by_zero():
    comparison = compare_savings_vs_debt(m(1000), D(".05"),
        [("Loan", D(".04"), False, m(500))],
        TaxProfile(federal_marginal=D(1), state_marginal=D(0)))
    assert comparison.rows[0].one_year_net_benefit == m(20)
    assert comparison.break_even_cash_yield is None


def test_tool_caps_demo_card_at_actual_balance_without_changing_finances():
    hh = demo_household()
    before = encode(hh)
    result = ToolRegistry(hh).compare_savings_vs_debt(10000)
    card = next(row for row in result["rows"] if "Everyday Rewards Card" in row["label"])
    assert card["debt_applied"]["amount"] == "742.18"
    assert card["liquidity_retained"]["amount"] == "9257.82"
    assert card["selected_debt_remaining"]["amount"] == "0.00"
    assert D(card["debt_interest_benefit"]["amount"]) < D("2399")
    assert "by $10,000.00" not in card["label"]
    assert encode(hh) == before


def test_tool_requires_known_cash_yield_and_respects_explicit_zero_apy():
    hh = demo_household()
    hh.accounts["acc_savings"].apy = None
    assert "known APY" in ToolRegistry(hh).compare_savings_vs_debt(1000)["error"]
    hh.accounts["acc_savings"].apy = D(0)
    result = ToolRegistry(hh).compare_savings_vs_debt(1000)
    assert result["baseline"]["one_year_net_benefit"]["amount"] == "0.00"
