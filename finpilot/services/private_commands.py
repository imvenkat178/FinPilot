"""Private source deletion shared by forms and reviewed action execution."""
from sqlalchemy import select,delete
from ..persistence.database import MembershipRow,ChatRow
from ..persistence.conversation_models import GenerationRow
from ..persistence.document_models import DocumentRow
from ..persistence.mcp_models import MCPConnectionRow,MCPTokenRow

def delete_private(session,p,name,target):
    from .conversations import ConversationService,ConversationBusy
    if name=="delete_conversation":
        row=ConversationService(None)._owned(session,p,target)
        from ..persistence.action_models import ActionProposalRow
        from ..persistence.database import utcnow
        running=session.scalar(select(ActionProposalRow.id).where(
            ActionProposalRow.conversation_id==target,
            ActionProposalRow.status.in_(["running","executing"]),
            ActionProposalRow.expires_at>utcnow()).limit(1))
        if running:
            raise ConversationBusy("Wait for the pending connection operation before deleting this conversation.")
        ids=select(GenerationRow.id).where(GenerationRow.conversation_id==target)
        session.execute(delete(ChatRow).where(ChatRow.id.in_(ids),ChatRow.user_id==p.user_id,ChatRow.household_id==p.household_id))
    else:
        model={"delete_document":DocumentRow,"disconnect_mcp":MCPConnectionRow,"revoke_mcp_access":MCPTokenRow}[name]
        row=session.scalar(select(model).join(MembershipRow,
            (MembershipRow.household_id==model.household_id)&(MembershipRow.user_id==model.user_id)).where(
                model.id==target,model.user_id==p.user_id,model.household_id==p.household_id))
        if not row:
            raise KeyError("Source not found")
    session.delete(row)
    return {"capability":name,"id":target,"deleted":True}