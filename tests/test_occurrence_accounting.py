"""Dated obligations, period funding, and conservative payment applications."""
from datetime import date
from decimal import Decimal

import pytest

from finpilot.ai.tools import ToolRegistry
from finpilot.engine.allocator import AllocationEngine
from finpilot.engine.ledger import LedgerEngine
from finpilot.engine.recurring import RecurringActivityEngine
from finpilot.execution.engine import ExecutionEngine, LegKind, LegState, PaymentLeg, TransferGroup
from finpilot.models import AuthorizationMode, BillOwner, Confidence, Mandate, PolicyPurpose, now
from finpilot.money import Money
from finpilot.seed.demo import demo_household


def money(value):
    return Money(Decimal(str(value)), "USD")


def environment():
    household = demo_household()
    household.accounts["acc_checking"].available = money(20000)
    household.accounts["acc_checking"].current = money(20000)
    engine = ExecutionEngine(household)
    return household, engine, ToolRegistry(household, execution=engine)


def allocation_leg(household, engine, policy_id="pol_auto"):
    _, runs = AllocationEngine(household).allocate_month(household.as_of.year, household.as_of.month)
    for run in runs:
        if any(item.policy_id == policy_id and item.amount.is_positive for item in run.allocations):
            group = engine.build_group_from_allocation(run)
            return next(payment for payment in group.legs if payment.policy_id == policy_id)
    raise AssertionError("Requested policy was not funded")


def pay(engine, payment, *, credited=True):
    engine.authorize(payment, payment.mandate)
    engine.execute(payment)
    assert payment.state == LegState.PROCESSING, payment.failure_reason
    engine.settle(payment, credited=credited)


@pytest.mark.parametrize("first_route", ["allocation", "one_time"])
def test_all_routes_share_one_dated_obligation_and_cannot_pay_it_twice(first_route):
    hh, engine, _ = environment()
    allocated = allocation_leg(hh, engine)
    one_time = engine.build_group_from_bill("bill_auto").legs[0]
    assert allocated.occurrence_id == one_time.occurrence_id
    first, second = (allocated, one_time) if first_route == "allocation" else (one_time, allocated)
    pay(engine, first)
    checks = engine.preflight(second)
    assert not checks.ok
    assert not checks.checks["occurrence_remaining"]
    engine.authorize(second, second.mandate)
    engine.execute(second)
    assert second.state == LegState.FAILED
    assert len(engine.provider.submitted) == 1
    assert hh.accounts["acc_checking"].current == money(19750)


def test_distinct_partial_funding_intents_can_complete_but_not_exceed_one_bill():
    hh, engine, _ = environment()
    bill = hh.bills["bill_auto"]
    occurrence = hh.bill_occurrence(bill, bill.due_date, persist=True)
    partial = PaymentLeg(
        group_id="partial", source_account_id=bill.funding_account_id,
        destination_account_id=bill.payee_account_id, amount=money(100),
        kind=LegKind.BILL_PAYMENT, purpose=PolicyPurpose.REQUIRED_DEBT,
        scheduled_for=bill.due_date, occurrence_id=occurrence.id,
        funding_intent_id="income:first", mandate=hh.policies["pol_auto"].mandate)
    engine.groups["partial"] = TransferGroup(id="partial", legs=[partial])
    pay(engine, partial)
    rest = engine.build_group_from_bill(bill.id).legs[0]
    assert rest.amount == money(150)
    pay(engine, rest)
    assert occurrence.paid == money(250)
    assert occurrence.remaining.is_zero
    assert not engine.preflight(engine.build_group_from_bill(bill.id).legs[0]).ok


def test_in_flight_payment_reserves_remaining_occurrence_capacity():
    hh, engine, _ = environment()
    first = engine.build_group_from_bill("bill_auto").legs[0]
    engine.authorize(first, first.mandate)
    engine.execute(first)
    second = allocation_leg(hh, engine)
    assert not engine.preflight(second).checks["occurrence_remaining"]
    assert hh.bill_occurrences[first.occurrence_id].funded.is_zero


def test_paid_bill_leaves_upcoming_and_forecast_but_next_month_remains():
    hh, engine, registry = environment()
    payment = engine.build_group_from_bill("bill_auto").legs[0]
    pay(engine, payment)
    upcoming = registry.get_upcoming_obligations(days=40)["obligations"]
    auto_rows = [row for row in upcoming if row["id"] == "bill_auto"]
    assert [row["due_date"] for row in auto_rows] == ["2026-10-10"]
    entries = LedgerEngine(hh).entries_for("acc_checking", hh.as_of, date(2026, 10, 15))
    auto_entries = [entry for entry in entries if entry.label == hh.bills["bill_auto"].name]
    assert [entry.date for entry in auto_entries] == [date(2026, 10, 10)]
    assert auto_entries[0].amount == money(-250)
    hh.as_of = date(2026, 10, 10)
    next_month = engine.build_group_from_bill("bill_auto", occurrence_date=hh.as_of).legs[0]
    assert next_month.occurrence_id != payment.occurrence_id
    pay(engine, next_month)
    assert len(engine.provider.submitted) == 2


def test_funded_payment_awaiting_credit_does_not_debit_the_forecast_again():
    hh, engine, registry = environment()
    payment = engine.build_group_from_bill("bill_auto").legs[0]
    pay(engine, payment, credited=False)
    occurrence = hh.bill_occurrences[payment.occurrence_id]
    assert occurrence.funded == money(250)
    assert occurrence.paid.is_zero
    row = next(row for row in registry.get_upcoming_obligations()["obligations"]
               if row["occurrence_id"] == occurrence.id)
    assert Decimal(row["awaiting_application"]["amount"]) == Decimal("250")
    entries = LedgerEngine(hh).entries_for("acc_checking", hh.as_of, hh.as_of)
    assert not any(entry.label == hh.bills["bill_auto"].name for entry in entries)
    engine.settle(payment)
    assert occurrence.paid == money(250)


def test_return_reopens_original_overdue_obligation_without_canceling_next_month():
    hh, engine, registry = environment()
    payment = engine.build_group_from_bill("bill_auto").legs[0]
    pay(engine, payment)
    hh.as_of = date(2026, 9, 11)
    engine.apply_return(payment)
    auto_rows = [row for row in registry.get_upcoming_obligations(days=40)["obligations"]
                 if row["id"] == "bill_auto"]
    assert [row["due_date"] for row in auto_rows] == ["2026-09-10", "2026-10-10"]
    overdue = next(entry for entry in LedgerEngine(hh).entries_for(
        "acc_checking", hh.as_of, hh.as_of) if "overdue" in entry.label)
    assert overdue.date == hh.as_of
    assert overdue.amount == money(-250)


def test_existing_creditor_owner_blocks_new_app_submission():
    hh, engine, _ = environment()
    bill = hh.bills["bill_auto"]
    payment = engine.build_group_from_bill(bill.id).legs[0]
    bill.execution_owner = BillOwner.CREDITOR_AUTOPAY
    bill.autopay_confirmed = True
    assert not engine.preflight(payment).checks["app_execution_owner"]
    engine.authorize(payment, payment.mandate)
    engine.execute(payment)
    assert not engine.provider.calls


@pytest.mark.parametrize("change", ["source", "payee", "mandate"])
def test_material_bill_or_authority_changes_invalidate_existing_drafts(change):
    hh, engine, _ = environment()
    payment = engine.build_group_from_bill("bill_auto").legs[0]
    if change == "source":
        hh.bills["bill_auto"].funding_account_id = "acc_savings"
    elif change == "payee":
        hh.bills["bill_auto"].payee_account_id = "acc_student"
    else:
        hh.policies["pol_auto"].mandate = Mandate()
    assert not engine.preflight(payment).ok
    engine.authorize(payment, payment.mandate)
    engine.execute(payment)
    assert not engine.provider.calls


def test_mortgage_installment_does_not_guess_principal_from_gross_payment():
    hh, engine, _ = environment()
    mortgage = hh.liabilities["lia_mortgage"]
    before = mortgage.balance
    payment = engine.build_group_from_bill("bill_mortgage").legs[0]
    pay(engine, payment)
    assert mortgage.balance == before
    assert hh.accounts["acc_mortgage"].current == -before
    assert hh.accounts["acc_mortgage"].available.is_zero
    assert payment.application.principal_applied.is_zero
    assert payment.application.unapplied_to_principal == money(1800)
    assert payment.application.expected_interest == money(675)
    assert payment.application.expected_escrow == money("661.23")
    assert payment.application.confidence == Confidence.INSUFFICIENT
    assert "not posted amounts" in payment.application.explanation
    engine.apply_return(payment)
    assert mortgage.balance == before
    assert hh.accounts["acc_checking"].current == money(20000)


def test_principal_only_payment_applies_and_reverses_exact_principal():
    hh, engine, _ = environment()
    payment = PaymentLeg(
        group_id="principal", source_account_id="acc_checking",
        destination_account_id="acc_mortgage", amount=money(500),
        kind=LegKind.EXTRA_PRINCIPAL, purpose=PolicyPurpose.EXTRA_PRINCIPAL,
        principal_only=True, liability_id="lia_mortgage", scheduled_for=hh.as_of,
        mandate=hh.policies["pol_mortgage"].mandate)
    engine.groups["principal"] = TransferGroup(id="principal", legs=[payment])
    pay(engine, payment)
    assert hh.liabilities["lia_mortgage"].balance == money(179500)
    assert hh.accounts["acc_mortgage"].current == money(-179500)
    assert payment.application.principal_applied == money(500)
    engine.apply_return(payment)
    assert hh.liabilities["lia_mortgage"].balance == money(180000)


def test_funding_does_not_leak_into_next_month_and_old_return_preserves_new_month():
    hh, engine, _ = environment()
    policy = hh.policies["pol_emergency"]
    policy.record_funding(money(400), date(2026, 9, 15), hh.as_of)
    september = AllocationEngine(hh).build_monthly_plan(2026, 9)
    october = AllocationEngine(hh).build_monthly_plan(2026, 10)
    assert next(t for t in september.targets if t.policy_id == policy.id).funded == money(400)
    assert next(t for t in october.targets if t.policy_id == policy.id).funded.is_zero
    policy.record_funding(money(300), date(2026, 10, 15), hh.as_of)
    policy.reverse_funding(money(400), date(2026, 9, 15), hh.as_of)
    assert policy.funded_this_period == money(300)
    assert policy.funded_period == "2026-10"
    assert policy.funded_by_period["2026-09"].is_zero


def test_skip_is_fixed_to_its_dated_occurrence_even_after_clock_advances():
    hh, engine, registry = environment()
    policy = hh.policies["pol_emergency"]
    selected = registry.skip_next_occurrence(policy.id)
    assert selected["skipped_date"] == "2026-09-15"
    hh.as_of = date(2026, 9, 16)
    future = [run for run in RecurringActivityEngine(hh).upcoming_runs(40)
              if run.policy_id == policy.id]
    assert future and all(run.will_run for run in future)
    plan, runs = AllocationEngine(hh).allocate_month(2026, 10)
    assert any(a.policy_id == policy.id and a.amount.is_positive
               for run in runs for a in run.allocations)


def test_skipped_income_occurrence_does_not_erase_other_funding_events():
    hh, engine, registry = environment()
    hh.as_of = date(2026, 9, 1)
    policy = hh.policies["pol_emergency"]
    hh.policies = {policy.id: policy}
    registry.skip_next_occurrence(policy.id)
    _, runs = AllocationEngine(hh).allocate_month(2026, 9)
    first = [a for a in runs[0].allocations if a.policy_id == policy.id]
    second = [a for a in runs[1].allocations if a.policy_id == policy.id]
    assert first and first[0].amount.is_zero and "skipped" in first[0].reason
    assert second and second[0].amount == money(700)


def test_reading_obligations_does_not_create_persisted_occurrences():
    hh, engine, registry = environment()
    registry.get_upcoming_obligations()
    LedgerEngine(hh).forecast("acc_checking")
    AllocationEngine(hh).build_monthly_plan(2026, 9)
    assert not hh.bill_occurrences


def test_scheduled_income_identity_is_stable_across_rebuilds():
    hh, engine, _ = environment()
    first = AllocationEngine(hh).month_income_dates(2026, 10)
    second = AllocationEngine(hh).month_income_dates(2026, 10)
    assert first and all(event for _, _, event in first)
    assert [event.id for _, _, event in first] == [event.id for _, _, event in second]


def test_one_time_tool_requires_a_valid_explicit_occurrence_date():
    hh, engine, registry = environment()
    invalid = registry.pay_bill_once("bill_auto", "2026-10-11")
    assert "error" in invalid
    valid = registry.pay_bill_once("bill_auto", "2026-10-10")
    assert valid["due_date"] == "2026-10-10"
    assert valid["occurrence_id"].endswith(":2026-10-10")
    assert not valid["preflight"]["checks"]["scheduled_date_reached"]


def test_returned_external_bill_is_not_still_counted_as_spending():
    from finpilot.models import TxState
    hh, engine, _ = environment()
    payment = PaymentLeg(group_id="external", source_account_id="acc_checking",
                         destination_label="External bill", amount=money(50),
                         kind=LegKind.BILL_PAYMENT, purpose=PolicyPurpose.BILL,
                         scheduled_for=hh.as_of, mandate=hh.policies["pol_auto"].mandate)
    engine.groups["external"] = TransferGroup(id="external", legs=[payment])
    pay(engine, payment)
    assert sum(tx.counts_as_spending for tx in hh.transactions) == 1
    engine.apply_return(payment)
    assert any(tx.state == TxState.REVERSED for tx in hh.transactions)
    assert not any(tx.counts_as_spending for tx in hh.transactions)


def test_confirmed_return_replacement_uses_a_new_provider_attempt_identity():
    hh, engine, _ = environment()
    original = engine.build_group_from_bill("bill_auto").legs[0]
    pay(engine, original)
    engine.apply_return(original)
    replacement = engine.build_group_from_bill("bill_auto").legs[0]
    rebuild = engine.build_group_from_bill("bill_auto").legs[0]
    assert replacement.idempotency_key != original.idempotency_key
    assert replacement.idempotency_key == rebuild.idempotency_key
    pay(engine, replacement)
    assert replacement.provider_ref != original.provider_ref
    assert len(engine.provider.submitted) == 2
    assert hh.bill_occurrences[replacement.occurrence_id].paid == money(250)
    assert hh.accounts["acc_checking"].current == money(19750)


def test_pending_provider_amount_already_in_available_is_not_subtracted_twice():
    from finpilot.models import Transaction, TxState
    hh, engine, _ = environment()
    hh.bills.clear()
    hh.income_events.clear()
    hh.transactions = [Transaction(account_id="acc_checking", date=hh.as_of,
                                   amount=money(-80), state=TxState.PENDING,
                                   balance_already_reflected=True)]
    before = hh.accounts["acc_checking"].available
    assert LedgerEngine(hh).forecast("acc_checking", days=1).days[-1].closing == before
    hh.transactions[0].balance_already_reflected = False
    assert LedgerEngine(hh).forecast("acc_checking", days=1).days[-1].closing == before - money(80)


def test_returned_savings_rule_also_uses_a_new_attempt_on_rebuild():
    hh, engine, _ = environment()
    policy = hh.policies["pol_emergency"]
    hh.policies = {policy.id: policy}
    original = allocation_leg(hh, engine, policy.id)
    pay(engine, original)
    engine.apply_return(original)
    replacement = allocation_leg(hh, engine, policy.id)
    assert original.occurrence_id == replacement.occurrence_id
    assert original.idempotency_key != replacement.idempotency_key
    pay(engine, replacement)
    assert original.provider_ref != replacement.provider_ref


def test_persisted_occurrence_application_and_authority_survive_roundtrip():
    from finpilot.persistence.codec import decode, encode

    hh, engine, _ = environment()
    hh.live_dates = True
    payment = allocation_leg(hh, engine)
    pay(engine, payment)
    policy = hh.policies[payment.policy_id]
    hh.skip_policy_occurrence(policy)
    restored = decode(encode({"household": hh, "groups": engine.groups}))
    restored_hh = restored["household"]
    restored_leg = next(leg for group in restored["groups"].values()
                        for leg in group.legs if leg.id == payment.id)
    assert restored_hh.live_dates is True
    assert restored_leg.mandate is restored_hh.policies[policy.id].mandate
    assert restored_leg.application == payment.application
    assert restored_hh.bill_occurrences[payment.occurrence_id].paid == payment.amount
    assert restored_hh.policies[policy.id].funded_by_period == policy.funded_by_period
    assert restored_hh.policies[policy.id].skipped_dates == policy.skipped_dates
    assert restored_hh.transactions == hh.transactions


def test_legacy_household_snapshot_defaults_to_fixed_dates():
    from finpilot.persistence.codec import decode, encode

    hh, _, _ = environment()
    snapshot = encode(hh)
    del snapshot["data"]["fields"]["live_dates"]
    assert decode(snapshot).live_dates is False


def test_deleted_policy_cannot_authorize_its_existing_draft():
    hh, engine, _ = environment()
    payment = allocation_leg(hh, engine)
    del hh.policies[payment.policy_id]
    assert not engine.preflight(payment).checks["policy_exists"]


@pytest.mark.parametrize("edited_field, replacement", [
    ("funding_account_id", "acc_hysa"),
    ("payee_account_id", "acc_card"),
])
def test_rebuilt_policy_cannot_bypass_changed_explicit_bill_binding(edited_field, replacement):
    hh, engine, _ = environment()
    policy = hh.policies["pol_auto"]
    policy.bill_id = "bill_auto"
    setattr(hh.bills["bill_auto"], edited_field, replacement)
    payment = allocation_leg(hh, engine)
    assert not engine.preflight(payment).checks["policy_bill_binding"]
