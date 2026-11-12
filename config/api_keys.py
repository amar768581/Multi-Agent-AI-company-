"""Per-agent API key storage for CogniSphere Labs.

Design notes
------------
* Keys live in ``config/api_keys.json`` (chmod 600) and are mirrored to ``.env``
  so external scripts and future agent modules can read the same credentials.
* Raw key values never leave this module through the HTTP layer: the API only
  ever sees ``masked`` strings.
* Every agent calls ``ApiKeyStore.resolve_for_agent(agent_id)`` to get the
  provider + key that was assigned to it in the UI.

No third-party dependency is used so this stays Termux-friendly.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
STORE_PATH = CONFIG_DIR / "api_keys.json"
ENV_PATH = BASE_DIR / ".env"

# ---------------------------------------------------------------------------
# Provider catalog — the single source of truth shared by backend and UI.
# ---------------------------------------------------------------------------
PROVIDERS: List[Dict[str, Any]] = [
    {
        "slug": "gemini",
        "label": "Google Gemini",
        "env": "GEMINI_API_KEY",
        "category": "LLM / Reasoning",
        "hint": "Starts with AIza… (Google AI Studio)",
        "prefix": "AIza",
        "used_by": ["ceo_core", "script_writing", "ebook_writing", "seo_agent"],
        "docs": "https://aistudio.google.com/app/apikey",
    },
    {
        "slug": "groq",
        "label": "Groq",
        "env": "GROQ_API_KEY",
        "category": "Fast LLM",
        "hint": "Starts with gsk_…",
        "prefix": "gsk_",
        "used_by": ["lead_generation", "seo_agent"],
        "docs": "https://console.groq.com/keys",
    },
    {
        "slug": "openai",
        "label": "OpenAI",
        "env": "OPENAI_API_KEY",
        "category": "LLM / TTS",
        "hint": "Starts with sk-…",
        "prefix": "sk-",
        "used_by": ["ebook_writing", "script_writing"],
        "docs": "https://platform.openai.com/api-keys",
    },
    {
        "slug": "elevenlabs",
        "label": "ElevenLabs",
        "env": "ELEVENLABS_API_KEY",
        "category": "Voice / Audio",
        "hint": "Voiceover + narration for edits",
        "prefix": "",
        "used_by": ["video_editing"],
        "docs": "https://elevenlabs.io/app/settings/api-keys",
    },
    {
        "slug": "heygen",
        "label": "HeyGen",
        "env": "HEYGEN_API_KEY",
        "category": "Video / Avatars",
        "hint": "Avatar + scene generation",
        "prefix": "",
        "used_by": ["video_generation"],
        "docs": "https://app.heygen.com/settings?nav=API",
    },
    {
        "slug": "youtube",
        "label": "YouTube Data API",
        "env": "YOUTUBE_API_KEY",
        "category": "Publishing",
        "hint": "Google Cloud → YouTube Data API v3",
        "prefix": "AIza",
        "used_by": ["seo_agent", "video_editing"],
        "docs": "https://console.cloud.google.com/apis/credentials",
    },
    {
        "slug": "facebook",
        "label": "Facebook Graph API",
        "env": "FACEBOOK_API_KEY",
        "category": "Publishing",
        "hint": "Meta app access token",
        "prefix": "",
        "used_by": ["lead_generation", "seo_agent"],
        "docs": "https://developers.facebook.com/tools/explorer/",
    },
]

PROVIDER_SLUGS = [p["slug"] for p in PROVIDERS]

DEFAULT_ASSIGNMENTS: Dict[str, str] = {
    "ceo_core": "gemini",
    "script_writing": "gemini",
    "video_generation": "heygen",
    "video_editing": "elevenlabs",
    "ebook_writing": "openai",
    "lead_generation": "groq",
    "seo_agent": "gemini",
}


def _mask(value: str) -> str:
    """Return a safe display form of a secret."""
    if not value:
        return ""
    tail = value[-4:] if len(value) > 8 else ""
    return "••••••••" + tail


def _read_env_file(path: Path) -> Dict[str, str]:
    data: Dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            data[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return data


class ApiKeyStore:
    """Thread-safe JSON key store with .env mirroring and per-agent routing."""

    def __init__(self, store_path: Path = STORE_PATH, env_path: Path = ENV_PATH) -> None:
        self.store_path = Path(store_path)
        self.env_path = Path(env_path)
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = {
            "version": 1,
            "keys": {},
            "assignments": dict(DEFAULT_ASSIGNMENTS),
            "updated_at": time.time(),
        }
        self._load()

    # -- persistence --------------------------------------------------------
    def _load(self) -> None:
        if not self.store_path.exists():
            return
        try:
            raw = json.loads(self.store_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(raw, dict):
            keys = raw.get("keys") if isinstance(raw.get("keys"), dict) else {}
            assignments = dict(DEFAULT_ASSIGNMENTS)
            assignments.update(raw.get("assignments") or {})
            self._data = {
                "version": raw.get("version", 1),
                "keys": keys,
                "assignments": assignments,
                "updated_at": raw.get("updated_at", time.time()),
            }

    def _save(self) -> None:
        self._data["updated_at"] = time.time()
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.store_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self.store_path)
        self._sync_env()

    def _sync_env(self) -> None:
        """Mirror stored keys into .env so standalone scripts can use them too."""
        env_lines = ["# Generated by config/api_keys.py — do not commit."]
        env_lines.append(f"# updated: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        for provider in PROVIDERS:
            value = self._raw(provider["slug"])
            env_lines.append(f"{provider['env']}={value}")
        env_lines.append("")
        try:
            self.env_path.write_text("\n".join(env_lines), encoding="utf-8")
            os.chmod(self.env_path, 0o600)
        except OSError:
            pass

    # -- reads --------------------------------------------------------------
    @staticmethod
    def catalog() -> List[Dict[str, Any]]:
        return [
            {
                "slug": p["slug"],
                "label": p["label"],
                "env": p["env"],
                "category": p["category"],
                "hint": p["hint"],
                "used_by": list(p["used_by"]),
                "docs": p["docs"],
            }
            for p in PROVIDERS
        ]

    def _raw(self, provider: str) -> str:
        entry = self._data["keys"].get(provider)
        if isinstance(entry, dict):
            return str(entry.get("value") or "")
        if isinstance(entry, str):  # tolerate hand-edited files
            return entry
        return ""

    def get_key(self, provider: str) -> Optional[str]:
        """Resolve a key: JSON store first, then process env, then .env file."""
        with self._lock:
            value = self._raw(provider)
        if value:
            return value
        env_name = next((p["env"] for p in PROVIDERS if p["slug"] == provider), None)
        if not env_name:
            return None
        from_env = os.environ.get(env_name)
        if from_env:
            return from_env.strip()
        file_env = _read_env_file(self.env_path).get(env_name)
        return file_env or None

    def is_configured(self, provider: str) -> bool:
        return bool(self.get_key(provider))

    def configured_slugs(self) -> List[str]:
        return [slug for slug in PROVIDER_SLUGS if self.is_configured(slug)]

    def assignments(self) -> Dict[str, str]:
        with self._lock:
            return dict(self._data.get("assignments") or {})

    def resolve_for_agent(self, agent_id: str) -> Tuple[Optional[str], Optional[str]]:
        """Return ``(provider_slug, key)`` for an agent, with sensible fallbacks."""
        assigned = self.assignments().get(agent_id)
        if assigned and self.get_key(assigned):
            return assigned, self.get_key(assigned)
        # fall back to any provider the agent naturally uses
        for provider in PROVIDERS:
            if agent_id in provider["used_by"] and self.get_key(provider["slug"]):
                return provider["slug"], self.get_key(provider["slug"])
        # last resort: first configured provider
        for slug in self.configured_slugs():
            return slug, self.get_key(slug)
        return None, None

    def masked_state(self) -> Dict[str, Any]:
        """Safe view for the UI: no raw secrets ever."""
        providers = []
        for p in self.catalog():
            raw = self.get_key(p["slug"]) or ""
            providers.append(
                {
                    **p,
                    "configured": bool(raw),
                    "masked": _mask(raw),
                    "length": len(raw),
                }
            )
        return {
            "providers": providers,
            "assignments": self.assignments(),
            "configured": self.configured_slugs(),
            "store": str(self.store_path),
            "updated_at": self._data.get("updated_at"),
        }

    # -- writes -------------------------------------------------------------
    def set_key(self, provider: str, value: str) -> Dict[str, Any]:
        if provider not in PROVIDER_SLUGS:
            raise ValueError(f"Unknown provider '{provider}'")
        value = (value or "").strip()
        if not value:
            raise ValueError("Key value cannot be empty")
        if re.search(r"\s", value):
            raise ValueError("Key must not contain whitespace")
        warnings: List[str] = []
        meta = next(p for p in PROVIDERS if p["slug"] == provider)
        prefix = meta.get("prefix") or ""
        if prefix and not value.startswith(prefix):
            warnings.append(f"Expected this key to start with '{prefix}'.")
        if len(value) < 16:
            warnings.append("Key looks unusually short — double-check it.")
        with self._lock:
            self._data["keys"][provider] = {
                "value": value,
                "updated_at": time.time(),
            }
            self._save()
        return {"provider": provider, "configured": True, "masked": _mask(value), "warnings": warnings}

    def delete_key(self, provider: str) -> Dict[str, Any]:
        if provider not in PROVIDER_SLUGS:
            raise ValueError(f"Unknown provider '{provider}'")
        with self._lock:
            self._data["keys"].pop(provider, None)
            self._save()
        return {"provider": provider, "configured": False}

    def set_assignment(self, agent_id: str, provider: str) -> Dict[str, Any]:
        if provider not in PROVIDER_SLUGS:
            raise ValueError(f"Unknown provider '{provider}'")
        with self._lock:
            self._data.setdefault("assignments", {})[agent_id] = provider
            self._save()
        return {"agent_id": agent_id, "provider": provider}
