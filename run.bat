@echo off
setlocal

rem Always run from this script's own folder, regardless of where it's launched from.
cd /d "%~dp0"

if not exist ".venv" (
    echo Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo Could not create a virtual environment. Is Python installed and on PATH?
        pause
        exit /b 1
    )
)

echo Checking dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet

if not exist ".env" (
    echo No .env found -- copying .env.example. Edit .env with your real settings, then run this again.
    copy ".env.example" ".env" >nul
    notepad ".env"
    pause
    exit /b 0
)

echo Starting dashboard API on http://localhost:8001 ...
start "Job Agent - API" cmd /k .venv\Scripts\python.exe -m uvicorn api:app --host 127.0.0.1 --port 8001

echo Starting scheduler (daily/weekly triggers) ...
start "Job Agent - Scheduler" cmd /k .venv\Scripts\python.exe scheduler.py

echo Waiting for the API to come up...
timeout /t 3 /nobreak >nul

echo Opening dashboard...
start "" "http://localhost:8001/"

echo.
echo Job Application Agent is running.
echo   - API + Scheduler are in the two new console windows -- close them to stop.
echo   - Dashboard: http://localhost:8001/
echo.
endlocal
