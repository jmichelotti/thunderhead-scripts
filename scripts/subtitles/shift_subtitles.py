import argparse
import re
from datetime import timedelta
from pathlib import Path
import sys

time_re = re.compile(r"(\d{2}:\d{2}:\d{2},\d{3})")

def parse_time(t):
    h, m, rest = t.split(":")
    s, ms = rest.split(",")
    return timedelta(
        hours=int(h),
        minutes=int(m),
        seconds=int(s),
        milliseconds=int(ms),
    )

def format_time(td):
    if td.total_seconds() < 0:
        td = timedelta(0)
    total_ms = int(td.total_seconds() * 1000)
    h = total_ms // 3600000
    m = (total_ms % 3600000) // 60000
    s = (total_ms % 60000) // 1000
    ms = total_ms % 1000
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

def shift_srt(path: Path, offset_seconds: float):
    text = path.read_text(encoding="utf-8")

    offset = timedelta(seconds=offset_seconds)

    shifted = time_re.sub(
        lambda m: format_time(parse_time(m.group(1)) + offset),
        text
    )

    # Write to temp file first (safety)
    tmp_path = path.with_suffix(".tmp.srt")
    tmp_path.write_text(shifted, encoding="utf-8")

    # Replace original
    tmp_path.replace(path)

def main():
    parser = argparse.ArgumentParser(
        description="Shift all timestamps in an .srt file and replace it in-place."
    )
    parser.add_argument(
        "srt",
        nargs="?",
        help="Path to .srt file"
    )
    parser.add_argument(
        "-d", "--delta",
        type=float,
        default=0.0,
        help="Time shift in seconds (positive or negative)"
    )

    args = parser.parse_args()

    if not args.srt:
        print("Error: No .srt file specified", file=sys.stderr)
        sys.exit(1)

    srt_path = Path(args.srt)

    if not srt_path.exists():
        print(f"Error: File not found: {srt_path}", file=sys.stderr)
        sys.exit(1)

    shift_srt(srt_path, args.delta)
    print(f"Shifted '{srt_path}' by {args.delta} seconds (in-place)")

if __name__ == "__main__":
    main()
