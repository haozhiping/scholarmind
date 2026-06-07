"""
Prompt loader: read prompts/*.md, extract the fenced prompt block, and fill variables.

Each prompt file keeps the runnable template inside the first ``` fenced block.
We extract that block and expose render(name, **vars) which does str.format(**vars).
"""
import re
from functools import lru_cache
from pathlib import Path

# prompts/ lives at repo root, two levels up from this file (backend/common/prompts.py)
_PROMPT_DIR = Path(__file__).parents[2] / "prompts"


@lru_cache(maxsize=64)
def _load_raw(name: str) -> str:
    """Load a prompt template, returning the content of the first fenced block (or whole file)."""
    path = _PROMPT_DIR / f"{name}.md"
    text = path.read_text(encoding="utf-8")
    match = re.search(r"```\s*\n(.*?)\n```", text, re.DOTALL)
    return match.group(1) if match else text


def render(name: str, **variables) -> str:
    """Load prompt `name` and fill {var} placeholders. Unused braces are left as-is."""
    template = _load_raw(name)
    if not variables:
        return template
    try:
        return template.format(**variables)
    except Exception:
        # Many prompts embed literal JSON braces ({"intent": ...}) that are NOT format fields.
        # Fall back to targeted replacement of only our known {var} placeholders.
        out = template
        for key, val in variables.items():
            out = out.replace("{" + key + "}", str(val))
        return out
