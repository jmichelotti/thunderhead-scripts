# BrocoFlix Browser-Side Download — Progress Log

## Status: IN PROGRESS — CDN rate limiting is the remaining blocker

## What Works
- Full relay chain: MAIN-world iframe → postMessage → content script → background → server
- Server receives and writes chunks to disk (confirmed with server-side chunk logging)
- Session lifecycle: `/brocoflix-start` → `/brocoflix-chunk` → `/brocoflix-done` (mux) or `/brocoflix-abort`
- Popup shows "uploading" status with chunk progress
- Client-side dedup (`brocoflixActiveUrls` Set) prevents duplicate `startBrocoflixDownload` calls
- Server-side dedup (`seen_urls`) catches any that slip through
- OMDb lookup, movie vs TV routing, subtitle matching all work

## The Problem: CDN Rate Limiting
The CDN (rotating domains: `silvercloud9.pro`, `mistwolf88.xyz`, `bluehorizon4.site`, `tmstr3.neonhorizonworkshops.com`, etc.) consistently kills fetch requests after ~100-350 segments. The error is always a generic "Failed to fetch" (network-level, not HTTP status).

### Test Results (Rocky movie, ~1170-1434 segments depending on server)
| Date | Segments reached | Retry config | Throttle | Result |
|------|-----------------|-------------|---------|--------|
| 2026-03-15 | 101/1170 | No retries | None | Failed to fetch |
| 2026-03-16 | 342/1217 | 3 retries, 2s/6s backoff | 100ms every 10th segment | Failed after 3 attempts |
| 2026-03-17 (attempt 1) | 127/1170 | 5 retries, 2-16s backoff | 200ms every segment | Failed after 5 attempts |
| 2026-03-17 (attempt 2) | 342/1217 | 5 retries, 2-16s backoff, manifest refresh on 3rd | 200ms, bumps to 1s after failure | Failed after 5 attempts |
| 2026-03-17 (attempt 3) | ~100/1434 | Unlimited retries, 2-60s backoff | 200ms, bumps to 1s after failure | Stuck in retry loop (never recovered) |
| 2026-03-18 | Not yet tested | Unlimited retries, 3-120s backoff | 200ms, bumps to 1.5s after failure | Pending — also kills video player + relays logs |

### Key Observations
1. The CDN allows ~100 fast sequential requests before throttling
2. Once rate-limited, even 60s backoff wasn't enough to recover in the 2026-03-17 test
3. The video player in the iframe was ALSO fetching segments concurrently, doubling CDN load — latest code kills the player before downloading
4. "Failed to fetch" is a network-level error (connection reset/timeout), not an HTTP error code
5. Different CDN domains are used each time (page reload gets a new domain), but behavior is consistent

## Current Implementation (as of 2026-03-18)

### Retry Logic (`brocoflixDownloaderFunc` in background.js)
- Unlimited retries per segment (no cap)
- Exponential backoff: 3s, 6s, 12s, 24s, 48s, 96s, capped at 120s
- Every 3rd retry: refreshes the m3u8 manifest for fresh segment URLs (up to 10 refreshes)
- 30s fetch timeout via AbortController
- Adaptive throttle: starts at 200ms/segment, permanently increases to 1500ms/segment after first failure
- Kills video player (`<video>` elements + JWPlayer/HLS.js) before starting download to free CDN bandwidth

### Log Relay
MAIN-world logs are invisible (BrocoFlix blocks F12 devtools). All `[BF-dl]` logs now relay through:
`relayLog()` → `postMessage(hlsBrocoLog)` → content script → `chrome.runtime.sendMessage(brocoflixLog)` → background `console.log`

So retry/progress logs appear in the **service worker console**.

### Server Chunk Logging (`hls_download_server.py`)
`brocoflix_chunk()` now prints progress every 50 chunks + first + last chunk:
```
[MOVIE] BrocoFlix chunk 50/1434 (76.5MB received)
```

## What to Try Next
1. **Test the latest code** — video player kill + 1.5s throttle + 120s max backoff + log relay. This is the most likely to succeed since the player was eating half the rate limit budget.
2. **If still stuck after killing player**: the CDN may be enforcing a per-token or per-IP request limit that resets on a longer timescale (5-10 minutes). Try:
   - Increasing throttle to 3-5s/segment (would take ~1-2 hours for a movie but should stay under any rate limit)
   - After a failure, do a single long cooldown (5 minutes) then resume at the slower rate
3. **Alternative approach — piggyback on the player**: Instead of downloading segments ourselves, let the video play at normal speed and intercept each TS segment via `webRequest.onBeforeRequest` as the player requests them. This is slow (real-time playback speed) but guaranteed to work since it's exactly how normal viewing works. Could be combined with `video.playbackRate = 16` to speed it up.
4. **Alternative approach — parallel chunk downloads**: Instead of sequential fetches, download 2-3 segments in parallel. This increases throughput but may also increase rate limit risk. Worth testing if the rate limit is per-minute rather than per-burst.

## Duplicate m3u8 Interception (Minor Issue)
Each page load fires 2-4 webRequest events for the same m3u8 URL. `seenM3u8` Set should catch duplicates but doesn't always (possibly URL encoding differences or timing with `chrome.tabs.get` async callback). Server-side dedup handles it cleanly — the noise is cosmetic only.
