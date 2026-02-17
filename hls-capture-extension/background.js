const SERVER_URL = "http://localhost:9876/capture";
const SUBTITLE_URL = "http://localhost:9876/subtitle";

// Track captures: [{m3u8_url, page_url, timestamp, status}]
let captures = [];

// Track subtitle URLs per tab to avoid duplicates
const subtitlesSent = new Set();

// Listen for m3u8 and subtitle requests
chrome.webRequest.onBeforeRequest.addListener(
  (details) => {
    if (details.type === "main_frame") return;

    const url = details.url;
    const now = Date.now();

    // Check for subtitle files (.vtt, .srt)
    if (/\.(vtt|srt)(\?|$)/i.test(url)) {
      if (subtitlesSent.has(url)) return;
      subtitlesSent.add(url);

      chrome.tabs.get(details.tabId, (tab) => {
        if (chrome.runtime.lastError) return;
        sendSubtitle(url, tab?.url || "");
      });
      return;
    }

    // Check for m3u8
    if (!url.includes(".m3u8")) return;

    // Skip duplicate URLs captured in the last 5 seconds
    const isDupe = captures.some(
      (c) => c.m3u8_url === url && now - c.timestamp < 5000
    );
    if (isDupe) return;

    // Get the tab URL for context
    chrome.tabs.get(details.tabId, (tab) => {
      if (chrome.runtime.lastError) return;

      const capture = {
        m3u8_url: url,
        page_url: tab?.url || "",
        timestamp: now,
        status: "sending",
      };
      captures.push(capture);

      // Keep only last 50 captures
      if (captures.length > 50) {
        captures = captures.slice(-50);
      }

      updateBadge();
      sendToServer(capture);
    });
  },
  { urls: ["<all_urls>"] },
  []
);

async function sendToServer(capture) {
  try {
    const resp = await fetch(SERVER_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        m3u8_url: capture.m3u8_url,
        page_url: capture.page_url,
      }),
    });

    const data = await resp.json();
    capture.status = data.status || "sent";
    capture.message = data.message || "";
  } catch (err) {
    capture.status = "error";
    capture.message = "Server not running? " + err.message;
  }

  updateBadge();
}

async function sendSubtitle(subtitleUrl, pageUrl) {
  try {
    await fetch(SUBTITLE_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        subtitle_url: subtitleUrl,
        page_url: pageUrl,
      }),
    });
  } catch {
    // Server not running, ignore
  }
}

function updateBadge() {
  const pending = captures.filter(
    (c) => c.status === "sending" || c.status === "downloading"
  ).length;
  const text = pending > 0 ? String(pending) : String(captures.length || "");
  chrome.action.setBadgeText({ text: captures.length > 0 ? text : "" });
  chrome.action.setBadgeBackgroundColor({
    color: pending > 0 ? "#f59e0b" : "#22c55e",
  });
}

// Handle messages from popup
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "getCaptures") {
    sendResponse({ captures });
  } else if (msg.type === "clearCaptures") {
    captures = [];
    subtitlesSent.clear();
    updateBadge();
    sendResponse({ ok: true });
  }
  return true;
});
