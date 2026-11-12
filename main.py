"""CogniSphere Labs — FastAPI entry point (Termux friendly).

Run:
    python main.py          # http://0.0.0.0:8000
    PORT=8080 python main.py
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from config.api_keys import ApiKeyStore
from core.events import EventBus
from core.registry import AgentRegistry
from core.store import STATE

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"

bus = EventBus()
keys = ApiKeyStore()
registry = AgentRegistry(bus, keys)

app = FastAPI(
    title="CogniSphere Labs",
    version="0.1.0",
    description="Modular Multi-Agent AI Company — Phase 1 foundation",
)

# Local-first tool: allow the Termux device / LAN browser to reach the API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

if FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIR)), name="assets")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class ChatIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    context: Optional[Dict[str, Any]] = None


class KeyIn(BaseModel):
    provider: str
    value: str = Field(..., min_length=1, max_length=4096)


class AssignmentIn(BaseModel):
    agent_id: str
    provider: str


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
def build_state(since_log: int = 0) -> Dict[str, Any]:
    org = registry.snapshot()
    return {
        "revision": bus.revision,
        "hub": org["hub"],
        "agents": org["agents"],
        "org": org["org"],
        "missions": registry.ceo.missions[-10:] if registry.ceo else [],
        "logs": bus.snapshot(since_log),
        "settings": {
            "configured": keys.configured_slugs(),
            "assignments": keys.assignments(),
        },
        "server_time": __import__("time").time(),
    }


@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "app": "CogniSphere Labs",
        "phase": 7,
        "departments_live": sum(1 for a in registry.all_agents() if a.module_status == "live"),
        "departments_total": len(registry.all_agents()),
        "keys_configured": keys.configured_slugs(),
        "persistence": STATE.summary(),
    }


@app.get("/api/state")
def state(since_log: int = 0) -> Dict[str, Any]:
    return build_state(since_log)


@app.get("/api/agents")
def agents() -> Dict[str, Any]:
    return {"hub": registry.ceo.snapshot() if registry.ceo else None,
            "agents": [a.snapshot() for a in registry.all_agents()]}


@app.get("/api/agents/{agent_id}/chat")
def chat_history(agent_id: str) -> Dict[str, Any]:
    agent = registry.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Unknown agent '{agent_id}'")
    return {"agent_id": agent_id, "messages": agent.history}


@app.post("/api/agents/{agent_id}/chat")
async def chat(agent_id: str, payload: ChatIn) -> Dict[str, Any]:
    agent = registry.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Unknown agent '{agent_id}'")
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="message cannot be empty")

    if agent_id != "ceo_core":
        previous = agent.status
        agent.set_state(status="working" if agent.module_status == "live" else agent.status)
        try:
            reply = await agent.handle(message, payload.context or {})
        except Exception as exc:  # surface, don't swallow
            agent.set_state(status="error")
            bus.log("error", agent_id, f"handler failed: {exc}")
            raise HTTPException(status_code=500, detail=f"{agent_id} failed: {exc}") from exc
        if agent.module_status == "live" and agent.status == "working":
            agent.set_state(status="idle")
        elif agent.module_status != "live":
            agent.set_state(status=previous)
    else:
        try:
            reply = await agent.handle(message, payload.context or {})
        except Exception as exc:
            bus.log("error", agent_id, f"handler failed: {exc}")
            raise HTTPException(status_code=500, detail=f"{agent_id} failed: {exc}") from exc

    return {"agent_id": agent_id, "reply": reply, "state": build_state()}


@app.post("/api/ceo/dispatch")
async def ceo_dispatch(payload: ChatIn) -> Dict[str, Any]:
    """Convenience endpoint for automation: same path as chatting to CEO CORE."""
    return await chat("ceo_core", payload)


@app.get("/api/settings")
def get_settings() -> Dict[str, Any]:
    return keys.masked_state()


@app.post("/api/settings/keys")
def save_key(payload: KeyIn) -> Dict[str, Any]:
    try:
        result = keys.set_key(payload.provider, payload.value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    provider_label = next(
        (p["label"] for p in keys.catalog() if p["slug"] == payload.provider), payload.provider
    )
    bus.log("success", "settings", f"{provider_label} key saved")
    return {"result": result, "settings": keys.masked_state()}


@app.delete("/api/settings/keys/{provider}")
def remove_key(provider: str) -> Dict[str, Any]:
    try:
        result = keys.delete_key(provider)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    bus.log("warn", "settings", f"{provider} key removed")
    return {"result": result, "settings": keys.masked_state()}


@app.post("/api/settings/assignments")
def assign_key(payload: AssignmentIn) -> Dict[str, Any]:
    if not registry.get(payload.agent_id):
        raise HTTPException(status_code=404, detail=f"Unknown agent '{payload.agent_id}'")
    try:
        result = keys.set_assignment(payload.agent_id, payload.provider)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    bus.log("info", "settings", f"{payload.agent_id} → {payload.provider}")
    return {"result": result, "settings": keys.masked_state()}


@app.post("/api/system/reset")
def reset() -> Dict[str, Any]:
    """Clear chat history + monitor log (keys are untouched)."""
    for agent in registry.all_agents():
        agent.history.clear()
    if registry.ceo:
        registry.ceo.history.clear()
        registry.ceo.missions.clear()
        registry.ceo.set_state(status="online", progress=0, current_task="")
    bus.clear()
    STATE.clear()
    bus.log("info", "system", "session state reset — API keys preserved, persisted chats cleared")
    return build_state()


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------
@app.get("/")
def index() -> FileResponse:
    target = FRONTEND_DIR / "index.html"
    if not target.exists():
        raise HTTPException(status_code=500, detail="frontend/index.html missing")
    return FileResponse(target)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        reload=False,
    )
