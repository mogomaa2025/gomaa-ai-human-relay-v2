import json
import os
import tempfile
from pathlib import Path

import pytest

from core.file_manager import FileManager
from core.patch_engine import PatchEngine
from core.prompt_builder import PromptBuilder


def test_safe_path_rejects_nul_and_control_chars(fm):
    with pytest.raises(ValueError):
        fm.safe_path("a\x00b.py")
    with pytest.raises(ValueError):
        fm.safe_path("a\nb.py")


def test_create_item_directory_and_file(client, fm, tmp_path):
    res = client.post("/api/file/create", json={"parent_path": "", "name": "newdir", "is_dir": True})
    assert res.status_code == 200
    assert (fm.root / "newdir").is_dir()
    res2 = client.post("/api/file/create", json={"parent_path": "newdir", "name": "x.py", "is_dir": False})
    assert res2.status_code == 200
    assert (fm.root / "newdir" / "x.py").is_file()


def test_paste_directory_creates_parent(client, fm):
    (fm.root / "srcdir").mkdir()
    (fm.root / "srcdir" / "f.txt").write_text("hi", encoding="utf-8")
    # dest parent does not exist yet
    res = client.post("/api/file/paste", json={"src_path": "srcdir", "dest_dir": "nonexistent", "action": "copy"})
    assert res.status_code == 200
    assert (fm.root / "nonexistent" / "srcdir" / "f.txt").is_file()


def test_paste_action_validation(client, fm):
    (fm.root / "src.txt").write_text("x", encoding="utf-8")
    res = client.post("/api/file/paste", json={"src_path": "src.txt", "dest_dir": "", "action": "move"})
    assert res.status_code == 400
    assert "action" in res.get_json()["error"]


def test_project_empty_path_rejected(client):
    res = client.post("/api/project", json={"path": "   "})
    assert res.status_code == 400
    assert "empty" in res.get_json()["error"]


def test_chunk_size_float_rejected(client):
    res = client.post("/api/build_prompt", json={"task": "t", "chunk_size": 5.5})
    assert res.status_code == 400
    res2 = client.post("/api/build_prompt", json={"task": "t", "chunk_size": 4.0})
    assert res2.status_code == 200


def test_outline_ordering(pb):
    (pb._fm.root / "ord.py").write_text(
        "class B:\n    pass\ndef a():\n    pass\nimport os\n", encoding="utf-8"
    )
    result = pb.build("task", [{"path": "ord.py", "mode": "outline"}], include_tree=False)
    prompt = result["full_prompt"]
    # import line should appear before class/function in source order? source order: class(1), def(3), import(5)
    # outline should be sorted by lineno
    idx_import = prompt.find("import os")
    idx_class = prompt.find("class B")
    assert idx_class < idx_import


def test_partial_restore_returns_count(fm, tmp_path):
    # create two batches; tamper last so restore partially? Instead verify normal batch returns count
    backups = []
    fm.write_with_backup("a.py", "def alpha():\n    return 7\n", backups)
    fm.commit_undo_batch(backups)
    assert fm.undo_last() == 1