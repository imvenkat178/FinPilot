"""Opt-in evaluation against a real local model; never substitutes a mock.

Run: .venv/Scripts/python.exe scripts/evaluate_ai.py --output .local/ai-evaluation.json
Only fictional fixtures and disposable in-memory user workspaces are used.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import threading
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import sys
import time
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from finpilot.ai.graph import FinanceAgent, ToolChoice
from finpilot.ai.guardrails import check_grounding
from finpilot.ai.llm import LLMConfig, LocalLLM, autodetect
from finpilot.ai.tools import ToolRegistry
from finpilot.api.app import create_app
from finpilot.engine.allocator import AllocationEngine
from finpilot.engine.tax import TaxProfile
from finpilot.execution.engine import ExecutionEngine, ProviderFault, SimulatedProvider
from finpilot.seed.demo import demo_household
from tests.query_suite import CASES, verify


class ObservedLLM(LocalLLM):
    """Measure real HTTP completions without retaining prompts or credentials."""
    def __init__(self, config):
        super().__init__(config)
        self.observations = []
        self.started = threading.Event()

    def chat(self, *args, **kwargs):
        start = time.perf_counter()
        self.started.set()
        observation = {}
        try:
            response = super().chat(*args, **kwargs)
            observation.update(model=response.get('model'), usage=response.get('usage'),
                finish_reason=response['choices'][0].get('finish_reason'), completed=True,
                draft=response['choices'][0]['message'].get('content'),
                tool_calls=response['choices'][0]['message'].get('tool_calls'))
            return response
        except Exception as exc:
            observation.update(completed=False, error=type(exc).__name__, detail=str(exc))
            raise
        finally:
            observation['latency_ms'] = round((time.perf_counter()-start)*1000)
            self.observations.append(observation)


def fixtures(*, transfer_failure=False):
    household = demo_household()
    provider = SimulatedProvider()
    execution = ExecutionEngine(household, provider)
    if transfer_failure:
        _, runs = AllocationEngine(household).allocate_month(2026, 9)
        group = execution.build_group_from_allocation(runs[0])
        provider.inject(max(group.legs, key=lambda leg: leg.amount.amount).id, ProviderFault.TIMEOUT)
        execution.run_group(group, settle=True)
    return ToolRegistry(household, TaxProfile(), execution)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='.local/ai-evaluation.json')
    parser.add_argument('--timeout', type=float, default=12)
    parser.add_argument('--max-tokens', type=int, default=384)
    parser.add_argument('--tool-choice', choices=['router', 'model'], default='router')
    parser.add_argument('--only', help='Comma-separated one-based catalogue case numbers')
    parser.add_argument('--api-only', action='store_true')
    args = parser.parse_args()
    config = autodetect()
    if not config:
        raise SystemExit('FAIL: No real model discovered. Start Ollama or configure FINPILOT_LLM_BASE_URL/MODEL.')
    config.timeout, config.max_tokens = args.timeout, args.max_tokens
    # Every selected case must attempt the real model even after a previous timeout.
    config.failure_cooldown = 0
    config.__post_init__()
    llm = ObservedLLM(config)
    health = llm.health(force=True)
    if not health.get('reachable') or config.model not in health.get('models_served', []):
        raise SystemExit('FAIL: configured model is not listed by the real endpoint.')
    print(f'Real model: {config.model}; budget={config.timeout}s; tokens={config.max_tokens}', flush=True)
    report = {'at': datetime.now(timezone.utc).isoformat(), 'fixture': 'fictional demo household only',
        'configuration': config.to_json(), 'tool_choice': args.tool_choice, 'failure_cooldown': 0,
        'notes': ['Case wording checks include exact expected phrases; inspect paraphrase-only failures separately.',
                  'A passed calculator fallback does not establish that the model wrote an answer.',
                  'API cases use real authentication and disposable SQLite, not a hosted PostgreSQL service.'],
        'cases': [], 'api_checks': []}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    def save():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    selected = {int(x) for x in args.only.split(',')} if args.only else None
    if not args.api_only:
        for index, case in enumerate(CASES, 1):
            if selected and index not in selected: continue
            # A settled payment fixture must not silently consume the funds for
            # unrelated card/allowance cases. Each question gets isolated data.
            agent = FinanceAgent(fixtures(transfer_failure=case.expect_tool == "explain_transfer_outcome"),
                                 llm, compile_graph=False)
            start = len(llm.observations)
            try:
                result = verify(agent, case, ToolChoice(args.tool_choice))
                row = {'number': index, 'question': case.question, 'problems': result['problems'],
                       'result': result['result'].to_json()}
            except Exception as exc:
                row = {'number': index, 'question': case.question,
                       'problems': [f'{type(exc).__name__}: {exc}']}
            row['completions'] = llm.observations[start:]
            report['cases'].append(row)
            print(f"{index:02d} {'FAIL' if row['problems'] else 'PASS'} AI={row.get('result',{}).get('used_model',False)} {row.get('result',{}).get('latency_ms',0)}ms {case.question}", flush=True)
            if row['problems']: print('  '+ '; '.join(row['problems']), flush=True)
            save()

    app = create_app('sqlite:///:memory:', llm=llm)
    with TestClient(app) as client:
        def check(name, condition):
            report['api_checks'].append({'name': name, 'passed': bool(condition)})
            print(f"API {'PASS' if condition else 'FAIL'} {name}", flush=True)
            save()
        check('Anonymous AI request denied', client.post('/api/ask', json={'question':'My balance?'}).status_code == 401)
        def register():
            response = client.post('/api/auth/register', json={'name':'Synthetic AI evaluator',
                'email':f'ai-eval-{uuid4().hex}@example.com','password':uuid4().hex+'-A9!', 'sample_data':True})
            response.raise_for_status()
            return response.json()
        identity = register()
        check('AI mutation endpoint requires CSRF', client.post('/api/ask',json={'question':'My balance?'}).status_code == 403)
        client.headers['X-CSRF-Token'] = identity['csrf_token']
        first_cookie = client.cookies.get('finpilot_session')
        before = client.get('/api/bootstrap').json()
        start = len(llm.observations)
        llm.started.clear()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(client.post, '/api/ask', json={'question':'How much can I spend this week?',
                'context':{'page':'cashflow'}, 'include_evidence':True})
            inference_started = llm.started.wait(timeout=5)
            pending_before_read = not future.done()
            read_start = time.perf_counter()
            concurrent_read = client.get('/api/bootstrap')
            read_ms = round((time.perf_counter()-read_start)*1000)
            report['financial_read_during_inference_ms'] = read_ms
            check('Financial read completes while real inference is pending', inference_started
                  and pending_before_read and not future.done() and concurrent_read.status_code == 200)
            asked = future.result(timeout=config.timeout + 15)
        check('Authenticated question returns answer and evidence', asked.status_code == 200 and bool(asked.json().get('evidence')))
        answer = asked.json()
        report['api_answer'] = answer
        report['api_completions'] = llm.observations[start:]
        check('Published figures match calculation data', check_grounding(answer.get('answer',''),answer.get('evidence',[])).ok)
        history = client.get('/api/ask/history').json()
        check('Answer saved and retrievable for its user', any(row.get('id')==answer.get('answer_id') for row in history.get('answers',[])))
        after = client.get('/api/bootstrap').json()
        check('Read-only AI question leaves workspace revision unchanged', before.get('revision') == after.get('revision') and answer.get('workspace_changed') is False)
        check('Forged account context rejected', client.post('/api/ask',json={'question':'My balance?',
            'context':{'page':'accounts','account_id':'another-household-account'}}).status_code == 404)
        client.post('/api/auth/logout')
        second = register()
        client.headers['X-CSRF-Token'] = second['csrf_token']
        check('Second user cannot see first user chat history', client.get('/api/ask/history').json().get('answers') == [])
        client.post('/api/auth/logout')
        client.cookies.set('finpilot_session',first_cookie)
        check('Revoked session cannot read AI history', client.get('/api/ask/history').status_code == 401)
    rows = report['cases']
    accepted = sum(row.get('result',{}).get('used_model',False) for row in rows)
    model_tools = sum(node.get('node') == 'execute' and node.get('mode') == 'model' and node.get('ok') is True
        for row in rows for node in row.get('result',{}).get('trace',[]))
    report['summary'] = {'model_selected_tools_completed':model_tools, 'catalogue_cases':len(rows), 'catalogue_passed':sum(not row['problems'] for row in rows),
        'model_answers_accepted':accepted, 'api_checks_passed':sum(row['passed'] for row in report['api_checks']),
        'api_checks':len(report['api_checks']), 'real_completions':sum(row['completed'] for row in llm.observations),
        'api_model_answer_accepted':bool(report.get('api_answer',{}).get('used_model')),
        'catalogue_median_ms':statistics.median([row.get('result',{}).get('latency_ms',0) for row in rows]) if rows else None}
    save()
    print(json.dumps(report['summary']),flush=True)
    print(f'Report: {output.resolve()}',flush=True)
    failures = any(row['problems'] for row in rows) or any(not row['passed'] for row in report['api_checks'])
    # Never let a run with only refusals/fallbacks masquerade as live-model success.
    return int(failures or (args.tool_choice == 'model' and rows and not model_tools)
               or not any(row['completed'] for row in llm.observations)
               or not (accepted or report['summary']['api_model_answer_accepted']))

if __name__ == '__main__':
    raise SystemExit(main())
