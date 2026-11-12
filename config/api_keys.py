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
import uuid
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

    MAX_KEYS_PER_AGENT = 8

    def __init__(self, store_path: Path = STORE_PATH, env_path: Path = ENV_PATH) -> None:
        self.store_path = Path(store_path)
        self.env_path = Path(env_path)
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = {
            "version": 2,
            "keys": {},
            "assignments": dict(DEFAULT_ASSIGNMENTS),
            "agent_keys": {},
            "agent_active": {},
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
            agent_keys = raw.get("agent_keys") if isinstance(raw.get("agent_keys"), dict) else {}
            clean_agent_keys: Dict[str, List[Dict[str, Any]]] = {}
            for agent_id, entries in agent_keys.items():
                if not isinstance(entries, list):
                    continue
                clean_agent_keys[str(agent_id)] = [
                    entry for entry in entries
                    if isinstance(entry, dict) and entry.get("provider") in PROVIDER_SLUGS
                    and str(entry.get("value") or "").strip()
                ]
            agent_active = raw.get("agent_active") if isinstance(raw.get("agent_active"), dict) else {}
            self._data = {
                "version": raw.get("version", 2),
                "keys": keys,
                "assignments": assignments,
                "agent_keys": clean_agent_keys,
                "agent_active": {str(k): str(v) for k, v in agent_active.items()},
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

    def _legacy_resolve(self, agent_id: str) -> Tuple[Optional[str], Optional[str]]:
        """Assigned provider, then a natural provider, then any configured one."""
        assigned = self.assignments().get(agent_id)
        if assigned and self.get_key(assigned):
            return assigned, self.get_key(assigned)
        for provider in PROVIDERS:
            if agent_id in provider["used_by"] and self.get_key(provider["slug"]):
                return provider["slug"], self.get_key(provider["slug"])
        for slug in self.configured_slugs():
            return slug, self.get_key(slug)
        return None, None

    def resolve_for_agent(self, agent_id: str) -> Tuple[Optional[str], Optional[str]]:
        """Return the agent's active pool key, else the legacy global routing."""
        entry = self._active_entry(agent_id)
        if entry:
            return str(entry.get("provider")), str(entry.get("value"))
        return self._legacy_resolve(agent_id)

    # -- per-agent key pools ------------------------------------------------
    def _entries(self, agent_id: str) -> List[Dict[str, Any]]:
        return list(self._data.get("agent_keys", {}).get(agent_id) or [])

    def _active_entry(self, agent_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            entries = self._entries(agent_id)
            if not entries:
                return None
            active_id = self._data.get("agent_active", {}).get(agent_id)
            for entry in entries:
                if entry.get("id") == active_id:
                    return entry
            return entries[0]

    @staticmethod
    def _public_key(entry: Dict[str, Any], active_id: Optional[str]) -> Dict[str, Any]:
        value = str(entry.get("value") or "")
        return {
            "id": entry.get("id"),
            "provider": entry.get("provider"),
            "label": entry.get("label") or "",
            "masked": _mask(value),
            "length": len(value),
            "active": entry.get("id") == active_id,
            "added_at": entry.get("added_at"),
            "updated_at": entry.get("updated_at"),
        }

    def agent_key_state(self, agent_id: str) -> Dict[str, Any]:
        with self._lock:
            entries = self._entries(agent_id)
            active_id = self._data.get("agent_active", {}).get(agent_id)
            if entries and not any(entry.get("id") == active_id for entry in entries):
                active_id = entries[0].get("id")
            return {
                "agent_id": agent_id,
                "provider": self.assignments().get(agent_id)
                or DEFAULT_ASSIGNMENTS.get(agent_id)
                or "",
                "active_id": active_id if entries else None,
                "count": len(entries),
                "max_keys": self.MAX_KEYS_PER_AGENT,
                "keys": [self._public_key(entry, active_id) for entry in entries],
            }

    def agent_key_summary(self) -> Dict[str, Any]:
        with self._lock:
            counts = {
                agent_id: len(entries)
                for agent_id, entries in (self._data.get("agent_keys") or {}).items()
                if entries
            }
        return {"counts": counts, "total": sum(counts.values())}

    def candidates(
        self, agent_id: str, allowed_providers: Optional[List[str]] = None
    ) -> List[Tuple[str, str]]:
        """Ordered ``(provider, key)`` attempts: active key, other pool keys, legacy."""
        with self._lock:
            entries = self._entries(agent_id)
            active_id = self._data.get("agent_active", {}).get(agent_id)
            ordered = sorted(
                entries,
                key=lambda entry: (entry.get("id") != active_id, entry.get("added_at") or 0),
            )
            legacy = self._legacy_resolve(agent_id)
        allowed = set(allowed_providers) if allowed_providers else None
        result: List[Tuple[str, str]] = []
        seen: set = set()
        for entry in ordered:
            provider = str(entry.get("provider") or "")
            value = str(entry.get("value") or "")
            if allowed is not None and provider not in allowed:
                continue
            if not value or value in seen:
                continue
            seen.add(value)
            result.append((provider, value))
        provider, key = legacy
        if provider and key and (allowed is None or provider in allowed) and key not in seen:
            result.append((provider, key))
        return result

    @staticmethod
    def _clean_secret(provider: str, value: str) -> str:
        if provider not in PROVIDER_SLUGS:
            raise ValueError(f"Unknown provider '{provider}'")
        value = (value or "").strip()
        if not value:
            raise ValueError("Key value cannot be empty")
        if re.search(r"\s", value):
            raise ValueError("Key must not contain whitespace")
        if len(value) > 4096:
            raise ValueError("Key is too long")
        return value

    @staticmethod
    def _key_warnings(provider: str, value: str) -> List[str]:
        warnings: List[str] = []
        meta = next((p for p in PROVIDERS if p["slug"] == provider), None)
        prefix = (meta or {}).get("prefix") or ""
        if prefix and not value.startswith(prefix):
            warnings.append(f"Expected this key to start with '{prefix}'.")
        if len(value) < 16:
            warnings.append("Key looks unusually short — double-check it.")
        return warnings

    def add_agent_key(
        self, agent_id: str, provider: str, value: str, label: str = ""
    ) -> Dict[str, Any]:
        value = self._clean_secret(provider, value)
        warnings = self._key_warnings(provider, value)
        with self._lock:
            entries = self._data.setdefault("agent_keys", {}).setdefault(agent_id, [])
            if any(str(entry.get("value")) == value for entry in entries):
                raise ValueError("That key is already stored for this agent")
            if len(entries) >= self.MAX_KEYS_PER_AGENT:
                raise ValueError(f"Limit is {self.MAX_KEYS_PER_AGENT} keys per agent")
            entry = {
                "id": uuid.uuid4().hex[:8],
                "provider": provider,
                "label": (label or "").strip()[:40],
                "value": value,
                "added_at": time.time(),
                "updated_at": time.time(),
            }
            entries.append(entry)
            self._data.setdefault("agent_active", {})[agent_id] = entry["id"]
            self._data.setdefault("assignments", {})[agent_id] = provider
            self._save()
            return {
                "entry": self._public_key(entry, entry["id"]),
                "warnings": warnings,
                "state": self.agent_key_state(agent_id),
            }

    def update_agent_key(
        self,
        agent_id: str,
        key_id: str,
        value: Optional[str] = None,
        provider: Optional[str] = None,
        label: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            entries = self._entries(agent_id)
            entry = next((item for item in entries if item.get("id") == key_id), None)
            if entry is None:
                raise ValueError("Unknown key for this agent")
            if provider is not None and provider != entry.get("provider"):
                if provider not in PROVIDER_SLUGS:
                    raise ValueError(f"Unknown provider '{provider}'")
                entry["provider"] = provider
            if value is not None:
                cleaned = self._clean_secret(str(entry.get("provider")), value)
                if any(item is not entry and str(item.get("value")) == cleaned for item in entries):
                    raise ValueError("That key is already stored for this agent")
                entry["value"] = cleaned
            if label is not None:
                entry["label"] = (label or "").strip()[:40]
            entry["updated_at"] = time.time()
            self._save()
            return self.agent_key_state(agent_id)

    def delete_agent_key(self, agent_id: str, key_id: str) -> Dict[str, Any]:
        with self._lock:
            entries = self._entries(agent_id)
            remaining = [entry for entry in entries if entry.get("id") != key_id]
            if len(remaining) == len(entries):
                raise ValueError("Unknown key for this agent")
            if remaining:
                self._data.setdefault("agent_keys", {})[agent_id] = remaining
            else:
                self._data.get("agent_keys", {}).pop(agent_id, None)
            if self._data.get("agent_active", {}).get(agent_id) == key_id:
                if remaining:
                    self._data["agent_active"][agent_id] = remaining[0].get("id")
                else:
                    self._data.get("agent_active", {}).pop(agent_id, None)
            self._save()
            return self.agent_key_state(agent_id)

    def activate_agent_key(self, agent_id: str, key_id: str) -> Dict[str, Any]:
        with self._lock:
            entry = next(
                (item for item in self._entries(agent_id) if item.get("id") == key_id), None
            )
            if entry is None:
                raise ValueError("Unknown key for this agent")
            self._data.setdefault("agent_active", {})[agent_id] = key_id
            self._data.setdefault("assignments", {})[agent_id] = entry.get("provider")
            self._save()
            return self.agent_key_state(agent_id)

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
            "agent_keys": self.agent_key_summary(),
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
