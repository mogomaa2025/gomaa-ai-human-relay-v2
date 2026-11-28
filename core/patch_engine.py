import difflib
import re
from core.file_manager import BackupEntry, FileManager
from core.validators import validate_syntax


class PatchEngine:
    """Parses edits, file requests, interactive questions, and goal status from AI responses."""

    EDIT_RE = re.compile(
        r'<<<EDIT\s+path=["\']([^"\']+)["\']\s*>>>\s*\r?\n<<<OLD>>>\r?\n(.*?)\r?\n<<<NEW>>>\r?\n(.*?)(?:\r?\n)?<<<END>>>',
        re.DOTALL,
    )
    WRITE_RE = re.compile(
        r'<<<WRITE\s+path=["\']([^"\']+)["\']\s*>>>\s*\r?\n(.*?)(?:\r?\n)?<<<END>>>',
        re.DOTALL,
    )
    NEED_RE = re.compile(r"<<<NEED_FILES>>>\r?\n(.*?)(?:\r?\n)?<<<END>>>", re.DOTALL)
    ASK_RE = re.compile(r"<<<ASK>>>\r?\n(.*?)(?:\r?\n)?<<<END>>>", re.DOTALL)
    PLAN_RE = re.compile(r"<<<PLAN>>>\r?\n(.*?)(?:\r?\n)?<<<END>>>", re.DOTALL)
    STATUS_RE = re.compile(r"<<<STATUS:\s*(NEED_MORE|APPLIED_NEXT|DONE)\s*>>>")
    NEED_SPEC_RE = re.compile(r"^(?:(\d+)-(\d+)|(full|outline))$", re.IGNORECASE)

    def __init__(self, file_manager: FileManager) -> None:
        self._fm = file_manager

    @classmethod
    def _parse_need_line(cls, raw_line: str) -> dict:
        """Parse one NEED_FILES line.

        Supports ``path``, ``path:full``, ``path:outline`` and ``path:10-20``.
        Windows paths (``C:\\dir\\file.py``) and directories containing hyphens are
        handled by only treating the trailing segment as a spec when it matches a
        known spec pattern; otherwise the whole line is the path.
        """
        line = raw_line.strip()
        if ":" in line:
            head, tail = line.rsplit(":", 1)
            m = cls.NEED_SPEC_RE.match(tail.strip())
            if m:
                path = head.strip()
                start, end, mode = m.group(1), m.group(2), m.group(3)
                if mode:
                    return {"path": path, "mode": mode.lower()}
                return {
                    "path": path,
                    "mode": "range",
                    "start": int(start),
                    "end": int(end),
                }
        return {"path": line, "mode": "full"}

    def parse_response(self, response_text: str) -> dict:
        ops: list[dict] = []
        for m in self.EDIT_RE.finditer(response_text):
            ops.append({"type": "edit", "path": m.group(1), "old": m.group(2), "new": m.group(3)})
        for m in self.WRITE_RE.finditer(response_text):
            ops.append({"type": "write", "path": m.group(1), "content": m.group(2)})

        need_files: list[dict] = []
        for m in self.NEED_RE.finditer(response_text):
            for raw_line in FileManager._split_lines(m.group(1)):
                if not raw_line.strip():
                    continue
                need_files.append(self._parse_need_line(raw_line))

        questions: list[dict] = []
        for m in self.ASK_RE.finditer(response_text):
            lines = [l.strip() for l in FileManager._split_lines(m.group(1)) if l.strip()]
            if not lines:
                continue
            q_text = lines[0]
            opts = [l[1:].strip() for l in lines[1:] if l.startswith("-")]
            questions.append({"question": q_text, "options": opts})

        plan: list[dict] = []
        for m in self.PLAN_RE.finditer(response_text):
            for raw_line in FileManager._split_lines(m.group(1)):
                if not raw_line.strip():
                    continue
                indent = len(raw_line) - len(raw_line.lstrip(" \t"))
                clean = raw_line.strip().lstrip("-*•").strip()
                done = False
                if clean.startswith("[x]") or clean.startswith("[X]"):
                    done = True
                    clean = clean[3:].strip()
                elif clean.startswith("[ ]") or clean.startswith("[]"):
                    clean = clean.split("]", 1)[1].strip()
                if indent >= 2 and plan:
                    plan[-1]["subtasks"].append({"text": clean, "done": done})
                else:
                    plan.append({"text": clean, "done": done, "subtasks": []})

        status_match = self.STATUS_RE.search(response_text)
        status_flag = status_match.group(1) if status_match else ""

        return {
            "ops": ops,
            "need_files": need_files,
            "questions": questions,
            "plan": plan,
            "status_flag": status_flag,
        }

    @staticmethod
    def _validate_syntax(rel_path: str, content: str) -> str | None:
        return validate_syntax(rel_path, content)

    @staticmethod
    def _preserve_newlines(new_text: str, use_crlf: bool) -> str:
        normalized = new_text.replace("\r\n", "\n")
        return normalized.replace("\n", "\r\n") if use_crlf else normalized

    @staticmethod
    def _build_diff_lines(old_text: str, new_text: str, rel_path: str) -> list[dict]:
        raw_diff = difflib.unified_diff(
            FileManager._split_lines(old_text),
            FileManager._split_lines(new_text),
            fromfile=f"a/{rel_path}",
            tofile=f"b/{rel_path}",
            lineterm="",
        )
        structured = []
        for line in raw_diff:
            if line.startswith("+++") or line.startswith("---"):
                kind = "meta"
            elif line.startswith("@@"):
                kind = "hunk"
            elif line.startswith("+"):
                kind = "add"
            elif line.startswith("-"):
                kind = "del"
            else:
                kind = "ctx"
            structured.append({"kind": kind, "text": line})
        return structured

    @staticmethod
    def _fuzzy_line_replace(norm_full: str, norm_old: str, norm_new: str) -> str | None:
        """Fallback matcher ignoring trailing spaces or non-breaking spaces when exact match fails."""
        full_lines = FileManager._split_lines(norm_full)
        old_lines = [l.replace("\u00a0", " ").rstrip() for l in FileManager._split_lines(norm_old)]
        if not old_lines:
            return None
        n = len(old_lines)
        matches = []
        for i in range(len(full_lines) - n + 1):
            window = [l.replace("\u00a0", " ").rstrip() for l in full_lines[i:i + n]]
            if window == old_lines:
                matches.append(i)
        if len(matches) == 1:
            idx = matches[0]
            merged = full_lines[:idx] + FileManager._split_lines(norm_new) + full_lines[idx + n:]
            trailing = "\n" if norm_full.endswith("\n") else ""
            return "\n".join(merged) + trailing
        return None

    def execute(self, ops: list[dict], apply_changes: bool = False) -> list[dict]:
        results: list[dict] = []
        backups: list[BackupEntry] = []

        try:
            for op in ops:
                if not isinstance(op, dict):
                    results.append({"path": "-", "status": "error", "message": "Invalid operation (not an object)."})
                    continue
                rel = op.get("path")
                if not isinstance(rel, str) or not rel.strip():
                    results.append({"path": str(rel) if rel is not None else "-", "status": "error", "message": "Operation is missing a valid path."})
                    continue
                op_type = op.get("type")
                if op_type not in ("edit", "write"):
                    results.append({"path": rel, "status": "error", "message": f"Unknown operation type: {op_type!r}."})
                    continue
                try:
                    if op_type == "edit":
                        old_full = self._fm.read_raw(rel)
                        use_crlf = "\r\n" in old_full
                        norm_full = old_full.replace("\r\n", "\n")
                        norm_old = op["old"].replace("\r\n", "\n")
                        norm_new = op["new"].replace("\r\n", "\n")

                        count = norm_full.count(norm_old)
                        if count == 1:
                            replaced = norm_full.replace(norm_old, norm_new, 1)
                        elif count == 0:
                            fuzzy = self._fuzzy_line_replace(norm_full, norm_old, norm_new)
                            if fuzzy is None:
                                results.append({"path": rel, "status": "error", "message": "OLD block not found in file."})
                                continue
                            replaced = fuzzy
                        else:
                            results.append({"path": rel, "status": "error", "message": f"Match found {count} occurrences; include more context lines."})
                            continue

                        new_full = self._preserve_newlines(replaced, use_crlf)

                    elif op_type == "write":
                        try:
                            old_full = self._fm.read_raw(rel)
                            use_crlf = "\r\n" in old_full
                        except FileNotFoundError:
                            old_full = ""
                            use_crlf = False
                        new_full = self._preserve_newlines(op["content"], use_crlf)
                    else:
                        continue

                    syntax_err = self._validate_syntax(rel, new_full)
                    if syntax_err:
                        results.append({"path": rel, "status": "error", "message": syntax_err})
                        continue

                    diff_lines = self._build_diff_lines(old_full, new_full, rel)
                    if apply_changes:
                        self._fm.write_with_backup(rel, new_full, backups)
                    results.append({
                        "path": rel,
                        "status": "applied" if apply_changes else "ready",
                        "diff_lines": diff_lines,
                    })
                except Exception as e:
                    results.append({"path": rel, "status": "error", "message": str(e)})
        finally:
            if apply_changes and backups:
                self._fm.commit_undo_batch(backups)
        return results