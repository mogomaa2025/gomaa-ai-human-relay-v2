def test_modes_endpoint_lists_all(client, modes):
    data = client.get("/api/modes").get_json()
    assert data["default"] == "gemini"
    assert set(data["modes"]) == set(modes)
    assert len(data["modes"]) == 4


def test_build_prompt_echoes_copilot_mode(client):
    res = client.post(
        "/api/build_prompt",
        json={"task": "refactor", "files": [], "mode": "copilot"},
    )
    assert res.status_code == 200
    data = res.get_json()
    assert data["mode"] == "copilot"
    assert data["max_input_chars"] == 30_000
    assert data["output_format"] == "json_parts"
    assert len(data["json_parts"]) == len(data["parts"])


def test_build_prompt_unknown_mode_falls_back(client):
    res = client.post(
        "/api/build_prompt",
        json={"task": "refactor", "files": [], "mode": "nope"},
    )
    assert res.get_json()["mode"] == "gemini"


def test_save_rejects_invalid_python(client, fm):
    before = fm.read_raw("a.py")
    res = client.post(
        "/api/file",
        json={"path": "a.py", "content": "def broken(:\n    pass\n"},
    )
    assert res.status_code == 400
    assert "SyntaxError" in res.get_json()["error"]
    assert fm.read_raw("a.py") == before


def test_save_rejects_invalid_json(client, fm):
    before = fm.read_raw("data.json")
    res = client.post(
        "/api/file",
        json={"path": "data.json", "content": '{"ok": '},
    )
    assert res.status_code == 400
    assert "JSON" in res.get_json()["error"]
    assert fm.read_raw("data.json") == before


def test_save_valid_file_then_undo(client, fm):
    original = fm.read_raw("a.py")
    res = client.post(
        "/api/file",
        json={"path": "a.py", "content": "def alpha():\n    return 99\n"},
    )
    assert res.status_code == 200
    assert "return 99" in fm.read_raw("a.py")

    undo = client.post("/api/undo")
    assert undo.status_code == 200
    assert fm.read_raw("a.py") == original


def test_undo_with_nothing_to_restore(client):
    res = client.post("/api/undo")
    assert res.status_code == 400
    assert res.get_json()["error"] == "Nothing to undo."
