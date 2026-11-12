"""SCRIPT WRITING — plot, structure & dialogue.

Fully functional department module:

* parses a director's brief (topic, platform, duration, tone),
* writes a scene-by-scene script + titles, description and tags using the
  provider assigned to this agent (Gemini / Groq / OpenAI),
* falls back to a deterministic offline template when no usable key exists, so
  the pipeline still works on a fresh Termux install,
* runs every draft through ``compliance.monetization_rules`` and auto-fixes or
  rewrites until it is monetization-ready (or reports exactly what is blocked).

The CEO/dispatch layer only needs the standard contract:
``AGENT_SPEC`` + ``build(bus, keys)``.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from compliance import monetization_rules as rules
from config.api_keys import ApiKeyStore
from core import llm
from core.base_agent import AgentBase
from core.events import EventBus
from core.language import (
    clean_topic,
    detect as detect_language,
    duration_seconds,
    keywords,
    label,
    normalize_commands,
    platform_hint,
)

AGENT_SPEC = {
    "id": "script_writing",
    "name": "SCRIPT WRITING",
    "role": "Plot & Dialogue",
    "accent": "#7c9cff",
}

SYSTEM_PROMPT = """You are the Script Writing department of CogniSphere Labs,
a multi-agent content company. You write original, publish-ready video scripts
for YouTube and Facebook that are fully monetization compliant.

Craft rules:
- Open with a hook in the first 3 seconds; earn the next 10 seconds.
- Every claim must be accurate and supportable. No statistics you cannot verify.
- Write for the ear: short sentences, natural spoken rhythm, concrete visuals.
- Structure: hook → context → 3 practical beats → example → call to action.
- No third-party footage, music or trademarks. Original only.

{rules}

Return ONLY minified JSON, no markdown fence, with exactly these keys:
{{"title_options": ["3 sentence-case titles <= 90 chars"],
  "hook": "2-4 spoken sentences",
  "scenes": [{{"time": "0:00-0:05", "visual": "what is on screen", "voiceover": "spoken line(s)"}}],
  "cta": "2 spoken sentences",
  "description": ">= 180 chars, includes chapters, AI disclosure and a non-advice disclaimer",
  "tags": ["5-15 relevant tags"],
  "thumbnail_text": "max 5 words"}}
"""

STOPWORDS = {
    "about", "after", "again", "against", "also", "because", "before", "being",
    "between", "could", "every", "from", "have", "into", "just", "like", "make",
    "many", "more", "most", "much", "need", "only", "other", "over", "should",
    "some", "such", "than", "that", "their", "them", "then", "there", "these",
    "they", "this", "those", "through", "under", "very", "want", "were", "what",
    "when", "where", "which", "while", "will", "with", "would", "your", "yours",
    "please", "write", "create", "draft", "script", "video", "story", "series",
    "about", "using", "used", "help", "helps",
}

TONE_WORDS = [
    "energetic", "calm", "authoritative", "friendly", "cinematic", "educational",
    "funny", "serious", "inspiring", "documentary",
]


def _fmt_time(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}"


def _topic_case(topic: str) -> str:
    topic = topic.strip().rstrip(".")
    return topic[:1].upper() + topic[1:] if topic else topic


def _topic_lower(topic: str) -> str:
    topic = _topic_case(topic)
    return topic[:1].lower() + topic[1:] if topic and not topic[:2].isupper() else topic


def _keywords(topic: str, limit: int = 6) -> List[str]:
    words = [w for w in re.findall(r"[a-z0-9]+", topic.lower()) if len(w) > 3 and w not in STOPWORDS]
    seen, result = set(), []
    for word in words:
        if word not in seen:
            seen.add(word)
            result.append(word)
    return result[:limit]


class ScriptWritingAgent(AgentBase):
    id = AGENT_SPEC["id"]
    name = AGENT_SPEC["name"]
    role = AGENT_SPEC["role"]
    accent = AGENT_SPEC["accent"]
    providers = ["gemini", "openai", "groq"]
    persisted_artifacts = {
        "brief": "last_brief",
        "script": "last_script",
        "report": "last_report",
        "mode": "last_mode",
    }

    def __init__(self, bus: EventBus, keys: ApiKeyStore) -> None:
        super().__init__(bus, keys)
        self.last_brief: Optional[Dict[str, Any]] = None
        self.last_script: Optional[Dict[str, Any]] = None
        self.last_report: Optional[Dict[str, Any]] = None
        self.last_mode = "offline"

    # -- entry point --------------------------------------------------------
    async def handle(self, message: str, context: Optional[Dict[str, Any]] = None) -> str:
        text = (message or "").strip()
        if not text:
            return "Send a brief: topic, platform, target length and tone."

        self.record("user", text)
        self.lang = detect_language(text)
        low = normalize_commands(text).lower()

        if low in {"status", "report", "progress"} or low.startswith("status "):
            reply = self._status()
        elif low.startswith("revise"):
            reply = await self._revise(text)
        elif low in {"help", "commands"}:
            reply = self._help()
        else:
            brief = self._parse_brief(text)
            self.last_brief = brief
            reply = await self._produce(brief)

        self.record("agent", reply)
        self.bus.bump()
        return reply

    # -- commands -----------------------------------------------------------
    def _help(self) -> str:
        if self.lang == "bn":
            return (
                "স্ক্রিপ্ট রাইটিং — কমান্ড\n"
                "• যেকোনো ব্রিফ দিন → কমপ্লায়েন্স-চেক করা স্ক্রিপ্ট, যেমন\n"
                '  "এআই এজেন্ট নিয়ে ৬০ সেকেন্ডের ইউটিউব স্ক্রিপ্ট লেখো"\n'
                '• "রিভাইজ: হুক আরও ছোট করো"\n'
                '• "স্ট্যাটাস" → মডিউল, কী ও সর্বশেষ কমপ্লায়েন্স ফলাফল\n'
                "ডিফল্ট: ইউটিউব + ফেসবুক নিয়ম, ৬০ সেকেন্ড।"
            )
        return (
            "SCRIPT WRITING — commands\n"
            "• any brief → a monetization-checked script, e.g.\n"
            '  "60-second YouTube script about why small teams use AI agents, energetic tone"\n'
            '• "revise: make the hook shorter and add a concrete example"\n'
            '• "status" → module + key + last compliance result\n'
            '• Bangla / Banglish briefs work too: "এআই নিয়ে স্ক্রিপ্ট লেখো"\n'
            "Platform defaults to YouTube + Facebook rules; duration defaults to 60s."
        )

    def _status(self) -> str:
        candidates = self.keys.candidates(self.id, list(llm.SUPPORTED_PROVIDERS))
        provider, key = candidates[0] if candidates else (None, None)
        pool = len(candidates)
        if self.lang == "bn":
            lines = [
                "স্ক্রিপ্ট রাইটিং — স্ট্যাটাস",
                f"মডিউল: চালু · মোড: {'লাইভ LLM' if (llm.supports(provider) and key) else 'অফলাইন টেমপ্লেট'}",
                f"প্রোভাইডার: {provider or 'নেই'} ({'কী সেট আছে' if key else 'কী নেই'}) · কী পুল: {pool}",
            ]
            if self.last_script and self.last_report:
                lines += [
                    f"সর্বশেষ: {self.last_script['title_options'][0]}",
                    f"কমপ্লায়েন্স: {self.last_report['score']}/100 · {self.last_report['verdict']}",
                    f"দৃশ্য: {len(self.last_script['scenes'])} · ট্যাগ: {len(self.last_script['tags'])}",
                ]
            else:
                lines.append("এই সেশনে এখনো কোনো স্ক্রিপ্ট তৈরি হয়নি।")
            return "\n".join(lines)
        lines = [
            "SCRIPT WRITING — status",
            f"Module: live · mode: {'live LLM' if (llm.supports(provider) and key) else 'offline template'}",
            f"Provider: {provider or 'none'} ({'key set' if key else 'no key'}) · key pool: {pool}",
        ]
        if self.last_script and self.last_report:
            lines += [
                f"Last delivery: {self.last_script['title_options'][0]}",
                f"Compliance: {self.last_report['score']}/100 · {self.last_report['verdict']}",
                f"Scenes: {len(self.last_script['scenes'])} · tags: {len(self.last_script['tags'])}",
            ]
        else:
            lines.append("No script delivered yet in this session.")
        return "\n".join(lines)

    async def _revise(self, text: str) -> str:
        if not self.last_brief:
            return "Nothing to revise yet — send a script brief first."
        note = text.split(":", 1)[1].strip() if ":" in text else " ".join(text.split()[1:]).strip()
        if not note:
            return "Tell me what to change, e.g. 'revise: tighter hook, 45 seconds'."
        brief = dict(self.last_brief)
        low = note.lower()
        if "facebook" in low and "youtube" not in low:
            brief["platform"] = "facebook"
        elif "youtube" in low:
            brief["platform"] = "youtube"
        duration = re.search(r"(\d+)\s*[- ]?\s*(seconds?|secs?|minutes?|mins?|s|m)\b", low)
        if duration:
            value = int(duration.group(1))
            brief["duration"] = max(20, min(900, value * 60 if duration.group(2).startswith("m") else value))
        tone = next((word for word in TONE_WORDS if word in low), None)
        if tone:
            brief["tone"] = tone
        brief["revision_note"] = note[:400]
        brief["directive"] = f"{brief.get('directive', '')} | revision: {note}"
        self.last_brief = brief
        return await self._produce(brief, revision_note=note)

    # -- brief parsing ------------------------------------------------------
    def _parse_brief(self, message: str) -> Dict[str, Any]:
        original = message.strip()
        text = original

        duration = duration_seconds(text, default=60)
        platform = platform_hint(text) or "both"

        low = text.lower()
        tone = next((word for word in TONE_WORDS if word in low), "clear, conversational, credible")
        paid = any(word in low for word in ("sponsor", "paid promotion", "sponsored", "ad read", "স্পনসর"))

        topic_clean = self._clean_topic(text)

        if len(topic_clean) < 4:
            topic_clean = re.sub(r"\s{2,}", " ", original).strip()

        return {
            "directive": original,
            "topic": topic_clean[:200] or "building a small AI-powered content studio",
            "platform": platform,
            "duration": duration,
            "tone": tone,
            "paid_promotion": paid,
        }

    def _clean_topic(self, text: str) -> str:
        """Strip command words, platform words and length modifiers (Bengali-safe)."""
        if self.lang in ("bn", "banglish"):
            return clean_topic(text)
        prefixes = [
            r"^(?:please\s+)?(?:write|create|draft|generate|make|produce|develop)\b",
            r"^(?:me|us)\b",
            r"^(?:a|an|the)\b",
            r"^\d+\s*[- ]?\s*(?:seconds?|secs?|s|minutes?|mins?|m)\b",
            r"^(?:short|long|viral|full|complete|quick|entire)\b",
            r"^(?:\d+[- ]part|multi[- ]part)\b",
            r"^(?:youtube|facebook|reels|shorts|tiktok)\b",
            r"^(?:video\s+)?(?:script|screenplay|story|outline|piece|series|breakdown|explainer)\b",
            r"^(?:about|on|for|covering|around)\b",
            r"^[,.:;\-\s]+",
        ]
        tone_words = "|".join(TONE_WORDS)
        topic = text.strip()
        for _ in range(12):
            before = topic
            for pattern in prefixes:
                topic = re.sub(pattern, "", topic, count=1, flags=re.I).strip(" ,.:;-")
            if topic == before:
                break
        topic = re.sub(
            rf"[,\s]+(?:in|with|using)?\s*(?:a|an)?\s*(?:{tone_words})\s+tone\b.*$",
            "",
            topic,
            flags=re.I,
        )
        topic = re.sub(
            r"[,\s]+(?:in|under|about)\s+\d+\s*(?:seconds?|secs?|s|minutes?|mins?|m)\b.*$",
            "",
            topic,
            flags=re.I,
        )
        return topic.strip(" ,.:;-")

    # -- generation ---------------------------------------------------------
    async def _produce(self, brief: Dict[str, Any], revision_note: str = "") -> str:
        topic = brief["topic"]
        candidates = self.keys.candidates(self.id, list(llm.SUPPORTED_PROVIDERS))
        live = bool(candidates)
        mode = candidates[0][0] if live else "offline template"
        used_provider: Optional[str] = None

        self.set_state(status="working", progress=5, current_task=f"brief: {topic[:52]}")
        self.log(f"script brief received — {brief['duration']}s · {brief['platform']} · {brief['tone']}")

        script: Optional[Dict[str, Any]] = None
        note = ""

        if live:
            self.set_state(progress=25, current_task="writing draft with " + str(candidates[0][0]))
            try:
                raw, used_provider = await llm.generate_first(
                    candidates,
                    SYSTEM_PROMPT.format(rules=rules.compliance_brief()),
                    self._build_prompt(brief, revision_note),
                    temperature=0.75,
                    max_tokens=2800,
                    language=self.lang,
                )
                if used_provider != candidates[0][0]:
                    note = f"key fallback → {used_provider} after a provider failure"
                    mode = used_provider
                script = self._parse_model_json(raw)
                if script is None:
                    note = "provider returned non-JSON; draft discarded"
                    self.log("model output was not valid JSON", "warn")
            except llm.LLMError as exc:
                note = f"provider error — {exc}"
                self.log(f"LLM unavailable: {exc}", "warn")

        if script is None:
            script = self._offline_script(brief)
            if live:
                note = note or "used offline template"
                mode = "offline template (fallback)"

        script = self._normalize_script(script, brief)
        self.set_state(progress=65, current_task="running monetization gate")
        report = self._gate(script, brief)

        if report["blockers"]:
            script, fixes = self._sanitize_script(script)
            if fixes:
                self.log(f"auto-fixed {len(fixes)} phrase(s) during compliance pass")
            report = self._gate(script, brief)

        if report["blockers"] and live:
            self.set_state(progress=80, current_task="rewriting blocked sections with provider")
            revised = await self._llm_repair(script, report, brief, candidates)
            if revised is not None:
                script = self._normalize_script(revised, brief)
                script, fixes = self._sanitize_script(script)
                report = self._gate(script, brief)
                note = (note + " · " if note else "") + "rewritten once for compliance"

        self.last_script = script
        self.last_report = report
        self.last_mode = used_provider or mode
        self.persist_artifacts()

        self.set_state(
            status="review",
            progress=100,
            current_task=f"{script['title_options'][0][:44]} · compliance {report['score']}/100",
        )
        if report["ok"]:
            self.log(f"script ready — compliance {report['score']}/100, no blockers", "success")
        else:
            self.log(
                f"script blocked by policy: {', '.join(b['id'] for b in report['blockers'])}",
                "error",
            )

        return self._format_reply(script, report, brief, mode, note)

    def _build_prompt(self, brief: Dict[str, Any], revision_note: str) -> str:
        lines = [
            f"Directive: {brief['directive']}",
            f"Topic: {brief['topic']}",
            f"Platform: {brief['platform']}",
            f"Target duration: {brief['duration']} seconds",
            f"Tone: {brief['tone']}",
            f"Paid promotion: {'yes' if brief['paid_promotion'] else 'no'}",
            "Write 5-9 scenes that fill the duration. Times must be real ranges.",
        ]
        if revision_note:
            lines.append(f"Apply this revision: {revision_note}")
        return "\n".join(lines)

    def _parse_model_json(self, raw: str) -> Optional[Dict[str, Any]]:
        cleaned = raw.strip()
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            data = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        return data

    async def _llm_repair(
        self,
        script: Dict[str, Any],
        report: Dict[str, Any],
        brief: Dict[str, Any],
        candidates: List[Tuple[str, str]],
    ) -> Optional[Dict[str, Any]]:
        try:
            raw, _ = await llm.generate_first(
                candidates,
                SYSTEM_PROMPT.format(rules=rules.compliance_brief()),
                "\n".join(
                    [
                        "This draft failed the monetization gate. Rewrite ONLY the offending parts,",
                        "keep the structure and topic, and return the same JSON schema.",
                        f"Topic: {brief['topic']}",
                        f"Violations to remove: {', '.join(b['title'] for b in report['blockers'])}",
                        "Draft JSON:",
                        json.dumps(script, ensure_ascii=False),
                    ]
                ),
                temperature=0.4,
                max_tokens=2800,
                language=self.lang,
            )
        except llm.LLMError as exc:
            self.log(f"compliance rewrite failed: {exc}", "warn")
            return None
        return self._parse_model_json(raw)

    # -- offline writer -----------------------------------------------------
    def _offline_script(self, brief: Dict[str, Any]) -> Dict[str, Any]:
        if self.lang in ("bn", "banglish"):
            return self._offline_bengali(brief)
        topic = _topic_case(brief["topic"])
        seconds = int(brief["duration"])
        scene_count = max(5, min(8, round(seconds / 14)))
        keywords = _keywords(topic) or ["ai", "workflow"]

        beats: List[Tuple[str, str, str]] = [
            (
                "Cold open: fast cuts of the workflow in action, bold text overlay of the topic.",
                f"Everyone talks about {brief['topic']}. Almost nobody explains what actually makes it work.",
            ),
            (
                "Host on camera, then a clean diagram of the three moving parts.",
                f"Here is the honest version. {topic} is a system, not a hack, and it has three moving parts.",
            ),
            (
                "Screen recording: writing the outcome in one sentence and circling it.",
                "Part one: define the outcome you can measure. One sentence, one number, one deadline.",
            ),
            (
                "Screen recording: the smallest repeatable process, step by step.",
                "Part two: build the smallest repeatable process. Small enough that you can run it this week.",
            ),
            (
                "Dashboard close-up: a single tracked metric moving over time.",
                "Part three: watch one number. If it does not move, change one variable, not five.",
            ),
            (
                "Split screen: the process before and after the change.",
                f"That is how {keywords[0]} work becomes predictable instead of lucky.",
            ),
            (
                "Host to camera, wide shot, soft outro music.",
                "If this was useful, subscribe for the next part. No hype, just the process.",
            ),
        ]

        body_beats, cta_beat = beats[:-1], beats[-1]
        while len(body_beats) < scene_count - 1:
            body_beats.append(
                ("B-roll of the team reviewing notes.", "Keep the loop short, and review it honestly once a week.")
            )
        beats = body_beats[: max(1, scene_count - 1)] + [cta_beat]
        scenes = []
        step = max(3, seconds // len(beats))
        for index, (visual, voiceover) in enumerate(beats):
            start = index * step
            end = min(seconds, start + step)
            scenes.append(
                {
                    "time": f"{_fmt_time(start)}-{_fmt_time(end)}",
                    "visual": visual,
                    "voiceover": voiceover,
                }
            )

        description_intro = (
            f"A practical walkthrough of {brief['topic']} — what it is, the three parts that "
            "actually matter, and how to run it in a small team without adding overhead."
        )
        chapters = "\n".join(f"{scene['time'].split('-')[0]} Scene {i + 1}" for i, scene in enumerate(scenes))
        description = (
            f"{description_intro}\n\n"
            f"Chapters:\n{chapters}\n\n"
            "This video uses AI-generated visuals and voice narration. "
            "Educational content only — not professional, financial or medical advice."
        )

        phrase = " ".join(keywords[:2])
        tags = [phrase, *[word for word in keywords if word not in phrase.split()]]
        platform_tag = brief["platform"] if brief["platform"] in ("youtube", "facebook") else "video"
        tags += ["ai workflow", "small team", "explainer", "tutorial", platform_tag]
        tags = list(dict.fromkeys([t for t in tags if t]))[:15]

        return {
            "title_options": [
                f"{topic}: A Practical 3-Step Framework",
                f"The Honest Guide to {_topic_lower(topic)}",
                f"{topic} — What Actually Matters",
            ],
            "hook": scenes[0]["voiceover"],
            "scenes": scenes,
            "cta": scenes[-1]["voiceover"],
            "description": description,
            "tags": tags,
            "thumbnail_text": " ".join(keywords[:3]).title() or "The Real Process",
        }

    def _offline_bengali(self, brief: Dict[str, Any]) -> Dict[str, Any]:
        """Deterministic Bengali / Banglish script used when no provider is configured."""
        bn = self.lang == "bn"
        topic = brief["topic"]
        seconds = int(brief["duration"])
        scene_count = max(5, min(8, round(seconds / 14)))
        keys = keywords(topic, 6) or (["এআই", "কাজ"] if bn else ["ai", "work"])

        if bn:
            beats: List[Tuple[str, str]] = [
                ("দ্রুত কাট: workflow চলছে, স্ক্রিনে বড় করে বিষয় লেখা।",
                 f"সবাই {topic} নিয়ে কথা বলে, কিন্তু আসলে কী কাজ করে তা কেউ সহজভাবে বলে না।"),
                ("উপস্থাপক ক্যামেরার সামনে, পেছনে তিন ধাপের ডায়াগ্রাম।",
                 f"{topic} কোনো জাদু নয় — এটি একটি সিস্টেম, যার তিনটি অংশ আছে।"),
                ("স্ক্রিন রেকর্ডিং: এক বাক্যে লক্ষ্য লেখা হচ্ছে।",
                 "প্রথম ধাপ: এমন একটি ফলাফল ঠিক করুন যা মাপা যায়। এক বাক্য, একটি সংখ্যা, একটি সময়সীমা।"),
                ("স্ক্রিন রেকর্ডিং: ছোট ও পুনরাবৃত্তিযোগ্য প্রক্রিয়া।",
                 "দ্বিতীয় ধাপ: সবচেয়ে ছোট প্রক্রিয়াটি বানান, যা এই সপ্তাহেই চালানো যায়।"),
                ("ড্যাশবোর্ডের ক্লোজ-আপ: একটি মাত্র মেট্রিক বদলাতে দেখা যাচ্ছে।",
                 "তৃতীয় ধাপ: একটি সংখ্যা লক্ষ্য করুন। না বাড়লে পাঁচটি নয়, একটাই জিনিস বদলান।"),
                ("স্প্লিট স্ক্রিন: আগে ও পরে।",
                 f"এভাবেই {keys[0]} নিয়ে কাজ অনুমানের বদলে পূর্বানুমেয় হয়ে ওঠে।"),
                ("উপস্থাপক ক্যামেরার সামনে, নরম আউট্রো সংগীত।",
                 "ভিডিওটি কাজে লাগলে সাবস্ক্রাইব করুন। বাড়তি হাইপ নয়, শুধু প্রক্রিয়া।"),
            ]
            desc_intro = (
                f"{brief['topic']} নিয়ে ব্যবহারিক আলোচনা — আসলে কী, কোন তিনটি অংশ গুরুত্বপূর্ণ, "
                "এবং ছোট টিমে বাড়তি ঝামেলা ছাড়াই এটি কীভাবে চালানো যায়।"
            )
            disclosure = (
                "এই ভিডিওতে এআই-জেনারেটেড ভিজ্যুয়াল ও ভয়েস নারেশন ব্যবহার করা হয়েছে। "
                "শুধু শিক্ষামূলক তথ্য — পেশাদার, আর্থিক বা চিকিৎসা পরামর্শ নয়।"
            )
            titles = [
                f"{topic}: বাস্তব ৩ ধাপের ফ্রেমওয়ার্ক",
                f"{topic} — সহজ ভাষায় গাইড",
                f"{topic}: আসলে যা গুরুত্বপূর্ণ",
            ]
            chapter_word = "দৃশ্য"
            extra_tags = ["এআই", "বাংলা", "টিউটোরিয়াল", "ছোট টিম", "ভিডিও"]
            thumbnail = " ".join(keys[:3]) or "আসল প্রক্রিয়া"
        else:
            beats = [
                ("Cold open: fast cuts of the workflow, bold topic overlay.",
                 f"Sobai {topic} niye kotha bole, kintu asole ki kaj kore seta keu shohoj kore bole na."),
                ("Host on camera with a three-step diagram behind.",
                 f"{topic} kono jadu na — eta ekta system, jar tin ta ongsho ache."),
                ("Screen recording: writing the outcome in one sentence.",
                 "Prothom dhap: emon ekta result thik koro ja mapa jay. Ek bakko, ek shongkha, ek deadline."),
                ("Screen recording: the smallest repeatable process.",
                 "Ditiyo dhap: shob cheye choto process ta banao, ja ei shoptah ei chalate paro."),
                ("Dashboard close-up: one tracked metric moving.",
                 "Tritiyo dhap: ekta shongkha dekho. Na barle pach ta na, ekta jinish bodlao."),
                ("Split screen: before and after.",
                 f"Ebhabei {keys[0]} niye kaj onuman er bodole predictable hoy."),
                ("Host to camera, soft outro music.",
                 "Video ta kaj e lagle subscribe koro. Bari hype na, shudhu process."),
            ]
            desc_intro = (
                f"{brief['topic']} niye practical walkthrough — asole ki, kon tin ta ongsho important, "
                "ar choto team e extra jhamela chara eta kivabe chalano jay."
            )
            disclosure = (
                "Ei video te AI-generated visual ar voice narration use kora hoyeche. "
                "Shudhu educational — professional, financial ba medical advice noy."
            )
            titles = [
                f"{topic}: Ekta Practical 3-Dhap Framework",
                f"{topic} — Shohoj Bhashay Guide",
                f"{topic}: Asole Ki Gurottopurno",
            ]
            chapter_word = "Scene"
            extra_tags = ["ai", "bangla", "tutorial", "small team", "video"]
            thumbnail = " ".join(keys[:3]).title() or "The Real Process"

        body_beats, cta_beat = beats[:-1], beats[-1]
        while len(body_beats) < scene_count - 1:
            body_beats.append(
                ("B-roll: the team reviewing notes.", "Loop ta choto rakho, ar shoptah e ekbar honestly review koro.")
                if not bn else
                ("বি-রোল: টিম নোট দেখছে।", "লুপটি ছোট রাখুন, আর সপ্তাহে একবার সৎভাবে রিভিউ করুন।")
            )
        beats = body_beats[: max(1, scene_count - 1)] + [cta_beat]

        scenes = []
        step = max(3, seconds // len(beats))
        for index, (visual, voiceover) in enumerate(beats):
            start = index * step
            end = min(seconds, start + step)
            scenes.append(
                {"time": f"{_fmt_time(start)}-{_fmt_time(end)}", "visual": visual, "voiceover": voiceover}
            )

        chapters = "\n".join(
            f"{scene['time'].split('-')[0]} {chapter_word} {i + 1}" for i, scene in enumerate(scenes)
        )
        description = f"{desc_intro}\n\n{'Chapters' if not bn else 'অধ্যায়'}:\n{chapters}\n\n{disclosure}"

        platform_tag = brief["platform"] if brief["platform"] in ("youtube", "facebook") else "video"
        tags = list(dict.fromkeys([*keys, *extra_tags, platform_tag]))[:15]

        return {
            "title_options": titles,
            "hook": scenes[0]["voiceover"],
            "scenes": scenes,
            "cta": scenes[-1]["voiceover"],
            "description": description,
            "tags": tags,
            "thumbnail_text": thumbnail[:60],
        }

    def _normalize_script(self, script: Dict[str, Any], brief: Dict[str, Any]) -> Dict[str, Any]:
        titles = script.get("title_options") or script.get("titles") or []
        if isinstance(titles, str):
            titles = [titles]
        titles = [str(t).strip()[:95] for t in titles if str(t).strip()]
        if not titles:
            titles = [
                f"{_topic_case(brief['topic'])} — A Practical Guide"
                if self.lang == "en" else
                f"{brief['topic']} — একটি ব্যবহারিক গাইড"
            ]

        raw_scenes = script.get("scenes") or []
        scenes: List[Dict[str, str]] = []
        for index, scene in enumerate(raw_scenes):
            if isinstance(scene, str):
                scenes.append({"time": f"0:{index * 5:02d}", "visual": "B-roll", "voiceover": scene})
            elif isinstance(scene, dict):
                scenes.append(
                    {
                        "time": str(scene.get("time") or f"0:{index * 5:02d}"),
                        "visual": str(scene.get("visual") or "B-roll").strip(),
                        "voiceover": str(scene.get("voiceover") or scene.get("vo") or "").strip(),
                    }
                )
        if not scenes:
            scenes = self._offline_script(brief)["scenes"]

        tags = script.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",")]
        tags = [str(t).strip() for t in tags if str(t).strip()][:15]

        description = str(script.get("description") or "").strip()
        markers = ("ai-generated", "ai generated", "এআই", "কৃত্রিম বুদ্ধিমত্তা", "ai diye", "ai-generated")
        if not any(marker in description.lower() for marker in markers):
            if self.lang == "bn":
                description += (
                    "\n\nএই ভিডিওতে এআই-জেনারেটেড ভিজ্যুয়াল ও ভয়েস নারেশন ব্যবহার করা হয়েছে। "
                    "শুধু শিক্ষামূলক তথ্য — পেশাদার, আর্থিক বা চিকিৎসা পরামর্শ নয়।"
                )
            elif self.lang == "banglish":
                description += (
                    "\n\nEi video te AI-generated visual ar voice narration use kora hoyeche. "
                    "Shudhu educational — professional, financial ba medical advice noy."
                )
            else:
                description += (
                    "\n\nThis video uses AI-generated visuals and voice narration. "
                    "Educational content only — not professional, financial or medical advice."
                )
        if len(description) < rules.DESCRIPTION_MIN:
            description += (
                f"\n\nএই পর্বে {brief['topic']} নিয়ে ধাপে ধাপে আলোচনা, বাস্তব উদাহরণ এবং "
                "যে একটি মেট্রিক লক্ষ্য করা উচিত তা দেওয়া হয়েছে।"
                if self.lang == "bn" else
                f"\n\nAbout this episode: a practical, no-hype walkthrough of {brief['topic']} "
                "with the steps, the trade-offs and the one metric worth tracking."
            )

        return {
            "title_options": titles[:4],
            "hook": str(script.get("hook") or scenes[0]["voiceover"]).strip(),
            "scenes": scenes,
            "cta": str(script.get("cta") or scenes[-1]["voiceover"]).strip(),
            "description": description,
            "tags": tags or ["ai workflow", "explainer", "tutorial"],
            "thumbnail_text": str(script.get("thumbnail_text") or "The Real Process")[:60],
        }

    # -- compliance ---------------------------------------------------------
    def _body(self, script: Dict[str, Any]) -> str:
        parts = [script.get("hook", ""), script.get("cta", "")]
        parts += [scene.get("voiceover", "") for scene in script.get("scenes", [])]
        return "\n".join(part for part in parts if part)

    def _gate(self, script: Dict[str, Any], brief: Dict[str, Any]) -> Dict[str, Any]:
        return rules.evaluate(
            script=self._body(script),
            title=script["title_options"][0],
            description=script["description"],
            tags=script["tags"],
            platform=brief["platform"],
            ai_generated=True,
            paid_promotion=bool(brief.get("paid_promotion")),
        )

    def _sanitize_script(self, script: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
        changes: List[str] = []

        def clean(value: str) -> str:
            new_value, applied = rules.sanitize(value)
            changes.extend(applied)
            return new_value

        for scene in script["scenes"]:
            scene["voiceover"] = clean(scene["voiceover"])
            scene["visual"] = clean(scene["visual"])
        script["title_options"] = [clean(t) for t in script["title_options"]]
        script["hook"] = clean(script["hook"])
        script["cta"] = clean(script["cta"])
        script["description"] = clean(script["description"])
        return script, changes

    # -- reply --------------------------------------------------------------
    def _format_reply(
        self,
        script: Dict[str, Any],
        report: Dict[str, Any],
        brief: Dict[str, Any],
        mode: str,
        note: str,
    ) -> str:
        bl = lambda key: label(self.lang, key)  # noqa: E731
        bn = self.lang == "bn"
        lines = [
            f"{bl('delivered')} — {_topic_case(brief['topic'])}",
            f"{brief['duration']}s · {brief['platform']} · {brief['tone']} · {mode} · "
            f"{len(script['scenes'])} {'দৃশ্য' if bn else 'scenes'}",
            "",
            bl("titles"),
        ]
        for index, title in enumerate(script["title_options"], start=1):
            lines.append(f"  {index}. {title}")

        lines += ["", bl("hook"), f"  {script['hook']}", "", bl("scenes")]
        for index, scene in enumerate(script["scenes"], start=1):
            lines.append(f"  {'দৃশ্য' if bn else 'Scene'} {index} [{scene['time']}]")
            lines.append(f"    {bl('visual')}: {scene['visual']}")
            lines.append(f"    {bl('vo')}: {scene['voiceover']}")

        lines += [
            "",
            bl("cta"),
            f"  {script['cta']}",
            "",
            bl("description"),
            *[f"  {line}" for line in script["description"].splitlines() if line.strip()],
            "",
            f"{bl('tags')}: {', '.join(script['tags'])}",
            f"{bl('thumbnail')}: {script['thumbnail_text']}",
            "",
            rules.format_report(report),
        ]
        if note:
            lines.append(f"{'নোট' if bn else 'Note'}: {note}")
        if report["ok"]:
            lines.append(
                "পরবর্তী: 'রিভাইজ: <পরিবর্তন>' দিয়ে সংশোধন করুন, অথবা এটি ভিডিও জেনারেশনে পাঠান।"
                if bn else
                "NEXT: revise with 'revise: <change>', or hand this to VIDEO GENERATION."
            )
        else:
            lines.append(
                "পরবর্তী: উপরের ব্লকারগুলো ঠিক না হলে এই খসড়া প্রকাশ করা যাবে না।"
                if bn else
                "NEXT: this draft is not publishable until the blockers above are resolved."
            )
        return "\n".join(lines)

    # -- snapshot -----------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        data = super().snapshot()
        if self.last_script and self.last_report:
            data["script"] = {
                "title": self.last_script["title_options"][0],
                "scenes": len(self.last_script["scenes"]),
                "compliance_score": self.last_report["score"],
                "verdict": self.last_report["verdict"],
                "mode": self.last_mode,
            }
        return data


def build(bus: EventBus, keys: ApiKeyStore) -> ScriptWritingAgent:
    return ScriptWritingAgent(bus, keys)
