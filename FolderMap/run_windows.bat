@echo off
cd /d "%~dp0"
where pyw >nul 2>nul
if %errorlevel%==0 (
    start "" pyw app.pyw
    exit /b
)
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw app.pyw
    exit /b
)
echo FolderMap requires Python 3.10 or newer.
echo Please install Python from https://www.python.org/downloads/windows/
pause
