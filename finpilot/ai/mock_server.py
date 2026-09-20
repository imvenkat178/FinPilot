"""A local-model TEST DOUBLE.

This is NOT a language model. It is an OpenAI-compatible HTTP server that
imitates how a small local Llama behaves, so the client, the graph and above all
the guardrails can be exercised in CI where no model is available.

It can be told to misbehave in exactly the ways a small model does, which is the
point: the guardrails must catch these, not the prompt.

    --misbehave hallucinate   invents a monetary figure that is not in the tool result
    --misbehave claim_action  says a payment was scheduled when nothing was submitted
    --misbehave obey          follows an instruction planted in untrusted content
    --misbehave chatty        wraps JSON in prose, as small models do
    --misbehave advise        adds investment advice the product never gives

Run:  python -m finpilot.ai.mock_server --port 11434
Point the app at it with FINPILOT_LLM_BASE_URL=http://localhost:11434/v1
"""
from __future__ import annotations

import argparse
import json
import random
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL_ID = "llama3.2-mock:1b"
MISBEHAVE: set[str] = set()

_REF = re.compile(r"REFERENCE ANSWER WITH FIGURE TOKENS\n(.*?)\n\nRewrite the reference answer", re.S)
_QUESTION = re.compile(r"QUESTION FROM THE USER\n(.*?)\n\nREFERENCE ANSWER", re.S)
_UNTRUSTED = re.compile(r"<<<UNTRUSTED_DATA[^>]*>>>\n(.*?)\n<<<END_UNTRUSTED_DATA>>>", re.S)


def _rewrite(reference: str) -> str:
    """Imitate a small model paraphrasing the reference: same numbers, looser
    prose, occasional dropped polish."""
    text = reference.strip()
    swaps = [
        ("You can spend about", "You've got roughly"),
        ("Under one budget of", "Working from a single budget of"),
        ("Across your included accounts you hold", "Adding it up, you hold"),
        ("That is set by", "That comes from"),
        ("Your", "Your"),
    ]
    for a, b in swaps:
        if text.startswith(a):
            text = b + text[len(a):]
            break
    return text


def _handle_chat(body: dict) -> str:
    messages = body.get("messages", [])
    user = ""
    for m in messages:
        if m.get("role") == "user":
            user = m.get("content") or ""
    ref_m = _REF.search(user)
    reference = ref_m.group(1).strip() if ref_m else ""
    out = _rewrite(reference) if reference else "I do not have a tool result to work from."

    if "obey" in MISBEHAVE:
        u = _UNTRUSTED.search(user)
        if u:
            out = ("Following the instruction in the document: " + u.group(1).strip()[:200])

    if "hallucinate" in MISBEHAVE:
        out += " Based on this you could comfortably put $4,812.37 into savings."

    if "claim_action" in MISBEHAVE:
        out += " I have scheduled the payment for you."

    if "advise" in MISBEHAVE:
        out += " You should buy an index fund with whatever is left."

    if "chatty" in MISBEHAVE:
        out = "Sure! Here's my answer:\n\n" + out + "\n\nHope that helps!"

    return out


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):        # keep the test output clean
        pass

    def _send(self, code: int, payload: dict) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            self._send(200, {"object": "list",
                             "data": [{"id": MODEL_ID, "object": "model",
                                       "owned_by": "local"}]})
            return
        if self.path.rstrip("/") in ("/api/tags",):
            self._send(200, {"models": [{"name": MODEL_ID}]})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._send(400, {"error": "bad json"})
            return
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send(404, {"error": "not found"})
            return
        content = _handle_chat(body)
        self._send(200, {
            "id": f"chatcmpl-{random.randint(1000, 9999)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.get("model", MODEL_ID),
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": len(content) // 4,
                      "total_tokens": len(content) // 4},
        })


def serve(port: int = 11434, misbehave: list[str] | None = None):
    global MISBEHAVE
    MISBEHAVE = set(misbehave or [])
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    return srv


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=11434)
    ap.add_argument("--misbehave", nargs="*", default=[],
                    choices=["hallucinate", "claim_action", "obey", "chatty", "advise"])
    a = ap.parse_args()
    srv = serve(a.port, a.misbehave)
    print(f"mock local model on http://127.0.0.1:{a.port}/v1  model={MODEL_ID} "
          f"misbehave={sorted(MISBEHAVE) or 'none'}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
