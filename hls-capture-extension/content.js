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

// Listen for messages from background.js
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "showCaptureDialog") {
    showDialog(msg);
    sendResponse({ ok: true });
  } else if (msg.type === "updateCaptureDialog") {
    updateDialog(msg);
    sendResponse({ ok: true });
  }
  return true;
});
