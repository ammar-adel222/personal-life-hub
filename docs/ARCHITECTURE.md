# Architecture & Technical Design

This document details how **Personal Life Hub** implements a voice-first, multi-turn tool-calling agent in pure Python without external orchestration frameworks.

---

## 1. Pure-Python Agent Loop vs. LangChain / LangGraph

Instead of pulling in LangChain/LangGraph abstractions, `app/agent.py` + `app/tools.py` run a focused, inspectable ~150-line tool-calling loop directly over Groq's OpenAI-compatible `/chat/completions` API:

1. **Session Context Assembly**: Loads the active session from `agent_sessions` and the last 12 `agent_messages`, injecting compact `[Executed actions: domain #id: label]` annotations onto prior assistant messages so follow-up voice turns (*"check off the first item on that lecture note"*) resolve exact database IDs across turns.
2. **Bounded Tool Loop (`MAX_TOOL_ROUNDS = 12`)**:
   - Sends messages + 22 JSON tool schemas (`tools.TOOL_SCHEMAS`) + a 14-day local calendar context to `openai/gpt-oss-120b` (with automatic fallback to `qwen/qwen3.8-27b`, `openai/gpt-oss-20b`, and dynamic `/models` discovery on HTTP `404`).
   - Executes returned `tool_calls` synchronously in `app/tools.py`, appends structured JSON results as `"tool"` messages, and collects UI badges (`action_badge`) and navigation triggers (`navigate_to`).
   - When the model returns a natural-language response, persists both the reply and action metadata into SQLite and returns `is_question` so the UI knows whether to speak clarifications aloud.
3. **Offline Fallback**: When no Groq key is present (or the network is unreachable), `_offline_agent_fallback` handles checklist task syntax (`steps: ...`), tagged lecture notes, gym sets, and quick captures cleanly.

---

## 2. Agent Tool Catalog (`app/tools.py`)

| Tool | Purpose |
| :--- | :--- |
| `get_today_overview` | Read today's gym/art status, open tasks, notes, and active games |
| `list_tasks` | Inspect tasks + nested checklist items filtered by list, status, or query |
| `create_task` | Create Active or Want-to-Do task with timeline, frequency, dates & `checklist_items` |
| `create_tasks_batch` | Decompose a multi-item spoken plan into multiple Active Tasks in one call |
| `update_task` | Update title, priority, start/due dates, timeline, frequency, `progress_note`, or 3-state `status` |
| `manage_task_checklist` | Add, check, uncheck, or remove sub-items with automatic parent rollup |
| `search_notes` | Search notes by keywords, folder, pinned status, and `#tags` |
| `get_note` | Read full note content, folder, summary, tags, and Markdown checklist stats |
| `create_note` | Create a structured Markdown note with folder, TL;DR summary, tags, `- [ ]` checklists, and optional disk sync |
| `update_note` | Rewrite or organize an existing note's title, structured Markdown content, folder, summary, tags, or pin status |
| `append_to_note` | Append structured remarks (optionally under a `## heading`), research checklist items, folder, or tags to a note |
| `update_note_checklist` | Toggle `- [ ]` / `- [x]` lines inside a note by substring match |
| `log_gym_day` | Mark a date on the Gym consistency calendar |
| `log_gym_exercise` | Log sets/reps/weight and automatically mark that date as a Gym Day |
| `get_gym_stats` | Retrieve recent workout logs and Personal Bests per exercise |
| `upsert_art` | Mark Art Day and/or create or update an Idea, WIP, or Finished piece |
| `upsert_game` | Create or update game status, platform, rating (`1–10`), and notes |
| `control_study_timer` | Start/pause/resume/stop/reset countdown or stopwatch timer linked to a task or note |
| `log_study_session` | Log a completed study/focus session with duration, subject, topic, and optional task/note link |
| `get_study_stats` | Retrieve live timer state, today/7d/all-time focus hours, streak, subject breakdown, and sessions |
| `navigate_ui` | Switch the WebView2 desktop view (`hub`, `tasks`, `notes`, `timer`, `gym`, `art`, `gaming`, `ask`, `settings`) |
| `delete_item` | Safely delete a task, note, gym log, art item, game, or study session — requires `confirmed=true` |

---

## 3. SQLite Schema & Legacy Migration (`app/db.py`)

Databases use `PRAGMA journal_mode=WAL` and `PRAGMA foreign_keys=ON`. `_migrate_db()` inspects `PRAGMA table_info(tasks)` and `PRAGMA table_info(notes)` on startup so older databases automatically receive new columns (`status`, `progress_note`, `start_date`, `timeline`, `frequency`, `folder`, `summary`) without data loss.

```mermaid
erDiagram
    tasks ||--o{ task_items : "has checklist steps"
    tasks ||--o{ study_sessions : "optional task link"
    notes ||--o{ study_sessions : "optional note link"
    agent_sessions ||--o{ agent_messages : "contains turns"

    tasks {
        int id PK
        text title
        text list_type "daily | want_to_do"
        text start_date
        text due_date
        text timeline
        text frequency
        text status "not_started | partially_completed | completed"
        text progress_note
        int done
    }
    task_items {
        int id PK
        int task_id FK
        text text
        int done
        int position
    }
    notes {
        int id PK
        text title
        text folder "Notebook category (e.g. Lectures, Projects)"
        text summary "1-2 sentence TL;DR"
        text content "Structured Markdown + - [ ] checklists"
        text tags "JSON array"
        text source_path "Optional synced .md/.txt path"
    }
    study_sessions {
        int id PK
        text subject
        text topic
        text mode "countdown | stopwatch | manual"
        int duration_seconds
        text date
        int task_id FK "ON DELETE SET NULL"
        int note_id FK "ON DELETE SET NULL"
    }
    active_timer {
        int id PK "Singleton row id = 1"
        int active
        int paused
        text mode "countdown | stopwatch"
        text phase "focus | break"
        int target_seconds
        int accumulated_seconds
        real started_at_epoch
    }
    agent_sessions {
        int id PK
        text title
        text updated_at
    }
    agent_messages {
        int id PK
        int session_id FK
        text role "user | assistant"
        text content
        text actions_json
        text source "voice | text"
    }
```

### 3-State Task Auto-Rollup
- `0` sub-items checked → `not_started`
- `1 .. N-1` sub-items checked → `partially_completed`
- `N / N` sub-items checked → `completed`

---

## 4. Voice Input & Pluggable TTS Pipeline

1. **Recording & Pure-Python RMS VAD (`app/voice.py`)**:
   - Captures `16kHz`, mono, 16-bit PCM via `sounddevice.RawInputStream` into an in-memory buffer capped at 120 seconds.
   - Computes per-chunk RMS in pure Python (`array('h')` + `math.sqrt` — no `numpy` dependency). When `auto_silence` is enabled, once sustained speech exceeds `SPEECH_RMS_THRESHOLD` (`450`) and drops below `SILENCE_RMS_THRESHOLD` (`220`) for `silence_seconds` (default `3.5s`), the callback sets `_auto_stopped = True` and raises `sounddevice.CallbackStop` safely without calling `stream.stop()` inside the PortAudio thread. Manual Start/Stop is the default so longer multi-item voice plans never cut off mid-thought.
2. **Speech-to-Text**: Sends standard WAV bytes to Groq `whisper-large-v3-turbo` (`/openai/v1/audio/transcriptions`).
3. **Text-to-Speech (`app/tts.py`) & Extending with Google TTS**:
   - Primary provider calls Groq Orpheus (`canopylabs/orpheus-v1-english`, `response_format="wav"`).
   - Any missing key, rate limit (`429`), or network error returns `{"fallback_local": true}`, causing the UI to speak via `window.speechSynthesis`.
   - To add Google Cloud TTS or Gemini TTS later, implement `_synthesize_google_tts(text)` in `app/tts.py` returning WAV bytes and add a `google_api_key` setting in `app/config.py`.
