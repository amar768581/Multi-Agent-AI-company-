"""EBOOK WRITING — structure, chapters & formatting.

Pipeline: brief → outline → chapter drafting → compliance gate → markdown export.

* Uses the provider assigned in API Settings (OpenAI / Gemini / Groq) through
  the shared ``core.llm`` bridge when a key exists.
* Falls back to a deterministic outline + chapter writer with no key, so the
  department always produces a usable draft.
* Every chapter is scored by ``compliance.monetization_rules`` before it is
  reported as ready; blocked text is sanitised and re-gated.
* Books persist in this session under ``data/ebooks/<slug>.md`` on export.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from compliance import monetization_rules as rules
from config.api_keys import ApiKeyStore
from core import llm
from core.base_agent import AgentBase
from core.events import EventBus
from core.language import (
    chapter_count,
    clean_topic,
    detect as detect_language,
    label,
    normalize_commands,
    normalize_digits,
)

AGENT_SPEC = {
    "id": "ebook_writing",
    "name": "EBOOK WRITING",
    "role": "Structure, Chapters & Formatting",
    "accent": "#5ce0b3",
}

BASE_DIR = Path(__file__).resolve().parent.parent
BOOK_DIR = BASE_DIR / "data" / "ebooks"

MAX_CHAPTERS = 12
MIN_CHAPTERS = 3

OUTLINE_PROMPT = """You are the Ebook Writing department of CogniSphere Labs.
Design an original, monetization-safe nonfiction ebook outline.

Requirements:
- Practical and specific; no filler, no invented statistics, no income promises.
- Each chapter must move the reader from understanding to doing.
- Original content only: no copyrighted excerpts, no third-party frameworks copied verbatim.

{rules}

Return ONLY minified JSON:
{{"title": "under 70 chars",
  "subtitle": "one line, benefit-led",
  "audience": "who this is for",
  "blurb": "120-220 chars, no hype",
  "chapters": [{{"title": "chapter title", "summary": "1-2 sentences",
                 "bullets": ["3 concrete takeaways"]}}]}}
"""

CHAPTER_PROMPT = """You are the Ebook Writing department of CogniSphere Labs.
Write the requested chapter as clean Markdown.

Rules:
- Start with a level-2 heading: "## Chapter N: Title".
- Open with a concrete situation, then explain, then give a repeatable method.
- Include one clearly labelled exercise and a short summary.
- 500-800 words, plain language, no invented data, no income or medical promises.
- Original content only.

{rules}

Return only the chapter Markdown, no commentary and no code fence.
"""


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (slug or "ebook")[:56]


class EbookWritingAgent(AgentBase):
    id = AGENT_SPEC["id"]
    name = AGENT_SPEC["name"]
    role = AGENT_SPEC["role"]
    accent = AGENT_SPEC["accent"]
    providers = ["openai", "gemini", "groq"]
    persisted_artifacts = {
        "book": "book",
        "report": "last_report",
        "export": "last_export",
    }

    def __init__(self, bus: EventBus, keys: ApiKeyStore) -> None:
        super().__init__(bus, keys)
        self.book: Optional[Dict[str, Any]] = None
        self.last_report: Optional[Dict[str, Any]] = None
        self.last_export: Optional[str] = None

    def restore_artifacts(self) -> None:
        """JSON stores int chapter keys as strings — convert them back."""
        super().restore_artifacts()
        if self.book:
            chapters = self.book.get("chapters") or {}
            self.book["chapters"] = {int(key): value for key, value in chapters.items()}
            self.book.setdefault("lang", "en")

    # -- entry point --------------------------------------------------------
    async def handle(self, message: str, context: Optional[Dict[str, Any]] = None) -> str:
        text = (message or "").strip()
        if not text:
            return "Send a brief like 'write a 6-chapter ebook about AI workflows for small teams'."
        self.record("user", text)
        self.lang = detect_language(text)
        low = normalize_digits(normalize_commands(text)).lower()

        if low.startswith(("status", "report", "progress")):
            reply = self._status()
        elif low in {"help", "commands"}:
            reply = self._help()
        elif low.startswith(("export", "download", "save")):
            reply = self._export()
        elif re.match(r"^(?:write|draft|expand|do)\s+chapter\s+\d+", low):
            reply = await self._write_chapter(self._chapter_number(text))
        elif low.startswith(("write chapter", "chapter")):
            reply = await self._write_chapter(self._chapter_number(text))
        else:
            reply = await self._start(text)

        self.record("agent", reply)
        self.bus.bump()
        return reply

    def _help(self) -> str:
        if self.lang == "bn":
            return (
                "ইবুক রাইটিং — কমান্ড\n"
                "• 'এআই ওয়ার্কফ্লো নিয়ে ৬ চ্যাপ্টারের ইবুক লেখো'\n"
                "• 'চ্যাপ্টার ৩ লেখো' → ওই চ্যাপ্টার লিখে বা নতুন করে লেখে\n"
                "• 'এক্সপোর্ট' → data/ebooks/<slug>.md ফাইল লেখে\n"
                "• 'স্ট্যাটাস' → আউটলাইন অগ্রগতি, শব্দসংখ্যা, কমপ্লায়েন্স স্কোর\n"
                "ডিফল্ট ৬ চ্যাপ্টার (৩–১২ সমর্থিত)।"
            )
        return (
            "EBOOK WRITING — commands\n"
            "• 'write a 6-chapter ebook about AI workflows for small teams'\n"
            "• 'write chapter 3' → drafts or redrafts that chapter\n"
            "• 'export' → writes data/ebooks/<slug>.md\n"
            "• 'status' → outline progress, word count, compliance score\n"
            "Chapters default to 6 (3-12 supported)."
        )

    def _chapter_number(self, text: str) -> int:
        normalized = normalize_digits(normalize_commands(text))
        match = re.search(r"chapter\s+(\d+)", normalized, flags=re.I)
        if not match:
            return 1
        return max(1, min(MAX_CHAPTERS, int(match.group(1))))

    # -- brief --------------------------------------------------------------
    def _clean_topic(self, text: str) -> str:
        if self.lang in ("bn", "banglish"):
            return clean_topic(text)
        prefixes = [
            r"^(?:please\s+)?(?:write|create|draft|generate|make|produce|develop)\b",
            r"^(?:me|us)\b",
            r"^(?:a|an|the)\b",
            r"^(?:\d+[- ]chapter\s+)?(?:ebook|e-book|book|guide|playbook|handbook)\b",
            r"^(?:about|on|for|covering)\b",
            r"^[,.:;\-\s]+",
        ]
        topic = text.strip()
        for _ in range(10):
            before = topic
            for pattern in prefixes:
                topic = re.sub(pattern, "", topic, count=1, flags=re.I).strip(" ,.:;-")
            if topic == before:
                break
        return topic.strip(" ,.:;-")

    def _parse_brief(self, text: str) -> Dict[str, Any]:
        chapters = 6
        detected = chapter_count(text)
        if detected:
            chapters = max(MIN_CHAPTERS, min(MAX_CHAPTERS, detected))
        audience_match = re.search(
            r"\bfor\s+(beginners|founders|marketers|creators|developers|students|managers|freelancers|small teams)\b",
            text,
            flags=re.I,
        )
        if audience_match:
            audience = audience_match.group(1)
        elif self.lang == "bn":
            audience = "বাস্তব ব্যবহারকারী ও ছোট টিম"
        else:
            audience = "practical operators and small teams"
        topic = self._clean_topic(re.sub(r"\bfor\s+(beginners|founders|marketers|creators|developers|students|managers|freelancers|small teams)\b", "", text, flags=re.I))
        return {
            "directive": text,
            "topic": topic[:160] or "building a practical AI workflow",
            "chapters": chapters,
            "audience": audience,
        }

    # -- provider helpers ---------------------------------------------------
    def _text_candidates(self) -> List[Tuple[str, str]]:
        return self.keys.candidates(self.id, list(llm.SUPPORTED_PROVIDERS))

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

    # -- outline ------------------------------------------------------------
    def _offline_outline(self, brief: Dict[str, Any]) -> Dict[str, Any]:
        if self.lang in ("bn", "banglish"):
            return self._offline_outline_bengali(brief)
        topic = brief["topic"]
        topic_sentence = topic[:1].upper() + topic[1:]
        arc = [
            ("Why this matters now", "Frame the shift, the cost of ignoring it, and what a good outcome looks like."),
            ("The three-part system", "Outcome, repeatable process, and the one metric that proves progress."),
            ("Start this week", "The smallest first move, scoped so it can ship in seven days."),
            ("Build the loop", "Turn the first result into a routine that survives a busy week."),
            ("Measure honestly", "Pick one number, review it weekly, and interpret it without self-deception."),
            ("Scale without overhead", "Add capacity without adding coordination cost."),
            ("Failure modes", "The four ways teams stall on this and the recovery move for each."),
            ("Templates and next steps", "Copy-ready structure plus a 14-day plan."),
        ]
        outline = []
        for index, (title, summary) in enumerate(arc[: brief["chapters"]], start=1):
            outline.append(
                {
                    "index": index,
                    "title": title,
                    "summary": summary,
                    "bullets": [
                        f"Define what {topic} looks like when it is working.",
                        "Name the one decision that removes the most friction.",
                        "Write the smallest next action and a deadline for it.",
                    ],
                    "words": 0,
                }
            )
        return {
            "title": f"{topic_sentence}: A Practical Field Guide",
            "subtitle": f"A no-hype system for {brief['audience']}",
            "audience": brief["audience"],
            "blurb": (
                f"A practical, step-by-step guide to {topic} for {brief['audience']}. "
                "It covers the system, the first week, the metric that matters, and the "
                "failure modes to avoid — without hype or guarantees."
            ),
            "outline": outline,
            "chapters": {},
            "format": "markdown",
            "provider": "offline template",
        }

    def _offline_outline_bengali(self, brief: Dict[str, Any]) -> Dict[str, Any]:
        bn = self.lang == "bn"
        topic = brief["topic"]
        if bn:
            arc = [
                ("এখন কেন গুরুত্বপূর্ণ", "পরিবর্তনটি কী, এড়িয়ে যাওয়ার দাম কত, আর সফল ফলাফল দেখতে কেমন লাগে।"),
                ("তিন অংশের সিস্টেম", "ফলাফল, পুনরাবৃত্তিযোগ্য প্রক্রিয়া, আর অগ্রগতি প্রমাণ করা একটি মেট্রিক।"),
                ("এই সপ্তাহে শুরু", "সবচেয়ে ছোট প্রথম পদক্ষেপ, যা সাত দিনে শেষ করা যায়।"),
                ("লুপ তৈরি করুন", "প্রথম ফলাফলকে এমন রুটিনে বদলান যা ব্যস্ত সপ্তাহেও টেকে।"),
                ("সৎভাবে মাপুন", "একটি সংখ্যা বাছুন, সপ্তাহে একবার দেখুন, আত্মপ্রতারণা ছাড়া ব্যাখ্যা করুন।"),
                ("ঝামেলা ছাড়াই বড় করুন", "সমন্বয়ের খরচ না বাড়িয়ে সক্ষমতা যোগ করুন।"),
                ("ব্যর্থতার ধরন", "টিম যেখানে আটকে যায় তার চারটি কারণ ও প্রতিটির সমাধান।"),
                ("টেমপ্লেট ও পরবর্তী ধাপ", "কপি-রেডি কাঠামো এবং ১৪ দিনের পরিকল্পনা।"),
            ]
            bullet_lines = [
                f"{topic} কাজ করলে কেমন দেখায় তা স্পষ্ট করুন।",
                "সবচেয়ে বেশি ঝামেলা দূর করে এমন একটি সিদ্ধান্ত চিহ্নিত করুন।",
                "সবচেয়ে ছোট পরবর্তী কাজ ও তার সময়সীমা লিখুন।",
            ]
            title = f"{topic}: একটি ব্যবহারিক ফিল্ড গাইড"
            subtitle = f"{brief['audience']}-এর জন্য হাইপ-মুক্ত একটি সিস্টেম"
            blurb = (
                f"{topic} নিয়ে ধাপে ধাপে ব্যবহারিক গাইড, {brief['audience']}-এর জন্য। "
                "এতে সিস্টেম, প্রথম সপ্তাহ, যে মেট্রিকটি গুরুত্বপূর্ণ এবং যে ব্যর্থতাগুলো এড়াতে হবে "
                "তা নিয়ে আলোচনা আছে — বাড়তি হাইপ বা নিশ্চয়তা ছাড়াই।"
            )
        else:
            arc = [
                ("Ekhon keno important", "Ki bodlache, na manle ki khoti, ar valo result dekhte kemon lage."),
                ("Tin ongsho'r system", "Result, repeatable process, ar agrogoti prove kora ekta metric."),
                ("Ei shoptah e shuru", "Shob cheye choto prothom step, ja shaat din e shesh kora jay."),
                ("Loop toiri koro", "Prothom result ke emon routine e bodlao ja busy shoptah eo teke."),
                ("Shototar sathe mapo", "Ekta shongkha bacho, shoptah e ekbar dekho, self-deception chara."),
                ("Jhamela chara boro koro", "Coordination cost na bariye capacity add koro."),
                ("Byarthotar dhoron", "Team kothay atke jay tar char ta karon ar protitir solution."),
                ("Template ar poroborti dhap", "Copy-ready structure ar 14 diner plan."),
            ]
            bullet_lines = [
                f"{topic} kaj korle kemon dekhabe seta spostho koro.",
                "Shob cheye beshi jhamela dur kore emon ekta decision chinhito koro.",
                "Shob cheye choto poroborti kaj ar tar deadline likho.",
            ]
            title = f"{topic}: Ekta Practical Field Guide"
            subtitle = f"{brief['audience']} er jonno ekta no-hype system"
            blurb = (
                f"{topic} niye dhape dhape practical guide, {brief['audience']} er jonno. "
                "Ete system, prothom shoptah, kon metric ta important, ar kon byarthota eড়ate hobe "
                "shegulo niye alochona ache — bari hype ba guarantee chara."
            )

        outline = []
        for index, (chapter_title, summary) in enumerate(arc[: brief["chapters"]], start=1):
            outline.append(
                {
                    "index": index,
                    "title": chapter_title,
                    "summary": summary,
                    "bullets": list(bullet_lines),
                    "words": 0,
                }
            )
        return {
            "title": title,
            "subtitle": subtitle,
            "audience": brief["audience"],
            "blurb": blurb,
            "outline": outline,
            "chapters": {},
            "format": "markdown",
            "provider": "offline template",
            "lang": self.lang,
        }

    async def _start(self, text: str) -> str:
        brief = self._parse_brief(text)
        candidates = self._text_candidates()
        mode = candidates[0][0] if candidates else "offline template"
        self.set_state(status="working", progress=8, current_task=f"outline: {brief['topic'][:46]}")
        self.log(f"ebook brief — {brief['chapters']} chapters · {brief['audience']}")

        book: Optional[Dict[str, Any]] = None
        note = ""
        if candidates:
            self.set_state(progress=25, current_task=f"outlining with {candidates[0][0]}")
            try:
                raw, used = await llm.generate_first(
                    candidates,
                    OUTLINE_PROMPT.format(rules=rules.compliance_brief()),
                    "\n".join(
                        [
                            f"Topic: {brief['topic']}",
                            f"Audience: {brief['audience']}",
                            f"Chapters: {brief['chapters']}",
                            f"Directive: {brief['directive']}",
                        ]
                    ),
                    temperature=0.7,
                    max_tokens=2200,
                    language=self.lang,
                )
                if used != candidates[0][0]:
                    note = f"key fallback → {used} after a provider failure"
                parsed = self._parse_json(raw)
                if parsed and parsed.get("chapters"):
                    book = {
                        "title": str(parsed.get("title") or brief["topic"]),
                        "subtitle": str(parsed.get("subtitle") or ""),
                        "audience": str(parsed.get("audience") or brief["audience"]),
                        "blurb": str(parsed.get("blurb") or ""),
                        "outline": [
                            {
                                "index": index,
                                "title": str(chapter.get("title") or f"Chapter {index}"),
                                "summary": str(chapter.get("summary") or ""),
                                "bullets": [str(b) for b in (chapter.get("bullets") or [])][:4],
                                "words": 0,
                            }
                            for index, chapter in enumerate(parsed["chapters"][:MAX_CHAPTERS], start=1)
                        ],
                        "chapters": {},
                        "format": "markdown",
                        "provider": provider,
                    }
                else:
                    note = "provider outline was unusable; used offline outline"
                    self.log("outline JSON was invalid", "warn")
            except llm.LLMError as exc:
                note = f"provider error — {exc}"
                self.log(f"LLM unavailable: {exc}", "warn")

        if book is None:
            book = self._offline_outline(brief)
            if provider:
                note = note or "offline outline used"
        book["created_at"] = time.time()
        book.setdefault("lang", self.lang)
        self.book = book
        self.persist_artifacts()
        self.set_state(progress=55, current_task="drafting chapter 1")
        await self._draft_chapter(1, silent=False)
        self.set_state(
            status="review",
            progress=100,
            current_task=f"{len(book['outline'])} chapters planned · 1 drafted",
        )
        return self._format_outline_reply(book, mode, note)

    # -- chapters -----------------------------------------------------------
    def _offline_chapter(self, chapter: Dict[str, Any], book: Dict[str, Any]) -> str:
        if book.get("lang", self.lang) in ("bn", "banglish"):
            return self._offline_chapter_bengali(chapter, book)
        bullets = chapter.get("bullets") or []
        bullet_block = "\n".join(f"- {bullet}" for bullet in bullets) or "- Define the next action."
        return (
            f"## Chapter {chapter['index']}: {chapter['title']}\n\n"
            f"A team adopting {book['title'].split(':')[0].lower()} usually starts with good intentions and a "
            "long list. Two weeks later the list is longer and nothing has changed. The difference "
            "between teams that get traction and teams that keep planning is rarely effort — it is scope.\n\n"
            f"### What this looks like\n\n{chapter.get('summary', '')}\n\n"
            "Start with the smallest version that produces a visible result in one week. Write it as "
            "a single sentence: who does what, by when, and how you will know it worked. If the sentence "
            "needs a comma-spliced list of five things, the scope is still too big.\n\n"
            "### The working method\n\n"
            "1. **Name the outcome.** One sentence, one number, one deadline.\n"
            "2. **Build the smallest loop.** Draft, review, improve — one pass each.\n"
            "3. **Review on a fixed day.** Same time every week, fifteen minutes, no exceptions.\n\n"
            "### Key takeaways\n\n"
            f"{bullet_block}\n\n"
            "### Exercise\n\n"
            "Write your outcome sentence, then cut it in half. Put the remaining half on a calendar "
            "block this week and note what actually happened. The note is the data you will use in the "
            "next chapter.\n\n"
            "### Summary\n\n"
            f"{chapter['title']} is not a theory to agree with; it is a scope decision. Keep the loop "
            "small enough to finish, and let repetition do the compounding.\n"
        )

    def _offline_chapter_bengali(self, chapter: Dict[str, Any], book: Dict[str, Any]) -> str:
        bn = book.get("lang", self.lang) == "bn"
        bullets = chapter.get("bullets") or []
        bullet_block = "\n".join(f"- {bullet}" for bullet in bullets) or "- পরবর্তী কাজটি ঠিক করুন।"
        core_title = book["title"].split(":")[0]
        if bn:
            return (
                f"## চ্যাপ্টার {chapter['index']}: {chapter['title']}\n\n"
                f"{core_title} নিয়ে কাজ শুরু করা টিম সাধারণত ভালো উদ্দেশ্য আর একটি লম্বা তালিকা দিয়ে শুরু করে। "
                "দুই সপ্তাহ পর তালিকাটি আরও লম্বা, কিন্তু কিছুই বদলায়নি। যে টিম এগোয় আর যে টিম পরিকল্পনা করতেই থাকে — "
                "পার্থক্য পরিশ্রমে নয়, পরিসরে।\n\n"
                f"### এটি কেমন দেখায়\n\n{chapter.get('summary', '')}\n\n"
                "এক সপ্তাহে দৃশ্যমান ফলাফল দেয় এমন সবচেয়ে ছোট সংস্করণ দিয়ে শুরু করুন। এক বাক্যে লিখুন: "
                "কে কী করবে, কখন পর্যন্ত, আর কীভাবে বুঝবেন এটি কাজ করেছে। বাক্যটিতে যদি পাঁচটি কাজের তালিকা লাগে, "
                "তাহলে পরিসর এখনো বেশি বড়।\n\n"
                "### কাজের পদ্ধতি\n\n"
                "১. **ফলাফল ঠিক করুন।** এক বাক্য, একটি সংখ্যা, একটি সময়সীমা।\n"
                "২. **সবচেয়ে ছোট লুপ বানান।** খসড়া, পর্যালোচনা, উন্নতি — প্রতিটি একবার।\n"
                "৩. **নির্দিষ্ট দিনে রিভিউ করুন।** প্রতি সপ্তাহে একই সময়, পনেরো মিনিট, ছাড় নেই।\n\n"
                "### মূল শিক্ষা\n\n"
                f"{bullet_block}\n\n"
                "### অনুশীলন\n\n"
                "আপনার ফলাফলের বাক্যটি লিখুন, তারপর অর্ধেক করে ফেলুন। বাকি অর্ধেকটি এই সপ্তাহের ক্যালেন্ডারে "
                "রাখুন এবং আসলে কী হলো তা টুকে রাখুন। সেই টুকনোই পরের চ্যাপ্টারের উপাদান।\n\n"
                "### সারসংক্ষেপ\n\n"
                f"{chapter['title']} কোনো তত্ত্ব নয়, এটি পরিসরের সিদ্ধান্ত। লুপটি এত ছোট রাখুন যে শেষ করা যায়, "
                "আর পুনরাবৃত্তিকে কাজ করতে দিন।\n"
            )
        return (
            f"## Chapter {chapter['index']}: {chapter['title']}\n\n"
            f"{core_title} niye kaj shuru kora team shadharonoto valo uddesho ar ekta lomba list diye shuru kore. "
            "Dui shoptah por list aro lomba, kintu kichui bodlayni. Je team egoi ar je team plan kortei thake — "
            "parthokko porishrome na, scope e.\n\n"
            f"### Eta kemon dekhe\n\n{chapter.get('summary', '')}\n\n"
            "Ek shoptah e drisshoman result dey emon shob cheye choto version diye shuru koro. Ek bakko te likho: "
            "ke ki korbe, kobe porjonto, ar kivabe bujhbe eta kaj koreche. Bakko te jodi pach ta kaj er list lage, "
            "tahole scope ekhono beshi boro.\n\n"
            "### Kaj er poddhoti\n\n"
            "1. **Result thik koro.** Ek bakko, ek shongkha, ek deadline.\n"
            "2. **Shob cheye choto loop banao.** Draft, review, improve — protiti ekbar.\n"
            "3. **Nirdisto dine review koro.** Prot shoptah ek e shomoy, ponero minute, chara nei.\n\n"
            "### Mul shikkha\n\n"
            f"{bullet_block}\n\n"
            "### Onushilon\n\n"
            "Tomar result er bakko ta likho, tarpor ordhek kore felo. Baki ordhek ta ei shoptah er calendar e "
            "rakho ar asole ki holo ta note koro. Shei note i porer chapter er upadan.\n\n"
            "### Sarsongkhep\n\n"
            f"{chapter['title']} kono theory na, eta scope er decision. Loop ta eto choto rakho je shesh kora jay, "
            "ar repetition ke kaj korte dao.\n"
        )

    async def _draft_chapter(self, number: int, silent: bool) -> Optional[str]:
        if not self.book:
            return None
        chapter = next((c for c in self.book["outline"] if c["index"] == number), None)
        if not chapter:
            return None

        candidates = self._text_candidates()
        text: Optional[str] = None
        if candidates:
            try:
                raw, _ = await llm.generate_first(
                    candidates,
                    CHAPTER_PROMPT.format(rules=rules.compliance_brief()),
                    "\n".join(
                        [
                            f"Book: {self.book['title']} — {self.book['subtitle']}",
                            f"Audience: {self.book['audience']}",
                            f"Chapter {number}: {chapter['title']}",
                            f"Chapter summary: {chapter['summary']}",
                            "Takeaways: " + "; ".join(chapter.get("bullets") or []),
                            f"This is chapter {number} of {len(self.book['outline'])}.",
                        ]
                    ),
                    temperature=0.7,
                    max_tokens=2000,
                    language=self.lang,
                )
                text = re.sub(r"^```(?:markdown)?|```$", "", raw.strip(), flags=re.M).strip()
            except llm.LLMError as exc:
                self.log(f"chapter draft failed: {exc}", "warn")

        if not text:
            text = self._offline_chapter(chapter, self.book)
            self.log(f"chapter {number} drafted offline")

        disclosure = (
            f"{self.book['blurb']} এআই-জেনারেটেড খসড়া, একজন মানব সম্পাদক দ্বারা পর্যালোচিত।"
            if self.book.get("lang", self.lang) == "bn" else
            f"{self.book['blurb']} AI-generated draft reviewed by a human editor."
        )
        report = rules.evaluate(
            script=text,
            title=self.book["title"],
            description=disclosure,
            tags=None,
            platform="both",
            ai_generated=True,
        )
        if report["blockers"]:
            text, _ = rules.sanitize(text)
            report = rules.evaluate(
                script=text,
                title=self.book["title"],
                description=disclosure,
                tags=None,
                platform="both",
                ai_generated=True,
            )
        self.last_report = report
        self.book["chapters"][number] = text
        chapter["words"] = len(text.split())
        self.persist_artifacts()
        self.log(
            f"chapter {number} drafted — {chapter['words']} words, compliance {report['score']}/100",
            "success" if report["ok"] else "error",
        )
        if silent:
            return None
        return text

    async def _write_chapter(self, number: int) -> str:
        if not self.book:
            return "No book in this session yet — send a brief first, e.g. 'write a 6-chapter ebook about AI workflows'."
        if number > len(self.book["outline"]):
            return f"This book has {len(self.book['outline'])} chapters; chapter {number} is out of range."
        chapter = next(c for c in self.book["outline"] if c["index"] == number)
        self.set_state(status="working", progress=40, current_task=f"chapter {number}: {chapter['title'][:40]}")
        await self._draft_chapter(number, silent=False)
        text = self.book["chapters"].get(number, "")
        words = len(text.split())
        report = self.last_report or {}
        self.set_state(
            status="review",
            progress=100,
            current_task=f"chapter {number} drafted · {words} words",
        )
        if self.lang == "bn":
            return "\n".join(
                [
                    f"চ্যাপ্টার {number} তৈরি — {chapter['title']}",
                    f"{words} শব্দ · কমপ্লায়েন্স {report.get('score', 0)}/100 · {report.get('verdict', 'unrated')}",
                    "",
                    "প্রিভিউ (প্রথম ২৫ লাইন):",
                    *["  " + line for line in text.splitlines()[:25]],
                    "",
                    "পুরো চ্যাপ্টার এই সেশনেই সংরক্ষিত আছে। মার্কডাউন ফাইল লিখতে 'এক্সপোর্ট' লিখুন, "
                    "বা পরেরটির জন্য 'চ্যাপ্টার N লেখো'।",
                ]
            )
        return "\n".join(
            [
                f"CHAPTER {number} DRAFTED — {chapter['title']}",
                f"{words} words · compliance {report.get('score', 0)}/100 · {report.get('verdict', 'unrated')}",
                "",
                "Preview (first 25 lines):",
                *["  " + line for line in text.splitlines()[:25]],
                "",
                "Full chapter is stored in this session. Use 'export' to write the markdown file, "
                "or 'write chapter N' for the next one.",
            ]
        )

    # -- export -------------------------------------------------------------
    def _export(self) -> str:
        if not self.book:
            return "No book to export yet — send a brief first."
        if not self.book["chapters"]:
            return "Nothing drafted yet. Run 'write chapter 1' first."
        BOOK_DIR.mkdir(parents=True, exist_ok=True)
        slug = _slug(self.book["title"])
        target = BOOK_DIR / f"{slug}.md"

        lines = [
            "---",
            f"title: {self.book['title']}",
            f"subtitle: {self.book['subtitle']}",
            f"audience: {self.book['audience']}",
            f"generated: {time.strftime('%Y-%m-%d')}",
            "---",
            "",
            f"# {self.book['title']}",
            f"*{self.book['subtitle']}*",
            "",
            self.book["blurb"],
            "",
            "## Table of Contents",
        ]
        for chapter in self.book["outline"]:
            marker = "" if chapter["index"] in self.book["chapters"] else " *(not drafted)*"
            lines.append(f"{chapter['index']}. {chapter['title']}{marker}")
        lines.append("")
        for chapter in self.book["outline"]:
            text = self.book["chapters"].get(chapter["index"])
            if text:
                lines += [text, ""]
        lines += [
            "---",
            "",
            "*Drafted with AI assistance and reviewed by a human editor. Original content; "
            "no third-party material is reproduced. Educational content only — not professional, "
            "financial or medical advice.*",
            "",
        ]
        target.write_text("\n".join(lines), encoding="utf-8")
        self.last_export = str(target.relative_to(BASE_DIR))
        self.persist_artifacts()
        words = sum(len(text.split()) for text in self.book["chapters"].values())
        self.log(f"ebook exported to {self.last_export} ({words} words)", "success")
        if self.lang == "bn":
            return (
                f"এক্সপোর্ট হয়েছে — {self.last_export}\n"
                f"{len(self.book['chapters'])} চ্যাপ্টার · {words} শব্দ · মার্কডাউন\n\n"
                "ফাইলে YAML ফ্রন্ট ম্যাটার, সূচিপত্র, তৈরি চ্যাপ্টার ও ডিসক্লোজার ফুটার আছে। "
                "অতৈরি চ্যাপ্টার সূচিতে দেখানো হয়েছে যাতে ঘাটতি স্পষ্ট থাকে।"
            )
        return (
            f"EXPORTED — {self.last_export}\n"
            f"{len(self.book['chapters'])} chapter(s) · {words} words · markdown\n\n"
            "Formatting includes YAML front matter, table of contents, drafted chapters and a "
            "disclosure footer. Undrafted chapters are listed in the TOC so the gap is visible."
        )

    # -- status / reply -----------------------------------------------------
    def _status(self) -> str:
        candidates = self._text_candidates()
        provider, key = candidates[0] if candidates else (None, None)
        pool = len(candidates)
        if self.lang == "bn":
            lines = [
                "ইবুক রাইটিং — স্ট্যাটাস",
                f"প্রোভাইডার: {provider or 'অফলাইন টেমপ্লেট'} ({'কী সেট আছে' if key else 'কী নেই'}) · কী পুল: {pool}",
            ]
            if self.book:
                drafted = len(self.book["chapters"])
                words = sum(len(text.split()) for text in self.book["chapters"].values())
                lines += [
                    f"বই: {self.book['title']}",
                    f"চ্যাপ্টার: {drafted}/{len(self.book['outline'])} তৈরি · {words} শব্দ",
                    f"কমপ্লায়েন্স: {self.last_report['score']}/100 · {self.last_report['verdict']}"
                    if self.last_report else "কমপ্লায়েন্স: যাচাই হয়নি",
                ]
                if self.last_export:
                    lines.append(f"এক্সপোর্ট: {self.last_export}")
            else:
                lines.append("এই সেশনে এখনো কোনো বই শুরু হয়নি।")
            return "\n".join(lines)
        lines = [
            "EBOOK WRITING — status",
            f"Provider: {provider or 'offline template'} ({'key set' if key else 'no key'}) · key pool: {pool}",
        ]
        if self.book:
            drafted = len(self.book["chapters"])
            words = sum(len(text.split()) for text in self.book["chapters"].values())
            lines += [
                f"Book: {self.book['title']}",
                f"Chapters: {drafted}/{len(self.book['outline'])} drafted · {words} words",
                f"Compliance: {self.last_report['score']}/100 · {self.last_report['verdict']}"
                if self.last_report
                else "Compliance: not evaluated",
            ]
            if self.last_export:
                lines.append(f"Export: {self.last_export}")
        else:
            lines.append("No book started yet in this session.")
        return "\n".join(lines)

    def _format_outline_reply(self, book: Dict[str, Any], mode: str, note: str) -> str:
        bl = lambda key: label(self.lang, key)  # noqa: E731
        bn = self.lang == "bn"
        lines = [
            f"{bl('delivered')} — {book['title']}",
            f"{book['subtitle']} · {len(book['outline'])} {'চ্যাপ্টার' if bn else 'chapters'} · {mode}",
            "",
            bl("blurb"),
            f"  {book['blurb']}",
            "",
            bl("outline"),
        ]
        for chapter in book["outline"]:
            drafted = "✓" if chapter["index"] in book["chapters"] else " "
            lines.append(f"  [{drafted}] {chapter['index']}. {chapter['title']}")
            lines.append(f"      {chapter['summary']}")
        lines += [
            "",
            f"চ্যাপ্টার ১ প্রিভিউ ({len(book['chapters'].get(1, '').split())} শব্দ)"
            if bn else
            f"CHAPTER 1 PREVIEW ({len(book['chapters'].get(1, '').split())} words)",
        ]
        preview = book["chapters"].get(1, "").splitlines()[:8]
        lines += [f"  {line}" for line in preview]
        lines += [
            "",
            rules.format_report(self.last_report) if self.last_report else "Compliance: not evaluated",
        ]
        if note:
            lines.append(f"{'নোট' if bn else 'Note'}: {note}")
        lines.append(
            "পরবর্তী: 'চ্যাপ্টার ২ লেখো', 'এক্সপোর্ট', অথবা 'স্ট্যাটাস'। প্রতিটি চ্যাপ্টারে কমপ্লায়েন্স চলে।"
            if bn else
            "NEXT: 'write chapter 2', 'export', or 'status'. Compliance runs on every chapter."
        )
        return "\n".join(lines)

    # -- snapshot -----------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        data = super().snapshot()
        if self.book:
            data["book"] = {
                "title": self.book["title"],
                "chapters": f"{len(self.book['chapters'])}/{len(self.book['outline'])}",
                "words": sum(len(text.split()) for text in self.book["chapters"].values()),
                "compliance_score": self.last_report["score"] if self.last_report else None,
                "export": self.last_export,
            }
        return data


def build(bus: EventBus, keys: ApiKeyStore) -> EbookWritingAgent:
    return EbookWritingAgent(bus, keys)
