# Architecture & Technical Design

This document details how **Personal Life Hub** implements a voice-first, multi-turn tool-calling agent in pure Python without external orchestration frameworks.

---

## 1. Pure-Python Agent Loop vs. LangChain / LangGraph

Instead of pulling in LangChain/LangGraph abstractions, `app/agent.py` + `app/tools.py` run a focused, inspectable ~150-line tool-calling loop directly over Groq's OpenAI-compatible `/chat/completions` API:

1. **Session Context Assembly**: Loads the active session from `agent_sessions` and the last 12 `agent_messages`, injecting compact `[Executed actions: domain #id: label]` annotations onto prior assistant messages so follow-up voice turns (*"check off the first item on that lecture note"*) resolve exact database IDs across turns.
2. **Bounded Tool Loop (`MAX_TOOL_ROUNDS = 12`)**:
   - Sends messages + 24 JSON tool schemas (`tools.TOOL_SCHEMAS`) + a 14-day local calendar context to `openai/gpt-oss-120b` (with automatic fallback to `qwen/qwen3.8-27b`, `openai/gpt-oss-20b`, and dynamic `/models` discovery on HTTP `404`).
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
| `upsert_watchlist_item` | Save or update a playlist, video, course, podcast, or article (with URL auto-detection, tags & episode progress) |
| `list_watchlist` | List or search saved watchlist items filtered by status, type, category, or query |
| `navigate_ui` | Switch the WebView2 desktop view (`hub`, `tasks`, `notes`, `timer`, `watchlist`, `gym`, `art`, `gaming`, `ask`, `settings`) |
| `delete_item` | Safely delete a task, note, gym log, art item, game, study session, or watchlist item — requires `confirmed=true` |

---

## 3. SQLite Schema & Legacy Migration (`app/db.py`)

Databases use `PRAGMA journal_mode=WAL` and `PRAGMA foreign_keys=ON`. `_migrate_db()` inspects existing tables on startup so older databases automatically receive new columns (`status`, `progress_note`, `start_date`, `timeline`, `frequency`, `parent_task_id`, `parent_item_id`, `is_recurring`, `target_days`, `daily_target_minutes`, `folder`, `summary`) and new tables (`task_daily_logs`, `study_sessions`, `active_timer`, `watchlist_items`) without data loss.

```mermaid
erDiagram
    tasks ||--o{ task_items : "has checklist steps"
    tasks ||--o{ tasks : "optional parent goal"
    tasks ||--o{ task_daily_logs : "logs Done Today dates"
    tasks ||--o{ study_sessions : "optional task link"
    notes ||--o{ study_sessions : "optional note link"
    notes ||--o{ watchlist_items : "optional linked note"
    agent_sessions ||--o{ agent_messages : "contains turns"

    tasks {
        int id PK
        text title
        text list_type "daily | want_to_do"
        int parent_task_id FK "ON DELETE SET NULL"
        int parent_item_id
        int is_recurring
        int target_days
        int daily_target_minutes
        text start_date
        text due_date
        text timeline
        text frequency
        text status "not_started | partially_completed | completed"
        text progress_note
        int done
    }
    task_daily_logs {
        int id PK
        int task_id FK "ON DELETE CASCADE"
        text date
        text note
        text completed_at
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
    watchlist_items {
        int id PK
        text title
        text url
        text content_type "playlist | video | course | podcast | article"
        text source
        text category
        text status "to_watch | watching | watched"
        int progress_current
        int progress_total
        int note_id FK "ON DELETE SET NULL"
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

### 3-State Task Auto-Rollup & Parent-Goal / Study Linking
- `0` sub-items checked → `not_started`
- `1 .. N-1` sub-items checked → `partially_completed`
- `N / N` sub-items checked → `completed` (for recurring daily habits, logs today into `task_daily_logs` and checks `days_completed / target_days` instead of closing future days prematurely)
- **3-Tier Parent Detection (`_analyze_parent_candidates_conn`)**: High-confidence matches auto-link Today's child task to its larger/recurring goal (and completing the child propagates to the parent); medium/tied matches populate `possible_parents` so the Voice Agent can ask the user to confirm; live focus timers count immediately in `get_study_stats()` and roll study seconds up into linked goals.

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

---

## 5. Win32 Global Hotkey, System Tray & Floating Pill (`app/desktop_host.py`)

When running via `run_app.py` (skipped cleanly under `--smoke-test`), `DesktopHost` spins up a dedicated 64-bit-safe `ctypes` Win32 message-loop thread owning:
1. **System-Wide Hotkey (`RegisterHotKey` + `MOD_NOREPEAT`)**: Parses hotkey strings such as `ctrl+shift+space` into Win32 modifier/VK codes and toggles hands-free recording from any focused app, playing subtle `winsound` feedback chimes (`start` / `stop` / `done` / `error`). Hotkey edits in `PATCH /api/settings` post `WM_RELOAD_HOTKEY` live without restarting the app.
2. **System Tray (`Shell_NotifyIconW`)**: Uses a Pillow-generated indigo `tray.ico` saved in `%LOCALAPPDATA%\PersonalLifeHub\`. With `minimize_to_tray=true`, clicking the window close button (`window.events.closing`) returns `False` and defers `window.hide()`, keeping the tray menu (**Open**, **Toggle Recording**, **Exit**) active in the background.
3. **Floating Indicator Pill (`WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE`)**: Displays a small rounded GDI pill at top-center while recording, transcribing, or summarizing — without stealing keyboard focus from lectures, editors, or games. State is also exposed over `/api/voice/global-state` so the frontend stays synchronized.

---

## 6. Watchlist Metadata & Dual Cloud Sync (`app/watchlist.py`, `app/cloud_sync.py`)

- **Safe URL Detection & Auto-Fill (`app/watchlist.py`)**: `is_safe_http_url()` restricts links to public `http://`/`https://` targets (blocking loopback, link-local, RFC1918 private IPs, and non-HTTP schemes). `fetch_url_metadata()` queries YouTube oEmbed and safe HTML `<meta property="og:title">` / `<title>` tags with deterministic URL fallbacks on offline errors.
- **Instant Local Cloud Folder Mirror (`sync_to_cloud_folder`)**: Detects installed **Google Drive Desktop**, **OneDrive**, and **Dropbox** roots on Windows, rendering organized `Notes/<Notebook>/<title>-<id>.md`, `Watchlist.md`, `Tasks.md`, and `lifehub-backup.json` atomically (`*.tmp` -> `replace`) only when file content actually changes. When `cloud_auto_sync` is enabled, note/watchlist writes trigger a debounced background sync thread.
- **Direct Google Drive OAuth (`sync_to_gdrive`)**: Uses `google-auth-oauthlib` (`InstalledAppFlow`) and `googleapiclient` with narrow `https://www.googleapis.com/auth/drive.file` scope so the app can create/update only its own `PersonalLifeHub` folder hierarchy in Drive. `google_credentials.json` and `google_token.json` never enter the Git repository.
