"""Reproduce and re-check the shared-address throttle behind a real reverse proxy (G1.2).

Runs FinPilot under Uvicorn on this machine and an nginx container in front of it, so requests
reach FinPilot from the container network rather than from loopback. Each simulated user sends its
own address in `X-Test-Client`, which nginx passes on as `X-Forwarded-For`, exactly as an ingress
would. Nine sign-ups then run through the proxy twice:

  untrusted  `FORWARDED_ALLOW_IPS` names an address that is not the proxy, so forwarded addresses
             are ignored, every user shares the proxy's address and the ninth sign-up is throttled.
             This is what a deployment gets by default whenever the ingress is not on loopback.
  trusted    `FORWARDED_ALLOW_IPS` names the proxy's address, so each user is counted separately
             and nine sign-ups succeed, while nine sign-ups from one address are still throttled.

The proxy's own address depends on the container runtime: Docker Desktop for Windows forwards
container traffic from `127.0.0.1`, which Uvicorn's default already trusts, while a Linux bridge
network reaches the host from a gateway such as `172.17.0.1`, which it does not.

Writes `validation/proxy-client-addresses.json`. Needs Docker and the `nginx:alpine` image; it
creates a throwaway SQLite database in a temporary folder and no account outside it.

Usage: .venv/Scripts/python.exe scripts/verify_proxy_client_addresses.py
"""
from __future__ import annotations

import json
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "validation" / "proxy-client-addresses.json"
IMAGE = "nginx:alpine"
REGISTER_LIMIT = 8  # finpilot/api/auth_routes.py: 8 sign-ups per 5 minutes per client address
USERS = REGISTER_LIMIT + 1
# Documentation addresses (RFC 5737); they never belong to a real host.
CLIENTS = [f"203.0.113.{number + 10}" for number in range(USERS)]
# Reserved for documentation (RFC 5737) and never the proxy, so forwarded headers are ignored.
NOT_THE_PROXY = "198.51.100.7"
DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))
NGINX_CONF = """server {{
    listen 80;
    location / {{
        proxy_pass http://host.docker.internal:{port};
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto http;
        proxy_set_header X-Forwarded-For $http_x_test_client;
    }}
}}
"""


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def docker_available() -> bool:
    try:
        return subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"],
                              capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def start_app(port: int, database: Path, log: Path, trusted: str | None):
    """Uvicorn bound to every interface so the container can reach it; access log on to read peers."""
    environment = {**os.environ, "PYTHONUNBUFFERED": "1", "FINPILOT_ENV": "development",
                   "FINPILOT_PUBLIC_ORIGIN": "", "FINPILOT_LLM_BASE_URL": "http://127.0.0.1:9/v1",
                   "FINPILOT_DATABASE_URL": "sqlite:///" + database.as_posix()}
    environment.pop("FORWARDED_ALLOW_IPS", None)
    if trusted:
        environment["FORWARDED_ALLOW_IPS"] = trusted
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "finpilot.api.app:app", "--host", "0.0.0.0", "--port", str(port)],
        cwd=ROOT, env=environment, stdout=log.open("wb"), stderr=subprocess.STDOUT)
    deadline = time.monotonic() + 90
    while True:
        try:
            DIRECT.open(f"http://127.0.0.1:{port}/api/health", timeout=2).read()
            return server
        except OSError:
            if server.poll() is not None or time.monotonic() > deadline:
                server.terminate()
                raise SystemExit("Uvicorn did not start:\n" + log.read_text(encoding="utf-8", errors="replace"))
            time.sleep(0.25)


def start_proxy(app_port: int, folder: Path) -> tuple[str, int]:
    """An nginx container in front of the app, published on loopback only."""
    config = folder / "proxy.conf"
    config.write_text(NGINX_CONF.format(port=app_port), encoding="utf-8")
    port, name = free_port(), "finpilot-proxy-check-" + uuid.uuid4().hex[:8]
    started = subprocess.run(
        ["docker", "run", "--detach", "--name", name, "--add-host", "host.docker.internal:host-gateway",
         "--publish", f"127.0.0.1:{port}:80", "--volume", f"{config}:/etc/nginx/conf.d/default.conf:ro", IMAGE],
        capture_output=True, text=True, timeout=300)
    if started.returncode != 0:
        raise SystemExit("Could not start the nginx container: " + started.stderr.strip())
    deadline = time.monotonic() + 60
    while True:
        try:
            DIRECT.open(f"http://127.0.0.1:{port}/api/health", timeout=2).read()
            return name, port
        except OSError:
            if time.monotonic() > deadline:
                stop_proxy(name)
                raise SystemExit("The nginx container never forwarded a request.")
            time.sleep(0.25)


def stop_proxy(name: str) -> None:
    subprocess.run(["docker", "rm", "--force", name], capture_output=True, timeout=120)


def register(port: int, client: str) -> int:
    """One sign-up through the proxy, as the given simulated client address."""
    body = json.dumps({"name": "Proxy check", "email": f"proxy-{uuid.uuid4().hex}@example.com",
                       "password": secrets.token_urlsafe(18), "sample_data": False}).encode("utf-8")
    request = urllib.request.Request(f"http://127.0.0.1:{port}/api/auth/register", data=body,
                                     headers={"Content-Type": "application/json", "X-Test-Client": client})
    try:
        return DIRECT.open(request, timeout=30).status
    except urllib.error.HTTPError as error:
        error.read()
        return error.code


def peer_addresses(log: Path) -> list[str]:
    """Client addresses Uvicorn saw, from its access log."""
    text = log.read_text(encoding="utf-8", errors="replace")
    return sorted({match.group(1) for match in re.finditer(r"(\d+\.\d+\.\d+\.\d+):\d+ - \"POST /api/auth/register", text)})


def run_phase(folder: Path, name: str, trusted: str | None, clients: list[str]) -> dict:
    app_port, log, database = free_port(), folder / f"{name}.log", folder / f"{name}.db"
    server = start_app(app_port, database, log, trusted)
    proxy = None
    try:
        proxy, proxy_port = start_proxy(app_port, folder)
        statuses = [register(proxy_port, client) for client in clients]
    finally:
        if proxy:
            stop_proxy(proxy)
        server.terminate()
        server.wait(timeout=30)
    return {"trusted_proxy_addresses": trusted or "default (127.0.0.1)",
            "distinct_client_addresses": len(set(clients)), "sign_up_statuses": statuses,
            "throttled": statuses.count(429), "app_saw_peers": peer_addresses(log)}


def main() -> int:
    if not docker_available():
        print("Docker is not available, so the proxy reproduction cannot run.", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
        folder = Path(temporary)
        untrusted = run_phase(folder, "untrusted", NOT_THE_PROXY, CLIENTS)
        proxy_address = next(iter(untrusted["app_saw_peers"]), "")
        if not proxy_address:
            print("Could not read the proxy address from the Uvicorn access log.", file=sys.stderr)
            return 2
        trusted = run_phase(folder, "trusted", proxy_address, CLIENTS)
        one_client = run_phase(folder, "one-client", proxy_address, [CLIENTS[0]] * USERS)
    evidence = {
        "checked": date.today().isoformat(), "goal": "G1.2", "proxy": f"{IMAGE} container",
        "register_limit_per_client": REGISTER_LIMIT, "simulated_users": USERS,
        "proxy_peer_address": proxy_address,
        "uvicorn_default_trusts": "127.0.0.1",
        "default_is_safe_here": proxy_address == "127.0.0.1",
        "untrusted_proxy": untrusted, "trusted_proxy": trusted, "trusted_proxy_one_client": one_client,
        "expected": {
            "untrusted_proxy": "forwarded addresses ignored, so all users share the proxy address and one is throttled",
            "proxy_peer_address": "the address FinPilot must trust; the Uvicorn default only covers 127.0.0.1",
            "trusted_proxy": "each forwarded address counted separately, so no user is throttled",
            "trusted_proxy_one_client": "one address over the limit is still throttled"},
    }
    evidence["passed"] = (untrusted["throttled"] == 1 and trusted["throttled"] == 0
                          and one_client["throttled"] == 1)
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2))
    print(("PASS: " if evidence["passed"] else "FAIL: ") + str(EVIDENCE))
    return 0 if evidence["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
