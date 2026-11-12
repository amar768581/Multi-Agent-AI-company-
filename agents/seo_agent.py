"""SEO AGENT — keyword targeting, metadata & on-page SEO.

Publishes the final metadata package for a video or ebook:

* focus keyword set and search-intent map,
* title (≤100 chars), description with chapters/timestamps, tags, hashtags,
* thumbnail text, pinned comment, alternate titles,
* platform-specific publishing checklist (YouTube + Facebook),
* compliance gate over the metadata and narration.

Consumes the SCRIPT WRITING script or the VIDEO GENERATION storyboard through
``self.registry``; falls back to a standalone keyword brief when neither exists.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from compliance import monetization_rules as rules
from config.api_keys import ApiKeyStore
from core import llm
from core.base_agent import AgentBase
from core.events import EventBus
from core.language import (
    clean_topic,
    detect as detect_language,
    keywords as extract_keywords,
    label,
    normalize_commands,
)

AGENT_SPEC = {
    "id": "seo_agent",
    "name": "SEO AGENT",
    "role": "Keyword & On-Page SEO",
    "accent": "#4fd1ff",
}

SEO_PROMPT = """You are the SEO department of CogniSphere Labs.
Write metadata for a video that must rank and stay monetization-ready.

Rules:
- Titles must accurately describe the content, sentence case, ≤100 characters.
- No clickbait, no fake urgency, no keyword stuffing.
- Description ≥120 characters, includes timestamps when scenes are given, plus the
  AI-generated disclosure and a non-advice disclaimer.
- Tags: 5-15 specific phrases a viewer would actually search.
- Hashtags: exactly 3, relevant, no #followforfollow.

{rules}

Return ONLY minified JSON:
{{"title": "<=100 chars",
  "description": ">=150 chars with Timestamps and disclosures",
  "tags": ["5-15"],
  "hashtags": ["3 without #"],
  "pinned_comment": "1-2 sentences",
  "thumbnail_text": "max 5 words",
  "alternate_titles": ["2 accurate alternatives"]}}
"""

STOPWORDS = {
    "about", "after", "again", "against", "also", "because", "before", "being",
    "between", "could", "every", "from", "have", "into", "just", "like", "make",
    "many", "more", "most", "much", "need", "only", "other", "over", "should",
    "some", "such", "than", "that", "their", "them", "then", "there", "these",
    "they", "this", "those", "through", "under", "very", "want", "were", "what",
    "when", "where", "which", "while", "will", "with", "would", "your",
}

PLATFORM_NOTES = {
    "youtube": [
        "Enable the AI-generated / altered-content disclosure before publishing.",
        "Add chapters (0:00) — already included in the description below.",
        "Set the end screen to the next video in the series.",
    ],
    "facebook": [
        "Add the paid-partnership label if the video was sponsored.",
        "Upload captions — most Reels/feed views start muted.",
        "Select the closest topic tag so the video enters the right recommendation pool.",
    ],
}


def _keywords(text: str, limit: int = 8) -> List[str]:
    words = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 3 and w not in STOPWORDS]
    seen: List[str] = []
    for word in words:
        if word not in seen:
            seen.append(word)
    return seen[:limit]


def _truncate(value: str, limit: int) -> str:
    value = value.strip()
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


class SEOAgent(AgentBase):
    id = AGENT_SPEC["id"]
    name = AGENT_SPEC["name"]
    role = AGENT_SPEC["role"]
    accent = AGENT_SPEC["accent"]
    providers = ["gemini", "groq", "openai"]
    persisted_artifacts = {
        "package": "last_package",
        "report": "last_report",
        "source": "last_source",
    }

    def __init__(self, bus: EventBus, keys: ApiKeyStore) -> None:
        super().__init__(bus, keys)
        self.last_package: Optional[Dict[str, Any]] = None
        self.last_report: Optional[Dict[str, Any]] = None
        self.last_source: Optional[Dict[str, Any]] = None

    # -- entry point --------------------------------------------------------
    async def handle(self, message: str, context: Optional[Dict[str, Any]] = None) -> str:
        text = (message or "").strip()
        if not text:
            return "Say 'optimize the last video', or 'keywords: <topic>'."
        self.record("user", text)
        self.lang = detect_language(text)
        low = normalize_commands(text).lower()

        if low.startswith(("status", "report", "progress")):
            reply = self._status()
        elif low in {"help", "commands"}:
            reply = self._help()
        else:
            reply = await self._produce(text)

        self.record("agent", reply)
        self.bus.bump()
        return reply

    def _help(self) -> str:
        if self.lang == "bn":
            return (
                "এসইও এজেন্ট — কমান্ড\n"
                "• 'সর্বশেষ ভিডিওর এসইও করো' → ভিডিও জেনারেশন / স্ক্রিপ্ট রাইটিং থেকে মেটাডেটা\n"
                "• 'কীওয়ার্ড: বিগিনারদের জন্য এআই ভিডিও এডিটিং' → স্বতন্ত্র কীওয়ার্ড প্যাকেজ\n"
                "• 'স্ট্যাটাস' → প্রোভাইডার, সর্বশেষ প্যাকেজ, কমপ্লায়েন্স স্কোর\n"
                "আউটপুট: শিরোনাম, টাইমস্ট্যাম্পসহ বিবরণ, ট্যাগ, হ্যাশট্যাগ, থাম্বনেইল টেক্সট, "
                "পিন করা মন্তব্য, বিকল্প শিরোনাম ও প্ল্যাটফর্ম চেকলিস্ট।"
            )
        return (
            "SEO AGENT — commands\n"
            "• 'optimize the last video' → metadata from VIDEO GENERATION / SCRIPT WRITING\n"
            "• 'keywords: AI video editing for beginners' → standalone keyword package\n"
            "• 'status' → provider, last package, compliance score\n"
            "Output: title, description with timestamps, tags, hashtags, thumbnail text, "
            "pinned comment, alternate titles and platform checklists."
        )

    def _status(self) -> str:
        candidates = self.keys.candidates(self.id, list(llm.SUPPORTED_PROVIDERS))
        provider, key = candidates[0] if candidates else (None, None)
        pool = len(candidates)
        if self.lang == "bn":
            lines = [
                "এসইও এজেন্ট — স্ট্যাটাস",
                f"প্রোভাইডার: {provider or 'অফলাইন টেমপ্লেট'} ({'কী সেট আছে' if key else 'কী নেই'}) · কী পুল: {pool}",
            ]
            if self.last_package:
                lines += [
                    f"সর্বশেষ প্যাকেজ: {self.last_package['title'][:80]}",
                    f"ট্যাগ: {len(self.last_package['tags'])} · বিবরণ: {len(self.last_package['description'])} অক্ষর",
                    f"কমপ্লায়েন্স: {self.last_report['score']}/100 · {self.last_report['verdict']}"
                    if self.last_report else "কমপ্লায়েন্স: যাচাই হয়নি",
                ]
            else:
                lines.append("এই সেশনে এখনো কোনো মেটাডেটা প্যাকেজ তৈরি হয়নি।")
            return "\n".join(lines)
        lines = [
            "SEO AGENT — status",
            f"Provider: {provider or 'offline template'} ({'key set' if key else 'no key'}) · key pool: {pool}",
        ]
        if self.last_package:
            lines += [
                f"Last package: {self.last_package['title'][:80]}",
                f"Tags: {len(self.last_package['tags'])} · description: {len(self.last_package['description'])} chars",
                f"Compliance: {self.last_report['score']}/100 · {self.last_report['verdict']}"
                if self.last_report
                else "Compliance: not evaluated",
            ]
        else:
            lines.append("No metadata package produced yet in this session.")
        return "\n".join(lines)

    # -- sources ------------------------------------------------------------
    def _latest_plan(self) -> Optional[Dict[str, Any]]:
        if not self.registry:
            return None
        video = self.registry.get("video_generation")
        return getattr(video, "last_plan", None) if video else None

    def _latest_script(self) -> Optional[Dict[str, Any]]:
        if not self.registry:
            return None
        script = self.registry.get("script_writing")
        return getattr(script, "last_script", None) if script else None

    def _source(self, text: str) -> Dict[str, Any]:
        low = normalize_commands(text).lower()
        if self.lang != "en":
            standalone = re.match(r"^(?:keywords?|seo)\s*:?\s*(.+)$", low)
            if standalone:
                topic = clean_topic(standalone.group(1))
                base = topic.split(":")[0].strip() or topic
                return {
                    "title": (f"{base}: আসলে যা গুরুত্বপূর্ণ" if self.lang == "bn"
                              else f"{base[:1].upper() + base[1:]}: What Actually Matters"),
                    "scenes": [],
                    "description": "",
                    "tags": [],
                    "origin": "standalone keyword brief" if self.lang == "en" else "স্বতন্ত্র কীওয়ার্ড ব্রিফ",
                    "platform": "both",
                }
        topic_match = re.search(r"(?:keywords?|seo)\s*(?:for|on|about)?\s*:\s*(.+)", text, flags=re.I)
        if topic_match:
            topic = topic_match.group(1).strip()
            base = topic.split(":")[0].strip() or topic
            return {
                "title": f"{base[:1].upper() + base[1:]}: What Actually Matters",
                "scenes": [],
                "description": "",
                "tags": [],
                "origin": "standalone keyword brief",
                "platform": "both",
            }

        plan = self._latest_plan()
        script = self._latest_script()
        use_plan = plan and (
            any(word in low for word in ("the video", "last video", "storyboard", "package", "video"))
            or len(text.split()) <= 4
            or not script
        )

        if use_plan and plan:
            scenes = [
                {"time": shot.get("timecode", ""), "voiceover": shot.get("voiceover", "")}
                for shot in plan.get("shots") or []
            ]
            return {
                "title": plan.get("title") or "Untitled video",
                "scenes": scenes,
                "description": plan.get("description", ""),
                "tags": plan.get("tags") or [],
                "origin": "VIDEO GENERATION storyboard",
                "platform": "youtube",
            }

        if script:
            return {
                "title": (script.get("title_options") or ["Untitled video"])[0],
                "scenes": script.get("scenes") or [],
                "description": script.get("description", ""),
                "tags": script.get("tags") or [],
                "origin": "SCRIPT WRITING script",
                "platform": "youtube",
            }

        if self.lang in ("bn", "banglish"):
            topic = clean_topic(text)
        else:
            topic = re.sub(r"^(?:please\s+)?(?:optimize|write|create|generate|make)\s*", "", text, flags=re.I).strip(" :.-")
        base = topic.split(":")[0].strip() or topic
        return {
            "title": (
                f"{base}: আসলে যা গুরুত্বপূর্ণ"
                if self.lang == "bn" else
                f"{base[:1].upper() + base[1:]}: What Actually Matters"
            ),
            "scenes": [],
            "description": "",
            "tags": [],
            "origin": "freestanding brief" if self.lang == "en" else "স্বতন্ত্র ব্রিফ",
            "platform": "both",
        }

    # -- offline metadata ---------------------------------------------------
    def _chapters(self, scenes: List[Dict[str, Any]]) -> List[str]:
        bn = self.lang == "bn"
        chapters = []
        for index, scene in enumerate(scenes):
            timecode = str(scene.get("time") or "")
            start = timecode.split("-")[0].strip()
            if not re.match(r"^\d{1,2}:\d{2}$", start):
                start = f"{index * 5 // 60}:{index * 5 % 60:02d}"
            label = (
                ("ভূমিকা" if index == 0 else ("উপসংহার" if index == len(scenes) - 1 else f"ধাপ {index}"))
                if bn else
                ("Intro" if index == 0 else ("Wrap-up" if index == len(scenes) - 1 else f"Step {index}"))
            )
            voice = str(scene.get("voiceover") or "").strip()
            if voice and index not in (0, len(scenes) - 1):
                label = _truncate(re.sub(r"[:\-–—].*$", "", voice), 46) or label
            chapters.append(f"{start} {label}")
        return chapters

    def _offline_package(self, source: Dict[str, Any]) -> Dict[str, Any]:
        if self.lang in ("bn", "banglish"):
            return self._offline_package_bengali(source)
        title = _truncate(source["title"], 100)
        words = _keywords(f"{title} {' '.join(str(s.get('voiceover', '')) for s in source['scenes'])}")
        focus = " ".join(words[:2]) or "ai workflow"

        chapters = self._chapters(source.get("scenes") or [])
        chapter_block = "\n".join(chapters)
        description_parts = [
            f"{title} — the process, not the hype. You get the three parts that actually matter, "
            "the first step to run this week, and the single metric worth tracking.",
        ]
        if chapter_block:
            description_parts.append(f"Timestamps:\n{chapter_block}")
        description_parts.append(
            "This video uses AI-generated visuals and voice narration. "
            "Educational content only — not professional, financial or medical advice."
        )
        description = "\n\n".join(description_parts)

        tags = list(dict.fromkeys([focus, *words[:5], "ai workflow", "explainer", "tutorial", "how to"]))[:15]

        return {
            "title": title,
            "description": description,
            "tags": tags,
            "hashtags": ["#aiworkflow", "#smallteam", "#explainer"],
            "pinned_comment": (
                "Chapters are in the description. Which step will you run first? "
                "I read every reply and cover the common blockers in the next video."
            ),
            "thumbnail_text": " ".join(words[:3]).title() or "The Real Process",
            "alternate_titles": [
                _truncate(f"{focus.title()}: A Practical 3-Step Framework", 100),
                _truncate(f"The Honest Guide to {focus}", 100),
            ],
            "focus_keywords": words[:6] or ["ai workflow"],
            "provider": "offline template",
        }

    def _offline_package_bengali(self, source: Dict[str, Any]) -> Dict[str, Any]:
        bn = self.lang == "bn"
        title = _truncate(source["title"], 100)
        words = extract_keywords(
            f"{title} " + " ".join(str(s.get("voiceover", "")) for s in source.get("scenes") or []), 8
        )
        focus = " ".join(words[:2]) or ("এআই ওয়ার্কফ্লো" if bn else "ai workflow")
        chapters = self._chapters(source.get("scenes") or [])
        chapter_block = "\n".join(chapters)

        if bn:
            description_parts = [
                f"{title} — প্রক্রিয়াটাই আসল, হাইপ নয়। যে তিনটি অংশ সত্যিই গুরুত্বপূর্ণ, "
                "এই সপ্তাহে চালানোর প্রথম ধাপ, আর যে একটি মেট্রিক লক্ষ্য করা উচিত — সব একসাথে।",
            ]
            if chapter_block:
                description_parts.append(f"টাইমস্ট্যাম্প:\n{chapter_block}")
            description_parts.append(
                "এই ভিডিওতে এআই-জেনারেটেড ভিজ্যুয়াল ও ভয়েস নারেশন ব্যবহার করা হয়েছে। "
                "শুধু শিক্ষামূলক তথ্য — পেশাদার, আর্থিক বা চিকিৎসা পরামর্শ নয়।"
            )
            tags = list(dict.fromkeys([focus, *words[:6], "এআই", "টিউটোরিয়াল", "বাংলা", "কীভাবে"]))[:15]
            return {
                "title": title,
                "description": "\n\n".join(description_parts),
                "tags": tags,
                "hashtags": ["aibangla", "banglatutorial", "explainer"],
                "pinned_comment": (
                    "চ্যাপ্টারগুলো বিবরণে আছে। আপনি প্রথমে কোন ধাপটি চালাবেন? "
                    "প্রতিটি উত্তরের উত্তর দিই এবং সাধারণ সমস্যাগুলো পরের ভিডিওতে তুলে ধরি।"
                ),
                "thumbnail_text": " ".join(words[:3]) or "আসল প্রক্রিয়া",
                "alternate_titles": [
                    _truncate(f"{focus}: বাস্তব ৩ ধাপের ফ্রেমওয়ার্ক", 100),
                    _truncate(f"{focus} — সৎ গাইড", 100),
                ],
                "focus_keywords": words[:6] or ["এআই ওয়ার্কফ্লো"],
                "provider": "offline template",
                "lang": self.lang,
            }

        description_parts = [
            f"{title} — process ta i asol, hype na. Tin ta ongsho ja asole important, "
            "ei shoptah e chalanor prothom dhap, ar je ekta metric dekhte hobe — shob ek sathe.",
        ]
        if chapter_block:
            description_parts.append(f"Timestamps:\n{chapter_block}")
        description_parts.append(
            "Ei video te AI-generated visual ar voice narration use kora hoyeche. "
            "Shudhu educational — professional, financial ba medical advice noy."
        )
        tags = list(dict.fromkeys([focus, *words[:6], "ai", "bangla", "tutorial", "kivabe"]))[:15]
        return {
            "title": title,
            "description": "\n\n".join(description_parts),
            "tags": tags,
            "hashtags": ["aibangla", "banglatutorial", "explainer"],
            "pinned_comment": (
                "Chapter gulo description e ache. Tumi prothome kon dhap ta chalabe? "
                "Protiti reply er uttor dei ar common blocker gulo porer video te tuli dhorii."
            ),
            "thumbnail_text": " ".join(words[:3]) or "The Real Process",
            "alternate_titles": [
                _truncate(f"{focus}: Practical 3-Dhap Framework", 100),
                _truncate(f"{focus} — Shotto Guide", 100),
            ],
            "focus_keywords": words[:6] or ["ai workflow"],
            "provider": "offline template",
            "lang": self.lang,
        }

    def _parse_json(self, raw: str) -> Optional[Dict[str, Any]]:
        cleaned = re.sub(r"^```(?:json)?", "", raw.strip()).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            data = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    def _normalize(self, package: Dict[str, Any]) -> Dict[str, Any]:
        title = _truncate(str(package.get("title") or ""), 100)
        description = str(package.get("description") or "").strip()
        markers = ("ai-generated", "ai generated", "এআই", "কৃত্রিম বুদ্ধিমত্তা", "ai diye")
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
        tags = package.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",")]
        tags = list(dict.fromkeys([str(t).strip() for t in tags if str(t).strip()]))[:15]
        hashtags = package.get("hashtags") or []
        if isinstance(hashtags, str):
            hashtags = [h.strip() for h in hashtags.split() if h.strip()]
        hashtags = [h.lstrip("#") for h in hashtags][:3]

        focus_keywords = package.get("focus_keywords") or _keywords(title, 4)
        return {
            "title": title,
            "description": description,
            "tags": tags or ["ai workflow", "explainer", "tutorial", "how to", "small team"],
            "hashtags": hashtags or ["aiworkflow", "smallteam", "explainer"],
            "pinned_comment": str(package.get("pinned_comment") or "").strip(),
            "thumbnail_text": _truncate(str(package.get("thumbnail_text") or "The Real Process"), 40),
            "alternate_titles": [str(t) for t in (package.get("alternate_titles") or [])][:3],
            "focus_keywords": focus_keywords,
            "provider": package.get("provider", "offline template"),
        }

    # -- pipeline -----------------------------------------------------------
    async def _produce(self, text: str) -> str:
        source = self._source(text)
        self.last_source = source
        self.set_state(status="working", progress=10, current_task=f"optimising: {source['title'][:46]}")
        self.log(f"seo source: {source['origin']}")

        candidates = self.keys.candidates(self.id, list(llm.SUPPORTED_PROVIDERS))
        package: Optional[Dict[str, Any]] = None
        note = ""

        if candidates:
            self.set_state(progress=40, current_task=f"writing metadata with {candidates[0][0]}")
            scenes = "\n".join(
                f"{scene.get('time', '')} — {str(scene.get('voiceover', ''))[:120]}"
                for scene in source["scenes"]
            )
            try:
                raw, used = await llm.generate_first(
                    candidates,
                    SEO_PROMPT.format(rules=rules.compliance_brief()),
                    "\n".join(
                        [
                            f"Working title: {source['title']}",
                            f"Existing description: {source['description'][:600] or 'none'}",
                            f"Existing tags: {', '.join(source['tags'][:10]) or 'none'}",
                            "Scenes/timestamps:",
                            scenes or "not supplied",
                            f"Directive: {text}",
                        ]
                    ),
                    temperature=0.5,
                    max_tokens=1800,
                    language=self.lang,
                )
                if used != candidates[0][0]:
                    note = f"key fallback → {used} after a provider failure"
                parsed = self._parse_json(raw)
                if parsed and parsed.get("title") and parsed.get("description"):
                    parsed["provider"] = str(provider)
                    package = parsed
                else:
                    note = "provider output unusable; offline metadata used"
                    self.log("SEO JSON invalid", "warn")
            except llm.LLMError as exc:
                note = f"provider error — {exc}"
                self.log(f"LLM unavailable: {exc}", "warn")

        if package is None:
            package = self._offline_package(source)
            if note == "" and llm.supports(provider) and key:
                note = "offline metadata used"

        package = self._normalize(package)
        package["title"], _ = rules.sanitize(package["title"])
        package["description"], _ = rules.sanitize(package["description"])
        package["tags"] = [rules.sanitize(str(tag))[0] for tag in package["tags"]]

        report = self._gate(package, source)
        self.last_package = package
        self.last_report = report
        self.persist_artifacts()
        checks = self._checks(package)

        passed = sum(1 for _, ok, _ in checks if ok)
        self.set_state(
            status="review",
            progress=100,
            current_task=f"{len(package['tags'])} tags · {passed}/{len(checks)} SEO checks · {report['score']}/100",
        )
        self.log(
            f"metadata ready — {passed}/{len(checks)} checks, compliance {report['score']}/100",
            "success" if report["ok"] else "error",
        )
        return self._format_reply(package, report, checks, source, note)

    def _gate(self, package: Dict[str, Any], source: Dict[str, Any]) -> Dict[str, Any]:
        narration = "\n".join(str(scene.get("voiceover", "")) for scene in source["scenes"])
        return rules.evaluate(
            script=narration,
            title=package["title"],
            description=package["description"],
            tags=package["tags"],
            platform="both",
            ai_generated=True,
        )

    def _checks(self, package: Dict[str, Any]) -> List[Tuple[str, bool, str]]:
        focus = (package.get("focus_keywords") or [""])[0]
        title = package["title"]
        description = package["description"]
        tags = package["tags"]
        return [
            ("Title length", 10 <= len(title) <= rules.TITLE_MAX, f"{len(title)}/{rules.TITLE_MAX} chars"),
            (
                "Focus keyword in title",
                bool(focus) and focus.lower() in title.lower(),
                f"focus: {focus or '—'}",
            ),
            (
                "Description depth",
                len(description) >= rules.DESCRIPTION_MIN,
                f"{len(description)} chars (min {rules.DESCRIPTION_MIN})",
            ),
            ("Tag count", rules.TAGS_MIN <= len(tags) <= rules.TAGS_MAX, f"{len(tags)} tags"),
            (
                "AI disclosure present",
                any(m in description.lower() for m in ("ai-generated", "ai generated", "এআই", "কৃত্রিম বুদ্ধিমত্তা", "ai diye")),
                "synthetic media disclosure",
            ),
            (
                "Thumbnail text short",
                len(package["thumbnail_text"].split()) <= 6,
                f"{len(package['thumbnail_text'].split())} words",
            ),
            ("Hashtags", len(package["hashtags"]) == 3, f"{len(package['hashtags'])} hashtags"),
        ]

    # -- reply --------------------------------------------------------------
    def _format_reply(
        self,
        package: Dict[str, Any],
        report: Dict[str, Any],
        checks: List[Tuple[str, bool, str]],
        source: Dict[str, Any],
        note: str,
    ) -> str:
        bl = lambda key: label(self.lang, key)  # noqa: E731
        bn = self.lang == "bn"
        lines = [
            f"{bl('delivered')} — {package['title']}",
            f"{source['origin']} · {package['provider']} · {len(package['tags'])} {'ট্যাগ' if bn else 'tags'} · "
            f"{'বিবরণ' if bn else 'description'} {len(package['description'])} {'অক্ষর' if bn else 'chars'}",
            "",
            f"{'শিরোনাম' if bn else 'TITLE'} ({len(package['title'])}/100)",
            f"  {package['title']}",
            "",
            bl("description"),
            *[f"  {line}" for line in package["description"].splitlines() if line.strip()],
            "",
            f"{bl('tags')} ({len(package['tags'])})",
            f"  {', '.join(package['tags'])}",
            "",
            "হ্যাশট্যাগ" if bn else "HASHTAGS",
            f"  {' '.join('#' + tag for tag in package['hashtags'])}",
            "",
            "থাম্বনেইল / পিন করা মন্তব্য" if bn else "THUMBNAIL / PINNED",
            f"  thumbnail: {package['thumbnail_text']}",
            f"  pinned: {package['pinned_comment'] or '—'}",
        ]
        if package.get("alternate_titles"):
            lines += ["", "বিকল্প শিরোনাম" if bn else "ALTERNATE TITLES"]
            for alternate in package["alternate_titles"]:
                lines.append(f"  • {alternate}")

        lines += ["", bl("checks")]
        for check_name, ok, detail in checks:
            lines.append(f"  {'✓' if ok else '✗'} {check_name} — {detail}")

        lines += ["", "প্ল্যাটফর্ম চেকলিস্ট (YouTube)" if bn else "PLATFORM CHECKLIST (YouTube)"]
        for item in PLATFORM_NOTES["youtube"]:
            lines.append(f"  □ {item}")
        lines += ["প্ল্যাটফর্ম চেকলিস্ট (Facebook)" if bn else "PLATFORM CHECKLIST (Facebook)"]
        for item in PLATFORM_NOTES["facebook"]:
            lines.append(f"  □ {item}")

        lines += ["", rules.format_report(report)]
        if note:
            lines.append(f"Note: {note}")
        if report["ok"]:
            lines.append("NEXT: copy this into the upload form, or export the description with the edit.")
        else:
            lines.append("NEXT: resolve the compliance blockers before publishing.")
        return "\n".join(lines)

    # -- snapshot -----------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        data = super().snapshot()
        if self.last_package:
            data["seo"] = {
                "title": self.last_package["title"],
                "tags": len(self.last_package["tags"]),
                "description_chars": len(self.last_package["description"]),
                "compliance_score": self.last_report["score"] if self.last_report else None,
                "provider": self.last_package.get("provider"),
            }
        return data


def build(bus: EventBus, keys: ApiKeyStore) -> SEOAgent:
    return SEOAgent(bus, keys)
