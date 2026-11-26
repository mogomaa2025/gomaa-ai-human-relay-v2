import ast
import fnmatch
import json
import os
import re
import string
import tempfile
import threading
import time
import uuid
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
        ".exe", ".dll", ".pyd", ".so", ".dylib", ".o", ".a", ".class",
        ".jar", ".war", ".tar", ".gz", ".7z", ".rar", ".pyc", ".woff",
        ".woff2", ".ttf", ".otf", ".mp3", ".mp4", ".mov", ".avi", ".mkv",
        ".flac", ".ogg", ".wav", ".db", ".sqlite", ".bin", ".psd", ".ai",
    }
    MAX_CONTENT_SCAN_BYTES = 64 * 1024
    MAX_SCORE_FILE_BYTES = 2_000_000
    DEFAULT_SCAN_LIMIT = 1500
    TREE_LIMIT = 250
    UNDO_MAX_BATCHES = 50
    UNDO_MAX_BYTES = 50 * 1024 * 1024

    def __init__(self, root_dir: str | Path | None = None) -> None:
        self._root = Path(root_dir or os.getcwd()).resolve()
        self._lock = threading.RLock()

    @property
    def root(self) -> Path:
        with self._lock:
            return self._root

    @property
    def _undo_dir(self) -> Path:
        return self.root / ".chatbridge_undo"

    def set_root(self, new_root: str) -> Path:
        candidate = Path(new_root.strip()).resolve()
        if not candidate.is_dir():
            raise ValueError(f"Directory does not exist: {new_root}")
        with self._lock:
            self._root = candidate
        return self._root

    PROTECTED_DIRS = {".chatbridge_undo", ".git"}

    def safe_path(self, rel_path: str) -> Path:
        with self._lock:
            if not isinstance(rel_path, str):
                raise ValueError(f"Path must be a string: {rel_path!r}")
            if "\x00" in rel_path:
                raise ValueError(f"Path must not contain NUL bytes: {rel_path!r}")
            cleaned = rel_path.strip()
            if not cleaned:
                raise ValueError("Path must not be empty.")
            if any(ord(ch) < 32 for ch in cleaned):
                raise ValueError(f"Path must not contain control characters: {rel_path!r}")
            target = (self._root / cleaned).resolve()
            if not target.is_relative_to(self._root):
                raise ValueError(f"Access denied outside project root: {rel_path}")
            if target == self._root:
                raise ValueError(f"Access denied to project root: {rel_path}")
            try:
                rel = target.relative_to(self._root)
            except ValueError:
                raise ValueError(f"Access denied outside project root: {rel_path}")
            if any(part in self.PROTECTED_DIRS for part in rel.parts):
                raise ValueError(f"Access denied to protected path: {rel_path}")
            return target

    @staticmethod
    def _split_lines(text: str) -> list[str]:
        """Split text into lines on ``\\n`` only.

        Unlike ``str.splitlines()``, this does not split on Unicode line
        separators (e.g. ``\\u2028``) or other control characters, so line
        counts stay stable. A single trailing ``\\r`` is stripped to match the
        previous ``splitlines()`` behaviour for CRLF files.
        """
        if text == "":
            return []
        lines = text.split("\n")
        if text.endswith("\n"):
            lines = lines[:-1]
        return [line[:-1] if line.endswith("\r") else line for line in lines]

    @staticmethod
    def _is_binary_bytes(sample: bytes) -> bool:
        if not sample:
            return False
        if b"\x00" in sample:
            return True
        try:
            sample.decode("utf-8")
        except UnicodeDecodeError:
            return True
        return False

    def browse_dirs(self, path_str: str = "") -> dict:
        """Pure-Python directory browser for Windows drives & folders (no subprocess)."""
        with self._lock:
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
            p = self.root / fname
            if p.is_file():
                for line in self._split_lines(p.read_text(encoding="utf-8", errors="ignore")):
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

    def scan_files(self, limit: int = None, include_ignored: bool = False) -> list[dict]:
        if limit is None:
            limit = self.DEFAULT_SCAN_LIMIT
        try:
            limit = max(1, int(limit))
        except (TypeError, ValueError):
            limit = self.DEFAULT_SCAN_LIMIT
        root = self.root
        items: list[dict] = []
        try:
            patterns = self._load_ignore_patterns() if not include_ignored else []
            for dirpath, dirnames, filenames in os.walk(root):
                try:
                    rel_dir = Path(dirpath).relative_to(root)
                except Exception:
                    continue
                kept_dirs = []
                for d in sorted(dirnames):
                    if not include_ignored and d in self.IGNORE_DIRS:
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
                        rel = p.relative_to(root).as_posix()
                        if not include_ignored and self._is_ignored(rel, fname, patterns):
                            continue
                        size = p.stat().st_size
                        try:
                            with p.open("rb") as bf:
                                sample = bf.read(8192)
                            if self._is_binary_bytes(sample):
                                continue
                        except OSError:
                            continue
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
        lines = self._split_lines(raw_text)
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

    def _read_head(self, rel_path: str, max_bytes: int) -> str:
        """Read at most ``max_bytes`` from a file to bound memory usage."""
        p = self.safe_path(rel_path)
        if not p.is_file():
            raise FileNotFoundError(f"File not found: {rel_path}")
        with p.open("r", encoding="utf-8", errors="replace", newline="") as fh:
            return fh.read(max_bytes)

    def smart_select(self, task: str, max_files: int = 12) -> list[dict]:
        """Selects relevant files & targeted line ranges by matching task keywords against paths, contents, and imports."""
        keywords = [w.lower() for w in re.findall(r"[A-Za-z0-9_./-]{3,}", task)]
        stopwords = {"the", "and", "for", "with", "this", "that", "from", "into", "add", "fix", "edit", "make", "use"}
        terms = [k for k in dict.fromkeys(keywords) if k not in stopwords]

        files = self.scan_files(limit=800)
        scored: list[tuple[int, dict]] = []

        for f in files:
            path_l = f["path"].lower()
            score = 0
            for t in terms:
                if t in path_l:
                    score += 10
            if terms and f["size"] <= self.MAX_SCORE_FILE_BYTES:
                try:
                    content_l = self._read_head(f["path"], self.MAX_CONTENT_SCAN_BYTES).lower()
                    for t in terms[:10]:
                        if t in content_l:
                            score += 2
                except OSError:
                    pass
            if score > 0:
                scored.append((score, f))

        scored.sort(key=lambda x: (-x[0], x[1]["est_tokens"]))
        top_items = scored[:max_files]

        selected_map: dict[str, dict] = {}
        all_paths = {f["path"]: f for f in files}
        for _, f in top_items:
            raw_cache = ""
            try:
                raw_cache = self._read_head(f["path"], self.MAX_CONTENT_SCAN_BYTES)
            except OSError:
                pass
            rng = self._find_best_line_range(raw_cache, terms) if raw_cache else None
            if rng:
                s, e = rng
                est = max(20, int(f["est_tokens"] * ((e - s + 1) / max(1, len(self._split_lines(raw_cache))))))
                selected_map[f["path"]] = {"path": f["path"], "mode": "range", "start": s, "end": e, "tokens": est}
            else:
                mode = "outline" if f["est_tokens"] > 4000 else "full"
                selected_map[f["path"]] = {"path": f["path"], "mode": mode, "start": "", "end": "", "tokens": f["est_tokens"]}

            if f["path"].endswith(".py") and len(selected_map) < max_files:
                try:
                    tree = ast.parse(raw_cache or "")
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
        with self._lock:
            p = self.safe_path(rel_path)
            if not p.is_file():
                raise FileNotFoundError(f"File not found: {rel_path}")
            try:
                with p.open("rb") as bf:
                    sample = bf.read(8192)
                if self._is_binary_bytes(sample):
                    raise ValueError(f"Binary file cannot be read as text: {rel_path}")
            except OSError:
                raise
            with p.open("r", encoding="utf-8", errors="replace", newline="") as fh:
                return fh.read()

    def read_text(self, rel_path: str, start_line: int | None = None, end_line: int | None = None) -> tuple[str, int]:
        with self._lock:
            text = self.read_raw(rel_path)
            lines = self._split_lines(text)
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
        with self._lock:
            p = self.safe_path(rel_path)
            existed = p.is_file()
            old = self.read_raw(rel_path) if existed else ""
            backups.append(BackupEntry(path=p, existed=existed, content=old))
            self._atomic_write(p, content)

    def commit_undo_batch(self, backups: list[BackupEntry]) -> None:
        if not backups:
            return
        with self._lock:
            self._undo_dir.mkdir(parents=True, exist_ok=True)
            batch_file = self._undo_dir / f"undo_{int(time.time() * 1000)}_{uuid.uuid4().hex}.json"
            payload = [
                {"path": str(b.path), "existed": b.existed, "content": b.content}
                for b in backups
            ]
            self._atomic_write(batch_file, json.dumps(payload, ensure_ascii=False))
            self._prune_undo_batches()

    def _prune_undo_batches(self) -> None:
        if not self._undo_dir.is_dir():
            return
        batches = sorted(self._undo_dir.glob("undo_*.json"))
        while len(batches) > self.UNDO_MAX_BATCHES:
            try:
                batches[0].unlink()
            except OSError:
                pass
            batches.pop(0)
        total = 0
        for b in batches:
            try:
                total += b.stat().st_size
            except OSError:
                pass
        idx = 0
        while total > self.UNDO_MAX_BYTES and idx < len(batches):
            try:
                size = batches[idx].stat().st_size
            except OSError:
                size = 0
            try:
                batches[idx].unlink()
            except OSError:
                pass
            total -= size
            idx += 1

    def _is_contained_path(self, candidate: Path) -> bool:
        try:
            path_obj = Path(candidate)
            if not path_obj.is_absolute():
                path_obj = self.root / path_obj
            resolved = path_obj.resolve()
        except (OSError, RuntimeError):
            return False
        if not resolved.is_relative_to(self.root):
            return False
        if resolved == self.root:
            return False
        try:
            rel = resolved.relative_to(self.root)
        except ValueError:
            return False
        return not any(part in self.PROTECTED_DIRS for part in rel.parts)

    def _restore_batch_unlocked(self, batch_path: Path) -> int:
        def _discard() -> None:
            try:
                batch_path.unlink()
            except OSError:
                pass

        try:
            raw = batch_path.read_text(encoding="utf-8")
            entries = json.loads(raw)
        except (OSError, json.JSONDecodeError, UnicodeError):
            _discard()
            return 0
        if not isinstance(entries, list) or not entries:
            _discard()
            return 0
        validated: list[tuple[Path, bool, str]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                _discard()
                return 0
            if not all(k in entry for k in ("path", "existed", "content")):
                _discard()
                return 0
            if not isinstance(entry["path"], str) or not isinstance(entry["existed"], bool) or not isinstance(entry["content"], str):
                _discard()
                return 0
            p = Path(entry["path"])
            if not self._is_contained_path(p):
                _discard()
                return 0
            validated.append((p, entry["existed"], entry["content"]))
        restored = 0
        for p, existed, content in reversed(validated):
            try:
                if existed:
                    self._atomic_write(p, content)
                elif p.exists():
                    p.unlink()
                restored += 1
            except Exception:
                continue
        if restored > 0:
            try:
                batch_path.unlink()
            except OSError:
                pass
        return restored

    def undo_last(self) -> int:
        with self._lock:
            if not self._undo_dir.is_dir():
                return 0
            batches = sorted(self._undo_dir.glob("undo_*.json"))
            if not batches:
                return 0
            return self._restore_batch_unlocked(batches[-1])

    def list_undo_batches(self) -> list[dict]:
        with self._lock:
            if not self._undo_dir.is_dir():
                return []
            out: list[dict] = []
            for b in sorted(self._undo_dir.glob("undo_*.json")):
                count = 0
                try:
                    entries = json.loads(b.read_text(encoding="utf-8"))
                    if isinstance(entries, list):
                        count = len(entries)
                except Exception:
                    count = 0
                out.append({"name": b.name, "count": count})
            return out

    def undo_batch(self, name: str) -> int:
        if not isinstance(name, str) or not name:
            return 0
        if "/" in name or "\\" in name or ".." in name:
            return 0
        if not (name.startswith("undo_") and name.endswith(".json")):
            return 0
        path = self._undo_dir / name
        if not path.is_file():
            return 0
        with self._lock:
            return self._restore_batch_unlocked(path)