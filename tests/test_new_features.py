"""Coverage for the top-priority features added after the initial build:
account connection health, recurring-rule activity with per-rule pause/skip,
and one-time bill payments -- all named in the specification's "First
production release" tier (table 15) and the Screens table (table 7), and not
covered by the original worked-example suite.
"""
from datetime import date

import pytest

from finpilot.ai.tools import ToolRegistry
from finpilot.engine.allocator import AllocationEngine
from finpilot.engine.recurring import RecurringActivityEngine
from finpilot.execution.engine import ExecutionEngine, SimulatedProvider
from finpilot.seed.demo import demo_household


def make_registry():
    hh = demo_household()
    ex = ExecutionEngine(hh, SimulatedProvider())
    return hh, ex, ToolRegistry(hh, execution=ex)


# ---------------------------------------------------------------------------
# account connections
# ---------------------------------------------------------------------------

def test_connection_health_is_visible_per_account():
    hh, ex, tr = make_registry()
    result = tr.call("get_account_connections")
    assert result["unhealthy_count"] == 1
    row = next(a for a in result["accounts"] if a["id"] == "acc_401k")
    assert row["healthy"] is False
    assert "re-authentication" in row["issue"]
    assert row["affects_planning"] is True


def test_a_healthy_account_names_no_issue():
    hh, ex, tr = make_registry()
    result = tr.call("get_account_connections")
    row = next(a for a in result["accounts"] if a["id"] == "acc_checking")
    assert row["healthy"] is True
    assert row["issue"] is None


def test_connection_scope_hides_out_of_scope_accounts():
    hh = demo_household()
    tr = ToolRegistry(hh, allowed_account_ids={"acc_checking"})
    result = tr.call("get_account_connections")
    ids = {a["id"] for a in result["accounts"]}
    assert ids == {"acc_checking"}


# ---------------------------------------------------------------------------
# recurring activity: visibility
# ---------------------------------------------------------------------------

def test_upcoming_runs_cover_on_income_policies_via_the_paycheck_schedule():
    """Demo policies use cadence=ON_INCOME with no policy-level Schedule; their
    trigger dates must come from the income source's own schedule, the same
    source the allocator reads."""
    hh = demo_household()
    eng = RecurringActivityEngine(hh)
    runs = eng.upcoming_runs(horizon_days=45, as_of=date(2026, 9, 11))
    assert runs, "expected at least one upcoming occurrence"
    dates = {r.date for r in runs}
    assert date(2026, 9, 15) in dates
    assert all(r.will_run for r in runs), "a fully authorized demo plan should show every rule running"


def test_unresolved_is_empty_when_every_policy_has_a_standing_mandate():
    hh = demo_household()
    eng = RecurringActivityEngine(hh)
    assert eng.unresolved() == []


def test_an_unauthorized_policy_is_both_unresolved_and_wont_run():
    from finpilot.models import AuthorizationMode, Mandate
    hh = demo_household()
    pol = hh.policies["pol_utilities"]
    pol.mandate = Mandate(mode=AuthorizationMode.EXPLORE)   # never authorized
    eng = RecurringActivityEngine(hh)
    unresolved_ids = {u["policy_id"] for u in eng.unresolved()}
    assert "pol_utilities" in unresolved_ids
    runs = eng.upcoming_runs(horizon_days=45, as_of=date(2026, 9, 11))
    util_runs = [r for r in runs if r.policy_id == "pol_utilities"]
    assert util_runs and all(not r.will_run for r in util_runs)
    assert "authoriz" in util_runs[0].reason_if_not


# ---------------------------------------------------------------------------
# pause and skip: both the tool surface and the allocator's own behavior
# ---------------------------------------------------------------------------

def test_pausing_one_policy_stops_every_future_occurrence():
    hh, ex, tr = make_registry()
    tr.call("pause_recurring_policy", {"policy_id": "pol_insurance", "paused": True})
    eng = RecurringActivityEngine(hh)
    runs = [r for r in eng.upcoming_runs(horizon_days=90, as_of=date(2026, 9, 11))
            if r.policy_id == "pol_insurance"]
    assert len(runs) >= 2, "need more than one occurrence in the horizon to prove it"
    assert all(not r.will_run for r in runs)
    assert all(r.reason_if_not == "policy is paused" for r in runs)


def test_resuming_a_paused_policy_restores_it():
    hh, ex, tr = make_registry()
    tr.call("pause_recurring_policy", {"policy_id": "pol_insurance", "paused": True})
    tr.call("pause_recurring_policy", {"policy_id": "pol_insurance", "paused": False})
    assert hh.policies["pol_insurance"].paused is False


def test_skip_next_stops_only_the_next_occurrence_not_the_one_after():
    hh, ex, tr = make_registry()
    tr.call("skip_next_occurrence", {"policy_id": "pol_utilities"})
    eng = RecurringActivityEngine(hh)
    runs = [r for r in eng.upcoming_runs(horizon_days=90, as_of=date(2026, 9, 11))
            if r.policy_id == "pol_utilities"]
    assert len(runs) >= 2
    assert runs[0].will_run is False
    assert runs[0].reason_if_not == "user chose to skip the next occurrence"
    assert runs[1].will_run is True


def test_pause_and_skip_are_unknown_policy_safe():
    hh, ex, tr = make_registry()
    assert "error" in tr.call("pause_recurring_policy", {"policy_id": "nope"})
    assert "error" in tr.call("skip_next_occurrence", {"policy_id": "nope"})


def test_a_paused_policy_is_excluded_from_the_monthly_plan():
    """The allocator itself, not just the activity view, must honor pause."""
    hh = demo_household()
    hh.policies["pol_insurance"].paused = True
    plan = AllocationEngine(hh).build_monthly_plan(2026, 9)
    assert not any(t.policy_id == "pol_insurance" for t in plan.targets)


def test_a_skip_next_policy_is_excluded_from_the_monthly_plan():
    hh = demo_household()
    hh.policies["pol_insurance"].skip_next = True
    plan = AllocationEngine(hh).build_monthly_plan(2026, 9)
    assert not any(t.policy_id == "pol_insurance" for t in plan.targets)


def test_pausing_one_policy_does_not_touch_another():
    hh, ex, tr = make_registry()
    tr.call("pause_recurring_policy", {"policy_id": "pol_insurance", "paused": True})
    assert hh.policies["pol_utilities"].paused is False
    assert hh.policies["pol_mortgage"].paused is False


# ---------------------------------------------------------------------------
# one-time bill payments
# ---------------------------------------------------------------------------

def test_paying_a_bill_with_a_backing_mandate_preflights_clean():
    hh, ex, tr = make_registry()
    result = tr.call("pay_bill_once", {"bill_id": "bill_mortgage"})
    assert result["preflight"]["ok"] is True
    assert result["leg"]["amount"]["amount"] == "1800.00"
    assert result["leg"]["state"] == "draft"


def test_paying_a_bill_never_moves_money_by_itself():
    """Building the one-time payment must never advance the leg past draft --
    execution is a separate, explicit step."""
    hh, ex, tr = make_registry()
    tr.call("pay_bill_once", {"bill_id": "bill_mortgage"})
    legs = [l for g in ex.groups.values() for l in g.legs]
    assert legs and all(l.state.value == "draft" for l in legs)


def test_paying_an_ad_hoc_bill_with_no_backing_policy_is_honestly_blocked():
    hh, ex, tr = make_registry()
    result = tr.call("pay_bill_once", {"bill_id": "bill_utilities"})
    assert result["preflight"]["ok"] is False
    assert any("mandate" in b.lower() for b in result["preflight"]["blockers"])


def test_pay_bill_once_unknown_bill_is_an_honest_error():
    hh, ex, tr = make_registry()
    result = tr.call("pay_bill_once", {"bill_id": "nope"})
    assert "error" in result


def test_pay_bill_once_without_an_execution_engine_is_an_honest_error():
    hh = demo_household()
    tr = ToolRegistry(hh)   # no execution engine attached
    result = tr.call("pay_bill_once", {"bill_id": "bill_mortgage"})
    assert "error" in result


def test_build_group_from_bill_raises_on_unknown_bill_at_the_engine_level():
    hh = demo_household()
    ex = ExecutionEngine(hh, SimulatedProvider())
    with pytest.raises(KeyError):
        ex.build_group_from_bill("not_a_real_bill")


# ---------------------------------------------------------------------------
# REST surface -- app.py wiring for all of the above
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from finpilot.api.app import app
    return TestClient(app)


def test_api_connections_endpoint(client):
    r = client.get("/api/connections")
    assert r.status_code == 200
    assert r.json()["unhealthy_count"] == 1


def test_api_recurring_activity_endpoint(client):
    r = client.get("/api/recurring/activity", params={"horizon_days": 45})
    assert r.status_code == 200
    assert len(r.json()["upcoming_runs"]) > 0


def test_api_skip_next_endpoint(client):
    r = client.post("/api/policies/pol_utilities/skip-next")
    assert r.status_code == 200
    assert r.json()["skip_next"] is True


def test_api_skip_next_unknown_policy_is_404(client):
    r = client.post("/api/policies/not_a_real_policy/skip-next")
    assert r.status_code == 404


def test_api_pay_bill_once_endpoint(client):
    r = client.post("/api/bills/bill_mortgage/pay-once")
    assert r.status_code == 200
    assert r.json()["preflight"]["ok"] is True


def test_api_pay_bill_once_unknown_bill_is_404(client):
    r = client.post("/api/bills/not_a_real_bill/pay-once")
    assert r.status_code == 404


def test_api_dashboard_includes_the_new_sections(client):
    r = client.get("/api/dashboard")
    assert r.status_code == 200
    data = r.json()
    assert "connections" in data and "recurring_activity" in data
    assert data["connections"]["unhealthy_count"] == 1
