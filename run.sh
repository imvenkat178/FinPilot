#!/usr/bin/env bash
# FinPilot launcher.
#
#   ./run.sh serve                 start the API + dashboard on :8099
#   ./run.sh serve 8080            ...on another port
#   ./run.sh stop                  stop it
#   ./run.sh mock [misbehave...]   start the local-model test double on :11434
#   ./run.sh test                  run the full test suite
#   ./run.sh check-llm             10-second sanity check of your real local model
#   ./run.sh queries               run the user-query suite against the assistant
#
# To use YOUR local Llama:
#   export FINPILOT_LLM_BASE_URL=http://localhost:11434/v1     # Ollama
#   export FINPILOT_LLM_MODEL=llama3.2
#   ./run.sh check-llm             <- run this first
#   ./run.sh serve
set -euo pipefail
cd "$(dirname "$0")"
CMD="${1:-serve}"; shift || true

case "$CMD" in
  serve)
    PORT="${1:-8099}"
    echo "FinPilot on http://127.0.0.1:${PORT}  (dashboard at /)"
    exec python3 -m uvicorn finpilot.api.app:app --host 127.0.0.1 --port "$PORT"
    ;;
  stop)
    pgrep -f "uvicorn finpilot" | xargs -r kill && echo "stopped" || echo "not running"
    ;;
  mock)
    exec python3 -m finpilot.ai.mock_server --port 11434 ${@:+--misbehave "$@"}
    ;;
  test)
    exec python3 -m pytest tests -q "$@"
    ;;
  queries)
    exec python3 -m tests.query_suite "$@"
    ;;
  check-llm)
    exec python3 -m finpilot.ai.diagnose
    ;;
  *)
    echo "unknown command: $CMD"; exit 2 ;;
esac
