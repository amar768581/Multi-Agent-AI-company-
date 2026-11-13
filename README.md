# CogniSphere Labs

A modular **Multi-Agent AI Company** — FastAPI backend + animated pipeline UI, built to
run comfortably on Termux (pure-Python deps, no build step, no compiled extras).

| Department | File | Scope |
|---|---|---|
| CEO CORE | `agents/ceo_core.py` | orchestrator, mission plans, approvals |
| Script Writing | `agents/script_writing.py` | plot & dialogue — **live**, compliance-checked |
| Video Generation | `agents/video_generation.py` | scenes & animation — **live** (HeyGen) |
| Video Editing | `agents/video_editing.py` | sequencing & post — **live** (ElevenLabs/OpenAI TTS) |
| Ebook Writing | `agents/ebook_writing.py` | structure, chapters, export — **live** |
| Lead Generation | `agents/lead_generation.py` | prospecting & marketing — **live** |
| SEO Agent | `agents/seo_agent.py` | keywords & on-page SEO — **live** |
| Compliance | `compliance/monetization_rules.py` | YouTube/Facebook policy gate — **live** |

## Run (Termux / Linux)

```bash
pkg install python            # Termux only
pip install -r requirements.txt
python main.py                # http://127.0.0.1:8000
```

`PORT=8080 python main.py` to change the port. Open the printed URL in the device browser.

## Missions execute themselves

Send a directive in the CEO CORE chat (e.g. `full pipeline: launch an AI content campaign`),
approve it (`approve MSN-001`), and the plan runs in the background, in order, passing each
deliverable to the next department. The pipeline panel shows live task chips, a mission
progress bar and animating pipes; the monitor logs every step and the CEO chat receives a
final deliverables report with the compliance score. A second approval while a mission is
running is refused instead of starting a duplicate.

Pathways: video (`script → video gen → video edit → SEO`), ebook (`ebook → SEO`),
leads (`lead → compliance`), and `full pipeline` for all six. Every pathway ends at the
YouTube/Facebook compliance gate.

## বাংলা ও Banglish

Every agent understands English, Bengali script and Banglish. Write naturally:

```
এআই এজেন্ট নিয়ে ৬০ সেকেন্ডের ভিডিও বানাও      → Bengali mission plan
অনুমোদন MSN-001                                → approve (approve MSN-001 also works)
স্ট্যাটাস / status · রিভাইজ: হুক ছোট করো / revise: …
```

Routing, topic/title/description extraction, shot prompts, ebook chapters, outreach
copy and SEO metadata are produced in Bengali when you write Bengali or ask for Bengali
content, and in Banglish when you ask for Banglish. Technical identifiers (`MSN-001`,
JSON keys, command words) stay in English. Comments and section labels also localise
(শিরোনাম, দৃশ্য, ডেলিভারেবল, কমপ্লায়েন্স গেট). The offline deterministic writers are
Bengali-capable, so the pipeline works without any provider key; with a Gemini/Groq/OpenAI
key, `core/llm.py` instructs the model to answer in the requested language.

## Per-agent key pools

Every chat drawer has its own **API KEYS** box. Pick the provider for that agent
(Gemini, Groq, OpenAI, HeyGen, ElevenLabs), then add up to 8 keys; each row can be made
active, edited or deleted, and the top badge shows how many are stored. Keys are tried in
order — **active key → remaining pool keys → the global key from API Settings** — so an
invalid key, a rate limit (HTTP 429) or a provider outage rolls over to the next key, then
to the deterministic offline writer if the whole pool fails. Script, ebook, lead and SEO
text generation, HeyGen renders and ElevenLabs/OpenAI voiceovers all follow that order.

Everything is saved immediately to `config/api_keys.json` (`agent_keys` / `agent_active`)
and applied to the next message — no restart. The UI only ever receives masked values;
raw keys never appear in responses, logs or the chat transcript.

## State survives restarts

Chat histories, missions, deliverables **and each department's working artifact** are
written to `data/state.json` (UTF-8, `ensure_ascii=False`, atomic replace, chmod 600), so
Bengali text is stored as-is rather than `\u09xx` escapes. Saved artifacts: script + brief
(SCRIPT WRITING), storyboard + render state (VIDEO GENERATION), EDL + audio list
(VIDEO EDITING), the whole book with drafted chapters (EBOOK WRITING), campaign
(LEAD GENERATION) and metadata package (SEO AGENT).

So a new daily session can simply continue:

```
চ্যাপ্টার ২ লেখো            → drafts chapter 2 of last session's book
রিভাইজ: হুক আরও ছোট করো     → revises the saved brief
শেষ স্ক্রিপ্টের ভিডিও বানাও  → builds from the restored script
optimize the last video      → reuses the restored storyboard
status                       → reports the saved delivery, score and counts
```

On boot the registry restores histories, missions and artifacts (corrupt artifacts are
skipped with a warning); a mission that was mid-run becomes `interrupted` with the running
task marked `failed: server restarted`. `POST /api/system/reset` clears the persisted file
as well as in-memory state (API keys are untouched).

## All six departments are live

- **Ebook Writing** — `write a 5-chapter ebook about AI workflows`, then `write chapter 2`,
  then `export` to `data/ebooks/<slug>.md` (front matter, TOC, chapters, disclosure footer).
- **Lead Generation** — ICP, targeting segments with real search strings (never invented
  contacts), a 4-touch email/LinkedIn sequence with opt-out, qualification rubric, realistic
  targets and a consent/deliverability checklist.
- **SEO Agent** — title, description with timestamps, tags, hashtags, thumbnail text,
  pinned comment, alternate titles, 7 on-page checks and YouTube/Facebook publishing
  checklists; consumes the latest storyboard or script automatically.
- Every department follows the same contract, routes keys through
  `resolve_for_agent`, falls back to a deterministic offline writer, and passes the
  compliance gate before marking output ready.

## What Phase 3 ships

- **Video Generation**: in its chat say `generate the video for the last script` and it
  turns the SCRIPT WRITING scenes into a shot list — per-shot prompts, shot type
  (avatar / b-roll / screen capture), camera move, overlay, timecode. With a HeyGen key
  plus `HEYGEN_AVATAR_ID` and `HEYGEN_VOICE_ID` it submits `/v2/video/generate` and
  returns the `video_id`; without them it returns the complete render payload and
  storyboard, never a fake success.
- **Video Editing**: `assemble the last storyboard` builds an EDL — in/out points,
  transitions, caption cues, music-bed and loudness spec (`-14 LUFS`), and YouTube or
  Facebook export presets. `add voiceover` synthesises each segment through ElevenLabs
  or OpenAI TTS into `data/audio/` (no key → timed voiceover script only).
- Both departments run the monetization gate before returning and expose compact
  `video` / `edit` state to the pipeline UI.

## What Phase 2 ships

- **Script Writing is a working department.** Give it a brief in its chat
  (`"60-second YouTube script about why small teams use AI agents, energetic tone"`) and
  it returns title options, hook, a timed scene-by-scene script with visuals and
  voiceover, CTA, description with chapters, tags and thumbnail text.
- **Provider-backed or offline.** With Gemini/Groq/OpenAI assigned it writes via the
  provider (JSON contract, one repair pass on policy failures); without a key it uses a
  deterministic offline template so the pipeline still runs.
- **Compliance gate** (`compliance/monetization_rules.py`): 15 rules covering copyright
  and originality, misleading/guaranteed claims, medical claims, prohibited content,
  engagement bait, metadata quality, AI disclosures and paid-promotion disclosure.
  Output is scored, blocked items are listed with fixes, and safe violations are
  auto-rewritten. Commands: `revise: <change>`, `status`, `help`.
- `core/llm.py` — shared Termux-safe LLM bridge (urllib in a worker thread).

## What Phase 1 ships

- Animated pipeline: CEO CORE hub + 6 department nodes with live data-flow pipes
  (glowing packets that speed up while a department works), live status and progress.
- **4K Pipeline Studio** preview: mirrors the live canvas scene and exports a true
  3840 × 2160 PNG (plus QHD/FHD) with fullscreen.
- Canvas-rendered 3D isometric workflow (`frontend/workflow-canvas.js`): golden CEO CORE
  sphere, perspective metallic board, golden constellation mesh, six colour-coded machine
  towers with animated bays, side robot helpers, sub-agent pedestals, glass pipes with
  flowing energy packets, live status LEDs/progress, FPS + flow-speed + Surge/Pause HUD,
  and a live notification stack (mission, department status and API-key events) whose
  department toasts open the matching chat drawer.
- Per-agent chat drawers (CEO CORE fully functional; departments answer as
  "module pending" until Phase 2 files land).
- **API Settings** panel: separate keys for Gemini, Groq, OpenAI, ElevenLabs, HeyGen,
  YouTube and Facebook, stored in `config/api_keys.json` (chmod 600) and mirrored to
  `.env`. Values are masked in the UI and never returned raw.
- Per-agent provider routing (`ApiKeyStore.resolve_for_agent`).
- Live monitor log + mission board, `POST /api/system/reset` to clear session state.

## Layout

```
agents/       one file per agent (never import agent→agent)
compliance/   monetization policy modules
compliance/   monetization_rules.py — policy gate used by content agents
config/       api_keys.py — key store + routing
core/         events.py, base_agent.py, registry.py, llm.py, language.py, store.py
frontend/     index.html, styles.css, app.js
main.py       FastAPI app
PROGRESS.md   session checkpoint — read this first
```

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | liveness + module/key counts |
| GET | `/api/state?since_log=` | full UI state (+ incremental logs) |
| GET/POST | `/api/agents/{id}/chat` | isolated history / send instruction |
| POST | `/api/ceo/dispatch` | automation shortcut to CEO CORE |
| GET | `/api/settings` | masked provider state + routing |
| POST | `/api/settings/keys` | save a provider key |
| DELETE | `/api/settings/keys/{provider}` | remove a key |
| POST | `/api/settings/assignments` | route an agent to a provider |
| POST | `/api/system/reset` | clear chats/logs/missions + `data/state.json` (keys kept) |

Keys are read from the JSON store, then `os.environ`, then `.env` — so agents work in
Termux whether you use the UI panel or export the env vars yourself.

Video providers also read optional settings from `.env`:
`HEYGEN_AVATAR_ID`, `HEYGEN_VOICE_ID`, `ELEVENLABS_VOICE_ID`, `ELEVENLABS_MODEL`,
`OPENAI_TTS_VOICE`, `OPENAI_TTS_MODEL`.
