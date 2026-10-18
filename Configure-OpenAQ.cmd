@echo off
setlocal
"%~dp0runtime\python\python.exe" -B -m clean_air configure-openaq
if errorlevel 1 pause
