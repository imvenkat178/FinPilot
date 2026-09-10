"""Money, dates and ledger invariants.

Section 13: "Money uses decimal arithmetic and explicit currency... A current
balance is never overwritten by a forecast balance... Internal transfers cannot
inflate income or spending."

Section 24: "A paycheck or internal transfer never creates duplicate income; a
card repayment never creates duplicate purchase spending."

The multi-currency tests are the GX01 foundation from the Version 4 expansion:
currency is a first-class attribute from the start, so the ledger never has to
be migrated for a second market.
"""
from datetime import date
from decimal import Decimal as D

import pytest

from finpilot.dates import (BusinessDayRule, Cadence, Calendar, DayRule,
                            Schedule, day_in_month)
from finpilot.engine.ledger import LedgerEngine
from finpilot.models import AccountType, Transaction, TxKind, TxState
from finpilot.money import CurrencyMismatch, Money, allocate, msum
from finpilot.seed.demo import demo_household


def M(v, c="USD"):
    return Money(D(str(v)), c)


# ---------------------------------------------------------------------------
# money
# ---------------------------------------------------------------------------

def test_decimal_arithmetic_has_no_float_error():
    total = msum([M("0.1")] * 10)
    assert str(total.round().amount) == "1.00"


def test_unlike_currencies_never_combine_silently():
    with pytest.raises(CurrencyMismatch):
        M(100, "USD") + M(100, "EUR")
    with pytest.raises(CurrencyMismatch):
        M(100, "USD") < M(100, "GBP")


@pytest.mark.parametrize("currency,places", [
    ("USD", 2), ("GBP", 2), ("JPY", 0), ("KWD", 3),
])
def test_minor_units_are_per_currency(currency, places):
    m = M("1.23456", currency)
    assert -m.round().amount.as_tuple().exponent == places


def test_allocation_assigns_the_final_cent_deterministically():
    """Section 14: 'Rounding uses cents, with the final cent assigned
    deterministically.'"""
    parts = allocate(M("100.00"), [D(1), D(1), D(1)])
    assert [str(p.amount) for p in parts] == ["33.34", "33.33", "33.33"]
    assert msum(parts) == M("100.00")
    again = allocate(M("100.00"), [D(1), D(1), D(1)])
    assert [p.amount for p in parts] == [p.amount for p in again]


def test_allocation_always_reconciles_to_the_total():
    for total in ("0.01", "7.77", "1000.00", "3333.33"):
        for n in range(1, 8):
            parts = allocate(M(total), [D(1)] * n)
            assert msum(parts) == M(total), (total, n)


def test_clamp_and_min_max():
    assert M(-5).clamp_min_zero() == M(0)
    assert M(5).min(M(9)) == M(5)
    assert M(5).max(M(9)) == M(9)


# ---------------------------------------------------------------------------
# dates
# ---------------------------------------------------------------------------

def test_a_day_that_does_not_exist_uses_the_documented_rule():
    """Section 14 requires 'a documented rule for a requested day that does not
    exist in that month'."""
    assert day_in_month(2026, 2, 31, DayRule.LAST_DAY_OF_MONTH) == date(2026, 2, 28)
    assert day_in_month(2026, 2, 31, DayRule.SKIP) is None
    assert day_in_month(2026, 2, 31, DayRule.FIRST_OF_NEXT) == date(2026, 3, 1)
    assert day_in_month(2028, 2, 31, DayRule.LAST_DAY_OF_MONTH) == date(2028, 2, 29)


def test_business_day_adjustment_prefers_earlier_for_payments():
    cal = Calendar("US")
    saturday = date(2026, 9, 12)
    assert cal.adjust(saturday, BusinessDayRule.PRECEDING) == date(2026, 9, 11)
    assert cal.adjust(saturday, BusinessDayRule.FOLLOWING) == date(2026, 9, 14)


def test_holidays_are_excluded_from_business_days():
    cal = Calendar("US")
    assert not cal.is_business_day(date(2026, 7, 3))     # observed 4 July
    assert not cal.is_business_day(date(2026, 12, 25))
    assert cal.is_business_day(date(2026, 9, 10))


def test_semimonthly_and_biweekly_produce_different_calendars():
    """'Biweekly and twice-monthly pay schedules need different calendars.'"""
    semi = Schedule(Cadence.SEMIMONTHLY, date(2026, 1, 1),
                    day_of_month=1, second_day_of_month=15)
    bi = Schedule(Cadence.BIWEEKLY, date(2026, 1, 2))
    a = semi.occurrences(date(2026, 1, 1), date(2026, 12, 31))
    b = bi.occurrences(date(2026, 1, 1), date(2026, 12, 31))
    assert len(a) == 24
    assert len(b) == 26


def test_last_business_day_of_a_month():
    cal = Calendar("US")
    assert cal.last_business_day(2026, 5) == date(2026, 5, 29)   # 30/31 weekend


# ---------------------------------------------------------------------------
# ledger integrity
# ---------------------------------------------------------------------------

def test_an_internal_transfer_is_neither_income_nor_spending():
    tx = Transaction(kind=TxKind.INTERNAL_TRANSFER, amount=M(-500),
                     state=TxState.POSTED)
    assert not tx.counts_as_income
    assert not tx.counts_as_spending


def test_a_card_repayment_is_not_duplicate_spending():
    """'Credit-card payments settle liabilities; the original purchases already
    represent spending.'"""
    purchase = Transaction(kind=TxKind.PURCHASE, amount=M(-80), state=TxState.POSTED)
    repayment = Transaction(kind=TxKind.CARD_REPAYMENT, amount=M(-80),
                            state=TxState.POSTED)
    assert purchase.counts_as_spending
    assert not repayment.counts_as_spending


def test_a_pending_deposit_is_not_counted_as_received_income():
    tx = Transaction(kind=TxKind.INCOME, amount=M(3000), state=TxState.PENDING)
    assert not tx.counts_as_income


def test_a_provisional_credit_is_not_income():
    """FD02 from the Version 4 expansion."""
    tx = Transaction(kind=TxKind.PROVISIONAL_CREDIT, amount=M(240),
                     state=TxState.POSTED)
    assert not tx.counts_as_income
    assert not tx.counts_as_spending


def test_spending_allowance_uses_the_low_point_not_the_ending_balance():
    """'The spending estimate must respect the lowest balance over the planning
    horizon, not just the ending balance.'"""
    hh = demo_household()
    led = LedgerEngine(hh)
    fc = led.forecast("acc_checking", days=45)
    allowance = led.spending_allowance("acc_checking", 45)
    assert allowance.low_point_balance <= fc.ending
    assert allowance.amount <= fc.low_point.closing


def test_a_credit_limit_is_never_spendable():
    hh = demo_household()
    card = hh.accounts["acc_card_a"]
    assert card.spendable.is_zero
    assert hh.accounts["acc_401k"].spendable.is_zero
    assert hh.accounts["acc_home"].spendable.is_zero


def test_estimated_assets_are_labelled_and_never_cash():
    hh = demo_household()
    assert hh.estimated_assets() == M(412000)
    assert hh.accounts["acc_home"] not in hh.cash_accounts
    assert hh.accounts["acc_home"].provenance.verification.value == "estimated"


def test_net_worth_reconciles_with_assets_minus_debt():
    hh = demo_household()
    assets = msum([a.current for a in hh.accounts.values()
                   if a.type not in (AccountType.CREDIT_CARD, AccountType.AUTO_LOAN,
                                     AccountType.STUDENT_LOAN, AccountType.MORTGAGE,
                                     AccountType.PERSONAL_LOAN, AccountType.SBLOC,
                                     AccountType.MARGIN, AccountType.BNPL)])
    assert hh.net_worth() == assets - hh.total_debt()


def test_protected_reserves_are_excluded_from_spendable():
    hh = demo_household()
    assert hh.protected_reserves().is_positive
    spendable = msum([a.spendable for a in hh.accounts.values()])
    assert (spendable - hh.protected_reserves()) < spendable
