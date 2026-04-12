@echo off
REM Nightly Jellyfin audit (tier 1+2+3 deep scan)
REM Scheduled via Task Scheduler to run at 2:00 AM
python C:\dev\thunderhead\scripts\audit_jellyfin.py --deep >> C:\dev\thunderhead\scripts\audit_reports\nightly.log 2>&1
