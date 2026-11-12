"""VIDEO GENERATION — scenes, avatars & animation.

Consumes the scene output of SCRIPT WRITING (via the registry, never an import)
and turns it into a production-ready storyboard:

* per-shot prompts, timing, shot type, on-screen text and avatar direction,
* a HeyGen render request (``/v2/video/generate``) when a HeyGen key and
  avatar/voice IDs are available, with automatic offline fallback,
* the same monetization gate every other content agent uses.

Offline behaviour is first-class: with no key the department still returns a
complete, compliance-checked storyboard the editing department can consume.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from compliance import monetization_rules as rules
from config.api_keys import ApiKeyStore
from core.base_agent import AgentBase
from core.events import EventBus
from core.language import detect as detect_language, label, normalize_commands
from core.llm import USER_AGENT

AGENT_SPEC = {
    "id": "video_generation",
    "name": "VIDEO GENERATION",
    "role": "Scene & Animation",
    "accent": "#ff9f6e",
}

HEYGEN_GENERATE = "https://api.heygen.com/v2/video/generate"
HEYGEN_STATUS = "https://api.heygen.com/v1/video_status.get"
MAX_SHOTS = 8
WORDS_PER_SECOND = 2.6

STYLE_SUFFIX = (
    "cinematic lighting, shallow depth of field, clean modern workspace, "
    "brand accents in deep navy and cyan, 16:9 composition, no on-screen text, no logos"
)


def _fmt(seconds: float) -> str:
    total = int(round(seconds))
    return f"{total // 60}:{total % 60:02d}"


def _parse_range(value: str) -> Optional[Tuple[int, int]]:
    match = re.match(
        r"\s*(\d{1,2}):(\d{2})\s*[-–—]\s*(\d{1,2}):(\d{2})",
        str(value or ""),
    )
    if not match:
        return None
    start = int(match.group(1)) * 60 + int(match.group(2))
    end = int(match.group(3)) * 60 + int(match.group(4))
    return (start, max(start + 2, end))


def _post_json(url: str, payload: Dict[str, Any], headers: Dict[str, str], timeout: int = 60) -> Dict[str, Any]:
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
            detail = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:  # pragma: no cover
            pass
        raise RuntimeError(f"HeyGen HTTP {exc.code}: {detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error reaching HeyGen: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("HeyGen returned a non-JSON response") from exc


class VideoGenerationAgent(AgentBase):
    id = AGENT_SPEC["id"]
    name = AGENT_SPEC["name"]
    role = AGENT_SPEC["role"]
    accent = AGENT_SPEC["accent"]
    providers = ["heygen", "gemini", "openai"]
    persisted_artifacts = {
        "plan": "last_plan",
        "report": "last_report",
        "render": "last_render",
    }

    def __init__(self, bus: EventBus, keys: ApiKeyStore) -> None:
        super().__init__(bus, keys)
        self.last_plan: Optional[Dict[str, Any]] = None
        self.last_report: Optional[Dict[str, Any]] = None
        self.last_render: Dict[str, Any] = {}

    # -- entry point --------------------------------------------------------
    async def handle(self, message: str, context: Optional[Dict[str, Any]] = None) -> str:
        text = (message or "").strip()
        if not text:
            return "Send a script, a storyboard brief, or say 'generate the video for the last script'."
        self.record("user", text)
        self.lang = detect_language(text)
        low = normalize_commands(text).lower()

        if low.startswith(("status", "report", "progress")):
            reply = self._status()
        elif low in {"help", "commands"}:
            reply = self._help()
        elif "render" in low and ("heyge" in low or "submit" in low):
            reply = await self._render_existing()
        else:
            reply = await self._produce(text)

        self.record("agent", reply)
        self.bus.bump()
        return reply

    def _help(self) -> str:
        if self.lang == "bn":
            return (
                "ভিডিও জেনারেশন — কমান্ড\n"
                "• 'শেষ স্ক্রিপ্টের ভিডিও বানাও' → স্ক্রিপ্ট রাইটিং থেকে স্টোরিবোর্ড\n"
                "• 'স্টোরিবোর্ড: <স্ক্রিপ্ট বা দৃশ্যের তালিকা>' → পেস্ট করা লেখা থেকে\n"
                "• 'হেইজেন দিয়ে রেন্ডার করো' → সর্বশেষ স্টোরিবোর্ড জমা (HeyGen কী + আইডি লাগবে)\n"
                "• 'স্ট্যাটাস' → প্রোভাইডার, অ্যাভাটার/ভয়েস আইডি, সর্বশেষ রেন্ডার\n"
                "আসল রেন্ডারের জন্য .env-এ HEYGEN_AVATAR_ID ও HEYGEN_VOICE_ID সেট করুন।"
            )
        return (
            "VIDEO GENERATION — commands\n"
            "• 'generate the video for the last script' → storyboard from SCRIPT WRITING\n"
            "• 'storyboard: <script or scene list>' → build from pasted text\n"
            "• 'render with heygen' → submit the last storyboard (needs HeyGen key + IDs)\n"
            "• 'status' → provider, avatar/voice IDs, last render\n"
            "Set HEYGEN_AVATAR_ID and HEYGEN_VOICE_ID in .env to enable real rendering."
        )

    def _status(self) -> str:
        provider, key = self.keys.resolve_for_agent(self.id)
        avatar = os.environ.get("HEYGEN_AVATAR_ID", "")
        voice = os.environ.get("HEYGEN_VOICE_ID", "")
        if self.lang == "bn":
            lines = [
                "ভিডিও জেনারেশন — স্ট্যাটাস",
                f"প্রোভাইডার: {provider or 'নেই'} ({'কী সেট আছে' if key else 'কী নেই'})",
                f"HeyGen অ্যাভাটার: {'সেট' if avatar else 'নেই'} · ভয়েস: {'সেট' if voice else 'নেই'}",
            ]
            if self.last_plan:
                lines.append(
                    f"সর্বশেষ স্টোরিবোর্ড: {self.last_plan['title']} · "
                    f"{len(self.last_plan['shots'])} শট · {self.last_plan['total_duration']}s"
                )
                lines.append(
                    f"কমপ্লায়েন্স: {self.last_report['score']}/100 · {self.last_report['verdict']}"
                    if self.last_report else "কমপ্লায়েন্স: যাচাই হয়নি"
                )
                render = self.last_plan.get("render") or {}
                if render.get("submitted"):
                    lines.append(f"রেন্ডার জমা হয়েছে: {render.get('video_id')}")
                elif render.get("error"):
                    lines.append(f"রেন্ডার সমস্যা: {render['error']}")
                else:
                    lines.append("রেন্ডার: শুধু স্টোরিবোর্ড (জমা হয়নি)")
            else:
                lines.append("এই সেশনে এখনো কোনো স্টোরিবোর্ড তৈরি হয়নি।")
            return "\n".join(lines)
        lines = [
            "VIDEO GENERATION — status",
            f"Provider: {provider or 'none'} ({'key set' if key else 'no key'})",
            f"HeyGen avatar: {'set' if avatar else 'missing'} · voice: {'set' if voice else 'missing'}",
        ]
        if self.last_plan:
            lines.append(
                f"Last storyboard: {self.last_plan['title']} · "
                f"{len(self.last_plan['shots'])} shots · {self.last_plan['total_duration']}s"
            )
            lines.append(
                f"Compliance: {self.last_report['score']}/100 · {self.last_report['verdict']}"
                if self.last_report
                else "Compliance: not evaluated"
            )
            render = self.last_plan.get("render") or {}
            if render.get("submitted"):
                lines.append(f"Render submitted: {render.get('video_id')}")
            elif render.get("error"):
                lines.append(f"Render issue: {render['error']}")
            else:
                lines.append("Render: storyboard only (not submitted)")
        else:
            lines.append("No storyboard generated yet in this session.")
        return "\n".join(lines)

    # -- source resolution --------------------------------------------------
    def _latest_script(self) -> Optional[Dict[str, Any]]:
        if not self.registry:
            return None
        agent = self.registry.get("script_writing")
        return getattr(agent, "last_script", None) if agent else None

    def _from_script(self, script: Dict[str, Any]) -> Dict[str, Any]:
        title = (script.get("title_options") or ["Untitled"])[0]
        scenes = script.get("scenes") or []
        return {
            "title": title,
            "platform": "youtube",
            "scenes": scenes,
            "description": script.get("description", ""),
            "tags": script.get("tags") or [],
            "origin": "SCRIPT WRITING (this session)",
        }

    def _from_text(self, text: str) -> Dict[str, Any]:
        cleaned = re.sub(
            r"^(?:please\s+)?(?:generate|make|create|build|produce|storyboard)\s+"
            r"(?:me\s+)?(?:a|an|the)?\s*(?:video|storyboard|scenes?)?\s*"
            r"(?:for|from|about|of)?\s*:?\s*",
            "",
            text,
            flags=re.I,
        ).strip()
        blocks = [b.strip() for b in re.split(r"\n\s*\n|(?=^\s*Scene\s+\d+)", cleaned, flags=re.M) if b.strip()]
        if not blocks:
            blocks = [cleaned or text]

        scenes: List[Dict[str, Any]] = []
        cursor = 0
        for block in blocks[:MAX_SHOTS]:
            voice = re.sub(r"^\s*Scene\s+\d+\s*[:.\-]?\s*", "", block).strip()
            seconds = max(4, round(len(voice.split()) / WORDS_PER_SECOND))
            scenes.append(
                {
                    "time": f"{_fmt(cursor)}-{_fmt(cursor + seconds)}",
                    "visual": f"Generated scene for: {voice[:110]}",
                    "voiceover": voice,
                }
            )
            cursor += seconds

        title_guess = re.split(r"[.!?\n]", cleaned)[0].strip()[:80] or "Untitled video"
        return {
            "title": title_guess[:1].upper() + title_guess[1:],
            "platform": "youtube",
            "scenes": scenes,
            "description": (
                "এআই-জেনারেটেড অ্যাভাটার ও ভয়েস নারেশন। শুধু শিক্ষামূলক কনটেন্ট।"
                if self.lang == "bn" else
                "AI-generated avatar and voice narration. Original footage only."
            ),
            "tags": [],
            "origin": "pasted brief" if self.lang == "en" else "পেস্ট করা ব্রিফ",
        }

    def _from_topic(self, topic: str) -> Dict[str, Any]:
        if self.lang in ("bn", "banglish"):
            return self._bengali_topic_plan(topic)
        subject = topic.strip().rstrip(".") or "an AI-powered workflow"
        beats = [
            (f"Cold open on the result: close-up of {subject} in action, bold reveal.",
             f"Everyone talks about {subject}. Here is what actually makes it work."),
            ("Host avatar on camera, clean diagram behind them.",
             f"{subject[:1].upper() + subject[1:]} is a system. It has three moving parts, and none of them is a shortcut."),
            ("Screen capture: the first step typed out in one sentence.",
             "Part one: define one measurable outcome. One sentence, one number, one deadline."),
            ("Screen capture: the repeatable checklist running end to end.",
             "Part two: build the smallest repeatable process, small enough to run this week."),
            ("Avatar close-up, then the metric dashboard.",
             "Part three: watch a single number. If it does not move, change one variable."),
            ("Host avatar on camera, soft outro music.",
             "If this was useful, subscribe for the next part. No hype, just the process."),
        ]
        cursor = 0
        scenes = []
        for visual, voice in beats:
            seconds = max(5, round(len(voice.split()) / WORDS_PER_SECOND))
            scenes.append(
                {
                    "time": f"{_fmt(cursor)}-{_fmt(cursor + seconds)}",
                    "visual": visual,
                    "voiceover": voice,
                }
            )
            cursor += seconds
        return {
            "title": f"{subject[:1].upper() + subject[1:]}: A Practical Walkthrough",
            "platform": "youtube",
            "scenes": scenes,
            "description": "AI-generated avatar and voice narration. Original footage only.",
            "tags": [],
            "origin": "freestanding topic",
        }

    def _bengali_topic_plan(self, topic: str) -> Dict[str, Any]:
        bn = self.lang == "bn"
        subject = topic.strip().rstrip(".।") or ("একটি এআই ওয়ার্কফ্লো" if bn else "an AI workflow")
        if bn:
            beats = [
                ("কোল্ড ওপেন: ফলাফলের ক্লোজ-আপ, কাজ চলছে, বড় করে বিষয় লেখা।",
                 f"সবাই {subject} নিয়ে কথা বলে। আসলে কী কাজ করে, সেটাই এখানে বলা হবে।"),
                ("উপস্থাপক অ্যাভাটার ক্যামেরার সামনে, পেছনে পরিষ্কার ডায়াগ্রাম।",
                 f"{subject} একটি সিস্টেম — এর তিনটি অংশ আছে, কোনোটিই শর্টকাট নয়।"),
                ("স্ক্রিন ক্যাপচার: প্রথম ধাপ এক বাক্যে লেখা হচ্ছে।",
                 "প্রথম ধাপ: একটি মাপা যায় এমন ফলাফল ঠিক করুন। এক বাক্য, একটি সংখ্যা, একটি সময়সীমা।"),
                ("স্ক্রিন ক্যাপচার: পুনরাবৃত্তিযোগ্য চেকলিস্ট চলছে।",
                 "দ্বিতীয় ধাপ: সবচেয়ে ছোট প্রক্রিয়াটি বানান, যা এই সপ্তাহেই চালানো যায়।"),
                ("অ্যাভাটার ক্লোজ-আপ, তারপর মেট্রিক ড্যাশবোর্ড।",
                 "তৃতীয় ধাপ: একটি সংখ্যা লক্ষ্য করুন। না বাড়লে একটাই ভেরিয়েবল বদলান।"),
                ("উপস্থাপক অ্যাভাটার, নরম আউট্রো সংগীত।",
                 "ভিডিওটি কাজে লাগলে সাবস্ক্রাইব করুন। হাইপ নয়, শুধু প্রক্রিয়া।"),
            ]
            title = f"{subject}: ব্যবহারিক ওয়াকথ্রু"
            description = "এআই-জেনারেটেড অ্যাভাটার ও ভয়েস নারেশন। শুধু শিক্ষামূলক কনটেন্ট।"
        else:
            beats = [
                ("Cold open on the result, bold reveal.",
                 f"Sobai {subject} niye kotha bole. Asole ki kaj kore setai ekhane bola hobe."),
                ("Host avatar on camera, clean diagram behind.",
                 f"{subject} ekta system — tar tin ta ongsho ache, kono tai shortcut na."),
                ("Screen capture: step one typed out.",
                 "Prothom dhap: ekta mapa jay emon result thik koro. Ek bakko, ek shongkha, ek deadline."),
                ("Screen capture: repeatable checklist running.",
                 "Ditiyo dhap: shob cheye choto process ta banao, ja ei shoptah ei chalate paro."),
                ("Avatar close-up, then the metric dashboard.",
                 "Tritiyo dhap: ekta shongkha dekho. Na barle ekta variable bodlao."),
                ("Host avatar, soft outro music.",
                 "Video ta kaj e lagle subscribe koro. Hype na, shudhu process."),
            ]
            title = f"{subject}: A Practical Walkthrough"
            description = "AI-generated avatar and voice narration. Original footage only."

        cursor = 0
        scenes = []
        for visual, voice in beats:
            seconds = max(5, round(len(voice.split()) / WORDS_PER_SECOND))
            scenes.append(
                {"time": f"{_fmt(cursor)}-{_fmt(cursor + seconds)}", "visual": visual, "voiceover": voice}
            )
            cursor += seconds
        return {
            "title": title,
            "platform": "youtube",
            "scenes": scenes,
            "description": description,
            "tags": [],
            "origin": "freestanding topic" if not bn else "স্বতন্ত্র বিষয়",
        }

    # -- storyboard ---------------------------------------------------------
    def _shot_type(self, visual: str) -> str:
        low = visual.lower()
        if any(word in low for word in ("host", "avatar", "on camera", "presenter", "talking head",
                                        "উপস্থাপক", "অ্যাভাটার", "ক্যামেরা")):
            return "avatar"
        if any(word in low for word in ("screen recording", "screen capture", "dashboard", "screen",
                                        "স্ক্রিন", "ড্যাশবোর্ড")):
            return "screen_capture"
        if any(word in low for word in ("b-roll", "cold open", "fast cuts", "cuts of",
                                        "বি-রোল", "কাট")):
            return "b_roll"
        return "avatar"

    def _build_shots(self, scenes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        shots: List[Dict[str, Any]] = []
        cursor = 0.0
        for index, scene in enumerate(scenes[:MAX_SHOTS], start=1):
            voice = str(scene.get("voiceover") or scene.get("vo") or "").strip()
            visual = str(scene.get("visual") or "Generated b-roll").strip()
            window = _parse_range(str(scene.get("time") or ""))
            if window:
                start, end = window
            else:
                start = int(round(cursor))
                end = start + max(4, round(len(voice.split()) / WORDS_PER_SECOND))
            cursor = end
            shot_type = self._shot_type(visual)
            panel = "9:16" if any(w in visual.lower() for w in ("vertical", "reel")) else "16:9"
            style = STYLE_SUFFIX if self.lang == "en" else (
                "সিনেমাটিক আলো, হালকা ডেপথ অফ ফিল্ড, পরিচ্ছন্ন আধুনিক কর্মক্ষেত্র, "
                "গাঢ় নেভি ও সায়ান ব্র্যান্ড অ্যাকসেন্ট, ১৬:৯ কম্পোজিশন, স্ক্রিনে লেখা নেই, লোগো নেই"
            )
            prompt = (
                f"{visual.rstrip('.।')}. {style}. "
                f"Shot type: {shot_type.replace('_', ' ')}. Aspect {panel}."
            )
            shots.append(
                {
                    "index": index,
                    "start": start,
                    "end": end,
                    "duration": max(2, end - start),
                    "timecode": f"{_fmt(start)}-{_fmt(end)}",
                    "shot": shot_type,
                    "prompt": prompt[:600],
                    "voiceover": voice,
                    "overlay": self._overlay_text(voice, index),
                    "camera": "slow push-in" if index % 2 else "locked-off wide",
                }
            )
        return shots

    def _overlay_text(self, voice: str, index: int) -> str:
        # Keep Unicode intact: stripping non-\w characters removes Bengali vowel
        # signs (matras), so drop only surrounding punctuation per word.
        words = [w.strip("।,.:;!?\"'()[]-–—") for w in voice.split()]
        words = [w for w in words if w]
        if not words:
            return f"Scene {index}"
        return " ".join(words[:5]).upper()

    def _aspect(self, directive: str, total: int) -> Tuple[int, int]:
        low = directive.lower()
        vertical = any(word in low for word in ("shorts", "reel", "vertical", "tiktok"))
        if not vertical and total <= 60 and "facebook" in low:
            vertical = True
        return (1080, 1920) if vertical else (1920, 1080)

    # -- HeyGen -------------------------------------------------------------
    def _heygen_payload(self, shots: List[Dict[str, Any]], directive: str, total: int) -> Dict[str, Any]:
        avatar = os.environ.get("HEYGEN_AVATAR_ID", "").strip()
        voice = os.environ.get("HEYGEN_VOICE_ID", "").strip()
        width, height = self._aspect(directive, total)
        inputs = []
        for shot in shots:
            inputs.append(
                {
                    "character": {
                        "type": "avatar",
                        "avatar_id": avatar,
                        "avatar_style": "normal",
                    },
                    "voice": {
                        "type": "text",
                        "input_text": shot["voiceover"][:1500] or " ",
                        "voice_id": voice,
                    },
                    "background": {"type": "color", "value": "#0b1120"},
                }
            )
        return {"video_inputs": inputs, "dimension": {"width": width, "height": height}}

    async def _submit(self, payload: Dict[str, Any], key: str) -> Dict[str, Any]:
        headers = {"Content-Type": "application/json", "X-Api-Key": key}
        data = await asyncio.to_thread(_post_json, HEYGEN_GENERATE, payload, headers)
        error = data.get("error")
        if error:
            raise RuntimeError(f"HeyGen error: {error}")
        video_id = (data.get("data") or {}).get("video_id")
        if not video_id:
            raise RuntimeError(f"HeyGen returned no video_id: {str(data)[:200]}")
        return {"submitted": True, "video_id": video_id, "status_url": HEYGEN_STATUS}

    # -- pipeline -----------------------------------------------------------
    async def _produce(self, text: str) -> str:
        low = normalize_commands(text).lower()
        script = self._latest_script()
        use_last = script is not None and (
            any(phrase in low for phrase in ("last script", "latest script", "previous script", "the script", "that script"))
            or len(text.split()) <= 3
            or (self.lang != "en" and "script" in low)
        )
        if use_last and script:
            source = self._from_script(script)
        elif len(text.split()) <= 6 and not any(mark in text for mark in (".", "\n", ":", "—")):
            source = self._from_topic(text)
        else:
            source = self._from_text(text)

        self.set_state(status="working", progress=10, current_task=f"storyboard: {source['title'][:48]}")
        self.log(f"storyboard source: {source['origin']} · {len(source['scenes'])} scenes")

        shots = self._build_shots(source["scenes"])
        total = shots[-1]["end"] if shots else 0
        source["shots"] = shots
        source["total_duration"] = total
        source["aspect"] = self._aspect(text, total)
        self.set_state(progress=45, current_task="building shot prompts")

        report = self._gate(source)
        if report["blockers"]:
            source = self._sanitize(source)
            report = self._gate(source)

        provider, _ = self.keys.resolve_for_agent(self.id)
        heygen_keys = self.keys.candidates(self.id, ["heygen"])
        render: Dict[str, Any] = {}
        payload = self._heygen_payload(source["shots"], text, total)
        avatar_id = os.environ.get("HEYGEN_AVATAR_ID", "").strip()
        voice_id = os.environ.get("HEYGEN_VOICE_ID", "").strip()

        if not avatar_id or not voice_id:
            render = {
                "submitted": False,
                "error": "HEYGEN_AVATAR_ID / HEYGEN_VOICE_ID not set — storyboard ready, not submitted",
            }
        elif not heygen_keys:
            render = {"submitted": False, "error": "no HeyGen key assigned"}
        else:
            self.set_state(progress=70, current_task="submitting HeyGen render")
            failures = 0
            for index, (_, key) in enumerate(heygen_keys, start=1):
                try:
                    render = await self._submit(payload, str(key))
                    self.log(
                        f"HeyGen render submitted ({render['video_id']})"
                        + (f" using key {index} after {failures} failure(s)" if failures else ""),
                        "success",
                    )
                    break
                except RuntimeError as exc:
                    failures += 1
                    render = {"submitted": False, "error": str(exc)}
                    self.log(f"HeyGen key {index} failed: {exc}", "warn")

        source["render"] = render
        source["provider"] = provider
        source["payload"] = payload
        self.last_plan = source
        self.last_report = report
        self.last_render = render
        self.persist_artifacts()

        self.set_state(
            status="review",
            progress=100,
            current_task=f"{len(shots)} shots · {_fmt(total)} · compliance {report['score']}/100",
        )
        if report["ok"]:
            self.log(f"storyboard ready — {len(shots)} shots, compliance {report['score']}/100", "success")
        else:
            self.log(f"storyboard blocked: {', '.join(b['id'] for b in report['blockers'])}", "error")

        return self._format_reply(source, report, render)

    def _gate(self, source: Dict[str, Any]) -> Dict[str, Any]:
        narration = "\n".join(shot["voiceover"] for shot in source["shots"])
        return rules.evaluate(
            script=narration,
            title=source["title"],
            description=source.get("description", "") + "\nAI-generated avatar and voice narration.",
            tags=source.get("tags") or None,
            platform="both",
            ai_generated=True,
        )

    def _sanitize(self, source: Dict[str, Any]) -> Dict[str, Any]:
        for shot in source["shots"]:
            shot["voiceover"], _ = rules.sanitize(shot["voiceover"])
            shot["prompt"], _ = rules.sanitize(shot["prompt"])
        source["title"], _ = rules.sanitize(source["title"])
        source["description"], _ = rules.sanitize(source.get("description", ""))
        return source

    # -- reply --------------------------------------------------------------
    def _format_reply(self, source: Dict[str, Any], report: Dict[str, Any], render: Dict[str, Any]) -> str:
        width, height = source.get("aspect") or self._aspect(source["title"], source["total_duration"])
        bl = lambda key: label(self.lang, key)  # noqa: E731
        bn = self.lang == "bn"
        lines = [
            f"{bl('delivered')} — {source['title']}",
            f"{source['origin']} · {len(source['shots'])} {'শট' if bn else 'shots'} · "
            f"{_fmt(source['total_duration'])} · {width}x{height} · {source.get('provider') or 'offline'}",
            "",
            bl("shots"),
        ]
        for shot in source["shots"]:
            lines.append(f"  {shot['index']}. [{shot['timecode']}] {shot['shot'].replace('_', ' ').upper()} · {shot['camera']}")
            lines.append(f"     {bl('prompt').upper()}: {shot['prompt']}")
            lines.append(f"     {bl('vo')}: {shot['voiceover'] or '—'}")
            lines.append(f"     {bl('overlay')}: {shot['overlay']}")

        lines += ["", "অ্যাভাটার / ভয়েস" if bn else "AVATAR / VOICE"]
        avatar = os.environ.get("HEYGEN_AVATAR_ID", "")
        voice = os.environ.get("HEYGEN_VOICE_ID", "")
        lines.append(f"  avatar_id: {avatar or 'not set (add HEYGEN_AVATAR_ID to .env)'}")
        lines.append(f"  voice_id: {voice or 'not set (add HEYGEN_VOICE_ID to .env)'}")

        lines += ["", bl("render")]
        if render.get("submitted"):
            lines.append(f"  HeyGen-এ জমা · video_id: {render['video_id']}" if bn else
                         f"  submitted to HeyGen · video_id: {render['video_id']}")
            lines.append(f"  {'স্ট্যাটাস দেখুন' if bn else 'poll status'}: GET {HEYGEN_STATUS}?video_id={render['video_id']}")
        else:
            lines.append(f"  {'শুধু স্টোরিবোর্ড' if bn else 'storyboard only'} — {render.get('error') or 'not submitted'}")

        lines += ["", rules.format_report(report)]
        if report["ok"]:
            lines.append(
                "পরবর্তী: এটি ভিডিও এডিটিং-এ পাঠান — তার চ্যাটে লিখুন 'সর্বশেষ স্টোরিবোর্ড অ্যাসেম্বল করো'।"
                if bn else
                "NEXT: hand this to VIDEO EDITING — open its chat and say 'assemble the last storyboard'."
            )
        else:
            lines.append("পরবর্তী: রেন্ডারের আগে উপরের ব্লকারগুলো ঠিক করুন।" if bn else
                         "NEXT: resolve the blockers above before rendering.")
        return "\n".join(lines)

    # -- snapshot -----------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        data = super().snapshot()
        if self.last_plan:
            data["video"] = {
                "title": self.last_plan["title"],
                "shots": len(self.last_plan["shots"]),
                "duration": self.last_plan["total_duration"],
                "render": self.last_plan.get("render", {}),
                "compliance_score": self.last_report["score"] if self.last_report else None,
            }
        return data


def build(bus: EventBus, keys: ApiKeyStore) -> VideoGenerationAgent:
    return VideoGenerationAgent(bus, keys)
