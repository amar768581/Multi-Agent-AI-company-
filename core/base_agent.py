"""Shared agent contract.

Every file in ``agents/`` subclasses :class:`AgentBase` and exposes
``build(bus, keys)``. Nothing in here knows about a specific department.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

from config.api_keys import ApiKeyStore
from core.events import EventBus
from core.store import STATE


class AgentBase:
    id: str = "agent"
    name: str = "Agent"
    role: str = ""
    accent: str = "#39d3ff"
    providers: List[str] = []
    module_status: str = "live"  # "live" | "pending"
    # Artifacts that must survive a restart: state key → attribute name.
    # A department declares this and the base class handles persistence.
    persisted_artifacts: Dict[str, str] = {}

    def __init__(self, bus: EventBus, keys: ApiKeyStore) -> None:
        self.bus = bus
        self.keys = keys
        self.registry: Any = None  # injected by core.registry.AgentRegistry
        self.status: str = "idle"
        self.progress: int = 0
        self.current_task: str = ""
        self.history: List[Dict[str, Any]] = []
        self.lang: str = "en"  # "en" | "bn" | "banglish" for the latest request
        self.created_at = time.time()

    # -- observability ------------------------------------------------------
    def log(self, message: str, level: str = "info") -> None:
        self.bus.log(level, self.id, message)

    def set_state(
        self,
        status: Optional[str] = None,
        progress: Optional[int] = None,
        current_task: Optional[str] = None,
    ) -> None:
        if status is not None:
            self.status = status
        if progress is not None:
            self.progress = max(0, min(100, int(progress)))
        if current_task is not None:
            self.current_task = current_task
        self.bus.bump()

    def record(self, role: str, text: str) -> Dict[str, Any]:
        entry = {
            "id": uuid.uuid4().hex[:10],
            "role": role,
            "text": text,
            "at": time.time(),
        }
        self.history.append(entry)
        self.history = self.history[-120:]
        STATE.save_history(self.id, self.history)
        return entry

    # -- durable working artifacts -----------------------------------------
    def persist_artifacts(self) -> None:
        """Write this agent's declared artifacts to data/state.json."""
        for key, attribute in self.persisted_artifacts.items():
            STATE.save_artifact(self.id, key, getattr(self, attribute, None))
        STATE.save_artifact(self.id, "lang", self.lang)

    def restore_artifacts(self) -> None:
        """Rehydrate declared artifacts saved by a previous session."""
        for key, attribute in self.persisted_artifacts.items():
            value = STATE.artifact(self.id, key)
            if value is not None:
                setattr(self, attribute, value)
        if self.persisted_artifacts:
            self.lang = STATE.artifact(self.id, "lang") or self.lang

    def key_status(self) -> Dict[str, Any]:
        provider, key = self.keys.resolve_for_agent(self.id)
        return {"provider": provider, "configured": bool(key)}

    def snapshot(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "accent": self.accent,
            "status": self.status,
            "progress": self.progress,
            "current_task": self.current_task,
            "providers": list(self.providers),
            "module_status": self.module_status,
            "key": self.key_status(),
        }

    # -- behaviour ----------------------------------------------------------
    async def handle(self, message: str, context: Optional[Dict[str, Any]] = None) -> str:
        raise NotImplementedError
