#!/bin/bash
# Compute Engine startup script for Sa'ei, sized for the Always Free e2-micro
# instance (us-west1 / us-central1 / us-east1 only).
#
# Idempotent — it reruns on every VM boot (it's registered as instance
# metadata), which is also how the app updates: reboot, or rerun it with
#   sudo google_metadata_script_runner startup
# Never overwrites .env once it exists, so your real secrets survive reboots;
# it only (re)applies the few settings a server needs (see apply_server_env).
set -euo pipefail

APP_DIR="/opt/job-agent"
REPO_URL="https://github.com/alymaklad/saei.git"
BRANCH="main"
RUN_USER="job-agent"
ENV_FILE="$APP_DIR/.env"

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip git

# ---- swap: 1 GB of RAM is enough to run, not to pip-install and parse PDFs at once
if ! swapon --show | grep -q /swapfile; then
  if [ ! -f /swapfile ]; then
    fallocate -l 1G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
  fi
  swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

id -u "$RUN_USER" &>/dev/null || useradd -m -s /usr/sbin/nologin "$RUN_USER"

# ---- code
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" remote set-url origin "$REPO_URL"
  git -C "$APP_DIR" fetch --quiet origin "$BRANCH"
  git -C "$APP_DIR" checkout --quiet "$BRANCH"
  git -C "$APP_DIR" reset --hard --quiet "origin/$BRANCH"   # tracked files only; .env, data/, cv/ are untracked
else
  git clone --quiet --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi
mkdir -p "$APP_DIR"/{data,cv,cv_output,credentials}
chown -R "$RUN_USER":"$RUN_USER" "$APP_DIR"

sudo -u "$RUN_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$RUN_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip -q
sudo -u "$RUN_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q

# ---- .env
if [ ! -f "$ENV_FILE" ]; then
  cp "$APP_DIR/.env.example" "$ENV_FILE"
  echo ">>> Created $ENV_FILE from .env.example -- fill in your API keys (or upload your own .env)."
fi

set_env() {   # set_env KEY VALUE: replace the KEY= line, or append one
  local key="$1" value="$2"
  if grep -q "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}
get_env() { grep "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- || true; }

apply_server_env() {
  # One process runs the API and the 08:00/20:00/Monday schedule.
  set_env RUN_SCHEDULER_IN_API true
  # No browser on a server: Gmail sign-in happens on your PC, the token is copied here.
  set_env GMAIL_INTERACTIVE_AUTH false
  # The VM clock is UTC; the schedule should follow yours.
  [ -n "$(get_env SCHEDULER_TIMEZONE)" ] || set_env SCHEDULER_TIMEZONE Africa/Cairo
  # 1 GB of RAM can't run Ollama: use a hosted model and hosted embeddings.
  if [ "$(get_env LLM_PROVIDER)" = "ollama" ] || [ -z "$(get_env LLM_PROVIDER)" ]; then
    set_env LLM_PROVIDER gemini
    echo ">>> LLM_PROVIDER was ollama -- switched to gemini (needs GEMINI_API_KEY)."
  fi
  if [ "$(get_env EMBEDDING_PROVIDER)" != "gemini" ]; then
    set_env EMBEDDING_PROVIDER gemini
    echo ">>> EMBEDDING_PROVIDER switched to gemini (needs GEMINI_API_KEY)."
  fi
  # This server is reachable from the internet: never run it without a login.
  if [ -z "$(get_env APP_PASSWORD)" ]; then
    set_env APP_USERNAME saei
    set_env APP_PASSWORD "$(head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)"
    echo ">>> Generated a dashboard login. Read it with:"
    echo ">>>   sudo grep -E '^APP_(USERNAME|PASSWORD)=' $ENV_FILE"
  fi
  chown "$RUN_USER":"$RUN_USER" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
}
apply_server_env

# ---- service: one unit (API + schedule). The old two-unit layout is retired.
if systemctl list-unit-files | grep -q '^job-agent-scheduler.service'; then
  systemctl disable --now job-agent-scheduler || true
  rm -f /etc/systemd/system/job-agent-scheduler.service
fi
cp "$APP_DIR/deploy/gcp/systemd/job-agent-api.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable job-agent-api
systemctl restart job-agent-api

echo ">>> Startup script complete. Check with:"
echo ">>>   systemctl status job-agent-api ; journalctl -u job-agent-api -f"
