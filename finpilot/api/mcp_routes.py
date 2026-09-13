"""User-authorized MCP imports and scoped read-only export for stdio clients."""
from contextlib import contextmanager
from datetime import timedelta, timezone
from threading import BoundedSemaphore
import json
import secrets
import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool

from .dependencies import P, R, same_origin
from .workspace import _encode
from ..integrations.mcp_client import (MCPClient, MCPError, configured_servers,
                                       encrypt_token, decrypt_token, cipher)
from ..persistence.database import MembershipRow, UserRow, utcnow
from ..persistence.mcp_models import MCPConnectionRow, MCPTokenRow
from ..services.auth import Principal, digest

router = APIRouter(prefix="/api/mcp", tags=["MCP"])
_REMOTE_SLOTS = BoundedSemaphore(8)


@contextmanager
def remote_slot():
    if not _REMOTE_SLOTS.acquire(blocking=False):
        raise HTTPException(429, "MCP connections are busy. Please try again shortly.")
    try:
        yield
    finally:
        _REMOTE_SLOTS.release()


class TokenBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    expires_days: int = Field(30, ge=1, le=30)


class ConnectionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    server_id: str = Field(min_length=1, max_length=80)
    name: str | None = Field(None, min_length=1, max_length=100)
    token: str = Field("", max_length=4000, repr=False)


class ImportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_name: str | None = Field(None, max_length=128)
    arguments: dict = Field(default_factory=dict)
    resource_uri: str | None = Field(None, max_length=2000)
    title: str | None = Field(None, min_length=1, max_length=150)

    @model_validator(mode="after")
    def one_source(self):
        if bool(self.tool_name) == bool(self.resource_uri):
            raise ValueError("Choose exactly one MCP tool or resource.")
        if self.resource_uri and self.arguments:
            raise ValueError("Resource imports do not accept tool arguments.")
        if len(json.dumps(self.arguments)) > 16000:
            raise ValueError("MCP tool inputs exceed the allowed size.")
        return self


class CallBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    arguments: dict = Field(default_factory=dict)


def scope(model, p):
    return (model.household_id == p.household_id, model.user_id == p.user_id)


def owned(session, model, p, row_id):
    row = session.execute(select(model).where(model.id == row_id, *scope(model, p))).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "MCP item not found.")
    return row


def public(row):
    out = {"id": row.id, "name": row.name,
           "created_at": row.created_at.replace(tzinfo=timezone.utc).isoformat()}
    if isinstance(row, MCPTokenRow):
        out.update(expires_at=row.expires_at.replace(tzinfo=timezone.utc).isoformat(), scope="finance:read")
    else:
        out["server_id"] = row.server_id
    return out


def servers(request):
    try:
        factory = getattr(request.app.state, "mcp_servers_factory", configured_servers)
        return factory()
    except MCPError as exc:
        raise HTTPException(503, str(exc)) from None


def bridge_setup(request):
    # Hosted origin is operator-controlled; local Host is checked by middleware.
    return {"module": "finpilot.integrations.mcp_server",
            "api_url": request.app.state.public_origin or str(request.base_url).rstrip("/")}


def import_setup(approved):
    try:
        cipher()
        credential_storage_ready = True
    except MCPError:
        credential_storage_ready = False
    configured = bool(approved)
    if not configured:
        message = "Your workspace operator has not enabled external MCP sources yet."
    elif not credential_storage_ready:
        message = "Your workspace operator must finish secure credential storage before sources can be connected."
    else:
        message = "You can connect an approved source. Its availability is checked when you browse its data."
    return {"configured": configured, "credential_storage_ready": credential_storage_ready,
            "ready": configured and credential_storage_ready,
            "server_count": len(approved), "message": message}


@router.get("/setup")
def setup(request: Request, r: R, p: P):
    r.read(p)
    try:
        imports = import_setup(servers(request))
    except HTTPException:
        imports = {"configured": False, "credential_storage_ready": False,
                   "ready": False, "server_count": 0,
                   "message": "Your workspace operator must correct the external MCP source configuration."}
    return {"export": {"available": True, "transport": "stdio", **bridge_setup(request)},
            "imports": imports}


@router.get("/tokens")
def tokens(r: R, p: P):
    r.read(p)
    with r.db.sessions() as session:
        rows = session.execute(select(MCPTokenRow).where(*scope(MCPTokenRow, p))
            .order_by(MCPTokenRow.created_at.desc())).scalars()
        return {"tokens": [public(row) for row in rows]}


@router.post("/tokens", status_code=201)
def create_token(body: TokenBody, request: Request, r: R, p: P):
    r.read(p)
    r.auth.throttle("mcp-token:" + p.user_id, limit=10, seconds=60)
    token = "fp_mcp_" + secrets.token_urlsafe(36)
    with r.db.sessions.begin() as session:
        if session.scalar(select(func.count()).select_from(MCPTokenRow).where(*scope(MCPTokenRow, p))) >= 20:
            raise HTTPException(409, "Revoke an existing MCP token before creating another.")
        row = MCPTokenRow(id="mct_" + uuid.uuid4().hex, household_id=p.household_id,
            user_id=p.user_id, token_hash=digest(token), name=body.name.strip(),
            created_at=utcnow(), expires_at=utcnow() + timedelta(days=body.expires_days))
        session.add(row)
        session.flush()
        grant = public(row)
    return {"token": token, "grant": grant, "bridge": bridge_setup(request),
            "command": "python -m finpilot.integrations.mcp_server",
            "environment": ["FINPILOT_API_URL", "FINPILOT_MCP_TOKEN"],
            "scope": "Read financial data and calculations in your workspace. Cannot change data or move money."}


@router.delete("/tokens/{token_id}")
def revoke_token(token_id: str, r: R, p: P):
    r.read(p)
    with r.db.sessions.begin() as session:
        from ..services.private_commands import delete_private
        delete_private(session,p,"revoke_mcp_access",token_id)
    return {"deleted": True}


@router.get("/connections")
def connections(request: Request, r: R, p: P):
    r.read(p)
    approved = servers(request)
    with r.db.sessions() as session:
        rows = session.execute(select(MCPConnectionRow).where(*scope(MCPConnectionRow, p))
            .order_by(MCPConnectionRow.created_at.desc())).scalars()
        return {"servers": [config.public() for config in approved.values()],
                "connections": [public(row) for row in rows], "configured": bool(approved),
                "setup": import_setup(approved)}


@router.post("/connections", status_code=201)
def create_connection(body: ConnectionBody, request: Request, r: R, p: P):
    r.read(p)
    r.auth.throttle("mcp-connect:" + p.user_id, limit=10, seconds=60)
    config = servers(request).get(body.server_id)
    if config is None:
        raise HTTPException(422, "Choose a server approved by the operator.")
    if any(ch in body.token for ch in ("\r", "\n")):
        raise HTTPException(422, "The MCP token contains invalid characters.")
    try:
        ciphertext = encrypt_token(body.token, config.url)
    except MCPError as exc:
        raise HTTPException(503, str(exc)) from None
    with r.db.sessions.begin() as session:
        if session.scalar(select(func.count()).select_from(MCPConnectionRow).where(*scope(MCPConnectionRow, p))) >= 10:
            raise HTTPException(409, "Remove an existing MCP connection before adding another.")
        row = MCPConnectionRow(id="mcc_" + uuid.uuid4().hex, household_id=p.household_id,
            user_id=p.user_id, server_id=config.id, name=(body.name or config.name).strip(),
            encrypted_token=ciphertext, created_at=utcnow())
        session.add(row)
        session.flush()
        result = public(row)
    return {"connection": result}


@router.delete("/connections/{connection_id}")
def delete_connection(connection_id: str, r: R, p: P):
    r.read(p)
    with r.db.sessions.begin() as session:
        from ..services.private_commands import delete_private
        delete_private(session,p,"disconnect_mcp",connection_id)
    return {"deleted": True}


def connection_client(request, r, p, connection_id):
    r.read(p)
    r.auth.throttle("mcp-read:" + p.user_id, limit=20, seconds=60)
    with r.db.sessions() as session:
        row = owned(session, MCPConnectionRow, p, connection_id)
        server_id, encrypted = row.server_id, row.encrypted_token
    config = servers(request).get(server_id)
    if config is None:
        raise HTTPException(409, "This MCP server is no longer approved by the operator.")
    try:
        token = decrypt_token(encrypted, config.url)
        factory = getattr(request.app.state, "mcp_client_factory", MCPClient)
        return factory(config, token), config
    except MCPError as exc:
        raise HTTPException(503, str(exc)) from None


@router.get("/connections/{connection_id}/catalog")
async def catalog(connection_id: str, request: Request, r: R, p: P):
    with remote_slot():
        client, _ = await run_in_threadpool(connection_client, request, r, p, connection_id)
        try:
            return await client.catalog()
        except MCPError as exc:
            raise HTTPException(502, str(exc)) from None


@router.post("/connections/{connection_id}/import")
async def import_result(connection_id: str, body: ImportBody, request: Request, r: R, p: P):
    with remote_slot():
        client, config = await run_in_threadpool(connection_client, request, r, p, connection_id)
        try:
            text = await client.read(tool_name=body.tool_name, arguments=body.arguments, resource_uri=body.resource_uri)
        except MCPError as exc:
            raise HTTPException(502, str(exc)) from None
        return await run_in_threadpool(save_import, r, p, connection_id, config, body, text)


def save_import(r, p, connection_id, config, body, text):
    # Connection may be revoked while a remote request is running.
    with r.db.sessions() as session:
        owned(session, MCPConnectionRow, p, connection_id)
    from ..services.documents import DocumentService
    result = DocumentService(r.db).ingest_text(p, body.title or f"{config.name}: {body.tool_name or 'resource'}",
        text, source_type="mcp", source_metadata={"server_id": config.id,
        "server_name": config.name, "connection_id": connection_id,
        "tool_name": body.tool_name, "resource_uri": body.resource_uri,
        "imported_at": utcnow().isoformat(), "trust": "untrusted_external_content"})
    return {**result, "imported": True}


def export_principal(request, r):
    same_origin(request)
    value = request.headers.get("authorization", "")
    if not value.startswith("Bearer fp_mcp_") or len(value) > 200:
        raise HTTPException(401, "A FinPilot MCP read token is required.")
    with r.db.sessions() as session:
        record = session.execute(select(MCPTokenRow, MembershipRow, UserRow)
            .join(MembershipRow, (MembershipRow.user_id == MCPTokenRow.user_id) &
                  (MembershipRow.household_id == MCPTokenRow.household_id))
            .join(UserRow, UserRow.id == MCPTokenRow.user_id)
            .where(MCPTokenRow.token_hash == digest(value[7:]), MCPTokenRow.expires_at > utcnow())).first()
        if record is None:
            raise HTTPException(401, "This MCP token is expired or revoked.")
        grant, membership, user = record
        principal = Principal(user.id, grant.household_id, membership.role, user.name, user.email)
    r.auth.throttle("mcp-export:" + principal.user_id, limit=60, seconds=60)
    return principal


@router.get("/export/tools")
def export_tools(request: Request, r: R):
    principal = export_principal(request, r)
    registry = r.read(principal).registry
    return {"tools": [{"name": schema["function"]["name"],
                       "description": schema["function"]["description"],
                       "inputSchema": schema["function"]["parameters"]}
                      for schema in registry.openai_schemas(read_only=True)]}


@router.post("/export/call")
def export_call(body: CallBody, request: Request, r: R):
    principal = export_principal(request, r)
    ctx = r.read(principal)
    spec = ctx.registry.spec(body.name)
    if spec is None or not spec.read_only:
        raise HTTPException(403, "Only approved read-only financial tools are available through MCP.")
    if len(json.dumps(body.arguments)) > 16000:
        raise HTTPException(422, "MCP arguments exceed the allowed size.")
    error = ctx.registry.validate_arguments(body.name, body.arguments)
    if error:
        raise HTTPException(422, error)
    result = _encode(ctx.registry.call(body.name, body.arguments))
    if len(json.dumps(result)) > 250000:
        raise HTTPException(422, "Narrow this request to return less financial data.")
    return {"result": result, "revision": ctx.revision, "read_only": True}
