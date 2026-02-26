const SERVER_URL = "http://localhost:9876/capture";
const PREVIEW_URL = "http://localhost:9876/preview";
const SUBTITLE_URL = "http://localhost:9876/subtitle";

// ========= SITE CONFIGS =========

const SITE_CONFIGS = {
  "1movies.bz": {
    navStrategy: "hash-reload",         // navigate via location.hash + reload
    makeEpisodeHash: (s, e) => `#ep=${s},${e}`,
    playButtonSelector: "#player button.player-btn",
    subtitleWaitMs: 4000,
  },
  "brocoflix.xyz": {
    navStrategy: "click-card",          // navigate by clicking .episode-card elements
    playButtonSelector: ".episode-play-button", // inside each episode card
    serverButtonSelector: ".server-button",     // Nth button = server N (1-indexed)
    subtitleWaitMs: 5000,
    showTitleSelector: "#details-container h1",
    episodeCardSelector: ".episode-card",
    seasonSelectSelector: "#season-select",
  },
};

function getSiteConfig(url) {
  if (!url) return null;
  try {
    const host = new URL(url).hostname;
    for (const [site, cfg] of Object.entries(SITE_CONFIGS)) {
      if (host.includes(site)) return { site, ...cfg };
    }
  } catch {}
  return null;
}

// ========= STATE =========

// Pending captures awaiting user confirmation: [{m3u8_url, page_url, tabId, timestamp, preview}]
let pendingCaptures = [];

// Confirmed/active captures: [{m3u8_url, page_url, tabId, timestamp, status, message}]
let captures = [];

// Track subtitle URLs per tab to avoid duplicates
const subtitlesSent = new Set();

// Track m3u8 URLs already pending or confirmed to avoid duplicates
const seenM3u8 = new Set();

// Per-tab episode context set by content script for DOM-based sites (e.g. brocoflix)
// Map<tabId, {show_name, season, episode}>
const episodeContextByTab = new Map();

// Auto-capture state
let autoCapture = {
  active: false,
  finished: false,
  tabId: null,
  season: null,
  startEp: null,
  endEp: null,
  currentEp: null,
  doneCount: 0,
  totalCount: 0,
  siteConfig: null,
  serverNum: 1,
  graceUntil: 0,  // timestamp: auto-confirm still fires during grace period after last ep
};

// ========= NETWORK INTERCEPTION =========

// Listen for m3u8 and subtitle requests
chrome.webRequest.onBeforeRequest.addListener(
  (details) => {
    if (details.type === "main_frame") return;

    const url = details.url;
    const tabId = details.tabId;

    // Check for subtitle files (.vtt, .srt)
    if (/\.(vtt|srt)(\?|$)/i.test(url)) {
      if (subtitlesSent.has(url)) return;
      subtitlesSent.add(url);

      chrome.tabs.get(tabId, (tab) => {
        if (chrome.runtime.lastError) return;
        sendSubtitle(url, tab?.url || "", tabId);
      });
      return;
    }

    // Check for m3u8
    if (!url.includes(".m3u8")) return;

    // Skip if already pending or confirmed
    if (seenM3u8.has(url)) return;
    seenM3u8.add(url);

    // Get the tab URL for context
    chrome.tabs.get(tabId, (tab) => {
      if (chrome.runtime.lastError) return;

      const pageUrl = tab?.url || "";

      // Auto-capture mode: skip pending queue, auto-confirm immediately.
      // Also fires during the grace period after the last episode, in case the
      // video was slow to start and the content script timed out before the m3u8 fired.
      if (tabId === autoCapture.tabId &&
          (autoCapture.active || Date.now() < autoCapture.graceUntil)) {
        autoConfirmCapture(url, pageUrl, tabId);
        return;
      }

      const pending = {
        m3u8_url: url,
        page_url: pageUrl,
        tabId: tabId,
        timestamp: Date.now(),
        preview: null,
        previewStatus: "loading",
      };
      pendingCaptures.push(pending);

      // Keep only last 20 pending
      if (pendingCaptures.length > 20) {
        pendingCaptures = pendingCaptures.slice(-20);
      }

      const index = pendingCaptures.length - 1;

      updateBadge();

      // Don't show dialog yet — wait for preview to determine quality.
      // Only 720p+ will get an on-page dialog; lower quality stays in popup only.
      fetchPreview(pending, index);
    });
  },
  { urls: ["<all_urls>"] },
  []
);

// ========= PREVIEW / CONFIRM / SEND =========

async function fetchPreview(pending, index) {
  const ctx = episodeContextByTab.get(pending.tabId) || {};
  try {
    const resp = await fetch(PREVIEW_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        m3u8_url: pending.m3u8_url,
        page_url: pending.page_url,
        show_name: ctx.show_name || "",
        season: ctx.season ?? null,
        episode: ctx.episode ?? null,
      }),
    });

    const data = await resp.json();
    if (data.status === "ok") {
      pending.preview = data;
      pending.previewStatus = "ready";
    } else {
      pending.previewStatus = "error";
      pending.previewError = data.message || "Preview failed";
    }
  } catch (err) {
    pending.previewStatus = "error";
    pending.previewError = "Server not running? " + err.message;
  }

  updateBadge();

  // Show on-page dialog only for 720p+ content
  const currentIndex = pendingCaptures.indexOf(pending);
  if (currentIndex >= 0 && isHighQuality(pending.preview)) {
    notifyTab(pending.tabId, {
      type: "showCaptureDialog",
      index: currentIndex,
      previewStatus: pending.previewStatus,
      previewError: pending.previewError || null,
      preview: pending.preview,
    });
  }
}

function notifyTab(tabId, message) {
  if (!tabId || tabId < 0) return;
  chrome.tabs.sendMessage(tabId, message).catch(() => {
    // Content script not loaded yet, ignore
  });
}

function isHighQuality(preview) {
  if (!preview || !preview.quality) return false;
  // quality is like "1920x1080 mp4" — extract height
  const match = preview.quality.match(/(\d+)x(\d+)/);
  if (!match) return false;
  const height = parseInt(match[2], 10);
  return height >= 720;
}

async function confirmDownload(index) {
  if (index < 0 || index >= pendingCaptures.length) return;

  const pending = pendingCaptures.splice(index, 1)[0];

  const capture = {
    m3u8_url: pending.m3u8_url,
    page_url: pending.page_url,
    tabId: pending.tabId,
    timestamp: Date.now(),
    status: "sending",
  };
  captures.push(capture);

  if (captures.length > 50) {
    captures = captures.slice(-50);
  }

  updateBadge();
  await sendToServer(capture);
}

function dismissCapture(index) {
  if (index < 0 || index >= pendingCaptures.length) return;
  const removed = pendingCaptures.splice(index, 1)[0];
  // Allow this URL to be recaptured if it appears again
  seenM3u8.delete(removed.m3u8_url);
  updateBadge();
}

async function sendToServer(capture) {
  const ctx = episodeContextByTab.get(capture.tabId) || {};
  try {
    const resp = await fetch(SERVER_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        m3u8_url: capture.m3u8_url,
        page_url: capture.page_url,
        show_name: ctx.show_name || "",
        season: ctx.season ?? null,
        episode: ctx.episode ?? null,
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

async function sendSubtitle(subtitleUrl, pageUrl, tabId) {
  const ctx = tabId != null ? (episodeContextByTab.get(tabId) || {}) : {};
  try {
    await fetch(SUBTITLE_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        subtitle_url: subtitleUrl,
        page_url: pageUrl,
        show_name: ctx.show_name || "",
        season: ctx.season ?? null,
        episode: ctx.episode ?? null,
      }),
    });
  } catch {
    // Server not running, ignore
  }
}

// ========= BADGE =========

function updateBadge() {
  // Auto-capture mode: show progress like "3/15"
  if (autoCapture.active) {
    chrome.action.setBadgeText({ text: `${autoCapture.doneCount}/${autoCapture.totalCount}` });
    chrome.action.setBadgeBackgroundColor({ color: "#4338ca" });
    return;
  }

  const pendingCount = pendingCaptures.length;
  const activeCount = captures.filter(
    (c) => c.status === "sending" || c.status === "downloading"
  ).length;

  if (pendingCount > 0) {
    chrome.action.setBadgeText({ text: String(pendingCount) });
    chrome.action.setBadgeBackgroundColor({ color: "#f59e0b" });
  } else if (activeCount > 0) {
    chrome.action.setBadgeText({ text: String(activeCount) });
    chrome.action.setBadgeBackgroundColor({ color: "#6366f1" });
  } else if (captures.length > 0) {
    chrome.action.setBadgeText({ text: String(captures.length) });
    chrome.action.setBadgeBackgroundColor({ color: "#22c55e" });
  } else {
    chrome.action.setBadgeText({ text: "" });
  }
}

// ========= AUTO-CAPTURE =========

async function autoConfirmCapture(m3u8Url, pageUrl, tabId) {
  const capture = {
    m3u8_url: m3u8Url,
    page_url: pageUrl,
    tabId: tabId,
    timestamp: Date.now(),
    status: "sending",
  };
  captures.push(capture);

  if (captures.length > 50) {
    captures = captures.slice(-50);
  }

  updateBadge();
  await sendToServer(capture);

  // Wait a moment for any remaining subtitle requests to arrive at the server
  // (subtitles load on page init but some may still be in-flight)
  await new Promise((r) => setTimeout(r, 2000));

  // Notify the content script that this episode is done
  autoCapture.doneCount++;
  updateBadge();

  notifyTab(tabId, {
    type: "autoCaptureEpisodeDone",
    season: autoCapture.season,
    episode: autoCapture.currentEp,
  });
}

function startAutoCapture(season, startEp, endEp, tabId, tabUrl, serverNum) {
  const siteConfig = getSiteConfig(tabUrl);

  autoCapture = {
    active: true,
    finished: false,
    tabId,
    season,
    startEp,
    endEp,
    currentEp: startEp,
    doneCount: 0,
    totalCount: endEp - startEp + 1,
    siteConfig,
    serverNum: serverNum || 1,
  };

  // Clear seen state so first episode's m3u8 and subtitles are detected fresh
  seenM3u8.clear();
  subtitlesSent.clear();
  pendingCaptures = [];
  updateBadge();

  // Forward to content script — it will navigate to the first episode
  notifyTab(tabId, {
    type: "beginAutoCapture",
    season,
    startEp,
    endEp,
    siteConfig,
    serverNum: autoCapture.serverNum,
  });
}

function stopAutoCapture() {
  const tabId = autoCapture.tabId;
  autoCapture.active = false;
  autoCapture.finished = false;
  updateBadge();

  if (tabId) {
    notifyTab(tabId, { type: "stopAutoCapture" });
  }
}

// ========= MESSAGE HANDLER =========

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "getCaptures") {
    sendResponse({ captures, pendingCaptures });
  } else if (msg.type === "confirmDownload") {
    confirmDownload(msg.index).then(() => sendResponse({ ok: true }));
    return true;  // async response
  } else if (msg.type === "dismissCapture") {
    dismissCapture(msg.index);
    sendResponse({ ok: true });
  } else if (msg.type === "clearCaptures") {
    captures = [];
    pendingCaptures = [];
    subtitlesSent.clear();
    seenM3u8.clear();
    episodeContextByTab.clear();
    autoCapture = {
      active: false, finished: false, tabId: null, season: null,
      startEp: null, endEp: null, currentEp: null, doneCount: 0,
      totalCount: 0, siteConfig: null, graceUntil: 0,
    };
    updateBadge();
    sendResponse({ ok: true });
  } else if (msg.type === "startAutoCapture") {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      const tab = tabs[0];
      if (!tab) {
        sendResponse({ ok: false, error: "No active tab" });
        return;
      }
      startAutoCapture(msg.season, msg.startEp, msg.endEp, tab.id, tab.url, msg.serverNum);
      sendResponse({ ok: true });
    });
    return true; // async response
  } else if (msg.type === "stopAutoCapture") {
    stopAutoCapture();
    sendResponse({ ok: true });
  } else if (msg.type === "getAutoCaptureState") {
    sendResponse({
      active: autoCapture.active,
      finished: autoCapture.finished,
      season: autoCapture.season,
      startEp: autoCapture.startEp,
      endEp: autoCapture.endEp,
      currentEp: autoCapture.currentEp,
      doneCount: autoCapture.doneCount,
      totalCount: autoCapture.totalCount,
    });
  } else if (msg.type === "checkAutoCapture") {
    // Content script checks on page load if auto-capture is active for this tab
    if (autoCapture.active && sender.tab && sender.tab.id === autoCapture.tabId) {
      sendResponse({
        active: true,
        season: autoCapture.season,
        currentEp: autoCapture.currentEp,
        startEp: autoCapture.startEp,
        endEp: autoCapture.endEp,
        siteConfig: autoCapture.siteConfig,
        serverNum: autoCapture.serverNum,
      });
    } else {
      sendResponse({ active: false });
    }
  } else if (msg.type === "autoCaptureAdvance") {
    // Content script finished an episode, advance to next or complete
    if (autoCapture.active && autoCapture.currentEp < autoCapture.endEp) {
      autoCapture.currentEp++;
      // Clear seen state so next episode's m3u8 and subtitles are detected fresh
      seenM3u8.clear();
      subtitlesSent.clear();
      updateBadge();
      sendResponse({
        hasNext: true,
        season: autoCapture.season,
        nextEp: autoCapture.currentEp,
        startEp: autoCapture.startEp,
        endEp: autoCapture.endEp,
        siteConfig: autoCapture.siteConfig,
        serverNum: autoCapture.serverNum,
      });
    } else {
      autoCapture.active = false;
      autoCapture.finished = true;
      // Grace period: keep auto-confirming for 15s in case the last episode's
      // m3u8 fires after the content script already timed out and advanced.
      autoCapture.graceUntil = Date.now() + 15000;
      updateBadge();
      sendResponse({ hasNext: false });
    }
  } else if (msg.type === "autoCaptureClickedPlay") {
    // Content script tells us it clicked play for an episode — update current ep
    autoCapture.currentEp = msg.episode;
    updateBadge();
    sendResponse({ ok: true });
  } else if (msg.type === "autoCaptureComplete") {
    autoCapture.active = false;
    autoCapture.finished = true;
    updateBadge();
    sendResponse({ ok: true });
  } else if (msg.type === "clearEpisodeState") {
    // Clear seen m3u8s between episodes so the next capture is detected fresh
    seenM3u8.clear();
    subtitlesSent.clear();
    sendResponse({ ok: true });
  } else if (msg.type === "setEpisodeContext") {
    // Content script reports which episode is playing (for DOM-based sites like brocoflix)
    const tabId = sender.tab?.id;
    if (tabId != null) {
      episodeContextByTab.set(tabId, {
        show_name: msg.show_name || "",
        season: msg.season ?? null,
        episode: msg.episode ?? null,
      });
    }
    sendResponse({ ok: true });
  }
  return true;
});
