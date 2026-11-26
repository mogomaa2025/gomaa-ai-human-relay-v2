from core.modes import MODES
from core.prompt_builder import PromptBuilder


def test_default_mode_is_gemini(pb):
    result = pb.build("do a thing", [])
    assert result["mode"] == "gemini"
    assert result["max_input_chars"] == 1_000_000


def test_mode_limits_are_returned(pb):
    for name, cfg in MODES.items():
        result = pb.build("task", [], mode=name)
        assert result["mode"] == name
        assert result["max_input_chars"] == cfg["max_input_chars"]
        assert result["output_format"] == cfg["output_format"]


def test_unknown_mode_falls_back_to_gemini(pb):
    result = pb.build("task", [], mode="does-not-exist")
    assert result["mode"] == "gemini"


def test_copilot_parts_fit_under_28000(pb):
    result = pb.build(
        "refactor everything",
        [{"path": "big.py", "mode": "full"}],
        include_tree=False,
        mode="copilot",
    )
    assert len(result["parts"]) > 1
    for part in result["parts"]:
        assert len(part) <= 28_000


def test_json_parts_shape(pb):
    result = pb.build(
        "refactor everything",
        [{"path": "big.py", "mode": "full"}],
        include_tree=False,
        mode="copilot",
    )
    parts = result["json_parts"]
    assert len(parts) == len(result["parts"])
    for idx, obj in enumerate(parts):
        assert set(obj) == {"mode", "part", "total_parts", "prompt"}
        assert obj["mode"] == "copilot"
        assert obj["part"] == idx + 1
        assert obj["total_parts"] == len(parts)
        assert len(obj["prompt"]) <= 28_000


def test_over_limit_flag(pb):
    result = pb.build("x" * 100_001, [], include_tree=False, mode="chatgpt")
    assert result["chars"] > 100_000
    assert result["over_limit"] is True


def test_not_over_limit_for_small_prompt(pb):
    result = pb.build("small task", [], include_tree=False, mode="copilot")
    assert result["over_limit"] is False


def test_split_on_lines_preserves_content():
    text = "".join(f"line {i} ....................\n" for i in range(100))
    chunks = PromptBuilder._split_on_lines(text, 200)
    assert "".join(chunks) == text
    for chunk in chunks[:-1]:
        assert chunk.endswith("\n")


def test_no_orphan_file_header():
    text = "A" * 90 + "\n=== FILE: x.py ===\n" + "B" * 50
    chunks = PromptBuilder._split_on_lines(text, 100)
    for chunk in chunks:
        assert not chunk.endswith("=== FILE: x.py ===\n")
    assert "=== FILE: x.py ===\n" in "".join(chunks)


def test_overlong_single_line_is_hard_split():
    text = "x" * 250
    chunks = PromptBuilder._split_on_lines(text, 100)
    assert "".join(chunks) == text
    assert all(len(c) <= 100 for c in chunks)


def test_part_headers_wait_and_final(pb):
    result = pb.build(
        "refactor",
        [{"path": "big.py", "mode": "full"}],
        include_tree=False,
        chunk_size=2000,
        mode="gemini",
    )
    assert len(result["parts"]) > 1
    assert result["parts"][0].startswith("[PART 1/")
    assert "Wait for remaining parts" in result["parts"][0]
    assert "All parts sent. Reply now" in result["parts"][-1]


def test_single_part_has_no_header(pb):
    result = pb.build("tiny task", [], include_tree=False, mode="gemini")
    assert len(result["parts"]) == 1
    assert not result["parts"][0].startswith("[PART")


def test_file_and_outline_headers(pb):
    full = pb.build("task", [{"path": "a.py", "mode": "full"}], include_tree=False)
    assert "=== FILE: a.py ===" in full["full_prompt"]
    outline = pb.build("task", [{"path": "a.py", "mode": "outline"}], include_tree=False)
    assert "=== OUTLINE: a.py" in outline["full_prompt"]


def test_copilot_adds_json_mode_note(pb):
    result = pb.build("task", [], include_tree=False, mode="copilot")
    assert "MODE NOTE (Copilot)" in result["full_prompt"]


def test_build_handles_invalid_file_specs(pb):
    result = pb.build("task", [None, {"foo": "bar"}, {"path": "a.py", "start": "abc", "end": 2}], include_tree=False)
    assert "=== FILE: a.py ===" in result["full_prompt"] or "=== FILE: a.py (lines" in result["full_prompt"]


def test_build_handles_non_list_inputs(pb):
    result = pb.build("task", None, include_tree=False, answers="x", applied_log=5, mode="nope")
    assert result["mode"] == "gemini"
