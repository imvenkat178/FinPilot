"""Advice wording and unrelated prompts are stopped by deterministic checks."""
import pytest

from finpilot.ai.guardrails import OUT_OF_DOMAIN_ANSWER, advice_claim_error, in_domain
from tests.api_support import authenticated_client
from tests.chat_capability_manifest import CASES
from tests.test_ai import SECTION_10_TABLE


@pytest.mark.parametrize("draft", [
    "You should buy shares of an index fund with the surplus.",
    "Consider investing the extra in the stock market.",
    "It is time to rebalance your portfolio.",
    "Open a new credit card to earn more rewards.",
    "You are eligible for the credit.",
    "That's legal as long as you keep receipts.",
    "This plan offers guaranteed returns.",
])
def test_added_advice_is_rejected(draft):
    assert advice_claim_error(draft, "You can spend about $1,350.00 through 2026-09-21.")


def test_advice_words_already_in_the_reference_are_allowed():
    reference = "A recast or refinance requires servicer confirmation."
    assert advice_claim_error("A refinance needs servicer confirmation first.", reference) is None
    assert advice_claim_error("You can spend about $1,350.00 this week.", "") is None


def test_every_capability_and_routed_request_passes_the_topic_gate():
    questions = [case.question for case in CASES] + [question for question, _ in SECTION_10_TABLE]
    assert [question for question in questions if not in_domain(question)] == []


@pytest.mark.parametrize("question", [
    "What's the weather in Paris tomorrow?", "Write me a poem about the sea",
    "Who won the football game last night?", "Tell me a joke", "Translate hello into Spanish",
])
def test_unrelated_prompts_are_outside_the_domain(question):
    assert not in_domain(question)


def test_an_unrelated_prompt_gets_the_fixed_answer_without_model_calls():
    with authenticated_client() as client:
        response = client.post("/api/ask", json={"question": "Write me a poem about the sea"})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["intent"] == "out_of_domain" and data["answer"] == OUT_OF_DOMAIN_ANSWER
        assert data["generation"]["calls"] == [] and not data["used_model"]
        assert data["record_refs"] == [] and data["evidence_confidence"] is None


@pytest.mark.parametrize("message", ["thanks!", "Yes, go ahead", "ok, confirm it", "Hello"])
def test_short_acknowledgements_pass_the_topic_gate(message):
    assert in_domain(message)
