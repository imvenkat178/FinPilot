"""FinPilot MCP stdio bridge for desktop/agent clients.

Run ``python -m finpilot.integrations.mcp_server`` with FINPILOT_API_URL and
FINPILOT_MCP_TOKEN in the client environment. The token is a revocable FinPilot
read grant, not an OAuth token. Stdout is reserved for MCP protocol messages.
"""
from __future__ import annotations

import asyncio
import json
import os
from urllib.parse import urlsplit

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent, Tool, ToolAnnotations

from . import mcp_logging  # noqa: F401 -- redact SDK validation payloads in diagnostics


class FinPilotAPI:
    def __init__(self, url, token, *, transport=None):
        target = urlsplit(url)
        if (target.scheme not in ("http", "https") or not target.hostname
                or target.username or target.password or target.query or target.fragment
                or (target.scheme == "http" and target.hostname not in ("localhost", "127.0.0.1", "::1"))):
            raise ValueError("FINPILOT_API_URL must use HTTPS, or HTTP on localhost for development.")
        if not token.startswith("fp_mcp_") or any(ch in token for ch in ("\r", "\n")):
            raise ValueError("A FinPilot MCP read token is required.")
        self.client = httpx.AsyncClient(base_url=url.rstrip("/"),
            headers={"Authorization": "Bearer " + token}, transport=transport,
            timeout=httpx.Timeout(15), follow_redirects=False, trust_env=False)

    async def request(self, method, path, payload=None):
        try:
            async with self.client.stream(method, path, json=payload) as response:
                response.raise_for_status()
                data = bytearray()
                async for part in response.aiter_bytes():
                    data.extend(part)
                    if len(data) > 1024 * 1024:
                        raise ValueError("Response too large")
            return json.loads(data)
        except Exception:
            raise ValueError("FinPilot could not complete the read. Check the token, connection, and tool inputs.") from None

    async def tools(self):
        return (await self.request("GET", "/api/mcp/export/tools"))["tools"]

    async def call(self, name, arguments):
        return await self.request("POST", "/api/mcp/export/call", {"name": name, "arguments": arguments})

    async def close(self):
        await self.client.aclose()


def create_server(api):
    server = Server("finpilot", version="1.0.0", instructions=
        "Read-only financial calculations for the authenticated user's FinPilot workspace. "
        "Data is a cached workspace snapshot; no tool can change financial records or move money.")

    @server.list_tools()
    async def list_tools():
        return [Tool(**item, annotations=ToolAnnotations(readOnlyHint=True,
            destructiveHint=False, openWorldHint=False)) for item in await api.tools()]

    @server.call_tool()
    async def call_tool(name, arguments):
        try:
            result = await api.call(name, arguments)
            failed = bool(result.get("result", {}).get("error"))
            return CallToolResult(content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False))],
                                  structuredContent=result, isError=failed)
        except Exception:
            return CallToolResult(content=[TextContent(type="text",
                text="FinPilot could not complete this read-only request. Check the connection and tool inputs.")], isError=True)

    return server


async def main():
    api = FinPilotAPI(os.getenv("FINPILOT_API_URL", "http://127.0.0.1:8100"),
                     os.getenv("FINPILOT_MCP_TOKEN", ""))
    server = create_server(api)
    try:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    finally:
        await api.close()


if __name__ == "__main__":
    asyncio.run(main())
