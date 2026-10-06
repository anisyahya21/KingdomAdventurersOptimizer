@echo off
cd /d "%~dp0"
python launch.py go
if errorlevel 1 pause
