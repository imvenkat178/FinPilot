"""A 10-second sanity check for your local model, before running the full
query suite.

    python -m finpilot.ai.diagnose

Answers three questions in order, stopping at the first failure:

  1. Is anything listening at the configured (or autodetected) endpoint?
  2. Does a plain chat completion come back at all?
  3. Can it follow "reply with JSON only" -- the one behaviour the whole
     assistant graph depends on, since every tool answer is composed from a
     JSON tool result?

Each step prints exactly what it sent and received, because the fastest way
to fix a real local model's quirks is to see its raw output once.
"""
from __future__ import annotations

import sys

from finpilot.ai.llm import LLMConfig, LocalLLM, autodetect, extract_json


def main() -> int:
    print("== FinPilot / local model diagnostic ==\n")

    cfg = autodetect()
    if cfg is None:
        env = LLMConfig.from_env()
        print("1. Endpoint reachability: FAIL")
        if env.base_url:
            print(f"   Configured FINPILOT_LLM_BASE_URL={env.base_url} did not answer.")
            print("   Is the server actually running? (e.g. `ollama serve`, or the")
            print("   app itself if it's Ollama/LM Studio with a background service)")
        else:
            print("   No FINPILOT_LLM_BASE_URL set, and none of the usual local ports")
            print("   answered (11434 Ollama, 8080 llama.cpp, 1234 LM Studio, 8000 vLLM).")
            print("   Set it explicitly, e.g.:")
            print("     export FINPILOT_LLM_BASE_URL=http://localhost:11434/v1")
            print("     export FINPILOT_LLM_MODEL=llama3.2")
        return 1

    print("1. Endpoint reachability: OK")
    print(f"   runtime={cfg.runtime}  base_url={cfg.base_url}  model={cfg.model}\n")

    llm = LocalLLM(cfg)
    print("2. Plain chat completion:")
    try:
        reply = llm.complete("You are a terse assistant.",
                              "Reply with exactly the word: pong")
    except Exception as e:
        print(f"   FAIL -- {e}")
        return 1
    print(f"   sent:     'Reply with exactly the word: pong'")
    print(f"   received: {reply!r}")
    if not reply.strip():
        print("   FAIL -- empty response. Check the model is fully loaded (first")
        print("   request after a cold start can also just be slow, not empty --")
        print("   try again if this was the very first call).")
        return 1
    print("   OK (model responds; exact wording doesn't have to match)\n")

    print("3. JSON-only instruction-following:")
    system = ("You are a finance assistant. Reply with JSON only, no prose, "
              "no code fence, matching exactly this shape: "
              '{"answer": "<one short sentence>", "confidence": "high"|"low"}')
    user = "Restate: the checking account balance is $2,431.09."
    try:
        raw = llm.complete(system, user)
    except Exception as e:
        print(f"   FAIL -- {e}")
        return 1
    print(f"   raw reply: {raw!r}")
    parsed = extract_json(raw)
    if parsed is None:
        print("   FAIL -- could not extract JSON even after fence/think-tag/")
        print("   trailing-comma repair. This model may need a stronger system")
        print("   prompt, a JSON grammar/response_format flag if your runtime")
        print("   supports one, or FinPilot's router-only mode:")
        print("   the app already falls back to calculator wording whenever")
        print("   this happens, so it is safe -- just less natural-sounding.")
        return 1
    print(f"   parsed:    {parsed!r}")
    print("   OK\n")

    print("All checks passed. Run the full suite next:")
    print("   ./run.sh queries")
    print("or start the app against this model:")
    print("   ./run.sh serve")
    return 0


if __name__ == "__main__":
    sys.exit(main())
