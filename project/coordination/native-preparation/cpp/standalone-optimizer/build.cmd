@echo off
setlocal
cd /d "%~dp0"
where cl >nul 2>nul
if errorlevel 1 (echo Run from an x64 Visual Studio Developer Command Prompt. & exit /b 1)
if not defined KA_CPP_BUILD_WORKERS set KA_CPP_BUILD_WORKERS=4
if not exist .build-cache mkdir .build-cache
cl /nologo /MP%KA_CPP_BUILD_WORKERS% /std:c++20 /O2 /EHsc /I "..\include" /I ".." main.cpp admission.cpp normalization.cpp preparation.cpp effective_combat_features.cpp fixed_formation.cpp pipeline.cpp execution.cpp replay.cpp monitor.cpp /Fe:".build-cache\optimizer-corrected.exe" /link bcrypt.lib user32.lib gdi32.lib
exit /b %errorlevel%
