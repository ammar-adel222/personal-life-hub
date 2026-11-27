/* Personal Life Hub Frontend */

function localTodayStr() {
  const now = new Date();
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function shiftMonth(monthStr, delta) {
  const [y, m] = monthStr.split("-").map(Number);
  const dt = new Date(y, m - 1 + delta, 1);
  return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}`;
}

const state = {
  view: "hub",
  captureMode: "auto",
  settings: null,
  voiceMode: "auto_ai",
  autoSilence: false,
  continuousVoice: false,
  recording: false,
  voiceBusy: false,
  voiceTimerId: null,
  voicePollId: null,
  currentAudio: null,
  dashboard: null,
  tasks: [],
  taskFilter: "all",
  notes: [],
  noteFolders: null,
  noteTags: [],
  notesFolder: "all",
  notesTag: "",
  notesSort: "updated",
  noteEditorMode: "read",
  noteOrganizeStyle: "structured",
  noteDraft: null,
  activeNoteId: null,
  notesQuery: "",
  gymMonth: localTodayStr().slice(0, 7),
  artMonth: localTodayStr().slice(0, 7),
  gymFilterExercise: "",
  gameFilter: "all",
  activeSessionId: null,
  agentSessions: [],
  agentMessages: [],
  askQuery: "",
  askResult: null,
  timerState: null,
  timerClientSyncedAt: 0,
  timerTickId: null,
  timerCompleting: false,
  timerDraft: {
    mode: "countdown",
    focusMins: 25,
    breakMins: 5,
    subject: "Study",
    topic: "",
    taskId: "",
    noteId: "",
    notes: "",
  },
  studyData: null,
  watchlist: [],
  watchlistStatus: "all",
  watchlistType: "all",
  watchlistQuery: "",
  lastGlobalVoiceSeq: 0,
  globalVoicePollId: null,
};

// ---------------------------------------------------------------------------
// Helpers & API
// ---------------------------------------------------------------------------

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

async function api(url, options = {}) {
  const opts = { ...options };
  if (opts.body && !(opts.body instanceof FormData) && typeof opts.body === "object") {
    opts.headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
    opts.body = JSON.stringify(opts.body);
  }
  const resp = await fetch(url, opts);
  const contentType = resp.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await resp.json() : await resp.text();
  if (!resp.ok) {
    const msg = typeof data === "object" && data?.detail ? data.detail : String(data || resp.statusText);
    throw new Error(msg);
  }
  return data;
}

function toast(message, kind = "info") {
  const container = document.getElementById("toast-container");
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = message;
  container.appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

function isTypingInField(target) {
  if (!target) return false;
  const tag = (target.tagName || "").toUpperCase();
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable;
}

function formatTodayLabel(isoDate) {
  try {
    const dt = new Date(`${isoDate}T12:00:00`);
    return dt.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
  } catch {
    return isoDate;
  }
}

// ---------------------------------------------------------------------------
// Navigation & Shell
// ---------------------------------------------------------------------------

async function switchView(viewName) {
  const target = viewName === "agent" ? "ask" : viewName;
  state.view = target;
  document.querySelectorAll(".nav-item").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.view === target);
  });
  document.querySelectorAll(".view").forEach((sec) => {
    sec.classList.toggle("active", sec.id === `view-${target}`);
  });
  await renderCurrentView();
}

async function renderCurrentView() {
  try {
    if (state.view === "hub") await renderHub();
    else if (state.view === "tasks") await renderTasks();
    else if (state.view === "notes") await renderNotes();
    else if (state.view === "timer") await renderTimer();
    else if (state.view === "watchlist") await renderWatchlist();
    else if (state.view === "gym") await renderGym();
    else if (state.view === "art") await renderArt();
    else if (state.view === "gaming") await renderGaming();
    else if (state.view === "ask") await renderAsk();
    else if (state.view === "settings") await renderSettings();
  } catch (err) {
    toast(err.message || "Failed to load view", "error");
  }
}

async function refreshSettingsBadge() {
  try {
    const s = await api("/api/settings");
    state.settings = s;
    state.voiceMode = s.voice_mode || "auto_ai";
    state.autoSilence = s.auto_silence !== undefined ? Boolean(s.auto_silence) : false;
    updateVoiceModeLabel();
    const pill = document.getElementById("sidebar-ai-status");
    if (s.has_api_key) {
      pill.textContent = "Groq AI: Connected";
      pill.classList.add("ok");
    } else {
      pill.textContent = "Groq AI: Offline mode";
      pill.classList.remove("ok");
    }
  } catch {
    // ignore
  }
}

// ---------------------------------------------------------------------------
// Top Quick-Capture, Voice Agent Loop & TTS Playback
// ---------------------------------------------------------------------------

function updateVoiceModeLabel() {
  const btn = document.getElementById("voice-mode-toggle");
  btn.textContent = state.voiceMode === "auto_ai" ? "Auto-file" : "To text";
}

function stopCurrentSpeech() {
  if (state.currentAudio) {
    try {
      state.currentAudio.pause();
      state.currentAudio = null;
    } catch {
      // ignore
    }
  }
  if ("speechSynthesis" in window) {
    try {
      window.speechSynthesis.cancel();
    } catch {
      // ignore
    }
  }
}

function speakLocal(text) {
  return new Promise((resolve) => {
    if (!("speechSynthesis" in window)) {
      resolve();
      return;
    }
    try {
      window.speechSynthesis.cancel();
      const utt = new SpeechSynthesisUtterance(text);
      utt.rate = 1.05;
      utt.onend = () => resolve();
      utt.onerror = () => resolve();
      window.speechSynthesis.speak(utt);
    } catch {
      resolve();
    }
  });
}

async function playAgentSpeech(text, isQuestion = false, forceSpeak = false) {
  if (!text) return;
  const ttsMode = state.settings?.tts_mode || "questions_only";
  if (!forceSpeak) {
    if (ttsMode === "off") return;
    if (ttsMode === "questions_only" && !isQuestion) return;
  }

  stopCurrentSpeech();
  try {
    const resp = await fetch("/api/voice/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const ctype = resp.headers.get("content-type") || "";
    if (resp.ok && ctype.includes("audio/")) {
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      await new Promise((resolve) => {
        const audio = new Audio(url);
        state.currentAudio = audio;
        const done = () => {
          URL.revokeObjectURL(url);
          if (state.currentAudio === audio) state.currentAudio = null;
          resolve();
        };
        audio.onended = done;
        audio.onerror = done;
        audio.play().catch(done);
      });
      return;
    }
    const data = await resp.json().catch(() => ({}));
    if (data && data.fallback_local) {
      if (forceSpeak && data.reason && String(data.reason).toLowerCase().includes("terms")) {
        toast("Using Windows speech (accept Orpheus terms in Groq Console for AI voice)", "info");
      }
      await speakLocal(text);
    }
  } catch {
    await speakLocal(text);
  }
}

function formatHotkeyTooltip(hotkey) {
  const parts = String(hotkey || "")
    .split("+")
    .map((p) => p.trim())
    .filter(Boolean);
  if (!parts.length) return "Disabled";
  return parts
    .map((p) =>
      p.length === 1 || /^f\d{1,2}$/i.test(p)
        ? p.toUpperCase()
        : p[0].toUpperCase() + p.slice(1).toLowerCase()
    )
    .join("+");
}

function syncVoiceBtnWithGlobalState(gv) {
  const btn = document.getElementById("voice-btn");
  const label = document.getElementById("voice-btn-label");
  const timer = document.getElementById("voice-timer");
  if (!btn || !label || !timer || !gv) return;

  const hkLabel = formatHotkeyTooltip(gv.hotkey);
  btn.title = gv.hotkey
    ? `Global Voice Capture (${hkLabel} anywhere · Alt+V in-app)`
    : "Voice Capture (Alt+V or Ctrl+Space in-app)";

  if (state.recording || state.voiceBusy) return;

  if (gv.phase === "recording") {
    btn.classList.add("recording");
    label.textContent = "Global Rec…";
    timer.classList.remove("hidden");
    timer.textContent = `${Number(gv.elapsed_seconds || 0).toFixed(1)}s`;
  } else {
    btn.classList.remove("recording");
    label.textContent = gv.phase === "processing" ? "Transcribing…" : "🎙️ Speak";
    timer.classList.add("hidden");
  }
}

function setRecordingUI(active, elapsed = 0, speechDetected = false) {
  state.recording = active;
  const btn = document.getElementById("voice-btn");
  const label = document.getElementById("voice-btn-label");
  const timer = document.getElementById("voice-timer");
  const cancelBtn = document.getElementById("voice-cancel-btn");

  btn.classList.toggle("recording", active);
  cancelBtn.classList.toggle("hidden", !active);
  timer.classList.toggle("hidden", !active);
  label.textContent = active ? (speechDetected ? "Listening…" : "Stop & Send") : "🎙️ Speak";
  timer.textContent = `${elapsed.toFixed(1)}s`;

  const agentBtn = document.getElementById("agent-voice-btn");
  if (agentBtn) {
    agentBtn.classList.toggle("recording", active);
    const agentLbl = document.getElementById("agent-voice-label");
    if (agentLbl) {
      agentLbl.textContent = active
        ? (speechDetected ? `Hearing speech… (${elapsed.toFixed(1)}s)` : `Listening… (${elapsed.toFixed(1)}s)`)
        : "🎙️ Tap to Speak";
    }
  }

  if (state.voiceTimerId) {
    clearInterval(state.voiceTimerId);
    state.voiceTimerId = null;
  }
  if (active) {
    const started = performance.now() - elapsed * 1000;
    state.voiceTimerId = setInterval(() => {
      const sec = (performance.now() - started) / 1000;
      timer.textContent = `${sec.toFixed(1)}s`;
      const agentLbl = document.getElementById("agent-voice-label");
      if (agentLbl && state.recording) {
        agentLbl.textContent = `${speechDetected ? "Hearing speech" : "Listening"}… (${sec.toFixed(1)}s)`;
      }
    }, 100);
  }
}

function clearVoiceTimers() {
  if (state.voiceTimerId) {
    clearInterval(state.voiceTimerId);
    state.voiceTimerId = null;
  }
  if (state.voicePollId) {
    clearInterval(state.voicePollId);
    state.voicePollId = null;
  }
}

async function handleAgentResponse(res, transcriptPrefix = "") {
  if (res.session_id) {
    state.activeSessionId = res.session_id;
  }
  const msg = res.warning ? `${res.reply} (${res.warning})` : res.reply;
  const fullToast = transcriptPrefix ? `🎙️ "${transcriptPrefix}" → ${msg}` : msg;
  toast(fullToast || "Done!", res.warning ? "error" : "success");

  await syncTimerState();
  if (res.navigate_to && res.navigate_to !== state.view) {
    await switchView(res.navigate_to);
  } else {
    await renderCurrentView();
  }
  await playAgentSpeech(res.reply, Boolean(res.is_question));
}

async function submitCapture() {
  const input = document.getElementById("capture-input");
  const btn = document.getElementById("capture-submit-btn");
  const text = input.value.trim();
  if (!text) return;
  btn.disabled = true;
  btn.textContent = state.captureMode === "auto" ? "Thinking…" : "Filing…";
  try {
    if (state.captureMode === "auto") {
      const res = await api("/api/agent/chat", {
        method: "POST",
        body: { text, session_id: state.activeSessionId, source: "text" },
      });
      input.value = "";
      await handleAgentResponse(res);
    } else {
      const res = await api("/api/capture", {
        method: "POST",
        body: { text, mode: state.captureMode, source: "text" },
      });
      input.value = "";
      const msg = res.warning ? `${res.summary} (${res.warning})` : res.summary;
      toast(msg || "Captured!", res.warning ? "info" : "success");
      await syncTimerState();
      await renderCurrentView();
    }
  } catch (err) {
    toast(err.message, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "Capture";
  }
}

async function startVoiceRecording() {
  if (state.recording || state.voiceBusy) return;
  stopCurrentSpeech();
  try {
    await api("/api/voice/start", {
      method: "POST",
      body: {
        auto_silence: state.autoSilence,
        silence_seconds: state.settings?.silence_seconds ?? 1.8,
      },
    });
    setRecordingUI(true, 0, false);
    if (state.voicePollId) clearInterval(state.voicePollId);
    state.voicePollId = setInterval(async () => {
      if (!state.recording || state.voiceBusy) return;
      try {
        const st = await api("/api/voice/status");
        if (st.auto_stopped) {
          await stopAndProcessVoice();
        } else if (st.speech_detected) {
          const lbl = document.getElementById("voice-btn-label");
          if (lbl && state.recording) lbl.textContent = "Hearing…";
        }
      } catch {
        // ignore transient poll errors
      }
    }, 250);
  } catch (err) {
    setRecordingUI(false, 0, false);
    toast(err.message, "error");
  }
}

async function stopAndProcessVoice() {
  if (!state.recording || state.voiceBusy) return;
  state.voiceBusy = true;
  clearVoiceTimers();
  const btn = document.getElementById("voice-btn");
  btn.disabled = true;
  document.getElementById("voice-btn-label").textContent = "Transcribing…";
  const agentLbl = document.getElementById("agent-voice-label");
  if (agentLbl) agentLbl.textContent = "Transcribing & thinking…";

  try {
    const res = await api("/api/voice/stop", {
      method: "POST",
      body: {
        voice_mode: state.voiceMode,
        session_id: state.activeSessionId,
        use_agent: true,
      },
    });
    setRecordingUI(false, 0, false);
    if (res.voice_mode === "auto_ai" && res.agent) {
      await handleAgentResponse(res.agent, res.transcript);
      if (state.continuousVoice) {
        state.voiceBusy = false;
        btn.disabled = false;
        await startVoiceRecording();
        return;
      }
    } else if (res.voice_mode === "auto_ai" && res.capture) {
      toast(`🎙️ "${res.transcript}" → ${res.capture.summary}`, "success");
      await renderCurrentView();
    } else {
      const input = document.getElementById("capture-input");
      input.value = res.transcript || "";
      input.focus();
      toast("🎙️ Transcribed to capture box", "info");
    }
  } catch (err) {
    setRecordingUI(false, 0, false);
    toast(err.message, "error");
  } finally {
    state.voiceBusy = false;
    btn.disabled = false;
  }
}

async function toggleVoiceRecording() {
  if (!state.recording) {
    await startVoiceRecording();
  } else {
    await stopAndProcessVoice();
  }
}

async function cancelVoiceRecording() {
  state.continuousVoice = false;
  const contCb = document.getElementById("agent-continuous-cb");
  if (contCb) contCb.checked = false;
  clearVoiceTimers();
  if (!state.recording) return;
  try {
    await api("/api/voice/cancel", { method: "POST" });
  } catch {
    // ignore
  }
  setRecordingUI(false, 0, false);
  toast("Voice recording cancelled", "info");
}

// ---------------------------------------------------------------------------
// View 1: Hub (Today Overview)
// ---------------------------------------------------------------------------

function nextTaskStatus(status) {
  if (status === "not_started") return "partially_completed";
  if (status === "partially_completed") return "completed";
  return "not_started";
}

function taskStatusIcon(status) {
  if (status === "completed") return "✓";
  if (status === "partially_completed") return "◐";
  return "○";
}

function taskStatusBadge(t) {
  if (t.status === "partially_completed") {
    return '<span class="badge badge-art">◐ Partial</span>';
  }
  if (t.status === "completed") {
    return '<span class="badge badge-gym">✓ Completed</span>';
  }
  return "";
}

function formatDueBadge(dueDate, done) {
  if (!dueDate) return "";
  if (done) return `<span class="badge">Due ${esc(dueDate)}</span>`;
  const [ty, tm, td] = localTodayStr().split("-").map(Number);
  const [dy, dm, dd] = dueDate.split("-").map(Number);
  if (!dy || !dm || !dd) return `<span class="badge">Due ${esc(dueDate)}</span>`;
  const diffDays = Math.round((new Date(dy, dm - 1, dd) - new Date(ty, tm - 1, td)) / 86400000);
  if (diffDays < 0) {
    return `<span class="badge badge-high">Due ${esc(dueDate)} (${Math.abs(diffDays)}d overdue)</span>`;
  }
  if (diffDays === 0) {
    return `<span class="badge badge-art">Due Today</span>`;
  }
  return `<span class="badge">Due ${esc(dueDate)} · ${diffDays}d left</span>`;
}

function taskMetaBadges(t) {
  return `
    ${taskStatusBadge(t)}
    ${t.checklist_total ? `<span class="badge">${t.checklist_done}/${t.checklist_total} steps</span>` : ""}
    ${t.timeline ? `<span class="badge badge-accent">🗓️ ${esc(t.timeline)}</span>` : ""}
    ${t.frequency ? `<span class="badge badge-gym">🔁 ${esc(t.frequency)}</span>` : ""}
    ${t.priority === "high" ? '<span class="badge badge-high">High</span>' : ""}
    ${t.category ? `<span class="badge">${esc(t.category)}</span>` : ""}
    ${formatDueBadge(t.due_date, t.done)}
    ${t.progress_note ? `<span style="color:var(--art)">Note: ${esc(t.progress_note)}</span>` : ""}
  `;
}

async function renderHub() {
  const data = await api("/api/dashboard");
  state.dashboard = data;
  document.getElementById("sidebar-today").textContent = formatTodayLabel(data.today);

  const el = document.getElementById("view-hub");
  const taskHtml = data.daily_tasks.length
    ? data.daily_tasks
        .map(
          (t) => `
      <div class="list-row">
        <button type="button" class="status-btn ${esc(t.status)}" data-hub-cycle="${t.id}" data-cur="${esc(t.status)}" title="Cycle: Not Started → Partial → Completed">${taskStatusIcon(t.status)}</button>
        <div class="row-main">
          <div class="row-title">${esc(t.title)}</div>
          <div class="row-meta">${taskMetaBadges(t)}</div>
        </div>
      </div>`
        )
        .join("")
    : `<div class="empty-state">All caught up on active tasks!</div>`;

  const wantHtml = data.want_to_do.length
    ? data.want_to_do
        .map(
          (w) => `
      <div class="list-row">
        <div class="row-main">
          <div class="row-title">${esc(w.title)}</div>
          ${w.category ? `<div class="row-meta"><span>${esc(w.category)}</span></div>` : ""}
        </div>
        <button class="btn btn-xs btn-ghost" data-hub-promote="${w.id}" title="Move to Daily Tasks">→ Today</button>
      </div>`
        )
        .join("")
    : `<div class="empty-state">No someday items yet.</div>`;

  const notesHtml = data.recent_notes.length
    ? data.recent_notes
        .map(
          (n) => `
      <div class="note-list-item" data-jump-note="${n.id}">
        <div class="row-title">${n.pinned ? "📌 " : ""}${esc(n.title)}</div>
        <div class="row-meta">
          ${n.folder ? `<span class="badge badge-accent">📁 ${esc(n.folder)}</span>` : ""}
          <span>${esc(n.summary || stripMarkdownPreview(n.content, 90))}</span>
        </div>
      </div>`
        )
        .join("")
    : `<div class="empty-state">No notes saved yet.</div>`;

  const hobbyHtml = `
    <div class="item-list">
      ${data.playing_games
        .map(
          (g) => `
        <div class="list-row" data-jump-view="gaming" style="cursor:pointer">
          <span class="badge badge-game">🎮 Playing</span>
          <div class="row-main">
            <div class="row-title">${esc(g.title)}</div>
            ${g.notes ? `<div class="row-meta">${esc(g.notes.slice(0, 80))}</div>` : ""}
          </div>
        </div>`
        )
        .join("")}
      ${data.art_wips
        .map(
          (a) => `
        <div class="list-row" data-jump-view="art" style="cursor:pointer">
          <span class="badge badge-art">🎨 WIP</span>
          <div class="row-main">
            <div class="row-title">${esc(a.title)}</div>
            ${a.notes ? `<div class="row-meta">${esc(a.notes.slice(0, 80))}</div>` : ""}
          </div>
        </div>`
        )
        .join("")}
      ${!data.playing_games.length && !data.art_wips.length ? '<div class="empty-state">No active art WIPs or games right now.</div>' : ""}
    </div>`;

  el.innerHTML = `
    <div class="view-header">
      <div>
        <h1 class="view-title">⚡ Today's Hub</h1>
        <p class="view-sub">${esc(formatTodayLabel(data.today))} · Quick check-ins and active focus</p>
      </div>
      <div style="display:flex; gap:8px; flex-wrap:wrap;">
        <button id="hub-gym-toggle" class="btn btn-gym ${data.gym.today_active ? "active" : ""}">
          ${data.gym.today_active ? "✓ Went to Gym Today" : "🏋️ Mark Gym Today"}
        </button>
        <button id="hub-art-toggle" class="btn btn-art ${data.art.today_active ? "active" : ""}">
          ${data.art.today_active ? "✓ Drew Today" : "🎨 Mark Art Today"}
        </button>
      </div>
    </div>

    <div class="stat-strip">
      <div class="stat-card">
        <span class="stat-label">Active Tasks</span>
        <span class="stat-value">${data.daily_open_count} open <span style="font-size:13px;color:var(--muted)">(${data.daily_done_today_count} done)</span></span>
      </div>
      <div class="stat-card">
        <span class="stat-label">Gym This Month</span>
        <span class="stat-value" style="color:var(--gym)">${data.gym.month_count} days <span style="font-size:13px;color:var(--muted)">(${data.gym.streak}d streak)</span></span>
      </div>
      <div class="stat-card">
        <span class="stat-label">Art This Month</span>
        <span class="stat-value" style="color:var(--art)">${data.art.month_count} days <span style="font-size:13px;color:var(--muted)">(${data.art.streak}d streak)</span></span>
      </div>
      <div class="stat-card">
        <span class="stat-label">Playing &amp; Art WIPs</span>
        <span class="stat-value">${data.playing_games.length + data.art_wips.length} active</span>
      </div>
      <div class="stat-card" data-jump-view="timer" style="cursor:pointer" title="Open Focus Timer (Alt+4)">
        <span class="stat-label">⏱️ Study Today</span>
        <span class="stat-value" style="color:var(--accent-hover)">${esc(formatDurationHuman(data.study?.today_seconds || 0))} <span style="font-size:13px;color:var(--muted)">(${data.study?.week_hours || 0}h / 7d)</span></span>
      </div>
    </div>

    <div class="grid-2">
      <div class="card">
        <div class="card-header">
          <h2 class="card-title">✅ Active Tasks</h2>
          <button class="btn btn-xs btn-ghost" data-jump-view="tasks">Open All</button>
        </div>
        <div class="item-list">${taskHtml}</div>
      </div>

      <div class="card">
        <div class="card-header">
          <h2 class="card-title">🌟 Want to Do / Someday</h2>
          <button class="btn btn-xs btn-ghost" data-jump-view="tasks">Open All</button>
        </div>
        <div class="item-list">${wantHtml}</div>
      </div>

      <div class="card">
        <div class="card-header">
          <h2 class="card-title">📝 Recent Notes</h2>
          <button class="btn btn-xs btn-ghost" data-jump-view="notes">Open Notes</button>
        </div>
        <div class="item-list">${notesHtml}</div>
      </div>

      <div class="card">
        <div class="card-header">
          <h2 class="card-title">🎨 &amp; 🎮 Active Hobbies</h2>
        </div>
        ${hobbyHtml}
      </div>
    </div>
  `;

  el.querySelector("#hub-gym-toggle").addEventListener("click", async () => {
    await api("/api/activity-days", { method: "POST", body: { domain: "gym" } });
    await renderHub();
  });
  el.querySelector("#hub-art-toggle").addEventListener("click", async () => {
    await api("/api/activity-days", { method: "POST", body: { domain: "art" } });
    await renderHub();
  });
  el.querySelectorAll("[data-hub-cycle]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const next = nextTaskStatus(btn.dataset.cur);
      await api(`/api/tasks/${btn.dataset.hubCycle}`, { method: "PATCH", body: { status: next } });
      await renderHub();
    });
  });
  el.querySelectorAll("[data-hub-promote]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await api(`/api/tasks/${btn.dataset.hubPromote}`, { method: "PATCH", body: { list_type: "daily" } });
      toast("Moved to Daily Tasks", "success");
      await renderHub();
    });
  });
  el.querySelectorAll("[data-jump-view]").forEach((btn) => {
    btn.addEventListener("click", () => switchView(btn.dataset.jumpView));
  });
  el.querySelectorAll("[data-jump-note]").forEach((card) => {
    card.addEventListener("click", () => {
      state.notesFolder = "all";
      state.notesTag = "";
      state.notesQuery = "";
      state.noteDraft = null;
      state.activeNoteId = Number(card.dataset.jumpNote);
      state.noteEditorMode = "read";
      switchView("notes");
    });
  });
}

// ---------------------------------------------------------------------------
// View 2: Tasks & Want to Do
// ---------------------------------------------------------------------------

async function renderTasks() {
  const { items } = await api("/api/tasks");
  state.tasks = items;

  const filterMatches = (t) =>
    state.taskFilter === "all" ? true : t.status === state.taskFilter;

  const daily = items.filter((t) => t.list_type === "daily" && filterMatches(t));
  const want = items.filter((t) => t.list_type === "want_to_do" && filterMatches(t));

  const counts = {
    all: items.length,
    not_started: items.filter((t) => t.status === "not_started").length,
    partially_completed: items.filter((t) => t.status === "partially_completed").length,
    completed: items.filter((t) => t.status === "completed").length,
  };

  const renderTaskRow = (t, isWant) => {
    const pct = t.checklist_total ? Math.round((t.checklist_done / t.checklist_total) * 100) : 0;
    return `
    <div class="task-card ${esc(t.status)}">
      <div style="display:flex;align-items:flex-start;gap:10px;">
        <button type="button" class="status-btn ${esc(t.status)}" data-task-cycle="${t.id}" data-cur="${esc(t.status)}" title="Cycle: Not Started → Partially Completed → Completed">
          ${taskStatusIcon(t.status)}
        </button>
        <div class="row-main">
          <div class="row-title">${esc(t.title)}</div>
          <div class="row-meta">${taskMetaBadges(t)}</div>
          ${
            t.checklist_total
              ? `<div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>`
              : ""
          }
        </div>
        <div class="row-actions">
          ${
            isWant
              ? `<button class="btn btn-xs btn-ghost" data-move-list="${t.id}" data-target="daily" title="Move to Active Tasks">→ Active</button>`
              : `<button class="btn btn-xs btn-ghost" data-move-list="${t.id}" data-target="want_to_do" title="Move to Want to Do">→ Someday</button>`
          }
          <button class="btn btn-xs btn-danger" data-del-task="${t.id}" title="Delete">✕</button>
        </div>
      </div>

      <div class="checklist-box">
        ${(t.checklist_items || [])
          .map(
            (sub) => `
          <div class="sub-item-row ${sub.done ? "done" : ""}">
            <input type="checkbox" class="checkbox" data-sub-toggle="${t.id}" data-sub-id="${sub.id}" ${sub.done ? "checked" : ""} />
            <span style="flex:1">${esc(sub.text)}</span>
            <button type="button" class="btn btn-xs btn-ghost" data-sub-del="${t.id}" data-sub-id="${sub.id}" title="Remove step">✕</button>
          </div>`
          )
          .join("")}
        <form class="sub-add-form" data-sub-add-form="${t.id}">
          <input class="input" type="text" placeholder="+ Add checklist step…" />
          <button type="submit" class="btn btn-xs btn-ghost">Add</button>
        </form>
      </div>
    </div>`;
  };

  const tabs = [
    { key: "all", label: "All" },
    { key: "not_started", label: "○ Not Started" },
    { key: "partially_completed", label: "◐ Partially Completed" },
    { key: "completed", label: "✓ Completed" },
  ];

  const el = document.getElementById("view-tasks");
  el.innerHTML = `
    <div class="view-header">
      <div>
        <h1 class="view-title">✅ Tasks &amp; Want to Do</h1>
        <p class="view-sub">3-state checklist tasks (Not Started · ◐ Partial · ✓ Completed) — checking sub-steps updates progress automatically</p>
      </div>
    </div>

    <div class="tab-bar">
      ${tabs
        .map(
          (tab) => `
        <button class="tab-btn ${state.taskFilter === tab.key ? "active" : ""}" data-task-filter="${tab.key}">
          ${tab.label} (${counts[tab.key]})
        </button>`
        )
        .join("")}
    </div>

    <div class="grid-2">
      <div class="card">
        <div class="card-header">
          <h2 class="card-title">Active Tasks <span class="badge badge-accent">${daily.filter((x) => !x.done).length} open</span></h2>
        </div>
        <form id="form-daily-task" class="inline-form">
          <input id="daily-title" class="input grow" type="text" placeholder="Task title…" required />
          <input id="daily-steps" class="input grow" type="text" placeholder="Checklist steps (comma-separated, optional)" />
          <input id="daily-timeline" class="input" type="text" placeholder="Timeline (e.g. Next 10d)" style="width:145px" />
          <input id="daily-frequency" class="input" type="text" placeholder="Frequency (e.g. 4x/wk)" style="width:140px" />
          <select id="daily-priority">
            <option value="normal">Normal</option>
            <option value="high">High</option>
          </select>
          <input id="daily-due" class="input" type="date" title="Due date" />
          <button type="submit" class="btn btn-primary btn-sm">Add</button>
        </form>
        <div class="item-list">
          ${daily.length ? daily.map((t) => renderTaskRow(t, false)).join("") : '<div class="empty-state">No matching active tasks.</div>'}
        </div>
      </div>

      <div class="card">
        <div class="card-header">
          <h2 class="card-title">Want to Do / Someday <span class="badge">${want.filter((x) => !x.done).length} open</span></h2>
        </div>
        <form id="form-want-task" class="inline-form">
          <input id="want-title" class="input grow" type="text" placeholder="Thing you want to do…" required />
          <input id="want-steps" class="input grow" type="text" placeholder="Checklist steps (optional)" />
          <input id="want-cat" class="input" type="text" placeholder="Tag" style="width:100px" />
          <button type="submit" class="btn btn-primary btn-sm">Add</button>
        </form>
        <div class="item-list">
          ${want.length ? want.map((t) => renderTaskRow(t, true)).join("") : '<div class="empty-state">No matching someday items.</div>'}
        </div>
      </div>
    </div>
  `;

  el.querySelectorAll("[data-task-filter]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      state.taskFilter = btn.dataset.taskFilter;
      await renderTasks();
    });
  });

  el.querySelector("#form-daily-task").addEventListener("submit", async (e) => {
    e.preventDefault();
    const title = el.querySelector("#daily-title").value.trim();
    if (!title) return;
    const rawSteps = el.querySelector("#daily-steps").value;
    const checklistItems = rawSteps
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    await api("/api/tasks", {
      method: "POST",
      body: {
        title,
        list_type: "daily",
        priority: el.querySelector("#daily-priority").value,
        due_date: el.querySelector("#daily-due").value || null,
        timeline: el.querySelector("#daily-timeline").value.trim(),
        frequency: el.querySelector("#daily-frequency").value.trim(),
        checklist_items: checklistItems,
      },
    });
    await renderTasks();
  });

  el.querySelector("#form-want-task").addEventListener("submit", async (e) => {
    e.preventDefault();
    const title = el.querySelector("#want-title").value.trim();
    if (!title) return;
    const rawSteps = el.querySelector("#want-steps").value;
    const checklistItems = rawSteps
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    await api("/api/tasks", {
      method: "POST",
      body: {
        title,
        list_type: "want_to_do",
        category: el.querySelector("#want-cat").value.trim(),
        checklist_items: checklistItems,
      },
    });
    await renderTasks();
  });

  el.querySelectorAll("[data-task-cycle]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const next = nextTaskStatus(btn.dataset.cur);
      await api(`/api/tasks/${btn.dataset.taskCycle}`, { method: "PATCH", body: { status: next } });
      await renderTasks();
    });
  });

  el.querySelectorAll("[data-sub-toggle]").forEach((cb) => {
    cb.addEventListener("change", async () => {
      await api(`/api/tasks/${cb.dataset.subToggle}/items/${cb.dataset.subId}`, {
        method: "PATCH",
        body: { done: cb.checked },
      });
      await renderTasks();
    });
  });

  el.querySelectorAll("[data-sub-del]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await api(`/api/tasks/${btn.dataset.subDel}/items/${btn.dataset.subId}`, {
        method: "DELETE",
      });
      await renderTasks();
    });
  });

  el.querySelectorAll("[data-sub-add-form]").forEach((form) => {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const input = form.querySelector("input");
      const text = input.value.trim();
      if (!text) return;
      await api(`/api/tasks/${form.dataset.subAddForm}/items`, {
        method: "POST",
        body: { text },
      });
      await renderTasks();
    });
  });

  el.querySelectorAll("[data-move-list]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await api(`/api/tasks/${btn.dataset.moveList}`, {
        method: "PATCH",
        body: { list_type: btn.dataset.target },
      });
      await renderTasks();
    });
  });

  el.querySelectorAll("[data-del-task]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await api(`/api/tasks/${btn.dataset.delTask}`, { method: "DELETE" });
      await renderTasks();
    });
  });
}

// ---------------------------------------------------------------------------
// View 3: Structured Markdown Notes, Folders & AI Note Organizer
// ---------------------------------------------------------------------------

const FOLDER_ICONS = {
  Lectures: "🎓",
  Projects: "🚀",
  Study: "📖",
  Personal: "🏠",
  Ideas: "💡",
};

const NOTE_TEMPLATES = {
  lecture: {
    folder: "Lectures",
    tags: "lecture, study",
    content: [
      "## Overview",
      "Topic, instructor, or main theme of the session.",
      "",
      "## Core Concepts",
      "- Key idea or definition",
      "- Important mechanism or formula",
      "",
      "> [!IMPORTANT]",
      "> Core takeaway to remember from this lecture.",
      "",
      "## To Research / Review",
      "- [ ] Concept or paper to look into later",
      "- [ ] Practice exercise or follow-up question",
    ].join("\n"),
  },
  project: {
    folder: "Projects",
    tags: "project, planning",
    content: [
      "## Goal & Scope",
      "What this milestone aims to ship.",
      "",
      "## Architecture / Notes",
      "- Component or design decision",
      "- Constraints and trade-offs",
      "",
      "> [!NOTE]",
      "> Open risk, dependency, or blocker to watch.",
      "",
      "## Next Action Items",
      "- [ ] First deliverable",
      "- [ ] Second deliverable",
    ].join("\n"),
  },
  study: {
    folder: "Study",
    tags: "study, research",
    content: [
      "## Question / Hypothesis",
      "What I am trying to understand or solve.",
      "",
      "## Findings",
      "- Observation 1",
      "- Observation 2",
      "",
      "```python",
      "# Example or formula snippet",
      "```",
      "",
      "## Review Checklist",
      "- [ ] Summarize in own words",
    ].join("\n"),
  },
};

function stripMarkdownPreview(rawMd, maxLen = 110) {
  let s = String(rawMd || "");
  s = s.replace(/```[\s\S]*?```/g, " [code] ");
  s = s.replace(/`([^`]*)`/g, "$1");
  s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, "$1");
  s = s.replace(/^\s*>\s*\[!(?:NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*/gim, "");
  s = s.replace(/^\s{0,3}(?:#{1,6}|>|[-*]|\d+\.)\s+/gm, "");
  s = s.replace(/\[([ xX])\]\s+/g, "");
  s = s.replace(/[*_~=]+/g, "");
  s = s.replace(/\s+/g, " ").trim();
  if (s.length > maxLen) {
    return `${s.slice(0, maxLen - 1).trimEnd()}…`;
  }
  return s;
}

function formatInlineMd(rawText) {
  const inlineCodes = [];
  let text = String(rawText || "").replace(/`([^`\n]+)`/g, (_m, code) => {
    const idx = inlineCodes.length;
    inlineCodes.push(`<code class="md-inline-code">${esc(code)}</code>`);
    return `\u0000IC${idx}\u0000`;
  });

  text = esc(text);

  const links = [];
  text = text.replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, (full, label, hrefRaw) => {
    const decodedHref = hrefRaw.replaceAll("&amp;", "&").trim();
    if (!/^(https?:\/\/|mailto:|#)/i.test(decodedHref)) {
      return label;
    }
    const idx = links.length;
    links.push(
      `<a class="md-link" href="${esc(decodedHref)}" target="_blank" rel="noopener noreferrer">${label}</a>`
    );
    return `\u0000LN${idx}\u0000`;
  });

  text = text.replace(/~~([^~\n]+)~~/g, "<del>$1</del>");
  text = text.replace(/==([^=\n]+)==/g, "<mark>$1</mark>");
  text = text.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  text = text.replace(/(^|[^\w*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");

  text = text.replace(/\u0000LN(\d+)\u0000/g, (_m, i) => links[Number(i)] || "");
  text = text.replace(/\u0000IC(\d+)\u0000/g, (_m, i) => inlineCodes[Number(i)] || "");
  return text;
}

function isTableDelimiterLine(line) {
  return /^\s*\|?(?:\s*:?-+:?\s*\|)+\s*:?-+:?\s*\|?\s*$/.test(line || "") && line.includes("-");
}

function splitTableCells(line) {
  let trimmed = String(line || "").trim();
  if (trimmed.startsWith("|")) trimmed = trimmed.slice(1);
  if (trimmed.endsWith("|")) trimmed = trimmed.slice(0, -1);
  return trimmed.split("|").map((c) => c.trim());
}

function renderMarkdown(rawMd) {
  const normalized = String(rawMd || "").replace(/\r\n/g, "\n");
  if (!normalized.trim()) {
    return '<div class="empty-state" style="padding:24px 8px;">Nothing in this note yet. Switch to <strong>Split</strong> or <strong>Edit</strong> to write, or pick a template.</div>';
  }

  const codeBlocks = [];
  const withSentinels = normalized.replace(
    /```([a-zA-Z0-9_+-]*)\n([\s\S]*?)```/g,
    (_m, lang, body) => {
      const idx = codeBlocks.length;
      const safeLang = esc((lang || "code").trim() || "code");
      const safeCode = esc(body.replace(/\n$/, ""));
      codeBlocks.push(
        `<pre class="md-code"><div class="md-code-header"><span>${safeLang}</span></div><code>${safeCode}</code></pre>`
      );
      return `\n@@LIFEHUB_CB_${idx}@@\n`;
    }
  );

  const lines = withSentinels.split("\n");
  const out = [];
  let i = 0;
  let chkCounter = 0;

  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    if (!trimmed) {
      i += 1;
      continue;
    }

    const cbMatch = /^@@LIFEHUB_CB_(\d+)@@$/.exec(trimmed);
    if (cbMatch) {
      out.push(codeBlocks[Number(cbMatch[1])] || "");
      i += 1;
      continue;
    }

    if (/^(?:---+|___+|\*\*\*+)$/.test(trimmed)) {
      out.push('<hr class="md-hr" />');
      i += 1;
      continue;
    }

    const hMatch = /^(#{1,4})\s+(.*)$/.exec(trimmed);
    if (hMatch) {
      const level = hMatch[1].length;
      out.push(`<h${level}>${formatInlineMd(hMatch[2])}</h${level}>`);
      i += 1;
      continue;
    }

    // GFM table
    if (line.includes("|") && i + 1 < lines.length && isTableDelimiterLine(lines[i + 1])) {
      const headers = splitTableCells(line);
      i += 2;
      const bodyRows = [];
      while (i < lines.length && lines[i].includes("|") && lines[i].trim() !== "") {
        bodyRows.push(splitTableCells(lines[i]));
        i += 1;
      }
      const thead = `<thead><tr>${headers.map((h) => `<th>${formatInlineMd(h)}</th>`).join("")}</tr></thead>`;
      const tbody = bodyRows.length
        ? `<tbody>${bodyRows
            .map(
              (cells) =>
                `<tr>${headers.map((_h, ci) => `<td>${formatInlineMd(cells[ci] || "")}</td>`).join("")}</tr>`
            )
            .join("")}</tbody>`
        : "";
      out.push(`<div class="md-table-wrap"><table class="md-table">${thead}${tbody}</table></div>`);
      continue;
    }

    // GitHub-style Callout or Blockquote
    if (/^\s*>\s?/.test(line)) {
      const quoteLines = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        quoteLines.push(lines[i].replace(/^\s*>\s?/, ""));
        i += 1;
      }
      const calloutMatch = /^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*(.*)$/i.exec(
        (quoteLines[0] || "").trim()
      );
      if (calloutMatch) {
        const kind = calloutMatch[1].toLowerCase();
        const icons = {
          note: "📝 Note",
          tip: "💡 Tip",
          important: "⭐ Key Takeaway",
          warning: "⚠️ Warning",
          caution: "🛑 Caution",
        };
        const rest = [];
        if (calloutMatch[2]) rest.push(calloutMatch[2]);
        for (let q = 1; q < quoteLines.length; q += 1) {
          rest.push(quoteLines[q]);
        }
        const bodyHtml = rest.length
          ? `<div class="md-callout-body">${rest.map((r) => formatInlineMd(r)).join("<br />")}</div>`
          : "";
        out.push(
          `<div class="md-callout callout-${kind}"><div class="md-callout-title">${icons[kind] || kind.toUpperCase()}</div>${bodyHtml}</div>`
        );
      } else {
        out.push(`<blockquote class="md-quote">${quoteLines.map((q) => formatInlineMd(q)).join("<br />")}</blockquote>`);
      }
      continue;
    }

    // Checklist lines `- [ ]` or `- [x]`
    if (/^\s*[-*]\s*\[([ xX])\]\s+(.*)$/.test(line)) {
      const taskItems = [];
      while (i < lines.length) {
        const m = /^\s*[-*]\s*\[([ xX])\]\s+(.*)$/.exec(lines[i]);
        if (!m) break;
        const done = m[1].toLowerCase() === "x";
        const idx = chkCounter;
        chkCounter += 1;
        taskItems.push(
          `<label class="md-task-item ${done ? "done" : ""}">` +
            `<input type="checkbox" class="checkbox" data-md-chk-idx="${idx}" ${done ? "checked" : ""} />` +
            `<span>${formatInlineMd(m[2].trim())}</span>` +
          `</label>`
        );
        i += 1;
      }
      out.push(`<div class="md-task-list">${taskItems.join("")}</div>`);
      continue;
    }

    // Unordered list `- ` / `* ` (excluding checklist lines)
    if (/^\s*[-*]\s+(.*)$/.test(line)) {
      const ulItems = [];
      while (i < lines.length) {
        if (/^\s*[-*]\s*\[([ xX])\]\s+/.test(lines[i])) break;
        const m = /^\s*[-*]\s+(.*)$/.exec(lines[i]);
        if (!m) break;
        ulItems.push(`<li>${formatInlineMd(m[1])}</li>`);
        i += 1;
      }
      out.push(`<ul>${ulItems.join("")}</ul>`);
      continue;
    }

    // Ordered list `1. `
    if (/^\s*\d+\.\s+(.*)$/.test(line)) {
      const olItems = [];
      while (i < lines.length) {
        const m = /^\s*\d+\.\s+(.*)$/.exec(lines[i]);
        if (!m) break;
        olItems.push(`<li>${formatInlineMd(m[1])}</li>`);
        i += 1;
      }
      out.push(`<ol>${olItems.join("")}</ol>`);
      continue;
    }

    // Plain paragraph until blank line or block boundary
    const paraLines = [];
    while (i < lines.length) {
      const cur = lines[i];
      const curT = cur.trim();
      if (
        !curT ||
        /^@@LIFEHUB_CB_\d+@@$/.test(curT) ||
        /^(?:---+|___+|\*\*\*+)$/.test(curT) ||
        /^#{1,4}\s+/.test(curT) ||
        /^\s*>\s?/.test(cur) ||
        /^\s*[-*]\s+/.test(cur) ||
        /^\s*\d+\.\s+/.test(cur) ||
        (cur.includes("|") && i + 1 < lines.length && isTableDelimiterLine(lines[i + 1]))
      ) {
        break;
      }
      paraLines.push(formatInlineMd(cur));
      i += 1;
    }
    if (paraLines.length) {
      out.push(`<p>${paraLines.join("<br />")}</p>`);
    }
  }

  return out.join("\n");
}

function toggleChecklistLineInText(rawMd, targetIndex, done) {
  const lines = String(rawMd || "").replace(/\r\n/g, "\n").split("\n");
  let counter = 0;
  for (let i = 0; i < lines.length; i += 1) {
    const m = /^(\s*[-*]\s*)\[([ xX])\](\s+)(.*)$/.exec(lines[i]);
    if (!m) continue;
    if (counter === targetIndex) {
      lines[i] = `${m[1]}[${done ? "x" : " "}]${m[3]}${m[4]}`;
      break;
    }
    counter += 1;
  }
  return lines.join("\n");
}

function applyMdToolbarAction(textarea, action) {
  if (!textarea) return;
  const start = textarea.selectionStart ?? 0;
  const end = textarea.selectionEnd ?? 0;
  const val = textarea.value;
  const selected = val.slice(start, end);

  const replaceSelection = (replacement, selStartOffset = 0, selEndOffset = replacement.length) => {
    textarea.value = val.slice(0, start) + replacement + val.slice(end);
    textarea.focus();
    textarea.setSelectionRange(start + selStartOffset, start + selEndOffset);
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  };

  if (action === "bold") {
    const inner = selected || "bold text";
    replaceSelection(`**${inner}**`, 2, 2 + inner.length);
  } else if (action === "italic") {
    const inner = selected || "italic text";
    replaceSelection(`*${inner}*`, 1, 1 + inner.length);
  } else if (action === "highlight") {
    const inner = selected || "key highlight";
    replaceSelection(`==${inner}==`, 2, 2 + inner.length);
  } else if (action === "inline_code") {
    const inner = selected || "code";
    replaceSelection(`\`${inner}\``, 1, 1 + inner.length);
  } else if (action === "h1" || action === "h2" || action === "h3") {
    const prefix = action === "h1" ? "# " : action === "h2" ? "## " : "### ";
    const lineStart = val.lastIndexOf("\n", Math.max(0, start - 1)) + 1;
    const before = val.slice(0, lineStart);
    const chunk = val.slice(lineStart, end || lineStart);
    const lineText = chunk || (val.slice(lineStart).split("\n")[0] || "");
    const cleanLine = lineText.replace(/^#{1,4}\s+/, "");
    const endPos = chunk ? end : lineStart + lineText.length;
    const inserted = `${prefix}${cleanLine || "Section Heading"}`;
    textarea.value = before + inserted + val.slice(endPos);
    textarea.focus();
    textarea.setSelectionRange(before.length + prefix.length, before.length + inserted.length);
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  } else if (action === "ul" || action === "ol" || action === "chk") {
    const block = selected || (action === "chk" ? "Research item" : "List item");
    const lines = block.split("\n").map((ln, idx) => {
      if (action === "chk") return `- [ ] ${ln.replace(/^[-*]\s*(\[[ xX]\]\s*)?/, "")}`;
      if (action === "ol") return `${idx + 1}. ${ln.replace(/^\d+\.\s*/, "")}`;
      return `- ${ln.replace(/^[-*]\s*/, "")}`;
    });
    const outText = (start > 0 && val[start - 1] !== "\n" ? "\n" : "") + lines.join("\n");
    replaceSelection(outText, outText.length, outText.length);
  } else if (action === "callout") {
    const body = selected ? selected.split("\n").map((l) => `> ${l}`).join("\n") : "> Key takeaway or reminder";
    const outText = `${start > 0 && val[start - 1] !== "\n" ? "\n\n" : ""}> [!NOTE]\n${body}\n`;
    replaceSelection(outText, outText.length, outText.length);
  } else if (action === "code_block") {
    const inner = selected || "# code or snippet";
    const outText = `${start > 0 && val[start - 1] !== "\n" ? "\n\n" : ""}\`\`\`python\n${inner}\n\`\`\`\n`;
    replaceSelection(outText, outText.length, outText.length);
  } else if (action === "table") {
    const outText =
      `${start > 0 && val[start - 1] !== "\n" ? "\n\n" : ""}` +
      "| Topic | Key Detail |\n| --- | --- |\n| Item 1 | Notes |\n| Item 2 | Notes |\n";
    replaceSelection(outText, outText.length, outText.length);
  } else if (action === "hr") {
    const outText = `${start > 0 && val[start - 1] !== "\n" ? "\n\n" : ""}---\n\n`;
    replaceSelection(outText, outText.length, outText.length);
  }
}

function buildMdToolbarHtml(isNew = false) {
  return `
    <div class="md-toolbar">
      <div class="md-toolbar-group">
        <button type="button" class="md-tool-btn" data-md-tool="h1" title="Heading 1">H1</button>
        <button type="button" class="md-tool-btn" data-md-tool="h2" title="Heading 2">H2</button>
        <button type="button" class="md-tool-btn" data-md-tool="h3" title="Heading 3">H3</button>
      </div>
      <div class="md-toolbar-group">
        <button type="button" class="md-tool-btn" data-md-tool="bold" title="Bold"><strong>B</strong></button>
        <button type="button" class="md-tool-btn" data-md-tool="italic" title="Italic"><em>I</em></button>
        <button type="button" class="md-tool-btn" data-md-tool="highlight" title="Highlight ==text==">🖍️</button>
        <button type="button" class="md-tool-btn" data-md-tool="inline_code" title="Inline Code">&lt;/&gt;</button>
      </div>
      <div class="md-toolbar-group">
        <button type="button" class="md-tool-btn" data-md-tool="ul" title="Bullet List">• List</button>
        <button type="button" class="md-tool-btn" data-md-tool="ol" title="Numbered List">1. List</button>
        <button type="button" class="md-tool-btn" data-md-tool="chk" title="Checklist Item">☑ Task</button>
      </div>
      <div class="md-toolbar-group">
        <button type="button" class="md-tool-btn" data-md-tool="callout" title="Insert Note Callout">💡 Callout</button>
        <button type="button" class="md-tool-btn" data-md-tool="code_block" title="Code Block">{ } Block</button>
        <button type="button" class="md-tool-btn" data-md-tool="table" title="Insert Table">⊞ Table</button>
        <button type="button" class="md-tool-btn" data-md-tool="hr" title="Section Divider">—</button>
      </div>
      <div class="md-toolbar-group" style="margin-left:auto;">
        <span style="font-size:11px;color:var(--muted);align-self:center;padding:0 4px;">Templates:</span>
        <button type="button" class="md-tool-btn" data-note-tpl="lecture" title="Lecture Note Template">${isNew ? "🎓 Lecture" : "+ Lecture"}</button>
        <button type="button" class="md-tool-btn" data-note-tpl="project" title="Project Note Template">${isNew ? "🚀 Project" : "+ Project"}</button>
        <button type="button" class="md-tool-btn" data-note-tpl="study" title="Study Note Template">${isNew ? "📖 Study" : "+ Study"}</button>
      </div>
    </div>
  `;
}

async function renderNotes() {
  const prevSearchEl = document.getElementById("notes-search-input");
  const restoreSearchFocus = document.activeElement === prevSearchEl;
  const searchSelStart = restoreSearchFocus ? prevSearchEl.selectionStart : null;
  const searchSelEnd = restoreSearchFocus ? prevSearchEl.selectionEnd : null;

  const params = new URLSearchParams();
  if (state.notesQuery) params.set("q", state.notesQuery);
  if (state.notesTag) params.set("tag", state.notesTag);
  if (state.notesFolder === "__pinned__") {
    params.set("pinned_only", "true");
  } else if (state.notesFolder && state.notesFolder !== "all") {
    params.set("folder", state.notesFolder);
  }
  params.set("sort", state.notesSort || "updated");

  const data = await api(`/api/notes?${params.toString()}`);
  const items = data.items || [];
  state.notes = items;
  state.noteFolders = data.folders || { total: items.length, pinned: 0, unfiled: 0, folders: [] };
  state.noteTags = data.tags || [];

  if (state.activeNoteId !== -1) {
    if (!state.activeNoteId || !items.some((n) => n.id === state.activeNoteId)) {
      state.activeNoteId = items.length ? items[0].id : -1;
      state.noteDraft = null;
    }
  }
  const active = state.activeNoteId === -1 ? null : (items.find((n) => n.id === state.activeNoteId) || null);

  if (active) {
    if (!state.noteDraft || state.noteDraft.id !== active.id) {
      state.noteDraft = {
        id: active.id,
        title: active.title || "",
        folder: active.folder || "",
        summary: active.summary || "",
        tags: (active.tags || []).join(", "),
        content: active.content || "",
        dirty: false,
      };
    }
  } else {
    if (!state.noteDraft || state.noteDraft.id !== -1) {
      const defaultFolder =
        state.notesFolder && !["all", "__pinned__", "__unfiled__"].includes(state.notesFolder)
          ? state.notesFolder
          : "";
      state.noteDraft = {
        id: -1,
        title: "",
        folder: defaultFolder,
        summary: "",
        tags: state.notesTag || "",
        content: "",
        dirty: false,
      };
    }
    if (state.noteEditorMode === "read") {
      state.noteEditorMode = "split";
    }
  }

  const draft = state.noteDraft;
  const isNew = !active;
  const hasSyncFolder = Boolean(state.settings?.notes_folder);
  const foldersInfo = state.noteFolders;
  const folderOptions = (foldersInfo.folders || []).map((f) => f.name);

  const noteChkBadge = (n) => {
    const c = n.checklist;
    if (!c || !c.total) return "";
    if (c.status === "completed") return `<span class="badge badge-gym">✓ ${c.done}/${c.total}</span>`;
    if (c.status === "partially_completed") return `<span class="badge badge-art">◐ ${c.done}/${c.total}</span>`;
    return `<span class="badge">☐ ${c.done}/${c.total}</span>`;
  };

  const folderPillHtml = (key, label, count) => `
    <button type="button" class="folder-pill ${state.notesFolder === key ? "active" : ""}" data-folder-filter="${esc(key)}">
      <span>${label}</span>
      <span class="folder-count">${count}</span>
    </button>
  `;

  const editorMode = isNew && state.noteEditorMode === "read" ? "split" : state.noteEditorMode;
  const wordCount = (draft.content || "").trim() ? (draft.content || "").trim().split(/\s+/).length : 0;

  const el = document.getElementById("view-notes");
  el.innerHTML = `
    <div class="view-header">
      <div>
        <h1 class="view-title">📝 Structured Notes</h1>
        <p class="view-sub">Notebooks, rich Markdown (Read / Split / Edit), interactive checklists &amp; ✨ AI note organizer</p>
      </div>
      <div style="display:flex; gap:8px; flex-wrap:wrap;">
        <button id="notes-sync-btn" class="btn btn-ghost btn-sm" ${hasSyncFolder ? "" : "title='Configure a folder in Settings first'"}>
          🔄 Sync Folder
        </button>
        <button id="notes-new-btn" class="btn btn-primary btn-sm">+ New Note</button>
      </div>
    </div>

    <div class="notes-layout">
      <!-- Left Sidebar: Notebooks, Search/Sort, Tag Filters & Note Cards -->
      <div class="card notes-sidebar-card">
        <div class="folder-pills">
          ${folderPillHtml("all", "🗂️ All", foldersInfo.total || 0)}
          ${folderPillHtml("__pinned__", "📌 Pinned", foldersInfo.pinned || 0)}
          ${(foldersInfo.folders || [])
            .map((f) =>
              folderPillHtml(
                f.name,
                `${FOLDER_ICONS[f.name] || "📁"} ${esc(f.name)}`,
                f.count
              )
            )
            .join("")}
          ${foldersInfo.unfiled > 0 ? folderPillHtml("__unfiled__", "📥 Unfiled", foldersInfo.unfiled) : ""}
        </div>

        <div class="field-row" style="gap:6px;">
          <input id="notes-search-input" class="input" type="search" placeholder="Search title, body, #tags…" value="${esc(state.notesQuery)}" />
          <select id="notes-sort-select" style="flex:0 0 auto;padding:7px 8px;font-size:12px;" title="Sort notes">
            <option value="updated" ${state.notesSort === "updated" ? "selected" : ""}>Updated</option>
            <option value="created" ${state.notesSort === "created" ? "selected" : ""}>Created</option>
            <option value="title" ${state.notesSort === "title" ? "selected" : ""}>Title A–Z</option>
          </select>
        </div>

        ${
          state.noteTags.length
            ? `<div class="tag-filter-row">
                ${state.noteTags
                  .slice(0, 14)
                  .map(
                    (t) =>
                      `<button type="button" class="tag-chip ${state.notesTag.toLowerCase() === t.name.toLowerCase() ? "active" : ""}" data-tag-filter="${esc(t.name)}">#${esc(t.name)} <span>(${t.count})</span></button>`
                  )
                  .join("")}
              </div>`
            : ""
        }

        <div class="item-list" style="overflow-y:auto;max-height:460px;padding-right:2px;">
          ${
            items.length
              ? items
                  .map(
                    (n) => `
              <div class="note-list-item ${n.id === state.activeNoteId ? "active" : ""}" data-select-note="${n.id}">
                <div class="row-title">${n.pinned ? "📌 " : ""}${esc(n.title)}</div>
                <div class="note-card-preview">${esc(n.summary || stripMarkdownPreview(n.content, 85))}</div>
                <div class="row-meta">
                  ${n.folder ? `<span class="badge badge-accent">${FOLDER_ICONS[n.folder] || "📁"} ${esc(n.folder)}</span>` : ""}
                  <span>${esc(n.updated_at.slice(0, 10))}</span>
                  ${noteChkBadge(n)}
                  ${n.source_path ? '<span class="badge">Synced</span>' : ""}
                  ${n.tags.slice(0, 3).map((t) => `<span class="badge">#${esc(t)}</span>`).join("")}
                </div>
              </div>`
                  )
                  .join("")
              : '<div class="empty-state">No notes match this filter. Click "+ New Note" to start!</div>'
          }
        </div>
      </div>

      <!-- Right Main Note Workspace -->
      <div class="card note-workspace-card">
        <datalist id="note-folders-datalist">
          ${folderOptions.map((fn) => `<option value="${esc(fn)}"></option>`).join("")}
        </datalist>

        <div class="note-editor">
          <!-- Title + Primary Actions Bar -->
          <div class="note-top-bar">
            <input
              id="note-edit-title"
              class="input"
              type="text"
              value="${esc(draft.title)}"
              placeholder="${isNew ? "New note title (or leave blank & click Organize with AI)…" : "Note title"}"
              style="flex:1;min-width:190px;font-weight:700;font-size:15.5px;"
            />
            <div class="editor-mode-tabs">
              ${
                !isNew
                  ? `<button type="button" class="mode-tab ${editorMode === "read" ? "active" : ""}" data-editor-mode="read">📖 Read</button>`
                  : ""
              }
              <button type="button" class="mode-tab ${editorMode === "split" ? "active" : ""}" data-editor-mode="split">◫ Split</button>
              <button type="button" class="mode-tab ${editorMode === "edit" ? "active" : ""}" data-editor-mode="edit">✏️ Edit</button>
            </div>
          </div>

          <!-- Folder + Tags + AI Organize + Save Bar -->
          <div class="note-meta-bar">
            <input
              id="note-edit-folder"
              class="input"
              type="text"
              list="note-folders-datalist"
              value="${esc(draft.folder)}"
              placeholder="📁 Folder (e.g. Lectures)"
              style="width:165px;font-size:12.5px;"
            />
            <input
              id="note-edit-tags"
              class="input"
              type="text"
              value="${esc(draft.tags)}"
              placeholder="Tags (comma-separated, e.g. ai, lecture)"
              style="flex:1;min-width:160px;font-size:12.5px;"
            />
            <div class="note-ai-group">
              <select id="note-organize-style" title="Organization style" style="padding:5px 8px;font-size:12px;">
                <option value="structured" ${state.noteOrganizeStyle === "structured" ? "selected" : ""}>Structured</option>
                <option value="lecture" ${state.noteOrganizeStyle === "lecture" ? "selected" : ""}>Lecture Note</option>
                <option value="concise" ${state.noteOrganizeStyle === "concise" ? "selected" : ""}>Concise TL;DR</option>
              </select>
              <button id="note-organize-btn" type="button" class="btn btn-sm btn-art" title="Clean up messy text into structured headings, bullets, summary & takeaways">
                ✨ Organize with AI
              </button>
            </div>
            ${
              !isNew
                ? `
                  <button id="note-pin-btn" type="button" class="btn btn-sm ${active.pinned ? "btn-primary" : "btn-ghost"}" title="Pin / Unpin note">📌</button>
                  <button id="note-save-btn" type="button" class="btn btn-sm btn-primary" title="Save (Ctrl+S)">
                    ${draft.dirty ? "● Save" : "Save"}
                  </button>
                  <button id="note-del-btn" type="button" class="btn btn-sm btn-danger">Delete</button>
                `
                : `<button id="note-create-btn" type="button" class="btn btn-sm btn-primary" title="Create Note (Ctrl+S)">Save Note</button>`
            }
          </div>

          <!-- Summary Banner (Read) or Summary Field (Edit/Split) -->
          ${
            editorMode === "read"
              ? draft.summary
                ? `<div class="note-summary-banner"><strong>✨ Summary:</strong> ${esc(draft.summary)}</div>`
                : ""
              : `<input id="note-edit-summary" class="input" type="text" value="${esc(draft.summary)}" placeholder="TL;DR summary (optional — auto-filled by ✨ Organize with AI)" style="font-size:12.5px;color:var(--muted);" />`
          }

          <!-- Formatting Toolbar (Edit & Split) -->
          ${editorMode !== "read" ? buildMdToolbarHtml(isNew) : ""}

          <!-- Body Workspace -->
          ${
            editorMode === "read"
              ? `
                <div id="note-md-preview" class="markdown-body">${renderMarkdown(draft.content)}</div>
                <form id="note-add-chk-form" class="sub-add-form" style="margin-top:4px;">
                  <input id="note-add-chk-input" class="input" type="text" placeholder="+ Add checklist / research-later item directly to this note…" />
                  <button type="submit" class="btn btn-sm btn-ghost">Add Item</button>
                </form>
              `
              : editorMode === "split"
              ? `
                <div class="note-split-pane">
                  <textarea id="note-edit-content" placeholder="Write Markdown here… Use headings (##), bullets (- ), checklists (- [ ]), callouts (> [!NOTE]), or click a template above.">${esc(draft.content)}</textarea>
                  <div id="note-md-preview" class="markdown-body note-split-preview">${renderMarkdown(draft.content)}</div>
                </div>
              `
              : `
                <textarea id="note-edit-content" style="min-height:390px;" placeholder="Write Markdown here…">${esc(draft.content)}</textarea>
              `
          }

          ${
            isNew && hasSyncFolder
              ? `<label style="display:flex;align-items:center;gap:6px;font-size:12.5px;color:var(--muted)">
                  <input id="note-save-folder" type="checkbox" checked /> Also save .md file inside sync folder
                </label>`
              : ""
          }

          <div class="row-meta" style="justify-content:space-between;">
            <span>
              ${wordCount} words
              ${active && active.checklist && active.checklist.total ? ` · Checklist ${active.checklist.done}/${active.checklist.total}` : ""}
              ${editorMode !== "read" ? " · Ctrl+S to save" : ""}
            </span>
            <span>
              ${active ? `Updated ${esc(active.updated_at)}` : "New Note Draft"}
              ${active && active.source_path ? ` · File: ${esc(active.source_path)}` : ""}
            </span>
          </div>
        </div>
      </div>
    </div>
  `;

  // Restore search cursor focus if user was actively typing in search bar
  const searchInput = el.querySelector("#notes-search-input");
  if (restoreSearchFocus && searchInput) {
    searchInput.focus();
    if (searchSelStart !== null && searchSelEnd !== null) {
      searchInput.setSelectionRange(searchSelStart, searchSelEnd);
    }
  }

  let searchTimer = null;
  searchInput.addEventListener("input", () => {
    state.notesQuery = searchInput.value;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => renderNotes(), 180);
  });

  el.querySelector("#notes-sort-select").addEventListener("change", async (e) => {
    state.notesSort = e.target.value;
    await renderNotes();
  });

  el.querySelectorAll("[data-folder-filter]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      state.notesFolder = btn.dataset.folderFilter;
      state.noteDraft = null;
      if (state.activeNoteId === -1) state.activeNoteId = null;
      await renderNotes();
    });
  });

  el.querySelectorAll("[data-tag-filter]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const clicked = btn.dataset.tagFilter;
      state.notesTag = state.notesTag.toLowerCase() === clicked.toLowerCase() ? "" : clicked;
      state.noteDraft = null;
      if (state.activeNoteId === -1) state.activeNoteId = null;
      await renderNotes();
    });
  });

  el.querySelector("#notes-sync-btn").addEventListener("click", async () => {
    if (!state.settings?.notes_folder) {
      toast("Pick a local notes folder in Settings first.", "info");
      switchView("settings");
      return;
    }
    const res = await api("/api/notes/sync", { method: "POST" });
    toast(`Synced folder: +${res.imported} imported, ${res.updated} updated`, "success");
    await renderNotes();
  });

  el.querySelector("#notes-new-btn").addEventListener("click", () => {
    state.activeNoteId = -1;
    state.noteDraft = null;
    state.noteEditorMode = "split";
    renderNotes();
  });

  el.querySelectorAll("[data-select-note]").forEach((item) => {
    item.addEventListener("click", () => {
      state.activeNoteId = Number(item.dataset.selectNote);
      state.noteDraft = null;
      renderNotes();
    });
  });

  const titleEl = el.querySelector("#note-edit-title");
  const folderEl = el.querySelector("#note-edit-folder");
  const tagsEl = el.querySelector("#note-edit-tags");
  const summaryEl = el.querySelector("#note-edit-summary");
  const contentEl = el.querySelector("#note-edit-content");
  const previewEl = el.querySelector("#note-md-preview");
  const saveBtnEl = el.querySelector("#note-save-btn");

  const markDirty = () => {
    draft.dirty = true;
    if (saveBtnEl) saveBtnEl.textContent = "● Save";
  };

  if (titleEl) {
    titleEl.addEventListener("input", () => {
      draft.title = titleEl.value;
      markDirty();
    });
  }
  if (folderEl) {
    folderEl.addEventListener("input", () => {
      draft.folder = folderEl.value;
      markDirty();
    });
  }
  if (tagsEl) {
    tagsEl.addEventListener("input", () => {
      draft.tags = tagsEl.value;
      markDirty();
    });
  }
  if (summaryEl) {
    summaryEl.addEventListener("input", () => {
      draft.summary = summaryEl.value;
      markDirty();
    });
  }

  const bindPreviewCheckboxes = () => {
    if (!previewEl) return;
    previewEl.querySelectorAll("[data-md-chk-idx]").forEach((cb) => {
      cb.addEventListener("change", async () => {
        const idx = Number(cb.dataset.mdChkIdx);
        if (!isNew && !draft.dirty) {
          const res = await api(`/api/notes/${active.id}/checklist-toggle`, {
            method: "POST",
            body: { item_index: idx, done: cb.checked },
          });
          state.noteDraft = null;
          state.activeNoteId = res.item.id;
          await renderNotes();
        } else {
          draft.content = toggleChecklistLineInText(draft.content, idx, cb.checked);
          draft.dirty = true;
          if (contentEl) contentEl.value = draft.content;
          if (saveBtnEl) saveBtnEl.textContent = "● Save";
          previewEl.innerHTML = renderMarkdown(draft.content);
          bindPreviewCheckboxes();
        }
      });
    });
  };
  bindPreviewCheckboxes();

  if (contentEl) {
    contentEl.addEventListener("input", () => {
      draft.content = contentEl.value;
      markDirty();
      if (previewEl) {
        previewEl.innerHTML = renderMarkdown(draft.content);
        bindPreviewCheckboxes();
      }
    });
  }

  // Formatting toolbar buttons
  el.querySelectorAll("[data-md-tool]").forEach((btn) => {
    btn.addEventListener("click", () => applyMdToolbarAction(contentEl, btn.dataset.mdTool));
  });

  // Template insertion buttons
  el.querySelectorAll("[data-note-tpl]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const tpl = NOTE_TEMPLATES[btn.dataset.noteTpl];
      if (!tpl || !contentEl) return;
      if (!draft.folder.trim() && folderEl) {
        draft.folder = tpl.folder;
        folderEl.value = tpl.folder;
      }
      if (!draft.tags.trim() && tagsEl) {
        draft.tags = tpl.tags;
        tagsEl.value = tpl.tags;
      }
      if (!contentEl.value.trim()) {
        contentEl.value = tpl.content;
      } else {
        contentEl.value = `${contentEl.value.trimEnd()}\n\n${tpl.content}`;
      }
      contentEl.dispatchEvent(new Event("input", { bubbles: true }));
      toast(`Applied ${tpl.folder} template`, "info");
    });
  });

  // Read / Split / Edit switcher (preserves unsaved draft)
  el.querySelectorAll("[data-editor-mode]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.noteEditorMode = btn.dataset.editorMode;
      renderNotes();
    });
  });

  const styleSelect = el.querySelector("#note-organize-style");
  if (styleSelect) {
    styleSelect.addEventListener("change", () => {
      state.noteOrganizeStyle = styleSelect.value;
    });
  }

  // AI Organize button (both existing notes & new note drafts)
  const orgBtn = el.querySelector("#note-organize-btn");
  if (orgBtn) {
    orgBtn.addEventListener("click", async () => {
      const rawBody = (draft.content || "").trim();
      const rawTitle = (draft.title || "").trim();
      if (!rawBody && !rawTitle) {
        toast("Write or dictate some note content first.", "info");
        return;
      }
      orgBtn.disabled = true;
      orgBtn.textContent = "✨ Organizing…";
      try {
        if (!isNew) {
          const res = await api(`/api/notes/${active.id}/organize`, {
            method: "POST",
            body: {
              style: state.noteOrganizeStyle,
              apply: true,
              draft_content: draft.content,
              draft_title: draft.title,
              draft_folder: draft.folder,
              draft_tags: draft.tags,
            },
          });
          state.noteDraft = null;
          state.noteEditorMode = "read";
          toast(
            res.ai_used
              ? `✨ Organized note in ${res.item.folder || "Notes"}`
              : "Structured locally (add Groq key for AI rewriting)",
            res.ai_used ? "success" : "info"
          );
          await renderNotes();
        } else {
          const org = await api("/api/notes/organize-preview", {
            method: "POST",
            body: {
              content: draft.content,
              title: draft.title,
              folder: draft.folder,
              tags: draft.tags,
              style: state.noteOrganizeStyle,
            },
          });
          draft.title = org.title || draft.title;
          draft.folder = org.folder || draft.folder;
          draft.summary = org.summary || "";
          draft.tags = (org.tags || []).join(", ");
          draft.content = org.content || draft.content;
          draft.dirty = true;
          toast(
            org.ai_used
              ? "✨ Draft organized! Review and click Save Note."
              : "Structured locally! Review and click Save Note.",
            org.ai_used ? "success" : "info"
          );
          await renderNotes();
        }
      } catch (err) {
        toast(err.message || "Could not organize note", "error");
      } finally {
        if (document.body.contains(orgBtn)) {
          orgBtn.disabled = false;
          orgBtn.textContent = "✨ Organize with AI";
        }
      }
    });
  }

  if (!isNew) {
    const chkForm = el.querySelector("#note-add-chk-form");
    if (chkForm) {
      chkForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const text = el.querySelector("#note-add-chk-input").value.trim();
        if (!text) return;
        await api(`/api/notes/${active.id}/append`, {
          method: "POST",
          body: { checklist_items: [text] },
        });
        state.noteDraft = null;
        await renderNotes();
      });
    }

    el.querySelector("#note-save-btn").addEventListener("click", async () => {
      await api(`/api/notes/${active.id}`, {
        method: "PATCH",
        body: {
          title: draft.title,
          folder: draft.folder,
          summary: draft.summary,
          tags: draft.tags,
          content: draft.content,
        },
      });
      state.noteDraft = null;
      toast("Note saved", "success");
      await renderNotes();
    });

    el.querySelector("#note-pin-btn").addEventListener("click", async () => {
      await api(`/api/notes/${active.id}`, { method: "PATCH", body: { pinned: !active.pinned } });
      state.noteDraft = null;
      await renderNotes();
    });

    el.querySelector("#note-del-btn").addEventListener("click", async () => {
      await api(`/api/notes/${active.id}`, { method: "DELETE" });
      state.activeNoteId = null;
      state.noteDraft = null;
      toast("Note deleted", "info");
      await renderNotes();
    });
  } else {
    el.querySelector("#note-create-btn").addEventListener("click", async () => {
      const saveToFolder = Boolean(el.querySelector("#note-save-folder")?.checked);
      const res = await api("/api/notes", {
        method: "POST",
        body: {
          title: draft.title.trim(),
          folder: draft.folder.trim(),
          summary: draft.summary.trim(),
          tags: draft.tags.trim(),
          content: draft.content,
          save_to_folder: saveToFolder,
        },
      });
      state.noteDraft = null;
      state.activeNoteId = res.item.id;
      state.noteEditorMode = "read";
      toast("Note created", "success");
      await renderNotes();
    });
  }
}

// ---------------------------------------------------------------------------
// View 4: Focus / Study Timer & Session History
// ---------------------------------------------------------------------------

const TIMER_RING_CIRC = 2 * Math.PI * 96;

function formatClock(totalSeconds) {
  const s = Math.max(0, Math.floor(Number(totalSeconds) || 0));
  const hrs = Math.floor(s / 3600);
  const mins = Math.floor((s % 3600) / 60);
  const secs = s % 60;
  const mm = String(mins).padStart(2, "0");
  const ss = String(secs).padStart(2, "0");
  return hrs > 0 ? `${hrs}:${mm}:${ss}` : `${mm}:${ss}`;
}

function formatDurationHuman(totalSeconds) {
  const s = Math.max(0, Math.round(Number(totalSeconds) || 0));
  const totalMins = Math.round(s / 60);
  if (s > 0 && totalMins === 0) return `${s}s`;
  const hrs = Math.floor(totalMins / 60);
  const mins = totalMins % 60;
  if (hrs > 0 && mins > 0) return `${hrs}h ${mins}m`;
  if (hrs > 0) return `${hrs}h`;
  return `${totalMins}m`;
}

function playTimerChime() {
  try {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) return;
    const ctx = new AudioCtx();
    const notes = [523.25, 659.25, 783.99];
    notes.forEach((freq, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      const start = ctx.currentTime + i * 0.14;
      gain.gain.setValueAtTime(0.0001, start);
      gain.gain.exponentialRampToValueAtTime(0.14, start + 0.03);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.45);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(start);
      osc.stop(start + 0.48);
    });
  } catch {
    // ignore WebAudio restrictions
  }
}

function setTimerState(nextState) {
  state.timerState = nextState;
  state.timerClientSyncedAt = Date.now();
}

function computeLiveTimerValues() {
  const t = state.timerState;
  if (!t || !t.active) {
    const isCd = state.timerDraft.mode === "countdown";
    const planned = Math.max(60, Math.round((Number(state.timerDraft.focusMins) || 25) * 60));
    return {
      active: false,
      paused: false,
      mode: state.timerDraft.mode,
      phase: "focus",
      elapsed: 0,
      remaining: isCd ? planned : 0,
      displaySeconds: isCd ? planned : 0,
      targetSeconds: planned,
      progress: 1,
    };
  }

  let baseElapsed = Number(t.elapsed_seconds ?? t.accumulated_seconds ?? 0);
  if (!t.paused && t.started_at_epoch != null && t.server_epoch != null) {
    const runningSecs = Math.max(0, Number(t.server_epoch) - Number(t.started_at_epoch));
    baseElapsed = Number(t.accumulated_seconds || 0) + runningSecs;
  }
  let elapsed = Math.floor(Math.max(0, baseElapsed));
  if (!t.paused) {
    const deltaSec = Math.max(0, (Date.now() - state.timerClientSyncedAt) / 1000);
    elapsed = Math.floor(Math.max(0, baseElapsed + deltaSec));
  }

  if (t.mode === "countdown") {
    const target = Math.max(1, Number(t.target_seconds || 1));
    const remaining = Math.max(0, target - elapsed);
    return {
      active: true,
      paused: Boolean(t.paused),
      mode: "countdown",
      phase: t.phase || "focus",
      elapsed,
      remaining,
      displaySeconds: remaining,
      targetSeconds: target,
      progress: Math.min(1, Math.max(0, remaining / target)),
    };
  }

  return {
    active: true,
    paused: Boolean(t.paused),
    mode: "stopwatch",
    phase: "focus",
    elapsed,
    remaining: null,
    displaySeconds: elapsed,
    targetSeconds: 0,
    progress: (elapsed % 60) / 60,
  };
}

function updateTimerDomInPlace() {
  const live = computeLiveTimerValues();
  const clockStr = formatClock(live.displaySeconds);

  const pill = document.getElementById("top-timer-pill");
  const pillReadout = document.getElementById("top-timer-readout");
  const pillSubj = document.getElementById("top-timer-subject");
  if (pill && pillReadout && pillSubj) {
    pill.classList.toggle("hidden", !live.active);
    pill.classList.toggle("paused", live.active && live.paused);
    pill.classList.toggle("break", live.active && live.phase === "break");
    pillReadout.textContent = clockStr;
    const subj = state.timerState?.subject || "Study";
    pillSubj.textContent = live.phase === "break" ? "Break" : subj;
  }

  const clockEl = document.getElementById("timer-clock-text");
  const phaseEl = document.getElementById("timer-phase-badge");
  const ringEl = document.getElementById("timer-ring-fg");
  if (clockEl) clockEl.textContent = clockStr;
  if (phaseEl) {
    if (!live.active) {
      phaseEl.textContent = live.mode === "countdown" ? "Ready · Focus Countdown" : "Ready · Open Stopwatch";
    } else if (live.paused) {
      phaseEl.textContent = live.phase === "break" ? "Break Paused" : "Paused";
    } else if (live.phase === "break") {
      phaseEl.textContent = "☕ Break Time";
    } else {
      phaseEl.textContent = live.mode === "stopwatch" ? "⏱️ Stopwatch Running" : "🎯 Deep Focus";
    }
  }
  if (ringEl) {
    const offset = TIMER_RING_CIRC * (1 - live.progress);
    ringEl.style.strokeDashoffset = `${offset.toFixed(2)}`;
    ringEl.classList.toggle("break", live.active && live.phase === "break");
    ringEl.classList.toggle("paused", live.active && live.paused);
  }

  if (
    live.active &&
    !live.paused &&
    live.mode === "countdown" &&
    live.remaining <= 0 &&
    !state.timerCompleting
  ) {
    state.timerCompleting = true;
    playTimerChime();
    api("/api/timer/control", {
      method: "POST",
      body: { action: "complete", start_break: true },
    })
      .then(async (res) => {
        setTimerState(res.state);
        updateTimerDomInPlace();
        if (res.logged_session) {
          const msg = res.state.active
            ? `🎉 Logged ${res.logged_session.duration_minutes}m (${res.logged_session.subject})! Break started.`
            : `🎉 Logged ${res.logged_session.duration_minutes}m (${res.logged_session.subject})!`;
          toast(msg, "success");
        } else {
          toast("☕ Break finished! Ready for your next focus block.", "info");
        }
        await renderCurrentView();
      })
      .catch(() => {
        // ignore transient errors; ticker will recover on next sync
      })
      .finally(() => {
        state.timerCompleting = false;
      });
  }
}

function ensureTimerTicker() {
  if (state.timerTickId) return;
  state.timerTickId = setInterval(updateTimerDomInPlace, 500);
}

async function syncTimerState() {
  try {
    const data = await api("/api/timer/state");
    setTimerState(data.state);
    ensureTimerTicker();
    updateTimerDomInPlace();
  } catch {
    // ignore
  }
}

async function renderTimer() {
  const [timerData, tasksRes, notesRes] = await Promise.all([
    api("/api/timer"),
    api("/api/tasks?list_type=daily&include_done=false"),
    api("/api/notes"),
  ]);
  setTimerState(timerData.state);
  state.studyData = timerData;
  ensureTimerTicker();

  const t = timerData.state;
  const stats = timerData.stats || {};
  const sessions = timerData.sessions || [];
  const openTasks = tasksRes.items || [];
  const notes = notesRes.items || [];
  const subjects = stats.subjects || ["Study", "Lectures", "Coding", "Projects", "Art", "Reading", "Research"];

  if (t.active) {
    state.timerDraft.mode = t.mode || "countdown";
    state.timerDraft.subject = t.subject || "Study";
    state.timerDraft.topic = t.topic || "";
    state.timerDraft.taskId = t.task_id ? String(t.task_id) : "";
    state.timerDraft.noteId = t.note_id ? String(t.note_id) : "";
    state.timerDraft.notes = t.notes || "";
  }

  const draft = state.timerDraft;
  const live = computeLiveTimerValues();
  const maxDaySecs = Math.max(60, ...(stats.daily_breakdown || []).map((d) => d.seconds || 0));

  const el = document.getElementById("view-timer");
  el.innerHTML = `
    <div class="view-header">
      <div>
        <h1 class="view-title">⏱️ Focus &amp; Study Timer</h1>
        <p class="view-sub">Pomodoro / custom countdown &amp; open stopwatch linked to your tasks, notes &amp; voice agent</p>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;">
        <button id="timer-voice-hint" type="button" class="btn btn-ghost btn-sm" title="Try speaking commands">
          🎙️ Voice: "Start 25m focus on Coding"
        </button>
      </div>
    </div>

    <div class="stat-strip">
      <div class="stat-card">
        <span class="stat-label">Today's Focus</span>
        <span class="stat-value" style="color:var(--accent-hover)">${esc(formatDurationHuman(stats.today_seconds || 0))} <span style="font-size:13px;color:var(--muted)">(${stats.today_sessions_count || 0} sessions)</span></span>
      </div>
      <div class="stat-card">
        <span class="stat-label">Last 7 Days</span>
        <span class="stat-value" style="color:var(--gym)">${stats.week_hours || 0}h <span style="font-size:13px;color:var(--muted)">(${esc(formatDurationHuman(stats.week_seconds || 0))})</span></span>
      </div>
      <div class="stat-card">
        <span class="stat-label">Study Streak</span>
        <span class="stat-value" style="color:var(--art)">${stats.streak_days || 0}d</span>
      </div>
      <div class="stat-card">
        <span class="stat-label">All-Time Logged</span>
        <span class="stat-value">${stats.total_hours || 0}h <span style="font-size:13px;color:var(--muted)">(${stats.total_sessions || 0} total)</span></span>
      </div>
    </div>

    <div class="timer-layout">
      <!-- Left: Interactive Clock, Presets & Session Tagging -->
      <div class="card timer-hero-card">
        <datalist id="study-subjects-datalist">
          ${subjects.map((s) => `<option value="${esc(s)}"></option>`).join("")}
        </datalist>

        ${
          !t.active
            ? `
              <div class="editor-mode-tabs" style="align-self:center;">
                <button type="button" class="mode-tab ${draft.mode === "countdown" ? "active" : ""}" data-timer-mode="countdown">⏳ Countdown / Pomodoro</button>
                <button type="button" class="mode-tab ${draft.mode === "stopwatch" ? "active" : ""}" data-timer-mode="stopwatch">⏱️ Open Stopwatch</button>
              </div>
              ${
                draft.mode === "countdown"
                  ? `
                    <div class="timer-presets">
                      <button type="button" class="mode-pill ${draft.focusMins === 25 && draft.breakMins === 5 ? "active" : ""}" data-preset-focus="25" data-preset-break="5">🍅 25 / 5</button>
                      <button type="button" class="mode-pill ${draft.focusMins === 50 && draft.breakMins === 10 ? "active" : ""}" data-preset-focus="50" data-preset-break="10">⚡ 50 / 10</button>
                      <button type="button" class="mode-pill ${draft.focusMins === 90 && draft.breakMins === 15 ? "active" : ""}" data-preset-focus="90" data-preset-break="15">🧠 90 / 15</button>
                    </div>
                    <div class="field-row" style="justify-content:center;">
                      <label style="font-size:12px;color:var(--muted);">Focus (min)
                        <input id="timer-custom-focus" class="input" type="number" min="1" max="360" value="${esc(draft.focusMins)}" style="width:76px;margin-left:4px;padding:5px 8px;" />
                      </label>
                      <label style="font-size:12px;color:var(--muted);">Break (min)
                        <input id="timer-custom-break" class="input" type="number" min="0" max="90" value="${esc(draft.breakMins)}" style="width:72px;margin-left:4px;padding:5px 8px;" />
                      </label>
                    </div>`
                  : `<div style="text-align:center;font-size:12.5px;color:var(--muted);">Open-ended study block — counts up until you click Finish &amp; Save.</div>`
              }
            `
            : ""
        }

        <div class="timer-ring-wrap">
          <svg class="timer-svg" viewBox="0 0 220 220">
            <circle class="timer-ring-bg" cx="110" cy="110" r="96"></circle>
            <circle id="timer-ring-fg" class="timer-ring-fg ${t.active && t.phase === "break" ? "break" : ""} ${t.active && t.paused ? "paused" : ""}" cx="110" cy="110" r="96" style="stroke-dashoffset:${(TIMER_RING_CIRC * (1 - live.progress)).toFixed(2)}"></circle>
          </svg>
          <div class="timer-ring-center">
            <div id="timer-clock-text" class="timer-clock-text">${esc(formatClock(live.displaySeconds))}</div>
            <div id="timer-phase-badge" class="timer-phase-badge">${t.active ? (t.paused ? "Paused" : t.phase === "break" ? "☕ Break Time" : "🎯 Deep Focus") : "Ready"}</div>
          </div>
        </div>

        <div class="timer-controls-row">
          ${
            !t.active
              ? `<button id="timer-start-btn" type="button" class="btn btn-primary" style="min-width:160px;">▶ Start ${draft.mode === "stopwatch" ? "Stopwatch" : "Focus"}</button>`
              : `
                ${
                  t.paused
                    ? `<button id="timer-resume-btn" type="button" class="btn btn-primary">▶ Resume</button>`
                    : `<button id="timer-pause-btn" type="button" class="btn btn-art">⏸ Pause</button>`
                }
                ${
                  t.phase === "focus"
                    ? `<button id="timer-stop-btn" type="button" class="btn btn-gym">⏹ Finish &amp; Save</button>`
                    : `<button id="timer-skip-break-btn" type="button" class="btn btn-primary">⏭ Skip Break</button>`
                }
                <button id="timer-reset-btn" type="button" class="btn btn-danger btn-sm">↺ Cancel</button>
              `
          }
        </div>

        <div class="timer-subject-pills">
          ${subjects
            .slice(0, 8)
            .map(
              (s) =>
                `<button type="button" class="mode-pill ${draft.subject.toLowerCase() === s.toLowerCase() ? "active" : ""}" data-pick-subj="${esc(s)}">${esc(s)}</button>`
            )
            .join("")}
        </div>

        <div class="field-group" style="margin-bottom:0;">
          <label class="field-label">Subject &amp; Topic</label>
          <div class="field-row">
            <input id="timer-subject-input" class="input" type="text" list="study-subjects-datalist" value="${esc(draft.subject)}" placeholder="Subject" style="max-width:140px;" />
            <input id="timer-topic-input" class="input" type="text" value="${esc(draft.topic)}" placeholder="What are you studying? e.g. Lecture 4 ResNets" />
          </div>
        </div>

        <div class="field-group" style="margin-bottom:0;">
          <label class="field-label">Link Active Task &amp; Note (Optional)</label>
          <div class="field-row" style="margin-bottom:6px;">
            <select id="timer-task-select">
              <option value="">No linked task</option>
              ${openTasks
                .map(
                  (tk) =>
                    `<option value="${tk.id}" ${String(tk.id) === String(draft.taskId) ? "selected" : ""}>✅ ${esc(tk.title)}</option>`
                )
                .join("")}
            </select>
          </div>
          <div class="field-row">
            <select id="timer-note-select">
              <option value="">No linked note</option>
              ${notes
                .map(
                  (nt) =>
                    `<option value="${nt.id}" ${String(nt.id) === String(draft.noteId) ? "selected" : ""}>📝 ${esc(nt.title)}${nt.folder ? ` (${esc(nt.folder)})` : ""}</option>`
                )
                .join("")}
            </select>
            ${
              draft.noteId
                ? `<button id="timer-open-linked-note" type="button" class="btn btn-ghost btn-sm" title="Open linked note">Open</button>`
                : ""
            }
          </div>
        </div>

        <div class="field-group" style="margin-bottom:0;">
          <label class="field-label">Session Reflection / Quick Notes</label>
          <textarea id="timer-notes-input" rows="2" style="min-height:60px;" placeholder="Key takeaways, stopping point, or follow-ups…">${esc(draft.notes)}</textarea>
        </div>
      </div>

      <!-- Right: 7-Day Activity, Subject Breakdown, Manual Logger & Session History -->
      <div style="display:flex;flex-direction:column;gap:16px;">
        <div class="card">
          <div class="card-header">
            <h2 class="card-title">📊 Last 7 Days &amp; Subjects</h2>
            <span class="badge badge-accent">${stats.week_hours || 0}h this week</span>
          </div>
          <div class="timer-week-bars">
            ${(stats.daily_breakdown || [])
              .map((d) => {
                const pct = d.seconds > 0 ? Math.max(8, Math.round((d.seconds / maxDaySecs) * 100)) : 3;
                const dayLbl = formatTodayLabel(d.date).split(",")[0];
                return `
                  <div class="timer-day-col" title="${esc(d.date)}: ${esc(formatDurationHuman(d.seconds))}">
                    <span class="timer-day-val">${d.seconds > 0 ? esc(formatDurationHuman(d.seconds)) : "–"}</span>
                    <div class="timer-day-bar-wrap">
                      <div class="timer-day-bar" style="height:${pct}%;opacity:${d.seconds > 0 ? 1 : 0.25};"></div>
                    </div>
                    <span class="timer-day-lbl">${esc(dayLbl)}</span>
                  </div>`;
              })
              .join("")}
          </div>
          ${
            stats.by_subject && stats.by_subject.length
              ? `<div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:12px;">
                  ${stats.by_subject
                    .map(
                      (s) =>
                        `<span class="badge badge-accent">${esc(s.subject)}: ${esc(formatDurationHuman(s.seconds))} (${s.sessions})</span>`
                    )
                    .join("")}
                </div>`
              : ""
          }
        </div>

        <div class="card">
          <div class="card-header">
            <h2 class="card-title">📖 Log Past Session &amp; History</h2>
            <span class="badge">${sessions.length} recent</span>
          </div>
          <form id="study-manual-form" class="inline-form">
            <input id="study-man-subject" class="input" type="text" list="study-subjects-datalist" value="Study" placeholder="Subject" style="width:115px;" required />
            <input id="study-man-topic" class="input grow" type="text" placeholder="Topic (e.g. Lecture 3 notes)" />
            <input id="study-man-mins" class="input" type="number" min="1" max="720" value="30" placeholder="Mins" style="width:82px;" required />
            <button type="submit" class="btn btn-primary btn-sm">+ Log</button>
          </form>

          <div class="item-list" style="max-height:360px;overflow-y:auto;">
            ${
              sessions.length
                ? sessions
                    .map((s) => {
                      const modeIcon = s.mode === "stopwatch" ? "⏱️" : s.mode === "countdown" ? "⏳" : "📝";
                      return `
                        <div class="list-row">
                          <div class="row-main">
                            <div class="row-title">
                              <span class="badge badge-accent">${esc(s.subject)}</span>
                              ${s.topic ? `<span>${esc(s.topic)}</span>` : ""}
                              <strong style="color:var(--gym)">${esc(formatDurationHuman(s.duration_seconds))}</strong>
                            </div>
                            <div class="row-meta">
                              <span>${modeIcon} ${esc(s.date)}</span>
                              ${s.task_title ? `<button type="button" class="tag-chip" data-session-task="${s.task_id}">✅ ${esc(s.task_title)}</button>` : ""}
                              ${s.note_title ? `<button type="button" class="tag-chip" data-session-note="${s.note_id}">📝 ${esc(s.note_title)}</button>` : ""}
                              ${s.notes ? `<span>${esc(s.notes)}</span>` : ""}
                            </div>
                          </div>
                          <button type="button" class="btn btn-xs btn-danger" data-del-study="${s.id}" title="Delete session">✕</button>
                        </div>`;
                    })
                    .join("")
                : '<div class="empty-state">No study sessions recorded yet. Start a timer or log past study time above!</div>'
            }
          </div>
        </div>
      </div>
    </div>
  `;

  updateTimerDomInPlace();

  el.querySelector("#timer-voice-hint")?.addEventListener("click", () => {
    toast("Tap 🎙️ Speak and say e.g. 'Start a 25 minute coding timer' or 'I studied 1 hour today'", "info");
  });

  // Idle mode & preset controls
  el.querySelectorAll("[data-timer-mode]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.timerDraft.mode = btn.dataset.timerMode;
      renderTimer();
    });
  });

  el.querySelectorAll("[data-preset-focus]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.timerDraft.focusMins = Number(btn.dataset.presetFocus);
      state.timerDraft.breakMins = Number(btn.dataset.presetBreak);
      renderTimer();
    });
  });

  const focusInput = el.querySelector("#timer-custom-focus");
  if (focusInput) {
    focusInput.addEventListener("input", () => {
      state.timerDraft.focusMins = Math.max(1, Number(focusInput.value) || 25);
      updateTimerDomInPlace();
    });
  }
  const breakInput = el.querySelector("#timer-custom-break");
  if (breakInput) {
    breakInput.addEventListener("input", () => {
      state.timerDraft.breakMins = Math.max(0, Number(breakInput.value) || 0);
    });
  }

  const syncMetaIfRunning = async () => {
    if (!t.active) return;
    try {
      const res = await api("/api/timer/control", {
        method: "POST",
        body: {
          action: "update_meta",
          subject: state.timerDraft.subject,
          topic: state.timerDraft.topic,
          task_id: state.timerDraft.taskId ? Number(state.timerDraft.taskId) : null,
          note_id: state.timerDraft.noteId ? Number(state.timerDraft.noteId) : null,
          notes: state.timerDraft.notes,
        },
      });
      setTimerState(res.state);
      updateTimerDomInPlace();
    } catch {
      // ignore
    }
  };

  el.querySelectorAll("[data-pick-subj]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      state.timerDraft.subject = btn.dataset.pickSubj;
      const inp = el.querySelector("#timer-subject-input");
      if (inp) inp.value = state.timerDraft.subject;
      el.querySelectorAll("[data-pick-subj]").forEach((b) =>
        b.classList.toggle("active", b.dataset.pickSubj === state.timerDraft.subject)
      );
      await syncMetaIfRunning();
    });
  });

  const subjEl = el.querySelector("#timer-subject-input");
  const topicEl = el.querySelector("#timer-topic-input");
  const taskEl = el.querySelector("#timer-task-select");
  const noteEl = el.querySelector("#timer-note-select");
  const notesEl = el.querySelector("#timer-notes-input");

  subjEl?.addEventListener("input", () => {
    state.timerDraft.subject = subjEl.value;
  });
  subjEl?.addEventListener("change", syncMetaIfRunning);
  topicEl?.addEventListener("input", () => {
    state.timerDraft.topic = topicEl.value;
  });
  topicEl?.addEventListener("change", syncMetaIfRunning);
  taskEl?.addEventListener("change", async () => {
    state.timerDraft.taskId = taskEl.value;
    await syncMetaIfRunning();
  });
  noteEl?.addEventListener("change", async () => {
    state.timerDraft.noteId = noteEl.value;
    await syncMetaIfRunning();
    renderTimer();
  });
  notesEl?.addEventListener("input", () => {
    state.timerDraft.notes = notesEl.value;
  });
  notesEl?.addEventListener("change", syncMetaIfRunning);

  el.querySelector("#timer-open-linked-note")?.addEventListener("click", () => {
    if (!state.timerDraft.noteId) return;
    state.notesFolder = "all";
    state.notesTag = "";
    state.notesQuery = "";
    state.noteDraft = null;
    state.activeNoteId = Number(state.timerDraft.noteId);
    state.noteEditorMode = "read";
    switchView("notes");
  });

  // Start / Pause / Resume / Finish & Save / Skip Break / Cancel controls
  el.querySelector("#timer-start-btn")?.addEventListener("click", async () => {
    const res = await api("/api/timer/control", {
      method: "POST",
      body: {
        action: "start",
        mode: state.timerDraft.mode,
        duration_minutes: state.timerDraft.focusMins,
        break_minutes: state.timerDraft.breakMins,
        subject: state.timerDraft.subject || "Study",
        topic: state.timerDraft.topic || "",
        task_id: state.timerDraft.taskId ? Number(state.timerDraft.taskId) : null,
        note_id: state.timerDraft.noteId ? Number(state.timerDraft.noteId) : null,
        notes: state.timerDraft.notes || "",
      },
    });
    setTimerState(res.state);
    toast("Focus timer started!", "success");
    await renderTimer();
  });

  el.querySelector("#timer-pause-btn")?.addEventListener("click", async () => {
    const res = await api("/api/timer/control", { method: "POST", body: { action: "pause" } });
    setTimerState(res.state);
    await renderTimer();
  });

  el.querySelector("#timer-resume-btn")?.addEventListener("click", async () => {
    const res = await api("/api/timer/control", { method: "POST", body: { action: "resume" } });
    setTimerState(res.state);
    await renderTimer();
  });

  el.querySelector("#timer-stop-btn")?.addEventListener("click", async () => {
    const res = await api("/api/timer/control", {
      method: "POST",
      body: {
        action: "stop",
        save_session: true,
        notes: state.timerDraft.notes || "",
      },
    });
    setTimerState(res.state);
    state.timerDraft.notes = "";
    if (res.logged_session) {
      toast(`Saved ${formatDurationHuman(res.logged_session.duration_seconds)} of ${res.logged_session.subject}!`, "success");
    } else {
      toast("Timer stopped (under 1s — not saved)", "info");
    }
    await renderTimer();
  });

  el.querySelector("#timer-skip-break-btn")?.addEventListener("click", async () => {
    const res = await api("/api/timer/control", { method: "POST", body: { action: "skip_break" } });
    setTimerState(res.state);
    toast("Break skipped", "info");
    await renderTimer();
  });

  el.querySelector("#timer-reset-btn")?.addEventListener("click", async () => {
    const res = await api("/api/timer/control", { method: "POST", body: { action: "reset" } });
    setTimerState(res.state);
    toast("Timer cancelled", "info");
    await renderTimer();
  });

  // Manual past session logging
  el.querySelector("#study-manual-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const subj = el.querySelector("#study-man-subject").value.trim() || "Study";
    const topic = el.querySelector("#study-man-topic").value.trim();
    const mins = Number(el.querySelector("#study-man-mins").value || 0);
    if (mins <= 0) return;
    await api("/api/study/sessions", {
      method: "POST",
      body: {
        subject: subj,
        topic,
        duration_minutes: mins,
        mode: "manual",
        task_id: state.timerDraft.taskId ? Number(state.timerDraft.taskId) : null,
        note_id: state.timerDraft.noteId ? Number(state.timerDraft.noteId) : null,
      },
    });
    toast(`Logged ${mins}m of ${subj}`, "success");
    await renderTimer();
  });

  el.querySelectorAll("[data-session-task]").forEach((btn) => {
    btn.addEventListener("click", () => switchView("tasks"));
  });

  el.querySelectorAll("[data-session-note]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.notesFolder = "all";
      state.notesTag = "";
      state.notesQuery = "";
      state.noteDraft = null;
      state.activeNoteId = Number(btn.dataset.sessionNote);
      state.noteEditorMode = "read";
      switchView("notes");
    });
  });

  el.querySelectorAll("[data-del-study]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await api(`/api/study/sessions/${btn.dataset.delStudy}`, { method: "DELETE" });
      toast("Session deleted", "info");
      await renderTimer();
    });
  });
}

// ---------------------------------------------------------------------------
// View 5: Watchlist & Playlists
// ---------------------------------------------------------------------------

const WATCHLIST_TYPE_ICONS = {
  playlist: "📺 Playlist",
  video: "🎬 Video",
  course: "🎓 Course",
  podcast: "🎧 Podcast",
  article: "📄 Article",
};

function nextWatchStatus(status) {
  if (status === "to_watch") return "watching";
  if (status === "watching") return "watched";
  return "to_watch";
}

function watchStatusBadge(status) {
  if (status === "watching") return '<span class="badge badge-accent">👀 Watching</span>';
  if (status === "watched") return '<span class="badge badge-gym">✓ Watched</span>';
  return '<span class="badge">📌 To Watch</span>';
}

async function openExternalUrl(url) {
  if (!url) return;
  try {
    await api("/api/system/open-url", { method: "POST", body: { url } });
  } catch (err) {
    toast(err.message || "Could not open URL", "error");
  }
}

async function renderWatchlist() {
  const prevSearchEl = document.getElementById("watchlist-search-input");
  const restoreFocus = document.activeElement === prevSearchEl;
  const selStart = restoreFocus ? prevSearchEl.selectionStart : null;
  const selEnd = restoreFocus ? prevSearchEl.selectionEnd : null;

  const params = new URLSearchParams();
  if (state.watchlistStatus && state.watchlistStatus !== "all") {
    params.set("status", state.watchlistStatus);
  }
  if (state.watchlistType && state.watchlistType !== "all") {
    params.set("content_type", state.watchlistType);
  }
  if (state.watchlistQuery) {
    params.set("q", state.watchlistQuery);
  }

  const { items } = await api(`/api/watchlist?${params.toString()}`);
  state.watchlist = items || [];

  const el = document.getElementById("view-watchlist");
  el.innerHTML = `
    <div class="view-header">
      <div>
        <h1 class="view-title">📺 Watchlist &amp; Playlists</h1>
        <p class="view-sub">Save YouTube playlists, courses, talks, and articles — track episodes &amp; jump straight to Notes or Focus Timer</p>
      </div>
    </div>

    <div class="card" style="margin-bottom:16px;">
      <div class="card-header">
        <h2 class="card-title">+ Save Playlist or Link</h2>
        <span class="badge">Auto-fetches titles</span>
      </div>
      <form id="watchlist-add-form" style="display:flex;flex-direction:column;gap:8px;">
        <div class="field-row" style="flex-wrap:wrap;">
          <input id="wl-url-input" class="input" type="url" placeholder="Paste URL (e.g. https://www.youtube.com/playlist?list=...)" style="flex:2;min-width:240px;" />
          <button id="wl-fetch-btn" type="button" class="btn btn-ghost btn-sm">✨ Auto-Fill</button>
          <input id="wl-title-input" class="input" type="text" placeholder="Title (auto-filled from link if left blank)" style="flex:2;min-width:220px;" />
        </div>
        <div class="field-row" style="flex-wrap:wrap;">
          <select id="wl-type-select" style="width:140px;">
            <option value="playlist">📺 Playlist</option>
            <option value="course">🎓 Course</option>
            <option value="video">🎬 Video</option>
            <option value="podcast">🎧 Podcast</option>
            <option value="article">📄 Article</option>
          </select>
          <input id="wl-category-input" class="input" type="text" placeholder="Category (AI, Coding, Art…)" style="width:160px;" />
          <input id="wl-total-input" class="input" type="number" min="0" placeholder="Episodes (opt)" style="width:130px;" />
          <input id="wl-tags-input" class="input" type="text" placeholder="Tags (comma-separated)" style="flex:1;min-width:150px;" />
          <button type="submit" class="btn btn-primary">+ Add to Watchlist</button>
        </div>
      </form>
    </div>

    <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:12px;">
      <div class="tab-bar" style="margin-bottom:0;">
        ${[
          ["all", "All"],
          ["watching", "👀 Watching"],
          ["to_watch", "📌 To Watch"],
          ["watched", "✓ Watched"],
        ]
          .map(
            ([val, lbl]) =>
              `<button type="button" class="tab-btn ${state.watchlistStatus === val ? "active" : ""}" data-wl-status="${val}">${lbl}</button>`
          )
          .join("")}
      </div>

      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
        <select id="wl-filter-type" style="padding:5px 9px;font-size:12.5px;">
          <option value="all" ${state.watchlistType === "all" ? "selected" : ""}>All Types</option>
          <option value="playlist" ${state.watchlistType === "playlist" ? "selected" : ""}>Playlists</option>
          <option value="course" ${state.watchlistType === "course" ? "selected" : ""}>Courses</option>
          <option value="video" ${state.watchlistType === "video" ? "selected" : ""}>Videos</option>
          <option value="podcast" ${state.watchlistType === "podcast" ? "selected" : ""}>Podcasts</option>
          <option value="article" ${state.watchlistType === "article" ? "selected" : ""}>Articles</option>
        </select>
        <input id="watchlist-search-input" class="input" type="search" placeholder="Search saved links…" value="${esc(state.watchlistQuery)}" style="width:210px;padding:6px 10px;font-size:12.5px;" />
      </div>
    </div>

    <div class="watchlist-grid">
      ${
        items.length
          ? items
              .map((w) => {
                const hasTotal = w.progress_total > 0;
                const pct = hasTotal ? Math.min(100, Math.round((w.progress_current / w.progress_total) * 100)) : 0;
                return `
                  <div class="watch-card ${esc(w.status)}">
                    <div style="display:flex;align-items:center;justify-content:space-between;gap:6px;flex-wrap:wrap;">
                      <div style="display:flex;gap:5px;flex-wrap:wrap;">
                        <span class="badge">${WATCHLIST_TYPE_ICONS[w.content_type] || esc(w.content_type)}</span>
                        ${w.source ? `<span class="badge badge-accent">${esc(w.source)}</span>` : ""}
                        ${w.category ? `<span class="badge">${esc(w.category)}</span>` : ""}
                      </div>
                      ${watchStatusBadge(w.status)}
                    </div>

                    <div>
                      ${
                        w.url
                          ? `<button type="button" class="watch-link-btn" data-open-url="${esc(w.url)}" title="Open in browser">${esc(w.title)}</button>
                             <div class="watch-url-line" title="${esc(w.url)}">${esc(w.url)}</div>`
                          : `<div style="font-size:15px;font-weight:700;">${esc(w.title)}</div>`
                      }
                    </div>

                    ${
                      hasTotal
                        ? `
                          <div>
                            <div style="display:flex;align-items:center;justify-content:space-between;font-size:12px;color:var(--muted);margin-bottom:4px;">
                              <span>Progress: <strong>${w.progress_current} / ${w.progress_total}</strong> (${pct}%)</span>
                              <span style="display:inline-flex;gap:4px;">
                                <button type="button" class="btn btn-xs btn-ghost" data-wl-step="${w.id}" data-cur="${w.progress_current}" data-tot="${w.progress_total}" data-delta="-1">−1</button>
                                <button type="button" class="btn btn-xs btn-ghost" data-wl-step="${w.id}" data-cur="${w.progress_current}" data-tot="${w.progress_total}" data-delta="1">+1</button>
                              </span>
                            </div>
                            <div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>
                          </div>`
                        : ""
                    }

                    ${w.notes ? `<div style="font-size:12.5px;color:var(--muted)">${esc(w.notes)}</div>` : ""}
                    ${
                      w.tags && w.tags.length
                        ? `<div class="row-meta">${w.tags.map((t) => `<span class="badge">#${esc(t)}</span>`).join("")}</div>`
                        : ""
                    }

                    <div class="watch-actions">
                      ${
                        w.url
                          ? `<button type="button" class="btn btn-xs btn-primary" data-open-url="${esc(w.url)}">↗ Open</button>`
                          : ""
                      }
                      <button type="button" class="btn btn-xs btn-ghost" data-wl-timer="${w.id}" title="Start study timer for this">⏱️ Study</button>
                      <button type="button" class="btn btn-xs btn-ghost" data-wl-note="${w.id}" title="Open or create structured lecture note">${w.note_id ? "📝 Open Note" : "📝 + Note"}</button>
                      <button type="button" class="btn btn-xs btn-ghost" data-wl-cycle="${w.id}" data-cur-st="${esc(w.status)}">Status</button>
                      <button type="button" class="btn btn-xs btn-danger" style="margin-left:auto;" data-wl-del="${w.id}">✕</button>
                    </div>
                  </div>`;
              })
              .join("")
          : '<div class="card empty-state" style="grid-column:1 / -1;">No playlists or links saved yet. Paste any YouTube playlist, course, or article link above!</div>'
      }
    </div>
  `;

  const searchInput = el.querySelector("#watchlist-search-input");
  if (restoreFocus && searchInput) {
    searchInput.focus();
    if (selStart !== null && selEnd !== null) {
      searchInput.setSelectionRange(selStart, selEnd);
    }
  }

  let wlSearchTimer = null;
  searchInput.addEventListener("input", () => {
    state.watchlistQuery = searchInput.value;
    clearTimeout(wlSearchTimer);
    wlSearchTimer = setTimeout(() => renderWatchlist(), 180);
  });

  const urlInput = el.querySelector("#wl-url-input");
  const titleInput = el.querySelector("#wl-title-input");
  const typeSelect = el.querySelector("#wl-type-select");

  const autoFillFromUrl = async (forceToast = false) => {
    const rawUrl = urlInput.value.trim();
    if (!rawUrl || !/^https?:\/\//i.test(rawUrl)) return;
    const btn = el.querySelector("#wl-fetch-btn");
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Fetching…";
    }
    try {
      const meta = await api("/api/watchlist/fetch-meta", {
        method: "POST",
        body: { url: rawUrl },
      });
      if (meta.title && !titleInput.value.trim()) {
        titleInput.value = meta.title;
      }
      if (meta.content_type) {
        typeSelect.value = meta.content_type;
      }
      if (forceToast) {
        toast(meta.fetched ? `Found: ${meta.title}` : "Detected link type & source", "info");
      }
    } catch (err) {
      if (forceToast) toast(err.message, "error");
    } finally {
      if (btn && document.body.contains(btn)) {
        btn.disabled = false;
        btn.textContent = "✨ Auto-Fill";
      }
    }
  };

  el.querySelector("#wl-fetch-btn").addEventListener("click", () => autoFillFromUrl(true));
  urlInput.addEventListener("paste", () => setTimeout(() => autoFillFromUrl(false), 60));

  el.querySelector("#watchlist-add-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const url = urlInput.value.trim();
    const title = titleInput.value.trim();
    if (!url && !title) {
      toast("Enter a link URL or title.", "info");
      return;
    }
    await api("/api/watchlist", {
      method: "POST",
      body: {
        url,
        title,
        content_type: typeSelect.value,
        category: el.querySelector("#wl-category-input").value.trim(),
        progress_total: Number(el.querySelector("#wl-total-input").value || 0),
        tags: el.querySelector("#wl-tags-input").value.trim(),
      },
    });
    toast("Added to Watchlist!", "success");
    await renderWatchlist();
  });

  el.querySelectorAll("[data-wl-status]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.watchlistStatus = btn.dataset.wlStatus;
      renderWatchlist();
    });
  });

  el.querySelector("#wl-filter-type").addEventListener("change", (e) => {
    state.watchlistType = e.target.value;
    renderWatchlist();
  });

  el.querySelectorAll("[data-open-url]").forEach((btn) => {
    btn.addEventListener("click", () => openExternalUrl(btn.dataset.openUrl));
  });

  el.querySelectorAll("[data-wl-step]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.dataset.wlStep;
      const cur = Number(btn.dataset.cur || 0);
      const tot = Number(btn.dataset.tot || 0);
      const delta = Number(btn.dataset.delta || 0);
      const next = Math.max(0, tot > 0 ? Math.min(tot, cur + delta) : cur + delta);
      await api(`/api/watchlist/${id}`, {
        method: "PATCH",
        body: { progress_current: next },
      });
      await renderWatchlist();
    });
  });

  el.querySelectorAll("[data-wl-cycle]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const next = nextWatchStatus(btn.dataset.curSt);
      await api(`/api/watchlist/${btn.dataset.wlCycle}`, {
        method: "PATCH",
        body: { status: next },
      });
      await renderWatchlist();
    });
  });

  el.querySelectorAll("[data-wl-note]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const res = await api(`/api/watchlist/${btn.dataset.wlNote}/open-note`, { method: "POST" });
      state.notesFolder = "all";
      state.notesTag = "";
      state.notesQuery = "";
      state.noteDraft = null;
      state.activeNoteId = res.note.id;
      state.noteEditorMode = res.created ? "split" : "read";
      toast(res.created ? "Created linked study note" : "Opened linked note", "success");
      await switchView("notes");
    });
  });

  el.querySelectorAll("[data-wl-timer]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const w = state.watchlist.find((x) => x.id === Number(btn.dataset.wlTimer));
      if (!w) return;
      state.timerDraft.subject = w.category || (w.content_type === "course" || w.content_type === "playlist" ? "Lectures" : "Study");
      state.timerDraft.topic = w.title;
      state.timerDraft.noteId = w.note_id ? String(w.note_id) : "";
      await switchView("timer");
    });
  });

  el.querySelectorAll("[data-wl-del]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await api(`/api/watchlist/${btn.dataset.wlDel}`, { method: "DELETE" });
      toast("Removed from Watchlist", "info");
      await renderWatchlist();
    });
  });
}

// ---------------------------------------------------------------------------
// Reusable Monthly Activity Calendar
// ---------------------------------------------------------------------------

function buildCalendarHtml(summary, domain) {
  const [year, month] = summary.month.split("-").map(Number);
  const firstDay = new Date(year, month - 1, 1);
  const daysInMonth = new Date(year, month, 0).getDate();
  const startDow = firstDay.getDay(); // 0=Sun..6=Sat
  const monthLabel = firstDay.toLocaleDateString(undefined, { month: "long", year: "numeric" });
  const today = localTodayStr();
  const activeDates = new Set(summary.all_dates || []);
  const activeClass = domain === "gym" ? "active-gym" : "active-art";

  const cells = [];
  for (let i = 0; i < startDow; i += 1) {
    cells.push('<div class="cal-day empty"></div>');
  }
  for (let day = 1; day <= daysInMonth; day += 1) {
    const iso = `${summary.month}-${String(day).padStart(2, "0")}`;
    const isToday = iso === today ? "today" : "";
    const isActive = activeDates.has(iso) ? activeClass : "";
    cells.push(
      `<button type="button" class="cal-day ${isToday} ${isActive}" data-cal-date="${iso}" title="Click to toggle ${iso}">${day}</button>`
    );
  }

  return `
    <div class="calendar-header">
      <button type="button" class="btn btn-xs btn-ghost" data-cal-nav="-1">◀</button>
      <strong>${esc(monthLabel)}</strong>
      <button type="button" class="btn btn-xs btn-ghost" data-cal-nav="1">▶</button>
    </div>
    <div class="calendar-grid">
      <div class="cal-dow">Sun</div>
      <div class="cal-dow">Mon</div>
      <div class="cal-dow">Tue</div>
      <div class="cal-dow">Wed</div>
      <div class="cal-dow">Thu</div>
      <div class="cal-dow">Fri</div>
      <div class="cal-dow">Sat</div>
      ${cells.join("")}
    </div>
    <div class="row-meta" style="margin-top:10px;justify-content:space-between;">
      <span>This month: <strong>${summary.month_count}</strong> days</span>
      <span>Streak: <strong>${summary.streak}</strong> days</span>
      <span>All-time: <strong>${summary.total_count}</strong> days</span>
    </div>
  `;
}

// ---------------------------------------------------------------------------
// View 4: Gym Tracker (Simple Days + Exercises/Reps/Weight)
// ---------------------------------------------------------------------------

async function renderGym() {
  const [cal, exData, logsData] = await Promise.all([
    api(`/api/activity-days?domain=gym&month=${encodeURIComponent(state.gymMonth)}`),
    api("/api/gym/exercises"),
    api(
      `/api/gym/logs?limit=80${
        state.gymFilterExercise ? `&exercise=${encodeURIComponent(state.gymFilterExercise)}` : ""
      }`
    ),
  ]);

  const exercises = exData.items || [];
  const logs = logsData.items || [];
  const defaultUnit = state.settings?.weight_unit || "kg";
  const today = localTodayStr();

  const el = document.getElementById("view-gym");
  el.innerHTML = `
    <div class="view-header">
      <div>
        <h1 class="view-title">🏋️ Gym Tracker</h1>
        <p class="view-sub">Track the days you went and your exercises, sets, reps &amp; weights</p>
      </div>
      <button id="gym-today-btn" class="btn btn-gym ${cal.today_active ? "active" : ""}">
        ${cal.today_active ? "✓ Went to Gym Today" : "🏋️ Mark Gym Today"}
      </button>
    </div>

    <div class="grid-sidebar">
      <div class="card">
        <div class="card-header">
          <h2 class="card-title">📅 Gym Days</h2>
        </div>
        ${buildCalendarHtml(cal, "gym")}
      </div>

      <div style="display:flex;flex-direction:column;gap:16px;">
        <div class="card">
          <div class="card-header">
            <h2 class="card-title">Log Exercise</h2>
          </div>
          <form id="gym-log-form" class="inline-form">
            <input id="gym-ex-input" class="input grow" list="gym-exercise-list" type="text" placeholder="Exercise (e.g. Bench Press)" required />
            <datalist id="gym-exercise-list">
              ${exercises.map((e) => `<option value="${esc(e.exercise)}"></option>`).join("")}
            </datalist>
            <input id="gym-sets" class="input" type="number" min="1" value="3" title="Sets" style="width:70px" />
            <span style="color:var(--muted)">×</span>
            <input id="gym-reps" class="input" type="number" min="0" value="10" title="Reps" style="width:70px" />
            <input id="gym-weight" class="input" type="number" step="0.5" placeholder="Weight" style="width:92px" />
            <select id="gym-unit" style="width:70px">
              <option value="kg" ${defaultUnit === "kg" ? "selected" : ""}>kg</option>
              <option value="lbs" ${defaultUnit === "lbs" ? "selected" : ""}>lbs</option>
            </select>
            <input id="gym-date" class="input" type="date" value="${today}" />
            <input id="gym-notes" class="input grow" type="text" placeholder="Short note (optional)" />
            <button type="submit" class="btn btn-primary">Log Set</button>
          </form>

          ${
            exercises.length
              ? `
            <div style="margin-top:8px;">
              <div class="field-label" style="margin-bottom:6px;">Personal Bests &amp; 1-Click Repeat Last</div>
              <div style="display:flex;gap:8px;flex-wrap:wrap;">
                ${exercises
                  .slice(0, 12)
                  .map((e) => {
                    const pb =
                      e.max_weight !== null
                        ? `${e.max_weight}${e.max_weight_unit}`
                        : `${e.max_reps} reps`;
                    const lastTxt =
                      e.last_weight !== null
                        ? `${e.last_sets}×${e.last_reps} @ ${e.last_weight}${e.last_unit}`
                        : `${e.last_sets}×${e.last_reps}`;
                    return `
                    <div class="list-row" style="padding:6px 10px;gap:8px;">
                      <button type="button" class="btn btn-xs btn-ghost" data-gym-filter="${esc(e.exercise)}" title="Filter logs for ${esc(e.exercise)}">
                        <strong>${esc(e.exercise)}</strong> · PR ${esc(pb)}
                      </button>
                      <button type="button" class="btn btn-xs btn-gym" data-gym-repeat='${esc(JSON.stringify(e))}' title="Log ${esc(lastTxt)} for today">
                        ⚡ ${esc(lastTxt)}
                      </button>
                    </div>`;
                  })
                  .join("")}
              </div>
            </div>`
              : ""
          }
        </div>

        <div class="card">
          <div class="card-header">
            <h2 class="card-title">
              Recent Exercise Logs
              ${state.gymFilterExercise ? `<span class="badge badge-gym">${esc(state.gymFilterExercise)}</span>` : ""}
            </h2>
            ${
              state.gymFilterExercise
                ? '<button id="gym-clear-filter" class="btn btn-xs btn-ghost">Show All</button>'
                : ""
            }
          </div>
          <div class="item-list">
            ${
              logs.length
                ? logs
                    .map((g) => {
                      const wTxt =
                        g.weight !== null ? `${g.weight}${g.unit}` : "bodyweight";
                      return `
                    <div class="list-row">
                      <span class="badge badge-gym">${esc(g.date)}</span>
                      <div class="row-main">
                        <div class="row-title">${esc(g.exercise)} — ${g.sets}×${g.reps} @ ${esc(wTxt)}</div>
                        ${g.notes ? `<div class="row-meta">${esc(g.notes)}</div>` : ""}
                      </div>
                      <button class="btn btn-xs btn-danger" data-del-gym="${g.id}" title="Delete log">✕</button>
                    </div>`;
                    })
                    .join("")
                : '<div class="empty-state">No gym exercises logged yet. Log your first set above!</div>'
            }
          </div>
        </div>
      </div>
    </div>
  `;

  el.querySelector("#gym-today-btn").addEventListener("click", async () => {
    await api("/api/activity-days", { method: "POST", body: { domain: "gym" } });
    await renderGym();
  });

  el.querySelectorAll("[data-cal-nav]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      state.gymMonth = shiftMonth(state.gymMonth, Number(btn.dataset.calNav));
      await renderGym();
    });
  });

  el.querySelectorAll("[data-cal-date]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await api("/api/activity-days", {
        method: "POST",
        body: { domain: "gym", date: btn.dataset.calDate, mode: "toggle" },
      });
      await renderGym();
    });
  });

  el.querySelector("#gym-log-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const exercise = el.querySelector("#gym-ex-input").value.trim();
    if (!exercise) return;
    const rawWeight = el.querySelector("#gym-weight").value.trim();
    await api("/api/gym/logs", {
      method: "POST",
      body: {
        exercise,
        sets: Number(el.querySelector("#gym-sets").value || 1),
        reps: Number(el.querySelector("#gym-reps").value || 0),
        weight: rawWeight !== "" ? Number(rawWeight) : null,
        unit: el.querySelector("#gym-unit").value,
        date: el.querySelector("#gym-date").value || localTodayStr(),
        notes: el.querySelector("#gym-notes").value.trim(),
      },
    });
    toast(`Logged ${exercise}`, "success");
    await renderGym();
  });

  el.querySelectorAll("[data-gym-filter]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const name = btn.dataset.gymFilter;
      state.gymFilterExercise = state.gymFilterExercise === name ? "" : name;
      await renderGym();
    });
  });

  const clearBtn = el.querySelector("#gym-clear-filter");
  if (clearBtn) {
    clearBtn.addEventListener("click", async () => {
      state.gymFilterExercise = "";
      await renderGym();
    });
  }

  el.querySelectorAll("[data-gym-repeat]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const info = JSON.parse(btn.dataset.gymRepeat);
      await api("/api/gym/logs", {
        method: "POST",
        body: {
          exercise: info.exercise,
          sets: info.last_sets,
          reps: info.last_reps,
          weight: info.last_weight,
          unit: info.last_unit,
          date: localTodayStr(),
        },
      });
      toast(`Repeated ${info.exercise} for today`, "success");
      await renderGym();
    });
  });

  el.querySelectorAll("[data-del-gym]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await api(`/api/gym/logs/${btn.dataset.delGym}`, { method: "DELETE" });
      await renderGym();
    });
  });
}

// ---------------------------------------------------------------------------
// View 5: Art Tracker (Drew Today + Ideas + WIPs/Finished + Ref Images)
// ---------------------------------------------------------------------------

async function renderArt() {
  const [cal, artData] = await Promise.all([
    api(`/api/activity-days?domain=art&month=${encodeURIComponent(state.artMonth)}`),
    api("/api/art"),
  ]);
  const items = artData.items || [];
  const wips = items.filter((a) => a.status === "wip");
  const ideas = items.filter((a) => a.status === "idea");
  const finished = items.filter((a) => a.status === "finished");

  const renderArtCard = (a) => `
    <div class="list-row" style="flex-direction:column;align-items:stretch;gap:8px;">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;">
        <div class="row-title" style="font-size:15px;">${esc(a.title)}</div>
        <div class="row-actions">
          ${
            a.status === "idea"
              ? `<button class="btn btn-xs btn-art" data-art-promote="${a.id}" data-next="wip">→ Start WIP</button>`
              : a.status === "wip"
              ? `<button class="btn btn-xs btn-gym" data-art-promote="${a.id}" data-next="finished">✓ Finish</button>`
              : `<button class="btn btn-xs btn-ghost" data-art-promote="${a.id}" data-next="wip">Reopen WIP</button>`
          }
          <label class="btn btn-xs btn-ghost" style="cursor:pointer;">
            📎 Ref Image
            <input type="file" accept="image/*" class="hidden" data-art-upload="${a.id}" />
          </label>
          <button class="btn btn-xs btn-danger" data-del-art="${a.id}">✕</button>
        </div>
      </div>

      <div style="display:flex;gap:8px;align-items:center;">
        <input class="input grow" type="text" value="${esc(a.notes)}" placeholder="Notes / brushes / progress…" data-art-notes="${a.id}" />
        <button class="btn btn-xs btn-primary" data-art-save="${a.id}">Save Note</button>
      </div>

      ${
        a.image_files && a.image_files.length
          ? `<div class="art-thumbs">
              ${a.image_files
                .map(
                  (fname) => `
                <div class="thumb-wrap">
                  <img src="/api/media/${encodeURIComponent(fname)}" alt="Art reference" data-open-img="/api/media/${encodeURIComponent(fname)}" />
                  <button class="thumb-del" data-del-art-img="${a.id}" data-fname="${esc(fname)}" title="Remove image">✕</button>
                </div>`
                )
                .join("")}
            </div>`
          : ""
      }
    </div>`;

  const el = document.getElementById("view-art");
  el.innerHTML = `
    <div class="view-header">
      <div>
        <h1 class="view-title">🎨 Art Tracker</h1>
        <p class="view-sub">Track days you drew, drawing ideas, current WIPs &amp; reference images</p>
      </div>
      <button id="art-today-btn" class="btn btn-art ${cal.today_active ? "active" : ""}">
        ${cal.today_active ? "✓ Drew Today" : "🎨 Mark Drew Today"}
      </button>
    </div>

    <div class="grid-sidebar">
      <div class="card">
        <div class="card-header">
          <h2 class="card-title">📅 Art Days</h2>
        </div>
        ${buildCalendarHtml(cal, "art")}
      </div>

      <div style="display:flex;flex-direction:column;gap:16px;">
        <div class="card">
          <div class="card-header"><h2 class="card-title">Add Piece or Idea</h2></div>
          <form id="art-add-form" class="inline-form">
            <input id="art-title" class="input grow" type="text" placeholder="Piece title or drawing idea…" required />
            <select id="art-status">
              <option value="idea">Idea to Draw</option>
              <option value="wip">Current WIP</option>
              <option value="finished">Finished</option>
            </select>
            <input id="art-notes" class="input grow" type="text" placeholder="Notes (optional)" />
            <button type="submit" class="btn btn-primary">Add</button>
          </form>
        </div>

        <div class="card">
          <div class="card-header">
            <h2 class="card-title">🖌️ Current WIPs <span class="badge badge-art">${wips.length}</span></h2>
          </div>
          <div class="item-list">
            ${wips.length ? wips.map(renderArtCard).join("") : '<div class="empty-state">No active WIPs yet.</div>'}
          </div>
        </div>

        <div class="card">
          <div class="card-header">
            <h2 class="card-title">💡 Ideas to Draw <span class="badge">${ideas.length}</span></h2>
          </div>
          <div class="item-list">
            ${ideas.length ? ideas.map(renderArtCard).join("") : '<div class="empty-state">No drawing ideas queued.</div>'}
          </div>
        </div>

        ${
          finished.length
            ? `
          <div class="card">
            <div class="card-header">
              <h2 class="card-title">🖼️ Finished Pieces <span class="badge badge-gym">${finished.length}</span></h2>
            </div>
            <div class="item-list">${finished.map(renderArtCard).join("")}</div>
          </div>`
            : ""
        }
      </div>
    </div>
  `;

  el.querySelector("#art-today-btn").addEventListener("click", async () => {
    await api("/api/activity-days", { method: "POST", body: { domain: "art" } });
    await renderArt();
  });

  el.querySelectorAll("[data-cal-nav]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      state.artMonth = shiftMonth(state.artMonth, Number(btn.dataset.calNav));
      await renderArt();
    });
  });

  el.querySelectorAll("[data-cal-date]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await api("/api/activity-days", {
        method: "POST",
        body: { domain: "art", date: btn.dataset.calDate, mode: "toggle" },
      });
      await renderArt();
    });
  });

  el.querySelector("#art-add-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const title = el.querySelector("#art-title").value.trim();
    if (!title) return;
    await api("/api/art", {
      method: "POST",
      body: {
        title,
        status: el.querySelector("#art-status").value,
        notes: el.querySelector("#art-notes").value.trim(),
      },
    });
    toast(`Added art item: ${title}`, "success");
    await renderArt();
  });

  el.querySelectorAll("[data-art-promote]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await api(`/api/art/${btn.dataset.artPromote}`, {
        method: "PATCH",
        body: { status: btn.dataset.next },
      });
      await renderArt();
    });
  });

  el.querySelectorAll("[data-art-save]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.dataset.artSave;
      const notes = el.querySelector(`[data-art-notes="${id}"]`).value;
      await api(`/api/art/${id}`, { method: "PATCH", body: { notes } });
      toast("Art note saved", "success");
      await renderArt();
    });
  });

  el.querySelectorAll("[data-art-upload]").forEach((input) => {
    input.addEventListener("change", async () => {
      const file = input.files?.[0];
      if (!file) return;
      const form = new FormData();
      form.append("file", file);
      await api(`/api/art/${input.dataset.artUpload}/image`, {
        method: "POST",
        body: form,
      });
      toast("Reference image attached", "success");
      await renderArt();
    });
  });

  el.querySelectorAll("[data-open-img]").forEach((img) => {
    img.addEventListener("click", () => {
      document.getElementById("image-modal-img").src = img.dataset.openImg;
      document.getElementById("image-modal").classList.remove("hidden");
    });
  });

  el.querySelectorAll("[data-del-art-img]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await api(`/api/art/${btn.dataset.delArtImg}/image/${encodeURIComponent(btn.dataset.fname)}`, {
        method: "DELETE",
      });
      await renderArt();
    });
  });

  el.querySelectorAll("[data-del-art]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await api(`/api/art/${btn.dataset.delArt}`, { method: "DELETE" });
      await renderArt();
    });
  });
}

// ---------------------------------------------------------------------------
// View 6: Gaming Tracker
// ---------------------------------------------------------------------------

async function renderGaming() {
  const { items } = await api("/api/games");
  const counts = {
    all: items.length,
    playing: items.filter((g) => g.status === "playing").length,
    want_to_play: items.filter((g) => g.status === "want_to_play").length,
    finished: items.filter((g) => g.status === "finished").length,
    on_hold: items.filter((g) => g.status === "on_hold").length,
  };
  const visible =
    state.gameFilter === "all" ? items : items.filter((g) => g.status === state.gameFilter);

  const tabs = [
    { key: "all", label: "All" },
    { key: "playing", label: "Playing" },
    { key: "want_to_play", label: "Want to Play" },
    { key: "finished", label: "Finished" },
    { key: "on_hold", label: "On Hold" },
  ];

  const el = document.getElementById("view-gaming");
  el.innerHTML = `
    <div class="view-header">
      <div>
        <h1 class="view-title">🎮 Gaming Tracker</h1>
        <p class="view-sub">Keep tabs on what you're playing, your backlog, ratings, and next session goals</p>
      </div>
    </div>

    <div class="card" style="margin-bottom:16px;">
      <form id="game-add-form" class="inline-form" style="margin-bottom:0;">
        <input id="game-title" class="input grow" type="text" placeholder="Game title…" required />
        <select id="game-status">
          <option value="playing">Playing</option>
          <option value="want_to_play" selected>Want to Play</option>
          <option value="finished">Finished</option>
          <option value="on_hold">On Hold</option>
        </select>
        <input id="game-platform" class="input" type="text" placeholder="Platform (PC, PS5…)" style="width:140px" />
        <select id="game-rating" style="width:90px">
          <option value="">Rating</option>
          ${[10, 9, 8, 7, 6, 5, 4, 3, 2, 1].map((n) => `<option value="${n}">${n}/10</option>`).join("")}
        </select>
        <input id="game-notes" class="input grow" type="text" placeholder="Session notes / build / goal…" />
        <button type="submit" class="btn btn-primary">Add Game</button>
      </form>
    </div>

    <div class="tab-bar">
      ${tabs
        .map(
          (t) => `
        <button class="tab-btn ${state.gameFilter === t.key ? "active" : ""}" data-game-tab="${t.key}">
          ${t.label} (${counts[t.key]})
        </button>`
        )
        .join("")}
    </div>

    <div class="item-list">
      ${
        visible.length
          ? visible
              .map(
                (g) => `
            <div class="card" style="padding:13px 15px;">
              <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;margin-bottom:8px;">
                <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                  <strong style="font-size:15px;">${esc(g.title)}</strong>
                  ${g.platform ? `<span class="badge">${esc(g.platform)}</span>` : ""}
                </div>
                <div class="row-actions">
                  <select data-game-status="${g.id}" style="padding:4px 8px;font-size:12px;">
                    <option value="playing" ${g.status === "playing" ? "selected" : ""}>Playing</option>
                    <option value="want_to_play" ${g.status === "want_to_play" ? "selected" : ""}>Want to Play</option>
                    <option value="finished" ${g.status === "finished" ? "selected" : ""}>Finished</option>
                    <option value="on_hold" ${g.status === "on_hold" ? "selected" : ""}>On Hold</option>
                  </select>
                  <select data-game-rating="${g.id}" style="padding:4px 8px;font-size:12px;">
                    <option value="">— /10</option>
                    ${[10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
                      .map((n) => `<option value="${n}" ${g.rating === n ? "selected" : ""}>${n}/10</option>`)
                      .join("")}
                  </select>
                  <button class="btn btn-xs btn-danger" data-del-game="${g.id}">✕</button>
                </div>
              </div>
              <div style="display:flex;gap:8px;align-items:center;">
                <input class="input grow" type="text" value="${esc(g.notes)}" placeholder="Session notes, boss/quest reminder…" data-game-notes="${g.id}" />
                <button class="btn btn-xs btn-primary" data-game-save="${g.id}">Save</button>
              </div>
            </div>`
              )
              .join("")
          : '<div class="card empty-state">No games in this list yet.</div>'
      }
    </div>
  `;

  el.querySelector("#game-add-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const title = el.querySelector("#game-title").value.trim();
    if (!title) return;
    const rawRating = el.querySelector("#game-rating").value;
    await api("/api/games", {
      method: "POST",
      body: {
        title,
        status: el.querySelector("#game-status").value,
        platform: el.querySelector("#game-platform").value.trim(),
        rating: rawRating ? Number(rawRating) : null,
        notes: el.querySelector("#game-notes").value.trim(),
      },
    });
    toast(`Added ${title}`, "success");
    await renderGaming();
  });

  el.querySelectorAll("[data-game-tab]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      state.gameFilter = btn.dataset.gameTab;
      await renderGaming();
    });
  });

  el.querySelectorAll("[data-game-status]").forEach((sel) => {
    sel.addEventListener("change", async () => {
      await api(`/api/games/${sel.dataset.gameStatus}`, {
        method: "PATCH",
        body: { status: sel.value },
      });
      await renderGaming();
    });
  });

  el.querySelectorAll("[data-game-rating]").forEach((sel) => {
    sel.addEventListener("change", async () => {
      await api(`/api/games/${sel.dataset.gameRating}`, {
        method: "PATCH",
        body: { rating: sel.value ? Number(sel.value) : null },
      });
      await renderGaming();
    });
  });

  el.querySelectorAll("[data-game-save]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.dataset.gameSave;
      const notes = el.querySelector(`[data-game-notes="${id}"]`).value;
      await api(`/api/games/${id}`, { method: "PATCH", body: { notes } });
      toast("Game note saved", "success");
    });
  });

  el.querySelectorAll("[data-del-game]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await api(`/api/games/${btn.dataset.delGame}`, { method: "DELETE" });
      await renderGaming();
    });
  });
}

// ---------------------------------------------------------------------------
// View 7: Voice Agent Console (Multi-Turn Tool Calling + TTS)
// ---------------------------------------------------------------------------

async function renderAsk() {
  const sessData = await api("/api/agent/sessions");
  const sessions = sessData.sessions || [];
  state.agentSessions = sessions;
  if (!state.activeSessionId && sessions.length) {
    state.activeSessionId = sessions[0].id;
  }

  let messages = [];
  if (state.activeSessionId) {
    try {
      const detail = await api(`/api/agent/sessions/${state.activeSessionId}`);
      messages = detail.session?.messages || [];
    } catch {
      state.activeSessionId = sessions.length ? sessions[0].id : null;
    }
  }
  state.agentMessages = messages;

  const ttsMode = state.settings?.tts_mode || "questions_only";
  const examples = [
    "I went to the gym, did squats 100kg 3x5, drew a sketch today, and watched an ML lecture tagged AI, neural-nets",
    "Add to that lecture note: research vanishing gradients later",
    "Make a daily task to clean my room with steps: desk, cables, closet",
    "What is partially completed or still open right now?",
  ];

  const el = document.getElementById("view-ask");
  el.innerHTML = `
    <div class="view-header">
      <div>
        <h1 class="view-title">🎙️ Voice Agent Console</h1>
        <p class="view-sub">Talk across multiple turns — auto-stops on silence, calls tools, and organizes checklists &amp; lecture notes</p>
      </div>
      <button id="agent-new-session" class="btn btn-primary btn-sm">+ New Conversation</button>
    </div>

    <div class="agent-layout">
      <div class="card" style="display:flex;flex-direction:column;gap:8px;">
        <div class="card-header" style="margin-bottom:4px;">
          <h2 class="card-title">Conversations</h2>
        </div>
        <div class="item-list" style="max-height:480px;overflow-y:auto;">
          ${
            sessions.length
              ? sessions
                  .map(
                    (s) => `
                  <div class="note-list-item ${s.id === state.activeSessionId ? "active" : ""}" style="display:flex;align-items:center;gap:6px;" data-agent-sess="${s.id}">
                    <div style="flex:1;min-width:0;">
                      <div class="row-title" style="font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${esc(s.title)}</div>
                      <div class="row-meta">${esc(s.updated_at.slice(0, 16).replace("T", " "))}</div>
                    </div>
                    <button type="button" class="btn btn-xs btn-ghost" data-del-sess="${s.id}" title="Delete conversation">✕</button>
                  </div>`
                  )
                  .join("")
              : '<div class="empty-state">Tap Speak or type below to start a session!</div>'
          }
        </div>
      </div>

      <div class="card" style="display:flex;flex-direction:column;gap:10px;">
        <div class="agent-hero">
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
            <button id="agent-voice-btn" type="button" class="btn-voice btn-voice-lg ${state.recording ? "recording" : ""}">
              <span class="voice-dot"></span>
              <span id="agent-voice-label">${state.recording ? "Listening…" : "🎙️ Tap to Speak"}</span>
            </button>
            ${
              state.recording
                ? '<button id="agent-voice-cancel" type="button" class="btn btn-ghost btn-sm">Cancel</button>'
                : ""
            }
          </div>

          <div class="agent-toggles">
            <label title="Automatically send when you pause speaking">
              <input id="agent-autosilence-cb" type="checkbox" ${state.autoSilence ? "checked" : ""} />
              Auto-stop on pause
            </label>
            <label title="Keep listening again after the agent finishes replying">
              <input id="agent-continuous-cb" type="checkbox" ${state.continuousVoice ? "checked" : ""} />
              Continuous loop
            </label>
            <label>
              🔊
              <select id="agent-tts-select" style="padding:3px 7px;font-size:12px;">
                <option value="questions_only" ${ttsMode === "questions_only" ? "selected" : ""}>Questions Only</option>
                <option value="all" ${ttsMode === "all" ? "selected" : ""}>Speak All</option>
                <option value="off" ${ttsMode === "off" ? "selected" : ""}>Muted</option>
              </select>
            </label>
          </div>
        </div>

        <div id="agent-chat-feed" class="chat-feed">
          ${
            messages.length
              ? messages
                  .map(
                    (m) => `
                  <div class="chat-bubble ${esc(m.role)}">
                    <div class="chat-meta">
                      <strong>${m.role === "user" ? (m.source === "voice" ? "🎙️ You" : "You") : "🤖 Agent"}</strong>
                      <span>${esc((m.created_at || "").slice(11, 16))}</span>
                      ${
                        m.role === "assistant"
                          ? `<button type="button" class="btn btn-xs btn-ghost" data-replay-tts="${esc(m.content)}" title="Speak reply">🔊</button>`
                          : ""
                      }
                    </div>
                    <div>${esc(m.content)}</div>
                    ${
                      m.actions && m.actions.length
                        ? `<div class="action-chips">
                            ${m.actions
                              .map(
                                (a) =>
                                  `<button type="button" class="action-chip" data-chip-domain="${esc(a.domain || "")}" data-chip-id="${a.item_id ?? ""}">${esc(a.label)}</button>`
                              )
                              .join("")}
                          </div>`
                        : ""
                    }
                  </div>`
                  )
                  .join("")
              : `
                <div class="empty-state" style="text-align:left;padding:10px 4px;">
                  <div style="margin-bottom:10px;color:var(--text);font-weight:600;">Try speaking or clicking an example:</div>
                  <div style="display:flex;gap:6px;flex-wrap:wrap;">
                    ${examples
                      .map(
                        (q) =>
                          `<button type="button" class="mode-pill" data-agent-example="${esc(q)}">${esc(q)}</button>`
                      )
                      .join("")}
                  </div>
                </div>`
          }
        </div>

        <form id="agent-chat-form" class="inline-form" style="margin-bottom:0;margin-top:auto;">
          <input id="agent-chat-input" class="input grow" type="text" placeholder="Message the agent (or press Alt+V to speak)…" required />
          <button id="agent-chat-send" type="submit" class="btn btn-primary">Send</button>
        </form>
      </div>
    </div>
  `;

  const feed = el.querySelector("#agent-chat-feed");
  if (feed) {
    feed.scrollTop = feed.scrollHeight;
  }

  const sendAgentText = async (text) => {
    const sendBtn = el.querySelector("#agent-chat-send");
    if (sendBtn) {
      sendBtn.disabled = true;
      sendBtn.textContent = "Thinking…";
    }
    try {
      const res = await api("/api/agent/chat", {
        method: "POST",
        body: { text, session_id: state.activeSessionId, source: "text" },
      });
      await handleAgentResponse(res);
    } finally {
      if (sendBtn && document.body.contains(sendBtn)) {
        sendBtn.disabled = false;
        sendBtn.textContent = "Send";
      }
    }
  };

  el.querySelector("#agent-new-session").addEventListener("click", async () => {
    const res = await api("/api/agent/sessions", { method: "POST", body: { title: "New Session" } });
    state.activeSessionId = res.session.id;
    await renderAsk();
  });

  el.querySelectorAll("[data-agent-sess]").forEach((row) => {
    row.addEventListener("click", async (e) => {
      if (e.target.closest("[data-del-sess]")) return;
      state.activeSessionId = Number(row.dataset.agentSess);
      await renderAsk();
    });
  });

  el.querySelectorAll("[data-del-sess]").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const id = Number(btn.dataset.delSess);
      await api(`/api/agent/sessions/${id}`, { method: "DELETE" });
      if (state.activeSessionId === id) state.activeSessionId = null;
      await renderAsk();
    });
  });

  el.querySelector("#agent-voice-btn").addEventListener("click", toggleVoiceRecording);
  const cancelBtn = el.querySelector("#agent-voice-cancel");
  if (cancelBtn) {
    cancelBtn.addEventListener("click", cancelVoiceRecording);
  }

  el.querySelector("#agent-autosilence-cb").addEventListener("change", async (e) => {
    state.autoSilence = e.target.checked;
    await api("/api/settings", { method: "PATCH", body: { auto_silence: state.autoSilence } }).catch(() => {});
  });

  el.querySelector("#agent-continuous-cb").addEventListener("change", (e) => {
    state.continuousVoice = e.target.checked;
  });

  el.querySelector("#agent-tts-select").addEventListener("change", async (e) => {
    const tts_mode = e.target.value;
    await api("/api/settings", { method: "PATCH", body: { tts_mode } });
    await refreshSettingsBadge();
  });

  el.querySelector("#agent-chat-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = el.querySelector("#agent-chat-input");
    const txt = input.value.trim();
    if (!txt) return;
    input.value = "";
    await sendAgentText(txt);
  });

  el.querySelectorAll("[data-agent-example]").forEach((btn) => {
    btn.addEventListener("click", () => sendAgentText(btn.dataset.agentExample));
  });

  el.querySelectorAll("[data-replay-tts]").forEach((btn) => {
    btn.addEventListener("click", () => playAgentSpeech(btn.dataset.replayTts, true, true));
  });

  el.querySelectorAll("[data-chip-domain]").forEach((chip) => {
    chip.addEventListener("click", () => {
      const dom = chip.dataset.chipDomain;
      const id = chip.dataset.chipId ? Number(chip.dataset.chipId) : null;
      if (dom === "note") {
        state.notesFolder = "all";
        state.notesTag = "";
        state.notesQuery = "";
        state.noteDraft = null;
        if (id) state.activeNoteId = id;
        state.noteEditorMode = "read";
        switchView("notes");
      } else if (dom === "task" || dom === "want_to_do") switchView("tasks");
      else if (dom === "timer" || dom === "study" || dom === "study_session") switchView("timer");
      else if (dom === "watchlist") switchView("watchlist");
      else if (dom === "gym") switchView("gym");
      else if (dom === "art") switchView("art");
      else if (dom === "game") switchView("gaming");
    });
  });
}

// ---------------------------------------------------------------------------
// View 8: Settings & Privacy Controls
// ---------------------------------------------------------------------------

async function renderSettings() {
  const [settings, devData, cloudStatus, globalVoice] = await Promise.all([
    api("/api/settings"),
    api("/api/voice/devices"),
    api("/api/cloud/status"),
    api("/api/voice/global-state"),
  ]);
  state.settings = settings;
  const devices = devData.devices || [];
  const gdrive = cloudStatus.gdrive || {};
  const detectedClouds = cloudStatus.detected_folders || [];

  const el = document.getElementById("view-settings");
  el.innerHTML = `
    <div class="view-header">
      <div>
        <h1 class="view-title">⚙️ Settings</h1>
        <p class="view-sub">Groq free API key, system-wide voice hotkey, Google Drive / Cloud sync, and private storage</p>
      </div>
    </div>

    <div class="settings-grid">
      <div class="card">
        <div class="card-header">
          <h2 class="card-title">🤖 Groq Free API</h2>
          <span class="badge ${settings.has_api_key ? "badge-gym" : ""}">
            ${settings.has_api_key ? `Saved (${esc(settings.groq_api_key_masked)})` : "Not configured"}
          </span>
        </div>

        <div class="field-group">
          <label class="field-label">Groq API Key (get a free key at console.groq.com/keys)</label>
          <div class="field-row">
            <input id="set-groq-key" class="input" type="password" placeholder="${settings.has_api_key ? "Leave blank to keep existing key, or paste a new gsk_... key" : "Paste your gsk_... Groq API key"}" autocomplete="off" />
            <button id="set-test-groq" type="button" class="btn btn-ghost">Test Key</button>
            ${settings.has_api_key ? '<button id="set-clear-groq" type="button" class="btn btn-danger btn-sm">Clear</button>' : ""}
          </div>
        </div>

        <div class="field-group">
          <label class="field-label">Groq Chat Model</label>
          <select id="set-groq-model">
            ${(settings.allowed_models || ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b"])
              .map(
                (m) =>
                  `<option value="${esc(m)}" ${settings.groq_model === m ? "selected" : ""}>${esc(m)}</option>`
              )
              .join("")}
          </select>
        </div>
      </div>

      <div class="card">
        <div class="card-header">
          <h2 class="card-title">⌨️ Global Voice Hotkey &amp; Background Tray</h2>
          <span class="badge ${globalVoice.hotkey_registered ? "badge-gym" : ""}">
            ${
              globalVoice.hotkey_registered
                ? `Active: ${esc(globalVoice.hotkey)}`
                : globalVoice.hotkey_error
                ? esc(globalVoice.hotkey_error)
                : "Runs in desktop app"
            }
          </span>
        </div>

        <div class="field-group">
          <label class="field-label">Global Start / Stop Recording Shortcut (works from any app when running)</label>
          <div class="field-row" style="margin-bottom:8px;">
            <input id="set-global-hotkey" class="input" type="text" value="${esc(settings.global_hotkey)}" placeholder="e.g. ctrl+shift+space (leave empty to disable)" />
          </div>
          <div style="display:flex;gap:6px;flex-wrap:wrap;">
            ${["ctrl+shift+space", "ctrl+alt+v", "alt+shift+space", "ctrl+shift+r"]
              .map(
                (hk) =>
                  `<button type="button" class="mode-pill ${settings.global_hotkey === hk ? "active" : ""}" data-hk-preset="${hk}">${esc(hk)}</button>`
              )
              .join("")}
          </div>
        </div>

        <div style="display:flex;flex-direction:column;gap:8px;">
          <label class="check-row">
            <input id="set-minimize-tray" type="checkbox" ${settings.minimize_to_tray ? "checked" : ""} />
            Hide to System Tray when closing window (keeps global hotkey alive; right-click tray icon → Exit to quit)
          </label>
          <label class="check-row">
            <input id="set-floating-pill" type="checkbox" ${settings.floating_indicator ? "checked" : ""} />
            Show tiny always-on-top floating recording indicator when speaking from another app
          </label>
          <label class="check-row">
            <input id="set-sound-feedback" type="checkbox" ${settings.sound_feedback ? "checked" : ""} />
            Play subtle start / stop / saved audio chimes
          </label>
        </div>
      </div>

      <div class="card">
        <div class="card-header"><h2 class="card-title">🎙️ Voice &amp; Tracker Preferences</h2></div>

        <div class="field-group">
          <label class="field-label">Microphone Input Device (Groq Whisper)</label>
          <select id="set-mic-device">
            <option value="">Default System Microphone</option>
            ${devices
              .map(
                (d) =>
                  `<option value="${d.id}" ${String(settings.mic_device) === String(d.id) ? "selected" : ""}>${esc(d.name)}${d.is_default ? " (Default)" : ""}</option>`
              )
              .join("")}
          </select>
        </div>

        <div class="grid-2">
          <div class="field-group">
            <label class="field-label">Default Voice Action</label>
            <select id="set-voice-mode">
              <option value="auto_ai" ${settings.voice_mode === "auto_ai" ? "selected" : ""}>Transcribe &amp; Run Voice Agent</option>
              <option value="fill_input" ${settings.voice_mode === "fill_input" ? "selected" : ""}>Transcribe into Quick-Capture Box</option>
            </select>
          </div>
          <div class="field-group">
            <label class="field-label">Default Gym Weight Unit</label>
            <select id="set-weight-unit">
              <option value="kg" ${settings.weight_unit === "kg" ? "selected" : ""}>Kilograms (kg)</option>
              <option value="lbs" ${settings.weight_unit === "lbs" ? "selected" : ""}>Pounds (lbs)</option>
            </select>
          </div>
        </div>

        <div class="grid-2">
          <div class="field-group">
            <label class="field-label">Auto-Stop Recording on Silence</label>
            <select id="set-auto-silence">
              <option value="true" ${settings.auto_silence ? "selected" : ""}>Enabled (hands-free)</option>
              <option value="false" ${!settings.auto_silence ? "selected" : ""}>Manual tap Start/Stop only</option>
            </select>
          </div>
          <div class="field-group">
            <label class="field-label">Silence Pause Delay</label>
            <select id="set-silence-seconds">
              ${[1.8, 2.5, 3.5, 5.0]
                .map(
                  (sec) =>
                    `<option value="${sec}" ${Number(settings.silence_seconds) === sec ? "selected" : ""}>${sec}s pause</option>`
                )
                .join("")}
            </select>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-header"><h2 class="card-title">🔊 Spoken Replies (TTS)</h2></div>
        <div class="grid-3">
          <div class="field-group">
            <label class="field-label">When to Speak</label>
            <select id="set-tts-mode">
              <option value="questions_only" ${settings.tts_mode === "questions_only" ? "selected" : ""}>Questions &amp; Clarifications Only</option>
              <option value="all" ${settings.tts_mode === "all" ? "selected" : ""}>All Agent Replies</option>
              <option value="off" ${settings.tts_mode === "off" ? "selected" : ""}>Muted / Visual Only</option>
            </select>
          </div>
          <div class="field-group">
            <label class="field-label">TTS Provider</label>
            <select id="set-tts-provider">
              <option value="groq" ${settings.tts_provider === "groq" ? "selected" : ""}>Groq Orpheus + Local Fallback</option>
              <option value="local" ${settings.tts_provider === "local" ? "selected" : ""}>Windows / Edge Built-in Only</option>
              <option value="google" ${settings.tts_provider === "google" ? "selected" : ""}>Google TTS (Fallback Until Configured)</option>
            </select>
          </div>
          <div class="field-group">
            <label class="field-label">Groq Orpheus Voice</label>
            <div class="field-row">
              <select id="set-tts-voice">
                ${(settings.allowed_tts_voices || ["troy", "hannah", "autumn", "diana", "austin", "daniel"])
                  .map(
                    (v) =>
                      `<option value="${esc(v)}" ${settings.tts_voice === v ? "selected" : ""}>${esc(v)}</option>`
                  )
                  .join("")}
              </select>
              <button id="set-test-tts" type="button" class="btn btn-ghost btn-sm">🔊 Test</button>
            </div>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-header"><h2 class="card-title">☁️ Cloud &amp; Google Drive Sync</h2></div>

        <div style="display:flex;flex-direction:column;gap:14px;">
          <!-- Mode A: Instant Cloud Folder Auto-Sync -->
          <div class="cloud-subpanel">
            <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;">
              <strong>Option A — Instant Cloud Folder Sync (Google Drive Desktop / OneDrive / Dropbox)</strong>
              ${cloudStatus.last_sync ? `<span class="badge">Last synced: ${esc(cloudStatus.last_sync)}</span>` : ""}
            </div>
            ${
              detectedClouds.length
                ? `<div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;">
                    <span style="font-size:12px;color:var(--muted)">Detected:</span>
                    ${detectedClouds
                      .map(
                        (c) =>
                          `<button type="button" class="mode-pill" data-cloud-detect="${esc(c.suggested_target)}">${esc(c.provider)}: ${esc(c.suggested_target)}</button>`
                      )
                      .join("")}
                  </div>`
                : ""
            }
            <div class="field-row">
              <input id="set-cloud-folder" class="input" type="text" value="${esc(settings.cloud_folder)}" placeholder="e.g. G:\\My Drive\\PersonalLifeHub or OneDrive\\PersonalLifeHub" />
              <button id="set-pick-cloud-folder" type="button" class="btn btn-ghost">Browse…</button>
              <button id="set-cloud-sync-btn" type="button" class="btn btn-primary btn-sm">☁️ Sync Now</button>
            </div>
            <div style="display:flex;gap:14px;flex-wrap:wrap;">
              <label class="check-row"><input id="set-cloud-auto" type="checkbox" ${settings.cloud_auto_sync ? "checked" : ""} /> Auto-sync when Notes / Watchlist change</label>
              <label class="check-row"><input id="set-cloud-notes" type="checkbox" ${settings.cloud_sync_notes ? "checked" : ""} /> Notes (.md)</label>
              <label class="check-row"><input id="set-cloud-watch" type="checkbox" ${settings.cloud_sync_watchlist ? "checked" : ""} /> Watchlist &amp; Tasks</label>
              <label class="check-row"><input id="set-cloud-backup" type="checkbox" ${settings.cloud_sync_backup ? "checked" : ""} /> JSON Backup</label>
            </div>
          </div>

          <!-- Mode B: Direct Google Drive OAuth API -->
          <div class="cloud-subpanel">
            <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;">
              <strong>Option B — Direct Google Drive API (OAuth — No Desktop App Needed)</strong>
              <span class="badge ${gdrive.connected ? "badge-gym" : ""}">
                ${
                  gdrive.connected
                    ? `Connected${gdrive.account_email ? ` (${esc(gdrive.account_email)})` : ""}`
                    : gdrive.has_credentials
                    ? "Credentials loaded (Ready to Sign In)"
                    : "Not configured"
                }
              </span>
            </div>
            <div class="field-row" style="flex-wrap:wrap;">
              <input id="set-gdrive-folder" class="input" type="text" value="${esc(settings.gdrive_folder_name || "PersonalLifeHub")}" placeholder="Google Drive folder name" style="max-width:220px;" />
              <button id="set-gdrive-pick-creds" type="button" class="btn btn-ghost btn-sm">📄 Load credentials.json</button>
              <button id="set-gdrive-toggle-paste" type="button" class="btn btn-ghost btn-sm">Paste JSON</button>
              ${
                gdrive.connected
                  ? `
                    <button id="set-gdrive-sync-btn" type="button" class="btn btn-primary btn-sm">☁️ Sync to Google Drive</button>
                    <button id="set-gdrive-disconnect-btn" type="button" class="btn btn-danger btn-sm">Disconnect</button>
                  `
                  : `<button id="set-gdrive-connect-btn" type="button" class="btn btn-primary btn-sm" ${gdrive.has_credentials ? "" : "disabled"}>🔗 Sign in with Google</button>`
              }
            </div>
            <div id="set-gdrive-paste-wrap" class="hidden" style="display:flex;flex-direction:column;gap:6px;">
              <textarea id="set-gdrive-paste-json" rows="4" placeholder='Paste Google Cloud Desktop OAuth credentials.json contents here…'></textarea>
              <button id="set-gdrive-save-raw" type="button" class="btn btn-sm btn-ghost" style="align-self:flex-start;">Save Credentials JSON</button>
            </div>
            <details style="font-size:12px;color:var(--muted);">
              <summary style="cursor:pointer;">How to get your own free Google Cloud OAuth credentials.json (1-time setup)</summary>
              <ol style="margin:6px 0 0;padding-left:18px;line-height:1.5;">
                <li>Open Google Cloud Console → Create/select a project → Enable <strong>Google Drive API</strong>.</li>
                <li>Go to <strong>Credentials → Create Credentials → OAuth client ID</strong> → Application type: <strong>Desktop app</strong> (add your Gmail as a Test User if Testing mode).</li>
                <li>Download JSON, click <strong>Load credentials.json</strong> above, then click <strong>Sign in with Google</strong>. Credentials stay strictly in <code>%LOCALAPPDATA%\\PersonalLifeHub</code>.</li>
              </ol>
            </details>
            ${gdrive.last_sync ? `<div style="font-size:12px;color:var(--muted)">Last Drive API sync: ${esc(gdrive.last_sync)}</div>` : ""}
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-header"><h2 class="card-title">📁 Local Notes Folder Sync (.md / .txt)</h2></div>
        <div class="field-group">
          <label class="field-label">Folder Path</label>
          <div class="field-row">
            <input id="set-notes-folder" class="input" type="text" value="${esc(settings.notes_folder)}" placeholder="e.g. C:\\Users\\...\\Documents\\Notes" />
            <button id="set-pick-folder" type="button" class="btn btn-ghost">Browse…</button>
            <button id="set-sync-now" type="button" class="btn btn-ghost">Sync Now</button>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-header"><h2 class="card-title">🔒 Private Local Storage &amp; Backup</h2></div>
        <p class="view-sub" style="margin-bottom:12px;">
          Your database, settings, API key, OAuth tokens, and reference images are stored locally outside Git at:<br/>
          <code>${esc(settings.data_dir)}</code>
        </p>
        <div style="display:flex;gap:10px;flex-wrap:wrap;justify-content:space-between;align-items:center;">
          <div style="display:flex;gap:8px;flex-wrap:wrap;">
            <button id="set-open-data" type="button" class="btn btn-ghost">Open Data Folder</button>
            <a href="/api/system/export" download="personal-life-hub-backup.json" class="btn btn-ghost" style="text-decoration:none;">Export Backup JSON</a>
          </div>
          <button id="set-save-btn" type="button" class="btn btn-primary">Save Settings</button>
        </div>
      </div>
    </div>
  `;

  el.querySelectorAll("[data-hk-preset]").forEach((btn) => {
    btn.addEventListener("click", () => {
      el.querySelector("#set-global-hotkey").value = btn.dataset.hkPreset;
      el.querySelectorAll("[data-hk-preset]").forEach((b) =>
        b.classList.toggle("active", b.dataset.hkPreset === btn.dataset.hkPreset)
      );
    });
  });

  el.querySelectorAll("[data-cloud-detect]").forEach((btn) => {
    btn.addEventListener("click", () => {
      el.querySelector("#set-cloud-folder").value = btn.dataset.cloudDetect;
      el.querySelector("#set-cloud-auto").checked = true;
    });
  });

  el.querySelector("#set-pick-cloud-folder").addEventListener("click", async () => {
    const res = await api("/api/system/pick-folder", { method: "POST" });
    if (res.path) {
      el.querySelector("#set-cloud-folder").value = res.path;
    }
  });

  el.querySelector("#set-test-groq").addEventListener("click", async () => {
    const entered = el.querySelector("#set-groq-key").value.trim();
    const btn = el.querySelector("#set-test-groq");
    btn.disabled = true;
    btn.textContent = "Testing…";
    try {
      const res = await api("/api/settings/test-groq", {
        method: "POST",
        body: { api_key: entered || null },
      });
      toast(`Groq key valid! (${res.models_count} models reachable)`, "success");
    } catch (err) {
      toast(err.message, "error");
    } finally {
      btn.disabled = false;
      btn.textContent = "Test Key";
    }
  });

  const clearKeyBtn = el.querySelector("#set-clear-groq");
  if (clearKeyBtn) {
    clearKeyBtn.addEventListener("click", async () => {
      await api("/api/settings", { method: "PATCH", body: { groq_api_key: "" } });
      await refreshSettingsBadge();
      toast("Cleared Groq API key", "info");
      await renderSettings();
    });
  }

  el.querySelector("#set-pick-folder").addEventListener("click", async () => {
    const res = await api("/api/system/pick-folder", { method: "POST" });
    if (res.path) {
      el.querySelector("#set-notes-folder").value = res.path;
    }
  });

  const saveAllSettings = async () => {
    const payload = {
      groq_model: el.querySelector("#set-groq-model").value,
      voice_mode: el.querySelector("#set-voice-mode").value,
      weight_unit: el.querySelector("#set-weight-unit").value,
      notes_folder: el.querySelector("#set-notes-folder").value.trim(),
      auto_silence: el.querySelector("#set-auto-silence").value === "true",
      silence_seconds: Number(el.querySelector("#set-silence-seconds").value || 3.5),
      tts_mode: el.querySelector("#set-tts-mode").value,
      tts_provider: el.querySelector("#set-tts-provider").value,
      tts_voice: el.querySelector("#set-tts-voice").value,
      global_hotkey: el.querySelector("#set-global-hotkey").value.trim(),
      minimize_to_tray: el.querySelector("#set-minimize-tray").checked,
      floating_indicator: el.querySelector("#set-floating-pill").checked,
      sound_feedback: el.querySelector("#set-sound-feedback").checked,
      cloud_folder: el.querySelector("#set-cloud-folder").value.trim(),
      cloud_auto_sync: el.querySelector("#set-cloud-auto").checked,
      cloud_sync_notes: el.querySelector("#set-cloud-notes").checked,
      cloud_sync_watchlist: el.querySelector("#set-cloud-watch").checked,
      cloud_sync_backup: el.querySelector("#set-cloud-backup").checked,
      gdrive_folder_name: el.querySelector("#set-gdrive-folder").value.trim() || "PersonalLifeHub",
      mic_device: (() => {
        const raw = el.querySelector("#set-mic-device").value;
        return raw === "" ? null : Number(raw);
      })(),
    };
    const newKey = el.querySelector("#set-groq-key").value.trim();
    if (newKey) {
      payload.groq_api_key = newKey;
    }
    await api("/api/settings", { method: "PATCH", body: payload });
    await refreshSettingsBadge();
  };

  el.querySelector("#set-cloud-sync-btn").addEventListener("click", async () => {
    const btn = el.querySelector("#set-cloud-sync-btn");
    btn.disabled = true;
    btn.textContent = "Syncing…";
    try {
      await saveAllSettings();
      const res = await api("/api/cloud/sync-folder", { method: "POST", body: {} });
      toast(`Cloud folder synced: ${res.files_written} updated, ${res.files_unchanged} unchanged`, "success");
      await renderSettings();
    } catch (err) {
      toast(err.message, "error");
    } finally {
      if (document.body.contains(btn)) {
        btn.disabled = false;
        btn.textContent = "☁️ Sync Now";
      }
    }
  });

  el.querySelector("#set-gdrive-pick-creds").addEventListener("click", async () => {
    const res = await api("/api/system/pick-json", { method: "POST" });
    if (!res.path) return;
    try {
      await api("/api/cloud/gdrive/credentials-file", {
        method: "POST",
        body: { path: res.path },
      });
      toast("Loaded credentials.json! Click 'Sign in with Google'.", "success");
      await renderSettings();
    } catch (err) {
      toast(err.message, "error");
    }
  });

  el.querySelector("#set-gdrive-toggle-paste").addEventListener("click", () => {
    el.querySelector("#set-gdrive-paste-wrap").classList.toggle("hidden");
  });

  el.querySelector("#set-gdrive-save-raw").addEventListener("click", async () => {
    const raw = el.querySelector("#set-gdrive-paste-json").value.trim();
    if (!raw) return;
    try {
      await api("/api/cloud/gdrive/credentials-raw", {
        method: "POST",
        body: { credentials_json: raw },
      });
      toast("Saved credentials.json!", "success");
      await renderSettings();
    } catch (err) {
      toast(err.message, "error");
    }
  });

  el.querySelector("#set-gdrive-connect-btn")?.addEventListener("click", async () => {
    toast("Opening browser for Google Drive sign-in…", "info");
    try {
      const res = await api("/api/cloud/gdrive/connect", { method: "POST" });
      toast(`Connected Google Drive${res.account_email ? ` (${res.account_email})` : ""}!`, "success");
      await renderSettings();
    } catch (err) {
      toast(err.message, "error");
    }
  });

  el.querySelector("#set-gdrive-disconnect-btn")?.addEventListener("click", async () => {
    await api("/api/cloud/gdrive/disconnect", { method: "POST" });
    toast("Disconnected Google Drive", "info");
    await renderSettings();
  });

  el.querySelector("#set-gdrive-sync-btn")?.addEventListener("click", async () => {
    const btn = el.querySelector("#set-gdrive-sync-btn");
    btn.disabled = true;
    btn.textContent = "Uploading…";
    try {
      await saveAllSettings();
      const res = await api("/api/cloud/gdrive/sync", { method: "POST" });
      toast(`Google Drive synced: ${res.created} new, ${res.updated} updated`, "success");
      await renderSettings();
    } catch (err) {
      toast(err.message, "error");
    } finally {
      if (document.body.contains(btn)) {
        btn.disabled = false;
        btn.textContent = "☁️ Sync to Google Drive";
      }
    }
  });

  el.querySelector("#set-test-tts").addEventListener("click", async () => {
    await saveAllSettings();
    await playAgentSpeech("Hey! Your Personal Life Hub voice agent is ready.", true, true);
  });

  el.querySelector("#set-sync-now").addEventListener("click", async () => {
    await saveAllSettings();
    const res = await api("/api/notes/sync", { method: "POST" });
    toast(`Synced folder: +${res.imported} new, ${res.updated} updated`, "success");
    await renderSettings();
  });

  el.querySelector("#set-open-data").addEventListener("click", async () => {
    await api("/api/system/open-data-dir", { method: "POST" });
  });

  el.querySelector("#set-save-btn").addEventListener("click", async () => {
    try {
      await saveAllSettings();
      toast("Settings saved", "success");
      await renderSettings();
    } catch (err) {
      toast(err.message, "error");
    }
  });
}

// ---------------------------------------------------------------------------
// Bootstrap & Shortcuts
// ---------------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", async () => {
  document.querySelectorAll(".nav-item").forEach((btn) => {
    btn.addEventListener("click", () => switchView(btn.dataset.view));
  });

  document.querySelectorAll("#capture-modes .mode-pill").forEach((pill) => {
    pill.addEventListener("click", () => {
      state.captureMode = pill.dataset.mode;
      document.querySelectorAll("#capture-modes .mode-pill").forEach((p) => p.classList.remove("active"));
      pill.classList.add("active");
    });
  });

  document.getElementById("capture-submit-btn").addEventListener("click", submitCapture);
  document.getElementById("capture-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submitCapture();
    }
  });

  document.getElementById("voice-btn").addEventListener("click", toggleVoiceRecording);
  document.getElementById("top-timer-pill").addEventListener("click", () => switchView("timer"));
  document.getElementById("voice-cancel-btn").addEventListener("click", cancelVoiceRecording);
  document.getElementById("voice-mode-toggle").addEventListener("click", async () => {
    state.voiceMode = state.voiceMode === "auto_ai" ? "fill_input" : "auto_ai";
    updateVoiceModeLabel();
    try {
      await api("/api/settings", { method: "PATCH", body: { voice_mode: state.voiceMode } });
    } catch {
      // ignore
    }
  });

  document.getElementById("image-modal-backdrop").addEventListener("click", () => {
    document.getElementById("image-modal").classList.add("hidden");
  });
  document.getElementById("image-modal-close").addEventListener("click", () => {
    document.getElementById("image-modal").classList.add("hidden");
  });

  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s" && state.view === "notes") {
      e.preventDefault();
      const saveBtn = document.getElementById("note-save-btn") || document.getElementById("note-create-btn");
      if (saveBtn && !saveBtn.disabled) {
        saveBtn.click();
      }
      return;
    }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      const input = document.getElementById("capture-input");
      input.focus();
      input.select();
      return;
    }
    if (e.altKey && e.key.toLowerCase() === "v") {
      e.preventDefault();
      toggleVoiceRecording();
      return;
    }
    if (e.ctrlKey && e.code === "Space" && !isTypingInField(e.target)) {
      e.preventDefault();
      toggleVoiceRecording();
      return;
    }
    if (e.key === "Escape") {
      if (!document.getElementById("image-modal").classList.contains("hidden")) {
        document.getElementById("image-modal").classList.add("hidden");
        return;
      }
      if (state.recording) {
        cancelVoiceRecording();
        return;
      }
    }
    if (e.altKey && !e.ctrlKey && !e.shiftKey) {
      const views = ["hub", "tasks", "notes", "timer", "watchlist", "gym", "art", "gaming", "ask", "settings"];
      const idx = e.key === "0" ? 9 : Number(e.key) - 1;
      if (idx >= 0 && idx < views.length) {
        e.preventDefault();
        switchView(views[idx]);
      }
    }
  });

  const pollGlobalVoiceOnce = async () => {
    try {
      const gv = await api("/api/voice/global-state");
      syncVoiceBtnWithGlobalState(gv);
      if (gv.event_seq > state.lastGlobalVoiceSeq) {
        state.lastGlobalVoiceSeq = gv.event_seq;
        if (gv.phase === "done" && gv.last_result) {
          const r = gv.last_result;
          toast(`🎙️ "${r.transcript}" → ${r.reply}`, "success");
          await syncTimerState();
          if (r.navigate_to && r.navigate_to !== state.view) {
            await switchView(r.navigate_to);
          } else {
            await renderCurrentView();
          }
        } else if (gv.phase === "error" && gv.message) {
          toast(`Global voice error: ${gv.message}`, "error");
        }
      }
    } catch {
      // ignore transient polls
    }
  };

  try {
    const initGv = await api("/api/voice/global-state");
    state.lastGlobalVoiceSeq = Number(initGv.event_seq || 0);
    syncVoiceBtnWithGlobalState(initGv);
    if (initGv.hotkey && !initGv.hotkey_registered && initGv.hotkey_error) {
      toast(
        `Global hotkey (${initGv.hotkey}) unavailable: ${initGv.hotkey_error} — check Settings (Alt+0)`,
        "error"
      );
    }
  } catch {
    // ignore
  }
  state.globalVoicePollId = setInterval(pollGlobalVoiceOnce, 1500);

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      syncTimerState();
    }
  });
  window.addEventListener("focus", () => {
    syncTimerState();
  });

  await refreshSettingsBadge();
  await syncTimerState();
  await renderHub();
});
