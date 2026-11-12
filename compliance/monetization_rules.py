"""YouTube + Facebook monetization / policy gate.

Every content agent (script, video, ebook, SEO, publishing) runs its output
through :func:`evaluate` before it is considered "monetization-ready".

The module is deterministic and dependency-free — it uses policy rule sets
expressed as regex patterns, structural metadata checks, and required
disclosures. It is a guardrail, not a legal opinion.

Public API
----------
evaluate(script, title, description, tags, platform, ai_generated, paid_promotion)
    -> report dict (see ``evaluate``)
sanitize(text)             -> (clean_text, [changes])  deterministic auto-fix
compliance_brief()         -> str  brief for LLM system prompts
rewrite_instructions(r)    -> [str] actionable fixes from a report
format_report(report)      -> str  chat-ready rendering
checklist()                -> dict platform publishing requirements
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

PLATFORMS = ("youtube", "facebook")

TITLE_MAX = 100
DESCRIPTION_MIN = 120
TAGS_MIN = 3
TAGS_MAX = 30

POLICY_SOURCES = {
    "youtube_monetization": "https://support.google.com/youtube/answer/1311392",
    "youtube_inauthentic": "https://support.google.com/youtube/answer/1311392",
    "youtube_ad_friendly": "https://support.google.com/youtube/answer/6162278",
    "youtube_synthetic_media": "https://support.google.com/youtube/answer/14328491",
    "youtube_community": "https://support.google.com/youtube/answer/9283445",
    "facebook_content_monetization": "https://www.facebook.com/business/help/1233758062500145",
    "facebook_community": "https://transparency.fb.com/policies/community-standards/",
}


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    category: str
    severity: str  # "block" (not monetizable) | "warn" (limited ads / needs review)
    platforms: Tuple[str, ...]
    patterns: Tuple[str, ...]
    guidance: str
    fix: str


# ---------------------------------------------------------------------------
# Rule catalogue
# ---------------------------------------------------------------------------
RULES: Tuple[Rule, ...] = (
    # -- copyright / originality -------------------------------------------
    Rule(
        id="reused-content",
        title="Reused / unoriginal content",
        category="Copyright & originality",
        severity="block",
        platforms=("youtube", "facebook"),
        patterns=(
            r"\bre-?upload(ed|ing)?\b",
            r"\bcompilation of (other people'?s )?(clips|videos)\b",
            r"\breused content\b",
            r"\bno commentary\b",
            r"\bsilent gameplay\b",
            r"\bslideshow of (stock|other people'?s) (images|photos)\b",
        ),
        guidance=(
            "YouTube requires original, authentic value. Republishing or lightly edited "
            "third-party footage without commentary or transformation is demonetized; "
            "Facebook applies the same originality test to Content Monetization."
        ),
        fix="Add original commentary, analysis or transformation — or produce the footage yourself.",
    ),
    Rule(
        id="copyright-admission",
        title="Third-party copyright admission",
        category="Copyright & originality",
        severity="block",
        platforms=("youtube", "facebook"),
        patterns=(
            r"\bno copyright (infringement )?intended\b",
            r"\bi do not own (the rights|copyright)\b",
            r"\bfree download\b",
            r"\bdownload (the )?full (movie|album|song)\b",
            r"\bpirated\b",
            r"\bleaked (footage|movie|album)\b",
            r"\bstream (the )?full (movie|album)\b",
        ),
        guidance=(
            "A disclaimer does not license third-party material. Using music, footage or "
            "art you do not own can trigger copyright claims that strip monetization and "
            "strike the channel."
        ),
        fix="Remove the third-party asset. Replace audio with licensed or royalty-free music and cite the license.",
    ),
    Rule(
        id="music-license",
        title="Music licence not evidenced",
        category="Copyright & originality",
        severity="warn",
        platforms=("youtube", "facebook"),
        patterns=(
            r"\b(popular|chart|famous|hit) (song|track|music)\b",
            r"\bmovie soundtrack\b",
            r"\b(beyonc[eé]|taylor swift|drake|eminem|ed sheeran)\b",
        ),
        guidance=(
            "Commercial tracks need an explicit licence for monetized video. Claims are "
            "automatic and revenue is redirected to the rights holder."
        ),
        fix="Swap to a licensed library track (e.g. your ElevenLabs/royalty-free library) and record the licence in the description.",
    ),
    # -- misleading claims / scams -----------------------------------------
    Rule(
        id="guaranteed-claims",
        title="Guaranteed results claim",
        category="Misleading claims",
        severity="block",
        platforms=("youtube", "facebook"),
        patterns=(
            r"\bguaranteed (income|returns?|results?|profits?|money|success)\b",
            r"\b100% (guaranteed|profit|passive income)\b",
            r"\bget rich quick\b",
            r"\brisk[- ]free (investment|returns?|profit)\b",
            r"\bdouble your (money|investment)\b",
            r"\binstant (riches|wealth|money)\b",
            r"\bpassive income guaranteed\b",
        ),
        guidance=(
            "Unrealistic income promises are treated as misleading claims and are not "
            "advertiser-friendly on either platform."
        ),
        fix="Replace promises with specific, evidence-based outcomes and realistic timelines.",
    ),
    Rule(
        id="financial-advice",
        title="Financial advice without disclaimer",
        category="Misleading claims",
        severity="warn",
        platforms=("youtube", "facebook"),
        patterns=(
            r"\b(invest|investing) (your|all your) (savings|money|401k)\b",
            r"\bbuy (this|these) stocks? now\b",
            r"(?<!not )\bfinancial advice\b",
            r"\bcrypto (signal|trading) group\b",
        ),
        guidance=(
            "Financial content stays monetizable with a clear disclaimer and no personalised "
            "recommendations."
        ),
        fix='Add: "Educational content only — not financial advice. Consult a licensed professional."',
    ),
    Rule(
        id="medical-claims",
        title="Medical cure / treatment claim",
        category="Health claims",
        severity="block",
        platforms=("youtube", "facebook"),
        patterns=(
            r"\bcures? (cancer|diabetes|disease|depression|anxiety)\b",
            r"\bmiracle cure\b",
            r"\btreats? (cancer|disease|diabetes)\b",
            r"\bdetox (cure|cleanse)\b",
            r"\blose \d+ (pounds|kg|lbs) in \d+ (days|weeks)\b",
            r"\bdoctors? (hate|don'?t want you to know)\b",
        ),
        guidance=(
            "Health claims that promise cures are a restricted category on both platforms "
            "and can remove ads entirely."
        ),
        fix="Rephrase as personal experience or general information, add a medical disclaimer, and drop cure claims.",
    ),
    Rule(
        id="health-disclaimer",
        title="Health topic needs disclaimer",
        category="Health claims",
        severity="warn",
        platforms=("youtube", "facebook"),
        patterns=(
            r"\b(supplements?|dosage|diagnos(is|e)|symptoms?|therapy|medication)\b",
            r"\bweight loss\b",
        ),
        guidance="Health-adjacent content is monetizable with a visible disclaimer and no personalised diagnosis.",
        fix='Add: "General information only — not medical advice. See a qualified professional."',
    ),
    # -- prohibited content -------------------------------------------------
    Rule(
        id="hate-harassment",
        title="Hate or harassment content",
        category="Community guidelines",
        severity="block",
        platforms=("youtube", "facebook"),
        patterns=(
            r"\bhate speech\b",
            r"\b(kill|exterminate) all\b",
            r"\bethnic cleansing\b",
            r"\binferior race\b",
            r"\b(racial|ethnic) slur\b",
        ),
        guidance="Hate speech and harassment violate community guidelines and end monetization eligibility.",
        fix="Remove the content entirely. There is no compliant rewrite for this category.",
    ),
    Rule(
        id="adult-content",
        title="Adult / sexual content",
        category="Community guidelines",
        severity="block",
        platforms=("youtube", "facebook"),
        patterns=(
            r"\bpornograph(y|ic)\b",
            r"\bexplicit nudity\b",
            r"\bnsfw\b",
            r"\bsexual (content|services)\b",
        ),
        guidance="Adult content is prohibited or age-restricted; age-restricted videos cannot carry full ads.",
        fix="Remove the content. Keep any educational discussion non-graphic and clearly contextualised.",
    ),
    Rule(
        id="violence-graphic",
        title="Graphic violence",
        category="Community guidelines",
        severity="block",
        platforms=("youtube", "facebook"),
        patterns=(
            r"\bgraphic (violence|gore|footage)\b",
            r"\bbeheading\b",
            r"\btorture footage\b",
            r"\banimal cruelty\b",
            r"\bdead bodies\b",
        ),
        guidance="Graphic violence is not advertiser-friendly and may be removed entirely.",
        fix="Remove graphic footage; describe impact instead of showing it.",
    ),
    Rule(
        id="weapons-drugs",
        title="Weapons / illegal drugs",
        category="Restricted goods",
        severity="block",
        platforms=("youtube", "facebook"),
        patterns=(
            r"\bbuy (guns?|firearms?|ammo)\b",
            r"\bhow to (make|build) (a )?(bomb|explosive|silencer)\b",
            r"\bsell(ing)? (illegal )?drugs\b",
            r"\bbuy (cocaine|heroin|meth)\b",
        ),
        guidance="Sale or manufacture instructions for weapons and illegal drugs are prohibited.",
        fix="Remove the content. Educational/medical context must not include acquisition or manufacture instructions.",
    ),
    Rule(
        id="scams",
        title="Scam / prize bait",
        category="Misleading claims",
        severity="block",
        platforms=("youtube", "facebook"),
        patterns=(
            r"\byou('ve| have) won\b",
            r"\bclaim your (prize|reward)\b",
            r"\bfree (crypto|bitcoin) giveaway\b",
            r"\bsend (crypto|btc|eth|money) to\b",
            r"\bclick (this|the) link to (win|claim)\b",
        ),
        guidance="Prize and crypto scams violate both platforms' integrity policies.",
        fix="Remove the offer. Never ask viewers to send funds or click unverified links.",
    ),
    # -- engagement / metadata ---------------------------------------------
    Rule(
        id="engagement-bait",
        title="Engagement bait",
        category="Metadata & authenticity",
        severity="warn",
        platforms=("youtube", "facebook"),
        patterns=(
            r"\bsmash that like\b",
            r"\blike (this video )?if you agree\b",
            r"\btag a friend\b",
            r"\bshare to win\b",
            r"\bcomment .{0,15}to win\b",
            r"\bsub4sub\b",
            r"\bfollow for follow\b",
            r"\bsubscribe or (else|i will)\b",
        ),
        guidance="Engagement bait is demoted by Facebook and discouraged by YouTube; it reads as inauthentic.",
        fix="Ask for a normal action once: 'If this was useful, subscribe for the next part.'",
    ),
    Rule(
        id="sensitive-event",
        title="Sensitive event / distressing topic",
        category="Advertiser friendliness",
        severity="warn",
        platforms=("youtube", "facebook"),
        patterns=(
            r"\b(mass shooting|terrorist attack|war crimes?)\b",
            r"\b(suicide|self[- ]harm)\b",
            r"\b(pandemic|outbreak)\b",
            r"\b(vaccine) misinformation\b",
        ),
        guidance=(
            "Sensitive events can limit ads. If the topic is necessary, keep it factual, "
            "non-graphic and add support resources where relevant."
        ),
        fix="Add context, avoid graphic detail, and include a helpline if self-harm is discussed.",
    ),
    Rule(
        id="clickbait-title",
        title="Clickbait / misleading metadata",
        category="Metadata & authenticity",
        severity="warn",
        platforms=("youtube", "facebook"),
        patterns=(
            r"\byou won'?t believe\b",
            r"\b(shocking|insane) (truth|secret)\b",
            r"\bthey don'?t want you to know\b",
            r"\b(this|it) changes everything\b",
            r"\b\d+ (secrets|tricks) they\b",
        ),
        guidance="Metadata must accurately describe the content; sensational titles are a misleading-metadata violation.",
        fix="Rewrite the title so it states the actual promise of the video.",
    ),
)

PROFANITY = (
    "fuck", "shit", "bitch", "asshole", "bastard", "dick", "cunt", "motherfucker",
)

AI_DISCLOSURE_PATTERNS = (
    r"\bai[- ]generated\b",
    r"\bsynthetic (media|voice|avatar)\b",
    r"\bcreated with ai\b",
    r"\bai (avatar|voice|narration)\b",
    r"\bdigitally (generated|created)\b",
    # Bengali / Banglish disclosures
    r"এআই[- ]?(?:জেনারেটেড|নির্মিত|দ্বারা|দিয়ে)",
    r"কৃত্রিম বুদ্ধিমত্তা",
    r"\bai[- ](?:diye|generated|banano)\b",
)

PAID_PROMO_PATTERNS = (
    r"\bpaid promotion\b",
    r"\bincludes paid promotion\b",
    r"\bsponsored by\b",
    r"\b#ad\b",
    r"\bpaid partnership\b",
    r"\baffiliate (link|disclosure)\b",
)


# ---------------------------------------------------------------------------
# Deterministic auto-fix
# ---------------------------------------------------------------------------
_REPLACEMENTS: Sequence[Tuple[str, str]] = (
    (r"\bguaranteed (income|returns?|results?|profits?|money|success)\b", r"realistic \1"),
    (r"\b100% (guaranteed|profit|passive income)\b", r"documented results"),
    (r"\bget rich quick\b", "build skills deliberately"),
    (r"\brisk[- ]free (investment|returns?|profit)\b", r"carefully researched \1"),
    (r"\bdouble your (money|investment)\b", r"grow your \1 over time"),
    (r"\binstant (riches|wealth|money)\b", "gradual progress"),
    (r"\bmiracle cure\b", "evidence-based support"),
    (r"\bcures? cancer\b", "may support wellbeing (not medical advice)"),
    (r"\bcures? (diabetes|disease)\b", r"may support \1 management (not medical advice)"),
    (r"\btreats? (cancer|diabetes|disease)\b", r"may support people managing \1 (not medical advice)"),
    (r"\bdetox (cure|cleanse)\b", "balanced routine"),
    (r"\bsmash that like\b", "if this helped, subscribe"),
    (r"\btag a friend\b", "share it with your team"),
    (r"\bshare to win\b", "share it with someone who needs it"),
    (r"\bsub4sub\b", "subscribe for the next part"),
    (r"\bfollow for follow\b", "follow for the next part"),
    (r"\byou won'?t believe\b", "here is"),
    (r"\bthey don'?t want you to know\b", "worth understanding"),
    (r"\bno copyright (infringement )?intended\b", ""),
    (r"\bi do not own (the rights|copyright)\b", ""),
    (r"\bclaim your (prize|reward)\b", "review the offer terms"),
)


def sanitize(text: str) -> Tuple[str, List[str]]:
    """Apply deterministic rewrites for the most common violation phrases."""
    if not text:
        return text, []
    cleaned = text
    changes: List[str] = []
    for pattern, replacement in _REPLACEMENTS:
        new_value, count = re.subn(pattern, replacement, cleaned, flags=re.IGNORECASE)
        if count:
            changes.append(f"replaced '{pattern}' ×{count}")
            cleaned = new_value
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip(), changes


# ---------------------------------------------------------------------------
# Core evaluation
# ---------------------------------------------------------------------------
def _norm_platform(platform: str) -> str:
    p = (platform or "both").lower()
    return p if p in PLATFORMS else "both"


def _rule_applies(rule: Rule, platform: str) -> bool:
    return platform == "both" or platform in rule.platforms or "both" in rule.platforms


def _finding(rule: Rule, matches: Iterable[str]) -> Dict[str, Any]:
    return {
        "id": rule.id,
        "title": rule.title,
        "category": rule.category,
        "severity": rule.severity,
        "platforms": list(rule.platforms),
        "guidance": rule.guidance,
        "fix": rule.fix,
        "matches": sorted({m.strip() for m in matches if m})[:6],
    }


def _scan(text: str, platform: str) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    if not text:
        return findings
    for rule in RULES:
        if not _rule_applies(rule, platform):
            continue
        hits: List[str] = []
        for pattern in rule.patterns:
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                hits.append(match.group(0))
        if hits:
            findings.append(_finding(rule, hits))
    return findings


def _profanity_findings(text: str, platform: str) -> List[Dict[str, Any]]:
    if not text:
        return []
    lowered = text.lower()
    hits = [word for word in PROFANITY if re.search(rf"\b{re.escape(word)}\w*\b", lowered)]
    if not hits:
        return []
    total = sum(len(re.findall(rf"\b{re.escape(w)}\w*\b", lowered)) for w in hits)
    if total < 2 and len(hits) < 2:
        return []
    return [
        {
            "id": "profanity",
            "title": "Strong language",
            "category": "Advertiser friendliness",
            "severity": "warn",
            "platforms": ["youtube", "facebook"],
            "guidance": "Repeated strong language limits or removes ads on both platforms.",
            "fix": "Reduce profanity or bleep it; keep any necessary language in context.",
            "matches": hits,
        }
    ]


def _metadata_findings(
    title: Optional[str],
    description: Optional[str],
    tags: Optional[Sequence[str]],
    platform: str,
    ai_generated: bool,
    paid_promotion: bool,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    blocks: List[Dict[str, Any]] = []
    warns: List[Dict[str, Any]] = []
    title = (title or "").strip()
    description = (description or "").strip()
    tag_list = [t for t in (tags or []) if str(t).strip()]

    def make(rid: str, title_text: str, category: str, severity: str, guidance: str, fix: str) -> Dict[str, Any]:
        return {
            "id": rid,
            "title": title_text,
            "category": category,
            "severity": severity,
            "platforms": list(PLATFORMS),
            "guidance": guidance,
            "fix": fix,
            "matches": [],
        }

    if not title:
        blocks.append(make("title-missing", "No title supplied", "Metadata & authenticity", "block",
                           "A monetized upload needs an accurate title.", "Generate 3 honest title options."))
    elif len(title) > TITLE_MAX:
        warns.append(make("title-length", "Title too long", "Metadata & authenticity", "warn",
                          f"Titles are truncated after {TITLE_MAX} characters.", "Shorten the title to ≤100 characters."))
    elif title.isupper() and len(title) > 12:
        warns.append(make("title-caps", "All-caps title", "Metadata & authenticity", "warn",
                          "All-caps titles read as sensational and can be flagged as misleading metadata.",
                          "Use sentence case and reserve caps for acronyms."))
    if title.count("!") >= 3 or re.search(r"[!?]{2,}", title):
        warns.append(make("title-punctuation", "Excessive punctuation in title", "Metadata & authenticity", "warn",
                          "Repeated exclamation marks are treated as clickbait signals.",
                          "Use one punctuation mark and state the promise plainly."))

    if not description:
        warns.append(make("description-missing", "No description", "Metadata & authenticity", "warn",
                          "A description is required for search, context and disclosures.",
                          "Write a 2–4 sentence description with chapters and disclosures."))
    elif len(description) < DESCRIPTION_MIN:
        warns.append(make("description-short", "Description too thin", "Metadata & authenticity", "warn",
                          f"Descriptions under {DESCRIPTION_MIN} characters lack room for context and disclosures.",
                          "Expand the description with what the viewer will learn."))

    if tag_list and len(tag_list) < TAGS_MIN:
        warns.append(make("tags-few", "Few tags", "Metadata & authenticity", "warn",
                          "Too few tags limits discovery but is not a policy breach.", "Add 5–15 relevant tags."))
    if len(tag_list) > TAGS_MAX:
        warns.append(make("tags-many", "Too many tags", "Metadata & authenticity", "warn",
                          "Excessive tags look like keyword stuffing.", "Keep the most relevant 15 tags."))

    if ai_generated:
        haystack = f"{title}\n{description}"
        if not any(re.search(p, haystack, flags=re.IGNORECASE) for p in AI_DISCLOSURE_PATTERNS):
            warns.append(make("ai-disclosure", "AI disclosure missing", "Disclosure", "warn",
                              "YouTube requires disclosure of realistic synthetic media; Facebook requires "
                              "AI labelling for generated content.",
                              'Add "AI-generated visuals/voice" to the description and enable the platform disclosure.'))

    if paid_promotion:
        haystack = f"{title}\n{description}"
        if not any(re.search(p, haystack, flags=re.IGNORECASE) for p in PAID_PROMO_PATTERNS):
            blocks.append(make("paid-disclosure", "Paid promotion not disclosed", "Disclosure", "block",
                               "Undisclosed paid promotion violates both platforms' advertising policies.",
                               'Add "Includes paid promotion" and enable the paid-promotion checkbox.'))

    return blocks, warns


def evaluate(
    script: Optional[str] = None,
    title: Optional[str] = None,
    description: Optional[str] = None,
    tags: Optional[Sequence[str]] = None,
    platform: str = "both",
    ai_generated: bool = True,
    paid_promotion: bool = False,
) -> Dict[str, Any]:
    """Run a piece of content through every policy rule and score it."""
    platform_key = _norm_platform(platform)
    body = "\n".join(part for part in (title, description, script) if part)

    text_findings = _scan(body, platform_key)
    text_findings += _profanity_findings(body, platform_key)
    meta_blocks, meta_warns = _metadata_findings(
        title, description, tags, platform_key, ai_generated, paid_promotion
    )

    blockers = [f for f in text_findings if f["severity"] == "block"] + meta_blocks
    warnings = [f for f in text_findings if f["severity"] == "warn"] + meta_warns

    seen = set()
    deduped_blocks, deduped_warns = [], []
    for item in blockers:
        if item["id"] not in seen:
            seen.add(item["id"])
            deduped_blocks.append(item)
    for item in warnings:
        if item["id"] not in seen:
            seen.add(item["id"])
            deduped_warns.append(item)

    blocks = deduped_blocks
    warns = deduped_warns

    score = max(0, 100 - 30 * len(blocks) - 8 * len(warns))
    ok = not blocks and score >= 70

    passed_rule_ids = [r.id for r in RULES if r.id not in {f["id"] for f in text_findings}]

    return {
        "ok": ok,
        "score": score,
        "platform": platform_key,
        "verdict": "monetization-ready" if ok and not warns else "ready with fixes" if ok else "blocked",
        "blockers": blocks,
        "warnings": warns,
        "passed": passed_rule_ids,
        "checked_rules": len(RULES),
        "required_actions": [f["fix"] for f in blocks] + [f["fix"] for f in warns],
        "disclosures": {
            "ai_generated": ai_generated,
            "paid_promotion": paid_promotion,
        },
    }


def rewrite_instructions(report: Dict[str, Any]) -> List[str]:
    """Actionable, ordered instructions derived from a report."""
    actions: List[str] = []
    for finding in report.get("blockers", []):
        actions.append(f"BLOCKER — {finding['title']}: {finding['fix']}")
    for finding in report.get("warnings", []):
        actions.append(f"FIX — {finding['title']}: {finding['fix']}")
    if not actions:
        actions.append("No policy actions required — publish as generated.")
    return actions


def format_report(report: Dict[str, Any]) -> str:
    """Compact chat rendering of a report."""
    lines = [
        f"COMPLIANCE GATE — {report['score']}/100 · {report['verdict']} "
        f"({report['platform']} rules)"
    ]
    if report["blockers"]:
        lines.append("Blockers (must fix before publishing):")
        for finding in report["blockers"]:
            lines.append(f"  ✖ {finding['title']} [{finding['category']}]")
            if finding["matches"]:
                lines.append(f"    matched: {', '.join(finding['matches'][:4])}")
            lines.append(f"    fix: {finding['fix']}")
    else:
        lines.append("Blockers: none")
    if report["warnings"]:
        lines.append("Warnings (may limit ads):")
        for finding in report["warnings"]:
            lines.append(f"  ! {finding['title']} → {finding['fix']}")
    else:
        lines.append("Warnings: none")
    return "\n".join(lines)


def compliance_brief() -> str:
    """Brief injected into every content-generation system prompt."""
    lines = [
        "MONETIZATION POLICY GATE (YouTube + Facebook) — output must pass all of this:",
        "1. Original content only. No re-uploads, compilations of others' clips, or silent third-party footage.",
        "2. No third-party music/footage without a licence. Never write a 'no copyright intended' disclaimer.",
        "3. No guaranteed income/results, get-rich-quick, risk-free, or prize/scam offers.",
        "4. No medical cure claims; health topics need a non-medical-advice disclaimer.",
        "5. No hate, harassment, adult content, graphic violence, weapons/drug manufacture, or illegal goods.",
        "6. No engagement bait ('smash like', 'tag a friend', 'share to win', 'sub4sub').",
        "7. Titles must be accurate sentence case, ≤100 chars; no clickbait or excessive punctuation.",
        "8. Description ≥120 chars, with chapters where useful, AI-generated disclosure, and paid-promotion disclosure when relevant.",
        "9. Disclose realistic synthetic media and paid partnerships in both content and platform settings.",
        "10. Provide 5–15 relevant tags; no keyword stuffing.",
    ]
    return "\n".join(lines)


def checklist() -> Dict[str, Any]:
    """Publishing requirements shared with the UI / other agents."""
    return {
        "platforms": list(PLATFORMS),
        "title_max_chars": TITLE_MAX,
        "description_min_chars": DESCRIPTION_MIN,
        "tags": {"min": TAGS_MIN, "max": TAGS_MAX},
        "required_disclosures": [
            "AI-generated / synthetic media (realistic content)",
            "Paid promotion or sponsorship",
            "Health topics: not medical advice",
            "Financial topics: not financial advice",
        ],
        "hard_blocks": [r.id for r in RULES if r.severity == "block"],
        "soft_warnings": [r.id for r in RULES if r.severity == "warn"],
        "sources": POLICY_SOURCES,
    }
