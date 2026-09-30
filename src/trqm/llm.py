from __future__ import annotations

import json
import os
import re
from typing import Any

from openai import OpenAI

PROMPT_TRQMMETA = "trqmmeta-v1"


def get_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY", "ollama")
    base_url = os.getenv("OPENAI_BASE_URL")  # e.g. http://localhost:11434/v1
    kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


def get_model() -> str:
    return os.getenv("TRQM_MODEL", os.getenv("OPENAI_MODEL", "gpt-4o-mini"))


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
    """Call the chat model and parse a JSON object from the response."""
    client = get_client()
    model = get_model()
    response = client.chat.completions.create(
        model=model,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content or "{}"
    return _extract_json(content)


def chat_text(system: str, user: str, *, temperature: float = 0.2) -> str:
    client = get_client()
    model = get_model()
    response = client.chat.completions.create(
        model=model,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return response.choices[0].message.content or ""
