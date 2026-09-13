"""Model tool authorization uses synthetic in-memory completions, never a live LLM."""
from decimal import Decimal
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from finpilot.ai.graph import FinanceAgent, ToolChoice
from finpilot.ai.guardrails import AnswerState
from finpilot.ai.llm import LLMUnavailable
from finpilot.ai.router import template_answer
from finpilot.ai.tools import ToolRegistry
from finpilot.execution.engine import ExecutionEngine, SimulatedProvider
from finpilot.seed.demo import demo_household
from finpilot.persistence.codec import encode

WRITES = {"pause_recurring_policy", "skip_next_occurrence", "pay_bill_once"}


class Model:
    available = True
    config = SimpleNamespace(timeout=12, model="synthetic-tool-choice")

    def __init__(self, calls):
        self.calls = calls
        self.schemas = []
        self.chat_count = 0

    def chat(self, messages, *, tools, timeout):
        self.schemas = tools
        self.chat_count += 1
        return {"choices": [{"message": {"role": "assistant", "tool_calls":
            self.calls if self.chat_count == 1 else []}}]}

    def complete(self, *args, **kwargs):
        raise LLMUnavailable("Synthetic test exercises calculator wording after tool selection")


def tool_call(name, arguments="{}"):
    return {"id": "call-synthetic", "function": {"name": name, "arguments": arguments}}


def registry():
    hh = demo_household()
    return ToolRegistry(hh, execution=ExecutionEngine(hh, SimulatedProvider()))


def test_model_schemas_exclude_mutators_and_unclassified_future_tools():
    tools = registry()
    tools._add("unreviewed_tool", "Not classified", {"type": "object", "properties": {}}, lambda: {})
    readonly = {schema["function"]["name"] for schema in tools.openai_schemas(read_only=True)}
    assert not readonly.intersection(WRITES)
    assert "unreviewed_tool" not in readonly
    assert {"get_money_overview", "get_cash_forecast", "what_if_extra_payment"} <= readonly
    assert WRITES <= {schema["function"]["name"] for schema in tools.openai_schemas()}


@pytest.mark.parametrize("name", sorted(WRITES))
def test_model_cannot_mutate_for_an_informational_question(name):
    tools = registry()
    before = encode(tools.hh)
    policy_id, bill_id = next(iter(tools.hh.policies)), next(iter(tools.hh.bills))
    arguments = {"bill_id": bill_id} if name == "pay_bill_once" else {"policy_id": policy_id}
    model = Model([tool_call(name, json.dumps(arguments))])
    tools.call = Mock(wraps=tools.call)
    result = FinanceAgent(tools, model, compile_graph=False).ask("How much money do I have?", tool_choice=ToolChoice.MODEL)
    assert encode(tools.hh) == before
    assert tools.execution.groups == {}
    assert all(call.args[0] not in WRITES for call in tools.call.call_args_list)
    assert result.tools_called == ["get_money_overview"]
    assert result.state == AnswerState.INFORMATIONAL
    assert result.answer == template_answer("money_overview", result.tool_results[0])
    assert any("not authorized" in item.get("error", "") for item in result.trace)
    assert not WRITES.intersection(schema["function"]["name"] for schema in model.schemas)


def test_unclassified_fabricated_tool_is_blocked_at_execution():
    tools = registry()
    mutation = Mock(return_value={"changed": True})
    tools._add("unreviewed_tool", "Not classified", {"type": "object", "properties": {}}, mutation)
    result = FinanceAgent(tools, Model([tool_call("unreviewed_tool")]), compile_graph=False).ask(
        "How much money do I have?", tool_choice=ToolChoice.MODEL)
    mutation.assert_not_called()
    assert result.tools_called == ["get_money_overview"]


@pytest.mark.parametrize("arguments", ["[]", "null", "true", "not-json", "{", "{\"account_id\": 7}",
    "{\"days\": true}", "{\"days\": \"7\"}", "{\"days\": 2.5}", "{\"unknown\": 1}", {}])
def test_malformed_arguments_do_not_reach_python_tool(arguments):
    tools = registry()
    tools.call = Mock(wraps=tools.call)
    result = FinanceAgent(tools, Model([tool_call("get_cash_forecast", arguments)]), compile_graph=False).ask(
        "How much money do I have?", tool_choice=ToolChoice.MODEL)
    assert all(call.args[0] != "get_cash_forecast" for call in tools.call.call_args_list)
    assert result.tools_called == ["get_money_overview"]
    assert any(item.get("error") == "invalid model tool arguments" for item in result.trace)


@pytest.mark.parametrize("calls", ["wrong-shape", ["wrong-shape"], [{"function": "wrong-shape"}],
    [{"function": {"name": []}}], [tool_call("does_not_exist")]])
def test_malformed_calls_fall_back_without_crashing(calls):
    tools = registry()
    result = FinanceAgent(tools, Model(calls), compile_graph=False).ask("How much money do I have?", tool_choice=ToolChoice.MODEL)
    assert result.tools_called == ["get_money_overview"]


@pytest.mark.parametrize("name, arguments", [
    ("what_if_extra_payment", "{}"),
    ("what_if_extra_payment", '{"amount": NaN}'),
    ("what_if_extra_payment", '{"amount": Infinity}'),
    ("choose_card", '{"amount": 80, "category": "invented"}'),
])
def test_required_fields_nonfinite_numbers_and_invalid_enums_are_rejected(name, arguments):
    tools = registry()
    tools.call = Mock(wraps=tools.call)
    result = FinanceAgent(tools, Model([tool_call(name, arguments)]), compile_graph=False).ask(
        "How much money do I have?", tool_choice=ToolChoice.MODEL)
    assert all(call.args[0] != name for call in tools.call.call_args_list)
    assert result.tools_called == ["get_money_overview"]


def test_unrelated_model_tool_cannot_break_template_or_relabel_answer_state():
    tools = registry()
    result = FinanceAgent(tools, Model([tool_call("get_tax_profile")]), compile_graph=False).ask(
        "What if I pay another $300 toward debt?", tool_choice=ToolChoice.MODEL)
    assert result.tools_called == ["what_if_extra_payment", "get_tax_profile"]
    assert result.state == AnswerState.HYPOTHETICAL
    assert result.answer.startswith("Scenario only")
    assert Decimal(result.tool_results[0]["extra_per_month"]["amount"]) == Decimal("300")
    assert any(item.get("mode") == "router_fallback" and item.get("model_tool") == "get_tax_profile" for item in result.trace)


def test_direct_explicit_mutation_interface_remains_available():
    tools = registry()
    policy_id = next(iter(tools.hh.policies))
    result = tools.call("pause_recurring_policy", {"policy_id": policy_id, "paused": True})
    assert not result.get("error")
    assert tools.hh.policies[policy_id].paused
    assert not tools.spec("pause_recurring_policy").read_only


def test_default_account_applies_to_declared_parameters_on_router_and_model_paths():
    tools = registry()
    selected = next(a.id for a in tools.hh.accounts.values() if a.type.value == "savings")
    for choice in (ToolChoice.ROUTER, ToolChoice.MODEL):
        tools.call = Mock(wraps=tools.call)
        result = FinanceAgent(tools, Model([tool_call("get_cash_forecast")]), compile_graph=False,
            default_account_id=selected).ask("Explain my cash forecast", tool_choice=choice)
        assert result.tool_results[0]["account_id"] == selected
        call = next(c for c in tools.call.call_args_list if c.args[0] == "get_cash_forecast")
        assert call.args[1]["account_id"] == selected
    agent = FinanceAgent(tools, Model([]), compile_graph=False, default_account_id=selected)
    assert agent._context_arguments("get_money_overview", {}) == {}
    assert agent._context_arguments("get_cash_forecast", {"account_id": "explicit-account"}) == {"account_id": "explicit-account"}


@pytest.mark.parametrize("name, arguments", [
    ("get_cash_forecast", {"days": 10**12}),
    ("get_cash_forecast", {"days": 0}),
    ("get_spending_allowance", {"days": -1}),
    ("get_recurring_activity", {"horizon_days": 10**12}),
    ("stress_income_loss", {"months": 121}),
    ("get_paycheck_plan", {"year": 2201, "month": 1}),
    ("get_paycheck_plan", {"year": 2026, "month": 13}),
    ("what_if_extra_payment", {"amount": float("nan")}),
    ("get_cash_forecast", {"account_id": "x" * 501}),
])
def test_shared_registry_validation_rejects_unsafe_arguments_before_calculation(name, arguments):
    tools = registry()
    expensive = Mock(side_effect=AssertionError("Invalid inputs must not reach a calculator"))
    tools.spec(name).fn = expensive
    result = tools.call(name, arguments)
    assert result.get("error") and result["_tool"] == name
    expensive.assert_not_called()


def test_model_cannot_run_unbounded_duration():
    tools = registry()
    tools.call = Mock(wraps=tools.call)
    result = FinanceAgent(tools, Model([tool_call("get_cash_forecast", '{"days": 1000000000000}')]),
        compile_graph=False).ask("How much money do I have?", tool_choice=ToolChoice.MODEL)
    assert result.tools_called == ["get_money_overview"]
    assert all(call.args[0] != "get_cash_forecast" for call in tools.call.call_args_list)
    schema = tools.spec("get_cash_forecast").parameters["properties"]["days"]
    assert schema["minimum"] == 1 and schema["maximum"] == 3660


@pytest.mark.parametrize("question, intent", [
    ("What if I pay another $300 toward debt every payday?", "extra_payment_cadence_clarification"),
    ("Which card should I use in Paris?", "card_location_clarification"),
])
def test_required_clarification_bypasses_model_selection_and_composition(question, intent):
    tools = registry()
    model = Model([])
    model.chat = Mock(side_effect=AssertionError("Clarification cannot rely on model selection"))
    model.complete = Mock(side_effect=AssertionError("Clarification must preserve its exact wording"))
    result = FinanceAgent(tools, model, compile_graph=False).ask(question, tool_choice=ToolChoice.MODEL)
    assert result.intent == intent
    assert not result.used_model
    assert any(item.get("mode") == "clarification" for item in result.trace)
    model.chat.assert_not_called()
    model.complete.assert_not_called()


def test_model_does_not_spend_another_round_after_required_calculation_completes():
    tools = registry()
    model = Model([tool_call("get_money_overview")])
    result = FinanceAgent(tools, model, compile_graph=False).ask(
        "How much money do I have?", tool_choice=ToolChoice.MODEL)
    assert model.chat_count == 1
    assert result.tools_called == ["get_money_overview"]
    assert result.tool_results[0]['total_cash']['amount'] == '25900.00'
