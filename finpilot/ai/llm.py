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

from contextvars import ContextVar
_inference_metrics = ContextVar("finpilot_inference_metrics", default=None)

def start_inference_metrics():
    return _inference_metrics.set([])

def inference_metrics():
    return list(_inference_metrics.get() or [])

def stop_inference_metrics(token):
    _inference_metrics.reset(token)


import json
import math
import os
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

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
    max_tokens: int = 384
    timeout: float = 12.0
    runtime: str = "unknown"
    health_ttl: float = 15.0
    failure_cooldown: float = 5.0

    def __post_init__(self) -> None:
        # Bound local-model work even when configuration comes from an env file.
        self.timeout = _bounded(self.timeout, 12.0, 1.0, 120.0)
        self.max_tokens = int(_bounded(self.max_tokens, 384, 1, 2048))
        self.health_ttl = _bounded(self.health_ttl, 15.0, 0.0, 300.0)
        self.failure_cooldown = _bounded(self.failure_cooldown, 5.0, 0.0, 60.0)

    @classmethod
    def from_env(cls) -> "LLMConfig":
        return cls(
            base_url=os.environ.get("FINPILOT_LLM_BASE_URL", "").rstrip("/"),
            model=os.environ.get("FINPILOT_LLM_MODEL", ""),
            api_key=os.environ.get("FINPILOT_LLM_API_KEY", "not-needed"),
            temperature=float(os.environ.get("FINPILOT_LLM_TEMPERATURE", "0.1")),
            max_tokens=int(os.environ.get("FINPILOT_LLM_MAX_TOKENS", "384")),
            timeout=float(os.environ.get("FINPILOT_LLM_TIMEOUT", "12")),
            health_ttl=float(os.environ.get("FINPILOT_LLM_HEALTH_TTL", "15")),
            failure_cooldown=float(os.environ.get("FINPILOT_LLM_FAILURE_COOLDOWN", "5")),
        )

    def to_json(self) -> dict:
        return {"base_url": self.base_url, "model": self.model,
                "runtime": self.runtime, "temperature": self.temperature,
                "max_tokens": self.max_tokens, "timeout": self.timeout}


def _bounded(value: float, default: float, minimum: float, maximum: float) -> float:
    number = float(value)
    return min(maximum, max(minimum, number)) if math.isfinite(number) else default


def probe(base_url: str, timeout: float = 2.0, *, api_key: str = "not-needed",
          client: Optional[httpx.Client] = None) -> Optional[list[str]]:
    """Return the model ids a runtime is serving, or None if unreachable."""
    try:
        get = client.get if client is not None else httpx.get
        r = get(f"{base_url.rstrip('/')}/models", timeout=timeout,
                headers={"Authorization": f"Bearer {api_key}"})
        if r.status_code != 200:
            return None
        data = r.json()
        items = data if isinstance(data, list) else data.get("data", [])
        ids = [m.get("id") for m in items if isinstance(m, dict) and m.get("id")]
        return ids or []
    except Exception:
        return None


def autodetect(prefer: str = "llama") -> Optional[LLMConfig]:
    """Find a running local model. Prefers an id containing `prefer`."""
    env = LLMConfig.from_env()
    if env.base_url:
        ids = probe(env.base_url, api_key=env.api_key)
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


_AUTODETECT = object()


class LocalLLM:
    """Thin OpenAI-compatible chat client with a JSON-extraction helper."""

    def __init__(self, config: Optional[LLMConfig] | object = _AUTODETECT):
        # An explicit None means detection was already attempted. This avoids
        # probing twice in callers using LocalLLM(autodetect()).
        if config is _AUTODETECT:
            config = autodetect()
        if config is not None and not isinstance(config, LLMConfig):
            raise TypeError("config must be an LLMConfig or None")
        self.config = config or LLMConfig()
        self._client: Optional[httpx.Client] = None
        self._client_lock = threading.RLock()
        self._state_lock = threading.Lock()
        self._probe_lock = threading.Lock()
        self._active_requests = 0
        self._closed = False
        self._health: Optional[dict] = None
        self._health_checked = 0.0
        self._retry_after = 0.0

    @property
    def available(self) -> bool:
        return bool(self.config.base_url and self.config.model)

    @property
    def client(self) -> httpx.Client:
        with self._client_lock:
            if self._closed:
                raise LLMUnavailable("The local model client is closed.")
            if self._client is None:
                self._client = httpx.Client(timeout=self.config.timeout)
            return self._client

    @contextmanager
    def _connection(self):
        with self._client_lock:
            client = self.client
            self._active_requests += 1
        try:
            yield client
        finally:
            closing = None
            with self._client_lock:
                self._active_requests -= 1
                if self._closed and not self._active_requests:
                    closing, self._client = self._client, None
            if closing is not None:
                closing.close()

    def close(self) -> None:
        """Stop accepting requests; finish existing calls before closing the pool."""
        closing = None
        with self._client_lock:
            self._closed = True
            if not self._active_requests:
                closing, self._client = self._client, None
        if closing is not None:
            closing.close()

    def status(self) -> dict:
        """Return the latest observation without performing network I/O."""
        with self._state_lock:
            out = dict(self._health) if self._health is not None else {
                "reachable": False, "health_state": "unknown",
                "reason": "model health has not been checked",
                "base_url": self.config.base_url, "model": self.config.model,
                "runtime": self.config.runtime, "models_served": [],
            }
            out["models_served"] = list(out.get("models_served", []))
            age = max(0, time.monotonic() - self._health_checked)
            out["cache_age_seconds"] = round(age, 3) if self._health is not None else None
            return out

    def _record_health(self, result: dict) -> None:
        with self._state_lock:
            self._health = {**result,
                            "health_state": "healthy" if result.get("reachable") else "unavailable",
                            "checked_at": datetime.now(timezone.utc).isoformat()}
            self._health_checked = time.monotonic()

    def _health_fresh(self) -> bool:
        with self._state_lock:
            return (self._health is not None
                    and time.monotonic() - self._health_checked < self.config.health_ttl)

    def health(self, *, force: bool = False) -> dict:
        """Cache reachability checks; concurrent refreshes share one probe."""
        if not force and self._health_fresh():
            return self.status()
        if not self._probe_lock.acquire(blocking=False):
            with self._probe_lock:
                return self.status()
        try:
            if not force and self._health_fresh():
                return self.status()
            if not self.config.base_url:
                self._record_health({"reachable": False,
                    "reason": "no endpoint configured or detected",
                    "hint": "set FINPILOT_LLM_BASE_URL, e.g. http://localhost:11434/v1"})
            else:
                try:
                    with self._connection() as client:
                        ids = probe(self.config.base_url, timeout=2.0,
                                    api_key=self.config.api_key, client=client)
                except LLMUnavailable:
                    ids = None
                self._record_health({"reachable": ids is not None,
                    "base_url": self.config.base_url, "runtime": self.config.runtime,
                    "model": self.config.model, "models_served": ids or []})
            return self.status()
        finally:
            self._probe_lock.release()

    def chat(self, messages: list[dict], *, temperature: Optional[float] = None,
             max_tokens: Optional[int] = None, stop: Optional[list[str]] = None,
             tools: Optional[list[dict]] = None, timeout: Optional[float] = None, response_format: Optional[dict] = None) -> dict:
        if not self.available:
            raise LLMUnavailable(
                "No local model endpoint is reachable. Set FINPILOT_LLM_BASE_URL "
                "and FINPILOT_LLM_MODEL, or start Ollama / llama.cpp / LM Studio.")
        with self._state_lock:
            if time.monotonic() < self._retry_after:
                raise LLMUnavailable("The local model is temporarily unavailable; "
                                     "calculator answers remain available.")
        remaining = self.config.timeout if timeout is None else min(self.config.timeout, timeout)
        if remaining <= 0:
            raise LLMUnavailable("The local model response budget was exhausted.")
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature if temperature is None else temperature,
            "max_tokens": (min(self.config.max_tokens, max(1, int(max_tokens)))
                           if max_tokens is not None else self.config.max_tokens),
            "stream": False,
        }
        if response_format is not None:
            payload["response_format"] = response_format
        if stop:
            payload["stop"] = stop
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        started = time.monotonic()
        try:
            with self._connection() as client:
                r = client.post(f"{self.config.base_url}/chat/completions",
                                json=payload,
                                timeout=httpx.Timeout(remaining, connect=min(2.0, remaining),
                                                      pool=min(2.0, remaining)),
                                headers={"Authorization": f"Bearer {self.config.api_key}",
                                         "Content-Type": "application/json"})
            r.raise_for_status()
            data = r.json()
            if (not isinstance(data, dict)
                    or not isinstance(data.get("choices"), list)
                    or not data["choices"]
                    or not isinstance(data["choices"][0], dict)
                    or not isinstance(data["choices"][0].get("message"), dict)):
                raise ValueError("invalid completion response")
            with self._state_lock:
                self._retry_after = 0.0
            self._record_health({"reachable": True, "base_url": self.config.base_url,
                "runtime": self.config.runtime, "model": self.config.model,
                "models_served": [self.config.model], "observation": "chat completion"})
            metrics = _inference_metrics.get()
            if metrics is not None:
                metrics.append({"model": self.config.model,
                    "usage": data.get("usage"),
                    "finish_reason": data["choices"][0].get("finish_reason"),
                    "provider": "local", "status": "completed", "latency_ms": round((time.monotonic()-started)*1000), "cost_usd": None})
            return data
        except (httpx.HTTPError, ValueError) as e:
            metrics = _inference_metrics.get()
            if metrics is not None:
                metrics.append({"model":self.config.model,"provider":"local","status":"failed",
                    "error":type(e).__name__,"latency_ms":round((time.monotonic()-started)*1000),"cost_usd":None})
            with self._state_lock:
                self._retry_after = time.monotonic() + self.config.failure_cooldown
            self._record_health({"reachable": False, "base_url": self.config.base_url,
                "runtime": self.config.runtime, "model": self.config.model,
                "reason": f"local model request failed ({type(e).__name__})"})
            raise LLMUnavailable(f"local model call failed ({type(e).__name__})") from e

    def complete(self, system: str, user: str, **kw) -> str:
        resp = self.chat([{"role": "system", "content": system},
                          {"role": "user", "content": user}], **kw)
        try:
            choice = resp["choices"][0]
            # A token-limit cutoff may omit the qualification that makes a
            # financial explanation true. Do not publish a partial draft.
            if choice.get("finish_reason") in {"length", "content_filter"}:
                raise LLMUnavailable("The local model did not finish its answer.")
            msg = choice["message"]
        except (KeyError, IndexError, TypeError):
            return ""
        if not isinstance(msg, dict):
            return ""
        content = msg.get("content") or ""
        if not isinstance(content, str):
            return ""
        # A reasoning-tuned local model (DeepSeek-R1 distills, QwQ, some Llama
        # fine-tunes) puts its scratch thinking in a separate field or in
        # <think>...</think> tags ahead of the real answer. Neither guardrail
        # nor grounding check should ever see the scratch content, so strip it
        # here rather than downstream in every caller.
        if not content and isinstance(msg.get("reasoning_content"), str):
            content = ""  # reasoning-only response with no real answer yet
        content = _THINK_TAG.sub("", content).strip()
        return content

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
_THINK_TAG = re.compile(r"<think>.*?</think>", re.S | re.I)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


def _try_parse(t: str) -> Any:
    try:
        return json.loads(t)
    except Exception:
        pass
    # A small model very commonly leaves a trailing comma before a closing
    # brace/bracket -- valid in plenty of languages, not in JSON. Repair and
    # retry once rather than rejecting an otherwise-correct answer.
    repaired = _TRAILING_COMMA.sub(r"\1", t)
    if repaired != t:
        try:
            return json.loads(repaired)
        except Exception:
            pass
    return None


def extract_json(text: str) -> Any:
    if not text:
        return None
    t = _THINK_TAG.sub("", text).strip()
    m = _FENCE.search(t)
    if m:
        t = m.group(1).strip()
    parsed = _try_parse(t)
    if parsed is not None:
        return parsed
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
                    parsed = _try_parse(t[start:i + 1])
                    if parsed is not None:
                        return parsed
                    break
    return None
