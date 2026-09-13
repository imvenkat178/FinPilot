"""Opt-in migration and reviewed-action checks in a newly created local PostgreSQL DB.

The supplied maintenance URL must be loopback. Only this run's random database
is created and dropped. Financial fixtures, MCP and bank data are fictional.
"""
import argparse,json,os,time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine,inspect,select,func
from sqlalchemy.engine import make_url
from tests.test_migrations import migration_config
from tests.chat_fixtures import workflow_client
from tests.api_support import authenticated_client
from tests.test_chat_actions import proposal,confirm
from finpilot.ai.llm import LocalLLM,LLMConfig
from finpilot.persistence.database import Base,Database
from finpilot.persistence.action_models import ActionReceiptRow
from finpilot.runtime import Runtime,RevisionConflict
from finpilot.services.proposals import ProposalService


@contextmanager
def disposable_database(maintenance_url):
    url=make_url(maintenance_url)
    if url.get_backend_name()!='postgresql' or url.host not in {'localhost','127.0.0.1','::1'}:
        raise ValueError('Use an explicit loopback PostgreSQL maintenance URL.')
    name='finpilot_e2e_'+uuid4().hex
    admin=create_engine(url,isolation_level='AUTOCOMMIT')
    identifier=admin.dialect.identifier_preparer.quote(name)
    created=False
    try:
        with admin.connect() as connection:
            connection.exec_driver_sql('CREATE DATABASE '+identifier)
        created=True
        yield url.set(database=name).render_as_string(hide_password=False)
    finally:
        if created:
            with admin.connect() as connection:
                connection.exec_driver_sql('DROP DATABASE '+identifier+' WITH (FORCE)')
        admin.dispose()


def verify(url):
    checks=[]
    config=migration_config(url)
    with patch.dict(os.environ,{'FINPILOT_DATABASE_URL':url,'FINPILOT_ENV':'test'}):
        command.upgrade(config,'head')
        command.upgrade(config,'head')
        engine=create_engine(url)
        try:
            with engine.connect() as connection:
                assert compare_metadata(MigrationContext.configure(connection,opts={'compare_type':True}),Base.metadata)==[]
            assert set(inspect(engine).get_table_names())==set(Base.metadata.tables)|{'alembic_version'}
        finally:
            engine.dispose()
        checks.append('migration_head_repeat_and_metadata')
        with workflow_client(url) as c:
            other=Runtime(Database(url),LocalLLM(LLMConfig()))
            try:
                a=c.post('/api/ask',json={'question':'List all account records and find transactions for Music'}).json()
                assert len([p for p in a['parts'] if p['type']=='result'])==2
                assert a['generation']['calls']==[]
                checks.append('compound_authenticated_chat')
                p=proposal(c.post('/api/ask',json={'question':'Update bill Mortgage payment amount to 1999.99'}).json())
                services=[ProposalService(c.runtime),ProposalService(other)]
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results=list(pool.map(lambda service:service.confirm(c.principal,p['id'],p['version']),services))
                receipt=results[0]['receipt']
                assert receipt==results[1]['receipt']
                assert c.runtime.read(c.principal).revision==p['revision']+1
                assert c.runtime.read(c.principal).household.bills['bill_mortgage'].amount.amount==Decimal('1999.99')
                with c.runtime.db.sessions() as session:
                    assert session.scalar(select(func.count()).select_from(ActionReceiptRow).where(ActionReceiptRow.proposal_id==p['id']))==1
                assert c.get('/api/bootstrap').json()['revision']==receipt['revision']
                restored=c.get('/api/conversations/'+p['conversation_id']).json()
                assert any(receipt in answer.get('action_receipts',[]) for answer in restored['answers'])
                checks.append('two_workers_one_confirmation_receipt_and_refresh')
                pending=[]
                for value in ['210','220']:
                    pending.append(proposal(c.post('/api/ask',json={'question':'Update bill Utilities amount to '+value}).json()))
                def apply(item):
                    service,action=item
                    try:
                        service.confirm(c.principal,action['id'],action['version'])
                        return 'succeeded'
                    except RevisionConflict:
                        return 'stale'
                with ThreadPoolExecutor(max_workers=2) as pool:
                    outcomes=list(pool.map(apply,zip(services,pending)))
                assert sorted(outcomes)==['stale','succeeded'],outcomes
                checks.append('two_workers_conflicting_previews_reject_stale')
                p=proposal(c.post('/api/ask',json={'question':'Update bill Utilities amount to 230'}).json())
                with authenticated_client(database_url=url) as foreign:
                    assert foreign.get('/api/assistant/proposals/'+p['id']).status_code==404
                    assert foreign.post('/api/assistant/proposals/'+p['id']+'/confirm',json={'version':p['version']}).status_code==404
                assert c.get('/api/assistant/proposals/'+p['id']).json()['status']=='proposed'
                assert c.post('/api/assistant/proposals/'+p['id']+'/cancel',json={'version':p['version']}).status_code==200
                checks.append('cross_tenant_rejection_and_cancel')
                response=c.post('/api/ask',json={'question':'Import Report source statement','workflow_input':[
                    {'capability':'import_mcp','arguments':{'record_id':c.fixture_ids['mcp'],'tool_name':'statement'}}]}).json()
                saved=confirm(c,proposal(response))
                assert saved.status_code==200,saved.text
                assert saved.json()['receipt']['status']=='succeeded'
                checks.append('actual_mcp_sdk_fixture_import_and_receipt')
            finally:
                other.close()
        command.downgrade(config,'base')
        engine=create_engine(url)
        try:
            assert inspect(engine).get_table_names()==['alembic_version']
        finally:
            engine.dispose()
        command.upgrade(config,'head')
        checks.append('migration_downgrade_and_reupgrade')
    return checks


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--maintenance-url',required=True)
    parser.add_argument('--output',default='.local/postgres-e2e.json')
    args=parser.parse_args()
    started=time.perf_counter()
    with disposable_database(args.maintenance_url) as url:
        checks=verify(url)
    report={'passed':True,'database':'local PostgreSQL, disposable database','live_external_calls':False,
            'checks':checks,'elapsed_seconds':round(time.perf_counter()-started,3)}
    Path(args.output).write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
