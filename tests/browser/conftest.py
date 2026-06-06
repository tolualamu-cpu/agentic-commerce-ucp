"""Playwright browser-test harness — REAL JS-lifecycle coverage.

The TestClient suite can assert that the chat/cart wiring is PRESENT in the
rendered HTML, but it cannot prove the JavaScript behaves correctly because it
never runs a browser. These tests close that gap: they drive a real headless
Chromium against a live, throwaway server so the actual event handlers run —
the only way to prove the recurring bugs (user bubble not appearing until you
navigate; cart badge reading 2 for a 1-item cart) truly cannot recur.

Determinism without a live model:
  * The server boots OFFLINE (``CARTO_FORCE_OFFLINE=1``) so the orchestrator
    never calls Anthropic — the POST /chat path emits a fixed "chat offline"
    reply + done, which is enough to exercise the optimistic user bubble, the
    swallowed SSE echo, and the in-place reveal.
  * For everything that normally needs the model (product cards rendered above
    the summary, the single summary bubble, the cart-action confirmation, the
    absolute badge), the test injects an EXACT ordered SSE burst via the
    env-gated ``/__test__/sse/emit`` hook (``CARTO_ENABLE_TEST_HOOKS=1``).

Graceful skip: if Playwright (or its browser binary) is not installed, the
whole browser suite SKIPS rather than fails, so ``pytest tests/ -x -q`` stays
green for contributors who haven't run ``playwright install``. CI installs it
and runs these for real.

Isolation / async rule: the server runs in a SEPARATE process (subprocess),
and Playwright's sync API runs in its own greenlet — neither touches the
in-process asyncio event loop the rest of the suite uses, so there is no
loop-contamination concern regardless of file sort order.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

# Skip the entire browser suite cleanly if Playwright isn't available.
sync_api = pytest.importorskip(
    "playwright.sync_api",
    reason="Playwright not installed — run `pip install -r requirements-dev.txt` "
    "and `python -m playwright install chromium` to enable browser e2e tests.",
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _free_port() -> int:
    """Grab an ephemeral free port (small race window, fine for tests)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_until_ready(base_url: str, proc: subprocess.Popen, timeout: float = 40.0) -> None:
    """Poll GET /chat until the server answers 200, or fail/skip on trouble."""
    deadline = time.time() + timeout
    last_err: Exception | None = None
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"test server exited early (code {proc.returncode}) before becoming ready"
            )
        try:
            with urllib.request.urlopen(f"{base_url}/chat", timeout=2) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, OSError) as exc:
            last_err = exc
        time.sleep(0.25)
    raise RuntimeError(f"test server did not become ready within {timeout}s: {last_err}")


@pytest.fixture(scope="session")
def live_server(tmp_path_factory):
    """Launch a throwaway uvicorn server (offline + test hooks) on a free port."""
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"

    env = dict(os.environ)
    # Deterministic, model-free, with the SSE injection hook available. Run
    # from a temp cwd with PYTHONPATH set so the app's load_dotenv() does NOT
    # pick up the real .env, and force-offline guarantees no live model even if
    # a key leaks through the environment.
    env["CARTO_FORCE_OFFLINE"] = "1"
    env["CARTO_ENABLE_TEST_HOOKS"] = "1"
    env["ANTHROPIC_API_KEY"] = ""
    db_dir = tmp_path_factory.mktemp("carto_browser_db")
    env["DB_PATH"] = str(db_dir / "demo.json")
    env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "web.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(db_dir),  # outside the project tree → load_dotenv finds no .env
        env=env,
    )
    try:
        _wait_until_ready(base_url, proc)
    except Exception:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        raise

    yield base_url

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()


@pytest.fixture(scope="session")
def _browser():
    """Launch headless Chromium once for the suite; skip if not installed."""
    pw = sync_api.sync_playwright().start()
    try:
        browser = pw.chromium.launch()
    except Exception as exc:  # noqa: BLE001 — missing browser binary → skip, not fail
        pw.stop()
        pytest.skip(
            f"Chromium not available for Playwright ({exc}); "
            "run `python -m playwright install chromium`."
        )
    yield browser
    browser.close()
    pw.stop()


@pytest.fixture
def page(_browser):
    """Fresh browser context (fresh cookies/session) per test."""
    context = _browser.new_context()
    pg = context.new_page()
    yield pg
    context.close()


def emit_sse(page, base_url: str, events: list[dict]) -> None:
    """Inject an ordered SSE burst onto the page's session via the test hook.

    Uses the page's OWN request context so the session cookie is carried — the
    events land on exactly the session whose /chat/stream the page is consuming.
    """
    resp = page.request.post(f"{base_url}/__test__/sse/emit", data={"events": events})
    assert resp.ok, f"sse emit failed: {resp.status} {resp.text()}"


@pytest.fixture
def sse_emit():
    """Expose the SSE-injection helper as a fixture.

    ``tests/browser`` is not a Python package (no ``__init__.py``, by design so
    it doesn't perturb the flat ``tests/`` collection), so test modules can't
    ``from .conftest import emit_sse``. They take this fixture instead.
    """
    return emit_sse
