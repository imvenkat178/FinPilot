"""Verified wording is cached without figures and reused with the current figures."""
from decimal import Decimal

from finpilot.ai.tools import ToolRegistry
from finpilot.ai.wording_cache import WordingCache
from finpilot.money import Money
from finpilot.seed.demo import demo_household
from tests.test_fact_tokens import mock_agent


def test_cache_expires_evicts_and_scopes_keys():
    now = [0.0]
    cache = WordingCache(max_entries=2, ttl_seconds=10, clock=lambda: now[0])
    cache.put("a", "one")
    cache.put("b", "two")
    cache.put("c", "three")
    assert cache.get("a") is None and cache.get("c") == "three"
    now[0] = 11
    assert cache.get("c") is None
    assert WordingCache.key("household-1", "compose", user="x") != WordingCache.key("household-2", "compose", user="x")


def test_cached_wording_is_reused_with_the_current_figures():
    household = demo_household()
    cache = WordingCache()
    agent, server = mock_agent(ToolRegistry(household), [], 12393, wording_cache=cache, cache_scope="household")
    try:
        first = agent.ask("How much can I spend this week?")
        assert first.used_model and first.wording_cache == "miss"
        checking = household.accounts["acc_checking"]
        checking.available = checking.available + Money(Decimal("100"), checking.currency)
        second = agent.ask("How much can I spend this week?")
        assert second.used_model and second.wording_cache == "hit"
        before = first.tool_results[0]["amount"]["display"]
        after = second.tool_results[0]["amount"]["display"]
        assert before != after and after in second.answer and before not in second.answer
    finally:
        server.shutdown()


def test_rejected_wording_is_never_cached():
    cache = WordingCache()
    agent, server = mock_agent(ToolRegistry(demo_household()), ["hallucinate"], 12394,
                               wording_cache=cache, cache_scope="household")
    try:
        assert not agent.ask("How much can I spend this week?").used_model
        assert cache.stats()["entries"] == 0
    finally:
        server.shutdown()


def test_a_repeated_plan_is_reused_and_validated_again():
    import json
    from tests.chat_fixtures import workflow_client
    from tests.test_workflow_invariants import ScriptedModel

    plan = {"operations": [{"capability": "get_spending_allowance", "arguments": {"days": 14}}], "clarification": ""}
    with workflow_client() as c:
        model = ScriptedModel([json.dumps(plan)])
        c.runtime.llm = model
        request = {"question": "How much can I safely spend for the next 14 days?", "workflow_mode": "model"}
        first = c.post("/api/ask", json=request).json()
        second = c.post("/api/ask", json=request).json()
        assert len(model.calls) == 1
        assert first["workflow"]["planner"]["cache"] == "miss" and second["workflow"]["planner"]["cache"] == "hit"
        assert second["workflow"]["planner"]["accepted"]
        assert second["workflow"]["operations"] == first["workflow"]["operations"]
