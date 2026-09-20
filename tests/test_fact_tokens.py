"""Model wording never supplies numbers: figures travel as tokens and code inserts them."""
import re
import threading
import time

import pytest

from finpilot.ai.facts import TOKEN, TokenError, figure_sources, mask, substitute, tokenize
from finpilot.ai.graph import FinanceAgent
from finpilot.ai.llm import LLMConfig, LocalLLM
from finpilot.ai.mock_server import serve
from finpilot.ai.tools import ToolRegistry
from finpilot.seed.demo import demo_household

REFERENCE = ("You can spend about $1,350.00 through 2026-09-21. The lowest projected balance is "
             "$2,100.50 on 2026-09-18, with 24 months left at 8.2% utilization. Again, $1,350.00.")


def mock_agent(registry, misbehave, port, **options):
    server = serve(port, misbehave)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(0.25)
    llm = LocalLLM(LLMConfig(base_url=f"http://127.0.0.1:{port}/v1", model="llama3.2-mock:1b"))
    return FinanceAgent(registry, llm, **options), server


def test_tokenize_replaces_every_figure_and_reuses_tokens():
    sheet = tokenize(REFERENCE)
    assert sheet.facts["F1"] == "$1,350.00" and sheet.facts["F2"] == "2026-09-21"
    assert "8.2%" in sheet.facts.values() and "24" in sheet.facts.values()
    assert sheet.tokenized.count("[F1]") == 2
    assert not re.search(r"\d|\$|%", TOKEN.sub("", sheet.tokenized))


def test_substitute_inserts_the_exact_figures():
    text, used = substitute("Roughly [F1] is spendable until [F2].", tokenize(REFERENCE))
    assert text == "Roughly $1,350.00 is spendable until 2026-09-21." and used == ["F1", "F2"]


@pytest.mark.parametrize("draft,reason", [
    ("You could save $4,812.37 instead.", "typed a figure"),
    ("Spend [F1] within 7 days.", "typed a figure"),
    ("Spend [F1] and about two thousand more.", "typed a figure"),
    ("Spend [F9].", "do not exist"),
    ("You have plenty of room to spend.", "dropped every figure"),
])
def test_substitute_rejects_typed_invented_or_missing_figures(draft, reason):
    with pytest.raises(TokenError, match=reason):
        substitute(draft, tokenize(REFERENCE))


def test_mask_hides_context_figures_and_keeps_known_tokens():
    assert mask("Last time you had $1,350.00 and 3 cards.", tokenize(REFERENCE)) == "Last time you had [F1] and # cards."


def test_figure_sources_point_at_result_fields():
    payload = {"_tool": "get_spending_allowance", "through": "2026-09-21",
               "amount": {"amount": "1350.00", "currency": "USD", "display": "$1,350.00"}}
    figures = figure_sources(tokenize("Spend $1,350.00 by 2026-09-21."), [payload])
    assert figures[0]["sources"] == ["get_spending_allowance.amount"]
    assert figures[1]["sources"] == ["get_spending_allowance.through"]


def test_model_wording_uses_tokens_and_reports_figure_sources():
    agent, server = mock_agent(ToolRegistry(demo_household()), [], 12390)
    try:
        result = agent.ask("How much can I spend this week?")
        assert result.used_model and result.grounding["method"] == "figure_tokens"
        assert "$1,350.00" in result.answer
        assert any(figure["text"] == "$1,350.00" and figure["sources"] for figure in result.figures)
    finally:
        server.shutdown()


def test_unknown_requests_never_call_the_model():
    agent, server = mock_agent(ToolRegistry(demo_household()), [], 12391)
    try:
        result = agent.ask("Write me a poem about the sea")
        assert not result.used_model
        assert not any(step.get("node") == "compose" and step.get("mode") == "model" for step in result.trace)
    finally:
        server.shutdown()


def test_added_investment_advice_is_rejected():
    agent, server = mock_agent(ToolRegistry(demo_household()), ["advise"], 12392)
    try:
        result = agent.ask("How much can I spend this week?")
        assert not result.used_model and "index fund" not in result.answer
        assert any("advice" in str(step.get("reason", "")) for step in result.trace)
    finally:
        server.shutdown()


def test_written_numbers_count_as_typed_figures():
    with pytest.raises(TokenError, match="typed a figure"):
        substitute("Spend [F1] across three cards.", tokenize(REFERENCE))


def test_tokens_tolerate_spacing_and_case():
    text, _ = substitute("Spend [ f1 ] by [F2].", tokenize(REFERENCE))
    assert text == "Spend $1,350.00 by 2026-09-21."


def test_every_routed_reference_answer_round_trips_through_tokens():
    from finpilot.ai.facts import _STRAY
    from finpilot.ai.router import route, template_answer
    from tests.chat_capability_manifest import CASES
    from tests.test_ai import SECTION_10_TABLE

    registry = ToolRegistry(demo_household())
    failures = {}
    for question in [case.question for case in CASES] + [question for question, _ in SECTION_10_TABLE]:
        routed = route(question)
        reference = template_answer(routed.intent, registry.call(routed.tool, routed.arguments))
        sheet = tokenize(reference)
        if _STRAY.search(TOKEN.sub(" ", sheet.tokenized)) or substitute(sheet.tokenized, sheet)[0] != reference:
            failures[question] = sheet.tokenized
    assert failures == {}
