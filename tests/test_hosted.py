"""Hosted boundaries exercised through the real ASGI application and database."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone, date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from finpilot.api.app import create_app
from finpilot.ai.llm import LocalLLM, LLMConfig
from finpilot.persistence.database import UserRow, SessionRow, AuditRow, MembershipRow
from finpilot.persistence.codec import encode, decode
from finpilot.runtime import Runtime, WorkspaceContext
from finpilot.persistence.database import Database
from finpilot.money import Money

PASSWORD = 'hosted-integration-test-password'


def register(client, email='owner@example.com', sample=True):
    result=client.post('/api/auth/register',json={'name':'Owner','email':email,
        'password':PASSWORD,'sample_data':sample})
    assert result.status_code == 201, result.text
    data=result.json()
    client.headers['X-CSRF-Token']=data['csrf_token']
    return data


@pytest.fixture
def hosted(tmp_path):
    url='sqlite:///'+(tmp_path/'hosted.db').as_posix()
    app=create_app(url,llm=LocalLLM(LLMConfig()))
    with TestClient(app) as client:
        yield client, app.state.runtime, url


@pytest.mark.parametrize('path',['/api/bootstrap','/api/workspace','/api/dashboard',
    '/api/audit','/api/ask/history','/api/execution/groups'])
def test_anonymous_cannot_read_finances(hosted,path):
    client,_,_=hosted
    assert client.get(path).status_code == 401


@pytest.mark.parametrize('path',['/api/transactions/search','/api/documents/search','/api/debt/compare','/api/debt/what-if',
    '/api/mortgage/scenarios','/api/mortgage/biweekly','/api/cards/utilization','/api/tax/net-benefit'])
def test_anonymous_cannot_run_body_reads(hosted,path):
    client,_,_=hosted
    assert client.post(path,json={}).status_code == 401


def test_password_and_session_are_hashed_and_logout_revokes(hosted):
    client,r,_=hosted
    identity=register(client)
    cookie=client.cookies.get('finpilot_session')
    with r.db.sessions() as db:
        user=db.scalar(select(UserRow))
        session=db.scalar(select(SessionRow))
        assert user.password_hash.startswith('$argon2id$')
        assert PASSWORD not in user.password_hash
        assert session.token_hash != cookie and len(session.token_hash) == 64
    response=client.post('/api/auth/logout')
    assert response.status_code == 200
    client.cookies.set('finpilot_session',cookie)
    assert client.get('/api/bootstrap').status_code == 401
    assert client.post('/api/auth/login',json={'email':'owner@example.com','password':'wrong'}).status_code == 401
    assert client.post('/api/auth/login',json={'email':'owner@example.com','password':PASSWORD}).status_code == 200


def test_expired_session_and_short_password_are_rejected(hosted):
    client,r,_=hosted
    assert client.post('/api/auth/register',json={'name':'A','email':'a@example.com','password':'short'}).status_code == 422
    register(client)
    with r.db.sessions.begin() as db:
        db.scalar(select(SessionRow)).expires_at=datetime.now(timezone.utc)-timedelta(seconds=1)
    assert client.get('/api/auth/me').status_code == 401


def test_csrf_and_cross_origin_checks_cover_mutations(hosted):
    client,_,_=hosted
    identity=register(client)
    path='/api/policies/pol_card/pause'
    del client.headers['X-CSRF-Token']
    assert client.post(path).status_code == 403
    client.headers['X-CSRF-Token']='unrelated-session'
    assert client.post(path).status_code == 403
    client.headers['X-CSRF-Token']=identity['csrf_token']
    assert client.post(path,headers={'Origin':'https://unrelated.example'}).status_code == 403
    assert client.post(path,headers={'Sec-Fetch-Site':'cross-site'}).status_code == 403
    assert client.post(path).status_code == 200


def test_households_are_isolated_even_with_identical_sample_ids(hosted):
    client,r,_=hosted
    first=register(client)
    other=TestClient(client.app)
    second=register(other,'second@example.com')
    assert first['household_id'] != second['household_id']
    assert client.patch('/api/manage/accounts/acc_checking',json={'nickname':'Private one'}).status_code == 200
    one=client.get('/api/workspace').json()
    two=other.get('/api/workspace').json()
    assert next(a for a in one['accounts'] if a['id']=='acc_checking')['nickname']=='Private one'
    assert all(a['nickname']!='Private one' for a in two['accounts'])
    assert other.get('/api/workspace?household='+first['household_id']).status_code == 404
    fresh=client.post('/api/manage/accounts',json={'nickname':'Only one','type':'checking','current':'10','available':'10'}).json()['id']
    assert other.patch('/api/manage/accounts/'+fresh,json={'nickname':'Hijack'}).status_code == 404
    assert other.post('/api/ask',json={'question':'Show balance','context':{'account_id':fresh}}).status_code == 404
    assert other.get('/api/audit').json()['events']==[]
    other.close()


def test_stale_write_and_failed_mutation_leave_no_audit_or_partial_state(hosted):
    client,r,_=hosted
    register(client)
    first=client.get('/api/bootstrap').json()
    revision=first['revision']
    headers={'If-Match':str(revision)}
    assert client.patch('/api/manage/accounts/acc_checking',json={'nickname':'Saved'},headers=headers).status_code==200
    assert client.patch('/api/manage/accounts/acc_checking',json={'nickname':'Stale'},headers=headers).status_code==409
    count=len(client.get('/api/audit').json()['events'])
    response=client.patch('/api/manage/accounts/acc_checking',json={'nickname':'Partial','current':'NaN'})
    assert response.status_code==422
    current=client.get('/api/bootstrap').json()
    assert current['revision']==revision+1
    assert next(a for a in current['workspace']['accounts'] if a['id']=='acc_checking')['nickname']=='Saved'
    assert len(client.get('/api/audit').json()['events'])==count


def test_database_restart_retains_workspace_drafts_and_sessions(hosted):
    client,r,url=hosted
    register(client)
    client.patch('/api/manage/accounts/acc_checking',json={'nickname':'Persisted checking'})
    groups=client.post('/api/execution/build').json()['groups']
    assert groups
    second=create_app(url,llm=LocalLLM(LLMConfig()))
    with TestClient(second) as restarted:
        restarted.cookies.update(client.cookies)
        workspace=restarted.get('/api/workspace').json()
        assert next(a for a in workspace['accounts'] if a['id']=='acc_checking')['nickname']=='Persisted checking'
        assert len(restarted.get('/api/execution/groups').json()['groups'])==len(groups)


def test_csv_is_atomic_idempotent_paginated_and_tenant_scoped(hosted):
    client,r,_=hosted
    register(client)
    csv='Date,Description,Amount\n2026-09-08,Coffee,-4.85\n2026-09-09,Pay,1200\n'
    payload={'account_id':'acc_checking','csv':csv,'mapping':{'date':'Date','description':'Description','amount':'Amount'}}
    before=client.get('/api/workspace').json()['accounts']
    assert client.post('/api/transactions/preview',json=payload).json()['new_count']==2
    assert client.post('/api/transactions/search',json={}).json()['total']==0
    assert client.post('/api/transactions/import',json=payload).json()['imported']==2
    assert client.post('/api/transactions/import',json=payload).json()['duplicates']==2
    result=client.post('/api/transactions/search',json={'limit':1}).json()
    assert result['total']==2 and len(result['transactions'])==1
    assert client.post('/api/transactions/search',json={'q':'Coffee'}).json()['total']==1
    malformed={**payload,'csv':'Date,Description,Amount\n2026-09-07,Good,-10\ninvalid,Bad,50'}
    assert client.post('/api/transactions/import',json=malformed).status_code==422
    assert client.post('/api/transactions/search',json={}).json()['total']==2
    assert client.get('/api/workspace').json()['accounts']==before
    other=TestClient(client.app);register(other,'second@example.com')
    assert other.post('/api/transactions/search',json={}).json()['total']==0
    other.close()


def test_rule_authorization_uses_session_actor_and_is_not_shared(hosted):
    client,r,_=hosted
    identity=register(client)
    assert client.post('/api/policies/pol_card/authorize',json={'mode':'standing','per_run_cap':'500','authorized_by':'someone'}).status_code==422
    response=client.post('/api/policies/pol_card/authorize',json={'mode':'standing','per_run_cap':'500'})
    assert response.status_code==200
    assert response.json()['authorized_by']==identity['user']['id']
    principal=r.auth.authenticate(client.cookies.get('finpilot_session'))
    ctx=r.read(principal)
    assert ctx.household.policies['pol_card'].mandate.active
    assert all(not p.mandate.active for p in ctx.household.policies.values() if p.id!='pol_card')
    groups=client.post('/api/execution/build').json()['groups']
    assert client.post('/api/execution/run/'+groups[0]['group_id'],json={'confirm_simulation':False}).status_code==422


def test_viewer_cannot_mutate_and_revocation_takes_effect_next_request(hosted):
    client,r,_=hosted
    identity=register(client)
    with r.db.sessions.begin() as db:
        db.get(MembershipRow,(identity['user']['id'],identity['household_id'])).role='viewer'
    assert client.get('/api/bootstrap').status_code==200
    assert client.post('/api/policies/pol_card/pause').status_code==403
    with r.db.sessions.begin() as db:
        db.delete(db.get(MembershipRow,(identity['user']['id'],identity['household_id'])))
    assert client.get('/api/bootstrap').status_code==401


def test_assistant_answers_are_persisted_and_private(hosted):
    client,r,_=hosted
    register(client)
    response=client.post('/api/ask',json={'question':'How much can I spend this week?'})
    assert response.status_code==200,response.text
    answer=response.json()
    assert answer['answer_id'] and answer['tools_called']
    assert client.get('/api/ask/history').json()['answers'][0]['id']==answer['answer_id']
    other=TestClient(client.app);register(other,'second@example.com')
    assert other.get('/api/ask/history').json()['answers']==[]
    assert other.post('/api/llm/configure',json={'base_url':'http://unrelated.example'}).status_code==404
    other.close()


def test_serialized_concurrent_writers_preserve_all_updates(hosted):
    client,r,_=hosted
    register(client)
    principal=r.auth.authenticate(client.cookies.get('finpilot_session'))
    before=r.read(principal)
    amount=before.household.accounts['acc_checking'].current.amount
    def add_one(_):
        with r.transaction(principal,'test.concurrent') as ctx:
            account=ctx.household.accounts['acc_checking']
            account.current=account.current+Money('1')
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(add_one,range(12)))
    after=r.read(principal)
    assert after.household.accounts['acc_checking'].current.amount==amount+12
    assert after.revision==before.revision+12
    assert len(client.get('/api/audit').json()['events'])==12


def test_cache_is_revision_scoped_and_cannot_be_mutated_by_a_reader(hosted):
    client,r,_=hosted
    register(client)
    principal=r.auth.authenticate(client.cookies.get('finpilot_session'))
    data=r.dashboard(principal);data['overview']['private-test']='leaked'
    assert 'private-test' not in r.dashboard(principal)['overview']
    old=r.dashboard(principal)['revision']
    client.post('/api/policies/pol_card/pause')
    assert r.dashboard(principal)['revision']==old+1


def test_real_workspaces_roll_planning_date_without_claiming_balance_sync(hosted):
    client,r,_=hosted
    register(client,sample=False)
    principal=r.auth.authenticate(client.cookies.get('finpilot_session'))
    with r.transaction(principal,'test.old-date') as ctx:
        ctx.household.as_of=date(2020,1,1)
    assert r.read(principal).household.as_of==date.today()


def test_codec_rejects_unapproved_types_and_preserves_decimal_and_aliases():
    from finpilot.models import Mandate
    mandate=Mandate(per_run_cap=Money('123.45'))
    restored=decode(encode({'policy':mandate,'leg':mandate}))
    assert restored['policy'] is restored['leg']
    assert restored['policy'].per_run_cap.amount==Decimal('123.45')
    with pytest.raises(KeyError):
        decode({'schema_version':1,'data':{'$type':'os.system','$id':'0','fields':{}}})


def test_in_flight_principal_cannot_mutate_after_membership_role_downgrade(hosted):
    from finpilot.services.auth import AuthError

    client, runtime, _ = hosted
    identity = register(client)
    principal = runtime.auth.authenticate(client.cookies.get("finpilot_session"))
    before = runtime.read(principal)
    with runtime.db.sessions.begin() as session:
        session.get(MembershipRow, (identity["user"]["id"], identity["household_id"])).role = "viewer"
    with pytest.raises(AuthError) as denied:
        with runtime.transaction(principal, "test.delayed-mutation") as ctx:
            ctx.household.name = "Changed using stale authority"
    assert denied.value.status == 403
    after = runtime.read(principal)
    assert after.household.name == before.household.name
    assert after.revision == before.revision
    assert client.get("/api/audit").json()["events"] == []
