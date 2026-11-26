"""Chat mode registry: per-mode input limits and output format."""

DEFAULT_MODE = "gemini"

MODES = {
    "gemini": {
        "label": "Gemini",
        "max_input_chars": 1_000_000,
        "chunk_chars": 120_000,
        "output_format": "text",
    },
    "copilot": {
        "label": "Copilot",
        "max_input_chars": 30_000,
        "chunk_chars": 28_000,
        "output_format": "json_parts",
    },
    "chatgpt": {
        "label": "ChatGPT",
        "max_input_chars": 100_000,
        "chunk_chars": 90_000,
        "output_format": "text",
    },
    "claude": {
        "label": "Claude",
        "max_input_chars": 200_000,
        "chunk_chars": 180_000,
        "output_format": "text",
    },
}


def get_mode(name: str) -> dict:
    """Return the config for a mode, falling back to the default mode."""
    return MODES.get((name or "").strip().lower(), MODES[DEFAULT_MODE])
