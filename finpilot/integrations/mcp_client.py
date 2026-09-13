"""Bounded, operator-allowlisted Streamable HTTP MCP client.

Endpoint DNS is resolved and pinned before connecting. TLS SNI and Host remain
original; redirects, proxy environment, model sampling and elicitation are off.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
import ipaddress
import json
import logging
import os
import re
import socket
from threading import BoundedSemaphore
from urllib.parse import urlsplit

import anyio
from cryptography.fernet import Fernet, InvalidToken
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from . import mcp_logging  # noqa: F401 -- sanitize SDK diagnostics before first request

MAX_OUTPUT = 48000
MAX_WIRE_BYTES = 1024 * 1024
TIMEOUT_SECONDS = 15
_DNS_LIMITER = anyio.CapacityLimiter(8)
_DNS_ACTIVE = BoundedSemaphore(8)
logger = logging.getLogger(__name__)


class MCPError(ValueError):
    pass


@dataclass(frozen=True)
class ServerConfig:
    id: str
    name: str
    url: str
    read_tools: tuple[str, ...] = ()
    allow_resources: bool = False

    def public(self):
        return {"id": self.id, "name": self.name, "url": self.url,
                "read_tools": list(self.read_tools), "allow_resources": self.allow_resources}


def configured_servers():
    try:
        raw = json.loads(os.getenv("FINPILOT_MCP_SERVERS", "[]"))
        if not isinstance(raw, list) or len(raw) > 20:
            raise ValueError()
        result = {}
        for value in raw:
            sid, name, url = value["id"], value["name"], value["url"]
            parsed = urlsplit(url)
            if (not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", sid) or sid in result
                    or not isinstance(name, str) or not 1 <= len(name) <= 100
                    or parsed.scheme != "https" or not parsed.hostname or parsed.username
                    or parsed.password or parsed.fragment or parsed.query):
                raise ValueError()
            reads = value.get("read_tools", [])
            if not isinstance(reads, list) or len(reads) > 100 or any(
                    not isinstance(t, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,128}", t) for t in reads):
                raise ValueError()
            result[sid] = ServerConfig(sid, name, url, tuple(reads), value.get("allow_resources") is True)
        return result
    except (ValueError, TypeError, KeyError, AttributeError):
        raise MCPError("The server MCP allowlist is invalid; ask the operator to check it.") from None


def cipher():
    try:
        return Fernet(os.environ["FINPILOT_TOKEN_KEY"].encode())
    except (KeyError, ValueError, TypeError):
        raise MCPError("MCP connections require a valid FINPILOT_TOKEN_KEY on the server.") from None


def canonical_endpoint(url):
    return str(httpx.URL(url))


def encrypt_token(token, endpoint):
    payload = json.dumps({"endpoint": canonical_endpoint(endpoint), "token": token})
    return cipher().encrypt(payload.encode()).decode()


def decrypt_token(value, endpoint):
    try:
        payload = json.loads(cipher().decrypt(value.encode()).decode())
        if (not isinstance(payload, dict) or payload.get("endpoint") != canonical_endpoint(endpoint)
                or not isinstance(payload.get("token"), str)):
            raise MCPError("This MCP endpoint changed. Reconnect to authorize the new destination.")
        return payload["token"]
    except MCPError:
        raise
    except (InvalidToken, UnicodeError, ValueError, TypeError):
        raise MCPError("This MCP connection must be reconnected to verify its credential destination.") from None


class LimitedStream(httpx.AsyncByteStream):
    def __init__(self, stream):
        self.stream = stream

    async def __aiter__(self):
        count = 0
        async for chunk in self.stream:
            count += len(chunk)
            if count > MAX_WIRE_BYTES:
                raise MCPError("The MCP response exceeds the one megabyte transport limit.")
            yield chunk

    async def aclose(self):
        await self.stream.aclose()


class PinnedTransport(httpx.AsyncBaseTransport):
    def __init__(self, endpoint, address):
        self.endpoint = httpx.URL(endpoint)
        self.address = address
        self.transport = httpx.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request):
        if request.url != self.endpoint:
            raise MCPError("The MCP server requested an unapproved endpoint.")
        request.headers["Host"] = self.endpoint.netloc.decode()
        request.extensions["sni_hostname"] = self.endpoint.host
        request.url = request.url.copy_with(host=self.address)
        response = await self.transport.handle_async_request(request)
        if response.headers.get("content-encoding", "identity").lower() != "identity":
            await response.aclose()
            raise MCPError("Compressed MCP responses are not accepted by this bounded transport.")
        response.stream = LimitedStream(response.stream)
        return response

    async def aclose(self):
        await self.transport.aclose()


async def public_address(url):
    target = httpx.URL(url)
    try:
        def resolve():
            # Cancellation releases the async limiter before an abandoned thread
            # finishes. This second gate bounds actual getaddrinfo calls too.
            if not _DNS_ACTIVE.acquire(blocking=False):
                raise MCPError("MCP DNS resolution is busy. Please retry shortly.")
            try:
                return socket.getaddrinfo(target.host, target.port or 443, type=socket.SOCK_STREAM)
            finally:
                _DNS_ACTIVE.release()
        records = await anyio.to_thread.run_sync(resolve, abandon_on_cancel=True, limiter=_DNS_LIMITER)
        addresses = {record[4][0] for record in records}
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise MCPError("MCP endpoints must resolve exclusively to public network addresses.")
        return sorted(addresses)[0]
    except MCPError:
        raise
    except (socket.gaierror, ValueError):
        raise MCPError("The MCP endpoint could not be resolved safely.") from None


class MCPClient:
    def __init__(self, config, token="", *, transport=None):
        self.config, self.token, self.transport = config, token, transport

    @asynccontextmanager
    async def session(self):
        try:
            with anyio.fail_after(TIMEOUT_SECONDS):
                transport = self.transport or PinnedTransport(self.config.url, await public_address(self.config.url))
                headers = {"Accept-Encoding": "identity"}
                if self.token:
                    headers["Authorization"] = "Bearer " + self.token
                async with httpx.AsyncClient(transport=transport, headers=headers,
                        timeout=httpx.Timeout(TIMEOUT_SECONDS), follow_redirects=False,
                        trust_env=False) as http:
                    async with streamable_http_client(self.config.url, http_client=http) as (read, write, _):
                        async with ClientSession(read, write,
                                read_timeout_seconds=timedelta(seconds=TIMEOUT_SECONDS)) as session:
                            await session.initialize()
                            yield session
        except MCPError:
            raise
        except Exception:
            # The SDK's source-scoped log filter removes raw payloads before
            # handlers see them. Keep our own diagnostic free of exception text.
            logger.warning("MCP request failed; provider payload and exception details withheld.")
            raise MCPError("The MCP server could not complete this request. Check its authorization and availability.") from None

    async def catalog(self):
        async with self.session() as session:
            tools = []
            cursor = None
            for _ in range(5):
                page = await session.list_tools(cursor=cursor)
                tools.extend({"name": t.name, "description": (t.description or "")[:1000],
                              "inputSchema": t.inputSchema} for t in page.tools
                             if t.name in self.config.read_tools)
                cursor = page.nextCursor
                if not cursor:
                    break
            resources = []
            if self.config.allow_resources:
                cursor = None
                for _ in range(5):
                    page = await session.list_resources(cursor=cursor)
                    resources.extend({"uri": str(t.uri), "name": t.name,
                                      "description": (t.description or "")[:1000]} for t in page.resources)
                    cursor = page.nextCursor
                    if not cursor:
                        break
            payload = {"tools": tools[:100], "resources": resources[:100]}
            if len(json.dumps(payload)) > MAX_OUTPUT:
                raise MCPError("The MCP catalog is too large. Ask the operator to narrow its read tool allowlist.")
            return payload

    async def read(self, *, tool_name=None, arguments=None, resource_uri=None):
        if bool(tool_name) == bool(resource_uri):
            raise MCPError("Choose exactly one MCP tool or resource.")
        if tool_name and tool_name not in self.config.read_tools:
            raise MCPError("This MCP tool is not approved for read access.")
        if resource_uri and not self.config.allow_resources:
            raise MCPError("Resource access is not enabled for this MCP server.")
        if len(json.dumps(arguments or {})) > 16000:
            raise MCPError("MCP tool inputs exceed the allowed size.")
        async with self.session() as session:
            if tool_name:
                result = await session.call_tool(tool_name, arguments or {})
                if result.isError:
                    raise MCPError("The MCP tool reported an error; no document was imported.")
                if result.structuredContent:
                    text = json.dumps(result.structuredContent, ensure_ascii=False)
                else:
                    text = "\n\n".join(block.text for block in result.content if block.type == "text")
            else:
                # A resource URI is an opaque MCP identifier. It is sent only to
                # this server and is never fetched by FinPilot as a URL.
                result = await session.read_resource(resource_uri)
                text = "\n\n".join(block.text for block in result.contents if hasattr(block, "text"))
            if not text.strip():
                raise MCPError("This MCP result contains no searchable text.")
            if len(text) > MAX_OUTPUT:
                raise MCPError("The MCP result exceeds 48,000 characters. Narrow the tool inputs and retry.")
            return text
