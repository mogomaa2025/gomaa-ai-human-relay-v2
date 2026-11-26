import inspect

from core.patch_engine import PatchEngine


def _edit_response(path, old, new, status="DONE"):
    return (
        f'<<<EDIT path="{path}">>>\n'
        "<<<OLD>>>\n"
        f"{old}\n"
        "<<<NEW>>>\n"
        f"{new}\n"
        "<<<END>>>\n"
        f"<<<STATUS: {status}>>>"
    )


def test_edit_apply(pe, fm):
    response = _edit_response(
        "a.py",
        "def alpha():\n    return 1",
        "def alpha():\n    return 2",
    )
    parsed = pe.parse_response(response)
    assert len(parsed["ops"]) == 1
    assert parsed["status_flag"] == "DONE"

    results = pe.execute(parsed["ops"], apply_changes=True)
    assert results[0]["status"] == "applied"
    assert "return 2" in fm.read_raw("a.py")


def test_edit_ready_without_apply(pe, fm):
    response = _edit_response(
        "a.py",
        "def alpha():\n    return 1",
        "def alpha():\n    return 3",
    )
    results = pe.execute(pe.parse_response(response)["ops"], apply_changes=False)
    assert results[0]["status"] == "ready"
    assert "return 1" in fm.read_raw("a.py")


def test_write_creates_file(pe, fm):
    response = '<<<WRITE path="new_file.py">>>\nprint("created")\n<<<END>>>'
    results = pe.execute(pe.parse_response(response)["ops"], apply_changes=True)
    assert results[0]["status"] == "applied"
    assert fm.read_raw("new_file.py") == 'print("created")'


def test_fuzzy_match_ignores_trailing_space(pe, fm):
    response = _edit_response(
        "a.py",
        "def alpha():   \n    return 1   ",
        "def alpha():\n    return 42",
    )
    results = pe.execute(pe.parse_response(response)["ops"], apply_changes=True)
    assert results[0]["status"] == "applied"
    assert "return 42" in fm.read_raw("a.py")


def test_multi_occurrence_is_rejected(pe, fm):
    (fm.root / "dup.py").write_text("x = 1\nx = 1\n", encoding="utf-8")
    response = _edit_response("dup.py", "x = 1", "x = 2")
    results = pe.execute(pe.parse_response(response)["ops"], apply_changes=True)
    assert results[0]["status"] == "error"
    assert "occurrences" in results[0]["message"]
    assert (fm.root / "dup.py").read_text(encoding="utf-8") == "x = 1\nx = 1\n"


def test_crlf_is_preserved(pe, fm):
    target = fm.root / "crlf.py"
    target.write_bytes(b"def crlf():\r\n    return 1\r\n")
    response = _edit_response(
        "crlf.py",
        "def crlf():\n    return 1",
        "def crlf():\n    return 2",
    )
    results = pe.execute(pe.parse_response(response)["ops"], apply_changes=True)
    assert results[0]["status"] == "applied"
    raw = target.read_bytes()
    assert b"return 2" in raw
    assert b"\r\n" in raw
    assert b"\n" not in raw.replace(b"\r\n", b"")


def test_invalid_python_syntax_is_rejected(pe, fm):
    before = fm.read_raw("a.py")
    response = _edit_response(
        "a.py",
        "def alpha():\n    return 1",
        "def alpha(:\n    return 2",
    )
    results = pe.execute(pe.parse_response(response)["ops"], apply_changes=True)
    assert results[0]["status"] == "error"
    assert "SyntaxError" in results[0]["message"]
    assert fm.read_raw("a.py") == before


def test_invalid_json_syntax_is_rejected(pe, fm):
    before = fm.read_raw("data.json")
    response = _edit_response("data.json", '{"ok": true}', '{"ok": ')
    results = pe.execute(pe.parse_response(response)["ops"], apply_changes=True)
    assert results[0]["status"] == "error"
    assert "JSON" in results[0]["message"]
    assert fm.read_raw("data.json") == before


def test_ask_plan_and_status_parsing(pe):
    response = (
        "<<<ASK>>>\nWhich option?\n- Alpha\n- Beta\n<<<END>>>\n"
        "<<<PLAN>>>\n- [ ] First task\n  - [ ] Sub task\n- [x] Done task\n<<<END>>>\n"
        "<<<STATUS: NEED_MORE>>>"
    )
    parsed = pe.parse_response(response)
    assert parsed["questions"] == [
        {"question": "Which option?", "options": ["Alpha", "Beta"]}
    ]
    assert parsed["plan"][0]["text"] == "First task"
    assert parsed["plan"][0]["subtasks"] == [{"text": "Sub task", "done": False}]
    assert parsed["plan"][1] == {"text": "Done task", "done": True, "subtasks": []}
    assert parsed["status_flag"] == "NEED_MORE"


def test_windows_need_files_paths(pe):
    response = (
        "<<<NEED_FILES>>>\n"
        "C:\\work\\file.py\n"
        "C:\\work\\file.py:full\n"
        "C:\\my-dir\\file.py:10-20\n"
        "C:\\work\\file.py:outline\n"
        "src/app.py\n"
        "big.py:100-220\n"
        "<<<END>>>"
    )
    needs = pe.parse_response(response)["need_files"]
    assert needs == [
        {"path": "C:\\work\\file.py", "mode": "full"},
        {"path": "C:\\work\\file.py", "mode": "full"},
        {"path": "C:\\my-dir\\file.py", "mode": "range", "start": 10, "end": 20},
        {"path": "C:\\work\\file.py", "mode": "outline"},
        {"path": "src/app.py", "mode": "full"},
        {"path": "big.py", "mode": "range", "start": 100, "end": 220},
    ]


def test_dead_api_removed(pe):
    assert not hasattr(PatchEngine, "_strip_wrappers")
    params = list(inspect.signature(pe.parse_response).parameters)
    assert params == ["response_text"]


def test_execute_handles_malformed_ops(pe, fm):
    before = fm.read_raw("a.py")
    ops = [{"type": "edit"}, {"path": 123}, {"type": "bogus", "path": "a.py"}, "not-a-dict"]
    results = pe.execute(ops, apply_changes=True)
    assert len(results) == 4
    assert all(r["status"] == "error" for r in results)
    assert fm.read_raw("a.py") == before


def test_execute_rejects_op_without_path(pe, fm):
    results = pe.execute([{"type": "write", "content": "x"}], apply_changes=True)
    assert results[0]["status"] == "error"
    assert "path" in results[0]["message"].lower()
