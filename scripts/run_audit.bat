@echo off
REM Nightly Jellyfin audit (tier 1+2+3 deep scan)
REM Scheduled via Task Scheduler to run at 2:00 AM
REM --cpu-limit 25: cap ffmpeg to 25% total CPU, spread across cores (quiet fan).
REM The Python script also enforces a PID lockfile to prevent duplicate instances.
start /B /BELOWNORMAL /WAIT python C:\dev\thunderhead\scripts\audit_jellyfin.py --deep --cpu-limit 25 >> C:\dev\thunderhead\scripts\audit_reports\nightly.log 2>&1
