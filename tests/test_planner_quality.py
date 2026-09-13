"""Regression cases from observed local-model errors, not mocked success text."""
import json
import pytest
from scripts.evaluate_workflows import PARAPHRASES
from tests.chat_capability_manifest import CASES
from tests.chat_fixtures import workflow_client
from tests.test_workflow_invariants import ScriptedModel
from tests.test_chat_actions import proposal,confirm
from finpilot.ai.capabilities import catalog
from finpilot.ai.planner_context import input_schema,entity_choices,numeric_values,requested_name
from finpilot.ai.workflows import shortlist
from finpilot.services.proposals import ProposalService


def test_catalog_retrieval_keeps_every_manifest_intent_and_paraphrase():
    with workflow_client() as c:
        caps=catalog(c.runtime.read(c.principal))
        for case in CASES:
            for question in [case.question]+PARAPHRASES.get(case.name,[]):
                assert case.name in [v.name for v in shortlist(caps,question)[:2]],question


@pytest.mark.parametrize("question,kind,expected",[
    ('Create rule Travel savings, purpose goal, fixed amount 50','rule','Travel savings'),
    ('Add Holiday as a savings goal with target 1000','goal','Holiday'),
    ('Create a goal named "Holiday in Europe" with target 4000','goal','Holiday in Europe'),
    ('Add Travel Cash as a manual checking account with balance 100','account','Travel Cash'),
])
def test_new_names_do_not_include_creation_verbs_or_cut_quoted_names(question,kind,expected):
    assert requested_name(question,kind)==expected


def test_exact_numeric_candidates_exclude_dates_and_convert_rates():
    assert numeric_values('100.01 due 2026-10-01 from acc_456')==['100.01']
    assert numeric_values('federal 25 percent and state 5%',rate=True)==['0.25','0.05']
    assert numeric_values('balance $1,234.123456789012')==['1234.123456789012']


def test_schema_excludes_unrequested_defaults_and_wrong_income_fields():
    with workflow_client() as c:
        ctx=c.runtime.read(c.principal);caps=catalog(ctx)
        choices=entity_choices(ctx,ProposalService(c.runtime).choices(c.principal))
        for name,q,excluded in [
            ('draft_bill_payment','Prepare Mortgage payment bill draft',{'occurrence_date'}),
            ('correct_transaction','Recategorize transaction csv_x as subscriptions',{'description','kind'}),
            ('create_income','Create monthly income source Bonus net 250 in Everyday Checking starting 2026-10-01',{'source_id','expected_date','expected_amount'}),
            ('create_rule','Create rule Travel savings purpose goal fixed amount 50 from Everyday Checking to Emergency & Annual Savings on income',{'destination_reserve_id','liability_id'}),
        ]:
            assert not excluded & input_schema(caps[name],q,choices,{})['properties'].keys()


def test_compound_reads_do_not_lose_a_task_or_spend_model_tokens():
    with workflow_client() as c:
        model=ScriptedModel([]);c.runtime.llm=model
        answer=c.post('/api/ask',json={'question':'List all account records and find transactions for Music'}).json()
        assert answer['workflow']['operations']==[
            {'capability':'list_records','arguments':{'kind':'account'}},
            {'capability':'search_transactions','arguments':{'query':'Music'}}]
        assert len([p for p in answer['parts'] if p['type']=='result'])==2
        assert model.calls==[]
        assert not answer['explanation']['attempted']


def test_missing_model_inputs_become_durable_server_clarification():
    with workflow_client() as c:
        c.runtime.llm=ScriptedModel([json.dumps({'operations':[{'capability':'create_account',
            'arguments':{'nickname':'Travel Cash','type':'checking','current':None}}],'clarification':''})])
        answer=c.post('/api/ask',json={'question':'Create a checking account called Travel Cash','workflow_mode':'model'}).json()
        assert answer['workflow']['pending']
        assert any(p['type']=='clarification' and 'current' in p['fields'] for p in answer['parts'])
        assert not any(p['type']=='proposal' for p in answer['parts'])
        saved=c.get('/api/conversations/'+answer['conversation_id']).json()
        assert saved['answers'][-1]['workflow']['pending']


def test_mcp_tool_allowlist_checked_before_preview_and_before_claim():
    with workflow_client() as c:
        body={'conversation_id':c.action_conversation,'operations':[{'capability':'import_mcp',
            'arguments':{'record_id':c.fixture_ids['mcp'],'tool_name':'not_approved'}}]}
        bad=c.post('/api/assistant/proposals',json=body)
        assert bad.status_code==422 and 'approved' in bad.text
        body['operations'][0]['arguments']['tool_name']='statement'
        p=c.post('/api/assistant/proposals',json=body).json()
        c.app.state.mcp_servers_factory=lambda:{}
        failed=confirm(c,p)
        assert failed.status_code==422
        unchanged=c.get('/api/assistant/proposals/'+p['id']).json()
        assert unchanged['status']=='proposed' and unchanged['receipt'] is None


def test_broken_mcp_configuration_does_not_disable_local_financial_actions():
    from finpilot.integrations.mcp_client import MCPError
    with workflow_client() as c:
        def unavailable():
            raise MCPError("Invalid configuration")
        c.app.state.mcp_servers_factory=unavailable
        answer=c.post('/api/ask',json={'question':'Update bill Mortgage payment amount to 1850'}).json()
        assert proposal(answer)['status']=='proposed'


def test_unrelated_history_not_materialized_for_planner_context(monkeypatch):
    import finpilot.ai.planner_context as module
    with workflow_client() as c:
        original=module.records
        observed=[]
        def tracked(ctx,kind):
            observed.append(kind)
            return original(ctx,kind)
        monkeypatch.setattr(module,'records',tracked)
        choices=entity_choices(c.runtime.read(c.principal),{}, {'account'})
        assert list(choices)==['account']
        assert observed==['account']


def test_clear_rename_uses_exact_target_and_no_inference():
    with workflow_client() as c:
        model=ScriptedModel([]);c.runtime.llm=model
        answer=c.post('/api/ask',json={'question':'Call the Mortgage required payment rule Home allocation'}).json()
        assert answer['workflow']['operations']==[{'capability':'update_rule','arguments':{'record_id':'pol_mortgage','name':'Home allocation'}}]
        assert proposal(answer)['operations']==answer['workflow']['operations']
        assert model.calls==[]


def test_creation_using_existing_account_name_is_never_reinterpreted_as_rename():
    from finpilot.ai.workflows import deterministic
    with workflow_client() as c:
        assert deterministic('Create a manual account called Everyday Checking with a balance of 100',c.runtime.read(c.principal),{}) is None


def test_record_names_do_not_become_unrequested_income_cadence_changes():
    with workflow_client() as c:
        ctx=c.runtime.read(c.principal)
        entities=entity_choices(ctx,{})
        schema=input_schema(catalog(ctx)['update_income'],'Edit Salary (semi-monthly): net amount should be 3100',entities,{})
        assert set(schema['properties'])=={'record_id','net_amount'}
        assert schema['properties']['net_amount']['enum']==['3100']
        changed=input_schema(catalog(ctx)['update_income'],'Edit Salary (semi-monthly): cadence monthly',entities,{})
        assert changed['properties']['cadence']['enum']==['monthly']


def test_bill_creation_name_ignores_the_schedule_descriptor():
    assert requested_name('Set up a monthly Music bill, amount 12.99, next due 2026-10-05','bill')=='Music'


def test_paycheck_amount_wording_does_not_add_record_type():
    with workflow_client() as c:
        ctx=c.runtime.read(c.principal)
        choices=entity_choices(ctx,{}, {"income"})
        schema=input_schema(catalog(ctx)["update_income"],"Set the Salary (semi-monthly) net paycheck amount to 3100",choices,{})
        assert set(schema["properties"])=={"record_id","net_amount"}


def test_calculator_rate_schema_converts_percent_units():
    with workflow_client() as c:
        cap=catalog(c.runtime.read(c.principal))["assess_sweep_move"]
        schema=input_schema(cap,"Assess moving 1000 for 30 days to 4 percent APY with transfer fee 0",{}, {})
        assert schema["properties"]["destination_apy"]["enum"]==[0.04,0.0]
        assert 4 not in schema["properties"]["destination_apy"]["enum"]


def test_compound_model_cannot_omit_or_mix_task_slots():
    with workflow_client() as c:
        model=ScriptedModel([json.dumps({"operations":{
            "task_1":{"capability":"search_transactions","arguments":{"query":"Music"}},
            "task_2":{"capability":"update_bill","arguments":{"record_id":"bill_utilities","amount":"210"}}},"clarification":""})])
        c.runtime.llm=model
        answer=c.post('/api/ask',json={'question':'Find transactions for Music and update bill Utilities amount to 210','workflow_mode':'model'}).json()
        assert answer['workflow']['planner']['accepted']
        assert len(answer['workflow']['operations'])==2
        assert any(p['type']=='result' for p in answer['parts'])
        assert proposal(answer)['operations']==[{'capability':'update_bill','arguments':{'record_id':'bill_utilities','amount':'210'}}]
        schema=model.calls[0]['response_format']['json_schema']['schema']
        assert schema['properties']['operations']['required']==['task_1','task_2']
        assert len(model.calls)==1


def test_more_than_four_explicit_tasks_never_spend_inference_or_partially_run():
    with workflow_client() as c:
        model=ScriptedModel([]);c.runtime.llm=model
        answer=c.post('/api/ask',json={'question':'List accounts and list bills and list goals and list rules and list documents','workflow_mode':'model'}).json()
        assert answer['workflow']['planner']['reason']=='operation_limit'
        assert not answer['workflow']['operations']
        assert model.calls==[]


def test_document_search_and_tax_profile_share_one_answer_without_inference():
    with workflow_client() as c:
        model=ScriptedModel([]);c.runtime.llm=model
        answer=c.post('/api/ask',json={'question':'Search documents for annual fee and show my tax assumptions'}).json()
        assert [op['capability'] for op in answer['workflow']['operations']]==['search_documents','get_tax_profile']
        assert answer['document_sources']
        assert len([part for part in answer['parts'] if part['type']=='result'])==2
        assert model.calls==[]


def test_model_cannot_guess_an_id_to_bypass_duplicate_record_names():
    with workflow_client() as c:
        with c.runtime.transaction(c.principal,"fixture.duplicate_names") as ctx:
            ctx.household.accounts["acc_savings"].nickname="Everyday Checking"
        plan={"operations":[{"capability":"update_account","arguments":{"record_id":"acc_checking","current":"100"}}],"clarification":""}
        c.runtime.llm=ScriptedModel([json.dumps(plan),json.dumps(plan)])
        answer=c.post('/api/ask',json={'question':'Update account Everyday Checking balance to 100','workflow_mode':'model'}).json()
        assert any(p['type']=='clarification' and p['fields']==['record_id'] for p in answer['parts'])
        assert not any(p['type']=='proposal' for p in answer['parts'])
        explicit=c.post('/api/ask',json={'question':'Update account acc_checking balance to 100','workflow_mode':'model'}).json()
        assert proposal(explicit)['operations']==plan['operations']
