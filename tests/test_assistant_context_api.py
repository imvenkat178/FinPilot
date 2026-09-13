"""The assistant's viewing context selects tenant-owned calculation inputs."""
import pytest
from tests.api_support import authenticated_client, read_workspace
from finpilot.ai.router import route


@pytest.mark.parametrize('question,tool', [
    ('Explain my cash forecast', 'get_cash_forecast'),
    ('How much can I spend this week?', 'get_spending_allowance'),
])
def test_viewed_savings_account_drives_calculations(question, tool):
    with authenticated_client() as client:
        ctx = read_workspace(client)
        arguments = dict(route(question).arguments)
        expected = ctx.registry.call(tool, {**arguments, 'account_id': 'acc_savings'})
        checking = ctx.registry.call(tool, {**arguments, 'account_id': 'acc_checking'})
        result = client.post('/api/ask', json={
            'question': question,
            'context': {'account_id': 'acc_savings', 'page': 'accounts'},
            'untrusted_context': '{"account_name":"Different invented name"}',
        })
        assert result.status_code == 200, result.text
        answer = result.json()
        assert answer['evidence'][0] == expected
        assert answer['evidence'][0] != checking
        assert answer['viewing']['name'] == ctx.household.accounts['acc_savings'].nickname
        assert answer['model_status']['reachable'] is False
        history = client.get('/api/ask/history').json()['answers']
        assert history[0]['evidence'][0] == expected
        assert history[0]['viewing']['name'] == answer['viewing']['name']


def test_foreign_account_context_is_rejected_before_any_answer():
    with authenticated_client() as first, authenticated_client(sample=False) as second:
        created = second.post('/api/manage/accounts', json={
            'nickname': 'Other user savings', 'type': 'savings', 'current': '1000',
            'available': '1000', 'institution': 'Other bank',
        })
        assert created.status_code == 201, created.text
        result = first.post('/api/ask', json={
            'question': 'Explain my cash forecast',
            'context': {'account_id': created.json()['id'], 'page': 'accounts'},
        })
        assert result.status_code == 404
        assert first.get('/api/ask/history').json()['answers'] == []


def test_page_without_an_account_keeps_household_default():
    with authenticated_client() as client:
        result = client.post('/api/ask', json={
            'question': 'Explain my cash forecast',
            'context': {'page': 'cashflow'},
        })
        assert result.status_code == 200, result.text
        assert result.json()['evidence'][0]['account_id'] == 'acc_checking'


def test_viewed_second_card_uses_its_card_record_for_utilization():
    with authenticated_client() as client:
        ctx = read_workspace(client)
        card = ctx.household.cards['card_b']
        assert card.id != card.account_id
        expected = ctx.registry.call('check_utilization_timing', {'card_id': card.id})
        first_card = ctx.registry.call('check_utilization_timing')
        assert expected != first_card
        result = client.post('/api/ask', json={
            'question': 'Explain my credit utilization',
            'context': {'account_id': card.account_id, 'page': 'accounts'},
        })
        assert result.status_code == 200, result.text
        answer = result.json()
        assert answer['evidence'][0] == expected
        assert card.nickname in answer['evidence'][0]['card']
        assert answer['evidence'][0]['statement_close_day'] == card.statement_close_day
        assert answer['evidence'][0]['credit_limit'] == card.credit_limit.to_json()
        assert client.get('/api/ask/history').json()['answers'][0]['evidence'][0] == expected


def test_conditional_tax_answer_never_claims_exact_confidence():
    with authenticated_client() as client:
        response = client.post('/api/ask', json={'question': 'Is my mortgage interest deductible?'})
        assert response.status_code == 200
        answer = response.json()
        assert answer['evidence'][0]['requires_verification'] is True
        assert answer['confidence'] == 'bounded'
        assert client.get('/api/ask/history').json()['answers'][0]['confidence'] == 'bounded'
