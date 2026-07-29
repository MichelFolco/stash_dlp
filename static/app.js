// Stash DLP Web — ported from stash_dlp.py's YtdlpManagerApp.
// State machine (READY / EDITING / FETCHING / INTERCEPTING) and app_mode
// (DOWNLOAD / FIND_LINK) mirror the desktop app; URLs are pasted manually
// into the input field rather than auto-read from the clipboard, and
// window-chrome pieces differ due to browser sandboxing.

// The server's snapshot endpoints (GET /api/jobs, refresh, folder-change,
// folder-change, etc.) all return newest-first. We want the Map's own
// insertion order to be true chronological (oldest→newest) always -
// consistent whether jobs arrive via an initial snapshot or one at a
// time via job_added - so sorting logic elsewhere can just trust Map
// order without needing to know which case it came from.
function loadJobsIntoMap(jobsArray) {
  state.jobs.clear();
  for (const job of [...jobsArray].reverse()) state.jobs.set(job.filename, job);
}

const el = (id) => document.getElementById(id);

// "Manage External Programs" and "Open With..." only make sense on the
// machine actually running the server (that's where the programs and
// the screen to show them on live) - same reasoning as the folder
// browse buttons and Open in Explorer, which the backend already
// localhost-gates. We mirror that here on the client using the
// hostname the browser used to reach the server, so remote (e.g.
// Tailscale) sessions see the options as disabled with an explanation
// rather than have them silently fail server-side.
const IS_LOCAL = ["127.0.0.1", "localhost", "::1"].includes(window.location.hostname);
const REMOTE_ONLY_TOOLTIP = "Only available when browsing from the same machine as the server.";

const state = {
  appMode: "DOWNLOAD",        // DOWNLOAD | FIND_LINK
  current: "READY",           // READY | EDITING | FETCHING | INTERCEPTING
  targetUrl: "",
  stagedUrl: "",
  tagDomain: true,
  m3uSniffer: false,
  isMiniMode: false,
  jobs: new Map(),            // filename -> job dict
  ws: null,
  recentDirs: [],
  recentTargetDirs: [],
  externalPrograms: [],
  filterText: "",
  sortField: "added",         // added | size | name
  sortDir: "desc",            // desc | asc
};

const inputField = el("input-field");
const app = el("app");
const queueList = el("queue-list");
const miniStats = el("mini-stats");
const dlModeBtn = el("dl-mode-btn");
const findModeBtn = el("find-mode-btn");
const modeContainer = el("mode-container");
const resDropdown = el("res-dropdown");
const ctxVersion = el("ctx-version");
const ctxSaveDir = el("ctx-save-dir");
const ctxChangeFolder = el("ctx-change-folder");
const logoMenu = el("logo-menu");
const jobMenu = el("job-menu");
const folderModal = el("folder-modal");
const folderInput = el("folder-input");
const folderError = el("folder-error");
const folderRecentWrap = el("folder-recent-wrap");
const folderRecentList = el("folder-recent-list");
const ctxTargetDir = el("ctx-target-dir");
const targetFolderModalEl = el("target-folder-modal");
const videoModal = el("video-modal");
const videoPlayer = el("video-player");
const videoModalTitle = el("video-modal-title");
const audioPlayerWrap = el("audio-player-wrap");
const audioPlayer = el("audio-player");
const openWithFlyout = el("open-with-flyout");
const openWithProgramsList = el("open-with-programs-list");
const copyFlyout = el("copy-flyout");
const fileFlyout = el("file-flyout");
const foldersFlyout = el("folders-flyout");
const settingsFlyout = el("settings-flyout");
const externalProgramsModal = el("external-programs-modal");
const externalProgramsList = el("external-programs-list");
const programFormModal = el("program-form-modal");
const programFormTitle = el("program-form-title");
const programNameInput = el("program-name-input");
const programPathInput = el("program-path-input");
const programArgsInput = el("program-args-input");
const programFormError = el("program-form-error");
const programDeleteBtn = el("program-delete-btn");

// ── Boot ──────────────────────────────────────────────────────
async function boot() {
  await refreshVersion();
  await refreshSaveDir();
  await refreshTargetDir();
  await refreshExternalPrograms();
  await loadJobsSnapshot();
  connectWebSocket();
  applyLocalOnlyUI();
  inputField.setPlaceholderText = null; // n/a, kept for readability
  inputField.placeholder = "Paste a link, then press ENTER...";
  inputField.focus();
}

// Static for the life of the page (IS_LOCAL doesn't change at runtime),
// so this just needs to run once at boot rather than on every menu open.
function applyLocalOnlyUI() {
  if (IS_LOCAL) return;
  for (const id of ["ctx-manage-programs", "ctx-open-with", "flyout-manage-programs"]) {
    const item = el(id);
    item.classList.add("ctx-disabled");
    item.title = REMOTE_ONLY_TOOLTIP;
  }
}

async function refreshExternalPrograms() {
  try {
    const res = await fetch("/api/external-programs");
    const data = await res.json();
    state.externalPrograms = data.programs || [];
  } catch (e) {
    state.externalPrograms = [];
  }
}

async function refreshSaveDir() {
  try {
    const res = await fetch("/api/settings");
    const data = await res.json();
    setSaveDirDisplay(data.save_dir, data.free_space);
    state.recentDirs = data.recent_dirs || [];
  } catch (e) {
    ctxSaveDir.querySelector(".ctx-folder-path").textContent = "Folder: (unavailable)";
    ctxSaveDir.querySelector("#ctx-save-free").textContent = "";
  }
}

function setSaveDirDisplay(path, freeSpace) {
  ctxSaveDir.querySelector(".ctx-folder-path").textContent = `Folder: ${path}`;
  ctxSaveDir.querySelector("#ctx-save-free").textContent = freeSpace || "";
  ctxSaveDir.title = path;
}

async function refreshTargetDir() {
  try {
    const res = await fetch("/api/target-settings");
    const data = await res.json();
    setTargetDirDisplay(data.target_dir, data.free_space);
    state.recentTargetDirs = data.recent_dirs || [];
  } catch (e) {
    ctxTargetDir.querySelector(".ctx-folder-path").textContent = "Target: (unavailable)";
    ctxTargetDir.querySelector("#ctx-target-free").textContent = "";
  }
}

function setTargetDirDisplay(path, freeSpace) {
  const pathEl = ctxTargetDir.querySelector(".ctx-folder-path");
  const freeEl = ctxTargetDir.querySelector("#ctx-target-free");
  if (!path) {
    pathEl.textContent = "Target: (none set)";
    freeEl.textContent = "";
  } else {
    pathEl.textContent = `Target: ${path}`;
    freeEl.textContent = freeSpace || "";
  }
  ctxTargetDir.title = path || "";
}

async function refreshVersion() {
  try {
    const res = await fetch("/api/version");
    const data = await res.json();
    let text = data.version ? `yt-dlp v${data.version}` : "yt-dlp version unknown";
    if (data.version && data.just_updated) text += " (just updated)";
    ctxVersion.textContent = text;
  } catch (e) {
    ctxVersion.textContent = "yt-dlp version unknown";
  }
}

async function loadJobsSnapshot() {
  try {
    const res = await fetch("/api/jobs");
    const jobs = await res.json();
    loadJobsIntoMap(jobs);
    renderLedger();
  } catch (e) {
    // backend not reachable yet; ignore
  }
}

// ── WebSocket ─────────────────────────────────────────────────
function connectWebSocket() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${proto}//${location.host}/ws`);
  state.ws = ws;

  ws.onopen = () => {
    // Covers both the very first connection and any reconnect (e.g.
    // after a restart) - re-sync everything rather than trusting
    // whatever local state we had before the gap.
    refreshVersion();
    refreshSaveDir();
    refreshTargetDir();
    loadJobsSnapshot();

    if (inputField.disabled) {
      inputField.disabled = false;
      inputField.placeholder = "Paste a link, then press ENTER...";
    }
  };

  ws.onmessage = (evt) => {
    const msg = JSON.parse(evt.data);
    if (msg.type === "job_added") {
      state.jobs.set(msg.job.filename, msg.job);
      renderLedger();
    } else if (msg.type === "job_progress") {
      const job = state.jobs.get(msg.filename);
      if (job) {
        job.pct = msg.pct; job.total = msg.total; job.speed = msg.speed; job.eta = msg.eta;
        updateJobCardProgress(msg.filename, job);
        updateMiniStats();
      }
    } else if (msg.type === "job_finished") {
      const job = state.jobs.get(msg.filename);
      if (job) {
        job.status = msg.status;
        job.file_size = msg.file_size;
        job.is_audio = msg.is_audio;
        renderLedger();
        updateMiniStats();
      }
    } else if (msg.type === "job_deleted") {
      state.jobs.delete(msg.filename);
      renderLedger();
    } else if (msg.type === "refresh") {
      loadJobsIntoMap(msg.jobs);
      renderLedger();
    }
  };

  ws.onclose = () => {
    setTimeout(connectWebSocket, 2000); // reconnect, e.g. after a backend restart
  };
}

// ── Ledger rendering ────────────────────────────────────────────
function parseSizeToBytes(sizeStr) {
  if (!sizeStr) return -1; // unknown/in-progress sizes sort to the bottom
  const m = /^([\d.]+)\s*(B|KB|MB|GB|TB|PB)$/i.exec(sizeStr.trim());
  if (!m) return -1;
  const value = parseFloat(m[1]);
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  const exp = units.indexOf(m[2].toUpperCase());
  return value * Math.pow(1024, Math.max(exp, 0));
}

function getFilteredSortedJobs() {
  // "added" order = Map insertion order, oldest first; reverse for
  // newest-first, matching the ledger's existing default look.
  const withIndex = Array.from(state.jobs.values()).map((job, i) => ({ job, addedIndex: i }));

  const query = state.filterText.trim().toLowerCase();
  const filtered = query
    ? withIndex.filter(({ job }) => job.filename.toLowerCase().includes(query))
    : withIndex;

  const dirMul = state.sortDir === "asc" ? 1 : -1;
  filtered.sort((a, b) => {
    let cmp;
    if (state.sortField === "size") {
      cmp = parseSizeToBytes(a.job.file_size) - parseSizeToBytes(b.job.file_size);
    } else if (state.sortField === "name") {
      cmp = a.job.filename.toLowerCase().localeCompare(b.job.filename.toLowerCase());
    } else {
      cmp = a.addedIndex - b.addedIndex;
    }
    return cmp * dirMul;
  });

  return filtered.map(({ job }) => job);
}

function renderLedger() {
  queueList.innerHTML = "";
  for (const job of getFilteredSortedJobs()) {
    queueList.appendChild(buildJobCard(job));
  }
}

const ledgerFilterInput = el("ledger-filter");
const ledgerSortSelect = el("ledger-sort");
const ledgerSortDirBtn = el("ledger-sort-dir");

ledgerFilterInput.addEventListener("input", () => {
  state.filterText = ledgerFilterInput.value;
  renderLedger();
});

ledgerSortSelect.addEventListener("change", () => {
  state.sortField = ledgerSortSelect.value;
  renderLedger();
});

ledgerSortDirBtn.addEventListener("click", () => {
  state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
  ledgerSortDirBtn.textContent = state.sortDir === "asc" ? "↑" : "↓";
  renderLedger();
});

function buildJobCard(job) {
  const card = document.createElement("div");
  card.className = "job-card";
  card.dataset.filename = job.filename;
  if (job.is_audio) card.classList.add("audio");

  const thumb = document.createElement("div");
  thumb.className = "job-thumb";
  if (job.is_audio) {
    // No point requesting a video-frame thumbnail for an audio-only
    // file - just show a distinct icon directly.
    const placeholder = document.createElement("span");
    placeholder.className = "job-thumb-placeholder";
    placeholder.textContent = "🎵";
    thumb.appendChild(placeholder);
  } else {
    const thumbImg = document.createElement("img");
    thumbImg.loading = "lazy";
    thumbImg.alt = "";
    thumbImg.src = `/api/jobs/thumbnail?filename=${encodeURIComponent(job.filename)}`;
    thumbImg.addEventListener("error", () => {
      thumbImg.remove();
      const placeholder = document.createElement("span");
      placeholder.className = "job-thumb-placeholder";
      placeholder.textContent = "🎬";
      thumb.appendChild(placeholder);
    }, { once: true });
    thumb.appendChild(thumbImg);
  }
  card.appendChild(thumb);

  const body = document.createElement("div");
  body.className = "job-card-body";

  const titleRow = document.createElement("div");
  titleRow.className = "job-title-row";
  const title = document.createElement("div");
  title.className = "job-title";
  title.textContent = job.filename;
  title.title = buildTooltip(job);
  titleRow.appendChild(title);
  if (job.is_audio) {
    const badge = document.createElement("span");
    badge.className = "audio-badge";
    badge.textContent = "AUDIO";
    titleRow.appendChild(badge);
  }
  body.appendChild(titleRow);

  const status = job.status;
  if (status === "DOWNLOADING") {
    const stats = document.createElement("div");
    stats.className = "job-stats";
    stats.textContent = "Initializing metrics...";
    body.appendChild(stats);

    const track = document.createElement("div");
    track.className = "job-progress-track";
    const fill = document.createElement("div");
    fill.className = "job-progress-fill";
    track.appendChild(fill);
    body.appendChild(track);

    card.appendChild(body);
    updateProgressDOM(card, job);
  } else {
    card.classList.add(status === "DONE" ? "done" : status === "CANCELLED" ? "cancelled" : "error");
    if (status === "DONE" && job.file_size) {
      const size = document.createElement("div");
      size.className = "job-size";
      size.textContent = job.file_size;
      body.appendChild(size);
    }
    card.appendChild(body);
  }

  // Clicking the thumbnail itself starts playback directly (for
  // completed items); clicking anywhere else on the card opens the
  // options menu. For non-completed items there's nothing to play yet,
  // so a thumbnail click there just falls through to the same menu
  // behavior as the rest of the card.
  thumb.addEventListener("click", (e) => {
    if (job.status === "DONE") {
      e.stopPropagation();
      openMediaModal(job.filename, job.is_audio);
    }
  });

  card.addEventListener("click", (e) => {
    e.stopPropagation();
    openJobMenu(e.clientX, e.clientY, job);
  });

  return card;
}

function buildTooltip(job) {
  let t = `File: ${job.filename}\nURL: ${job.url || ""}`;
  if (job.file_size) t += `\nSize: ${job.file_size}`;
  return t;
}

function updateJobCardProgress(filename, job) {
  const card = queueList.querySelector(`.job-card[data-filename="${cssEscape(filename)}"]`);
  if (card) updateProgressDOM(card, job);
}

function updateProgressDOM(card, job) {
  const stats = card.querySelector(".job-stats");
  const fill = card.querySelector(".job-progress-fill");
  if (!stats || !fill) return;
  if (job.speed === "mrg") {
    stats.textContent = "Merging audio and video streams...";
  } else {
    stats.textContent = `${job.pct}%  |  ${job.total}  |  ${job.speed}  |  ETA: ${job.eta}`;
  }
  const pct = parseFloat(job.pct) || 0;
  fill.style.width = `${Math.max(0, Math.min(100, pct))}%`;
}

function cssEscape(s) {
  return s.replace(/["\\]/g, "\\$&");
}

// ── Mini-mode aggregate telemetry (client-side, since progress events
// already arrive in the browser — no need for a server-side aggregate) ──
function updateMiniStats() {
  if (!state.isMiniMode) { miniStats.classList.add("hidden"); return; }

  const active = Array.from(state.jobs.values()).filter((j) => j.status === "DOWNLOADING");
  if (active.length === 0) { miniStats.classList.add("hidden"); return; }

  let totalKbps = 0;
  let maxEta = 0;
  for (const job of active) {
    const speedMatch = /([\d.]+)\s*(MB|KB|B)\/s/.exec(job.speed || "");
    if (speedMatch) {
      const val = parseFloat(speedMatch[1]);
      totalKbps += speedMatch[2] === "MB" ? val * 1024 : speedMatch[2] === "KB" ? val : val / 1024;
    }
    const etaMatch = /(?:(\d+)m\s*)?(\d+)s/.exec(job.eta || "");
    if (etaMatch) {
      const secs = (parseInt(etaMatch[1] || "0", 10) * 60) + parseInt(etaMatch[2], 10);
      if (secs > maxEta) maxEta = secs;
    }
  }

  const speedMbps = totalKbps / 1024;
  const speedStr = speedMbps >= 0.1 ? `${speedMbps.toFixed(1)}MB/s` : `${totalKbps.toFixed(0)}KB/s`;
  const etaStr = maxEta >= 60 ? `${Math.floor(maxEta / 60)}m${maxEta % 60}s` : maxEta > 0 ? `${maxEta}s` : "--";

  miniStats.textContent = `[${active.length}] \u26A1${speedStr} \u23F1\uFE0F${etaStr}`;
  miniStats.classList.remove("hidden");
}

// Every submenu flyout in the app - Copy/File (job menu) and
// Folders/Settings (logo menu) all share this one open/close/position
// mechanism, so anything that resets menu state just walks this list.
const ALL_FLYOUTS = [openWithFlyout, copyFlyout, fileFlyout, foldersFlyout, settingsFlyout];

function hideAllFlyouts() {
  for (const flyout of ALL_FLYOUTS) flyout.classList.add("hidden");
}

// ── Context menus ─────────────────────────────────────────────
function openLogoMenu(x, y) {
  jobMenu.classList.add("hidden");
  hideAllFlyouts();
  el("ctx-m3u-toggle").style.display = state.appMode === "DOWNLOAD" ? "" : "none";
  positionMenu(logoMenu, x, y);
  refreshSaveDir();
  refreshTargetDir();
}

function openJobMenu(x, y, job) {
  logoMenu.classList.add("hidden");
  hideAllFlyouts();

  const isDownloading = job.status === "DOWNLOADING";
  const isDone = job.status === "DONE";
  const isVideo = isDone && !job.is_audio;
  const isAudioDone = isDone && job.is_audio;
  el("ctx-cancel-job").classList.toggle("hidden", !isDownloading);
  el("ctx-play-video").classList.toggle("hidden", !isVideo);
  el("ctx-play-audio").classList.toggle("hidden", !isAudioDone);
  el("ctx-extract-audio").classList.toggle("hidden", !isVideo);
  // Shown for any completed item (video or audio) once at least one
  // external program is configured; enabled/disabled state (local vs
  // remote) is applied once at boot via applyLocalOnlyUI.
  el("ctx-open-with").classList.toggle("hidden", !(isDone && state.externalPrograms.length > 0));

  // Copy and File are submenu triggers now (see copy-flyout/file-flyout)
  // rather than flat top-level items - the trigger itself is shown
  // whenever at least one of its children would be, and the individual
  // rows inside each flyout are toggled the same way they always were.
  el("ctx-copy-link").classList.toggle("hidden", isDownloading);
  el("ctx-copy-filename").classList.toggle("hidden", isDownloading);
  el("ctx-copy-submenu").classList.toggle("hidden", isDownloading);

  el("ctx-rename-file").classList.toggle("hidden", isDownloading);
  el("ctx-move-to-target").classList.toggle("hidden", !isDone);
  el("ctx-open-folder").classList.toggle("hidden", !isDone);
  el("ctx-file-submenu").classList.toggle("hidden", isDownloading);

  el("ctx-delete-file").classList.toggle("hidden", isDownloading);

  jobMenu.dataset.filename = job.filename;
  positionMenu(jobMenu, x, y);
}

el("ctx-cancel-job").addEventListener("click", async () => {
  const filename = jobMenu.dataset.filename;
  closeMenus();
  await fetch("/api/jobs/cancel", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename }),
  });
});

el("ctx-play-video").addEventListener("click", () => {
  const filename = jobMenu.dataset.filename;
  closeMenus();
  openMediaModal(filename, false);
});

el("ctx-play-audio").addEventListener("click", () => {
  const filename = jobMenu.dataset.filename;
  closeMenus();
  openMediaModal(filename, true);
});

el("ctx-extract-audio").addEventListener("click", async () => {
  const filename = jobMenu.dataset.filename;
  closeMenus();
  flashStatus(`Extracting audio from "${filename}"...`);
  try {
    const res = await fetch("/api/jobs/extract-audio", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename }),
    });
    const data = await res.json();
    if (!res.ok) {
      window.alert(`Couldn't extract audio:\n${data.error || "Unknown error"}`);
      return;
    }
    state.jobs.set(data.job.filename, data.job);
    renderLedger();
    flashStatus(`Audio extracted: ${data.job.filename}`);
  } catch (e) {
    window.alert("Couldn't reach the server to extract audio.");
  }
});

el("ctx-open-with").addEventListener("click", () => {
  if (!IS_LOCAL) {
    flashStatus(REMOTE_ONLY_TOOLTIP);
    return;
  }
  // Click toggles the flyout open/closed rather than hover, so this
  // behaves sanely with touch/Moonlight-style input.
  if (!openWithFlyout.classList.contains("hidden")) {
    openWithFlyout.classList.add("hidden");
    return;
  }
  openOpenWithFlyout();
});

function closeOtherFlyouts(except) {
  for (const flyout of ALL_FLYOUTS) {
    if (flyout !== except) flyout.classList.add("hidden");
  }
}

function positionFlyoutNextTo(flyoutEl, anchorMenu) {
  const anchorRect = anchorMenu.getBoundingClientRect();
  flyoutEl.classList.remove("hidden");
  const flyoutRect = flyoutEl.getBoundingClientRect();

  // Prefer opening to the right of the anchor menu; flip to the left if
  // that would run off the edge of the viewport.
  let left = anchorRect.right + 4;
  if (left + flyoutRect.width > window.innerWidth - 8) {
    left = anchorRect.left - flyoutRect.width - 4;
  }
  left = Math.max(8, left);

  let top = anchorRect.top;
  const maxTop = window.innerHeight - flyoutRect.height - 8;
  top = Math.min(top, Math.max(8, maxTop));

  flyoutEl.style.left = `${left}px`;
  flyoutEl.style.top = `${top}px`;
  lastMenuOpenAt = Date.now(); // extend the ghost-click grace window
}

function openOpenWithFlyout() {
  closeOtherFlyouts(openWithFlyout);
  openWithProgramsList.innerHTML = "";
  for (const prog of state.externalPrograms) {
    const item = document.createElement("div");
    item.className = "ctx-item";
    item.textContent = prog.name;
    item.title = prog.path;
    item.addEventListener("click", () => launchExternalProgram(prog.id));
    openWithProgramsList.appendChild(item);
  }
  positionFlyoutNextTo(openWithFlyout, jobMenu);
}

el("ctx-copy-submenu").addEventListener("click", () => {
  if (!copyFlyout.classList.contains("hidden")) {
    copyFlyout.classList.add("hidden");
    return;
  }
  closeOtherFlyouts(copyFlyout);
  positionFlyoutNextTo(copyFlyout, jobMenu);
});

el("ctx-file-submenu").addEventListener("click", () => {
  if (!fileFlyout.classList.contains("hidden")) {
    fileFlyout.classList.add("hidden");
    return;
  }
  closeOtherFlyouts(fileFlyout);
  positionFlyoutNextTo(fileFlyout, jobMenu);
});

el("ctx-folders-submenu").addEventListener("click", () => {
  if (!foldersFlyout.classList.contains("hidden")) {
    foldersFlyout.classList.add("hidden");
    return;
  }
  closeOtherFlyouts(foldersFlyout);
  positionFlyoutNextTo(foldersFlyout, logoMenu);
});

el("ctx-settings-submenu").addEventListener("click", () => {
  if (!settingsFlyout.classList.contains("hidden")) {
    settingsFlyout.classList.add("hidden");
    return;
  }
  closeOtherFlyouts(settingsFlyout);
  positionFlyoutNextTo(settingsFlyout, logoMenu);
});

async function launchExternalProgram(programId) {
  const filename = jobMenu.dataset.filename;
  closeMenus();
  try {
    const res = await fetch("/api/jobs/open-with", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename, program_id: programId }),
    });
    const data = await res.json();
    if (!res.ok) {
      window.alert(`Couldn't launch program:\n${data.error || "Unknown error"}`);
    }
  } catch (e) {
    window.alert("Couldn't reach the server to launch that program.");
  }
}

el("flyout-manage-programs").addEventListener("click", () => {
  closeMenus();
  if (!IS_LOCAL) {
    flashStatus(REMOTE_ONLY_TOOLTIP);
    return;
  }
  openExternalProgramsModal();
});

const NEAR_END_THRESHOLD_SECONDS = 5;
const POSITION_SAVE_INTERVAL_MS = 5000;
let mediaTracking = null; // { filename, element, lastSentAt }

function openMediaModal(filename, isAudio) {
  videoModalTitle.textContent = filename;
  videoModalTitle.title = filename;
  const streamUrl = `/api/jobs/stream?filename=${encodeURIComponent(filename)}`;

  videoPlayer.classList.toggle("hidden", isAudio);
  audioPlayerWrap.classList.toggle("hidden", !isAudio);

  const job = state.jobs.get(filename);
  const resumePosition = job && job.playback_position > 0 ? job.playback_position : 0;
  const activeEl = isAudio ? audioPlayer : videoPlayer;

  const onLoadedMetadata = () => {
    if (resumePosition > 0 && activeEl.duration && resumePosition < activeEl.duration - NEAR_END_THRESHOLD_SECONDS) {
      activeEl.currentTime = resumePosition;
    }
    activeEl.removeEventListener("loadedmetadata", onLoadedMetadata);
  };
  activeEl.addEventListener("loadedmetadata", onLoadedMetadata);

  activeEl.src = streamUrl;
  videoModal.classList.remove("hidden");
  attachPlaybackTracking(filename, activeEl);
  activeEl.play().catch(() => {}); // ignore autoplay-blocked rejections
}

function closeMediaModal() {
  flushPlaybackPosition();
  detachPlaybackTracking();

  videoPlayer.pause();
  videoPlayer.removeAttribute("src");
  videoPlayer.load(); // release the buffered stream instead of leaving it open
  audioPlayer.pause();
  audioPlayer.removeAttribute("src");
  audioPlayer.load();
  videoModal.classList.add("hidden");
}

function attachPlaybackTracking(filename, element) {
  detachPlaybackTracking();

  const tracking = { filename, element, lastSentAt: 0 };

  tracking.onTimeUpdate = () => {
    const now = Date.now();
    if (now - tracking.lastSentAt < POSITION_SAVE_INTERVAL_MS) return;
    tracking.lastSentAt = now;
    flushPlaybackPosition();
  };
  tracking.onPause = () => flushPlaybackPosition();
  tracking.onEnded = () => sendPlaybackPosition(filename, 0); // finished - replay from the start next time

  element.addEventListener("timeupdate", tracking.onTimeUpdate);
  element.addEventListener("pause", tracking.onPause);
  element.addEventListener("ended", tracking.onEnded);

  mediaTracking = tracking;
}

function detachPlaybackTracking() {
  if (!mediaTracking) return;
  const { element, onTimeUpdate, onPause, onEnded } = mediaTracking;
  element.removeEventListener("timeupdate", onTimeUpdate);
  element.removeEventListener("pause", onPause);
  element.removeEventListener("ended", onEnded);
  mediaTracking = null;
}

function flushPlaybackPosition() {
  if (!mediaTracking) return;
  const { filename, element } = mediaTracking;
  if (!element.duration || isNaN(element.duration)) return;
  const nearEnd = element.currentTime >= element.duration - NEAR_END_THRESHOLD_SECONDS;
  sendPlaybackPosition(filename, nearEnd ? 0 : element.currentTime);
}

function sendPlaybackPosition(filename, position) {
  fetch("/api/jobs/playback-position", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename, position }),
  }).catch(() => {}); // best-effort - a dropped save just means less-precise resume next time

  const job = state.jobs.get(filename);
  if (job) job.playback_position = position; // keep local state fresh within this session too
}

el("video-modal-close").addEventListener("click", closeMediaModal);
videoModal.addEventListener("click", (e) => {
  if (e.target === videoModal) closeMediaModal();
});

el("ctx-delete-file").addEventListener("click", async () => {
  const filename = jobMenu.dataset.filename;
  closeMenus();
  if (!window.confirm(`Delete "${filename}"?\n\nThis removes the file from disk and can't be undone.`)) return;

  try {
    await fetch("/api/jobs/delete", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename }),
    });
    state.jobs.delete(filename);
    renderLedger();
  } catch (e) {
    window.alert("Couldn't reach the server to delete that file.");
  }
});

el("ctx-rename-file").addEventListener("click", async () => {
  const filename = jobMenu.dataset.filename;
  closeMenus();
  const proposed = window.prompt("Rename to:", filename);
  if (proposed === null) return;
  const trimmed = proposed.trim();
  if (!trimmed || trimmed === filename) return;

  try {
    const res = await fetch("/api/jobs/rename", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename, new_filename: trimmed }),
    });
    const data = await res.json();
    if (!res.ok) {
      window.alert(`Couldn't rename:\n${data.error || "Unknown error"}`);
      return;
    }
    loadJobsIntoMap(data.jobs);
    renderLedger();
  } catch (e) {
    window.alert("Couldn't reach the server to rename that file.");
  }
});

el("ctx-copy-link").addEventListener("click", async () => {
  const filename = jobMenu.dataset.filename;
  closeMenus();
  try {
    const res = await fetch("/api/history-search", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: filename }),
    });
    const data = await res.json();
    if (data.url) {
      await navigator.clipboard.writeText(data.url);
      flashStatus("Link copied to clipboard.");
    } else {
      flashStatus("No link found in history for this file.");
    }
  } catch (e) {
    flashStatus("Couldn't copy the link.");
  }
});

el("ctx-copy-filename").addEventListener("click", async () => {
  const filename = jobMenu.dataset.filename;
  closeMenus();
  try {
    await navigator.clipboard.writeText(filename);
    flashStatus(`Copied name: ${filename}`);
  } catch (e) {
    flashStatus("Couldn't copy the file name.");
  }
});

el("ctx-move-to-target").addEventListener("click", async () => {
  const filename = jobMenu.dataset.filename;
  closeMenus();
  try {
    const res = await fetch("/api/jobs/move-to-target", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename }),
    });
    const data = await res.json();
    if (!res.ok) {
      window.alert(`Couldn't move "${filename}":\n${data.error || "Unknown error"}`);
      return;
    }
    state.jobs.delete(filename);
    renderLedger();
    flashStatus(`Moved to target: ${filename}`);
  } catch (e) {
    window.alert("Couldn't reach the server to move that file.");
  }
});

el("ctx-open-folder").addEventListener("click", async () => {
  const filename = jobMenu.dataset.filename;
  closeMenus();
  try {
    const res = await fetch("/api/jobs/open-folder", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename }),
    });
    const data = await res.json();
    if (!res.ok) {
      window.alert(`Couldn't open Explorer:\n${data.error || "Unknown error"}`);
    }
  } catch (e) {
    window.alert("Couldn't reach the server to open Explorer.");
  }
});

function flashStatus(message) {
  const original = inputField.placeholder;
  inputField.placeholder = message;
  setTimeout(() => { inputField.placeholder = original; }, 2500);
}

let lastMenuOpenAt = 0;
const MENU_CLOSE_GRACE_MS = 400;

function positionMenu(menu, x, y) {
  menu.classList.remove("hidden");
  const rect = menu.getBoundingClientRect();
  const maxX = window.innerWidth - rect.width - 8;
  const maxY = window.innerHeight - rect.height - 8;
  menu.style.left = `${Math.min(x, Math.max(8, maxX))}px`;
  menu.style.top = `${Math.min(y, Math.max(8, maxY))}px`;
  lastMenuOpenAt = Date.now();
}

function closeMenus() {
  logoMenu.classList.add("hidden");
  jobMenu.classList.add("hidden");
  hideAllFlyouts();
}

document.addEventListener("click", (e) => {
  // Touch-to-mouse translation layers (e.g. Moonlight/Sunshine turning an
  // iPad long-press into a simulated right-click) can fire a stray ghost
  // click right after the long-press completes. Without this grace
  // period, that ghost click hits this same listener and closes the menu
  // before it's possible to actually tap an item in it.
  if (Date.now() - lastMenuOpenAt < MENU_CLOSE_GRACE_MS) return;
  const clickedInsideAMenu =
    logoMenu.contains(e.target) ||
    jobMenu.contains(e.target) ||
    ALL_FLYOUTS.some((flyout) => flyout.contains(e.target));
  if (!clickedInsideAMenu) closeMenus();
});

el("logo").addEventListener("contextmenu", (e) => {
  e.preventDefault();
  openLogoMenu(e.clientX, e.clientY);
});
// Right-clicking anywhere else in the top bar / empty ledger area also
// opens the logo/app menu, mirroring the desktop window's fallback
// context menu behavior.
app.addEventListener("contextmenu", (e) => {
  if (e.target.closest(".job-card")) return; // handled by job card's own listener
  if (e.target.closest(".ctx-menu")) return;
  e.preventDefault();
  openLogoMenu(e.clientX, e.clientY);
});

el("ctx-tag-toggle").addEventListener("click", () => {
  state.tagDomain = !state.tagDomain;
  el("ctx-tag-toggle").querySelector(".ctx-check").textContent = state.tagDomain ? "✓" : "";
});

el("ctx-m3u-toggle").addEventListener("click", () => {
  state.m3uSniffer = !state.m3uSniffer;
  el("ctx-m3u-toggle").querySelector(".ctx-check").textContent = state.m3uSniffer ? "✓" : "";
  if (state.m3uSniffer) {
    inputField.placeholder = "Paste a link, then press ENTER...";
  }
});

el("ctx-restart-app").addEventListener("click", async () => {
  closeMenus();
  const activeDownloads = Array.from(state.jobs.values()).filter((j) => j.status === "DOWNLOADING").length;
  const warning = activeDownloads > 0
    ? `Restart the app now?\n\n${activeDownloads} download(s) in progress will be interrupted.`
    : "Restart the app now?";
  if (!window.confirm(warning)) return;

  try {
    await fetch("/api/app/restart", { method: "POST" });
  } catch (e) {
    // The connection dying right as the process exits is expected here,
    // not a real failure - the reconnect logic below handles it either way.
  }
  inputField.disabled = true;
  inputField.placeholder = "Restarting... reconnecting automatically once it's back up.";
});

el("ctx-change-folder").addEventListener("click", () => {
  closeMenus();
  downloadFolderModal.open();
});

el("ctx-change-target").addEventListener("click", () => {
  closeMenus();
  targetFolderModal.open();
});

el("ctx-manage-programs").addEventListener("click", () => {
  closeMenus();
  if (!IS_LOCAL) {
    flashStatus(REMOTE_ONLY_TOOLTIP);
    return;
  }
  openExternalProgramsModal();
});

// ── External programs (Manage / Add / Edit) ────────────────────
function openExternalProgramsModal() {
  renderExternalProgramsList();
  externalProgramsModal.classList.remove("hidden");
}

function closeExternalProgramsModal() {
  externalProgramsModal.classList.add("hidden");
}

function renderExternalProgramsList() {
  externalProgramsList.innerHTML = "";
  if (state.externalPrograms.length === 0) {
    const empty = document.createElement("div");
    empty.className = "program-empty-note";
    empty.textContent = "No external programs added yet.";
    externalProgramsList.appendChild(empty);
    return;
  }
  for (const prog of state.externalPrograms) {
    const row = document.createElement("div");
    row.className = "program-row";

    const info = document.createElement("div");
    info.className = "program-row-info";
    const nameEl = document.createElement("div");
    nameEl.className = "program-row-name";
    nameEl.textContent = prog.name;
    const pathEl = document.createElement("div");
    pathEl.className = "program-row-path";
    pathEl.textContent = prog.path;
    pathEl.title = prog.path;
    info.appendChild(nameEl);
    info.appendChild(pathEl);

    const editBtn = document.createElement("button");
    editBtn.className = "program-edit-btn";
    editBtn.textContent = "Edit";
    editBtn.addEventListener("click", () => openProgramForm(prog));

    row.appendChild(info);
    row.appendChild(editBtn);
    externalProgramsList.appendChild(row);
  }
}

el("add-program-btn").addEventListener("click", () => openProgramForm(null));
el("external-programs-close-btn").addEventListener("click", closeExternalProgramsModal);
externalProgramsModal.addEventListener("click", (e) => {
  if (e.target === externalProgramsModal) closeExternalProgramsModal();
});

let editingProgramId = null;

function openProgramForm(prog) {
  editingProgramId = prog ? prog.id : null;
  programFormTitle.textContent = prog ? "Edit Program" : "Add Program";
  programNameInput.value = prog ? prog.name : "";
  programPathInput.value = prog ? prog.path : "";
  programArgsInput.value = prog ? (prog.args || "") : "";
  programFormError.classList.add("hidden");
  programDeleteBtn.classList.toggle("hidden", !prog);
  programFormModal.classList.remove("hidden");
  programNameInput.focus();
}

function closeProgramForm() {
  programFormModal.classList.add("hidden");
  editingProgramId = null;
}

function showProgramFormError(message) {
  programFormError.textContent = message;
  programFormError.classList.remove("hidden");
}

el("program-form-cancel-btn").addEventListener("click", closeProgramForm);
programFormModal.addEventListener("click", (e) => {
  if (e.target === programFormModal) closeProgramForm();
});

el("program-browse-btn").addEventListener("click", async () => {
  const btn = el("program-browse-btn");
  programFormError.classList.add("hidden");
  const original = btn.textContent;
  btn.textContent = "...";
  btn.disabled = true;
  try {
    const res = await fetch("/api/browse-program-file", { method: "POST" });
    const data = await res.json();
    if (!res.ok) {
      showProgramFormError(data.error || "Couldn't open a file browser.");
    } else if (data.path) {
      programPathInput.value = data.path;
    }
    // empty data.path just means the dialog was cancelled - do nothing
  } catch (e) {
    showProgramFormError("Couldn't reach the server.");
  } finally {
    btn.textContent = original;
    btn.disabled = false;
  }
});

async function saveProgramForm() {
  const name = programNameInput.value.trim();
  const path = programPathInput.value.trim();
  const args = programArgsInput.value;
  if (!name) { showProgramFormError("Program name can't be empty."); return; }
  if (!path) { showProgramFormError("Program path can't be empty."); return; }

  const endpoint = editingProgramId ? "/api/external-programs/update" : "/api/external-programs";
  const body = editingProgramId
    ? { id: editingProgramId, name, path, args }
    : { name, path, args };

  try {
    const res = await fetch(endpoint, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) { showProgramFormError(data.error || "Unknown error"); return; }
    state.externalPrograms = data.programs || [];
    renderExternalProgramsList();
    closeProgramForm();
  } catch (e) {
    showProgramFormError("Couldn't reach the server to save that program.");
  }
}

el("program-form-save-btn").addEventListener("click", saveProgramForm);

el("program-delete-btn").addEventListener("click", async () => {
  if (!editingProgramId) return;
  if (!window.confirm(`Remove "${programNameInput.value.trim()}" from external programs?`)) return;
  try {
    const res = await fetch("/api/external-programs/delete", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: editingProgramId }),
    });
    const data = await res.json();
    if (!res.ok) { showProgramFormError(data.error || "Unknown error"); return; }
    state.externalPrograms = data.programs || [];
    renderExternalProgramsList();
    closeProgramForm();
  } catch (e) {
    showProgramFormError("Couldn't reach the server to delete that program.");
  }
});

for (const input of [programNameInput, programPathInput, programArgsInput]) {
  input.addEventListener("keydown", (e) => {
    e.stopPropagation(); // don't trigger the global Enter/Escape pipeline handlers
    if (e.key === "Enter") saveProgramForm();
    if (e.key === "Escape") closeProgramForm();
  });
}

function createFolderModalController({
  modal, input, errorEl, recentWrap, recentList, browseBtn, cancelBtn, setBtn,
  browseEndpoint, applyEndpoint, applyBodyKey, removeEndpoint,
  getCurrentPath, getRecentDirs, setRecentDirs, onApplied,
}) {
  function open() {
    input.value = getCurrentPath() || "";
    errorEl.classList.add("hidden");
    renderRecent();
    modal.classList.remove("hidden");
    input.focus();
    input.select();
  }

  function close() {
    modal.classList.add("hidden");
  }

  function renderRecent() {
    recentList.innerHTML = "";
    const current = getCurrentPath() || "";
    const entries = getRecentDirs().filter((p) => p !== current);
    if (entries.length === 0) {
      recentWrap.classList.add("hidden");
      return;
    }
    recentWrap.classList.remove("hidden");
    for (const path of entries) {
      const row = document.createElement("div");
      row.className = "modal-recent-row";

      const item = document.createElement("div");
      item.className = "modal-recent-item";
      item.textContent = path;
      item.title = path;
      item.addEventListener("click", () => apply(path));

      const removeBtn = document.createElement("button");
      removeBtn.className = "modal-recent-remove";
      removeBtn.textContent = "✕";
      removeBtn.title = "Remove from recent folders";
      removeBtn.addEventListener("click", (e) => {
        e.stopPropagation(); // don't also trigger the row's own click-to-select
        removeRecent(path);
      });

      row.appendChild(item);
      row.appendChild(removeBtn);
      recentList.appendChild(row);
    }
  }

  async function removeRecent(path) {
    try {
      const res = await fetch(removeEndpoint, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path }),
      });
      const data = await res.json();
      if (res.ok) {
        setRecentDirs(data.recent_dirs || []);
        renderRecent();
      }
    } catch (e) {
      // best-effort - if this fails the entry just stays in the list, no harm done
    }
  }

  async function apply(path) {
    if (!path || !path.trim()) return;
    errorEl.classList.add("hidden");
    try {
      const res = await fetch(applyEndpoint, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [applyBodyKey]: path.trim() }),
      });
      const data = await res.json();
      if (!res.ok) {
        errorEl.textContent = data.error || "Unknown error";
        errorEl.classList.remove("hidden");
        return;
      }
      onApplied(data);
      close();
    } catch (e) {
      errorEl.textContent = "Couldn't reach the server to change the folder.";
      errorEl.classList.remove("hidden");
    }
  }

  cancelBtn.addEventListener("click", close);
  modal.addEventListener("click", (e) => {
    if (e.target === modal) close();
  });

  browseBtn.addEventListener("click", async () => {
    errorEl.classList.add("hidden");
    const originalLabel = browseBtn.textContent;
    browseBtn.textContent = "...";
    browseBtn.disabled = true;
    try {
      const res = await fetch(browseEndpoint, { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        errorEl.textContent = data.error || "Couldn't open a folder browser.";
        errorEl.classList.remove("hidden");
      } else if (data.path) {
        input.value = data.path;
      }
      // empty data.path just means the dialog was cancelled - do nothing
    } catch (e) {
      errorEl.textContent = "Couldn't reach the server.";
      errorEl.classList.remove("hidden");
    } finally {
      browseBtn.textContent = originalLabel;
      browseBtn.disabled = false;
    }
  });

  setBtn.addEventListener("click", () => apply(input.value));

  input.addEventListener("keydown", (e) => {
    e.stopPropagation(); // don't trigger the global Enter/Escape pipeline handlers
    if (e.key === "Enter") apply(input.value);
    if (e.key === "Escape") close();
  });

  return { open, close };
}

const downloadFolderModal = createFolderModalController({
  modal: folderModal, input: folderInput, errorEl: folderError,
  recentWrap: folderRecentWrap, recentList: folderRecentList,
  browseBtn: el("folder-browse-btn"), cancelBtn: el("folder-cancel-btn"), setBtn: el("folder-set-btn"),
  browseEndpoint: "/api/browse-folder", applyEndpoint: "/api/settings", applyBodyKey: "save_dir",
  removeEndpoint: "/api/settings/recent/remove",
  getCurrentPath: () => ctxSaveDir.title || "",
  getRecentDirs: () => state.recentDirs,
  setRecentDirs: (dirs) => { state.recentDirs = dirs; },
  onApplied: (data) => {
    setSaveDirDisplay(data.save_dir, data.free_space);
    state.recentDirs = data.recent_dirs || [];
    loadJobsIntoMap(data.jobs);
    renderLedger();
  },
});

const targetFolderModal = createFolderModalController({
  modal: targetFolderModalEl, input: el("target-folder-input"), errorEl: el("target-folder-error"),
  recentWrap: el("target-folder-recent-wrap"), recentList: el("target-folder-recent-list"),
  browseBtn: el("target-folder-browse-btn"), cancelBtn: el("target-folder-cancel-btn"), setBtn: el("target-folder-set-btn"),
  browseEndpoint: "/api/browse-target-folder", applyEndpoint: "/api/target-settings", applyBodyKey: "target_dir",
  removeEndpoint: "/api/target-settings/recent/remove",
  getCurrentPath: () => ctxTargetDir.title || "",
  getRecentDirs: () => state.recentTargetDirs,
  setRecentDirs: (dirs) => { state.recentTargetDirs = dirs; },
  onApplied: (data) => {
    setTargetDirDisplay(data.target_dir, data.free_space);
    state.recentTargetDirs = data.recent_dirs || [];
  },
});

resDropdown.addEventListener("click", (e) => e.stopPropagation());

// ── Mode buttons (Download / Find Link) ───────────────────────
function setAppModeDownload() {
  state.appMode = "DOWNLOAD";
  resetToReady();
  updateModeButtons();
}

function setAppModeFindLink() {
  state.appMode = "FIND_LINK";
  inputField.disabled = false;
  inputField.value = "";
  inputField.placeholder = "Paste target file name to find link...";
  inputField.focus();
  updateModeButtons();
}

function updateModeButtons() {
  const isDl = state.appMode === "DOWNLOAD";
  dlModeBtn.classList.toggle("active", isDl);
  findModeBtn.classList.toggle("active", !isDl);
  dlModeBtn.title = isDl ? "Download Mode Active" : "Switch to Download Mode (Ctrl+D)";
  findModeBtn.title = isDl ? "Switch to Find Link Mode (Ctrl+F)" : "Find Link Mode Active";
}

dlModeBtn.addEventListener("click", () => {
  if (state.current === "EDITING") {
    handleEnterPipeline(); // proceed with the download using the current input as the title
    return;
  }
  const currentValue = inputField.value.trim();
  const isValidUrl = /^https?:\/\//i.test(currentValue);
  if (isValidUrl && state.current === "READY") {
    state.appMode = "DOWNLOAD";
    updateModeButtons();
    beginDownloadPipeline(currentValue);
  } else {
    setAppModeDownload();
  }
});
findModeBtn.addEventListener("click", setAppModeFindLink);

// ── Mini mode toggle ───────────────────────────────────────────
el("ui-mode-btn").addEventListener("click", toggleMiniMode);
function toggleMiniMode() {
  state.isMiniMode = !state.isMiniMode;
  app.classList.toggle("mini", state.isMiniMode);
  updateMiniStats();
  inputField.focus();
}

// ── Control bar ────────────────────────────────────────────────
el("refresh-btn").addEventListener("click", async () => {
  const res = await fetch("/api/refresh", { method: "POST" });
  const data = await res.json();
  loadJobsIntoMap(data.jobs);
  renderLedger();
});


el("move-all-btn").addEventListener("click", async () => {
  const targetPath = ctxTargetDir.title || "";
  if (!targetPath) {
    window.alert("Set a target folder first (right-click the logo \u2192 Change Target Folder...).");
    return;
  }
  const completedCount = Array.from(state.jobs.values()).filter((j) => j.status === "DONE").length;
  if (completedCount === 0) {
    window.alert("Nothing completed to move.");
    return;
  }
  if (!window.confirm(`Move all ${completedCount} completed item(s) to:\n${targetPath}\n\nThis can't be undone.`)) {
    return;
  }

  try {
    const res = await fetch("/api/jobs/move-all-to-target", { method: "POST" });
    const data = await res.json();
    if (!res.ok) {
      window.alert(`Couldn't move files:\n${data.error || "Unknown error"}`);
      return;
    }
    loadJobsIntoMap(data.jobs);
    renderLedger();
    let message = `Moved ${data.moved.length} item(s) to target.`;
    if (data.failed && data.failed.length > 0) {
      message += `\n\n${data.failed.length} failed:\n` +
        data.failed.map((f) => `- ${f.filename}: ${f.error}`).join("\n");
    }
    window.alert(message);
  } catch (e) {
    window.alert("Couldn't reach the server to move files.");
  }
});

// ── Core pipeline: READY -> (fetch title | sniff m3u8) -> EDITING -> submit ──
function resetToReady() {
  state.appMode = "DOWNLOAD";
  state.current = "READY";
  state.stagedUrl = "";
  state.targetUrl = "";
  inputField.disabled = false;
  modeContainer.style.pointerEvents = "";
  inputField.value = "";
  inputField.placeholder = "Paste a link, then press ENTER...";
  updateModeButtons();
}

async function handleEnterPipeline() {
  if (state.current === "READY") {
    const typedValue = inputField.value.trim();
    if (/^https?:\/\//i.test(typedValue)) {
      await beginDownloadPipeline(typedValue);
    } else {
      inputField.value = "";
      inputField.placeholder = "No valid URL - paste a link and press ENTER.";
    }
  } else if (state.current === "EDITING") {
    const finalTitle = inputField.value.trim();
    if (finalTitle) {
      const cap = resDropdown.value;
      try {
        await fetch("/api/jobs", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            url: state.stagedUrl, filename: finalTitle, res_cap: cap,
            original_pasted_url: state.targetUrl,
          }),
        });
      } catch (e) { /* job_added will simply never arrive */ }
      resetToReady();
    }
  }
}

async function beginDownloadPipeline(url) {
  state.targetUrl = url;
  inputField.disabled = true;
  modeContainer.style.pointerEvents = "none";

  if (state.m3uSniffer) {
    state.current = "INTERCEPTING";
    inputField.value = "Sniffing m3u...";
    try {
      const res = await fetch("/api/find-link", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, tag_domain: state.tagDomain }),
      });
      if (!res.ok) {
        const err = await res.json();
        handleInterceptionFailure(err.error || "Unknown error");
        return;
      }
      const data = await res.json();
      handleIntercepted(data.stream_url, data.suggested_title);
    } catch (e) {
      handleInterceptionFailure(String(e));
    }
  } else {
    state.current = "FETCHING";
    state.stagedUrl = url;
    inputField.value = "Fetching Title metadata...";
    try {
      const res = await fetch("/api/fetch-title", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, tag_domain: state.tagDomain }),
      });
      const data = await res.json();
      promptTitleEdit(data.title);
    } catch (e) {
      promptTitleEdit("Unknown Title");
    }
  }
}

function promptTitleEdit(title) {
  state.current = "EDITING";
  inputField.disabled = false;
  modeContainer.style.pointerEvents = "";
  inputField.value = title;
  inputField.focus();
  inputField.setSelectionRange(0, 0);
}

function handleIntercepted(streamUrl, suggestedTitle) {
  state.stagedUrl = streamUrl;
  state.current = "EDITING";
  inputField.disabled = false;
  modeContainer.style.pointerEvents = "";
  inputField.value = suggestedTitle;
  inputField.focus();
  inputField.setSelectionRange(0, 0);
}

function handleInterceptionFailure(message) {
  resetToReady();
  inputField.placeholder = `Failed: ${message}`;
}

async function executeHistorySearch() {
  const query = inputField.value.trim();
  if (!query) return "";
  try {
    const res = await fetch("/api/history-search", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    });
    const data = await res.json();
    return data.url || "";
  } catch (e) {
    return "";
  }
}

// ── Keyboard handling (mirrors keyPressEvent) ─────────────────
let baseFontSize = 12;

document.addEventListener("keydown", async (e) => {
  if (!folderModal.classList.contains("hidden")) return; // modal has its own handler
  if (!targetFolderModalEl.classList.contains("hidden")) return; // ditto
  if (!externalProgramsModal.classList.contains("hidden")) {
    if (e.key === "Escape") closeExternalProgramsModal();
    return;
  }
  if (!programFormModal.classList.contains("hidden")) return; // its inputs have their own handler

  if (!videoModal.classList.contains("hidden")) {
    if (e.key === "Escape") closeMediaModal();
    return;
  }

  if (e.ctrlKey || e.metaKey) {
    if (e.key === "=" || e.key === "+") {
      baseFontSize = Math.min(24, baseFontSize + 1);
      document.documentElement.style.setProperty("--base-font", `${baseFontSize}px`);
      e.preventDefault();
      return;
    }
    if (e.key === "-") {
      baseFontSize = Math.max(8, baseFontSize - 1);
      document.documentElement.style.setProperty("--base-font", `${baseFontSize}px`);
      e.preventDefault();
      return;
    }
    if (e.key.toLowerCase() === "f") { setAppModeFindLink(); e.preventDefault(); return; }
    if (e.key.toLowerCase() === "d") { setAppModeDownload(); e.preventDefault(); return; }
    if (e.key.toLowerCase() === "m") { toggleMiniMode(); e.preventDefault(); return; }
  }

  if (e.key === "Escape") {
    if (["EDITING", "FETCHING", "INTERCEPTING"].includes(state.current)) {
      resetToReady();
      inputField.placeholder = "Cancelled. Paste a link and press ENTER.";
      setTimeout(() => { inputField.placeholder = "Paste a link, then press ENTER..."; }, 2500);
    } else {
      resetToReady();
    }
    return;
  }

  if (e.key === "Enter") {
    if (document.activeElement !== inputField) return;
    if (state.appMode === "FIND_LINK") {
      const found = await executeHistorySearch();
      if (found) {
        inputField.value = found;
        inputField.focus();
        inputField.select();
      } else {
        inputField.value = "";
        inputField.placeholder = "No matches found in history.";
      }
    } else {
      handleEnterPipeline();
    }
  }
});

boot();
