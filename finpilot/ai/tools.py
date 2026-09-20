"""Calculation tools exposed to the assistant.

Every tool is a deterministic service. The model chooses which one to call and
writes the explanation; it never produces a number itself. Each result carries
its own inputs, assumptions and confidence so AI03 -- "Show calculation inputs,
source references, dates, assumptions, and alternatives for consequential
answers" -- is satisfied from the data rather than from the prose.
"""
from __future__ import annotations

import json
import math
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal as D
from typing import Any, Callable, Optional

from ..engine.allocator import AllocationEngine
from ..engine.cards import (Alternative, Channel, Purchase, bill_payment_comparison,
                            compare_channels, interest_vs_reward, rank_cards,
                            utilization_timing)
from ..engine.coverage import (DeclaredDeposit, build_coverage, coverage_remedy,
                               stress_collateral)
from ..engine.debt import (DebtSnapshot, Strategy, biweekly_comparison,
                           compare_strategies, mortgage_scenarios,
                           promo_payoff_reserve)
from ..engine.ledger import LedgerEngine
from ..engine.liquidity import (BufferEngine, assess_sweep, classify_liquidity,
                                compare_timing)
from ..engine.recurring import RecurringActivityEngine
from ..engine.tax import (TaxProfile, compare_savings_vs_debt,
                          interest_deduction_check)
from ..models import AccountType, Confidence, Household, PolicyPurpose
from ..money import Money, msum


def _argument_error(arguments: Any, schema: dict) -> Optional[str]:
    """Flat JSON tool contracts, shared by routed/API and model-selected calls."""
    if not isinstance(arguments, dict):
        return "arguments must be an object"
    properties = schema.get("properties", {})
    if not set(schema.get("required", [])).issubset(arguments):
        return "a required input is missing"
    if not set(arguments).issubset(properties):
        return "an input is not supported by this tool"
    for key, value in arguments.items():
        rule = properties[key]
        expected = rule.get("type")
        if expected == "string":
            if not isinstance(value, str) or len(value) > rule.get("maxLength", 500):
                return f"{key} must be a string of at most {rule.get('maxLength', 500)} characters"
        elif expected == "boolean":
            if type(value) is not bool:
                return f"{key} must be a boolean"
        elif expected in ("integer", "number"):
            if type(value) not in ((int,) if expected == "integer" else (int, float)):
                return f"{key} must be a finite {expected}"
            try:
                if not math.isfinite(value):
                    return f"{key} must be finite"
            except OverflowError:
                return f"{key} must be finite"
            if "minimum" in rule and value < rule["minimum"]:
                return f"{key} must be at least {rule['minimum']}"
            if "maximum" in rule and value > rule["maximum"]:
                return f"{key} must be at most {rule['maximum']}"
        else:
            return f"{key} uses an unsupported input type"
        if "enum" in rule and value not in rule["enum"]:
            return f"{key} must be one of the supported values"
    return None


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict
    fn: Callable[..., dict]
    consequential: bool = False
    intents: tuple[str, ...] = ()
    # Unclassified/new tools are excluded from model selection by default.
    read_only: bool = False


def _forecast_record_ids(forecast) -> dict:
    """The bills, income sources and pending transactions dated inside a forecast window."""
    keys = {"bill": "bill_ids", "optional_bill": "bill_ids", "income": "income_source_ids",
            "pending": "transaction_ids"}
    out: dict[str, list[str]] = {"bill_ids": [], "income_source_ids": [], "transaction_ids": []}
    for day in forecast.days:
        for entry in day.entries:
            key = keys.get(entry.kind)
            if key and entry.record_id and entry.record_id not in out[key]:
                out[key].append(entry.record_id)
    return out


class ToolRegistry:
    """Binds the calculators to one household with one permission scope."""

    def __init__(self, household: Household, tax_profile: Optional[TaxProfile] = None,
                 execution=None, allowed_account_ids: Optional[set[str]] = None):
        self.hh = household
        self.tax = tax_profile or TaxProfile()
        self.execution = execution
        self.scope = allowed_account_ids
        self.cur = household.base_currency
        self._specs: dict[str, ToolSpec] = {}
        self._register_all()

    # -- permission scope (TR01) ---------------------------------------
    def _in_scope(self, account_id: str) -> bool:
        return self.scope is None or account_id in self.scope

    def _accounts(self):
        return [a for a in self.hh.accounts.values() if self._in_scope(a.id)]

    # ------------------------------------------------------------------
    def spec(self, name: str) -> Optional[ToolSpec]:
        return self._specs.get(name)

    def names(self) -> list[str]:
        return list(self._specs)

    def openai_schemas(self, *, read_only: bool = False) -> list[dict]:
        return [{"type": "function",
                 "function": {"name": s.name, "description": s.description,
                              "parameters": s.parameters}}
                for s in self._specs.values() if not read_only or s.read_only]

    def validate_arguments(self, name: str, arguments: Any) -> Optional[str]:
        spec = self._specs.get(name)
        return _argument_error(arguments, spec.parameters) if spec else "unknown tool"

    def call(self, name: str, arguments: Optional[dict] = None) -> dict:
        spec = self._specs.get(name)
        if spec is None:
            return {"error": f"unknown tool {name}",
                    "available": list(self._specs)}
        args = {} if arguments is None else arguments
        error = self.validate_arguments(name, args)
        if error:
            return {"error": f"bad arguments for {name}: {error}",
                    "expected": spec.parameters, "_tool": name}
        try:
            out = spec.fn(**args)
        except TypeError as e:
            return {"error": f"bad arguments for {name}: {e}",
                    "expected": spec.parameters}
        except Exception as e:
            # a tool failure must never be narrated as success
            return {"error": f"{name} failed: {type(e).__name__}: {e}",
                    "tool_failed": True}
        out.setdefault("_tool", name)
        return out

    def _add(self, name, description, parameters, fn, consequential=False, intents=(), *, read_only=False):
        parameters = deepcopy(parameters)
        parameters["additionalProperties"] = False
        bounds = {"days": (1, 3660), "horizon_days": (1, 3660), "hold_days": (1, 3660),
                  "months": (1, 120), "year": (1900, 2200), "month": (1, 12),
                  "paychecks_remaining": (1, 520)}
        for key, rule in parameters.get("properties", {}).items():
            if key in bounds:
                rule.setdefault("minimum", bounds[key][0])
                rule.setdefault("maximum", bounds[key][1])
            if rule.get("type") == "string":
                rule.setdefault("maxLength", 500)
        self._specs[name] = ToolSpec(name, description, parameters, fn,
                                     consequential, intents, read_only)

    # ==================================================================
    def _register_all(self) -> None:
        NO_ARGS = {"type": "object", "properties": {}, "required": []}

        # ---- money overview -------------------------------------------
        self._add("get_money_overview",
                  "All money across every included account: balances by type, "
                  "total cash, total debt, net worth, and what is actually "
                  "spendable today.",
                  NO_ARGS, self.get_money_overview,
                  intents=("overview", "balances", "net_worth"), read_only=True)

        self._add("get_paycheck_plan",
                  "The dated allocation of each paycheck this month across bills, "
                  "reserves, debt and goals, with the reason for every amount and "
                  "any shortfall.",
                  {"type": "object", "properties": {
                      "year": {"type": "integer"}, "month": {"type": "integer"}},
                   "required": []},
                  self.get_paycheck_plan,
                  intents=("paycheck", "split", "allocation", "plan"), read_only=True)

        self._add("get_spending_allowance",
                  "How much is safe to spend through a named date after required "
                  "commitments and protected reserves, computed from the lowest "
                  "projected balance rather than the ending balance.",
                  {"type": "object", "properties": {
                      "days": {"type": "integer", "description": "horizon, default 14"},
                      "account_id": {"type": "string"}}, "required": []},
                  self.get_spending_allowance,
                  intents=("afford", "spend", "allowance"), read_only=True)

        self._add("get_cash_forecast",
                  "Daily projected balance for an account, with the low point and "
                  "any date the balance would go negative.",
                  {"type": "object", "properties": {
                      "account_id": {"type": "string"},
                      "days": {"type": "integer"}}, "required": []},
                  self.get_cash_forecast, intents=("forecast", "low", "negative"), read_only=True)

        self._add("get_upcoming_obligations",
                  "Bills and required payments due in the next N days with their "
                  "funding account and confirmation state.",
                  {"type": "object", "properties": {"days": {"type": "integer"}},
                   "required": []},
                  self.get_upcoming_obligations, intents=("bills", "due", "upcoming"), read_only=True)

        # ---- debt ------------------------------------------------------
        self._add("compare_debt_strategies",
                  "Compare repayment orderings under one budget: lowest modelled "
                  "cost, smallest balance first, equal shares. Returns payoff "
                  "dates, total interest and the cost premium of each.",
                  {"type": "object", "properties": {
                      "extra_payment": {"type": "number",
                                        "description": "extra above required minimums"},
                      "use_worked_example": {"type": "boolean",
                                             "description": "use the specification's "
                                             "section 8 fixture instead of the "
                                             "household's own debts"}},
                   "required": []},
                  self.compare_debt_strategies, True,
                  ("debt", "payoff", "snowball", "avalanche", "repayment"), read_only=True)

        self._add("what_if_extra_payment",
                  "The effect of adding a specific extra amount to debt every "
                  "month: revised payoff dates, modelled cost difference and the "
                  "effect on the cash floor.",
                  {"type": "object", "properties": {
                      "amount": {"type": "number"}},
                   "required": ["amount"]},
                  self.what_if_extra_payment, True, ("extra", "what if", "more"), read_only=True)

        self._add("get_mortgage_scenarios",
                  "Model continuing scheduled payments, adding regular principal, "
                  "a lump sum keeping the same installment, and a conditional "
                  "recast after that lump sum.",
                  {"type": "object", "properties": {
                      "lump_sum": {"type": "number"},
                      "extra_monthly": {"type": "number"}}, "required": []},
                  self.get_mortgage_scenarios, True,
                  ("mortgage", "recast", "prepay", "principal"), read_only=True)

        self._add("compare_biweekly_mortgage",
                  "Compare a biweekly mortgage program with monthly payments plus "
                  "extra principal under the same annual budget.",
                  {"type": "object", "properties": {
                      "program_fee_per_year": {"type": "number"}}, "required": []},
                  self.compare_biweekly_mortgage, intents=("biweekly", "fortnightly"), read_only=True)

        self._add("plan_promotional_payoff",
                  "Compute the per-paycheck reserve needed to clear a promotional "
                  "balance before its deadline.",
                  {"type": "object", "properties": {
                      "balance": {"type": "number"},
                      "paychecks_remaining": {"type": "integer"}},
                   "required": ["balance", "paychecks_remaining"]},
                  self.plan_promotional_payoff, intents=("promo", "0%", "deferred"), read_only=True)

        # ---- cards -----------------------------------------------------
        self._add("choose_card",
                  "Rank the cards you already own for a specific purchase, using "
                  "net value after fees, caps, lost discounts and any borrowing "
                  "cost. Never recommends opening a new card.",
                  {"type": "object", "properties": {
                      "amount": {"type": "number"},
                      "category": {"type": "string",
                                   "enum": ["dining", "groceries", "travel", "fuel",
                                            "transit", "subscriptions", "shopping",
                                            "utilities", "base"]},
                      "merchant": {"type": "string"},
                      "channel": {"type": "string",
                                  "enum": ["in_person", "online", "portal", "direct",
                                           "recurring_bill", "delivery"]},
                      "foreign": {"type": "boolean"},
                      "mcc_certain": {"type": "boolean"},
                      "processing_fee_rate": {"type": "number"}},
                   "required": ["amount"]},
                  self.choose_card, intents=("card", "which card", "pay with"), read_only=True)

        self._add("compare_card_vs_bank_for_bill",
                  "Compare paying a bill with a card against the existing bank or "
                  "debit option, including processing fees and any lost autopay "
                  "discount.",
                  {"type": "object", "properties": {
                      "amount": {"type": "number"},
                      "card_rate": {"type": "number"},
                      "processing_fee_rate": {"type": "number"},
                      "lost_autopay_discount": {"type": "number"}},
                   "required": ["amount"]},
                  self.compare_card_vs_bank_for_bill, intents=("bill", "fee", "surcharge"), read_only=True)

        self._add("compare_booking_channels",
                  "Compare a direct booking against a portal booking on total price "
                  "after rewards.",
                  {"type": "object", "properties": {
                      "direct_price": {"type": "number"}, "direct_rate": {"type": "number"},
                      "portal_price": {"type": "number"}, "portal_rate": {"type": "number"}},
                   "required": ["direct_price", "portal_price"]},
                  self.compare_booking_channels, intents=("hotel", "portal", "booking"), read_only=True)

        self._add("check_utilization_timing",
                  "Compare paying a card before statement close against paying at "
                  "the due date, showing the effect on reported credit utilization "
                  "while preserving the grace period.",
                  {"type": "object", "properties": {
                      "card_id": {"type": "string"}, "payment": {"type": "number"}},
                   "required": []},
                  self.check_utilization_timing,
                  intents=("utilization", "credit score", "statement close"), read_only=True)

        # ---- liquidity and timing -------------------------------------
        self._add("get_buffer",
                  "How much cash an account must retain to meet its own dated "
                  "obligations, and how much is genuinely sweepable.",
                  {"type": "object", "properties": {"account_id": {"type": "string"}},
                   "required": []},
                  self.get_buffer, intents=("buffer", "cushion", "floor", "sweep"), read_only=True)

        self._add("assess_sweep_move",
                  "Whether moving a specific amount to a higher-yield existing "
                  "account is economic after tax, fees and the holding period.",
                  {"type": "object", "properties": {
                      "amount": {"type": "number"}, "hold_days": {"type": "integer"},
                      "destination_apy": {"type": "number"},
                      "transfer_fee": {"type": "number"}},
                   "required": ["amount"]},
                  self.assess_sweep_move, intents=("move", "sweep", "yield"), read_only=True)

        self._add("get_liquidity_tiers",
                  "Classify every asset by verified accessibility, separating "
                  "spendable cash from investments and from borrowing capacity.",
                  NO_ARGS, self.get_liquidity_tiers, intents=("liquid", "access", "tiers"), read_only=True)

        self._add("compare_payment_timing",
                  "Compare holding cash against the cost of delaying a payment, "
                  "including the latest safe initiation date.",
                  {"type": "object", "properties": {
                      "amount": {"type": "number"}, "due_date": {"type": "string"},
                      "debt_apr": {"type": "number"}, "cash_apy": {"type": "number"},
                      "accrues_daily": {"type": "boolean"}},
                   "required": ["amount", "due_date"]},
                  self.compare_payment_timing, intents=("when to pay", "timing", "early"), read_only=True)

        # ---- tax --------------------------------------------------------
        self._add("get_tax_profile",
                  "Read the saved tax profile, rates, and verification status without a scenario.",
                  NO_ARGS, self.get_tax_profile, intents=("tax assumptions", "tax profile"), read_only=True)
        self._add("compare_savings_vs_debt",
                  "Net benefit comparison: keep the cash, or apply it to an "
                  "existing debt, over the same period after supported taxes and "
                  "fees.",
                  {"type": "object", "properties": {
                      "amount": {"type": "number"}, "horizon_days": {"type": "integer"}},
                   "required": ["amount"]},
                  self.compare_savings_vs_debt, True,
                  ("save or pay", "net benefit", "after tax"), read_only=True)

        self._add("check_interest_deduction",
                  "Whether interest on a loan type can qualify for a deduction, "
                  "with the conditions that must be verified first.",
                  {"type": "object", "properties": {
                      "loan_type": {"type": "string",
                                    "enum": ["mortgage", "student_loan", "credit_card",
                                             "auto_loan", "investment"]}},
                   "required": ["loan_type"]},
                  self.check_interest_deduction, intents=("deduct", "tax", "write off"), read_only=True)

        # ---- coverage and entities --------------------------------------
        self._add("get_deposit_coverage",
                  "Estimated insured and uncovered deposits by underlying "
                  "institution, owner and ownership category, including sweep "
                  "look-through.",
                  NO_ARGS, self.get_deposit_coverage,
                  intents=("insured", "fdic", "ncua", "coverage", "protected"), read_only=True)

        self._add("plan_coverage_remedy",
                  "How much to move from an over-limit institution to an existing "
                  "eligible account, and what must be confirmed first.",
                  {"type": "object", "properties": {
                      "source_institution": {"type": "string"},
                      "destination_institution": {"type": "string"}},
                   "required": ["source_institution"]},
                  self.plan_coverage_remedy, True, ("uninsured", "move", "excess"), read_only=True)

        self._add("stress_collateral_line",
                  "Stress an existing securities-backed line: market decline, "
                  "advance-rate cut, resulting headroom or deficiency.",
                  {"type": "object", "properties": {
                      "collateral_value": {"type": "number"},
                      "advance_rate": {"type": "number"},
                      "drawn": {"type": "number"},
                      "decline": {"type": "number"},
                      "reduced_advance_rate": {"type": "number"}},
                   "required": ["collateral_value", "advance_rate", "drawn"]},
                  self.stress_collateral_line, intents=("collateral", "margin", "sbloc"), read_only=True)

        # ---- automation and execution ------------------------------------
        self._add("get_automation_status",
                  "Upcoming automated runs, their amounts and bounds, the mandate "
                  "behind each, and anything unresolved.",
                  NO_ARGS, self.get_automation_status,
                  intents=("automation", "recurring", "rules", "scheduled"), read_only=True)

        self._add("get_account_connections",
                  "Connection health for every account -- last synced, and the "
                  "specific reason when a link needs attention -- separate from "
                  "the balances themselves.",
                  NO_ARGS, self.get_account_connections,
                  intents=("connection", "reconnect", "link", "sync", "reauthenticate",
                            "stale", "not updating"), read_only=True)

        self._add("get_recurring_activity",
                  "Every standing rule's upcoming dated occurrences over the next "
                  "N days, whether each will actually run, and why not when it "
                  "won't -- paused, skipped, or missing authorization.",
                  {"type": "object", "properties":
                   {"horizon_days": {"type": "integer"}}, "required": []},
                  self.get_recurring_activity,
                  intents=("upcoming run", "next run", "when will", "will it run",
                            "unresolved payment"), read_only=True)

        self._add("pause_recurring_policy",
                  "Pause or resume one standing rule by id, without touching any "
                  "other rule or the global pause.",
                  {"type": "object", "properties":
                   {"policy_id": {"type": "string"}, "paused": {"type": "boolean"}},
                   "required": ["policy_id"]},
                  self.pause_recurring_policy, consequential=True,
                  intents=("pause this", "stop this rule", "turn off", "resume this rule"))

        self._add("skip_next_occurrence",
                  "Skip only the very next scheduled occurrence of one rule; it "
                  "resumes normally on the following one. Different from pausing, "
                  "which stops every future occurrence until resumed.",
                  {"type": "object", "properties":
                   {"policy_id": {"type": "string"}}, "required": ["policy_id"]},
                  self.skip_next_occurrence, consequential=True,
                  intents=("skip next", "skip this one", "skip the next"))

        self._add("pay_bill_once",
                  "Build a one-time payment for a specific bill, independent of "
                  "the paycheck plan. This only builds and preflights the payment "
                  "-- it never sends money by itself; authorization and execution "
                  "are separate, explicit steps.",
                  {"type": "object", "properties":
                   {"bill_id": {"type": "string"},
                    "occurrence_date": {"type": "string", "description": "Exact due date (YYYY-MM-DD), when selecting a recurrence"}},
                   "required": ["bill_id"]},
                  self.pay_bill_once, consequential=True,
                  intents=("pay this bill now", "one-time payment", "pay it once",
                            "make a one time payment"))

        self._add("explain_transfer_outcome",
                  "Why a scheduled transfer did not run, naming the actual failed "
                  "check or provider state and the recovery available.",
                  {"type": "object", "properties": {"leg_id": {"type": "string"}},
                   "required": []},
                  self.explain_transfer_outcome,
                  intents=("failed", "did not run", "why", "transfer"), read_only=True)

        self._add("stress_income_loss",
                  "A separate stress scenario: lose income for N months. Shows "
                  "unmet obligations, reserve use and what must change.",
                  {"type": "object", "properties": {"months": {"type": "integer"}},
                   "required": []},
                  self.stress_income_loss, intents=("lose", "job", "stress", "what if"), read_only=True)

        self._add("explain_product_boundary",
                  "Explain what this application will not do: recommend opening "
                  "accounts, select securities, or determine eligibility.",
                  {"type": "object", "properties": {"topic": {"type": "string"}},
                   "required": []},
                  self.explain_product_boundary,
                  intents=("invest", "buy", "stock", "should i open"), read_only=True)

    # ==================================================================
    # implementations
    # ==================================================================
    def get_money_overview(self) -> dict:
        by_type: dict[str, Money] = {}
        rows = []
        for a in self._accounts():
            key = a.type.value
            by_type[key] = by_type.get(key, Money.zero(self.cur)) + a.current
            rows.append({"id": a.id, "name": a.nickname, "type": a.type.value,
                         "institution": a.institution,
                         "mask": a.mask, "current": a.current.to_json(),
                         "available": a.available.to_json(),
                         "spendable": a.spendable.to_json(),
                         "protection": a.protection.value,
                         "liquidity_tier": a.liquidity_tier.value,
                         "connection_healthy": a.connection_healthy,
                         "as_of": a.provenance.as_of.isoformat() if a.provenance.as_of else None,
                         "verification": a.provenance.verification.value})
        gross_spendable = msum([a.spendable for a in self._accounts()], self.cur)
        # Every total below is scoped to `self.scope` (None means full
        # household access). A caller restricted to a subset of accounts
        # must never see the whole household's cash, debt, assets or net
        # worth just because these figures are computed from the Household
        # aggregate rather than from the caller's own account list.
        protected = self.hh.protected_reserves(account_ids=self.scope)
        spendable = (gross_spendable - protected).clamp_min_zero()
        reserves = [{"name": r.name, "account": self.hh.accounts[r.account_id].nickname
                     if r.account_id in self.hh.accounts else r.account_id,
                     "purpose": r.purpose.value, "funded": r.funded.to_json(),
                     "target": r.target.to_json(),
                     "remaining": r.remaining.to_json()}
                    for r in self.hh.reserves.values() if self._in_scope(r.account_id)]
        return {
            "as_of": self.hh.as_of.isoformat(),
            "household": self.hh.name,
            "total_cash": self.hh.total_cash(account_ids=self.scope).to_json(),
            "protected_reserves": protected.to_json(),
            "spendable_now": spendable.to_json(),
            "total_debt": self.hh.total_debt(account_ids=self.scope).to_json(),
            "estimated_assets": self.hh.estimated_assets(account_ids=self.scope).to_json(),
            "net_worth": self.hh.net_worth(account_ids=self.scope).to_json(),
            "by_type": {k: v.to_json() for k, v in by_type.items()},
            "accounts": rows,
            "reserves": reserves,
            "notes": [
                "Spendable is cash less protected reserves. It excludes investments, "
                "retirement holdings and credit limits.",
                "Estimated assets such as property are included in net worth but "
                "labelled separately; they are never spendable and never verified "
                "balances.",
                "Net worth counts contributions and transfers as movements, never as "
                "investment performance.",
            ],
        }

    def get_paycheck_plan(self, year: Optional[int] = None,
                          month: Optional[int] = None) -> dict:
        y = year or self.hh.as_of.year
        m = month or self.hh.as_of.month
        eng = AllocationEngine(self.hh)
        plan, runs = eng.allocate_month(y, m)
        return {"month": f"{y}-{m:02d}", "plan": plan.to_json(),
                "paychecks": [r.to_json() for r in runs],
                "rule": ("A deadline that falls before the next paycheck is funded "
                         "from this one. An even split across paychecks is rejected "
                         "when it would leave a due date unfunded.")}

    def get_spending_allowance(self, days: int = 14,
                               account_id: Optional[str] = None) -> dict:
        acct_id = account_id or next((a.id for a in self.hh.checking
                                      if self._in_scope(a.id)), None)
        if not acct_id or not self._in_scope(acct_id):
            return {"error": "no checking account in scope"}
        led = LedgerEngine(self.hh)
        out = led.spending_allowance(acct_id, days).to_json()
        out["account_id"] = acct_id
        out.update(_forecast_record_ids(led.forecast(acct_id, days=days)))
        out["reserve_ids"] = sorted(r.id for r in self.hh.reserves.values()
                                    if r.account_id == acct_id and r.protected and r.funded.is_positive)
        return out

    def get_cash_forecast(self, account_id: Optional[str] = None,
                          days: int = 45) -> dict:
        acct_id = account_id or next((a.id for a in self.hh.checking
                                      if self._in_scope(a.id)), None)
        if not acct_id or not self._in_scope(acct_id):
            return {"error": "no account in scope"}
        led = LedgerEngine(self.hh)
        fc = led.forecast(acct_id, days=days)
        out = fc.to_json(include_days=False)
        out["daily"] = [{"date": d.date.isoformat(), "closing": str(d.closing.round().amount)}
                        for d in fc.days]
        out.update(_forecast_record_ids(fc))
        return out

    def get_upcoming_obligations(self, days: int = 30) -> dict:
        start = self.hh.as_of
        end = start + timedelta(days=days)
        rows = []
        for b in self.hh.bills.values():
            if not self._in_scope(b.funding_account_id):
                continue
            occ = self.hh.bill_dates(b, start, end)
            for d in occ:
                occurrence = self.hh.bill_occurrence(b, d)
                if occurrence.remaining.is_zero:
                    continue
                rows.append({"id": b.id, "occurrence_id": occurrence.id,
                             "name": b.name, "amount": occurrence.remaining.to_json(),
                             "original_amount": occurrence.amount.to_json(),
                             "paid": occurrence.paid.to_json(),
                             "funded": occurrence.funded.to_json(),
                             "awaiting_application": (occurrence.funded - occurrence.paid).clamp_min_zero().to_json(),
                             "due_date": d.isoformat(), "required": b.required,
                             "category": b.category,
                             "funding_account": self.hh.accounts[b.funding_account_id].nickname
                             if b.funding_account_id in self.hh.accounts else "",
                             "execution_owner": b.execution_owner.value,
                             "amount_confirmed": b.amount_confirmed,
                             "days_away": (d - start).days})
        rows.sort(key=lambda r: r["due_date"])
        total = msum([Money(D(r["amount"]["amount"]), self.cur) for r in rows], self.cur)
        return {"window_days": days, "total_due": total.to_json(),
                "count": len(rows), "obligations": rows}

    # ---- debt ---------------------------------------------------------
    def _household_debts(self) -> list[DebtSnapshot]:
        return [DebtSnapshot.from_liability(l) for l in self.hh.liabilities.values()
                if l.balance.is_positive and l.terms_complete and self._in_scope(l.account_id)]

    def _missing_terms(self) -> list[str]:
        """Debts in totals whose rate or required payment is unknown, so models leave them out."""
        return sorted(l.name for l in self.hh.liabilities.values()
                      if l.balance.is_positive and not l.terms_complete and self._in_scope(l.account_id))

    def _no_modelled_debts(self) -> dict:
        missing = self._missing_terms()
        if missing:
            return {"error": "Enter the interest rate and required payment for " + ", ".join(missing)
                             + " to compare payoff plans.", "missing_terms": missing}
        return {"error": "no debts on file"}

    def _no_modelled_mortgage(self) -> dict:
        waiting = sorted(l.name for l in self.hh.liabilities.values() if l.type == AccountType.MORTGAGE
                         and not l.terms_complete and self._in_scope(l.account_id))
        if waiting:
            return {"error": "Enter principal and interest and escrow separately for " + ", ".join(waiting)
                             + " to model mortgage scenarios.", "missing_terms": waiting}
        return {"error": "no mortgage on file"}

    def compare_debt_strategies(self, extra_payment: Optional[float] = None,
                                use_worked_example: bool = False) -> dict:
        if use_worked_example:
            from ..seed.demo import SECTION8_BUDGET, section8_debts
            debts, budget = section8_debts(), SECTION8_BUDGET
            source = "specification section 8 worked example"
        else:
            debts = self._household_debts()
            if not debts:
                return self._no_modelled_debts()
            required = msum([d.minimum for d in debts], self.cur)
            extra = Money(D(str(extra_payment)), self.cur) if extra_payment is not None \
                else Money(D("500"), self.cur)
            budget = required + extra
            source = "your accounts"
        cmp_ = compare_strategies(debts, budget)
        out = cmp_.to_json()
        out["source"] = source
        out["missing_terms"] = [] if use_worked_example else self._missing_terms()
        out["debts"] = [{"id": d.id, "name": d.name, "balance": d.balance.to_json(),
                         "apr": str(d.apr), "minimum": d.minimum.to_json()}
                        for d in debts]
        return out

    def what_if_extra_payment(self, amount: float) -> dict:
        debts = self._household_debts()
        if not debts:
            return self._no_modelled_debts()
        required = msum([d.minimum for d in debts], self.cur)
        base = compare_strategies(debts, required, [Strategy.HIGHEST_RATE])
        with_extra = compare_strategies(
            debts, required + Money(D(str(amount)), self.cur), [Strategy.HIGHEST_RATE])
        b, w = base.results[0], with_extra.results[0]
        led = LedgerEngine(self.hh)
        in_scope_checking = [a for a in self.hh.checking if self._in_scope(a.id)]
        allowance = (led.spending_allowance(in_scope_checking[0].id, 30)
                     if in_scope_checking else None)
        return {
            "extra_per_month": Money(D(str(amount)), self.cur).to_json(),
            "without_extra": {"months": b.months_to_clear,
                              "total_interest": b.total_interest.to_json()},
            "with_extra": {"months": w.months_to_clear,
                           "total_interest": w.total_interest.to_json()},
            "months_saved": b.months_to_clear - w.months_to_clear,
            "interest_saved": (b.total_interest - w.total_interest).to_json(),
            "target_order": [p.name for p in w.payoffs],
            "missing_terms": self._missing_terms(),
            "cash_floor_effect": (allowance.to_json() if allowance else None),
            "caveat": ("Modelled savings, not realised savings. Extra principal does "
                       "not reduce the next required installment unless the servicer "
                       "confirms a change."),
        }

    def get_mortgage_scenarios(self, lump_sum: Optional[float] = None,
                               extra_monthly: Optional[float] = None) -> dict:
        mort = next((l for l in self.hh.liabilities.values()
                     if l.type == AccountType.MORTGAGE and l.terms_complete), None)
        if mort is None:
            return self._no_modelled_mortgage()
        if not self._in_scope(mort.account_id):
            return {"error": "no mortgage on file"}
        cash = self.hh.total_cash(account_ids=self.scope)
        scen = mortgage_scenarios(
            mort.balance, mort.apr, mort.remaining_term_months or 240,
            mort.minimum_payment,
            Money(D(str(lump_sum)), self.cur) if lump_sum else None,
            Money(D(str(extra_monthly)), self.cur) if extra_monthly else None,
            cash, Money(D("250"), self.cur))
        return {"mortgage": {"liability_id": mort.id, "balance": mort.balance.to_json(), "apr": str(mort.apr),
                             "principal_and_interest": mort.minimum_payment.to_json(),
                             "escrow": mort.escrow.to_json()},
                "scenarios": [s.to_json() for s in scen],
                "note": ("Escrow, taxes and insurance are shown separately and are "
                         "not affected by any of these choices. A recast requires "
                         "servicer confirmation of eligibility, fee and effective date.")}

    def compare_biweekly_mortgage(self, program_fee_per_year: Optional[float] = None
                                  ) -> dict:
        mort = next((l for l in self.hh.liabilities.values()
                     if l.type == AccountType.MORTGAGE and l.terms_complete), None)
        if mort is None or not self._in_scope(mort.account_id):
            return self._no_modelled_mortgage() if mort is None else {"error": "no mortgage on file"}
        out = biweekly_comparison(
            mort.balance, mort.apr, mort.minimum_payment,
            Money(D(str(program_fee_per_year)), self.cur) if program_fee_per_year else None)
        out["liability_id"] = mort.id
        return out

    def plan_promotional_payoff(self, balance: float, paychecks_remaining: int) -> dict:
        return promo_payoff_reserve(Money(D(str(balance)), self.cur),
                                    paychecks_remaining)

    # ---- cards ---------------------------------------------------------
    def choose_card(self, amount: float, category: str = "base", merchant: str = "",
                    channel: str = "in_person", foreign: bool = False,
                    mcc_certain: bool = True,
                    processing_fee_rate: float = 0.0) -> dict:
        cards = [c for c in self.hh.cards.values() if self._in_scope(c.account_id)]
        if not cards:
            return {"error": "no cards on file"}
        amt = Money(D(str(amount)), self.cur)
        led = LedgerEngine(self.hh)
        feasible = True
        in_scope_checking = [a for a in self.hh.checking if self._in_scope(a.id)]
        if in_scope_checking:
            allowance = led.spending_allowance(in_scope_checking[0].id, 30)
            feasible = allowance.amount >= amt
        p = Purchase(amount=amt, merchant=merchant, category=category,
                     channel=Channel(channel), foreign=foreign,
                     mcc_certain=mcc_certain,
                     processing_fee_rate=D(str(processing_fee_rate)),
                     date=self.hh.as_of)
        r = rank_cards(cards, p, Alternative(reward=Money.zero(self.cur),
                                             fee=Money.zero(self.cur),
                                             discount_kept=Money.zero(self.cur)),
                       repayment_feasible=feasible)
        out = r.to_json()
        out["repayment_feasible"] = feasible
        if not feasible:
            out["repayment_note"] = (
                "A card purchase immediately consumes budget and creates a future "
                "card-payment commitment. Your funded spending allowance does not "
                "currently cover this amount, so no card is recommended.")
        return out

    def compare_card_vs_bank_for_bill(self, amount: float, card_rate: float = 0.02,
                                      processing_fee_rate: float = 0.0295,
                                      lost_autopay_discount: float = 0.0) -> dict:
        return bill_payment_comparison(
            Money(D(str(amount)), self.cur), D(str(card_rate)),
            D(str(processing_fee_rate)), Money.zero(self.cur),
            Money(D(str(lost_autopay_discount)), self.cur))

    def compare_booking_channels(self, direct_price: float, portal_price: float,
                                 direct_rate: float = 0.02,
                                 portal_rate: float = 0.05) -> dict:
        return compare_channels(Money(D(str(direct_price)), self.cur), D(str(direct_rate)),
                                Money(D(str(portal_price)), self.cur), D(str(portal_rate)))

    def check_utilization_timing(self, card_id: Optional[str] = None,
                                 payment: Optional[float] = None) -> dict:
        cards = [c for c in self.hh.cards.values() if self._in_scope(c.account_id)]
        if card_id:
            card = self.hh.cards.get(card_id)
            if card is not None and not self._in_scope(card.account_id):
                card = None
        else:
            card = cards[0] if cards else None
        if card is None:
            return {"error": "no card on file"}
        pay = Money(D(str(payment)), self.cur) if payment is not None else card.current_balance
        out = utilization_timing(card, pay, self.hh.as_of)
        out["card_id"] = card.id
        return out

    # ---- liquidity -------------------------------------------------------
    def get_buffer(self, account_id: Optional[str] = None) -> dict:
        acct_id = account_id or next((a.id for a in self.hh.checking
                                      if self._in_scope(a.id)), None)
        if not acct_id or not self._in_scope(acct_id):
            return {"error": "no account in scope"}
        return BufferEngine(self.hh).for_account(acct_id).to_json()

    def assess_sweep_move(self, amount: float, hold_days: int = 30,
                          destination_apy: Optional[float] = None,
                          transfer_fee: float = 0.0) -> dict:
        in_scope_checking = [a for a in self.hh.checking if self._in_scope(a.id)]
        chk = in_scope_checking[0] if in_scope_checking else None
        sav = next((a for a in self.hh.accounts.values()
                    if a.type == AccountType.SAVINGS and self._in_scope(a.id)), None)
        if not chk or not sav:
            return {"error": "need a checking and a savings account in scope"}
        dest_apy = D(str(destination_apy)) if destination_apy else (sav.apy or D("0"))
        buf = BufferEngine(self.hh).for_account(chk.id)
        amt = Money(D(str(amount)), self.cur)
        result = assess_sweep(amt, chk.apy or D("0"), dest_apy, hold_days,
                              self.tax.combined_marginal,
                              Money(D(str(transfer_fee)), self.cur),
                              destination=sav.nickname).to_json()
        result["sweepable_ceiling"] = buf.sweepable.to_json()
        result["exceeds_sweepable"] = amt > buf.sweepable
        if result["exceeds_sweepable"]:
            result["blocker"] = (
                f"Only {buf.sweepable} is sweepable from {chk.nickname} once dated "
                "obligations, your floor and the uncertainty allowance are held back.")
        return result

    def get_liquidity_tiers(self) -> dict:
        rows = [r for r in classify_liquidity(self.hh)
                if self._in_scope(r["account_id"])]
        near = msum([Money(D(r["available"]["amount"]), self.cur) for r in rows
                     if r["counts_as_near_term_cash"]], self.cur)
        return {"tiers": rows, "near_term_cash": near.to_json(),
                "note": ("A money market deposit account and a money market fund are "
                         "different products. Funds can lose value and are not "
                         "insured deposits.")}

    def compare_payment_timing(self, amount: float, due_date: str,
                               debt_apr: float = 0.0, cash_apy: Optional[float] = None,
                               accrues_daily: bool = True) -> dict:
        sav = next((a for a in self.hh.accounts.values()
                    if a.type == AccountType.SAVINGS and self._in_scope(a.id)), None)
        apy = D(str(cash_apy)) if cash_apy is not None else (sav.apy if sav else D("0"))
        d = date.fromisoformat(due_date)
        return compare_timing("Payment", Money(D(str(amount)), self.cur), d,
                              self.hh.as_of, apy, D(str(debt_apr)),
                              self.tax.combined_marginal,
                              accrues_daily=accrues_daily).to_json()

    # ---- tax ---------------------------------------------------------------
    def get_tax_profile(self) -> dict:
        return {"tax_profile": self.tax.to_json(),
                "confidence": "verified" if self.tax.verified else "approximate",
                "assumptions": [self.tax.to_json()["label"]]}

    def compare_savings_vs_debt(self, amount: float, horizon_days: int = 365) -> dict:
        options = []
        for l in self.hh.liabilities.values():
            if (not l.balance.is_positive or not self._in_scope(l.account_id)
                    or l.balance.currency != self.cur):
                continue
            options.append((l.name, l.apr, l.tax_deductible_interest, l.balance))
        if not options:
            return {"error": "no eligible debt on file"}
        sav = next((a for a in self.hh.accounts.values()
                    if a.type == AccountType.SAVINGS and self._in_scope(a.id)
                    and a.currency == self.cur and a.apy is not None), None)
        if sav is None:
            return {"error": "add a savings account with a known APY before comparing savings and debt"}
        apy = sav.apy
        cmp_ = compare_savings_vs_debt(Money(D(str(amount)), self.cur), apy,
                                       options, self.tax, horizon_days)
        return cmp_.to_json()

    def check_interest_deduction(self, loan_type: str) -> dict:
        return interest_deduction_check(loan_type, self.tax)

    # ---- coverage -----------------------------------------------------------
    def get_deposit_coverage(self) -> dict:
        from ..seed.demo import SWEEP_BANK_NAMES
        kinds = {"cu_harbor": "credit_union"}
        rep = build_coverage(self.hh, self.hh.as_of, institution_kind=kinds,
                             institution_names=SWEEP_BANK_NAMES)
        out = rep.to_json()
        for b in out["buckets"]:
            ent = self.hh.entities.get(b["owner"])
            if ent:
                b["owner"] = ent.name
        return out

    def plan_coverage_remedy(self, source_institution: str,
                             destination_institution: Optional[str] = None) -> dict:
        from ..seed.demo import SWEEP_BANK_NAMES
        rep = build_coverage(self.hh, self.hh.as_of,
                             institution_kind={"cu_harbor": "credit_union"},
                             institution_names=SWEEP_BANK_NAMES)
        dest = destination_institution
        if not dest:
            others = [b for b in rep.buckets if b.institution_id != source_institution]
            dest = others[0].institution_id if others else ""
        dest_bucket = next((b for b in rep.buckets if b.institution_id == dest), None)
        return coverage_remedy(rep, source_institution, dest,
                               dest_bucket.total if dest_bucket else Money.zero(self.cur))

    def stress_collateral_line(self, collateral_value: float, advance_rate: float,
                               drawn: float, decline: float = 0.30,
                               reduced_advance_rate: Optional[float] = None) -> dict:
        return stress_collateral(
            Money(D(str(collateral_value)), self.cur), D(str(advance_rate)),
            Money(D(str(drawn)), self.cur), [D(str(decline))],
            [D(str(reduced_advance_rate))] if reduced_advance_rate else [])

    # ---- automation ---------------------------------------------------------
    def get_automation_status(self) -> dict:
        rows = []
        for p in self.hh.policies_sorted():
            if not self._in_scope(p.destination_account_id):
                continue
            m = p.mandate
            rows.append({
                "id": p.id, "name": p.name, "purpose": p.purpose.value,
                "method": p.method.value,
                "amount": (p.monthly_target or p.amount).to_json(),
                "destination": self.hh.accounts[p.destination_account_id].nickname
                if p.destination_account_id in self.hh.accounts else p.destination_account_id,
                "priority": p.priority, "paused": p.paused,
                "authorization": m.mode.value if m else "none",
                "authorized": bool(m and m.active),
                "per_run_cap": m.per_run_cap.to_json() if m and m.per_run_cap else None,
                "notice_days": m.notice_days if m else None,
                "jurisdiction": m.jurisdiction if m else None,
            })
        groups = []
        if self.execution:
            groups = [g.summary() for g in self.execution.groups.values()]
        return {"policies": rows, "count": len(rows),
                "globally_paused": bool(self.execution and self.execution.paused),
                "transfer_groups": groups,
                "control": ("You can pause any single rule, skip its next occurrence, "
                            "or pause all future runs. Pausing cannot recall a payment "
                            "already submitted.")}

    def get_account_connections(self) -> dict:
        rows = []
        for a in self._accounts():
            rows.append({
                "id": a.id, "name": a.nickname, "institution": a.institution,
                "healthy": a.connection_healthy,
                "issue": a.connection_issue or None,
                "last_synced_at": a.last_synced_at.isoformat() if a.last_synced_at else None,
                "affects_planning": (not a.connection_healthy) and a.included_in_planning,
            })
        unhealthy = [r for r in rows if not r["healthy"]]
        return {
            "accounts": rows,
            "unhealthy_count": len(unhealthy),
            "unhealthy": unhealthy,
            "note": ("A stale or broken connection only affects figures that "
                     "read from that account; it does not silently disappear "
                     "from a plan, and every affected estimate says so at a "
                     "lower confidence rather than staying silent about it."),
        }

    def get_recurring_activity(self, horizon_days: int = 60) -> dict:
        eng = RecurringActivityEngine(self.hh)
        runs = eng.upcoming_runs(horizon_days=horizon_days)
        runs = [r for r in runs if self._in_scope(
            next((p.destination_account_id for p in self.hh.policies.values()
                  if p.id == r.policy_id), ""))]
        return {
            "horizon_days": horizon_days,
            "upcoming_runs": [r.to_json() for r in runs],
            "will_not_run": [r.to_json() for r in runs if not r.will_run],
            "unresolved_policies": eng.unresolved(),
            "note": ("Each row is a dated occurrence this rule is scheduled to "
                     "reach, not a payment already sent. Pausing a rule stops "
                     "every future occurrence; skipping stops only the next one."),
        }

    def pause_recurring_policy(self, policy_id: str, paused: bool = True) -> dict:
        pol = self.hh.policies.get(policy_id)
        if pol is None or not self._in_scope(pol.destination_account_id):
            return {"error": f"unknown policy: {policy_id}"}
        pol.paused = paused
        return {
            "policy_id": policy_id, "name": pol.name, "paused": pol.paused,
            "note": ("Paused now: no future occurrence of this rule runs until "
                     "it is resumed. This does not touch any other rule, and "
                     "cannot recall a payment already submitted."
                     if paused else
                     "Resumed: this rule's normal schedule applies again."),
        }

    def skip_next_occurrence(self, policy_id: str) -> dict:
        pol = self.hh.policies.get(policy_id)
        if pol is None or not self._in_scope(pol.destination_account_id):
            return {"error": f"unknown policy: {policy_id}"}
        skipped_date = self.hh.skip_policy_occurrence(pol)
        if skipped_date is None:
            return {"error": "No future occurrence is scheduled for this policy."}
        return {
            "policy_id": policy_id, "name": pol.name, "skip_next": True,
            "skipped_date": skipped_date.isoformat(),
            "note": ("Only the next occurrence of this rule is skipped; it "
                     "resumes normally starting with the one after that."),
        }

    def pay_bill_once(self, bill_id: str, occurrence_date: Optional[str] = None) -> dict:
        bill = self.hh.bills.get(bill_id)
        if bill is None or not self._in_scope(bill.funding_account_id):
            return {"error": f"unknown bill: {bill_id}"}
        if not self.execution:
            return {"error": "no execution engine attached"}
        try:
            selected_date = date.fromisoformat(str(occurrence_date)) if occurrence_date else None
            grp = self.execution.build_group_from_bill(bill_id, occurrence_date=selected_date)
        except ValueError as error:
            return {"error": str(error)}
        leg = grp.legs[0]
        pf = self.execution.preflight(leg)
        return {
            "group_id": grp.id, "leg": leg.to_json(),
            "amount": leg.amount.to_json(), "bill": bill.name,
            "due_date": leg.scheduled_for.isoformat(),
            "occurrence_id": leg.occurrence_id,
            "preflight": pf.to_json(),
            "note": ("This only builds and checks a one-time payment; nothing "
                     "has been sent. " +
                     ("It is ready to authorize and run." if pf.ok else
                      "It cannot run yet for the reason(s) above.")),
        }

    def explain_transfer_outcome(self, leg_id: Optional[str] = None) -> dict:
        if not self.execution:
            return {"error": "no execution engine attached"}
        # Scoped like every other tool: a leg touching an account outside
        # this caller's permission scope is invisible here, on either side
        # of the transfer, and an explicit leg_id cannot be used to reach
        # around that.
        legs = [l for g in self.execution.groups.values() for l in g.legs
                if self._in_scope(l.source_account_id)
                or self._in_scope(l.destination_account_id)]
        target = next((l for l in legs if l.id == leg_id), None) if leg_id else None
        if target is None:
            problems = [l for l in legs if l.state.value in
                        ("failed", "returned", "outcome_unknown", "canceled")]
            target = problems[0] if problems else (legs[0] if legs else None)
        if target is None:
            return {"error": "no payment legs exist yet"}
        return {
            "leg": target.to_json(),
            "state": target.state.value,
            "actual_reason": target.failure_reason or "no failure recorded",
            "affected_obligation": target.destination_label,
            "amount": target.amount.to_json(),
            "available_actions": _recovery_actions(target.state.value),
            "note": ("Authorization, scheduling, processing, settlement, failure and "
                     "unknown status are distinct states. A payment is only described "
                     "as scheduled or sent when the payment service confirms it."),
        }

    def stress_income_loss(self, months: int = 1) -> dict:
        # Scoped the same way as every other total here: a caller restricted
        # to a subset of accounts must not see income, bills or reserves
        # tied to accounts outside that scope, any more than it can see
        # those accounts' balances directly.
        def _income_in_scope(ev) -> bool:
            src = self.hh.income_sources.get(ev.source_id)
            return src is None or self._in_scope(src.deposit_account_id)

        monthly_income = msum([e.effective_amount for e in self.hh.income_events
                               if _income_in_scope(e)], self.cur)
        required = msum([b.amount for b in self.hh.bills.values()
                         if b.required and self._in_scope(b.funding_account_id)],
                        self.cur)
        reserves = msum([r.funded for r in self.hh.reserves.values()
                         if r.purpose.value == "emergency" and self._in_scope(r.account_id)],
                        self.cur)
        cash = self.hh.total_cash(account_ids=self.scope)
        gap_per_month = (required - Money.zero(self.cur))
        total_need = gap_per_month * months
        runway_months = int((cash.amount / required.amount)) if required.is_positive else 0
        unmet = (total_need - cash).clamp_min_zero()
        return {
            "scenario": f"no income for {months} month(s)",
            "monthly_required_obligations": required.to_json(),
            "total_required_over_period": total_need.to_json(),
            "available_cash": cash.to_json(),
            "emergency_reserve": reserves.to_json(),
            "runway_months_at_required_only": runway_months,
            "unmet": unmet.to_json(),
            "bill_ids": sorted(b.id for b in self.hh.bills.values()
                               if b.required and self._in_scope(b.funding_account_id)),
            "reserve_ids": sorted(r.id for r in self.hh.reserves.values()
                                  if r.purpose.value == "emergency" and self._in_scope(r.account_id)),
            "account_ids": sorted(a.id for a in self.hh.cash_accounts if self._in_scope(a.id)
                                  and a.included_in_planning and a.currency == self.hh.base_currency),
            "changes_required": [
                "Optional transfers pause: brokerage funding, extra principal and "
                "discretionary spending.",
                "Protected reserves are the funding source, and the replenishment "
                "plan restarts when income resumes.",
                "Extra debt payments stop; required minimums continue.",
            ],
            "note": ("This is a separate stress scenario. It does not change your "
                     "saved plan or any authorized rule."),
        }

    def explain_product_boundary(self, topic: str = "") -> dict:
        return {
            "topic": topic,
            "in_scope": [
                "Showing your existing holdings and their values.",
                "Comparing uses of cash you already have across accounts you already own.",
                "Routing cash to an existing brokerage or contribution destination "
                "you select, without changing what that platform buys.",
                "Modelling a scenario you describe, clearly labelled as hypothetical.",
            ],
            "out_of_scope": [
                "Recommending that you open a new account or product.",
                "Selecting securities to buy, sell or rebalance.",
                "Tax-loss harvesting, retirement conversions, or new leverage.",
                "Determining tax or program eligibility.",
            ],
            "reason": ("Personalised recommendations use only accounts, cards, loans "
                       "and memberships you already own and have explicitly included. "
                       "A transfer to brokerage cash is not the same as buying a fund."),
        }


def _recovery_actions(state: str) -> list[str]:
    return {
        "failed": ["Correct the named blocker and resubmit.",
                   "The obligation is still open and is shown as unfunded."],
        "outcome_unknown": [
            "Status recovery is running against the provider with the original "
            "identity. No replacement payment will be created until it resolves.",
            "A manual replacement is withheld while the original outcome is unresolved."],
        "returned": ["Cash availability and dependent legs have been rebuilt from "
                     "actual status.", "The affected obligation is reopened."],
        "canceled": ["This run was cancelled before submission. Nothing was sent."],
        "submitted": ["Already submitted. It cannot be stopped through this "
                      "application; a recall must be raised with the provider and is "
                      "not guaranteed."],
        "processing": ["In flight with the provider. Cancellation is not available."],
        "reconciled": ["Complete and matched to bank activity and creditor application."],
    }.get(state, ["No action required."])
