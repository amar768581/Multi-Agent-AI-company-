"""VIDEO EDITING — sequencing, voiceover & post-production.

Consumes the shot list from VIDEO GENERATION (falling back to SCRIPT WRITING)
and produces:

* an edit decision list (EDL): clip order, in/out points, transitions, overlays,
  caption cues and music-bed instructions,
* platform export presets (resolution, bitrate, loudness, caption files),
* optional voiceover synthesis through ElevenLabs or OpenAI TTS when the
  assigned provider has a key — otherwise a timed voiceover script,
* the monetization compliance gate over the final narration.

No agent is imported directly; sources come through ``self.registry``.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from compliance import monetization_rules as rules
from config.api_keys import ApiKeyStore
from core.base_agent import AgentBase
from core.events import EventBus
from core.language import detect as detect_language, label, normalize_commands
from core.llm import USER_AGENT

AGENT_SPEC = {
    "id": "video_editing",
    "name": "VIDEO EDITING",
    "role": "Sequencing & Post",
    "accent": "#f7d774",
}

BASE_DIR = Path(__file__).resolve().parent.parent
AUDIO_DIR = BASE_DIR / "data" / "audio"

ELEVENLABS_TTS = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
OPENAI_TTS = "https://api.openai.com/v1/audio/speech"
DEFAULT_ELEVEN_VOICE = os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
DEFAULT_OPENAI_VOICE = os.environ.get("OPENAI_TTS_VOICE", "alloy")
MAX_TTS_SEGMENTS = 8

EXPORT_PRESETS = {
    "youtube": {
        "container": "MP4 (H.264 High, AAC)",
        "resolution": "1920x1080",
        "fps": 30,
        "video_bitrate": "12–16 Mbps",
        "audio": "48 kHz stereo, -14 LUFS integrated, -1 dBTP",
        "captions": "burned-in + .srt upload",
    },
    "facebook": {
        "container": "MP4 (H.264 High, AAC)",
        "resolution": "1080x1080 feed / 1080x1920 reels",
        "fps": 30,
        "video_bitrate": "8–12 Mbps",
        "audio": "48 kHz stereo, -14 LUFS integrated",
        "captions": "burned-in (sound-off viewing) + .srt",
    },
}


def _fmt(seconds: float) -> str:
    total = int(round(seconds))
    return f"{total // 60}:{total % 60:02d}"


def _parse_range(value: str) -> Optional[Tuple[int, int]]:
    match = re.match(r"\s*(\d{1,2}):(\d{2})\s*[-–—]\s*(\d{1,2}):(\d{2})", str(value or ""))
    if not match:
        return None
    start = int(match.group(1)) * 60 + int(match.group(2))
    end = int(match.group(3)) * 60 + int(match.group(4))
    return (start, max(start + 2, end))


def _post_bytes(url: str, payload: Dict[str, Any], headers: Dict[str, str], timeout: int = 120) -> bytes:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={**headers, "User-Agent": USER_AGENT}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:  # pragma: no cover
            pass
        raise RuntimeError(f"HTTP {exc.code}: {detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error: {exc.reason}") from exc
    if not data:
        raise RuntimeError("provider returned empty audio")
    return data


class VideoEditingAgent(AgentBase):
    id = AGENT_SPEC["id"]
    name = AGENT_SPEC["name"]
    role = AGENT_SPEC["role"]
    accent = AGENT_SPEC["accent"]
    providers = ["elevenlabs", "openai"]
    persisted_artifacts = {
        "edit": "last_edit",
        "report": "last_report",
        "audio": "last_audio",
    }

    def __init__(self, bus: EventBus, keys: ApiKeyStore) -> None:
        super().__init__(bus, keys)
        self.last_edit: Optional[Dict[str, Any]] = None
        self.last_report: Optional[Dict[str, Any]] = None
        self.last_audio: List[Dict[str, Any]] = []

    # -- entry point --------------------------------------------------------
    async def handle(self, message: str, context: Optional[Dict[str, Any]] = None) -> str:
        text = (message or "").strip()
        if not text:
            return "Say 'assemble the last storyboard' or paste a shot list to edit."
        self.record("user", text)
        self.lang = detect_language(text)
        low = normalize_commands(text).lower()

        if low.startswith(("status", "report", "progress")):
            reply = self._status()
        elif low in {"help", "commands"}:
            reply = self._help()
        else:
            with_audio = any(word in low for word in ("voiceover", "voice over", "tts", "narrate", "narration", "audio"))
            reply = await self._produce(text, synthesize=with_audio)

        self.record("agent", reply)
        self.bus.bump()
        return reply

    def _help(self) -> str:
        if self.lang == "bn":
            return (
                "ভিডিও এডিটিং — কমান্ড\n"
                "• 'সর্বশেষ স্টোরিবোর্ড অ্যাসেম্বল করো' → EDL, ক্যাপশন, সংগীত ও এক্সপোর্ট প্ল্যান\n"
                "• 'ভয়েসওভার যোগ করো' / 'ন্যারেশন বানাও' → প্রতিটি সেগমেন্টের ElevenLabs বা OpenAI TTS\n"
                "• 'এডিট: <শট তালিকা>' → পেস্ট করা লেখা থেকে\n"
                "• 'স্ট্যাটাস' → প্রোভাইডার, ভয়েস, সর্বশেষ রেন্ডার অ্যাসেট\n"
                "আসল অডিওর জন্য API সেটিংসে ElevenLabs বা OpenAI অ্যাসাইন করুন।"
            )
        return (
            "VIDEO EDITING — commands\n"
            "• 'assemble the last storyboard' → EDL, captions, music + export plan\n"
            "• 'add voiceover' / 'generate narration' → ElevenLabs or OpenAI TTS for each segment\n"
            "• 'edit: <shot list>' → build from pasted text\n"
            "• 'status' → provider, voice, last render assets\n"
            "Assign ElevenLabs or OpenAI in API Settings for real audio."
        )

    def _status(self) -> str:
        provider, key = self.keys.resolve_for_agent(self.id)
        if self.lang == "bn":
            lines = [
                "ভিডিও এডিটিং — স্ট্যাটাস",
                f"প্রোভাইডার: {provider or 'নেই'} ({'কী সেট আছে' if key else 'কী নেই'})",
                f"ভয়েস: {DEFAULT_ELEVEN_VOICE if provider == 'elevenlabs' else DEFAULT_OPENAI_VOICE}",
            ]
            if self.last_edit:
                lines.append(
                    f"সর্বশেষ এডিট: {self.last_edit['title']} · {len(self.last_edit['segments'])} সেগমেন্ট · "
                    f"{_fmt(self.last_edit['duration'])}"
                )
                lines.append(
                    f"কমপ্লায়েন্স: {self.last_report['score']}/100 · {self.last_report['verdict']}"
                    if self.last_report else "কমপ্লায়েন্স: যাচাই হয়নি"
                )
                lines.append(
                    f"অডিও: {len(self.last_audio)} ফাইল তৈরি" if self.last_audio
                    else "অডিও: ভয়েসওভার তৈরি হয়নি"
                )
            else:
                lines.append("এই সেশনে এখনো কোনো এডিট তৈরি হয়নি।")
            return "\n".join(lines)
        lines = [
            "VIDEO EDITING — status",
            f"Provider: {provider or 'none'} ({'key set' if key else 'no key'})",
            f"Voice: {DEFAULT_ELEVEN_VOICE if provider == 'elevenlabs' else DEFAULT_OPENAI_VOICE}",
        ]
        if self.last_edit:
            lines.append(
                f"Last edit: {self.last_edit['title']} · {len(self.last_edit['segments'])} segments · "
                f"{_fmt(self.last_edit['duration'])}"
            )
            lines.append(
                f"Compliance: {self.last_report['score']}/100 · {self.last_report['verdict']}"
                if self.last_report
                else "Compliance: not evaluated"
            )
            lines.append(
                f"Audio: {len(self.last_audio)} file(s) generated"
                if self.last_audio
                else "Audio: voiceover not generated"
            )
        else:
            lines.append("No edit assembled yet in this session.")
        return "\n".join(lines)

    # -- source resolution --------------------------------------------------
    def _latest_plan(self) -> Optional[Dict[str, Any]]:
        if not self.registry:
            return None
        agent = self.registry.get("video_generation")
        return getattr(agent, "last_plan", None) if agent else None

    def _latest_script(self) -> Optional[Dict[str, Any]]:
        if not self.registry:
            return None
        agent = self.registry.get("script_writing")
        return getattr(agent, "last_script", None) if agent else None

    def _from_plan(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "title": plan.get("title", "Untitled video"),
            "shots": plan.get("shots", []),
            "platform": "youtube",
            "description": plan.get("description", ""),
            "payload": plan.get("payload"),
            "origin": "VIDEO GENERATION storyboard",
        }

    def _from_script(self, script: Dict[str, Any]) -> Dict[str, Any]:
        title = (script.get("title_options") or ["Untitled video"])[0]
        shots = []
        cursor = 0
        for index, scene in enumerate(script.get("scenes") or [], start=1):
            voice = str(scene.get("voiceover") or "")
            window = _parse_range(str(scene.get("time") or ""))
            start, end = window if window else (cursor, cursor + max(4, round(len(voice.split()) / 2.6)))
            cursor = end
            shots.append(
                {
                    "index": index,
                    "start": start,
                    "end": end,
                    "duration": max(2, end - start),
                    "timecode": f"{_fmt(start)}-{_fmt(end)}",
                    "shot": "avatar",
                    "voiceover": voice,
                    "overlay": "",
                }
            )
        return {
            "title": title,
            "shots": shots,
            "platform": "youtube",
            "description": script.get("description", ""),
            "origin": "SCRIPT WRITING script",
        }

    def _from_text(self, text: str) -> Dict[str, Any]:
        cleaned = re.sub(
            r"^(?:please\s+)?(?:edit|assemble|cut|sequence)\s*(?:the|this|a)?\s*(?:video|storyboard|edit)?\s*:?\s*",
            "",
            text,
            flags=re.I,
        ).strip()
        blocks = [b.strip() for b in re.split(r"\n\s*\n", cleaned) if b.strip()] or [cleaned]
        shots = []
        cursor = 0
        for index, block in enumerate(blocks[:MAX_TTS_SEGMENTS], start=1):
            seconds = max(4, round(len(block.split()) / 2.6))
            shots.append(
                {
                    "index": index,
                    "start": cursor,
                    "end": cursor + seconds,
                    "duration": seconds,
                    "timecode": f"{_fmt(cursor)}-{_fmt(cursor + seconds)}",
                    "shot": "avatar",
                    "voiceover": block,
                    "overlay": "",
                }
            )
            cursor += seconds
        title = re.split(r"[.!?\n]", cleaned)[0].strip()[:80] or "Untitled edit"
        return {
            "title": title,
            "shots": shots,
            "platform": "youtube",
            "description": "AI-generated avatar and voice narration.",
            "origin": "pasted shot list",
        }

    # -- EDL ----------------------------------------------------------------
    def _captions(self, voice: str, start: float, end: float) -> List[Dict[str, Any]]:
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", voice) if s.strip()]
        if not sentences:
            return []
        words = [max(1, len(s.split())) for s in sentences]
        total = sum(words)
        span = max(1.0, end - start)
        cues = []
        cursor = float(start)
        for sentence, count in zip(sentences, words):
            share = span * (count / total)
            cues.append(
                {
                    "start": _fmt(cursor),
                    "end": _fmt(min(end, cursor + share)),
                    "text": sentence[:110],
                }
            )
            cursor += share
        return cues

    def _build_edl(self, source: Dict[str, Any]) -> Dict[str, Any]:
        segments: List[Dict[str, Any]] = []
        for index, shot in enumerate(source["shots"], start=1):
            start = float(shot.get("start", 0))
            end = float(shot.get("end", start + shot.get("duration", 5)))
            transition = "crossfade 12f" if index in (1, len(source["shots"])) else "cut"
            segments.append(
                {
                    "index": index,
                    "start_sec": start,
                    "end_sec": end,
                    "in": _fmt(start),
                    "out": _fmt(end),
                    "duration": max(2, round(end - start)),
                    "transition": transition,
                    "shot": shot.get("shot", "avatar"),
                    "voiceover": shot.get("voiceover", ""),
                    "overlay": shot.get("overlay") or "",
                    "captions": self._captions(shot.get("voiceover", ""), start, end),
                    "audio": None,
                }
            )
        total = segments[-1]["out"] if segments else "0:00"
        duration = sum(segment["duration"] for segment in segments)
        return {
            "title": source["title"],
            "origin": source["origin"],
            "platform": source.get("platform", "youtube"),
            "segments": segments,
            "duration": duration,
            "last_out": total,
            "music": {
                "bed": "licensed royalty-free ambient bed (no commercial tracks)",
                "level": "-18 dB under voiceover, ducked 4 dB on VO enters",
                "master": "-14 LUFS integrated, -1 dBTP ceiling",
            },
        }

    # -- voiceover ----------------------------------------------------------
    def _slug(self, title: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        return (slug or "video")[:48]

    async def _synthesize(self, segments: List[Dict[str, Any]], provider: str, key: str) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        slug = self._slug(self.last_edit["title"] if self.last_edit else "video")
        targets = [s for s in segments if s["voiceover"].strip()][:MAX_TTS_SEGMENTS]

        for segment in targets:
            text = segment["voiceover"][:1200]
            target = AUDIO_DIR / f"{slug}-{segment['index']:02d}.mp3"
            try:
                if provider == "elevenlabs":
                    url = ELEVENLABS_TTS.format(voice_id=DEFAULT_ELEVEN_VOICE) + "?output_format=mp3_44100_128"
                    headers = {
                        "xi-api-key": key,
                        "Content-Type": "application/json",
                        "Accept": "audio/mpeg",
                    }
                    payload = {
                        "text": text,
                        "model_id": os.environ.get("ELEVENLABS_MODEL", "eleven_multilingual_v2"),
                        "voice_settings": {"stability": 0.4, "similarity_boost": 0.8},
                    }
                else:
                    url = OPENAI_TTS
                    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
                    payload = {
                        "model": os.environ.get("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),
                        "voice": DEFAULT_OPENAI_VOICE,
                        "input": text,
                        "response_format": "mp3",
                    }
                audio = await asyncio.to_thread(_post_bytes, url, payload, headers)
                target.write_bytes(audio)
                segment["audio"] = str(target.relative_to(BASE_DIR))
                results.append(
                    {
                        "index": segment["index"],
                        "file": segment["audio"],
                        "bytes": len(audio),
                        "provider": provider,
                    }
                )
                self.set_state(
                    progress=min(90, 60 + segment["index"] * 4),
                    current_task=f"voiceover {segment['index']}/{len(targets)}",
                )
            except (RuntimeError, OSError) as exc:
                results.append({"index": segment["index"], "error": str(exc), "provider": provider})
                self.log(f"voiceover segment {segment['index']} failed: {exc}", "warn")
        return results

    # -- pipeline -----------------------------------------------------------
    async def _produce(self, text: str, synthesize: bool) -> str:
        low = normalize_commands(text).lower()
        plan = self._latest_plan()
        script = self._latest_script()

        if plan and (
            any(word in low for word in ("storyboard", "last video", "the video", "plan", "video"))
            or len(text.split()) <= 4
            or not script
        ):
            source = self._from_plan(plan)
        elif script and (
            any(word in low for word in ("script", "last script", "narration", "the script"))
            or len(text.split()) <= 4
        ):
            source = self._from_script(script)
        elif plan:
            source = self._from_plan(plan)
        elif script:
            source = self._from_script(script)
        else:
            source = self._from_text(text)

        self.set_state(status="working", progress=15, current_task=f"edit: {source['title'][:48]}")
        self.log(f"edit source: {source['origin']} · {len(source['shots'])} segments")

        edl = self._build_edl(source)
        self.last_edit = edl
        narration = "\n".join(segment["voiceover"] for segment in edl["segments"])
        report = rules.evaluate(
            script=narration,
            title=edl["title"],
            description=(source.get("description") or "") + "\nAI-generated visuals, avatar and voice narration.",
            platform="both",
            ai_generated=True,
        )
        if report["blockers"]:
            for segment in edl["segments"]:
                segment["voiceover"], _ = rules.sanitize(segment["voiceover"])
                segment["captions"] = self._captions(
                    segment["voiceover"],
                    segment["start_sec"],
                    segment["end_sec"],
                )
            report = rules.evaluate(
                script="\n".join(s["voiceover"] for s in edl["segments"]),
                title=edl["title"],
                description=(source.get("description") or "") + "\nAI-generated visuals, avatar and voice narration.",
                platform="both",
                ai_generated=True,
            )
        self.last_report = report

        provider, key = self.keys.resolve_for_agent(self.id)
        audio: List[Dict[str, Any]] = []
        audio_note = "not requested"
        if synthesize:
            if provider in ("elevenlabs", "openai") and key:
                self.set_state(progress=60, current_task=f"synthesising voiceover via {provider}")
                self.log(f"voiceover via {provider}")
                audio = await self._synthesize(edl["segments"], str(provider), str(key))
                generated = [a for a in audio if "file" in a]
                failures = [a for a in audio if "error" in a]
                audio_note = f"{len(generated)} file(s) generated in data/audio"
                if failures:
                    audio_note += f", {len(failures)} failed"
            else:
                audio_note = "no ElevenLabs/OpenAI key assigned — voiceover script only"
                self.log("voiceover requested without a usable key", "warn")

        self.last_audio = audio
        self.persist_artifacts()
        export = self._export_plan(source, edl)
        self.set_state(
            status="review",
            progress=100,
            current_task=f"{len(edl['segments'])} cuts · {_fmt(edl['duration'])} · compliance {report['score']}/100",
        )
        if report["ok"]:
            self.log(f"edit ready — compliance {report['score']}/100", "success")
        else:
            self.log(f"edit blocked: {', '.join(b['id'] for b in report['blockers'])}", "error")

        return self._format_reply(edl, report, export, audio, audio_note)

    def _export_plan(self, source: Dict[str, Any], edl: Dict[str, Any]) -> Dict[str, Any]:
        platform = "facebook" if "facebook" in source.get("platform", "").lower() else "youtube"
        preset = dict(EXPORT_PRESETS[platform])
        dimension = ((source.get("payload") or {}).get("dimension") or {}) if isinstance(source.get("payload"), dict) else {}
        if dimension.get("height", 0) > dimension.get("width", 0):
            preset["resolution"] = "1080x1920 (vertical)"
        return {
            "platform": platform,
            "preset": preset,
            "filename": f"{self._slug(edl['title'])}-final.mp4",
            "duration": _fmt(edl["duration"]),
        }

    # -- reply --------------------------------------------------------------
    def _format_reply(
        self,
        edl: Dict[str, Any],
        report: Dict[str, Any],
        export: Dict[str, Any],
        audio: List[Dict[str, Any]],
        audio_note: str,
    ) -> str:
        bl = lambda key: label(self.lang, key)  # noqa: E731
        bn = self.lang == "bn"
        lines = [
            f"{bl('delivered')} — {edl['title']}",
            f"{edl['origin']} · {len(edl['segments'])} {'সেগমেন্ট' if bn else 'segments'} · "
            f"{_fmt(edl['duration'])} · out {edl['last_out']}",
            "",
            bl("timeline"),
        ]
        for segment in edl["segments"]:
            lines.append(
                f"  {segment['index']}. [{segment['in']}–{segment['out']}] {segment['transition']} · "
                f"{segment['shot'].replace('_', ' ')}"
            )
            if segment["voiceover"]:
                lines.append(f"     {bl('vo')}: {segment['voiceover']}")
            if segment["overlay"]:
                lines.append(f"     {bl('overlay')}: {segment['overlay']}")
            if segment["captions"]:
                first = segment["captions"][0]
                lines.append(f"     {bl('captions')}: {len(segment['captions'])} cue(s) starting {first['start']} · \"{first['text'][:60]}\"")
            if segment.get("audio"):
                lines.append(f"     {bl('audio')}: {segment['audio']}")

        lines += [
            "",
            bl("music"),
            f"  bed: {edl['music']['bed']}",
            f"  level: {edl['music']['level']}",
            f"  master: {edl['music']['master']}",
            "",
            bl("voiceover"),
            f"  {audio_note}",
        ]
        for item in audio:
            if "file" in item:
                lines.append(f"     {item['index']}. {item['file']} · {item['bytes'] // 1024} KB · {item['provider']}")
            else:
                lines.append(f"     {item['index']}. FAILED — {item.get('error')}")

        preset = export["preset"]
        lines += [
            "",
            bl("export"),
            f"  file: {export['filename']} · {export['duration']}",
            f"  {preset['resolution']} · {preset['fps']} fps · {preset['video_bitrate']}",
            f"  audio: {preset['audio']}",
            f"  captions: {preset['captions']}",
            "",
            rules.format_report(report),
        ]
        if report["ok"]:
            lines.append(
                "পরবর্তী: প্রকাশের জন্য প্রস্তুত। ফাইলটি SEO এজেন্টে পাঠান, অথবা আগে 'ভয়েসওভার যোগ করো' চালান।"
                if bn else
                "NEXT: publish-ready. Hand the file to SEO AGENT for metadata, or run 'add voiceover' first."
            )
        else:
            lines.append("পরবর্তী: এক্সপোর্টের আগে উপরের ব্লকারগুলো ঠিক করুন।" if bn else
                         "NEXT: resolve the blockers above before exporting.")
        return "\n".join(lines)

    # -- snapshot -----------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        data = super().snapshot()
        if self.last_edit:
            data["edit"] = {
                "title": self.last_edit["title"],
                "segments": len(self.last_edit["segments"]),
                "duration": self.last_edit["duration"],
                "audio_files": len([a for a in self.last_audio if "file" in a]),
                "compliance_score": self.last_report["score"] if self.last_report else None,
            }
        return data


def build(bus: EventBus, keys: ApiKeyStore) -> VideoEditingAgent:
    return VideoEditingAgent(bus, keys)
