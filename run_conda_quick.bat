@echo off
setlocal

rem Sa'ei quick launcher: the same as run_conda.bat minus the dependency sync
rem ("conda env update -f environment.yml --prune"), which is what makes that
rem script slow -- conda re-resolves and diffs the whole environment on every
rem launch, even when nothing changed (often 10-60+ seconds by itself). This
rem one just activates the existing "job-agent" env, starts Ollama when .env
rem uses it, then the API and the scheduler, and opens the dashboard once the
rem API answers.
rem
rem Use run_conda.bat instead whenever a DEPENDENCY has changed -- this script
rem does NOT sync them. That means environment.yml *or* requirements.txt:
rem environment.yml only says "pip install -r requirements.txt", so package
rem changes actually live in requirements.txt. A missing package usually shows
rem up as an unexplained deprecation warning or an ImportError from a provider
rem class, not as an obvious "not installed" error -- so if something looks
rem stale after a git pull, run run_conda.bat once before debugging anything.

rem Always run from this script's own folder, regardless of where it's launched from.
cd /d "%~dp0"

set "API_PORT=8001"
set "API_URL=http://localhost:%API_PORT%"
set "OLLAMA_URL=http://localhost:11434"

where conda >nul 2>nul
if errorlevel 1 (
    echo Conda was not found on PATH.
    echo Install Miniconda/Anaconda, or open an "Anaconda Prompt" and run this script from there.
    pause
    exit /b 1
)

rem ---- 1. Environment (must already exist -- no sync here) -----------------
conda env list | findstr /b /c:"job-agent " >nul
if errorlevel 1 (
    echo The "job-agent" conda environment doesn't exist yet.
    echo Run run_conda.bat once first to create it, then use this script from then on.
    pause
    exit /b 1
)

if not exist ".env" (
    echo No .env found -- copying .env.example. Edit .env with your real settings, then run this again.
    copy ".env.example" ".env" >nul
    notepad ".env"
    pause
    exit /b 0
)

rem ---- 2. Port check --------------------------------------------------------
rem Another app on the same port makes uvicorn exit at once with "address
rem already in use" -- say so here instead of leaving a dead API window.
netstat -ano | findstr /r /c:":%API_PORT% .*LISTENING" >nul
if not errorlevel 1 (
    curl -s -m 3 "%API_URL%/api/status" | findstr /c:"llm_provider" >nul
    if not errorlevel 1 (
        echo Sa'ei is already running on %API_URL% -- opening the dashboard.
        start "" "%API_URL%/"
        exit /b 0
    )
    echo Port %API_PORT% is taken by another program, so the Sa'ei API can't start.
    echo Close that program, or change the port in this script and in frontend\config.js.
    pause
    exit /b 1
)

rem ---- 3. Ollama (only when .env uses it) -----------------------------------
rem Embeddings and/or the LLM run locally when .env says "ollama". Without it
rem the search still runs, but semantic matching is skipped.
findstr /r /i /c:"^LLM_PROVIDER=ollama" /c:"^EMBEDDING_PROVIDER=ollama" ".env" >nul
if not errorlevel 1 call :start_ollama

rem ---- 4. API + scheduler ---------------------------------------------------
echo Starting the Sa'ei API on %API_URL% ...
start "Sa'ei - API" cmd /k "conda activate job-agent && python -m uvicorn api:app --host 127.0.0.1 --port %API_PORT%"

echo Starting the scheduler (daily/weekly triggers) ...
start "Sa'ei - Scheduler" cmd /k "conda activate job-agent && python scheduler.py"

rem If those windows immediately close or show "'conda' is not recognized" /
rem "CondaError: Run 'conda init'", conda hasn't been initialized for a plain
rem Command Prompt yet -- run `conda init cmd.exe` once from an Anaconda
rem Prompt, restart your terminal, then run this script again.

rem Wait until the API actually answers (up to ~60s: first start loads the
rem models and the database) rather than guessing with a fixed delay.
echo Waiting for the API to come up...
set /a tries=0
:wait_api
curl -s -m 2 "%API_URL%/api/status" >nul 2>nul
if not errorlevel 1 goto api_up
set /a tries+=1
if %tries% geq 30 (
    echo The API didn't answer after a minute -- check the "Sa'ei - API" window for errors.
    echo Opening the dashboard anyway; it will connect once the API is up.
    goto open_dashboard
)
"%SystemRoot%\System32\timeout.exe" /t 2 /nobreak >nul
goto wait_api

:api_up
echo API is up.

:open_dashboard
echo Opening the dashboard...
start "" "%API_URL%/"

echo.
echo Sa'ei is running (conda env: job-agent, no dependency sync).
echo   - API and scheduler are in the two new console windows -- close them to stop.
echo   - The dashboard opened in your browser and reads from %API_URL%.
echo.
endlocal
exit /b 0

rem ---- helpers ---------------------------------------------------------------
:start_ollama
curl -s -m 2 "%OLLAMA_URL%/api/version" >nul 2>nul
if not errorlevel 1 (
    echo Ollama is already running.
    exit /b 0
)
set "OLLAMA_EXE="
for /f "delims=" %%i in ('where ollama 2^>nul') do if not defined OLLAMA_EXE set "OLLAMA_EXE=%%i"
if not defined OLLAMA_EXE if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" set "OLLAMA_EXE=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
if not defined OLLAMA_EXE (
    echo .env uses Ollama, but Ollama isn't installed -- semantic matching will be skipped.
    echo Install it from https://ollama.com, or change the provider in Settings.
    exit /b 0
)
echo Starting Ollama...
start "Sa'ei - Ollama" /min "%OLLAMA_EXE%" serve
set /a otries=0
:wait_ollama
"%SystemRoot%\System32\timeout.exe" /t 1 /nobreak >nul
curl -s -m 2 "%OLLAMA_URL%/api/version" >nul 2>nul
if not errorlevel 1 (
    echo Ollama is up.
    exit /b 0
)
set /a otries+=1
if %otries% lss 15 goto wait_ollama
echo Ollama didn't answer after 15 seconds -- the search will run without semantic matching.
exit /b 0
