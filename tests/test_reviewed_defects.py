"""Regression tests for defects found in an external review of commit
3dd0983. Each test reproduces the reviewer's exact scenario before the fix,
so a regression here means one of these specific failures came back.
"""
from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from finpilot.ai.graph import _claims_completed_action
from finpilot.ai.guardrails import check_grounding
from finpilot.ai.tools import ToolRegistry
from finpilot.engine.allocator import AllocationEngine
from finpilot.engine.cards import Purchase, evaluate_card, rank_cards
from finpilot.execution.engine import (ExecutionEngine, LegKind, LegState,
                                       PaymentLeg, SimulatedProvider,
                                       TransferGroup)
from finpilot.models import AuthorizationMode, Card, IncomeEvent, Mandate, \
    PolicyPurpose, RewardRule, now
from finpilot.money import Money
from finpilot.seed.demo import demo_household


def M(v, c="USD"):
    return Money(D(str(v)), c)


# ---------------------------------------------------------------------------
# Same-date income events must not collapse onto one event's identity
# ---------------------------------------------------------------------------

def test_a_same_day_bonus_does_not_double_count_the_salary():
    """Reviewer: '$3,000 salary and $1,000 bonus on the same date... the
    allocator processed the salary twice and produced $6,000 of available
    income instead of $4,000.'"""
    hh = demo_household()
    hh.income_events.append(IncomeEvent(
        id="ie_bonus", source_id=hh.income_events[0].source_id,
        expected_date=date(2026, 9, 1), expected_amount=M(1000),
        received_date=date(2026, 9, 1), received_amount=M(1000)))

    eng = AllocationEngine(hh)
    plan, runs = eng.allocate_month(2026, 9)

    same_day = [r for r in runs if r.pay_date == date(2026, 9, 1)]
    assert len(same_day) == 2, "each same-day event must produce its own run"
    ids = {r.income_event_id for r in same_day}
    assert ids == {"ie_sep01", "ie_bonus"}
    amounts = sorted(r.available.amount for r in same_day)
    assert amounts == [D("1000.00"), D("3000.00")]
    # total expected income for the month: 3000 (sep1) + 1000 (bonus) + 3000 (sep15)
    assert plan.expected_income == M(7000)


def test_month_income_dates_carries_each_events_own_identity():
    hh = demo_household()
    hh.income_events.append(IncomeEvent(
        id="ie_bonus", source_id=hh.income_events[0].source_id,
        expected_date=date(2026, 9, 1), expected_amount=M(1000),
        received_date=date(2026, 9, 1), received_amount=M(1000)))
    eng = AllocationEngine(hh)
    rows = eng.month_income_dates(2026, 9)
    by_id = {ev.id: amt for _, amt, ev in rows if ev is not None}
    assert by_id["ie_sep01"] == M(3000)
    assert by_id["ie_bonus"] == M(1000)
    assert by_id["ie_sep15"] == M(3000)


# ---------------------------------------------------------------------------
# A flat-rate card must not be excluded for lacking a category-specific rule
# ---------------------------------------------------------------------------

def _flat_rate_card(rate="0.02"):
    return Card(id="c_flat", nickname="Flat Rewards Card", mask="1111",
               rules=[RewardRule(category="base", rate=D(rate))])


def test_a_card_earning_a_flat_rate_is_eligible_for_a_category_with_no_specific_rule():
    """Reviewer: 'A hypothetical owned card earning 2% on all purchases
    calculated $2 on a $100 rideshare purchase, then was marked ineligible
    because it lacked a rideshare-specific rule.'"""
    card = _flat_rate_card("0.02")
    purchase = Purchase(amount=M(100), category="rideshare", merchant="Uber")
    opt = evaluate_card(card, purchase)
    assert opt.eligible is True
    assert opt.exclusions == []
    assert opt.reward_value == M(2)


def test_flat_rate_card_is_picked_by_rank_cards_with_no_other_option():
    card = _flat_rate_card("0.02")
    purchase = Purchase(amount=M(100), category="rideshare", merchant="Uber")
    ranking = rank_cards([card], purchase)
    assert ranking.winner is not None
    assert ranking.winner.card_id == "c_flat"


def test_a_genuinely_disqualifying_exclusion_still_excludes_the_card():
    """The fix must not turn every exclusion into a mere note -- a card the
    user has actually excluded is still genuinely ineligible."""
    card = _flat_rate_card("0.02")
    card.excluded_by_user = True
    purchase = Purchase(amount=M(100), category="rideshare", merchant="Uber")
    opt = evaluate_card(card, purchase)
    assert opt.eligible is False
    assert opt.exclusions == ["Excluded by you"]


def test_an_unactivated_required_category_rule_falls_back_to_base_not_to_ineligible():
    """A rotating-category rule that requires activation and is not yet
    activated is skipped by rule matching, same as having no category rule
    at all -- it must fall back to the base rate rather than block the
    purchase, since a different, unconditional rule still applies."""
    card = Card(id="c_needs_activation", nickname="Rotating Card", mask="2222",
               rules=[RewardRule(category="base", rate=D("0.01")),
                      RewardRule(category="rideshare", rate=D("0.05"),
                                 requires_activation=True, activated=False)])
    purchase = Purchase(amount=M(100), category="rideshare", merchant="Uber")
    opt = evaluate_card(card, purchase)
    assert opt.eligible is True
    assert opt.reward_value == M(1)


# ---------------------------------------------------------------------------
# A restricted account scope must not leak household-wide totals
# ---------------------------------------------------------------------------
#
# Reviewer: "I could authorize policies and execute simulated payments without
# signing in... Account permissions also need consistent enforcement:
# restricting visible accounts still exposes household-wide financial
# totals." The demo household's checking account (acc_checking) funds every
# bill and holds the operating-floor reserve; its savings account
# (acc_savings) holds the emergency and annual reserves and no debt; its
# mortgage/auto/student liabilities are tied to their own accounts and to
# policies whose destination is those accounts.

def test_scoped_money_overview_excludes_other_accounts_balances():
    hh = demo_household()
    scoped = ToolRegistry(hh, allowed_account_ids={"acc_checking"})
    full = ToolRegistry(hh)

    scoped_view = scoped.get_money_overview()
    full_view = full.get_money_overview()

    checking = hh.accounts["acc_checking"]
    # only the in-scope account's row is visible
    assert [a["id"] for a in scoped_view["accounts"]] == ["acc_checking"]
    # and every household-wide total is computed from that account alone,
    # not from the whole household -- this is the leak the reviewer found
    assert scoped_view["total_cash"] == checking.available.to_json()
    assert scoped_view["total_debt"] == Money.zero(hh.base_currency).to_json()
    assert scoped_view["net_worth"] == checking.current.to_json()
    assert scoped_view["total_cash"] != full_view["total_cash"]
    assert scoped_view["net_worth"] != full_view["net_worth"]
    assert D(full_view["total_debt"]["amount"]) > 0
    # the checking-only operating-floor reserve is visible; the
    # savings-backed emergency/annual reserves are not
    assert scoped_view["protected_reserves"] == hh.reserves["res_floor"].funded.to_json()


def test_scoped_stress_income_loss_excludes_other_accounts_cash_and_bills():
    hh = demo_household()
    # every bill in the fixture is funded from checking, so scoping to
    # savings alone must hide all of them and all of the cash
    scoped = ToolRegistry(hh, allowed_account_ids={"acc_savings"})
    out = scoped.stress_income_loss(months=1)
    assert out["available_cash"] == hh.accounts["acc_savings"].available.to_json()
    assert out["monthly_required_obligations"] == Money.zero(hh.base_currency).to_json()


def test_scoped_pay_bill_once_rejects_a_bill_funded_from_an_out_of_scope_account():
    hh = demo_household()
    scoped = ToolRegistry(hh, allowed_account_ids={"acc_savings"})
    out = scoped.pay_bill_once("bill_mortgage")
    assert "error" in out


def test_scoped_pause_and_skip_reject_a_policy_targeting_an_out_of_scope_account():
    hh = demo_household()
    scoped = ToolRegistry(hh, allowed_account_ids={"acc_savings"})
    pause_out = scoped.pause_recurring_policy("pol_mortgage", paused=True)
    skip_out = scoped.skip_next_occurrence("pol_mortgage")
    assert "error" in pause_out
    assert "error" in skip_out
    # confirm the policy was genuinely left untouched
    assert hh.policies["pol_mortgage"].paused is False
    assert hh.policies["pol_mortgage"].skip_next is False


def test_scoped_automation_status_excludes_policies_for_other_accounts():
    hh = demo_household()
    scoped = ToolRegistry(hh, allowed_account_ids={"acc_checking"})
    out = scoped.get_automation_status()
    ids = {p["id"] for p in out["policies"]}
    assert "pol_mortgage" not in ids  # destination is acc_mortgage, out of scope
    assert "pol_floor" in ids         # destination is acc_checking, in scope


def test_scoped_debt_tools_exclude_liabilities_on_other_accounts():
    hh = demo_household()
    scoped = ToolRegistry(hh, allowed_account_ids={"acc_checking"})
    out = scoped.compare_debt_strategies()
    assert out.get("error") == "no debts on file"


def test_full_access_registry_is_unaffected_by_the_scope_fix():
    """The fix must not regress the ordinary, unscoped case."""
    hh = demo_household()
    full = ToolRegistry(hh)
    out = full.get_money_overview()
    assert out["total_cash"] == hh.total_cash().to_json()
    assert out["net_worth"] == hh.net_worth().to_json()
    assert len(out["accounts"]) == len(hh.accounts)


# ---------------------------------------------------------------------------
# Rebuilding a plan must not pay the same obligation twice
# ---------------------------------------------------------------------------
#
# Reviewer: "I reproduced two successful provider submissions for the same
# destination, amount, purpose, and date after the first payment settled.
# The duplicate key depends on the transfer group, while the secondary check
# only blocks certain pending states."

def _standing_mandate(cap="2500"):
    return Mandate(mode=AuthorizationMode.STANDING, authorized_at=now(),
                   authorized_by="user_primary", per_run_cap=Money(D(cap), "USD"))


def _obligation_leg(group_id: str) -> PaymentLeg:
    """Two calls with different `group_id` model the same real-world
    obligation being rebuilt into a brand new TransferGroup on the next
    planning pass -- exactly what a rebuild does."""
    return PaymentLeg(group_id=group_id, kind=LegKind.BILL_PAYMENT,
                      source_account_id="acc_checking",
                      destination_account_id="acc_mortgage",
                      destination_label="Mortgage payment",
                      amount=M(1800), purpose=PolicyPurpose.REQUIRED_DEBT,
                      scheduled_for=date(2026, 9, 5),
                      mandate=_standing_mandate())


def test_rebuilt_legs_for_the_same_obligation_share_an_idempotency_key():
    a = _obligation_leg("g1")
    b = _obligation_leg("g2")
    assert a.idempotency_key == b.idempotency_key


def test_a_rebuild_cannot_pay_an_already_settled_obligation_twice():
    hh = demo_household()
    eng = ExecutionEngine(hh, SimulatedProvider())

    first = _obligation_leg("g1")
    eng.groups["g1"] = TransferGroup(id="g1", legs=[first])
    first.transition(LegState.AUTHORIZED, "standing mandate")
    eng.execute(first)
    eng.settle(first)
    assert first.state == LegState.RECONCILED

    # the plan is rebuilt: a brand new group and a brand new leg object for
    # the very same obligation (same destination, amount, purpose, date)
    rebuilt = _obligation_leg("g2")
    eng.groups["g2"] = TransferGroup(id="g2", legs=[rebuilt])
    rebuilt.transition(LegState.AUTHORIZED, "standing mandate")

    pf = eng.preflight(rebuilt)
    assert not pf.ok
    assert not pf.checks["no_duplicate"]
    assert any("already reconciled" in b for b in pf.blockers)

    result = eng.execute(rebuilt)
    assert result.state == LegState.FAILED
    assert len(eng.provider.submitted) == 1  # never reached the provider a second time


def test_a_settled_duplicate_is_still_caught_even_with_a_different_key():
    """Belt-and-suspenders: even if the key-based check somehow missed it
    (say the amount was recalculated slightly), the same-obligation
    signature check must still catch an already-settled duplicate rather
    than only catching ones still in flight."""
    hh = demo_household()
    eng = ExecutionEngine(hh, SimulatedProvider())

    first = _obligation_leg("g1")
    eng.groups["g1"] = TransferGroup(id="g1", legs=[first])
    first.transition(LegState.AUTHORIZED, "standing mandate")
    eng.execute(first)
    eng.settle(first)
    assert first.state == LegState.RECONCILED

    rebuilt = PaymentLeg(group_id="g2", kind=LegKind.BILL_PAYMENT,
                         source_account_id="acc_checking",
                         destination_account_id="acc_mortgage",
                         destination_label="Mortgage payment",
                         amount=M("1800.01"),  # deliberately not identical
                         purpose=PolicyPurpose.REQUIRED_DEBT,
                         scheduled_for=date(2026, 9, 5),
                         mandate=_standing_mandate())
    eng.groups["g2"] = TransferGroup(id="g2", legs=[rebuilt])
    assert rebuilt.idempotency_key != first.idempotency_key

    pf = eng.preflight(rebuilt)
    assert not pf.ok
    assert not pf.checks["no_duplicate"]


# ---------------------------------------------------------------------------
# Payment results must correctly update the financial picture
# ---------------------------------------------------------------------------
#
# Reviewer: "Returning a $500 payment before settlement increased checking
# from $3,200 to $3,700, although it had never been debited. A reconciled
# card payment left the separate debt balance unchanged. Reserve funding
# likewise did not update reserve progress or the policy's funded amount."

def test_a_return_before_settlement_does_not_move_money_that_never_moved():
    hh = demo_household()
    eng = ExecutionEngine(hh, SimulatedProvider())
    checking_before = hh.accounts["acc_checking"].current

    l = PaymentLeg(group_id="g1", kind=LegKind.RESERVE_FUNDING,
                  source_account_id="acc_checking",
                  destination_account_id="acc_savings",
                  destination_label="Emergency reserve", amount=M(500),
                  purpose=PolicyPurpose.EMERGENCY_RESERVE,
                  scheduled_for=hh.as_of,
                  mandate=_standing_mandate())
    eng.groups["g1"] = TransferGroup(id="g1", legs=[l])
    l.transition(LegState.AUTHORIZED, "standing mandate")
    eng.execute(l)
    assert l.state == LegState.PROCESSING
    assert l.settlement_applied is False
    # not yet settled -- checking must not have moved at all
    assert hh.accounts["acc_checking"].current == checking_before

    eng.apply_return(l, "returned before it ever settled")
    assert l.state == LegState.RETURNED
    # the bug: returning it credited checking anyway, for money that had
    # never left. It must still be exactly what it started as.
    assert hh.accounts["acc_checking"].current == checking_before


def test_a_return_after_settlement_correctly_reverses_the_real_movement():
    hh = demo_household()
    eng = ExecutionEngine(hh, SimulatedProvider())
    checking_before = hh.accounts["acc_checking"].current
    savings_before = hh.accounts["acc_savings"].current

    l = PaymentLeg(group_id="g1", kind=LegKind.RESERVE_FUNDING,
                  source_account_id="acc_checking",
                  destination_account_id="acc_savings",
                  destination_label="Emergency reserve", amount=M(500),
                  purpose=PolicyPurpose.EMERGENCY_RESERVE,
                  scheduled_for=hh.as_of,
                  mandate=_standing_mandate())
    eng.groups["g1"] = TransferGroup(id="g1", legs=[l])
    l.transition(LegState.AUTHORIZED, "standing mandate")
    eng.execute(l)
    eng.settle(l, credited=False)
    assert l.state == LegState.FUNDS_AVAILABLE
    assert hh.accounts["acc_checking"].current == checking_before - M(500)
    assert hh.accounts["acc_savings"].current == savings_before + M(500)

    eng.apply_return(l, "returned after settlement")
    assert hh.accounts["acc_checking"].current == checking_before
    assert hh.accounts["acc_savings"].current == savings_before


def test_a_reconciled_card_payment_reduces_the_linked_liability_balance():
    hh = demo_household()
    eng = ExecutionEngine(hh, SimulatedProvider())
    balance_before = hh.liabilities["lia_card_a"].balance

    l = PaymentLeg(group_id="g1", kind=LegKind.CARD_STATEMENT,
                  source_account_id="acc_checking",
                  destination_account_id="acc_card_a",
                  destination_label="Card statement", amount=M(600),
                  purpose=PolicyPurpose.CARD_STATEMENT,
                  scheduled_for=hh.as_of,
                  mandate=_standing_mandate(),
                  policy_id="pol_card", liability_id="lia_card_a")
    hh.policies[l.policy_id].mandate = l.mandate
    eng.groups["g1"] = TransferGroup(id="g1", legs=[l])
    l.transition(LegState.AUTHORIZED, "standing mandate")
    eng.execute(l)
    eng.settle(l)
    assert l.state == LegState.RECONCILED

    assert hh.liabilities["lia_card_a"].balance == balance_before - M(600)


def test_reserve_funding_updates_reserve_progress_and_policy_funded_amount():
    hh = demo_household()
    eng = ExecutionEngine(hh, SimulatedProvider())
    funded_before = hh.reserves["res_emergency"].funded
    policy_funded_before = hh.policies["pol_emergency"].funded_this_period

    l = PaymentLeg(group_id="g1", kind=LegKind.RESERVE_FUNDING,
                  source_account_id="acc_checking",
                  destination_account_id="acc_savings",
                  destination_label="Emergency reserve", amount=M(700),
                  purpose=PolicyPurpose.EMERGENCY_RESERVE,
                  scheduled_for=hh.as_of,
                  mandate=_standing_mandate(),
                  policy_id="pol_emergency", reserve_id="res_emergency")
    hh.policies[l.policy_id].mandate = l.mandate
    eng.groups["g1"] = TransferGroup(id="g1", legs=[l])
    l.transition(LegState.AUTHORIZED, "standing mandate")
    eng.execute(l)
    eng.settle(l)

    assert hh.reserves["res_emergency"].funded == funded_before + M(700)
    assert (hh.policies["pol_emergency"].funded_this_period
            == policy_funded_before + M(700))


# ---------------------------------------------------------------------------
# Scheduling and spending limits must be enforced before submission
# ---------------------------------------------------------------------------
#
# Reviewer: "A payment dated January 1, 2030 executed immediately. Another
# transfer left checking at $700 despite a protected $1,000 reserve. A
# one-time mandate with a $100 period cap allowed two $100 payments."

def test_a_payment_dated_years_in_the_future_does_not_execute_immediately():
    hh = demo_household()
    eng = ExecutionEngine(hh, SimulatedProvider())
    assert hh.as_of < date(2030, 1, 1)

    l = PaymentLeg(group_id="g1", kind=LegKind.RESERVE_FUNDING,
                  source_account_id="acc_checking",
                  destination_account_id="acc_savings",
                  destination_label="Emergency reserve", amount=M(500),
                  purpose=PolicyPurpose.EMERGENCY_RESERVE,
                  scheduled_for=date(2030, 1, 1),
                  mandate=_standing_mandate())
    eng.groups["g1"] = TransferGroup(id="g1", legs=[l])
    l.transition(LegState.AUTHORIZED, "standing mandate")

    pf = eng.preflight(l)
    assert not pf.ok
    assert not pf.checks["scheduled_date_reached"]
    assert any("has not arrived yet" in b for b in pf.blockers)

    eng.execute(l)
    assert l.state == LegState.FAILED
    assert len(eng.provider.submitted) == 0


def test_a_transfer_cannot_drain_checking_through_its_protected_reserve():
    hh = demo_household()
    eng = ExecutionEngine(hh, SimulatedProvider())
    # acc_checking carries res_floor, a $1000 protected operating-floor
    # reserve. Leave $1,700 available so a naive check (ignoring the floor)
    # would happily approve a $1,000 transfer and leave checking at $700.
    hh.accounts["acc_checking"].available = M(1700)

    l = PaymentLeg(group_id="g1", kind=LegKind.RESERVE_FUNDING,
                  source_account_id="acc_checking",
                  destination_account_id="acc_savings",
                  destination_label="Annual bills reserve", amount=M(1000),
                  purpose=PolicyPurpose.ANNUAL_RESERVE,
                  scheduled_for=hh.as_of,
                  mandate=_standing_mandate())
    eng.groups["g1"] = TransferGroup(id="g1", legs=[l])
    l.transition(LegState.AUTHORIZED, "standing mandate")

    pf = eng.preflight(l)
    assert not pf.ok
    assert not pf.checks["executable_funds"]
    assert any("protected reserves" in b for b in pf.blockers)

    # a smaller transfer that respects the floor is fine
    ok_leg = PaymentLeg(group_id="g2", kind=LegKind.RESERVE_FUNDING,
                        source_account_id="acc_checking",
                        destination_account_id="acc_savings",
                        destination_label="Annual bills reserve", amount=M(700),
                        purpose=PolicyPurpose.ANNUAL_RESERVE,
                        scheduled_for=hh.as_of,
                        mandate=_standing_mandate())
    eng.groups["g2"] = TransferGroup(id="g2", legs=[ok_leg])
    ok_leg.transition(LegState.AUTHORIZED, "standing mandate")
    assert eng.preflight(ok_leg).ok


def test_a_one_time_mandates_period_cap_blocks_a_second_payment():
    hh = demo_household()
    eng = ExecutionEngine(hh, SimulatedProvider())
    mandate = Mandate(mode=AuthorizationMode.ONE_TIME, authorized_at=now(),
                      authorized_by="user_primary", period_cap=M(100))

    first = PaymentLeg(group_id="g1", kind=LegKind.INTERNAL_TRANSFER,
                       source_account_id="acc_checking",
                       destination_account_id="acc_brokerage",
                       destination_label="one-time transfer", amount=M(100),
                       purpose=PolicyPurpose.GOAL, scheduled_for=hh.as_of,
                       mandate=mandate)
    eng.groups["g1"] = TransferGroup(id="g1", legs=[first])
    first.transition(LegState.AUTHORIZED, "one-time mandate")
    eng.execute(first)
    assert first.state == LegState.PROCESSING

    second = PaymentLeg(group_id="g2", kind=LegKind.INTERNAL_TRANSFER,
                        source_account_id="acc_checking",
                        destination_account_id="acc_brokerage",
                        destination_label="one-time transfer", amount=M(100),
                        purpose=PolicyPurpose.GOAL, scheduled_for=hh.as_of,
                        mandate=mandate)
    eng.groups["g2"] = TransferGroup(id="g2", legs=[second])
    second.transition(LegState.AUTHORIZED, "one-time mandate")

    pf = eng.preflight(second)
    assert not pf.ok
    assert not pf.checks["authorization"]
    assert any("already been used" in b for b in pf.blockers)

    eng.execute(second)
    assert second.state == LegState.FAILED
    assert len(eng.provider.submitted) == 1


def test_a_standing_mandates_period_cap_blocks_once_exceeded_within_the_month():
    hh = demo_household()
    eng = ExecutionEngine(hh, SimulatedProvider())
    mandate = Mandate(mode=AuthorizationMode.STANDING, authorized_at=now(),
                      authorized_by="user_primary", period_cap=M(150))

    def transfer(amount, group_id):
        l = PaymentLeg(group_id=group_id, kind=LegKind.INTERNAL_TRANSFER,
                       source_account_id="acc_checking",
                       destination_account_id="acc_brokerage",
                       destination_label="standing transfer", amount=M(amount),
                       purpose=PolicyPurpose.GOAL, scheduled_for=hh.as_of,
                       mandate=mandate)
        eng.groups[group_id] = TransferGroup(id=group_id, legs=[l])
        l.transition(LegState.AUTHORIZED, "standing mandate")
        return l

    a = transfer(100, "g1")
    eng.execute(a)
    assert a.state == LegState.PROCESSING  # $100 of $150 used this period

    b = transfer(100, "g2")  # would bring the period to $200, over the $150 cap
    pf = eng.preflight(b)
    assert not pf.ok
    assert any("period cap" in blocker for blocker in pf.blockers)


# ---------------------------------------------------------------------------
# The AI must not accept a premature payment-success claim
# ---------------------------------------------------------------------------
#
# Reviewer: "The completion guard permits 'Payment completed' when the
# evidence only says processing. Its numeric check also accepted a positive
# $500 balance against negative $500 evidence."

def test_payment_completed_is_not_satisfied_by_merely_processing_evidence():
    payload = [{"leg": {"id": "leg_1", "state": "processing"}}]
    reason = _claims_completed_action("Your payment completed successfully.", payload)
    assert reason is not None
    assert "settled" in reason


def test_payment_completed_is_satisfied_by_actual_settlement_evidence():
    for state in ("funds_available", "credited_by_biller", "reconciled"):
        payload = [{"leg": {"id": "leg_1", "state": state}}]
        assert _claims_completed_action("Your payment completed successfully.",
                                        payload) is None


def test_weaker_in_flight_claims_are_still_satisfied_by_processing_evidence():
    """The fix must not overcorrect: 'I have sent the transfer' is a weaker
    claim than 'payment completed' and is still properly evidenced by
    submitted/processing, exactly as before."""
    payload = [{"leg": {"id": "leg_1", "state": "processing"}}]
    assert _claims_completed_action("I have sent the transfer.", payload) is None
    payload_none = [{"leg": {"id": "leg_1", "state": "authorized"}}]
    reason = _claims_completed_action("I have sent the transfer.", payload_none)
    assert reason is not None


def test_grounding_does_not_accept_a_positive_figure_against_negative_evidence():
    """Reviewer: 'a positive $500 balance against negative $500 evidence.'"""
    payload = {"leg": {"amount": {"amount": "-500.00", "display": "-$500.00"}}}
    r = check_grounding("Your balance increased by $500.00.", [payload])
    assert not r.ok
    assert "$500.00" in r.ungrounded


def test_grounding_still_accepts_a_genuinely_matching_positive_figure():
    payload = {"leg": {"amount": {"amount": "500.00", "display": "$500.00"}}}
    r = check_grounding("Your balance increased by $500.00.", [payload])
    assert r.ok


def test_grounding_does_not_accept_a_negative_figure_against_positive_evidence():
    payload = {"leg": {"amount": {"amount": "500.00", "display": "$500.00"}}}
    r = check_grounding("Your balance decreased by -$500.00.", [payload])
    assert not r.ok
    assert "-$500.00" in r.ungrounded


# ---------------------------------------------------------------------------
# Authorizing a policy must not silently attribute it to an unchecked identity
# ---------------------------------------------------------------------------
#
# Reviewer: "Payment authorization does not authenticate the user. I could
# authorize policies and execute simulated payments without signing in. The
# authorization endpoint assigns the caller the identity user_primary."
#
# Hosted authentication now verifies an opaque session and CSRF token. The
# authorizing identity comes only from that session, never from request data.

@pytest.fixture
def client():
    from tests.api_support import authenticated_client
    with authenticated_client() as client:
        yield client


def test_authorize_requires_a_signed_in_identity(client):
    client.cookies.clear()
    response = client.post("/api/policies/pol_insurance/authorize",
        json={"mode": "standing", "per_run_cap": "2500"})
    assert response.status_code == 401


def test_authorize_rejects_a_missing_csrf_token(client):
    response = client.post("/api/policies/pol_insurance/authorize",
        json={"mode": "standing", "per_run_cap": "2500"},
        headers={"X-CSRF-Token": ""})
    assert response.status_code == 403


def test_authorize_rejects_a_forged_request_identity(client):
    response = client.post("/api/policies/pol_insurance/authorize",
        json={"mode": "standing", "per_run_cap": "2500",
              "authorized_by": "someone_who_never_signed_in"})
    assert response.status_code == 422


def test_authorize_records_the_session_member_and_only_the_selected_rule(client):
    from tests.api_support import read_workspace
    response = client.post("/api/policies/pol_utilities/authorize",
        json={"mode": "standing", "per_run_cap": "2500"})
    assert response.status_code == 200
    body = response.json()
    assert body["authorized_by"] == client.identity["user"]["id"]
    household = read_workspace(client).household
    assert household.policies["pol_utilities"].mandate.authorized_by == client.principal.user_id
    assert household.policies["pol_utilities"].mandate.active
    assert not household.policies["pol_insurance"].mandate.active
