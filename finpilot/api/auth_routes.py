"""Sign-up, sign-in and session lifecycle."""
from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field, ConfigDict
from ..services.auth import SESSION_SECONDS, csrf_for
from .dependencies import R, P, same_origin

router = APIRouter(prefix="/api/auth", tags=["Identity"])


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class Registration(Credentials):
    name: str = Field(min_length=1, max_length=100)
    sample_data: bool = False


def identity(p):
    return {"user": {"id": p.user_id, "name": p.name, "email": p.email},
            "household_id": p.household_id, "role": p.role, "csrf_token": csrf_for(p.token)}


def set_cookie(response, request, principal):
    response.set_cookie("finpilot_session", principal.token, httponly=True,
        secure=request.app.state.secure_cookies, samesite="lax", path="/", max_age=SESSION_SECONDS)


@router.post("/register", status_code=201)
def register(body: Registration, request: Request, response: Response, r: R):
    same_origin(request)
    r.auth.throttle("register:" + (request.client.host if request.client else "unknown"), limit=8, seconds=300)
    principal = r.auth.register(body.name, body.email, body.password, body.sample_data)
    set_cookie(response, request, principal)
    return identity(principal)


@router.post("/login")
def login(body: Credentials, request: Request, response: Response, r: R):
    same_origin(request)
    r.auth.throttle("login-ip:" + (request.client.host if request.client else "unknown"), limit=30, seconds=300)
    r.auth.throttle("login-email:" + body.email.strip().casefold(), limit=10, seconds=300)
    principal = r.auth.login(body.email, body.password)
    set_cookie(response, request, principal)
    return identity(principal)


@router.get("/me")
def me(p: P):
    return identity(p)


@router.post("/logout")
def logout(request: Request, response: Response, p: P, r: R):
    r.auth.logout(p.token)
    response.delete_cookie("finpilot_session", path="/", secure=request.app.state.secure_cookies,
                           httponly=True, samesite="lax")
    return {"signed_out": True}
