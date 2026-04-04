import subprocess
import sys
import re
from pathlib import Path
from typing import List

# ================= CONFIG =================

OUTPUT_DIR = Path(r"C:\Users\thunderhead\Downloads")

SUB_LANGS = ["en", "eng"]
MAX_HEIGHT = 1080

SHOW_NAME = "Australian Survivor"

# 🔹 Hardcode the real Jellyfin season number
HARDCODE_SEASON = 9

URLS: List[str] = [
]

EP_REGEX = re.compile(r"survivor-(\d+)-(\d+)/?")

# =========================================


def download_for_jellyfin(url: str):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    match = EP_REGEX.search(url)
    if not match:
        raise ValueError(f"Could not parse episode from URL: {url}")

    episode = int(match.group(2))
    season = HARDCODE_SEASON

    output_file = OUTPUT_DIR / f"{SHOW_NAME} S{season:02d}E{episode:02d}.mp4"

    cmd = [
        "yt-dlp",
        "-f", f"bestvideo[ext=mp4][height<={MAX_HEIGHT}]/"
              f"bv*[height<={MAX_HEIGHT}]+ba/best",
        "--merge-output-format", "mp4",
        "--postprocessor-args", "ffmpeg:-movflags +faststart",
        "--write-sub",
        "--write-auto-sub",
        "--sub-lang", ",".join(SUB_LANGS),
        "--convert-subs", "srt",
        "--embed-subs",
        "--add-metadata",
        "-o", str(output_file),
        url
    ]

    print(f"\nDownloading {SHOW_NAME} S{season:02d}E{episode:02d}")
    subprocess.run(cmd, check=True)


def main():
    if URLS:
        print(f"Batch mode: downloading {len(URLS)} episodes")
        for url in URLS:
            download_for_jellyfin(url)
    else:
        if len(sys.argv) != 2:
            print("Usage: python download_for_jellyfin.py <URL>")
            sys.exit(1)
        download_for_jellyfin(sys.argv[1])


if __name__ == "__main__":
    main()
