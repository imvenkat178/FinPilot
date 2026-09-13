"""Deterministic calculation and explicitly simulated execution endpoints."""
from decimal import Decimal
from datetime import date
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, ConfigDict
from ..models import AuthorizationMode, Mandate, now
from ..money import Money
from ..engine.allocator import AllocationEngine
from ..execution.engine import LegState
from ..services.workspace import WorkspaceService
from .dependencies import P, R, revision

router = APIRouter(prefix="/api", tags=["Financial workspace"])


@router.get("/overview")
def overview(p: P, r: R):
    return r.read(p).registry.get_money_overview()


@router.get("/liquidity")
def liquidity(p: P, r: R):
    return r.read(p).registry.get_liquidity_tiers()


@router.get("/coverage")
def coverage(p: P, r: R):
    return r.read(p).registry.get_deposit_coverage()


@router.get("/bills")
def bills(p: P, r: R, days: int = Query(30, ge=1, le=366)):
    return r.read(p).registry.get_upcoming_obligations(days=days)


@router.get("/forecast")
def forecast(p: P, r: R, account_id: Optional[str] = None, days: int = Query(45, ge=1, le=366)):
    return r.read(p).registry.get_cash_forecast(account_id=account_id, days=days)


@router.get("/allowance")
def allowance(p: P, r: R, days: int = Query(14, ge=1, le=366)):
    return r.read(p).registry.get_spending_allowance(days=days)


@router.get("/buffer")
def buffer(p: P, r: R, account_id: Optional[str] = None):
    return r.read(p).registry.get_buffer(account_id=account_id)


@router.get("/connections")
def connections(p: P, r: R):
    return r.read(p).registry.get_account_connections()


@router.get("/recurring/activity")
def recurring_activity(p: P, r: R, horizon_days: int = Query(60, ge=1, le=366)):
    return r.read(p).registry.get_recurring_activity(horizon_days=horizon_days)


@router.get("/plan")
def plan(p: P, r: R, year: Optional[int] = Query(None, ge=1900, le=2200),
         month: Optional[int] = Query(None, ge=1, le=12)):
    return r.read(p).registry.get_paycheck_plan(year=year, month=month)


@router.get("/policies")
def policies(p: P, r: R):
    return r.read(p).registry.get_automation_status()


@router.post("/policies")
def create_policy(body: dict, request: Request, p: P, r: R):
    # Compatibility for the existing fixed-rule form; new forms use /manage.
    body = dict(body)
    for key in ("amount", "percent"):
        if key in body and isinstance(body[key], (int, float)):
            body[key] = str(body[key])
    with r.transaction(p, "policy.create", revision(request)) as ctx:
        result = WorkspaceService(ctx.household, ctx.tax).upsert("policies", body)
        policy = ctx.household.policies[result["id"]]
        result.update(created=policy.id, name=policy.name,
                      authorization="none — a saved rule has no payment authority")
    request.state.revision = ctx.revision
    return result


class AuthorizationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: AuthorizationMode = AuthorizationMode.STANDING
    per_run_cap: Decimal = Field(gt=0, le=Decimal("1000000000"), allow_inf_nan=False)


@router.post("/policies/{policy_id}/authorize")
def authorize_policy(policy_id: str, body: AuthorizationIn, request: Request, p: P, r: R):
    from ..services.commands import execute
    with r.transaction(p, 'policy.authorize', revision(request)) as ctx:
        ctx.actor_id = p.user_id
        result = execute(ctx, 'authorize_rule', {"record_id":policy_id,"mode":body.mode.value,"per_run_cap":str(body.per_run_cap)})
    request.state.revision = ctx.revision
    return result


@router.post("/policies/{policy_id}/pause")
def pause_policy(policy_id: str, request: Request, p: P, r: R, paused: bool = True):
    from ..services.commands import execute
    with r.transaction(p, 'policy.pause' if paused else 'policy.resume', revision(request)) as ctx:
        ctx.actor_id = p.user_id
        result = execute(ctx, ("pause_rule" if paused else "resume_rule"), {"record_id":policy_id})
    request.state.revision = ctx.revision
    return result


@router.post("/policies/{policy_id}/skip-next")
def skip_next_policy(policy_id: str, request: Request, p: P, r: R):
    from ..services.commands import execute
    with r.transaction(p, 'policy.skip', revision(request)) as ctx:
        ctx.actor_id = p.user_id
        result = execute(ctx, 'skip_rule', {"record_id":policy_id})
    request.state.revision = ctx.revision
    return result


@router.post("/bills/{bill_id}/pay-once")
def pay_bill_once(bill_id: str, request: Request, p: P, r: R, occurrence_date: date | None = None):
    from ..services.commands import execute
    with r.transaction(p, 'payment.draft', revision(request)) as ctx:
        ctx.actor_id = p.user_id
        result = execute(ctx, 'draft_bill_payment', {"record_id":bill_id,**({"occurrence_date":occurrence_date.isoformat()} if occurrence_date else {})})
    request.state.revision = ctx.revision
    return result


@router.get("/debt/compare")
def debt_compare(p: P, r: R, extra_payment: Optional[Decimal] = Query(None, ge=0, le=1000000000)):
    return r.read(p).registry.compare_debt_strategies(extra_payment=extra_payment)


@router.get("/debt/what-if")
def debt_what_if(p: P, r: R, amount: Decimal = Query(..., gt=0, le=1000000000)):
    return r.read(p).registry.what_if_extra_payment(amount=amount)


@router.get("/mortgage/scenarios")
def mortgage(p: P, r: R, lump_sum: Optional[Decimal] = Query(None, ge=0, le=1000000000),
             extra_monthly: Optional[Decimal] = Query(None, ge=0, le=1000000000)):
    return r.read(p).registry.get_mortgage_scenarios(lump_sum=lump_sum, extra_monthly=extra_monthly)


@router.get("/mortgage/biweekly")
def biweekly(p: P, r: R, program_fee_per_year: Optional[Decimal] = Query(None, ge=0, le=1000000)):
    return r.read(p).registry.compare_biweekly_mortgage(program_fee_per_year=program_fee_per_year)


class CardQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: Decimal = Field(gt=0, le=1000000000, allow_inf_nan=False)
    category: str = Field("base", max_length=100)
    merchant: str = Field("", max_length=200)
    channel: str = Field("in_person", max_length=40)
    foreign: bool = False
    mcc_certain: bool = True
    processing_fee_rate: Decimal = Field(Decimal(0), ge=0, le=1, allow_inf_nan=False)


@router.post("/cards/choose")
def choose_card(body: CardQuery, p: P, r: R):
    return r.read(p).registry.choose_card(**body.model_dump())


@router.get("/cards/utilization")
def card_utilization(p: P, r: R, card_id: Optional[str] = None,
                     payment: Optional[Decimal] = Query(None, ge=0, le=1000000000)):
    return r.read(p).registry.check_utilization_timing(card_id=card_id, payment=payment)


@router.get("/tax/net-benefit")
def net_benefit(p: P, r: R, amount: Decimal = Query(..., gt=0, le=1000000000),
                horizon_days: int = Query(365, ge=1, le=3650)):
    return r.read(p).registry.compare_savings_vs_debt(amount=amount, horizon_days=horizon_days)


@router.get("/tax/profile")
def tax_profile(p: P, r: R):
    return r.read(p).tax.to_json()


@router.post("/execution/build")
def execution_build(request: Request, p: P, r: R,
                    year: Optional[int] = Query(None, ge=1900, le=2200),
                    month: Optional[int] = Query(None, ge=1, le=12),
                    income_event_id: str | None = Query(None, max_length=100)):
    from ..services.commands import execute
    with r.transaction(p, 'payment.build', revision(request)) as ctx:
        ctx.actor_id = p.user_id
        result = execute(ctx, 'build_payment_drafts', {k:v for k,v in {"year":year,"month":month,"income_event_id":income_event_id}.items() if v is not None})
    request.state.revision = ctx.revision
    return result


class SimulationIn(BaseModel):
    confirm_simulation: bool


@router.post("/execution/run/{group_id}")
def execution_run(group_id: str, body: SimulationIn, request: Request, p: P, r: R):
    from ..services.commands import execute
    if not body.confirm_simulation:
        raise HTTPException(422, "Review and confirm the simulated payment first.")
    if not r.read(p).household.payment_sandbox:
        raise HTTPException(403, "Payment simulation is available only in sample workspaces.")
    with r.transaction(p, 'payment.simulate', revision(request)) as ctx:
        ctx.actor_id = p.user_id
        result = execute(ctx, 'simulate_payment', {"record_id":group_id})
    request.state.revision = ctx.revision
    return result


@router.post("/execution/recover/{leg_id}")
def execution_recover(leg_id: str, request: Request, p: P, r: R):
    from ..services.commands import execute
    if not r.read(p).household.payment_sandbox:
        raise HTTPException(403, "Payment simulation is available only in sample workspaces.")
    with r.transaction(p, 'payment.recover', revision(request)) as ctx:
        ctx.actor_id = p.user_id
        result = execute(ctx, 'recover_payment', {"record_id":leg_id})
    request.state.revision = ctx.revision
    return result


@router.post("/execution/pause-all")
def pause_all(request: Request, p: P, r: R):
    from ..services.commands import execute
    with r.transaction(p, 'execution.pause', revision(request)) as ctx:
        ctx.actor_id = p.user_id
        result = execute(ctx, 'pause_execution', {})
    request.state.revision = ctx.revision
    return result


@router.post("/execution/resume")
def resume_all(request: Request, p: P, r: R):
    from ..services.commands import execute
    with r.transaction(p, 'execution.resume', revision(request)) as ctx:
        ctx.actor_id = p.user_id
        result = execute(ctx, 'resume_execution', {})
    request.state.revision = ctx.revision
    return result


@router.get("/execution/groups")
def execution_groups(p: P, r: R):
    ctx = r.read(p)
    return {"groups": [g.to_json() for g in ctx.execution.groups.values()],
            "globally_paused": ctx.execution.paused,
            "reservations": {k: str(v) for k, v in ctx.execution.reservations.items()},
            "provider": "simulation", "revision": ctx.revision}


@router.get("/execution/groups/{group_id}")
def execution_group(group_id: str, request: Request, p: P, r: R):
    ctx = r.read(p)
    group = ctx.execution.groups.get(group_id)
    if group is None:
        raise HTTPException(404, "Payment group not found.")
    request.state.revision = ctx.revision
    return {"group": group.to_json(), "preflight": {leg.id: ctx.execution.preflight(leg).to_json()
        for leg in group.legs}, "globally_paused": ctx.execution.paused, "provider": "simulation", "revision": ctx.revision}
