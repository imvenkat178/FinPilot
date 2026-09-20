"""Request logs stay private: one request line, and no query strings, bodies or amounts (G1.1)."""
from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from uvicorn.logging import AccessFormatter

from finpilot.ai.llm import LocalLLM, LLMConfig
from finpilot.api.app import create_app
from finpilot.api.request_logging import ACCESS_LOGGER, REQUEST_LOGGER, DropQueryStrings

ROOT = Path(__file__).resolve().parent.parent
SENSITIVE = ("therapy-copay", "category=medical", "98765.43")
# Query strings reach proxy, hosting platform and error tracker logs that FinPilot cannot
# configure, so they may carry only identifiers, dates, counts and flags.
ALLOWED_QUERY_PARAMETERS = {"account_id", "conversation_id", "days", "horizon_days", "income_event_id",
                            "limit", "month", "occurrence_date", "paused", "year"}
DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def test_query_parameters_carry_no_amounts_or_free_text():
    schema = create_app("sqlite://").openapi()
    unexpected = sorted(
        f"{method.upper()} {path} ?{parameter['name']}"
        for path, operations in schema["paths"].items()
        for method, operation in operations.items()
        for parameter in operation.get("parameters", [])
        if parameter["in"] == "query" and parameter["name"] not in ALLOWED_QUERY_PARAMETERS)
    assert not unexpected, f"Send these values in the JSON request body instead: {unexpected}"


def test_request_line_leaves_out_query_strings_and_bodies(caplog):
    app = create_app("sqlite:///:memory:", llm=LocalLLM(LLMConfig()))
    with caplog.at_level(logging.INFO, logger=REQUEST_LOGGER), TestClient(app) as client:
        client.get("/api/forecast", params={"days": 30, "q": "therapy-copay", "category": "medical"})
        client.post("/api/debt/what-if", json={"amount": "98765.43"})
    lines = [record.getMessage() for record in caplog.records if record.name == REQUEST_LOGGER]
    assert any(line.startswith("GET /api/forecast status=401 ") for line in lines), lines
    assert any(line.startswith("POST /api/debt/what-if status=401 ") for line in lines), lines
    assert not [line for line in lines if "?" in line or any(value in line for value in SENSITIVE)], lines


def test_access_log_filter_drops_query_strings():
    create_app("sqlite://")
    assert any(isinstance(item, DropQueryStrings) for item in logging.getLogger(ACCESS_LOGGER).filters)
    record = logging.LogRecord(ACCESS_LOGGER, logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                               ("127.0.0.1:50000", "GET", "/api/forecast?days=30&q=therapy-copay", "1.1", 401), None)
    assert DropQueryStrings().filter(record)
    assert record.getMessage() == '127.0.0.1:50000 - "GET /api/forecast HTTP/1.1" 401'
    formatter = AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False)
    assert formatter.format(record) == '127.0.0.1:50000 - "GET /api/forecast HTTP/1.1" 401 Unauthorized'


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _container_uvicorn_arguments() -> list[str]:
    command = next(line for line in (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()
                   if line.startswith("CMD "))
    argv = json.loads(command[len("CMD "):])
    return argv[argv.index("uvicorn") + 1:]


def _call(url: str, body: dict | None = None) -> None:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"} if data else {})
    try:
        DIRECT.open(request, timeout=10).read()
    except urllib.error.HTTPError as error:
        error.read()


def _serve(arguments: list[str], tmp_path: Path) -> str:
    """Start Uvicorn as a real process, send sensitive requests and return everything it printed."""
    port, arguments = _free_port(), list(arguments)
    for flag, value in (("--host", "127.0.0.1"), ("--port", str(port))):
        if flag in arguments:
            arguments[arguments.index(flag) + 1] = value
        else:
            arguments += [flag, value]
    environment = {**os.environ, "PYTHONUNBUFFERED": "1", "FINPILOT_ENV": "development",
                   "FINPILOT_PUBLIC_ORIGIN": "", "FINPILOT_LLM_BASE_URL": "http://127.0.0.1:9/v1",
                   "FINPILOT_DATABASE_URL": "sqlite:///" + (tmp_path / "server.db").as_posix()}
    output, base = tmp_path / "server.log", f"http://127.0.0.1:{port}"
    with output.open("wb") as sink:
        server = subprocess.Popen([sys.executable, "-m", "uvicorn", *arguments], cwd=ROOT, env=environment,
                                  stdout=sink, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 90
            while True:
                try:
                    DIRECT.open(base + "/api/health", timeout=2).read()
                    break
                except OSError:
                    if server.poll() is not None or time.monotonic() > deadline:
                        pytest.fail("Uvicorn did not start:\n" + output.read_text(encoding="utf-8", errors="replace"))
                    time.sleep(0.25)
            _call(base + "/api/forecast?days=30&q=therapy-copay&category=medical")
            _call(base + "/api/debt/what-if", {"amount": "98765.43"})
            _call(base + "/api/health")
        finally:
            server.terminate()
            server.wait(timeout=30)
    return output.read_text(encoding="utf-8", errors="replace")


def _request_lines(output: str) -> list[str]:
    return [line for line in output.splitlines() if f" {REQUEST_LOGGER} " in line]


def _assert_private(output: str) -> None:
    leaked = [value for value in SENSITIVE if value in output]
    assert not leaked, f"sensitive values reached the server output: {leaked}\n{output}"
    assert not [line for line in output.splitlines() if "/api/" in line and "?" in line], output


def test_container_command_writes_one_private_line_per_request(tmp_path):
    arguments = _container_uvicorn_arguments()
    assert "--no-access-log" in arguments
    output = _serve(arguments, tmp_path)
    lines = _request_lines(output)
    assert any("GET /api/forecast status=401 " in line for line in lines), output
    assert any("POST /api/debt/what-if status=401 " in line for line in lines), output
    assert ' HTTP/1.1" ' not in output, "the container image should write one line per request, not a second access line"
    _assert_private(output)


def test_plain_uvicorn_command_emits_request_lines_without_query_strings(tmp_path):
    output = _serve(["finpilot.api.app:app"], tmp_path)
    assert any("GET /api/forecast status=401 " in line for line in _request_lines(output)), output
    assert '"GET /api/forecast HTTP/1.1" 401' in output, output
    _assert_private(output)
