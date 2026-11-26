# ChatBridge

ChatBridge is a local Flask app that lets you edit any project using an AI chat
only. You pick files (or let Smart Select choose them), ChatBridge builds a
token-efficient prompt, you paste it into your AI chat, then paste the AI reply
back — ChatBridge validates and applies the edits with undo support.

## Run

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Open http://127.0.0.1:5000

### Port override

Default port is 5000. Override with either a flag or an environment variable:

```bash
python main.py --port 5055
CHATBRIDGE_PORT=5055 python main.py
PORT=5055 python main.py
```

`--host` (or `CHATBRIDGE_HOST`) sets the bind address; the default is
`127.0.0.1`.

Set `PROJECT_DIR` to choose the project root at startup.

## Modes

Pick a mode in the top bar. The mode controls the maximum input size and the
output format used to feed the AI chat. The choice is saved in `localStorage`.

| Mode | Max input chars | Output format |
|------|----------------:|---------------|
| Gemini | 1,000,000 | text |
| Copilot | 30,000 | JSON parts |
| ChatGPT | 100,000 | text |
| Claude | 200,000 | text |

Prompts are split on line boundaries, never mid-line. For Copilot, ChatBridge
splits into parts of at most 28,000 chars and can copy each part as JSON
(`{mode, part, total_parts, prompt}`), copy them all sequentially, or download
them as a `.json` file. If the full prompt exceeds the selected mode's limit a
warning is shown before copying.

## AI reply format

The prompt asks the AI to reply with tagged blocks:

- `<<<EDIT path="...">>>` + `<<<OLD>>>` / `<<<NEW>>>` / `<<<END>>>`
- `<<<WRITE path="...">>>` + content + `<<<END>>>`
- `<<<NEED_FILES>>>` + `path` or `path:100-220` + `<<<END>>>`
- `<<<ASK>>>`, `<<<PLAN>>>`, `<<<STATUS: NEED_MORE|APPLIED_NEXT|DONE>>>`

Windows paths such as `C:\project\file.py:10-20` are supported.

## Tests

```bash
pip install -r requirements-dev.txt
pytest tests -q                 # unit tests
python -m playwright install chromium
pytest e2e -q                   # Playwright end-to-end tests
```

E2E screenshots are written to `e2e/screenshots/`.

## Undo

Every applied batch is stored under `.chatbridge_undo/` (git-ignored). Click
**↶ Undo** in the top bar to revert the last batch.
