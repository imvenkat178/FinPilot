"""Desktop bridge configuration works outside a FinPilot checkout."""
import asyncio
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from uuid import uuid4

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import uvicorn

from finpilot.api.workspace import _encode
from tests.api_support import authenticated_client


@contextmanager
def live_api(app):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(16)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
        log_level="error", access_log=False, lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, "The isolated API did not start."
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
    assert not thread.is_alive(), "The isolated API did not stop."


def test_desktop_bridge_launches_from_unrelated_directory_with_spaces(tmp_path, caplog):
    checkout = Path(__file__).resolve().parents[1]
    desktop_directory = tmp_path / "Desktop MCP client working directory"
    desktop_directory.mkdir()
    assert not (desktop_directory / "finpilot").exists()
    assert desktop_directory.resolve() != checkout

    with authenticated_client() as client:
        response = client.post("/api/mcp/tokens", json={"name": "Desktop launch regression"})
        assert response.status_code == 201
        grant = response.json()
        owner = client.runtime.read(client.principal)
        expected = _encode(owner.registry.call("get_money_overview", {}))

        # Browser identity must not determine whose data the separately launched bridge reads.
        other_response = client.post("/api/auth/register", json={
            "name": "Other desktop user", "email": f"desktop-{uuid4().hex}@example.com",
            "password": "test-password-for-local-suite", "sample_data": False,
        })
        assert other_response.status_code == 201
        other = client.runtime.auth.authenticate(client.cookies.get("finpilot_session"))
        other_data = _encode(client.runtime.read(other).registry.call("get_money_overview", {}))
        assert other_data != expected

        with live_api(client.app) as api_url, tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errors:
            # These are the same command, arguments and environment fields exported by the UI.
            params = StdioServerParameters(
                command=str(Path(sys.executable).resolve()),
                args=["-m", grant["bridge"]["module"]],
                env={"PYTHONPATH": str(checkout), "FINPILOT_API_URL": api_url,
                     "FINPILOT_MCP_TOKEN": grant["token"]},
                cwd=str(desktop_directory),
            )

            async def run():
                with anyio.fail_after(25):
                    async with stdio_client(params, errlog=errors) as (read, write):
                        async with ClientSession(read, write,
                            read_timeout_seconds=timedelta(seconds=10)) as session:
                            initialized = await session.initialize()
                            assert initialized.serverInfo.name == "finpilot"
                            catalog = await session.list_tools()
                            assert any(tool.name == "get_money_overview" for tool in catalog.tools)
                            assert all(tool.annotations.readOnlyHint for tool in catalog.tools)
                            result = await session.call_tool("get_money_overview", {})
                            assert result.isError is False
                            assert result.structuredContent == {
                                "result": expected, "revision": owner.revision, "read_only": True,
                            }

            asyncio.run(run())
            errors.seek(0)
            assert grant["token"] not in errors.read()
        assert grant["token"] not in caplog.text
