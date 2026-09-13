"""Independent checks that memory and document quotes preserve financial meaning."""
import json
from finpilot.ai.knowledge import source_answer
from finpilot.ai.llm import LLMConfig
from finpilot.models import Money
from tests.api_support import authenticated_client, edit_workspace


def test_model_cannot_drop_negation_by_selecting_a_substring():
    class Model:
        available = True
        config = LLMConfig(model="test", timeout=1)
        def complete(self, *args, **kwargs):
            return json.dumps({"excerpts": [{"source": 1, "quote": "withdraw before 14 days."}]})
    sources = [{"id": "doc", "title": "Withdrawal terms", "page": 1, "chunk_id": "chunk",
                "text": "You must not withdraw before 14 days."}]
    answer = source_answer("When may I withdraw?", sources, Model())
    assert answer["used_model"] is False
    assert "You must not withdraw before 14 days." in answer["answer"]


def test_followup_reads_current_balance_after_workspace_changes():
    with authenticated_client() as client:
        first = client.post("/api/ask", json={"question": "How much can I spend this week?",
            "context": {"page": "accounts", "account_id": "acc_checking"}}).json()
        with edit_workspace(client) as ctx:
            ctx.household.accounts["acc_checking"].available = Money.of("21.00")
            ctx.household.accounts["acc_checking"].current = Money.of("21.00")
        followup = client.post("/api/ask", json={"question": "Explain that",
            "conversation_id": first["conversation_id"]})
        assert followup.status_code == 200, followup.text
        second = followup.json()
        current = client.runtime.read(client.principal)
        expected = current.registry.call("get_spending_allowance", {"days": 7, "account_id": "acc_checking"})
        assert second["evidence"][0] == expected
        assert second["evidence"][0] != first["evidence"][0]
        assert second["revision"] == current.revision > first["revision"]
        assert second["memory_turns"] == 1


def test_document_financial_numbers_never_replace_account_balances():
    with authenticated_client() as client:
        upload = client.post("/api/documents", files={"file": ("stale-statement.txt",
            b"Your checking available balance is $987654321. This statement was issued long ago.", "text/plain")})
        did = upload.json()["document"]["id"]
        original = client.runtime.read(client.principal)
        first = client.post("/api/ask", json={"question": "What balance is on this statement?", "document_ids": [did]}).json()
        assert "987654321" in first["answer"]
        answer = client.post("/api/ask", json={"question": "How much can I spend this week?",
            "conversation_id": first["conversation_id"]}).json()
        assert answer["intent"] != "document_retrieval"
        assert answer["evidence"][0] == original.registry.call("get_spending_allowance", {"days": 7})
        assert "987654321" not in answer["answer"]
        assert answer["workspace_changed"] is False
        assert client.runtime.read(client.principal).revision == original.revision


def test_targeted_memory_query_does_not_claim_unrelated_history_answers_it():
    class Offline:
        available = False
    sources = [{"id": "memory", "title": "Your prior message", "page": None, "chunk_id": "1",
                "text": "Remember my priority is building an emergency fund."}]
    answer = source_answer("What did I tell you about college tuition?", sources, Offline(), kind="memory")
    assert "do not have a matching detail" in answer["answer"]
    assert "emergency fund" not in answer["answer"]


def test_long_passage_is_visibly_marked_as_incomplete():
    class Offline:
        available = False
    sources = [{"id": "doc", "title": "Terms", "page": 1, "chunk_id": "chunk",
                "text": "Coverage limitations " + "include a variety of conditions " * 35 + "until verified."}]
    answer = source_answer("What coverage limitations apply?", sources, Offline())
    assert "\u2026" in answer["answer"] or "..." in answer["answer"]
