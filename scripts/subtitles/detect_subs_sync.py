#!/usr/bin/env python
import subprocess
import re
from pathlib import Path

COMMON_FPS = [
    (25.0, 23.976),
    (23.976, 25.0),
    (24.0, 23.976),
]

def get_video_info(video):
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate,duration",
        "-of", "default=noprint_wrappers=1"
    ]
    out = subprocess.check_output(cmd + [video], text=True)
    info = {}
    for line in out.splitlines():
        k, v = line.split("=")
        info[k] = v
    num, den = map(float, info["r_frame_rate"].split("/"))
    return float(info["duration"]), num / den

def parse_srt_times(srt: Path):
    times = []
    for line in srt.read_text(encoding="utf-8").splitlines():
        if "-->" in line:
            start = line.split("-->")[0].strip()
            h, m, rest = start.split(":")
            s, ms = rest.split(",")
            total = (
                int(h) * 3600 +
                int(m) * 60 +
                int(s) +
                int(ms) / 1000.0
            )
            times.append(total)
    return times

def detect_fps_fix(video_dur, sub_dur):
    ratio = video_dur / sub_dur
    for src, tgt in COMMON_FPS:
        if abs(ratio - (tgt / src)) < 0.01:
            return src, tgt
    return None

def main(video_path, srt_path):
    video_dur, fps = get_video_info(video_path)
    times = parse_srt_times(srt_path)
    sub_dur = times[-1] - times[0]

    fix = detect_fps_fix(video_dur, sub_dur)
    if fix:
        print(f"[FPS FIX] {fix[0]} -> {fix[1]}")
    else:
        print("[OK] No FPS drift detected")

if __name__ == "__main__":
    import sys
    main(sys.argv[1], Path(sys.argv[2]))
