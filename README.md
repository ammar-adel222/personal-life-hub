# Personal Life Hub

A **voice-first, pure-Python agentic desktop app** built with **FastAPI + Edge WebView2 (`pywebview`) + SQLite + Groq (`gpt-oss-120b` / `qwen3.8-27b` + Whisper + Orpheus TTS)** to capture tasks, 3-state checklists, lecture notes, gym sessions, art projects, and gaming logs in one fast local workspace.

> Designed to be genuinely useful every day — and clean, modular, and self-contained without LangChain or LangGraph bloat.

---

## System Architecture

```mermaid
flowchart LR
    U["Voice or Text Input\n(Auto-Stop Silence VAD)"] -->|"16kHz Mono WAV"| W["Groq Whisper\nwhisper-large-v3-turbo"]
    W -->|"Transcript"| AG["Pure-Python Tool Agent\n(app/agent.py)"]
    U -->|"Typed Text"| AG
    AG <-->|"OpenAI Tool Schemas\n(gpt-oss-120b / qwen3.8-27b)"| G["Groq Chat API"]
    AG -->|"22 Typed Tools\n(app/tools.py)"| DB[("SQLite WAL\n%LOCALAPPDATA%\\PersonalLifeHub")]
    DB -->|"Sync .md/.txt"| FS["Local Notes Folder"]
    AG -->|"Reply"| TTS["Groq Orpheus TTS\n(orpheus-v1-english)"]
    TTS -->|"Fallback on offline/429"| LOCAL["Windows / Edge Speech"]
    AG -->|"UI State & Action Chips"| UI["Edge WebView2 Desktop UI"]
```

---

## Key Features

- **🎙️ Voice-First Multi-Turn Agent (`Alt+V` / `Ctrl+Space`)**:
  - Speak naturally across multiple turns (*"I went to the gym and did squats 100kg 3x5, started a portrait WIP, and watched an ML lecture tagged AI, neural-nets"*) — the agent reads existing records and calls every required tool automatically.
  - **Mid-Lecture Note Appending**: Pause a lecture, tap Speak, and add concepts or `- [ ]` research-later items straight onto the active lecture note.
  - **Pure-Python RMS Silence Auto-Stop**: Automatically detects when you finish speaking and submits hands-free (with an optional **Continuous Loop** toggle).
  - **Smart TTS Replies**: Uses **Groq Orpheus** (`canopylabs/orpheus-v1-english`) with automatic fallback to built-in Windows/Edge speech, defaulting to **Questions & Clarifications Only** (configurable to *All Replies* or *Muted*).
- **✅ 3-State Tasks & Auto-Rollup Checklists**:
  - Every task supports **`not_started` (`○`)**, **`partially_completed` (`◐`)**, and **`completed` (`✓`)** states plus optional progress notes.
  - Tasks can contain nested checklist steps — checking `1..N-1` steps automatically sets the parent task to **`partially_completed`** with a progress bar; checking all steps completes it.
  - **Active Tasks** and **Want to Do / Someday** lists with timelines (`start_date` / `due_date` / `timeline`), target frequencies (`frequency`), and 1-click promotion.
- **📝 Structured Markdown Notes, Notebooks & ✨ AI Organizer**:
  - **Read / Split / Edit Modes + Formatting Toolbar**: Render headings, callouts (`> [!NOTE]`, `> [!IMPORTANT]`), fenced code blocks, tables, highlighted text, links, and clickable `- [ ]` / `- [x]` checkboxes directly in preview.
  - **Notebooks & Filtering**: Organize notes into folders (`Lectures`, `Projects`, `Study`, `Personal`, `Ideas`, or custom notebooks), filter by pinned or `#tag`, search across summaries/titles/bodies, and sort by Updated, Created, or Title.
  - **✨ Organize with AI & Auto-Structured Voice Notes**: 1-click button (`Structured`, `Lecture Note`, `Concise TL;DR`) turns messy voice transcripts into crisp Markdown headings, bullets, summaries, tags, and follow-up checklists; Voice Agent also auto-structures dictated notes.
  - **Subfolder Sync**: Bidirectional synchronization with a local `.md`/`.txt` folder (mapping subfolders to note notebooks).
- **⏱️ Focus / Study Timer & Session History (`Alt+4`)**:
  - **Countdown / Pomodoro (`25/5`, `50/10`, `90/15`, or custom) & Open Stopwatch** with live top-bar timer pill visible across every view and gentle WebAudio completion chime.
  - **Tagging, Linking & Stats**: Categorize by subject (`Study`, `Lectures`, `Coding`, `Projects`, `Art`, `Reading`, `Research`, or custom), link sessions to any **Active Task** or **Note**, log past sessions manually, track streaks & 7-day charts, and control everything by voice (*"Start a 25 minute coding timer"*, *"I studied neural nets for 90 minutes"*).
- **🏋️ Simple Gym Tracker**:
  - 1-click **"Went to Gym Today"** toggle, monthly consistency calendar, fast sets/reps/weight logger (auto-marks Gym Day), 1-click repeat last set, and Personal Best history — zero routine-builder clutter.
- **🎨 Simple Art & 🎮 Gaming Trackers**:
  - **Art**: **"Drew Today"** calendar, Ideas to Draw, WIP/Finished cards, and local reference image attachments with lightbox preview.
  - **Gaming**: `Playing`, `Want to Play`, `Finished`, and `On Hold` filter tabs, `1–10` ratings, platform badges, and session notes.

---

## Privacy & Data Isolation

All personal data stays outside this repository in:

```text
%LOCALAPPDATA%\PersonalLifeHub
├── lifehub.db        # SQLite WAL database
├── settings.json     # Groq API key & preferences
├── app.log           # Windowless startup diagnostics
└── media\            # Art reference images
```

---

## Quick Start

1. **Create virtual environment & install dependencies** (Python 3.13+):
   ```powershell
   py -3.13 -m venv .venv --system-site-packages
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```
2. **Launch the desktop app**:
   - Double-click **`Start Personal Life Hub.vbs`** (windowless native Edge WebView2 launch), or run:
   ```powershell
   .\.venv\Scripts\python.exe run_app.py
   ```
3. **Configure Groq Free API**:
   - Open **⚙️ Settings** (`Alt+9`), paste your free API key from [Groq Console](https://console.groq.com/keys), click **Test Key**, and click **Save Settings**.
   - *(Everything also works offline via deterministic fallback heuristics if no key is configured.)*

---

## Keyboard Shortcuts

| Shortcut | Action |
| :--- | :--- |
| `Ctrl+K` | Focus top Quick-Capture / Agent input |
| `Alt+V` | Start or stop voice recording |
| `Ctrl+Space` | Start or stop voice recording (when not typing in a field) |
| `Esc` | Cancel active voice recording or close image modal |
| `Ctrl+S` | Save current or new note (when in Notes view) |
| `Alt+1` .. `Alt+9` | Jump to Hub, Tasks, Notes, Focus Timer, Gym, Art, Gaming, Voice Agent, or Settings |

---

## Running Tests & Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -v
.\.venv\Scripts\python.exe run_app.py --smoke-test
```

For deeper technical details (agent loop, 22-tool catalog, SQLite ER schema, RMS VAD, and extending TTS with Google TTS), see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
