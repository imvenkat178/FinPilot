"""Reviewed actions must change exactly the displayed workspace, once."""
from datetime import timedelta
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import select
from tests.api_support import authenticated_client
from finpilot.persistence.database import AuditRow,MembershipRow,utcnow
from finpilot.persistence.action_models import ActionProposalRow
from finpilot.services.proposals import ProposalService

def ask(c,q,operations=None):
    payload={"question":q}
    if operations is not None:
        payload["workflow_input"]=operations
    response=c.post("/api/ask",json=payload)
    assert response.status_code==200,response.text
    return response.json()

def proposal(answer):
    return next(p["proposal"] for p in answer["parts"] if p["type"]=="proposal")

def confirm(c,p):
    return c.post("/api/assistant/proposals/"+p["id"]+"/confirm",json={"version":p["version"]})

def test_natural_language_review_commit_receipt_replay():
    with authenticated_client() as c:
        before=c.runtime.read(c.principal)
        answer=ask(c,"Update bill Mortgage payment amount to 1999.99")
        p=proposal(answer)
        assert c.runtime.read(c.principal).household.bills["bill_mortgage"].amount==before.household.bills["bill_mortgage"].amount
        changed=confirm(c,p)
        assert changed.status_code==200,changed.text
        assert c.runtime.read(c.principal).household.bills["bill_mortgage"].amount.amount==Decimal("1999.99")
        revision=changed.json()["receipt"]["revision"]
        assert confirm(c,p).json()["receipt"]==changed.json()["receipt"]
        assert c.runtime.read(c.principal).revision==revision
        saved=c.get("/api/conversations/"+answer["conversation_id"]).json()
        assert saved["answers"][0]["action_receipts"][0]["proposal_id"]==p["id"]
        assert c.get("/api/bootstrap").json()["revision"]==revision

def test_stale_preview_edit_cancel_and_no_text_confirmation():
    with authenticated_client() as c:
        a=ask(c,"Update bill Mortgage payment amount to 1999.99")
        p=proposal(a)
        with c.runtime.transaction(c.principal,"test.unrelated") as ctx:
            ctx.household.name="Renamed"
        assert confirm(c,p).status_code==409
        edited=c.patch("/api/assistant/proposals/"+p["id"],json={"version":1}).json()
        assert edited["version"]==2
        assert confirm(c,p).status_code==409
        assert c.post("/api/assistant/proposals/"+p["id"]+"/cancel",json={"version":2}).status_code==200
        assert confirm(c,edited).status_code==409
        said=c.post("/api/ask",json={"question":"confirm","conversation_id":a["conversation_id"]}).json()
        assert "Confirm" in said["answer"]
        assert c.runtime.read(c.principal).household.bills["bill_mortgage"].amount.amount!=Decimal("1999.99")

def test_batch_validation_does_not_partially_save_and_expiry():
    with authenticated_client() as c:
        a=ask(c,"Update the bills",[
            {"capability":"update_bill","arguments":{"record_id":"bill_mortgage","amount":"1900"}},
            {"capability":"update_bill","arguments":{"record_id":"missing","amount":"20"}}])
        assert not any(x["type"]=="proposal" for x in a["parts"])
        assert c.runtime.read(c.principal).revision==1
        p=proposal(ask(c,"Update bill Mortgage payment amount to 1999.99"))
        with c.runtime.db.sessions.begin() as s:
            s.get(ActionProposalRow,p["id"]).expires_at=utcnow()-timedelta(minutes=1)
        assert confirm(c,p).status_code==409

def test_role_change_and_foreign_proposal_denied():
    with authenticated_client() as c:
        p=proposal(ask(c,"Update bill Mortgage payment amount to 1999.99"))
        with c.runtime.db.sessions.begin() as s:
            s.get(MembershipRow,(c.principal.user_id,c.principal.household_id)).role="viewer"
        assert confirm(c,p).status_code==403
        c.post("/api/auth/register",json={"name":"Other","email":"other-action@example.test","password":"another-safe-password","sample_data":True})
        assert c.get("/api/assistant/proposals/"+p["id"]).status_code==404

def test_untrusted_model_cannot_execute_or_override_confirmation():
    with authenticated_client() as c:
        result=c.post("/api/ask",json={"question":"Make an edit","workflow_input":[{"capability":"confirm_proposal","arguments":{"id":"x"}}]})
        assert result.status_code==200
        assert c.runtime.read(c.principal).revision==1
        p=proposal(ask(c,"Update bill Mortgage payment amount to 1999.99"))
        assert c.post("/api/assistant/proposals/"+p["id"]+"/confirm",
                      json={"version":1,"arguments":{"amount":"999"}}).status_code==422

def test_private_deletion_is_reviewed_without_financial_revision():
    with authenticated_client() as c:
        doc=c.post("/api/documents",files={"file":("test.txt",b"Fictional source","text/plain")}).json()["document"]
        before=c.runtime.read(c.principal).revision
        p=proposal(ask(c,"Delete the document",[{"capability":"delete_document","arguments":{"record_id":doc["id"]}}]))
        assert c.get("/api/documents/"+doc["id"]).status_code==200
        result=confirm(c,p)
        assert result.status_code==200,result.text
        assert c.get("/api/documents/"+doc["id"]).status_code==404
        assert c.runtime.read(c.principal).revision==before

def test_all_capability_inputs_have_bounded_classified_contracts():
    with authenticated_client() as c:
        data=c.get("/api/assistant/capabilities").json()
        assert len(data["capabilities"])>=79
        for cap in data["capabilities"]:
            assert cap["mode"] in {"read","write","private","provider","handoff","navigate"}
            assert cap["input_schema"]["additionalProperties"] is False
            assert cap["review_required"]==(cap["mode"] in {"write","private","provider"})