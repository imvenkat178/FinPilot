"""Behavioral contracts for chat, scoped evidence, and durable action commits."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from finpilot.ai.llm import LLMConfig
from finpilot.ai.llm import LocalLLM
from finpilot.api.app import create_app
from finpilot.conversation.contracts import ConfirmIn, ReadQuery, Scope
from finpilot.conversation.queries import read_query
from finpilot.conversation.service import ConversationService
from finpilot.models import Transaction, TxKind, TxState
from finpilot.money import Money
from finpilot.persistence.database import ActionProposalRow, MembershipRow, utcnow
from tests.api_support import authenticated_client, edit_workspace, read_workspace


@pytest.fixture
def client():
    with authenticated_client() as c:
        yield c


def conversation(c, scope=None):
    r = c.post('/api/conversations', json={'scope': scope} if scope else {})
    assert r.status_code == 201, r.text
    return r.json()['id']


def ask(c, tid, question, **kw):
    r = c.post(f'/api/conversations/{tid}/messages', json={
        'question': question, 'client_message_id': uuid4().hex, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def preview(c, tid, operations, **kw):
    r = c.post(f'/api/conversations/{tid}/proposals', json={
        'question': 'Review these exact changes', 'client_message_id': uuid4().hex,
        'batch': {'operations': operations}, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def confirm(c, tid, proposal, **kw):
    return c.post(f'/api/conversations/{tid}/proposals/{proposal["id"]}/confirm',
                  json={'digest': proposal['digest'], 'confirm': True, **kw})


def test_durable_follow_up_reuses_scope_and_query_but_reads_fresh_data(client):
    tid = conversation(client, {'kind': 'account', 'account_id': 'acc_checking'})
    with edit_workspace(client) as ctx:
        ctx.household.transactions.append(Transaction(id='first', account_id='acc_checking',
            date=ctx.household.as_of, amount=Money.of('-12.35'), category='dining'))
    first = ask(client, tid, 'Show spending by category')
    with edit_workspace(client) as ctx:
        ctx.household.transactions.append(Transaction(id='second', account_id='acc_checking',
            date=ctx.household.as_of, amount=Money.of('-7.65'), category='dining'))
    second = ask(client, tid, 'Show it as a chart')
    assert first['evidence']['result']['gross_spending']['amount'] == '12.35'
    assert second['evidence']['result']['gross_spending']['amount'] == '20.00'
    assert second['scope']['account_ids'] == ['acc_checking']
    assert any(c['type'] == 'chart' for c in second['components'])
    saved = client.get(f'/api/conversations/{tid}').json()
    assert len(saved['turns']) == 2
    assert saved['turns'][0]['response']['evidence'] == first['evidence']
    assert saved['scope']['account_id'] == 'acc_checking'


def test_account_and_platform_scope_filter_actual_calculations(client):
    tid = conversation(client)
    scoped = ask(client, tid, 'Summarize Everyday Checking')
    assert scoped['scope']['account_ids'] == ['acc_checking']
    assert {a['id'] for a in scoped['evidence']['result']['accounts']} == {'acc_checking'}
    platform = read_workspace(client).household.accounts['acc_checking'].institution
    scoped = ask(client, tid, 'Summarize my accounts', scope={'kind': 'platform', 'platform': platform})
    assert all(a['institution'] == platform for a in scoped['evidence']['result']['accounts'])
    blocked = ask(client, tid, 'How should I split my next paycheck?')
    assert blocked['state'] == 'needs_input' and 'household context' in blocked['answer']
    widened = ask(client, tid, 'Show a paycheck plan for all my accounts')
    assert widened['scope']['kind'] == 'household'
    assert widened['evidence']['tool'] == 'get_paycheck_plan'


def test_visual_follow_up_preserves_dates_and_long_comparisons_match_exposure(client):
    tid = conversation(client)
    first = ask(client, tid, 'Show spending last month')
    second = ask(client, tid, 'Show it as a chart')
    assert second['evidence']['arguments'] == first['evidence']['arguments']
    ctx = read_workspace(client)
    end = ctx.household.as_of
    start = end - timedelta(days=60)
    result = read_query(ctx, Scope(), ReadQuery(tool='analyze_spending', arguments={
        'start': start.isoformat(), 'end': end.isoformat(), 'compare_previous': True}))['evidence']['result']
    previous = result['comparison']
    assert date.fromisoformat(previous['end']) == start - timedelta(days=1)
    assert date.fromisoformat(previous['end']) - date.fromisoformat(previous['start']) == end - start


def test_spending_excludes_transfers_pending_reversals_and_keeps_refunds_separate(client):
    with edit_workspace(client) as ctx:
        for i, (kind, state, value, account_id) in enumerate([
            (TxKind.PURCHASE, TxState.POSTED, '-25', 'acc_checking'),
            (TxKind.FEE, TxState.POSTED, '-5', 'acc_checking'),
            (TxKind.REFUND, TxState.POSTED, '10', 'acc_checking'),
            (TxKind.PURCHASE, TxState.PENDING, '-90', 'acc_checking'),
            (TxKind.INTERNAL_TRANSFER, TxState.POSTED, '-600', 'acc_checking'),
            (TxKind.CARD_REPAYMENT, TxState.POSTED, '-500', 'acc_checking'),
            (TxKind.PURCHASE, TxState.REVERSED, '-70', 'acc_checking'),
            (TxKind.PURCHASE, TxState.POSTED, '-100', 'acc_savings'),
        ]):
            ctx.household.transactions.append(Transaction(id=str(i), account_id=account_id,
                date=ctx.household.as_of, amount=Money.of(value), kind=kind, state=state))
    tid = conversation(client, {'kind': 'account', 'account_id': 'acc_checking'})
    result = ask(client, tid, 'Show spending by category')['evidence']['result']
    assert result['gross_spending']['amount'] == '30.00'
    assert result['refunds']['amount'] == '10.00'
    assert result['pending_count'] == 1
    assert result['purchase_and_fee_count'] == 2


def test_message_idempotency_and_out_of_order_messages(client):
    tid = conversation(client)
    msg = {'question':'Summarize my accounts','client_message_id':uuid4().hex,'conversation_revision':0}
    first = client.post(f'/api/conversations/{tid}/messages',json=msg)
    assert first.status_code == 200
    assert client.post(f'/api/conversations/{tid}/messages',json=msg).json() == first.json()
    changed = client.post(f'/api/conversations/{tid}/messages',json={**msg,'question':'Show spending'})
    assert changed.status_code == 409
    stale = client.post(f'/api/conversations/{tid}/messages',json={**msg,'client_message_id':uuid4().hex})
    assert stale.status_code == 409
    assert len(client.get(f'/api/conversations/{tid}').json()['turns']) == 1


def test_asking_and_saying_yes_do_not_mutate_then_confirmation_applies_once(client):
    tid = conversation(client)
    before = read_workspace(client)
    p = ask(client, tid, 'Pause Utilities')['proposal']
    assert read_workspace(client).snapshot() == before.snapshot()
    assert ask(client, tid, 'yes')['state'] == 'needs_input'
    assert read_workspace(client).snapshot() == before.snapshot()
    first = confirm(client, tid, p)
    assert first.status_code == 200, first.text
    assert read_workspace(client).household.policies['pol_utilities'].paused
    assert read_workspace(client).revision == before.revision + 1
    second = confirm(client, tid, p)
    assert second.json() == first.json()
    assert read_workspace(client).revision == before.revision + 1
    saved = client.get(f'/api/conversations/{tid}').json()['turns'][0]['response']['proposal']
    assert saved['state'] == 'applied' and saved['receipt'] == first.json()


def test_parallel_confirmation_returns_same_receipt_and_single_revision(client):
    tid = conversation(client)
    p = ask(client, tid, 'Pause Utilities')['proposal']
    before = read_workspace(client).revision
    body = ConfirmIn(digest=p['digest'], confirm=True)
    service = ConversationService(client.runtime, client.principal)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.confirm(tid, p['id'], body), range(2)))
    assert results[0] == results[1]
    assert read_workspace(client).revision == before + 1


def test_revised_proposal_supersedes_previous_without_reusing_authority(client):
    tid = conversation(client)
    first = ask(client, tid, 'Change Utilities to $200')['proposal']
    second = ask(client, tid, 'Make it $300')['proposal']
    assert first['id'] != second['id']
    assert confirm(client, tid, first).status_code == 409
    assert confirm(client, tid, second).status_code == 200
    policy = read_workspace(client).household.policies['pol_utilities']
    assert policy.monthly_target.amount == Decimal('300')
    assert not policy.mandate.active


def test_stale_and_canceled_proposals_do_not_change_data(client):
    tid = conversation(client)
    p = ask(client, tid, 'Pause Utilities')['proposal']
    with edit_workspace(client) as ctx:
        ctx.household.accounts['acc_checking'].nickname = 'Renamed checking'
    before = read_workspace(client).snapshot()
    assert confirm(client, tid, p).status_code == 409
    assert read_workspace(client).snapshot() == before
    p = ask(client, tid, 'Pause Utilities')['proposal']
    assert client.post(f'/api/conversations/{tid}/proposals/{p["id"]}/cancel').status_code == 200
    assert confirm(client, tid, p).status_code == 409
    assert read_workspace(client).snapshot() == before


def test_tampering_and_expiration_block_confirmation(client):
    tid = conversation(client)
    p = ask(client, tid, 'Pause Utilities')['proposal']
    assert confirm(client, tid, {**p,'digest':'0'*64}).status_code == 422
    with client.runtime.db.sessions.begin() as session:
        row = session.get(ActionProposalRow,p['id'])
        row.expires_at = utcnow() - timedelta(seconds=1)
        # Simulate expiry of an originally valid preview without corrupting its fingerprint.
        from finpilot.conversation.service import proposal_digest
        row.digest = proposal_digest(row)
        p['digest'] = row.digest
    assert confirm(client, tid, p).status_code == 409
    assert not read_workspace(client).household.policies['pol_utilities'].paused


def test_private_conversations_and_proposals_reject_other_users(client):
    tid = conversation(client)
    p = ask(client, tid, 'Pause Utilities')['proposal']
    other = TestClient(client.app)
    identity = other.post('/api/auth/register',json={'name':'Other','email':uuid4().hex+'@example.com',
                          'password':'another-local-test-password','sample_data':True}).json()
    other.headers['X-CSRF-Token'] = identity['csrf_token']
    assert other.get('/api/conversations').json()['conversations'] == []
    assert other.get(f'/api/conversations/{tid}').status_code == 404
    assert confirm(other, tid, p).status_code == 404
    assert other.post(f'/api/conversations/{tid}/messages',json={'question':'hello','client_message_id':uuid4().hex}).status_code == 404


def test_membership_role_is_rechecked_at_commit(client):
    tid = conversation(client)
    p = ask(client, tid, 'Pause Utilities')['proposal']
    with client.runtime.db.sessions.begin() as session:
        member = session.get(MembershipRow,(client.principal.user_id,client.principal.household_id))
        member.role = 'viewer'
    assert confirm(client, tid, p).status_code == 403
    assert not read_workspace(client).household.policies['pol_utilities'].paused


def test_manual_record_creation_is_previewed_and_does_not_grant_payment_capabilities(client):
    tid = conversation(client)
    before = len(read_workspace(client).household.accounts)
    p = preview(client, tid,[{'op':'upsert','collection':'accounts','values':{
        'nickname':'My new savings','institution':'My bank','type':'savings','current':'123.45','available':'123.45'}}])['proposal']
    assert len(read_workspace(client).household.accounts) == before
    receipt = confirm(client, tid,p).json()
    account = read_workspace(client).household.accounts[receipt['results'][0]['id']]
    assert account.current.amount == Decimal('123.45')
    assert all('transfer' not in c.value for c in account.capabilities)


@pytest.mark.parametrize('bad_amount',[12.34,'NaN','Infinity','-1','1.001'])
def test_invalid_write_amounts_never_create_a_proposal(client,bad_amount):
    tid = conversation(client)
    before = read_workspace(client).snapshot()
    result = preview(client,tid,[{'op':'upsert','collection':'policies','record_id':'pol_utilities','values':{'amount':bad_amount}}])
    assert result['state'] == 'needs_input' and not result.get('proposal')
    assert read_workspace(client).snapshot() == before


def test_batch_preview_failure_and_commit_failure_both_roll_back(client,monkeypatch):
    tid = conversation(client)
    before = read_workspace(client).snapshot()
    invalid = preview(client,tid,[{'op':'pause_policy','policy_id':'pol_utilities'},
                                  {'op':'pause_policy','policy_id':'missing'}])
    assert invalid['state'] == 'needs_input' and not invalid.get('proposal')
    assert read_workspace(client).snapshot() == before
    p = preview(client,tid,[{'op':'pause_policy','policy_id':'pol_utilities'},
                            {'op':'pause_policy','policy_id':'pol_insurance'}])['proposal']
    import finpilot.conversation.service as module
    original = module.apply_operation
    def fail_second(ctx, op, actor):
        if op.policy_id == 'pol_insurance':
            raise ValueError('A commit-time domain check failed')
        return original(ctx,op,actor)
    monkeypatch.setattr(module,'apply_operation',fail_second)
    assert confirm(client,tid,p).status_code == 422
    assert read_workspace(client).snapshot() == before
    with client.runtime.db.sessions() as session:
        row = session.get(ActionProposalRow,p['id'])
        assert row.state == 'pending' and row.receipt is None


def test_model_output_cannot_call_a_write_tool_or_invent_an_account(client,monkeypatch):
    client.runtime.llm.config = LLMConfig(base_url='http://127.0.0.1:1/v1',model='test-model')
    output = {'kind':'read','query':{'tool':'pause_recurring_policy','arguments':{'policy_id':'pol_utilities'}}}
    monkeypatch.setattr(client.runtime.llm,'complete_json',lambda *a,**kw:output)
    tid = conversation(client)
    result = ask(client,tid,'Please perform operation alpha')
    assert result['state'] == 'needs_input'
    assert not read_workspace(client).household.policies['pol_utilities'].paused
    output.update(kind='action',query=None,action={'operations':[{'op':'upsert','collection':'policies','values':{
        'name':'Bad','source_account_id':'external-account','destination_account_id':'acc_savings','amount':'10'}}]})
    result = ask(client,tid,'Please perform operation beta')
    assert result['state'] == 'needs_input' and not result.get('proposal')


def test_model_planner_is_schema_checked_and_uses_prior_turns(client,monkeypatch):
    client.runtime.llm.config = LLMConfig(base_url='http://127.0.0.1:1/v1',model='test-model')
    captured = []
    def output(system,user,**kwargs):
        captured.append(user)
        return {'kind':'read','query':{'tool':'get_money_overview'}}
    monkeypatch.setattr(client.runtime.llm,'complete_json',output)
    tid = conversation(client)
    ask(client,tid,'Summarize my accounts')
    response = ask(client,tid,'Please inspect position alpha')
    assert response['used_model']
    assert 'Summarize my accounts' in captured[0]
    assert 'last_plan' in captured[0] and 'owned_records' in captured[0]


def test_sample_simulation_requires_separate_explicit_confirmation(client):
    tid = conversation(client)
    p = preview(client,tid,[{'op':'build_paycheck','year':2026,'month':9,'income_event_id':'ie_sep01'}])['proposal']
    receipt = confirm(client,tid,p)
    assert receipt.status_code == 200, receipt.text
    group_id = receipt.json()['results'][0]['groups'][0]['group_id']
    sim = preview(client,tid,[{'op':'simulate_group','group_id':group_id}])['proposal']
    assert sim['requires_simulation_confirmation']
    before = read_workspace(client).snapshot()
    assert confirm(client,tid,sim).status_code == 422
    assert read_workspace(client).snapshot() == before
    done = confirm(client,tid,sim,confirm_simulation=True)
    assert done.status_code == 200, done.text
    assert done.json()['payment_mode'] == 'simulation'
    assert confirm(client,tid,sim,confirm_simulation=True).json() == done.json()


def test_real_workspace_never_accepts_sample_execution():
    with authenticated_client(sample=False) as c:
        tid = conversation(c)
        d = preview(c,tid,[{'op':'simulate_group','group_id':'anything'}])
        assert d['state'] == 'needs_input' and not d.get('proposal')
        assert 'sample workspaces' in d['answer']


def test_legacy_question_route_cannot_bypass_preview(client):
    before = read_workspace(client).snapshot()
    result = client.post('/api/ask',json={'question':'Pause pol_utilities'}).json()
    assert not result['workspace_changed']
    assert read_workspace(client).snapshot() == before


def test_missing_card_amount_is_requested_and_horizons_are_bounded(client):
    tid = conversation(client)
    assert ask(client,tid,'Which card should I use for dinner?')['state'] == 'needs_input'
    ctx = read_workspace(client)
    with pytest.raises(ValueError,match='365'):
        read_query(ctx,Scope(),ReadQuery(tool='get_cash_forecast',arguments={'days':1000000}))
    with pytest.raises(ValueError,match='recorded accounts'):
        read_query(ctx,Scope(),ReadQuery(tool='compare_debt_strategies',arguments={'use_worked_example':True}))


def test_inline_setup_is_returned_without_creating_records(client):
    tid = conversation(client)
    before = read_workspace(client).snapshot()
    result = ask(client,tid,'Add an account')
    assert result['components'] == [{'type':'form','collection':'accounts','record_id':None}]
    assert read_workspace(client).snapshot() == before


def test_created_policy_identity_is_bound_for_follow_up_edits(client):
    tid = conversation(client)
    p = preview(client,tid,[{'op':'upsert','collection':'policies','values':{
        'name':'Chat savings','source_account_id':'acc_checking','destination_account_id':'acc_savings',
        'amount':'100','monthly_target':'100','method':'fixed','purpose':'goal'}}])['proposal']
    receipt = confirm(client,tid,p).json()
    record_id = receipt['results'][0]['id']
    revised = ask(client,tid,'Make it $300')['proposal']
    assert revised['operations'][0]['record_id'] == record_id
    assert confirm(client,tid,revised).status_code == 200
    assert read_workspace(client).household.policies[record_id].monthly_target.amount == Decimal('300')


def test_monthly_split_is_atomic_and_per_deposit_semantics_are_not_silently_changed(client):
    hh = read_workspace(client).household
    savings = hh.accounts['acc_savings'].nickname
    source = hh.accounts['acc_checking'].nickname
    tid = conversation(client)
    question = f'Split $200 to {savings} from {source} monthly'
    p = ask(client,tid,question)['proposal']
    assert p['operations'][0]['values']['monthly_target'] == '200'
    assert confirm(client,tid,p).status_code == 200
    result = ask(client,tid,question.replace('monthly','every paycheck'))
    assert result['state'] == 'needs_input' and not result.get('proposal')


def test_conversation_and_receipt_survive_application_restarts(tmp_path):
    url = 'sqlite:///' + str(tmp_path / 'restart.db')
    def app():
        return create_app(url,llm=LocalLLM(LLMConfig()))
    with TestClient(app()) as c:
        identity = c.post('/api/auth/register',json={'name':'Restart fixture','email':'restart@example.com',
            'password':'restart-test-only-password','sample_data':True}).json()
        token = c.cookies.get('finpilot_session')
        csrf = identity['csrf_token']
        c.headers['X-CSRF-Token'] = csrf
        tid = conversation(c)
        p = ask(c,tid,'Pause Utilities')['proposal']
    with TestClient(app()) as c:
        c.cookies.set('finpilot_session',token)
        c.headers['X-CSRF-Token'] = csrf
        client_get = c.get(f'/api/conversations/{tid}').json()
        assert client_get['turns'][0]['response']['proposal']['id'] == p['id']
        receipt = confirm(c,tid,p).json()
    with TestClient(app()) as c:
        c.cookies.set('finpilot_session',token)
        c.headers['X-CSRF-Token'] = csrf
        assert confirm(c,tid,p).json() == receipt
        assert c.get('/api/workspace').status_code == 200


def test_chat_routes_require_authentication_and_csrf(client):
    anonymous = TestClient(client.app)
    assert anonymous.get('/api/conversations').status_code == 401
    assert anonymous.post('/api/conversations',json={}).status_code == 401
    tid = conversation(client)
    client.headers.pop('X-CSRF-Token')
    assert client.post(f'/api/conversations/{tid}/messages',json={'question':'hello','client_message_id':uuid4().hex}).status_code == 403


def test_inference_does_not_hold_financial_locks_and_stale_draft_cannot_commit(client,monkeypatch):
    client.runtime.llm.config = LLMConfig(base_url='http://127.0.0.1:1/v1',model='test-model')
    def model(*a,**kw):
        with client.runtime.transaction(client.principal,'test.concurrent') as ctx:
            ctx.household.accounts['acc_checking'].nickname = 'Changed while interpreting'
        return {'kind':'action','action':{'operations':[{'op':'pause_policy','policy_id':'pol_utilities'}]}}
    monkeypatch.setattr(client.runtime.llm,'complete_json',model)
    tid = conversation(client)
    p = ask(client,tid,'Please perform operation gamma')['proposal']
    assert confirm(client,tid,p).status_code == 409
    assert not read_workspace(client).household.policies['pol_utilities'].paused
