"""Payment races, live rule checks, and actual settlement/return postings."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Event

import pytest

from finpilot.execution.engine import (ExecutionEngine, LegKind, LegState,
                                      PaymentLeg, ProviderFault, TransferGroup)
from finpilot.models import AuthorizationMode, Mandate, PolicyPurpose, TxKind, TxState, now
from finpilot.money import Money
from finpilot.seed.demo import demo_household


def money(amount):
    return Money(Decimal(str(amount)), "USD")


def make_leg(hh, group_id="g1", amount="500", destination="acc_savings", **kwargs):
    return PaymentLeg(
        group_id=group_id, source_account_id="acc_checking",
        destination_account_id=destination, destination_label="Test payment",
        amount=money(amount), purpose=kwargs.pop("purpose", PolicyPurpose.GOAL),
        scheduled_for=hh.as_of,
        mandate=hh.policies[kwargs["policy_id"]].mandate if kwargs.get("policy_id") else
                Mandate(mode=AuthorizationMode.STANDING, authorized_at=now(),
                        authorized_by="user_primary", per_run_cap=money(2500)),
        **kwargs)


def register(engine, payment):
    engine.groups[payment.group_id] = TransferGroup(id=payment.group_id, legs=[payment])
    payment.transition(LegState.AUTHORIZED, "test authorization")


@pytest.mark.parametrize("same_intent", [True, False])
def test_preflight_and_reservation_are_one_serialized_operation(same_intent):
    hh = demo_household()
    engine = ExecutionEngine(hh)
    first = make_leg(hh, amount="1500")
    second = make_leg(hh, group_id="g2", amount="1500",
                      destination="acc_savings" if same_intent else "acc_brokerage")
    for payment in (first, second):
        register(engine, payment)
    original_preflight = engine.preflight
    first_checked, allow_first, second_started, second_checked = (Event() for _ in range(4))

    def controlled_preflight(payment):
        result = original_preflight(payment)
        if payment is first:
            first_checked.set()
            assert allow_first.wait(3), "First execution was not released"
        else:
            second_checked.set()
        return result

    def run_second():
        second_started.set()
        return engine.execute(second)

    engine.preflight = controlled_preflight
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_result = pool.submit(engine.execute, first)
        assert first_checked.wait(3)
        second_result = pool.submit(run_second)
        assert second_started.wait(3)
        try:
            assert not second_checked.wait(.1), "Another preflight raced the reservation"
        finally:
            allow_first.set()
        first_result.result(timeout=3)
        second_result.result(timeout=3)

    assert first.state == LegState.PROCESSING
    assert second.state == LegState.FAILED
    assert len(engine.provider.submitted) == 1
    assert engine.reservations["acc_checking"] == money(1500)
    engine.settle(first)
    engine.settle(second)
    assert hh.accounts["acc_checking"].current == money(1700)
    assert len(hh.transactions) == 2


def test_parallel_settlement_and_return_do_not_post_twice():
    hh = demo_household()
    engine = ExecutionEngine(hh)
    payment = make_leg(hh)
    register(engine, payment)
    engine.execute(payment)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(engine.settle, [payment] * 4))
    assert hh.accounts["acc_checking"].current == money(2700)
    assert len(hh.transactions) == 2
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(engine.apply_return, [payment] * 4))
    assert hh.accounts["acc_checking"].current == money(3200)
    assert len(hh.transactions) == 4


@pytest.mark.parametrize("flag, check", [("paused", "policy_not_paused"),
                                        ("skip_next", "policy_not_skipped")])
def test_a_rule_change_blocks_a_previously_built_leg(flag, check):
    hh = demo_household()
    engine = ExecutionEngine(hh)
    payment = make_leg(hh, policy_id="pol_emergency")
    register(engine, payment)
    assert engine.preflight(payment).ok
    setattr(hh.policies["pol_emergency"], flag, True)
    assert not engine.preflight(payment).checks[check]
    engine.execute(payment)
    assert payment.state == LegState.FAILED
    assert not engine.provider.calls
    assert not engine.reservations
    assert payment.mandate.used_this_period.is_zero
    assert not hh.transactions


def test_preflight_checks_do_not_consume_skip_or_authorization():
    hh = demo_household()
    engine = ExecutionEngine(hh)
    payment = make_leg(hh, policy_id="pol_emergency")
    hh.policies["pol_emergency"].skip_next = True
    for _ in range(2):
        assert not engine.preflight(payment).ok
    assert hh.policies["pol_emergency"].skip_next
    assert payment.mandate.used_this_period.is_zero
    assert payment.state == LegState.DRAFT


def test_settlement_records_balanced_account_activity_once():
    hh = demo_household()
    engine = ExecutionEngine(hh)
    payment = make_leg(hh)
    register(engine, payment)
    engine.execute(payment)
    assert not hh.transactions
    engine.settle(payment, credited=False)
    transactions = hh.transactions
    assert len(transactions) == 2
    by_account = {tx.account_id: tx for tx in transactions}
    debit, credit = by_account["acc_checking"], by_account["acc_savings"]
    assert debit.amount == money(-500)
    assert credit.amount == money(500)
    assert debit.linked_tx_id == credit.id
    assert credit.linked_tx_id == debit.id
    assert all(tx.transfer_group_id == payment.group_id for tx in transactions)
    assert all(tx.date == hh.as_of and tx.state == TxState.POSTED for tx in transactions)
    assert all(tx.kind == TxKind.INTERNAL_TRANSFER for tx in transactions)
    assert not any(tx.counts_as_income or tx.counts_as_spending for tx in transactions)
    engine.settle(payment)
    engine.settle(payment)
    assert len(transactions) == 2
    assert all(tx.state == TxState.RECONCILED for tx in transactions)


def test_post_settlement_return_retains_linked_reversal_activity():
    hh = demo_household()
    engine = ExecutionEngine(hh)
    payment = make_leg(hh)
    register(engine, payment)
    engine.execute(payment)
    engine.settle(payment)
    originals = list(hh.transactions)
    engine.apply_return(payment, "Bank returned payment")
    returns = [tx for tx in hh.transactions if tx not in originals]
    assert len(returns) == 2
    assert all(tx.state == TxState.REVERSED for tx in originals)
    assert {tx.linked_tx_id for tx in returns} == {tx.id for tx in originals}
    for original in originals:
        reverse = next(tx for tx in returns if tx.linked_tx_id == original.id)
        assert reverse.amount == -original.amount
        assert reverse.account_id == original.account_id
        assert reverse.transfer_group_id == payment.group_id
    assert sum(tx.amount.amount for tx in hh.transactions) == 0
    engine.apply_return(payment)
    assert len(hh.transactions) == 4


@pytest.mark.parametrize("fault", [ProviderFault.NONE, ProviderFault.TIMEOUT])
def test_pre_settlement_return_releases_commitments_without_inventing_postings(fault):
    hh = demo_household()
    engine = ExecutionEngine(hh)
    payment = make_leg(hh)
    register(engine, payment)
    engine.provider.inject(payment.id, fault)
    engine.execute(payment)
    assert engine.reservations["acc_checking"] == money(500)
    assert payment.mandate.used_this_period == money(500)
    engine.apply_return(payment)
    assert engine.reservations["acc_checking"].is_zero
    assert payment.mandate.used_this_period.is_zero
    assert hh.accounts["acc_checking"].current == money(3200)
    assert not hh.transactions


def test_card_postings_update_current_balance_without_rewriting_statement():
    hh = demo_household()
    engine = ExecutionEngine(hh)
    payment = make_leg(hh, amount="600", destination="acc_card_a",
                       kind=LegKind.CARD_STATEMENT,
                       purpose=PolicyPurpose.CARD_STATEMENT,
                       policy_id="pol_card", liability_id="lia_card_a")
    register(engine, payment)
    engine.execute(payment)
    engine.settle(payment)
    card = hh.cards["card_a"]
    assert card.current_balance == money("142.18")
    assert card.statement_balance == money("742.18")
    assert all(tx.kind == TxKind.CARD_REPAYMENT for tx in hh.transactions)
    assert not any(tx.counts_as_income or tx.counts_as_spending for tx in hh.transactions)
    engine.apply_return(payment)
    assert card.current_balance == money("742.18")
    assert card.statement_balance == money("742.18")


def test_external_bill_records_only_actual_household_side():
    hh = demo_household()
    engine = ExecutionEngine(hh)
    payment = make_leg(hh, destination="", kind=LegKind.BILL_PAYMENT,
                       purpose=PolicyPurpose.BILL)
    register(engine, payment)
    engine.execute(payment)
    engine.settle(payment)
    assert len(hh.transactions) == 1
    transaction = hh.transactions[0]
    assert transaction.account_id == "acc_checking"
    assert transaction.amount == money(-500)
    assert transaction.kind == TxKind.PURCHASE
    assert transaction.counts_as_spending
    assert not transaction.counts_as_income
