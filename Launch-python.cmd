@echo off
cd /d "%~dp0"
python launch.py original
if errorlevel 1 pause
