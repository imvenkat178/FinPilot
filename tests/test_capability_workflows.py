"""All capabilities exercised through /ask, never by calling commands directly."""
import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import pytest
from sqlalchemy import select,func
from tests.chat_capability_manifest import CASES
from tests.chat_fixtures import workflow_client,materialize
from tests.test_chat_actions import proposal,confirm
from finpilot.persistence.action_models import ActionReceiptRow,WorkflowStateRow
from finpilot.persistence.conversation_models import GenerationRow
from finpilot.services.conversations import ConversationService

def test_manifest_matches_catalog():
    with workflow_client() as c:
        names={v["name"] for v in c.get("/api/assistant/capabilities").json()["capabilities"]}
        assert {x.name for x in CASES}==names

@pytest.mark.parametrize("case",CASES,ids=lambda c:c.name)
def test_each_capability_chat_to_application(case):
    with workflow_client() as c:
        question,args=materialize(case,c)
        before=c.runtime.read(c.principal).snapshot()
        response=c.post("/api/ask",json={"question":question,"conversation_id":c.action_conversation,
            "workflow_input":[{"capability":case.name,"arguments":args}]})
        assert response.status_code==200,response.text
        answer=response.json()
        assert answer.get("workflow",{}).get("resolved"),answer
        assert answer["workflow"]["operations"]==[{"capability":case.name,"arguments":args}]
        assert c.runtime.read(c.principal).snapshot()==before
        parts=answer["parts"]
        if any(v["type"]=="proposal" for v in parts):
            p=proposal(answer)
            saved=confirm(c,p)
            assert saved.status_code==200,saved.text
            receipt=saved.json()["receipt"]
            assert receipt["status"]=="succeeded"
            assert confirm(c,p).json()["receipt"]==receipt
            assert c.get("/api/bootstrap").json()["revision"]==receipt["revision"]
            detail=c.get("/api/conversations/"+c.action_conversation).json()
            assert detail["answers"][-1]["action_receipts"][-1]==receipt
            with c.runtime.db.sessions() as s:
                assert s.scalar(select(func.count()).select_from(ActionReceiptRow).where(ActionReceiptRow.proposal_id==p["id"]))==1
            if p["preview"]["mode"]=="write":
                assert c.runtime.read(c.principal).revision==p["revision"]+1
        else:
            expected="handoff" if case.name in {"upload_csv","connect_bank","connect_mcp","create_mcp_access","upload_document","select_documents","new_conversation","open_conversation"} else "navigation" if case.name=="navigate" else "result"
            assert any(x["type"]==expected for x in parts)

def test_concurrent_confirm_returns_same_receipt_and_one_revision(tmp_path):
    with workflow_client("sqlite:///"+(tmp_path/"concurrent.db").as_posix()) as c:
        a=c.post("/api/ask",json={"question":"Update bill Mortgage payment amount to 1999.99"}).json()
        p=proposal(a)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _:confirm(c,p),range(4)))
        assert all(r.status_code==200 for r in results),[r.text for r in results]
        assert all(r.json()["receipt"]==results[0].json()["receipt"] for r in results)
        assert c.runtime.read(c.principal).revision==p["revision"]+1

def test_compound_results_proposal_and_atomic_batch():
    with workflow_client() as c:
        operations=[
            {"capability":"search_transactions","arguments":{"query":"Music"}},
            {"capability":"search_documents","arguments":{"query":"annual fee"}},
            {"capability":"get_spending_allowance","arguments":{"account_id":"acc_checking"}},
            {"capability":"update_bill","arguments":{"record_id":"bill_utilities","amount":"210"}}]
        a=c.post("/api/ask",json={"question":"Find the charges, consult the annual fee, explain spending capacity and update Utilities to 210",
            "workflow_input":operations}).json()
        assert len([p for p in a["parts"] if p["type"]=="result"])==3
        assert a["document_sources"] and a["document_sources"][0]["text"]
        assert c.runtime.read(c.principal).household.bills["bill_utilities"].amount.amount!=Decimal("210")
        assert confirm(c,proposal(a)).status_code==200
        ops=[{"capability":"update_bill","arguments":{"record_id":"bill_utilities","amount":"220"}},
             {"capability":"update_bill","arguments":{"record_id":"bill_mortgage","amount":"1801"}}]
        p=proposal(c.post("/api/ask",json={"question":"Update both bills","workflow_input":ops}).json())
        assert confirm(c,p).status_code==200
        hh=c.runtime.read(c.principal).household
        assert hh.bills["bill_utilities"].amount.amount==Decimal("220")
        assert hh.bills["bill_mortgage"].amount.amount==Decimal("1801")

def test_credentials_rejected_before_storage_or_model():
    with workflow_client() as c:
        with c.runtime.db.sessions() as s:
            count=s.scalar(select(func.count()).select_from(GenerationRow))
        for request in [{"question":"Save api_key=sk-123456789123456789"},
                        {"question":"Import report","workflow_input":[{"capability":"import_mcp","arguments":{"arguments":{"password":"secret"}}}]}]:
            assert c.post("/api/ask",json=request).status_code==422
        with c.runtime.db.sessions() as s:
            assert count==s.scalar(select(func.count()).select_from(GenerationRow))

def test_pending_clarification_survives_language_window_and_restore():
    with workflow_client() as c:
        request={"question":"Update bill amount","conversation_id":c.action_conversation,
            "workflow_input":[{"capability":"update_bill","arguments":{"amount":"40"}}]}
        answer=c.post("/api/ask",json=request).json()
        assert answer["workflow"]["pending"]
        service=ConversationService(c.runtime.db)
        for i in range(8):
            gid,_=service.begin(c.principal,c.action_conversation,"Remember note "+str(i),{})
            service.finish(c.principal,c.action_conversation,gid,{"answer":"Saved"},2)
        restored=c.get("/api/conversations/"+c.action_conversation).json()
        assert restored["pending_workflow"]["operations"][0]["arguments"]["amount"]=="40"
        with c.runtime.db.sessions() as s:
            assert s.get(WorkflowStateRow,c.action_conversation).state["pending"]

def test_provider_failure_is_durable_and_never_blindly_retried():
    with workflow_client() as c:
        a=c.post("/api/ask",json={"question":"Sync bank","workflow_input":[
            {"capability":"sync_bank","arguments":{"record_id":"bank_fixture"}}]}).json()
        p=proposal(a)
        c.bank_provider.fail="/transactions/sync"
        failed=confirm(c,p)
        assert failed.status_code==502,failed.text
        assert c.get("/api/assistant/proposals/"+p["id"]).json()["status"]=="outcome_unknown"
        calls=len(c.bank_provider.calls)
        assert confirm(c,p).status_code==409
        assert len(c.bank_provider.calls)==calls