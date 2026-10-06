@echo off
cd /d "%~dp0"
python launch.py rust
if errorlevel 1 pause
