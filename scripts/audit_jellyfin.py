#!/usr/bin/env python
"""
audit_jellyfin.py - Audit Jellyfin media libraries for corruption and layout issues.

Tiers:
  1 (fast, default)  - ffprobe structural checks (streams, duration, codecs, encoder tag, container)
  2 (fast, default)  - naming/layout checks against canonical Show (Year)/Season NN/ and Title (Year)/ conventions
  3 (slow, --deep)   - full ffmpeg decode sweep, cache-gated on (size, mtime)

Run:
  python audit_jellyfin.py                  # tier 1+2 across all drives
  python audit_jellyfin.py --deep           # + tier 3 decode sweep
  python audit_jellyfin.py --drive D        # limit to one or more drives
  python audit_jellyfin.py --limit 50       # cap files per root (testing)
  python audit_jellyfin.py --clear-cache    # wipe the deep-decode cache

Outputs land in scripts/audit_reports/:
  YYYY-MM-DD_HHMM.csv    per-run report
  latest.csv             copy of most recent report (stable path for scheduler)
  latest_summary.txt     human-readable counts
  .deep_cache.json       tier-3 cache (never commit)
"""

from pathlib import Path
from datetime import datetime
from collections import Counter
import argparse
import csv
import json
import re
import subprocess
import sys

# ============================================================
# CONFIG
# ============================================================

TV_ROOTS = [
    r"D:\TV Shows",
    r"F:\TV Shows",
    r"L:\TV Shows",
]

MOVIE_ROOTS = [
    r"D:\Movies",
    r"F:\Movies",
    r"L:\Movies",
]

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov"}
SUB_EXTS = {".srt", ".vtt", ".ass", ".ssa", ".sub", ".idx"}

# Show folders exempt from tier 2 layout checks (no year, intentional)
LAYOUT_WHITELIST = {"P90X"}

ENCODER_BAD_PATTERNS = ["hls.js", "dailymotion"]

# Jellyfin-friendly codecs (flagged as warn if not in set)
OK_VIDEO_CODECS = {"h264", "hevc", "av1", "vp9", "vp8", "mpeg4", "mpeg2video"}
OK_AUDIO_CODECS = {"aac", "mp3", "ac3", "eac3", "opus", "flac", "dts", "truehd", "pcm_s16le"}

# ffprobe format_name tokens expected for each extension
EXT_TO_FORMAT = {
    ".mp4": {"mp4", "mov"},
    ".mkv": {"matroska", "webm"},
    ".avi": {"avi"},
    ".mov": {"mov", "mp4"},
}

TV_EP_RE = re.compile(r"[Ss](\d{1,2})[Ee](\d{1,3})")
YEAR_FOLDER_RE = re.compile(r".+ \(\d{4}\)$")
SEASON_FOLDER_RE = re.compile(r"^Season \d{2}$")
MIGRATED_RE = re.compile(r"\(migrated\s*\d*\)", re.IGNORECASE)
LANG_SUFFIX_RE = re.compile(r"\.[a-z]{2,3}$", re.IGNORECASE)

SANE_MAX_DURATION_SEC = 12 * 3600

REPORTS_DIR = Path(__file__).parent / "audit_reports"
CACHE_FILE = REPORTS_DIR / ".deep_cache.json"

FIELDNAMES = ["drive", "path", "tier", "severity", "issue", "detail", "mtime"]

# ============================================================


def make_issue(path, drive, tier, severity, issue, detail="", mtime=""):
    return {
        "drive": drive,
        "path": str(path),
        "tier": tier,
        "severity": severity,
        "issue": issue,
        "detail": detail,
        "mtime": mtime,
    }


# ============================================================
# ffprobe
# ============================================================


def run_ffprobe(path: Path):
    cmd = [
        "ffprobe", "-v", "error",
        "-print_format", "json",
        "-show_format", "-show_streams",
        str(path),
    ]
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            timeout=60,
        )
        if result.returncode != 0:
            tail = (result.stderr or "").strip().splitlines()
            return None, (tail[-1] if tail else f"ffprobe rc={result.returncode}")
        return json.loads(result.stdout), None
    except subprocess.TimeoutExpired:
        return None, "ffprobe timeout"
    except json.JSONDecodeError as e:
        return None, f"ffprobe invalid json: {e}"
    except FileNotFoundError:
        print("[FATAL] ffprobe not found on PATH", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        return None, f"ffprobe error: {e}"


# ============================================================
# Tier 1 - structural
# ============================================================


def check_tier1(path: Path, drive: str, mtime_str: str):
    issues = []
    info, err = run_ffprobe(path)
    if info is None:
        issues.append(make_issue(path, drive, "tier1", "error", "unreadable", err or "", mtime_str))
        return issues

    streams = info.get("streams", []) or []
    fmt = info.get("format", {}) or {}

    v_streams = [s for s in streams if s.get("codec_type") == "video"]
    a_streams = [s for s in streams if s.get("codec_type") == "audio"]

    if not v_streams:
        issues.append(make_issue(path, drive, "tier1", "error", "no_video_stream", "", mtime_str))
    if not a_streams:
        issues.append(make_issue(path, drive, "tier1", "warn", "no_audio_stream", "", mtime_str))

    try:
        duration = float(fmt.get("duration") or 0)
    except (ValueError, TypeError):
        duration = 0
    if duration <= 0:
        issues.append(make_issue(path, drive, "tier1", "error", "zero_duration", "", mtime_str))
    elif duration > SANE_MAX_DURATION_SEC:
        issues.append(make_issue(path, drive, "tier1", "warn", "suspicious_duration",
                                 f"{duration:.0f}s", mtime_str))

    def _enc_bad(enc):
        enc = (enc or "").lower()
        if enc.startswith("lavf"):
            return None
        for p in ENCODER_BAD_PATTERNS:
            if p in enc:
                return p
        return None

    fmt_tags = fmt.get("tags", {}) or {}
    if _enc_bad(fmt_tags.get("encoder")):
        issues.append(make_issue(path, drive, "tier1", "warn", "bad_encoder_tag",
                                 f"format.encoder={fmt_tags.get('encoder')}", mtime_str))
    else:
        for s in streams:
            st = s.get("tags", {}) or {}
            if _enc_bad(st.get("encoder")):
                issues.append(make_issue(path, drive, "tier1", "warn", "bad_encoder_tag",
                                         f"stream.encoder={st.get('encoder')}", mtime_str))
                break

    if v_streams:
        vcodec = (v_streams[0].get("codec_name") or "").lower()
        if vcodec and vcodec not in OK_VIDEO_CODECS:
            issues.append(make_issue(path, drive, "tier1", "warn", "unusual_video_codec",
                                     vcodec, mtime_str))
    if a_streams:
        acodec = (a_streams[0].get("codec_name") or "").lower()
        if acodec and acodec not in OK_AUDIO_CODECS:
            issues.append(make_issue(path, drive, "tier1", "warn", "unusual_audio_codec",
                                     acodec, mtime_str))

    fmt_name = (fmt.get("format_name") or "").lower()
    ext = path.suffix.lower()
    expected = EXT_TO_FORMAT.get(ext, set())
    if expected and fmt_name and not any(e in fmt_name for e in expected):
        issues.append(make_issue(path, drive, "tier1", "warn", "container_mismatch",
                                 f"ext={ext} format={fmt_name}", mtime_str))

    return issues


# ============================================================
# Tier 2 - naming/layout
# ============================================================


def check_tier2_tv(path: Path, drive: str, mtime_str: str, tv_root: Path):
    issues = []
    try:
        rel = path.relative_to(tv_root)
    except ValueError:
        return issues

    parts = rel.parts
    if len(parts) != 3:
        issues.append(make_issue(path, drive, "tier2", "warn", "bad_tv_layout",
                                 f"expected 3 path parts below root, got {len(parts)}: {rel}",
                                 mtime_str))
    else:
        show_folder, season_folder, filename = parts
        if show_folder in LAYOUT_WHITELIST:
            return issues
        if not YEAR_FOLDER_RE.match(show_folder):
            issues.append(make_issue(path, drive, "tier2", "warn", "bad_tv_layout",
                                     f"show folder missing (Year): {show_folder}", mtime_str))
        if not SEASON_FOLDER_RE.match(season_folder):
            issues.append(make_issue(path, drive, "tier2", "warn", "bad_tv_layout",
                                     f"season folder not 'Season NN': {season_folder}", mtime_str))
        if not TV_EP_RE.search(filename):
            issues.append(make_issue(path, drive, "tier2", "warn", "bad_tv_layout",
                                     f"filename missing SxxExx: {filename}", mtime_str))

    if MIGRATED_RE.search(path.name):
        issues.append(make_issue(path, drive, "tier2", "warn", "migrated_leftover",
                                 path.name, mtime_str))
    return issues


def check_tier2_movie(path: Path, drive: str, mtime_str: str, movie_root: Path):
    issues = []
    try:
        rel = path.relative_to(movie_root)
    except ValueError:
        return issues

    parts = rel.parts
    if len(parts) != 2:
        issues.append(make_issue(path, drive, "tier2", "warn", "bad_movie_layout",
                                 f"expected 2 path parts below root, got {len(parts)}: {rel}",
                                 mtime_str))
    else:
        folder, filename = parts
        if not YEAR_FOLDER_RE.match(folder):
            issues.append(make_issue(path, drive, "tier2", "warn", "bad_movie_layout",
                                     f"movie folder missing (Year): {folder}", mtime_str))
        stem = Path(filename).stem
        if stem != folder:
            issues.append(make_issue(path, drive, "tier2", "info", "movie_filename_mismatch",
                                     f"file stem '{stem}' != folder '{folder}'", mtime_str))

    if MIGRATED_RE.search(path.name):
        issues.append(make_issue(path, drive, "tier2", "warn", "migrated_leftover",
                                 path.name, mtime_str))
    return issues


def scan_orphans_and_empty_dirs(root: Path, drive: str):
    issues = []
    if not root.exists():
        return issues
    for d in root.rglob("*"):
        if not d.is_dir():
            continue
        try:
            children = list(d.iterdir())
        except PermissionError:
            continue
        if not children:
            issues.append(make_issue(d, drive, "tier2", "info", "empty_dir", "", ""))
            continue
        video_stems = {c.stem for c in children if c.is_file() and c.suffix.lower() in VIDEO_EXTS}
        for c in children:
            if c.is_file() and c.suffix.lower() in SUB_EXTS:
                stem = c.stem
                stem_no_lang = LANG_SUFFIX_RE.sub("", stem)
                if stem not in video_stems and stem_no_lang not in video_stems:
                    issues.append(make_issue(c, drive, "tier2", "info", "orphan_subtitle", "", ""))
    return issues


# ============================================================
# Tier 3 - decode sweep (cache-gated)
# ============================================================


def load_cache():
    if not CACHE_FILE.exists():
        return {}
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_cache(cache):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def check_tier3(path: Path, drive: str, mtime_str: str, cache: dict):
    key = str(path)
    try:
        st = path.stat()
    except OSError as e:
        return [make_issue(path, drive, "tier3", "error", "stat_failed", str(e), mtime_str)]

    cached = cache.get(key)
    if cached and cached.get("size") == st.st_size and cached.get("mtime") == st.st_mtime:
        if cached.get("result") == "ok":
            return []
        return [make_issue(path, drive, "tier3", "error", "decode_error",
                           f"{cached.get('detail', '')} (cached)", mtime_str)]

    cmd = [
        "ffmpeg", "-v", "error", "-xerror",
        "-nostdin", "-hide_banner",
        "-i", str(path),
        "-map", "0:v:0?", "-map", "0:a?",
        "-f", "null", "-",
    ]
    issues = []
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            timeout=3 * 3600,
        )
        stderr = (result.stderr or "").strip()
        if result.returncode != 0 or stderr:
            tail = stderr.splitlines()[-1] if stderr else f"rc={result.returncode}"
            detail = tail[:500]
            issues.append(make_issue(path, drive, "tier3", "error", "decode_error", detail, mtime_str))
            cache[key] = {
                "size": st.st_size, "mtime": st.st_mtime,
                "last_checked": datetime.now().isoformat(timespec="seconds"),
                "result": "error", "detail": detail,
            }
        else:
            cache[key] = {
                "size": st.st_size, "mtime": st.st_mtime,
                "last_checked": datetime.now().isoformat(timespec="seconds"),
                "result": "ok", "detail": "",
            }
    except subprocess.TimeoutExpired:
        issues.append(make_issue(path, drive, "tier3", "error", "decode_timeout", "", mtime_str))
    except FileNotFoundError:
        print("[FATAL] ffmpeg not found on PATH", file=sys.stderr)
        sys.exit(2)
    return issues


# ============================================================
# Walk
# ============================================================


def walk_videos(root: Path):
    if not root.exists():
        return
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS:
            yield p


# ============================================================
# Main
# ============================================================


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="Audit Jellyfin media libraries.")
    ap.add_argument("--deep", action="store_true",
                    help="Run tier 3 full decode sweep (slow, cache-gated).")
    ap.add_argument("--drive", action="append", default=[],
                    help="Limit to specific drives (e.g. --drive D --drive F). Repeatable.")
    ap.add_argument("--limit", type=int, default=0,
                    help="Process at most N videos per root (testing).")
    ap.add_argument("--clear-cache", action="store_true",
                    help="Clear deep-decode cache and exit.")
    args = ap.parse_args()

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    if args.clear_cache:
        if CACHE_FILE.exists():
            CACHE_FILE.unlink()
            print(f"Cleared cache: {CACHE_FILE}")
        else:
            print("No cache to clear.")
        return

    drive_filter = {d.upper().rstrip(":") for d in args.drive} if args.drive else None

    def _filter(root_list):
        out = []
        for r in root_list:
            drive = r[0].upper()
            if drive_filter is None or drive in drive_filter:
                out.append((Path(r), drive))
        return out

    tv_roots = _filter(TV_ROOTS)
    movie_roots = _filter(MOVIE_ROOTS)

    print("=== Jellyfin Audit ===")
    print(f"Started:      {datetime.now().isoformat(timespec='seconds')}")
    print(f"Deep mode:    {args.deep}")
    print(f"TV roots:     {[str(r) for r, _ in tv_roots]}")
    print(f"Movie roots:  {[str(r) for r, _ in movie_roots]}")
    print("======================\n")

    all_issues = []
    cache = load_cache() if args.deep else {}
    files_scanned = 0

    def _process(video: Path, drive: str, kind: str, root: Path):
        nonlocal files_scanned
        files_scanned += 1
        try:
            st = video.stat()
            mtime_str = datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")
        except OSError:
            mtime_str = ""

        all_issues.extend(check_tier1(video, drive, mtime_str))
        if kind == "tv":
            all_issues.extend(check_tier2_tv(video, drive, mtime_str, root))
        else:
            all_issues.extend(check_tier2_movie(video, drive, mtime_str, root))
        if args.deep:
            all_issues.extend(check_tier3(video, drive, mtime_str, cache))

        if files_scanned % 100 == 0:
            print(f"[progress] {files_scanned} files scanned, {len(all_issues)} issues", flush=True)

    for root, drive in tv_roots:
        if not root.exists():
            print(f"[WARN] Missing root: {root}")
            continue
        print(f"[scan] TV     {root}")
        count = 0
        for video in walk_videos(root):
            _process(video, drive, "tv", root)
            count += 1
            if args.limit and count >= args.limit:
                break
        all_issues.extend(scan_orphans_and_empty_dirs(root, drive))

    for root, drive in movie_roots:
        if not root.exists():
            print(f"[WARN] Missing root: {root}")
            continue
        print(f"[scan] Movies {root}")
        count = 0
        for video in walk_videos(root):
            _process(video, drive, "movie", root)
            count += 1
            if args.limit and count >= args.limit:
                break
        all_issues.extend(scan_orphans_and_empty_dirs(root, drive))

    if args.deep:
        save_cache(cache)

    ts = datetime.now().strftime("%Y-%m-%d_%H%M")
    report_path = REPORTS_DIR / f"{ts}.csv"
    latest_path = REPORTS_DIR / "latest.csv"
    summary_path = REPORTS_DIR / "latest_summary.txt"

    with open(report_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        for row in all_issues:
            w.writerow(row)

    latest_path.write_bytes(report_path.read_bytes())

    sev_counts = Counter(i["severity"] for i in all_issues)
    issue_counts = Counter(i["issue"] for i in all_issues)
    drives_label = ",".join(sorted(drive_filter)) if drive_filter else "D,F,L"
    summary_lines = [
        f"Audit run:     {datetime.now().isoformat(timespec='seconds')}",
        f"Mode:          {'tier1+tier2+tier3 (deep)' if args.deep else 'tier1+tier2'}",
        f"Drives:        {drives_label}",
        f"Files scanned: {files_scanned}",
        "",
        f"Errors:        {sev_counts.get('error', 0)}",
        f"Warnings:      {sev_counts.get('warn', 0)}",
        f"Info:          {sev_counts.get('info', 0)}",
        "",
        "Top issues:",
    ]
    for issue, n in issue_counts.most_common(15):
        summary_lines.append(f"  {n:5d} x {issue}")
    summary = "\n".join(summary_lines) + "\n"
    summary_path.write_text(summary, encoding="utf-8")

    print()
    print(summary)
    print(f"Report:  {report_path}")
    print(f"Latest:  {latest_path}")
    print(f"Summary: {summary_path}")


if __name__ == "__main__":
    main()
