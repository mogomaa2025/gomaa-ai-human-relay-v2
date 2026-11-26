# CHANGELOG — ChatBridge (Gomaa Relay)

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