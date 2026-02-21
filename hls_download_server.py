#!/usr/bin/env python
"""
Local HTTP server that receives m3u8 URLs from the HLS Capture browser extension,
resolves show metadata via OMDb, and downloads episodes using yt-dlp with
Jellyfin-friendly naming.

Usage:
    python hls_download_server.py              # dry-run (logs only, no download)
    python hls_download_server.py --apply      # actually download files
    python hls_download_server.py --port 9999  # custom port

Workflow:
    1. Start this server
    2. Load the hls-capture-extension in Vivaldi
    3. Browse to a streaming site, navigate to an episode, press play
    4. Extension captures the m3u8 URL and POSTs it here
    5. Server downloads + names the file into C:\\Temp_Media\\TV Shows\\
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from pathlib import Path
from typing import Optional, Dict
from urllib.parse import urlparse

import requests


# ========= CONFIG =========

OUTPUT_DIR = Path(r"C:\Temp_Media\TV Shows")
TEMP_DIR = Path(r"C:\Temp_Media\_hls_tmp")
OMDB_API_KEY = "591dfd18"
DEFAULT_PORT = 9876
MAX_HEIGHT = 1080

# ========= HELPERS =========

def sanitize_for_windows(name: str) -> str:
    invalid_chars = r'<>:"/\\|?*'
    name = name.translate(str.maketrans({ch: " " for ch in invalid_chars}))
    name = re.sub(r"\s+", " ", name).strip()
    return name.rstrip(" .")


def try_omdb(params: dict) -> Optional[Dict[str, str]]:
    try:
        r = requests.get("https://www.omdbapi.com/", params=params, timeout=10)
        data = r.json()
    except Exception:
        return None
    if data.get("Response") != "True":
        return None
    year = "".join(c for c in data.get("Year", "") if c.isdigit())[:4]
    if len(year) != 4:
        return None
    return {"title": data.get("Title"), "year": year}


def lookup_show(title_guess: str) -> Optional[Dict[str, str]]:
    """Try OMDb exact title match, then search."""
    if not OMDB_API_KEY or not title_guess:
        return None

    # Exact title match
    meta = try_omdb({"apikey": OMDB_API_KEY, "t": title_guess, "type": "series"})
    if meta:
        return meta

    # Search fallback
    meta = try_omdb({"apikey": OMDB_API_KEY, "s": title_guess, "type": "series"})
    if meta:
        return meta

    return None


# ========= URL PARSING =========

def parse_show_from_url(page_url: str) -> dict:
    """
    Parse show name, season, and episode from a streaming site page URL.

    Expected URL patterns:
        https://1movies.bz/tv-the-pitt-4vevg#ep=1,5
        https://1movies.bz/tv-show-name-xxxxx#ep=<season>,<episode>

    Returns: {show_slug, show_name, season, episode} or partial dict.
    """
    result = {"show_slug": "", "show_name": "", "season": None, "episode": None}

    parsed = urlparse(page_url)
    path = parsed.path.strip("/")

    # Extract show slug from path: "watch/tv-the-pitt-4vevg" or "tv-the-pitt-4vevg"
    slug_match = re.search(r"tv-(.+?)(?:-[a-z0-9]{4,6})?$", path, re.IGNORECASE)
    if slug_match:
        slug = slug_match.group(1)
        result["show_slug"] = slug
        # Convert slug to title case: "the-pitt" -> "The Pitt"
        result["show_name"] = slug.replace("-", " ").title()

    # Extract season + episode from fragment: "#ep=2,5" -> season=2, episode=5
    fragment = parsed.fragment
    ep_match = re.search(r"ep=(\d+),(\d+)", fragment)
    if ep_match:
        result["season"] = int(ep_match.group(1))
        result["episode"] = int(ep_match.group(2))

    return result


# ========= DOWNLOAD PROGRESS TRACKING =========

# Shared download state: {ep_key: {filename, status, percent, speed, eta, frag, total_frags, quality, size}}
_downloads: Dict[str, dict] = {}
_downloads_lock = threading.Lock()

# yt-dlp progress line pattern:
# [download]  42.3% of ~ 500.00MiB at  5.23MiB/s ETA 01:23 (frag 381/900)
_PROGRESS_RE = re.compile(
    r"\[download\]\s+"
    r"(?P<pct>[\d.]+)%\s+"
    r"of\s+~?\s*(?P<size>\S+)\s+"
    r"at\s+(?P<speed>\S+)\s+"
    r"ETA\s+(?P<eta>\S+)\s+"
    r"\(frag\s+(?P<frag>\d+)/(?P<total>\d+)\)"
)
# [info] ...: Downloading 1 format(s): 4500
_QUALITY_RE = re.compile(r"Downloading \d+ format\(s\):\s*(?P<quality>\S+)")
# [download] 100% ...
_DONE_RE = re.compile(r"\[download\]\s+100%")
# Total fragments
_FRAGS_RE = re.compile(r"Total fragments:\s*(?P<total>\d+)")


def update_download(ep_key: str, **kwargs):
    with _downloads_lock:
        if ep_key in _downloads:
            _downloads[ep_key].update(kwargs)


def download_m3u8(m3u8_url: str, output_path: Path, dry_run: bool, ep_key: str) -> str:
    """Download an m3u8 stream using yt-dlp. Returns status message."""
    if dry_run:
        update_download(ep_key, status="dry_run")
        return f"[DRY RUN] Would download to {output_path}"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    # Download to short temp path to avoid Windows long-path issues with fragments
    temp_file = TEMP_DIR / output_path.name

    cmd = [
        "yt-dlp",
        "-f", f"bestvideo[ext=mp4][height<={MAX_HEIGHT}]/"
              f"bv*[height<={MAX_HEIGHT}]+ba/best",
        "--merge-output-format", "mp4",
        "--postprocessor-args", "ffmpeg:-movflags +faststart",
        "--no-write-subs",
        "--newline",  # Force one progress line per update (no \r overwrites)
        "-o", str(temp_file),
        m3u8_url,
    ]

    print(f"  Running: yt-dlp -> {temp_file}", flush=True)
    update_download(ep_key, status="downloading")

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1
    )

    for line in proc.stdout:
        line = line.rstrip()

        # Parse quality format
        m = _QUALITY_RE.search(line)
        if m:
            update_download(ep_key, quality=m.group("quality"))
            print(f"  Quality: {m.group('quality')}", flush=True)

        # Parse total fragments
        m = _FRAGS_RE.search(line)
        if m:
            update_download(ep_key, total_frags=int(m.group("total")))

        # Parse progress
        m = _PROGRESS_RE.search(line)
        if m:
            update_download(
                ep_key,
                percent=float(m.group("pct")),
                size=m.group("size"),
                speed=m.group("speed"),
                eta=m.group("eta"),
                frag=int(m.group("frag")),
                total_frags=int(m.group("total")),
            )
            # Print a summary line every 50 fragments
            frag = int(m.group("frag"))
            total = int(m.group("total"))
            if frag % 50 == 0 or frag == total:
                print(f"  [{m.group('pct')}%] frag {frag}/{total}  "
                      f"{m.group('speed')}  ETA {m.group('eta')}  ~{m.group('size')}", flush=True)

        # Check for 100% done
        if _DONE_RE.search(line):
            update_download(ep_key, percent=100.0)

    proc.wait()

    if proc.returncode != 0:
        update_download(ep_key, status="error")
        return f"yt-dlp failed (exit {proc.returncode})"

    # Move from temp to final location (video + any subtitle files)
    update_download(ep_key, status="moving")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(temp_file), str(output_path))
    print(f"  Moved to: {output_path}", flush=True)

    # Move any .srt files that match the video name (skip thumbnail sprites)
    for sub_file in TEMP_DIR.glob(f"{temp_file.stem}*.srt"):
        content = sub_file.read_text(encoding="utf-8", errors="ignore")
        if "xywh=" in content or "thumbnails" in content.lower():
            sub_file.unlink()
            print(f"  Deleted thumbnail sprite: {sub_file.name}", flush=True)
            continue
        sub_dest = output_path.parent / sub_file.name
        shutil.move(str(sub_file), str(sub_dest))
        print(f"  Moved sub: {sub_file.name}", flush=True)

    update_download(ep_key, status="done", percent=100.0)
    return "downloaded"


# ========= SUBTITLE HANDLING =========

# Pending subtitles keyed by ep_key, waiting for video download to determine final path
# {ep_key: [subtitle_url, ...]}
_pending_subs: Dict[str, list] = {}
_pending_subs_lock = threading.Lock()

# Resolved output dirs keyed by ep_key: {ep_key: (show_title, season, episode)}
_resolved_episodes: Dict[str, tuple] = {}
_resolved_lock = threading.Lock()

# Episodes that already have a saved subtitle — prevents race where multiple
# threads pass the file-exists check before any have written the file
_saved_subs: set = set()
_saved_subs_lock = threading.Lock()


def vtt_to_srt(vtt_text: str) -> str:
    """Convert WebVTT content to SRT format."""
    # Strip VTT header
    lines = vtt_text.strip().splitlines()
    output = []
    cue_num = 0
    i = 0

    # Skip header lines (WEBVTT and any metadata before first blank line)
    while i < len(lines) and lines[i].strip():
        i += 1
    while i < len(lines) and not lines[i].strip():
        i += 1

    while i < len(lines):
        # Skip blank lines
        if not lines[i].strip():
            i += 1
            continue

        # Look for timestamp line (contains " --> ")
        # Skip any cue ID line that might precede it
        timestamp_line = None
        if " --> " in lines[i]:
            timestamp_line = lines[i]
        elif i + 1 < len(lines) and " --> " in lines[i + 1]:
            i += 1  # skip cue ID
            timestamp_line = lines[i]
        else:
            i += 1
            continue

        # Convert VTT timestamps (may have . instead of ,) to SRT format
        timestamp_line = timestamp_line.replace(".", ",")
        # Remove any positioning info after timestamps
        timestamp_line = re.sub(r"([\d:,]+\s*-->\s*[\d:,]+).*", r"\1", timestamp_line)
        # Ensure HH:MM:SS,mmm format (VTT may omit hours)
        parts = timestamp_line.split(" --> ")
        fixed_parts = []
        for part in parts:
            part = part.strip()
            if part.count(":") == 1:
                part = "00:" + part
            fixed_parts.append(part)
        timestamp_line = " --> ".join(fixed_parts)

        cue_num += 1
        i += 1

        # Collect text lines until blank line or end
        text_lines = []
        while i < len(lines) and lines[i].strip():
            text_lines.append(lines[i])
            i += 1

        if text_lines:
            output.append(f"{cue_num}")
            output.append(timestamp_line)
            output.extend(text_lines)
            output.append("")

    return "\n".join(output)


def is_english_subtitle(text: str) -> bool:
    """Heuristic: check if subtitle text is predominantly English."""
    # Extract just the dialogue lines (skip timestamps, cue numbers, headers)
    dialogue = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("WEBVTT") or line.startswith("NOTE"):
            continue
        if re.match(r"^\d+$", line):  # cue number
            continue
        if "-->" in line:  # timestamp
            continue
        dialogue.append(line)

    if not dialogue:
        return False

    # Sample first 50 dialogue lines for better accuracy
    sample = " ".join(dialogue[:50])
    if not sample:
        return False

    # Reject thumbnail sprite maps (VTT files with xywh= coordinates, not dialogue)
    if "xywh=" in sample or "thumbnails" in sample.lower():
        return False

    # Check for UTF-8 mojibake (Latin text decoded as cp1252/latin1 then re-encoded)
    # e.g. "Ã©" (é), "Ã¨" (è), "Ã´" (ô), "Ã§" (ç) — common in French/Spanish/etc.
    mojibake_count = len(re.findall(r"Ã[\x80-\xbf]", sample))
    if mojibake_count > 2:
        return False

    # Check for CP1250/Latin-1 encoding corruption: characters in U+0080–U+00BF
    # (like ³ ¹ ¿ ¯ ± ¶) are Latin-1 supplement symbols that never appear in clean
    # dialogue text. When Polish/Czech CP1250 is decoded as Latin-1, accented letters
    # become these symbols (e.g. ł→³, ż→¿, ą→¹). More than 2 = encoding corruption.
    latin1_symbols = sum(1 for c in sample if 0x80 <= ord(c) <= 0xBF)
    if latin1_symbols > 2:
        return False

    # Check for actual Unicode accented Latin characters (common in non-English European)
    # Only count real accented letters (À-ö, ø-ÿ, etc.), not stray bytes from
    # mis-decoded symbols like ♪ (U+266A) whose UTF-8 bytes E2/99/AA become â/\x99/ª
    # when decoded as latin-1.
    # Focus on the most common accented ranges used in European languages:
    ACCENTED_RANGES = (
        (0x00C0, 0x00D6),  # À-Ö
        (0x00D8, 0x00F6),  # Ø-ö
        (0x00F8, 0x00FF),  # ø-ÿ
        (0x0100, 0x017F),  # Latin Extended-A
        (0x0180, 0x024F),  # Latin Extended-B
    )
    accented = sum(
        1 for c in sample
        if any(lo <= ord(c) <= hi for lo, hi in ACCENTED_RANGES)
    )
    alpha = sum(1 for c in sample if c.isalpha())
    if alpha > 0 and accented / alpha > 0.05:
        return False

    # Detect non-English by common foreign words/patterns
    sample_lower = sample.lower()
    foreign_markers = [
        # French
        r"\bje\b", r"\bqu['e]", r"\bc'est\b", r"\bune?\b", r"\bpour\b",
        r"\bpas\b", r"\bvous\b", r"\bles\b", r"\bdes\b", r"\bdans\b",
        # Spanish
        r"\bel\b", r"\blos\b", r"\bpor\b", r"\bque\b", r"\buna\b",
        r"\bestá\b", r"\bcomo\b",
        # German
        r"\bich\b", r"\bein\b", r"\bdas\b", r"\bist\b", r"\bnicht\b",
        r"\baber\b",
        # Portuguese
        r"\bnão\b", r"\bcom\b", r"\buma\b", r"\bpara\b", r"\bvocê\b",
    ]
    foreign_hits = sum(
        len(re.findall(pat, sample_lower)) for pat in foreign_markers
    )
    # English text might have occasional "the" matching "les" etc., but
    # a high density of foreign words is a clear signal
    if foreign_hits > 8:
        return False

    # Basic ASCII ratio check as final filter
    ascii_letters = sum(1 for c in sample if c.isascii() and c.isalpha())
    all_letters = sum(1 for c in sample if c.isalpha())
    if all_letters == 0:
        return False

    return ascii_letters / all_letters > 0.9


def download_subtitle(subtitle_url: str, ep_key: str):
    """Download a subtitle file and save it next to the video (English only)."""
    with _resolved_lock:
        episode_info = _resolved_episodes.get(ep_key)

    if not episode_info:
        # Video hasn't been processed yet, queue for later
        with _pending_subs_lock:
            _pending_subs.setdefault(ep_key, []).append(subtitle_url)
        print(f"  Subtitle queued (waiting for video): {subtitle_url[:80]}...", flush=True)
        return

    show_title, season, episode = episode_info
    ep_tag = f"S{season:02d}E{episode:02d}"
    srt_name = f"{show_title} {ep_tag}.srt"
    srt_path = OUTPUT_DIR / show_title / f"Season {season:02d}" / srt_name

    # Check if subtitle already saved (file on disk or claimed by another thread)
    with _saved_subs_lock:
        if ep_key in _saved_subs or srt_path.exists():
            print(f"  Subtitle skipped (already exists): {srt_path.name}", flush=True)
            return

    try:
        resp = requests.get(subtitle_url, timeout=15)
        resp.raise_for_status()
        # Force UTF-8 decoding — VTT files are almost always UTF-8, but
        # requests may guess latin-1 from headers, mangling multibyte chars
        # like ♪ (U+266A) into separate bytes (â/\x99/ª)
        resp.encoding = "utf-8"
        content = resp.text

        # Check if English before saving
        if not is_english_subtitle(content):
            print(f"  Subtitle rejected (not English): {subtitle_url[:80]}...", flush=True)
            return

        # Claim this episode under lock so only the first English subtitle wins
        with _saved_subs_lock:
            if ep_key in _saved_subs:
                print(f"  Subtitle skipped (already saved by another thread): {srt_path.name}", flush=True)
                return
            _saved_subs.add(ep_key)

        print(f"  Found English subtitle: {subtitle_url[:80]}...", flush=True)

        # Convert VTT to SRT if needed
        if subtitle_url.lower().endswith(".vtt") or content.strip().startswith("WEBVTT"):
            content = vtt_to_srt(content)

        srt_path.parent.mkdir(parents=True, exist_ok=True)
        srt_path.write_text(content, encoding="utf-8")
        print(f"  Saved subtitle: {srt_path}", flush=True)
    except Exception as e:
        print(f"  Subtitle download failed: {e}", flush=True)


def process_pending_subs(ep_key: str):
    """Process any subtitles that arrived before the video was resolved."""
    with _pending_subs_lock:
        urls = _pending_subs.pop(ep_key, [])

    for url in urls:
        download_subtitle(url, ep_key)


# ========= HTTP SERVER =========

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def probe_formats(m3u8_url: str) -> list:
    """Run yt-dlp -F to list available formats without downloading."""
    cmd = ["yt-dlp", "-F", "--no-download", m3u8_url]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30
        )
        formats = []
        for line in result.stdout.splitlines():
            # Parse format lines like: "4500 mp4 1920x1080 ..."
            m = re.match(
                r"^(\S+)\s+(mp4|webm|mhtml|\w+)\s+(\d+x\d+|\w+)?\s*(.*)",
                line.strip()
            )
            if m and "x" in (m.group(3) or ""):
                fmt_id = m.group(1)
                ext = m.group(2)
                resolution = m.group(3) or ""
                rest = m.group(4).strip()
                formats.append({
                    "format_id": fmt_id,
                    "ext": ext,
                    "resolution": resolution,
                    "detail": rest,
                })
        return formats
    except Exception as e:
        print(f"  Format probe failed: {e}", flush=True)
        return []


def get_best_format_label(formats: list) -> str:
    """Return a human-readable label for the best format that would be selected."""
    if not formats:
        return "unknown"
    # Find highest resolution
    best = None
    best_pixels = 0
    for f in formats:
        res = f.get("resolution", "")
        if "x" in res:
            try:
                w, h = res.split("x")
                pixels = int(w) * int(h)
                if pixels > best_pixels and int(h) <= MAX_HEIGHT:
                    best_pixels = pixels
                    best = f
            except ValueError:
                pass
    if best:
        return f"{best['resolution']} {best['ext']}"
    return formats[-1].get("resolution", "unknown")


class HLSHandler(BaseHTTPRequestHandler):
    dry_run = True
    seen_urls = set()
    _lock = threading.Lock()

    def log_message(self, format, *args):
        msg = format % args
        # Suppress noisy poll logs
        if "GET /status" in msg or "GET /downloads" in msg:
            return
        print(f"  [{self.client_address[0]}] {msg}", flush=True)

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path == "/status":
            self._send_json({"status": "ok", "dry_run": self.dry_run})
        elif self.path == "/downloads":
            with _downloads_lock:
                self._send_json({"downloads": list(_downloads.values())})
        else:
            self._send_json({"error": "not found"}, 404)

    def _handle_subtitle(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
        except Exception as e:
            self._send_json({"status": "error", "message": str(e)}, 400)
            return

        subtitle_url = body.get("subtitle_url", "")
        page_url = body.get("page_url", "")

        if not subtitle_url:
            self._send_json({"status": "error", "message": "missing subtitle_url"}, 400)
            return

        info = parse_show_from_url(page_url)
        if not info["show_slug"] or info["season"] is None or info["episode"] is None:
            self._send_json({"status": "skipped", "message": "cannot parse episode"})
            return

        ep_key = f"{info['show_slug']}|{info['season']}|{info['episode']}"
        print(f"  Subtitle URL captured: {subtitle_url[:80]}... [{ep_key}]", flush=True)

        self._send_json({"status": "ok"})

        # Download in background
        threading.Thread(
            target=download_subtitle, args=(subtitle_url, ep_key), daemon=True
        ).start()

    def _handle_preview(self):
        """Return show info + quality without starting a download."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
        except Exception as e:
            self._send_json({"status": "error", "message": str(e)}, 400)
            return

        m3u8_url = body.get("m3u8_url", "")
        page_url = body.get("page_url", "")

        if not m3u8_url:
            self._send_json({"status": "error", "message": "missing m3u8_url"}, 400)
            return

        # Parse show info
        info = parse_show_from_url(page_url)
        if not info["show_name"] or info["season"] is None or info["episode"] is None:
            self._send_json({
                "status": "error",
                "message": "Could not parse show/season/episode from page URL"
            })
            return

        # OMDb lookup
        meta = lookup_show(info["show_name"])
        if meta:
            show_title = sanitize_for_windows(f"{meta['title']} ({meta['year']})")
        else:
            show_title = sanitize_for_windows(info["show_name"])

        season = info["season"]
        episode = info["episode"]
        ep_tag = f"S{season:02d}E{episode:02d}"
        filename = f"{show_title} {ep_tag}.mp4"

        # Probe available formats
        formats = probe_formats(m3u8_url)
        best_quality = get_best_format_label(formats)

        print(f"  Preview: {filename} [{best_quality}]", flush=True)

        self._send_json({
            "status": "ok",
            "show_title": show_title,
            "season": season,
            "episode": episode,
            "ep_tag": ep_tag,
            "filename": filename,
            "quality": best_quality,
            "formats": formats,
        })

    def do_POST(self):
        if self.path == "/subtitle":
            self._handle_subtitle()
            return
        if self.path == "/preview":
            self._handle_preview()
            return
        if self.path != "/capture":
            self._send_json({"error": "not found"}, 404)
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
        except Exception as e:
            self._send_json({"status": "error", "message": f"bad request: {e}"}, 400)
            return

        m3u8_url = body.get("m3u8_url", "")
        page_url = body.get("page_url", "")

        if not m3u8_url:
            self._send_json({"status": "error", "message": "missing m3u8_url"}, 400)
            return

        print(f"\n{'='*60}", flush=True)
        print(f"  Captured m3u8: {m3u8_url[:100]}...", flush=True)
        print(f"  Page URL:      {page_url}", flush=True)

        # Parse show info from page URL
        info = parse_show_from_url(page_url)
        print(f"  Parsed:        {info}", flush=True)

        if not info["show_name"] or info["season"] is None or info["episode"] is None:
            msg = "Could not parse show/season/episode from page URL"
            print(f"  ERROR: {msg}", flush=True)
            self._send_json({"status": "error", "message": msg})
            return

        # Deduplicate by episode (not by m3u8 URL, since players request multiple variants)
        ep_key = f"{info['show_slug']}|{info['season']}|{info['episode']}"
        with HLSHandler._lock:
            if ep_key in HLSHandler.seen_urls:
                print(f"  Skipping duplicate episode: {ep_key}", flush=True)
                self._send_json({"status": "skipped", "message": "duplicate episode"})
                return
            HLSHandler.seen_urls.add(ep_key)

        # OMDb lookup
        meta = lookup_show(info["show_name"])
        if meta:
            show_title = sanitize_for_windows(f"{meta['title']} ({meta['year']})")
            print(f"  OMDb match:    {show_title}", flush=True)
        else:
            show_title = sanitize_for_windows(info["show_name"])
            print(f"  OMDb miss, using: {show_title}", flush=True)

        season = info["season"]
        episode = info["episode"]
        ep_tag = f"S{season:02d}E{episode:02d}"
        filename = f"{show_title} {ep_tag}.mp4"
        output_path = OUTPUT_DIR / show_title / f"Season {season:02d}" / filename

        print(f"  Output:        {output_path}", flush=True)

        # Register resolved episode so subtitles know where to save
        with _resolved_lock:
            _resolved_episodes[ep_key] = (show_title, season, episode)

        # Process any subtitles that arrived before this video capture
        threading.Thread(
            target=process_pending_subs, args=(ep_key,), daemon=True
        ).start()

        # Register download in progress tracker
        with _downloads_lock:
            _downloads[ep_key] = {
                "ep_key": ep_key,
                "filename": filename,
                "show": show_title,
                "ep_tag": ep_tag,
                "status": "queued",
                "percent": 0.0,
                "speed": "",
                "eta": "",
                "frag": 0,
                "total_frags": 0,
                "quality": "",
                "size": "",
                "started": time.time(),
            }

        # Respond immediately, download in background
        self._send_json({"status": "downloading", "message": filename})

        def do_download():
            result = download_m3u8(m3u8_url, output_path, self.dry_run, ep_key)
            print(f"  Result:        {result}", flush=True)

        thread = threading.Thread(target=do_download, daemon=True)
        thread.start()


# ========= MAIN =========

def main():
    parser = argparse.ArgumentParser(
        description="Local server for HLS capture extension"
    )
    parser.add_argument("--apply", action="store_true",
                        help="Actually download files (default: dry-run)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"Port to listen on (default: {DEFAULT_PORT})")
    args = parser.parse_args()

    HLSHandler.dry_run = not args.apply

    mode = "LIVE" if args.apply else "DRY RUN"
    print(f"HLS Download Server [{mode}]", flush=True)
    print(f"  Listening on http://localhost:{args.port}", flush=True)
    print(f"  Output dir:  {OUTPUT_DIR}", flush=True)
    print(f"  Ctrl+C to stop\n", flush=True)

    server = ThreadingHTTPServer(("127.0.0.1", args.port), HLSHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()


if __name__ == "__main__":
    main()
