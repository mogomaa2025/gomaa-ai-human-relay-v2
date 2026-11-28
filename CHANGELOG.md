# CHANGELOG — ChatBridge (Gomaa Relay)

## Round-5 batch (implemented)
- `safe_path` gained `follow_final` so delete/rename/paste operate on a symlink itself instead of following it (fixes data-loss when deleting a symlinked folder).
- Undo now supports manual file operations (delete, rename, create, copy/cut paste) via snapshot entries (file/blob/dir/symlink/remove), with binary-safe capture.
- Partial undo restore now rewrites the batch with the entries that failed instead of discarding the whole batch.
- Protected dirs (`.git`, `.chatbridge_undo`) are always excluded from scans, even with `include_ignored=true`.
- Python outline now joins with a real newline instead of a literal `\\n`.
- Manual destructive file APIs (save/rename/delete/create/paste) now run under the FileManager lock (TOCTOU fix).
- Boolean flags (`include_tree`, `is_dir`, `apply`) are parsed strictly instead of `bool("false")`.
- Prompt builder guards missing `question` keys in answers.
- Browser directory rows no longer inject folder names via `innerHTML` (XSS fix).
- Auto-apply on clipboard capture/paste applies immediately (preview-first reverted).
- Tests updated: symlink delete preserves target; full suite (85) + Playwright e2e (3) pass.

## Round-4 batch (implemented)
- `safe_path` now rejects NUL bytes and control characters in paths.
- Undo batch is committed via `try/finally` so partial writes remain undoable on fatal errors.
- Undo restore now returns the count of successfully restored entries instead of discarding the whole batch on one failure.
- Directory paste creates missing parent directories; `action` validated as copy/cut.
- Context-menu Paste resolves the correct parent when invoked on a file.
- `set_root` rejects empty paths; `api_project` validates accordingly.
- Thread-safety: lock is now an RLock and `root`/reads/listing snapshot under lock.
- Prompt outline extraction sorted by line number.
- Frontend: debounced editor highlighting, undo-list refresh after undo, @-mention modal closes when no matches, editor closes on delete/rename, clipboard cleared on goal reset.
- `chunk_size` rejects non-integral floats; `answers`/`applied_log` coerced/filtered.
- README test count refreshed (85+).
- Dropped: undo batch size cap already handled by existing pruning (no change); symlink paste preservation infeasible under resolved `safe_path` (copy follows link).

## Round-3 batch (implemented)
- Undo self-healing: tampered/malformed batch files are removed automatically so undo is not permanently blocked.
- `_fuzzy_line_replace` now splits on `\n` only (no Unicode line separators), matching `FileManager._split_lines`.
- File paste no longer overwrites an existing destination (returns 400).
- Context-menu New File/Folder resolves the correct parent directory.
- Editor syntax highlighting now refreshes while typing.
- Escape key closes the Undo History modal.
- Remaining `splitlines()` usages (parse blocks, diff, outline, chunker, ignore patterns) unified to `\n`-only splitting.
- Directory delete/paste handles symlinked entries more robustly.
- Create-item rejects names containing path separators.
- Relative undo-batch paths are resolved against the project root for containment checks.
- Added tests for undo self-healing, paste overwrite, unicode fuzzy split, symlink delete, create-item validation, relative containment.

## Unreleased (P2–P4 batch)
- Binary content sniffing in scan/read; expanded binary extensions.
- Undo retention: keep last 50 batches / 50 MB total.
- Thread-safe FileManager operations.
- CRLF consistency improvements.
- Accessibility (ARIA, ESC-to-close) and mobile layout breakpoints.
- Undo history modal with per-batch undo and Preview (dry-run) button.
- Clipboard capture no longer clears the clipboard; no-replay guard prevents re-applying.
- README refreshed; PLAN.md renamed to CHANGELOG.md.

## Shipped (P0+P1 batch, commit f315767)
- Hardened safe_path: protected dirs, empty/root, symlink escape; root-level ops guarded.
- Request validation + 10 MB payload cap; strict apply flag.
- Undo restore containment + malformed-batch handling; unique undo filenames.
- escapeHtml + editor backdrop; coercion of patch/prompt inputs.
- Scan/tree limits aligned; autoSelectSmart wired.
- Unit suite expanded to 58 tests.

## Earlier batches
- Modes system (gemini/copilot/chatgpt/claude), Copilot JSON export, line-boundary chunking.
- Defect fixes, shared validators, requirements, README, pytest suite, Playwright e2e.