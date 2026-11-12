"""Persistent JSON state for chat histories, missions and deliverables.

Production rule: UTF-8 everywhere, ``ensure_ascii=False`` so Bengali survives,
atomic replace so a crash cannot truncate the file. No database needed for a
single-user Termux app — one file under ``data/`` is enough.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
STATE_PATH = DATA_DIR / "state.json"


class StateStore:
    def __init__(self, path: Path = STATE_PATH) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = self._empty()
        self.load()

    @staticmethod
    def _empty() -> Dict[str, Any]:
        return {
            "version": 1,
            "agents": {},
            "missions": [],
            "artifacts": {},
            "updated_at": time.time(),
        }

    # -- persistence --------------------------------------------------------
    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(raw, dict):
            return
        agents = raw.get("agents") if isinstance(raw.get("agents"), dict) else {}
        missions = raw.get("missions") if isinstance(raw.get("missions"), list) else []
        artifacts = raw.get("artifacts") if isinstance(raw.get("artifacts"), dict) else {}
        self._data = {
            "version": raw.get("version", 1),
            "agents": agents,
            "missions": missions,
            "artifacts": artifacts,
            "updated_at": raw.get("updated_at", time.time()),
        }

    def _save(self) -> None:
        self._data["updated_at"] = time.time()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self.path)

    # -- chat histories -----------------------------------------------------
    def save_history(self, agent_id: str, history: List[Dict[str, Any]]) -> None:
        with self._lock:
            self._data.setdefault("agents", {})[agent_id] = history[-120:]
            self._save()

    def history(self, agent_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            items = self._data.get("agents", {}).get(agent_id) or []
            return [entry for entry in items if isinstance(entry, dict)]

    # -- missions / deliverables -------------------------------------------
    def save_missions(self, missions: List[Dict[str, Any]]) -> None:
        with self._lock:
            self._data["missions"] = missions[-20:]
            self._save()

    def missions(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [m for m in (self._data.get("missions") or []) if isinstance(m, dict)]

    # -- department working artifacts --------------------------------------
    def save_artifact(self, agent_id: str, key: str, value: Any) -> None:
        """Persist one working artifact (last script/book/campaign/…)."""
        with self._lock:
            bucket = self._data.setdefault("artifacts", {}).setdefault(agent_id, {})
            if value is None or value == "" or value == [] or value == {}:
                bucket.pop(key, None)
            else:
                bucket[key] = value
            if not bucket:
                self._data["artifacts"].pop(agent_id, None)
            self._save()

    def artifact(self, agent_id: str, key: str) -> Optional[Any]:
        with self._lock:
            return (self._data.get("artifacts", {}).get(agent_id) or {}).get(key)

    def artifacts(self, agent_id: str) -> Dict[str, Any]:
        with self._lock:
            return dict(self._data.get("artifacts", {}).get(agent_id) or {})

    # -- reset --------------------------------------------------------------
    def clear(self) -> None:
        with self._lock:
            self._data = self._empty()
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass

    # -- diagnostics --------------------------------------------------------
    def summary(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "path": str(self.path),
                "agents_with_history": len([k for k, v in self._data.get("agents", {}).items() if v]),
                "missions": len(self._data.get("missions") or []),
                "agents_with_artifacts": len(self._data.get("artifacts") or {}),
                "bytes": self.path.stat().st_size if self.path.exists() else 0,
            }


STATE = StateStore()
