"""Shared, dependency-free text LLM client.

Used by content agents to call the provider that the user assigned to them in
the API Settings panel. Uses only ``urllib`` so it stays Termux-safe; the
blocking call is pushed to a worker thread so FastAPI's loop is never blocked.

Supported providers: ``gemini``, ``groq``, ``openai``.
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

SUPPORTED_PROVIDERS = ("gemini", "groq", "openai")

# Cloudflare in front of Groq rejects urllib's default agent (error 1010), so
# every provider call identifies the app explicitly.
USER_AGENT = (
    "CogniSphereLabs/1.0 (Termux; +https://github.com/amar768581/Multi-Agent-AI-company-)"
)

# Appended to the system prompt so every provider honours the requested language.
LANGUAGE_INSTRUCTIONS = {
    "bn": (
        "LANGUAGE: Write every human-readable value — titles, hooks, dialogue, "
        "voiceover, descriptions, prose, outreach copy and metadata — in natural, "
        "idiomatic Bengali (বাংলা). Keep JSON keys, command words, IDs and technical "
        "identifiers in English. Do not transliterate Bengali into Latin letters."
    ),
    "banglish": (
        "LANGUAGE: Write every human-readable value in natural Banglish (Romanized "
        "Bengali), the way Bengali speakers type in chat. Keep JSON keys, command "
        "words, IDs and technical identifiers in English."
    ),
}


def language_directive(language: str) -> str:
    """Instruction block for the requested output language ('' for English)."""
    return LANGUAGE_INSTRUCTIONS.get(language, "")

DEFAULT_MODELS = {
    "gemini": os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"),
    "groq": os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
    "openai": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
}


class LLMError(RuntimeError):
    """Raised when a provider call fails or is unsupported."""


def supports(provider: Optional[str]) -> bool:
    return bool(provider) and provider in SUPPORTED_PROVIDERS


async def generate(
    provider: str,
    api_key: str,
    system: str,
    prompt: str,
    *,
    temperature: float = 0.8,
    max_tokens: int = 2048,
    timeout: int = 60,
    language: str = "en",
) -> str:
    """Return the model's text answer, in ``language`` when requested."""
    if not supports(provider):
        raise LLMError(
            f"Provider '{provider}' is not a text LLM. Assign Gemini, Groq or OpenAI to this agent."
        )
    if not api_key:
        raise LLMError(f"No API key configured for '{provider}'.")
    directive = language_directive(language)
    if directive:
        system = f"{system}\n\n{directive}"
    return await asyncio.to_thread(
        _generate_sync,
        provider,
        api_key,
        system,
        prompt,
        temperature,
        max_tokens,
        timeout,
    )


def _post_json(url: str, payload: Dict[str, Any], headers: Dict[str, str], timeout: int) -> Dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={**headers, "User-Agent": USER_AGENT}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
        except Exception:  # pragma: no cover - best effort
            pass
        raise LLMError(f"HTTP {exc.code} from provider: {detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise LLMError(f"Network error reaching provider: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise LLMError("Provider returned a non-JSON response") from exc


def _generate_sync(
    provider: str,
    api_key: str,
    system: str,
    prompt: str,
    temperature: float,
    max_tokens: int,
    timeout: int,
) -> str:
    model = DEFAULT_MODELS[provider]

    if provider == "gemini":
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent"
        )
        payload = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
        data = _post_json(url, payload, headers, timeout)
        candidates = data.get("candidates") or []
        if not candidates:
            raise LLMError(f"Gemini returned no candidates: {str(data)[:200]}")
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(part.get("text", "") for part in parts).strip()
    else:
        base = (
            "https://api.groq.com/openai/v1/chat/completions"
            if provider == "groq"
            else "https://api.openai.com/v1/chat/completions"
        )
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        data = _post_json(base, payload, headers, timeout)
        choices = data.get("choices") or []
        if not choices:
            raise LLMError(f"{provider} returned no choices: {str(data)[:200]}")
        text = (choices[0].get("message", {}).get("content") or "").strip()

    if not text:
        raise LLMError(f"{provider} returned an empty completion")
    return text
