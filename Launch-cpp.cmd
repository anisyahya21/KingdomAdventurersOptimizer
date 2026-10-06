@echo off
cd /d "%~dp0"
python launch.py cpp
if errorlevel 1 pause
