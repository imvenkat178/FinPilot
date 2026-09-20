"""A small in-process cache for model output that already passed verification.

Keys are digests of the household scope, the model, a prompt version and the exact prompt.
Compose prompts carry figure tokens instead of numbers, so a cached sentence can be reused
after balances change: the application inserts the current figures and re-runs every check.
Cached plans are validated again on every use. The cache never stores a financial result,
lives only in process memory, and forgets entries after a time limit.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from typing import Callable, Optional


class WordingCache:
    def __init__(self, max_entries: int = 512, ttl_seconds: float = 24 * 3600,
                 clock: Callable[[], float] = time.monotonic):
        self.max_entries, self.ttl, self._clock = max_entries, ttl_seconds, clock
        self._items: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    @staticmethod
    def key(scope: str, kind: str, **parts) -> str:
        blob = json.dumps({"scope": scope, "kind": kind, **parts}, sort_keys=True,
                          ensure_ascii=False, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def get(self, key: Optional[str]) -> Optional[str]:
        if not key:
            return None
        now = self._clock()
        with self._lock:
            item = self._items.get(key)
            if item is None or now - item[0] > self.ttl:
                self._items.pop(key, None)
                self.misses += 1
                return None
            self._items.move_to_end(key)
            self.hits += 1
            return item[1]

    def put(self, key: Optional[str], value: str) -> None:
        if not key or not isinstance(value, str) or not value:
            return
        with self._lock:
            self._items[key] = (self._clock(), value)
            self._items.move_to_end(key)
            while len(self._items) > self.max_entries:
                self._items.popitem(last=False)

    def stats(self) -> dict:
        with self._lock:
            return {"entries": len(self._items), "hits": self.hits, "misses": self.misses}
