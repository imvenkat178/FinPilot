"""Opt-in real local Llama checks for saved memory and document retrieval."""
from pathlib import Path
import sys,json,time
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from finpilot.api.app import create_app
from finpilot.ai.llm import LocalLLM, LLMConfig

model=LocalLLM(LLMConfig(base_url='http://127.0.0.1:11434/v1', model='llama3.2:latest',timeout=60,max_tokens=384))
assert model.health(force=True)['reachable']
app=create_app('sqlite:///:memory:',llm=model)
report={'model':'llama3.2:latest','timeout_seconds':60,'checks':[]}
with TestClient(app) as c:
    response=c.post('/api/auth/register',json={'name':'Llama Feature QA','email':f'llama-{uuid4().hex}@example.test','password':'local-llama-fixture-password','sample_data':True})
    assert response.status_code==201,response.text
    c.headers['X-CSRF-Token']=response.json()['csrf_token']
    def ask(name, question, **kwargs):
        start=time.perf_counter(); res=c.post('/api/ask',json={'question':question,**kwargs})
        assert res.status_code==200,res.text
        data=res.json()
        report['checks'].append({'name':name,'latency_seconds':round(time.perf_counter()-start,3),**data})
        print(json.dumps({'name':name,'used_model':data['used_model'],'latency_seconds':report['checks'][-1]['latency_seconds'],'answer':data['answer'][:300],'calls':data['generation']['calls']}),flush=True)
        Path('.local/knowledge-llama-validation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        return data
    first=ask('monthly-extra-payment','What if I pay $300 extra toward my debt each month?')
    second=ask('remembered-extra-payment','What about $500?',conversation_id=first['conversation_id'])
    assert second['resolved_route']['arguments']['amount']==500 and second['memory_turns']==1
    doc=c.post('/api/documents',files={'file':('benefits.txt',b'Commuter benefit: employees receive $120 each month for public transit. Dental enrollment closes on October 15. Vacation allowance is twenty days per year.','text/plain')}).json()['document']
    result=ask('document-answer','What commuter benefit does the document describe?',document_ids=[doc['id']])
    assert '$120' in result['answer'] and result['document_sources']
    follow=ask('document-followup','What about dental enrollment?',conversation_id=result['conversation_id'])
    assert 'October 15' in follow['answer'] and follow['memory_turns']==1
    assert len(c.get('/api/conversations/'+result['conversation_id']).json()['answers'])==2
    prior=ask('remember-user-priority','Remember: my priority is building an emergency fund.')
    recall=ask('recall-user-priority','What did I tell you my priority was?',conversation_id=prior['conversation_id'])
    assert 'emergency fund' in recall['answer']
    report['completed']=True
    report['model_verified_answers']=sum(x['used_model'] for x in report['checks'])
    Path('.local/knowledge-llama-validation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
