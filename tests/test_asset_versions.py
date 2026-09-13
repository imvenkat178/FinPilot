"""A refreshed document loads one current version of the complete module tree."""
import re
from urllib.parse import urljoin
from tests.api_support import authenticated_client


def test_document_versions_relative_modules_and_revalidates_legacy_assets():
    with authenticated_client(sample=False) as client:
        page = client.get('/')
        assert page.status_code == 200
        entry = re.search(r'src="(/assets/v[0-9a-f]+/app.js)"', page.text).group(1)
        for name in ('app.js', 'assistant.js', 'knowledge.js', 'core.js', 'theme.css', 'assistant.css'):
            asset = client.get(urljoin(entry, name))
            assert asset.status_code == 200
            assert asset.headers['cache-control'] == 'no-cache'
        assert 'Conversations are saved to your account' in client.get(urljoin(entry, 'assistant.js')).text
        assert client.get('/assets/assistant.js').headers['cache-control'] == 'no-cache'
