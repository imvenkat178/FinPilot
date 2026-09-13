"""Actual SDK MCP import -> private retrieval -> actual local Llama, fictional data."""
from pathlib import Path
import sys,os,json
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from tests.test_mcp_integration import fixture_server
from finpilot.integrations.mcp_client import MCPClient, ServerConfig
from finpilot.ai.llm import LocalLLM,LLMConfig
from finpilot.api.app import create_app
config=ServerConfig('fixture','SDK fixture','https://testserver/mcp',('statement',),True)
class SDKFixtureClient:
    async def read(self,**kwargs):
        server=fixture_server()
        server_app=server.streamable_http_app()
        async with server.session_manager.run():
            return await MCPClient(config,transport=httpx.ASGITransport(app=server_app)).read(**kwargs)
model=LocalLLM(LLMConfig(base_url='http://127.0.0.1:11434/v1',model='llama3.2:latest',timeout=12))
app=create_app('sqlite:///:memory:',llm=model)
app.state.mcp_servers_factory=lambda:{config.id:config}
app.state.mcp_client_factory=lambda *args:SDKFixtureClient()
# This disposable process owns this key; no real provider credential is used.
os.environ['FINPILOT_TOKEN_KEY']=Fernet.generate_key().decode()
with TestClient(app) as c:
    r=c.post('/api/auth/register',json={'name':'MCP Llama QA','email':uuid4().hex+'@example.test','password':'test-only-mcp-llama-password','sample_data':True});r.raise_for_status()
    c.headers['X-CSRF-Token']=r.json()['csrf_token']
    r=c.post('/api/mcp/connections',json={'server_id':'fixture'});r.raise_for_status();connection=r.json()['connection']
    r=c.post('/api/mcp/connections/'+connection['id']+'/import',json={'resource_uri':'report://annual','title':'Annual fee statement'});r.raise_for_status();doc=r.json()['document']
    r=c.post('/api/ask',json={'question':'What annual fee does my statement document report?','document_ids':[doc['id']]});r.raise_for_status();answer=r.json()
    assert answer['used_model'] and '$95' in answer['answer'],answer
    assert answer['document_sources'][0]['source_type']=='mcp'
    assert answer['document_sources'][0]['source_metadata']['server_id']=='fixture'
    assert not answer['workspace_changed']
    Path('.local/mcp-llama-validation.json').write_text(json.dumps(answer,indent=2),encoding='utf-8')
    print(json.dumps({'passed':True,'used_model':answer['used_model'],'latency_ms':answer['latency_ms'],'answer':answer['answer'],'calls':answer['generation']['calls']}))
