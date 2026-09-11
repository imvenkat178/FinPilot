"""Repeatable authenticated ASGI latency benchmark; all data and model calls are local.

Run: .venv/Scripts/python.exe scripts/benchmark.py --rows 5000 --runs 12
Optional --output PATH writes the JSON report. Each scenario uses a disposable
SQLite database, the real app/router/auth stack, and a network-free model stub.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta, datetime, timezone
import json
import math
from pathlib import Path
import platform
import statistics
import sys
from tempfile import TemporaryDirectory
from threading import Event
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from fastapi.testclient import TestClient

from finpilot.ai.llm import LLMConfig, LocalLLM
from finpilot.api.app import create_app


class HeldModel:
    """Real LLM client with a transport that cannot contact a network."""
    def __init__(self):
        self.entered = Event()
        self.release = Event()
        self.calls = 0
        self.client = LocalLLM(LLMConfig(base_url="http://benchmark.invalid/v1", model="benchmark-model", timeout=30))
        self.client._client = httpx.Client(transport=httpx.MockTransport(self.handle))

    def handle(self, request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "benchmark-model"}]})
        self.calls += 1
        self.entered.set()
        if not self.release.wait(30):
            raise RuntimeError("Benchmark failed to release mock inference")
        return httpx.Response(200, json={"choices": [{"message": {
            "role": "assistant", "content": "Review the verified account breakdown and its assumptions."}}]})


def request_ms(client, method, path, *, expected=200, **kwargs):
    started = perf_counter()
    response = client.request(method, path, **kwargs)
    elapsed = (perf_counter() - started) * 1000
    if response.status_code != expected:
        raise AssertionError(f"{method} {path}: expected {expected}, got {response.status_code}: {response.text[:300]}")
    return elapsed, response


def summarize(samples, sizes):
    ordered = sorted(samples)
    return {"runs": len(samples), "median_ms": round(statistics.median(samples), 2),
            "p95_ms": round(ordered[max(0, math.ceil(len(ordered) * .95) - 1)], 2),
            "min_ms": round(ordered[0], 2), "max_ms": round(ordered[-1], 2),
            "response_bytes": int(statistics.median(sizes)),
            "samples_ms": [round(value, 2) for value in samples]}


def measure(client, runs, method, path, *, before=None, payload=None, expected=200, revision=None):
    samples, sizes = [], []
    for index in range(runs):
        if before:
            before()
        kwargs = {"json": payload(index)} if payload else {}
        if revision is not None:
            kwargs["headers"] = {"If-Match": str(revision[0])}
        elapsed, response = request_ms(client, method, path, expected=expected, **kwargs)
        if revision is not None:
            revision[0] = int(response.headers["X-Workspace-Revision"])
        samples.append(elapsed)
        sizes.append(len(response.content))
    return summarize(samples, sizes)


def csv_payload(rows):
    base = date(2026, 9, 10)
    lines = ["Date,Description,Amount"]
    for index in range(rows):
        when = base - timedelta(days=index % 730)
        amount = "2500.00" if index % 50 == 0 else f"-{(index % 180) + 1}.{index % 100:02d}"
        lines.append(f"{when.isoformat()},Benchmark transaction {index:06d},{amount}")
    return {"account_id": "acc_checking", "csv": "\n".join(lines),
            "mapping": {"date": "Date", "description": "Description", "amount": "Amount"}}


def inference_isolation(client, held, revision):
    """Both operations must finish while the mock model is still withheld."""
    held.entered.clear()
    held.release.clear()
    with ThreadPoolExecutor(max_workers=3) as pool:
        inference = pool.submit(request_ms, client, "POST", "/api/ask",
                                json={"question": "How much money do I have?"})
        try:
            if not held.entered.wait(10):
                raise AssertionError("Assistant did not reach mock inference")
            started = perf_counter()
            read = pool.submit(request_ms, client, "GET", "/api/overview")
            write = pool.submit(request_ms, client, "PATCH", "/api/manage/accounts/acc_checking",
                                json={"nickname": "Edited while inference waits"},
                                headers={"If-Match": str(revision)})
            read_ms, read_response = read.result(timeout=10)
            write_ms, write_response = write.result(timeout=10)
            if inference.done() or held.release.is_set():
                raise AssertionError("Inference completed before the isolation check")
            result = {"passed": True, "read_ms": round(read_ms, 2), "write_ms": round(write_ms, 2),
                      "both_completed_before_model_release": True,
                      "held_during_operations_ms": round((perf_counter() - started) * 1000, 2),
                      "revision_after_write": int(write_response.headers["X-Workspace-Revision"])}
        finally:
            held.release.set()
        inference_ms, answer = inference.result(timeout=10)
        result["assistant_total_ms"] = round(inference_ms, 2)
        result["mock_model_calls"] = held.calls
        if not answer.json().get("answer_id"):
            raise AssertionError("Assistant answer was not persisted")
        return result


def scenario(directory, rows, runs):
    held = HeldModel()
    app = create_app("sqlite:///" + (directory / f"latency-{rows}.db").as_posix(), llm=held.client)
    with TestClient(app) as client:
        _, response = request_ms(client, "POST", "/api/auth/register", expected=201, json={
            "name": "Benchmark", "email": f"benchmark-{rows}@example.com", "password": "benchmark-only-password",
            "sample_data": True})
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        runtime = app.state.runtime
        setup = {"imported_rows": 0, "import_ms": 0}
        if rows:
            elapsed, imported = request_ms(client, "POST", "/api/transactions/import", json=csv_payload(rows))
            if imported.json()["imported"] != rows:
                raise AssertionError("Representative transaction import was incomplete")
            setup = {"imported_rows": rows, "import_ms": round(elapsed, 2)}

        def clear_calculation_cache():
            with runtime._guard:
                runtime._cache.clear()
                runtime._bootstrap_cache.clear()

        metrics = {}
        metrics["bootstrap_cold_calculation"] = measure(client, runs, "GET", "/api/bootstrap", before=clear_calculation_cache)
        metrics["bootstrap_warm_calculation"] = measure(client, runs, "GET", "/api/bootstrap")
        metrics["indexed_history_first_page"] = measure(client, runs, "GET", "/api/transactions?account_id=acc_checking&limit=50")
        if rows:
            metrics["indexed_history_last_page"] = measure(client, runs, "GET", f"/api/transactions?account_id=acc_checking&limit=50&offset={max(0, rows - 50)}")
        current = client.get("/api/workspace").json()
        if current["transaction_count"] != rows or len(current["transactions"]) != min(rows, 100):
            raise AssertionError("Workspace transaction projection is not bounded as expected")
        revision = [current["revision"]]
        metrics["account_update"] = measure(client, runs, "PATCH", "/api/manage/accounts/acc_checking",
            payload=lambda i: {"nickname": f"Benchmark checking {i}"}, revision=revision)
        metrics["account_create"] = measure(client, runs, "POST", "/api/manage/accounts", expected=201,
            payload=lambda i: {"nickname": f"Benchmark cash {i}", "type": "checking", "current": "0", "available": "0"}, revision=revision)
        isolation = inference_isolation(client, held, revision[0])
        return {"dataset": f"sample_plus_{rows}_transactions", "setup": setup,
                "metrics": metrics, "inference_isolation": isolation}


def run_benchmark(rows=5000, runs=12):
    if not 1 <= rows <= 5000 or not 3 <= runs <= 100:
        raise ValueError("Use 1–5000 imported rows and 3–100 measurement runs")
    with TemporaryDirectory(prefix="finpilot-benchmark-") as temporary:
        results = [scenario(Path(temporary), count, runs) for count in (0, rows)]
    return {"measured_at": datetime.now(timezone.utc).isoformat(),
            "environment": {"python": platform.python_version(), "platform": platform.platform(),
                            "database": "disposable SQLite WAL", "transport": "in-process ASGI TestClient",
                            "live_external_calls": False},
            "method": {"cold": "Application response/calculation caches cleared before each request; database/OS caches remain warm.",
                       "warm": "Same authenticated workspace revision with calculation cache populated.",
                       "timing": "Complete HTTP request/response including session auth, database, JSON and middleware; excludes setup unless separately reported.",
                       "percentile": "Nearest-rank p95; small repeat count, not a production SLA.",
                       "writes": "Create/update use the real command routes and If-Match revisions. Deletion is not an exposed workspace operation.",
                       "limitations": "Local single-process SQLite results exclude browser rendering, network/TLS and hosted PostgreSQL contention."},
            "scenarios": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=5000)
    parser.add_argument("--runs", type=int, default=12)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run_benchmark(args.rows, args.runs)
    rendered = json.dumps(result, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
