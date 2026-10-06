@echo off
setlocal
cd /d "%~dp0"
where cl >nul 2>nul
if errorlevel 1 (echo Run from an x64 Visual Studio Developer Command Prompt. & exit /b 1)
if not defined KA_CPP_BUILD_WORKERS set KA_CPP_BUILD_WORKERS=4
cl /nologo /MP%KA_CPP_BUILD_WORKERS% /std:c++20 /O2 /EHsc /I "..\include" main.cpp admission.cpp fixed_formation.cpp normalization.cpp preparation.cpp pipeline.cpp execution.cpp replay.cpp monitor.cpp /Fe:optimizer.exe /link bcrypt.lib user32.lib gdi32.lib
exit /b %errorlevel%
