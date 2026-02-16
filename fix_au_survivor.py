#!/usr/bin/env python
"""
fix_au_survivor.py

Purpose-built cleaner for Australian Survivor (AU) releases.

Features:
- Flattens nested release folders (SLAG-style)
- Fixes playback compatibility for Jellyfin (MKV/AVI -> MP4)
- Renames to Jellyfin TV format
- Applies season offset (AU S03 -> Jellyfin S01)
- Moves into final TV library

DRY RUN by default. Use --apply to commit changes.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

# ============================================================
# CONFIG
# ============================================================

SOURCE_ROOTS = [
    r"F:\S11",
]

DEST_TV_ROOT = Path(r"F:\TV Shows")

SHOW_TITLE = "Australian Survivor"
SHOW_YEAR = "2016"

SEASON_OFFSET = -2  # AU numbering -> Jellyfin numbering

VIDEO_EXTS = {".mkv", ".mp4", ".avi"}

SAFE_VIDEO_CODECS = {"h264"}
SAFE_AUDIO_CODECS = {"aac", "mp3", "ac3", "eac3"}

# ============================================================
# FFPROBE / FFMPEG
# ============================================================

def run_ffprobe(path: Path):
    cmd = [
        "ffprobe",
        "-v", "error",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        str(path),
    ]
    try:
        r = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        return json.loads(r.stdout)
    except Exception as e:
        print(f"[ffprobe ERROR] {path}: {e}", file=sys.stderr)
        return None


def get_codecs(info: dict):
    v = a = None
    for s in info.get("streams", []):
        if s.get("codec_type") == "video" and not v:
            v = s.get("codec_name")
        elif s.get("codec_type") == "audio" and not a:
            a = s.get("codec_name")
    return (v or "").lower(), (a or "").lower()


def remux(src: Path, dst: Path, dry_run: bool):
    if dry_run:
        print(f"[DRY RUN] Remux {src} -> {dst}")
        return True

    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(src),
        "-c", "copy",
        "-movflags", "+faststart",
        str(dst),
    ]
    subprocess.run(cmd, check=True)
    return True


def reencode(src: Path, dst: Path, dry_run: bool):
    if dry_run:
        print(f"[DRY RUN] Re-encode {src} -> {dst}")
        return True

    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(src),
        "-map", "0",
        "-c:v", "libx264",
        "-crf", "18",
        "-preset", "slow",
        "-c:a", "aac",
        "-movflags", "+faststart",
        str(dst),
    ]
    subprocess.run(cmd, check=True)
    return True

# ============================================================
# PARSING
# ============================================================

EP_RE = re.compile(r"S(?P<season>\d+)E(?P<ep>\d+)", re.IGNORECASE)

def parse_episode(path: Path):
    m = EP_RE.search(path.name)
    if not m:
        return None

    return int(m.group("season")), int(m.group("ep"))

# ============================================================
# CORE LOGIC
# ============================================================

def process_video(src: Path, dry_run: bool):
    parsed = parse_episode(src)
    if not parsed:
        print(f"[SKIP] Cannot parse episode: {src.name}")
        return

    src_season, episode = parsed
    dst_season = src_season + SEASON_OFFSET
    if dst_season <= 0:
        print(f"[WARN] Season offset invalid for {src.name}")
        return

    season_dir = (
        DEST_TV_ROOT
        / f"{SHOW_TITLE} ({SHOW_YEAR})"
        / f"Season {dst_season:02d}"
    )
    dst_name = f"{SHOW_TITLE} ({SHOW_YEAR}) S{dst_season:02d}E{episode:02d}.mp4"
    final_path = season_dir / dst_name
    tmp_path = final_path.with_suffix(".tmp.mp4")

    # ✅ EARLY GUARD: episode already exists
    if final_path.exists():
        print(f"[SKIP] Episode already exists: {final_path.name}")
        return

    info = run_ffprobe(src)
    if not info:
        return

    vcodec, acodec = get_codecs(info)
    can_remux = vcodec in SAFE_VIDEO_CODECS and acodec in SAFE_AUDIO_CODECS

    # Direct move if already MP4 and safe
    if src.suffix.lower() == ".mp4" and can_remux:
        if dry_run:
            print(f"[DRY RUN] Move {src} -> {final_path}")
            return
        season_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(src, final_path)
        return

    # Remux or re-encode
    if can_remux:
        print(f"[REMUX] {src.name}")
        remux(src, tmp_path, dry_run)
    else:
        print(f"[REENCODE] {src.name}")
        reencode(src, tmp_path, dry_run)

    if dry_run:
        return

    # ✅ SAFETY CHECK BEFORE FINALIZE
    if final_path.exists():
        print(f"[SKIP] Final already exists after processing: {final_path.name}")
        if tmp_path.exists():
            tmp_path.unlink()
        return

    src.unlink()
    tmp_path.rename(final_path)


def find_videos(root: Path):
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS:
            yield p

# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Clean Australian Survivor releases for Jellyfin"
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    dry_run = not args.apply

    print("=== Australian Survivor Jellyfin Fixer ===")
    print(f"Mode: {'APPLY' if args.apply else 'DRY RUN'}")
    print("=" * 50)

    for root in map(Path, SOURCE_ROOTS):
        if not root.exists():
            continue

        print(f"\nScanning: {root}")
        for video in find_videos(root):
            process_video(video, dry_run)

    print("\nDone.")

if __name__ == "__main__":
    main()
