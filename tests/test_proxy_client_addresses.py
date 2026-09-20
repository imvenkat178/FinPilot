"""Throttles key on a forwarded client address only when the peer is trusted (G1.2)."""
from __future__ import annotations

import json
import logging
import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

from finpilot.api.app import warn_about_proxy_trust

ROOT = Path(__file__).resolve().parent.parent
REGISTER_LIMIT = 8  # finpilot/api/auth_routes.py
# Reserved for documentation (RFC 5737), so it is never this machine: forwarded headers are ignored.
NOT_THIS_MACHINE = "198.51.100.7"
CLIENTS = [f"203.0.113.{number + 10}" for number in range(REGISTER_LIMIT + 1)]
DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def test_production_warns_when_only_loopback_is_trusted(monkeypatch, caplog):
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    with caplog.at_level(logging.WARNING, logger="finpilot.api.app"):
        warn_about_proxy_trust(True)
    assert "FORWARDED_ALLOW_IPS is unset" in caplog.text and "throttles" in caplog.text


@pytest.mark.parametrize("trusted,warns", [("", True), ("127.0.0.1", True), ("localhost", True),
                                           ("10.0.1.4", False), ("10.0.1.4, 10.0.1.5", False)])
def test_the_warning_stops_once_an_ingress_is_named(monkeypatch, caplog, trusted, warns):
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", trusted)
    with caplog.at_level(logging.WARNING, logger="finpilot.api.app"):
        warn_about_proxy_trust(True)
    assert bool(caplog.text) is warns


@pytest.mark.parametrize("trusted", ["", "127.0.0.1"])
def test_development_never_warns(monkeypatch, caplog, trusted):
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", trusted)
    with caplog.at_level(logging.WARNING, logger="finpilot.api.app"):
        warn_about_proxy_trust(False)
    assert not caplog.text


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _register(base: str, client: str) -> int:
    body = json.dumps({"name": "Proxy check", "email": f"proxy-{uuid.uuid4().hex}@example.com",
                       "password": secrets.token_urlsafe(18), "sample_data": False}).encode("utf-8")
    request = urllib.request.Request(base + "/api/auth/register", data=body,
                                     headers={"Content-Type": "application/json", "X-Forwarded-For": client})
    try:
        return DIRECT.open(request, timeout=30).status
    except urllib.error.HTTPError as error:
        error.read()
        return error.code


def _sign_up_statuses(trusted: str, tmp_path: Path) -> list[int]:
    """Real Uvicorn, so its proxy-header handling decides what FinPilot sees as the client."""
    port = _free_port()
    environment = {**os.environ, "PYTHONUNBUFFERED": "1", "FINPILOT_ENV": "development",
                   "FINPILOT_PUBLIC_ORIGIN": "", "FINPILOT_LLM_BASE_URL": "http://127.0.0.1:9/v1",
                   "FORWARDED_ALLOW_IPS": trusted,
                   "FINPILOT_DATABASE_URL": "sqlite:///" + (tmp_path / f"{trusted}.db").as_posix()}
    output, base = tmp_path / f"{trusted}.log", f"http://127.0.0.1:{port}"
    with output.open("wb") as sink:
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "finpilot.api.app:app", "--host", "127.0.0.1",
             "--port", str(port), "--no-access-log"], cwd=ROOT, env=environment,
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
            return [_register(base, client) for client in CLIENTS]
        finally:
            server.terminate()
            server.wait(timeout=30)


def test_forwarded_addresses_from_a_trusted_peer_get_their_own_throttle(tmp_path):
    statuses = _sign_up_statuses("127.0.0.1", tmp_path)
    assert statuses == [201] * len(CLIENTS), "each forwarded address should have its own sign-up limit"


def test_forwarded_addresses_from_an_untrusted_peer_are_ignored(tmp_path):
    statuses = _sign_up_statuses(NOT_THIS_MACHINE, tmp_path)
    assert statuses[:REGISTER_LIMIT] == [201] * REGISTER_LIMIT
    assert statuses[REGISTER_LIMIT] == 429, "a spoofed address must not buy a fresh sign-up limit"
