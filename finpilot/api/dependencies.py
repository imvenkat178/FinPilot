"""HTTP dependencies: verified session identity is the only tenant selector."""
from typing import Annotated
from fastapi import Depends, HTTPException, Request
from ..runtime import Runtime
from ..services.auth import Principal, csrf_for
import hmac


def runtime(request: Request) -> Runtime:
    value = request.app.state.runtime
    value.initialize()
    return value


def same_origin(request: Request):
    origin = request.headers.get("origin")
    expected = request.app.state.public_origin or str(request.base_url).rstrip("/")
    if origin and origin.rstrip("/") != expected.rstrip("/"):
        raise HTTPException(403, "This request did not originate from FinPilot.")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Cross-site requests are not allowed.")


def current_principal(request: Request, service: Annotated[Runtime, Depends(runtime)]) -> Principal:
    principal = service.auth.authenticate(request.cookies.get("finpilot_session", ""))
    requested = request.query_params.get("household")
    if requested and requested != principal.household_id:
        raise HTTPException(404, "Workspace not found.")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        same_origin(request)
        provided = request.headers.get("x-csrf-token", "")
        if not hmac.compare_digest(provided, csrf_for(principal.token)):
            raise HTTPException(403, "Session verification failed. Refresh and try again.")
    return principal


R = Annotated[Runtime, Depends(runtime)]
P = Annotated[Principal, Depends(current_principal)]


def revision(request: Request):
    value = request.headers.get("if-match")
    if value is None:
        return None
    try:
        return int(value.strip('"'))
    except ValueError:
        raise HTTPException(400, "If-Match must contain the workspace revision")
