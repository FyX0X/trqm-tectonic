from __future__ import annotations

from pathlib import Path

from google import genai

_ROOT = Path(__file__).resolve().parent.parent
_API_KEY_PATH = _ROOT / "api_key.txt"
_MODEL = "gemini-3.1-flash-lite"

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        if not _API_KEY_PATH.exists():
            raise FileNotFoundError(
                f"Gemini API key file not found: {_API_KEY_PATH}. "
                "Create api_key.txt in the project root with your Google AI API key."
            )
        api_key = _API_KEY_PATH.read_text(encoding="utf-8").strip()
        if not api_key:
            raise ValueError(f"API key file is empty: {_API_KEY_PATH}")
        _client = genai.Client(api_key=api_key)
    return _client


def call_ai(prompt: str) -> str:
    """Send a prompt to Gemini and return the response text."""
    response = _get_client().models.generate_content(
        model=_MODEL,
        contents=prompt,
    )
    return response.text or ""


def get_model_name() -> str:
    return _MODEL
