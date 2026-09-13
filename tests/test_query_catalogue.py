"""Catalogue fixtures and its explicit offline mode cannot contaminate each other."""
from finpilot.ai.graph import FinanceAgent, ToolChoice
from finpilot.ai.llm import LocalLLM, LLMConfig
from finpilot.execution.engine import LegState
from tests import query_suite


def test_transfer_failure_fixture_does_not_consume_another_cases_buying_power():
    failure_case = next(c for c in query_suite.CASES if c.expect_tool == "explain_transfer_outcome")
    failure = query_suite.case_registry(failure_case)
    assert any(leg.state == LegState.OUTCOME_UNKNOWN for group in failure.execution.groups.values()
               for leg in group.legs)
    assert failure.hh.accounts["acc_checking"].current.amount < 3200

    dinner = next(c for c in query_suite.CASES if c.question == "Which of my cards should I use for an $80 dinner?")
    fresh = query_suite.case_registry(dinner)
    assert fresh.hh.accounts["acc_checking"].current.amount == 3200
    assert fresh.hh.transactions == []
    assert fresh.execution.groups == {}
    assert fresh.execution.reservations == {}
    model = LocalLLM(LLMConfig())
    try:
        answer = query_suite.verify(FinanceAgent(fresh, model, compile_graph=False), dinner, ToolChoice.ROUTER)
        assert answer["problems"] == []
        assert answer["result"].tool_results[0]["winner"]["net_value"]["amount"] == "3.20"
    finally:
        model.close()


def test_full_catalogue_offline_never_discovers_or_calls_a_live_model(monkeypatch, capsys):
    def forbidden_discovery():
        raise AssertionError("Offline evaluation must not discover a live model")
    monkeypatch.setattr(query_suite, "autodetect", forbidden_discovery)
    monkeypatch.setattr("sys.argv", ["query_suite", "--offline"])
    assert query_suite.main() == 0
    output = capsys.readouterr().out
    assert "34/34 passed" in output
    assert "model wrote 0 of 31 answers" in output


def test_rejected_draft_is_not_confused_with_invalid_published_answer(monkeypatch):
    case = next(c for c in query_suite.CASES if c.question == "What is my net worth?")
    registry = query_suite.case_registry(case)
    model = LocalLLM(LLMConfig(base_url="http://unused.test/v1", model="synthetic"))
    monkeypatch.setattr(model, "complete", lambda *args, **kwargs: "Your cash is $999999.00 and net worth is $999999.00.")
    result = query_suite.verify(FinanceAgent(registry, model, compile_graph=False),case,ToolChoice.ROUTER)
    assert not result['result'].used_model
    assert result['result'].grounding['ok'] is False
    assert result['problems'] == []
    model.close()
