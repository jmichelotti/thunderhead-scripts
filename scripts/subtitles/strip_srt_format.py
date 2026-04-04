import re
import sys
from pathlib import Path

BASE_DIR = Path(r"C:\Users\thunderhead\Downloads")

def strip_html_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)

def read_srt_with_fallback(path: Path) -> str:
    """
    Try UTF-8 first, fall back to Windows-1252 if needed.
    """
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="cp1252")

def main():
    if len(sys.argv) != 2:
        print("Usage: python strip_srt_format.py <file.srt>")
        sys.exit(1)

    filename = sys.argv[1]
    srt_path = BASE_DIR / filename

    if not srt_path.exists():
        print(f"Error: File not found: {srt_path}")
        sys.exit(1)

    raw_text = read_srt_with_fallback(srt_path)

    cleaned_text = strip_html_tags(raw_text)

    # Write back as UTF-8 (standard, safe for Jellyfin)
    srt_path.write_text(cleaned_text, encoding="utf-8")

    print(f"[OK] Formatting stripped and file normalized to UTF-8:")
    print(f"     {srt_path}")

if __name__ == "__main__":
    main()
