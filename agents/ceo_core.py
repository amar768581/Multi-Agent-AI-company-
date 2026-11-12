"""CEO CORE — central orchestrator, strategic directives & mission execution.

Owns:
  * intent routing in English, Bengali (বাংলা) and Banglish (Romanized Bengali),
  * mission plans across the 6 departments (in workflow order),
  * approval + background execution of the plan, department by department,
  * the compliance gate step and the final deliverables report,
  * persistence of missions/deliverables into ``data/state.json``.

Execution runs as an ``asyncio.Task`` so the HTTP request returns immediately;
the animated UI follows progress by polling ``/api/state``.

Department agents are never imported here; the registry is used so a missing
module degrades to "blocked" instead of breaking the pipeline.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Dict, List, Optional

from config.api_keys import ApiKeyStore
from core.base_agent import AgentBase
from core.events import EventBus
from core.language import detect as detect_language, label, normalize_commands
from core.store import STATE

AGENT_SPEC = {
    "id": "ceo_core",
    "name": "CEO CORE",
    "role": "Central Orchestrator",
    "accent": "#34e0ff",
}

# Keywords → departments. Order inside a plan is controlled by PIPELINE_ORDER.
INTENT_MAP: Dict[str, List[str]] = {
    "script_writing": [
        "script", "plot", "dialogue", "story", "screenplay", "narration", "hook",
        "স্ক্রিপ্ট", "গল্প", "সংলাপ", "স্ক্রিনপ্লে", "golpo", "sanglap",
    ],
    "video_generation": [
        "video", "animate", "animation", "scene", "avatar", "heygen", "b-roll", "broll",
        "ভিডিও", "অ্যানিমেশন", "দৃশ্য", "অ্যাভাটার", "bideo",
    ],
    "video_editing": [
        "edit", "editing", "sequence", "post-production", "post production", "cut",
        "voiceover", "voice over", "subtitle", "render",
        "এডিট", "এডিটিং", "সম্পাদনা", "ভয়েসওভার", "রেন্ডার", "সাবটাইটেল", "sampadona",
    ],
    "ebook_writing": [
        "ebook", "e-book", "book", "chapter", "manuscript", "kindle", "guide",
        "ইবুক", "ই-বুক", "বই", "চ্যাপ্টার", "অধ্যায়", "গাইড", "boi", "odhyay",
    ],
    "lead_generation": [
        "lead", "prospect", "outreach", "marketing", "campaign", "funnel", "client",
        "cold email", "dm",
        "লিড", "প্রসপেক্ট", "গ্রাহক", "মার্কেটিং", "ক্যাম্পেইন", "আউটরিচ", "lid",
        "grahok", "prospect",
    ],
    "seo_agent": [
        "seo", "keyword", "ranking", "rank", "metadata", "tags", "thumbnail text",
        "description", "search",
        "এসইও", "কীওয়ার্ড", "র‍্যাংকিং", "মেটাডেটা", "ট্যাগ", "বর্ণনা", "কিওয়ার্ড",
    ],
}

PIPELINE_ORDER = [
    "script_writing",
    "video_generation",
    "video_editing",
    "ebook_writing",
    "lead_generation",
    "seo_agent",
]

FULL_PIPELINE_TRIGGERS = [
    "full pipeline", "whole company", "everything", "company-wide", "campaign",
    "ফুল পাইপলাইন", "সব বিভাগ", "পুরো কোম্পানি", "সম্পূর্ণ পাইপলাইন",
]

# snapshot key each department exposes after a successful run
DELIVERABLE_KEYS = {
    "script_writing": "script",
    "video_generation": "video",
    "video_editing": "edit",
    "ebook_writing": "book",
    "lead_generation": "campaign",
    "seo_agent": "seo",
}

STEP_PAUSE_SECONDS = 0.55

STATUS_WORDS = {
    "en": {
        "completed": "COMPLETED", "partial": "PARTIAL", "blocked": "BLOCKED",
        "failed": "FAILED", "in_progress": "RUNNING", "interrupted": "INTERRUPTED",
        "planned": "PLANNED", "cancelled": "CANCELLED",
    },
    "bn": {
        "completed": "সম্পন্ন", "partial": "আংশিক", "blocked": "ব্লকড",
        "failed": "ব্যর্থ", "in_progress": "চলছে", "interrupted": "বাধাপ্রাপ্ত",
        "planned": "পরিকল্পিত", "cancelled": "বাতিল",
    },
    "banglish": {
        "completed": "Somporno", "partial": "Angshik", "blocked": "Blocked",
        "failed": "Byartho", "in_progress": "Cholche", "interrupted": "Interrupted",
        "planned": "Plan kora", "cancelled": "Batil",
    },
}


def _compliance_gate_live() -> bool:
    """The compliance gate is a library, not an agent, so it has no registry entry."""
    try:
        import compliance.monetization_rules  # noqa: F401
        return True
    except Exception:
        return False


class CEOAgent(AgentBase):
    id = "ceo_core"
    name = "CEO CORE"
    role = "Central Orchestrator"
    accent = "#34e0ff"
    providers = ["gemini", "openai"]

    def __init__(self, bus: EventBus, keys: ApiKeyStore) -> None:
        super().__init__(bus, keys)
        self.status = "online"
        self.missions: List[Dict[str, Any]] = []
        self._runner: Optional[asyncio.Task] = None
        self.running_mission_id: Optional[str] = None

    # -- language helpers ---------------------------------------------------
    def _status_word(self, lang: str, status: str) -> str:
        return (STATUS_WORDS.get(lang) or STATUS_WORDS["en"]).get(status, status)

    def _persist(self) -> None:
        STATE.save_missions(self.missions)

    # -- public entry -------------------------------------------------------
    async def handle(self, message: str, context: Optional[Dict[str, Any]] = None) -> str:
        text = (message or "").strip()
        if not text:
            return "Give me a directive and I will route it to the right department."
        self.record("user", text)
        self.lang = detect_language(text)
        low = normalize_commands(text).lower()

        if low.startswith(("approve", "approved", "green light", "greenlight")):
            reply = self._decide(low, approve=True)
        elif low.startswith(("run msn", "execute msn", "run mission", "execute mission")):
            reply = self._decide(low, approve=True)
        elif low.startswith(("reject", "hold", "decline")):
            reply = self._decide(low, approve=False)
        elif any(word in low for word in ["status", "report", "progress", "where are we", "summary"]):
            reply = self._report()
        else:
            reply = self._create_mission(text, low)

        self.record("agent", reply)
        self.bus.bump()
        return reply

    # -- planning -----------------------------------------------------------
    @staticmethod
    def _mentions(text: str, word: str) -> bool:
        """Whole-word match with simple plural tolerance (no 'book' in 'bookkeeping')."""
        return bool(re.search(rf"\b{re.escape(word)}(?:s|es)?\b", text))

    def _match_departments(self, low: str) -> List[str]:
        if any(trigger in low for trigger in FULL_PIPELINE_TRIGGERS):
            return ["script_writing", "video_generation", "video_editing", "ebook_writing",
                    "lead_generation", "seo_agent"]
        matched: List[str] = []
        for agent_id, words in INTENT_MAP.items():
            if any(self._mentions(low, word) for word in words):
                matched.append(agent_id)
        # A video ask implies a script upstream, an edit downstream, and SEO metadata last.
        if "video_generation" in matched or "video_editing" in matched:
            if "script_writing" not in matched:
                matched.append("script_writing")
            if "video_generation" in matched and "video_editing" not in matched:
                matched.append("video_editing")
            if "seo_agent" not in matched:
                matched.append("seo_agent")
        if "ebook_writing" in matched and "seo_agent" not in matched:
            matched.append("seo_agent")
        if not matched:
            matched = ["script_writing", "seo_agent"]
        return [a for a in PIPELINE_ORDER if a in matched]

    def _create_mission(self, text: str, low: str) -> str:
        departments = self._match_departments(low)
        mission_id = f"MSN-{len(self.missions) + 1:03d}"
        tasks: List[Dict[str, Any]] = []
        for index, agent_id in enumerate(departments, start=1):
            live = bool(self.registry and self.registry.is_live(agent_id))
            meta = self._org_meta(agent_id)
            tasks.append(
                {
                    "id": f"{mission_id}-T{index}",
                    "agent": agent_id,
                    "label": f"{meta.get('name', agent_id)} — {meta.get('role', '')}".strip(" —"),
                    "status": "queued" if live else "awaiting_module",
                }
            )
        # Every pathway ends at the compliance gate, including outreach-only missions.
        if tasks:
            tasks.append(
                {
                    "id": f"{mission_id}-T{len(tasks) + 1}",
                    "agent": "compliance",
                    "label": "Monetization compliance gate (YouTube + Facebook)",
                    "status": "queued" if _compliance_gate_live() else "awaiting_module",
                }
            )

        awaiting = [t for t in tasks if t["status"] == "awaiting_module"]
        mission = {
            "id": mission_id,
            "objective": text[:400],
            "lang": self.lang,
            "created_at": time.time(),
            "status": "awaiting_modules" if awaiting else "planned",
            "approval": "pending",
            "progress": 0,
            "tasks": tasks,
            "deliverables": [],
            "compliance": None,
        }
        self.missions.append(mission)
        self._persist()

        self.set_state(
            status="online",
            progress=0,
            current_task=f"{mission_id} planned — approval pending",
        )
        self.log(f"mission {mission_id} created with {len(tasks)} tasks ({self.lang})", "info")
        if awaiting:
            self.log(f"{len(awaiting)} task(s) blocked on unbuilt modules", "warn")
        return self._plan_reply(mission)

    def _plan_reply(self, mission: Dict[str, Any]) -> str:
        lang = mission.get("lang", "en")
        tasks = mission["tasks"]
        bn = lang == "bn"
        lines = [
            f"{'ডিরেক্টিভ গৃহীত' if bn else 'DIRECTIVE ACCEPTED'} — {mission['id']}",
            "",
            f"{'লক্ষ্য' if bn else 'Objective'}: {mission['objective']}",
            "",
            "পরিকল্পনা (কাজের ক্রমে):" if bn else "Plan (workflow order):",
        ]
        for task in tasks:
            marker = "•" if task["status"] == "queued" else "○"
            state = ("কিউতে" if bn else "queued") if task["status"] == "queued" else ("মডিউল অপেক্ষমাণ" if bn else "module pending")
            lines.append(f"  {marker} {task['label']}  [{state}]")
        lines.append("")
        awaiting = [t for t in tasks if t["status"] == "awaiting_module"]
        if awaiting:
            lines.append(
                f"{len(awaiting)} ধাপ এখনো চালানো যাবে না — চালানোর সময় সেগুলো ব্লকড হিসেবে রিপোর্ট হবে।"
                if bn
                else f"{len(awaiting)} step(s) cannot execute yet — they will be reported as blocked when the mission runs."
            )
        if bn:
            lines.append(
                f"অনুমোদনের অপেক্ষায়। চালু করতে লিখুন: 'অনুমোদন {mission['id']}'।\n"
                "পাইপলাইন ব্যাকগ্রাউন্ডে চলবে এবং ইউআই-তে লাইভ দেখা যাবে।"
            )
        elif lang == "banglish":
            lines.append(
                f"Approval baki. Chalu korte likho: 'approve {mission['id']}'.\n"
                "Pipeline background e cholbe, UI te live dekhbe."
            )
        else:
            lines.append(
                "Approval pending. Reply 'approve " + mission["id"] + "' to run the pipeline.\n"
                "Execution starts in the background and the pipeline animates live."
            )
        return "\n".join(lines)

    def _org_meta(self, agent_id: str) -> Dict[str, Any]:
        if self.registry:
            for spec in self.registry.specs():
                if spec["id"] == agent_id:
                    return spec
        return {}

    # -- approval / execution ----------------------------------------------
    def _decide(self, low: str, approve: bool) -> str:
        if not self.missions:
            return "কোনো মিশন নেই — প্রথমে একটি ডিরেক্টিভ পাঠান।" if self.lang == "bn" else \
                "There are no missions to decide on yet. Send a directive first."

        match = re.search(r"msn[-\s]?0*(\d+)", low)
        target = None
        if match:
            wanted = int(match.group(1))
            for mission in reversed(self.missions):
                number = int(re.sub(r"\D", "", mission["id"]) or 0)
                if number == wanted:
                    target = mission
                    break
        target = target or self.missions[-1]
        lang = target.get("lang", self.lang)

        if not approve:
            target["approval"] = "on_hold"
            self._persist()
            self.log(f"{target['id']} placed on hold", "warn")
            return f"{target['id']} হোল্ডে রাখা হয়েছে।" if lang == "bn" else \
                f"{target['id']} is on hold. Send a revised directive when ready."

        if self._runner and not self._runner.done():
            if lang == "bn":
                return (
                    f"{self.running_mission_id} এখনো চলছে ({target.get('progress', 0)}%)। "
                    "শেষ হওয়ার আগে অন্য মিশন অনুমোদন করা যাবে না।"
                )
            return (
                f"{self.running_mission_id} is still executing "
                f"({target.get('progress', 0)}% overall). Wait for it to finish before "
                "approving another mission."
            )
        if target["status"] == "completed":
            return f"{target['id']} আগেই সম্পন্ন হয়েছে।" if lang == "bn" else \
                f"{target['id']} already completed. Send a new directive to start another."

        for task in target["tasks"]:
            task["status"] = "queued"
            task.pop("error", None)
            task.pop("note", None)
        target["approval"] = "approved"
        target["status"] = "in_progress"
        target["progress"] = 0
        target["deliverables"] = []
        target["compliance"] = None
        target["started_at"] = time.time()
        target["finished_at"] = None
        self._persist()

        self.running_mission_id = target["id"]
        self.set_state(
            status="orchestrating",
            progress=3,
            current_task=f"{target['id']} approved — starting pipeline",
        )
        self.log(f"{target['id']} approved — background execution starting", "success")
        try:
            self._runner = asyncio.create_task(self._execute_mission(target))
        except RuntimeError as exc:  # no running loop (never in FastAPI, but stay safe)
            target["status"] = "planned"
            self.running_mission_id = None
            self.set_state(status="error", progress=0, current_task="execution loop unavailable")
            return f"Could not start execution: {exc}"

        steps = len(target["tasks"])
        if lang == "bn":
            return (
                f"{target['id']} অনুমোদিত — পাইপলাইন ব্যাকগ্রাউন্ডে চালু হয়েছে।\n\n"
                f"{steps} ধাপ ক্রমানুসারে চলবে, প্রতিটি ফলাফল পরের ধাপে যাবে। "
                "পাইপ ও প্রোগ্রেস বার লাইভ আপডেট হবে; ফলাফল এই চ্যাটে ও মিশন বোর্ডে দেখাবে।"
            )
        if lang == "banglish":
            return (
                f"{target['id']} approved — pipeline background e chalu hoyeche.\n\n"
                f"{steps} dhap kromanusare cholbe, protiti result porer dhape jabe. "
                "Pipe ar progress bar live update hobe; result ei chat e ar mission board e dekhabe."
            )
        return (
            f"{target['id']} approved — pipeline started in the background.\n\n"
            f"{steps} step(s) will run in order, passing each output to the next. "
            "The pipeline pipes and progress bars update live; results land in this chat "
            "and in the mission board."
        )

    async def _execute_mission(self, mission: Dict[str, Any]) -> None:
        mission_id = mission["id"]
        total = len(mission["tasks"])
        self.log(f"{mission_id}: pipeline started — {total} steps")
        try:
            for position, task in enumerate(mission["tasks"], start=1):
                await self._run_task(mission, task)
                mission["progress"] = round(position / total * 100)
                self._persist()
                self.set_state(
                    progress=min(96, mission["progress"]),
                    current_task=f"{mission_id}: {position}/{total} steps complete",
                )
                await asyncio.sleep(STEP_PAUSE_SECONDS)
            self._finalise(mission)
        except asyncio.CancelledError:  # pragma: no cover - no cancel UI yet
            mission["status"] = "cancelled"
            self._persist()
            self.log(f"{mission_id} execution cancelled", "warn")
            raise
        except Exception as exc:
            mission["status"] = "failed"
            mission["error"] = str(exc)
            mission["finished_at"] = time.time()
            self._persist()
            self.log(f"{mission_id} execution failed: {exc}", "error")
            self.set_state(status="error", progress=0, current_task=f"{mission_id} failed: {exc}"[:80])
        finally:
            self.running_mission_id = None
            self._runner = None
            self.bus.bump()

    async def _run_task(self, mission: Dict[str, Any], task: Dict[str, Any]) -> None:
        if task["agent"] == "compliance":
            self._run_compliance(mission, task)
            return

        agent = self.registry.get(task["agent"]) if self.registry else None
        if agent is None or agent.module_status != "live":
            task["status"] = "blocked"
            task["note"] = "module not available"
            self._persist()
            self.log(f"{mission['id']} → {task['agent']} blocked (module unavailable)", "warn")
            return

        task["status"] = "running"
        task["started_at"] = time.time()
        self._persist()
        self.bus.bump()
        self.log(f"{mission['id']} → {agent.name} started")
        directive = self._directive(task["agent"], mission)

        try:
            await agent.handle(directive, {"mission_id": mission["id"], "objective": mission["objective"]})
        except Exception as exc:
            task["status"] = "failed"
            task["error"] = str(exc)
            self._persist()
            self.log(f"{mission['id']} → {agent.name} failed: {exc}", "error")
            return

        snapshot = agent.snapshot()
        data = snapshot.get(DELIVERABLE_KEYS.get(task["agent"], ""))
        if not data:
            task["status"] = "failed"
            task["error"] = "no deliverable produced"
            self._persist()
            self.log(f"{mission['id']} → {agent.name} returned no deliverable", "error")
            return

        deliverable = self._deliverable(task["agent"], agent, data, mission.get("lang", "en"))
        mission["deliverables"].append(deliverable)
        task["status"] = "done"
        task["completed_at"] = time.time()
        task["note"] = deliverable["summary"]
        self._persist()
        self.bus.bump()
        self.log(f"{mission['id']} deliverable: {deliverable['name']} — {deliverable['title']}", "success")

    def _directive(self, agent_id: str, mission: Dict[str, Any]) -> str:
        objective = mission["objective"]
        clean_objective = re.sub(
            r"^(?:full pipeline|whole company|everything|company-wide|campaign)\s*[:\-]\s*",
            "",
            objective,
            flags=re.I,
        ).strip() or objective
        agents = [task["agent"] for task in mission["tasks"]]
        lang = mission.get("lang", "en")
        bn = lang == "bn"
        if agent_id == "script_writing":
            return objective
        if agent_id == "video_generation":
            return "সর্বশেষ স্ক্রিপ্টের ভিডিও বানাও।" if bn else "Generate the video for the last script."
        if agent_id == "video_editing":
            return (
                "সর্বশেষ স্টোরিবোর্ড ক্যাপশন ও এক্সপোর্ট সেটিংসসহ অ্যাসেম্বল করো।"
                if bn else "Assemble the last storyboard with captions and export settings."
            )
        if agent_id == "ebook_writing":
            return (
                f"{clean_objective} নিয়ে ৪ চ্যাপ্টারের ইবুক লেখো।"
                if bn else f"Write a 4-chapter ebook about: {clean_objective}"
            )
        if agent_id == "lead_generation":
            return (
                f"{clean_objective} এর জন্য একটি লিড ক্যাম্পেইন বানাও।"
                if bn else f"Build a lead campaign for: {clean_objective}"
            )
        if agent_id == "seo_agent":
            if "video_generation" in agents or "script_writing" in agents:
                return ("সর্বশেষ ভিডিওর মেটাডেটা অপ্টিমাইজ করো।" if bn
                        else "Optimize the metadata for the last video.")
            ebook = self.registry.get("ebook_writing") if self.registry else None
            book = (ebook.snapshot().get("book") or {}) if ebook else {}
            title = book.get("title") or clean_objective
            return (f"কীওয়ার্ড: {title}" if bn else f"keywords: {title}")
        return objective

    def _deliverable(
        self, agent_id: str, agent: AgentBase, data: Dict[str, Any], lang: str
    ) -> Dict[str, Any]:
        title = str(data.get("title") or f"{agent.name} output")
        score = data.get("compliance_score")
        bn = lang == "bn"

        if agent_id == "script_writing":
            summary = (
                f"{data.get('scenes', '?')} দৃশ্য · কমপ্লায়েন্স {score}/100"
                if bn else f"{data.get('scenes', '?')} scenes · compliance {score}/100"
            )
        elif agent_id == "video_generation":
            render = "সাবমিট" if (data.get("render") or {}).get("submitted") else "স্টোরিবোর্ড"
            summary = (
                f"{data.get('shots', '?')} শট · {data.get('duration', '?')}s · রেন্ডার: {render}"
                if bn else f"{data.get('shots', '?')} shots · {data.get('duration', '?')}s · render: "
                + ("submitted" if (data.get("render") or {}).get("submitted") else "storyboard only")
            )
        elif agent_id == "video_editing":
            summary = (
                f"{data.get('segments', '?')} কাট · {data.get('duration', '?')}s · অডিও ফাইল: {data.get('audio_files', 0)}"
                if bn else f"{data.get('segments', '?')} cuts · {data.get('duration', '?')}s · "
                f"audio files: {data.get('audio_files', 0)}"
            )
        elif agent_id == "ebook_writing":
            summary = (
                f"চ্যাপ্টার {data.get('chapters', '?')} · {data.get('words', 0)} শব্দ"
                if bn else f"chapters {data.get('chapters', '?')} · {data.get('words', 0)} words"
            )
        elif agent_id == "lead_generation":
            title = str(data.get("offer") or title)
            summary = (
                f"{data.get('segments', '?')} সেগমেন্ট · {data.get('touches', '?')} টাচ"
                if bn else f"{data.get('segments', '?')} segments · {data.get('touches', '?')} touches"
            )
        elif agent_id == "seo_agent":
            summary = (
                f"{data.get('tags', '?')} ট্যাগ · বিবরণ {data.get('description_chars', 0)} অক্ষর"
                if bn else f"{data.get('tags', '?')} tags · description {data.get('description_chars', 0)} chars"
            )
        else:
            summary = "delivered"

        artifact = None
        if data.get("export"):
            artifact = str(data["export"])
        render = data.get("render") or {}
        if render.get("video_id"):
            artifact = f"HeyGen video {render['video_id']}"

        return {
            "agent": agent_id,
            "name": agent.name,
            "title": title,
            "summary": summary,
            "compliance_score": score,
            "verdict": data.get("verdict"),
            "artifact": artifact,
        }

    def _run_compliance(self, mission: Dict[str, Any], task: Dict[str, Any]) -> None:
        task["status"] = "running"
        self._persist()
        self.bus.bump()
        deliverables = mission.get("deliverables") or []
        scores = [d["compliance_score"] for d in deliverables if isinstance(d.get("compliance_score"), (int, float))]
        blocked = [d for d in deliverables if d.get("verdict") == "blocked"]

        if not scores:
            task["status"] = "failed"
            task["error"] = "no deliverables to gate"
            self._persist()
            self.log(f"{mission['id']} compliance gate had nothing to check", "warn")
            return

        score = min(scores)
        ok = score >= 70 and not blocked
        mission["compliance"] = {
            "score": score,
            "verdict": "monetization-ready" if ok else "blocked",
            "deliverables": len(scores),
            "platforms": "YouTube + Facebook",
        }
        task["status"] = "done" if ok else "blocked"
        task["note"] = f"lowest score {score}/100 on {len(scores)} deliverable(s)"
        self._persist()
        self.bus.bump()
        if ok:
            self.log(f"{mission['id']} compliance gate passed — {score}/100", "success")
        else:
            self.log(f"{mission['id']} compliance gate blocked — {score}/100", "error")

    def _finalise(self, mission: Dict[str, Any]) -> None:
        statuses = [task["status"] for task in mission["tasks"]]
        if any(status == "failed" for status in statuses):
            mission["status"] = "partial"
        elif any(status == "blocked" for status in statuses):
            mission["status"] = "blocked" if (mission.get("compliance") or {}).get("verdict") == "blocked" else "partial"
        else:
            mission["status"] = "completed"

        mission["progress"] = 100
        mission["finished_at"] = time.time()
        mission["approval"] = "approved"
        self._persist()

        report = self._delivery_report(mission)
        mission["result"] = report
        self._persist()
        self.record("agent", report)

        completed = sum(1 for status in statuses if status == "done")
        self.set_state(
            status="online",
            progress=100,
            current_task=f"{mission['id']} {mission['status']} — {completed}/{len(statuses)} steps",
        )
        level = "success" if mission["status"] == "completed" else "warn"
        self.log(
            f"{mission['id']} {mission['status']} — {completed}/{len(statuses)} steps, "
            f"{len(mission['deliverables'])} deliverable(s)",
            level,
        )

    def _delivery_report(self, mission: Dict[str, Any]) -> str:
        lang = mission.get("lang", "en")
        bn = lang == "bn"
        status = self._status_word(lang, mission["status"]).upper() if not bn else self._status_word(lang, mission["status"])
        lines = [
            f"{'মিশন' if bn else 'MISSION'} {mission['id']} — {status}",
            "",
            f"{'লক্ষ্য' if bn else 'Objective'}: {mission['objective']}",
            "",
        ]
        deliverables = mission.get("deliverables") or []
        lines.append("ডেলিভারেবল" if bn else "DELIVERABLES")
        if deliverables:
            for index, item in enumerate(deliverables, start=1):
                lines.append(f"  {index}. {item['name']} — {item['title']}")
                lines.append(f"     {item['summary']}")
                if item.get("artifact"):
                    lines.append(f"     {'আর্টিফ্যাক্ট' if bn else 'artifact'}: {item['artifact']}")
        else:
            lines.append("  কোনোটিই নেই" if bn else "  none")

        compliance = mission.get("compliance")
        lines.append("")
        if compliance:
            lines.append(
                f"{label(lang, 'compliance')} — {compliance['score']}/100 · {compliance['verdict']} "
                f"({compliance['platforms']})"
            )
        else:
            lines.append(f"{label(lang, 'compliance')} — {'চালানো হয়নি' if bn else 'not run'}")

        problems = [t for t in mission["tasks"] if t["status"] in ("failed", "blocked")]
        if problems:
            lines += ["", "মনোযোগ প্রয়োজন" if bn else "ATTENTION REQUIRED"]
            for task in problems:
                reason = task.get("error") or task.get("note") or task["status"]
                lines.append(f"  ! {task['label']} — {task['status']}: {reason}")

        lines += [
            "",
            "আর্টিফ্যাক্টগুলো সংশ্লিষ্ট বিভাগের চ্যাটে ও মিশন বোর্ডে আছে। সারসংক্ষেপের জন্য 'স্ট্যাটাস' লিখুন।"
            if bn
            else "Artifacts live in each department's chat and the mission board. Send 'status' for the executive summary.",
        ]
        return "\n".join(lines)

    # -- reporting ----------------------------------------------------------
    def _report(self) -> str:
        key_state = self.key_status()
        bn = self.lang == "bn"
        lines = ["নির্বাহী স্ট্যাটাস" if bn else "EXECUTIVE STATUS", ""]
        lines.append(
            (f"কী রাউটিং: {key_state['provider'] or 'none'} "
             f"({'সেট আছে' if key_state['configured'] else 'নেই'})")
            if bn else
            (f"Key routing: {key_state['provider'] or 'none'} "
             f"({'configured' if key_state['configured'] else 'missing'})")
        )
        if self.registry:
            specs = self.registry.specs()
            live = [s for s in specs if s["module_status"] == "live"]
            lines.append(
                f"বিভাগ চালু: {len(live)}/{len(specs)}" if bn else f"Departments live: {len(live)}/{len(specs)}"
            )
        if self.running_mission_id:
            lines.append(
                f"এখন চলছে: {self.running_mission_id}" if bn else f"Executing now: {self.running_mission_id}"
            )
        lines.append(f"মিশন: {len(self.missions)}" if bn else f"Missions: {len(self.missions)}")
        for mission in self.missions[-5:]:
            open_tasks = [t for t in mission["tasks"] if t["status"] != "done"]
            status_text = self._status_word(mission.get("lang", "en"), mission["status"]) if bn else mission["status"]
            lines.append(
                f"  {mission['id']} [{status_text}] {mission.get('progress', 0)}% · "
                f"অনুমোদন={mission['approval']} · খোলা কাজ={len(open_tasks)}"
                if bn else
                f"  {mission['id']} [{mission['status']}] {mission.get('progress', 0)}% · "
                f"approval={mission['approval']} · open_tasks={len(open_tasks)}"
            )
            if mission.get("compliance"):
                lines.append(
                    f"     কমপ্লায়েন্স: {mission['compliance']['score']}/100 "
                    f"({mission['compliance']['verdict']})"
                    if bn else
                    f"     compliance: {mission['compliance']['score']}/100 "
                    f"({mission['compliance']['verdict']})"
                )
        if not self.missions:
            lines.append("  এখনো কোনো মিশন নেই" if bn else "  no missions yet")
        lines.append("")
        lines.append(
            "পরবর্তী: একটি ডিরেক্টিভ পাঠান (যেমন: 'এআই এজেন্ট নিয়ে একটি ভিডিও বানাও')।"
            if bn else
            "Next: send a directive (e.g. 'produce a video series about AI agents')."
        )
        return "\n".join(lines)

    # -- snapshot -----------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        data = super().snapshot()
        data["missions"] = self.missions[-10:]
        data["running_mission"] = self.running_mission_id
        data["lang"] = self.lang
        data["live_departments"] = (
            sum(1 for s in self.registry.specs() if s["module_status"] == "live")
            if self.registry else 0
        )
        data["total_departments"] = len(self.registry.specs()) if self.registry else 0
        return data


def build(bus: EventBus, keys: ApiKeyStore) -> CEOAgent:
    return CEOAgent(bus, keys)
