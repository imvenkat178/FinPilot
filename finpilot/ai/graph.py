"""The assistant, as a LangGraph state machine.

    scope_guard -> classify -> execute_tools -> compose -> verify -> finalize

The graph is deliberately *constrained*: the router decides which calculator
runs, and the model's job is to read the JSON result and write the sentence. A
1B-parameter local Llama is not a reliable free-choice tool caller, and the
specification does not want it to be one -- section 10 gives the model exactly
two jobs, explaining outputs and requesting missing inputs.

`ToolChoice.MODEL` switches to genuine model-driven tool calling for a capable
local model; the guardrails are identical either way, because they run on the
output, not on the model's good intentions.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Annotated, Any, Optional, TypedDict

from .facts import TOKEN, FactSheet, TokenError, figure_sources, mask, substitute, tokenize
from .guardrails import (AnswerState, GroundingReport, GuardVerdict, STATE_PREFIX,
                         SYSTEM_PROMPT, TOKEN_SYSTEM_PROMPT, advice_claim_error,
                         answer_is_substantive, check_grounding,
                         echoes_untrusted, fence_untrusted, scan_untrusted,
                         scope_check, spending_allowance_claim_error)
from .llm import LLMUnavailable, LocalLLM
from .router import RouteResult, route, template_answer
from .tools import ToolRegistry

try:
    from langgraph.graph import END, StateGraph
    LANGGRAPH = True
except Exception:                                    # pragma: no cover
    LANGGRAPH = False
    END = "__end__"


class ToolChoice(str, Enum):
    ROUTER = "router"      # deterministic selection (default, reliable on small models)
    MODEL = "model"        # model-driven tool calling
    AUTO = "auto"          # compatibility alias for ROUTER; no capability discovery


_DETERMINISTIC_CLARIFICATIONS = {
    "extra_payment_cadence_clarification", "card_location_clarification",
}


class AgentState(TypedDict, total=False):
    question: str
    untrusted_context: str
    tool_choice: str
    route: dict
    tool_results: list[dict]
    draft: str
    answer: str
    state: str
    grounding: dict
    guard: dict
    evidence: dict
    trace: list[dict]
    used_model: bool
    latency_ms: int
    error: Optional[str]
    inference_deadline: float
    facts: dict
    tokenized_reference: str
    cache: str
    cache_key: Optional[str]
    figures: list[dict]


@dataclass
class AgentResult:
    question: str
    answer: str
    state: AnswerState
    intent: str
    tools_called: list[str]
    tool_results: list[dict]
    used_model: bool
    grounding: dict
    guard: dict
    assumptions: list[str]
    confidence: str
    latency_ms: int
    trace: list[dict] = field(default_factory=list)
    figures: list[dict] = field(default_factory=list)
    wording_cache: str = "off"

    def to_json(self) -> dict:
        return {"question": self.question, "answer": self.answer,
                "state": self.state.value, "intent": self.intent,
                "tools_called": self.tools_called,
                "used_model": self.used_model, "grounding": self.grounding,
                "guard": self.guard, "assumptions": self.assumptions,
                "confidence": self.confidence, "latency_ms": self.latency_ms,
                "evidence": self.tool_results, "trace": self.trace,
                "figures": self.figures, "wording_cache": self.wording_cache}


COMPOSE_VERSION = "figure-tokens-1"
COMPOSE_TOKENS = """QUESTION FROM THE USER
{question}

REFERENCE ANSWER WITH FIGURE TOKENS
{reference}

Rewrite the reference answer for the user in clear, natural sentences. Keep the [F#] tokens exactly where their figures belong.{notes}

Write the answer now."""


class FinanceAgent:
    def __init__(self, registry: ToolRegistry, llm: Optional[LocalLLM] = None,
                 tool_choice: ToolChoice = ToolChoice.ROUTER,
                 max_tool_calls: int = 3, *, compile_graph: bool = True,
                 default_account_id: Optional[str] = None,
                 conversation_context: str = "", route_override: Optional[RouteResult] = None,
                 wording_cache=None, cache_scope: str = ""):
        self.tools = registry
        self.wording_cache = wording_cache
        self.cache_scope = cache_scope
        self.llm = llm or LocalLLM()
        self.tool_choice = tool_choice
        self.max_tool_calls = max_tool_calls
        self.default_account_id = default_account_id
        self.conversation_context = conversation_context
        self.route_override = route_override
        self._graph = self._build() if LANGGRAPH and compile_graph else None

    # ------------------------------------------------------------------
    # nodes
    # ------------------------------------------------------------------
    def n_scope_guard(self, s: AgentState) -> AgentState:
        q = s.get("question", "")
        trace = s.setdefault("trace", [])
        scan = scan_untrusted(q)
        untrusted_scan = scan_untrusted(s.get("untrusted_context", ""))
        verdict = {"question_scan": scan.to_json(),
                   "context_scan": untrusted_scan.to_json()}

        if scan.suspicious:
            # An instruction-shaped user message is not itself an attack, but it
            # never gains authority: tools still enforce ownership and mandates.
            trace.append({"node": "scope_guard",
                          "note": "instruction-shaped phrasing detected; tool "
                                  "permissions are unaffected",
                          "matches": scan.matches})
        boundary = scope_check(q)
        if boundary:
            kind, text = boundary
            verdict["boundary"] = kind
            trace.append({"node": "scope_guard", "boundary": kind})
            # a refusal still shows its evidence: what the product does and does
            # not do comes from the same tool as every other answer
            evidence = self.tools.call("explain_product_boundary", {"topic": kind})
            return {**s, "guard": verdict, "answer": text,
                    "state": AnswerState.REFUSED.value,
                    "route": {"intent": "boundary", "tool": "explain_product_boundary",
                              "arguments": {"topic": kind}, "state": "refused",
                              "score": 99, "alternatives": []},
                    "tool_results": [evidence], "used_model": False, "trace": trace}
        return {**s, "guard": verdict, "trace": trace}

    def n_classify(self, s: AgentState) -> AgentState:
        if s.get("answer"):
            return s
        trace = s.setdefault("trace", [])
        r = self.route_override or route(s.get("question", ""))
        r.arguments = self._context_arguments(r.tool, r.arguments)
        trace.append({"node": "classify", "intent": r.intent, "tool": r.tool,
                      "score": r.score, "alternatives": r.alternatives})
        return {**s, "route": r.to_json(), "trace": trace}

    def n_execute(self, s: AgentState) -> AgentState:
        if s.get("answer"):
            return s
        trace = s.setdefault("trace", [])
        r = s["route"]
        results: list[dict] = []

        if self._model_tool_calling(s):
            results = self._model_driven_tools(s, trace)
        if results and results[0].get("_tool") != r["tool"]:
            # A model may read useful related evidence, but a forecast payload
            # cannot be passed to a debt template (or inherit its answer state).
            trace.append({"node": "execute", "mode": "router_fallback",
                          "reason": "model primary tool did not match the routed answer",
                          "model_tool": results[0].get("_tool"), "tool": r["tool"]})
            results.insert(0, self.tools.call(r["tool"], r["arguments"]))
        if not results:
            res = self.tools.call(r["tool"], r["arguments"])
            results = [res]
            trace.append({"node": "execute", "tool": r["tool"],
                          "arguments": r["arguments"],
                          "ok": not res.get("error")})

        # a question about affordability needs both the allowance and the card
        if r["intent"] == "card_choice" and self.tools.spec("get_spending_allowance"):
            extra = self.tools.call("get_spending_allowance", {"days": 30})
            results.append(extra)
            trace.append({"node": "execute", "tool": "get_spending_allowance",
                          "reason": "repayment feasibility for a card purchase"})
        return {**s, "tool_results": results, "trace": trace}

    def n_compose(self, s: AgentState) -> AgentState:
        if s.get("answer"):
            return s
        trace = s.setdefault("trace", [])
        r = s["route"]
        primary = s["tool_results"][0] if s["tool_results"] else {}
        reference = template_answer(r["intent"], primary)

        if r["intent"] in _DETERMINISTIC_CLARIFICATIONS or r["intent"] == "unknown":
            # Fixed guidance needs no model, so an unrelated prompt never receives model wording.
            trace.append({"node": "compose", "mode": "clarification"})
            return {**s, "answer": reference, "draft": reference, "used_model": False,
                    "trace": trace}

        if primary.get("error"):
            trace.append({"node": "compose", "mode": "error_passthrough"})
            return {**s, "answer": reference, "draft": reference, "used_model": False,
                    "trace": trace}

        if not self.llm.available:
            trace.append({"node": "compose", "mode": "template",
                          "reason": "no local model reachable"})
            return {**s, "answer": reference, "draft": reference, "used_model": False,
                    "trace": trace}

        sheet = tokenize(reference)
        user = COMPOSE_TOKENS.format(question=mask(s["question"], sheet), reference=sheet.tokenized,
                                     notes=_figure_notes(r["intent"], primary, sheet))
        if self.conversation_context:
            user += ("\n\nPrevious conversation for language context only; its figures are hidden.\n"
                     + fence_untrusted("prior conversation", mask(self.conversation_context)))
        if s.get("untrusted_context"):
            user = fence_untrusted("retrieved document", mask(s["untrusted_context"])) + "\n\n" + user
        facts = {"facts": sheet.facts, "tokenized_reference": sheet.tokenized}
        cache_key = None
        if self.wording_cache is not None:
            cache_key = self.wording_cache.key(self.cache_scope, "compose", model=self.llm.config.model,
                                               version=COMPOSE_VERSION, system=TOKEN_SYSTEM_PROMPT, user=user)
            cached = self.wording_cache.get(cache_key)
            if cached:
                trace.append({"node": "compose", "mode": "model", "cache": "hit",
                              "model": self.llm.config.model, "chars": len(cached)})
                return {**s, **facts, "draft": cached, "used_model": True, "cache": "hit",
                        "cache_key": cache_key, "trace": trace}
        try:
            t0 = time.monotonic()
            draft = self.llm.complete(TOKEN_SYSTEM_PROMPT, user,
                                      timeout=self._remaining_budget(s)).strip()
            if not draft:
                raise LLMUnavailable("The local model returned no answer.")
            trace.append({"node": "compose", "mode": "model",
                          "model": self.llm.config.model,
                          "ms": int((time.monotonic() - t0) * 1000),
                          "chars": len(draft), "cache": "miss" if cache_key else "off"})
            return {**s, **facts, "draft": draft, "used_model": True,
                    "cache": "miss" if cache_key else "off", "cache_key": cache_key, "trace": trace}
        except LLMUnavailable as e:
            trace.append({"node": "compose", "mode": "template",
                          "reason": f"model call failed: {e}"})
            return {**s, "answer": reference, "draft": reference, "used_model": False,
                    "trace": trace}

    def n_verify(self, s: AgentState) -> AgentState:
        if s.get("answer") and not s.get("draft"):
            return s
        trace = s.setdefault("trace", [])
        r = s["route"]
        primary = s["tool_results"][0] if s["tool_results"] else {}
        reference = template_answer(r["intent"], primary)
        draft = s.get("draft") or reference
        if s.get("used_model") and s.get("facts") is not None:
            try:
                draft, _ = substitute(draft, FactSheet(s.get("tokenized_reference", ""), s["facts"]))
            except TokenError as exc:
                # Report typed figures the way numeric grounding sees them, so a reviewer sees what was invented.
                typed = check_grounding(TOKEN.sub(" ", draft), s.get("tool_results", []))
                trace.append({"node": "verify", "result": "rejected", "reason": str(exc),
                              "action": "fell back to the deterministic answer"})
                return {**s, "answer": reference, "used_model": False,
                        "grounding": {"ok": False, "ungrounded": typed.ungrounded, "checked": typed.checked,
                                      "rejected_for": "figure_tokens"}, "trace": trace}

        if s.get("used_model") and r["intent"] == "coverage" and not _coverage_caveats_present(draft):
            trace.append({"node": "verify", "result": "rejected",
                          "reason": "coverage wording omitted the estimate or ownership-verification caveat",
                          "action": "fell back to the deterministic answer"})
            return {**s, "answer": reference, "used_model": False,
                    "grounding": {"ok": False, "ungrounded": [], "checked": 0,
                                  "rejected_for": "coverage_caveat"}, "trace": trace}

        # an echoed injection carries no figures, so it must be checked before
        # the numeric grounding test, not after it
        echo = echoes_untrusted(draft, s.get("untrusted_context", ""))
        if echo:
            trace.append({"node": "verify", "result": "rejected", "reason": echo,
                          "action": "fell back to the deterministic answer"})
            return {**s, "answer": reference, "used_model": False,
                    "grounding": {"ok": False, "ungrounded": [], "checked": 0,
                                  "rejected_for": "untrusted_echo"}, "trace": trace}

        thin = answer_is_substantive(draft, reference)
        if thin:
            trace.append({"node": "verify", "result": "rejected", "reason": thin,
                          "action": "fell back to the deterministic answer"})
            return {**s, "answer": reference, "used_model": False,
                    "grounding": {"ok": False, "ungrounded": [], "checked": 0,
                                  "rejected_for": "not_substantive"}, "trace": trace}

        sources = list(s.get("tool_results", []))
        if s.get("used_model") and s.get("facts") is not None:
            # Every figure in a token draft was inserted from the deterministic reference answer.
            sources.append(list(s["facts"].values()))
        report = check_grounding(draft, sources)
        if not report.ok:
            trace.append({"node": "verify", "result": "rejected",
                          "ungrounded": report.ungrounded,
                          "action": "fell back to the deterministic answer"})
            return {**s, "answer": reference, "grounding": report.to_json(),
                    "used_model": False, "trace": trace}

        if s.get("used_model") and r["intent"] == "spending_allowance":
            allowance_error = spending_allowance_claim_error(draft, primary)
            if allowance_error:
                trace.append({"node": "verify", "result": "rejected", "reason": allowance_error,
                              "action": "fell back to the deterministic answer"})
                return {**s, "answer": reference, "used_model": False,
                        "grounding": {"ok": False, "ungrounded": [], "checked": report.checked,
                                      "rejected_for": "spending_allowance_claim"}, "trace": trace}

        forbidden = _claims_completed_action(draft, s.get("tool_results", []))
        if forbidden:
            trace.append({"node": "verify", "result": "rejected",
                          "reason": forbidden,
                          "action": "fell back to the deterministic answer"})
            return {**s, "answer": reference, "grounding": report.to_json(),
                    "used_model": False, "trace": trace}

        advice = advice_claim_error(draft, reference) if s.get("used_model") else None
        if advice:
            trace.append({"node": "verify", "result": "rejected", "reason": advice,
                          "action": "fell back to the deterministic answer"})
            return {**s, "answer": reference, "used_model": False,
                    "grounding": {"ok": False, "ungrounded": [], "checked": report.checked,
                                  "rejected_for": "advice"}, "trace": trace}

        if s.get("used_model") and s.get("cache") == "miss" and self.wording_cache is not None:
            # Only wording that passed every check is kept, and it holds tokens, not figures.
            self.wording_cache.put(s.get("cache_key"), s.get("draft", ""))
        grounding = report.to_json()
        if s.get("used_model") and s.get("facts") is not None:
            grounding["method"] = "figure_tokens"
        trace.append({"node": "verify", "result": "accepted",
                      "figures_checked": report.checked})
        return {**s, "answer": draft, "grounding": grounding, "trace": trace}

    def n_finalize(self, s: AgentState) -> AgentState:
        r = s.get("route", {})
        state = AnswerState(s.get("state") or r.get("state", "informational"))
        prefix = STATE_PREFIX.get(state, "")
        answer = s.get("answer", "")
        if prefix and not answer.startswith(prefix):
            answer = f"{prefix} {answer}"
        figures = figure_sources(tokenize(answer), s.get("tool_results", [])) if s.get("tool_results") else []
        return {**s, "answer": answer, "state": state.value, "figures": figures}

    # ------------------------------------------------------------------
    def _model_tool_calling(self, s: AgentState) -> bool:
        choice = ToolChoice(s.get("tool_choice") or self.tool_choice)
        return (choice == ToolChoice.MODEL and self.llm.available
                and s.get("route", {}).get("intent") not in _DETERMINISTIC_CLARIFICATIONS)

    def _remaining_budget(self, s: AgentState) -> float:
        remaining = s.get("inference_deadline", time.monotonic() + self.llm.config.timeout) - time.monotonic()
        if remaining <= 0:
            raise LLMUnavailable("The local model response budget was exhausted.")
        return remaining

    def _context_arguments(self, name: str, arguments: dict) -> dict:
        args = dict(arguments)
        spec = self.tools.spec(name)
        if self.default_account_id and spec:
            properties = spec.parameters.get("properties", {})
            if "account_id" in properties and "account_id" not in args:
                args["account_id"] = self.default_account_id
            if "card_id" in properties and "card_id" not in args:
                cards = [card for card in self.tools.hh.cards.values()
                         if card.account_id == self.default_account_id
                         and self.tools._in_scope(card.account_id)]
                if len(cards) == 1:
                    args["card_id"] = cards[0].id
        return args

    def _model_driven_tools(self, s: AgentState, trace: list) -> list[dict]:
        """Model reads are separately authorized from explicit user mutations."""
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": s["question"]}]
        schemas = self.tools.openai_schemas(read_only=True)
        allowed = {schema["function"]["name"] for schema in schemas}
        results: list[dict] = []
        for _ in range(self.max_tool_calls):
            try:
                resp = self.llm.chat(messages, tools=schemas,
                                     timeout=self._remaining_budget(s))
            except LLMUnavailable as e:
                trace.append({"node": "execute", "mode": "model", "error": str(e)})
                return results
            msg = (resp.get("choices") or [{}])[0].get("message", {}) or {}
            calls = msg.get("tool_calls") or []
            if not calls:
                break
            if not isinstance(calls, list):
                trace.append({"node": "execute", "mode": "model", "ok": False,
                              "error": "invalid model tool-call list"})
                return results
            messages.append(msg)
            for c in calls:
                if len(results) >= self.max_tool_calls:
                    break
                fn = c.get("function") if isinstance(c, dict) else None
                name = fn.get("name") if isinstance(fn, dict) else None
                spec = self.tools.spec(name) if isinstance(name, str) else None
                # Filtering the advertised schemas is not authorization: a model
                # can fabricate any function name, including a state-changing one.
                if not spec or name not in allowed or not spec.read_only:
                    trace.append({"node": "execute", "mode": "model", "ok": False,
                                  "tool": name if isinstance(name, str) else None,
                                  "error": "model tool is not authorized for read-only execution"})
                    return results
                try:
                    raw_arguments = fn.get("arguments", "{}")
                    if not isinstance(raw_arguments, str):
                        raise ValueError("arguments must be a JSON object string")
                    args = json.loads(raw_arguments)
                    if self.tools.validate_arguments(name, args):
                        raise ValueError("arguments do not match the tool schema")
                    args = self._context_arguments(name, args)
                except (ValueError, TypeError):
                    trace.append({"node": "execute", "mode": "model", "tool": name,
                                  "ok": False, "error": "invalid model tool arguments"})
                    return results
                out = self.tools.call(name, args)
                results.append(out)
                trace.append({"node": "execute", "mode": "model", "tool": name,
                              "arguments": args, "ok": not out.get("error")})
                messages.append({"role": "tool", "tool_call_id": c.get("id", ""),
                                 "content": json.dumps(_slim(out), default=str)[:4000]})
            if (len(results) >= self.max_tool_calls
                    or any(result.get("_tool") == s["route"]["tool"] and not result.get("error")
                           for result in results)):
                # The required calculation is complete. A separate compose step
                # already explains it; another selection round only spends the
                # remaining budget asking the model whether it is done.
                break
        return results

    # ------------------------------------------------------------------
    def _build(self):
        g = StateGraph(AgentState)
        g.add_node("scope_guard", self.n_scope_guard)
        g.add_node("classify", self.n_classify)
        g.add_node("execute", self.n_execute)
        g.add_node("compose", self.n_compose)
        g.add_node("verify", self.n_verify)
        g.add_node("finalize", self.n_finalize)
        g.set_entry_point("scope_guard")
        g.add_edge("scope_guard", "classify")
        g.add_edge("classify", "execute")
        g.add_edge("execute", "compose")
        g.add_edge("compose", "verify")
        g.add_edge("verify", "finalize")
        g.add_edge("finalize", END)
        return g.compile()

    # ------------------------------------------------------------------
    def ask(self, question: str, untrusted_context: str = "",
            tool_choice: Optional[ToolChoice] = None) -> AgentResult:
        t0 = time.monotonic()
        init: AgentState = {"question": question,
                            "untrusted_context": untrusted_context,
                            "tool_choice": (tool_choice or self.tool_choice).value,
                            "trace": [], "tool_results": [],
                            "inference_deadline": t0 + self.llm.config.timeout}
        if self._graph is not None:
            final = self._graph.invoke(init)
        else:                                        # pragma: no cover
            final = init
            for node in (self.n_scope_guard, self.n_classify, self.n_execute,
                         self.n_compose, self.n_verify, self.n_finalize):
                final = node(final)

        primary = (final.get("tool_results") or [{}])[0]
        return AgentResult(
            question=question,
            answer=final.get("answer", ""),
            state=AnswerState(final.get("state", "informational")),
            intent=final.get("route", {}).get("intent", "unknown"),
            tools_called=[t.get("_tool", "?") for t in final.get("tool_results", [])],
            tool_results=final.get("tool_results", []),
            used_model=bool(final.get("used_model")),
            grounding=final.get("grounding", {}),
            guard=final.get("guard", {}),
            assumptions=(primary.get("assumptions") or primary.get("notes")
                         or primary.get("caveats") or []),
            confidence=primary.get("confidence", "exact"),
            latency_ms=int((time.monotonic() - t0) * 1000),
            trace=final.get("trace", []),
            figures=final.get("figures", []),
            wording_cache=final.get("cache", "off"))


# ---------------------------------------------------------------------------

# Two tiers, not one -- "I have sent the transfer" and "the payment is
# completed" are different claims and need different evidence. Treating
# them alike is exactly how a "processing" leg (dispatched, outcome not
# yet known) was previously accepted as proof of "payment completed" (a
# claim that the money has actually landed): the single combined check
# below used to accept ANY of these states for ANY of these phrases.
_IN_FLIGHT_CLAIMS = ("payment scheduled", "i have scheduled", "i've scheduled",
                     "transfer sent", "i have sent", "i've sent",
                     "i moved", "i have transferred", "i've transferred",
                     "successfully sent")
_SETTLED_CLAIMS = ("i have paid", "i've paid", "has been paid",
                   "money has been moved", "payment completed")
_COMPLETION_CLAIMS = _IN_FLIGHT_CLAIMS + _SETTLED_CLAIMS

_IN_FLIGHT_STATES = ("\"submitted\"", "\"processing\"", "\"reconciled\"",
                    "\"credited_by_biller\"", "\"funds_available\"")
# A claim that the money has actually arrived / the obligation is settled
# needs evidence the payment reached or passed that point -- "submitted" or
# "processing" mean it was dispatched, not that it landed.
_SETTLED_STATES = ("\"reconciled\"", "\"credited_by_biller\"", "\"funds_available\"")


def _coverage_caveats_present(text: str) -> bool:
    """A coverage estimate must not be rewritten as verified deposit insurance."""
    low = text.lower()
    estimated = re.search(r"\bestimat(?:e|es|ed)\b", low)
    ownership = re.search(r"\b(?:ownership|owner|registration|category)\b", low)
    uncertain = re.search(r"\b(?:verify|confirm|unverified|unconfirmed)\b", low)
    return bool(estimated and ownership and uncertain)


def _claims_completed_action(text: str, payloads: list[dict]) -> Optional[str]:
    """A response such as 'payment scheduled' is permitted only after the payment
    service confirms that state -- and 'payment completed' needs stronger
    evidence than 'transfer sent' does, since one claims the money is in
    flight and the other claims it has actually arrived."""
    low = (text or "").lower()
    settled_claimed = [c for c in _SETTLED_CLAIMS if c in low]
    in_flight_claimed = [c for c in _IN_FLIGHT_CLAIMS if c in low]
    if not settled_claimed and not in_flight_claimed:
        return None
    blob = json.dumps(payloads, default=str).lower()

    if settled_claimed:
        if not any(k in blob for k in _SETTLED_STATES):
            return (f"the draft claimed a settled action ({settled_claimed[0]!r}) "
                    "but no tool result shows the payment actually landed "
                    "(only, at most, submitted/processing evidence)")
    if in_flight_claimed:
        if not any(k in blob for k in _IN_FLIGHT_STATES):
            claimed = in_flight_claimed[0]
            return (f"the draft claimed a completed action ({claimed!r}) that no "
                    "tool result confirms")
    return None


def _figure_notes(intent: str, primary: dict, sheet: FactSheet) -> str:
    """Name the tokens a small model most often confuses, without showing their values."""
    if intent != "spending_allowance":
        return ""
    tokens = {value: token for token, value in sheet.facts.items()}

    def token_for(key: str) -> Optional[str]:
        value = primary.get(key)
        return tokens.get(str(value.get("display"))) if isinstance(value, dict) else None

    spendable, low_point = token_for("amount"), token_for("low_point_balance")
    notes = []
    if spendable:
        notes.append(f"[{spendable}] is the spendable allowance after protected reserves are subtracted.")
    if low_point and low_point != spendable:
        notes.append(f"[{low_point}] is the lowest projected balance before that subtraction and is never spendable.")
    return "\n" + " ".join(notes) if notes else ""


def _slim(payload: dict) -> dict:
    """Trim long arrays before they reach a small model's context window."""
    out = {}
    for k, v in payload.items():
        if isinstance(v, list) and len(v) > 12:
            out[k] = v[:12] + [{"_truncated": f"{len(v) - 12} more items"}]
        elif isinstance(v, dict) and k in ("plan",):
            out[k] = {kk: vv for kk, vv in v.items() if kk != "targets"}
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# LangSmith tracing -- opt in via environment
# ---------------------------------------------------------------------------

def enable_langsmith(project: str = "finpilot") -> dict:
    """LANGCHAIN_TRACING_V2=true and LANGCHAIN_API_KEY=... turn this on."""
    if os.environ.get("LANGCHAIN_TRACING_V2", "").lower() not in ("1", "true", "yes"):
        return {"enabled": False,
                "hint": "set LANGCHAIN_TRACING_V2=true and LANGCHAIN_API_KEY to trace"}
    os.environ.setdefault("LANGCHAIN_PROJECT", project)
    try:
        from langsmith import Client
        Client()
        return {"enabled": True, "project": os.environ["LANGCHAIN_PROJECT"]}
    except Exception as e:
        return {"enabled": False, "error": str(e)}
