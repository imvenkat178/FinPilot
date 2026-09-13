"""Review proposals. Financial state and its receipt commit in the same UoW."""
from __future__ import annotations
from datetime import timedelta, timezone
from uuid import uuid4
from sqlalchemy import select, update, delete
from ..persistence.action_models import ActionProposalRow, ActionReceiptRow, ActionAttachmentRow
from ..persistence.conversation_models import ConversationRow, GenerationRow
from ..persistence.database import MembershipRow, ChatRow, utcnow
from ..runtime import RevisionConflict
from ..services.auth import AuthError
from ..ai.capabilities import catalog, canonicalize, records, label
from ..api.workspace import _encode
from .commands import execute
from .conversations import ConversationService, ConversationBusy
from .documents import DocumentService

def stamp(value):
    return value.replace(tzinfo=value.tzinfo or timezone.utc).isoformat()

def record_view(ctx,entity,record_id):
    item=next((v for v in records(ctx,entity) if v.id==record_id),None)
    if item is None:
        return None
    value=item.to_json() if entity in {"group","leg"} else _encode(item)
    if entity=="account":
        value["loan_details"]=[_encode(v) for v in records(ctx,"liability") if v.account_id==record_id]
        value["card_details"]=[_encode(v) for v in records(ctx,"card") if v.account_id==record_id]
    if entity=="income":
        value["paycheck_events"]=[_encode(v) for v in records(ctx,"income_event") if v.source_id==record_id]
    return value

def public(row, receipt=None):
    return {"id":row.id,"conversation_id":row.conversation_id,"version":row.version,"revision":row.revision,
        "status":row.status,"operations":row.operations,"preview":row.preview,
        "expires_at":stamp(row.expires_at),"receipt":receipt.result if receipt else None}

class Replay(Exception):
    def __init__(self,result):
        self.result=result

class ProposalService:
    def __init__(self,runtime,approved_servers=None):
        self.r=runtime
        self.approved_servers=approved_servers

    def owned(self,s,p,proposal_id,lock=False):
        query=select(ActionProposalRow).join(MembershipRow,
            (MembershipRow.household_id==ActionProposalRow.household_id)&
            (MembershipRow.user_id==ActionProposalRow.user_id)).where(
                ActionProposalRow.id==proposal_id,ActionProposalRow.user_id==p.user_id,
                ActionProposalRow.household_id==p.household_id)
        row=s.scalar(query.with_for_update(of=ActionProposalRow) if lock else query)
        if not row:
            raise KeyError("Proposal not found")
        return row

    def choices(self,p):
        from ..persistence.mcp_models import MCPConnectionRow,MCPTokenRow
        from ..persistence.database import BankConnectionRow
        data={"document":DocumentService(self.r.db).list(p),"conversation":ConversationService(self.r.db).list(p)}
        with self.r.db.sessions() as s:
            for kind,model in (("mcp",MCPConnectionRow),("grant",MCPTokenRow)):
                data[kind]=[{"id":v.id,"name":v.name,**({"server_id":v.server_id} if kind=="mcp" else {})} for v in s.scalars(select(model).where(
                    model.household_id==p.household_id,model.user_id==p.user_id))]
            data["bank"]=[{"id":v.id,"name":v.institution_name,"status":v.status,"version":v.version}
                for v in s.scalars(select(BankConnectionRow).where(BankConnectionRow.household_id==p.household_id))]
        if data["mcp"]:
            from ..integrations.mcp_client import MCPError
            from fastapi import HTTPException
            try:
                configs=self.mcp_servers()
            except (MCPError,HTTPException):
                # Unavailable connection configuration must not disable local
                # reads, financial previews, or disconnecting a stale source.
                configs={}

            for row in data["mcp"]:
                config=configs.get(row["server_id"])
                row["read_tools"]=list(config.read_tools) if config else []
        return data

    def mcp_servers(self):
        from ..integrations.mcp_client import configured_servers
        provider=configured_servers if self.approved_servers is None else self.approved_servers
        return provider() if callable(provider) else provider

    def validate_mcp_import(self,p,args):
        from ..persistence.mcp_models import MCPConnectionRow
        from ..api.mcp_routes import ImportBody
        ImportBody.model_validate({k:v for k,v in args.items() if k!="record_id"})
        with self.r.db.sessions() as session:
            connection=session.scalar(select(MCPConnectionRow).where(MCPConnectionRow.id==args["record_id"],
                MCPConnectionRow.household_id==p.household_id,MCPConnectionRow.user_id==p.user_id))
            if not connection:
                raise ValueError("MCP connection not found.")
            server_id=connection.server_id
        config=self.mcp_servers().get(server_id)
        if not config:
            raise ValueError("This MCP server is no longer approved. Choose an approved connection.")
        if args.get("tool_name") and args["tool_name"] not in config.read_tools:
            raise ValueError("Choose an approved MCP read tool before reviewing the import.")
        if args.get("resource_uri") and not config.allow_resources:
            raise ValueError("This MCP server does not allow resource imports.")

    def attachments(self,s,p,ctx,operations,conversation_id):
        ctx.csv_attachments={}
        ctx.csv_attachment_labels={}
        for op in operations:
            if op["capability"]=="import_transactions":
                row=s.scalar(select(ActionAttachmentRow).where(
                    ActionAttachmentRow.id==op["arguments"]["attachment_id"],
                    ActionAttachmentRow.household_id==p.household_id,ActionAttachmentRow.user_id==p.user_id,
                    ActionAttachmentRow.conversation_id==conversation_id))
                if not row:
                    raise KeyError("CSV attachment not found in this conversation")
                ctx.csv_attachments[row.id]=row.payload
                ctx.csv_attachment_labels[row.id]=row.filename

    def preview(self,p,operations,conversation_id):
        if not isinstance(operations,list) or not 1<=len(operations)<=4:
            raise ValueError("Review between one and four operations.")
        ctx=self.r.read(p)
        ctx.actor_id=p.user_id
        caps=catalog(ctx)
        external=self.choices(p)
        validated=[]
        for op in operations:
            if not isinstance(op,dict) or set(op)!={"capability","arguments"}:
                raise ValueError("Use a named capability with its arguments.")
            cap=caps.get(op["capability"])
            if not cap or cap.mode not in {"write","private","provider"}:
                raise ValueError("This capability is not a reviewable action.")
            args=canonicalize(cap,op["arguments"],ctx,external)
            if cap.name=="delete_conversation" and args["record_id"]==conversation_id:
                raise ValueError("Open a different conversation before deleting this one.")
            validated.append({"capability":cap.name,"arguments":args})
        modes={caps[op["capability"]].mode for op in validated}
        if modes!={"write"} and len(validated)>1:
            raise ValueError("Review connection or private-library changes separately.")
        if "write" in modes or any(x["capability"] in {"sync_bank","disconnect_bank"} for x in validated):
            with self.r.db.sessions() as s:
                membership=s.get(MembershipRow,(p.user_id,p.household_id))
                if not membership or membership.role not in {"owner","approver"}:
                    raise AuthError("This workspace role cannot make financial changes.",403)
        with self.r.db.sessions() as s:
            self.attachments(s,p,ctx,validated,conversation_id)
        before_snapshot=ctx.snapshot()
        entries=[]
        for op in validated:
            cap=caps[op["capability"]]
            args=op["arguments"]
            old=None
            if cap.mode=="write":
                entity="income_event" if cap.entity=="income" and args.get("record_type")=="event" else cap.entity
                if args.get("record_id") and entity:
                    old=record_view(ctx,entity,args["record_id"])
                if cap.entity=="tax":
                    old=ctx.tax.to_json()
                if cap.name=="import_transactions":
                    from .workspace import WorkspaceService
                    csv_preview=WorkspaceService(ctx.household,ctx.tax).preview_transactions(ctx.csv_attachments[args["attachment_id"]])
                result=execute(ctx,cap.name,args)
                if cap.name=="import_transactions":
                    result={**result,"reviewed_rows":csv_preview["rows"],"total_rows":csv_preview["total"],"preview_limit":100}
                new=None
                if entity and entity!="tax":
                    target=result.get("id") or args.get("record_id")
                    new=record_view(ctx,entity,target)
                if cap.entity=="tax":
                    new=ctx.tax.to_json()
                effects=[]
                if cap.entity=="income" and args.get("record_type","source")=="source":
                    effects.append("Future expected paychecks may be regenerated for the next 366 days; received history stays intact.")
                if cap.entity=="rule" and cap.name.startswith(("create_","update_")) and set(args)-{"record_id","name","paused"}:
                    effects.append("Material rule edits clear its previous simulation authorization.")
                if cap.entity=="goal":
                    effects.append("This assigns planning funds; no money is transferred.")
                if cap.entity=="tax":
                    effects.append("These remain user-entered, unverified tax assumptions.")
                if cap.name in {"draft_bill_payment","build_payment_drafts","authorize_rule","simulate_payment","recover_payment"}:
                    effects.append("Simulation/planning only. No real payment is submitted.")
                if cap.name=="pause_execution":
                    effects.append("Unsubmitted drafts will be canceled. Resuming does not recreate them.")
                entries.append({"capability":cap.name,"title":cap.description,"arguments":args,
                                "before":old,"after":new,"result":result,"effects":effects})
            else:
                target=next((v for v in external.get(cap.entity,[]) if v["id"]==args.get("record_id")),None)
                if cap.name=="import_mcp":
                    self.validate_mcp_import(p,args)
                entries.append({"capability":cap.name,"title":cap.description,"arguments":args,"target":target,
                    "effects":["This action changes your saved source or access. Existing financial records are retained."]})
        names=dict(getattr(ctx,"csv_attachment_labels",{}))
        for kind in ("account","bill","income","income_event","goal","rule","transaction","card","liability","group","leg"):
            names.update({v.id:label(v) for v in records(ctx,kind)})
        for choices in external.values():
            names.update({v["id"]:label(v) for v in choices})
        for entry in entries:
            entry["reference_labels"]={k:names[v] for k,v in entry["arguments"].items() if isinstance(v,str) and v in names}
        return validated,{"entries":entries,"mode":next(iter(modes)),"review_required":True,"as_of":ctx.household.as_of.isoformat(),"no_financial_changes":before_snapshot==ctx.snapshot()},ctx.revision

    def create(self,p,conversation_id,operations,generation_id=None):
        ConversationService(self.r.db).get(p,conversation_id)
        validated,preview,revision=self.preview(p,operations,conversation_id)
        with self.r.db.sessions.begin() as s:
            ConversationService(self.r.db)._owned(s,p,conversation_id)
            row=ActionProposalRow(id="act_"+uuid4().hex,household_id=p.household_id,user_id=p.user_id,
                conversation_id=conversation_id,generation_id=generation_id,version=1,revision=revision,
                status="proposed",operations=validated,preview=preview,expires_at=utcnow()+timedelta(minutes=15))
            s.add(row);s.flush()
            return public(row)

    def get(self,p,proposal_id):
        with self.r._lock(p.household_id),self.r.db.sessions.begin() as s:
            row=self.owned(s,p,proposal_id)
            if row.status=="running" and row.expires_at.replace(tzinfo=row.expires_at.tzinfo or timezone.utc)<=utcnow():
                s.execute(update(ActionProposalRow).where(ActionProposalRow.id==row.id,
                    ActionProposalRow.status=="running",ActionProposalRow.expires_at<=utcnow()).values(status="outcome_unknown").execution_options(synchronize_session=False))
                s.refresh(row)
            return public(row,s.get(ActionReceiptRow,row.id))

    def list(self,p,conversation_id):
        ConversationService(self.r.db).get(p,conversation_id)
        with self.r.db.sessions.begin() as s:
            s.execute(update(ActionProposalRow).where(ActionProposalRow.household_id==p.household_id,
                ActionProposalRow.user_id==p.user_id,ActionProposalRow.conversation_id==conversation_id,
                ActionProposalRow.status=="running",ActionProposalRow.expires_at<=utcnow()).values(status="outcome_unknown").execution_options(synchronize_session=False))
            rows=s.execute(select(ActionProposalRow,ActionReceiptRow).outerjoin(ActionReceiptRow,
                ActionReceiptRow.proposal_id==ActionProposalRow.id).where(ActionProposalRow.household_id==p.household_id,
                ActionProposalRow.user_id==p.user_id,ActionProposalRow.conversation_id==conversation_id)
                .order_by(ActionProposalRow.created_at.desc()).limit(100)).all()
            return [public(row,receipt) for row,receipt in reversed(rows)]

    def check(self,s,p,row,version):
        receipt=s.get(ActionReceiptRow,row.id)
        if receipt:
            if receipt.proposal_version!=version:
                raise RevisionConflict("This is not the confirmed proposal version.")
            raise Replay(public(row,receipt))
        if row.version!=version:
            raise RevisionConflict("The proposal changed. Review its latest version.")
        if row.status!="proposed":
            raise RevisionConflict("This proposal is "+row.status+". Review or prepare a new action.")
        if row.expires_at.replace(tzinfo=row.expires_at.tzinfo or timezone.utc)<=utcnow():
            raise RevisionConflict("This proposal expired. Refresh its preview.")
        claimed=s.execute(update(ActionProposalRow).where(ActionProposalRow.id==row.id,
            ActionProposalRow.version==version,ActionProposalRow.status=="proposed").values(status="executing"))
        if not claimed.rowcount:
            raise RevisionConflict("The action is already being processed.")
        conversation=ConversationService(self.r.db)._owned(s,p,row.conversation_id)
        if conversation.active_generation:
            raise ConversationBusy("Wait for the current answer before confirming a change.")

    def edit(self,p,proposal_id,version,operations=None):
        old=self.get(p,proposal_id)
        if old["status"]!="proposed" or old["version"]!=version:
            raise RevisionConflict("Only the current unconfirmed proposal can be edited.")
        ops,preview,revision=self.preview(p,operations or old["operations"],old["conversation_id"])
        with self.r._lock(p.household_id),self.r.db.sessions.begin() as s:
            row=self.owned(s,p,proposal_id,True)
            changed=s.execute(update(ActionProposalRow).where(ActionProposalRow.id==row.id,
                ActionProposalRow.version==version,ActionProposalRow.status=="proposed").values(
                    operations=ops,preview=preview,revision=revision,version=version+1,
                    expires_at=utcnow()+timedelta(minutes=15)))
            if not changed.rowcount:
                raise RevisionConflict("The proposal changed during editing.")
        return self.get(p,proposal_id)

    def cancel(self,p,proposal_id,version):
        with self.r._lock(p.household_id),self.r.db.sessions.begin() as s:
            row=self.owned(s,p,proposal_id,True)
            if row.version!=version or row.status not in {"proposed","canceled"}:
                raise RevisionConflict("This proposal can no longer be canceled.")
            changed=s.execute(update(ActionProposalRow).where(ActionProposalRow.id==row.id,
                ActionProposalRow.version==version,ActionProposalRow.status.in_(["proposed","canceled"])).values(status="canceled"))
            if not changed.rowcount:
                raise RevisionConflict("This proposal changed before cancellation.")
        return self.get(p,proposal_id)

    def receipt(self,s,row,result,revision,no_changes=False):
        row.status="succeeded"
        payload={"status":"succeeded","results":result,"revision":revision,"executed_at":utcnow().isoformat(),
                 "proposal_id":row.id,"proposal_version":row.version,"no_financial_changes":no_changes}
        receipt=ActionReceiptRow(proposal_id=row.id,proposal_version=row.version,result=payload)
        s.add(receipt)
        # Attach an authoritative receipt to the originating saved response.
        if row.generation_id:
            generation=s.get(GenerationRow,row.generation_id)
            if generation and generation.status=="completed":
                saved={**generation.result}
                saved["action_receipts"]=[*(saved.get("action_receipts") or []),payload]
                generation.result=saved
                legacy=s.get(ChatRow,row.generation_id)
                if legacy:
                    legacy.result=saved
        return receipt

    def confirm(self,p,proposal_id,version):
        initial=self.get(p,proposal_id)
        if initial["receipt"]:
            if initial["version"]!=version:
                raise RevisionConflict("Review the confirmed proposal version.")
            return initial
        if initial["preview"]["mode"]=="provider":
            raise ValueError("Provider execution requires the provider executor.")
        try:
            if initial["preview"]["mode"]=="write":
                with self.r.transaction(p,"assistant.confirm",initial["revision"]) as ctx:
                    row=self.owned(ctx.db_session,p,proposal_id,True)
                    self.check(ctx.db_session,p,row,version)
                    if row.revision!=ctx.revision:
                        raise RevisionConflict("Your workspace changed. Refresh the preview before confirming.")
                    if row.preview.get("as_of")!=ctx.household.as_of.isoformat():
                        raise RevisionConflict("The planning date changed. Refresh the preview.")
                    ctx.actor_id=p.user_id
                    before=ctx.snapshot()
                    self.attachments(ctx.db_session,p,ctx,row.operations,row.conversation_id)
                    results=[execute(ctx,op["capability"],op["arguments"]) for op in row.operations]
                    receipt=self.receipt(ctx.db_session,row,results,ctx.revision+1,no_changes=before==ctx.snapshot())
                    result=public(row,receipt)
                return result
            with self.r._lock(p.household_id),self.r.db.sessions.begin() as s:
                row=self.owned(s,p,proposal_id,True)
                self.check(s,p,row,version)
                op=row.operations[0]
                value=self.private_command(s,p,op)
                receipt=self.receipt(s,row,[value],initial["revision"])
                return public(row,receipt)
        except Replay as replay:
            return replay.result
        except RevisionConflict:
            # A concurrent successful confirm may have committed while this one
            # waited for the household lock. Return that exact receipt, not a retry.
            latest=self.get(p,proposal_id)
            if latest["receipt"] and latest["version"]==version:
                return latest
            raise

    def private_command(self,s,p,op):
        from .private_commands import delete_private
        return delete_private(s,p,op["capability"],op["arguments"]["record_id"])
