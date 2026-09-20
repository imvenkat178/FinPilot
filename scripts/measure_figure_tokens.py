"""Measure figure-token wording and the wording cache against a local model on fictional demo data.

Usage:
    python scripts/measure_figure_tokens.py --model llama3.2:latest --budget 12 --output validation/figure-tokens-llama3.2-12s.json

Each question is asked once with a model call. When that wording is accepted, the question is asked
again with the same figures and once more after a 100-dollar checking balance change, to show cached
wording reused with current figures. Every answer goes through the product's own verification. The
client failure cooldown is off, so one timeout does not turn later questions into calculator answers.
The output records what was measured, not a target.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finpilot.ai.facts import tokenize  # noqa: E402
from finpilot.ai.graph import FinanceAgent  # noqa: E402
from finpilot.ai.llm import (LLMConfig, LocalLLM, inference_metrics,  # noqa: E402
                             start_inference_metrics, stop_inference_metrics)
from finpilot.ai.router import route, template_answer  # noqa: E402
from finpilot.ai.tools import ToolRegistry  # noqa: E402
from finpilot.ai.wording_cache import WordingCache  # noqa: E402
from finpilot.money import Money  # noqa: E402
from finpilot.seed.demo import demo_household  # noqa: E402

QUESTIONS = [
    "How much can I spend this week?",
    "How much money do I have across all my accounts?",
    "What if I pay another $300 toward debt?",
    "How should I split my next paycheck?",
    "What bills are due in the next 30 days?",
    "Which of my cards should I use for an $80 dinner?",
    "Compare debt payoff strategies with 200 extra monthly",
    "Check utilization timing for Everyday Rewards with a 200 payment",
    "Compare putting 1000 into savings versus debt for 365 days",
    "How much is protected for emergencies?",
]
KINDS = (("typed a figure", "typed_figure"), ("do not exist", "unknown_token"),
         ("dropped every figure", "dropped_figures"), ("advice", "added_advice"),
         ("claimed", "claimed_action"), ("spending-allowance", "allowance_wording"),
         ("coverage", "coverage_caveat"))


class RecordingLLM:
    """Keeps each raw model draft so rejected wording can be inspected."""

    def __init__(self, llm: LocalLLM):
        self.llm, self.drafts = llm, []

    def __getattr__(self, name):
        return getattr(self.llm, name)

    def complete(self, *args, **kwargs):
        draft = self.llm.complete(*args, **kwargs)
        self.drafts.append(draft)
        return draft


def rejection_kind(step: dict) -> str:
    reason = str(step.get("reason") or "")
    kind = next((kind for needle, kind in KINDS if needle in reason), None)
    return kind or ("ungrounded_figure" if step.get("ungrounded") else "other")


def ask(agent: FinanceAgent, recorder: RecordingLLM, question: str) -> dict:
    recorder.drafts.clear()
    token = start_inference_metrics()
    started = time.monotonic()
    try:
        result = agent.ask(question)
        calls = inference_metrics() or []
    finally:
        stop_inference_metrics(token)
    compose = next((step for step in result.trace if step.get("node") == "compose"), {})
    rejected = [step for step in result.trace if step.get("result") == "rejected"]
    return {"intent": result.intent, "used_model": result.used_model, "cache": result.wording_cache,
            "compose_mode": compose.get("mode"), "compose_reason": compose.get("reason"),
            "model_calls": len(calls), "rejected": [rejection_kind(step) for step in rejected],
            "rejection_detail": [str(step.get("reason") or step.get("ungrounded"))[:240] for step in rejected],
            "model_draft": recorder.drafts[-1] if recorder.drafts else None,
            "seconds": round(time.monotonic() - started, 2), "answer": result.answer}


def median(values):
    values = list(values)
    return statistics.median(values) if values else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--budget", type=float, default=12.0)
    parser.add_argument("--base-url", default="http://127.0.0.1:11434/v1")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    config = LLMConfig(base_url=args.base_url, model=args.model, timeout=args.budget, failure_cooldown=0.0)
    recorder = RecordingLLM(LocalLLM(config))
    if not recorder.available:
        print("The local model endpoint did not answer.")
        return 2
    cache = WordingCache()
    rows = []
    for question in QUESTIONS:
        household = demo_household()
        registry = ToolRegistry(household)
        routed = route(question)
        reference = template_answer(routed.intent, registry.call(routed.tool, routed.arguments))
        agent = FinanceAgent(registry, recorder, compile_graph=False, wording_cache=cache, cache_scope="demo")
        row = {"question": question, "reference_figures": len(tokenize(reference).facts),
               "first": ask(agent, recorder, question)}
        if row["first"]["used_model"]:
            row["repeat"] = ask(agent, recorder, question)
            checking = household.accounts["acc_checking"]
            checking.available = checking.available + Money(Decimal("100"), checking.available.currency)
            row["after_balance_change"] = ask(agent, recorder, question)
        rows.append(row)
        print(json.dumps({"question": question, **{
            name: [row[name]["used_model"], row[name]["cache"], row[name]["rejected"], row[name]["seconds"]]
            for name in ("first", "repeat", "after_balance_change") if name in row}}), flush=True)
    first = [row["first"] for row in rows]
    accepted = [row for row in rows if row["first"]["used_model"]]
    kinds: dict[str, int] = {}
    for item in first:
        for kind in item["rejected"]:
            kinds[kind] = kinds.get(kind, 0) + 1
    summary = {
        "questions": len(rows),
        "first_model_calls": sum(item["model_calls"] for item in first),
        "first_wording_accepted": len(accepted),
        "first_wording_rejected": sum(bool(item["rejected"]) for item in first),
        "first_rejection_kinds": kinds,
        "first_model_failed_or_over_budget": sum(item["compose_mode"] == "template" for item in first),
        "first_no_model_needed": sum(item["compose_mode"] in ("clarification", "error_passthrough") for item in first),
        "median_seconds_with_model_call": median(item["seconds"] for item in first if item["model_calls"]),
        "repeat_cache_hits": sum(row["repeat"]["cache"] == "hit" for row in accepted),
        "repeat_model_calls": sum(row["repeat"]["model_calls"] for row in accepted),
        "median_repeat_seconds": median(row["repeat"]["seconds"] for row in accepted),
        "after_change_cache_hits": sum(row["after_balance_change"]["cache"] == "hit" for row in accepted),
        "after_change_accepted": sum(row["after_balance_change"]["used_model"] for row in accepted),
        "after_change_answer_changed": sum(row["after_balance_change"]["answer"] != row["repeat"]["answer"]
                                           for row in accepted),
    }
    evidence = {"checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "provider": "local Ollama",
                "model": args.model, "budget_seconds": args.budget, "failure_cooldown_seconds": 0,
                "data": "fictional demo household", "script": "scripts/measure_figure_tokens.py",
                "summary": summary, "results": rows}
    Path(args.output).write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
