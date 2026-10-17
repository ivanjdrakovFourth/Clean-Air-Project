@echo off
setlocal
if not exist "%~dp0runtime\python\python.exe" (
  echo The portable Python runtime is missing. See README.md for build instructions.
  pause
  exit /b 1
)
"%~dp0runtime\python\python.exe" -B -m clean_air serve --open-browser %*
if errorlevel 1 pause
