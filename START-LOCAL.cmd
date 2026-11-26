@echo off
py -3 "%~dp0scripts\start_local.py" %*
if errorlevel 1 pause
