from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from ..services.conversations import ConversationService
from .dependencies import P, R

router = APIRouter(prefix='/api/conversations', tags=['Conversations'])

class ConversationIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field('New conversation', max_length=120)

@router.get('')
def list_conversations(p:P, r:R):
    return {'conversations': ConversationService(r.db).list(p)}

@router.post('', status_code=201)
def create_conversation(body:ConversationIn, p:P, r:R):
    return {'conversation': ConversationService(r.db).create(p, body.title)}

@router.get('/{conversation_id}')
def conversation(conversation_id:str, p:P, r:R):
    from ..services.proposals import ProposalService
    data = ConversationService(r.db).detail(p, conversation_id)
    data["proposals"] = ProposalService(r).list(p, conversation_id)
    return data

@router.delete('/{conversation_id}')
def delete_conversation(conversation_id:str, p:P, r:R):
    return {'deleted': ConversationService(r.db).delete(p, conversation_id)}
