#!/usr/bin/env bash
# Sa'ei launcher for macOS and Linux -- the counterpart of run.bat.
#
#   ./run.sh            set up on first run, then start Sa'ei
#   ./run.sh --no-open  same, without opening the browser
#
# First run: creates .venv, installs requirements.txt and copies .env.example
# to .env. Every run: starts the API on http://localhost:8001 -- which also
# serves the dashboard and runs the daily schedule -- and opens the browser.
# Ctrl+C stops everything.
set -euo pipefail
cd "$(dirname "$0")"

PORT="${SAEI_PORT:-8001}"
URL="http://localhost:${PORT}/"

PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    PYTHON="$candidate"; break
  fi
done
if [ -z "$PYTHON" ]; then
  echo "Sa'ei needs Python 3.10 or newer (3.11+ recommended). Install it from https://www.python.org/downloads/" >&2
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "First run: creating .venv with $("$PYTHON" --version) ..."
  "$PYTHON" -m venv .venv
  .venv/bin/pip install --upgrade pip -q
  echo "Installing requirements (a few minutes, once) ..."
  .venv/bin/pip install -r requirements.txt -q
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example. Nothing is sent while DRY_RUN=true (the default)."
fi

if ! grep -qE '^LLM_PROVIDER=(gemini|groq|openrouter)' .env && ! command -v ollama >/dev/null 2>&1; then
  echo "Note: LLM_PROVIDER is ollama but Ollama isn't installed. Install it (https://ollama.com) and run"
  echo "      'ollama pull qwen3:4b', or pick Gemini/Groq/OpenRouter on the Settings page."
fi

mkdir -p data cv cv_output credentials

if [ "${1:-}" != "--no-open" ]; then
  ( sleep 3
    if command -v open >/dev/null 2>&1; then open "$URL"
    elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1
    fi ) &
fi

echo "Starting Sa'ei on $URL  (Ctrl+C to stop)"
# One process: the dashboard, the API and the 08:00 / 20:00 / Monday schedule.
# The environment variable wins over .env, so this needs no edit there.
RUN_SCHEDULER_IN_API=true exec .venv/bin/python -m uvicorn api:app --host 127.0.0.1 --port "$PORT"
