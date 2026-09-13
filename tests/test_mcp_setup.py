"""MCP setup is useful before a provider is connected, without exposing secrets."""
from cryptography.fernet import Fernet
from finpilot.integrations.mcp_client import ServerConfig
from tests.api_support import authenticated_client


def test_setup_distinguishes_available_export_from_unconfigured_imports(monkeypatch):
    monkeypatch.delenv('FINPILOT_TOKEN_KEY', raising=False)
    monkeypatch.delenv('FINPILOT_MCP_SERVERS', raising=False)
    with authenticated_client() as c:
        response=c.get('/api/mcp/setup')
        assert response.status_code == 200
        data=response.json()
        assert data['export']['available'] and data['export']['transport']=='stdio'
        assert data['export']['module']=='finpilot.integrations.mcp_server'
        assert data['export']['api_url']=='http://testserver'
        assert not data['imports']['ready']
        assert data['imports']['server_count']==0
        c.post('/api/auth/logout')
        assert c.get('/api/mcp/setup').status_code==401


def test_missing_encryption_detected_before_requesting_provider_credentials(monkeypatch):
    monkeypatch.delenv('FINPILOT_TOKEN_KEY',raising=False)
    with authenticated_client() as c:
        cfg=ServerConfig('source','Approved source','https://source.example/mcp',('read_statement',))
        c.app.state.mcp_servers_factory=lambda:{cfg.id:cfg}
        data=c.get('/api/mcp/connections').json()
        assert data['configured'] and data['setup']['configured']
        assert not data['setup']['credential_storage_ready'] and not data['setup']['ready']
        key=Fernet.generate_key().decode()
        monkeypatch.setenv('FINPILOT_TOKEN_KEY',key)
        response=c.get('/api/mcp/setup')
        assert response.json()['imports']['ready']
        assert key not in response.text
        monkeypatch.setenv('FINPILOT_TOKEN_KEY','invalid-secret-configuration')
        response=c.get('/api/mcp/setup')
        assert not response.json()['imports']['ready']
        assert 'invalid-secret-configuration' not in response.text


def test_invalid_import_config_does_not_hide_export_setup(monkeypatch):
    monkeypatch.setenv('FINPILOT_MCP_SERVERS','{"private-invalid-value":true}')
    with authenticated_client() as c:
        response=c.get('/api/mcp/setup')
        assert response.status_code==200
        assert response.json()['export']['available']
        assert not response.json()['imports']['ready']
        assert 'private-invalid-value' not in response.text


def test_token_bridge_uses_operator_origin_and_does_not_repeat_secret():
    with authenticated_client() as c:
        c.app.state.public_origin='https://finpilot.example'
        # Public origin also controls CSRF same-origin checks.
        response=c.post('/api/mcp/tokens',headers={'Origin':'https://finpilot.example'},json={'name':'Desktop'})
        assert response.status_code==201,response.text
        body=response.json()
        assert body['bridge']=={'module':'finpilot.integrations.mcp_server','api_url':'https://finpilot.example'}
        assert body['token'] not in c.get('/api/mcp/setup').text
        assert body['token'] not in c.get('/api/mcp/tokens').text
        assert 'C:\\Users' not in response.text and '/app/' not in response.text
