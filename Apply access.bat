@echo off
REM Double-click this after clicking "Save" in the dashboard's Access tab.
REM It applies the permission change and publishes it for everyone.
cd /d "%~dp0"
python "%~dp0apply_permissions.py"
