"""End-to-end API timing for deterministic chat and reviewed local actions.

These are application latency checks with inference disabled, not evidence of
Llama understanding. They use disposable fictional accounts and real services.
"""
import argparse,json,statistics,time,math
from pathlib import Path
from datetime import datetime,timezone
from tests.chat_fixtures import workflow_client


def summary(values):
    values=sorted(values)
    return {"runs":len(values),"median_ms":round(statistics.median(values),2),
            "p95_ms":round(values[max(0,math.ceil(len(values)*.95)-1)],2),"samples_ms":values}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--runs',type=int,default=12)
    parser.add_argument('--output',default='.local/workflow-route-benchmark.json')
    args=parser.parse_args()
    if not 3<=args.runs<=100:
        raise ValueError('Use 3–100 repetitions')
    queries={
        'compound_records_and_history':('List all account records and find transactions for Music',['list_records','search_transactions']),
        'transaction_search':('Find transactions for Music',['search_transactions']),
        'bill_records':('List my bill records',['list_records']),
    }
    result={"measured_at":datetime.now(timezone.utc).isoformat(),"transport":"authenticated in-process ASGI",
            "database":"disposable SQLite","model_understanding_test":False,"model_calls":0,
            "live_external_calls":False,"metrics":{}}
    with workflow_client() as c:
        for label,(question,expected) in queries.items():
            timings=[]
            for _ in range(args.runs):
                start=time.perf_counter()
                response=c.post('/api/ask',json={'question':question,'conversation_id':c.action_conversation})
                timings.append(round((time.perf_counter()-start)*1000,2))
                response.raise_for_status();answer=response.json()
                assert [o['capability'] for o in answer['workflow']['operations']]==expected
                assert not answer['workflow']['pending']
                assert answer['generation']['calls']==[]
            result['metrics'][label]=summary(timings)
        timing={name:[] for name in ['review_preview','confirmation','refreshed_bootstrap','receipt_restoration','whole_reviewed_workflow']}
        for index in range(args.runs):
            begin=start=time.perf_counter()
            answer=c.post('/api/ask',json={'question':f'Update bill Mortgage payment amount to {1800+index}.25',
                    'conversation_id':c.action_conversation}).json()
            timing['review_preview'].append(round((time.perf_counter()-start)*1000,2))
            assert answer['generation']['calls']==[]
            proposal=next(part['proposal'] for part in answer['parts'] if part['type']=='proposal')
            start=time.perf_counter()
            response=c.post('/api/assistant/proposals/'+proposal['id']+'/confirm',json={'version':proposal['version']})
            timing['confirmation'].append(round((time.perf_counter()-start)*1000,2))
            response.raise_for_status();receipt=response.json()['receipt']
            start=time.perf_counter();snapshot=c.get('/api/bootstrap').json()
            timing['refreshed_bootstrap'].append(round((time.perf_counter()-start)*1000,2))
            assert snapshot['revision']==receipt['revision']
            current=c.runtime.read(c.principal).household.bills['bill_mortgage'].amount.amount
            assert str(current)==f'{1800+index}.25'
            start=time.perf_counter();restored=c.get('/api/conversations/'+c.action_conversation).json()
            timing['receipt_restoration'].append(round((time.perf_counter()-start)*1000,2))
            assert any(receipt in item.get('action_receipts',[]) for item in restored['answers'])
            timing['whole_reviewed_workflow'].append(round((time.perf_counter()-begin)*1000,2))
        result['metrics'].update({name:summary(values) for name,values in timing.items()})
    Path(args.output).write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({name:{k:v for k,v in values.items() if k!='samples_ms'} for name,values in result['metrics'].items()},indent=2))

if __name__=='__main__':
    main()
