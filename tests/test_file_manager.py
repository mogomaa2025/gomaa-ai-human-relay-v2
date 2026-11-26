import pytest

from core.file_manager import FileManager


def test_safe_path_rejects_traversal(fm):
    with pytest.raises(ValueError):
        fm.safe_path("../outside.txt")
    with pytest.raises(ValueError):
        fm.safe_path("/etc/passwd")
    assert fm.safe_path("a.py").is_file()


def test_scan_files_ignores_cache_and_git(fm):
    paths = [item["path"] for item in fm.scan_files()]
    assert "a.py" in paths
    assert "data.json" in paths
    assert not any(p.startswith(".git/") for p in paths)
    assert not any(p.startswith("__pycache__/") for p in paths)
    assert not any(p.endswith(".pyc") for p in paths)


def test_read_text_line_ranges(fm):
    text, total = fm.read_text("a.py", 2, 3)
    assert total == 4
    lines = text.splitlines()
    assert lines[0].startswith("2: ")
    assert lines[1].startswith("3: ")
    assert len(lines) == 2


def test_backup_and_undo_last(fm):
    original = fm.read_raw("a.py")
    backups = []
    fm.write_with_backup("a.py", "def alpha():\n    return 7\n", backups)
    fm.commit_undo_batch(backups)
    assert "return 7" in fm.read_raw("a.py")

    assert fm.undo_last() == 1
    assert fm.read_raw("a.py") == original
    assert fm.undo_last() == 0


def test_undo_removes_created_file(fm):
    backups = []
    fm.write_with_backup("created.py", "value = 1\n", backups)
    fm.commit_undo_batch(backups)
    assert (fm.root / "created.py").exists()

    assert fm.undo_last() == 1
    assert not (fm.root / "created.py").exists()


def test_smart_select_bounds_reads(fm, monkeypatch):
    reads = []
    original = FileManager._read_head

    def spy(self, rel_path, max_bytes):
        reads.append((rel_path, max_bytes))
        return original(self, rel_path, max_bytes)

    monkeypatch.setattr(FileManager, "_read_head", spy)
    (fm.root / "huge.py").write_text("needle\n" * 300_000, encoding="utf-8")
    assert (fm.root / "huge.py").stat().st_size > FileManager.MAX_SCORE_FILE_BYTES

    selected = fm.smart_select("needle")
    assert selected
    assert all(max_bytes <= FileManager.MAX_CONTENT_SCAN_BYTES for _, max_bytes in reads)
    assert not any(rel_path == "huge.py" for rel_path, _ in reads)


def test_set_root_validates_directory(fm, tmp_path):
    with pytest.raises(ValueError):
        fm.set_root(str(tmp_path / "does-not-exist"))

    target = tmp_path / "other"
    target.mkdir()
    assert fm.set_root(str(target)) == target.resolve()
    assert fm.root == target.resolve()


def test_safe_path_rejects_empty_and_root(fm):
    with pytest.raises(ValueError):
        fm.safe_path("")
    with pytest.raises(ValueError):
        fm.safe_path("   ")
    with pytest.raises(ValueError):
        fm.safe_path(".")


def test_safe_path_rejects_protected_dirs(fm):
    with pytest.raises(ValueError):
        fm.safe_path(".chatbridge_undo/x.json")
    with pytest.raises(ValueError):
        fm.safe_path(".git/config")
    # traversal into protected dir is also blocked
    with pytest.raises(ValueError):
        fm.safe_path("a/../.chatbridge_undo/x.json")


def test_safe_path_allows_symlink_inside_root(fm, tmp_path):
    src = fm.root / "inside.txt"
    src.write_text("hi", encoding="utf-8")
    link = fm.root / "link.txt"
    try:
        link.symlink_to(src)
    except OSError:
        pytest.skip("symlink creation not supported")
    resolved = fm.safe_path("link.txt")
    assert resolved == src.resolve()


def test_safe_path_rejects_symlink_escape(fm, tmp_path):
    outside = tmp_path.parent / "outside_escape.txt"
    outside.write_text("secret", encoding="utf-8")
    link = fm.root / "escape.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation not supported")
    with pytest.raises(ValueError):
        fm.safe_path("escape.txt")


def test_scan_files_respects_limit(fm):
    for i in range(5):
        (fm.root / f"f{i}.py").write_text(f"x = {i}\n", encoding="utf-8")
    items = fm.scan_files(limit=2)
    assert len(items) <= 2


def test_undo_last_rejects_tampered_batch(fm, tmp_path):
    backups = []
    fm.write_with_backup("a.py", "def alpha():\n    return 7\n", backups)
    fm.commit_undo_batch(backups)
    # tamper the batch to point outside root
    batch = next((fm.root / ".chatbridge_undo").glob("undo_*.json"))
    import json
    payload = json.loads(batch.read_text(encoding="utf-8"))
    payload[0]["path"] = str(tmp_path.parent / "outside_escape.txt")
    batch.write_text(json.dumps(payload), encoding="utf-8")
    assert fm.undo_last() == 0


def test_undo_last_rejects_malformed_json(fm):
    fm._undo_dir.mkdir(parents=True, exist_ok=True)
    (fm._undo_dir / "undo_1.json").write_text("{not json", encoding="utf-8")
    assert fm.undo_last() == 0


def test_undo_batch_filenames_are_unique(fm):
    b1 = []
    fm.write_with_backup("a.py", "def alpha():\n    return 1\n", b1)
    fm.commit_undo_batch(b1)
    b2 = []
    fm.write_with_backup("a.py", "def alpha():\n    return 2\n", b2)
    fm.commit_undo_batch(b2)
    names = list((fm.root / ".chatbridge_undo").glob("undo_*.json"))
    assert len(names) >= 2
    assert len(names) == len(set(names))


def test_binary_bytes_detection():
    from core.file_manager import FileManager
    assert not FileManager._is_binary_bytes(b"hello world")
    assert FileManager._is_binary_bytes(b"\x00\x01")
    assert FileManager._is_binary_bytes("caf\xc3\xa9".encode("latin-1") + b"\xff")
    assert not FileManager._is_binary_bytes("caf\xc3\xa9".encode("utf-8"))


def test_scan_files_skips_binary_content(fm):
    (fm.root / "bin.py").write_bytes(b"def x():\n    return 1\n\x00")
    paths = [i["path"] for i in fm.scan_files()]
    assert "bin.py" not in paths


def test_read_raw_rejects_binary(fm):
    (fm.root / "blob.txt").write_bytes(b"hello\x00world")
    with pytest.raises(ValueError):
        fm.read_raw("blob.txt")


def test_undo_list_and_batch(fm):
    backups = []
    fm.write_with_backup("a.py", "def alpha():\n    return 7\n", backups)
    fm.commit_undo_batch(backups)
    batches = fm.list_undo_batches()
    assert len(batches) == 1
    name = batches[0]["name"]
    assert fm.undo_batch(name) == 1
    assert fm.list_undo_batches() == []


def test_undo_batch_rejects_bad_name(fm):
    assert fm.undo_batch("../evil.json") == 0
    assert fm.undo_batch("nope.json") == 0


def test_file_manager_has_lock(fm):
    import threading
    assert type(fm._lock) is type(threading.Lock())


def test_read_text_splits_on_newline_only(fm):
    (fm.root / "uni.txt").write_text("a\u2028b\nc\n", encoding="utf-8")
    text, total = fm.read_text("uni.txt")
    assert total == 2
    ranged, _ = fm.read_text("uni.txt", 1, 1)
    assert ranged.startswith("1: a\u2028b")


def test_read_text_crlf_line_count(fm):
    (fm.root / "crlf2.txt").write_bytes(b"alpha\r\nbeta\r\n")
    text, total = fm.read_text("crlf2.txt")
    assert total == 2
    assert "alpha" in text and "beta" in text


def test_undo_last_self_heals_tampered_batch(fm, tmp_path):
    b1 = []
    fm.write_with_backup("a.py", "def alpha():\n    return 2\n", b1)
    fm.commit_undo_batch(b1)
    b2 = []
    fm.write_with_backup("a.py", "def alpha():\n    return 3\n", b2)
    fm.commit_undo_batch(b2)

    batch = sorted((fm.root / ".chatbridge_undo").glob("undo_*.json"))[-1]
    import json
    payload = json.loads(batch.read_text(encoding="utf-8"))
    payload[0]["path"] = str(tmp_path.parent / "outside_escape.txt")
    batch.write_text(json.dumps(payload), encoding="utf-8")

    assert fm.undo_last() == 0
    assert not batch.exists()
    assert fm.undo_last() == 1


def test_is_contained_path_resolves_relative_against_root(fm):
    from pathlib import Path
    assert fm._is_contained_path(Path("a.py"))
    assert not fm._is_contained_path(Path("../outside.txt"))
