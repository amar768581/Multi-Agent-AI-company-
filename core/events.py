"""In-memory event bus + live monitor log.

Deliberately tiny: an ordered log buffer and a revision counter the frontend
polls. No external broker, no persistence — safe for Termux.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List


class EventBus:
    def __init__(self, limit: int = 400) -> None:
        self._logs: Deque[Dict[str, Any]] = deque(maxlen=limit)
        self._lock = threading.Lock()
        self._seq = 0
        self.revision = 0

    def log(self, level: str, source: str, message: str) -> Dict[str, Any]:
        with self._lock:
            self._seq += 1
            self.revision += 1
            entry = {
                "seq": self._seq,
                "level": level,
                "source": source,
                "message": message,
                "at": time.time(),
            }
            self._logs.append(entry)
            return entry

    def bump(self) -> None:
        """Signal a state change that is not itself a log line."""
        with self._lock:
            self.revision += 1

    def snapshot(self, since: int = 0) -> List[Dict[str, Any]]:
        with self._lock:
            if since:
                return [e for e in self._logs if e["seq"] > since]
            return list(self._logs)

    def clear(self) -> None:
        with self._lock:
            self._logs.clear()
            self.revision += 1
