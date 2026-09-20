"""Answers link to the exact records behind them and carry a deterministic confidence rubric."""
from decimal import Decimal

from finpilot.ai.evidence import evidence_confidence, record_refs
from finpilot.ai.tools import ToolRegistry
from finpilot.models import Transaction, Verification
from finpilot.money import Money
from finpilot.seed.demo import demo_household
from tests.api_support import authenticated_client


def test_refs_resolve_only_records_that_exist_in_the_household():
    household = demo_household()
    transaction = Transaction(account_id="acc_checking", description="Coffee",
                              amount=Money(Decimal("-4.85"), household.base_currency))
    household.transactions.append(transaction)
    obligations = ToolRegistry(household).call("get_upcoming_obligations", {"days": 30})
    payloads = [obligations, {"_tool": "search_transactions",
                              "transactions": [{"id": transaction.id, "account_id": transaction.account_id}],
                              "invented": {"id": "acc_not_real", "account_id": "acc_also_not_real"}}]
    refs = record_refs(payloads, household)
    kinds = {(ref["kind"], ref["id"]) for ref in refs}
    assert ("transaction", transaction.id) in kinds and ("account", "acc_checking") in kinds
    assert any(kind == "bill" for kind, _ in kinds)
    assert not {"acc_not_real", "acc_also_not_real"} & {ref["id"] for ref in refs}
    linked = next(ref for ref in refs if ref["kind"] == "transaction")
    assert linked["account_id"] == "acc_checking" and "Coffee" in linked["label"]


def test_confidence_drops_for_stale_data_and_errors():
    household = demo_household()
    for account in household.accounts.values():
        account.connection_healthy = True
        account.provenance.verification = Verification.CONFIRMED
    result = {"intent": "money_overview", "confidence": "exact", "assumptions": [],
              "evidence": [ToolRegistry(household).call("get_money_overview", {})]}
    refs = record_refs(result["evidence"], household)
    fresh = evidence_confidence(result, household, refs)
    assert fresh["level"] == "high" and fresh["score"] == 100
    household.accounts["acc_checking"].provenance.verification = Verification.STALE
    stale = evidence_confidence(result, household, refs)
    assert stale["score"] == 75 and any("stale" in reason for reason in stale["reasons"])
    assert evidence_confidence({**result, "evidence": [{"error": "no data"}]}, household, [])["level"] == "low"
    assert evidence_confidence({"intent": "out_of_domain", "evidence": []}, household, []) is None


def test_document_answers_are_medium_at_best():
    report = evidence_confidence({"intent": "document_retrieval", "evidence": [], "confidence": "source excerpts",
                                  "document_sources": [{"id": "document"}]}, demo_household(), [])
    assert report == {"level": "medium", "score": 70,
                      "reasons": ["Document excerpts are quoted source statements, not verified account data."]}


def test_ask_answers_link_their_accounts_and_carry_confidence():
    with authenticated_client() as client:
        data = client.post("/api/ask", json={"question": "How much money do I have across all my accounts?"}).json()
        assert data["intent"] == "money_overview"
        assert any(ref["kind"] == "account" for ref in data["record_refs"])
        assert data["evidence_confidence"]["level"] in {"high", "medium", "low"}
        assert data["evidence_confidence"]["reasons"] and data["figures"]


def test_evidence_that_fails_its_schema_is_dropped(monkeypatch):
    from finpilot.ai import evidence

    monkeypatch.setattr(evidence, "record_refs", lambda *args, **kwargs: [{"kind": "invented", "id": ""}])
    result = {"intent": "money_overview", "answer": "You hold $10.00.", "confidence": "exact",
              "evidence": [{"_tool": "get_money_overview"}], "trace": []}
    evidence.attach_evidence(result, demo_household())
    assert result["record_refs"] == [] and result["evidence_confidence"] is None and result["figures"] == []
    assert result["trace"][-1]["node"] == "evidence" and result["trace"][-1]["result"] == "rejected"


def test_spending_forecast_stress_and_mortgage_results_link_their_records():
    household = demo_household()
    registry = ToolRegistry(household)
    allowance = registry.call("get_spending_allowance", {"days": 30})
    bills = {ref["id"] for ref in record_refs([allowance], household) if ref["kind"] == "bill"}
    assert bills and all(household.bills[bill].funding_account_id == allowance["account_id"] for bill in bills)
    assert {"account", "bill"} <= {ref["kind"] for ref in record_refs([registry.call("get_cash_forecast", {})], household)}
    assert {"account", "bill"} <= {ref["kind"] for ref in record_refs([registry.call("stress_income_loss", {})], household)}
    assert [ref["kind"] for ref in record_refs([registry.call("get_mortgage_scenarios", {})], household)] == ["liability"]
