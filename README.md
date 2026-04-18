# Thunderhead

Personal tooling for managing and maintaining my **ThunderheadFlix** Jellyfin media server.

## Scripts (`scripts/`)

- **`master_jf_operations.py`** — Full pipeline: fix metadata, rename files, migrate (with dry-run approval)
- **`fix_metadata_for_jellyfin.py`** — Fix problematic encoder tags via ffmpeg remux/re-encode
- **`fix_file_names.py`** — Runner for TV + movie renaming scripts
- **`fix_tv_names.py`** / **`fix_movie_names.py`** — Rename media files using OMDb lookups
- **`migrate_files.py`** — Move processed media from staging to library drives
- **`download_youtube_jellyfin.py`** — Download YouTube videos via yt-dlp
- **`shift_subtitles.py`** — Shift .srt subtitle timestamps forward or backward
- **`audit_jellyfin.py`** — 3-tier library audit: structural (ffprobe), naming/layout, and deep decode sweep (`--deep`). Runs nightly at 2am via Task Scheduler.

## Browser Extension (`browser-extension/`)

Two-part system for capturing and downloading HLS streams:

- **`hls-capture/`** — Chrome/Vivaldi Manifest V3 extension that intercepts m3u8 URLs with auto-capture support
- **`hls-server/`** — Local Python server (port 9876) that downloads captured streams with Jellyfin-friendly naming

## Analytics (`analytics/`)

FastAPI service (port 1201) exposing Jellyfin server stats via REST API:

- **`/status`** — Server health, active streams, library counts, storage, users (all-in-one)
- **`/sessions`** — Who's watching what, playback progress, device, transcode details
- **`/library`** — Movie/series/episode counts, per-drive storage usage
- **`/playback/wrapped`** — Per-user viewing summary (top shows, movies, total time)
- **`/playback/currently-watching`** — Shows each user is actively watching with last episode
- **`/playback/most-watched`** — Most watched shows and movies across all users
- **`/playback/breakdowns`** — Usage breakdown by user, device, client, playback method
- **`/playback/hourly`** — Viewing heatmap by day of week and hour
- **`/playback/history/{username}`** — Per-user event-level playback log
- **`/playback/activity`** — Daily play counts and watch time per user

- **`/episodes/gaps`** — Detect missing episodes for currently-airing shows users are watching (compares Jellyfin library against TVmaze air dates)

Requires the **Playback Reporting** plugin on Jellyfin. Run with `cd analytics && python app.py`.

## Wrapped Frontend (`wrapped/`)

Static HTML/CSS/JS dashboard served by the analytics service at `/wrapped/`. Three views:

- **Wrapped** — Per-user stats cards, viewing heatmap, now-playing banner
- **Watching** — What shows each user is currently watching with episode details
- **Library** — Media counts, storage bars per drive, user table

This repo is primarily designed for my local media structure and workflows.
