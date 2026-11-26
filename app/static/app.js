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
  recording: false,
  voiceTimerId: null,
  dashboard: null,
  tasks: [],
  notes: [],
  activeNoteId: null,
  notesQuery: "",
  gymMonth: localTodayStr().slice(0, 7),
  artMonth: localTodayStr().slice(0, 7),
  gymFilterExercise: "",
  gameFilter: "all",
  askQuery: "",
  askResult: null,
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
  state.view = viewName;
  document.querySelectorAll(".nav-item").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.view === viewName);
  });
  document.querySelectorAll(".view").forEach((sec) => {
    sec.classList.toggle("active", sec.id === `view-${viewName}`);
  });
  await renderCurrentView();
}

async function renderCurrentView() {
  try {
    if (state.view === "hub") await renderHub();
    else if (state.view === "tasks") await renderTasks();
    else if (state.view === "notes") await renderNotes();
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
// Top Quick-Capture & Voice Input
// ---------------------------------------------------------------------------

function updateVoiceModeLabel() {
  const btn = document.getElementById("voice-mode-toggle");
  btn.textContent = state.voiceMode === "auto_ai" ? "Auto-file" : "To text";
}

function setRecordingUI(active, elapsed = 0) {
  state.recording = active;
  const btn = document.getElementById("voice-btn");
  const label = document.getElementById("voice-btn-label");
  const timer = document.getElementById("voice-timer");
  const cancelBtn = document.getElementById("voice-cancel-btn");

  btn.classList.toggle("recording", active);
  cancelBtn.classList.toggle("hidden", !active);
  timer.classList.toggle("hidden", !active);
  label.textContent = active ? "Stop & Send" : "🎙️ Speak";
  timer.textContent = `${elapsed.toFixed(1)}s`;

  if (state.voiceTimerId) {
    clearInterval(state.voiceTimerId);
    state.voiceTimerId = null;
  }
  if (active) {
    const started = performance.now() - elapsed * 1000;
    state.voiceTimerId = setInterval(() => {
      const sec = (performance.now() - started) / 1000;
      timer.textContent = `${sec.toFixed(1)}s`;
    }, 100);
  }
}

async function submitCapture() {
  const input = document.getElementById("capture-input");
  const btn = document.getElementById("capture-submit-btn");
  const text = input.value.trim();
  if (!text) return;
  btn.disabled = true;
  btn.textContent = "Filing…";
  try {
    const res = await api("/api/capture", {
      method: "POST",
      body: { text, mode: state.captureMode, source: "text" },
    });
    input.value = "";
    const msg = res.warning ? `${res.summary} (${res.warning})` : res.summary;
    toast(msg || "Captured!", res.warning ? "info" : "success");
    await renderCurrentView();
  } catch (err) {
    toast(err.message, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "Capture";
  }
}

async function toggleVoiceRecording() {
  const btn = document.getElementById("voice-btn");
  if (!state.recording) {
    try {
      await api("/api/voice/start", { method: "POST" });
      setRecordingUI(true, 0);
    } catch (err) {
      toast(err.message, "error");
    }
    return;
  }

  btn.disabled = true;
  document.getElementById("voice-btn-label").textContent = "Transcribing…";
  if (state.voiceTimerId) {
    clearInterval(state.voiceTimerId);
    state.voiceTimerId = null;
  }
  try {
    const res = await api("/api/voice/stop", {
      method: "POST",
      body: { voice_mode: state.voiceMode },
    });
    setRecordingUI(false, 0);
    if (res.voice_mode === "auto_ai" && res.capture) {
      toast(`🎙️ "${res.transcript}" → ${res.capture.summary}`, "success");
      await renderCurrentView();
    } else {
      const input = document.getElementById("capture-input");
      input.value = res.transcript || "";
      input.focus();
      toast("🎙️ Transcribed to capture box", "info");
    }
  } catch (err) {
    setRecordingUI(false, 0);
    toast(err.message, "error");
  } finally {
    btn.disabled = false;
  }
}

async function cancelVoiceRecording() {
  if (!state.recording) return;
  try {
    await api("/api/voice/cancel", { method: "POST" });
  } catch {
    // ignore
  }
  setRecordingUI(false, 0);
  toast("Voice recording cancelled", "info");
}

// ---------------------------------------------------------------------------
// View 1: Hub (Today Overview)
// ---------------------------------------------------------------------------

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
        <input type="checkbox" class="checkbox" data-hub-task="${t.id}" />
        <div class="row-main">
          <div class="row-title">${esc(t.title)}</div>
          <div class="row-meta">
            ${t.priority === "high" ? '<span class="badge badge-high">High</span>' : ""}
            ${t.due_date ? `<span>Due ${esc(t.due_date)}</span>` : ""}
          </div>
        </div>
      </div>`
        )
        .join("")
    : `<div class="empty-state">All caught up on daily tasks!</div>`;

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
        <div class="row-meta">${esc((n.content || "").slice(0, 95))}</div>
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
        <span class="stat-label">Daily Tasks</span>
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
    </div>

    <div class="grid-2">
      <div class="card">
        <div class="card-header">
          <h2 class="card-title">✅ Daily Tasks</h2>
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
  el.querySelectorAll("[data-hub-task]").forEach((cb) => {
    cb.addEventListener("change", async () => {
      await api(`/api/tasks/${cb.dataset.hubTask}`, { method: "PATCH", body: { done: cb.checked } });
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
      state.activeNoteId = Number(card.dataset.jumpNote);
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
  const daily = items.filter((t) => t.list_type === "daily");
  const want = items.filter((t) => t.list_type === "want_to_do");

  const renderTaskRow = (t, isWant) => `
    <div class="list-row ${t.done ? "done" : ""}">
      <input type="checkbox" class="checkbox" data-task-done="${t.id}" ${t.done ? "checked" : ""} />
      <div class="row-main">
        <div class="row-title">${esc(t.title)}</div>
        <div class="row-meta">
          ${t.priority === "high" ? '<span class="badge badge-high">High</span>' : ""}
          ${t.category ? `<span class="badge">${esc(t.category)}</span>` : ""}
          ${t.due_date ? `<span>Due ${esc(t.due_date)}</span>` : ""}
        </div>
      </div>
      <div class="row-actions">
        ${
          isWant
            ? `<button class="btn btn-xs btn-ghost" data-move-list="${t.id}" data-target="daily" title="Move to Daily Tasks">→ Daily</button>`
            : `<button class="btn btn-xs btn-ghost" data-move-list="${t.id}" data-target="want_to_do" title="Move to Want to Do">→ Someday</button>`
        }
        <button class="btn btn-xs btn-danger" data-del-task="${t.id}" title="Delete">✕</button>
      </div>
    </div>`;

  const el = document.getElementById("view-tasks");
  el.innerHTML = `
    <div class="view-header">
      <div>
        <h1 class="view-title">✅ Tasks &amp; Want to Do</h1>
        <p class="view-sub">Actionable daily to-dos on the left, someday ideas &amp; bucket items on the right</p>
      </div>
    </div>

    <div class="grid-2">
      <div class="card">
        <div class="card-header">
          <h2 class="card-title">Daily Tasks <span class="badge badge-accent">${daily.filter((x) => !x.done).length} open</span></h2>
        </div>
        <form id="form-daily-task" class="inline-form">
          <input id="daily-title" class="input grow" type="text" placeholder="Add daily task…" required />
          <select id="daily-priority">
            <option value="normal">Normal</option>
            <option value="high">High</option>
          </select>
          <input id="daily-due" class="input" type="date" />
          <button type="submit" class="btn btn-primary btn-sm">Add</button>
        </form>
        <div class="item-list">
          ${daily.length ? daily.map((t) => renderTaskRow(t, false)).join("") : '<div class="empty-state">No daily tasks yet.</div>'}
        </div>
      </div>

      <div class="card">
        <div class="card-header">
          <h2 class="card-title">Want to Do / Someday <span class="badge">${want.filter((x) => !x.done).length} open</span></h2>
        </div>
        <form id="form-want-task" class="inline-form">
          <input id="want-title" class="input grow" type="text" placeholder="Add thing you want to do…" required />
          <input id="want-cat" class="input" type="text" placeholder="Tag (optional)" style="width:120px" />
          <button type="submit" class="btn btn-primary btn-sm">Add</button>
        </form>
        <div class="item-list">
          ${want.length ? want.map((t) => renderTaskRow(t, true)).join("") : '<div class="empty-state">Add ideas, trips, or skills you want to try!</div>'}
        </div>
      </div>
    </div>
  `;

  el.querySelector("#form-daily-task").addEventListener("submit", async (e) => {
    e.preventDefault();
    const title = el.querySelector("#daily-title").value.trim();
    if (!title) return;
    await api("/api/tasks", {
      method: "POST",
      body: {
        title,
        list_type: "daily",
        priority: el.querySelector("#daily-priority").value,
        due_date: el.querySelector("#daily-due").value || null,
      },
    });
    await renderTasks();
  });

  el.querySelector("#form-want-task").addEventListener("submit", async (e) => {
    e.preventDefault();
    const title = el.querySelector("#want-title").value.trim();
    if (!title) return;
    await api("/api/tasks", {
      method: "POST",
      body: {
        title,
        list_type: "want_to_do",
        category: el.querySelector("#want-cat").value.trim(),
      },
    });
    await renderTasks();
  });

  el.querySelectorAll("[data-task-done]").forEach((cb) => {
    cb.addEventListener("change", async () => {
      await api(`/api/tasks/${cb.dataset.taskDone}`, { method: "PATCH", body: { done: cb.checked } });
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
// View 3: Notes + Local Folder Sync
// ---------------------------------------------------------------------------

async function renderNotes() {
  const { items } = await api(`/api/notes?q=${encodeURIComponent(state.notesQuery)}`);
  state.notes = items;
  if (!state.activeNoteId || !items.some((n) => n.id === state.activeNoteId)) {
    state.activeNoteId = items.length ? items[0].id : null;
  }
  const active = items.find((n) => n.id === state.activeNoteId) || null;
  const hasFolder = Boolean(state.settings?.notes_folder);

  const el = document.getElementById("view-notes");
  el.innerHTML = `
    <div class="view-header">
      <div>
        <h1 class="view-title">📝 Notes</h1>
        <p class="view-sub">Fast notes with tags, search, and safe local .md/.txt folder sync</p>
      </div>
      <div style="display:flex; gap:8px; flex-wrap:wrap;">
        <button id="notes-sync-btn" class="btn btn-ghost btn-sm" ${hasFolder ? "" : "title='Configure a folder in Settings first'"}>
          🔄 Sync Folder
        </button>
        <button id="notes-new-btn" class="btn btn-primary btn-sm">+ New Note</button>
      </div>
    </div>

    <div class="notes-layout">
      <div class="card" style="display:flex;flex-direction:column;gap:10px;">
        <input id="notes-search-input" class="input" type="search" placeholder="Search notes or #tags…" value="${esc(state.notesQuery)}" />
        <div class="item-list" style="overflow-y:auto;max-height:460px;">
          ${
            items.length
              ? items
                  .map(
                    (n) => `
              <div class="note-list-item ${n.id === state.activeNoteId ? "active" : ""}" data-select-note="${n.id}">
                <div class="row-title">${n.pinned ? "📌 " : ""}${esc(n.title)}</div>
                <div class="row-meta">
                  <span>${esc(n.updated_at.slice(0, 10))}</span>
                  ${n.source_path ? '<span class="badge badge-accent">Synced</span>' : ""}
                  ${n.tags.map((t) => `<span class="badge">#${esc(t)}</span>`).join("")}
                </div>
              </div>`
                  )
                  .join("")
              : '<div class="empty-state">No notes match. Click "+ New Note" to start!</div>'
          }
        </div>
      </div>

      <div class="card">
        ${
          active
            ? `
          <div class="note-editor">
            <div class="field-row">
              <input id="note-edit-title" class="input" type="text" value="${esc(active.title)}" placeholder="Note title" style="font-weight:700;font-size:15px;" />
              <button id="note-pin-btn" class="btn btn-sm ${active.pinned ? "btn-primary" : "btn-ghost"}" title="Pin note">📌</button>
              <button id="note-save-btn" class="btn btn-sm btn-primary">Save</button>
              <button id="note-del-btn" class="btn btn-sm btn-danger">Delete</button>
            </div>
            <input id="note-edit-tags" class="input" type="text" value="${esc(active.tags.join(", "))}" placeholder="Comma-separated tags (e.g. ideas, study)" />
            <textarea id="note-edit-content" placeholder="Write your note here…">${esc(active.content)}</textarea>
            <div class="row-meta">
              <span>Updated ${esc(active.updated_at)}</span>
              ${active.source_path ? `<span>File: ${esc(active.source_path)}</span>` : ""}
            </div>
          </div>`
            : `
          <div class="note-editor">
            <div class="card-header"><h2 class="card-title">Create a New Note</h2></div>
            <input id="note-new-title" class="input" type="text" placeholder="Title (optional)" />
            <input id="note-new-tags" class="input" type="text" placeholder="Tags (comma-separated)" />
            <textarea id="note-new-content" placeholder="Jot down anything…"></textarea>
            <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;">
              ${
                hasFolder
                  ? '<label style="display:flex;align-items:center;gap:6px;font-size:12.5px;color:var(--muted)"><input id="note-save-folder" type="checkbox" checked /> Also save .md file to sync folder</label>'
                  : "<span></span>"
              }
              <button id="note-create-btn" class="btn btn-primary">Save Note</button>
            </div>
          </div>`
        }
      </div>
    </div>
  `;

  const searchInput = el.querySelector("#notes-search-input");
  let searchTimer = null;
  searchInput.addEventListener("input", () => {
    state.notesQuery = searchInput.value;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => renderNotes(), 180);
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
    el.querySelector(".note-editor").innerHTML = `
      <div class="card-header"><h2 class="card-title">Create a New Note</h2></div>
      <input id="note-new-title" class="input" type="text" placeholder="Title (optional)" />
      <input id="note-new-tags" class="input" type="text" placeholder="Tags (comma-separated)" />
      <textarea id="note-new-content" placeholder="Jot down anything…"></textarea>
      <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;">
        ${
          hasFolder
            ? '<label style="display:flex;align-items:center;gap:6px;font-size:12.5px;color:var(--muted)"><input id="note-save-folder" type="checkbox" checked /> Also save .md file to sync folder</label>'
            : "<span></span>"
        }
        <button id="note-create-btn" class="btn btn-primary">Save Note</button>
      </div>`;
    bindNewNoteCreate(el);
  });

  el.querySelectorAll("[data-select-note]").forEach((item) => {
    item.addEventListener("click", () => {
      state.activeNoteId = Number(item.dataset.selectNote);
      renderNotes();
    });
  });

  if (active) {
    el.querySelector("#note-save-btn").addEventListener("click", async () => {
      await api(`/api/notes/${active.id}`, {
        method: "PATCH",
        body: {
          title: el.querySelector("#note-edit-title").value,
          tags: el.querySelector("#note-edit-tags").value,
          content: el.querySelector("#note-edit-content").value,
        },
      });
      toast("Note saved", "success");
      await renderNotes();
    });
    el.querySelector("#note-pin-btn").addEventListener("click", async () => {
      await api(`/api/notes/${active.id}`, { method: "PATCH", body: { pinned: !active.pinned } });
      await renderNotes();
    });
    el.querySelector("#note-del-btn").addEventListener("click", async () => {
      await api(`/api/notes/${active.id}`, { method: "DELETE" });
      state.activeNoteId = null;
      toast("Note deleted", "info");
      await renderNotes();
    });
  } else {
    bindNewNoteCreate(el);
  }
}

function bindNewNoteCreate(el) {
  const btn = el.querySelector("#note-create-btn");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    const title = el.querySelector("#note-new-title").value.trim();
    const tags = el.querySelector("#note-new-tags").value.trim();
    const content = el.querySelector("#note-new-content").value;
    const saveToFolder = Boolean(el.querySelector("#note-save-folder")?.checked);
    const res = await api("/api/notes", {
      method: "POST",
      body: { title, tags, content, save_to_folder: saveToFolder },
    });
    state.activeNoteId = res.item.id;
    toast("Note created", "success");
    await renderNotes();
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
// View 7: Ask AI & Search Everything
// ---------------------------------------------------------------------------

async function renderAsk() {
  const el = document.getElementById("view-ask");
  const examples = [
    "What are my open daily tasks?",
    "Summarize my recent gym progress and PBs",
    "What notes do I have?",
    "What games am I playing or want to play?",
  ];

  const res = state.askResult;
  el.innerHTML = `
    <div class="view-header">
      <div>
        <h1 class="view-title">🤖 Ask My Notes &amp; Life</h1>
        <p class="view-sub">Ask Groq AI or search across your notes, tasks, gym logs, art, and games</p>
      </div>
    </div>

    <div class="card" style="margin-bottom:16px;">
      <form id="ask-form" class="inline-form">
        <input id="ask-input" class="input grow" type="text" value="${esc(state.askQuery)}" placeholder="Ask anything about your notes, workouts, tasks, or hobbies…" required />
        <button id="ask-submit-btn" type="submit" class="btn btn-primary">Ask</button>
      </form>
      <div style="display:flex;gap:6px;flex-wrap:wrap;">
        ${examples
          .map(
            (q) =>
              `<button type="button" class="mode-pill" data-ask-example="${esc(q)}">${esc(q)}</button>`
          )
          .join("")}
      </div>
    </div>

    ${
      res
        ? `
      <div class="card" style="margin-bottom:16px;">
        <div class="card-header">
          <h2 class="card-title">Answer</h2>
          <span class="badge ${res.ai_used ? "badge-gym" : ""}">
            ${res.ai_used ? `Groq · ${esc(res.model)}` : "Local Search Mode"}
          </span>
        </div>
        <div class="answer-box">${esc(res.answer)}</div>
      </div>

      <div class="card">
        <div class="card-header">
          <h2 class="card-title">Matching Sources <span class="badge">${(res.sources || []).length}</span></h2>
        </div>
        <div class="item-list">
          ${
            res.sources && res.sources.length
              ? res.sources
                  .map(
                    (s) => `
                  <div class="list-row" data-source-jump="${esc(s.type)}" data-source-id="${s.id}" style="cursor:pointer;">
                    <span class="badge badge-accent">${esc(s.type.toUpperCase())}</span>
                    <div class="row-main">
                      <div class="row-title">${esc(s.title)}</div>
                      <div class="row-meta">
                        <span>${esc(s.meta || "")}</span>
                        <span>${esc((s.snippet || "").slice(0, 120))}</span>
                      </div>
                    </div>
                  </div>`
                  )
                  .join("")
              : '<div class="empty-state">No direct source records matched.</div>'
          }
        </div>
      </div>`
        : ""
    }
  `;

  const runAsk = async (question) => {
    state.askQuery = question;
    const btn = el.querySelector("#ask-submit-btn");
    btn.disabled = true;
    btn.textContent = "Thinking…";
    try {
      state.askResult = await api("/api/ai/ask", { method: "POST", body: { question } });
      await renderAsk();
    } finally {
      if (document.body.contains(btn)) {
        btn.disabled = false;
        btn.textContent = "Ask";
      }
    }
  };

  el.querySelector("#ask-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const q = el.querySelector("#ask-input").value.trim();
    if (q) await runAsk(q);
  });

  el.querySelectorAll("[data-ask-example]").forEach((btn) => {
    btn.addEventListener("click", () => runAsk(btn.dataset.askExample));
  });

  el.querySelectorAll("[data-source-jump]").forEach((row) => {
    row.addEventListener("click", () => {
      const t = row.dataset.sourceJump;
      const id = Number(row.dataset.sourceId);
      if (t === "note") {
        state.activeNoteId = id;
        switchView("notes");
      } else if (t === "task") switchView("tasks");
      else if (t === "gym") switchView("gym");
      else if (t === "art") switchView("art");
      else if (t === "game") switchView("gaming");
    });
  });
}

// ---------------------------------------------------------------------------
// View 8: Settings & Privacy Controls
// ---------------------------------------------------------------------------

async function renderSettings() {
  const [settings, devData] = await Promise.all([
    api("/api/settings"),
    api("/api/voice/devices"),
  ]);
  state.settings = settings;
  const devices = devData.devices || [];

  const el = document.getElementById("view-settings");
  el.innerHTML = `
    <div class="view-header">
      <div>
        <h1 class="view-title">⚙️ Settings</h1>
        <p class="view-sub">Groq free API key, voice microphone, local notes folder sync, and private storage</p>
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
            ${(settings.allowed_models || ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"])
              .map(
                (m) =>
                  `<option value="${esc(m)}" ${settings.groq_model === m ? "selected" : ""}>${esc(m)}</option>`
              )
              .join("")}
          </select>
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
              <option value="auto_ai" ${settings.voice_mode === "auto_ai" ? "selected" : ""}>Transcribe &amp; Auto-File via AI</option>
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
          Your database, settings, API key, and reference images are stored locally outside Git at:<br/>
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
    await saveAllSettings();
    toast("Settings saved", "success");
    await renderSettings();
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
      const views = ["hub", "tasks", "notes", "gym", "art", "gaming", "ask", "settings"];
      const idx = Number(e.key) - 1;
      if (idx >= 0 && idx < views.length) {
        e.preventDefault();
        switchView(views[idx]);
      }
    }
  });

  await refreshSettingsBadge();
  await renderHub();
});
