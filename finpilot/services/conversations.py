"""Small transactions surround inference; a lease serializes each conversation."""
from datetime import timedelta, timezone
from uuid import uuid4
from sqlalchemy import select, update, delete, or_
from ..persistence.database import ChatRow, MembershipRow, utcnow
from ..persistence.conversation_models import ConversationRow, GenerationRow
from .auth import AuthError
from ..persistence.action_models import WorkflowStateRow


class ConversationBusy(ValueError):
    pass


def stamp(value):
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None


def metadata(row):
    return {'id': row.id, 'title': row.title, 'updated_at': stamp(row.updated_at),
            'created_at': stamp(row.created_at), 'version': row.version,
            'document_ids': row.context.get('document_ids', []),
            'last_context': row.context.get('viewing'),
            'busy': bool(row.busy_until and row.busy_until.replace(tzinfo=timezone.utc) > utcnow())}


class ConversationService:
    def __init__(self, db):
        self.db = db

    def _owned(self, session, p, conversation_id):
        row = session.scalar(select(ConversationRow).join(MembershipRow,
            (MembershipRow.household_id == ConversationRow.household_id) &
            (MembershipRow.user_id == ConversationRow.user_id)).where(
                ConversationRow.id == conversation_id,
                ConversationRow.household_id == p.household_id,
                ConversationRow.user_id == p.user_id))
        if not row:
            raise KeyError('Conversation not found')
        return row

    def create(self, p, title='New conversation'):
        with self.db.sessions.begin() as s:
            if not s.get(MembershipRow, (p.user_id, p.household_id)):
                raise AuthError('Workspace access is unavailable.', 403)
            row = ConversationRow(id='conv_' + uuid4().hex, household_id=p.household_id,
                user_id=p.user_id, title=(title.strip() or 'New conversation')[:120], context={})
            s.add(row); s.flush()
            return metadata(row)

    def list(self, p):
        with self.db.sessions() as s:
            rows = s.scalars(select(ConversationRow).where(
                ConversationRow.household_id == p.household_id, ConversationRow.user_id == p.user_id
            ).order_by(ConversationRow.updated_at.desc()).limit(100))
            return [metadata(row) for row in rows]

    def get(self, p, conversation_id):
        with self.db.sessions() as s:
            return metadata(self._owned(s, p, conversation_id))

    def detail(self, p, conversation_id):
        with self.db.sessions() as s:
            row = self._owned(s, p, conversation_id)
            workflow = s.get(WorkflowStateRow, conversation_id)
            turns = list(s.scalars(select(GenerationRow).where(
                GenerationRow.conversation_id == row.id).order_by(GenerationRow.created_at.desc()).limit(100)))
            return {'conversation': metadata(row), 'pending_workflow': workflow.state if workflow else None, 'answers': [
                {**turn.result, 'id': turn.id, 'answer_id': turn.id, 'at': stamp(turn.created_at)}
                for turn in reversed(turns) if turn.status == 'completed'],
                'generations': [{'id': t.id, 'status': t.status, 'at': stamp(t.created_at)} for t in reversed(turns)]}

    def begin(self, p, conversation_id, question, context, *, expected_version=None):
        generation_id = 'ans_' + uuid4().hex
        with self.db.sessions.begin() as s:
            row = self._owned(s, p, conversation_id)
            claimed = s.execute(update(ConversationRow).execution_options(synchronize_session="fetch").where(ConversationRow.id == row.id,
                ConversationRow.version == (row.version if expected_version is None else expected_version),
                or_(ConversationRow.busy_until.is_(None), ConversationRow.busy_until < utcnow())
            ).values(active_generation=generation_id, busy_until=utcnow()+timedelta(seconds=180),
                     version=row.version+1, updated_at=utcnow(), context=context))
            if not claimed.rowcount:
                raise ConversationBusy('This conversation changed or is still answering. Reload it before sending the next message.')
            # A timed-out process may have left a pending record. Keep its identity, mark it interrupted.
            s.execute(update(GenerationRow).where(GenerationRow.conversation_id == row.id,
                GenerationRow.status == 'pending').values(status='interrupted', completed_at=utcnow()))
            turns = list(s.scalars(select(GenerationRow).where(GenerationRow.conversation_id == row.id,
                GenerationRow.status == 'completed').order_by(GenerationRow.created_at.desc()).limit(6)))
            memory = [{'question': t.question[:1500], 'answer': t.result.get('answer', '')[:1500],
                       'intent': t.result.get('intent'), 'route': t.result.get('resolved_route'),
                       'document_sources': t.result.get('document_sources', [])} for t in reversed(turns)]
            s.add(GenerationRow(id=generation_id, conversation_id=row.id, question=question, status='pending', result={}))
            if row.title == 'New conversation':
                row.title = question.strip()[:120]
            return generation_id, memory

    def finish(self, p, conversation_id, generation_id, result, revision):
        with self.db.sessions.begin() as s:
            self._owned(s, p, conversation_id)
            changed = s.execute(update(ConversationRow).execution_options(synchronize_session="fetch").where(ConversationRow.id == conversation_id,
                ConversationRow.active_generation == generation_id).values(
                    active_generation=None, busy_until=None, updated_at=utcnow()))
            if not changed.rowcount:
                raise ConversationBusy('A newer request replaced this answer. Reload the conversation.')
            s.execute(update(GenerationRow).where(GenerationRow.id == generation_id).values(
                status='completed', result=result, completed_at=utcnow()))
            workflow = result.get('workflow')
            if workflow is not None:
                current = s.get(WorkflowStateRow, conversation_id)
                if current:
                    current.state, current.generation_id, current.updated_at = workflow, generation_id, utcnow()
                else:
                    s.add(WorkflowStateRow(conversation_id=conversation_id, generation_id=generation_id, state=workflow))
            s.add(ChatRow(id=generation_id, household_id=p.household_id, user_id=p.user_id,
                          revision=revision, result=result))

    def fail(self, p, conversation_id, generation_id):
        with self.db.sessions.begin() as s:
            s.execute(update(ConversationRow).execution_options(synchronize_session="fetch").where(ConversationRow.id == conversation_id,
                ConversationRow.household_id == p.household_id, ConversationRow.user_id == p.user_id,
                ConversationRow.active_generation == generation_id).values(active_generation=None, busy_until=None))
            s.execute(update(GenerationRow).where(GenerationRow.id == generation_id,
                GenerationRow.conversation_id == conversation_id, GenerationRow.status == 'pending'
            ).values(status='failed', completed_at=utcnow()))

    def delete(self, p, conversation_id):
        from .private_commands import delete_private
        with self.db.sessions.begin() as s:
            delete_private(s,p,"delete_conversation",conversation_id)
        return True
