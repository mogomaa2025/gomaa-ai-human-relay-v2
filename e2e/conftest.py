import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

REPO_ROOT = Path(__file__).resolve().parents[1]
SCREENSHOTS_DIR = Path(__file__).resolve().parent / "screenshots"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def sandbox_project(tmp_path):
    project = tmp_path / "sandbox"
    project.mkdir()
    (project / "app.py").write_text('def hello():\n    return "hi"\n', encoding="utf-8")
    return project


@pytest.fixture
def server(sandbox_project):
    port = _free_port()
    env = os.environ.copy()
    env["PROJECT_DIR"] = str(sandbox_project)
    proc = subprocess.Popen(
        [sys.executable, "main.py", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 20
    while time.time() < deadline:
        if proc.poll() is not None:
            out, err = proc.communicate(timeout=5)
            raise RuntimeError(f"Server exited early:\n{out}\n{err}")
        try:
            with urllib.request.urlopen(base_url + "/", timeout=1) as resp:
                if resp.status == 200:
                    break
        except Exception:
            time.sleep(0.2)
    else:
        proc.terminate()
        out, err = proc.communicate(timeout=5)
        raise RuntimeError(f"Server did not start in time:\n{out}\n{err}")

    yield base_url

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True)
        yield instance
        instance.close()


@pytest.fixture
def page(browser, server):
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    context.grant_permissions(
        ["clipboard-read", "clipboard-write"],
        origin=server,
    )
    page = context.new_page()
    yield page
    context.close()


@pytest.fixture
def screenshots_dir():
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    return SCREENSHOTS_DIR
