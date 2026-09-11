"""Model latency, lifecycle, and verified-fallback contracts (no live runtime)."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

import finpilot.ai.graph as graph_module
import finpilot.ai.llm as llm_module
from finpilot.ai.graph import FinanceAgent, ToolChoice
from finpilot.ai.llm import LLMConfig, LLMUnavailable, LocalLLM
from finpilot.ai.router import route, template_answer
from finpilot.ai.tools import ToolRegistry
from finpilot.seed.demo import demo_household


def configured(handler, **kwargs):
    llm = LocalLLM(LLMConfig(base_url="http://model.test/v1", model="test-model", **kwargs))
    llm._client = httpx.Client(transport=httpx.MockTransport(handler))
    return llm


def completion(content="A response"):
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content}}]})


def clock(monkeypatch, module):
    now = [100.0]
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    return now


def test_status_never_performs_network_io_or_detection(monkeypatch):
    probe = Mock(side_effect=AssertionError("status must not probe"))
    monkeypatch.setattr(llm_module, "probe", probe)
    llm = LocalLLM(LLMConfig(base_url="http://model.test/v1", model="test-model"))
    assert llm.status()["health_state"] == "unknown"
    assert llm._client is None
    probe.assert_not_called()


def test_explicit_failed_detection_is_not_repeated(monkeypatch):
    detect = Mock(return_value=None)
    monkeypatch.setattr(llm_module, "autodetect", detect)
    llm = LocalLLM(llm_module.autodetect())
    assert not llm.available
    detect.assert_called_once()
    detect.reset_mock()
    LocalLLM()
    detect.assert_called_once()


@pytest.mark.parametrize("body", [{"data": [{"id": "test-model"}]}, [{"id": "test-model"}]])
def test_health_cache_reuses_client_authenticates_and_preserves_snapshot(monkeypatch, body):
    now = clock(monkeypatch, llm_module)
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=body)
    llm = configured(handler, api_key="test-only-key", health_ttl=15)
    try:
        assert llm.health()["reachable"]
        for _ in range(4):
            assert llm.health()["reachable"]
        assert len(requests) == 1
        assert requests[0].headers["authorization"] == "Bearer test-only-key"
        snapshot = llm.status()
        snapshot["models_served"].clear()
        assert llm.status()["models_served"] == ["test-model"]
        now[0] += 16
        assert llm.health()["reachable"]
        assert len(requests) == 2
        llm.health(force=True)
        assert len(requests) == 3
    finally:
        llm.close()


def test_failed_health_is_cached_and_expires(monkeypatch):
    now = clock(monkeypatch, llm_module)
    requests = []
    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(200, json={"data": [{"id": "test-model"}]})
    llm = configured(handler, health_ttl=15)
    try:
        assert not llm.health()["reachable"]
        assert not llm.health()["reachable"]
        assert len(requests) == 1
        now[0] += 16
        assert llm.health()["reachable"]
        assert len(requests) == 2
    finally:
        llm.close()


def test_simultaneous_health_checks_coalesce_and_status_does_not_wait():
    entered = threading.Event()
    release = threading.Event()
    requests = []
    def handler(request):
        requests.append(request)
        entered.set()
        assert release.wait(3)
        return httpx.Response(200, json={"data": [{"id": "test-model"}]})
    llm = configured(handler)
    try:
        with ThreadPoolExecutor(max_workers=9) as pool:
            checks = [pool.submit(llm.health) for _ in range(8)]
            assert entered.wait(1)
            status = pool.submit(llm.status).result(timeout=1)
            assert status["health_state"] == "unknown"
            release.set()
            assert all(check.result(timeout=2)["reachable"] for check in checks)
        assert len(requests) == 1
    finally:
        release.set()
        llm.close()


def test_close_finishes_active_call_and_rejects_new_calls():
    entered = threading.Event()
    release = threading.Event()
    def handler(request):
        entered.set()
        assert release.wait(3)
        return completion()
    llm = configured(handler)
    client = llm.client
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            active = pool.submit(llm.chat, [{"role": "user", "content": "Hello"}])
            assert entered.wait(1)
            llm.close()
            assert not client.is_closed
            with pytest.raises(LLMUnavailable, match="closed"):
                llm.chat([])
            release.set()
            assert active.result(timeout=2)["choices"]
        assert client.is_closed
        llm.close()
    finally:
        release.set()
        llm.close()


def test_failure_cooldown_skips_repeated_network_calls_and_then_recovers(monkeypatch):
    now = clock(monkeypatch, llm_module)
    requests = []
    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            raise httpx.ReadTimeout("slow model", request=request)
        return completion()
    llm = configured(handler, failure_cooldown=5)
    try:
        with pytest.raises(LLMUnavailable):
            llm.chat([])
        with pytest.raises(LLMUnavailable, match="temporarily unavailable"):
            llm.chat([])
        assert len(requests) == 1
        assert not llm.status()["reachable"]
        now[0] += 6
        assert llm.chat([])["choices"]
        assert len(requests) == 2
        assert llm.status()["reachable"]
    finally:
        llm.close()


def test_inference_timeout_and_token_budget_are_configurable_and_bounded():
    requests = []
    def handler(request):
        requests.append(request)
        return completion()
    llm = configured(handler, timeout=20, max_tokens=500)
    try:
        llm.chat([], timeout=3, max_tokens=1000)
        assert json.loads(requests[0].content)["max_tokens"] == 500
        assert requests[0].extensions["timeout"]["read"] == 3
        assert requests[0].extensions["timeout"]["connect"] == 2
        with pytest.raises(LLMUnavailable, match="budget"):
            llm.chat([], timeout=0)
        assert len(requests) == 1
        assert LLMConfig().timeout == 12
        assert LLMConfig().max_tokens == 384
        assert LLMConfig(timeout=9999, max_tokens=9999).timeout == 120
        assert LLMConfig(timeout=9999, max_tokens=9999).max_tokens == 2048
    finally:
        llm.close()


@pytest.mark.parametrize("failure", ["timeout", "malformed_json", "missing_choices", "malformed_content"])
def test_model_failures_return_exact_calculator_answer(failure):
    def handler(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("slow model", request=request)
        if failure == "malformed_json":
            return httpx.Response(200, text="not json")
        if failure == "missing_choices":
            return httpx.Response(200, json={})
        return completion([{"type": "unsupported"}])
    registry = ToolRegistry(demo_household())
    llm = configured(handler)
    try:
        agent = FinanceAgent(registry, llm, compile_graph=False)
        result = agent.ask("How much money do I have?")
        expected = template_answer("money_overview", registry.call("get_money_overview"))
        assert result.answer == expected
        assert not result.used_model
        assert result.tool_results
    finally:
        llm.close()


def test_model_budget_is_shared_across_tool_calls_and_composition(monkeypatch):
    now = clock(monkeypatch, graph_module)
    registry = ToolRegistry(demo_household())
    llm = LocalLLM(LLMConfig(base_url="http://model.test/v1", model="test-model", timeout=12))
    def model_tool_call(*args, **kwargs):
        assert kwargs["timeout"] == 12
        now[0] += 13
        return {"choices": [{"message": {"role": "assistant", "tool_calls": [{"id": "a", "function": {"name": "get_money_overview", "arguments": "{}"}}]}}]}
    chat = Mock(side_effect=model_tool_call)
    complete = Mock(side_effect=AssertionError("budget is exhausted"))
    monkeypatch.setattr(llm, "chat", chat)
    monkeypatch.setattr(llm, "complete", complete)
    agent = FinanceAgent(registry, llm, compile_graph=False)
    result = agent.ask("How much money do I have?", tool_choice=ToolChoice.MODEL)
    chat.assert_called_once()
    complete.assert_not_called()
    assert not result.used_model
    assert result.answer == template_answer("money_overview", registry.call("get_money_overview"))


def test_model_tool_budget_limits_tools_within_one_response(monkeypatch):
    registry = ToolRegistry(demo_household())
    llm = LocalLLM(LLMConfig(base_url="http://model.test/v1", model="test-model"))
    calls = [{"id": str(i), "function": {"name": "get_money_overview", "arguments": "{}"}} for i in range(10)]
    chat = Mock(return_value={"choices": [{"message": {"role": "assistant", "tool_calls": calls}}]})
    monkeypatch.setattr(llm, "chat", chat)
    expected = template_answer("money_overview", registry.call("get_money_overview"))
    monkeypatch.setattr(llm, "complete", Mock(return_value=expected))
    agent = FinanceAgent(registry, llm, max_tool_calls=3, compile_graph=False)
    result = agent.ask("How much money do I have?", tool_choice=ToolChoice.MODEL)
    chat.assert_called_once()
    assert len(result.tools_called) == 3
    assert result.answer == expected
    assert result.used_model and result.grounding["ok"]


def test_direct_and_compiled_execution_preserve_answers_evidence_and_guards():
    registry = ToolRegistry(demo_household())
    llm = LocalLLM(LLMConfig())
    direct = FinanceAgent(registry, llm, compile_graph=False)
    compiled = FinanceAgent(registry, llm)
    for q in ("How much can I spend this week?", "How should I split my paycheck?", "Which investments should I buy?"):
        actual, expected = direct.ask(q).to_json(), compiled.ask(q).to_json()
        actual.pop("latency_ms")
        expected.pop("latency_ms")
        assert actual == expected


@pytest.mark.parametrize("question,tool", [
    ("Summarize my accounts", "get_money_overview"),
    ("Which account connections need attention?", "get_account_connections"),
    ("Explain my net worth", "get_money_overview"),
    ("How should I split my next paycheck?", "get_paycheck_plan"),
    ("Why is my paycheck split uneven?", "get_paycheck_plan"),
    ("What bills does my paycheck cover?", "get_paycheck_plan"),
    ("What bills are coming up?", "get_upcoming_obligations"),
    ("Which payments need review?", "get_upcoming_obligations"),
    ("How much is due this month?", "get_upcoming_obligations"),
    ("Explain my cash forecast", "get_cash_forecast"),
    ("How much can I spend this week?", "get_spending_allowance"),
    ("How much should I keep in checking?", "get_buffer"),
    ("How are my savings reserves funded?", "get_money_overview"),
    ("How much is protected for emergencies?", "get_money_overview"),
    ("Compare my debt repayment strategies", "compare_debt_strategies"),
    ("How would an extra payment affect my debt?", "compare_debt_strategies"),
    ("Explain my credit utilization", "check_utilization_timing"),
    ("Explain my recurring rules", "get_automation_status"),
    ("Which automations are paused?", "get_automation_status"),
    ("Explain my tax assumptions", "get_tax_profile"),
    ("How is my cash protected?", "get_deposit_coverage"),
])
def test_workspace_suggestions_reach_the_relevant_calculator(question, tool):
    registry = ToolRegistry(demo_household())
    selected = route(question)
    assert selected.tool == tool
    assert selected.intent != "unknown"
    result = FinanceAgent(registry, LocalLLM(LLMConfig()), compile_graph=False).ask(question)
    assert result.tools_called == [tool]
    assert result.answer and "could not answer" not in result.answer.lower()
    assert not result.used_model


def test_tax_assumptions_do_not_require_debt_or_run_a_scenario():
    household = demo_household()
    household.liabilities.clear()
    registry = ToolRegistry(household)
    result = FinanceAgent(registry, LocalLLM(LLMConfig()), compile_graph=False).ask("Explain my tax assumptions")
    assert result.tools_called == ["get_tax_profile"]
    assert result.intent == "tax_assumptions"
    assert result.state.value == "informational"
    assert "0.24" in result.answer and "False" in result.answer
    assert "amount" not in result.tool_results[0]


def test_unspecified_extra_payment_asks_for_amount_without_inventing_one():
    registry = ToolRegistry(demo_household())
    result = FinanceAgent(registry, LocalLLM(LLMConfig()), compile_graph=False).ask("How would an extra payment affect my debt?")
    assert result.intent == "extra_payment_help"
    assert "How much extra per month" in result.answer
    assert result.state.value == "informational"
