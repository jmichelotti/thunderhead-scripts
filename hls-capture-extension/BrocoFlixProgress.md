# BrocoFlix Browser-Side Download — Progress Log

## Status: SERVICE WORKER DOWNLOAD IN PROGRESS (2026-03-27)

Movies and TV episodes both download successfully. TV episodes achieve 100%. Movies achieve ~99.3-99.4% with both the old MAIN-world approach and the new service worker approach. A new architecture (service worker fetch + declarativeNetRequest header spoofing) has been implemented but not fully tested — the 0ms throttle run needs testing.

## What Works
- **NEW: Service worker download path** (FetchV-style) — fetches segments directly from background.js service worker, bypassing MAIN-world HTTP/2 socket pool entirely
- **NEW: declarativeNetRequest** header spoofing — rewrites Origin and Referer headers at Chrome's network stack level so CDN accepts service worker requests
- **PRESERVED: MAIN-world fallback** — old relay chain still intact, auto-activates if SW manifest fetch fails
- Full relay chain (fallback): MAIN-world iframe → postMessage → content script → background → server
- Server receives and writes chunks to disk (confirmed with server-side chunk logging)
- Session lifecycle: `/brocoflix-start` → `/brocoflix-chunk` → `/brocoflix-done` (mux) or `/brocoflix-abort`
- Popup shows "uploading" status with chunk progress
- Client-side dedup (`brocoflixActiveUrls` Set) prevents duplicate `startBrocoflixDownload` calls
- Server-side dedup (`seen_urls`) catches any that slip through
- Confirmation popup flow: BrocoFlix queues m3u8 into `pendingCaptures` like 1movies, shows in-page dialog for 720p+, user confirms before download starts. Quality probed from iframe MAIN-world (CDN blocks server-side yt-dlp probing).
- Iframe reload recovery: skip-and-continue downloading + automatic iframe reload to get fresh CDN domain + fresh Chrome socket pool. Proactive reload every 800 segments. Retry passes with 10s delay.
- Auto-click play button after iframe reload (`#btn-play` selector + `autoPlay=true` URL param)
- Simplified file naming (title only, no year) with `fix_file_names.py` running post-mux
- Files saved to root staging dirs (`C:\Temp_Media\Movies` and `C:\Temp_Media\TV Shows`)
- Stall detection and give-up: after 3 stalled reloads at ≥99.5%, or 5 stalled reloads below target, or 15 total reloads → force-mux whatever we have

## Architecture Overview

### NEW: Service worker download (primary path, as of 2026-03-27)
1. `webRequest.onBeforeRequest` intercepts m3u8 URL from BrocoFlix embed iframe
2. m3u8 goes into pending queue → quality probed from iframe → preview dialog shown
3. User confirms → `startBrocoflixDownload()` → `runBrocoflixSwDownload()`
4. `webRequest.onSendHeaders` captures CDN request headers (Origin, Referer) from iframe's HLS player
5. `declarativeNetRequest` dynamic rules set to rewrite Origin→`https://streameeeeee.site` and Referer on all requests to CDN domains
6. Service worker kills the video player in the iframe (frees CDN bandwidth)
7. Service worker fetches m3u8 manifest directly, parses segment list
8. **Key step**: Extracts ALL unique CDN domains from segment URLs (manifest domain ≠ segment domain!) and updates DNR rules to cover all of them
9. Service worker fetches each segment sequentially via `fetch()` with `credentials: "include"` and `cache: "no-store"`
10. POSTs raw binary ArrayBuffer directly to `/brocoflix-chunk` on localhost:9876 (no base64 encoding needed)
11. On completion: `/brocoflix-done` → server muxes TS → MP4

### Why the service worker approach works
- **declarativeNetRequest** rewrites Origin header at Chrome's network stack level — bypasses the "forbidden header" restriction in fetch() API
- Service worker has a **separate HTTP/2 socket pool** from the page — immune to GOAWAY poisoning
- **No base64 encoding** — direct ArrayBuffer POST saves ~33% memory overhead
- **No relay chain** — service worker POSTs directly to localhost (no content script middleman)
- CDN accepts requests because DNR makes them look identical to iframe-origin requests

### Important: CDN uses different domains for manifest vs segments
The m3u8 manifest and the TS segments are often on DIFFERENT CDN domains. For example:
- Manifest: `stormfox27.live`
- Segments: `icynebula71.pro`

DNR rules must cover ALL domains. The code extracts segment domains after parsing the manifest and updates rules accordingly.

### FALLBACK: MAIN-world relay chain (old approach, auto-activates if SW manifest fetch fails)
1. `chrome.scripting.executeScript({ world: "MAIN" })` injects `brocoflixDownloaderFunc` into the embed iframe
2. Downloads each TS segment via fetch(), converts to base64, sends via `window.postMessage`
3. Content script receives postMessage → `chrome.runtime.sendMessage` → background service worker
4. Background decodes base64, POSTs to `/brocoflix-chunk`

### Why the MAIN-world fallback exists
- If the CDN changes its validation and starts rejecting service worker requests even with DNR header spoofing
- If `declarativeNetRequest` permission is unavailable
- The old relay chain is preserved intact in the codebase

### Iframe reload recovery
When segments fail mid-download, the downloader:
1. Skips failed segments and continues downloading remaining segments
2. Proactively reloads iframe every 800 segments (before GOAWAY threshold)
3. After pass completes, reports failed segments to background via `hlsBrocoNeedReload` postMessage
4. Background reloads only the embed iframe (not the full tab) — clears src, restores after 500ms
5. New m3u8 intercepted on fresh CDN domain → re-injects downloader with `completedIndices` skip list
6. Retry pass: only fetches missing segments with 10s delay between each
7. Each reload cancels previous reload's safety timer (prevents stale timer abort bug)
8. Stall detection: if no progress after multiple reloads, force-mux whatever we have

## The CDN Rate Limiting Problem

### What happens
The CDN blocks ~0.6% of segment fetches during movie downloads. The error is always instant "Failed to fetch" (network-level connection reset, not HTTP status, not timeout). Once a specific segment index fails for a given movie, that segment index is permanently blocked for the current IP address — no retry strategy within the same browser session or across CDN domain rotations has ever recovered a persistently blocked segment.

### Failure patterns observed
- **TV episodes succeed at 100%**: Survivor S50E04 (1088 segments) — only 2 transient failures, both recovered with 3s backoff
- **Movies hit ~99.3-99.4%**: Persistent failures at specific segment indices that never recover
- **Failure rate**: ~0.6% of segments per movie (5-9 persistent failures per 881-1528 segments)
- **First failure timing**: Varies by content — segment 114 for Rambo III, segment 261 for The 'Burbs, always the same index for the same movie
- **Failure spacing**: At 500ms pace, new failures appear every ~80-150 segments
- **Failures are instant**: "Failed to fetch" is a connection-level error. Increasing fetch timeout from 30s to 120s makes no difference — error is immediate
- **Deterministic per-content**: Same segment indices fail on every attempt for the same movie, across all CDN domains

### Two types of failures
1. **Transient failures** (~50% of initial failures): Recover on iframe reload with fresh CDN domain. These include "signal is aborted without reason" (fetch timeout at 30s) and some "Failed to fetch" errors.
2. **Persistent failures** (~50% of initial failures): Never recover across any number of reloads or CDN domain rotations. Appear to be per-IP, per-content blocks at the CDN backend.

## Test Results

### Successful Downloads

#### TV Episode: Survivor S50E04 (~1088 segments) — 2026-03-19
| Segments | Throttle | Result |
|----------|---------|--------|
| 1088/1088 ✅ | 200ms→1500ms after first failure | **100% SUCCESS** — only 2 retries (seg 348, 471), both succeeded with 3s backoff. Killing video player was the key fix. |

#### Movie: Send Help (~1362 segments) — 2026-03-23
| Segments | Reloads | Result |
|----------|---------|--------|
| 1362/1362 ✅ | 1 (proactive at 800) | **100% SUCCESS** — first movie to complete. All failures were transient and recovered on reload. |

#### Movie: Rambo III (~1528 segments) — 2026-03-24
| Segments | Reloads | Result |
|----------|---------|--------|
| 1519/1528 (99.4%) | 8 (1 proactive + 7 retry) | **COMPLETED** — 9 persistent failures: [114, 119, 220, 770, 968, 1039, 1073, 1117, 1497]. Muxed successfully. |

Reload progression for Rambo III:
- Pass 1: 800/1528 done, 5 skipped → proactive reload at 800
- Reload #1: recovered 367 (timeout) from first pass. New failures: 968, 1039, 1073, 1117, 1170, 1497. Total 1518/1528.
- Reload #2: retry pass recovered 1170 (timeout) and 1467. Total 1519/1528.
- Reloads #3-7: same 9 segments failed every time across domains stormfox27.live, mistwolf88.xyz, bluehorizon4.site, silvercloud9.pro. Zero progress.
- Reload #8: stalled=5/3, gave up and force-muxed.

#### Movie: Conan the Barbarian (~1479 segments) — 2026-03-24
Download started, proactive reload at 800 worked, but aborted due to stale reload timer bug (timer from reload #2 fired during reload #4). Timer bug was fixed mid-session. Did not re-test to completion.

### Failed Downloads (Pre-Iframe-Reload Era, 2026-03-15 to 2026-03-23)

All movie downloads before the iframe reload strategy was implemented failed completely. Segments that failed never recovered within the same browser session regardless of retry strategy.

#### The 'Burbs (~881 segments) — 2026-03-22/23, 7 attempts
| Attempt | Strategy | Result |
|---------|----------|--------|
| 1 | Unlimited retry + exponential backoff (3-120s) | Stuck forever on segment 261 |
| 2 | Skip after 3 tries + separate retry passes (30s cooldown) | First pass: skipped 5 segs (261,343,447,545,607). All 5 retry passes failed — none recovered |
| 3 | Interleaved retry (re-queue 20 segs later) | Segment 261 failed all 8 attempts despite 20 successful segments between each retry |
| 4 | Interleaved retry + manifest refresh on failure | Same — fresh manifest URLs didn't help |
| 5 | 90s cooldown + re-queue 60 segs later | Same — 90s pause didn't help |
| 6 | Same + `cache: "no-store"` on fetch | Same |
| 7 | Same + cache-busting query param (`?_r=timestamp`) | Same |

#### The 'Burbs — 2026-03-24 (with iframe reload)
| Segments | Reloads | Result |
|----------|---------|--------|
| 875/881 (99.3%) | Multiple | 6 persistent failures: [261, 343, 447, 545, 607, + 1 other]. Same indices as pre-reload attempts. |

#### Rambo III (~1528 segments) — 2026-03-22/23, pre-reload
| Attempt | Strategy | Result |
|---------|----------|--------|
| 1 | 90s cooldown + re-queue 60 segs later | Failed at segment 114 (all 5 attempts). Also: 119, 127, 220, 282 |
| 2 | Same + 120s fetch timeout | Same — errors are instant, not timeouts |
| 3 | Same + cache-busting query params | Same |

#### Rocky (~1170-1434 segments) — 2026-03-15 to 2026-03-17
| Date | Segments | Strategy | Result |
|------|----------|----------|--------|
| 2026-03-15 | 101/1170 | No retries, no throttle | Failed to fetch |
| 2026-03-16 | 342/1217 | 3 retries, 2s/6s backoff, 100ms throttle | Failed after 3 attempts |
| 2026-03-17 | 127/1170 | 5 retries, 2-16s backoff, 200ms throttle | Failed after 5 attempts |
| 2026-03-17 | 342/1217 | 5 retries + manifest refresh, 200ms→1s throttle | Failed after 5 attempts |
| 2026-03-17 | ~100/1434 | Unlimited retries, 2-60s backoff | Stuck in retry loop (never recovered) |

## What Has Been Tried and Ruled Out

### Retry strategies that don't work (within same browser session)
1. **Exponential backoff** (3s to 120s): Failed segments never recover regardless of wait time
2. **Interleaved retry** (re-queue N segments later): Failed segments still fail even after 20-60 successful segments between attempts
3. **Manifest refresh**: Getting fresh segment URLs (new manifest → new CDN paths for same segment index) doesn't help
4. **Longer fetch timeout** (30s → 120s): Errors are instant connection resets, not timeouts
5. **Cache bypass** (`cache: "no-store"` on fetch): Doesn't help
6. **Cache-busting query params** (`?_r=timestamp`): Doesn't help
7. **90s cooldown pause**: Doesn't help — CDN rate-limit window should be clear but segments stay blocked
8. **Multiple retry passes with delays**: Same segments fail on every pass

### Retry strategies that DO work
1. **Iframe reload** (fresh CDN domain + fresh Chrome socket pool): Recovers ~50% of failed segments per reload. Transient failures recover; persistent failures don't.
2. **Killing the video player** before downloading: Frees CDN bandwidth/connection slots. Was the key fix for TV episode success.

### Fetch API variations tried
1. `fetch()` with default options → "Failed to fetch"
2. `fetch()` with `cache: "no-store"` → same
3. `fetch()` with `cache: "no-store"` + AbortController timeout → same (plus "signal is aborted" for slow segments at 15s timeout — too aggressive)
4. `fetch()` with cache-busting query params → same

### Full sequential re-fetch on retry pass (2026-03-24)
Tried fetching ALL segments sequentially on retry passes (including already-completed ones) so the CDN would see normal playback access patterns instead of cherry-picked retry requests. **Did not help** — the same persistent segments still failed even when buried in a sequential stream of successful fetches. This rules out CDN access-pattern detection as the blocking mechanism.

### Pacing variations
- 0ms (no throttle): ~100 segments before first failure
- 100ms: ~340 segments before first failure
- 200ms: ~260-350 segments before first failure
- 500ms (current): ~260-800 segments before first failure, fewer total failures
- 1500ms: ~80-100 segments between new failures (paradoxically, failures still occur at roughly the same total count)

### Fetch timeout tuning
- 15s: Too aggressive — causes false-positive "signal is aborted" failures on slow-but-valid segments (~6 extra failures per 1500 segments). These recover on reload but waste reload cycles.
- 30s (current): Good balance — only catches genuinely dead connections
- 120s: Too long — hanging fetches freeze the download for 2 minutes per stuck segment

## Root Cause Analysis

### Confirmed: HTTP/2 GOAWAY + Chrome socket pool poisoning
The "permanently poisoned segments" within a single browser session are caused by Chrome's handling of HTTP/2 GOAWAY frames. When the CDN's NGINX sends a GOAWAY (triggered by `keepalive_requests` limit, typically ~1000), Chrome's socket pool marks those in-flight streams as permanently failed. Chrome bug #681477 documents that streams aborted by GOAWAY are NOT retried — they permanently fail as "Failed to fetch" in the same socket pool.

**Evidence:**
- Iframe reload (which creates fresh socket pool via new origin) recovers ~50% of failures
- Same segment indices fail deterministically for the same content
- Error is instant connection-level, not HTTP status

### Unconfirmed: Per-IP per-content CDN rate limiting
The ~50% of failures that persist even across iframe reloads (fresh CDN domains, fresh socket pools) appear to be rate-limited at a layer above the individual CDN edge server. This blocking is:
- **Per-IP**: Same segments blocked from same IP regardless of CDN domain
- **Per-content**: Different movies have different blocked segment indices (not a global rate limit)
- **Persistent**: Blocked segments never recover across any tested delay (up to minutes between retries)
- **Not pattern-based**: Full sequential re-fetch (blending retries into normal playback stream) didn't help

## Current Implementation (as of 2026-03-27)

### Service worker download flow (`runBrocoflixSwDownload` in background.js)
- Extracts CDN domain from m3u8 URL, determines embed origin from captured headers or defaults to `https://streameeeeee.site`
- Sets `declarativeNetRequest` dynamic rules: Origin + Referer for manifest CDN domain
- Kills video player in iframe via `chrome.scripting.executeScript`
- Fetches m3u8 manifest from service worker, parses segment URLs
- Extracts ALL unique CDN domains from segments, updates DNR rules to cover all of them
- Sequential download with **0ms throttle** (FetchV proves CDN accepts rapid requests from trusted origin)
- 30s abort timeout per segment via AbortController
- `credentials: "include"`, `cache: "no-store"` on each fetch
- Skip-and-continue: failed segments are skipped, download continues to end of manifest
- **No proactive reload** (disabled for SW path — no GOAWAY in SW's separate socket pool)
- After pass completes with failures → request iframe reload via `handleBrocoflixReload()`
- Retry pass (≤20 remaining segments): 3s between each fetch
- Chunks POSTed as raw ArrayBuffer to `/brocoflix-chunk` with binary `Content-Type: application/octet-stream`
- Chunks sent in order via `flushToServer()` buffer
- Falls back to MAIN-world `injectMainWorldDownloader()` if manifest fetch fails

### Refactored shared functions (2026-03-27)
- `handleBrocoflixReload()` — reload logic shared between SW downloader and MAIN-world fallback message handler
- `finalizeBrocoflixDownload()` — mux trigger shared between both paths
- `triggerAutoCaptureEpisodeDone()` — auto-capture signal shared between both paths
- `injectMainWorldDownloader()` — wraps old MAIN-world injection as named fallback

### Iframe reload mechanism (unchanged)
- Background clears iframe src, waits 500ms, restores with `autoPlay=true` URL param
- Auto-clicks `#btn-play` button via `chrome.scripting.executeScript({ allFrames: true })`
- Each reload cancels previous reload's safety timer (fixes stale timer abort bug)
- 120s safety timeout per reload (aborts if no new m3u8 intercepted)
- `seenM3u8` cleared before reload to allow re-interception
- On resume: new m3u8 → `startBrocoflixDownload()` → `runBrocoflixSwDownload()` with new CDN domain + new DNR rules

### Give-up logic (unchanged)
- `TARGET_PCT = 99.5%`
- `MAX_NO_PROGRESS_RELOADS = 3` — stall counter increments when no new segments recovered
- `MAX_TOTAL_RELOADS = 15` — hard limit
- Give up when: (meets 99.5% AND stalled ≥3) OR (stalled ≥5 even if below target) OR (reloads ≥15)
- On give-up: POST `/brocoflix-done` → server muxes whatever chunks were received

### declarativeNetRequest details
- Permission: `"declarativeNetRequest"` in manifest.json
- Rule ID 1: sets both Origin and Referer headers on requests matching CDN domains
- `requestDomains` condition (no resourceTypes filter — applies to all request types including SW fetches)
- Rules updated dynamically: set on download start, updated after manifest parse (segment domains), updated on reload (new CDN domain), cleared when last session finishes
- `setCdnHeaderRules(domains, embedOrigin)` accepts string or array of domains

### Confirmation popup
- BrocoFlix m3u8 goes through same pending/preview/confirm flow as 1movies
- Quality probed from iframe MAIN-world via `probeBrocoflixQuality()` (CDN blocks server-side yt-dlp)
- Server `/preview` endpoint accepts optional `quality` field to skip yt-dlp probe
- Episode context populated from DOM `<h1>` in `fetchPreview()` (movies have no card click)
- Stale context detection via `_pageUrl` field

### Server changes (`hls_download_server.py`)
- `brocoflix_start`: Simplified output paths — Movies: `C:\Temp_Media\Movies\{title}.mp4`, TV: `C:\Temp_Media\TV Shows\{title} SxxExx.mp4` (no year, no subfolder)
- `brocoflix_chunk`: Uses `session["chunks_received"] += 1` (not `chunk_index + 1`) for accurate counting with non-contiguous chunks
- `brocoflix_done`: Runs `fix_file_names.py` post-mux to normalize filename via OMDb

### Log Relay
MAIN-world logs are invisible (BrocoFlix blocks F12 devtools). All `[BF-dl]` logs relay through:
`relayLog()` → `postMessage(hlsBrocoLog)` → content script → `chrome.runtime.sendMessage(brocoflixLog)` → background `console.log`

Retry/progress logs appear in the **service worker console**.

### Server Chunk Logging
`brocoflix_chunk()` prints progress every 50 chunks + first + last chunk:
```
[MOVIE] BrocoFlix chunk 50/1434 (76.5MB received)
```

## Bugs Found and Fixed During Testing

1. **chrome.tabs.reload() navigated away from player** (2026-03-23): Full tab reload caused BrocoFlix to return to movie info page. Fix: reload only the embed iframe by clearing/restoring its src.

2. **Play button not clicked after iframe reload** (2026-03-23): Embed player showed play button overlay requiring manual click. Fix: auto-click `#btn-play` via `chrome.scripting.executeScript({ allFrames: true })` + `autoPlay=true` URL param.

3. **chrome.webNavigation.getAllFrames undefined** (2026-03-23): Used without required permission. Fix: replaced with `chrome.scripting.executeScript` which needs no extra permission.

4. **Infinite reload loop on persistent failures** (2026-03-23): Stop-on-first-failure caused infinite reload loops when specific segments were permanently blocked. Fix: skip-and-continue strategy — download all segments, skip failures, then reload once to retry.

5. **"Cannot access 'isRetryPass' before initialization"** (2026-03-23): `relayLog` referenced `isRetryPass` before `const` declaration. Fix: moved declarations above the log line.

6. **Give-up condition never fires** (2026-03-23): `meetsTarget && stalled >= 3` never triggered because 876/881 = 99.4% < 99.5%. Added fallback: give up after 5 stalled reloads even if below target.

7. **Retry pass triggers "connection dead" prematurely** (2026-03-23): `MAX_CONSECUTIVE_FAILS = remaining` on retry pass with 5 remaining meant all 5 fails triggered early exit before completing the loop. Fix: `MAX_CONSECUTIVE_FAILS = remaining + 1`.

8. **Stale reload timer abort** (2026-03-24): Timer from reload #N fired during reload #N+1's retry pass (which takes ~90s for 9 segments × 10s). Fix: store timer ID on session, cancel previous timer before setting new one.

9. **Fetch timeout too aggressive at 15s** (2026-03-24): Caused ~6 extra "signal is aborted" false-positive failures per 1500 segments. Fix: increased to 30s.

## Duplicate m3u8 Interception (Minor Issue)
Each page load fires 2-3 webRequest events for the same m3u8 URL. `seenM3u8` Set catches most duplicates, but 2 can race into `chrome.tabs.get` async callback before either adds to `seenM3u8`. Both get queued as pending. Server-side dedup handles it cleanly — the noise is cosmetic only (second pending shows "unknown" quality probe).

## Key Technical Details

### CDN infrastructure
- **Embed origins**: `streameeeeee.site`, `vidsrc.cc` — the iframe origin the CDN trusts
- **CDN URL patterns**: `https://<random-domain>/file1/<base64-token>` and `https://<domain>/pl/<base64-token>`
- **CDN domains rotate every page load**: silvercloud9.pro, mistwolf88.xyz, bluehorizon4.site, stormfox27.live, dustfalcon55.xyz, icynebula71.pro, solarwolf23.live, etc.
- **All CDN domains share the same backend**: Per-IP rate limits persist across domain rotations
- **CDN blocks non-browser clients**: yt-dlp, curl, wget, service worker fetch all get 403

### Browser constraints
- **BrocoFlix blocks F12 devtools**: All debugging through service worker console or server logs
- **CORS for localhost from iframe**: Blocked — relay chain required
- **Chrome HTTP/2 GOAWAY bug** (#681477): Failed streams permanently poisoned in socket pool
- **MV3 content scripts**: Share page's network context, can't fetch arbitrary URLs

### Content detection
- Movie pages: No `.episode-card`, title from DOM `<h1>` in `#details-container`
- Page URL contains type: `?type=movie` or `?type=tv` for routing
- Movie output: `C:\Temp_Media\Movies\{title}.mp4`
- TV output: `C:\Temp_Media\TV Shows\{title} SxxExx.mp4`

### FetchV extension reference — reverse-engineered (2026-03-27)
FetchV (nfmmmhanepmpifddlkkmihkalkoekpfd) can download from BrocoFlix at 100% with zero errors in ~3 minutes for an 881-segment movie.

**How FetchV works (from source analysis + GitHub daoquangphuong/fetchv):**
1. `webRequest.onBeforeSendHeaders` captures ALL original request headers from the m3u8/segment requests
2. Opens a dedicated tab (`fetchv.net/m3u8downloader`) where its content script (`m3u8downloader.js`) manages the download
3. Content script sends `FETCH_DATA` messages to the service worker for each segment
4. **Service worker fetches each segment** using `fetch(url, {mode: "cors", credentials: "include", headers: capturedHeaders})`
5. Returns binary data to content script → merges segments → writes final MP4 via Blob download
6. Multi-threaded by default (configurable thread count); "convert to single thread" reduces to 1 concurrent request
7. Auto-pauses after 30+ errors, user can reduce threads and resume
8. 30s timeout per segment via AbortController
9. Permissions: `webRequest`, `tabs`, `storage` + `host_permissions: ["<all_urls>"]`
10. Also has "record mode" fallback: hooks `MediaSource` + `SourceBuffer.appendBuffer()` to capture decoded video during playback (real-time speed)

**Key FetchV findings that informed our SW approach:**
- FetchV downloads from the **service worker**, NOT from MAIN-world — uses separate HTTP/2 socket pool
- FetchV passes captured headers via fetch()'s `headers` param (not declarativeNetRequest)
- FetchV uses `credentials: "include"` to send cookies
- The dedicated download tab (`fetchv.net/m3u8downloader`) means downloads happen in a completely separate tab context
- "Convert to single thread" = sequential download (like our approach), fixes CDN rate limiting

## Service Worker Download — Development Log (2026-03-27)

### Iteration 1: Basic SW fetch with captured headers
- Added `onSendHeaders` listener to capture CDN request headers
- Service worker fetched m3u8 and segments directly with `mode: "cors"`, `credentials: "include"`, replayed headers
- **Result: 403 on m3u8 fetch.** Only captured `User-Agent` and `Accept` — header filter matched `.ts`/`.m3u8` extensions but CDN URLs use `/file1/<token>` paths without extensions. Also, `Origin` is a forbidden header in fetch() — can't be set.
- Fell back to MAIN-world downloader automatically.

### Iteration 2: declarativeNetRequest header spoofing
- Added `declarativeNetRequest` permission to manifest.json
- `setCdnHeaderRules()` dynamically adds rules to rewrite Origin and Referer on CDN domain requests
- Fixed header capture filter: match `/file1/` and `/pl/` URL paths instead of file extensions
- **Result: 403 on all segments.** Manifest fetched successfully (881 segments parsed), but segments returned 403.
- Root cause: **manifest domain ≠ segment domain!** m3u8 on `bluehorizon4.site`, segments on `nebulacat8.site`. DNR rules only covered manifest domain.

### Iteration 3: Multi-domain DNR rules
- After parsing manifest, extract all unique CDN domains from segment URLs
- Update DNR rules to cover ALL domains (manifest + segments)
- Also fixed `chrome.tabs.get(tabId)` crash when `tabId = -1` (SW's own fetches trigger `onBeforeRequest`)
- **Result: 876/881 (99.4%) — same as MAIN-world approach.**
- 5 persistent failures at same indices (261, 343, 447, 545, 607) across all CDN domain rotations
- Errors are "Failed to fetch" (connection-level), not HTTP 403
- 2 iframe reloads occurred, same segments failed every time
- This confirms: the 5 persistent failures are NOT caused by HTTP/2 GOAWAY (SW has separate pool)

### Iteration 4: Remove throttle (NOT YET TESTED)
- Set `THROTTLE_MS = 0` for main pass (was 500ms) — FetchV proves CDN accepts rapid requests
- Disabled proactive reload at 800 segments (no GOAWAY in SW pool)
- Retry pass throttle reduced from 10s to 3s
- **Needs testing** — if same 5 segments still fail at full speed, the issue is not timing-related

### Remaining gap: 5 persistent segment failures
The same 5 segment indices fail with both MAIN-world and service worker approaches, across all CDN domains. FetchV downloads the same movie at 100% with zero errors. Possible remaining differences:
1. **FetchV replays ALL captured headers** directly in fetch() (not just Origin/Referer via DNR) — may include headers we're not sending
2. **FetchV downloads from a separate tab** (fetchv.net/m3u8downloader) — completely isolated context
3. **Speed** — FetchV at ~200ms/segment vs our 500ms may avoid token expiry or CDN pattern detection
4. **Threading** — FetchV defaults to multi-threaded (multiple concurrent requests)

### Next steps to try
1. **Test 0ms throttle** — see if speed alone fixes the 5 failures
2. **If still failing**: try passing ALL captured headers (not just Origin/Referer) directly in fetch() alongside DNR
3. **If still failing**: try multi-threaded download (2-4 concurrent fetches) like FetchV's default mode
4. **If still failing**: try offscreen document approach (Chrome MV3 offscreen API provides yet another network context)
5. **Nuclear option**: MSE record mode — hook MediaSource.appendBuffer() to capture decoded video during normal playback (real-time speed, guaranteed 100%)
