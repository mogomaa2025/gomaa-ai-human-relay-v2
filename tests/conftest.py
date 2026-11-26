import json
import os

import pytest

from core.file_manager import FileManager
from core.modes import MODES
from core.patch_engine import PatchEngine
from core.prompt_builder import PromptBuilder


@pytest.fixture
def project(tmp_path):
    (tmp_path / "a.py").write_text("def alpha():\n    return 1\n\n# needle\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("import a\n", encoding="utf-8")
    (tmp_path / "data.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    (tmp_path / "notes.txt").write_text("hello needle world\n", encoding="utf-8")
    (tmp_path / "big.py").write_text("".join(f"line_{i} = {i}\n" for i in range(2000)), encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "junk.pyc").write_text("x", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("x", encoding="utf-8")
    return tmp_path


@pytest.fixture
def fm(project):
    return FileManager(project)


@pytest.fixture
def pb(fm):
    return PromptBuilder(fm)


@pytest.fixture
def pe(fm):
    return PatchEngine(fm)


@pytest.fixture
def client(project, fm):
    import main

    main.file_manager.set_root(str(project))
    main.app.config["TESTING"] = True
    return main.app.test_client()


@pytest.fixture
def modes():
    return MODES


@pytest.fixture
def os_environ():
    return os.environ
