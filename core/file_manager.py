import ast
import fnmatch
import json
import os
import re
import string
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class BackupEntry:
    path: Path
    existed: bool
    content: str


class FileManager:
    """Pure-Python, Windows EDR-safe file system manager with atomic writes & disk undo."""

    IGNORE_DIRS = {
        ".git", "__pycache__", "node_modules", ".venv", "venv",
        "dist", "build", ".idea", ".vscode", ".pytest_cache", ".mypy_cache",
        ".chatbridge_undo",
    }
    BINARY_EXTS = {
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip",
        ".exe", ".dll", ".pyd", ".so", ".pyc", ".woff", ".woff2", ".ttf",
        ".mp3", ".mp4", ".db", ".sqlite", ".bin",
    }

    def __init__(self, root_dir: str | Path | None = None) -> None:
        self._root = Path(root_dir or os.getcwd()).resolve()

    @property
    def root(self) -> Path:
        return self._root

    @property
    def _undo_dir(self) -> Path:
        return self._root / ".chatbridge_undo"

    def set_root(self, new_root: str) -> Path:
        candidate = Path(new_root.strip()).resolve()
        if not candidate.is_dir():
            raise ValueError(f"Directory does not exist: {new_root}")
        self._root = candidate
        return self._root

    def safe_path(self, rel_path: str) -> Path:
        target = (self._root / rel_path.strip()).resolve()
        if not target.is_relative_to(self._root):
            raise ValueError(f"Access denied outside project root: {rel_path}")
        return target

    def browse_dirs(self, path_str: str = "") -> dict:
        """Pure-Python directory browser for Windows drives & folders (no subprocess)."""
        target = Path(path_str.strip()).resolve() if path_str.strip() else self._root
        if not target.is_dir():
            target = self._root

        drives = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
        dirs = []
        try:
            for entry in sorted(target.iterdir(), key=lambda p: p.name.lower()):
                if entry.is_dir() and not entry.name.startswith("."):
                    dirs.append({"name": entry.name, "path": str(entry)})
        except PermissionError:
            pass

        parent = str(target.parent) if target.parent != target else ""
        return {
            "current": str(target),
            "parent": parent,
            "drives": drives,
            "dirs": dirs,
        }

    def _load_ignore_patterns(self) -> list[str]:
        patterns = [".env", ".env.*"]
        for fname in (".gitignore", ".chatbridgeignore"):
            p = self._root / fname
            if p.is_file():
                for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        patterns.append(line.rstrip("/"))
        return patterns

    @staticmethod
    def _is_ignored(rel_posix: str, name: str, patterns: list[str]) -> bool:
        for pat in patterns:
            if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(rel_posix, pat):
                return True
        return False

    def scan_files(self, limit: int = 1500, include_ignored: bool = False) -> list[dict]:
        items: list[dict] = []
        try:
            patterns = self._load_ignore_patterns() if not include_ignored else []
            for dirpath, dirnames, filenames in os.walk(self._root):
                try:
                    rel_dir = Path(dirpath).relative_to(self._root)
                except Exception:
                    continue
                kept_dirs = []
                for d in sorted(dirnames):
                    if not include_ignored and (d in self.IGNORE_DIRS or d == "relative"):
                        continue
                    try:
                        rel_d = (rel_dir / d).as_posix()
                        if rel_d.startswith("./"):
                            rel_d = rel_d[2:]
                        if include_ignored or not self._is_ignored(rel_d, d, patterns):
                            kept_dirs.append(d)
                    except Exception:
                        kept_dirs.append(d)
                dirnames[:] = kept_dirs

                for fname in sorted(filenames):
                    try:
                        p = Path(dirpath) / fname
                        if p.suffix.lower() in self.BINARY_EXTS:
                            continue
                        rel = p.relative_to(self._root).as_posix()
                        if not include_ignored and self._is_ignored(rel, fname, patterns):
                            continue
                        size = p.stat().st_size
                        line_count = 0
                        try:
                            with p.open("r", encoding="utf-8", errors="ignore") as fh:
                                line_count = sum(1 for _ in fh)
                        except Exception:
                            line_count = max(1, size // 30)

                        items.append({
                            "path": rel,
                            "size": size,
                            "lines": line_count,
                            "est_tokens": max(1, size // 4),
                            "is_ignored": self._is_ignored(rel, fname, patterns) if not include_ignored else False,
                        })
                    except Exception:
                        continue
                    if len(items) >= limit:
                        return items
        except Exception:
            pass
        return items

    def _find_best_line_range(self, raw_text: str, terms: list[str], context_pad: int = 25) -> tuple[int, int] | None:
        """Finds a tight line range around matching keywords when a file is large."""
        lines = raw_text.splitlines()
        total = len(lines)
        if total <= 90 or not terms:
            return None
        hits = []
        for idx, line in enumerate(lines, 1):
            low = line.lower()
            if any(t in low for t in terms[:10]):
                hits.append(idx)
        if not hits:
            return None
        start = max(1, min(hits) - context_pad)
        end = min(total, max(hits) + context_pad)
        if (end - start) >= total * 0.75:
            return None
        return start, end

    def smart_select(self, task: str, max_files: int = 12) -> list[dict]:
        """Selects relevant files & targeted line ranges by matching task keywords against paths, contents, and imports."""
        keywords = [w.lower() for w in re.findall(r"[A-Za-z0-9_./-]{3,}", task)]
        stopwords = {"the", "and", "for", "with", "this", "that", "from", "into", "add", "fix", "edit", "make", "use"}
        terms = [k for k in dict.fromkeys(keywords) if k not in stopwords]

        files = self.scan_files(limit=800)
        scored: list[tuple[int, dict, str]] = []

        for f in files:
            path_l = f["path"].lower()
            score = 0
            raw_cache = ""
            for t in terms:
                if t in path_l:
                    score += 10
            if terms and f["size"] < 250_000:
                try:
                    raw_cache = self.read_raw(f["path"])
                    content_l = raw_cache.lower()
                    for t in terms[:10]:
                        if t in content_l:
                            score += 2
                except OSError:
                    pass
            if score > 0:
                scored.append((score, f, raw_cache))

        scored.sort(key=lambda x: (-x[0], x[1]["est_tokens"]))
        top_items = scored[:max_files]

        selected_map: dict[str, dict] = {}
        all_paths = {f["path"]: f for f in files}
        for _, f, raw_cache in top_items:
            rng = self._find_best_line_range(raw_cache, terms) if raw_cache else None
            if rng:
                s, e = rng
                est = max(20, int(f["est_tokens"] * ((e - s + 1) / max(1, len(raw_cache.splitlines())))))
                selected_map[f["path"]] = {"path": f["path"], "mode": "range", "start": s, "end": e, "tokens": est}
            else:
                mode = "outline" if f["est_tokens"] > 4000 else "full"
                selected_map[f["path"]] = {"path": f["path"], "mode": mode, "start": "", "end": "", "tokens": f["est_tokens"]}

            if f["path"].endswith(".py") and len(selected_map) < max_files:
                try:
                    tree = ast.parse(raw_cache or self.read_raw(f["path"]))
                    for node in ast.walk(tree):
                        if isinstance(node, ast.ImportFrom) and node.module:
                            mod_path = node.module.replace(".", "/") + ".py"
                            if mod_path in all_paths and mod_path not in selected_map:
                                mf = all_paths[mod_path]
                                selected_map[mod_path] = {"path": mod_path, "mode": "outline", "start": "", "end": "", "tokens": mf["est_tokens"]}
                except SyntaxError:
                    pass

        return list(selected_map.values())[:max_files]

    def read_raw(self, rel_path: str) -> str:
        """Read exact raw text preserving original CRLF/LF line endings."""
        p = self.safe_path(rel_path)
        if not p.is_file():
            raise FileNotFoundError(f"File not found: {rel_path}")
        with p.open("r", encoding="utf-8", errors="replace", newline="") as fh:
            return fh.read()

    def read_text(self, rel_path: str, start_line: int | None = None, end_line: int | None = None) -> tuple[str, int]:
        text = self.read_raw(rel_path)
        lines = text.splitlines()
        total = len(lines)
        if start_line is None and end_line is None:
            return text, total
        s = max(1, start_line or 1)
        e = min(total, end_line or total)
        sliced = [f"{idx}: {lines[idx - 1]}" for idx in range(s, e + 1)]
        return "\n".join(sliced), total

    @staticmethod
    def _atomic_write(target: Path, content: str) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".tmp_cb_", suffix=target.suffix)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
                fh.write(content)
            os.replace(tmp_name, target)
        except Exception:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise

    def write_with_backup(self, rel_path: str, content: str, backups: list[BackupEntry]) -> None:
        p = self.safe_path(rel_path)
        existed = p.is_file()
        old = self.read_raw(rel_path) if existed else ""
        backups.append(BackupEntry(path=p, existed=existed, content=old))
        self._atomic_write(p, content)

    def commit_undo_batch(self, backups: list[BackupEntry]) -> None:
        if not backups:
            return
        self._undo_dir.mkdir(parents=True, exist_ok=True)
        batch_file = self._undo_dir / f"undo_{int(time.time() * 1000)}.json"
        payload = [
            {"path": str(b.path), "existed": b.existed, "content": b.content}
            for b in backups
        ]
        self._atomic_write(batch_file, json.dumps(payload, ensure_ascii=False))

    def undo_last(self) -> int:
        if not self._undo_dir.is_dir():
            return 0
        batches = sorted(self._undo_dir.glob("undo_*.json"))
        if not batches:
            return 0
        latest = batches[-1]
        entries = json.loads(latest.read_text(encoding="utf-8"))
        for entry in reversed(entries):
            p = Path(entry["path"])
            if entry["existed"]:
                self._atomic_write(p, entry["content"])
            elif p.exists():
                p.unlink()
        latest.unlink()
        return len(entries)