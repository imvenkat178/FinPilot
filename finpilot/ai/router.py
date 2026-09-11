"""Deterministic intent router and answer templates.

Two jobs:

*   Decide which calculation tool answers a question, and with what arguments.
    A small local model is unreliable at free-choice tool calling, so the graph
    uses this router to constrain the choice and lets the model do what it is
    good at -- reading a JSON result and writing a sentence. Where the model is
    capable, `graph.py` can hand it the tool schemas instead.

*   Produce a correct, grounded answer with no model at all. This is the
    fallback whenever the local model is unreachable, and it is also the
    reference answer the grounding check compares against.

Every question in the specification's section 10 table is covered.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Optional

from .guardrails import AnswerState

# ---------------------------------------------------------------------------
# Intent table
# ---------------------------------------------------------------------------

@dataclass
class Intent:
    name: str
    tool: str
    patterns: list[str]
    weight: int = 1
    state: AnswerState = AnswerState.INFORMATIONAL
    args: dict = field(default_factory=dict)


INTENTS: list[Intent] = [
    Intent("paycheck_split", "get_paycheck_plan", [
        r"split (my |the |friday'?s? |this )?(pay|paycheck|income|salary|money)",
        r"how (should|do) i (split|allocate|divide|spread)",
        r"\b(paycheck|pay) (plan|allocation|split)\b",
        r"allocate (my |this )?(paycheck|pay|income)",
        r"where (should|does) my (pay|money) go",
        r"monthly (plan|budget|split|allocation)",
    ], 3, AnswerState.SAVED_PLAN),

    Intent("money_overview", "get_money_overview", [
        r"\b(all|total|how much) (of )?my money\b",
        r"\bnet worth\b", r"\bacross (all )?(my )?accounts\b",
        r"\b(overview|dashboard|summary) of (my )?(money|finances|accounts)\b",
        r"how much (do i have|money do i have)",
        r"\bwhat('?s| is) my (balance|position)\b",
    ], 2),

    Intent("spending_allowance", "get_spending_allowance", [
        r"\bcan i afford\b", r"\bhow much can i spend\b",
        r"\bsafe to spend\b", r"\bavailable (spending|to spend)\b",
        r"\bwhy did my (available )?spending (fall|drop|go down)\b",
        r"\bspending allowance\b",
    ], 3),

    Intent("cash_forecast", "get_cash_forecast", [
        r"\bforecast\b", r"\bwill i (go|run) (negative|out of money|short)\b",
        r"\blow(est)? balance\b", r"\bproject(ed|ion)\b",
        r"\bbalance (over|for) the next\b",
        r"\b(balance|account|checking)\b[^.?]*\b(go|goes|going) negative\b",
        r"\bovderdraw\b", r"\boverdraft\b", r"\brun out of (money|cash)\b",
    ], 2),

    Intent("upcoming_bills", "get_upcoming_obligations", [
        r"\bwhat('?s| is) (due|coming up|next)\b", r"\bupcoming (bills?|payments?)\b",
        r"\bbills? (due|this month|next)\b", r"\bwhat do i owe\b",
        r"\bwhat bills\b", r"\bbills?\b[^.?]*\bcoming up\b",
        r"\bpayments?\b[^.?]*\bcoming up\b",
    ], 2),

    Intent("debt_compare", "compare_debt_strategies", [
        r"\b(snowball|avalanche)\b", r"\bpay(ing)? off (my )?debt\b",
        r"\bdebt (plan|strategy|payoff|order)\b",
        r"\bwhich debt (should i|to) pay\b",
        r"\brepayment (plan|comparison|strategy)\b",
        r"\bminimi[sz]e (my )?interest\b",
        r"\bbest way to pay (off|down)\b",
    ], 3, AnswerState.HYPOTHETICAL),

    Intent("extra_payment", "what_if_extra_payment", [
        r"what if i pay (another |an extra )?\$?\d",
        r"\b(extra|additional) \$?\d+ (a month|per month|toward|towards|to)\b",
        r"\bif i (add|put) \$?\d+\b",
        r"\bpay \$?\d+ (more|extra)\b",
    ], 4, AnswerState.HYPOTHETICAL),

    Intent("mortgage", "get_mortgage_scenarios", [
        r"\bmortgage\b.*\b(extra|lump|recast|principal|prepay|pay ?off)\b",
        r"\b(recast|lump sum)\b", r"\bpay (down|off) (my )?(the )?mortgage\b",
        r"\bextra principal\b",
    ], 3, AnswerState.HYPOTHETICAL),

    Intent("biweekly", "compare_biweekly_mortgage", [
        r"\bbi-?weekly\b", r"\bfortnightly (mortgage|payment)\b",
        r"\b26 payments\b", r"\bhalf payments?\b",
    ], 4, AnswerState.HYPOTHETICAL),

    Intent("promo_payoff", "plan_promotional_payoff", [
        r"\b0%\b", r"\bpromotional? (balance|period|rate)\b",
        r"\bdeferred interest\b", r"\bpromo (expires|ends|deadline)\b",
    ], 3, AnswerState.HYPOTHETICAL),

    Intent("card_choice", "choose_card", [
        r"\bwhich (of my )?cards?\b", r"\bwhat card (should|do) i use\b",
        r"\bbest card for\b", r"\bpay (for|with) (this|a|an|my)\b.*\bcard\b",
        r"\bcard (should|to) use\b", r"\bwhich card\b",
        r"\b(uber|lyft|dinner|restaurant|groceries|hotel|flight|fuel|gas)\b.*\bcard\b",
        r"\bcard\b.*\b(uber|lyft|dinner|restaurant|groceries|hotel|flight|fuel|gas)\b",
    ], 4),

    Intent("bill_card_vs_bank", "compare_card_vs_bank_for_bill", [
        r"\b(pay|paying) (a |my |the )?bill (with|by|on) (a |my )?card\b",
        r"\bconvenience fee\b", r"\bprocessing fee\b", r"\bsurcharge\b",
        r"\bautopay discount\b",
    ], 4),

    Intent("booking_channel", "compare_booking_channels", [
        r"\bportal\b", r"\bbook (direct|through)\b", r"\bdirect (booking|rate)\b",
    ], 3, AnswerState.HYPOTHETICAL),

    Intent("utilization", "check_utilization_timing", [
        r"\butili[sz]ation\b", r"\bcredit score\b", r"\bstatement clos\w+\b",
        r"\bbefore (the )?statement\b", r"\breported balance\b",
    ], 4),

    Intent("buffer", "get_buffer", [
        r"\bbuffer\b", r"\bcushion\b", r"\bcash floor\b",
        r"\bhow much (should|do) i (keep|leave) in (my )?checking\b",
        r"\bsweep\b.*\bhow much\b", r"\bspare cash\b",
    ], 3),

    Intent("sweep", "assess_sweep_move", [
        r"\b(should i |can i )?move \$?\d+.*\b(savings|high.?yield|earn)\b",
        r"\bworth moving\b", r"\btransfer \$?\d+ to (my )?savings\b",
    ], 4, AnswerState.HYPOTHETICAL),

    Intent("liquidity", "get_liquidity_tiers", [
        r"\bliquid\b", r"\bhow (fast|quickly) can i (get|access)\b",
        r"\bwhat can i actually (spend|access|use)\b",
        r"\breally have\b",
    ], 2),

    Intent("timing", "compare_payment_timing", [
        r"\bwhen should i pay\b", r"\bpay (now|early|late) or\b",
        r"\bhold (the |my )?(cash|money)\b", r"\bwait to pay\b",
    ], 3, AnswerState.HYPOTHETICAL),

    Intent("save_vs_debt", "compare_savings_vs_debt", [
        r"\bsave or pay (off|down)\b", r"\bpay (off|down) debt or save\b",
        r"\bsave\b[^.?]*\bor pay (off|down)\b",
        r"\bpay (off|down)\b[^.?]*\bor save\b",
        r"\b(better|worth it) to (save|pay off)\b",
        r"\bafter.?tax\b", r"\bnet benefit\b",
        r"\bapy\b.*\bapr\b", r"\bapr\b.*\bapy\b",
    ], 4, AnswerState.HYPOTHETICAL),

    Intent("deduction", "check_interest_deduction", [
        r"\b(tax )?deduct\w*\b", r"\bwrite off\b", r"\bdeduction\b",
    ], 3),

    Intent("coverage", "get_deposit_coverage", [
        r"\b(fdic|ncua|insured|insurance)\b", r"\bcovered\b.*\bbank\b",
        r"\bis my (cash|money) (safe|insured|protected)\b",
        r"\bdeposit (protection|coverage|insurance)\b",
        r"\bdifferent platforms\b",
    ], 4),

    Intent("coverage_remedy", "plan_coverage_remedy", [
        r"\bover the (fdic|ncua|insurance) limit\b", r"\buninsured\b",
        r"\bhow much should i move\b.*\b(insur|cover)\b",
    ], 4, AnswerState.HYPOTHETICAL),

    Intent("collateral", "stress_collateral_line", [
        r"\b(sbloc|margin|collateral|pledged|securities.?backed)\b",
        r"\bmargin call\b", r"\badvance rate\b",
    ], 4, AnswerState.HYPOTHETICAL),

    Intent("automation", "get_automation_status", [
        r"\b(automation|recurring|standing|automatic) (rules?|transfers?|payments?)\b",
        r"\bwhat('?s| is) (set up|automated|running)\b",
        r"\b(rules?|automation)\b[^.?]{0,20}\b(set up|do i have|are running)\b",
        r"\bpause\b",
        r"\bevery (month|payday|paycheck)\b",
    ], 3, AnswerState.AUTHORIZED),

    Intent("account_connections", "get_account_connections", [
        r"\b(connection|link)s? (health|status|issue|problem|broken|working)\b",
        r"\bconnection (is|still)\b", r"\bstill (working|connected)\b",
        r"\b(re-?authenticat|reconnect|re-?link)\w*\b",
        r"\b(stale|not (updating|syncing|synced|refresh(ed|ing)?))\b",
        r"\bis (my|the) [\w\s]{0,20}\bconnect(ed|ion)\b",
        r"\bwhy (is|are) (my|the) (balance|account)s? (not updating|out of date|old)\b",
    ], 5),

    Intent("recurring_activity", "get_recurring_activity", [
        r"\bupcoming (runs?|transfers?|occurrences?)\b",
        r"\bnext (run|occurrence)\b",
        r"\bwhen (will|does) (my|the|a) (rule|transfer|payment) (run|happen|trigger)\b",
        r"\bwill (it|this|that|this rule|that rule|my \w+ (rule|payment|transfer)|"
        r"the \w+ (rule|payment|transfer))\b[^.?]{0,20}\brun\b",
        r"\bunresolved payments?\b",
        r"\bwhich rules? (won'?t|will not) run\b",
    ], 5),

    Intent("transfer_failure", "explain_transfer_outcome", [
        r"\bwhy (did|didn'?t|has|hasn'?t)\b.*\b(transfer|payment|run|go through)\b",
        r"\b(transfer|payment) (fail|failed|not run|didn'?t run|stuck|pending)\b",
        r"\bwhat happened to (my|the) (transfer|payment)\b",
    ], 4, AnswerState.CONFIRMED_EXTERNAL),

    Intent("income_loss", "stress_income_loss", [
        r"\blose (my )?(job|income)\b", r"\bif i (stop|lost) (getting paid|working)\b",
        r"\bmonth of (no |lost )?income\b", r"\bunemploy\w*\b",
        r"\bwhat happens if i lose\b",
    ], 4, AnswerState.HYPOTHETICAL),

    Intent("boundary", "explain_product_boundary", [
        r"\bwhich (stock|etf|fund|investment)s? should i\b",
        r"\bshould i (buy|sell|invest)\b",
        r"\bwhat should i invest in\b",
        r"\bopen a (new )?(account|card)\b",
    ], 5),
]


_NUM = re.compile(r"\$?\s?(\d[\d,]*(?:\.\d+)?)")
_CATEGORY_WORDS = {
    "dining": ["dinner", "restaurant", "lunch", "eat", "dining", "takeout",
               "food delivery", "doordash", "meal"],
    "groceries": ["grocery", "groceries", "supermarket", "food shop"],
    "travel": ["hotel", "flight", "airline", "travel", "uber", "lyft", "taxi",
               "ride", "booking", "trip"],
    "fuel": ["gas", "fuel", "petrol", "gas station"],
    "transit": ["transit", "subway", "metro", "bus", "train"],
    "subscriptions": ["subscription", "netflix", "spotify", "streaming"],
    "utilities": ["utility", "utilities", "electric", "phone bill", "internet"],
    "shopping": ["shopping", "amazon", "store", "retail"],
}


def extract_amount(text: str) -> Optional[float]:
    m = _NUM.search(text or "")
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def extract_category(text: str) -> str:
    low = (text or "").lower()
    for cat, words in _CATEGORY_WORDS.items():
        if any(w in low for w in words):
            return cat
    return "base"


@dataclass
class RouteResult:
    intent: str
    tool: str
    arguments: dict
    state: AnswerState
    score: int
    alternatives: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {"intent": self.intent, "tool": self.tool,
                "arguments": self.arguments, "state": self.state.value,
                "score": self.score, "alternatives": self.alternatives}


def route(question: str) -> RouteResult:
    q = (question or "").strip()
    low = q.lower()
    scores: list[tuple[int, Intent]] = []
    for it in INTENTS:
        hits = sum(1 for p in it.patterns if re.search(p, low))
        if hits:
            scores.append((hits * it.weight, it))
    scores.sort(key=lambda t: -t[0])

    if not scores:
        return RouteResult("unknown", "get_money_overview", {},
                           AnswerState.INFORMATIONAL, 0)

    score, best = scores[0]
    args = _arguments_for(best, q, low)
    alts = [i.name for _, i in scores[1:4]]
    return RouteResult(best.name, best.tool, args, best.state, score, alts)


def _arguments_for(it: Intent, q: str, low: str) -> dict:
    amount = extract_amount(q)
    if it.tool == "what_if_extra_payment":
        return {"amount": amount or 300.0}
    if it.tool == "choose_card":
        return {"amount": amount or 50.0, "category": extract_category(low),
                "merchant": _merchant(low),
                "channel": "online" if any(w in low for w in
                                           ("online", "portal", "app", "delivery"))
                else "in_person",
                "foreign": any(w in low for w in ("abroad", "overseas", "foreign",
                                                  "in europe", "in japan"))}
    if it.tool == "compare_card_vs_bank_for_bill":
        return {"amount": amount or 1000.0}
    if it.tool == "assess_sweep_move":
        return {"amount": amount or 1000.0}
    if it.tool == "compare_savings_vs_debt":
        return {"amount": amount or 10000.0}
    if it.tool == "plan_promotional_payoff":
        return {"balance": amount or 3600.0, "paychecks_remaining": 6}
    if it.tool == "get_mortgage_scenarios":
        return {"lump_sum": amount} if amount and amount > 1000 else {}
    if it.tool == "stress_income_loss":
        m = re.search(r"(\d+)\s*month", low)
        return {"months": int(m.group(1)) if m else 1}
    if it.tool == "get_spending_allowance":
        m = re.search(r"(\d+)\s*(day|week)", low)
        if m:
            n = int(m.group(1))
            return {"days": n * 7 if m.group(2) == "week" else n}
        return {"days": 14}
    if it.tool == "compare_debt_strategies":
        if amount:
            return {"extra_payment": amount}
        return {}
    if it.tool == "check_interest_deduction":
        for t in ("mortgage", "student", "credit card", "auto", "investment"):
            if t in low:
                return {"loan_type": {"student": "student_loan",
                                      "credit card": "credit_card",
                                      "auto": "auto_loan"}.get(t, t)}
        return {"loan_type": "mortgage"}
    if it.tool == "plan_coverage_remedy":
        return {"source_institution": "bank_meridian"}
    if it.tool == "stress_collateral_line":
        return {"collateral_value": 1000000, "advance_rate": 0.5, "drawn": 350000,
                "decline": 0.30, "reduced_advance_rate": 0.40}
    if it.tool == "compare_payment_timing":
        return {"amount": amount or 1000.0,
                "due_date": (date.today() + timedelta(days=14)).isoformat()}
    if it.tool == "compare_booking_channels":
        nums = [float(n.replace(",", "")) for n in _NUM.findall(q)][:2]
        if len(nums) == 2:
            return {"direct_price": nums[0], "portal_price": nums[1]}
        return {"direct_price": 500.0, "portal_price": 535.0}
    return dict(it.args)


def _merchant(low: str) -> str:
    for m in ("uber", "lyft", "amazon", "netflix", "costco", "walmart", "target",
              "starbucks", "doordash"):
        if m in low:
            return m.title()
    return ""


# ---------------------------------------------------------------------------
# Deterministic answer templates -- used when no model is reachable, and as the
# reference the grounding check falls back to.
# ---------------------------------------------------------------------------

def _mv(d: Any) -> str:
    if isinstance(d, dict) and "display" in d:
        return d["display"]
    return str(d)


def template_answer(intent: str, result: dict) -> str:
    if result.get("error"):
        return (f"I could not answer that: {result['error']}. Nothing has been "
                "calculated or changed.")

    if intent == "paycheck_split":
        lines = []
        pays = result.get("paychecks", [])
        for p in pays:
            funded = [a for a in p["allocations"]
                      if a["status"] in ("funded", "partial") and
                      float(a["amount"]["amount"]) > 0]
            head = (f"Paycheck on {p['pay_date']} — {_mv(p['available'])} in, "
                    f"{_mv(p['allocated'])} allocated:")
            lines.append(head)
            for a in funded:
                lines.append(f"  {_mv(a['amount'])} to {a['name']} ({a['reason']})")
            if p["shortfalls"]:
                for s in p["shortfalls"]:
                    lines.append(f"  Shortfall: {s['name']} needs {_mv(s['gap'])} more "
                                 f"by {s['due_date']} — {s['consequence']}")
        plan = result.get("plan", {})
        lines.insert(0, f"Your {result.get('month')} plan targets "
                        f"{_mv(plan.get('total_target'))} against expected income of "
                        f"{_mv(plan.get('expected_income'))}.")
        lines.append(result.get("rule", ""))
        return "\n".join(l for l in lines if l)

    if intent == "money_overview":
        return (f"Across your included accounts you hold "
                f"{_mv(result['total_cash'])} in cash, of which "
                f"{_mv(result['spendable_now'])} is spendable now. Debt totals "
                f"{_mv(result['total_debt'])}, so estimated net worth is "
                f"{_mv(result['net_worth'])} as of {result['as_of']}. "
                f"{result['notes'][0]}")

    if intent == "spending_allowance":
        return (f"You can spend about {_mv(result['amount'])} through "
                f"{result['through']}. That is set by the lowest projected balance "
                f"of {_mv(result['low_point_balance'])} on {result['low_point_date']}, "
                f"after {_mv(result['required_before'])} of required bills and "
                f"{_mv(result['protected'])} of protected reserves. "
                f"Confidence: {result['confidence']}."
                + (f" Still to confirm: {'; '.join(result['missing'])}."
                   if result.get("missing") else ""))

    if intent == "card_choice":
        if not result.get("winner"):
            return result.get("explanation", "No eligible card for this purchase.")
        w = result["winner"]
        parts = [result["explanation"]]
        if w.get("conditions"):
            parts.append(" ".join(w["conditions"]))
        return " ".join(parts)

    if intent in ("debt_compare",):
        rows = result.get("results", [])
        if not rows:
            return "No debts are on file to compare."
        out = [f"Under one budget of {_mv(result['budget'])} "
               f"({_mv(result['required_total'])} required, {_mv(result['extra'])} extra):"]
        for r in rows:
            fp = r.get("first_payoff")
            out.append(f"  {r['label']}: clears in {r['months_to_clear']} months, "
                       f"{_mv(r['total_interest'])} interest"
                       + (f", first payoff {fp['debt']} in month {fp['month']}"
                          if fp else "")
                       + (f", {_mv(r['interest_premium_vs_lowest_cost'])} more than "
                          "the lowest-cost plan"
                          if float(r['interest_premium_vs_lowest_cost']['amount']) > 0
                          else ""))
        out.append(result.get("objective_note", ""))
        return "\n".join(l for l in out if l)

    if intent == "extra_payment":
        return (f"Adding {_mv(result['extra_per_month'])} a month clears the debt in "
                f"{result['with_extra']['months']} months instead of "
                f"{result['without_extra']['months']}, saving "
                f"{_mv(result['interest_saved'])} of modelled interest "
                f"({result['months_saved']} months earlier). {result['caveat']}")

    if intent == "coverage":
        b = result.get("buckets", [])
        lead = (f"Total estimated uncovered deposits: "
                f"{_mv(result['total_uncovered'])} as of {result['as_of']}.")
        rows = [f"  {x['institution_name']} ({x['owner']}, {x['category']}): "
                f"{_mv(x['total'])} against a {_mv(x['limit'])} limit — "
                f"{_mv(x['uncovered'])} uncovered" for x in b if x]
        tail = result["caveats"][0]
        extra = ("\n  Unresolved: " + "; ".join(result["unresolved"])) \
            if result.get("unresolved") else ""
        return "\n".join([lead] + rows) + extra + "\n" + tail

    if intent == "income_loss":
        return (f"With {result['scenario']}, required obligations come to "
                f"{_mv(result['total_required_over_period'])} against "
                f"{_mv(result['available_cash'])} of available cash and "
                f"{_mv(result['emergency_reserve'])} in the emergency reserve. "
                f"Unmet: {_mv(result['unmet'])}. "
                + " ".join(result["changes_required"]) + " " + result["note"])

    if intent == "transfer_failure":
        return (f"That leg is {result['state']}. Reason: {result['actual_reason']}. "
                f"It affects {result['affected_obligation']} for "
                f"{_mv(result['amount'])}. "
                + " ".join(result["available_actions"]) + " " + result["note"])

    if intent == "automation":
        rows = result.get("policies", [])
        active = [r for r in rows if not r["paused"]]
        out = [f"You have {len(active)} active rules of {len(rows)} total"
               + (" (all future runs are currently paused)."
                  if result.get("globally_paused") else ".")]
        for r in active[:12]:
            out.append(f"  {r['name']}: {_mv(r['amount'])} to {r['destination']} "
                       f"under a {r['authorization']} authorization"
                       + (f", capped at {_mv(r['per_run_cap'])} per run"
                          if r.get("per_run_cap") else ""))
        out.append(result["control"])
        return "\n".join(out)

    if intent == "account_connections":
        unhealthy = result.get("unhealthy", [])
        if not unhealthy:
            return (f"All {len(result.get('accounts', []))} accounts have a healthy "
                     "connection right now.")
        out = [f"{len(unhealthy)} account connection(s) need attention:"]
        for a in unhealthy:
            when = f" (last synced {a['last_synced_at'][:10]})" if a.get("last_synced_at") else ""
            out.append(f"  {a['name']}: {a['issue']}{when}"
                       + (" — this affects figures that read from it."
                          if a.get("affects_planning") else ""))
        out.append(result["note"])
        return "\n".join(out)

    if intent == "recurring_activity":
        runs = result.get("upcoming_runs", [])
        blocked = result.get("will_not_run", [])
        out = [f"{len(runs)} occurrence(s) in the next {result.get('horizon_days')} days, "
               f"{len(blocked)} of them won't run as scheduled:"]
        for r in blocked[:8]:
            out.append(f"  {r['name']} on {r['date']}: {r['reason_if_not']}")
        if not blocked:
            out.append("Every upcoming occurrence is on track to run.")
        for u in result.get("unresolved_policies", [])[:5]:
            out.append(f"  Unresolved: {u['name']} — {u['issue']}")
        out.append(result["note"])
        return "\n".join(out)

    if intent == "buffer":
        return (f"{result['account_name']} should retain "
                f"{_mv(result['required_retained'])} of its "
                f"{_mv(result['available'])} available: "
                f"{_mv(result['scheduled_outflows'])} of scheduled outflows, "
                f"{_mv(result['operating_floor'])} operating floor and "
                f"{_mv(result['uncertainty_allowance'])} uncertainty allowance. "
                f"That leaves {_mv(result['sweepable'])} genuinely sweepable. "
                f"{result['assumptions'][0]}")

    if intent == "boundary":
        return (result["reason"] + " What it will do: "
                + "; ".join(result["in_scope"][:3]) + ".")

    if intent == "utilization":
        return f"{result['finding']} {result['grace_period_effect']} {result['caveat']}"

    if intent == "save_vs_debt":
        base = result["baseline"]
        out = [f"Keeping {_mv(result['amount'])} in the deposit account nets "
               f"{_mv(base['one_year_net_benefit'])} over {result['horizon_days']} days "
               f"after an estimated {result['tax_profile']['combined_marginal']} "
               "incremental tax rate."]
        for r in result["rows"]:
            out.append(f"  {r['label']}: {_mv(r['one_year_net_benefit'])} "
                       f"({_mv(r['versus_baseline'])} against keeping the cash)")
        out.append(result["assumptions"][0])
        return "\n".join(out)

    if intent == "upcoming_bills":
        out = [f"{result['count']} obligations totalling {_mv(result['total_due'])} "
               f"in the next {result['window_days']} days:"]
        for o in result["obligations"][:12]:
            out.append(f"  {o['due_date']}: {o['name']} {_mv(o['amount'])} from "
                       f"{o['funding_account']} ({o['execution_owner']})")
        return "\n".join(out)

    if intent == "liquidity":
        out = [f"Near-term cash across your accounts: {_mv(result['near_term_cash'])}."]
        for t in result["tiers"]:
            out.append(f"  {t['name']}: {_mv(t['available'])} — {t['tier']}. {t['treatment']}")
        out.append(result["note"])
        return "\n".join(out)

    if intent == "mortgage":
        out = [f"Mortgage balance {_mv(result['mortgage']['balance'])} at "
               f"{result['mortgage']['apr']}, principal and interest "
               f"{_mv(result['mortgage']['principal_and_interest'])} plus "
               f"{_mv(result['mortgage']['escrow'])} escrow."]
        for s in result["scenarios"]:
            out.append(f"  {s['name']}: {s['months_remaining']} months, "
                       f"{_mv(s['lifetime_interest'])} lifetime interest, "
                       f"{_mv(s['monthly_principal_interest'])} monthly P&I. {s['note']}")
        out.append(result["note"])
        return "\n".join(out)

    if intent == "cash_forecast":
        lp = result.get("low_point") or {}
        neg = result.get("negative_days") or []
        return (f"{result['account_name']} is projected to end at "
                f"{_mv(result['ending_balance'])} on {result['end']}, with a low point "
                f"of {_mv(lp.get('balance'))} on {lp.get('date')}."
                + (f" It would go negative on {', '.join(neg[:3])}." if neg
                   else " It does not go negative in this window."))

    if intent == "bill_card_vs_bank":
        return (f"{result['finding']} The card earns {_mv(result['card']['reward'])} "
                f"but the processing fee is {_mv(result['card']['processing_fee'])}, "
                f"netting {_mv(result['card']['net'])} against "
                f"{_mv(result['bank']['net'])} by bank.")

    if intent == "booking_channel":
        d, p = result["direct"], result["portal"]
        return (f"{result['finding']} Direct: {_mv(d['price'])} less "
                f"{_mv(d['reward'])} rewards is {_mv(d['net_cost'])}. Portal: "
                f"{_mv(p['price'])} less {_mv(p['reward'])} is {_mv(p['net_cost'])}. "
                f"{result['caveat']}")

    if intent == "deduction":
        return (f"For a {result['loan_type'].replace('_', ' ')}, deductibility is "
                f"'{result['deductible']}'. {result['explanation']} This needs "
                "verification against your actual return.")

    if intent == "sweep":
        base = (f"{result['reason']}")
        if result.get("exceeds_sweepable"):
            base += " " + result["blocker"]
        return base

    if intent == "timing":
        return (f"{result['recommendation']}. {result['rationale']}"
                + (" " + result["credit_utilization_note"]
                   if result.get("credit_utilization_note") else ""))

    if intent == "collateral":
        out = []
        for r in result["rows"]:
            out.append(f"  {r['scenario']}: collateral {_mv(r['collateral_value'])} "
                       f"at {r['advance_rate']} gives {_mv(r['capacity'])} capacity, "
                       f"headroom {_mv(r['headroom'])}, deficiency {_mv(r['deficiency'])}")
        return "\n".join(["Collateral stress:"] + out + [result["warnings"][0]])

    if intent == "coverage_remedy":
        if result.get("error"):
            return result["error"]
        return (f"Move {_mv(result['recommended_move'])} from "
                f"{result['source']} — that clears {_mv(result['excess'])} of excess "
                f"plus a {_mv(result['buffer_below_limit'])} buffer, leaving "
                f"{_mv(result['source_after'])} there and "
                f"{_mv(result['destination_after'])} at {result['destination']}. "
                + " ".join(result["checks_required"]))

    if intent == "promo_payoff":
        if result.get("error"):
            return result["error"]
        return (f"Reserve {_mv(result['per_paycheck'])} from each of the "
                f"{result['paychecks_remaining']} remaining paychecks to clear the "
                f"{_mv(result['promotional_balance'])} balance. {result['note']}")

    if intent == "biweekly":
        return (f"{result['finding']} A half payment is {_mv(result['half_payment'])}; "
                f"26 of them total {_mv(result['annual_paid_biweekly'])} a year against "
                f"{_mv(result['annual_paid_monthly'])} monthly — "
                f"{_mv(result['extra_paid_per_year'])} more, equivalent to "
                f"{_mv(result['equalising_monthly_extra'])} extra each month.")

    # generic fallback: report the payload honestly rather than improvising
    keys = [k for k in result if not k.startswith("_")][:6]
    return ("Here is what the calculation returned: "
            + "; ".join(f"{k} = {_mv(result[k])}" for k in keys if not isinstance(
                result[k], (list, dict))))
