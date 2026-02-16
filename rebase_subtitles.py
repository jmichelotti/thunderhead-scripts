import re
from datetime import timedelta
from pathlib import Path

src = Path(r"L:\TV Shows\Law & Order UK (2009)\Season 06\Law & Order UK (2009) S06E06.srt")
dst = Path(r"L:\TV Shows\Law & Order UK (2009)\Season 06\Law & Order UK (2009) S06E06.rebased.srt")

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

text = src.read_text(encoding="utf-8")

# Find the first timestamp in the file
first_match = time_re.search(text)
if not first_match:
    raise RuntimeError("No timestamps found")

offset = parse_time(first_match.group(1))

# Rebase all timestamps
rebased = time_re.sub(
    lambda m: format_time(parse_time(m.group(1)) - offset),
    text
)

dst.write_text(rebased, encoding="utf-8")
print(f"Rebased subtitles written to: {dst}")