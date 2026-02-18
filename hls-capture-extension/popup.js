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

refresh();

// Refresh every 1 second while popup is open
setInterval(refresh, 1000);
