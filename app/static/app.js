/* Personal Life Hub Frontend */

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
  gymMonth: new Date().toISOString().slice(0, 7),
  artMonth: new Date().toISOString().slice(0, 7),
  gymSelectedDate: "",
  gameFilter: "all",
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

// Placeholder stubs completed in Checkpoint 4
async function renderGym() {
  document.getElementById("view-gym").innerHTML = `<div class="card">Loading Gym…</div>`;
}
async function renderArt() {
  document.getElementById("view-art").innerHTML = `<div class="card">Loading Art…</div>`;
}
async function renderGaming() {
  document.getElementById("view-gaming").innerHTML = `<div class="card">Loading Gaming…</div>`;
}
async function renderAsk() {
  document.getElementById("view-ask").innerHTML = `<div class="card">Loading Ask AI…</div>`;
}
async function renderSettings() {
  document.getElementById("view-settings").innerHTML = `<div class="card">Loading Settings…</div>`;
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
