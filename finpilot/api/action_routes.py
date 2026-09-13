"""Authenticated capability discovery and version-bound action review."""
from typing import Any
from fastapi import APIRouter, Request, UploadFile, File, Form, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from ..ai.capabilities import catalog,records,label
from ..services.proposals import ProposalService
from .dependencies import P,R

router=APIRouter(prefix="/api/assistant",tags=["Assistant actions"])

class OperationIn(BaseModel):
    model_config=ConfigDict(extra="forbid")
    capability:str=Field(min_length=1,max_length=100)
    arguments:dict[str,Any]=Field(default_factory=dict)

class ProposalIn(BaseModel):
    model_config=ConfigDict(extra="forbid")
    conversation_id:str=Field(min_length=1,max_length=40)
    operations:list[OperationIn]=Field(min_length=1,max_length=4)

class VersionIn(BaseModel):
    model_config=ConfigDict(extra="forbid")
    version:int=Field(ge=1)

class EditIn(VersionIn):
    operations:list[OperationIn]|None=Field(None,min_length=1,max_length=4)

@router.get("/capabilities")
def capabilities(p:P,r:R):
    ctx=r.read(p)
    entities=ProposalService(r).choices(p)
    for kind in ("account","bill","goal","rule","income","income_event","transaction","card","liability","group","leg"):
        entities[kind]=[{"id":v.id,"label":label(v)} for v in records(ctx,kind)[:200]]
    return {"capabilities":[v.public(ctx,p.role) for v in catalog(ctx).values()],"entities":entities,
            "limits":{"operations_per_turn":4,"proposal_expiry_minutes":15,"inference_seconds":r.llm.config.timeout}}

@router.get("/proposals")
def proposals(conversation_id:str,p:P,r:R):
    return {"proposals":ProposalService(r).list(p,conversation_id)}

@router.post("/proposals",status_code=201)
def create_proposal(body:ProposalIn,request:Request,p:P,r:R):
    from .mcp_routes import servers
    return ProposalService(r,lambda:servers(request)).create(p,body.conversation_id,[x.model_dump() for x in body.operations])

@router.get("/proposals/{proposal_id}")
def get_proposal(proposal_id:str,p:P,r:R):
    return ProposalService(r).get(p,proposal_id)

@router.patch("/proposals/{proposal_id}")
def edit_proposal(proposal_id:str,body:EditIn,request:Request,p:P,r:R):
    from .mcp_routes import servers
    return ProposalService(r,lambda:servers(request)).edit(p,proposal_id,body.version,
                                  [x.model_dump() for x in body.operations] if body.operations else None)

@router.post("/proposals/{proposal_id}/cancel")
def cancel_proposal(proposal_id:str,body:VersionIn,p:P,r:R):
    return ProposalService(r).cancel(p,proposal_id,body.version)

@router.post("/proposals/{proposal_id}/confirm")
async def confirm_proposal(proposal_id:str,body:VersionIn,request:Request,p:P,r:R):
    from .mcp_routes import servers
    service=ProposalService(r,lambda:servers(request))
    initial=await run_in_threadpool(service.get,p,proposal_id)
    if initial["preview"]["mode"]=="provider":
        from ..services.provider_actions import confirm_provider
        return await confirm_provider(service,p,proposal_id,body.version,request)
    return await run_in_threadpool(service.confirm,p,proposal_id,body.version)

@router.post("/csv-preview",status_code=201)
async def csv_preview(request:Request,p:P,r:R,conversation_id:str=Form(...),account_id:str=Form(...),
                      file:UploadFile=File(...)):
    from uuid import uuid4
    from ..persistence.action_models import ActionAttachmentRow
    from ..services.conversations import ConversationService
    from ..services.workspace import WorkspaceService,MAX_CSV_BYTES
    await run_in_threadpool(ConversationService(r.db).get,p,conversation_id)
    raw=await file.read(MAX_CSV_BYTES+1)
    if len(raw)>MAX_CSV_BYTES:
        raise HTTPException(413,"CSV file is too large.")
    try:
        csv_text=raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(422,"Upload a UTF-8 CSV file.")
    form=await request.form()
    mapping={k:str(form.get("map_"+k) or "") for k in ("date","description","amount","category","kind")}
    payload={"account_id":account_id,"csv":csv_text,"date_format":str(form.get("date_format") or "YYYY-MM-DD")}
    if any(mapping.values()):
        payload["mapping"]=mapping
    ctx=await run_in_threadpool(r.read,p)
    preview=await run_in_threadpool(WorkspaceService(ctx.household,ctx.tax).preview_transactions,payload)
    if preview["needs_mapping"] or preview["errors"]:
        return {"preview":preview,"proposal":None}
    attachment_id="csv_"+uuid4().hex
    def save():
        with r.db.sessions.begin() as s:
            ConversationService(r.db)._owned(s,p,conversation_id)
            s.add(ActionAttachmentRow(id=attachment_id,household_id=p.household_id,user_id=p.user_id,
                conversation_id=conversation_id,filename=(file.filename or "transactions.csv")[:160],payload=payload))
        return ProposalService(r).create(p,conversation_id,[{"capability":"import_transactions","arguments":{"attachment_id":attachment_id}}])
    proposal=await run_in_threadpool(save)
    return {"preview":preview,"proposal":proposal}
