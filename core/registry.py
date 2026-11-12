"""Department metadata + dynamic agent discovery.

The registry is the only place that knows which files exist in ``agents/``.
Adding a module later requires no edits here — just drop the file in.
"""

from __future__ import annotations

import importlib
import time
from typing import Any, Dict, List, Optional

from config.api_keys import ApiKeyStore
from core.base_agent import AgentBase
from core.events import EventBus
from core.store import STATE

# The organisational workflow, in pipeline order. The UI renders exactly this.
ORG: List[Dict[str, Any]] = [
    {
        "id": "script_writing",
        "name": "SCRIPT WRITING",
        "role": "Plot & Dialogue",
        "accent": "#7c9cff",
        "stage": "creation",
        "outputs": "scripts, story beats, dialogue",
    },
    {
        "id": "video_generation",
        "name": "VIDEO GENERATION",
        "role": "Scene & Animation",
        "accent": "#ff9f6e",
        "stage": "creation",
        "outputs": "scenes, avatars, motion",
    },
    {
        "id": "video_editing",
        "name": "VIDEO EDITING",
        "role": "Sequencing & Post",
        "accent": "#f7d774",
        "stage": "production",
        "outputs": "cuts, audio, final render",
    },
    {
        "id": "ebook_writing",
        "name": "EBOOK WRITING",
        "role": "Structure & Formatting",
        "accent": "#5ce0b3",
        "stage": "creation",
        "outputs": "outlines, chapters, export",
    },
    {
        "id": "lead_generation",
        "name": "LEAD GENERATION",
        "role": "Prospecting & Marketing",
        "accent": "#e785ff",
        "stage": "growth",
        "outputs": "prospects, outreach, campaigns",
    },
    {
        "id": "seo_agent",
        "name": "SEO AGENT",
        "role": "Keyword & On-Page SEO",
        "accent": "#4fd1ff",
        "stage": "growth",
        "outputs": "keywords, metadata, descriptions",
    },
]

ORG_BY_ID = {item["id"]: item for item in ORG}


class PendingAgent(AgentBase):
    """Placeholder for a department whose module has not been written yet."""

    module_status = "pending"

    def __init__(self, spec: Dict[str, Any], bus: EventBus, keys: ApiKeyStore, reason: str = "") -> None:
        super().__init__(bus, keys)
        self.id = spec["id"]
        self.name = spec["name"]
        self.role = spec["role"]
        self.accent = spec["accent"]
        self.outputs = spec.get("outputs", "")
        self.status = "offline"
        self.reason = reason

    async def handle(self, message: str, context: Optional[Dict[str, Any]] = None) -> str:
        self.record("user", message)
        reply = (
            f"{self.name} is registered in the org chart but its module is not built yet.\n\n"
            f"Planned file: agents/{self.id}.py\n"
            f"Planned scope: {self.outputs}\n\n"
            "Directives sent now are queued in this conversation only. The CEO CORE "
            "will route work here automatically the moment the module is live "
            "(see PROGRESS.md → Pending Modules)."
        )
        self.record("agent", reply)
        self.bus.log("warn", self.id, "instruction received, module still pending")
        return reply

    def snapshot(self) -> Dict[str, Any]:
        data = super().snapshot()
        data["reason"] = "module not built yet"
        return data


class AgentRegistry:
    def __init__(self, bus: EventBus, keys: ApiKeyStore) -> None:
        self.bus = bus
        self.keys = keys
        self.agents: Dict[str, AgentBase] = {}
        self.ceo: Optional[AgentBase] = None
        self._load()

    def _load(self) -> None:
        importlib.invalidate_caches()
        for spec in ORG:
            agent = self._build_department(spec)
            self.agents[spec["id"]] = agent
        self.ceo = self._build_ceo()
        live = [a.id for a in self.agents.values() if a.module_status == "live"]
        self.bus.log(
            "info",
            "system",
            f"organisation online — {len(live)}/{len(ORG)} departments live",
        )
        self._restore_state()

    def _restore_state(self) -> None:
        """Rehydrate chat histories and missions from data/state.json."""
        restored_messages = 0
        for agent in [self.ceo, *self.agents.values()]:
            if not agent:
                continue
            history = STATE.history(agent.id)
            if history:
                agent.history = history[-120:]
                restored_messages += len(history)

        missions = STATE.missions()
        if missions and self.ceo:
            interrupted = 0
            for mission in missions:
                if mission.get("status") == "in_progress":
                    mission["status"] = "interrupted"
                    mission["finished_at"] = time.time()
                    mission["note"] = "server restarted during execution"
                    interrupted += 1
                    for task in mission.get("tasks") or []:
                        if task.get("status") == "running":
                            task["status"] = "failed"
                            task["error"] = "server restarted during execution"
            self.ceo.missions = missions
            if interrupted:
                STATE.save_missions(missions)
            self.bus.log(
                "info",
                "system",
                f"state restored — {len(missions)} mission(s), {restored_messages} message(s)"
                + (f", {interrupted} interrupted run(s)" if interrupted else ""),
            )
        elif restored_messages:
            self.bus.log(
                "info",
                "system",
                f"chat history restored — {restored_messages} message(s)",
            )

        restored_artifacts = 0
        for agent in [self.ceo, *self.agents.values()]:
            if not agent or not getattr(agent, "persisted_artifacts", None):
                continue
            try:
                agent.restore_artifacts()
            except Exception as exc:  # a corrupt artifact must not block startup
                self.bus.log("warn", "system", f"could not restore {agent.id} artifacts: {exc}")
                continue
            if any(STATE.artifact(agent.id, key) is not None for key in agent.persisted_artifacts):
                restored_artifacts += 1
        if restored_artifacts:
            self.bus.log(
                "info",
                "system",
                f"working artifacts restored — {restored_artifacts} department(s) can continue previous work",
            )

    def _build_department(self, spec: Dict[str, Any]) -> AgentBase:
        module_path = f"agents.{spec['id']}"
        try:
            module = importlib.import_module(module_path)
        except ModuleNotFoundError:
            return PendingAgent(spec, self.bus, self.keys)
        except Exception as exc:  # broken module must not take the app down
            self.bus.log("error", spec["id"], f"failed to import: {exc}")
            return PendingAgent(spec, self.bus, self.keys, reason=str(exc))
        try:
            agent = module.build(self.bus, self.keys)
        except Exception as exc:
            self.bus.log("error", spec["id"], f"build() failed: {exc}")
            return PendingAgent(spec, self.bus, self.keys, reason=str(exc))
        agent.registry = self
        return agent

    def _build_ceo(self) -> AgentBase:
        module = importlib.import_module("agents.ceo_core")
        ceo = module.build(self.bus, self.keys)
        ceo.registry = self
        return ceo

    # -- queries ------------------------------------------------------------
    def get(self, agent_id: str) -> Optional[AgentBase]:
        if agent_id == "ceo_core":
            return self.ceo
        return self.agents.get(agent_id)

    def is_live(self, agent_id: str) -> bool:
        agent = self.get(agent_id)
        return bool(agent and agent.module_status == "live")

    def all_agents(self) -> List[AgentBase]:
        return [a for a in self.agents.values()]

    def specs(self) -> List[Dict[str, Any]]:
        merged = []
        for spec in ORG:
            agent = self.agents.get(spec["id"])
            item = dict(spec)
            item["module_status"] = agent.module_status if agent else "pending"
            merged.append(item)
        return merged

    def snapshot(self) -> Dict[str, Any]:
        return {
            "hub": self.ceo.snapshot() if self.ceo else None,
            "agents": [a.snapshot() for a in self.all_agents()],
            "org": self.specs(),
        }
