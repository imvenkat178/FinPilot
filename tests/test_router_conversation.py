"""Ordinary financial paraphrases select read-only tools and useful fallbacks."""
from datetime import timedelta

import pytest

from finpilot.ai.graph import FinanceAgent
from finpilot.ai.llm import LocalLLM, LLMConfig
from finpilot.ai.router import route, template_answer
from finpilot.ai.tools import ToolRegistry
from finpilot.seed.demo import demo_household
from finpilot.persistence.codec import encode


@pytest.mark.parametrize("question", [
    "Explain my financial overview.",
    "Give me a financial summary.",
    "Can you show me a snapshot of my accounts?",
    "Summarize my finances.",
    "Show me my financial position.",
    "Where do I stand financially?",
    "How are we doing financially?",
    "How much money have I got?",
    "Explain my financial\n overview, please.",
])
def test_conversational_overview_paraphrases(question):
    selected = route(question)
    assert selected.intent == "money_overview"
    assert selected.tool == "get_money_overview"
    assert selected.arguments == {}


@pytest.mark.parametrize("question", [
    "How much can I safely spend this week?",
    "How much could I comfortably spend?",
    "How much money can we spend?",
    "What can I reasonably spend after bills?",
    "How much do I have left to spend?",
    "What is safe for me to spend?",
    "What’s my spending limit?",
    "Show me my safe spending budget.",
    "What is my safe-to-spend amount?",
    "Could we safely afford a hotel?",
])
def test_conversational_spending_paraphrases(question):
    selected = route(question)
    assert selected.intent == "spending_allowance"
    assert selected.tool == "get_spending_allowance"


@pytest.mark.parametrize("question, days", [
    ("How much can I safely spend this week?", 7),
    ("How much could I spend over the next week?", 7),
    ("What can I spend in 2 weeks?", 14),
    ("What can I spend in 9 days?", 9),
    ("Show me my spending limit", 14),
])
def test_explicit_spending_window_is_preserved(question, days):
    assert route(question).arguments == {"days": days}


@pytest.mark.parametrize("question, tool", [
    ("Is my cash safe?", "get_deposit_coverage"),
    ("Why is my account balance not updating?", "get_account_connections"),
    ("How much should I keep in checking?", "get_buffer"),
    ("Which of my cards should I use for a $70 dinner?", "choose_card"),
    ("What if I pay another $300 toward debt?", "what_if_extra_payment"),
    ("How should I split my next paycheck?", "get_paycheck_plan"),
    ("Pause my recurring rule", "get_automation_status"),
])
def test_specific_existing_intents_are_not_displaced(question, tool):
    assert route(question).tool == tool


@pytest.mark.parametrize("question, tool, amount_key", [
    ("How much can I safely spend this week?", "get_spending_allowance", "amount"),
    ("Explain my financial overview.", "get_money_overview", "total_cash"),
])
def test_missed_live_smoke_questions_now_answer_with_real_calculator_amounts(question, tool, amount_key):
    household = demo_household()
    before = encode(household)
    result = FinanceAgent(ToolRegistry(household), LocalLLM(LLMConfig()), compile_graph=False).ask(question)
    assert result.tools_called == [tool]
    assert result.tool_results[0][amount_key]["display"] in result.answer
    assert "Here is what the calculation returned" not in result.answer
    assert not result.used_model
    assert encode(household) == before
    if tool == "get_spending_allowance":
        assert result.tool_results[0]["through"] == (household.as_of + timedelta(days=7)).isoformat()


def test_unknown_fallback_asks_a_useful_question_without_pretending_to_answer():
    household = demo_household()
    registry = ToolRegistry(household)
    selected = route("Can you help me with something?")
    assert selected.intent == "unknown"
    answer = template_answer(selected.intent, registry.get_money_overview())
    assert "Which would you like to look at?" in answer
    assert "paycheck planning" in answer and "spending limits" in answer
    assert household.name not in answer
    assert household.as_of.isoformat() not in answer
    assert "$" not in answer
    assert "calculation returned" not in answer


@pytest.mark.parametrize("question", [
    "Explain my financial overview before I pause automation.",
    "How much can I safely spend without making a payment?",
    "Do not transfer or spend anything; can you help me?",
])
def test_conversational_read_routing_never_grants_mutation_authority(question):
    household = demo_household()
    before = encode(household)
    result = FinanceAgent(ToolRegistry(household), LocalLLM(LLMConfig()), compile_graph=False).ask(question)
    assert not set(result.tools_called) & {"pause_recurring_policy", "skip_next_occurrence", "pay_bill_once"}
    assert encode(household) == before


@pytest.mark.parametrize("question", [
    "Pay $300 extra to this loan every payday",
    "What if I pay another $300 each week?",
    "Pay an additional $300 to my loan biweekly",
    "Put $300 more towards my debt per paycheck",
    "What if I pay $300 extra every two weeks?",
    "What if I pay $300 extra twice a month?",
])
def test_nonmonthly_extra_payment_requires_cadence_clarification(question):
    household = demo_household()
    before = encode(household)
    selected = route(question)
    assert selected.intent == "extra_payment_cadence_clarification"
    assert selected.tool == "what_if_extra_payment"
    assert selected.arguments == {"amount": 300.0}
    result = FinanceAgent(ToolRegistry(household), LocalLLM(LLMConfig()), compile_graph=False).ask(question)
    assert result.state.value == "hypothetical"
    assert "pay frequency" in result.answer
    assert "$300.00 extra per month only" in result.answer
    assert "No payment or plan has been changed" in result.answer
    assert "You can pay" not in result.answer
    assert encode(household) == before


@pytest.mark.parametrize("question", [
    "Which card for a $60 dinner in Paris?",
    "Which card for lunch in Austin?",
    "Which card should I use for a hotel in an unfamiliar country?",
])
def test_unknown_card_location_does_not_calculate_a_domestic_fee_winner(question):
    household = demo_household()
    selected = route(question)
    assert selected.intent == "card_location_clarification"
    result = FinanceAgent(ToolRegistry(household), LocalLLM(LLMConfig()), compile_graph=False).ask(question)
    assert "choose_card" not in result.tools_called
    assert "domestic or abroad" in result.answer
    assert "foreign transaction fees" in result.answer
    assert "No card recommendation" in result.answer


@pytest.mark.parametrize("question, foreign", [
    ("Which card for a $60 dinner abroad?", True),
    ("Which card for a $60 dinner in Japan?", True),
    ("Which card for a $60 dinner in Paris, abroad?", True),
    ("Which card for a domestic $60 dinner in Austin?", False),
    ("Which card for a $60 dinner in person?", False),
    ("Which card for a $60 dinner in a restaurant?", False),
    ("Which card for a $60 dinner in two weeks?", False),
])
def test_explicit_card_fee_context_and_nonlocation_phrases_still_compare(question, foreign):
    selected = route(question)
    assert selected.intent == "card_choice"
    assert selected.arguments["foreign"] is foreign


def test_cadence_clarification_does_not_capture_monthly_mortgage_or_pause_questions():
    assert route("What if I pay $300 extra per month?").intent == "extra_payment"
    assert route("Compare biweekly mortgage payments").intent == "biweekly"
    assert route("Pause my $300 extra loan payment every payday").intent == "automation"
