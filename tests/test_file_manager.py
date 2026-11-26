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
