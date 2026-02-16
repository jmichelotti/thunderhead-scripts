#!/usr/bin/env python
"""
Normalize movie filenames/folders for Jellyfin using OMDb (IMDb) lookups.

- Looks at all video files in MOVIES_ROOT (not in subfolders).
- For each movie:
    * Queries OMDb by IMDb ID (if present) or title.
    * Creates a folder "Title (Year)" (sanitized for Windows).
    * Moves the video + matching subtitles into that folder.
    * Renames them to "Title (Year).ext".

Safe by default: DRY RUN unless you pass --apply.
"""

import os
import re
import shutil
from pathlib import Path
from typing import Optional, Dict

import requests


# ======== CONFIG ========

DEFAULT_MOVIES_ROOT = r"C:\Temp_Media\Movies"

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov"}
SUB_EXTS = {".srt", ".ass", ".ssa", ".sub", ".vtt"}

OMDB_API_KEY = "591dfd18"

USE_REMOTE_TITLE = True


# ======== HELPERS ========

IMDB_ID_RE = re.compile(r"(tt\d{7,8})", re.IGNORECASE)


def sanitize_for_windows(name: str) -> str:
    invalid_chars = r'<>:"/\\|?*'
    trans_table = str.maketrans({ch: " " for ch in invalid_chars})
    name = name.translate(trans_table)
    name = re.sub(r"\s+", " ", name).strip()
    name = name.rstrip(" .")
    return name


def try_omdb_imdb_id(imdb_id: str) -> Optional[Dict[str, str]]:
    params = {
        "apikey": OMDB_API_KEY,
        "i": imdb_id.lower(),
        "type": "movie",
    }

    try:
        resp = requests.get("https://www.omdbapi.com/", params=params, timeout=10)
        data = resp.json()
    except Exception as e:
        print(f"  [OMDb] IMDb lookup error: {e}")
        return None

    if data.get("Response") != "True":
        print(f"  [OMDb] IMDb lookup failed: {data.get('Error')}")
        return None

    title = data.get("Title")
    year = data.get("Year")

    if not title or not year:
        return None

    year = "".join(ch for ch in year if ch.isdigit())[:4]
    print(f"  [OMDb] IMDb match: {title} ({year}) [{imdb_id}]")
    return {"title": title, "year": year}


def try_omdb_exact(title: str) -> Optional[Dict[str, str]]:
    params = {
        "apikey": OMDB_API_KEY,
        "t": title,
        "type": "movie",
    }

    try:
        resp = requests.get("https://www.omdbapi.com/", params=params, timeout=10)
        data = resp.json()
    except Exception as e:
        print(f"  [OMDb] Error connecting to OMDb (exact): {e}")
        return None

    if data.get("Response") != "True":
        print(f"  [OMDb] Exact lookup failed: {data.get('Error')}")
        return None

    raw_title = data.get("Title")
    raw_year = data.get("Year")

    if not raw_title or not raw_year:
        return None

    year = "".join(ch for ch in raw_year if ch.isdigit())[:4]
    print(f"  [OMDb] Exact match: {raw_title} ({year})")
    return {"title": raw_title, "year": year}


def try_omdb_search(term: str) -> Optional[Dict[str, str]]:
    params = {
        "apikey": OMDB_API_KEY,
        "s": term,
        "type": "movie",
    }

    try:
        resp = requests.get("https://www.omdbapi.com/", params=params, timeout=10)
        data = resp.json()
    except Exception as e:
        print(f"  [OMDb] Error connecting to OMDb (search): {e}")
        return None

    if data.get("Response") != "True" or "Search" not in data:
        print(f"  [OMDb] Search failed: {data.get('Error')}")
        return None

    first = data["Search"][0]
    raw_title = first.get("Title")
    raw_year = first.get("Year")

    if not raw_title or not raw_year:
        return None

    year = "".join(ch for ch in raw_year if ch.isdigit())[:4]
    print(f"  [OMDb] Search match: {raw_title} ({year}) for term '{term}'")
    return {"title": raw_title, "year": year}


# ======== OMDb LOOKUP ========

def lookup_movie_metadata(base_title: str) -> Optional[Dict[str, str]]:
    if not OMDB_API_KEY:
        return None

    # 0) IMDb ID lookup (highest priority)
    m = IMDB_ID_RE.search(base_title)
    if m:
        meta = try_omdb_imdb_id(m.group(1))
        if meta:
            return meta

    # 1) Exact title
    meta = try_omdb_exact(base_title)
    if meta:
        return meta

    # 2) Strip trailing year and retry exact
    cleaned = re.sub(r"\b(19|20)\d{2}\b", "", base_title).strip()
    if cleaned != base_title:
        meta = try_omdb_exact(cleaned)
        if meta:
            return meta

    # 3) Split on '-' and search
    for part in cleaned.split("-"):
        part = part.strip()
        if part:
            meta = try_omdb_search(part)
            if meta:
                return meta

    # 4) Full search fallback
    return try_omdb_search(cleaned)


def base_has_year(base_name: str) -> bool:
    if "(" in base_name and ")" in base_name:
        inner = base_name.split("(")[-1].split(")")[0].strip()
        return len(inner) == 4 and inner.isdigit()
    return False


def make_target_title(base_name: str) -> str:
    if base_has_year(base_name):
        safe = sanitize_for_windows(base_name)
        return safe

    meta = lookup_movie_metadata(base_name)
    if not meta:
        return sanitize_for_windows(base_name)

    title = meta["title"] if USE_REMOTE_TITLE else base_name
    year = meta["year"]
    return sanitize_for_windows(f"{title} ({year})")


# ======== CORE LOGIC ========

def process_movies(movies_root: Path, dry_run: bool = True) -> None:
    print(f"Movies root: {movies_root}")
    print(f"DRY RUN: {dry_run}")
    print("=" * 60)

    for item in sorted(movies_root.iterdir()):
        if not item.is_file() or item.suffix.lower() not in VIDEO_EXTS:
            continue

        base_name = item.stem
        print(f"\nFound movie: {item.name}")
        print(f"  Base name: {base_name}")

        target_title = make_target_title(base_name)
        target_dir = movies_root / target_title
        target_video = target_dir / f"{target_title}{item.suffix.lower()}"

        print(f"  Target dir:  {target_dir}")
        print(f"  Target file: {target_video.name}")

        matching_subs = [
            s for s in movies_root.iterdir()
            if s.is_file()
            and s.stem == base_name
            and s.suffix.lower() in SUB_EXTS
        ]

        if dry_run:
            print("  [DRY RUN] Would create folder and move/rename files.")
            continue

        target_dir.mkdir(exist_ok=True)

        if not target_video.exists():
            shutil.move(str(item), str(target_video))

        for sub in matching_subs:
            target_sub = target_dir / f"{target_title}{sub.suffix}"
            if not target_sub.exists():
                shutil.move(str(sub), str(target_sub))


# ======== CLI ========

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Normalize movie folders and filenames for Jellyfin using OMDb (IMDb) lookups."
    )
    parser.add_argument("--root", default=DEFAULT_MOVIES_ROOT)
    parser.add_argument("--apply", action="store_true")

    args = parser.parse_args()
    process_movies(Path(args.root), dry_run=not args.apply)
