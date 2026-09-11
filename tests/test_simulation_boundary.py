"""The simulated provider can change fictional sandbox balances only."""
import copy

import pytest
from fastapi.testclient import TestClient

from finpilot.ai.llm import LocalLLM, LLMConfig
from finpilot.api.app import create_app
from finpilot.execution.engine import ExecutionEngine, LegState
from finpilot.persistence.codec import decode, encode
from finpilot.seed.demo import demo_household


def test_real_workspace_rejects_simulation_even_with_legacy_payment_capabilities():
    hh = demo_household()
    hh.payment_sandbox = False
    engine = ExecutionEngine(hh)
    payment = engine.build_group_from_bill("bill_auto").legs[0]
    before = copy.deepcopy(hh.accounts)
    engine.authorize(payment, payment.mandate)
    assert engine.preflight(payment).checks["simulation_workspace"] is False
    engine.execute(payment)
    assert payment.state == LegState.FAILED
    assert engine.provider.calls == []
    assert engine.reservations == {}
    assert hh.accounts == before
    assert hh.transactions == []


@pytest.mark.parametrize("callback, state", [
    ("settle", LegState.PROCESSING),
    ("apply_return", LegState.RECONCILED),
    ("recover_unknown", LegState.OUTCOME_UNKNOWN),
])
def test_late_simulated_callbacks_cannot_change_real_workspaces(callback, state):
    hh = demo_household()
    hh.payment_sandbox = False
    engine = ExecutionEngine(hh)
    payment = engine.build_group_from_bill("bill_auto").legs[0]
    payment.state = state
    payment.settlement_applied = state == LegState.RECONCILED
    before = encode({"household": hh, "groups": engine.groups})
    with pytest.raises(ValueError, match="sample workspaces"):
        getattr(engine, callback)(payment)
    assert encode({"household": hh, "groups": engine.groups}) == before


def test_legacy_snapshot_defaults_to_real_planning_without_payment_authority():
    snapshot = encode(demo_household())
    del snapshot["data"]["fields"]["payment_sandbox"]
    assert decode(snapshot).payment_sandbox is False


@pytest.mark.parametrize("sample", [False, True])
def test_registration_is_the_only_api_boundary_selecting_simulation_mode(tmp_path, sample):
    app = create_app("sqlite:///" + (tmp_path / "sandbox.db").as_posix(), llm=LocalLLM(LLMConfig()))
    with TestClient(app) as client:
        identity = client.post("/api/auth/register", json={
            "name": "Boundary", "email": "boundary@example.com", "password": "boundary-test-password",
            "sample_data": sample}).json()
        client.headers["X-CSRF-Token"] = identity["csrf_token"]
        runtime = app.state.runtime
        principal = runtime.auth.authenticate(client.cookies["finpilot_session"])
        assert runtime.read(principal).household.payment_sandbox is sample
        if sample:
            assert client.post("/api/policies/pol_auto/authorize", json={"per_run_cap": "250"}).status_code == 200
            draft = client.post("/api/bills/bill_auto/pay-once").json()
            result = client.post("/api/execution/run/" + draft["group_id"], json={"confirm_simulation": True})
            assert result.status_code == 200
            assert runtime.read(principal).household.accounts["acc_checking"].current.amount == 2950
        else:
            before = runtime.read(principal)
            assert client.post("/api/execution/run/anything", json={"confirm_simulation": True}).status_code == 403
            assert client.post("/api/execution/recover/anything").status_code == 403
            assert runtime.read(principal).revision == before.revision
            assert client.post("/api/manage/accounts", json={"nickname": "Real account", "type": "checking",
                "current": "100", "available": "100", "payment_sandbox": True}).status_code == 422
            assert runtime.read(principal).household.payment_sandbox is False
