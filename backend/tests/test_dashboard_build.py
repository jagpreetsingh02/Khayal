"""Frontend check: the React dashboard type-checks, builds and lints.

Skipped when Node/npm or dashboard/node_modules are missing (backend-only setups);
run `cd dashboard && npm install` once to enable it.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

DASHBOARD = Path(__file__).resolve().parents[2] / "dashboard"
pytestmark = pytest.mark.skipif(
    shutil.which("npm") is None or not (DASHBOARD / "node_modules").is_dir(),
    reason="npm or dashboard/node_modules missing (cd dashboard && npm install)",
)


def npm(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["npm", "run", script], cwd=DASHBOARD, capture_output=True, text=True, timeout=300
    )


def test_dashboard_builds():
    result = npm("build")  # tsc -b && vite build
    assert result.returncode == 0, result.stdout + result.stderr
    index = (DASHBOARD / "dist" / "index.html").read_text()
    assert "/dashboard/assets/" in index  # served by FastAPI under /dashboard


def test_dashboard_lints_clean():
    result = npm("lint")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "warning" not in result.stdout.lower(), result.stdout
