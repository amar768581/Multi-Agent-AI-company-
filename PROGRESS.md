# PROGRESS.md — CogniSphere Labs

> **Session protocol:** Read this file first. Resume from *Exact Next Step* only.
> Never rewrite an existing module — extend it or replace it in place with a note here.

Last updated: Session 7 (Phase 7 — department working artifacts survive restarts)

---

## Status Snapshot

| Field | Value |
|---|---|
| Phase | Phase 7 — every department resumes its last script/storyboard/book/campaign/SEO package after a restart |
| Session | 7 |
| Backend | FastAPI (Termux-friendly, no compiled extras) |
| Frontend | Vanilla HTML/CSS/JS served by FastAPI |
| Departments live | **6 / 6** + compliance gate |
| Pipeline | approve → script → video gen → video edit → ebook → leads → SEO → compliance |

---

## Completed Modules

### Phase 1 — foundation
- [x] Project structure, `requirements.txt`, `.env.example`, `.gitignore`, `README.md`
- [x] `config/api_keys.py` — key store, per-agent assignment, `.env` mirror, masking
- [x] `core/events.py`, `core/base_agent.py`, `core/registry.py`
- [x] `main.py` — state/chat/dispatch/settings endpoints + static frontend
- [x] `frontend/*` — animated pipeline, chat drawers, API Settings

### Phase 2 — script writing & compliance
- [x] `compliance/monetization_rules.py` — 15-rule YouTube/Facebook gate, scoring, sanitise
- [x] `core/llm.py` — shared stdlib LLM bridge (Gemini / Groq / OpenAI)
- [x] `agents/script_writing.py` — brief parsing, LLM + offline writer, compliance repair

### Phase 3 — video generation & editing
- [x] `agents/video_generation.py` — storyboard, shot classification, HeyGen submit/fallback
- [x] `agents/video_editing.py` — EDL, captions, export presets, ElevenLabs/OpenAI TTS

### Phase 4 — ebook, lead generation, SEO
- [x] `agents/ebook_writing.py` — outline → chapters → markdown export
- [x] `agents/lead_generation.py` — ICP, segments, sequence, consent checklist
- [x] `agents/seo_agent.py` — metadata, 7 on-page checks, platform checklists

### Phase 5 — mission execution
- [x] `agents/ceo_core.py` — approval starts an `asyncio` background task that runs every
      task in plan order, with a duplicate-run guard:
  - per-step directive per department (video steps consume the previous deliverable
    through the registry, SEO uses the book title on ebook-only missions),
  - task lifecycle `queued → running → done | failed | blocked` with notes/errors,
  - mission lifecycle `planned → in_progress → completed | partial | blocked`,
  - `mission.progress` percentage and deliverable records (title, summary, score,
    artifact such as the ebook export path or HeyGen video id),
  - compliance step gates every pathway (including lead-only) on the lowest score,
  - `_delivery_report()` appended to the CEO chat and written to the monitor log.
- [x] `agents/ceo_core.py` — intent matching now uses whole-word matching with plural
      tolerance, so "bookkeeping" no longer triggers Ebook and "facebook" no longer
      contains "book".
- [x] `frontend/app.js` + `styles.css` — polling speeds up to 500 ms while the hub is
      orchestrating; mission cards show status, live progress bar and
      running/failed/blocked task chips; node pipes animate on `running` tasks.
- [x] `main.py` — `/api/health` reports `phase: 7`

### Phase 6 — language + persistence
- [x] `core/language.py` — shared, dependency-free language layer:
  - `detect()` → `en` / `bn` / `banglish` (Bengali script, explicit requests, Banglish markers),
  - `normalize_commands()` maps Bengali and Banglish verbs/nouns to canonical English
    (`অনুমোদন`→approve, `রিভাইজ`→revise, `স্ট্যাটাস`→status, `স্ক্রিপ্ট`→script,
    `ভিডিও`→video, `স্টোরিবোর্ড`→storyboard, `কীওয়ার্ড`→keywords) **including inflections**
    (`স্ক্রিপ্টের`, `ভিডিওটা`), so technical commands keep working,
  - `clean_topic()` / `keywords()` / `duration_seconds()` / `chapter_count()` /
    `platform_hint()` keep Bengali Unicode (`\u0980-\u09FF`) intact and strip command
    words at both ends of a brief, and understand Bengali digits (৬০) and units,
  - `label()` provides Bengali/Banglish reply section names.
- [x] `core/llm.py` — `language_directive()` + `language=` on `generate()`; live providers
  are instructed to answer in Bengali/Banglish while keeping JSON keys and IDs in English.
- [x] All 7 agents — routing, topic extraction, help/status, offline content and replies
  localized: script (titles, hook, scenes, VO, description, tags), video (Bengali shot
  prompts, overlays that keep matras), editing (Bengali timelines/captions), ebook
  (Bengali outline + chapters + blurb), leads (Bengali ICP/segments/sequence with
  Bengali opt-out), SEO (Bengali title/description/tags/hashtags/pinned comment),
  CEO (Bengali plan, approval, status and delivery report).
- [x] `compliance/monetization_rules.py` — AI-disclosure rule also recognises
  `এআই-জেনারেটেড` / `কৃত্রিম বুদ্ধিমত্তা`, so Bengali content is not penalised.
- [x] `core/store.py` — `data/state.json` (UTF-8, `ensure_ascii=False`, atomic replace,
  chmod 600) for chat histories, missions and deliverables; `clear()` on reset.
- [x] `core/base_agent.py` — `record()` persists history; `self.lang` tracks the request.
- [x] `core/registry.py` — restores histories + missions on boot and marks unfinished
  missions `interrupted` (running tasks become `failed: server restarted`) with a log line.
- [x] `main.py` — `/api/health` exposes `persistence`; reset clears the persisted file.

### Phase 7 — durable department artifacts
- [x] `core/store.py` — new `artifacts` section in `data/state.json` with
      `save_artifact(agent_id, key, value)`, `artifact()` and `artifacts()`; empty values
      are pruned, `clear()` resets the section, and `summary()` exposes
      `agents_with_artifacts`.
- [x] `core/base_agent.py` — declarative persistence: a department lists
      `persisted_artifacts = {"script": "last_script", …}` and the base class provides
      `persist_artifacts()` / `restore_artifacts()` (state keys never leak into agent code).
- [x] All six departments persist after every successful run:
      script (`brief`, `script`, `report`, `mode`), video generation (`plan`, `report`,
      `render`), video editing (`edit`, `report`, `audio`), ebook (`book` incl. drafted
      chapters, `report`, `export`), lead generation (`campaign`, `report`), SEO
      (`package`, `report`, `source`). `lang` is stored for every department.
- [x] `agents/ebook_writing.py` — restores JSON string chapter keys back to ints so
      `write chapter 2` continues the saved book instead of failing on lookup.
- [x] `core/registry.py` — restores artifacts after histories/missions, isolates corrupt
      artifacts (warning, startup continues) and logs
      `working artifacts restored — N department(s) can continue previous work`.
- [x] `main.py` — `/api/health` reports `phase: 7`; persistence summary includes
      `agents_with_artifacts`.

## Current Active Task

Phase 7 verified. After a restart, every department resumes from its saved artifact:
`চ্যাপ্টার ২ লেখো`, `রিভাইজ: …`, `শেষ স্ক্রিপ্টের ভিডিও বানাও`, `optimize the last video`
and `status` continue the previous session's work.

## Pending Work (hardening, not features)

- [ ] Artifact schema version + migration guard (older `state.json` files are tolerated but not versioned)
- [ ] Provider connectivity probe on key save (currently format checks only)
- [ ] SSE stream (`/api/stream`) to replace adaptive polling
- [ ] Cancel/retry controls for a running mission
- [ ] Termux install script (`scripts/termux_setup.sh`)

## Exact Next Step

Verify the live-provider paths (Gemini/Groq/OpenAI, HeyGen, ElevenLabs) with real keys,
starting with the Bengali language directive and the JSON contracts. Update this file
before anything else.

---

## Architecture Notes (keep these invariants)

1. **One agent = one file** in `agents/`. Contract: `AGENT_SPEC` + `build(bus, keys)` +
   `async def handle(message, context)`.
2. **Never import one agent from another.** Handoffs and execution both go through
   `self.registry` / `AgentBase.handle`.
3. **Keys are never hard-coded and never returned raw.** `self.keys.resolve_for_agent(id)`.
4. **Every content output passes the compliance gate** before being marked ready.
5. **Offline-first:** every department has a deterministic fallback.
6. **Execution is asynchronous:** CEO `handle()` returns immediately after approval;
   the runner owns the loop and mutates mission/task state that the UI polls.
7. **UI contract is `/api/state`.** New fields go in `snapshot()` or the mission dict.
8. **Termux-safe:** pure-Python deps; artifacts under gitignored `data/`.

## Session Log

### Sessions 1–4
Foundation, compliance gate, all six departments, registry handoffs, 6/6 live.

### Session 5 — Phase 5
- Rebuilt `agents/ceo_core.py` around a background execution engine.
- Verified full pipeline over HTTP (`approve MSN-001`):
  - approval response returned in **0.00 s** (execution is asynchronous),
  - hub progress advanced 14 → 29 → 43 → 57 → 71 → 86 → 96 → 100 and then returned
    to `online`,
  - tasks progressed in strict order: script → video generation → video editing →
    ebook → leads → SEO → compliance,
  - 7/7 tasks `done`, 6 deliverables recorded, compliance gate **100/100**,
  - deliverable lines logged per step and a completion report appended to the CEO chat.
- Duplicate guard: a second `approve MSN-001` while running returned
  "still executing (33% overall)".
- Ebook pathway: 3 tasks (ebook → SEO → compliance), SEO consumed the book title,
  compliance 100/100, 2 deliverables.
- Lead-only pathway: 2 tasks (lead → compliance), compliance 100/100.
- Fixed intent routing: "bookkeeping" and "facebook" no longer false-match the ebook
  department; verified six routing cases including full-pipeline triggers.
- `node --check frontend/app.js` passes; no server errors during any run.
- Decision: pacing between steps (0.55 s) keeps the animated UI readable without SSE.

### Session 6 — Phase 6
- Added `core/language.py` + `core/store.py`; wired both through `core/llm.py`,
  `core/base_agent.py`, `core/registry.py`, `main.py` and all seven agents.
- Verified Bengali pipeline over HTTP:
  - `এআই এজেন্ট নিয়ে ৬০ সেকেন্ডের ভিডিও বানাও` → Bengali plan (script → video gen →
    video edit → SEO → compliance),
  - `অনুমোদন MSN-001` started execution; mission `completed`, 4 deliverables,
    compliance gate **92/100** (monetization-ready), Bengali delivery report in CEO chat,
  - `স্ট্যাটাস` → Bengali executive summary with mission progress.
- Verified every department directly: Bengali script (5 Bengali scenes + Bengali AI
  disclosure), Bengali ebook (outline, chapter 1 & 2, Bengali markdown export),
  Bengali lead campaign (ICP, 3 segments, 4 Bengali touches incl. opt-out), Bengali SEO
  package (Bengali metadata, 9 tags, hashtags, pinned comment), Bengali storyboard and
  EDL with intact Bengali overlays/captions.
- Banglish verified: `ai agent niye ekta script likho` returns Banglish conversational
  labels with Banglish voiceover lines.
- Persistence verified: restarting the server restored **7 agent histories / 57 messages**
  and both missions (`state restored — 2 mission(s), 57 message(s)`), with no
  `\u09xx` escaping in `data/state.json` (UTF-8).
- Crash recovery verified: killing the server mid-run marked the mission `interrupted`
  with `note: server restarted during execution`.
- `/api/system/reset` now calls `STATE.clear()` and reports zero missions/bytes.
- Fixed during verification: Bengali inflected commands (`স্ক্রিপ্টের`), trailing command
  words in topics, Bengali chapter digits (`চ্যাপ্টার ২`), and matra-stripping in shot
  overlays.

### Session 7 — Phase 7
- Added `artifacts` to `core/store.py` and declarative persistence to `core/base_agent.py`;
  all six departments now save and restore their working state.
- Restart verification (server killed, new process):
  - boot log: `state restored — 1 mission(s), 33 message(s)` and
    `working artifacts restored — 6 department(s) can continue previous work`,
  - `/api/agents` immediately showed the restored script (5 scenes, 100/100), storyboard
    (5 shots, 60 s), EDL (5 cuts), book (2 chapters), campaign (3 segments/4 touches) and
    SEO package, so the pipeline panel reflects previous work after a reboot.
  - `চ্যাপ্টার ২ লেখো` drafted chapter 2 of the saved book (int chapter keys restored
    correctly), `স্ট্যাটাস` reported the previous delivery, `রিভাইজ: হুক আরও ছোট করো`
    revised the restored brief, `শেষ স্ক্রিপ্টের ভিডিও বানাও` built from the restored
    script, video editing assembled the restored storyboard, `optimize the last video`
    reused the restored plan, and lead `স্ট্যাটাস` reported the saved campaign.
- `data/state.json` after six runs: `agents_with_artifacts: 6`, 122 KB, raw Bengali,
  no escapes; `/api/system/reset` clears histories, missions and artifacts to zero bytes.
- `compileall` passes for `core`, `agents`, `main.py`, `compliance`.
