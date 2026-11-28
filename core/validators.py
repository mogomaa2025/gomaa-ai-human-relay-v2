"""Shared content validation used by both the patch engine and manual saves."""

import ast
import json


def validate_syntax(rel_path: str, content: str) -> str | None:
    """Return a human-readable syntax error for Python/JSON files, else None."""
    lower = rel_path.lower()
    if lower.endswith(".py"):
        try:
            ast.parse(content, filename=rel_path)
        except SyntaxError as e:
            return f"Python SyntaxError on line {e.lineno}: {e.msg}"
    elif lower.endswith(".json"):
        try:
            json.loads(content, parse_constant=lambda value: (_ for _ in ()).throw(ValueError("invalid constant")))
        except json.JSONDecodeError as e:
            return f"JSON SyntaxError on line {e.lineno}, col {e.colno}: {e.msg}"
        except ValueError:
            return "JSON SyntaxError: invalid numeric constant (NaN or Infinity)"
    return None
