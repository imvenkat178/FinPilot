"""Authenticated API fixtures using the same session boundary as the browser."""
from contextlib import contextmanager
from uuid import uuid4

from fastapi.testclient import TestClient

from finpilot.api.app import create_app
from finpilot.ai.llm import LocalLLM, LLMConfig


@contextmanager
def authenticated_client(*, sample=True, database_url="sqlite:///:memory:"):
    app = create_app(database_url, llm=LocalLLM(LLMConfig()))
    with TestClient(app) as client:
        response = client.post('/api/auth/register', json={
            'name': 'API Test User', 'email': f'test-{uuid4().hex}@example.com',
            'password': 'test-password-for-local-suite', 'sample_data': sample,
        })
        assert response.status_code == 201, response.text
        identity = response.json()
        client.headers['X-CSRF-Token'] = identity['csrf_token']
        client.runtime = app.state.runtime
        client.principal = client.runtime.auth.authenticate(client.cookies.get('finpilot_session'))
        client.identity = identity
        yield client


def read_workspace(client):
    return client.runtime.read(client.principal)


@contextmanager
def edit_workspace(client):
    with client.runtime.transaction(client.principal, 'test.setup') as context:
        yield context
