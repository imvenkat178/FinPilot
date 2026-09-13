"""Regression checks for resuming conversations after context changes."""
import pytest
from finpilot.persistence.conversation_models import GenerationRow
from finpilot.services.conversations import ConversationService
from tests.api_support import authenticated_client


def ask(client, question, **values):
    response = client.post('/api/ask', json={'question': question, **values})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize('first_account,next_account', [
    ('acc_checking', 'acc_savings'), ('acc_savings', None),
])
def test_followup_rebinds_current_account_and_household_context(first_account, next_account):
    with authenticated_client() as client:
        first = ask(client, 'How much can I spend this week?', context={
            'page': 'accounts', 'account_id': first_account})
        second = ask(client, 'Explain that', conversation_id=first['conversation_id'])
        third = ask(client, 'Explain that', conversation_id=first['conversation_id'], context={
            'page': 'accounts' if next_account else 'overview', 'account_id': next_account})
        registry = client.runtime.read(client.principal).registry
        arguments = {'days': 7, **({'account_id': next_account} if next_account else {})}
        assert third['evidence'][0] == registry.call('get_spending_allowance', arguments)
        assert third['evidence'][0] != second['evidence'][0]
        assert 'account_id' not in second['resolved_route']['arguments']
        assert 'account_id' not in third['resolved_route']['arguments']


def test_legacy_followup_route_rebinds_card_to_current_view():
    with authenticated_client() as client:
        registry = client.runtime.read(client.principal).registry
        first_card, next_card = registry.hh.cards['card_a'], registry.hh.cards['card_b']
        first = ask(client, 'Explain my credit utilization', context={
            'page': 'accounts', 'account_id': first_card.account_id})
        # Persisted conversations from the earlier implementation included the
        # card selected by the page in their saved language route.
        with client.runtime.db.sessions.begin() as session:
            generation = session.get(GenerationRow, first['answer_id'])
            generation.result = {**generation.result, 'resolved_route': {
                **first['resolved_route'], 'arguments': {'card_id': first_card.id}}}
        answer = ask(client, 'Explain that', conversation_id=first['conversation_id'], context={
            'page': 'accounts', 'account_id': next_card.account_id})
        assert answer['evidence'][0] == registry.call('check_utilization_timing', {'card_id': next_card.id})
        assert answer['evidence'][0] != first['evidence'][0]
        assert 'card_id' not in answer['resolved_route']['arguments']


def test_stale_conversation_context_is_rejected_before_generation(monkeypatch):
    with authenticated_client() as client:
        first = ask(client, 'How much can I spend this week?', context={
            'page': 'accounts', 'account_id': 'acc_checking'})
        cid = first['conversation_id']
        original_get = ConversationService.get
        latest_context = {'viewing': {'page': 'accounts', 'account_id': 'acc_savings'}, 'document_ids': []}

        def interleaved_get(service, principal, conversation_id):
            snapshot = original_get(service, principal, conversation_id)
            # A second request completes after the first reads inherited context
            # but before it claims the conversation. No network timing required.
            generation, _ = service.begin(principal, conversation_id, 'Interleaved message', latest_context,
                                           expected_version=snapshot['version'])
            service.finish(principal, conversation_id, generation, {'question': 'Interleaved message',
                'answer': 'Saved newer context.'}, first['revision'])
            return snapshot

        monkeypatch.setattr(ConversationService, 'get', interleaved_get)
        response = client.post('/api/ask', json={'question': 'Explain that', 'conversation_id': cid})
        assert response.status_code == 409, response.text
        saved = ConversationService(client.runtime.db).detail(client.principal, cid)
        assert saved['conversation']['last_context'] == latest_context['viewing']
        assert saved['conversation']['busy'] is False
        assert len(saved['generations']) == 2
        assert all(g['status'] == 'completed' for g in saved['generations'])
