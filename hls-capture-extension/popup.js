const SERVER_BASE = "http://localhost:9876";

async function checkServer() {
  const el = document.getElementById("serverStatus");
  try {
    const resp = await fetch(`${SERVER_BASE}/status`, { signal: AbortSignal.timeout(2000) });
    if (resp.ok) {
      const data = await resp.json();
      const mode = data.dry_run ? "dry run" : "live";
      el.textContent = `server up (${mode})`;
      el.className = "server-status server-up";
    } else {
      throw new Error();
    }
  } catch {
    el.textContent = "server down";
    el.className = "server-status server-down";
  }
}

function renderPending(pendingCaptures) {
  const container = document.getElementById("pending");

  if (!pendingCaptures || pendingCaptures.length === 0) {
    container.innerHTML = "";
    return;
  }

  container.innerHTML =
    '<div class="section-label">Awaiting Confirmation</div>' +
    pendingCaptures.map((p, idx) => {
      if (p.previewStatus === "loading") {
        return `
          <div class="pending-card loading">
            <div class="pending-loading">Loading preview...</div>
          </div>
        `;
      }

      if (p.previewStatus === "error") {
        return `
          <div class="pending-card error">
            <div class="pending-error">${p.previewError || "Preview failed"}</div>
            <div class="pending-actions">
              <button class="btn-dismiss" data-dismiss="${idx}">Dismiss</button>
            </div>
          </div>
        `;
      }

      const preview = p.preview;
      return `
        <div class="pending-card">
          <div class="pending-title">${preview.filename}</div>
          <div class="pending-meta">
            <span class="episode">${preview.show_title}</span>
            <span class="episode">${preview.ep_tag}</span>
            <span class="quality">${preview.quality}</span>
          </div>
          <div class="pending-actions">
            <button class="btn-download" data-confirm="${idx}">Download</button>
            <button class="btn-dismiss" data-dismiss="${idx}">Dismiss</button>
          </div>
        </div>
      `;
    }).join("");

  // Attach event listeners
  container.querySelectorAll("[data-confirm]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const idx = parseInt(btn.dataset.confirm);
      btn.disabled = true;
      btn.textContent = "Starting...";
      chrome.runtime.sendMessage({ type: "confirmDownload", index: idx }, () => {
        refresh();
      });
    });
  });

  container.querySelectorAll("[data-dismiss]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const idx = parseInt(btn.dataset.dismiss);
      chrome.runtime.sendMessage({ type: "dismissCapture", index: idx }, () => {
        refresh();
      });
    });
  });
}

function renderDownloads(downloads) {
  const container = document.getElementById("downloads");

  if (!downloads || downloads.length === 0) {
    container.innerHTML = '<div class="empty">No downloads yet. Browse to a video page and press play.</div>';
    return;
  }

  // Show most recent first
  const sorted = [...downloads].reverse();
  container.innerHTML =
    '<div class="section-label">Downloads</div>' +
    sorted.map((dl) => {
      const pct = dl.percent ? dl.percent.toFixed(1) : "0.0";
      const isDone = dl.status === "done";
      const isActive = dl.status === "downloading";

      // Meta info line
      const metaParts = [];
      if (dl.quality) metaParts.push(`<span class="quality">${dl.quality} kbps</span>`);
      if (dl.size) metaParts.push(`<span>~${dl.size}</span>`);
      if (dl.speed && isActive) metaParts.push(`<span>${dl.speed}</span>`);
      if (dl.eta && isActive) metaParts.push(`<span>ETA ${dl.eta}</span>`);
      if (dl.frag && dl.total_frags) metaParts.push(`<span>frag ${dl.frag}/${dl.total_frags}</span>`);

      const statusLabel = {
        queued: "queued",
        downloading: "downloading",
        moving: "moving file",
        done: "complete",
        error: "failed",
        dry_run: "dry run",
      }[dl.status] || dl.status;

      return `
        <div class="dl-card ${dl.status}">
          <div class="dl-header">
            <span class="dl-filename">${dl.filename}</span>
            <span class="dl-status ${dl.status}">${statusLabel}</span>
          </div>
          ${metaParts.length ? `<div class="dl-meta">${metaParts.join("")}</div>` : ""}
          <div class="progress-bar-bg">
            <div class="progress-bar-fill ${isDone ? "done" : ""}" style="width: ${pct}%"></div>
          </div>
          <div class="progress-text">
            <span>${pct}%</span>
            <span>${dl.show || ""}</span>
          </div>
        </div>
      `;
    }).join("");
}

async function loadDownloads() {
  try {
    const resp = await fetch(`${SERVER_BASE}/downloads`, { signal: AbortSignal.timeout(2000) });
    if (resp.ok) {
      const data = await resp.json();
      renderDownloads(data.downloads);
    }
  } catch {
    // Server down, keep current display
  }
}

function loadPending() {
  chrome.runtime.sendMessage({ type: "getCaptures" }, (response) => {
    if (response) {
      renderPending(response.pendingCaptures);
    }
  });
}

function refresh() {
  checkServer();
  loadPending();
  loadDownloads();
}

document.getElementById("clearBtn").addEventListener("click", () => {
  chrome.runtime.sendMessage({ type: "clearCaptures" }, () => {
    refresh();
  });
});

// --- Auto-capture controls ---

const acSection = document.getElementById("autoCapture");
const acSeason = document.getElementById("acSeason");
const acFromEp = document.getElementById("acFromEp");
const acToEp = document.getElementById("acToEp");
const acStartBtn = document.getElementById("acStartBtn");
const acStopBtn = document.getElementById("acStopBtn");
const acIdle = document.getElementById("acIdle");
const acRunning = document.getElementById("acRunning");
const acIdleStatus = document.getElementById("acIdleStatus");
const acRunStatus = document.getElementById("acRunStatus");

let currentTabSeason = null;

// Parse season from the active tab's URL hash (#ep=season,episode)
function parseSeasonFromUrl(url) {
  if (!url) return null;
  const match = url.match(/#ep=(\d+),(\d+)/);
  return match ? parseInt(match[1], 10) : null;
}

function updateAutoCaptureUI() {
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    const tab = tabs[0];
    if (!tab) return;

    const season = parseSeasonFromUrl(tab.url);
    const serverUp = document.getElementById("serverStatus").classList.contains("server-up");

    if (season !== null && serverUp) {
      currentTabSeason = season;
      acSection.classList.remove("hidden");
      acSeason.textContent = `Season ${season}`;
    } else {
      acSection.classList.add("hidden");
      currentTabSeason = null;
    }
  });

  // Check auto-capture state from background
  chrome.runtime.sendMessage({ type: "getAutoCaptureState" }, (state) => {
    if (!state) return;

    if (state.active) {
      acSection.classList.remove("hidden");
      acSection.classList.add("active");
      acIdle.style.display = "none";
      acRunning.style.display = "";
      const current = state.currentEp || state.startEp;
      const total = state.endEp - state.startEp + 1;
      const done = current - state.startEp;
      acRunStatus.textContent = `Capturing EP ${current} of ${state.startEp}–${state.endEp}...`;
      if (state.doneCount !== undefined) {
        acRunStatus.textContent = `EP ${current} of ${state.startEp}–${state.endEp} (${state.doneCount} captured)`;
      }
    } else if (state.finished) {
      acSection.classList.add("active");
      acIdle.style.display = "";
      acRunning.style.display = "none";
      acIdleStatus.textContent = `Done (${state.doneCount}/${state.totalCount} captured)`;
      acIdleStatus.className = "ac-status done";
    } else {
      acSection.classList.remove("active");
      acIdle.style.display = "";
      acRunning.style.display = "none";
    }
  });
}

acStartBtn.addEventListener("click", () => {
  if (currentTabSeason === null) return;

  const startEp = parseInt(acFromEp.value, 10);
  const endEp = parseInt(acToEp.value, 10);
  if (!startEp || !endEp || startEp > endEp || startEp < 1) {
    acIdleStatus.textContent = "Invalid episode range";
    acIdleStatus.className = "ac-status error";
    return;
  }

  acStartBtn.disabled = true;
  acIdleStatus.textContent = "Starting...";

  chrome.runtime.sendMessage({
    type: "startAutoCapture",
    season: currentTabSeason,
    startEp,
    endEp,
  }, (resp) => {
    acStartBtn.disabled = false;
    if (resp && resp.ok) {
      acIdle.style.display = "none";
      acRunning.style.display = "";
      acRunStatus.textContent = `Capturing EP ${startEp} of ${startEp}–${endEp}...`;
    } else {
      acIdleStatus.textContent = resp?.error || "Failed to start";
      acIdleStatus.className = "ac-status error";
    }
  });
});

acStopBtn.addEventListener("click", () => {
  chrome.runtime.sendMessage({ type: "stopAutoCapture" }, () => {
    acIdle.style.display = "";
    acRunning.style.display = "none";
    acIdleStatus.textContent = "Stopped";
    acIdleStatus.className = "ac-status error";
    acSection.classList.remove("active");
  });
});

refresh();

// Refresh every 1 second while popup is open
setInterval(() => {
  refresh();
  updateAutoCaptureUI();
}, 1000);

// Initial auto-capture UI check
updateAutoCaptureUI();
