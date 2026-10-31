@echo off
rem The privacy-lock command (installed in "%ProgramFiles%\privacy-lock", which is on PATH).
"%~dp0venv\Scripts\python.exe" "%~dp0privacy_lock.py" %*
