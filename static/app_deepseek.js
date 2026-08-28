// Stash DLP Web — ported from stash_dlp.py's YtdlpManagerApp.
// State machine (READY / EDITING / FETCHING / INTERCEPTING) and app_mode
// (DOWNLOAD / SEARCH_HISTORY) mirror the desktop app; URLs are pasted
// manually into the input field rather than auto-read from the
// clipboard, and window-chrome pieces differ due to browser sandboxing.

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
const navToggleBtn = el("nav-toggle-btn");
const navTray = el("nav-tray");

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
  appMode: "DOWNLOAD",
  currentView: "downloads",
  current: "READY",
  targetUrl: "",
  stagedUrl: "",
  tagDomain: true,
  m3uSniffer: false,
  autoM3uRetry: true,
  autoConfirmTitles: false,
  ytdlpDefaultArgs: "",
  ytdlpDomainArgs: {},
  jobs: new Map(),
  saveDirPath: "",
  historyEntries: [],
  ws: null,
  recentDirs: [],
  recentTargetDirs: [],
  externalPrograms: [],
  filterText: "",
  audioOnlyFilter: false,
  sortField: "added",
  sortDir: "desc",
  selectionMode: false,
  selectedFilenames: new Set(),
  renderedCards: new Map(),
  renderedCardSigs: new Map(),
  encodeJobs: new Map(),
  encodeCapabilities: null,
  encodeSources: [],
  encodeFilterText: "",
  encodeSortField: "added",
  encodeSortDir: "desc",
  encodeSourceInfo: null,
  encodeEstimateSeq: 0,
  encodeProbeSeq: 0,
  clipboardUrl: "",
};

const inputField = el("input-field");
const downloadSubmitBtn = el("download-submit-btn");
const app = el("app");
const queueList = el("queue-list");
const gearBtn = el("gear-btn");
const logoImg = el("logo");
const dlModeBtn = el("dl-mode-btn");
const historyModeBtn = el("history-mode-btn");
const encodeModeBtn = el("encode-mode-btn");
const modeContainer = el("mode-container");
const resDropdown = el("res-dropdown");
const ctxVersion = el("ctx-version");
const ctxSaveDir = el("ctx-save-dir");
const ctxChangeFolder = el("ctx-change-folder");
const logoMenu = el("logo-menu");
const jobMenu = el("job-menu");
const historyMenu = el("history-menu");
const folderModal = el("folder-modal");
const stashMenuFlyout = el("stash-menu-flyout");
const stashStatusBtn = el("stash-status-btn");
const stashImportModal = el("stash-import-modal");
const stashImportInput = el("stash-import-input");
const stashImportError = el("stash-import-error");
const stashTagModal = el("stash-tag-modal");
const stashTagInput = el("stash-tag-input");
const stashTagError = el("stash-tag-error");
const stashTagResultsModal = el("stash-tag-results-modal");
const stashTagResultsTitle = el("stash-tag-results-title");
const stashTagResultsList = el("stash-tag-results-list");
const stashTagRecentWrap = el("stash-tag-recent-wrap");
const stashTagRecentList = el("stash-tag-recent-list");
const folderInput = el("folder-input");
const folderError = el("folder-error");
const folderRecentWrap = el("folder-recent-wrap");
const folderRecentList = el("folder-recent-list");
const ctxTargetDir = el("ctx-target-dir");
const folderStatusRow = el("folder-status-row");
const folderStatusSave = el("folder-status-save");
const folderStatusTarget = el("folder-status-target");
const targetFolderModalEl = el("target-folder-modal");
const dlFolderQuickMenu = el("dl-folder-quickmenu");
const dlFolderQuickList = el("dl-folder-quickmenu-list");
const targetFolderQuickMenu = el("target-folder-quickmenu");
const targetFolderQuickList = el("target-folder-quickmenu-list");
const reencodeChoiceModal = el("reencode-choice-modal");
const reencodeChoiceFilename = el("reencode-choice-filename");
const reencodeChoiceOriginalSize = el("reencode-choice-original-size");
const reencodeChoiceOriginalRes = el("reencode-choice-original-res");
const reencodeChoiceReencodedSize = el("reencode-choice-reencoded-size");
const reencodeChoiceReencodedRes = el("reencode-choice-reencoded-res");
const reencodeChoiceCancelBtn = el("reencode-choice-cancel-btn");
const reencodeChoiceOriginalBtn = el("reencode-choice-original-btn");
const reencodeChoiceReencodedBtn = el("reencode-choice-reencoded-btn");
const reencodeChoiceTitle = el("reencode-choice-title");
const reencodeChoiceReencodedLabel = el("reencode-choice-reencoded-label");
const videoModal = el("video-modal");
const videoPlayer = el("video-player");
const videoModalTitle = el("video-modal-title");
const audioPlayerWrap = el("audio-player-wrap");
const audioPlayer = el("audio-player");
const syncAudioModal = el("sync-audio-modal");
const syncAudioPlayer = el("sync-audio-player");
const syncAudioTitle = el("sync-audio-title");
const syncAudioFilename = el("sync-audio-filename");
const syncAudioStageBadge = el("sync-audio-stage-badge");
const syncAudioDelayInput = el("sync-audio-delay-input");
const syncAudioClipDurationInput = el("sync-audio-clip-duration-input");
const syncAudioStatus = el("sync-audio-status");
const syncAudioPrimaryBtn = el("sync-audio-primary-btn");
const syncAudioRedoClipBtn = el("sync-audio-redo-clip-btn");
const syncAudioConfirmBtn = el("sync-audio-confirm-btn");
const syncAudioDiscardBtn = el("sync-audio-discard-btn");
const syncAudioAcceptBtn = el("sync-audio-accept-btn");
const syncAudioCancelBtn = el("sync-audio-cancel-btn");
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
const urlArgsBtn = el("url-args-btn");
const urlArgsFlyout = el("url-args-flyout");
const urlArgsDomainLabel = el("url-args-domain-label");
const urlArgsQuickInput = el("url-args-quick-input");
const ytdlpArgsModal = el("ytdlp-args-modal");
const ytdlpDefaultArgsInput = el("ytdlp-default-args-input");
const ytdlpDefaultArgsError = el("ytdlp-default-args-error");
const ytdlpDomainArgsList = el("ytdlp-domain-args-list");
const ytdlpDomainArgsFormModal = el("ytdlp-domain-args-form-modal");
const ytdlpDomainArgsFormTitle = el("ytdlp-domain-args-form-title");
const ytdlpDomainInput = el("ytdlp-domain-input");
const ytdlpDomainArgsInput = el("ytdlp-domain-args-input");
const ytdlpDomainArgsFormError = el("ytdlp-domain-args-form-error");
const ytdlpDomainArgsDeleteBtn = el("ytdlp-domain-args-delete-btn");

function openStashImportModal() {
  stashImportError.classList.add("hidden");
  stashImportError.textContent = "";
  stashImportInput.value = "";
  stashImportModal.classList.remove("hidden");
  setTimeout(() => stashImportInput.focus(), 0);
}

function closeStashImportModal() {
  stashImportModal.classList.add("hidden");
}

async function importFromStash() {
  const scene = stashImportInput.value.trim();
  if (!scene) {
    stashImportError.textContent = "Enter a Stash scene URL or scene ID.";
    stashImportError.classList.remove("hidden");
    return;
  }
  const btn = el("stash-import-btn-confirm");
  btn.disabled = true;
  btn.textContent = "Importing...";
  stashImportError.classList.add("hidden");
  try {
    const res = await fetch("/api/import/stash", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scene }),
    });
    const data = await res.json();
    if (!res.ok) {
      stashImportError.textContent = data.error || "Import failed.";
      stashImportError.classList.remove("hidden");
      return;
    }
    if (data.job) state.jobs.set(data.job.filename, data.job);
    renderLedger();
    closeStashImportModal();
    flashStatus(`Imported from Stash: ${data.job.filename}`);
  } catch (e) {
    stashImportError.textContent = "Couldn't reach the server.";
    stashImportError.classList.remove("hidden");
  } finally {
    btn.disabled = false;
    btn.textContent = "Import";
  }
}

el("stash-import-cancel-btn").addEventListener("click", closeStashImportModal);
el("stash-import-btn-confirm").addEventListener("click", importFromStash);
stashImportInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") importFromStash();
  else if (e.key === "Escape") closeStashImportModal();
});

// ── Stash menu (Stash button -> Import from Stash / Check Tag) ──
function openStashMenuFlyout() {
  if (!stashMenuFlyout.classList.contains("hidden")) {
    stashMenuFlyout.classList.add("hidden");
    return;
  }
  closeOtherFlyouts(stashMenuFlyout);
  positionDropdownBelow(stashMenuFlyout, inputField);
}
stashStatusBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  openStashMenuFlyout();
});
el("stash-menu-import").addEventListener("click", () => {
  stashMenuFlyout.classList.add("hidden");
  openStashImportModal();
});
el("stash-menu-check-tag").addEventListener("click", () => {
  stashMenuFlyout.classList.add("hidden");
  openStashTagModal();
});
el("stash-menu-largest-files").addEventListener("click", () => {
  stashMenuFlyout.classList.add("hidden");
  openStashLargestFiles();
});

function openStashTagModal() {
  stashTagError.classList.add("hidden");
  stashTagError.textContent = "";
  stashTagInput.value = "";
  stashTagModal.classList.remove("hidden");
  setTimeout(() => stashTagInput.focus(), 0);
  refreshStashTagRecent();
}

function renderStashTagRecent(tags) {
  stashTagRecentList.innerHTML = "";
  if (!tags || tags.length === 0) {
    stashTagRecentWrap.classList.add("hidden");
    return;
  }
  stashTagRecentWrap.classList.remove("hidden");
  for (const tag of tags) {
    const chip = document.createElement("div");
    chip.className = "stash-tag-chip";
    chip.textContent = tag;
    chip.title = tag;
    chip.addEventListener("click", () => {
      stashTagInput.value = tag;
      checkStashTag();
    });
    stashTagRecentList.appendChild(chip);
  }
}

async function refreshStashTagRecent() {
  try {
    const res = await fetch("/api/stash/recent-tags");
    if (!res.ok) return;
    const data = await res.json();
    renderStashTagRecent(data.recent_tags);
  } catch (e) {
    // Non-critical - just skip showing the recent-tags chips this time.
  }
}

function closeStashTagModal() {
  stashTagModal.classList.add("hidden");
}

async function checkStashTag() {
  const tag = stashTagInput.value.trim();
  if (!tag) {
    stashTagError.textContent = "Enter a Stash tag name.";
    stashTagError.classList.remove("hidden");
    return;
  }
  const btn = el("stash-tag-btn-confirm");
  btn.disabled = true;
  btn.textContent = "Checking...";
  stashTagError.classList.add("hidden");
  try {
    const res = await fetch("/api/stash/check-tag", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tag }),
    });
    const data = await res.json();
    if (!res.ok) {
      stashTagError.textContent = data.error || "Check failed.";
      stashTagError.classList.remove("hidden");
      return;
    }
    closeStashTagModal();
    openStashTagResultsModal(data);
  } catch (e) {
    stashTagError.textContent = "Couldn't reach the server.";
    stashTagError.classList.remove("hidden");
  } finally {
    btn.disabled = false;
    btn.textContent = "Check";
  }
}

el("stash-tag-cancel-btn").addEventListener("click", closeStashTagModal);
el("stash-tag-btn-confirm").addEventListener("click", checkStashTag);
stashTagInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") checkStashTag();
  else if (e.key === "Escape") closeStashTagModal();
});

let currentStashTagCheck = null; // { id, name } of the most recently checked tag, for tagging imports

function openStashScenesResultsModal(title, scenes, tagContext, emptyMessage) {
  currentStashTagCheck = tagContext;
  stashTagResultsTitle.textContent = title;
  stashTagResultsList.innerHTML = "";

  if (!scenes || scenes.length === 0) {
    const empty = document.createElement("div");
    empty.className = "program-empty-note";
    empty.textContent = emptyMessage || "No scenes found.";
    stashTagResultsList.appendChild(empty);
  } else {
    for (const scene of scenes) {
      const row = document.createElement("div");
      row.className = "stash-scene-row";

      const info = document.createElement("div");
      info.className = "stash-scene-row-info";
      const titleEl = document.createElement("div");
      titleEl.className = "stash-scene-row-title";
      titleEl.textContent = scene.title;
      const pathEl = document.createElement("div");
      pathEl.className = "stash-scene-row-path";
      pathEl.textContent = scene.path || scene.url;
      pathEl.title = scene.path || scene.url;
      info.appendChild(titleEl);
      info.appendChild(pathEl);
      if (scene.size_label) {
        const sizeEl = document.createElement("div");
        sizeEl.className = "stash-scene-row-size";
        sizeEl.textContent = scene.size_label;
        info.appendChild(sizeEl);
      }

      const btnGroup = document.createElement("div");
      btnGroup.className = "stash-scene-row-btns";

      const openBtn = document.createElement("button");
      openBtn.className = "stash-scene-open-btn";
      openBtn.textContent = "Open";
      openBtn.addEventListener("click", () => window.open(scene.url, "_blank"));

      const importBtn = document.createElement("button");
      importBtn.className = "stash-scene-open-btn stash-scene-import-btn";
      importBtn.textContent = "Import";
      importBtn.addEventListener("click", () => importStashSceneFromResults(scene, importBtn));

      btnGroup.appendChild(openBtn);
      btnGroup.appendChild(importBtn);

      row.appendChild(info);
      row.appendChild(btnGroup);
      stashTagResultsList.appendChild(row);
    }
  }

  stashTagResultsModal.classList.remove("hidden");
}

function openStashTagResultsModal(data) {
  openStashScenesResultsModal(
    `Scenes tagged "${data.tag_name}" (${data.count})`,
    data.scenes,
    { id: data.tag_id, name: data.tag_name },
    "No scenes found with this tag."
  );
}

async function openStashLargestFiles() {
  try {
    const res = await fetch("/api/stash/largest-files");
    const data = await res.json();
    if (!res.ok) {
      flashStatus(data.error || "Couldn't load largest files.");
      return;
    }
    openStashScenesResultsModal(
      `50 Largest Files (${data.count})`,
      data.scenes,
      null,
      "No scenes found."
    );
  } catch (e) {
    flashStatus("Couldn't reach the server.");
  }
}

async function importStashSceneFromResults(scene, btn) {
  btn.disabled = true;
  btn.textContent = "Importing...";
  try {
    const res = await fetch("/api/import/stash", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        scene: scene.id,
        tag_id: currentStashTagCheck?.id || null,
        tag_name: currentStashTagCheck?.name || null,
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      btn.disabled = false;
      btn.textContent = "Import";
      flashStatus(data.error || "Import failed.");
      return;
    }
    if (data.job) state.jobs.set(data.job.filename, data.job);
    renderLedger();
    btn.textContent = "Imported";
    flashStatus(`Imported from Stash: ${data.job.filename}`);
  } catch (e) {
    btn.disabled = false;
    btn.textContent = "Import";
    flashStatus("Couldn't reach the server.");
  }
}

function closeStashTagResultsModal() {
  stashTagResultsModal.classList.add("hidden");
}

el("stash-tag-results-close-btn").addEventListener("click", closeStashTagResultsModal);
stashTagResultsModal.addEventListener("click", (e) => {
  if (e.target === stashTagResultsModal) closeStashTagResultsModal();
});

// ── Boot ──────────────────────────────────────────────────────
async function boot() {
  await refreshVersion();
  await refreshSaveDir();
  await refreshTargetDir();
  await refreshExternalPrograms();
  await refreshDownloadPrefs();
  await refreshYtdlpArgs();
  updateUrlArgsChip();
  await loadJobsSnapshot();
  await loadEncodeJobsSnapshot();
  connectWebSocket();
  applyLocalOnlyUI();
  inputField.setPlaceholderText = null; // n/a, kept for readability
  inputField.placeholder = "Paste a link, then press ENTER...";
  inputField.focus();
  refreshStashStatus();
  setInterval(refreshStashStatus, 30000);
}

// Polls Stash's reachability so the Stash button in the top row only
// shows up when Stash is actually running - avoids a dead button that
// just errors out on every click when Stash is off.
async function refreshStashStatus() {
  try {
    const res = await fetch("/api/stash/status");
    const data = await res.json();
    stashStatusBtn.classList.toggle("hidden", !data.running);
  } catch (e) {
    stashStatusBtn.classList.add("hidden");
  }
}

// Static for the life of the page (IS_LOCAL doesn't change at runtime),
// so this just needs to run once at boot rather than on every menu open.
function applyLocalOnlyUI() {
  if (IS_LOCAL) return;
  for (const id of ["ctx-manage-programs", "ctx-open-with", "flyout-manage-programs", "ctx-history-open-location"]) {
    const item = el(id);
    item.classList.add("ctx-disabled");
    item.title = REMOTE_ONLY_TOOLTIP;
  }
  for (const id of ["encode-source-browse-btn", "open-converted-btn"]) {
    const btn = el(id);
    btn.disabled = true;
    btn.title = REMOTE_ONLY_TOOLTIP;
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

async function refreshDownloadPrefs() {
  try {
    const res = await fetch("/api/download-prefs");
    const data = await res.json();
    resDropdown.value = data.quality || "720p";
    state.tagDomain = data.tag_domain !== false;
    state.m3uSniffer = !!data.m3u_sniffer;
    state.autoM3uRetry = data.auto_m3u_retry !== false;
    state.autoConfirmTitles = !!data.auto_confirm_titles;
  } catch (e) {
    // Backend unreachable at boot - just keep the hard-coded defaults
    // already baked into the HTML/state.
  }
  el("ctx-tag-toggle").querySelector(".ctx-check").textContent = state.tagDomain ? "✓" : "";
  el("ctx-m3u-toggle").querySelector(".ctx-check").textContent = state.m3uSniffer ? "✓" : "";
  el("ctx-auto-m3u-retry-toggle").querySelector(".ctx-check").textContent = state.autoM3uRetry ? "✓" : "";
  el("ctx-auto-confirm-titles-toggle").querySelector(".ctx-check").textContent = state.autoConfirmTitles ? "✓" : "";
}

async function refreshYtdlpArgs() {
  try {
    const res = await fetch("/api/ytdlp-args");
    const data = await res.json();
    state.ytdlpDefaultArgs = data.default_args || "";
    state.ytdlpDomainArgs = data.domain_args || {};
  } catch (e) {
    // Backend unreachable at boot - keep empty defaults, not worth
    // surfacing an error over what's a purely optional feature.
  }
}

// Mirrors backend get_domain() (ytdlp_utils.py) closely enough for a
// pure client-side lookup key: strip a www/www2/m subdomain, take the
// first label. Doesn't need to be byte-identical to the Python version
// since it's only ever used to look up state.ytdlpDomainArgs (already
// fetched from the server) for the chip's lit/unlit state - the
// authoritative save/apply path always goes back through the backend.
function getDomainClientSide(url) {
  try {
    const host = new URL(url.trim()).hostname || "";
    return host.replace(/^(www\d?|m)\./, "").split(".")[0];
  } catch (e) {
    return "";
  }
}

function updateUrlArgsChip() {
  const domain = getDomainClientSide(inputField.value);
  if (!domain) {
    urlArgsBtn.classList.remove("active");
    urlArgsBtn.title = "Paste a URL to set yt-dlp args for that site";
    return;
  }
  const args = state.ytdlpDomainArgs[domain] || "";
  urlArgsBtn.classList.toggle("active", !!args);
  urlArgsBtn.title = args
    ? `Custom yt-dlp args for "${domain}": ${args}`
    : `No custom yt-dlp args for "${domain}" yet - click to add`;
}

inputField.addEventListener("input", updateUrlArgsChip);

urlArgsBtn.addEventListener("click", () => {
  if (!urlArgsFlyout.classList.contains("hidden")) {
    urlArgsFlyout.classList.add("hidden");
    return;
  }
  const domain = getDomainClientSide(inputField.value);
  urlArgsFlyout.dataset.domain = domain;
  if (!domain) {
    urlArgsDomainLabel.textContent = "Paste a URL first";
    urlArgsQuickInput.value = "";
    urlArgsQuickInput.disabled = true;
  } else {
    urlArgsDomainLabel.textContent = `Args for "${domain}"`;
    urlArgsQuickInput.value = state.ytdlpDomainArgs[domain] || "";
    urlArgsQuickInput.disabled = false;
  }
  closeOtherFlyouts(urlArgsFlyout);
  positionFlyoutNextTo(urlArgsFlyout, urlArgsBtn);
});

async function saveUrlArgsQuick() {
  const domain = urlArgsFlyout.dataset.domain;
  if (!domain) return;
  try {
    const res = await fetch("/api/ytdlp-args/domain", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ domain, args: urlArgsQuickInput.value }),
    });
    const data = await res.json();
    if (!res.ok) { flashStatus(data.error || "Couldn't save args."); return; }
    state.ytdlpDefaultArgs = data.default_args || "";
    state.ytdlpDomainArgs = data.domain_args || {};
    updateUrlArgsChip();
    urlArgsFlyout.classList.add("hidden");
    flashStatus(`Saved yt-dlp args for "${domain}"`);
  } catch (e) {
    flashStatus("Couldn't reach the server to save args.");
  }
}
el("url-args-quick-save").addEventListener("click", saveUrlArgsQuick);
urlArgsQuickInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") saveUrlArgsQuick();
});

el("url-args-manage-all").addEventListener("click", () => {
  urlArgsFlyout.classList.add("hidden");
  openYtdlpArgsModal();
});

el("ctx-manage-ytdlp-args").addEventListener("click", () => {
  closeMenus();
  openYtdlpArgsModal();
});

// ── yt-dlp args management modal (default args + per-domain list) ──
function openYtdlpArgsModal() {
  ytdlpDefaultArgsInput.value = state.ytdlpDefaultArgs;
  ytdlpDefaultArgsError.classList.add("hidden");
  renderYtdlpDomainArgsList();
  ytdlpArgsModal.classList.remove("hidden");
}

function closeYtdlpArgsModal() {
  ytdlpArgsModal.classList.add("hidden");
}

function renderYtdlpDomainArgsList() {
  ytdlpDomainArgsList.innerHTML = "";
  const domains = Object.keys(state.ytdlpDomainArgs).sort();
  if (domains.length === 0) {
    const empty = document.createElement("div");
    empty.className = "program-empty-note";
    empty.textContent = "No site-specific rules added yet.";
    ytdlpDomainArgsList.appendChild(empty);
    return;
  }
  for (const domain of domains) {
    const row = document.createElement("div");
    row.className = "program-row";

    const info = document.createElement("div");
    info.className = "program-row-info";
    const nameEl = document.createElement("div");
    nameEl.className = "program-row-name";
    nameEl.textContent = domain;
    const argsEl = document.createElement("div");
    argsEl.className = "program-row-path";
    argsEl.textContent = state.ytdlpDomainArgs[domain];
    argsEl.title = state.ytdlpDomainArgs[domain];
    info.appendChild(nameEl);
    info.appendChild(argsEl);

    const editBtn = document.createElement("button");
    editBtn.className = "program-edit-btn";
    editBtn.textContent = "Edit";
    editBtn.addEventListener("click", () => openYtdlpDomainArgsForm(domain));

    row.appendChild(info);
    row.appendChild(editBtn);
    ytdlpDomainArgsList.appendChild(row);
  }
}

el("add-ytdlp-domain-args-btn").addEventListener("click", () => openYtdlpDomainArgsForm(null));
el("ytdlp-args-close-btn").addEventListener("click", closeYtdlpArgsModal);
ytdlpArgsModal.addEventListener("click", (e) => {
  if (e.target === ytdlpArgsModal) closeYtdlpArgsModal();
});

async function saveYtdlpDefaultArgs() {
  try {
    const res = await fetch("/api/ytdlp-args/default", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ args: ytdlpDefaultArgsInput.value }),
    });
    const data = await res.json();
    if (!res.ok) {
      ytdlpDefaultArgsError.textContent = data.error || "Unknown error";
      ytdlpDefaultArgsError.classList.remove("hidden");
      return;
    }
    state.ytdlpDefaultArgs = data.default_args || "";
    state.ytdlpDomainArgs = data.domain_args || {};
    ytdlpDefaultArgsError.classList.add("hidden");
  } catch (e) {
    ytdlpDefaultArgsError.textContent = "Couldn't reach the server.";
    ytdlpDefaultArgsError.classList.remove("hidden");
  }
}
ytdlpDefaultArgsInput.addEventListener("change", saveYtdlpDefaultArgs);

let editingYtdlpDomain = null;

function openYtdlpDomainArgsForm(domain) {
  editingYtdlpDomain = domain;
  ytdlpDomainArgsFormTitle.textContent = domain ? "Edit Site Rule" : "Add Site Rule";
  ytdlpDomainInput.value = domain || "";
  // Domain is the storage key - don't let an edit rename it out from
  // under state.ytdlpDomainArgs; delete + re-add covers that instead.
  ytdlpDomainInput.disabled = !!domain;
  ytdlpDomainArgsInput.value = domain ? (state.ytdlpDomainArgs[domain] || "") : "";
  ytdlpDomainArgsFormError.classList.add("hidden");
  ytdlpDomainArgsDeleteBtn.classList.toggle("hidden", !domain);
  ytdlpDomainArgsFormModal.classList.remove("hidden");
  (domain ? ytdlpDomainArgsInput : ytdlpDomainInput).focus();
}

function closeYtdlpDomainArgsForm() {
  ytdlpDomainArgsFormModal.classList.add("hidden");
  editingYtdlpDomain = null;
  ytdlpDomainInput.disabled = false;
}

function showYtdlpDomainArgsFormError(message) {
  ytdlpDomainArgsFormError.textContent = message;
  ytdlpDomainArgsFormError.classList.remove("hidden");
}

el("ytdlp-domain-args-form-cancel-btn").addEventListener("click", closeYtdlpDomainArgsForm);
ytdlpDomainArgsFormModal.addEventListener("click", (e) => {
  if (e.target === ytdlpDomainArgsFormModal) closeYtdlpDomainArgsForm();
});

async function saveYtdlpDomainArgsForm() {
  const domain = ytdlpDomainInput.value.trim();
  const args = ytdlpDomainArgsInput.value;
  if (!domain) { showYtdlpDomainArgsFormError("Domain can't be empty."); return; }
  try {
    const res = await fetch("/api/ytdlp-args/domain", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ domain, args }),
    });
    const data = await res.json();
    if (!res.ok) { showYtdlpDomainArgsFormError(data.error || "Unknown error"); return; }
    state.ytdlpDefaultArgs = data.default_args || "";
    state.ytdlpDomainArgs = data.domain_args || {};
    renderYtdlpDomainArgsList();
    updateUrlArgsChip();
    closeYtdlpDomainArgsForm();
  } catch (e) {
    showYtdlpDomainArgsFormError("Couldn't reach the server to save that rule.");
  }
}
el("ytdlp-domain-args-form-save-btn").addEventListener("click", saveYtdlpDomainArgsForm);

el("ytdlp-domain-args-delete-btn").addEventListener("click", async () => {
  if (!editingYtdlpDomain) return;
  if (!window.confirm(`Remove the yt-dlp args rule for "${editingYtdlpDomain}"?`)) return;
  try {
    const res = await fetch("/api/ytdlp-args/domain/delete", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ domain: editingYtdlpDomain }),
    });
    const data = await res.json();
    if (!res.ok) { showYtdlpDomainArgsFormError(data.error || "Unknown error"); return; }
    state.ytdlpDefaultArgs = data.default_args || "";
    state.ytdlpDomainArgs = data.domain_args || {};
    renderYtdlpDomainArgsList();
    updateUrlArgsChip();
    closeYtdlpDomainArgsForm();
  } catch (e) {
    showYtdlpDomainArgsFormError("Couldn't reach the server to delete that rule.");
  }
});

function saveDownloadPrefs() {
  fetch("/api/download-prefs", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      quality: resDropdown.value,
      tag_domain: state.tagDomain,
      m3u_sniffer: state.m3uSniffer,
      auto_m3u_retry: state.autoM3uRetry,
      auto_confirm_titles: state.autoConfirmTitles,
    }),
  }).catch((e) => { /* best-effort - not worth surfacing a UI error over */ });
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
    folderStatusSave.querySelector(".folder-status-path").textContent = "(unavailable)";
    folderStatusSave.title = "";
  }
}

function setSaveDirDisplay(path, freeSpace) {
  const { compact, tooltip } = compactFolderDisplay(path, freeSpace);
  ctxSaveDir.querySelector(".ctx-folder-path").textContent = path ? `Folder: ${compact}` : "Folder: (unavailable)";
  ctxSaveDir.querySelector("#ctx-save-free").textContent = "";
  ctxSaveDir.title = tooltip;

  state.saveDirPath = path || "";

  folderStatusSave.querySelector(".folder-status-path").textContent = path ? compact : "(unavailable)";
  folderStatusSave.title = tooltip;
}

// Builds a display-only full path for a job's file. Not used for any
// file operation - just so the card can show where the file lives (or
// would land, if it's still downloading/pending) without a server
// round-trip. Matches whichever separator style the folder path
// already uses, so it doesn't look out of place on Windows vs. *nix.
function joinDisplayPath(dir, filename, ext) {
  if (!dir) return filename;
  const sep = dir.includes("\\") ? "\\" : "/";
  const trimmedDir = dir.replace(/[\\/]+$/, "");
  const name = ext ? `${filename}.${ext.toLowerCase()}` : filename;
  return `${trimmedDir}${sep}${name}`;
}

// Long folder paths (a GitHub-cloned project several levels deep, a
// user profile with a long name, etc.) otherwise either overflow the
// toolbar/menu or get silently clipped by CSS ellipsis from whichever
// end happens to overflow first - neither shows the one thing that
// actually identifies the folder day-to-day: which drive it's on and
// what it's actually named. This collapses the middle instead, e.g.
// "C:\Users\Phil\Documents\GitHub\project\downloads" -> "C:\...\downloads",
// and folds the already-available free-space label in alongside it
// rather than as a separate element, e.g. "C:\...\downloads (123.3 GB free)".
// The full, uncollapsed path is always still available via the
// tooltip/title attribute wherever this is used.
function compactFolderDisplay(path, freeSpaceLabel) {
  if (!path) return { compact: "", tooltip: "" };
  const sep = path.includes("\\") ? "\\" : "/";
  const trimmed = path.replace(/[\\/]+$/, "");
  const segments = trimmed.split(/[\\/]+/).filter(Boolean);

  let drive = "";
  let rest = segments;
  if (/^[a-zA-Z]:$/.test(segments[0] || "")) {
    // Windows drive letter, e.g. "C:" - anchor on it and treat
    // everything after as the collapsible middle.
    drive = segments[0];
    rest = segments.slice(1);
  }
  // No drive letter (*nix/network path) just leaves drive "" - the
  // leading separator itself (from path.startsWith(sep)) still reads
  // fine as the "root" in the collapsed form, e.g. "/.../outputs".

  const lastFolder = rest[rest.length - 1] || "";
  const compactPath = rest.length <= 1 ? trimmed : `${drive}${sep}...${sep}${lastFolder}`;

  // free_space labels already carry their own drive prefix (see
  // diskspace.get_free_space_label, e.g. "C: 123.3 GB free") - strip
  // that off here since the collapsed path already shows the drive,
  // so it isn't repeated twice in something like "C:\...\downloads (C: 123.3 GB free)".
  let freeSuffix = freeSpaceLabel || "";
  if (drive) {
    freeSuffix = freeSuffix.replace(new RegExp(`^${drive}\\s*`, "i"), "");
  }

  return {
    compact: freeSuffix ? `${compactPath} (${freeSuffix})` : compactPath,
    tooltip: path,
  };
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
    folderStatusTarget.querySelector(".folder-status-path").textContent = "(unavailable)";
    folderStatusTarget.title = "";
  }
}

function setTargetDirDisplay(path, freeSpace) {
  const pathEl = ctxTargetDir.querySelector(".ctx-folder-path");
  const freeEl = ctxTargetDir.querySelector("#ctx-target-free");
  if (!path) {
    pathEl.textContent = "Target: (none set)";
    freeEl.textContent = "";
    folderStatusTarget.querySelector(".folder-status-path").textContent = "(none set)";
    folderStatusTarget.title = "";
  } else {
    const { compact, tooltip } = compactFolderDisplay(path, freeSpace);
    pathEl.textContent = `Target: ${compact}`;
    freeEl.textContent = "";
    folderStatusTarget.querySelector(".folder-status-path").textContent = compact;
    folderStatusTarget.title = tooltip;
  }
  ctxTargetDir.title = path || "";
}

function applyVersionState(data) {
  let text = data.version ? `yt-dlp v${data.version}` : "yt-dlp version unknown";
  if (data.version && data.just_updated) text += " (just updated)";
  ctxVersion.textContent = text;
  return text;
}

async function refreshVersion() {
  try {
    const res = await fetch("/api/version");
    const data = await res.json();
    applyVersionState(data);
  } catch (e) {
    ctxVersion.textContent = "yt-dlp version unknown";
  }
}

// Clicking the logo shows the app's own version (stash_dlp, not yt-dlp -
// see ctxVersion/refreshVersion above for that). Source of truth is
// APP_VERSION in backend/config.py via /api/app_version.
logoImg.addEventListener("click", async () => {
  try {
    const res = await fetch("/api/app_version");
    const data = await res.json();
    flashStatus(data.version ? `stash_dlp v${data.version}` : "stash_dlp version unknown");
  } catch (e) {
    flashStatus("stash_dlp version unknown");
  }
});

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
    loadEncodeJobsSnapshot();

    if (inputField.disabled) {
      inputField.disabled = false;
      inputField.placeholder = "Paste a link, then press ENTER...";
    }
  };

  ws.onmessage = (evt) => {
    const msg = JSON.parse(evt.data);
    if (msg.type === "clipboard_url") {
      const url = (msg.url || "").trim();
      if (/^https?:\/\/[^\s]+$/i.test(url) && state.current === "READY" && state.appMode === "DOWNLOAD") {
        inputField.value = url;
        inputField.dispatchEvent(new Event("input", { bubbles: true }));
        state.clipboardUrl = url;
      }
      return;
    } else if (msg.type === "job_added") {
      state.jobs.set(msg.job.filename, msg.job);
      renderLedger();
    } else if (msg.type === "job_progress") {
      const job = state.jobs.get(msg.filename);
      if (job) {
        job.pct = msg.pct; job.total = msg.total; job.speed = msg.speed; job.eta = msg.eta;
        updateJobCardProgress(msg.filename, job);
      }
    } else if (msg.type === "job_finished") {
      const job = state.jobs.get(msg.filename);
      if (job) {
        job.status = msg.status;
        job.file_size = msg.file_size;
        job.is_audio = msg.is_audio;
        job.width = msg.width;
        job.height = msg.height;
        job.duration = msg.duration;
        job.ext = msg.ext;
        job.video_codec = msg.video_codec;
        job.audio_codec = msg.audio_codec;
        renderLedger();
      }
    } else if (msg.type === "job_status") {
      // A QUEUED playlist item flipping to DOWNLOADING once its
      // playlist batch's concurrency slot frees up - distinct from
      // job_finished, which always carries full terminal metadata.
      const job = state.jobs.get(msg.filename);
      if (job) {
        job.status = msg.status;
        renderLedger();
      }
    } else if (msg.type === "job_deleted") {
      state.jobs.delete(msg.filename);
      renderLedger();
    } else if (msg.type === "download_failed_retry_m3u") {
      beginM3uRetryPipeline(msg.url, msg.res_cap, msg.original_pasted_url);
    } else if (msg.type === "refresh") {
      loadJobsIntoMap(msg.jobs);
      renderLedger();
    } else if (msg.type === "encode_job_added") {
      state.encodeJobs.set(msg.job.id, msg.job);
      renderEncodeLedger();
    } else if (msg.type === "encode_job_progress" || msg.type === "encode_job_updated") {
      state.encodeJobs.set(msg.job.id, msg.job);
      updateEncodeJobCard(msg.job);
      // A status flip (e.g. into/out of DONE) can change whether a
      // download's RE-ENCODED pill should show, so keep it in sync.
      if (msg.type === "encode_job_updated") {
        renderLedger();
        if (msg.job.status === "DONE") playCompletionPing();
      }
    } else if (msg.type === "encode_job_deleted") {
      state.encodeJobs.delete(msg.job_id);
      renderEncodeLedger();
      renderLedger();
    } else if (msg.type === "history_entry_deleted") {
      state.historyEntries = state.historyEntries.filter(
        (e) => !(e.timestamp === msg.timestamp && e.filename === msg.filename && e.url === msg.url)
      );
      if (state.appMode === "SEARCH_HISTORY") renderHistoryLedger();
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

function formatBytes(bytes) {
  if (!isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let i = 0;
  while (bytes >= 1024 && i < units.length - 1) {
    bytes /= 1024;
    i++;
  }
  return `${bytes.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function getFilteredSortedJobs() {
  // "added" order = Map insertion order, oldest first; reverse for
  // newest-first, matching the ledger's existing default look.
  const withIndex = Array.from(state.jobs.values()).map((job, i) => ({ job, addedIndex: i }));

  const query = state.filterText.trim().toLowerCase();
  let filtered = query
    ? withIndex.filter(({ job }) => job.filename.toLowerCase().includes(query))
    : withIndex;

  if (state.audioOnlyFilter) {
    filtered = filtered.filter(({ job }) => job.is_audio);
  }

  if (state.hideCompletedFilter) {
    filtered = filtered.filter(({ job }) => job.status !== "DONE");
  }

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

function getFilteredSortedHistory() {
  const query = state.filterText.trim().toLowerCase();
  let filtered = query
    ? state.historyEntries.filter((e) => e.filename.toLowerCase().includes(query))
    : state.historyEntries.slice();

  const dirMul = state.sortDir === "asc" ? 1 : -1;
  filtered.sort((a, b) => {
    let cmp;
    if (state.sortField === "name") {
      cmp = a.filename.toLowerCase().localeCompare(b.filename.toLowerCase());
    } else {
      // "added" (and "size", which doesn't apply to history entries and
      // is disabled in the sort dropdown while this mode is active)
      // both fall back to chronological order via the log timestamp.
      cmp = a.timestamp < b.timestamp ? -1 : a.timestamp > b.timestamp ? 1 : 0;
    }
    return cmp * dirMul;
  });

  return filtered;
}

function renderLedger() {
  if (state.appMode === "SEARCH_HISTORY") {
    renderHistoryLedger();
    return;
  }
  const jobs = getFilteredSortedJobs();

  // Reuse each job's existing card node when its signature (see
  // jobCardSignature) hasn't changed since the last render, instead of
  // rebuilding every card from scratch every call. A full rebuild would
  // recreate every thumbnail <img> (forcing a re-request/re-decode) and
  // flash the whole ledger, which is wasteful when this fires from the
  // 5s auto-refresh poll and nothing actually changed. Selection state
  // (checkbox/.selected class) lives on the DOM node itself via
  // setCardSelectedVisual(), so it survives reuse untouched.
  const fragment = document.createDocumentFragment();
  const seen = new Set();
  for (const job of jobs) {
    const sig = jobCardSignature(job);
    seen.add(job.filename);
    let card = state.renderedCards.get(job.filename);
    if (!card || state.renderedCardSigs.get(job.filename) !== sig) {
      card = buildJobCard(job);
      state.renderedCards.set(job.filename, card);
      state.renderedCardSigs.set(job.filename, sig);
    }
    fragment.appendChild(card); // moves the node out of queueList if it's already there
  }
  for (const filename of Array.from(state.renderedCards.keys())) {
    if (!seen.has(filename)) {
      state.renderedCards.delete(filename);
      state.renderedCardSigs.delete(filename);
    }
  }
  queueList.innerHTML = "";
  queueList.appendChild(fragment);
  renderLedgerStatsBar(jobs);
}

// Everything that visibly (or functionally, e.g. has_twin gating the
// context menu) affects a job's card. state.saveDirPath is folded in
// too since it feeds the path tooltip/filename-path line - keeping it
// out of the per-field list below so a folder change doesn't need its
// own special case.
function jobCardSignature(job) {
  return [
    state.saveDirPath,
    job.status, job.pct, job.total, job.speed, job.eta,
    job.file_size, job.is_audio, job.ext,
    job.width, job.height, job.duration, job.video_codec, job.audio_codec,
    job.url, job.source_type, job.source_path,
    job.stash_tag_name, job.stash_scene_id,
    job.synchronized, job.has_twin, job.playback_position, job.fully_played,
  ].join("\u0001");
}

// Thin summary row under the toolbar: count + total size of whatever's
// currently visible in the ledger, so it stays honest against the
// active filter/audio-only/hide-completed state instead of always
// reflecting the full queue.
function renderLedgerStatsBar(jobs) {
  if (jobs.length === 0) {
    ledgerStatsBar.textContent = "";
    return;
  }
  let totalBytes = 0;
  for (const job of jobs) {
    const bytes = parseSizeToBytes(job.file_size);
    if (bytes > 0) totalBytes += bytes;
  }
  const fileLabel = jobs.length === 1 ? "1 file" : `${jobs.length} files`;
  ledgerStatsBar.textContent = totalBytes > 0 ? `${fileLabel} \u00b7 ${formatBytes(totalBytes)}` : fileLabel;
}

function renderHistoryLedger() {
  ledgerStatsBar.textContent = "";
  queueList.innerHTML = "";
  const entries = getFilteredSortedHistory();
  if (entries.length === 0) {
    const empty = document.createElement("div");
    empty.className = "history-empty";
    empty.textContent = state.filterText.trim()
      ? "No history entries match your filter."
      : "No download history found yet.";
    queueList.appendChild(empty);
    return;
  }
  for (const entry of entries) {
    queueList.appendChild(buildHistoryCard(entry));
  }
}

async function refreshSearchHistory() {
  queueList.innerHTML = "";
  const loading = document.createElement("div");
  loading.className = "history-empty";
  loading.textContent = "Loading download history...";
  queueList.appendChild(loading);

  try {
    const res = await fetch("/api/history");
    const data = await res.json();
    state.historyEntries = data.entries || [];
  } catch (e) {
    state.historyEntries = [];
  }

  if (state.appMode === "SEARCH_HISTORY") renderHistoryLedger();
}

// Common video aspect ratios, checked against the actual pixel ratio so
// slightly-off dimensions (e.g. 1918x1080 from some encoders) still snap
// to the label a person would recognize, rather than showing "959:540".
const COMMON_ASPECT_RATIOS = [
  [1, 1], [4, 3], [3, 2], [16, 9], [16, 10], [21, 9], [9, 16], [3, 4], [2, 3], [10, 16],
];

function aspectRatioLabel(width, height) {
  if (!width || !height) return "";
  const ratio = width / height;
  for (const [rw, rh] of COMMON_ASPECT_RATIOS) {
    if (Math.abs(ratio - rw / rh) < 0.02) return `${rw}:${rh}`;
  }
  const divisor = gcd(width, height);
  return `${width / divisor}:${height / divisor}`;
}

function gcd(a, b) {
  return b === 0 ? a : gcd(b, a % b);
}

function stemOf(filename) {
  const idx = filename.lastIndexOf(".");
  return idx > 0 ? filename.slice(0, idx) : filename;
}

// A download is considered re-encoded if a completed Encode Manager job's
// source file traces back to it - i.e. its source_filename's stem matches
// this job's filename. Encode jobs are kept in sync client-side (see the
// websocket handler and loadEncodeJobsSnapshot), so this is just a lookup.
function isReencoded(filename) {
  for (const encodeJob of state.encodeJobs.values()) {
    if (encodeJob.status === "DONE" && stemOf(encodeJob.source_filename || "") === filename) {
      return true;
    }
  }
  return false;
}

// A download is synchronized once its Synchronize Audio twin in
// Converted/ has been confirmed - tracked directly on the job (see
// mark_synchronized on the backend), not derived like isReencoded()
// since sync renders aren't Encode Manager jobs.
function isSynchronized(filename) {
  const job = state.jobs.get(filename);
  return !!(job && job.synchronized);
}

// Short synthesized two-tone chime, so completion has an audible cue
// without needing to ship/load an actual sound file.
function playCompletionPing() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const now = ctx.currentTime;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.setValueAtTime(880, now);
    osc.frequency.exponentialRampToValueAtTime(1320, now + 0.12);
    gain.gain.setValueAtTime(0.0001, now);
    gain.gain.exponentialRampToValueAtTime(0.25, now + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.35);
    osc.connect(gain).connect(ctx.destination);
    osc.start(now);
    osc.stop(now + 0.4);
    osc.onended = () => ctx.close();
  } catch (e) {
    // Audio unavailable/blocked (e.g. no user interaction yet) - not
    // critical, the visual completion state still shows either way.
  }
}

const ledgerFilterInput = el("ledger-filter");
const ledgerAudioFilterBtn = el("ledger-audio-filter-btn");
const ledgerSortSelect = el("ledger-sort");
const ledgerSortDirBtn = el("ledger-sort-dir");
const ledgerStatsBar = el("ledger-stats-bar");

// ── Navigation tray toggle ──────────────────────────────────
navToggleBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  navTray.classList.remove("hidden");
  navTray.classList.toggle("open");
  navToggleBtn.classList.toggle("active");
});

// Close tray when clicking outside
document.addEventListener("click", (e) => {
  const isInside = navTray.contains(e.target) || navToggleBtn.contains(e.target);
  if (!isInside && navTray.classList.contains("open")) {
    navTray.classList.remove("open");
    navToggleBtn.classList.remove("active");
  }
});

ledgerFilterInput.addEventListener("input", () => {
  state.filterText = ledgerFilterInput.value;
  renderLedger();
});

ledgerAudioFilterBtn.addEventListener("click", () => {
  state.audioOnlyFilter = !state.audioOnlyFilter;
  ledgerAudioFilterBtn.classList.toggle("active", state.audioOnlyFilter);
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

// ── Multi-select ──────────────────────────────────────────────
// Selection lives as a Set of filenames on state, independent of any
// particular card's DOM node - buildJobCard just reflects it (checked
// state + .selected class) each time a card is (re)built, and the
// checkbox itself is always present in the DOM, shown/hidden purely via
// the .selection-mode class on queueList. That keeps toggling selection
// mode itself cheap (no re-render needed) while still surviving a full
// renderLedger() (e.g. from a websocket update) without losing state.
const selectModeBtn = el("select-mode-btn");
const selectionActionBar = el("selection-action-bar");
const selectionCountLabel = el("selection-count");
const selectionSelectAllBtn = el("selection-select-all-btn");
const selectionClearBtn = el("selection-clear-btn");
const selectionMoveBtn = el("selection-move-btn");
const selectionDeleteBtn = el("selection-delete-btn");

function isSelectable(job) {
  // Mirrors the single-item options menu's own gating: deleting or
  // moving a file that's still being written to would corrupt that
  // job's in-flight state, so downloading items simply aren't
  // selectable in the first place. QUEUED items (waiting on a
  // playlist batch's concurrency slot) have no file on disk yet
  // either, so the same exclusion applies to them.
  return job.status !== "DOWNLOADING" && job.status !== "QUEUED";
}

function updateSelectionBar() {
  const count = state.selectedFilenames.size;
  selectionCountLabel.textContent = count === 1 ? "1 selected" : `${count} selected`;
  selectionMoveBtn.disabled = count === 0;
  selectionDeleteBtn.disabled = count === 0;
}

function setCardSelectedVisual(filename, selected) {
  const card = queueList.querySelector(`.job-card[data-filename="${cssEscape(filename)}"]`);
  if (!card) return;
  card.classList.toggle("selected", selected);
  const checkbox = card.querySelector(".job-card-checkbox");
  if (checkbox) checkbox.checked = selected;
}

function toggleSelection(filename) {
  if (state.selectedFilenames.has(filename)) {
    state.selectedFilenames.delete(filename);
    setCardSelectedVisual(filename, false);
  } else {
    state.selectedFilenames.add(filename);
    setCardSelectedVisual(filename, true);
  }
  updateSelectionBar();
}

function enterSelectionMode() {
  state.selectionMode = true;
  selectModeBtn.classList.add("active");
  selectModeBtn.title = "Cancel Select";
  queueList.classList.add("selection-mode");
  selectionActionBar.classList.remove("hidden");
  updateSelectionBar();
}

function exitSelectionMode() {
  if (!state.selectionMode && state.selectedFilenames.size === 0) return;
  state.selectionMode = false;
  state.selectedFilenames.clear();
  selectModeBtn.classList.remove("active");
  selectModeBtn.title = "Select";
  queueList.classList.remove("selection-mode");
  selectionActionBar.classList.add("hidden");
  for (const card of queueList.querySelectorAll(".job-card.selected")) {
    card.classList.remove("selected");
  }
}

selectModeBtn.addEventListener("click", () => {
  if (state.selectionMode) exitSelectionMode();
  else enterSelectionMode();
});

selectionSelectAllBtn.addEventListener("click", () => {
  for (const job of getFilteredSortedJobs()) {
    if (isSelectable(job) && !state.selectedFilenames.has(job.filename)) {
      state.selectedFilenames.add(job.filename);
      setCardSelectedVisual(job.filename, true);
    }
  }
  updateSelectionBar();
});

selectionClearBtn.addEventListener("click", () => {
  for (const filename of state.selectedFilenames) setCardSelectedVisual(filename, false);
  state.selectedFilenames.clear();
  updateSelectionBar();
});

selectionDeleteBtn.addEventListener("click", async () => {
  const filenames = Array.from(state.selectedFilenames);
  if (filenames.length === 0) return;
  if (!window.confirm(
    `Delete ${filenames.length} file(s)?\n\nThis removes them from disk and can't be undone.`
  )) return;

  try {
    const res = await fetch("/api/jobs/delete-batch", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filenames }),
    });
    const data = await res.json();
    for (const filename of data.deleted || []) state.jobs.delete(filename);
    state.selectedFilenames.clear();
    renderLedger();
    updateSelectionBar();
    if (data.skipped && data.skipped.length > 0) {
      window.alert(
        `Deleted ${data.deleted.length} file(s).\n\n` +
        `${data.skipped.length} skipped (still downloading):\n` +
        data.skipped.join("\n")
      );
    } else {
      flashStatus(`Deleted ${data.deleted.length} file(s).`);
    }
  } catch (e) {
    window.alert("Couldn't reach the server to delete those files.");
  }
});

selectionMoveBtn.addEventListener("click", async () => {
  const filenames = Array.from(state.selectedFilenames);
  if (filenames.length === 0) return;
  const targetPath = ctxTargetDir.title || "";
  if (!targetPath) {
    window.alert("Set a target folder first (right-click the logo \u2192 Change Target Folder...).");
    return;
  }
  if (!window.confirm(`Move ${filenames.length} file(s) to:\n${targetPath}\n\nThis can't be undone.`)) {
    return;
  }

  try {
    const res = await fetch("/api/jobs/move-selected-to-target", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filenames }),
    });
    const data = await res.json();
    if (!res.ok) {
      window.alert(`Couldn't move files:\n${data.error || "Unknown error"}`);
      return;
    }
    loadJobsIntoMap(data.jobs);
    renderLedger();

    const movedNames = [...data.moved];
    const failed = [...data.failed];

    // Same as move-all-btn: files with a re-encoded twin weren't
    // touched yet - walk them one at a time so each gets its own
    // Original/Re-encoded/Cancel prompt.
    for (const info of data.pending_decisions || []) {
      const choice = await promptReencodeChoice(info);
      if (!choice) continue; // cancelled - leave it in the ledger
      const result = await requestMoveToTarget(info.filename, choice);
      if (result === "moved") {
        state.jobs.delete(info.filename);
        movedNames.push(info.filename);
      } else if (result === "error") {
        failed.push({ filename: info.filename, error: "Move failed - see alert." });
      }
    }

    state.selectedFilenames.clear();
    renderLedger();
    updateSelectionBar();

    let message = `Moved ${movedNames.length} item(s) to target.`;
    if (failed.length > 0) {
      message += `\n\n${failed.length} failed:\n` +
        failed.map((f) => `- ${f.filename}: ${f.error}`).join("\n");
    }
    window.alert(message);
  } catch (e) {
    window.alert("Couldn't reach the server to move those files.");
  }
});

// Convert a local Windows path to a file:// URI suitable for an
// OS-level drag operation. The browser cannot hand an arbitrary local
// file object to another application, but Windows/Firefox can consume
// file URIs supplied through the drag data transfer.
function localPathToFileUri(filePath) {
  if (!filePath) return "";
  const normalized = String(filePath).replace(/\\/g, "/");

  if (/^[A-Za-z]:\//.test(normalized)) {
    const drive = normalized.slice(0, 2);
    const rest = normalized.slice(2).split("/").filter(Boolean)
      .map(part => encodeURIComponent(part)).join("/");
    return `file:///${drive}${rest ? "/" + rest : ""}`;
  }

  if (normalized.startsWith("//")) {
    const parts = normalized.slice(2).split("/").filter(Boolean)
      .map(part => encodeURIComponent(part));
    return `file://${parts.join("/")}`;
  }

  const parts = normalized.split("/").filter(Boolean)
    .map(part => encodeURIComponent(part));
  return `file:///${parts.join("/")}`;
}

function addFileDragSupport(card, job) {
  // Only completed files can be dragged to an external editor. A
  // DOWNLOADING/ERROR card must never advertise a path that may not
  // exist or may still be changing.
  if (job.status !== "DONE" || !state.saveDirPath) return;

  const fullPath = joinDisplayPath(state.saveDirPath, job.filename, job.ext);
  const fileUri = localPathToFileUri(fullPath);
  if (!fileUri) return;

  card.draggable = true;
  card.title = "Drag this card to an editing program to open the file";

  card.addEventListener("dragstart", (e) => {
    if (state.selectionMode) {
      e.preventDefault();
      return;
    }

    const dt = e.dataTransfer;
    if (!dt) return;

    dt.effectAllowed = "copy";
    // text/uri-list is understood by browsers and many desktop
    // applications when a local file is dragged out of a webpage.
    dt.setData("text/uri-list", fileUri);
    dt.setData("text/plain", fileUri);

    // Chromium's DownloadURL format gives applications that support it
    // the filename, MIME type and local file URI in one payload.
    const mime = job.is_audio ? "audio/*" : "video/*";
    dt.setData("DownloadURL", `${mime}:${job.filename}:${fileUri}`);

    // Firefox exposes this custom flavor to some native drag targets.
    dt.setData("application/x-moz-file", fullPath);

    card.classList.add("dragging");
  });

  card.addEventListener("dragend", () => {
    card.classList.remove("dragging");
  });
}

function buildJobCard(job) {
  const card = document.createElement("div");
  card.className = "job-card job-card-row";
  card.dataset.filename = job.filename;
  if (job.is_audio) card.classList.add("audio");
  if (state.selectedFilenames.has(job.filename)) card.classList.add("selected");

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
  const thumbWrap = document.createElement("div");
  thumbWrap.className = "job-thumb-wrap";
  thumbWrap.appendChild(thumb);

  if (isSelectable(job)) {
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "job-card-checkbox";
    checkbox.checked = state.selectedFilenames.has(job.filename);
    checkbox.setAttribute("aria-label", `Select ${job.filename}`);
    checkbox.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleSelection(job.filename);
    });
    thumbWrap.appendChild(checkbox);
  }

  card.appendChild(thumbWrap);

  const body = document.createElement("div");
  body.className = "job-card-body job-card-body-wide";

  const titlePathCol = document.createElement("div");
  titlePathCol.className = "job-title-path-col";

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
  if (job.status === "DONE" && isReencoded(job.filename)) {
    const pill = document.createElement("span");
    pill.className = "reencoded-badge";
    pill.textContent = "RE-ENCODED";
    titleRow.appendChild(pill);
  }
  if (job.status === "DONE" && job.synchronized) {
    const pill = document.createElement("span");
    pill.className = "synchronized-badge";
    pill.textContent = "SYNCHRONIZED";
    titleRow.appendChild(pill);
  }
  // Direct filesystem check (done server-side on every Refresh, see
  // job_manager.seed_from_filesystem) for a twin sitting in Converted/.
  // Only surfaced when RE-ENCODED/SYNCHRONIZED aren't already showing,
  // since those already say "there's a twin" more specifically - this
  // pill exists to catch the cases those miss, e.g. a twin left over
  // from before a server restart (Encode Manager history is in-memory).
  if (job.status === "DONE" && job.has_twin && !isReencoded(job.filename) && !job.synchronized) {
    const pill = document.createElement("span");
    pill.className = "has-twin-badge";
    pill.textContent = "HAS TWIN";
    pill.title = "A file already exists for this download in Converted/. Press Refresh if this seems out of date.";
    titleRow.appendChild(pill);
  }
  if (job.source_type === "stash" && !job.is_audio) {
    const pill = document.createElement("span");
    pill.className = "stash-badge";
    pill.textContent = "Stash file";
    titleRow.appendChild(pill);
  }
  if (job.stash_tag_name) {
    const pill = document.createElement("span");
    pill.className = "stash-tag-badge";
    pill.textContent = job.stash_tag_name;
    pill.title = `Imported via Stash Check Tag: "${job.stash_tag_name}". Renaming is disabled for this item.`;
    titleRow.appendChild(pill);
  }
  if (job.status === "DONE" && job.fully_played) {
    const pill = document.createElement("span");
    pill.className = "watched-badge";
    pill.textContent = job.is_audio ? "✓ LISTENED" : "✓ WATCHED";
    pill.title = job.is_audio
      ? "Fully listened to at least once - still tracks your current position if you listen again."
      : "Fully watched at least once - still tracks your current position if you rewatch.";
    titleRow.appendChild(pill);
  }
  titlePathCol.appendChild(titleRow);

  const folderPath = state.saveDirPath || "";
  const fullFilePath = joinDisplayPath(state.saveDirPath, job.filename, job.ext);
  const pathEl = document.createElement("div");
  pathEl.className = "job-path";
  pathEl.textContent = folderPath ? compactFolderDisplay(folderPath, "").compact : "(unknown folder)";
  pathEl.title = fullFilePath;
  titlePathCol.appendChild(pathEl);

  body.appendChild(titlePathCol);

  const status = job.status;
  const isDownloadingCard = status === "DOWNLOADING";
  const isDoneCard = status === "DONE";
  const isQueuedCard = status === "QUEUED";

  // Quick-action icons - the same four operations available from the
  // card's options menu (copy link, copy filename, rename, move to
  // target), surfaced directly on the card so they're one tap away.
  // Gating mirrors openJobMenu: copy actions work at any status, rename
  // needs the download to be finished writing, and move-to-target needs
  // a completed file to actually move. On wide desktop layouts they sit
  // as a visible column at the end of the row (there's width to spare);
  // the mobile media query hides this column entirely since the tap
  // options menu already covers the same four actions there.
  function makeCardIconBtn(iconClass, title, onClick) {
    const btn = document.createElement("button");
    btn.className = "job-mini-btn";
    btn.title = title;
    btn.setAttribute("aria-label", title);
    const icon = document.createElement("i");
    icon.className = `ti ti-${iconClass}`;
    icon.setAttribute("aria-hidden", "true");
    btn.appendChild(icon);
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      onClick();
    });
    return btn;
  }

  const cardIconBtns = [
    makeCardIconBtn("external-link", "Copy URL", () => copyJobLink(job.filename, job.url)),
    makeCardIconBtn("file-text", "Copy file name", () => copyJobFilename(job.filename)),
  ];
  if (!isDownloadingCard && !isQueuedCard && !job.stash_tag_name) {
    cardIconBtns.push(makeCardIconBtn("forms", "Rename", () => renameJobPrompt(job.filename)));
  }
  if (isDoneCard && job.source_type !== "stash") {
    cardIconBtns.push(makeCardIconBtn("arrow-right", "Move to target folder", () => moveJobToTarget(job.filename)));
  }
  if (status === "ERROR" || status === "CANCELLED") {
    cardIconBtns.push(makeCardIconBtn("refresh", "Retry", () => retryJob(job.filename)));
  }
  const cardIcons = document.createElement("div");
  cardIcons.className = "job-card-icons";
  cardIconBtns.forEach((btn) => cardIcons.appendChild(btn));

  // Footer row - meta/size text (when present) plus the icon group,
  // on their own line below the title/path so the icons no longer
  // share the title's line and eat into its available width.
  const footer = document.createElement("div");
  footer.className = "job-card-footer";

  if (isDownloadingCard) {
    const stats = document.createElement("div");
    stats.className = "job-stats";
    stats.textContent = "Initializing metrics...";
    titlePathCol.appendChild(stats);

    const track = document.createElement("div");
    track.className = "job-progress-track";
    const fill = document.createElement("div");
    fill.className = "job-progress-fill";
    track.appendChild(fill);
    titlePathCol.appendChild(track);

    footer.appendChild(cardIcons);
    body.appendChild(footer);
    card.appendChild(body);
    updateProgressDOM(card, job);
  } else if (isQueuedCard) {
    card.classList.add("queued");
    const stats = document.createElement("div");
    stats.className = "job-stats";
    stats.textContent = "Queued — waiting for a download slot in this playlist...";
    titlePathCol.appendChild(stats);

    footer.appendChild(cardIcons);
    body.appendChild(footer);
    card.appendChild(body);
  } else {
    card.classList.add(status === "DONE" ? "done" : status === "CANCELLED" ? "cancelled" : "error");
    if (status === "DONE") {
      const metaParts = [];
      if (job.ext) metaParts.push(job.ext);
      if (!job.is_audio && job.width && job.height) {
        metaParts.push(`${job.width}\u00d7${job.height}`);
        const ar = aspectRatioLabel(job.width, job.height);
        if (ar) metaParts.push(ar);
      }
      if (!job.is_audio && job.video_codec) metaParts.push(job.video_codec.toUpperCase());
      if (job.is_audio && job.audio_codec) metaParts.push(job.audio_codec.toUpperCase());
      const durText = formatDuration(job.duration);
      if (durText) metaParts.push(durText);
      if (job.file_size) metaParts.push(job.file_size);

      if (metaParts.length) {
        const meta = document.createElement("div");
        meta.className = "job-size";
        meta.textContent = metaParts.join("  •  ");
        footer.appendChild(meta);
      }

      // Playback progress - shown for every completed file (not just
      // ones with a saved position) so scanning the ledger tells you
      // at a glance what you've started vs. never touched. A file
      // marked fully_played but currently at position 0 (i.e. not
      // mid-rewatch) shows a full green bar rather than an empty one -
      // an empty bar would misleadingly suggest "never watched", when
      // finishing playback is exactly what resets playback_position
      // back to 0 for next time. A rewatch-in-progress always takes
      // priority in what's displayed, using the normal amber
      // in-progress color even if fully_played is also set.
      // Only show the playback bar when there is actual playback progress
      // (or the file has been fully watched). A saved position of 0/null
      // means there is nothing useful to display, so don't leave an empty bar.
      if (job.duration > 0 && (job.playback_position > 0 || job.fully_played)) {
        const track = document.createElement("div");
        track.className = "job-playback-track";
        const fill = document.createElement("div");
        fill.className = "job-playback-fill";
        const inProgress = job.playback_position > 0;
        let pct;
        if (job.fully_played && !inProgress) {
          pct = 100;
          fill.classList.add("watched-complete");
        } else {
          pct = Math.max(0, Math.min(100, (job.playback_position / job.duration) * 100));
        }
        fill.style.width = `${pct}%`;
        track.title = !inProgress && !job.fully_played
          ? "Not started yet"
          : `${formatDuration(job.playback_position)} / ${formatDuration(job.duration)}`;
        track.appendChild(fill);
        titlePathCol.appendChild(track);
      }
    }
    footer.appendChild(cardIcons);
    body.appendChild(footer);
    card.appendChild(body);
  }

  // Clicking the thumbnail itself starts playback directly (for
  // completed items); clicking anywhere else on the card opens the
  // options menu. For non-completed items there's nothing to play yet,
  // so a thumbnail click there just falls through to the same menu
  // behavior as the rest of the card.
  addFileDragSupport(card, job);

  thumb.addEventListener("click", (e) => {
    if (state.selectionMode) {
      if (!isSelectable(job)) return;
      e.stopPropagation();
      toggleSelection(job.filename);
      return;
    }
    if (job.status === "DONE") {
      e.stopPropagation();
      openMediaModal(job.filename, job.is_audio);
    }
  });

  card.addEventListener("click", (e) => {
    e.stopPropagation();
    if (state.selectionMode) {
      if (!isSelectable(job)) return;
      toggleSelection(job.filename);
      return;
    }
    openJobMenu(e.clientX, e.clientY, job);
  });

  return card;
}

async function retryJob(filename) {
  try {
    const res = await fetch("/api/jobs/retry", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename }),
    });
    const data = await res.json();
    if (res.ok) {
      state.jobs.set(data.job.filename, data.job);
      renderLedger();
    } else {
      flashStatus(data.error || "Couldn't retry that download.");
    }
  } catch (e) {
    flashStatus("Couldn't reach the server to retry that download.");
  }
}

function buildTooltip(job) {
  let t = `File: ${job.filename}\nPath: ${joinDisplayPath(state.saveDirPath, job.filename, job.ext)}\nURL: ${job.url || ""}`;
  if (job.ext) t += `\nType: ${job.ext}`;
  if (!job.is_audio && job.width && job.height) {
    t += `\nResolution: ${job.width}\u00d7${job.height}`;
    const ar = aspectRatioLabel(job.width, job.height);
    if (ar) t += ` (${ar})`;
  }
  if (!job.is_audio && job.video_codec) t += `\nCodec: ${job.video_codec.toUpperCase()}`;
  if (job.is_audio && job.audio_codec) t += `\nCodec: ${job.audio_codec.toUpperCase()}`;
  const durText = formatDuration(job.duration);
  if (durText) t += `\nDuration: ${durText}`;
  if (job.file_size) t += `\nSize: ${job.file_size}`;
  if (job.status === "DONE" && job.has_twin) t += `\nTwin copy available in Converted/`;
  return t;
}

// A history entry is a plain log record (timestamp/status/filename/url) -
// not a ledger job, since the file it refers to may have been moved,
// renamed, or deleted since it was downloaded. It gets its own simpler
// card, built from the same visual pieces as buildJobCard for a
// consistent look, with a status icon standing in for the thumbnail.
const HISTORY_STATUS_ICON = { DONE: "🎬", ERROR: "⚠️", CANCELLED: "🚫" };

function buildHistoryCard(entry) {
  const card = document.createElement("div");
  card.className = "job-card history-card";
  card.classList.add(
    entry.status === "DONE" ? "done" : entry.status === "CANCELLED" ? "cancelled" : "error"
  );
  card.dataset.filename = entry.filename;

  const thumb = document.createElement("div");
  thumb.className = "job-thumb";
  const placeholder = document.createElement("span");
  placeholder.className = "job-thumb-placeholder";
  placeholder.textContent = HISTORY_STATUS_ICON[entry.status] || "🕓";
  thumb.appendChild(placeholder);
  card.appendChild(thumb);

  const body = document.createElement("div");
  body.className = "job-card-body";

  const titleRow = document.createElement("div");
  titleRow.className = "job-title-row";
  const title = document.createElement("div");
  title.className = "job-title";
  title.textContent = entry.filename;
  title.title = `File: ${entry.filename}\nURL: ${entry.url}\nWhen: ${entry.timestamp}\nStatus: ${entry.status}`;
  titleRow.appendChild(title);
  body.appendChild(titleRow);

  const meta = document.createElement("div");
  meta.className = "job-size";
  meta.textContent = `${entry.timestamp}  •  ${entry.status}`;
  body.appendChild(meta);

  card.appendChild(body);

  card.addEventListener("click", (e) => {
    e.stopPropagation();
    openHistoryMenu(e.clientX, e.clientY, entry);
  });

  return card;
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

// Every submenu flyout in the app - Copy/File (job menu) and
// Folders/Settings (logo menu) all share this one open/close/position
// mechanism, so anything that resets menu state just walks this list.
const ALL_FLYOUTS = [openWithFlyout, copyFlyout, fileFlyout, foldersFlyout, settingsFlyout, dlFolderQuickMenu, targetFolderQuickMenu, urlArgsFlyout, stashMenuFlyout];

function hideAllFlyouts() {
  for (const flyout of ALL_FLYOUTS) flyout.classList.add("hidden");
}

// ── Context menus ─────────────────────────────────────────────
function openLogoMenu(x, y) {
  jobMenu.classList.add("hidden");
  historyMenu.classList.add("hidden");
  hideAllFlyouts();
  positionMenu(logoMenu, x, y);
  refreshSaveDir();
  refreshTargetDir();
}

function openJobMenu(x, y, job) {
  logoMenu.classList.add("hidden");
  historyMenu.classList.add("hidden");
  hideAllFlyouts();

  const isDownloading = job.status === "DOWNLOADING";
  const isQueued = job.status === "QUEUED";
  const isDone = job.status === "DONE";
  const isVideo = isDone && !job.is_audio;
  const isAudioDone = isDone && job.is_audio;
  el("ctx-cancel-job").classList.toggle("hidden", !isDownloading && !isQueued);
  el("ctx-play-video").classList.toggle("hidden", !isVideo);
  el("ctx-play-audio").classList.toggle("hidden", !isAudioDone);
  // Offered whenever this file has a twin sitting in Converted/ -
  // regardless of how it got there (re-encode, audio sync, or just
  // dropped in by hand). Driven by job.has_twin, the direct Converted/
  // filesystem check refreshed on every Refresh press/auto-refresh -
  // NOT isReencoded()/isSynchronized(), since those miss a twin left
  // over from before a server restart or placed manually.
  el("ctx-play-converted").classList.toggle(
    "hidden",
    !isDone || !job.has_twin,
  );
  el("ctx-extract-audio").classList.toggle("hidden", !isVideo);
  // Jumps straight into the encode setup modal with this file
  // preselected as the source - only makes sense for a completed,
  // non-audio download since that's all the encoder can take as input
  // (mirrors the /api/encode/sources gating). Hidden if the file is
  // already synchronized, since a re-encode twin and a sync twin would
  // both want the same Converted/<stem> slot (see isSynchronized).
  el("ctx-reencode-file").classList.toggle("hidden", !isVideo || isSynchronized(job.filename));
  // Same twin-slot logic in reverse: only offered for a completed video
  // with an audio track, and only if there isn't already a re-encode
  // twin sitting in Converted/ for this file.
  el("ctx-sync-audio").classList.toggle(
    "hidden",
    !isVideo || !job.audio_codec || isReencoded(job.filename),
  );
  // Shown for any completed item (video or audio) once at least one
  // external program is configured; enabled/disabled state (local vs
  // remote) is applied once at boot via applyLocalOnlyUI.
  el("ctx-open-with").classList.toggle("hidden", !(isDone && state.externalPrograms.length > 0));

  // Copy and File are submenu triggers now (see copy-flyout/file-flyout)
  // rather than flat top-level items - the trigger itself is shown
  // whenever at least one of its children would be, and the individual
  // rows inside each flyout are toggled the same way they always were.
  // Copying the link/filename works fine mid-download too (both are
  // already known the moment the job starts), so these stay visible
  // regardless of status - only file-system actions below (rename,
  // move, open folder, delete) still require the download to be done.
  el("ctx-copy-link").classList.remove("hidden");
  el("ctx-copy-filename").classList.remove("hidden");
  el("ctx-copy-submenu").classList.remove("hidden");

  el("ctx-rename-file").classList.toggle("hidden", isDownloading || isQueued || !!job.stash_tag_name);
  el("ctx-move-to-target").classList.toggle("hidden", !isDone || job.source_type === "stash");
  el("ctx-replace-source").classList.toggle("hidden", !isDone || job.source_type !== "stash" || !job.source_path);
  el("ctx-replace-with-twin").classList.toggle("hidden", !isDone || !job.has_twin);
  el("ctx-open-folder").classList.toggle("hidden", !isDone);
  el("ctx-file-submenu").classList.toggle("hidden", isDownloading || isQueued);

  el("ctx-delete-file").classList.toggle("hidden", isDownloading || isQueued);

  jobMenu.dataset.filename = job.filename;
  jobMenu.dataset.url = job.url || "";
  positionMenu(jobMenu, x, y);
}

// Search History Mode's per-card menu - flat (no submenus needed for
// just four actions) and independent of job status, since a history
// record isn't a ledger job.
function openHistoryMenu(x, y, entry) {
  logoMenu.classList.add("hidden");
  jobMenu.classList.add("hidden");
  hideAllFlyouts();

  historyMenu.dataset.filename = entry.filename;
  historyMenu.dataset.url = entry.url;
  historyMenu.dataset.timestamp = entry.timestamp;
  positionMenu(historyMenu, x, y);
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

el("ctx-play-converted").addEventListener("click", () => {
  const filename = jobMenu.dataset.filename;
  const job = state.jobs.get(filename);
  closeMenus();
  if (!job) return;
  openMediaModal(filename, !!job.is_audio, "converted");
});

el("ctx-replace-with-twin").addEventListener("click", async () => {
  const filename = jobMenu.dataset.filename;
  closeMenus();
  if (!filename) return;

  if (!window.confirm(`Replace "${filename}" with its twin?`)) return;

  flashStatus(`Replacing "${filename}" with twin...`);
  try {
    const res = await fetch("/api/jobs/replace-with-twin", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename }),
    });
    const data = await res.json();
    if (!res.ok) {
      window.alert(`Couldn't replace "${filename}":\n${data.error || "Unknown error"}`);
      return;
    }
    loadJobsIntoMap(data.jobs);
    renderLedger();
    flashStatus(`Replaced "${filename}" with twin.`);
  } catch (e) {
    window.alert("Couldn't reach the server to replace the file.");
  }
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

el("ctx-reencode-file").addEventListener("click", () => {
  const filename = jobMenu.dataset.filename;
  closeMenus();
  setAppModeEncode();
  openNewEncodeJobModal(filename);
});

el("ctx-sync-audio").addEventListener("click", () => {
  const filename = jobMenu.dataset.filename;
  closeMenus();
  openSyncAudioModal(filename);
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

// Used by the DL/Target quick-select dropdowns, which drop straight down
// from the folder-status line that was clicked rather than opening to
// the side like the submenu flyouts above.
function positionDropdownBelow(dropdownEl, anchorEl) {
  dropdownEl.classList.remove("hidden");
  const anchorRect = anchorEl.getBoundingClientRect();
  const dropdownRect = dropdownEl.getBoundingClientRect();

  let left = anchorRect.left;
  if (left + dropdownRect.width > window.innerWidth - 8) {
    left = window.innerWidth - dropdownRect.width - 8;
  }
  left = Math.max(8, left);

  let top = anchorRect.bottom + 4;
  const maxTop = window.innerHeight - dropdownRect.height - 8;
  top = Math.min(top, Math.max(8, maxTop));

  dropdownEl.style.left = `${left}px`;
  dropdownEl.style.top = `${top}px`;
  lastMenuOpenAt = Date.now();
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

function openMediaModal(filename, isAudio, source = "original") {
  videoModalTitle.textContent = source === "converted" ? `${filename} (Converted)` : filename;
  videoModalTitle.title = filename;
  const streamUrl = `/api/jobs/stream?filename=${encodeURIComponent(filename)}&source=${source}`;

  videoPlayer.classList.toggle("hidden", isAudio);
  audioPlayerWrap.classList.toggle("hidden", !isAudio);

  const job = state.jobs.get(filename);
  // The converted twin is a distinct file from the original, so resuming
  // at the original's last position (and tracking position against it
  // below) wouldn't make sense - play it from the start untracked.
  const resumePosition = source === "original" && job && job.playback_position > 0 ? job.playback_position : 0;
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
  if (source === "original") {
    attachPlaybackTracking(filename, activeEl);
  } else {
    detachPlaybackTracking();
  }
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
  tracking.onEnded = () => sendPlaybackPosition(filename, 0, true); // finished - replay from the start next time, but remember it was fully watched/listened

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
  sendPlaybackPosition(filename, nearEnd ? 0 : element.currentTime, nearEnd);
}

function sendPlaybackPosition(filename, position, completed = false) {
  fetch("/api/jobs/playback-position", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename, position, completed }),
  }).catch(() => {}); // best-effort - a dropped save just means less-precise resume next time

  const job = state.jobs.get(filename);
  if (job) {
    job.playback_position = position; // keep local state fresh within this session too
    // fully_played is sticky - only ever flips False -> True here, and
    // is never reset elsewhere (e.g. a partial rewatch afterwards still
    // updates playback_position normally, but doesn't un-mark it).
    if (completed) job.fully_played = true;
    renderLedger();
  }
}

el("video-modal-close").addEventListener("click", closeMediaModal);
videoModal.addEventListener("click", (e) => {
  if (e.target === videoModal) closeMediaModal();
});

// ── Synchronize Audio ────────────────────────────────────────
// syncAudioState.stage walks through: "original" -> "clip" -> "full",
// with Redo Clip stepping back to "original" and Discard stepping
// back to "clip". everCreatedThisSession tracks whether any render
// exists server-side yet, so a plain Close/Cancel with nothing done
// can skip the cleanup call entirely.
let syncAudioState = null; // { filename, stage, clipStart, everCreatedThisSession }

function openSyncAudioModal(filename) {
  const job = state.jobs.get(filename);
  if (!job) return;

  // appliedDelayMs tracks the delay actually baked into whatever's
  // currently playing (updated by createClip()/applyClipDelay()) - the
  // value Confirm Sync trusts, as opposed to whatever's live in the
  // delay input field, which can drift ahead of it via typing/nudging.
  syncAudioState = { filename, stage: "original", clipStart: null, everCreatedThisSession: false, appliedDelayMs: null };
  // A previous sync operation may have disabled the action buttons while
  // rendering. Reinitialize them every time the dialog is opened.
  setSyncButtonsDisabled(false);
  syncAudioTitle.textContent = "Synchronize Audio";
  // Placeholder until setSyncPlayerSource's header fetch (below) resolves
  // the literal on-disk filename actually loaded in the player.
  syncAudioFilename.textContent = filename;
  syncAudioFilename.title = filename;
  syncAudioDelayInput.value = job.audio_delay_ms || 0;
  syncAudioClipDurationInput.value = 10;
  fetch("/api/jobs/sync-audio/settings")
    .then(r => r.json())
    .then(data => { if (data.clip_duration_s > 0) syncAudioClipDurationInput.value = data.clip_duration_s; })
    .catch(() => {});
  setSyncAudioStatus("");

  // Already-synchronized files play their confirmed twin at first;
  // anything else starts from the original download. Either way, the
  // sync workflow itself (Create Clip, Confirm Sync) always renders
  // off the true original media, never off a previous twin.
  const source = job.synchronized ? "converted" : "original";
  setSyncPlayerSource(filename, source);
  syncAudioStageBadge.textContent = job.synchronized ? "Synchronized file loaded" : "Original file loaded";
  applySyncStageUI();
  syncAudioModal.classList.remove("hidden");
}

function setSyncPlayerSource(filename, source) {
  const url = `/api/jobs/stream?filename=${encodeURIComponent(filename)}&source=${source}&t=${Date.now()}`;
  syncAudioPlayer.src = url;
  // The <video> element gives no way to read response headers, so grab
  // the actual on-disk filename (with extension) via a tiny ranged fetch
  // of the same URL - this is what shows in the filename line, since the
  // sync workflow's in-progress files (clip/staging) don't share the
  // job's own filename.
  fetch(url, { headers: { Range: "bytes=0-0" } })
    .then(res => {
      const name = res.headers.get("X-Media-Filename");
      if (name) {
        syncAudioFilename.textContent = name;
        syncAudioFilename.title = name;
      }
    })
    .catch(() => {});
}

// Pauses and fully detaches the player before any request that will
// replace/delete the file it's currently showing - on Windows a file
// still open for reading by the player can't be written to, and this
// gives the browser (and the server's streaming generator) a moment
// to actually let go of it before the file op runs.
function detachSyncPlayer() {
  syncAudioPlayer.pause();
  syncAudioPlayer.removeAttribute("src");
  syncAudioPlayer.load();
  return new Promise((resolve) => setTimeout(resolve, 150));
}

function setSyncAudioStatus(text, kind) {
  syncAudioStatus.textContent = text;
  syncAudioStatus.classList.toggle("error", kind === "error");
  syncAudioStatus.classList.toggle("success", kind === "success");
}

function formatClipTime(seconds) {
  const s = Math.max(0, Math.round(seconds || 0));
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return `${String(m).padStart(2, "0")}:${String(rem).padStart(2, "0")}`;
}

// Shows/hides/relabels the action buttons and delay dial for the
// current stage. The delay dial only makes sense while a clip is
// loaded (nothing to preview it against beforehand, nothing left to
// tweak once the full render is staged).
function applySyncStageUI() {
  const stage = syncAudioState ? syncAudioState.stage : "original";
  el("sync-audio-controls").classList.toggle("hidden", stage === "full");
  syncAudioCancelBtn.classList.toggle("hidden", stage === "full");
  syncAudioRedoClipBtn.classList.toggle("hidden", stage !== "clip");
  syncAudioConfirmBtn.classList.toggle("hidden", stage !== "clip");
  syncAudioDiscardBtn.classList.toggle("hidden", stage !== "full");
  syncAudioAcceptBtn.classList.toggle("hidden", stage !== "full");
  syncAudioPrimaryBtn.classList.toggle("hidden", stage === "full");
  syncAudioPrimaryBtn.textContent = stage === "clip" ? "Apply Sync" : "Create Clip";
}

function setSyncButtonsDisabled(disabled) {
  syncAudioPrimaryBtn.disabled = disabled;
  syncAudioRedoClipBtn.disabled = disabled;
  syncAudioConfirmBtn.disabled = disabled;
  syncAudioDiscardBtn.disabled = disabled;
  syncAudioAcceptBtn.disabled = disabled;
}

function nudgeSyncAudioDelay(deltaMs) {
  const current = parseFloat(syncAudioDelayInput.value) || 0;
  syncAudioDelayInput.value = current + deltaMs;
}

async function saveSyncClipDuration() {
  const value = parseFloat(syncAudioClipDurationInput.value);
  if (!Number.isFinite(value) || value <= 0) {
    setSyncAudioStatus("Clip duration must be greater than 0 seconds.", "error");
    return false;
  }
  try {
    const res = await fetch("/api/jobs/sync-audio/settings", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ clip_duration_s: value }),
    });
    const data = await res.json();
    if (!res.ok) {
      setSyncAudioStatus(data.error || "Couldn't save clip duration.", "error");
      return false;
    }
    syncAudioClipDurationInput.value = data.clip_duration_s;
    return true;
  } catch (e) {
    setSyncAudioStatus("Couldn't save clip duration.", "error");
    return false;
  }
}

syncAudioClipDurationInput.addEventListener("change", saveSyncClipDuration);
syncAudioClipDurationInput.addEventListener("blur", saveSyncClipDuration);

el("sync-audio-dial-down-big").addEventListener("click", () => nudgeSyncAudioDelay(-100));
el("sync-audio-dial-down-small").addEventListener("click", () => nudgeSyncAudioDelay(-10));
el("sync-audio-dial-up-small").addEventListener("click", () => nudgeSyncAudioDelay(10));
el("sync-audio-dial-up-big").addEventListener("click", () => nudgeSyncAudioDelay(100));

// Create Clip / Apply Sync share one button - same slot in the
// workflow, just a different render underneath depending on stage.
syncAudioPrimaryBtn.addEventListener("click", async () => {
  if (!syncAudioState) return;
  if (syncAudioState.stage === "clip") {
    await applyClipDelay();
  } else {
    await createClip();
  }
});

async function createClip() {
  const { filename } = syncAudioState;
  const startSeconds = syncAudioPlayer.currentTime || 0;
  // Carry forward whatever delay is currently dialed in (e.g. after Redo
  // Clip, or a job's previously-confirmed delay on the very first clip of
  // a re-sync session) - matches the backend's create_clip() contract,
  // which bakes this in immediately so the new clip previews accurately
  // instead of silently reverting to 0. See bakedDelayMs below for why
  // this is also the value Confirm Sync should trust afterward.
  const bakedDelayMs = parseFloat(syncAudioDelayInput.value) || 0;

  setSyncButtonsDisabled(true);
  setSyncAudioStatus(`Cutting a clip from ${formatClipTime(startSeconds)}...`);
  await detachSyncPlayer();

  try {
    const res = await fetch("/api/jobs/sync-audio/create-clip", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename, start_seconds: startSeconds, delay_ms: bakedDelayMs }),
    });
    const data = await res.json();
    if (!res.ok) {
      setSyncAudioStatus(data.error || "Couldn't create the clip.", "error");
      return;
    }
    syncAudioState.stage = "clip";
    syncAudioState.clipStart = data.render.clip_start;
    syncAudioState.everCreatedThisSession = true;
    // The field already holds bakedDelayMs - leave it alone rather than
    // zeroing it out, and record it as the delay actually baked into the
    // clip that's about to play, so Confirm Sync can trust it without
    // requiring a redundant Apply Sync click when nothing's changed.
    syncAudioState.appliedDelayMs = bakedDelayMs;
    setSyncPlayerSource(filename, "sync-clip");
    syncAudioStageBadge.textContent = bakedDelayMs
      ? `Clip from ${formatClipTime(syncAudioState.clipStart)} loaded (${bakedDelayMs} ms delay baked in)`
      : `Clip from ${formatClipTime(syncAudioState.clipStart)} loaded`;
    setSyncAudioStatus("");
    applySyncStageUI();
  } catch (e) {
    setSyncAudioStatus("Couldn't reach the server to create the clip.", "error");
  } finally {
    setSyncButtonsDisabled(false);
  }
}

async function applyClipDelay() {
  const { filename } = syncAudioState;
  const delayMs = parseFloat(syncAudioDelayInput.value) || 0;

  setSyncButtonsDisabled(true);
  setSyncAudioStatus(`Rendering the clip with a ${delayMs} ms delay...`);
  await detachSyncPlayer();

  try {
    const res = await fetch("/api/jobs/sync-audio/apply-clip-delay", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename, delay_ms: delayMs }),
    });
    const data = await res.json();
    if (!res.ok) {
      setSyncAudioStatus(data.error || "Couldn't render the synced clip.", "error");
      return;
    }
    // This is now the delay actually baked into what's playing - the
    // value Confirm Sync should trust (see syncAudioConfirmBtn handler).
    syncAudioState.appliedDelayMs = delayMs;
    setSyncPlayerSource(filename, "sync-clip");
    setSyncAudioStatus(`Clip re-rendered with a ${delayMs} ms delay.`, "success");
  } catch (e) {
    setSyncAudioStatus("Couldn't reach the server to render the synced clip.", "error");
  } finally {
    setSyncButtonsDisabled(false);
  }
}

syncAudioRedoClipBtn.addEventListener("click", async () => {
  if (!syncAudioState) return;
  const { filename } = syncAudioState;

  setSyncButtonsDisabled(true);
  setSyncAudioStatus("Reloading the full video...");
  await detachSyncPlayer();

  try {
    const res = await fetch("/api/jobs/sync-audio/redo-clip", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename }),
    });
    const data = await res.json();
    if (!res.ok) {
      setSyncAudioStatus(data.error || "Couldn't reload the full video.", "error");
      return;
    }
    syncAudioState.stage = "original";
    syncAudioState.clipStart = null;
    const job = state.jobs.get(filename);
    setSyncPlayerSource(filename, job && job.synchronized ? "converted" : "original");
    syncAudioStageBadge.textContent = job && job.synchronized ? "Synchronized file loaded" : "Original file loaded";
    setSyncAudioStatus("");
    applySyncStageUI();
  } catch (e) {
    setSyncAudioStatus("Couldn't reach the server to reload the full video.", "error");
  } finally {
    setSyncButtonsDisabled(false);
  }
});

syncAudioConfirmBtn.addEventListener("click", async () => {
  if (!syncAudioState) return;
  const { filename } = syncAudioState;
  const fieldDelayMs = parseFloat(syncAudioDelayInput.value) || 0;
  const appliedDelayMs = syncAudioState.appliedDelayMs != null ? syncAudioState.appliedDelayMs : 0;

  // Confirm Sync must render the full video with the SAME delay the
  // user actually previewed via Create Clip / Apply Sync - not
  // whatever happens to be sitting in the field, which can drift out
  // of sync with the preview if it was typed/nudged (dial buttons)
  // without an Apply Sync click afterward. Rendering an unpreviewed
  // value here is exactly how a full sync ends up not matching the
  // delay the user actually decided on.
  if (Math.abs(fieldDelayMs - appliedDelayMs) > 0.001) {
    setSyncAudioStatus(
      `Delay changed to ${fieldDelayMs} ms since the last preview (clip is playing at ${appliedDelayMs} ms). `
      + `Click "Apply Sync" to preview it before confirming.`,
      "error",
    );
    return;
  }
  const delayMs = appliedDelayMs;

  setSyncButtonsDisabled(true);
  setSyncAudioStatus("Synching full video, please wait...");
  await detachSyncPlayer();

  try {
    const res = await fetch("/api/jobs/sync-audio/render-full", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename, delay_ms: delayMs }),
    });
    const data = await res.json();
    if (!res.ok) {
      setSyncAudioStatus(data.error || "Couldn't render the full synced video.", "error");
      return;
    }
    syncAudioState.stage = "full";
    syncAudioState.everCreatedThisSession = true;
    syncAudioState.lastDelayMs = delayMs;
    setSyncPlayerSource(filename, "sync-full");
    syncAudioStageBadge.textContent = "Full Video Synchronized";
    setSyncAudioStatus("");
    applySyncStageUI();
  } catch (e) {
    setSyncAudioStatus("Couldn't reach the server to render the full synced video.", "error");
  } finally {
    setSyncButtonsDisabled(false);
  }
});

syncAudioAcceptBtn.addEventListener("click", async () => {
  if (!syncAudioState) return;
  const { filename } = syncAudioState;
  const delayMs = syncAudioState.lastDelayMs != null
    ? syncAudioState.lastDelayMs
    : (parseFloat(syncAudioDelayInput.value) || 0);

  setSyncButtonsDisabled(true);
  setSyncAudioStatus("Accepting the sync...");
  await detachSyncPlayer();

  try {
    const res = await fetch("/api/jobs/sync-audio/accept", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename, delay_ms: delayMs }),
    });
    const data = await res.json();
    if (!res.ok) {
      setSyncAudioStatus(data.error || "Couldn't accept the sync.", "error");
      setSyncButtonsDisabled(false);
      return;
    }
    state.jobs.set(filename, data.job);
    renderLedger();
    flashStatus(`Synchronized: ${filename}`);
    hideSyncAudioModal();
  } catch (e) {
    setSyncAudioStatus("Couldn't reach the server to accept the sync.", "error");
    setSyncButtonsDisabled(false);
  }
});

syncAudioDiscardBtn.addEventListener("click", async () => {
  if (!syncAudioState) return;
  const { filename } = syncAudioState;

  setSyncButtonsDisabled(true);
  setSyncAudioStatus("Discarding the full render...");
  await detachSyncPlayer();

  try {
    const res = await fetch("/api/jobs/sync-audio/discard-full", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename }),
    });
    const data = await res.json();
    if (!res.ok) {
      setSyncAudioStatus(data.error || "Couldn't discard the full render.", "error");
      return;
    }
    syncAudioState.stage = "clip";
    setSyncPlayerSource(filename, "sync-clip");
    syncAudioStageBadge.textContent = `Clip from ${formatClipTime(syncAudioState.clipStart)} loaded`;
    setSyncAudioStatus("");
    applySyncStageUI();
  } catch (e) {
    setSyncAudioStatus("Couldn't reach the server to discard the full render.", "error");
  } finally {
    setSyncButtonsDisabled(false);
  }
});

// UI-only teardown used right after a successful Accept - the backend
// has already cleaned up everything, so no cancel call is needed.
function hideSyncAudioModal() {
  syncAudioModal.classList.add("hidden");
  syncAudioPlayer.pause();
  syncAudioPlayer.removeAttribute("src");
  syncAudioPlayer.load();
  syncAudioState = null;
}

async function closeSyncAudioModal(runCancel = true) {
  const session = syncAudioState;
  await detachSyncPlayer();
  syncAudioModal.classList.add("hidden");
  syncAudioState = null;

  if (runCancel && session && session.everCreatedThisSession) {
    try {
      await fetch("/api/jobs/sync-audio/cancel", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: session.filename }),
      });
    } catch (e) {
      // Best-effort - an unconfirmed render being left behind isn't
      // harmful, just untidy; the next sync session will overwrite it.
    }
  }
}

el("sync-audio-close").addEventListener("click", () => closeSyncAudioModal(true));
el("sync-audio-cancel-btn").addEventListener("click", () => closeSyncAudioModal(true));

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

// The four functions below back both the card's options-menu items and
// its quick-action icon row, so the two entry points can't drift apart.
async function renameJobPrompt(filename) {
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
}

// navigator.clipboard.writeText() only works on https:// or localhost -
// browsers gate it purely by URL scheme, so a plain-http Tailscale address
// (even though Tailscale itself encrypts the connection) gets refused,
// especially on mobile Safari/Chrome. Fall back to the older execCommand
// copy trick, and as a last resort a prompt() the user can manually
// select-and-copy from. Returns true if a real clipboard write succeeded
// (only then do we claim "copied" - the other paths tell the user what
// happened instead of overpromising).
async function copyTextToClipboard(text) {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (e) {
      // fall through to the legacy path below
    }
  }

  try {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.style.position = "fixed";
    textarea.style.top = "-1000px";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    const ok = document.execCommand("copy");
    textarea.remove();
    if (ok) return true;
  } catch (e) {
    // fall through to the manual prompt below
  }

  window.prompt("Couldn't copy automatically - select and copy manually:", text);
  return false;
}

async function copyJobLink(filename, directUrl) {
  try {
    let urlToCopy = directUrl || "";
    if (!urlToCopy) {
      // Fallback for ledger entries with no recorded url (e.g. a file
      // picked up straight off disk with no queue record) - only the
      // history log might have it. Downloading/queued jobs always have
      // a url on the job object itself, so this path is skipped for them.
      const res = await fetch("/api/history-search", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: filename }),
      });
      const data = await res.json();
      urlToCopy = data.url || "";
    }
    if (urlToCopy) {
      const copied = await copyTextToClipboard(urlToCopy);
      if (copied) flashStatus("Link copied to clipboard.");
    } else {
      flashStatus("No link found for this file.");
    }
  } catch (e) {
    flashStatus("Couldn't copy the link.");
  }
}

async function copyJobFilename(filename) {
  try {
    const copied = await copyTextToClipboard(filename);
    if (copied) flashStatus(`Copied name: ${filename}`);
  } catch (e) {
    flashStatus("Couldn't copy the file name.");
  }
}

// Shows the Transfer Original / Transfer Converted / Cancel prompt and
// resolves with "original", "reencoded", or null (cancelled). Same modal
// for both twin kinds - info.kind ("reencoded" or "synchronized") just
// swaps the second card's label and button text.
function promptReencodeChoice(info) {
  return new Promise((resolve) => {
    const isSync = info.kind === "synchronized";
    reencodeChoiceTitle.textContent = isSync ? "Synchronized version found" : "Re-encoded version found";
    reencodeChoiceReencodedLabel.textContent = isSync ? "Synchronized" : "Re-encoded";
    reencodeChoiceReencodedBtn.textContent = isSync ? "Transfer Synchronized" : "Transfer Re-encoded";

    reencodeChoiceFilename.textContent = info.filename;
    reencodeChoiceOriginalSize.textContent = info.original.size_label || "Unknown size";
    reencodeChoiceOriginalRes.textContent = info.original.width && info.original.height
      ? `${info.original.width}\u00d7${info.original.height}` : "";
    reencodeChoiceReencodedSize.textContent = info.reencoded.size_label || "Unknown size";
    reencodeChoiceReencodedRes.textContent = info.reencoded.width && info.reencoded.height
      ? `${info.reencoded.width}\u00d7${info.reencoded.height}` : "";

    const cleanup = (result) => {
      reencodeChoiceModal.classList.add("hidden");
      reencodeChoiceCancelBtn.removeEventListener("click", onCancel);
      reencodeChoiceOriginalBtn.removeEventListener("click", onOriginal);
      reencodeChoiceReencodedBtn.removeEventListener("click", onReencoded);
      resolve(result);
    };
    const onCancel = () => cleanup(null);
    const onOriginal = () => cleanup("original");
    const onReencoded = () => cleanup("reencoded");

    reencodeChoiceCancelBtn.addEventListener("click", onCancel);
    reencodeChoiceOriginalBtn.addEventListener("click", onOriginal);
    reencodeChoiceReencodedBtn.addEventListener("click", onReencoded);
    reencodeChoiceModal.classList.remove("hidden");
  });
}

// Posts the actual move request. Returns "moved", "cancelled", or
// "error" (in which case an alert has already been shown).
async function requestMoveToTarget(filename, variant) {
  try {
    const res = await fetch("/api/jobs/move-to-target", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(variant ? { filename, variant } : { filename }),
    });
    const data = await res.json();
    if (res.status === 409 && data.needs_decision) {
      const choice = await promptReencodeChoice(data);
      if (!choice) return "cancelled";
      return requestMoveToTarget(filename, choice);
    }
    if (!res.ok) {
      window.alert(`Couldn't move "${filename}":\n${data.error || "Unknown error"}`);
      return "error";
    }
    return "moved";
  } catch (e) {
    window.alert("Couldn't reach the server to move that file.");
    return "error";
  }
}

async function moveJobToTarget(filename) {
  const result = await requestMoveToTarget(filename, null);
  if (result === "moved") {
    state.jobs.delete(filename);
    renderLedger();
    flashStatus(`Moved to target: ${filename}`);
  }
}

el("ctx-rename-file").addEventListener("click", () => {
  const filename = jobMenu.dataset.filename;
  closeMenus();
  renameJobPrompt(filename);
});

el("ctx-copy-link").addEventListener("click", () => {
  const filename = jobMenu.dataset.filename;
  const directUrl = jobMenu.dataset.url || "";
  closeMenus();
  copyJobLink(filename, directUrl);
});

el("ctx-copy-filename").addEventListener("click", () => {
  const filename = jobMenu.dataset.filename;
  closeMenus();
  copyJobFilename(filename);
});

el("ctx-move-to-target").addEventListener("click", () => {
  const filename = jobMenu.dataset.filename;
  closeMenus();
  moveJobToTarget(filename);
});

async function requestReplaceSource(filename, variant, deleteTagIds) {
  try {
    const body = { filename };
    if (variant) body.variant = variant;
    if (deleteTagIds && deleteTagIds.length) body.delete_tag_ids = deleteTagIds;
    const res = await fetch("/api/jobs/replace-source", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (res.status === 409 && data.needs_decision) {
      const choice = await promptReencodeChoice(data);
      if (!choice) return { status: "cancelled" };
      return requestReplaceSource(filename, choice, deleteTagIds);
    }
    if (!res.ok) {
      window.alert(`Couldn't replace the Stash source:\n${data.error || "Unknown error"}`);
      return { status: "error" };
    }
    return {
      status: "replaced",
      deleted_tag_names: data.deleted_tag_names || [],
      tag_delete_error: data.tag_delete_error || null,
    };
  } catch (e) {
    window.alert("Couldn't reach the server to replace the Stash source.");
    return { status: "error" };
  }
}

// Shows the replace-source confirm modal, with one checkbox per tag on
// job.stash_tags (the full tag list saved when the scene was imported -
// see stash_integration.import_stash_scene), so any combination of them
// can be picked for deletion from the Stash scene. Resolves null if
// cancelled, or { deleteTagIds } if confirmed.
function promptReplaceSourceConfirm(job) {
  return new Promise((resolve) => {
    const filenameEl = el("replace-source-filename");
    const tagsSection = el("replace-source-tags-section");
    const tagList = el("replace-source-tag-list");
    const modal = el("replace-source-modal");
    const cancelBtn = el("replace-source-cancel-btn");
    const confirmBtn = el("replace-source-confirm-btn");

    filenameEl.textContent = job.filename;
    tagList.innerHTML = "";
    const tags = job.stash_tags || [];
    if (tags.length) {
      tags.forEach((tag) => {
        const row = document.createElement("div");
        row.className = "checkbox-row";
        const checkboxId = `replace-source-tag-${tag.id}`;
        row.innerHTML = `<input type="checkbox" id="${checkboxId}" data-tag-id="${tag.id}"><label for="${checkboxId}"></label>`;
        row.querySelector("label").textContent = tag.name;
        tagList.appendChild(row);
      });
      tagsSection.classList.remove("hidden");
    } else {
      tagsSection.classList.add("hidden");
    }

    const cleanup = (result) => {
      modal.classList.add("hidden");
      cancelBtn.removeEventListener("click", onCancel);
      confirmBtn.removeEventListener("click", onConfirm);
      resolve(result);
    };
    const onCancel = () => cleanup(null);
    const onConfirm = () => {
      const deleteTagIds = Array.from(tagList.querySelectorAll("input[type=checkbox]:checked"))
        .map((cb) => cb.dataset.tagId);
      cleanup({ deleteTagIds });
    };

    cancelBtn.addEventListener("click", onCancel);
    confirmBtn.addEventListener("click", onConfirm);
    modal.classList.remove("hidden");
  });
}

el("ctx-replace-source").addEventListener("click", async () => {
  const filename = jobMenu.dataset.filename;
  closeMenus();
  const job = state.jobs.get(filename);
  if (!job || job.source_type !== "stash") return;
  const confirmChoice = await promptReplaceSourceConfirm(job);
  if (!confirmChoice) return;
  const result = await requestReplaceSource(filename, null, confirmChoice.deleteTagIds);
  if (result.status === "replaced") {
    state.jobs.delete(filename);
    renderLedger();
    if (result.deleted_tag_names.length) {
      flashStatus(`Replaced Stash source and removed tag(s) "${result.deleted_tag_names.join(", ")}": ${filename}`);
    } else if (result.tag_delete_error) {
      flashStatus(`Replaced Stash source: ${filename} (couldn't remove tag(s): ${result.tag_delete_error})`);
    } else {
      flashStatus(`Replaced Stash source: ${filename}`);
    }
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

// ── Search History Mode's per-entry menu actions ────────────────
el("ctx-history-copy-link").addEventListener("click", async () => {
  const url = historyMenu.dataset.url || "";
  closeMenus();
  if (!url) { flashStatus("No link recorded for this entry."); return; }
  try {
    const copied = await copyTextToClipboard(url);
    if (copied) flashStatus("Link copied to clipboard.");
  } catch (e) {
    flashStatus("Couldn't copy the link.");
  }
});

el("ctx-history-copy-name").addEventListener("click", async () => {
  const filename = historyMenu.dataset.filename || "";
  closeMenus();
  try {
    const copied = await copyTextToClipboard(filename);
    if (copied) flashStatus(`Copied name: ${filename}`);
  } catch (e) {
    flashStatus("Couldn't copy the file name.");
  }
});

el("ctx-history-open-location").addEventListener("click", async () => {
  if (!IS_LOCAL) { flashStatus(REMOTE_ONLY_TOOLTIP); return; }
  const filename = historyMenu.dataset.filename;
  closeMenus();
  try {
    const res = await fetch("/api/jobs/open-folder", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename }),
    });
    const data = await res.json();
    if (!res.ok) {
      window.alert(`Couldn't open Explorer:\n${data.error || "File not found - it may have been moved or deleted."}`);
    }
  } catch (e) {
    window.alert("Couldn't reach the server to open Explorer.");
  }
});

el("ctx-history-delete").addEventListener("click", async () => {
  const filename = historyMenu.dataset.filename;
  const url = historyMenu.dataset.url;
  const timestamp = historyMenu.dataset.timestamp;
  closeMenus();
  if (!window.confirm(`Remove "${filename}" from download history?\n\nThis only removes the history record - it doesn't delete any file.`)) return;

  try {
    const res = await fetch("/api/history/delete", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ timestamp, filename, url }),
    });
    const data = await res.json();
    if (!data.ok) {
      flashStatus("Couldn't find that entry to delete.");
      return;
    }
    state.historyEntries = state.historyEntries.filter(
      (e) => !(e.timestamp === timestamp && e.filename === filename && e.url === url)
    );
    renderLedger();
    flashStatus("Removed from history.");
  } catch (e) {
    window.alert("Couldn't reach the server to delete that history entry.");
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
  historyMenu.classList.add("hidden");
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
    historyMenu.contains(e.target) ||
    ALL_FLYOUTS.some((flyout) => flyout.contains(e.target));
  if (!clickedInsideAMenu) closeMenus();
});

el("ctx-tag-toggle").addEventListener("click", () => {
  state.tagDomain = !state.tagDomain;
  el("ctx-tag-toggle").querySelector(".ctx-check").textContent = state.tagDomain ? "✓" : "";
  saveDownloadPrefs();
});

el("ctx-m3u-toggle").addEventListener("click", () => {
  state.m3uSniffer = !state.m3uSniffer;
  el("ctx-m3u-toggle").querySelector(".ctx-check").textContent = state.m3uSniffer ? "✓" : "";
  if (state.m3uSniffer) {
    inputField.placeholder = "Paste a link, then press ENTER...";
  }
  saveDownloadPrefs();
});

el("ctx-auto-m3u-retry-toggle").addEventListener("click", () => {
  state.autoM3uRetry = !state.autoM3uRetry;
  el("ctx-auto-m3u-retry-toggle").querySelector(".ctx-check").textContent = state.autoM3uRetry ? "✓" : "";
  saveDownloadPrefs();
});

el("ctx-auto-confirm-titles-toggle").addEventListener("click", () => {
  state.autoConfirmTitles = !state.autoConfirmTitles;
  el("ctx-auto-confirm-titles-toggle").querySelector(".ctx-check").textContent = state.autoConfirmTitles ? "✓" : "";
  saveDownloadPrefs();
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

el("ctx-check-ytdlp-update").addEventListener("click", async () => {
  closeMenus();
  flashStatus("Checking for yt-dlp update...");
  try {
    const res = await fetch("/api/version/check", { method: "POST" });
    const data = await res.json();
    applyVersionState(data);
    if (!data.version) {
      flashStatus("Couldn't reach yt-dlp to check for an update.");
    } else if (data.just_updated) {
      flashStatus(`yt-dlp updated to v${data.version}.`);
    } else {
      flashStatus(`yt-dlp is up to date (v${data.version}).`);
    }
  } catch (e) {
    flashStatus("Couldn't check for a yt-dlp update.");
  }
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

  return { open, close, applyPath: apply };
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
resDropdown.addEventListener("change", saveDownloadPrefs);

// ── Mode buttons (Download / Search History / Encode) ───────────
function setAppModeDownload() {
  setView("downloads");
  resetToReady();
  updateModeButtons();
}

function setAppModeSearchHistory() {
  setView("downloads");
  exitSelectionMode();
  state.appMode = "SEARCH_HISTORY";
  inputField.disabled = false;
  inputField.value = "";
  inputField.placeholder = "Paste target file name to find link...";
  inputField.focus();
  updateModeButtons();
  enterHistoryModeUI();
  refreshSearchHistory();
}

function setView(view) {
  if (state.currentView === view) return;
  state.currentView = view;
  const isEncode = view === "encode";
  if (isEncode) exitSelectionMode();
  el("download-view").classList.toggle("hidden", isEncode);
  el("encode-view").classList.toggle("hidden", !isEncode);
  if (isEncode) {
    inputField.disabled = true;
    inputField.value = "";
    inputField.placeholder = "Switch to Download or Search History mode to paste a URL";
    // Always rescan the download folder when showing the Encode Manager,
    // not just when the source list happens to be empty - otherwise a
    // file pasted in manually while looking at another view stays
    // invisible (and un-probed) until "New Encode Job" is opened, which
    // separately does this same refresh.
    refreshDownloadLedger().then(refreshEncodeSources);
  } else {
    inputField.disabled = false;
  }
}

function setAppModeEncode() {
  setView("encode");
  updateModeButtons();
}

function updateModeButtons() {
  const isEncode = state.currentView === "encode";
  const isDl = !isEncode && state.appMode === "DOWNLOAD";
  const isHistory = !isEncode && state.appMode === "SEARCH_HISTORY";
  dlModeBtn.classList.toggle("active", isDl);
  historyModeBtn.classList.toggle("active", isHistory);
  encodeModeBtn.classList.toggle("active", isEncode);
  dlModeBtn.title = isDl ? "Download Mode Active" : "Switch to Download Mode (Ctrl+D)";
  historyModeBtn.title = isHistory ? "Search History Mode Active" : "Switch to Search History Mode (Ctrl+F)";
  encodeModeBtn.title = isEncode ? "Encode Manager Active" : "Switch to Encode Manager";
}

// Search History Mode reuses the same ledger toolbar (filter + sort),
// but "Move All"/"Refresh" and the audio-only filter don't
// apply to history records, and there's no file size to sort by.
function enterHistoryModeUI() {
  // Remove or comment out the old control bar references
  // el("control-bar").classList.add("hidden");
  ledgerAudioFilterBtn.classList.add("hidden");
  // Remove the hide-completed reference
  // ledgerHideCompletedBtn.classList.add("hidden");
  selectModeBtn.classList.add("hidden");
  // Disable the size sort option
  const sizeOption = ledgerSortSelect.querySelector('option[value="size"]');
  if (sizeOption) sizeOption.disabled = true;
  if (state.sortField === "size") {
    state.sortField = "added";
    ledgerSortSelect.value = "added";
  }
}

function exitHistoryModeUI() {
  // el("control-bar").classList.remove("hidden");
  ledgerAudioFilterBtn.classList.remove("hidden");
  // ledgerHideCompletedBtn.classList.remove("hidden");
  selectModeBtn.classList.remove("hidden");
  const sizeOption = ledgerSortSelect.querySelector('option[value="size"]');
  if (sizeOption) sizeOption.disabled = false;
}

downloadSubmitBtn.addEventListener("click", () => {
  const currentValue = inputField.value.trim();
  if (state.currentView === "encode") {
    setAppModeDownload();
    if (currentValue) setTimeout(() => handleEnterPipeline(), 0);
    return;
  }
  if (state.appMode === "SEARCH_HISTORY") {
    state.appMode = "DOWNLOAD";
    exitHistoryModeUI();
    updateModeButtons();
    renderLedger();
  }
  handleEnterPipeline();
});

dlModeBtn.addEventListener("click", () => {
  if (state.currentView === "encode") {
    setAppModeDownload();
    return;
  }
  if (state.current === "EDITING") {
    handleEnterPipeline(); // proceed with the download using the current input as the title
    return;
  }
  const currentValue = inputField.value.trim();
  const isValidUrl = /^https?:\/\//i.test(currentValue);
  if (isValidUrl && state.current === "READY") {
    const wasHistoryMode = state.appMode === "SEARCH_HISTORY";
    state.appMode = "DOWNLOAD";
    if (wasHistoryMode) {
      exitHistoryModeUI();
      renderLedger();
    }
    updateModeButtons();
    beginDownloadPipeline(currentValue);
  } else {
    setAppModeDownload();
  }
});
encodeModeBtn.addEventListener("click", setAppModeEncode);
historyModeBtn.addEventListener("click", setAppModeSearchHistory);

// ── Gear button (opens the options/logo menu; this menu is no longer
// reachable via right-click — the gear is now the only way in) ────
gearBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  const rect = gearBtn.getBoundingClientRect();
  openLogoMenu(rect.left, rect.bottom + 4);
});

// ── Folder quick-select dropdowns (click the DL:/Target: line) ──
// Lists saved folders for one-tap switching, dropping straight down
// from whichever line was clicked; falls through to the full
// modal (Browse.../type a path) via the last entry.
function openFolderQuickDropdown({ dropdownEl, listEl, anchorEl, getRecentDirs, getCurrentPath, applyPath, openAdvanced, noneLabel }) {
  closeOtherFlyouts(dropdownEl);
  listEl.innerHTML = "";

  const current = getCurrentPath() || "";
  const dirs = getRecentDirs();

  if (dirs.length === 0) {
    const empty = document.createElement("div");
    empty.className = "ctx-item ctx-disabled";
    empty.textContent = noneLabel;
    listEl.appendChild(empty);
  } else {
    for (const path of dirs) {
      const item = document.createElement("div");
      item.className = "ctx-item";
      item.textContent = path;
      item.title = path;
      if (path === current) {
        item.classList.add("ctx-disabled");
      } else {
        item.addEventListener("click", () => {
          dropdownEl.classList.add("hidden");
          applyPath(path);
        });
      }
      listEl.appendChild(item);
    }
  }

  const sep = document.createElement("div");
  sep.className = "ctx-separator";
  listEl.appendChild(sep);

  const advanced = document.createElement("div");
  advanced.className = "ctx-item";
  advanced.textContent = "Browse / set custom folder...";
  advanced.addEventListener("click", () => {
    dropdownEl.classList.add("hidden");
    openAdvanced();
  });
  listEl.appendChild(advanced);

  positionDropdownBelow(dropdownEl, anchorEl);
}

folderStatusSave.addEventListener("click", (e) => {
  e.stopPropagation();
  logoMenu.classList.add("hidden");
  jobMenu.classList.add("hidden");
  historyMenu.classList.add("hidden");
  openFolderQuickDropdown({
    dropdownEl: dlFolderQuickMenu, listEl: dlFolderQuickList, anchorEl: folderStatusSave,
    getRecentDirs: () => state.recentDirs,
    getCurrentPath: () => ctxSaveDir.title || "",
    applyPath: downloadFolderModal.applyPath,
    openAdvanced: () => downloadFolderModal.open(),
    noneLabel: "No saved download folders yet",
  });
});

folderStatusTarget.addEventListener("click", (e) => {
  e.stopPropagation();
  logoMenu.classList.add("hidden");
  jobMenu.classList.add("hidden");
  historyMenu.classList.add("hidden");
  openFolderQuickDropdown({
    dropdownEl: targetFolderQuickMenu, listEl: targetFolderQuickList, anchorEl: folderStatusTarget,
    getRecentDirs: () => state.recentTargetDirs,
    getCurrentPath: () => ctxTargetDir.title || "",
    applyPath: targetFolderModal.applyPath,
    openAdvanced: () => targetFolderModal.open(),
    noneLabel: "No saved target folders yet",
  });
});

// ── Control bar ────────────────────────────────────────────────
let _refreshInFlight = false;
async function refreshDownloadLedger() {
  // Guards against overlapping calls piling up if a previous refresh
  // (manual click or the auto-refresh poll below) is still in flight -
  // e.g. on a slow network the poll interval could otherwise fire again
  // before the first request even returns.
  if (_refreshInFlight) return;
  _refreshInFlight = true;
  try {
    const res = await fetch("/api/refresh", { method: "POST" });
    const data = await res.json();
    loadJobsIntoMap(data.jobs);
    renderLedger();
  } finally {
    _refreshInFlight = false;
  }
}

el("refresh-btn").addEventListener("click", refreshDownloadLedger);

// Keeps the queue (and in particular the has_twin/HAS TWIN pill, which
// only gets recomputed server-side on a scan) current without the user
// needing to press Refresh themselves - e.g. a twin dropped into
// Converted/ by hand, or Encode Manager history lost across a server
// restart, would otherwise only surface on the next manual Refresh.
// 30s (matching the Stash-status poll) rather than something tighter -
// everything the app does to its own jobs already arrives instantly
// over the websocket, so this poll's only real job is catching
// external changes (a file dropped in by hand), which was never on a
// tight SLA to begin with; a longer interval also matters more now
// that this does a full os.listdir() + Converted/ listing + potential
// ffprobe calls each tick, which isn't free on a networked path.
// Silently swallow failures (e.g. a momentary network blip on
// Tailscale) rather than spamming flashStatus every few seconds.
setInterval(() => {
  refreshDownloadLedger().catch(() => {});
}, 30000);


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

    const movedNames = [...data.moved];
    const failed = [...data.failed];

    // Files with a re-encoded twin weren't touched yet - walk them one
    // at a time so each gets its own Original/Re-encoded/Cancel prompt.
    for (const info of data.pending_decisions || []) {
      const choice = await promptReencodeChoice(info);
      if (!choice) continue; // cancelled - leave it in the ledger
      const result = await requestMoveToTarget(info.filename, choice);
      if (result === "moved") {
        state.jobs.delete(info.filename);
        movedNames.push(info.filename);
      } else if (result === "error") {
        failed.push({ filename: info.filename, error: "Move failed - see alert." });
      }
    }
    renderLedger();

    let message = `Moved ${movedNames.length} item(s) to target.`;
    if (failed.length > 0) {
      message += `\n\n${failed.length} failed:\n` +
        failed.map((f) => `- ${f.filename}: ${f.error}`).join("\n");
    }
    window.alert(message);
  } catch (e) {
    window.alert("Couldn't reach the server to move files.");
  }
});

// ── Core pipeline: READY -> (fetch title | sniff m3u8) -> EDITING -> submit ──
function resetToReady() {
  const wasHistoryMode = state.appMode === "SEARCH_HISTORY";
  state.appMode = "DOWNLOAD";
  state.current = "READY";
  state.stagedUrl = "";
  state.targetUrl = "";
  inputField.disabled = false;
  modeContainer.style.pointerEvents = "";
  inputField.value = "";
  inputField.placeholder = "Paste a link, then press ENTER...";
  updateUrlArgsChip();
  updateModeButtons();
  if (wasHistoryMode) {
    exitHistoryModeUI();
    renderLedger();
  }
}

async function submitDownloadJob(url, filename, resCap, originalPastedUrl) {
  try {
    await fetch("/api/jobs", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url, filename, res_cap: resCap, original_pasted_url: originalPastedUrl,
      }),
    });
  } catch (e) { /* job_added will simply never arrive */ }
  resetToReady();
}

async function submitPlaylistBatch(entries, resCap, playlistTitle) {
  const label = playlistTitle ? `"${playlistTitle}"` : "This playlist";
  const proceed = window.confirm(
    `${label} has ${entries.length} videos.\n\nQueue all ${entries.length} for download (max 3 at a time)?`
  );
  if (!proceed) {
    resetToReady();
    return;
  }
  try {
    await fetch("/api/playlist/queue", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entries, res_cap: resCap }),
    });
  } catch (e) {
    window.alert("Couldn't reach the server to queue the playlist.");
  }
  resetToReady();
}

async function handleEnterPipeline() {
  const typedValue = inputField.value.trim();

  if (state.current === "READY") {
    if (/^https?:\/\//i.test(typedValue)) {
      await beginDownloadPipeline(typedValue);
    } else {
      inputField.value = "";
      inputField.placeholder = "No valid URL - paste a link and press ENTER.";
      setTimeout(() => { inputField.placeholder = "Paste a link, then press ENTER..."; }, 2500);
    }
  } else if (state.current === "EDITING") {
    const finalTitle = typedValue;
    if (finalTitle) {
      await submitDownloadJob(state.stagedUrl, finalTitle, resDropdown.value, state.targetUrl);
    }
  } else if (state.current === "FETCHING" || state.current === "INTERCEPTING") {
    // If we're in the middle of fetching, don't do anything — the pipeline is already running
    return;
  }
}

// Triggered when a normal download fails server-side: the failed job is
// pulled from the ledger before the user ever sees an ERROR card, and
// the input box is hijacked to run the same M3U sniffing flow a manual
// "Find Link Mode" submission would - message, sniff, then the fetched
// stream's suggested name is staged in the input box for the user to
// review/edit before pressing Enter to start the download (or submitted
// immediately if Auto-Confirm Titles is on).
async function beginM3uRetryPipeline(url, resCap, originalPastedUrl) {
  state.targetUrl = originalPastedUrl || url;
  state.current = "INTERCEPTING";
  inputField.disabled = true;
  modeContainer.style.pointerEvents = "none";
  inputField.value = "yt-dlp failed to download, trying M3U sniffing method...";
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
    if (resCap) resDropdown.value = resCap;
    if (state.autoConfirmTitles) {
      await submitDownloadJob(data.stream_url, data.suggested_title, resDropdown.value, state.targetUrl);
    } else {
      handleIntercepted(data.stream_url, data.suggested_title);
    }
  } catch (e) {
    handleInterceptionFailure(String(e));
  }
}

// Playlist/channel detection runs on every pasted URL, in parallel with
// whatever the normal single-item path (M3U sniff or fetch-title) is
// already doing - so the common single-video case pays no extra
// latency waiting on the playlist probe before it can proceed. If the
// probe comes back positive, the single-item result is discarded and
// every entry is queued as its own ledger item instead (see
// job_manager.start_playlist_batch - each playlist gets its own
// 3-concurrent-download cap, independent of any other playlist queued
// separately).
async function beginDownloadPipeline(url) {
  state.targetUrl = url;
  inputField.disabled = true;
  modeContainer.style.pointerEvents = "none";

  const probePromise = fetch("/api/playlist/probe", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, tag_domain: state.tagDomain }),
  }).then((r) => r.json()).catch(() => ({ is_playlist: false }));

  if (state.m3uSniffer) {
    state.current = "INTERCEPTING";
    inputField.value = "Sniffing m3u...";
    try {
      const res = await fetch("/api/find-link", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, tag_domain: state.tagDomain }),
      });
      const probeData = await probePromise;
      if (probeData.is_playlist) {
        await submitPlaylistBatch(probeData.entries, resDropdown.value, probeData.playlist_title);
        return;
      }
      if (!res.ok) {
        const err = await res.json();
        handleInterceptionFailure(err.error || "Unknown error");
        return;
      }
      const data = await res.json();
      if (state.autoConfirmTitles) {
        await submitDownloadJob(data.stream_url, data.suggested_title, resDropdown.value, state.targetUrl);
      } else {
        handleIntercepted(data.stream_url, data.suggested_title);
      }
    } catch (e) {
      const probeData = await probePromise;
      if (probeData.is_playlist) {
        await submitPlaylistBatch(probeData.entries, resDropdown.value, probeData.playlist_title);
        return;
      }
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
      const probeData = await probePromise;
      if (probeData.is_playlist) {
        await submitPlaylistBatch(probeData.entries, resDropdown.value, probeData.playlist_title);
        return;
      }
      if (state.autoConfirmTitles) {
        await submitDownloadJob(url, data.title, resDropdown.value, state.targetUrl);
      } else {
        promptTitleEdit(data.title);
      }
    } catch (e) {
      const probeData = await probePromise;
      if (probeData.is_playlist) {
        await submitPlaylistBatch(probeData.entries, resDropdown.value, probeData.playlist_title);
        return;
      }
      if (state.autoConfirmTitles) {
        await submitDownloadJob(url, "Unknown Title", resDropdown.value, state.targetUrl);
      } else {
        promptTitleEdit("Unknown Title");
      }
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
  // ── Modals that should trap Esc ──────────────────────────────
  if (!stashImportModal.classList.contains("hidden")) return;
  if (!stashTagModal.classList.contains("hidden")) return;
  if (!stashTagResultsModal.classList.contains("hidden")) {
    if (e.key === "Escape") closeStashTagResultsModal();
    return;
  }
  if (!folderModal.classList.contains("hidden")) return;
  if (!targetFolderModalEl.classList.contains("hidden")) return;
  if (!externalProgramsModal.classList.contains("hidden")) {
    if (e.key === "Escape") closeExternalProgramsModal();
    return;
  }
  if (!programFormModal.classList.contains("hidden")) return;
  if (!videoModal.classList.contains("hidden")) {
    if (e.key === "Escape") closeMediaModal();
    return;
  }
  if (!syncAudioModal.classList.contains("hidden")) {
    if (e.key === "Escape") closeSyncAudioModal(true);
    return;
  }
  if (!newEncodeJobModal.classList.contains("hidden")) {
    if (e.key === "Escape") closeNewEncodeJobModal();
    return;
  }

  // ── Ctrl+ shortcuts ──────────────────────────────────────────
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
    if (e.key.toLowerCase() === "f") { setAppModeSearchHistory(); e.preventDefault(); return; }
    if (e.key.toLowerCase() === "d") { setAppModeDownload(); e.preventDefault(); return; }
    if (e.key.toLowerCase() === "s" && state.appMode === "DOWNLOAD") {
      e.preventDefault();
      openStashMenuFlyout();
      return;
    }
  }

  // ── Escape: cancel editing or close ─────────────────────────
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

  // ── Enter: only if the input field is focused ──────────────
  if (e.key === "Enter" && document.activeElement === inputField) {
    e.preventDefault();
    if (state.appMode === "SEARCH_HISTORY") {
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

// ── Encode Manager ──────────────────────────────────────────────
const encodeQueueList = el("encode-queue-list");
const encodeFilterInput = el("encode-filter");
const encodeSortSelect = el("encode-sort");
const encodeSortDirBtn = el("encode-sort-dir");
const newEncodeJobBtn = el("new-encode-job-btn");
const openConvertedBtn = el("open-converted-btn");
const newEncodeJobModal = el("new-encode-job-modal");
const closeEncodeJobModalBtn = el("close-encode-job-modal");
const cancelEncodeJobModalBtn = el("cancel-encode-job-modal");
const addEncodeJobBtn = el("add-encode-job-btn");
const encodeSourceSelect = el("encode-source-select");
const encodeSourceBrowseRow = el("encode-source-browse-row");
const encodeSourceBrowsePath = el("encode-source-browse-path");
const encodeSourceBrowseBtn = el("encode-source-browse-btn");
const encodeSourceInfo = el("encode-source-info");
const encodeModeCrfBtn = el("encode-mode-crf-btn");
const encodeModeSizeBtn = el("encode-mode-size-btn");
const encodeCrfFields = el("encode-crf-fields");
const encodeSizeFields = el("encode-size-fields");
const encodeCodecSelect = el("encode-codec-select");
const encodeBackendSelect = el("encode-backend-select");
const encodeCrfSlider = el("encode-crf-slider");
const encodeCrfValue = el("encode-crf-value");
const encodeCrfHint = el("encode-crf-hint");
const encodePresetSelect = el("encode-preset-select");
const encodeTargetSizeInput = el("encode-target-size-input");
const encodeResolutionSelect = el("encode-resolution-select");
const encodeResolutionLabel = el("encode-resolution-label");
const encodeForceArCheck = el("encode-force-ar-check");
const encodeForceArFields = el("encode-force-ar-fields");
const encodeAspectQuickRow = el("encode-aspect-quick-row");
const encodeArWidthInput = el("encode-ar-width-input");
const encodeArHeightInput = el("encode-ar-height-input");
const encodeDeinterlaceCheck = el("encode-deinterlace-check");
const encodeAutocropCheck = el("encode-autocrop-check");
const encodeAdvancedToggle = el("encode-advanced-toggle");
const encodeAdvancedBody = el("encode-advanced-body");
const encodeAdvancedCaret = el("encode-advanced-caret");
const encodeAudioSelect = el("encode-audio-select");
const encodeContainerSelect = el("encode-container-select");
const encodeDenoiseCheck = el("encode-denoise-check");
const encodeSubtitlesSelect = el("encode-subtitles-select");
const encodeOversizedSelect = el("encode-oversized-select");
const encodeEstimateOriginal = el("encode-estimate-original");
const encodeEstimateLabel = el("encode-estimate-label");
const encodeEstimateValue = el("encode-estimate-value");
const encodeEstimateSavings = el("encode-estimate-savings");
const encodeEstimateRefreshBtn = el("encode-estimate-refresh-btn");
const encodeJobError = el("encode-job-error");

async function loadEncodeCapabilities() {
  try {
    const res = await fetch("/api/encode/capabilities");
    state.encodeCapabilities = await res.json();
  } catch (e) {
    state.encodeCapabilities = null;
  }
}

async function refreshEncodeSources() {
  try {
    const res = await fetch("/api/encode/sources");
    const data = await res.json();
    state.encodeSources = data.sources || [];
  } catch (e) {
    state.encodeSources = [];
  }
}

async function loadEncodeJobsSnapshot() {
  try {
    const res = await fetch("/api/encode/jobs");
    const jobs = await res.json();
    state.encodeJobs.clear();
    for (const job of jobs) state.encodeJobs.set(job.id, job);
    renderEncodeLedger();
    renderLedger();
  } catch (e) {
    // backend not reachable yet; ignore
  }
}

function savingsPct(job) {
  const finalSize = job.final_bytes || job.estimated_bytes;
  if (!finalSize || !job.source_size) return 0;
  return (1 - finalSize / job.source_size) * 100;
}

function getFilteredSortedEncodeJobs() {
  const jobs = Array.from(state.encodeJobs.values());
  const query = state.encodeFilterText.trim().toLowerCase();
  const filtered = query ? jobs.filter((j) => j.source_filename.toLowerCase().includes(query)) : jobs;

  if (state.encodeSortField === "added") {
    // The server already orders this list correctly (active/pending in
    // real run order, then finished jobs newest-first) - respect sort
    // direction rather than re-deriving an "added" order client-side,
    // which would fight with move-up reordering.
    return state.encodeSortDir === "desc" ? filtered : [...filtered].reverse();
  }

  const dirMul = state.encodeSortDir === "asc" ? 1 : -1;
  const sorted = [...filtered];
  sorted.sort((a, b) => {
    const cmp = state.encodeSortField === "size"
      ? (a.source_size || 0) - (b.source_size || 0)
      : savingsPct(a) - savingsPct(b);
    return cmp * dirMul;
  });
  return sorted;
}

function renderEncodeLedger() {
  encodeQueueList.innerHTML = "";
  for (const job of getFilteredSortedEncodeJobs()) {
    encodeQueueList.appendChild(buildEncodeJobCard(job));
  }
}

function updateEncodeJobCard(job) {
  const card = encodeQueueList.querySelector(`.job-card[data-job-id="${cssEscape(job.id)}"]`);
  if (!card) { renderEncodeLedger(); return; }
  // Encode cards change shape enough between states (badges, action
  // buttons, progress bar) that a full rebuild-and-swap is simpler and
  // plenty fast for the handful of cards a personal queue will ever show.
  card.replaceWith(buildEncodeJobCard(job));
}

function formatEta(seconds) {
  if (seconds == null) return "?";
  const s = Math.max(0, Math.round(seconds));
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return `${m}:${String(rem).padStart(2, "0")}`;
}

function formatDuration(seconds) {
  if (!seconds) return "";
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const rem = s % 60;
  return h > 0
    ? `${h}:${String(m).padStart(2, "0")}:${String(rem).padStart(2, "0")}`
    : `${m}:${String(rem).padStart(2, "0")}`;
}

function resolutionTargetLabel(job) {
  if (!job.output_width || !job.output_height) return "";
  if (job.resolution_cap && job.resolution_cap !== "source" && !job.force_ar) {
    return job.resolution_cap;
  }
  return `${job.output_width}×${job.output_height}`;
}

function buildEncodeSettingsSummary(job) {
  const parts = [job.resolution_cap && job.resolution_cap !== "source" ? job.resolution_cap : "Source res"];
  if (job.deinterlace) parts.push("deinterlace on");
  if (job.auto_crop) parts.push("auto-crop");
  if (job.denoise) parts.push("denoise on");
  if (job.audio_mode !== "copy") parts.push(`audio ${job.audio_mode}`);
  if (job.status === "DONE") {
    parts.push("saved to Converted/");
    if (job.elapsed_seconds) parts.push(`took ${formatEta(job.elapsed_seconds)}`);
  }
  return parts.join(" · ");
}

function buildEncodeJobCard(job) {
  const card = document.createElement("div");
  card.className = "job-card";
  card.dataset.jobId = job.id;

  const statusClass = job.status === "DONE" ? "done"
    : job.status === "ERROR" ? "error"
    : job.status === "CANCELLED" ? "cancelled"
    : job.status === "QUEUED" ? "queued"
    : "";
  if (statusClass) card.classList.add(statusClass);

  const thumb = document.createElement("div");
  thumb.className = "job-thumb";
  const placeholder = document.createElement("span");
  placeholder.className = "job-thumb-placeholder";
  placeholder.textContent = job.status === "DONE" ? "▶" : job.status === "ERROR" ? "!" : "▤";
  thumb.appendChild(placeholder);
  card.appendChild(thumb);

  const body = document.createElement("div");
  body.className = "job-card-body";

  const titleRow = document.createElement("div");
  titleRow.className = "job-title-row";
  const title = document.createElement("div");
  title.className = "job-title";
  title.textContent = job.source_filename;
  title.title = job.source_path;
  titleRow.appendChild(title);

  if (job.status === "ENCODING" || job.status === "QUEUED") {
    const codecBadge = document.createElement("span");
    codecBadge.className = "codec-badge";
    const shortCodec = (job.codec_label || "").split(" ")[0];
    codecBadge.textContent = job.mode === "size" ? shortCodec : `${shortCodec} · CRF${job.crf}`;
    titleRow.appendChild(codecBadge);

    if (job.encoder_backend && job.encoder_backend !== "software") {
      const fastBadge = document.createElement("span");
      fastBadge.className = "fast-badge";
      fastBadge.textContent = job.encoder_backend.toUpperCase();
      titleRow.appendChild(fastBadge);
    }
  }

  if (job.oversized) {
    const oversizedBadge = document.createElement("span");
    oversizedBadge.className = "oversized-badge";
    oversizedBadge.textContent = "OVERSIZED";
    titleRow.appendChild(oversizedBadge);
  }

  const statusPill = document.createElement("span");
  if (job.status === "ENCODING") {
    statusPill.className = "status-pill pct";
    statusPill.textContent = `${job.pct}%`;
  } else if (job.status === "QUEUED") {
    statusPill.className = "status-pill queued";
    statusPill.textContent = "QUEUED";
  } else {
    statusPill.className = `status-pill ${statusClass}`;
    statusPill.textContent = job.status;
  }
  titleRow.appendChild(statusPill);
  body.appendChild(titleRow);

  if (job.status === "DONE") {
    const size = document.createElement("div");
    size.className = "job-size";
    const pct = job.source_size && job.final_bytes ? Math.round((1 - job.final_bytes / job.source_size) * 100) : null;
    size.innerHTML = `${job.source_size_label} <span class="arrow">→</span> ${job.final_size_label}`
      + (pct !== null ? ` <span class="saved">(${pct >= 0 ? "-" : "+"}${Math.abs(pct)}%)</span>` : "");
    body.appendChild(size);
  } else if (job.status === "ERROR") {
    const stats = document.createElement("div");
    stats.className = "job-stats";
    stats.style.color = "var(--error-text)";
    stats.textContent = job.error_message || "Encoding failed.";
    body.appendChild(stats);
  } else if (job.status === "CANCELLED") {
    const size = document.createElement("div");
    size.className = "job-size";
    size.textContent = job.source_size_label;
    body.appendChild(size);
  } else {
    const size = document.createElement("div");
    size.className = "job-size";
    const estClass = job.estimate_kind === "live" ? "est" : `est ${job.estimate_kind}`;
    const estLabel = !job.estimated_size_label ? "-"
      : job.estimate_kind === "target" ? job.estimated_size_label
      : `~${job.estimated_size_label}`;
    size.innerHTML = `${job.source_size_label} <span class="arrow">→ est.</span> <span class="${estClass}">${estLabel}</span>`;
    body.appendChild(size);
  }

  if (job.source_width && job.source_height) {
    const resLine = document.createElement("div");
    resLine.className = "job-res-line";
    const sourceRes = `${job.source_width}×${job.source_height}`;
    const targetLabel = (job.status === "CANCELLED" || job.status === "ERROR") ? "" : resolutionTargetLabel(job);
    if (targetLabel && targetLabel !== sourceRes) {
      resLine.innerHTML = `<span class="res-icon">▦</span> ${sourceRes} <span class="res-arrow">→</span> <span class="res-target">${targetLabel}</span>`
        + (job.force_ar ? ` <span class="res-tag">AR fix</span>` : "");
    } else {
      resLine.innerHTML = `<span class="res-icon">▦</span> ${sourceRes}`;
    }
    body.appendChild(resLine);
  }

  if (job.status === "ENCODING") {
    const track = document.createElement("div");
    track.className = "job-progress-track";
    const fill = document.createElement("div");
    fill.className = "job-progress-fill";
    fill.style.width = `${job.pct || 0}%`;
    track.appendChild(fill);
    body.appendChild(track);

    const stats = document.createElement("div");
    stats.className = "job-stats";
    const parts = [];
    if (job.speed) parts.push(`${job.speed} speed`);
    if (job.eta_seconds != null) parts.push(`ETA ${formatEta(job.eta_seconds)}`);
    stats.textContent = parts.join(" · ") || "Starting...";
    body.appendChild(stats);
  }

  const settingsLine = document.createElement("div");
  settingsLine.className = "job-settings-line";
  settingsLine.textContent = buildEncodeSettingsSummary(job);
  body.appendChild(settingsLine);

  card.appendChild(body);

  const actions = document.createElement("div");
  actions.className = "job-card-actions";
  if (job.status === "QUEUED") {
    const upBtn = document.createElement("button");
    upBtn.className = "job-mini-btn";
    upBtn.title = "Move up";
    upBtn.textContent = "↑";
    upBtn.addEventListener("click", (e) => { e.stopPropagation(); moveUpEncodeJob(job.id); });
    actions.appendChild(upBtn);

    const cancelBtn = document.createElement("button");
    cancelBtn.className = "job-mini-btn";
    cancelBtn.title = "Cancel";
    cancelBtn.textContent = "✕";
    cancelBtn.addEventListener("click", (e) => { e.stopPropagation(); cancelEncodeJob(job.id); });
    actions.appendChild(cancelBtn);
  } else if (job.status === "ENCODING") {
    const cancelBtn = document.createElement("button");
    cancelBtn.className = "job-mini-btn";
    cancelBtn.title = "Cancel";
    cancelBtn.textContent = "✕";
    cancelBtn.addEventListener("click", (e) => { e.stopPropagation(); cancelEncodeJob(job.id); });
    actions.appendChild(cancelBtn);
  } else if (job.status === "ERROR" || job.status === "CANCELLED") {
    const retryBtn = document.createElement("button");
    retryBtn.className = "job-mini-btn";
    retryBtn.title = "Retry";
    retryBtn.textContent = "↻";
    retryBtn.addEventListener("click", (e) => { e.stopPropagation(); retryEncodeJob(job.id); });
    actions.appendChild(retryBtn);

    const deleteBtn = document.createElement("button");
    deleteBtn.className = "job-mini-btn";
    deleteBtn.title = "Remove from list";
    deleteBtn.textContent = "🗑";
    deleteBtn.addEventListener("click", (e) => { e.stopPropagation(); deleteEncodeJob(job.id); });
    actions.appendChild(deleteBtn);
  } else if (job.status === "DONE") {
    const deleteBtn = document.createElement("button");
    deleteBtn.className = "job-mini-btn";
    deleteBtn.title = "Remove from list (keeps the encoded file)";
    deleteBtn.textContent = "🗑";
    deleteBtn.addEventListener("click", (e) => { e.stopPropagation(); deleteEncodeJob(job.id); });
    actions.appendChild(deleteBtn);
  }
  card.appendChild(actions);

  if (job.status === "DONE") {
    card.addEventListener("click", () => openEncodeJobFolder(job.id));
  }

  return card;
}

encodeFilterInput.addEventListener("input", () => {
  state.encodeFilterText = encodeFilterInput.value;
  renderEncodeLedger();
});
encodeSortSelect.addEventListener("change", () => {
  state.encodeSortField = encodeSortSelect.value;
  renderEncodeLedger();
});
encodeSortDirBtn.addEventListener("click", () => {
  state.encodeSortDir = state.encodeSortDir === "asc" ? "desc" : "asc";
  encodeSortDirBtn.textContent = state.encodeSortDir === "asc" ? "↑" : "↓";
  renderEncodeLedger();
});

async function cancelEncodeJob(id) {
  await fetch("/api/encode/jobs/cancel", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ job_id: id }),
  });
}

async function retryEncodeJob(id) {
  try {
    const res = await fetch("/api/encode/jobs/retry", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: id }),
    });
    const data = await res.json();
    if (res.ok) { state.encodeJobs.set(data.job.id, data.job); updateEncodeJobCard(data.job); }
    else flashStatus(data.error || "Couldn't retry that job.");
  } catch (e) {
    flashStatus("Couldn't reach the server.");
  }
}

async function deleteEncodeJob(id) {
  try {
    const res = await fetch("/api/encode/jobs/delete", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: id, delete_output: false }),
    });
    const data = await res.json();
    if (res.ok) { state.encodeJobs.delete(id); renderEncodeLedger(); }
    else flashStatus(data.error || "Couldn't remove that job.");
  } catch (e) {
    flashStatus("Couldn't reach the server.");
  }
}

async function moveUpEncodeJob(id) {
  try {
    const res = await fetch("/api/encode/jobs/move-up", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: id }),
    });
    // No websocket broadcast for a pure reorder (no job's own state
    // changed) - just re-sync the snapshot to reflect the new order.
    if (res.ok) await loadEncodeJobsSnapshot();
  } catch (e) {
    flashStatus("Couldn't reach the server.");
  }
}

async function openEncodeJobFolder(id) {
  if (!IS_LOCAL) { flashStatus(REMOTE_ONLY_TOOLTIP); return; }
  try {
    const res = await fetch("/api/encode/jobs/open-folder", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: id }),
    });
    const data = await res.json();
    if (!res.ok) flashStatus(data.error || "Couldn't open that file's folder.");
  } catch (e) {
    flashStatus("Couldn't reach the server.");
  }
}

newEncodeJobBtn.addEventListener("click", () => openNewEncodeJobModal());
openConvertedBtn.addEventListener("click", async () => {
  try {
    const res = await fetch("/api/encode/open-converted-folder", { method: "POST" });
    const data = await res.json();
    if (!res.ok) flashStatus(data.error || "Couldn't open the folder.");
  } catch (e) {
    flashStatus("Couldn't reach the server.");
  }
});

// ── New Encode Job modal ────────────────────────────────────────
function presetOptionsFor(kind) {
  if (kind === "x26x") {
    return ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"]
      .map((p) => [p, p]);
  }
  if (kind === "svt") {
    // libsvtav1: 0 (best/slowest) .. 13 (fastest)
    return [["2", "2 (slow, best)"], ["4", "4"], ["6", "6 (balanced)"], ["8", "8"], ["10", "10"], ["12", "12 (fast)"]];
  }
  if (kind === "vpx") {
    // libvpx-vp9 -cpu-used: 0 (best/slowest) .. 5 (fastest)
    return [["0", "0 (slow, best)"], ["1", "1"], ["2", "2 (balanced)"], ["3", "3"], ["4", "4"], ["5", "5 (fast)"]];
  }
  return [];
}

function populateEncodeSourceSelect() {
  encodeSourceSelect.innerHTML = "";
  for (const src of state.encodeSources) {
    const opt = document.createElement("option");
    opt.value = src.filename;
    opt.textContent = `${src.filename} — ${src.file_size}`;
    encodeSourceSelect.appendChild(opt);
  }
  const browseOpt = document.createElement("option");
  browseOpt.value = "__browse__";
  browseOpt.textContent = "Browse for a file not in the ledger...";
  encodeSourceSelect.appendChild(browseOpt);
}

function populateEncodeCodecSelect() {
  encodeCodecSelect.innerHTML = "";
  if (!state.encodeCapabilities) return;
  for (const [key, def] of Object.entries(state.encodeCapabilities.codecs)) {
    const opt = document.createElement("option");
    opt.value = key;
    opt.textContent = def.label;
    encodeCodecSelect.appendChild(opt);
  }
  encodeCodecSelect.value = "h265";
}

function populateEncodeResolutionSelect() {
  encodeResolutionSelect.innerHTML = "";
  const caps = (state.encodeCapabilities && state.encodeCapabilities.resolution_caps)
    || ["source", "2160p", "1440p", "1080p", "720p", "480p"];
  for (const cap of caps) {
    const opt = document.createElement("option");
    opt.value = cap;
    opt.textContent = cap === "source" ? "Source (no downscale)" : cap;
    encodeResolutionSelect.appendChild(opt);
  }
}

function applyActiveAspectRatio() {
  const activeBtn = encodeAspectQuickRow.querySelector(".aspect-quick-btn.active");
  if (!activeBtn || activeBtn.dataset.ratio === "Custom" || !state.encodeSourceInfo) return;
  const [rw, rh] = activeBtn.dataset.ratio.split(":").map(Number);
  const h = state.encodeSourceInfo.height;
  encodeArHeightInput.value = h;
  encodeArWidthInput.value = Math.round((h * (rw / rh)) / 2) * 2; // keep it even
}

function populateAspectQuickRow() {
  encodeAspectQuickRow.innerHTML = "";
  const ratios = (state.encodeCapabilities && state.encodeCapabilities.aspect_ratios) || ["16:9", "4:3", "21:9"];
  for (const ratio of [...ratios, "Custom"]) {
    const btn = document.createElement("div");
    btn.className = "aspect-quick-btn";
    btn.textContent = ratio;
    btn.dataset.ratio = ratio;
    btn.addEventListener("click", () => {
      encodeAspectQuickRow.querySelectorAll(".aspect-quick-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      applyActiveAspectRatio();
      // The preset row is always visible now (not gated behind the
      // checkbox), so picking a ratio here is itself the signal that
      // the user wants it forced - flip the checkbox on to match.
      if (!encodeForceArCheck.checked) {
        encodeForceArCheck.checked = true;
        encodeForceArFields.classList.remove("disabled-field");
      }
      requestEncodeEstimate();
    });
    encodeAspectQuickRow.appendChild(btn);
  }
  encodeAspectQuickRow.firstChild?.classList.add("active");
}

function onEncodeCodecChange() {
  const codec = encodeCodecSelect.value;
  const def = state.encodeCapabilities?.codecs?.[codec];
  if (!def) return;

  encodeBackendSelect.innerHTML = "";
  for (const backend of def.available_backends) {
    const opt = document.createElement("option");
    opt.value = backend;
    opt.textContent = backend === "software" ? "Software (recommended)" : `${backend.toUpperCase()} (hardware, faster, larger files)`;
    encodeBackendSelect.appendChild(opt);
  }
  encodeBackendSelect.value = "software";

  const [crfMin, crfMax] = def.crf_range;
  encodeCrfSlider.min = crfMin;
  encodeCrfSlider.max = crfMax;
  encodeCrfSlider.value = def.default_crf;
  encodeCrfValue.textContent = def.default_crf;
  encodeCrfHint.textContent = `Lower = higher quality, bigger file. This codec's typical default is ${def.default_crf}.`;

  encodePresetSelect.innerHTML = "";
  for (const [value, label] of presetOptionsFor(def.preset_kind)) {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = label;
    encodePresetSelect.appendChild(opt);
  }
  encodePresetSelect.value = def.default_preset;

  encodeContainerSelect.value = def.container_default;

  requestEncodeEstimate();
}
encodeCodecSelect.addEventListener("change", onEncodeCodecChange);
encodeBackendSelect.addEventListener("change", requestEncodeEstimate);
encodePresetSelect.addEventListener("change", requestEncodeEstimate);
encodeResolutionSelect.addEventListener("change", requestEncodeEstimate);
encodeAudioSelect.addEventListener("change", requestEncodeEstimate);
encodeDenoiseCheck.addEventListener("change", requestEncodeEstimate);
encodeCrfSlider.addEventListener("input", () => {
  encodeCrfValue.textContent = encodeCrfSlider.value;
  requestEncodeEstimate();
});
encodeTargetSizeInput.addEventListener("input", requestEncodeEstimate);

encodeForceArCheck.addEventListener("change", () => {
  encodeForceArFields.classList.toggle("disabled-field", !encodeForceArCheck.checked);
  requestEncodeEstimate();
});

encodeModeCrfBtn.addEventListener("click", () => setEncodeMode("crf"));
encodeModeSizeBtn.addEventListener("click", () => setEncodeMode("size"));
function setEncodeMode(mode) {
  encodeModeCrfBtn.classList.toggle("active", mode === "crf");
  encodeModeSizeBtn.classList.toggle("active", mode === "size");
  encodeCrfFields.style.display = mode === "crf" ? "block" : "none";
  encodeSizeFields.style.display = mode === "size" ? "block" : "none";
  requestEncodeEstimate();
}

encodeAdvancedToggle.addEventListener("click", () => {
  const isOpen = encodeAdvancedBody.classList.toggle("open");
  encodeAdvancedCaret.textContent = isOpen ? "▴" : "▾";
});

function currentSourceRequestBody() {
  const val = encodeSourceSelect.value;
  if (val === "__browse__") {
    const path = encodeSourceBrowsePath.value.trim();
    return path ? { path } : null;
  }
  return val ? { filename: val } : null;
}

async function onEncodeSourceChange() {
  const val = encodeSourceSelect.value;
  encodeSourceBrowseRow.classList.toggle("hidden", val !== "__browse__");
  if (val === "__browse__" && !encodeSourceBrowsePath.value.trim()) {
    encodeSourceInfo.textContent = "";
    state.encodeSourceInfo = null;
    return;
  }
  await probeSelectedSource();
}
encodeSourceSelect.addEventListener("change", onEncodeSourceChange);

encodeSourceBrowseBtn.addEventListener("click", async () => {
  try {
    const res = await fetch("/api/encode/browse-source", { method: "POST" });
    const data = await res.json();
    if (data.path) {
      encodeSourceBrowsePath.value = data.path;
      await probeSelectedSource();
    }
  } catch (e) {
    flashStatus("Couldn't reach the server.");
  }
});
encodeSourceBrowsePath.addEventListener("change", probeSelectedSource);

async function probeSelectedSource() {
  const body = currentSourceRequestBody();
  if (!body) return;
  // Guard against out-of-order responses: if the user switches from a
  // slow-to-probe file (e.g. a large newly-pasted one) to another file
  // before the first probe returns, an older response arriving after a
  // newer one must not overwrite the display with the wrong file's info.
  const mySeq = ++state.encodeProbeSeq;
  encodeSourceInfo.textContent = "Probing source file...";
  try {
    const res = await fetch("/api/encode/probe", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (mySeq !== state.encodeProbeSeq) return; // superseded by a newer selection
    if (!res.ok) {
      state.encodeSourceInfo = null;
      encodeSourceInfo.textContent = data.error || "Couldn't read that file.";
      encodeEstimateOriginal.textContent = "-";
      encodeEstimateValue.textContent = "-";
      encodeEstimateSavings.textContent = "-";
      return;
    }
    state.encodeSourceInfo = data;
    encodeSourceInfo.textContent = `${data.width}×${data.height} · ${formatDuration(data.duration)} · ${data.size_label}`;
    encodeResolutionLabel.textContent = `Resolution cap (downscale only) — source is ${data.width}×${data.height}`;
    encodeEstimateOriginal.textContent = data.size_label || "-";
    encodeEstimateSavings.textContent = "-";
    encodeArWidthInput.value = data.width;
    encodeArHeightInput.value = data.height;
    applyActiveAspectRatio();
    requestEncodeEstimate();
  } catch (e) {
    if (mySeq !== state.encodeProbeSeq) return; // superseded by a newer selection
    state.encodeSourceInfo = null;
    encodeSourceInfo.textContent = "Couldn't reach the server to probe that file.";
  }
}

function collectEncodeOptions() {
  const mode = encodeModeCrfBtn.classList.contains("active") ? "crf" : "size";
  const activeRatioBtn = encodeAspectQuickRow.querySelector(".aspect-quick-btn.active");
  return {
    mode,
    codec: encodeCodecSelect.value,
    encoder_backend: encodeBackendSelect.value,
    crf: parseInt(encodeCrfSlider.value, 10),
    preset: encodePresetSelect.value,
    target_size_mb: encodeTargetSizeInput.value ? parseFloat(encodeTargetSizeInput.value) : null,
    resolution_cap: encodeResolutionSelect.value,
    force_ar: encodeForceArCheck.checked,
    force_ar_label: activeRatioBtn ? activeRatioBtn.dataset.ratio : "",
    force_ar_width: encodeForceArCheck.checked ? (parseInt(encodeArWidthInput.value, 10) || null) : null,
    force_ar_height: encodeForceArCheck.checked ? (parseInt(encodeArHeightInput.value, 10) || null) : null,
    deinterlace: encodeDeinterlaceCheck.checked,
    auto_crop: encodeAutocropCheck.checked,
    denoise: encodeDenoiseCheck.checked,
    audio_mode: encodeAudioSelect.value,
    subtitles_mode: encodeSubtitlesSelect.value,
    container: encodeContainerSelect.value,
    oversized_behavior: encodeOversizedSelect.value,
  };
}

let encodeEstimateTimer = null;
function requestEncodeEstimate() {
  clearTimeout(encodeEstimateTimer);
  encodeEstimateTimer = setTimeout(doRequestEncodeEstimate, 250);
}

encodeEstimateRefreshBtn.addEventListener("click", async () => {
  clearTimeout(encodeEstimateTimer);
  encodeEstimateRefreshBtn.classList.remove("spinning");
  void encodeEstimateRefreshBtn.offsetWidth; // restart animation if clicked repeatedly
  encodeEstimateRefreshBtn.classList.add("spinning");
  // If a previous probe failed or never landed (e.g. a manually-pasted
  // file that was still being copied when it was first selected),
  // state.encodeSourceInfo is null and doRequestEncodeEstimate() would
  // silently no-op below - leaving the estimate stuck with no visible
  // feedback even though the user just pressed Refresh. Re-probe the
  // source first so a since-completed copy (or any other transient
  // failure) gets picked up instead of refusing to update forever.
  if (!state.encodeSourceInfo) await probeSelectedSource();
  doRequestEncodeEstimate();
});

async function doRequestEncodeEstimate() {
  const sourceBody = currentSourceRequestBody();
  if (!sourceBody || !state.encodeSourceInfo) return;
  const mySeq = ++state.encodeEstimateSeq;
  const options = collectEncodeOptions();
  encodeEstimateLabel.textContent = options.mode === "size" ? "Target" : "Estimated";
  try {
    const res = await fetch("/api/encode/estimate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...sourceBody, options }),
    });
    const data = await res.json();
    if (mySeq !== state.encodeEstimateSeq) return; // superseded by a newer request
    if (!res.ok) {
      encodeEstimateValue.textContent = "-";
      encodeEstimateSavings.textContent = "-";
      return;
    }
    const prefix = options.mode === "size" ? "" : "~";
    encodeEstimateValue.textContent = data.estimated_size_label ? `${prefix}${data.estimated_size_label}` : "-";

    const sourceBytes = state.encodeSourceInfo?.size_bytes;
    encodeEstimateSavings.classList.remove("good", "bad");
    if (sourceBytes && data.estimated_bytes) {
      const pct = Math.round((1 - data.estimated_bytes / sourceBytes) * 100);
      encodeEstimateSavings.textContent = pct >= 0 ? `-${pct}%` : `+${Math.abs(pct)}%`;
      encodeEstimateSavings.classList.add(pct >= 0 ? "good" : "bad");
    } else {
      encodeEstimateSavings.textContent = "-";
    }
  } catch (e) {
    if (mySeq === state.encodeEstimateSeq) {
      encodeEstimateValue.textContent = "-";
      encodeEstimateSavings.textContent = "-";
    }
  }
}

async function openNewEncodeJobModal(preselectFilename) {
  encodeJobError.classList.add("hidden");
  if (!state.encodeCapabilities) await loadEncodeCapabilities();
  // Check the download folder for anything new (manually dropped files,
  // etc.) before listing candidate sources, same as pressing Refresh.
  await refreshDownloadLedger();
  await refreshEncodeSources();

  populateEncodeSourceSelect();
  // Re-encode... from a ledger card's menu arrives here with that
  // file's name - select it if it's actually a valid candidate (it
  // will be, since the gating that shows that menu item matches
  // /api/encode/sources' own filter).
  if (preselectFilename && Array.from(encodeSourceSelect.options).some((o) => o.value === preselectFilename)) {
    encodeSourceSelect.value = preselectFilename;
  }
  populateEncodeCodecSelect();
  populateEncodeResolutionSelect();
  populateAspectQuickRow();

  encodeSourceBrowseRow.classList.add("hidden");
  encodeSourceBrowsePath.value = "";
  encodeEstimateOriginal.textContent = "-";
  encodeEstimateValue.textContent = "-";
  encodeEstimateSavings.textContent = "-";
  encodeEstimateSavings.classList.remove("good", "bad");
  encodeForceArCheck.checked = false;
  encodeForceArFields.classList.add("disabled-field");
  encodeArWidthInput.value = "";
  encodeArHeightInput.value = "";
  encodeDeinterlaceCheck.checked = false;
  encodeAutocropCheck.checked = false;
  encodeDenoiseCheck.checked = false;
  encodeAudioSelect.value = "copy";
  encodeSubtitlesSelect.value = "copy";
  encodeOversizedSelect.value = "flag";
  encodeTargetSizeInput.value = "";
  encodeAdvancedBody.classList.remove("open");
  encodeAdvancedCaret.textContent = "▾";
  setEncodeMode("crf");

  onEncodeCodecChange();

  if (encodeSourceSelect.options.length > 0) await onEncodeSourceChange();

  newEncodeJobModal.classList.remove("hidden");
  const modalBox = newEncodeJobModal.querySelector(".modal-box");
  if (modalBox) modalBox.scrollTop = 0;
}

function closeNewEncodeJobModal() {
  newEncodeJobModal.classList.add("hidden");
}

closeEncodeJobModalBtn.addEventListener("click", closeNewEncodeJobModal);
cancelEncodeJobModalBtn.addEventListener("click", closeNewEncodeJobModal);
newEncodeJobModal.addEventListener("click", (e) => { if (e.target === newEncodeJobModal) closeNewEncodeJobModal(); });

addEncodeJobBtn.addEventListener("click", async () => {
  encodeJobError.classList.add("hidden");
  const sourceBody = currentSourceRequestBody();
  if (!sourceBody) {
    encodeJobError.textContent = "Choose a source file first.";
    encodeJobError.classList.remove("hidden");
    return;
  }
  const options = collectEncodeOptions();
  if (options.mode === "size" && !options.target_size_mb) {
    encodeJobError.textContent = "Enter a target size in MB.";
    encodeJobError.classList.remove("hidden");
    return;
  }

  addEncodeJobBtn.disabled = true;
  try {
    const res = await fetch("/api/encode/jobs", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...sourceBody, options }),
    });
    const data = await res.json();
    if (!res.ok) {
      encodeJobError.textContent = data.error || "Couldn't queue that job.";
      encodeJobError.classList.remove("hidden");
      return;
    }
    state.encodeJobs.set(data.job.id, data.job);
    renderEncodeLedger();
    closeNewEncodeJobModal();
  } catch (e) {
    encodeJobError.textContent = "Couldn't reach the server.";
    encodeJobError.classList.remove("hidden");
  } finally {
    addEncodeJobBtn.disabled = false;
  }
});

boot();
