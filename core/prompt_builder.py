import ast
import re
from core.file_manager import FileManager
from core.modes import DEFAULT_MODE, MODES, get_mode


class PromptBuilder:
    """Builds token-efficient, goal-driven prompts with outline/range extraction and chunking."""

    FORMAT_RULES = (
        "OUTPUT FORMAT (REQUIRED — wrap entire reply in ONE ```text block):\n"
        "1) To request more files/line ranges before editing:\n"
        "<<<NEED_FILES>>>\n"
        "relative/path.py:full\n"
        "big_file.py:100-220\n"
        "<<<END>>>\n\n"
        "2) Optional: include a concise implementation plan with sub-tasks:\n"
        "<<<PLAN>>>\n"
        "- [ ] Main task 1\n"
        "  - [ ] Sub-task 1a\n"
        "- [ ] Main task 2\n"
        "<<<END>>>\n\n"
        "3) To ask the user a clarifying question with clickable options:\n"
        "<<<ASK>>>\n"
        "Your question here?\n"
        "- Option A\n"
        "- Option B\n"
        "<<<END>>>\n\n"
        "4) To edit or write files (exact, case-sensitive matches):\n"
        '<<<EDIT path="relative/path.ext">>>\n'
        "<<<OLD>>>\n"
        "exact lines to replace\n"
        "<<<NEW>>>\n"
        "replacement lines\n"
        "<<<END>>>\n\n"
        '<<<WRITE path="relative/path.ext">>>\n'
        "full file content\n"
        "<<<END>>>\n\n"
        "5) Always end your reply with ONE status tag:\n"
        "<<<STATUS: NEED_MORE>>>   (if you asked questions or requested files)\n"
        "<<<STATUS: APPLIED_NEXT>>> (if edits were provided and more steps remain)\n"
        "<<<STATUS: DONE>>>        (if the goal is completely finished)"
    )

    MODE_NOTE_JSON = (
        "\n\nMODE NOTE (Copilot): This prompt is delivered as ordered JSON parts. "
        "Process the parts in order and wait for the final part before replying."
    )

    SIG_RE = re.compile(
        r"^\s*(?:export\s+)?(?:async\s+)?(?:def|class|function|interface|type|const\s+\w+\s*=\s*(?:async\s*)?\()"
    )

    def __init__(self, file_manager: FileManager) -> None:
        self._fm = file_manager

    def _extract_outline(self, rel_path: str, content: str) -> str:
        """Extracts structural outline (imports, classes, functions) to save 70-90% tokens."""
        if rel_path.endswith(".py"):
            try:
                tree = ast.parse(content)
                lines = self._fm._split_lines(content)
                collected = []
                for node in ast.walk(tree):
                    if isinstance(node, (ast.Import, ast.ImportFrom)):
                        collected.append((node.lineno, f"L{node.lineno}: {lines[node.lineno - 1]}"))
                    elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                        collected.append((node.lineno, f"L{node.lineno}: {lines[node.lineno - 1]}"))
                collected.sort(key=lambda x: x[0])
                out = [text for _, text in collected]
                if out:
                    return "\\n".join(dict.fromkeys(out))
            except SyntaxError:
                pass

        out = [
            f"L{i}: {line.rstrip()}"
            for i, line in enumerate(self._fm._split_lines(content), 1)
            if self.SIG_RE.match(line)
        ]
        if out:
            return "\n".join(out)
        head = self._fm._split_lines(content)[:40]
        return "\n".join(f"L{i}: {l}" for i, l in enumerate(head, 1)) + "\n... (outline truncated)"

    @staticmethod
    def _split_on_lines(text: str, chunk_size: int) -> list[str]:
        """Split text on line boundaries, keeping section headers intact.

        A part never ends in the middle of a line. A ``=== FILE:`` / ``=== OUTLINE:``
        header is never left orphaned at the end of a part. As a last-resort guard a
        single line longer than ``chunk_size`` is hard-split.
        """
        chunk_size = max(1, chunk_size)
        chunks: list[str] = []
        current = ""
        pending_header = ""
        if text == "":
            line_items = []
        else:
            _raw = text.split("\n")
            line_items = [seg + "\n" for seg in _raw[:-1]]
            if _raw[-1] != "":
                line_items.append(_raw[-1])
        for line in line_items:
            # Move an orphaned header from the end of the previous chunk forward.
            if pending_header:
                line = pending_header + line
                pending_header = ""

            if current and len(current) + len(line) > chunk_size:
                if line.startswith(("=== FILE:", "=== OUTLINE:")):
                    pending_header = line
                    chunks.append(current)
                    current = ""
                    continue
                chunks.append(current)
                current = ""

            if not current and len(line) > chunk_size:
                # Single overlong line: hard-split so we always make progress.
                for i in range(0, len(line), chunk_size):
                    chunks.append(line[i:i + chunk_size])
                continue

            current += line

        if pending_header:
            current += pending_header
        if current:
            chunks.append(current)
        return chunks or [""]

    def build(
        self,
        task: str,
        file_specs: list[dict],
        include_tree: bool = True,
        chunk_size: int | None = None,
        goal_step: int = 1,
        answers: list[dict] | None = None,
        applied_log: list[str] | None = None,
        mode: str = DEFAULT_MODE,
    ) -> dict:
        mode_key = (mode or "").strip().lower() if isinstance(mode, str) else ""
        if mode_key not in MODES:
            mode_key = DEFAULT_MODE
        cfg = get_mode(mode_key)

        if not isinstance(task, str):
            task = "" if task is None else str(task)
        if not isinstance(file_specs, list):
            file_specs = []
        if not isinstance(answers, list):
            answers = []
        else:
            answers = [a for a in answers if isinstance(a, dict)]
        if not isinstance(applied_log, list):
            applied_log = []
        else:
            applied_log = [str(item) for item in applied_log]

        if chunk_size is None:
            effective_chunk = cfg["chunk_chars"]
        else:
            effective_chunk = min(int(chunk_size), cfg["chunk_chars"], cfg["max_input_chars"])
        effective_chunk = max(1000, effective_chunk)

        sections: list[str] = []

        if include_tree:
            tree_limit = getattr(self._fm, "TREE_LIMIT", 250)
            scanned_items = self._fm.scan_files(limit=tree_limit)
            tree_entries = [f"{item['path']} ({item.get('lines', 0)} lines)" for item in scanned_items]
            truncation_note = ""
            if len(scanned_items) >= tree_limit:
                truncation_note = f"\n... (tree truncated to first {tree_limit} files; use scan with include_ignored if needed)"
            sections.append(
                "TREE (with exact line counts):\n" +
                "\n".join(tree_entries) +
                truncation_note +
                "\n\nINSTRUCTION NOTICE: Be token-efficient. Do NOT request full files unless absolutely necessary. Inspect the line counts above and request specific line ranges (e.g., core/prompt_builder.py:50-150) using <<<NEED_FILES>>> when examining or editing large files."
            )

        def _to_int(value):
            if value in (None, ""):
                return None
            if isinstance(value, bool):
                return None
            try:
                return int(value)
            except (TypeError, ValueError):
                return None

        for spec in file_specs:
            if not isinstance(spec, dict):
                sections.append("=== FILE: (invalid spec) ===")
                continue
            rel = spec.get("path")
            if not isinstance(rel, str) or not rel.strip():
                sections.append("=== FILE: (missing path) ===")
                continue
            spec_mode = spec.get("mode", "full")
            if not isinstance(spec_mode, str):
                spec_mode = "full"
            start = _to_int(spec.get("start"))
            end = _to_int(spec.get("end"))
            try:
                if spec_mode == "range" and (start or end):
                    text, total = self._fm.read_text(rel, start, end)
                    sections.append(f"=== FILE: {rel} (lines {start or 1}-{end or total} of {total}) ===\n{text}")
                elif spec_mode == "outline":
                    raw, total = self._fm.read_text(rel)
                    outline = self._extract_outline(rel, raw)
                    sections.append(f"=== OUTLINE: {rel} ({total} lines) ===\n{outline}")
                else:
                    raw, _ = self._fm.read_text(rel)
                    sections.append(f"=== FILE: {rel} ===\n{raw}")
            except Exception as e:
                sections.append(f"=== FILE: {rel} (error: {e}) ===")

        goal_header = f"GOAL (Step {goal_step}):\n{task.strip()}"
        if applied_log:
            goal_header += "\n\nPREVIOUSLY APPLIED:\n" + "\n".join(f"- {item}" for item in applied_log)
        if answers:
            ans_lines = [f"Q: {a['question']}\nA: {a['answer']}" for a in answers if a.get("answer")]
            if ans_lines:
                goal_header += "\n\nUSER ANSWERS:\n" + "\n".join(ans_lines)

        format_rules = self.FORMAT_RULES
        if cfg["output_format"] == "json_parts":
            format_rules += self.MODE_NOTE_JSON

        sections.append(f"{goal_header}\n\n{format_rules}")
        full_prompt = "\n\n".join(sections)

        chunks = self._split_on_lines(full_prompt, effective_chunk)
        if len(chunks) > 1:
            # Reserve room so the prepended "[PART i/n ...]" header still fits
            # within the mode's chunk budget.
            chunks = self._split_on_lines(full_prompt, max(1, effective_chunk - 128))
        if len(chunks) == 1:
            parts = chunks
        else:
            total_parts = len(chunks)
            parts = [
                f"[PART {idx + 1}/{total_parts} — Wait for remaining parts before replying]\n\n{chunk}"
                if idx < total_parts - 1
                else f"[PART {idx + 1}/{total_parts} — All parts sent. Reply now]\n\n{chunk}"
                for idx, chunk in enumerate(chunks)
            ]

        json_parts = [
            {"mode": mode_key, "part": idx + 1, "total_parts": len(parts), "prompt": part}
            for idx, part in enumerate(parts)
        ]

        return {
            "full_prompt": full_prompt,
            "parts": parts,
            "json_parts": json_parts,
            "chars": len(full_prompt),
            "est_tokens": max(1, len(full_prompt) // 4),
            "mode": mode_key,
            "max_input_chars": cfg["max_input_chars"],
            "output_format": cfg["output_format"],
            "over_limit": len(full_prompt) > cfg["max_input_chars"],
        }
