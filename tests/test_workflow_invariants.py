"""Review invariants across independent workers, mixed reads and model failures."""
import json,time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace
import pytest
from sqlalchemy import select,func
from finpilot.persistence.action_models import ActionProposalRow,ActionReceiptRow
from finpilot.persistence.database import Database,utcnow
from finpilot.runtime import Runtime
from finpilot.services.proposals import ProposalService
from tests.chat_fixtures import workflow_client
from tests.test_chat_actions import proposal,confirm
from finpilot.ai.llm import LLMConfig,LocalLLM

class ScriptedModel:
    available=True
    config=SimpleNamespace(timeout=12,model="test-double")
    def __init__(self,replies):
        self.replies=list(replies);self.calls=[]
    def complete(self,system,user,**kw):
        self.calls.append({"system":system,"user":user,**kw})
        return self.replies.pop(0)
    def close(self): pass

def test_two_workers_confirm_one_execution(tmp_path):
    url="sqlite:///"+(tmp_path/"workers.db").as_posix()
    with workflow_client(url) as c:
        other=Runtime(Database(url),LocalLLM(LLMConfig()))
        try:
            p=proposal(c.post("/api/ask",json={"question":"Update bill Mortgage payment amount to 1999.99"}).json())
            services=[ProposalService(c.runtime),ProposalService(other)]
            with ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda service:service.confirm(c.principal,p["id"],p["version"]),services))
            assert results[0]["receipt"]==results[1]["receipt"]
            assert c.runtime.read(c.principal).revision==p["revision"]+1
            with c.runtime.db.sessions() as s:
                assert s.scalar(select(func.count()).select_from(ActionReceiptRow).where(ActionReceiptRow.proposal_id==p["id"]))==1
        finally:
            other.close()

def test_race_cancel_confirm_has_one_terminal_outcome(tmp_path):
    with workflow_client("sqlite:///"+(tmp_path/"cancel.db").as_posix()) as c:
        p=proposal(c.post("/api/ask",json={"question":"Update bill Mortgage payment amount to 1999.99"}).json())
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(confirm,c,p)
            b=pool.submit(c.post,"/api/assistant/proposals/"+p["id"]+"/cancel",json={"version":1})
            outcomes=[a.result(),b.result()]
        assert sorted(r.status_code for r in outcomes)==[200,409]
        latest=c.get("/api/assistant/proposals/"+p["id"]).json()
        assert (latest["status"]=="succeeded")==bool(latest["receipt"])

def test_abandoned_provider_claim_never_restarts():
    with workflow_client() as c:
        p=proposal(c.post("/api/ask",json={"question":"Sync bank","workflow_input":[
            {"capability":"sync_bank","arguments":{"record_id":"bank_fixture"}}]}).json())
        with c.runtime.db.sessions.begin() as s:
            row=s.get(ActionProposalRow,p["id"])
            row.status="running";row.expires_at=utcnow()-timedelta(minutes=1)
        calls=len(c.bank_provider.calls)
        assert c.get("/api/assistant/proposals/"+p["id"]).json()["status"]=="outcome_unknown"
        assert confirm(c,p).status_code==409
        assert len(c.bank_provider.calls)==calls

def test_csv_upload_mapping_review_and_duplicate_rows():
    with workflow_client() as c:
        raw=b"When,Merchant,Value\n09/12/2026,Train,-6.50\n"
        data={"conversation_id":c.action_conversation,"account_id":"acc_checking","map_date":"When",
              "map_description":"Merchant","map_amount":"Value","date_format":"MM/DD/YYYY"}
        before=c.runtime.read(c.principal)
        response=c.post("/api/assistant/csv-preview",data=data,files={"file":("history.csv",raw,"text/csv")})
        assert response.status_code==201,response.text
        p=response.json()["proposal"]
        assert len(c.runtime.read(c.principal).household.transactions)==len(before.household.transactions)
        assert p["preview"]["entries"][0]["result"]["reviewed_rows"][0]["description"]=="Train"
        receipt=confirm(c,p).json()["receipt"]
        assert receipt["results"][0]["imported"]==1
        assert c.runtime.read(c.principal).household.accounts["acc_checking"].current==before.household.accounts["acc_checking"].current
        duplicate=c.post("/api/assistant/csv-preview",data=data,files={"file":("history.csv",raw,"text/csv")}).json()
        assert duplicate["preview"]["duplicates"]==1
        assert confirm(c,duplicate["proposal"]).json()["receipt"]["no_financial_changes"]
        target=c.post("/api/conversations",json={}).json()["conversation"]["id"]
        foreign=c.post("/api/ask",json={"question":"Import this CSV","conversation_id":target,"workflow_input":p["operations"]}).json()
        assert not any(part["type"]=="proposal" for part in foreign["parts"])

def test_missing_compound_input_keeps_reads_and_whole_pending_batch():
    with workflow_client() as c:
        ops=[{"capability":"search_transactions","arguments":{"query":"Music"}},
             {"capability":"update_bill","arguments":{"record_id":"bill_utilities","amount":"210"}},
             {"capability":"update_goal","arguments":{"target":"1000"}}]
        answer=c.post("/api/ask",json={"question":"Review these charges and make these changes","workflow_input":ops}).json()
        assert answer["workflow"]["pending"]
        assert answer["workflow"]["operations"]==ops
        assert any(p["type"]=="result" for p in answer["parts"])
        clarification=next(p for p in answer["parts"] if p["type"]=="clarification")
        assert clarification["operation_index"]==2 and "record_id" in clarification["fields"]
        assert not any(p["type"]=="proposal" for p in answer["parts"])

def test_model_planning_explanation_budget_and_rejected_claim():
    with workflow_client() as c:
        plan={"operations":[{"capability":"get_spending_allowance","arguments":{"days":14}}],"clarification":""}
        model=ScriptedModel([json.dumps(plan),"I transferred $999999 to your savings account."])
        c.runtime.llm=model
        answer=c.post("/api/ask",json={"question":"How much can I safely spend for the next 14 days?","workflow_mode":"model"}).json()
        assert len(model.calls)==1
        assert model.calls[0]["response_format"]["type"]=="json_schema"
        assert model.calls[0]["timeout"]<=12
        assert not answer["explanation"]["attempted"]
        assert not answer["explanation"]["accepted"]
        assert "999999" not in answer["answer"]
        assert answer["workflow"]["planner"]["accepted"]
        assert not any(p["type"]=="proposal" for p in answer["parts"])

def test_model_chat_correction_updates_same_proposal_version():
    with workflow_client() as c:
        a=c.post("/api/ask",json={"question":"Update bill Mortgage payment amount to 1999.99"}).json()
        first=proposal(a)
        model=ScriptedModel([json.dumps({"operations":[{"capability":"update_bill","arguments":{"record_id":"bill_mortgage","amount":"2001"}}],"clarification":""})])
        c.runtime.llm=model
        answer=c.post("/api/ask",json={"question":"Actually make it 2001","conversation_id":a["conversation_id"]}).json()
        updated=proposal(answer)
        assert updated["id"]==first["id"] and updated["version"]==2
        assert confirm(c,first).status_code==409
        assert confirm(c,updated).status_code==200

def test_model_cannot_use_unlisted_actions_or_overlong_batch():
    with workflow_client() as c:
        for operations in [[{"capability":"arbitrary_http","arguments":{"url":"https://example.test"}}],
                           [{"capability":"get_money_overview","arguments":{}}]*5]:
            c.runtime.llm=ScriptedModel([json.dumps({"operations":operations,"clarification":""})])
            before=c.runtime.read(c.principal).snapshot()
            answer=c.post("/api/ask",json={"question":"Show my financial picture","workflow_mode":"model"}).json()
            assert not answer["workflow"]["planner"]["accepted"]
            assert c.runtime.read(c.principal).snapshot()==before

def test_paycheck_events_and_material_effects_match_review():
    from decimal import Decimal
    from finpilot.services.commands import execute
    with workflow_client() as c:
        def reviewed(name,args):
            answer=c.post("/api/ask",json={"question":"Prepare these exact changes","workflow_input":[{"capability":name,"arguments":args}]}).json()
            p=proposal(answer)
            saved=confirm(c,p)
            assert saved.status_code==200,saved.text
            assert saved.json()["receipt"]["proposal_version"]==p["version"]
            assert c.get("/api/bootstrap").json()["revision"]==saved.json()["receipt"]["revision"]
            assert c.get("/api/conversations/"+answer["conversation_id"]).json()["answers"][-1]["action_receipts"]
            return p,saved.json()["receipt"]
        hh=c.runtime.read(c.principal).household
        source=next(iter(hh.income_sources.values()))
        p,receipt=reviewed("create_income",{"record_type":"event","source_id":source.id,
            "expected_date":"2027-12-29","expected_amount":"123.45"})
        event_id=receipt["results"][0]["id"]
        reviewed("update_income",{"record_type":"event","record_id":event_id,
            "received_date":"2027-12-29","received_amount":"124.50"})
        hh=c.runtime.read(c.principal).household
        received=next(e for e in hh.income_events if e.id==event_id)
        assert received.received_amount.amount==Decimal("124.50")
        p,_=reviewed("update_income",{"record_id":source.id,"record_type":"source","net_amount":"3210.45"})
        displayed=p["preview"]["entries"][0]["after"]["paycheck_events"]
        actual=c.runtime.read(c.principal).household.income_events
        expected_by_date={e["expected_date"]:e["expected_amount"] for e in displayed}
        assert expected_by_date=={e.expected_date.isoformat():e.expected_amount.to_json() for e in actual if e.source_id==source.id}
        assert next(e for e in actual if e.id==event_id).received_amount.amount==Decimal("124.50")
        rule=next(iter(hh.policies.values()))
        reviewed("authorize_rule",{"record_id":rule.id,"per_run_cap":"2000"})
        p,_=reviewed("update_rule",{"record_id":rule.id,"amount":"500"})
        assert any("clear" in effect for effect in p["preview"]["entries"][0]["effects"])
        assert not c.runtime.read(c.principal).household.policies[rule.id].mandate.active


def test_card_details_and_nullable_fields_apply_shared_validation():
    from decimal import Decimal
    with workflow_client() as c:
        card=next(iter(c.runtime.read(c.principal).household.cards.values()))
        ops=[{"capability":"update_account","arguments":{"record_id":card.account_id,"apr":"0.2199",
            "annual_fee":"95","foreign_transaction_fee":"0.025","payment_due_day":19,"remaining_term_months":None}}]
        p=proposal(c.post("/api/ask",json={"question":"Update card details","workflow_input":ops}).json())
        assert p["preview"]["entries"][0]["after"]["card_details"][0]["purchase_apr"]=="0.2199"
        assert confirm(c,p).status_code==200
        ctx=c.runtime.read(c.principal)
        updated=ctx.household.cards[card.id]
        assert updated.purchase_apr==Decimal("0.2199") and updated.annual_fee.amount==Decimal("95")
        assert updated.foreign_transaction_fee==Decimal("0.025") and updated.payment_due_day==19
        assert next(v for v in ctx.household.liabilities.values() if v.account_id==card.account_id).remaining_term_months is None


def test_discovery_explains_role_availability():
    from finpilot.persistence.database import MembershipRow
    with workflow_client() as c:
        with c.runtime.db.sessions.begin() as s:
            s.get(MembershipRow,(c.principal.user_id,c.principal.household_id)).role="viewer"
        caps={x["name"]:x for x in c.get("/api/assistant/capabilities").json()["capabilities"]}
        assert not caps["update_bill"]["available"] and "role" in caps["update_bill"]["unavailable_reason"]
        assert caps["get_money_overview"]["available"]
        assert caps["update_bill"]["result_format"]=="proposal"


def test_model_cannot_turn_requested_bill_amount_into_a_rename():
    with workflow_client() as c:
        c.runtime.llm=ScriptedModel([json.dumps({"operations":[{"capability":"update_bill",
            "arguments":{"record_id":"bill_mortgage","name":"1999.99","amount_confirmed":True}}],"clarification":""})])
        before=c.runtime.read(c.principal).snapshot()
        answer=c.post("/api/ask",json={"question":"Update Mortgage payment bill amount to 1999.99","workflow_mode":"model"}).json()
        assert not answer["workflow"]["planner"]["accepted"]
        assert not any(p["type"]=="proposal" for p in answer["parts"])
        assert c.runtime.read(c.principal).snapshot()==before


def test_cannot_delete_conversation_while_provider_identity_is_running():
    with workflow_client() as c:
        p=proposal(c.post("/api/ask",json={"question":"Sync bank","conversation_id":c.action_conversation,
            "workflow_input":[{"capability":"sync_bank","arguments":{"record_id":"bank_fixture"}}]}).json())
        with c.runtime.db.sessions.begin() as s:
            row=s.get(ActionProposalRow,p["id"])
            row.status="running";row.expires_at=utcnow()+timedelta(minutes=3)
        assert c.delete("/api/conversations/"+c.action_conversation).status_code==409
        assert c.get("/api/assistant/proposals/"+p["id"]).json()["status"]=="running"


def test_compound_shortlist_keeps_reads_alongside_requested_writes():
    from finpilot.ai.workflows import shortlist
    from finpilot.ai.capabilities import catalog
    with workflow_client() as c:
        caps=catalog(c.runtime.read(c.principal))
        selected={v.name for v in shortlist(caps,"Find transactions for Music and update bill Utilities amount to 210")}
        assert {"search_transactions","update_bill"}<=selected
        selected={v.name for v in shortlist(caps,"Search documents for annual fee and show my tax assumptions")}
        assert {"search_documents","get_tax_profile"}<=selected
