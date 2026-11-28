import json
import os
from pathlib import Path

import pytest

from core.patch_engine import PatchEngine


def test_paste_preserves_symlink(client, fm, tmp_path):
    src = fm.root / "linksrc"
    target = fm.root / "a.py"
    try:
        src.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation not supported")
    (fm.root / "sub").mkdir(exist_ok=True)

    res = client.post(
        "/api/file/paste",
        json={"src_path": "linksrc", "dest_dir": "sub", "action": "copy"},
    )
    assert res.status_code == 200
    dst = fm.root / "sub" / "linksrc"
    assert dst.is_symlink()
    assert os.readlink(dst) == os.readlink(src)
    assert src.is_symlink()


def test_rename_into_own_subtree_rejected(client, fm):
    (fm.root / "parent").mkdir()
    (fm.root / "parent" / "child").mkdir()

    res = client.post(
        "/api/file/rename",
        json={"old_path": "parent", "new_path": "parent/child/grand"},
    )
    assert res.status_code == 400
    assert "subdirectory" in res.get_json()["error"].lower() or "itself" in res.get_json()["error"].lower()
    assert (fm.root / "parent").is_dir()


def test_parser_tolerates_missing_newline_before_end(pe, fm):
    response = (
        '<<<EDIT path="a.py">>>\n'
        "<<<OLD>>>\n"
        "def alpha():\n    return 1\n"
        "<<<NEW>>>\n"
        "def alpha():\n    return 77\n"
        "<<<END>>>"
    )
    # remove the newline immediately before <<<END>>> to simulate compact reply
    response = response.replace("    return 77\n<<<END>>>", "    return 77<<<END>>>")
    parsed = pe.parse_response(response)
    assert len(parsed["ops"]) == 1
    results = pe.execute(parsed["ops"], apply_changes=True)
    assert results[0]["status"] == "applied"
    assert "return 77" in fm.read_raw("a.py")


def test_gitignore_negation_and_anchored(fm):
    (fm.root / ".gitignore").write_text("*.log\n!important.log\n/rootonly.txt\n", encoding="utf-8")
    patterns = fm._load_ignore_patterns()
    assert fm._is_ignored("debug.log", "debug.log", patterns) is True
    assert fm._is_ignored("important.log", "important.log", patterns) is False
    assert fm._is_ignored("rootonly.txt", "rootonly.txt", patterns) is True
    assert fm._is_ignored("sub/rootonly.txt", "rootonly.txt", patterns) is False


def test_applied_log_capped_to_last_20(pb):
    logs = [f"step {i}" for i in range(25)]
    result = pb.build("task", [], include_tree=False, applied_log=logs)
    prompt = result["full_prompt"]
    assert "step 24" in prompt
    assert "step 5" in prompt
    assert "step 0" not in prompt
    assert "step 4" not in prompt


def test_read_raw_bound_at_5mb(fm):
    big = fm.root / "huge_read.txt"
    big.write_bytes(b"x" * (fm.MAX_READ_BYTES + 10))
    with pytest.raises(ValueError):
        fm.read_raw("huge_read.txt")


def test_undo_symlink_target_validation(fm, tmp_path):
    outside = tmp_path.parent / "outside_target.txt"
    outside.write_text("secret", encoding="utf-8")
    fm._undo_dir.mkdir(parents=True, exist_ok=True)
    batch = fm._undo_dir / "undo_symlink.json"
    link_path = fm.root / "link"
    payload = [{"kind": "symlink", "path": str(link_path), "target": str(outside)}]
    fm._atomic_write(batch, json.dumps(payload))
    assert fm.undo_last() == 0
    assert not batch.exists()


def test_file_kind_restore_removes_directory(fm):
    d = fm.root / "created_dir"
    d.mkdir()
    fm._undo_dir.mkdir(parents=True, exist_ok=True)
    batch = fm._undo_dir / "undo_filekind.json"
    payload = [{"kind": "file", "path": str(d), "existed": False, "content": ""}]
    fm._atomic_write(batch, json.dumps(payload))
    assert fm.undo_last() == 1
    assert not d.exists()


def test_browse_failure_returns_json(client, monkeypatch):
    def boom(path=""):
        raise ValueError("boom")
    monkeypatch.setattr("main.file_manager.browse_dirs", boom)
    res = client.get("/api/browse?path=/tmp")
    assert res.status_code == 400
    assert res.get_json()["error"] == "boom"


def test_smart_select_failure_returns_json(client, monkeypatch):
    def boom(task):
        raise ValueError("boom2")
    monkeypatch.setattr("main.file_manager.smart_select", boom)
    res = client.post("/api/smart_select", json={"task": "x"})
    assert res.status_code == 400
    assert res.get_json()["error"] == "boom2"
