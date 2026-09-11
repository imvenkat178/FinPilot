"""FastAPI surface.

One process holds the household, the calculators, the execution engine and the
assistant. Every endpoint returns the calculation's own inputs, assumptions and
confidence alongside its result, so a caller can always show the evidence.
"""
from __future__ import annotations

import json
import os
from datetime import date
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from ..ai.graph import FinanceAgent, ToolChoice, enable_langsmith
from ..ai.llm import LocalLLM, autodetect
from ..ai.tools import ToolRegistry
from ..engine.allocator import AllocationEngine
from ..engine.tax import TaxProfile
from ..execution.engine import (ExecutionEngine, LegState, ProviderFault,
                                SimulatedProvider)
from ..models import AuthorizationMode, Household
from ..money import Money
from ..seed.demo import demo_household, hnw_household

WEB = Path(__file__).resolve().parent.parent / "web"


class AppState:
    def __init__(self):
        self.households: dict[str, Household] = {
            "demo": demo_household(), "hnw": hnw_household()}
        self.tax = TaxProfile(federal_marginal=D("0.24"), state_marginal=D("0.05"),
                              itemizes=False, verified=False)
        self.provider = SimulatedProvider()
        self.execution = ExecutionEngine(self.households["demo"], self.provider)
        self.registry = ToolRegistry(self.households["demo"], self.tax, self.execution)
        self.llm = LocalLLM(autodetect())
        self.agent = FinanceAgent(self.registry, self.llm)
        self.langsmith = enable_langsmith()

    def reset(self) -> None:
        self.__init__()

    def use(self, name: str) -> ToolRegistry:
        if name == "demo":
            return self.registry
        hh = self.households.get(name)
        if hh is None:
            raise HTTPException(404, f"unknown household {name}")
        return ToolRegistry(hh, self.tax)


state = AppState()
app = FastAPI(title="FinPilot", version="1.0",
              description="AI personal finance application — reference implementation "
                          "of the Version 3 specification with the Version 4 additions.")


def ok(payload: Any) -> JSONResponse:
    return JSONResponse(content=json.loads(json.dumps(payload, default=str)))


# ---------------------------------------------------------------------------
# health and model
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return ok({"status": "ok", "as_of": str(date.today()),
               "households": list(state.households),
               "tools": state.registry.names(),
               "langsmith": state.langsmith})


@app.get("/api/llm/health")
def llm_health():
    return ok({**state.llm.health(), "config": state.llm.config.to_json()})


class LLMConfigIn(BaseModel):
    base_url: str = Field(..., examples=["http://localhost:11434/v1"])
    model: str = Field(..., examples=["llama3.2"])
    temperature: float = 0.1


@app.post("/api/llm/configure")
def llm_configure(cfg: LLMConfigIn):
    """Point the assistant at your local Llama without restarting."""
    os.environ["FINPILOT_LLM_BASE_URL"] = cfg.base_url.rstrip("/")
    os.environ["FINPILOT_LLM_MODEL"] = cfg.model
    state.llm = LocalLLM(autodetect())
    state.llm.config.temperature = cfg.temperature
    state.agent = FinanceAgent(state.registry, state.llm)
    return ok({"configured": state.llm.config.to_json(), **state.llm.health()})


# ---------------------------------------------------------------------------
# money
# ---------------------------------------------------------------------------

@app.get("/api/overview")
def overview(household: str = "demo"):
    return ok(state.use(household).get_money_overview())


@app.get("/api/liquidity")
def liquidity(household: str = "demo"):
    return ok(state.use(household).get_liquidity_tiers())


@app.get("/api/coverage")
def coverage(household: str = "demo"):
    return ok(state.use(household).get_deposit_coverage())


@app.get("/api/bills")
def bills(days: int = 30, household: str = "demo"):
    return ok(state.use(household).get_upcoming_obligations(days=days))


@app.get("/api/forecast")
def forecast(account_id: Optional[str] = None, days: int = 45,
             household: str = "demo"):
    return ok(state.use(household).get_cash_forecast(account_id=account_id, days=days))


@app.get("/api/allowance")
def allowance(days: int = 14, household: str = "demo"):
    return ok(state.use(household).get_spending_allowance(days=days))


@app.get("/api/buffer")
def buffer(account_id: Optional[str] = None, household: str = "demo"):
    return ok(state.use(household).get_buffer(account_id=account_id))


@app.get("/api/connections")
def connections(household: str = "demo"):
    return ok(state.use(household).get_account_connections())


@app.get("/api/recurring/activity")
def recurring_activity(horizon_days: int = 60, household: str = "demo"):
    return ok(state.use(household).get_recurring_activity(horizon_days=horizon_days))


# ---------------------------------------------------------------------------
# the paycheck plan -- the core of the product
# ---------------------------------------------------------------------------

@app.get("/api/plan")
def plan(year: Optional[int] = None, month: Optional[int] = None,
         household: str = "demo"):
    return ok(state.use(household).get_paycheck_plan(year=year, month=month))


class PolicyIn(BaseModel):
    name: str
    purpose: str = "goal"
    method: str = "fixed"
    amount: Optional[float] = None
    percent: Optional[float] = None
    percent_base: str = "net_paycheck"
    destination_account_id: str
    source_account_id: Optional[str] = None
    liability_id: Optional[str] = None
    priority: int = 100
    paused: bool = False


@app.get("/api/policies")
def policies(household: str = "demo"):
    return ok(state.use(household).get_automation_status())


@app.post("/api/policies")
def create_policy(p: PolicyIn, household: str = "demo"):
    """Draft a recurring rule. Section 21: saving a rule is not authorization —
    it is created without an active mandate and cannot move money until one
    is attached."""
    from ..models import (Mandate, PercentBase, PolicyMethod, PolicyPurpose,
                          RecurringPolicy)
    hh = state.households[household]
    if p.destination_account_id not in hh.accounts:
        raise HTTPException(400, "unknown destination account")
    cur = hh.base_currency
    pol = RecurringPolicy(
        name=p.name, purpose=PolicyPurpose(p.purpose), method=PolicyMethod(p.method),
        amount=Money(D(str(p.amount or 0)), cur),
        monthly_target=Money(D(str(p.amount)), cur) if p.amount else None,
        percent=D(str(p.percent or 0)), percent_base=PercentBase(p.percent_base),
        source_account_id=p.source_account_id or (hh.checking[0].id if hh.checking else ""),
        destination_account_id=p.destination_account_id,
        liability_id=p.liability_id, priority=p.priority, paused=p.paused,
        mandate=Mandate(mode=AuthorizationMode.EXPLORE))
    hh.policies[pol.id] = pol
    return ok({"created": pol.id, "name": pol.name,
               "authorization": "none — this rule is saved but cannot move money "
                                "until you authorize it",
               "next": f"POST /api/policies/{pol.id}/authorize"})


@app.post("/api/policies/{policy_id}/authorize")
def authorize_policy(policy_id: str, mode: str = "standing",
                     per_run_cap: Optional[float] = None,
                     household: str = "demo"):
    from ..models import now as _now
    hh = state.households[household]
    pol = hh.policies.get(policy_id)
    if pol is None:
        raise HTTPException(404, "unknown policy")
    pol.mandate.mode = AuthorizationMode(mode)
    pol.mandate.authorized_at = _now()
    pol.mandate.authorized_by = "user_primary"
    if per_run_cap:
        pol.mandate.per_run_cap = Money(D(str(per_run_cap)), hh.base_currency)
    return ok({"policy": policy_id, "mode": pol.mandate.mode.value,
               "active": pol.mandate.active,
               "per_run_cap": pol.mandate.per_run_cap.to_json()
               if pol.mandate.per_run_cap else None,
               "note": "A valid standing authorization runs ordinary eligible "
                       "occurrences without asking again. Changing the destination "
                       "or widening authority requires a new authorization."})


@app.post("/api/policies/{policy_id}/pause")
def pause_policy(policy_id: str, paused: bool = True, household: str = "demo"):
    hh = state.households[household]
    pol = hh.policies.get(policy_id)
    if pol is None:
        raise HTTPException(404, "unknown policy")
    pol.paused = paused
    return ok({"policy": policy_id, "paused": pol.paused})


@app.post("/api/policies/{policy_id}/skip-next")
def skip_next_policy(policy_id: str, household: str = "demo"):
    result = state.use(household).skip_next_occurrence(policy_id)
    if "error" in result:
        raise HTTPException(404, result["error"])
    return ok(result)


@app.post("/api/bills/{bill_id}/pay-once")
def pay_bill_once(bill_id: str, household: str = "demo"):
    result = state.use(household).pay_bill_once(bill_id)
    if "error" in result:
        raise HTTPException(404, result["error"])
    return ok(result)


# ---------------------------------------------------------------------------
# debt
# ---------------------------------------------------------------------------

@app.get("/api/debt/compare")
def debt_compare(extra_payment: Optional[float] = None,
                 worked_example: bool = False, household: str = "demo"):
    return ok(state.use(household).compare_debt_strategies(
        extra_payment=extra_payment, use_worked_example=worked_example))


@app.get("/api/debt/what-if")
def debt_what_if(amount: float = Query(..., gt=0), household: str = "demo"):
    return ok(state.use(household).what_if_extra_payment(amount=amount))


@app.get("/api/mortgage/scenarios")
def mortgage(lump_sum: Optional[float] = None, extra_monthly: Optional[float] = None,
             household: str = "demo"):
    return ok(state.use(household).get_mortgage_scenarios(
        lump_sum=lump_sum, extra_monthly=extra_monthly))


@app.get("/api/mortgage/biweekly")
def biweekly(program_fee_per_year: Optional[float] = None, household: str = "demo"):
    return ok(state.use(household).compare_biweekly_mortgage(
        program_fee_per_year=program_fee_per_year))


# ---------------------------------------------------------------------------
# cards
# ---------------------------------------------------------------------------

class CardQuery(BaseModel):
    amount: float
    category: str = "base"
    merchant: str = ""
    channel: str = "in_person"
    foreign: bool = False
    mcc_certain: bool = True
    processing_fee_rate: float = 0.0


@app.post("/api/cards/choose")
def choose_card(q: CardQuery, household: str = "demo"):
    return ok(state.use(household).choose_card(**q.model_dump()))


@app.get("/api/cards/utilization")
def card_utilization(card_id: Optional[str] = None, payment: Optional[float] = None,
                     household: str = "demo"):
    return ok(state.use(household).check_utilization_timing(
        card_id=card_id, payment=payment))


# ---------------------------------------------------------------------------
# tax
# ---------------------------------------------------------------------------

@app.get("/api/tax/net-benefit")
def net_benefit(amount: float = Query(..., gt=0), horizon_days: int = 365,
                household: str = "demo"):
    return ok(state.use(household).compare_savings_vs_debt(
        amount=amount, horizon_days=horizon_days))


@app.get("/api/tax/profile")
def tax_profile():
    return ok(state.tax.to_json())


# ---------------------------------------------------------------------------
# execution
# ---------------------------------------------------------------------------

@app.post("/api/execution/build")
def execution_build(year: Optional[int] = None, month: Optional[int] = None):
    """Turn this month's allocation into independently tracked payment legs."""
    hh = state.households["demo"]
    eng = AllocationEngine(hh)
    y = year or hh.as_of.year
    m = month or hh.as_of.month
    plan, runs = eng.allocate_month(y, m)
    groups = []
    for run in runs:
        grp = state.execution.build_group_from_allocation(
            run, label=f"Paycheck {run.pay_date}")
        groups.append(grp.to_json())
    return ok({"groups": groups,
               "note": "Legs are drafts. Each is checked immediately before "
                       "submission and runs only under a valid mandate."})


@app.post("/api/execution/run/{group_id}")
def execution_run(group_id: str, settle: bool = True):
    grp = state.execution.groups.get(group_id)
    if grp is None:
        raise HTTPException(404, "unknown transfer group")
    return ok(state.execution.run_group(grp, settle=settle))


class FaultIn(BaseModel):
    leg_id: str
    fault: str = Field(..., examples=["timeout", "insufficient_funds",
                                      "return_after_settlement"])


@app.post("/api/execution/inject-fault")
def inject_fault(f: FaultIn):
    """Exercise the recovery paths deterministically."""
    try:
        fault = ProviderFault(f.fault)
    except ValueError:
        raise HTTPException(400, f"unknown fault; try {[x.value for x in ProviderFault]}")
    state.provider.inject(f.leg_id, fault)
    return ok({"leg": f.leg_id, "fault": fault.value})


@app.post("/api/execution/recover/{leg_id}")
def execution_recover(leg_id: str):
    leg = state.execution._find_leg(leg_id)
    if leg is None:
        raise HTTPException(404, "unknown leg")
    if leg.state == LegState.OUTCOME_UNKNOWN:
        state.execution.recover_unknown(leg)
        return ok({"leg": leg.to_json(),
                   "action": "status recovery ran against the provider with the "
                             "original identity; no replacement payment was created"})
    return ok({"leg": leg.to_json(), "action": "nothing to recover"})


@app.post("/api/execution/return/{leg_id}")
def execution_return(leg_id: str, reason: str = "returned by the bank"):
    leg = state.execution._find_leg(leg_id)
    if leg is None:
        raise HTTPException(404, "unknown leg")
    return ok(state.execution.apply_return(leg, reason))


@app.post("/api/execution/pause-all")
def pause_all():
    return ok(state.execution.pause_all())


@app.get("/api/execution/groups")
def execution_groups():
    return ok({"groups": [g.to_json() for g in state.execution.groups.values()],
               "globally_paused": state.execution.paused,
               "reservations": {k: str(v) for k, v in state.execution.reservations.items()}})


# ---------------------------------------------------------------------------
# assistant
# ---------------------------------------------------------------------------

class AskIn(BaseModel):
    question: str
    untrusted_context: str = ""
    tool_choice: str = "router"
    include_evidence: bool = True


@app.post("/api/ask")
def ask(body: AskIn):
    try:
        choice = ToolChoice(body.tool_choice)
    except ValueError:
        choice = ToolChoice.ROUTER
    r = state.agent.ask(body.question, body.untrusted_context, choice)
    out = r.to_json()
    if not body.include_evidence:
        out.pop("evidence", None)
    return ok(out)


@app.get("/api/ask/suggestions")
def suggestions():
    """EN13: open with what can already be answered, not an empty prompt."""
    return ok({"suggestions": [
        "How should I split my next paycheck?",
        "How much can I spend this week?",
        "Which of my cards should I use for an $80 dinner?",
        "What if I pay another $300 toward debt every month?",
        "Is the cash in my different platforms insured?",
        "What happens if I lose a month of income?",
        "How much should I keep in checking?",
        "When should I pay the card to lower my reported utilization?",
    ]})


# ---------------------------------------------------------------------------
# dashboard
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def dashboard():
    f = WEB / "index.html"
    if not f.exists():
        return HTMLResponse("<h1>FinPilot</h1><p>Dashboard asset missing.</p>", 200)
    return HTMLResponse(f.read_text(encoding="utf-8"))


@app.get("/api/dashboard")
def dashboard_data(household: str = "demo"):
    """Everything the dashboard needs in one round trip."""
    reg = state.use(household)
    hh = state.households[household]
    y, m = hh.as_of.year, hh.as_of.month
    return ok({
        "household": hh.name,
        "as_of": hh.as_of.isoformat(),
        "overview": reg.get_money_overview(),
        "plan": reg.get_paycheck_plan(y, m),
        "forecast": reg.get_cash_forecast(days=45),
        "bills": reg.get_upcoming_obligations(days=30),
        "allowance": reg.get_spending_allowance(days=14),
        "buffer": reg.get_buffer(),
        "debt": reg.compare_debt_strategies(),
        "coverage": reg.get_deposit_coverage(),
        "liquidity": reg.get_liquidity_tiers(),
        "automation": reg.get_automation_status(),
        "connections": reg.get_account_connections(),
        "recurring_activity": reg.get_recurring_activity(horizon_days=45),
        "llm": state.llm.health(),
    })
