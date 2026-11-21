"""UI test infrastructure: starts an ISOLATED backend (its own internal DB) and serves the built
frontend, then hands Playwright a logged-out browser context.

Requirements: Node.js on PATH, `playwright install chromium`, the two local MongoDB test
instances. The frontend is built automatically if frontend/dist is missing.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from conftest import MONITORED_URI, RUN_ID

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_http(url: str, timeout: float = 60) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if httpx.get(url, timeout=2).status_code < 500:
                return
        except httpx.HTTPError:
            time.sleep(0.4)
    raise RuntimeError(f"timeout waiting for {url}")


@pytest.fixture(scope="session")
def ui_urls(mongo_available):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for UI tests")
    if not (FRONTEND / "node_modules").exists():
        pytest.skip("Run `npm install` in frontend/ first")
    if not (FRONTEND / "dist" / "index.html").exists():
        npm = shutil.which("npm")
        subprocess.run([npm, "run", "build"], cwd=FRONTEND, check=True, capture_output=True)
    api_port, web_port = _free_port(), _free_port()
    db_name = f"dbpulse_test_ui_{RUN_ID}"
    env = {
        **os.environ,
        "INTERNAL_DB_NAME": db_name,
        "ENABLE_INPROCESS_SCHEDULER": "false",
        "CORS_ORIGINS": f"http://127.0.0.1:{web_port}",
    }
    api = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(api_port), "--log-level", "warning"],
        cwd=ROOT / "backend",
        env=env,
    )
    web_env = {**os.environ, "VITE_API_PROXY": f"http://127.0.0.1:{api_port}"}
    web = subprocess.Popen(
        [
            node,
            str(FRONTEND / "node_modules" / "vite" / "bin" / "vite.js"),
            "preview",
            "--port",
            str(web_port),
            "--strictPort",
            "--host",
            "127.0.0.1",
        ],
        cwd=FRONTEND,
        env=web_env,
    )
    try:
        _wait_http(f"http://127.0.0.1:{api_port}/api/v1/health")
        _wait_http(f"http://127.0.0.1:{web_port}/")
        yield {"web": f"http://127.0.0.1:{web_port}", "api": f"http://127.0.0.1:{api_port}"}
    finally:
        for p in (web, api):
            p.terminate()
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        from pymongo import MongoClient

        MongoClient("mongodb://127.0.0.1:27117").drop_database(db_name)


@pytest.fixture(scope="module")
def ui(browser, ui_urls):
    ctx = browser.new_context(base_url=ui_urls["web"], viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(20_000)
    page = ctx.new_page()
    yield page
    ctx.close()


@pytest.fixture(scope="session")
def ui_mongo_uri() -> str:
    return MONITORED_URI
