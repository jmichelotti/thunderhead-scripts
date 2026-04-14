# Jellyfin Audit Script — Notes & Background

## What This Script Does

`audit_jellyfin.py` is a 3-tier health check for the Jellyfin media library across drives D, F, and L. It is read-only and never modifies media files.

- **Tier 1** — ffprobe structural checks: readable, has video/audio streams, sane duration, bad encoder tags, correct container format
- **Tier 2** — naming/layout validation: `Show (Year)/Season NN/SxxExx` and `Title (Year)/Title (Year).ext` conventions, orphan subtitles, leftover `(migrated)` files
- **Tier 3** (`--deep`) — full ffmpeg decode sweep: reads every frame to detect corruption that headers alone cannot reveal

Run nightly at 2am via Windows Task Scheduler (`run_audit.bat`). Reports land in `scripts/audit_reports/` as CSV + summary.

---

## Changes Made (April 2026 Session)

### Incremental Cache Saving
Previously the deep-decode cache (`.deep_cache.json`) was only written at the end of a completed run. If the script was killed or crashed, all tier 3 progress was lost and the next run started from zero.

**Fixed:** Cache now saves after every single file completes tier 3. A crash loses at most one file's worth of decode time.

### Better Logging
- Total file count printed at startup (`Total video files: N`)
- Progress line now includes total, percentage, and elapsed time: `[progress] 150 / 4849 (3.1%) — 12 issues — elapsed 01:23:45`
- Progress fires every 50 files (was 100)
- Per-file tier 3 logging: `[tier3] Decoding: filename.mp4 (1234 MB)` — so the log always shows what ffmpeg is currently working on

### CPU Limiting
- `run_audit.bat` now launches with `start /B /BELOWNORMAL /WAIT` for below-normal process priority
- ffmpeg decode command uses `-threads 1` (down from all cores) to reduce concurrent CPU pressure

### HLS Download Server
- Added `creationflags=subprocess.CREATE_NO_WINDOW` to all subprocess calls so yt-dlp and ffmpeg child processes don't steal window focus during downloads

---

## The Cache

The deep cache is stored at `scripts/audit_reports/.deep_cache.json`. Each entry is keyed on the full file path and stores `size`, `mtime`, `last_checked`, `result` (`ok` or `error`), and `detail`.

On each run, if a file's `(size, mtime)` matches the cache, the tier 3 decode is skipped entirely. This means:

- **First run**: Every file must be decoded. With 4,849 files across 4 TB, this takes days.
- **Subsequent runs**: Only new or changed files are decoded. A stable library will finish in minutes.

The cache persists indefinitely. It is never wiped unless `--clear-cache` is passed or the file is manually deleted.

---

## The Fan / Thermal Problem

The nightly audit was pegging CPU at 95–100%, causing the machine's fan to spin loudly for the entire duration of the run. Various thread-count configurations were tried:

- `-threads 4` (default): ~95-100% CPU, one or more cores fully hot, fan loud
- `-threads 2`: ~35-60% CPU, still loud fan
- `-threads 1`: ~25% average CPU (Task Manager), fan still loud

The key insight: **Task Manager's CPU% is an average across all cores.** With `-threads 1`, ffmpeg pins a single core at 100%. That single hot core is enough to trigger sustained fan spin regardless of the system-wide average. Spreading heat evenly across cores would keep each core cooler and the fan quieter.

---

## What Needs to Be Built

The goal is to run the tier 3 decode in a way that **distributes CPU usage across all cores at a configurable percentage** (e.g., 25% total) so that no single core runs hot enough to sustain fan spin.

Key requirements:

- The target CPU percentage should be configurable (e.g., `--cpu-limit 25`)
- The solution must not violate the sequential nature of video decode (each file decoded sequentially, frame-by-frame)
- When one decode finishes, the next file should be picked up immediately with no gap
- Windows-native approach preferred — the Windows equivalent of Linux `cgroups` CPU quotas is **Job Objects with CPU rate control** (available since Windows 8, kernel-level enforcement, no suspend/resume hacks needed)
- The existing cache, logging, and issue-reporting logic should remain intact
- Thread-safe cache writes will be needed if parallel execution is introduced
- Consider whether to process one file at a time (simpler) or multiple files in parallel (same throughput, different architecture) — both are valid

---

## Findings From the First Deep Scan

- **The Sopranos** — Multiple episodes across seasons 1–4 have corrupted AAC audio streams. The error is consistent across the series (`Invalid data found when processing input`, `channel element not allocated`, `Prediction is not allowed in AAC-LC`), suggesting all episodes came from the same bad source rip. The video stream is intact. These files need to be re-sourced.
- No other shows flagged with decode errors up to 5% of the library scanned (~250 files).

---

## Library Scale

- **4,849 video files** across D, F, L drives
- **~4 TB** total video data
- TV roots: `D:\TV Shows`, `F:\TV Shows`, `L:\TV Shows`
- Movie roots: `D:\Movies`, `F:\Movies`, `L:\Movies`
- At `-threads 1` with below-normal priority: ~25-30 files/hour → ~7 days for first full pass
- With the fan problem unsolved, the script can only practically run overnight via Task Scheduler

---

## Task Scheduler

The nightly job was **disabled** during the first deep scan to prevent a second instance from starting while the first was still running. There is currently no guard in the script or bat file to prevent duplicate instances.

The job should be **re-enabled** once either:
1. The first deep scan completes (cache is fully built), or
2. A duplicate-instance guard is added to `run_audit.bat`

A simple guard would be to check if `python.exe` is already running `audit_jellyfin.py` before launching.
