"""Bengali / Banglish language helpers (shared by every agent).

Everything here is dependency-free. The goals are:

* detect English, Bengali (বাংলা) and Banglish (Romanized Bengali),
* normalise Bengali/Banglish command words to the same canonical English verbs
  so routing code stays small,
* extract topics, durations, chapter counts, platforms and keywords without
  stripping Bengali Unicode (U+0980–U+09FF),
* provide reply-section labels for Bengali and Banglish.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

BENGALI_RANGE = "\u0980-\u09FF"
BENGALI_RE = re.compile(f"[{BENGALI_RANGE}]")
WORD_RE = re.compile(rf"[A-Za-z0-9]+|[{BENGALI_RANGE}]+")
BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")

# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------
BANGLISH_MARKERS = {
    "bangla", "banglay", "banglish", "banglai", "bengali",
    "likho", "lekho", "likhun", "banao", "banai", "banate", "koro", "kore",
    "korbo", "korte", "dao", "daw", "din", "chai", "amar", "amake", "amader",
    "kemon", "kivabe", "kibhabe", "ekta", "ekti", "ktha", "kotha", "niye",
    "somporche", "bishoye", "onk", "khub", "valo", "bhalo", "koro",
}

EXPLICIT_BENGALI = (
    "in bengali", "in bangla", "in bengali language", "bangla e", "banglay",
    "বাংলায়", "বাংলায়", "বাংলা ভাষায়", "বাংলা ভাষায়", "বাংলা",
)
EXPLICIT_BANGLISH = (
    "banglish", "romanized bengali", "romanised bengali", "bangla transliteration",
)


def has_bengali(text: str) -> bool:
    return bool(BENGALI_RE.search(text or ""))


def normalize_digits(text: str) -> str:
    return (text or "").translate(BN_DIGITS)


def detect(text: str) -> str:
    """Return ``"bn"``, ``"banglish"`` or ``"en"`` for a user message."""
    raw = text or ""
    low = raw.lower()
    if any(marker in low for marker in EXPLICIT_BANGLISH):
        return "banglish"
    if has_bengali(raw) or any(marker in low for marker in EXPLICIT_BENGALI):
        return "bn"
    tokens = set(re.findall(r"[a-z]+", low))
    if tokens & BANGLISH_MARKERS:
        return "banglish"
    return "en"


def is_bengali(lang: str) -> bool:
    return lang in ("bn", "banglish")


# ---------------------------------------------------------------------------
# Command normalisation
# ---------------------------------------------------------------------------
BN_COMMANDS: Dict[str, str] = {
    "অনুমোদন": "approve", "অনুমোদিত": "approve", "অনুমতি": "approve",
    "অ্যাপ্রুভ": "approve", "মানজুর": "approve", "অনুমোদন করুন": "approve",
    "স্ট্যাটাস": "status", "অবস্থা": "status", "রিপোর্ট": "report",
    "রিভাইজ": "revise", "সংশোধন": "revise", "পরিবর্তন": "revise",
    "সাহায্য": "help", "হেল্প": "help",
    "এক্সপোর্ট": "export", "রপ্তানি": "export",
    "ভয়েসওভার": "voiceover", "ভয়েস": "voiceover",
    "চ্যাপ্টার": "chapter", "অধ্যায়": "chapter",
    "লিখো": "write", "লেখো": "write", "লিখুন": "write",
    "তৈরি": "create", "বানাও": "create", "বানান": "create",
    "করো": "do", "করুন": "do", "দাও": "give", "দিন": "give",
    "শুরু": "start", "চালাও": "run", "রেন্ডার": "render", "জমা": "submit",
    "স্ক্রিপ্ট": "script", "স্টোরিবোর্ড": "storyboard", "অডিও": "audio",
    "ন্যারেশন": "narration", "ভিডিও": "video", "কীওয়ার্ড": "keywords",
}

BANGLISH_COMMANDS: Dict[str, str] = {
    "onumodon": "approve", "onumodit": "approve", "approve": "approve",
    "status": "status", "report": "report",
    "revise": "revise", "thik": "revise", "change": "revise",
    "help": "help", "export": "export",
    "voiceover": "voiceover", "chapter": "chapter",
    "likho": "write", "lekho": "write", "banao": "create", "koro": "do",
    "dao": "give", "chalao": "run",
}


def normalize_commands(text: str) -> str:
    """Rewrite Bengali/Banglish command words to canonical English tokens."""
    words = WORD_RE.findall(text or "")
    if not words:
        return text or ""
    mapped: List[str] = []
    for word in words:
        lower = word.lower()
        if word in BN_COMMANDS:
            mapped.append(BN_COMMANDS[word])
        elif lower in BANGLISH_COMMANDS:
            mapped.append(BANGLISH_COMMANDS[lower])
        else:
            # Bengali inflections: স্ক্রিপ্টের → স্ক্রিপ্ট, ভিডিওটা → ভিডিও
            stem = word
            for suffix in ("ের", "ে", "টা", "টি", "টার", "গুলোর", "গুলো"):
                if word.endswith(suffix) and len(word) > len(suffix) + 1:
                    stem = word[: -len(suffix)]
                    break
            mapped.append(BN_COMMANDS.get(stem, word))
    return " ".join(mapped)


# ---------------------------------------------------------------------------
# Topic / parameter extraction
# ---------------------------------------------------------------------------
FILLERS = {
    # english
    "write", "create", "draft", "generate", "make", "produce", "develop", "build",
    "run", "start", "please", "me", "us", "a", "an", "the", "some", "new",
    "short", "long", "viral", "full", "complete", "quick", "entire",
    "about", "on", "for", "covering", "around", "of", "in", "into", "with",
    "youtube", "facebook", "reels", "shorts", "tiktok", "video", "script",
    "screenplay", "story", "outline", "piece", "series", "ebook", "e-book",
    "book", "guide", "playbook", "handbook", "chapter", "chapters",
    "campaign", "leads", "lead", "prospects", "prospect", "clients", "client",
    "audience", "and", "to", "using", "take", "put", "make", "give",
    # bengali
    "লিখো", "লেখো", "লিখুন", "লেখেন", "তৈরি", "করো", "করুন", "বানাও", "বানান",
    "দাও", "দিন", "একটা", "একটি", "আমার", "আমাদের", "সম্পর্কে", "নিয়ে", "জন্য",
    "বাংলায়", "বাংলায়", "বাংলা", "ভাষায়", "ভিডিও", "স্ক্রিপ্ট", "ইবুক", "ই-বুক",
    "বই", "গাইড", "চ্যাপ্টার", "অধ্যায়", "এসইও", "লিড", "ক্যাম্পেইন", "সেকেন্ড",
    "সেকেন্ডের", "মিনিট", "মিনিটের", "প্লিজ", "এক", "এবং", "সাথে", "থেকে",
    # banglish
    "likho", "lekho", "banao", "banai", "koro", "dao", "ekta", "ekti", "amar",
    "amader", "somporche", "niye", "jonno", "bangla", "banglay", "video",
    "script", "ebook", "boi", "chapter", "seo", "lead", "campaign", "second",
    "sec", "minute", "min", "diye", "ek", "ar",
}

DURATION_RE = re.compile(
    r"(\d+)\s*[- ]?\s*(seconds?|secs?|sekend|minutes?|mins?|minit|s|m|"
    r"সেকেন্ড|সেকেন্ডে|সেকেন্ডের|মিনিট|মিনিটে|মিনিটের)\b",
    re.IGNORECASE,
)

CHAPTER_RE = re.compile(
    r"(\d+)\s*[- ]?\s*(chapters?|chapter|ch|অধ্যায়|অধ্যায়ের|চ্যাপ্টার|চ্যাপ্টারের)\b",
    re.IGNORECASE,
)

STOPWORDS_BN = {
    "এবং", "এই", "একটি", "একটা", "জন্য", "সাথে", "থেকে", "করে", "হয়", "হবে",
    "কিভাবে", "কী", "কি", "আমার", "আমাদের", "আপনার", "ভিডিও", "স্ক্রিপ্ট",
    "ইবুক", "বই", "চ্যাপ্টার", "এসইও", "লিড", "সম্পর্কে", "নিয়ে", "মধ্যে",
    "উপর", "দিন", "খুব", "এবং", "অথবা", "যে", "এবং", "নেই", "আর",
    "আসলে", "যা", "কোনো", "একই", "হিসেবে", "দিয়ে", "ভাবে", "নয়",
}

STOPWORDS_EN = {
    "about", "after", "again", "against", "also", "because", "before", "being",
    "between", "could", "every", "from", "have", "into", "just", "like", "make",
    "many", "more", "most", "much", "need", "only", "other", "over", "should",
    "some", "such", "than", "that", "their", "them", "then", "there", "these",
    "they", "this", "those", "through", "under", "very", "want", "were", "what",
    "when", "where", "which", "while", "will", "with", "would", "your",
}


def normalize_text(text: str) -> str:
    return normalize_digits(text or "")


def clean_topic(text: str) -> str:
    """Drop leading command/filler tokens without touching Bengali letters."""
    tokens = (text or "").strip().split()
    index = 0
    while index < len(tokens):
        token = tokens[index].strip(" ,.:;-।!?")
        if not token:
            index += 1
            continue
        low = token.lower()
        numeric = re.fullmatch(r"\d+(?:[- ]?(?:second|sec|s|minute|min|m))?", low)
        if low in FILLERS or numeric:
            index += 1
            continue
        break
    topic = " ".join(tokens[index:]).strip(" ,.:;-।")
    # strip a trailing "in bangla / বাংলায়" request phrase
    topic = re.sub(r"[, ]+(?:in|with)?\s*(?:bengali|bangla|banglish|বাংলায়|বাংলায়|বাংলা)\s*$", "", topic, flags=re.I)

    # strip trailing command words and length modifiers (… স্ক্রিপ্ট লেখো → …)
    trailing = set(FILLERS) | {"করো", "করুন", "লিখো", "লেখো", "লিখুন", "বানাও", "বানান",
                                 "দাও", "দিন", "চাই", "please", "চ্যাপ্টারের", "অধ্যায়ের",
                                 "লেখার", "বানানোর", "তৈরির"}
    tokens = topic.split()
    while tokens:
        last = tokens[-1].strip(" ,.:;-।!?").lower()
        numeric = re.fullmatch(r"\d+(?:[- ]?(?:second|sec|s|minute|min|m))?", last)
        if last in trailing or numeric or last in {
            "সেকেন্ড", "সেকেন্ডে", "সেকেন্ডের", "মিনিট", "মিনিটে", "মিনিটের",
        }:
            tokens.pop()
            continue
        break
    topic = " ".join(tokens)
    return topic.strip(" ,.:;-।")


def duration_seconds(text: str, default: Optional[int] = None) -> Optional[int]:
    match = DURATION_RE.search(normalize_digits(text or ""))
    if not match:
        return default
    value = int(match.group(1))
    unit = match.group(2).lower()
    return value * 60 if unit.startswith(("m", "মিনিট")) else value


def chapter_count(text: str) -> Optional[int]:
    match = CHAPTER_RE.search(normalize_digits(text or ""))
    if not match:
        return None
    return int(match.group(1))


def platform_hint(text: str) -> Optional[str]:
    low = (text or "").lower()
    youtube = any(word in low for word in ("youtube", "ইউটিউব", "ইউটিউবে", "shorts", "শর্টস"))
    facebook = any(word in low for word in ("facebook", "ফেসবুক", "ফেসবুকে", "reels", "রিলস"))
    if youtube and facebook:
        return "both"
    if facebook:
        return "facebook"
    if youtube:
        return "youtube"
    return None


def keywords(text: str, limit: int = 8) -> List[str]:
    """Unicode-aware keyword extraction (keeps বাংলা words intact)."""
    words = WORD_RE.findall(text or "")
    seen: List[str] = []
    for word in words:
        low = word.lower()
        if low in STOPWORDS_EN or word in STOPWORDS_BN:
            continue
        if re.fullmatch(r"\d+", low):
            continue
        if BENGALI_RE.search(word):
            if len(word) < 2:
                continue
        elif len(word) < 4:
            continue
        if low not in {w.lower() for w in seen}:
            seen.append(word)
    return seen[:limit]


# ---------------------------------------------------------------------------
# Reply labels
# ---------------------------------------------------------------------------
LABELS: Dict[str, Dict[str, str]] = {
    "en": {
        "delivered": "DELIVERED",
        "titles": "TITLE OPTIONS", "hook": "HOOK", "scenes": "SCENES",
        "cta": "CTA", "description": "DESCRIPTION", "tags": "TAGS",
        "thumbnail": "THUMBNAIL TEXT", "compliance": "COMPLIANCE GATE",
        "status": "STATUS", "provider": "Provider", "next": "NEXT",
        "visual": "VISUAL", "vo": "VO", "overlay": "OVERLAY",
        "shots": "SHOT LIST", "render": "RENDER", "timeline": "TIMELINE",
        "export": "EXPORT", "voiceover": "VOICEOVER", "outline": "OUTLINE",
        "chapters": "CHAPTERS", "blurb": "BLURB", "icp": "IDEAL CUSTOMER PROFILE",
        "sequence": "OUTREACH SEQUENCE", "checks": "SEO CHECKS",
        "prompt": "PROMPT", "captions": "CAPTIONS", "audio": "AUDIO",
        "music": "MUSIC & LOUDNESS", "help": "COMMANDS",
    },
    "bn": {
        "delivered": "প্রস্তুত হয়েছে",
        "titles": "শিরোনামের অপশন", "hook": "হুক", "scenes": "দৃশ্য",
        "cta": "কল টু অ্যাকশন", "description": "বিবরণ", "tags": "ট্যাগ",
        "thumbnail": "থাম্বনেইল টেক্সট", "compliance": "কমপ্লায়েন্স গেট",
        "status": "স্ট্যাটাস", "provider": "প্রোভাইডার", "next": "পরবর্তী ধাপ",
        "visual": "দৃশ্যপট", "vo": "সংলাপ", "overlay": "ওভারলে",
        "shots": "শট তালিকা", "render": "রেন্ডার", "timeline": "টাইমলাইন",
        "export": "এক্সপোর্ট", "voiceover": "ভয়েসওভার", "outline": "আউটলাইন",
        "chapters": "চ্যাপ্টার", "blurb": "ব্লার্ব", "icp": "আদর্শ গ্রাহক প্রোফাইল",
        "sequence": "আউটরিচ সিকোয়েন্স", "checks": "এসইও চেক",
        "prompt": "প্রম্পট", "captions": "ক্যাপশন", "audio": "অডিও",
        "music": "সংগীত ও লাউডনেস", "help": "কমান্ড",
    },
    "banglish": {
        "delivered": "Ready",
        "titles": "Shironam er option", "hook": "Hook", "scenes": "Drisso",
        "cta": "Call to action", "description": "Biboron", "tags": "Tag",
        "thumbnail": "Thumbnail text", "compliance": "Compliance gate",
        "status": "Status", "provider": "Provider", "next": "Poroborti dhap",
        "visual": "Visual", "vo": "Dialogue", "overlay": "Overlay",
        "shots": "Shot list", "render": "Render", "timeline": "Timeline",
        "export": "Export", "voiceover": "Voiceover", "outline": "Outline",
        "chapters": "Chapter", "blurb": "Blurb", "icp": "Ideal customer profile",
        "sequence": "Outreach sequence", "checks": "SEO check",
        "prompt": "Prompt", "captions": "Caption", "audio": "Audio",
        "music": "Music o loudness", "help": "Commands",
    },
}


def label(lang: str, key: str) -> str:
    table = LABELS.get(lang) or LABELS["en"]
    return table.get(key) or LABELS["en"].get(key, key)
