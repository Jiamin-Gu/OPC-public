@echo off
rem OPC one-click launcher for Windows
rem All messages are printed by scripts\launch.py: Chinese text inside a .bat file gets garbled
rem (cmd re-reads the file at the wrong byte offset after running another program).
setlocal
title OPC
cd /d "%~dp0"

set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
"%PY%" --version >nul 2>&1 || goto :no_python
"%PY%" scripts\launch.py %*
if errorlevel 1 pause
exit /b

:no_python
echo [Error] Python was not found.
echo Please install Python 3.10 or newer from https://www.python.org/downloads/
echo During installation, check "Add python.exe to PATH", then double-click this file again.
pause
exit /b 1
