"""Scope-aware reads and deterministic, renderer-ready financial evidence."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from ..ai.tools import ToolRegistry
from ..models import TxKind, TxState
from ..money import Money
from .contracts import ReadQuery, Scope

# These existing calculators really are household-wide. A viewing hint must
# never pretend their answer was computed for just one selected account.
WRITE_TOOLS = {"pause_recurring_policy", "skip_next_occurrence", "pay_bill_once"}
SCOPED_TOOLS = {"get_money_overview", "get_spending_allowance", "get_cash_forecast",
                "get_upcoming_obligations", "compare_debt_strategies", "what_if_extra_payment",
                "get_mortgage_scenarios", "compare_biweekly_mortgage", "check_utilization_timing",
                "get_buffer", "get_liquidity_tiers", "get_account_connections", "analyze_spending"}


def resolve_scope(hh, scope: Scope):
    if scope.kind == "household":
        return None, "Your household"
    if scope.kind == "account":
        account = hh.accounts.get(scope.account_id)
        if account is None:
            raise ValueError("Account not found in this workspace")
        return {account.id}, account.nickname
    accounts = [a for a in hh.accounts.values()
                if a.institution.casefold() == scope.platform.casefold()]
    if not accounts:
        raise ValueError("Platform not found in this workspace")
    return {a.id for a in accounts}, accounts[0].institution


def scope_json(hh, scope):
    ids, label = resolve_scope(hh, scope)
    return {**scope.model_dump(), "label": label,
            "account_ids": sorted(ids if ids is not None else hh.accounts)}


def amount(value):
    return value.get("amount", "0") if isinstance(value, dict) else str(value)


def display(value):
    if isinstance(value, dict):
        if "amount" in value:
            return value.get("display") or f"{value['amount']} {value.get('currency', '')}".strip()
        return "; ".join(f"{k.replace('_', ' ')}: {display(v)}" for k, v in list(value.items())[:8])
    if isinstance(value, list):
        return ", ".join(display(v) for v in value[:8])
    return str(value if value is not None else "Not recorded")


def table(title, headers, rows):
    return {"type": "table", "title": title, "columns": headers,
            "rows": [[display(v) for v in row] for row in rows[:100]],
            "truncated": len(rows) > 100}


def chart(title, rows, currency, *, kind="bar"):
    return {"type": "chart", "kind": kind, "title": title, "currency": currency,
            "points": [{"label": str(label), "value": amount(value)} for label, value in rows[:366]]}


def spending(hh, ids, arguments):
    allowed = {"start", "end", "group_by", "compare_previous"}
    if set(arguments) - allowed:
        raise ValueError("Unsupported spending filter")
    try:
        start = date.fromisoformat(arguments.get("start", hh.as_of.replace(day=1).isoformat()))
        end = date.fromisoformat(arguments.get("end", hh.as_of.isoformat()))
    except (ValueError, TypeError):
        raise ValueError("Use ISO dates (YYYY-MM-DD) for the start and end") from None
    if end < start or (end - start).days > 366 or end > hh.as_of:
        raise ValueError("Choose an observed date range of at most 367 days ending on or before the workspace date")
    group = arguments.get("group_by", "category")
    if group not in {"category", "merchant", "account", "day"}:
        raise ValueError("Group spending by category, merchant, account, or day")
    if not isinstance(arguments.get("compare_previous", False), bool):
        raise ValueError("Comparison must be true or false")
    scope = ids if ids is not None else set(hh.accounts)
    observed = [t for t in hh.transactions if t.account_id in scope and start <= t.date <= end]
    posted = [t for t in observed if t.state in {TxState.POSTED, TxState.RECONCILED, TxState.CORRECTED}]
    spent = [t for t in posted if t.counts_as_spending]
    groups = defaultdict(Decimal)
    for tx in spent:
        key = ({"category": tx.category, "merchant": tx.merchant or tx.description,
                "account": hh.accounts[tx.account_id].nickname, "day": tx.date.isoformat()})[group]
        groups[key or "Uncategorized"] += -tx.amount.amount
    ordered = sorted(groups.items()) if group == "day" else sorted(groups.items(), key=lambda pair: -pair[1])
    total = sum(groups.values(), Decimal(0))
    refunds = sum((t.amount.amount for t in posted if t.kind == TxKind.REFUND and t.amount.is_positive), Decimal(0))
    data = {"start": start.isoformat(), "end": end.isoformat(), "group_by": group,
            "gross_spending": Money(total, hh.base_currency).to_json(),
            "refunds": Money(refunds, hh.base_currency).to_json(),
            "purchase_and_fee_count": len(spent), "observed_count": len(observed),
            "pending_count": sum(t.state == TxState.PENDING for t in observed),
            "groups": [{"label": k, "amount": Money(v, hh.base_currency).to_json()} for k, v in ordered],
            "notes": ["Observed posted purchases and fees only. Internal transfers, repayments and pending items are excluded.",
                      "Refunds are shown separately; gross spending is not reduced by refunds.",
                      "Missing imported history is not evidence of zero actual spending."]}
    if arguments.get("compare_previous"):
        # Compare matching elapsed days in the prior month, not a partial month
        # against a silently different full-month exposure window.
        if (start.year, start.month) == (end.year, end.month):
            prior_end = start.replace(day=1) - timedelta(days=1)
            prior_start = prior_end.replace(day=min(start.day, prior_end.day))
            prior_end = min(prior_end, prior_start + (end - start))
            basis = "Matching elapsed days in the prior month, clipped to month end"
        else:
            prior_end = start - timedelta(days=1)
            prior_start = prior_end - (end - start)
            basis = "Immediately preceding period with the same number of days"
        previous = spending(hh, ids, {"start": prior_start.isoformat(), "end": prior_end.isoformat(), "group_by": group})
        data["comparison"] = {"start": previous["start"], "end": previous["end"],
                              "gross_spending": previous["gross_spending"],
                              "observed_count": previous["observed_count"],
                              "basis": basis}
    return data


def validate_arguments(spec, arguments):
    """Validate the limited JSON-schema vocabulary used by the existing tools."""
    properties = spec.parameters.get("properties", {})
    if set(arguments) - set(properties):
        raise ValueError("Unsupported calculator inputs")
    missing = set(spec.parameters.get("required", [])) - set(arguments)
    if missing:
        raise ValueError("Please provide " + ", ".join(sorted(missing)))
    for key, value in arguments.items():
        field = properties[key]
        kind = field.get("type")
        if kind == "boolean" and not isinstance(value, bool):
            raise ValueError(f"{key} must be true or false")
        if kind == "string" and (not isinstance(value, str) or len(value) > 200):
            raise ValueError(f"{key} must be short text")
        if "enum" in field and value not in field["enum"]:
            raise ValueError(f"Choose a supported {key}")
        if kind in {"number", "integer"}:
            try:
                number = Decimal(str(value))
            except InvalidOperation:
                raise ValueError(f"{key} must be a number") from None
            if isinstance(value, bool) or not number.is_finite() or not 0 <= number <= 1_000_000_000:
                raise ValueError(f"{key} is outside the supported range")
            if kind == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
                raise ValueError(f"{key} must be a whole number")
            if key in {"days", "horizon_days", "hold_days"} and not 1 <= number <= 365:
                raise ValueError("Choose a horizon between 1 and 365 days")
            if key in {"months", "paychecks_remaining"} and not 1 <= number <= 120:
                raise ValueError("Choose a count between 1 and 120")
            if key == "month" and not 1 <= number <= 12:
                raise ValueError("Choose a month between 1 and 12")
            if key == "year" and not 1900 <= number <= 2200:
                raise ValueError("Choose a year between 1900 and 2200")
            if ("rate" in key or key.endswith(("apr", "apy"))) and number > 1:
                raise ValueError("Rates must be decimal fractions between 0 and 1")
    if arguments.get("use_worked_example"):
        raise ValueError("Conversation calculations use only your recorded accounts")


def read_query(ctx, scope, query: ReadQuery):
    hh = ctx.household
    ids, label = resolve_scope(hh, scope)
    tool, args = query.tool, dict(query.arguments)
    registry = ToolRegistry(hh, ctx.tax, ctx.execution, allowed_account_ids=ids)
    if tool in WRITE_TOOLS:
        raise ValueError("This command requires an action preview and confirmation")
    if ids is not None and tool not in SCOPED_TOOLS:
        raise ValueError("This calculation needs your household context. Choose Your household and ask again")
    if tool == "analyze_spending":
        data = spending(hh, ids, args)
    else:
        spec = registry.spec(tool)
        if spec is None:
            raise ValueError("Unsupported calculator")
        if ids is not None and "account_id" in spec.parameters.get("properties", {}):
            if args.get("account_id") and args["account_id"] not in ids:
                raise ValueError("That account is outside the selected conversation scope")
            if len(ids) == 1:
                args.setdefault("account_id", next(iter(ids)))
            elif not args.get("account_id"):
                raise ValueError("Choose one account for this forecast or cash-floor calculation")
        if tool == "compare_debt_strategies":
            args.setdefault("extra_payment", "0")
        validate_arguments(spec, args)
        data = registry.call(tool, args)
        if "error" in data:
            raise ValueError(data["error"])
    components, answer = present(tool, data, hh.base_currency, label)
    if ids is not None and tool in {"get_mortgage_scenarios", "compare_biweekly_mortgage", "compare_debt_strategies", "what_if_extra_payment"}:
        components.append({"type": "notes", "items": ["This models the selected debt only. Choose household context to assess affordability against your other accounts and obligations."]})
    if query.presentation == "table":
        components = [c for c in components if c["type"] != "chart"]
    return {"answer": answer, "components": components,
            "evidence": {"tool": tool, "arguments": args, "workspace_revision": ctx.revision,
                         "as_of": hh.as_of.isoformat(), "scope": scope_json(hh, scope), "result": data},
            "state": "informational"}


def present(tool, d, cur, label):
    components = []
    answer = f"Here are the recorded calculations for {label}."
    if tool == "get_money_overview":
        components.append({"type": "metrics", "items": [{"label": k.replace("_", " ").capitalize(), "value": display(d[k])}
                           for k in ("net_worth", "total_cash", "total_debt", "spendable_now")]})
        components.append(table("Account balances", ["Account", "Platform", "Current balance", "Available", "As of"],
                          [[a["name"], a["institution"], a["current"], a["available"], a["as_of"]] for a in d["accounts"]]))
        components.append(chart("Recorded balances", [(a["name"], a["current"]) for a in d["accounts"]], cur))
    elif tool == "analyze_spending":
        answer = (f"For {label}, recorded gross spending from {d['start']} to {d['end']} is {display(d['gross_spending'])}. "
                  f"Refunds recorded separately: {display(d['refunds'])}.")
        components.extend([table("Posted spending", [d["group_by"].capitalize(), "Amount"], [[r["label"], r["amount"]] for r in d["groups"]]),
                           chart("Posted spending", [(r["label"], r["amount"]) for r in d["groups"]], cur, kind="line" if d["group_by"] == "day" else "bar")])
        if "comparison" in d:
            p = d["comparison"]
            components.append(table("Period comparison", ["Period", "Gross spending", "Observed transactions"],
                              [[f"{d['start']} – {d['end']}", d["gross_spending"], d["observed_count"]],
                               [f"{p['start']} – {p['end']}", p["gross_spending"], p["observed_count"]]]))
            components.append({"type": "notes", "items": [p["basis"]]})
    elif tool == "get_cash_forecast":
        components.extend([chart("Projected daily closing balance", [(r["date"], r["closing"]) for r in d["daily"]], cur, kind="line"),
                           table("Forecast", ["Date", "Projected closing balance"], [[r["date"], Money(Decimal(r["closing"]), cur).to_json()] for r in d["daily"]])])
        answer = f"Projected balances for {d['account_name']}, from {d['start']} to {d['end']}. These are forecasts, not settled balances."
    elif tool == "get_paycheck_plan":
        for p in d["paychecks"]:
            components.extend([table("Paycheck " + p["pay_date"], ["Allocation", "Amount", "Reason"],
                               [[a["name"], a["amount"], a["reason"]] for a in p["allocations"]]),
                               chart("Paycheck " + p["pay_date"], [(a["name"], a["amount"]) for a in p["allocations"]], cur)])
        answer = "This is your calculated paycheck split. Tell me which rule to change and the new amount or percentage to preview an update."
    elif tool == "compare_debt_strategies":
        components.extend([table("Debt payoff comparison", ["Strategy", "Months", "Interest", "Total paid", "Horizon limit reached"],
                           [[r["label"], r["months_to_clear"], r["total_interest"], r["total_paid"], r["truncated"]] for r in d["results"]]),
                           chart("Modeled interest cost", [(r["label"], r["total_interest"]) for r in d["results"]], cur)])
        answer = f"These modeled strategies use your recorded debts and {display(d['extra'])} extra per month. Review the model assumptions before changing payments."
    else:
        # Generic, finite table projection makes every existing calculator
        # usable inside chat while specialized views can be added gradually.
        rows = next((v for v in d.values() if isinstance(v, list) and v and isinstance(v[0], dict)), None)
        if rows:
            columns = [k for k in rows[0] if not k.startswith("_")][:6]
            components.append(table(tool.replace("_", " ").capitalize(), [k.replace("_", " ").capitalize() for k in columns],
                              [[r.get(k) for k in columns] for r in rows]))
        else:
            components.append(table("Calculation", ["Measure", "Result"],
                              [[k.replace("_", " ").capitalize(), v] for k, v in d.items() if not k.startswith("_")]))
        if d.get("explanation"):
            answer = str(d["explanation"])
    notes = []
    for key in ("notes", "assumptions", "note", "caveat", "rule", "objective_note"):
        if d.get(key):
            notes.extend(d[key] if isinstance(d[key], list) else [d[key]])
    # Debt strategy assumptions live on each result, not at the top level.
    for result in d.get("results", []):
        if isinstance(result, dict):
            notes.extend(result.get("assumptions", []))
    if notes:
        components.append({"type": "notes", "items": list(dict.fromkeys(str(n) for n in notes))})
    return components, answer
