# Personal Life Hub

A fast, lightweight, dark-mode Windows desktop app built with **Python 3.13 + FastAPI + Edge WebView2 (`pywebview`) + SQLite + Groq Free API (Llama & Whisper)** to capture and organize everyday life without bloat.

## Features

- **⚡ Top Smart Quick-Capture + 🎙️ Voice Input (`Ctrl+K` / `Alt+V`)**: Dump any thought by typing or speaking. Groq Whisper (`whisper-large-v3-turbo`) transcribes voice, and Groq Llama (`llama-3.3-70b-versatile`) automatically files tasks, someday ideas, gym logs, art progress, gaming notes, and general notes. Works offline with local fallback even without an API key.
- **✅ Tasks & Want to Do**: Separate **Daily Tasks** and **Want to Do / Someday** lists with 1-click promotion.
- **📝 Notes & Local Folder Sync**: Fast Markdown/plain-text notes with tags, pinning, instant search, safe sync with a local `.md`/`.txt` folder, and **Ask My Notes & Life** AI Q&A.
- **🏋️ Simple Gym Tracker**: 1-click **"Went to Gym Today"** attendance toggle, monthly consistency calendar, and fast exercise/sets/reps/weight logging with 1-click repeat and Personal Best history (zero routine-builder bloat).
- **🎨 Simple Art Tracker**: **"Drew Today"** attendance calendar, Ideas to Draw, WIP/Finished pieces, and optional local reference images.
- **🎮 Gaming Tracker**: Clean `Playing` / `Want to Play` / `Finished` / `On Hold` tracker with ratings, platform tags, and quick session notes.

## Privacy & Data Location

All personal data — SQLite database (`lifehub.db`), `settings.json` (including your Groq API key), and uploaded art reference images — is stored locally outside this repository in:

```text
%LOCALAPPDATA%\PersonalLifeHub
```

Nothing personal is ever written inside the project folder or committed to Git.

## Quick Start

1. Create/activate the virtual environment and install dependencies:
   ```powershell
   py -3.13 -m venv .venv --system-site-packages
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```
2. Launch the desktop app (double-click `Start Personal Life Hub.vbs` for windowless native launch, or run):
   ```powershell
   .\.venv\Scripts\python.exe run_app.py
   ```
3. Open **⚙️ Settings** inside the app to paste your free [Groq API key](https://console.groq.com/) and optionally pick a local `.md`/`.txt` notes folder to sync.
