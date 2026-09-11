"""Execution lifecycle, idempotency and recovery -- spec section 21.

Section 24's acceptance criteria for this module:

  "Repeated requests, worker races, delayed callbacks, returns, and unknown
   outcomes cannot create duplicate money movement or falsely satisfied bills.
   Partly completed transfer groups show each leg's actual state."
"""
from datetime import date
from decimal import Decimal as D

import pytest

from finpilot.engine.allocator import AllocationEngine
from finpilot.execution.engine import (ExecutionEngine, LegKind, LegState,
                                       PaymentLeg, ProviderFault,
                                       SimulatedProvider, TransferGroup)
from finpilot.models import (AuthorizationMode, Capability, Mandate,
                             PolicyPurpose, now)
from finpilot.money import Money
from finpilot.seed.demo import demo_household


def M(v):
    return Money(D(str(v)), "USD")


@pytest.fixture
def env():
    hh = demo_household()
    prov = SimulatedProvider()
    eng = ExecutionEngine(hh, prov)
    return hh, prov, eng


def standing(cap="2500"):
    return Mandate(mode=AuthorizationMode.STANDING, authorized_at=now(),
                   authorized_by="user_primary", per_run_cap=M(cap))


def leg(hh, amount="500", dest="acc_savings", kind=LegKind.RESERVE_FUNDING,
        mandate=None, group_id="g1", **kw):
    # Due today (the household's `as_of`), not some date after it -- a leg
    # scheduled for the future is exactly what the "not due yet" preflight
    # check now correctly refuses to execute early (see
    # tests/test_reviewed_defects.py), so ordinary lifecycle tests need a
    # leg that is actually due.
    return PaymentLeg(group_id=group_id, kind=kind,
                      source_account_id="acc_checking",
                      destination_account_id=dest, destination_label=dest,
                      amount=M(amount), purpose=PolicyPurpose.GOAL,
                      scheduled_for=hh.as_of,
                      mandate=mandate or standing(), **kw)


# ---------------------------------------------------------------------------
# state machine
# ---------------------------------------------------------------------------

def test_illegal_transitions_are_rejected(env):
    hh, _, _ = env
    l = leg(hh)
    with pytest.raises(ValueError):
        l.transition(LegState.RECONCILED, "skipping the whole lifecycle")


def test_happy_path_reaches_reconciled(env):
    hh, _, eng = env
    l = leg(hh)
    eng.groups["g1"] = TransferGroup(id="g1", legs=[l])
    l.transition(LegState.AUTHORIZED, "standing mandate")
    eng.execute(l)
    assert l.state == LegState.PROCESSING
    eng.settle(l)
    assert l.state == LegState.RECONCILED
    states = [a.state.value for a in l.audit]
    assert states == ["draft", "authorized", "validating", "submitted", "processing",
                      "funds_available", "credited_by_biller", "reconciled"]


def test_a_saved_scenario_cannot_move_money(env):
    """'Saving a hypothetical scenario alone does not authorize a payment.'"""
    hh, _, eng = env

    # a saved scenario with no authorization at all
    unsigned = leg(hh, mandate=Mandate(mode=AuthorizationMode.EXPLORE))
    eng.groups["g1"] = TransferGroup(id="g1", legs=[unsigned])
    unsigned.transition(LegState.AUTHORIZED, "wrongly marked")
    eng.execute(unsigned)
    assert unsigned.state == LegState.FAILED
    assert "no active authorization" in unsigned.failure_reason

    # and the subtler case: a scenario that carries a timestamp but is still
    # only an exploration. Explore mode can never move money.
    explored = leg(hh, group_id="g2", mandate=Mandate(
        mode=AuthorizationMode.EXPLORE, authorized_at=now(),
        authorized_by="user_primary"))
    eng.groups["g2"] = TransferGroup(id="g2", legs=[explored])
    explored.transition(LegState.AUTHORIZED, "wrongly marked")
    eng.execute(explored)
    assert explored.state == LegState.FAILED
    assert "explore mode cannot move money" in explored.failure_reason


# ---------------------------------------------------------------------------
# preflight
# ---------------------------------------------------------------------------

def test_visibility_does_not_imply_payment_capability(env):
    """Section 21: 'API access to one function does not prove access to another.'"""
    hh, _, eng = env
    hh.accounts["acc_checking"].capabilities.discard(Capability.SEND_TRANSFER)
    l = leg(hh)
    eng.groups["g1"] = TransferGroup(id="g1", legs=[l])
    pf = eng.preflight(l)
    assert not pf.ok
    assert any("does not support sending transfers" in b for b in pf.blockers)


def test_principal_only_is_never_silently_downgraded(env):
    """'If the provider does not support a required principal-only instruction,
    do not silently submit an ordinary installment instead.'"""
    hh, _, eng = env
    hh.accounts["acc_auto"].capabilities.discard(Capability.PRINCIPAL_ONLY)
    l = leg(hh, dest="acc_auto", kind=LegKind.EXTRA_PRINCIPAL, principal_only=True)
    eng.groups["g1"] = TransferGroup(id="g1", legs=[l])
    pf = eng.preflight(l)
    assert not pf.ok
    assert any("principal-only" in b for b in pf.blockers)


def test_per_run_cap_blocks_an_oversized_leg(env):
    hh, _, eng = env
    l = leg(hh, amount="3000", mandate=standing("2500"))
    eng.groups["g1"] = TransferGroup(id="g1", legs=[l])
    pf = eng.preflight(l)
    assert not pf.ok
    assert any("per-run cap" in b for b in pf.blockers)


def test_reservations_stop_two_legs_spending_the_same_dollar(env):
    """'Use concurrency control so two workers cannot spend the same
    uncommitted funds.'"""
    hh, _, eng = env
    # +1000 over the original bare $600: acc_checking carries a $1000
    # protected operating-floor reserve (res_floor) that executable funds
    # must now net out (see the "protected reserve" preflight check), so
    # the executable headroom this test actually exercises is still $600
    # -- just $1600 available minus that $1000 floor.
    hh.accounts["acc_checking"].available = M(1600)
    a = leg(hh, amount="500", dest="acc_savings")
    b = leg(hh, amount="500", dest="acc_brokerage")
    eng.groups["g1"] = TransferGroup(id="g1", legs=[a, b])
    for l in (a, b):
        l.transition(LegState.AUTHORIZED, "standing mandate")
    eng.execute(a)
    assert a.state == LegState.PROCESSING
    eng.execute(b)
    assert b.state == LegState.FAILED
    assert "Executable balance" in b.failure_reason


# ---------------------------------------------------------------------------
# idempotency and duplicates
# ---------------------------------------------------------------------------

def test_same_intent_produces_the_same_idempotency_key(env):
    hh, _, _ = env
    assert leg(hh).idempotency_key == leg(hh).idempotency_key
    assert leg(hh, amount="501").idempotency_key != leg(hh).idempotency_key


def test_a_retry_never_creates_a_second_payment(env):
    hh, prov, eng = env
    l = leg(hh)
    eng.groups["g1"] = TransferGroup(id="g1", legs=[l])
    l.transition(LegState.AUTHORIZED, "standing mandate")
    eng.execute(l)
    first_ref = l.provider_ref
    again = prov.submit(l)
    assert again.accepted and again.provider_ref == first_ref
    assert len(prov.submitted) == 1


def test_an_in_flight_leg_blocks_a_replacement(env):
    """Duplicate prevention across autopay and app-originated payments."""
    hh, _, eng = env
    a = leg(hh, dest="acc_card_a", kind=LegKind.CARD_STATEMENT)
    eng.groups["g1"] = TransferGroup(id="g1", legs=[a])
    a.transition(LegState.AUTHORIZED, "standing mandate")
    eng.execute(a)
    b = leg(hh, dest="acc_card_a", kind=LegKind.CARD_STATEMENT, group_id="g2")
    eng.groups["g2"] = TransferGroup(id="g2", legs=[b])
    pf = eng.preflight(b)
    assert not pf.ok
    assert any("already" in x and "withheld" in x for x in pf.blockers)


# ---------------------------------------------------------------------------
# failure and recovery
# ---------------------------------------------------------------------------

def test_a_timeout_becomes_outcome_unknown_not_a_second_payment(env):
    """SC54: 'Mark the outcome unknown, query the provider with the original
    identity, and block duplicate submission until resolved.'"""
    hh, prov, eng = env
    l = leg(hh)
    eng.groups["g1"] = TransferGroup(id="g1", legs=[l])
    prov.inject(l.id, ProviderFault.TIMEOUT)
    l.transition(LegState.AUTHORIZED, "standing mandate")
    eng.execute(l)
    assert l.state == LegState.OUTCOME_UNKNOWN
    assert len(prov.submitted) == 0
    assert l.blocks_replacement

    eng.recover_unknown(l)
    assert l.state == LegState.FAILED
    assert "no payment was created" in l.audit[-1].detail


def test_status_recovery_finds_an_existing_submission(env):
    hh, prov, eng = env
    l = leg(hh)
    eng.groups["g1"] = TransferGroup(id="g1", legs=[l])
    l.transition(LegState.AUTHORIZED, "standing mandate")
    eng.execute(l)
    l.state = LegState.OUTCOME_UNKNOWN            # simulate a lost callback
    eng.recover_unknown(l)
    assert l.state == LegState.PROCESSING


def test_insufficient_funds_fails_cleanly_and_releases_the_reservation(env):
    hh, prov, eng = env
    l = leg(hh)
    eng.groups["g1"] = TransferGroup(id="g1", legs=[l])
    prov.inject(l.id, ProviderFault.INSUFFICIENT_FUNDS)
    l.transition(LegState.AUTHORIZED, "standing mandate")
    eng.execute(l)
    assert l.state == LegState.FAILED
    assert eng.reservations.get("acc_checking", M(0)).is_zero


def test_a_return_after_settlement_rebuilds_the_plan(env):
    """SC55: 'A returned funding transfer can invalidate later allocations even
    after an initial settlement event.'"""
    hh, _, eng = env
    funding = leg(hh, amount="1000", dest="acc_savings")
    dependent = leg(hh, amount="500", dest="acc_brokerage")
    dependent.depends_on = [funding.id]
    eng.groups["g1"] = TransferGroup(id="g1", legs=[funding, dependent])
    funding.transition(LegState.AUTHORIZED, "standing mandate")
    eng.execute(funding)
    eng.settle(funding)
    assert funding.state == LegState.RECONCILED

    dependent.transition(LegState.AUTHORIZED, "standing mandate")
    out = eng.apply_return(funding, "returned by the bank")
    assert funding.state == LegState.RETURNED
    assert dependent.id in out["dependent_legs_canceled"]
    assert dependent.state == LegState.CANCELED
    assert "reopened" in out["recovery"] or out["reopened_obligations"]


def test_dependent_legs_wait_for_confirmed_funding(env):
    hh, _, eng = env
    funding = leg(hh, amount="1000")
    dependent = leg(hh, amount="500", dest="acc_brokerage")
    dependent.depends_on = [funding.id]
    eng.groups["g1"] = TransferGroup(id="g1", legs=[funding, dependent])
    pf = eng.preflight(dependent)
    assert not pf.ok
    assert any("Waits on confirmed usable funding" in b for b in pf.blockers)


def test_a_partial_group_reports_each_leg_honestly(env):
    """'If three transfers succeed and one fails, show the exact result and
    replan the remainder. Do not reverse completed legs automatically.'"""
    hh, prov, eng = env
    hh.accounts["acc_checking"].available = M(5000)
    legs = [leg(hh, amount="500", dest=d) for d in
            ("acc_savings", "acc_brokerage", "acc_card_a", "acc_auto")]
    grp = TransferGroup(id="g1", legs=legs)
    eng.groups["g1"] = grp
    prov.inject(legs[2].id, ProviderFault.REJECTED)
    out = eng.run_group(grp)
    states = {r["leg"]: r["state"] for r in out["results"]}
    assert states[legs[2].id] == "failed"
    assert sum(1 for v in states.values() if v == "reconciled") == 3
    assert out["by_state"]["failed"] == 1


def test_pause_all_distinguishes_stoppable_from_already_sent(env):
    hh, _, eng = env
    hh.accounts["acc_checking"].available = M(5000)
    sent = leg(hh, amount="500", dest="acc_savings")
    future = leg(hh, amount="500", dest="acc_brokerage")
    eng.groups["g1"] = TransferGroup(id="g1", legs=[sent, future])
    sent.transition(LegState.AUTHORIZED, "standing mandate")
    eng.execute(sent)
    future.transition(LegState.AUTHORIZED, "standing mandate")

    out = eng.pause_all()
    assert future.id in out["canceled"]
    assert any(x["leg"] == sent.id for x in out["already_sent_cannot_be_stopped"])
    assert "cannot be stopped through this application" in out["note"]
    assert eng.paused


def test_a_paused_engine_refuses_new_runs(env):
    hh, _, eng = env
    eng.pause_all()
    l = leg(hh)
    eng.groups["g2"] = TransferGroup(id="g2", legs=[l])
    pf = eng.preflight(l)
    assert not pf.ok
    assert any("paused" in b for b in pf.blockers)


# ---------------------------------------------------------------------------
# from allocation to legs
# ---------------------------------------------------------------------------

def test_an_allocation_becomes_independently_tracked_legs(env):
    hh, _, eng = env
    plan, runs = AllocationEngine(hh).allocate_month(2026, 9)
    grp = eng.build_group_from_allocation(runs[0])
    assert len(grp.legs) >= 4
    assert all(l.state == LegState.DRAFT for l in grp.legs)
    # deadline-driven legs are not optional
    mortgage = next(l for l in grp.legs if "Mortgage" in l.destination_label)
    assert not mortgage.optional
    # the protected floor is not a payment
    assert not any(l.destination_label == "Protected checking floor" for l in grp.legs)
