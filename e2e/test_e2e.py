import json
import re
import time

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


def _wait_for_file(path, predicate, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists() and predicate(path.read_text(encoding="utf-8")):
            return True
        time.sleep(0.1)
    return False


def test_copilot_json_apply_and_undo(server, page, sandbox_project, screenshots_dir):
    page.goto(server)

    expect(page.locator("#fileList")).to_contain_text("app.py", timeout=15_000)
    page.screenshot(path=str(screenshots_dir / "01-home.png"))

    page.select_option("#modePicker", "copilot")
    assert page.evaluate("localStorage.getItem('chatbridge.mode')") == "copilot"

    page.locator(".tree-row", has_text="app.py").first.click()
    expect(page.locator("#selectedTokens")).not_to_have_text("0 tokens")

    page.fill("#task", "Change the greeting to hello world")
    page.get_by_role("button", name=re.compile("Send Goal")).click()
    expect(page.locator("#stagePasteCard")).to_be_visible()

    expect(page.locator("#copyJsonBtn")).to_be_visible()
    page.click("#copyJsonBtn")
    expect(page.locator("#jsonPartsPanel")).to_be_visible()
    page.locator("#jsonPartsList button").first.click()

    page.wait_for_function(
        """() => navigator.clipboard.readText().then((t) => {
            try {
                const o = JSON.parse(t);
                return o && o.mode === 'copilot';
            } catch (e) {
                return false;
            }
        })""",
        timeout=10_000,
    )
    chunk = json.loads(page.evaluate("navigator.clipboard.readText()"))
    assert set(chunk) == {"mode", "part", "total_parts", "prompt"}
    assert chunk["mode"] == "copilot"
    assert chunk["part"] == 1
    assert 1 <= chunk["total_parts"] <= 100
    assert len(chunk["prompt"]) <= 28_000
    page.screenshot(path=str(screenshots_dir / "02-copilot-json.png"))

    reply = (
        '<<<EDIT path="app.py">>>\n'
        "<<<OLD>>>\n"
        "def hello():\n"
        '    return "hi"\n'
        "<<<NEW>>>\n"
        "def hello():\n"
        '    return "hello world"\n'
        "<<<END>>>\n"
        "<<<STATUS: DONE>>>"
    )
    page.fill("#aiResponse", reply)
    page.evaluate("App.runOps(true)")
    expect(page.locator(".diff-file").first).to_be_visible(timeout=10_000)

    app_file = sandbox_project / "app.py"
    assert _wait_for_file(app_file, lambda text: "hello world" in text)
    page.screenshot(path=str(screenshots_dir / "03-applied-diff.png"))

    page.click("#undoBtn")
    page.wait_for_function(
        "() => document.getElementById('inlinePopupMsg').textContent.includes('Reverted')",
        timeout=10_000,
    )
    assert _wait_for_file(app_file, lambda text: text == 'def hello():\n    return "hi"\n')


def test_editor_save_and_reload(server, page, sandbox_project):
    page.goto(server)
    expect(page.locator("#fileList")).to_contain_text("app.py", timeout=15_000)
    page.locator(".tree-row", has_text="app.py").first.dblclick()
    expect(page.locator("#editorModal")).to_be_visible(timeout=10_000)
    page.fill("#editorCode", 'def hello():\n    return "edited"\n')
    page.click('button:has-text("Save File")')
    page.wait_for_function("() => document.getElementById('editorModal').classList.contains('hidden')", timeout=10_000)
    app_file = sandbox_project / "app.py"
    assert _wait_for_file(app_file, lambda text: "edited" in text)


def test_undo_history_modal_opens(server, page, sandbox_project):
    page.goto(server)
    expect(page.locator("#fileList")).to_contain_text("app.py", timeout=15_000)
    page.click("#undoHistoryBtn")
    expect(page.locator("#undoModal")).to_be_visible(timeout=10_000)
    page.locator("#undoModal button", has_text="Close").click()
