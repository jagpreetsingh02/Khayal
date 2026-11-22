"""Start the Care Circle demo: the MCP server and the API (web simulator) together.

    cd backend
    uv run python ../scripts/start.py            # http://localhost:8000/simulator
    uv run python ../scripts/start.py --port 8080

The MCP server listens on MCP_SERVER_URL (default http://localhost:8001/mcp, localhost only);
the API's agent calls it over streamable HTTP. Both share the SQLite file DATABASE_URL.
Ctrl+C stops both.
"""

import argparse
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from app.config import get_settings  # noqa: E402


def wait_for_port(port: int, seconds: float, proc: subprocess.Popen) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        with socket.socket() as s:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.2)
    return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run the MCP server and the API together.")
    ap.add_argument("--port", type=int, default=8000, help="API port (default 8000)")
    ap.add_argument("--host", default="127.0.0.1", help="API host (default 127.0.0.1)")
    args = ap.parse_args(argv)

    # `kill <pid>` (SIGTERM) should stop both servers too, not only Ctrl+C.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    settings = get_settings()
    mcp_port = urlparse(settings.mcp_server_url).port or 8001
    py = sys.executable
    procs: list[subprocess.Popen] = []
    try:
        mcp = subprocess.Popen([py, "-m", "mcp_server.server"], cwd=BACKEND)
        procs.append(mcp)
        if not wait_for_port(mcp_port, 20, mcp):
            print(f"MCP server did not start on port {mcp_port}.", file=sys.stderr)
            return 1
        api_cmd = [py, "-m", "uvicorn", "app.main:app", "--host", args.host]
        api = subprocess.Popen([*api_cmd, "--port", str(args.port)], cwd=BACKEND)
        procs.append(api)
        if not wait_for_port(args.port, 20, api):
            print(f"API did not start on port {args.port}.", file=sys.stderr)
            return 1
        print(
            f"\n  Care Circle is running (APP_MODE={settings.app_mode}, "
            f"CHANNEL={settings.channel}, LLM_PROVIDER={settings.llm_provider})\n"
            f"  Simulator:  http://localhost:{args.port}/simulator\n"
            f"  MCP server: {settings.mcp_server_url}\n"
            "  Ctrl+C to stop.\n",
            flush=True,
        )
        while all(p.poll() is None for p in procs):
            time.sleep(0.5)
        return 1  # one of them exited on its own
    except KeyboardInterrupt:
        return 0
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    sys.exit(main())
