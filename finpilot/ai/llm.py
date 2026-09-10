"""Local model client.

The assistant runs against a locally hosted Llama. Every common local runtime
exposes an OpenAI-compatible `/v1/chat/completions` endpoint, so one client
covers all of them:

    Ollama            http://localhost:11434/v1     (also has a native /api)
    llama.cpp server  http://localhost:8080/v1
    LM Studio         http://localhost:1234/v1
    vLLM / TGI        http://localhost:8000/v1

Configure explicitly with environment variables, or let `autodetect()` probe.

    FINPILOT_LLM_BASE_URL=http://localhost:11434/v1
    FINPILOT_LLM_MODEL=llama3.2
    FINPILOT_LLM_API_KEY=not-needed

Section 10 of the specification governs what the model is allowed to do:
"Monetary arithmetic, eligibility checks, cash forecasts, and repayment
schedules come from tested services. The language model may explain these
outputs and request missing inputs; it must not invent balances, rates, terms,
or completed actions."

So this client is never asked to compute. It classifies, and it writes prose
over numbers a calculator produced.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

import httpx

DEFAULT_CANDIDATES = [
    ("http://localhost:11434/v1", "Ollama"),
    ("http://127.0.0.1:11434/v1", "Ollama"),
    ("http://localhost:8080/v1", "llama.cpp server"),
    ("http://127.0.0.1:8080/v1", "llama.cpp server"),
    ("http://localhost:1234/v1", "LM Studio"),
    ("http://localhost:8000/v1", "vLLM"),
    ("http://host.docker.internal:11434/v1", "Ollama (host)"),
]


class LLMUnavailable(RuntimeError):
    """Raised when no local model can be reached. Callers degrade to the
    deterministic router rather than failing the request."""


@dataclass
class LLMConfig:
    base_url: str = ""
    model: str = ""
    api_key: str = "not-needed"
    temperature: float = 0.1
    max_tokens: int = 768
    timeout: float = 120.0
    runtime: str = "unknown"

    @classmethod
    def from_env(cls) -> "LLMConfig":
        return cls(
            base_url=os.environ.get("FINPILOT_LLM_BASE_URL", "").rstrip("/"),
            model=os.environ.get("FINPILOT_LLM_MODEL", ""),
            api_key=os.environ.get("FINPILOT_LLM_API_KEY", "not-needed"),
            temperature=float(os.environ.get("FINPILOT_LLM_TEMPERATURE", "0.1")),
            max_tokens=int(os.environ.get("FINPILOT_LLM_MAX_TOKENS", "768")),
            timeout=float(os.environ.get("FINPILOT_LLM_TIMEOUT", "120")),
        )

    def to_json(self) -> dict:
        return {"base_url": self.base_url, "model": self.model,
                "runtime": self.runtime, "temperature": self.temperature}


def probe(base_url: str, timeout: float = 2.0) -> Optional[list[str]]:
    """Return the model ids a runtime is serving, or None if unreachable."""
    try:
        r = httpx.get(f"{base_url.rstrip('/')}/models", timeout=timeout,
                      headers={"Authorization": "Bearer not-needed"})
        if r.status_code != 200:
            return None
        data = r.json()
        items = data.get("data", data if isinstance(data, list) else [])
        ids = [m.get("id") for m in items if isinstance(m, dict) and m.get("id")]
        return ids or []
    except Exception:
        return None


def autodetect(prefer: str = "llama") -> Optional[LLMConfig]:
    """Find a running local model. Prefers an id containing `prefer`."""
    env = LLMConfig.from_env()
    if env.base_url:
        ids = probe(env.base_url)
        if ids is not None:
            env.runtime = "configured"
            if not env.model:
                env.model = _pick(ids, prefer) or (ids[0] if ids else "")
            return env
        # a configured URL that does not answer is still worth returning, so the
        # caller can report the exact endpoint that failed
        env.runtime = "configured (not answering)"
        return env if env.model else None

    for url, name in DEFAULT_CANDIDATES:
        ids = probe(url)
        if ids is None:
            continue
        cfg = LLMConfig.from_env()
        cfg.base_url = url
        cfg.runtime = name
        cfg.model = cfg.model or _pick(ids, prefer) or (ids[0] if ids else "")
        if cfg.model:
            return cfg
    return None


def _pick(ids: list[str], prefer: str) -> Optional[str]:
    for i in ids:
        if prefer.lower() in i.lower():
            return i
    return ids[0] if ids else None


class LocalLLM:
    """Thin OpenAI-compatible chat client with a JSON-extraction helper."""

    def __init__(self, config: Optional[LLMConfig] = None):
        self.config = config or autodetect() or LLMConfig()
        self._client: Optional[httpx.Client] = None

    @property
    def available(self) -> bool:
        return bool(self.config.base_url and self.config.model)

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self.config.timeout)
        return self._client

    def health(self) -> dict:
        if not self.config.base_url:
            return {"reachable": False, "reason": "no endpoint configured or detected",
                    "hint": "set FINPILOT_LLM_BASE_URL, e.g. http://localhost:11434/v1"}
        ids = probe(self.config.base_url, timeout=3.0)
        return {"reachable": ids is not None, "base_url": self.config.base_url,
                "runtime": self.config.runtime, "model": self.config.model,
                "models_served": ids or []}

    def chat(self, messages: list[dict], *, temperature: Optional[float] = None,
             max_tokens: Optional[int] = None, stop: Optional[list[str]] = None,
             tools: Optional[list[dict]] = None) -> dict:
        if not self.available:
            raise LLMUnavailable(
                "No local model endpoint is reachable. Set FINPILOT_LLM_BASE_URL "
                "and FINPILOT_LLM_MODEL, or start Ollama / llama.cpp / LM Studio.")
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature if temperature is None else temperature,
            "max_tokens": max_tokens or self.config.max_tokens,
            "stream": False,
        }
        if stop:
            payload["stop"] = stop
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        try:
            r = self.client.post(f"{self.config.base_url}/chat/completions",
                                 json=payload,
                                 headers={"Authorization": f"Bearer {self.config.api_key}",
                                          "Content-Type": "application/json"})
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as e:
            raise LLMUnavailable(f"local model call failed: {e}") from e

    def complete(self, system: str, user: str, **kw) -> str:
        resp = self.chat([{"role": "system", "content": system},
                          {"role": "user", "content": user}], **kw)
        try:
            return resp["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError):
            return ""

    def complete_json(self, system: str, user: str, *, retries: int = 2, **kw) -> Any:
        """Ask for JSON and extract it robustly. Small local models frequently
        wrap JSON in prose or a fenced block, so parse defensively rather than
        trusting the format."""
        last = ""
        for attempt in range(retries + 1):
            raw = self.complete(
                system + "\n\nReply with JSON only. No prose, no code fence.",
                user if attempt == 0 else
                user + "\n\nYour previous reply was not valid JSON. Reply with JSON only.",
                **kw)
            last = raw
            parsed = extract_json(raw)
            if parsed is not None:
                return parsed
        raise ValueError(f"model did not return JSON after {retries + 1} attempts: {last[:200]}")


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> Any:
    if not text:
        return None
    t = text.strip()
    m = _FENCE.search(t)
    if m:
        t = m.group(1).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    # first balanced object or array
    for opener, closer in (("{", "}"), ("[", "]")):
        start = t.find(opener)
        if start == -1:
            continue
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(t)):
            c = t[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == opener:
                depth += 1
            elif c == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(t[start:i + 1])
                    except Exception:
                        break
    return None
