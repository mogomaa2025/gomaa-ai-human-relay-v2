import ast
import re
from core.file_manager import FileManager


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
                lines = content.splitlines()
                out = []
                for node in ast.walk(tree):
                    if isinstance(node, (ast.Import, ast.ImportFrom)):
                        out.append(f"L{node.lineno}: {lines[node.lineno - 1]}")
                    elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                        out.append(f"L{node.lineno}: {lines[node.lineno - 1]}")
                if out:
                    return "\n".join(dict.fromkeys(out))
            except SyntaxError:
                pass

        out = [
            f"L{i}: {line.rstrip()}"
            for i, line in enumerate(content.splitlines(), 1)
            if self.SIG_RE.match(line)
        ]
        if out:
            return "\n".join(out)
        head = content.splitlines()[:40]
        return "\n".join(f"L{i}: {l}" for i, l in enumerate(head, 1)) + "\n... (outline truncated)"

    def build(
        self,
        task: str,
        file_specs: list[dict],
        include_tree: bool = True,
        chunk_size: int = 120000,
        goal_step: int = 1,
        answers: list[dict] | None = None,
        applied_log: list[str] | None = None,
    ) -> dict:
        sections: list[str] = []

        if include_tree:
            scanned_items = self._fm.scan_files(limit=250)
            tree_entries = [f"{item['path']} ({item.get('lines', 0)} lines)" for item in scanned_items]
            sections.append(
                "TREE (with exact line counts):\n" +
                "\n".join(tree_entries) +
                "\n\nINSTRUCTION NOTICE: Be token-efficient. Do NOT request full files unless absolutely necessary. Inspect the line counts above and request specific line ranges (e.g., core/prompt_builder.py:50-150) using <<<NEED_FILES>>> when examining or editing large files."
            )

        for spec in file_specs:
            rel = spec["path"]
            mode = spec.get("mode", "full")
            start = spec.get("start")
            end = spec.get("end")
            try:
                if mode == "range" and (start or end):
                    text, total = self._fm.read_text(rel, start, end)
                    sections.append(f"=== FILE: {rel} (lines {start or 1}-{end or total} of {total}) ===\n{text}")
                elif mode == "outline":
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

        sections.append(f"{goal_header}\n\n{self.FORMAT_RULES}")
        full_prompt = "\n\n".join(sections)

        if len(full_prompt) <= chunk_size:
            parts = [full_prompt]
        else:
            chunks = [full_prompt[i:i + chunk_size] for i in range(0, len(full_prompt), chunk_size)]
            total_parts = len(chunks)
            parts = [
                f"[PART {idx + 1}/{total_parts} — Wait for remaining parts before replying]\n\n{chunk}"
                if idx < total_parts - 1
                else f"[PART {idx + 1}/{total_parts} — All parts sent. Reply now]\n\n{chunk}"
                for idx, chunk in enumerate(chunks)
            ]

        return {
            "full_prompt": full_prompt,
            "parts": parts,
            "chars": len(full_prompt),
            "est_tokens": max(1, len(full_prompt) // 4),
        }