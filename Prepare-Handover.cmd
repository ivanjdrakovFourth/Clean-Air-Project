@echo off
setlocal
"%~dp0runtime\python\python.exe" -B -m clean_air handover %*
if errorlevel 1 pause
