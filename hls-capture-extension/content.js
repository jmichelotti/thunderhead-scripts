// HLS Capture - In-page confirmation dialog
// Injected into every page; listens for messages from background.js

const DIALOG_ID = "hls-capture-dialog-container";

function getOrCreateContainer() {
  let container = document.getElementById(DIALOG_ID);
  if (!container) {
    container = document.createElement("div");
    container.id = DIALOG_ID;
    Object.assign(container.style, {
      position: "fixed",
      top: "16px",
      right: "16px",
      zIndex: "2147483647",
      display: "flex",
      flexDirection: "column",
      gap: "10px",
      fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
      fontSize: "13px",
    });
    document.body.appendChild(container);
  }
  return container;
}

function showDialog(data) {
  const { index, preview, previewStatus, previewError } = data;
  const container = getOrCreateContainer();

  const card = document.createElement("div");
  card.dataset.hlsIndex = index;
  Object.assign(card.style, {
    background: "#1a1a2e",
    color: "#e0e0e0",
    borderRadius: "10px",
    padding: "14px 16px",
    minWidth: "320px",
    maxWidth: "400px",
    boxShadow: "0 8px 32px rgba(0,0,0,0.6), 0 0 0 1px rgba(124,58,237,0.3)",
    borderLeft: "3px solid #f59e0b",
    animation: "hls-slide-in 0.3s ease-out",
  });

  if (previewStatus === "loading") {
    card.innerHTML = `
      <div style="color:#9ca3af; font-style:italic;">Loading preview...</div>
    `;
    container.appendChild(card);
    return;
  }

  if (previewStatus === "error") {
    card.innerHTML = `
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
        <span style="font-weight:700; font-size:12px; color:#7c3aed; text-transform:uppercase; letter-spacing:0.5px;">HLS Capture</span>
      </div>
      <div style="color:#fca5a5; margin-bottom:8px;">${previewError || "Preview failed"}</div>
      <div style="display:flex; gap:8px;">
        <button class="hls-dismiss" style="
          background:#374151; color:#9ca3af; border:none; padding:6px 16px;
          border-radius:5px; cursor:pointer; font-size:12px;
        ">Dismiss</button>
      </div>
    `;
  } else {
    const p = preview;
    card.innerHTML = `
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
        <span style="font-weight:700; font-size:12px; color:#7c3aed; text-transform:uppercase; letter-spacing:0.5px;">HLS Capture</span>
        <span style="font-size:11px; padding:2px 8px; border-radius:10px; background:#78350f; color:#fbbf24; font-weight:600;">awaiting</span>
      </div>
      <div style="font-weight:600; font-size:15px; color:#f3f4f6; margin-bottom:6px;">
        ${p.show_title}
      </div>
      <div style="display:flex; gap:14px; font-size:12px; color:#9ca3af; margin-bottom:10px;">
        <span style="color:#a78bfa; font-weight:600;">${p.ep_tag}</span>
        <span style="color:#818cf8; font-weight:600;">${p.quality}</span>
      </div>
      <div style="font-size:11px; color:#6b7280; margin-bottom:10px; word-break:break-all;">
        ${p.filename}
      </div>
      <div style="display:flex; gap:8px;">
        <button class="hls-confirm" style="
          background:#065f46; color:#6ee7b7; font-weight:600; border:none;
          padding:7px 20px; border-radius:5px; cursor:pointer; font-size:13px;
        ">Download</button>
        <button class="hls-dismiss" style="
          background:#374151; color:#9ca3af; border:none; padding:7px 16px;
          border-radius:5px; cursor:pointer; font-size:12px;
        ">Dismiss</button>
      </div>
    `;
  }

  // Attach button handlers
  const confirmBtn = card.querySelector(".hls-confirm");
  if (confirmBtn) {
    confirmBtn.addEventListener("click", () => {
      confirmBtn.disabled = true;
      confirmBtn.textContent = "Starting...";
      confirmBtn.style.background = "#374151";
      confirmBtn.style.color = "#6b7280";
      chrome.runtime.sendMessage({ type: "confirmDownload", index }, () => {
        fadeOutCard(card);
      });
    });
    confirmBtn.addEventListener("mouseenter", () => {
      confirmBtn.style.background = "#047857";
    });
    confirmBtn.addEventListener("mouseleave", () => {
      confirmBtn.style.background = "#065f46";
    });
  }

  const dismissBtn = card.querySelector(".hls-dismiss");
  if (dismissBtn) {
    dismissBtn.addEventListener("click", () => {
      chrome.runtime.sendMessage({ type: "dismissCapture", index }, () => {
        fadeOutCard(card);
      });
    });
    dismissBtn.addEventListener("mouseenter", () => {
      dismissBtn.style.background = "#4b5563";
    });
    dismissBtn.addEventListener("mouseleave", () => {
      dismissBtn.style.background = "#374151";
    });
  }

  container.appendChild(card);
}

function fadeOutCard(card) {
  card.style.transition = "opacity 0.3s ease, transform 0.3s ease";
  card.style.opacity = "0";
  card.style.transform = "translateX(60px)";
  setTimeout(() => card.remove(), 300);
}

function updateDialog(data) {
  const container = document.getElementById(DIALOG_ID);
  if (!container) return;

  // Find existing card for this index and replace it
  const existing = container.querySelector(`[data-hls-index="${data.index}"]`);
  if (existing) {
    existing.remove();
  }
  showDialog(data);
}

// Inject slide-in animation
const style = document.createElement("style");
style.textContent = `
  @keyframes hls-slide-in {
    from { opacity: 0; transform: translateX(60px); }
    to { opacity: 1; transform: translateX(0); }
  }
`;
document.head.appendChild(style);

// --- Auto-capture overlay & page-reload-based loop ---

const AC_OVERLAY_ID = "hls-auto-capture-overlay";
let autoCaptureAborted = false;
let autoCaptureResolveEpisode = null;

function showAutoCaptureOverlay(text) {
  let overlay = document.getElementById(AC_OVERLAY_ID);
  if (!overlay) {
    overlay = document.createElement("div");
    overlay.id = AC_OVERLAY_ID;
    Object.assign(overlay.style, {
      position: "fixed",
      bottom: "16px",
      left: "16px",
      zIndex: "2147483647",
      background: "#1a1a2e",
      color: "#c7d2fe",
      fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
      fontSize: "13px",
      fontWeight: "600",
      padding: "8px 14px",
      borderRadius: "8px",
      borderLeft: "3px solid #6366f1",
      boxShadow: "0 4px 16px rgba(0,0,0,0.5)",
    });
    document.body.appendChild(overlay);
  }
  overlay.textContent = text;
}

function removeAutoCaptureOverlay() {
  const overlay = document.getElementById(AC_OVERLAY_ID);
  if (overlay) overlay.remove();
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function waitForEpisodeDone(timeoutMs) {
  return new Promise((resolve) => {
    autoCaptureResolveEpisode = resolve;
    setTimeout(() => resolve(false), timeoutMs);
  });
}

// Called on every page load — if auto-capture is active for this tab, run the current episode step
function checkAutoCaptureOnLoad() {
  chrome.runtime.sendMessage({ type: "checkAutoCapture" }, (state) => {
    if (chrome.runtime.lastError || !state || !state.active) return;
    runAutoCaptureStep(state);
  });
}

async function runAutoCaptureStep(state) {
  const { season, currentEp, endEp, startEp } = state;
  autoCaptureAborted = false;

  showAutoCaptureOverlay(`Auto-capture: EP ${currentEp} of ${startEp}–${endEp}`);

  // Wait for page and player to fully initialize (subtitles load during this time)
  await sleep(4000);
  if (autoCaptureAborted) return;

  // Tell background we're clicking play for this episode
  chrome.runtime.sendMessage({
    type: "autoCaptureClickedPlay",
    season,
    episode: currentEp,
  });

  // Click play
  let playBtn = document.querySelector("#player button.player-btn");
  if (playBtn) playBtn.click();

  // Wait for m3u8 capture confirmation (max 15s)
  let captured = await waitForEpisodeDone(15000);
  autoCaptureResolveEpisode = null;

  if (autoCaptureAborted) return;

  // Retry once if not captured
  if (!captured) {
    showAutoCaptureOverlay(`Auto-capture: EP ${currentEp} of ${startEp}–${endEp} (retrying...)`);
    playBtn = document.querySelector("#player button.player-btn");
    if (playBtn) playBtn.click();

    captured = await waitForEpisodeDone(15000);
    autoCaptureResolveEpisode = null;

    if (!captured && !autoCaptureAborted) {
      showAutoCaptureOverlay(`Auto-capture: EP ${currentEp} of ${startEp}–${endEp} (skipped)`);
      await sleep(1000);
    }
  }

  if (autoCaptureAborted) return;

  // Ask background to advance to next episode
  chrome.runtime.sendMessage({ type: "autoCaptureAdvance" }, (resp) => {
    if (chrome.runtime.lastError || !resp) return;

    if (resp.hasNext) {
      // Navigate to next episode with full page reload so subtitles load fresh
      location.hash = `#ep=${resp.season},${resp.nextEp}`;
      location.reload();
    } else {
      showAutoCaptureOverlay("Auto-capture complete!");
      setTimeout(removeAutoCaptureOverlay, 3000);
    }
  });
}

// On content script load, check if auto-capture is in progress for this tab
checkAutoCaptureOnLoad();

// Listen for messages from background.js
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "showCaptureDialog") {
    showDialog(msg);
    sendResponse({ ok: true });
  } else if (msg.type === "updateCaptureDialog") {
    updateDialog(msg);
    sendResponse({ ok: true });
  } else if (msg.type === "beginAutoCapture") {
    // First episode: set hash and reload to get a fresh page with subtitles
    location.hash = `#ep=${msg.season},${msg.startEp}`;
    location.reload();
    sendResponse({ ok: true });
  } else if (msg.type === "stopAutoCapture") {
    autoCaptureAborted = true;
    if (autoCaptureResolveEpisode) {
      autoCaptureResolveEpisode(false);
      autoCaptureResolveEpisode = null;
    }
    removeAutoCaptureOverlay();
    sendResponse({ ok: true });
  } else if (msg.type === "autoCaptureEpisodeDone") {
    if (autoCaptureResolveEpisode) {
      autoCaptureResolveEpisode(true);
      autoCaptureResolveEpisode = null;
    }
    sendResponse({ ok: true });
  }
  return true;
});
