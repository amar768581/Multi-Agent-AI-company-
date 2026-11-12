"""LEAD GENERATION — prospecting & marketing.

Produces a complete outbound campaign for an offer:

* ideal customer profile (buyer, company shape, pains, buying triggers, disqualifiers),
* targeting segments with real search strings (no scraped or invented contacts),
* a multi-touch outreach sequence (email + LinkedIn) with opt-out on every message,
* qualification rubric, realistic metric targets and a consent/compliance checklist.

Uses the provider assigned in API Settings (Groq / Gemini / OpenAI) through
``core.llm``; falls back to a deterministic campaign builder with no key. The
copy is gated by ``compliance.monetization_rules`` for misleading claims, and
the operator checklist covers anti-spam obligations.
"""

from __future__ import annotations

import json
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
    "id": "lead_generation",
    "name": "LEAD GENERATION",
    "role": "Prospecting & Marketing",
    "accent": "#e785ff",
}

PLAN_PROMPT = """You are the Lead Generation department of CogniSphere Labs.
Build an ethical, high-signal outbound campaign for the offer provided.

Rules:
- Never invent statistics, customer names or email addresses.
- No guaranteed results, income promises, or false urgency.
- Every message must identify the sender, state why it is relevant, and include a clear opt-out.
- Assume a lawful basis for contact (business context or prior consent); no purchased lists.

{rules}

Return ONLY minified JSON:
{{"icp": {{"buyer": "job title", "company": "company shape", "pain_points": ["3"],
          "triggers": ["2 buying triggers"], "disqualifiers": ["2"]}},
  "segments": [{{"name": "...", "where": "channel/community", "search_query": "boolean-style query", "why": "..."}}],
  "sequence": [{{"day": 0, "channel": "email", "subject": "<=60 chars", "body": "<=120 words with {first_name} and opt-out"}}],
  "qualification": {{"criteria": ["4"], "red_flags": ["2"]}},
  "metrics": {{"reply_rate": "range", "meetings_per_100": "range", "cadence": "review rhythm"}}}}
"""

ANTI_SPAM_RULES = [
    "Contact only with a lawful basis (business context, opt-in, or legitimate interest).",
    "No purchased, scraped or rented lists; no role-address blasting.",
    "Identify yourself and your company in every first message.",
    "Every message carries an opt-out; honour it within 24 hours and never re-add the address.",
    "Subjects must describe the message accurately — no fake replies or 'Re:' tricks.",
    "Cap volume (≈25 new contacts per inbox per day) and warm up new domains.",
    "Keep suppression lists for unsubscribes, bounces and complaints.",
]


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (slug or "campaign")[:50]


class LeadGenerationAgent(AgentBase):
    id = AGENT_SPEC["id"]
    name = AGENT_SPEC["name"]
    role = AGENT_SPEC["role"]
    accent = AGENT_SPEC["accent"]
    providers = ["groq", "gemini", "openai"]
    persisted_artifacts = {
        "campaign": "last_campaign",
        "report": "last_report",
    }

    def __init__(self, bus: EventBus, keys: ApiKeyStore) -> None:
        super().__init__(bus, keys)
        self.last_campaign: Optional[Dict[str, Any]] = None
        self.last_report: Optional[Dict[str, Any]] = None

    # -- entry point --------------------------------------------------------
    async def handle(self, message: str, context: Optional[Dict[str, Any]] = None) -> str:
        text = (message or "").strip()
        if not text:
            return "Describe the offer: 'build a lead campaign for an AI video service for founders'."
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
                "লিড জেনারেশন — কমান্ড\n"
                "• 'এআই ভিডিও সার্ভিসের জন্য একটি লিড ক্যাম্পেইন বানাও'\n"
                "• 'বুককিপিং রিটেইনারের জন্য লিড তৈরি করো'\n"
                "• 'স্ট্যাটাস' → প্রোভাইডার, সর্বশেষ ক্যাম্পেইন, কমপ্লায়েন্স স্কোর\n"
                "আউটপুট: ICP, টার্গেটিং সেগমেন্ট ও সার্চ স্ট্রিং, আউটরিচ সিকোয়েন্স, "
                "যোগ্যতা যাচাইয়ের মাপকাঠি, মেট্রিক ও সম্মতি চেকলিস্ট। কোনো যোগাযোগের তথ্য বানানো হয় না।"
            )
        return (
            "LEAD GENERATION — commands\n"
            "• 'build a lead campaign for an AI video service for founders'\n"
            "• 'generate leads for a bookkeeping retainer'\n"
            "• 'status' → provider, last campaign, compliance score\n"
            "Output: ICP, targeting segments + search strings, outreach sequence, "
            "qualification rubric, metrics and a consent checklist. No contact data is invented."
        )

    def _status(self) -> str:
        candidates = self.keys.candidates(self.id, list(llm.SUPPORTED_PROVIDERS))
        provider, key = candidates[0] if candidates else (None, None)
        pool = len(candidates)
        if self.lang == "bn":
            lines = [
                "লিড জেনারেশন — স্ট্যাটাস",
                f"প্রোভাইডার: {provider or 'অফলাইন টেমপ্লেট'} ({'কী সেট আছে' if key else 'কী নেই'}) · কী পুল: {pool}",
            ]
            if self.last_campaign:
                lines += [
                    f"ক্যাম্পেইন: {self.last_campaign['offer']}",
                    f"সেগমেন্ট: {len(self.last_campaign['segments'])} · টাচ: {len(self.last_campaign['sequence'])}",
                    f"কমপ্লায়েন্স: {self.last_report['score']}/100 · {self.last_report['verdict']}"
                    if self.last_report else "কমপ্লায়েন্স: যাচাই হয়নি",
                ]
            else:
                lines.append("এই সেশনে এখনো কোনো ক্যাম্পেইন তৈরি হয়নি।")
            return "\n".join(lines)
        lines = [
            "LEAD GENERATION — status",
            f"Provider: {provider or 'offline template'} ({'key set' if key else 'no key'}) · key pool: {pool}",
        ]
        if self.last_campaign:
            lines += [
                f"Campaign: {self.last_campaign['offer']}",
                f"Segments: {len(self.last_campaign['segments'])} · touches: {len(self.last_campaign['sequence'])}",
                f"Compliance: {self.last_report['score']}/100 · {self.last_report['verdict']}"
                if self.last_report
                else "Compliance: not evaluated",
            ]
        else:
            lines.append("No campaign built yet in this session.")
        return "\n".join(lines)

    # -- brief --------------------------------------------------------------
    def _clean_offer(self, text: str) -> str:
        if self.lang in ("bn", "banglish"):
            return clean_topic(text)
        prefixes = [
            r"^(?:please\s+)?(?:generate|build|create|find|get|source|run|make)\b",
            r"^(?:me|us)\b",
            r"^(?:some|a|an|the)?\s*(?:new\s+)?(?:leads?|prospects?|clients?|customers?|pipeline|campaign)s?\b",
            r"^(?:for|about|on|around|targeting|selling)\b",
            r"^(?:a|an|the)\b",
            r"^[,.:;\-\s]+",
        ]
        offer = text.strip()
        for _ in range(10):
            before = offer
            for pattern in prefixes:
                offer = re.sub(pattern, "", offer, count=1, flags=re.I).strip(" ,.:;-")
            if offer == before:
                break
        offer = re.sub(r"\bfor\s+(beginners|founders|marketers|creators|developers|agencies|small teams)\b", "", offer, flags=re.I)
        return offer.strip(" ,.:;-")

    def _fallback_offer(self) -> Optional[str]:
        if not self.registry:
            return None
        script = self.registry.get("script_writing")
        plan = getattr(script, "last_script", None) if script else None
        if plan and plan.get("title_options"):
            return plan["title_options"][0]
        video = self.registry.get("video_generation")
        vplan = getattr(video, "last_plan", None) if video else None
        if vplan and vplan.get("title"):
            return vplan["title"]
        return None

    def _keywords(self, offer: str) -> List[str]:
        if self.lang in ("bn", "banglish"):
            return extract_keywords(offer, 6)
        words = [w for w in re.findall(r"[a-z0-9]+", offer.lower()) if len(w) > 3]
        seen: List[str] = []
        for word in words:
            if word not in seen:
                seen.append(word)
        return seen[:6]

    # -- offline campaign ---------------------------------------------------
    def _offline_campaign(self, offer: str) -> Dict[str, Any]:
        if self.lang in ("bn", "banglish"):
            return self._offline_campaign_bengali(offer)
        keywords = self._keywords(offer) or ["ai", "workflow"]
        focus = " ".join(keywords[:2])
        buyer = "Founder or operations lead at a 5–50 person team"
        return {
            "offer": offer[:80],
            "icp": {
                "buyer": buyer,
                "company": f"Small teams already spending time on {focus} but without a repeatable process",
                "pain_points": [
                    f"{focus[:1].upper() + focus[1:]} work is done ad hoc, so quality varies week to week",
                    "The team cannot tell which activity actually produces results",
                    "Every new client adds coordination overhead instead of leverage",
                ],
                "triggers": [
                    "A new client or contract forces a repeatable process",
                    "A senior person is drowning in manual review work",
                ],
                "disqualifiers": [
                    "Enterprise procurement cycles longer than 90 days",
                    "Teams that want a one-off deliverable, not a working system",
                ],
            },
            "segments": [
                {
                    "name": "Founders shipping weekly",
                    "where": "LinkedIn + founder communities (Indie Hackers, relevant Slacks)",
                    "search_query": f'("founder" OR "co-founder") AND ("{keywords[0]}" OR "{keywords[-1]}") -jobs -hiring',
                    "why": "They feel the pain weekly and can approve a small pilot themselves.",
                },
                {
                    "name": "Operators inheriting the process",
                    "where": "LinkedIn (operations / marketing leads)",
                    "search_query": f'("head of operations" OR "marketing lead") AND "{focus}"',
                    "why": "They own the outcome and the budget for fixing it.",
                },
                {
                    "name": "Agencies serving the same buyer",
                    "where": "Partner / referral conversations",
                    "search_query": f'("agency" OR "consultancy") AND "{focus}" AND ("partner" OR "white label")',
                    "why": "They already have the audience and need execution capacity.",
                },
            ],
            "sequence": [
                {
                    "day": 0,
                    "channel": "email",
                    "subject": f"A repeatable process for {focus}",
                    "body": (
                        "Hi {first_name},\n\n"
                        f"I work with small teams on {offer}. The pattern I usually see: "
                        f"{focus} gets done, but differently every time, so nobody can tell what is working.\n\n"
                        "We install a small weekly loop — one outcome, one process, one metric — and review it "
                        "with you for the first month.\n\n"
                        "Worth 15 minutes to see whether it fits your setup?\n\n"
                        "If this is not relevant, reply 'unsubscribe' and I will not follow up.\n\n"
                        "— {sender_name}, {sender_company}"
                    ),
                },
                {
                    "day": 2,
                    "channel": "linkedin",
                    "subject": "Short note",
                    "body": (
                        "Hi {first_name} — sent you a note about making "
                        f"{focus} repeatable for your team. Happy to share the one-page process we use, "
                        "no call needed. Say the word and I will send it over."
                    ),
                },
                {
                    "day": 5,
                    "channel": "email",
                    "subject": "The one-page version",
                    "body": (
                        "Hi {first_name},\n\n"
                        "One concrete idea in case the last note was badly timed: pick a single number "
                        f"that reflects {focus} quality, review it every Friday for 15 minutes, and change "
                        "one variable at a time.\n\n"
                        "If you want the template we use for that review, reply 'template'.\n\n"
                        "Opt out any time by replying 'unsubscribe'.\n\n"
                        "— {sender_name}"
                    ),
                },
                {
                    "day": 9,
                    "channel": "email",
                    "subject": "Closing the loop",
                    "body": (
                        "Hi {first_name},\n\n"
                        "I will close the loop here — it sounds like the timing is not right for "
                        f"{focus} work. If it becomes a priority, reply and I will pick it back up.\n\n"
                        "No further follow-ups from me. Reply 'unsubscribe' to be removed entirely.\n\n"
                        "— {sender_name}"
                    ),
                },
            ],
            "qualification": {
                "criteria": [
                    "Feels the pain at least weekly",
                    "Can approve a paid pilot without a committee",
                    "Has someone who will own the weekly review",
                    "Already spends money or hours on the problem",
                ],
                "red_flags": [
                    "Wants a guaranteed outcome or a fixed revenue promise",
                    "No owner for the process after handover",
                ],
            },
            "metrics": {
                "reply_rate": "3–8% on a warm, well-targeted list",
                "meetings_per_100": "1–3 qualified conversations",
                "cadence": "Review weekly; retire any segment under 2% reply after 100 sends",
            },
            "provider": "offline template",
        }

    def _offline_campaign_bengali(self, offer: str) -> Dict[str, Any]:
        bn = self.lang == "bn"
        keys = self._keywords(offer) or (["এআই", "কনটেন্ট"] if bn else ["ai", "content"])
        focus = " ".join(keys[:2])
        if not bn:
            return {
                "offer": offer[:80],
                "icp": {
                    "buyer": "Founder ba operations lead, 5–50 jon er team",
                    "company": f"Choto team ja {focus} niye shomoy khoy kore kintu repeatable process nei",
                    "pain_points": [
                        f"{focus} er kaj ad hoc, tai quality shoptah e shoptah e bodlay",
                        "Team bujhte pare na kon activity asole result dey",
                        "Protiti notun client coordination overhead baray, leverage na",
                    ],
                    "triggers": [
                        "Notun client ba contract repeatable process dabi kore",
                        "Senior kew manual review e dubhe ache",
                    ],
                    "disqualifiers": [
                        "90 din er beshi enterprise procurement cycle",
                        "Jara ekbar er deliverable chay, working system na",
                    ],
                },
                "segments": [
                    {"name": "Shoptahe shoptah e ship kora founder",
                     "where": "LinkedIn + founder communities",
                     "search_query": f'("founder" OR "co-founder") AND ("{keys[0]}") -jobs -hiring',
                     "why": "Tara shoptah e pain ta feel kore ar nijei choto pilot approve korte pare."},
                    {"name": "Process inherit kora operator",
                     "where": "LinkedIn (operations / marketing lead)",
                     "search_query": f'("head of operations" OR "marketing lead") AND "{focus}"',
                     "why": "Outcome ar budget dui tai tar hate."},
                    {"name": "Ek e buyer ke serve kora agency",
                     "where": "Partner / referral conversation",
                     "search_query": f'("agency" OR "consultancy") AND "{focus}" AND ("partner" OR "white label")',
                     "why": "Audience already ache, execute korar capacity dorkar."},
                ],
                "sequence": [
                    {"day": 0, "channel": "email", "subject": f"{focus} er jonno repeatable process",
                     "body": (
                         "Hi {first_name},\n\n"
                         f"Ami choto team der {offer} niye kaj kori. Common pattern: "
                         f"{focus} hoye jay, kintu protibar alada bhabe, tai keu bujhte pare na ki kaj korche.\n\n"
                         "Amra ekta choto weekly loop boshi — ek outcome, ek process, ek metric — ar prothom mash "
                         "tomader sathe review kori.\n\n"
                         "Tomar setup e fit kore kina dekhte 15 minute hobe?\n\n"
                         "Relevant na hole 'unsubscribe' likho, ami aro follow up korbo na.\n\n"
                         "— {sender_name}, {sender_company}"
                     )},
                    {"day": 2, "channel": "linkedin", "subject": "Choto note",
                     "body": (
                         f"Hi {{first_name}} — {focus} ke tomar team er jonno repeatable kora niye ekta note pathiyechi. "
                         "Amra je one-page process use kori seta share korte pari, call chara i. Bolle pathiye debo."
                     )},
                    {"day": 5, "channel": "email", "subject": "One-page version",
                     "body": (
                         "Hi {first_name},\n\n"
                         f"Ekta concrete idea, jodi ager note ta kharap shomoy e giye thake: ekta number bacho ja {focus} "
                         "quality reflect kore, prot shukrobar 15 minute review koro, ar ekbar e ekta variable bodlao.\n\n"
                         "Shei review er template chaile 'template' likho.\n\n"
                         "'Unsubscribe' likhle jekono shomoy opt out korte paro.\n\n"
                         "— {sender_name}"
                     )},
                    {"day": 9, "channel": "email", "subject": "Loop bondho korchi",
                     "body": (
                         "Hi {first_name},\n\n"
                         f"Ami ekhane loop bondho korchi — mone hoy {focus} er kaj er jonno timing thik nei. "
                         "Jodi priority hoy, reply koro, ami abar shuru korbo.\n\n"
                         "Aro follow up nei. Puropuri remove korte 'unsubscribe' likho.\n\n"
                         "— {sender_name}"
                     )},
                ],
                "qualification": {
                    "criteria": [
                        "Shoptah e kam pokkhe ekbar pain ta feel kore",
                        "Committee chara paid pilot approve korte pare",
                        "Weekly review er ekjon owner ache",
                        "Problem ta te tk ba ghonta already khoy korche",
                    ],
                    "red_flags": [
                        "Guaranteed outcome ba fixed revenue promise chay",
                        "Handover er por process er kono owner nei",
                    ],
                },
                "metrics": {
                    "reply_rate": "3–8% warm, well-targeted list e",
                    "meetings_per_100": "1–3 qualified conversation",
                    "cadence": "Weekly review; 100 send er por 2% er niche reply hole segment retire koro",
                },
                "provider": "offline template",
                "lang": self.lang,
            }

        return {
            "offer": offer[:80],
            "icp": {
                "buyer": "৫–৫০ জনের টিমের প্রতিষ্ঠাতা বা অপারেশনস লিড",
                "company": f"ছোট টিম, যারা {focus} নিয়ে সময় ব্যয় করে কিন্তু পুনরাবৃত্তিযোগ্য প্রক্রিয়া নেই",
                "pain_points": [
                    f"{focus} নিয়ে কাজ এলোমেলোভাবে হয়, তাই সপ্তাহে সপ্তাহে মান বদলায়",
                    "কোন কাজটি আসলে ফলাফল দিচ্ছে তা টিম বুঝতে পারে না",
                    "প্রতিটি নতুন ক্লায়েন্ট সমন্বয়ের ঝামেলা বাড়ায়, সুবিধা নয়",
                ],
                "triggers": [
                    "নতুন ক্লায়েন্ট বা চুক্তি পুনরাবৃত্তিযোগ্য প্রক্রিয়া দাবি করে",
                    "একজন সিনিয়র সদস্য ম্যানুয়াল রিভিউতে ডুবে আছেন",
                ],
                "disqualifiers": [
                    "৯০ দিনের বেশি এন্টারপ্রাইজ ক্রয় প্রক্রিয়া",
                    "যারা এককালীন ডেলিভারেবল চায়, কাজ করা সিস্টেম নয়",
                ],
            },
            "segments": [
                {
                    "name": "সাপ্তাহিক শিপ করা প্রতিষ্ঠাতা",
                    "where": "লিংকডইন + ফাউন্ডার কমিউনিটি (Indie Hackers, প্রাসঙ্গিক Slack)",
                    "search_query": f'("founder" OR "co-founder") AND ("{keys[0]}") -jobs -hiring',
                    "why": "তাঁরা প্রতি সপ্তাহে সমস্যাটি অনুভব করেন এবং নিজেই ছোট পাইলট অনুমোদন করতে পারেন।",
                },
                {
                    "name": "প্রক্রিয়া হাতে পাওয়া অপারেটর",
                    "where": "লিংকডইন (অপারেশনস / মার্কেটিং লিড)",
                    "search_query": f'("head of operations" OR "marketing lead") AND "{focus}"',
                    "why": "ফলাফল ও বাজেট দুটোই তাঁর হাতে।",
                },
                {
                    "name": "একই ক্রেতাকে সেবা দেওয়া এজেন্সি",
                    "where": "পার্টনার / রেফারেল আলোচনা",
                    "search_query": f'("agency" OR "consultancy") AND "{focus}" AND ("partner" OR "white label")',
                    "why": "তাঁদের কাছে দর্শক আছে, প্রয়োজন কাজ করার সক্ষমতা।",
                },
            ],
            "sequence": [
                {
                    "day": 0, "channel": "email", "subject": f"{focus}-এর জন্য একটি পুনরাবৃত্তিযোগ্য প্রক্রিয়া",
                    "body": (
                        "হ্যালো {first_name},\n\n"
                        f"আমি ছোট টিমদের {offer} নিয়ে কাজ করি। সাধারণ প্যাটার্ন: "
                        f"{focus} হয়ে যায়, কিন্তু প্রতিবার ভিন্নভাবে, তাই কেউ বুঝতে পারে না কী কাজ করছে।\n\n"
                        "আমরা একটি ছোট সাপ্তাহিক লুপ বসাই — এক ফলাফল, এক প্রক্রিয়া, এক মেট্রিক — এবং প্রথম মাস "
                        "আপনাদের সঙ্গে রিভিউ করি।\n\n"
                        "আপনার সেটআপে ফিট করে কি না দেখতে ১৫ মিনিট হবে?\n\n"
                        "প্রাসঙ্গিক না হলে 'unsubscribe' লিখুন, আমি আর ফলোআপ করব না।\n\n"
                        "— {sender_name}, {sender_company}"
                    ),
                },
                {
                    "day": 2, "channel": "linkedin", "subject": "ছোট নোট",
                    "body": (
                        f"হ্যালো {{first_name}} — আপনার টিমের জন্য {focus} পুনরাবৃত্তিযোগ্য করা নিয়ে একটি নোট পাঠিয়েছি। "
                        "আমরা যে এক পাতার প্রক্রিয়া ব্যবহার করি সেটি শেয়ার করতে পারি, কল ছাড়াই। বললে পাঠিয়ে দেব।"
                    ),
                },
                {
                    "day": 5, "channel": "email", "subject": "এক পাতার সংস্করণ",
                    "body": (
                        "হ্যালো {first_name},\n\n"
                        f"আগের নোটটি যদি খারাপ সময়ে যায়, একটি নির্দিষ্ট ধারণা: এমন একটি সংখ্যা বাছুন যা {focus}-এর "
                        "মান প্রতিফলিত করে, প্রতি শুক্রবার ১৫ মিনিট রিভিউ করুন, আর একবারে একটি ভেরিয়েবল বদলান।\n\n"
                        "সেই রিভিউয়ের টেমপ্লেট চাইলে 'template' লিখুন।\n\n"
                        "'Unsubscribe' লিখলে যেকোনো সময় অপ্ট আউট করতে পারেন।\n\n"
                        "— {sender_name}"
                    ),
                },
                {
                    "day": 9, "channel": "email", "subject": "লুপ বন্ধ করছি",
                    "body": (
                        "হ্যালো {first_name},\n\n"
                        f"আমি এখানে লুপ বন্ধ করছি — মনে হয় {focus}-এর কাজের জন্য সময়টা ঠিক নেই। "
                        "যদি অগ্রাধিকার হয়, উত্তর দিন, আমি আবার শুরু করব।\n\n"
                        "আর কোনো ফলোআপ নেই। সম্পূর্ণ বাদ দিতে 'unsubscribe' লিখুন।\n\n"
                        "— {sender_name}"
                    ),
                },
            ],
            "qualification": {
                "criteria": [
                    "সপ্তাহে কমপক্ষে একবার সমস্যাটি অনুভব করেন",
                    "কমিটি ছাড়াই পেইড পাইলট অনুমোদন করতে পারেন",
                    "সাপ্তাহিক রিভিউয়ের একজন দায়িত্বশীল আছেন",
                    "সমস্যাটিতে ইতিমধ্যে টাকা বা ঘণ্টা ব্যয় হচ্ছে",
                ],
                "red_flags": [
                    "নিশ্চিত ফলাফল বা নির্দিষ্ট আয়ের প্রতিশ্রুতি চান",
                    "হস্তান্তরের পর প্রক্রিয়ার কোনো দায়িত্বশীল নেই",
                ],
            },
            "metrics": {
                "reply_rate": "৩–৮% উষ্ণ, ভালোভাবে টার্গেট করা তালিকায়",
                "meetings_per_100": "১–৩ যোগ্য আলোচনা",
                "cadence": "সাপ্তাহিক রিভিউ; ১০০ পাঠানোর পর ২%-এর নিচে উত্তর এলে সেগমেন্ট বাদ দিন",
            },
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

    # -- pipeline -----------------------------------------------------------
    async def _produce(self, text: str) -> str:
        offer = self._clean_offer(text)
        if len(offer) < 4:
            offer = self._fallback_offer() or "an AI content service for small teams"

        self.set_state(status="working", progress=10, current_task=f"campaign: {offer[:48]}")
        self.log(f"lead campaign brief — {offer[:80]}")

        candidates = self.keys.candidates(self.id, list(llm.SUPPORTED_PROVIDERS))
        campaign: Optional[Dict[str, Any]] = None
        note = ""

        if candidates:
            self.set_state(progress=35, current_task=f"building ICP with {candidates[0][0]}")
            try:
                raw, used = await llm.generate_first(
                    candidates,
                    PLAN_PROMPT.format(rules=rules.compliance_brief()),
                    f"Offer: {offer}\nDirective: {text}\nBuild 3 segments and a 4-touch sequence.",
                    temperature=0.7,
                    max_tokens=2400,
                    language=self.lang,
                )
                if used != candidates[0][0]:
                    note = f"key fallback → {used} after a provider failure"
                parsed = self._parse_json(raw)
                if parsed and parsed.get("sequence"):
                    campaign = {
                        "offer": offer[:80],
                        "icp": parsed.get("icp") or {},
                        "segments": parsed.get("segments") or [],
                        "sequence": parsed.get("sequence") or [],
                        "qualification": parsed.get("qualification") or {},
                        "metrics": parsed.get("metrics") or {},
                        "provider": str(provider),
                    }
                else:
                    note = "provider output was unusable; offline campaign used"
                    self.log("campaign JSON invalid", "warn")
            except llm.LLMError as exc:
                note = f"provider error — {exc}"
                self.log(f"LLM unavailable: {exc}", "warn")

        if campaign is None:
            campaign = self._offline_campaign(offer)
            if note == "" and llm.supports(provider) and key:
                note = "offline campaign used"

        campaign = self._sanitize(campaign)
        report = self._gate(campaign)
        campaign["compliance"] = report
        self.last_campaign = campaign
        self.last_report = report
        self.persist_artifacts()

        touches = len(campaign["sequence"])
        self.set_state(
            status="review",
            progress=100,
            current_task=f"{len(campaign['segments'])} segments · {touches} touches · {report['score']}/100",
        )
        self.log(
            f"campaign ready — {len(campaign['segments'])} segments, {touches} touches, compliance {report['score']}/100",
            "success" if report["ok"] else "error",
        )
        return self._format_reply(campaign, report, note)

    def _gate(self, campaign: Dict[str, Any]) -> Dict[str, Any]:
        bodies = "\n\n".join(
            f"{touch.get('subject', '')}\n{touch.get('body', '')}" for touch in campaign["sequence"]
        )
        icp = campaign.get("icp") or {}
        buyer = str(icp.get("buyer") or "small teams")
        description = (
            f"Outbound campaign for {campaign['offer']} aimed at {buyer}. "
            "Every message identifies the sender, states relevance and links an opt-out; "
            "no purchased lists and no guaranteed-results claims. "
            "AI-generated outreach drafts, reviewed by a human before any message is sent."
        )
        return rules.evaluate(
            script=bodies,
            title=f"{campaign['offer'][:60]} outreach",
            description=description,
            tags=None,
            platform="both",
            ai_generated=True,
        )

    def _sanitize(self, campaign: Dict[str, Any]) -> Dict[str, Any]:
        for touch in campaign["sequence"]:
            touch["subject"] = str(touch.get("subject") or "A quick note")
            touch["body"], _ = rules.sanitize(str(touch.get("body") or ""))
        return campaign

    # -- reply --------------------------------------------------------------
    def _format_reply(self, campaign: Dict[str, Any], report: Dict[str, Any], note: str) -> str:
        icp = campaign.get("icp") or {}
        bl = lambda key: label(self.lang, key)  # noqa: E731
        bn = self.lang == "bn"
        lines = [
            f"{bl('delivered')} — {campaign['offer']}",
            f"{len(campaign['segments'])} {'সেগমেন্ট' if bn else 'segments'} · "
            f"{len(campaign['sequence'])} {'টাচ' if bn else 'touches'} · {campaign['provider']}",
            "",
            bl("icp"),
            f"  {'ক্রেতা' if bn else 'Buyer'}: {icp.get('buyer', '—')}",
            f"  {'প্রতিষ্ঠান' if bn else 'Company'}: {icp.get('company', '—')}",
        ]
        for pain in icp.get("pain_points") or []:
            lines.append(f"  • {'সমস্যা' if bn else 'pain'}: {pain}")
        for trigger in icp.get("triggers") or []:
            lines.append(f"  • {'ট্রিগার' if bn else 'trigger'}: {trigger}")
        for bad in icp.get("disqualifiers") or []:
            lines.append(f"  • {'বাদ' if bn else 'disqualify'}: {bad}")

        lines += ["", "টার্গেটিং (সার্চ স্ট্রিং, সংগ্রহ করা যোগাযোগ নয়)" if bn else
                  "TARGETING (search strings, not scraped contacts)"]
        for segment in campaign["segments"]:
            lines.append(f"  ▸ {segment.get('name', 'segment')} — {segment.get('where', '')}")
            lines.append(f"    query: {segment.get('search_query', '')}")
            lines.append(f"    why: {segment.get('why', '')}")

        lines += ["", bl("sequence")]
        for touch in campaign["sequence"]:
            lines.append(
                f"  {'দিন' if bn else 'Day'} {touch.get('day', 0)} · "
                f"{str(touch.get('channel', 'email')).upper()} · "
                f"{'বিষয়' if bn else 'subject'}: {touch.get('subject', '')}"
            )
            for body_line in str(touch.get("body", "")).splitlines():
                lines.append(f"    {body_line}")

        qualification = campaign.get("qualification") or {}
        lines += ["", "যোগ্যতা যাচাই" if bn else "QUALIFICATION"]
        for criterion in qualification.get("criteria") or []:
            lines.append(f"  ✓ {criterion}")
        for flag in qualification.get("red_flags") or []:
            lines.append(f"  ✗ {flag}")

        metrics = campaign.get("metrics") or {}
        lines += [
            "",
            "বাস্তবসম্মত লক্ষ্য" if bn else "REALISTIC TARGETS",
            f"  {'উত্তরের হার' if bn else 'reply rate'}: {metrics.get('reply_rate', '3–8%')}",
            f"  {'প্রতি ১০০-তে মিটিং' if bn else 'meetings per 100'}: {metrics.get('meetings_per_100', '1–3')}",
            f"  {'ক্যাডেন্স' if bn else 'cadence'}: {metrics.get('cadence', 'review weekly')}",
            "",
            "সম্মতি ও ডেলিভারেবিলিটি চেকলিস্ট" if bn else "CONSENT & DELIVERABILITY CHECKLIST",
        ]
        for rule_text in ANTI_SPAM_RULES:
            lines.append(f"  □ {rule_text}")

        lines += ["", rules.format_report(report)]
        if note:
            lines.append(f"{'নোট' if bn else 'Note'}: {note}")
        if report["ok"]:
            lines.append(
                "পরবর্তী: সিকোয়েন্স অনুমোদন করুন, অথবা একই অফারের কীওয়ার্ড সেটের জন্য এসইও এজেন্টকে বলুন।"
                if bn else
                "NEXT: approve the sequence, or ask SEO AGENT for the keyword set to target the same offer."
            )
        else:
            lines.append("পরবর্তী: কোনো বার্তা পাঠানোর আগে উপরের ব্লকারগুলো ঠিক করুন।" if bn else
                         "NEXT: fix the blockers above before any message is sent.")
        return "\n".join(lines)

    # -- snapshot -----------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        data = super().snapshot()
        if self.last_campaign:
            data["campaign"] = {
                "offer": self.last_campaign["offer"],
                "segments": len(self.last_campaign["segments"]),
                "touches": len(self.last_campaign["sequence"]),
                "compliance_score": self.last_report["score"] if self.last_report else None,
                "provider": self.last_campaign.get("provider"),
            }
        return data


def build(bus: EventBus, keys: ApiKeyStore) -> LeadGenerationAgent:
    return LeadGenerationAgent(bus, keys)
