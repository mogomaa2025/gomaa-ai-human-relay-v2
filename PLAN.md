# PLAN.md — ChatBridge Improvement Plan

Status: approved plan.

## 0. Current-state facts
- `core/prompt_builder.py:132` splits with `full_prompt[i:i + chunk_size]` (mid-line).
- `main.py:55` clamps `chunk_size = max(10000, ...)`; no mode concept.
- `main.py:99-110` `/api/file` POST writes with no syntax validation.
- `core/patch_engine.py:27-33` `_strip_wrappers` never called; `parse_response(fallback_path=...)` unused; `patch_engine.py:96-97` dead `pass`.
- `core/patch_engine.py:49` `rsplit(":", 1)` breaks Windows paths whose dir contains `-`.
- `core/file_manager.py:178-198` `smart_select` reads up to 800 files (<250 KB) fully and keeps every `raw_cache`.
- Git tracks junk: `relative/path.ext`, `metadata.log_type.txt`, `core/__pycache__/*.pyc`, 60 `.chatbridge_undo/*.json`. No `.gitignore`, `requirements.txt`, `README.md`, tests.
- `static/app.js:955` builds `fallback_path`; `static/app.js:1281` touches `#fallbackFile`.
- No Undo control exists in the UI (only `POST /api/undo`).

## 1. Modes system
New `core/modes.py`:
```python
DEFAULT_MODE = "gemini"
MODES = {
    "gemini":  {"label": "Gemini",  "max_input_chars": 1_000_000, "chunk_chars": 120_000, "output_format": "text"},
    "copilot": {"label": "Copilot", "max_input_chars": 30_000,    "chunk_chars": 28_000,  "output_format": "json_parts"},
    "chatgpt": {"label": "ChatGPT", "max_input_chars": 100_000,   "chunk_chars": 90_000,  "output_format": "text"},
    "claude":  {"label": "Claude",  "max_input_chars": 200_000,   "chunk_chars": 180_000, "output_format": "text"},
}
def get_mode(name: str) -> dict
```
Copilot limit `30000`, chunk `28000` (safety margin). Confirmed limits: gemini 1,000,000 / chatgpt 100,000 / claude 200,000 / copilot 30,000.

`core/prompt_builder.py`: `build(..., mode="gemini")`; `cfg = get_mode(mode)`; `chunk_size = min(requested_chunk_size or cfg["chunk_chars"], cfg["max_input_chars"])`; copilot hard-capped at `28_000`; append a mode note to `FORMAT_RULES`; return `mode`, `max_input_chars`, `output_format`, `over_limit = chars > max_input_chars`.

`main.py`: import modes; `index()` passes `modes=MODES`, `default_mode=DEFAULT_MODE`; new `GET /api/modes`; `/api/build_prompt` reads `mode=data.get("mode", DEFAULT_MODE)`, validates, passes to builder.

`templates/index.html`: `<select id="modePicker">` in the topbar, options rendered via Jinja with `data-max`/`data-format`.

`static/app.js`: state `mode = localStorage.getItem("chatbridge.mode") || "gemini"`; `initModePicker()` restores value, persists on change, updates warning/JSON controls; `buildPrompt()` sends `mode`; `init()` calls `initModePicker()`.

Tests (`tests/test_prompt_builder.py`): default gemini; copilot chunk 28000; mode/max returned; over_limit; unknown→gemini. `tests/test_api.py`: build_prompt echoes copilot; `/api/modes` lists 4.

Acceptance: picker persists; default gemini; copilot parts ≤ 28,000 chars.

## 2. Copilot JSON export
`core/prompt_builder.py`: after parts, build `json_parts = [{"mode": name, "part": i+1, "total_parts": len(parts), "prompt": p} for i, p in enumerate(parts)]`; return it.

`templates/index.html`: `#copyJsonBtn` ("Copy as JSON") in `stagePasteCard` card-head; `#jsonPartsPanel` with `#jsonPartsList`, `#copyAllProgress`, `#downloadJsonBtn`.

`static/app.js`: `renderJsonParts(data)`, `copyJsonPart(index)` (per-part `navigator.clipboard.writeText(JSON.stringify(obj))`), `async copyAllJsonParts()` (await + ~400 ms delay, progress "Copied 2/5", disable while running), `downloadJsonParts()` (Blob `.json`, `<a download="chatbridge_prompt_<mode>.json">`). `buildPrompt()` stores `this.jsonParts`; shows `#copyJsonBtn` when `output_format === "json_parts"`.

`static/style.css`: `.json-parts`, `.json-part-row`, `.json-progress`.

Tests: unit asserts keys/values, `total_parts == len(parts)`, each `prompt` ≤ 28,000. Playwright parses clipboard JSON.

Acceptance: per-part copy, sequential copy-all with progress, `.json` download all work.

## 3. Line-boundary chunking
`core/prompt_builder.py`: replace slice with `_split_on_lines(text, chunk_size) -> list[str]`:
- accumulate lines with `keepends`; flush before exceeding `chunk_size`;
- if the line that would flush starts with `=== FILE:` / `=== OUTLINE:`, push it to the next chunk (no orphan header);
- hard-split a single line > `chunk_size` as a guard.
Keep `[PART i/n — Wait for remaining parts before replying]` and final `— All parts sent. Reply now` headers; single part has no header.

Tests: newline boundaries; no orphan `=== FILE:`; part headers; single part; overlong single line guard.

Acceptance: no mid-line split; headers intact; headers correct.

## 4. Mode-limit warning
`templates/index.html`: `<div id="modeLimitWarning" class="mode-warning hidden">` above the paste textarea.

`static/app.js`: `renderModeWarning(data)` shows amber banner `"Prompt is {chars} chars — exceeds {mode} limit of {max_input_chars}. It will be sent in {n} part(s)."` when `data.over_limit`, else hides; called at end of `buildPrompt()` before clipboard write; `initModePicker()` refreshes live on mode change.

`static/style.css`: `.mode-warning`.

Tests: unit asserts `over_limit`; Playwright asserts banner for oversized copilot prompt.

Acceptance: warning shown before copy whenever over limit.

## 5. Defect fixes

### 5a. NEED_FILES Windows paths — `core/patch_engine.py`
Add `NEED_SPEC_RE = re.compile(r"^(?:(\d+)-(\d+)|(full|outline))$", re.I)`. New `_parse_need_line(line)`: try `head, tail = line.rsplit(":", 1)`; only if `tail` matches `NEED_SPEC_RE` treat `head` as path; otherwise whole line is path with mode `full`. Replace inline parsing.

### 5b. Dead code
Remove `_strip_wrappers`; remove `fallback_path` param from `parse_response`; remove dead `pass` block; `main.py /api/apply` drops `fallback_path`; `app.js runOps` drops `fallback_path` and request field; remove `#fallbackFile` input and its `init()` reset.

### 5c. Manual-save syntax validation
New `core/validators.py` `validate_syntax(rel_path, content) -> str | None` (Python `ast.parse`, JSON `json.loads`), moved from `PatchEngine._validate_syntax`; patch engine imports it. `main.py /api/file` POST validates and returns `{"error": ...}, 400` before writing. `app.js saveEditor` already surfaces `data.error`.

### 5d. Repo hygiene
New `.gitignore`: `__pycache__/`, `*.py[cod]`, `.venv/`, `venv/`, `.chatbridge_undo/`, `.pytest_cache/`, `playwright-report/`, `test-results/`, `node_modules/` (do not ignore `e2e/screenshots/`). `git rm -r --cached core/__pycache__ .chatbridge_undo`; delete `relative/path.ext`, `metadata.log_type.txt`, cache/undo artifacts. Remove `d == "relative"` special case in `scan_files` (`file_manager.py:110`).

### 5e. smart_select memory cap — `core/file_manager.py`
Add `MAX_CONTENT_SCAN_BYTES = 64 * 1024`, `MAX_SCORE_FILE_BYTES = 2_000_000`; new `_read_head(rel, max_bytes)` using `open(..., newline="").read(max_bytes)`. In `smart_select`: skip content scoring if `size > MAX_SCORE_FILE_BYTES`; score via `_read_head(..., MAX_CONTENT_SCAN_BYTES)`; do not store `raw_cache` in `scored`; after sorting/slicing `top_items`, read limited heads only for those `max_files` for `_find_best_line_range`/`ast.parse`. Memory bounded to `max_files * 64 KB`.

Tests: Windows NEED_FILES (no spec / with range / hyphen dir / full+outline); `parse_response` has no `fallback_path`; `_strip_wrappers` gone; save py/json syntax → 400; valid save ok; `smart_select` caps read bytes and skips huge files.

Acceptance: Windows lines parse; no dead symbols; invalid syntax rejected; no junk tracked; ≤64 KB per file read in smart_select.

## 6. Basics + Undo button
- `requirements.txt`: `Flask==3.1.3`. `requirements-dev.txt`: `pytest==9.2.0`, `playwright==1.65.2`.
- `README.md`: run steps, modes table, port override, test commands, Playwright browser install.
- `main.py` port override: `_parse_args()` with `--port` (default `CHATBRIDGE_PORT`/`PORT`/5000) and `--host`; `app.run(host=args.host, port=args.port, debug=False)`.
- Undo button: add `<button class="btn ghost" id="undoBtn" onclick="App.undoLast()">↶ Undo</button>` in the topbar. New `async undoLast()` in `app.js`: `POST /api/undo`, on success toast, `await this.loadFiles()`, clear `lastFailedErrors`, re-render; on 400 toast "Nothing to undo." Add `.btn.ghost` reuse (no new CSS needed).

Acceptance: env var and `--port` bind 5055; default 5000; Undo button reverts last applied batch and refreshes the tree.

## 7. Tests

`pytest.ini`: `testpaths = tests e2e`, `markers = e2e: end-to-end Playwright tests`.

`tests/conftest.py`: `fm` fixture (`FileManager(tmp_path)` with sample `.py`/`.json`/ignored dirs); `app_client` fixture (Flask `test_client`, root monkeypatched).

`tests/test_prompt_builder.py`: mode defaults/limits; chunk boundaries; header protection; part headers; json_parts shape; over_limit.
`tests/test_patch_engine.py`: EDIT apply/ready; WRITE create; fuzzy; multi-occurrence error; CRLF; syntax rejection; ASK/PLAN/STATUS; Windows NEED_FILES; removed dead API.
`tests/test_file_manager.py`: `safe_path` traversal; `scan_files` ignores; `read_text` ranges; backup + `undo_last`; smart_select caps; `set_root` validation.
`tests/test_api.py`: build_prompt mode; save validation; undo.

`e2e/conftest.py`: `sandbox_project` (temp dir, `app.py` = `def hello():\n    return "hi"\n`); `server` (free port, subprocess `python main.py --port <port>` with `PROJECT_DIR`=sandbox, poll `/`); `page` (headless chromium, clipboard permissions granted for origin).

`e2e/test_e2e.py` (marked `e2e`):
1. Open `/`; assert UI; screenshot `e2e/screenshots/01-home.png`.
2. Set `#modePicker` = copilot; assert `localStorage["chatbridge.mode"] == "copilot"`.
3. Select sandbox file, fill `#task`, click "Send Goal"; wait for `#stagePasteCard`.
4. Click "Copy as JSON", copy part 1, read clipboard; assert `{mode, part, total_parts, prompt}`, `mode=="copilot"`, `len(prompt) <= 28000`; screenshot `02-copilot-json.png`.
5. Fill `#aiResponse` with `<<<EDIT path="app.py">>>...<<<END>>>` + `<<<STATUS: DONE>>>`; `page.evaluate("App.runOps(true)")`; wait for `.diff-file`.
6. Assert sandbox `app.py` changed; screenshot `03-applied-diff.png`.
7. Click `#undoBtn`; assert file reverted and toast shown.

Run: `pytest tests -q`; `python -m playwright install chromium`; `pytest e2e -q`.

Acceptance: unit tests pass; e2e switch→build→JSON copy→apply→verify→undo (via button); three screenshots exist.

## File-change summary
| File | Change |
|---|---|
| `core/modes.py` | new mode registry |
| `core/validators.py` | new shared `validate_syntax` |
| `core/prompt_builder.py` | mode param, line splitter, json_parts, over_limit |
| `core/patch_engine.py` | Windows NEED_FILES parser, remove dead code, use validators |
| `core/file_manager.py` | `_read_head`, byte caps, drop `relative` special case |
| `main.py` | `/api/modes`, mode in build, save validation, remove fallback_path, port flag/env |
| `templates/index.html` | mode picker, JSON panel, warning, Undo button, remove `#fallbackFile` |
| `static/app.js` | mode state/persist, JSON copy-all/download, warning, undoLast, remove fallback |
| `static/style.css` | mode picker, warning, JSON panel |
| `.gitignore`, `requirements.txt`, `requirements-dev.txt`, `README.md`, `pytest.ini` | new |
| `tests/`, `e2e/` | new suites + screenshots |
| `relative/path.ext`, `metadata.log_type.txt`, tracked caches/undo | deleted/untracked |
