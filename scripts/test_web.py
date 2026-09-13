"""Run web render contracts with a disposable authenticated local API.

Uses one process tree/network namespace; never touches a configured database,
bank provider, or model. Browser interaction/visual QA is a separate release gate.
"""
from pathlib import Path
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix="finpilot-web-") as directory:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = {**os.environ, "FINPILOT_ENV": "development", "FINPILOT_PUBLIC_ORIGIN": "",
               "FINPILOT_DATABASE_URL": "sqlite:///" + str(Path(directory) / "web.db"),
               "FINPILOT_LLM_BASE_URL": "http://127.0.0.1:1/v1", "FINPILOT_LLM_MODEL": "",
               "NO_PROXY": "127.0.0.1,localhost", "FINPILOT_TEST_URL": f"http://127.0.0.1:{port}"}
        for name in ("PLAID_CLIENT_ID", "PLAID_SECRET", "PLAID_ENV", "FINPILOT_TOKEN_KEY"):
            env.pop(name, None)
        with open(Path(directory) / "server.log", "w+") as log:
            process = subprocess.Popen([sys.executable, "-m", "uvicorn", "finpilot.api.app:app",
                "--host", "127.0.0.1", "--port", str(port)], cwd=ROOT, env=env, stdout=log, stderr=log)
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    try:
                        with opener.open(env["FINPILOT_TEST_URL"] + "/api/health", timeout=1) as response:
                            if response.status == 200:
                                break
                    except OSError:
                        if process.poll() is not None:
                            raise RuntimeError("Disposable API exited before readiness") from None
                        time.sleep(.1)
                else:
                    raise RuntimeError("Disposable API did not become ready")
                for name in ("frontend.mjs", "management_frontend.mjs", "execution_frontend.mjs",
                             "bank_frontend.mjs", "conversation_frontend.mjs"):
                    subprocess.run(["node", str(ROOT / "tests" / name)], env=env, cwd=ROOT, check=True, timeout=60)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    main()
