# HLS Capture Extension

Chrome/Vivaldi Manifest V3 browser extension that captures HLS (m3u8) video streams and sends them to a local Python download server (`hls_download_server.py` in the repo root) for saving to the Jellyfin media library.

## Architecture

### Files
- **background.js** — Service worker. Intercepts m3u8/subtitle network requests via `chrome.webRequest`, manages pending/confirmed capture state, orchestrates auto-capture episode advancement.
- **content.js** — Injected into every page. Shows in-page confirmation dialogs (top-right cards), runs auto-capture loops (DOM manipulation, play triggering, episode navigation), provides DOM inspector, sets up site-specific click listeners.
- **popup.js** — Extension popup UI. Shows server status, pending captures, download progress, auto-capture controls, DOM inspector button.
- **popup.html** — Popup markup and styles. Dark theme (#1a1a2e), 420px wide.
- **manifest.json** — MV3 manifest. Permissions: webRequest, activeTab, storage, scripting. Content script runs at document_idle on all URLs.

### Data Flow (Manual Capture)
1. `background.js` `webRequest.onBeforeRequest` intercepts `.m3u8` URLs
2. Fetches preview from server (`POST /preview`) with show/season/episode context
3. If 720p+, shows in-page dialog via content script; otherwise popup-only
4. User confirms → `POST /capture` → server downloads via yt-dlp
5. Subtitle `.vtt`/`.srt` URLs are intercepted and sent to `POST /subtitle`

### Data Flow (Auto-Capture)
Two navigation strategies based on site:
- **hash-reload** (1movies): Changes `location.hash` + `location.reload()` per episode. Content script re-runs `checkAutoCaptureOnLoad()` after each reload.
- **click-card** (brocoflix): Clicks `.episode-card` elements in-place (SPA). Uses `triggerIframeAutoPlay()` to start playback by manipulating iframe src.

Episode done-signal flow: `autoConfirmCapture()` in background.js sends `autoCaptureEpisodeDone` to content script after 2s subtitle grace period. Content script resolves `waitForEpisodeDone()` promise, then sends `autoCaptureAdvance` to background for next episode.

## Site Configs

Defined in `SITE_CONFIGS` (background.js). Keyed by partial hostname match.

| Key | Strategy | Notes |
|---|---|---|
| `"1movies"` | hash-reload | Partial match catches all TLDs (.bz, .to, etc). Episode links are `#ep=season,episode`. |
| `"brocoflix.xyz"` | click-card | SPA navigation. Episodes via `.episode-card` DOM elements with `data-season`/`data-episode`. |

`getSiteConfig(url)` matches by checking if the URL hostname `.includes()` the key.

## Server Endpoints (localhost:9876)

| Endpoint | Method | Purpose |
|---|---|---|
| `/capture` | POST | Start yt-dlp download of m3u8 URL |
| `/preview` | POST | Parse show/episode info, return filename/quality preview |
| `/subtitle` | POST | Download and save subtitle file |
| `/status` | GET | Server health + dry_run mode |
| `/downloads` | GET | Active/completed download progress |
| `/season-info` | POST | Log discovered episode list for a season |

## Key State (background.js)

- `pendingCaptures[]` — m3u8 URLs awaiting user confirmation (max 20)
- `captures[]` — Confirmed/active downloads (max 50)
- `seenM3u8` Set — Dedup: prevents same m3u8 from being captured twice
- `subtitlesSent` Set — Dedup for subtitle URLs
- `episodeContextByTab` Map — Per-tab show/season/episode from content script (for DOM-based sites)
- `autoCapture` object — Full auto-capture state: active flag, season/episode tracking, epoch counter, multi-season fields, discovered episode hashes

### Epoch System
`autoCapture.epoch` increments on each episode advance. Prevents stale done-signals from a previous episode's m3u8 (ad pre-rolls, quality variants) from resolving the next episode's `waitForEpisodeDone`. `episodeDoneSent` flag ensures only one done-signal per epoch.

## Auto-Capture Details

### Episode Discovery (hash-reload sites)
`discoverEpisodeHashes(season)` in content.js scans `a[href*="#ep="]` links. Polls until count stabilizes (handles JS-rendered links). Reports to background via `autoCaptureEpisodesDiscovered`. Handles combined episodes like `#ep=10,8-9`.

### Skip Logic (multi-season, unknown ep count)
Two consecutive skipped episodes = season done. Single skip = try next episode (transient failure tolerance).

### Grace Period
After last episode, `graceUntil = Date.now() + 15000` allows late m3u8 requests to still be auto-confirmed.

## Episode Context

For **hash-reload** sites (1movies): season/episode parsed from URL hash `#ep=season,episode` by the server.

For **click-card** sites (brocoflix): content script sends `setEpisodeContext` message on card/play clicks. Background stores in `episodeContextByTab` map. Server reads `show_name`, `season`, `episode` from POST body.

## BrocoFlix Browser-Side Download

BrocoFlix uses embed provider `streameeeeee.site` which loads video from CDN domains that **403 all non-browser clients** (yt-dlp, curl, service worker fetch). CDN hostnames rotate constantly (silvercloud9.pro, stormfox27.live, mistwolf88.xyz, etc.) — never match on CDN domain.

### How it works
1. `webRequest.onBeforeRequest` intercepts m3u8 URL from BrocoFlix page (detected via `getSiteConfig`)
2. Background POSTs `/brocoflix-start` with episode context → gets `session_id`
3. `chrome.scripting.executeScript({ world: "MAIN" })` injects `brocoflixDownloaderFunc` into the embed **iframe** (using `details.frameId` from webRequest)
4. Injected function runs in iframe's origin (`streameeeeee.site`) — CDN accepts requests from this origin
5. Downloads each TS segment, converts to base64, sends via `window.postMessage`
6. Content script (ISOLATED world in same iframe, via `<all_urls>` manifest match) receives postMessage
7. Relays base64 via `chrome.runtime.sendMessage` → background service worker
8. Background decodes base64, POSTs raw binary to `/brocoflix-chunk`
9. On completion: background POSTs `/brocoflix-done` → server runs `ffmpeg -i temp.ts -c copy -movflags +faststart output.mp4`

### Why the relay chain?
- MAIN-world can't POST to localhost (mixed content: HTTPS page → HTTP server, plus CSP)
- MV3 content scripts share the page's network context, can't fetch arbitrary URLs
- Background service worker can fetch localhost freely

### Server endpoints
| Endpoint | Method | Body | Purpose |
|---|---|---|---|
| `/brocoflix-start` | POST | JSON (episode info) | Create session, return session_id |
| `/brocoflix-chunk` | POST | Binary (raw TS data), headers: X-Session-Id, X-Chunk-Index, X-Total-Chunks | Append segment to temp file |
| `/brocoflix-done` | POST | JSON (session_id) | Mux TS→MP4 with ffmpeg, move to output |
| `/brocoflix-abort` | POST | JSON (session_id) | Clean up temp file |

### Key state
- `brocoflixSessions` Map in background.js: tracks active sessions
- `_brocoflix_sessions` dict in server: tracks temp files, chunk counts, progress

### Known issues (2026-03-15)
1. **Segment fetch fails mid-download**: `brocoflixDownloaderFunc` throws "Failed to fetch" around chunk ~100/1170 (tested with Rocky movie). Needs retry logic with exponential backoff. Possible causes: CDN rate limiting, token expiry, or network timeout on long downloads.
2. **Server may not be receiving chunks**: Server logged "session started" but no chunk receipt logs. Verify CORS preflight for `/brocoflix-chunk` works (custom headers `X-Session-Id`, `X-Chunk-Index`, `X-Total-Chunks` must be in `Access-Control-Allow-Headers`). Check if temp .ts file is being written to disk.
3. **Duplicate m3u8 interception**: Each URL intercepted 2-3x due to CDN retries. Server-side dedup (`seen_urls`) handles this, but first interception starts `startBrocoflixDownload` async, allowing second to also enter before first completes. Consider adding client-side dedup (e.g., `brocoflixActiveUrls` Set).

### BrocoFlix auto-capture timing
- Done-signal fires AFTER full download complete (not 2s after start like 1movies)
- Content script timeout for click-card strategy should be extended (90+ minutes for BrocoFlix)
- Background `brocoflixDoneSignal` handler fires `autoCaptureEpisodeDone` when download finishes

## Development Notes

- No build step — plain JS, load unpacked in chrome://extensions
- All server communication is to localhost:9876 (no auth)
- Test with server in dry-run mode to avoid actual downloads
- DOM inspector (popup "Inspect Page DOM" button) dumps page structure for debugging selectors
- Console logs prefixed with `[AC]` for auto-capture, `[BF]` for BrocoFlix download
- F12 dev tools cannot be opened on BrocoFlix (site detects and glitches out). All debugging must go through service worker console or server terminal logs.
- Content script diagnostic (3s after load) logs BrocoFlix DOM state to service worker console via `brocoflixDiag` message
