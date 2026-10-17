@echo off
setlocal
if not exist "%~dp0runtime\python\python.exe" (
  echo The portable Python runtime is missing. See README.md for build instructions.
  pause
  exit /b 1
)
for %%I in ("%~dp0runtime\python\python.exe") do if %%~zI LSS 1024 (
  echo The portable Python runtime has not been fully downloaded.
  echo Run git lfs install, then git lfs pull from the project folder.
  pause
  exit /b 1
)
"%~dp0runtime\python\python.exe" -B -m clean_air serve --open-browser %*
if errorlevel 1 pause
