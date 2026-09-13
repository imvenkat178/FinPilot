"""Opt-in Llama regression: follow-up scope and protected savings.

Uses a disposable fictional workspace. Only the final turn invokes the model.
Run with the project's Python environment and Ollama llama3.2:latest available.
"""
from pathlib import Path
import sys,json,time,argparse
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tests.api_support import authenticated_client
from finpilot.ai.llm import LocalLLM,LLMConfig
from finpilot.ai.guardrails import spending_allowance_claim_error

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument("--timeout",type=float,default=12)
parser.add_argument("--output",default=".local/ai-memory-scope-normal.json")
options=parser.parse_args()

with authenticated_client() as client:
    def ask(question,**context):
        response=client.post('/api/ask',json={'question':question,**context})
        response.raise_for_status()
        return response.json()
    first=ask('How much can I spend this week?',context={'page':'accounts','account_id':'acc_checking'})
    second=ask('Explain that',conversation_id=first['conversation_id'])
    model=LocalLLM(LLMConfig(base_url='http://127.0.0.1:11434/v1',model='llama3.2:latest',timeout=options.timeout,max_tokens=256))
    if not model.health(force=True)['reachable']:
        raise SystemExit('The local Llama endpoint is unavailable; no live-model pass claimed.')
    client.runtime.llm.close()
    client.runtime.llm=model
    start=time.perf_counter()
    last=ask('Explain that',conversation_id=first['conversation_id'],context={'page':'accounts','account_id':'acc_savings'})
    expected=client.runtime.read(client.principal).registry.call('get_spending_allowance',{'days':7,'account_id':'acc_savings'})
    assert last['evidence'][0]==expected
    assert last['evidence'][0]!=second['evidence'][0]
    assert last['memory_turns']==2
    assert expected['amount']['amount']=='0.00'
    assert spending_allowance_claim_error(last['answer'],expected) is None
    report={'check':'checking-followup-switch-to-protected-savings','passed':True,
            'budget_seconds':options.timeout,'diagnostic':options.timeout>12,
            'model_understanding_accepted':bool(last['used_model']),'fallback':not last['used_model'],
            'elapsed_seconds':round(time.perf_counter()-start,3),'response':last}
    Path(options.output).write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'passed':True,'used_model':last['used_model'],'elapsed_seconds':report['elapsed_seconds'],
        'viewing':last['viewing'],'memory_turns':last['memory_turns'],'answer':last['answer'],
        'calls':last['generation']['calls'],'verification':[x for x in last['trace'] if x.get('node')=='verify']}))
