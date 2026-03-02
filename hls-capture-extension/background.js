const SERVER_URL = "http://localhost:9876/capture";
const PREVIEW_URL = "http://localhost:9876/preview";
const SUBTITLE_URL = "http://localhost:9876/subtitle";
const SEASON_INFO_URL = "http://localhost:9876/season-info";

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
  epoch: 0,              // increments each episode advance; used to detect stale done-signals
  episodeDoneSent: false, // true after first done-signal for current epoch (prevents duplicates)
  // Multi-season fields
  multiSeason: false,
  startSeason: null,
  endSeason: null,
  currentSeason: null,
  // DOM-based episode discovery (hash-reload sites)
  episodeHashes: [],     // discovered episode list for current season [{hash, epStart, epEnd}]
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
    if (seenM3u8.has(url)) {
      if (autoCapture.active) console.log(`[AC] m3u8 blocked by seenM3u8 (ep ${autoCapture.currentEp}, epoch ${autoCapture.epoch}): ${url.slice(0, 80)}`);
      return;
    }
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
        // Verify page URL hash matches expected episode.
        // 1movies.bz redirects non-existent episodes to #ep=1,1 — without
        // this check the wrong content gets auto-confirmed and the skip
        // counter never triggers, causing an infinite loop on S01E01.
        if (autoCapture.episodeHashes.length > 0) {
          // Discovery mode: currentEp is a 1-based index into episodeHashes,
          // so validate by comparing the page hash against the discovered hash.
          const epIdx = autoCapture.currentEp - 1;
          const expectedHash = epIdx >= 0 && epIdx < autoCapture.episodeHashes.length
            ? autoCapture.episodeHashes[epIdx].hash
            : null;
          if (expectedHash) {
            const pageHashMatch = pageUrl.match(/#ep=\d+,\d+(?:-\d+)?/);
            if (pageHashMatch && pageHashMatch[0] !== expectedHash) {
              console.log(`[AC] m3u8 REJECTED: page hash ${pageHashMatch[0]} doesn't match expected ${expectedHash}`);
              return;
            }
          }
        } else {
          // Fallback (no discovery): currentEp IS the actual episode number,
          // so compare numerically.  Combined episodes use #ep=season,start-end.
          const hashMatch = pageUrl.match(/#ep=(\d+),(\d+)(?:-(\d+))?/);
          if (hashMatch) {
            const actualSeason = parseInt(hashMatch[1], 10);
            const actualEpStart = parseInt(hashMatch[2], 10);
            const actualEpEnd = hashMatch[3] ? parseInt(hashMatch[3], 10) : actualEpStart;
            const expectedSeason = autoCapture.multiSeason ? autoCapture.currentSeason : autoCapture.season;
            const expectedEp = autoCapture.currentEp;
            if (actualSeason !== expectedSeason || expectedEp < actualEpStart || expectedEp > actualEpEnd) {
              console.log(`[AC] m3u8 REJECTED: page hash S${actualSeason}E${actualEpStart}${hashMatch[3] ? "-" + actualEpEnd : ""} doesn't cover expected S${expectedSeason}E${expectedEp}`);
              return;
            }
          }
        }
        console.log(`[AC] m3u8 intercepted for ep ${autoCapture.currentEp} (epoch ${autoCapture.epoch}): ${url.slice(0, 80)}`);
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
  // Auto-capture mode: show progress
  if (autoCapture.active) {
    const text = autoCapture.totalCount > 0
      ? `${autoCapture.doneCount}/${autoCapture.totalCount}`
      : `${autoCapture.doneCount}`;
    chrome.action.setBadgeText({ text });
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
  // Snapshot the epoch so we can detect if we've advanced past this episode
  // by the time the download + delay finishes
  const epoch = autoCapture.epoch;
  const ep = autoCapture.currentEp;
  console.log(`[AC] autoConfirmCapture START ep=${ep} epoch=${epoch} url=${m3u8Url.slice(0, 80)}`);

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
  console.log(`[AC] autoConfirmCapture server responded ep=${ep} status=${capture.status}`);

  // Wait a moment for any remaining subtitle requests to arrive at the server
  // (subtitles load on page init but some may still be in-flight)
  await new Promise((r) => setTimeout(r, 2000));

  // Guard: only send done-signal if we're still on the same episode AND
  // haven't already sent one for this episode.  Multiple m3u8 URLs per episode
  // (ad pre-rolls, quality variants, CDN retries) each trigger this function,
  // but only the first should fire the done-signal.  Without this guard, a
  // stale done-signal from episode N can resolve episode N+1's
  // waitForEpisodeDone, causing it to be skipped.
  if (epoch !== autoCapture.epoch || autoCapture.episodeDoneSent) {
    console.log(`[AC] autoConfirmCapture SUPPRESSED ep=${ep} snapshotEpoch=${epoch} currentEpoch=${autoCapture.epoch} doneSent=${autoCapture.episodeDoneSent}`);
    return;
  }
  autoCapture.episodeDoneSent = true;

  // Notify the content script that this episode is done
  autoCapture.doneCount++;
  updateBadge();

  console.log(`[AC] autoConfirmCapture DONE-SIGNAL ep=${ep} epoch=${epoch}`);
  notifyTab(tabId, {
    type: "autoCaptureEpisodeDone",
    season: autoCapture.multiSeason ? autoCapture.currentSeason : autoCapture.season,
    episode: autoCapture.currentEp,
  });
}

function startAutoCapture(params, tabId, tabUrl) {
  const siteConfig = getSiteConfig(tabUrl);
  const serverNum = params.serverNum || 1;

  if (params.multiSeason) {
    // Multi-season mode: start at first season, ep 1, endEp unknown (detected per season)
    autoCapture = {
      active: true,
      finished: false,
      tabId,
      season: params.startSeason,
      startEp: 1,
      endEp: null,       // set per-season by autoCaptureEpisodesDiscovered or autoCaptureSeasonDetected
      currentEp: 1,
      doneCount: 0,
      totalCount: 0,     // accumulated as seasons are detected/discovered
      siteConfig,
      serverNum,
      epoch: 0,
      episodeDoneSent: false,
      graceUntil: 0,
      multiSeason: true,
      startSeason: params.startSeason,
      endSeason: params.endSeason,
      currentSeason: params.startSeason,
      consecutiveSkips: 0,
      episodeHashes: [],
    };
  } else {
    // Single-season episode-range mode (existing behavior)
    autoCapture = {
      active: true,
      finished: false,
      tabId,
      season: params.season,
      startEp: params.startEp,
      endEp: params.endEp,
      currentEp: params.startEp,
      doneCount: 0,
      totalCount: params.endEp - params.startEp + 1,
      siteConfig,
      serverNum,
      epoch: 0,
      episodeDoneSent: false,
      graceUntil: 0,
      multiSeason: false,
      startSeason: null,
      endSeason: null,
      currentSeason: null,
      episodeHashes: [],
    };
  }

  // Clear seen state so first episode's m3u8 and subtitles are detected fresh
  seenM3u8.clear();
  subtitlesSent.clear();
  pendingCaptures = [];
  updateBadge();

  // Navigate to the first episode
  if (siteConfig?.navStrategy === "hash-reload") {
    // For hash-reload sites, navigate directly via scripting API.
    // This is more reliable than notifyTab because it doesn't depend on
    // an existing content script (which may have a stale context after
    // extension reload).  After reload, the fresh content script picks up
    // auto-capture state via checkAutoCaptureOnLoad.
    const hash = siteConfig.makeEpisodeHash(autoCapture.season, autoCapture.startEp);
    chrome.scripting.executeScript({
      target: { tabId },
      func: (h) => { location.hash = h; location.reload(); },
      args: [hash],
    });
  } else {
    // SPA sites: content script handles navigation in-place
    notifyTab(tabId, {
      type: "beginAutoCapture",
      season: autoCapture.season,
      startEp: autoCapture.startEp,
      endEp: autoCapture.endEp,
      siteConfig,
      serverNum,
      multiSeason: autoCapture.multiSeason,
    });
  }
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
      epoch: 0, episodeDoneSent: false,
      multiSeason: false, startSeason: null, endSeason: null, currentSeason: null,
      consecutiveSkips: 0, episodeHashes: [],
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
      startAutoCapture(msg, tab.id, tab.url);
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
      multiSeason: autoCapture.multiSeason,
      startSeason: autoCapture.startSeason,
      endSeason: autoCapture.endSeason,
      currentSeason: autoCapture.currentSeason,
    });
  } else if (msg.type === "checkAutoCapture") {
    // Content script checks on page load if auto-capture is active for this tab
    if (autoCapture.active && sender.tab && sender.tab.id === autoCapture.tabId) {
      // Provide the discovered hash for the current episode (if available)
      const epIndex = autoCapture.currentEp - 1;
      const currentHash = autoCapture.episodeHashes.length > 0 && epIndex >= 0 && epIndex < autoCapture.episodeHashes.length
        ? autoCapture.episodeHashes[epIndex].hash
        : null;
      sendResponse({
        active: true,
        season: autoCapture.season,
        currentEp: autoCapture.currentEp,
        startEp: autoCapture.startEp,
        endEp: autoCapture.endEp,
        siteConfig: autoCapture.siteConfig,
        serverNum: autoCapture.serverNum,
        multiSeason: autoCapture.multiSeason,
        currentSeason: autoCapture.currentSeason,
        startSeason: autoCapture.startSeason,
        endSeason: autoCapture.endSeason,
        needsDiscovery: autoCapture.episodeHashes.length === 0,
        hash: currentHash,
      });
    } else {
      sendResponse({ active: false });
    }
  } else if (msg.type === "autoCaptureAdvance") {
    // Content script finished an episode, advance to next or complete
    const skipped = msg.skipped === true;

    // Multi-season with unknown episode count (hash-reload): use consecutive
    // skip counter — require 2 consecutive skipped episodes before declaring
    // a season done.  A single transient failure (slow page load, timing) won't
    // prematurely end the capture.
    if (skipped && autoCapture.active && autoCapture.multiSeason && autoCapture.endEp == null) {
      autoCapture.consecutiveSkips = (autoCapture.consecutiveSkips || 0) + 1;
      console.log(`[AC] SKIP S${autoCapture.currentSeason} EP${autoCapture.currentEp} (consecutive: ${autoCapture.consecutiveSkips})`);

      if (autoCapture.consecutiveSkips >= 2) {
        // 2 consecutive skips = season confirmed done
        autoCapture.consecutiveSkips = 0;
        if (autoCapture.currentSeason < autoCapture.endSeason) {
          // Advance to next season
          const prevSeason = autoCapture.currentSeason;
          autoCapture.currentSeason++;
          autoCapture.season = autoCapture.currentSeason;
          autoCapture.currentEp = 1;
          autoCapture.startEp = 1;
          autoCapture.endEp = null;
          autoCapture.episodeHashes = [];  // clear so content script re-discovers
          autoCapture.epoch++;
          autoCapture.episodeDoneSent = false;
          console.log(`[AC] SKIP->ADVANCE SEASON S${prevSeason} -> S${autoCapture.currentSeason} (epoch ${autoCapture.epoch})`);
          updateBadge();
          sendResponse({
            hasNext: true,
            season: autoCapture.currentSeason,
            nextEp: 1,
            startEp: 1,
            endEp: null,
            siteConfig: autoCapture.siteConfig,
            serverNum: autoCapture.serverNum,
            multiSeason: true,
            newSeason: true,
          });
        } else {
          // Last season done
          console.log(`[AC] SKIP on last season S${autoCapture.currentSeason} — finishing`);
          autoCapture.active = false;
          autoCapture.finished = true;
          autoCapture.graceUntil = Date.now() + 15000;
          updateBadge();
          sendResponse({ hasNext: false });
        }
      } else {
        // First skip — try next episode (might be a transient failure)
        const prevEp = autoCapture.currentEp;
        autoCapture.currentEp++;
        autoCapture.epoch++;
        autoCapture.episodeDoneSent = false;
        console.log(`[AC] SKIP->TRY NEXT ep ${prevEp} -> ${autoCapture.currentEp} (epoch ${autoCapture.epoch})`);
        updateBadge();
        const skipNextIdx = autoCapture.currentEp - 1;
        const skipNextHash = autoCapture.episodeHashes.length > skipNextIdx ? autoCapture.episodeHashes[skipNextIdx].hash : null;
        sendResponse({
          hasNext: true,
          season: autoCapture.multiSeason ? autoCapture.currentSeason : autoCapture.season,
          nextEp: autoCapture.currentEp,
          startEp: autoCapture.startEp,
          endEp: autoCapture.endEp,
          siteConfig: autoCapture.siteConfig,
          serverNum: autoCapture.serverNum,
          multiSeason: autoCapture.multiSeason,
          hash: skipNextHash,
        });
      }
    } else if (autoCapture.active && (autoCapture.endEp == null || autoCapture.currentEp < autoCapture.endEp)) {
      // Next episode in current season (endEp null = unknown count, keep going)
      if (!skipped) autoCapture.consecutiveSkips = 0;  // reset on successful capture
      const prevEp = autoCapture.currentEp;
      autoCapture.currentEp++;
      autoCapture.epoch++;
      autoCapture.episodeDoneSent = false;
      console.log(`[AC] ADVANCE ep ${prevEp} -> ${autoCapture.currentEp} (epoch ${autoCapture.epoch})`);
      updateBadge();
      const advNextIdx = autoCapture.currentEp - 1;
      const advNextHash = autoCapture.episodeHashes.length > advNextIdx ? autoCapture.episodeHashes[advNextIdx].hash : null;
      sendResponse({
        hasNext: true,
        season: autoCapture.multiSeason ? autoCapture.currentSeason : autoCapture.season,
        nextEp: autoCapture.currentEp,
        startEp: autoCapture.startEp,
        endEp: autoCapture.endEp,
        siteConfig: autoCapture.siteConfig,
        serverNum: autoCapture.serverNum,
        multiSeason: autoCapture.multiSeason,
        hash: advNextHash,
      });
    } else if (autoCapture.active && autoCapture.multiSeason && autoCapture.currentSeason < autoCapture.endSeason) {
      // Multi-season: advance to next season (endEp reached)
      const prevSeason = autoCapture.currentSeason;
      autoCapture.currentSeason++;
      autoCapture.season = autoCapture.currentSeason;
      autoCapture.currentEp = 1;
      autoCapture.startEp = 1;
      autoCapture.endEp = null;  // will be set by autoCaptureEpisodesDiscovered or autoCaptureSeasonDetected
      autoCapture.episodeHashes = [];  // clear so content script re-discovers
      autoCapture.epoch++;
      autoCapture.episodeDoneSent = false;
      console.log(`[AC] ADVANCE SEASON S${prevSeason} -> S${autoCapture.currentSeason} (epoch ${autoCapture.epoch})`);
      updateBadge();
      sendResponse({
        hasNext: true,
        season: autoCapture.currentSeason,
        nextEp: 1,
        startEp: 1,
        endEp: null,
        siteConfig: autoCapture.siteConfig,
        serverNum: autoCapture.serverNum,
        multiSeason: true,
        newSeason: true,
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
    console.log(`[AC] clearEpisodeState (ep ${autoCapture.currentEp}, epoch ${autoCapture.epoch}, had ${seenM3u8.size} seen urls)`);
    seenM3u8.clear();
    subtitlesSent.clear();
    autoCapture.episodeDoneSent = false;  // allow retry to receive a fresh done-signal
    sendResponse({ ok: true });
  } else if (msg.type === "autoCaptureEpisodesDiscovered") {
    // Content script discovered episode hashes from the DOM (hash-reload sites)
    if (autoCapture.active && msg.episodes && msg.episodes.length > 0) {
      autoCapture.episodeHashes = msg.episodes;
      autoCapture.endEp = msg.episodes.length;
      autoCapture.startEp = 1;
      autoCapture.totalCount += msg.episodes.length;
      const season = autoCapture.multiSeason ? autoCapture.currentSeason : autoCapture.season;
      console.log(`[AC] Season S${season}: discovered ${msg.episodes.length} episodes`);
      updateBadge();

      // Notify the server so it can log the season episode list
      fetch(SEASON_INFO_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          show_name: msg.showName || "",
          season,
          episodes: msg.episodes,
        }),
      }).catch(() => {}); // fire-and-forget
    }
    sendResponse({ ok: true });
  } else if (msg.type === "autoCaptureSeasonDetected") {
    // Content script reports episode count after selecting a season in the dropdown
    if (autoCapture.active && autoCapture.multiSeason) {
      autoCapture.endEp = msg.episodeCount;
      autoCapture.startEp = 1;
      autoCapture.totalCount += msg.episodeCount;
      console.log(`[AC] Season S${autoCapture.currentSeason} detected: ${msg.episodeCount} episodes (total across seasons: ${autoCapture.totalCount})`);
      updateBadge();
    }
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
