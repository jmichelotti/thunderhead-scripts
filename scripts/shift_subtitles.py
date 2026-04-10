#!/usr/bin/env python3
"""
shift_subtitles.py

Shifts all timestamps in an .srt subtitle file by a given number of seconds.

Usage:
  # Dry-run: show what the first few entries would look like after shifting
  python shift_subtitles.py 0.5 "F:/TV Shows/Show Name/Season 01/Show S01E01.srt"

  # Apply the shift (overwrites the file in place)
  python shift_subtitles.py 0.5 "F:/TV Shows/Show Name/Season 01/Show S01E01.srt" --apply

  # Shift backwards (negative value)
  python shift_subtitles.py -0.5 "path/to/file.srt" --apply

  # Scan staging directories for .srt files and pick interactively
  python shift_subtitles.py 0.5 --scan

Arguments:
  seconds   Amount to shift (positive = forward/later, negative = backward/earlier).
            Timestamps will not go below 00:00:00,000.
  file      Path to the .srt file. Required unless --scan is used.
  --scan    Search C:/Temp_Media/TV Shows and C:/Temp_Media/Movies for .srt files
            and let you pick which one to shift.
  --apply   Actually overwrite the file. Without this flag, only a preview is shown.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

STAGING_DIRS = [
    Path(r"C:\Temp_Media\TV Shows"),
    Path(r"C:\Temp_Media\Movies"),
]

TIMESTAMP_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3})"
    r"(\s*-->\s*)"
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3})"
)


def ts_to_ms(h: str, m: str, s: str, ms: str) -> int:
    return int(h) * 3_600_000 + int(m) * 60_000 + int(s) * 1_000 + int(ms)


def ms_to_ts(total_ms: int) -> str:
    if total_ms < 0:
        total_ms = 0
    h = total_ms // 3_600_000
    remainder = total_ms % 3_600_000
    m = remainder // 60_000
    remainder %= 60_000
    s = remainder // 1_000
    ms = remainder % 1_000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def shift_line(line: str, shift_ms: int) -> str:
    def replacer(match: re.Match) -> str:
        start_ms = ts_to_ms(match.group(1), match.group(2), match.group(3), match.group(4))
        arrow = match.group(5)
        end_ms = ts_to_ms(match.group(6), match.group(7), match.group(8), match.group(9))
        return ms_to_ts(start_ms + shift_ms) + arrow + ms_to_ts(end_ms + shift_ms)

    return TIMESTAMP_RE.sub(replacer, line)


def scan_for_srt_files() -> list[Path]:
    found: list[Path] = []
    for d in STAGING_DIRS:
        if d.exists():
            found.extend(sorted(d.rglob("*.srt")))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description="Shift .srt subtitle timestamps.")
    parser.add_argument("seconds", type=float, help="Seconds to shift (positive=forward, negative=backward)")
    parser.add_argument("file", nargs="?", type=Path, help="Path to the .srt file")
    parser.add_argument("--scan", action="store_true", help="Scan staging dirs for .srt files")
    parser.add_argument("--apply", action="store_true", help="Overwrite the file (default is dry-run)")
    args = parser.parse_args()

    # Resolve the target file
    if args.scan:
        srt_files = scan_for_srt_files()
        if not srt_files:
            print("No .srt files found in staging directories:")
            for d in STAGING_DIRS:
                print(f"  {d}")
            return 1
        print("Found .srt files:")
        for i, f in enumerate(srt_files, 1):
            print(f"  [{i}] {f}")
        try:
            choice = int(input("\nPick a file number: ")) - 1
        except (ValueError, EOFError):
            print("Cancelled.")
            return 1
        if choice < 0 or choice >= len(srt_files):
            print("Invalid choice.")
            return 1
        target = srt_files[choice]
    elif args.file:
        target = args.file
    else:
        parser.error("Provide a file path or use --scan")
        return 1

    if not target.exists():
        print(f"ERROR: File not found: {target}")
        return 1

    shift_ms = int(args.seconds * 1000)
    direction = "forward" if shift_ms >= 0 else "backward"
    print(f"Shifting {target.name} {direction} by {abs(args.seconds)}s ({abs(shift_ms)}ms)")

    original = target.read_text(encoding="utf-8-sig")
    lines = original.splitlines(keepends=True)
    shifted_lines = [shift_line(line, shift_ms) for line in lines]

    if not args.apply:
        # Preview: show first 5 timestamp lines before/after
        print(f"\n[DRY RUN] Preview (first 5 timestamp changes):\n")
        count = 0
        for orig, new in zip(lines, shifted_lines):
            if TIMESTAMP_RE.search(orig):
                print(f"  {orig.rstrip()}")
                print(f"  {new.rstrip()}")
                print()
                count += 1
                if count >= 5:
                    break
        print(f"Pass --apply to overwrite the file.")
        return 0

    target.write_text("".join(shifted_lines), encoding="utf-8")
    print(f"Done. Shifted {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
