from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

# Project root (…/trqm-tectonic) so ai_integration is importable
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ai_integration.ai import call_ai, get_model_name  # noqa: E402

PROMPT_TRQMMETA = "trqmmeta-v1"


def get_model() -> str:
    return get_model_name()


def _extract_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            return json.loads(match.group(0))
        raise


def chat_json(system: str, user: str, *, temperature: float = 0.2) -> Any:
    """Call Gemini via ai_integration and parse a JSON object from the response."""
    del temperature  # Gemini call in ai_integration has no temperature knob yet
    prompt = (
        f"{system.strip()}\n\n"
        "Respond with a single valid JSON object only. "
        "Do not wrap it in markdown unless necessary.\n\n"
        f"{user.strip()}"
    )
    content = call_ai(prompt) or "{}"
    return _extract_json(content)


def chat_text(system: str, user: str, *, temperature: float = 0.2) -> str:
    del temperature
    prompt = f"{system.strip()}\n\n{user.strip()}"
    return call_ai(prompt) or ""
