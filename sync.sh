#!/bin/bash
set -euo pipefail

# Load Meta-specific env vars
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [[ -f "$SCRIPT_DIR/.env.meta" ]]; then
  set -a; source "$SCRIPT_DIR/.env.meta"; set +a
fi

# ── Configuration ──────────────────────────────────────────────────
DEVVM="${DEVVM_HOST:?Set DEVVM_HOST in .env.meta}"
REMOTE_DIR="${DEVVM_REMOTE_DIR:-/home/$USER/sandbox-bench}"
LOCAL_DIR="$SCRIPT_DIR"

# SSH multiplexing — one Duo auth, reused for every subsequent command
SOCKET_DIR="$HOME/.ssh/sockets"
SOCKET="$SOCKET_DIR/devvm-%r@%h:%p"
mkdir -p "$SOCKET_DIR"

SSH_OPTS=(
  -o ControlMaster=auto
  -o ControlPath="$SOCKET"
  -o ControlPersist=3600        # keep connection alive 1 hour
  -o ServerAliveInterval=60
  -o ServerAliveCountMax=3
)

RSYNC_EXCLUDES=(
  --exclude='.git/'
  --exclude='__pycache__/'
  --exclude='*.pyc'
  --exclude='.env'
  --exclude='.env.meta'
  --exclude='venv/'
  --exclude='.claude/'
  --exclude='node_modules/'
)

# ── Helpers ────────────────────────────────────────────────────────
usage() {
  cat <<EOF
Usage: $(basename "$0") <command>

Commands:
  push        Sync entire local project → devvm  (overwrites remote)
  pull        Sync only changed files devvm → local
  connect     Open a persistent SSH connection (manual pre-warm)
  disconnect  Close the persistent SSH connection
  status      Show whether a multiplexed connection is active
  ssh         Open an interactive shell over the existing connection

Examples:
  ./sync.sh push          # upload your local tree to the devvm
  ./sync.sh ssh           # work on devvm — no extra auth needed
  # ... do work on devvm ...
  ./sync.sh pull          # bring back only what changed
  ./sync.sh disconnect    # clean up when done
EOF
  exit 1
}

ensure_connection() {
  if ! ssh "${SSH_OPTS[@]}" -O check "$DEVVM" 2>/dev/null; then
    echo "Opening SSH connection to $DEVVM (Duo auth required)..."
    ssh "${SSH_OPTS[@]}" -fN "$DEVVM"
    echo "Connection established."
  fi
}

# ── Commands ───────────────────────────────────────────────────────
cmd_push() {
  ensure_connection
  echo "Pushing local → $DEVVM:$REMOTE_DIR ..."

  # Ensure remote directory exists
  ssh "${SSH_OPTS[@]}" "$DEVVM" "mkdir -p '$REMOTE_DIR'"

  rsync -avz --delete \
    "${RSYNC_EXCLUDES[@]}" \
    -e "ssh ${SSH_OPTS[*]}" \
    "$LOCAL_DIR/" \
    "$DEVVM:$REMOTE_DIR/"

  echo "Push complete."
}

cmd_pull() {
  ensure_connection
  echo "Pulling changes $DEVVM:$REMOTE_DIR → local ..."

  rsync -avz \
    "${RSYNC_EXCLUDES[@]}" \
    -e "ssh ${SSH_OPTS[*]}" \
    "$DEVVM:$REMOTE_DIR/" \
    "$LOCAL_DIR/"

  echo "Pull complete."
}

cmd_connect() {
  ensure_connection
  echo "Persistent connection is active. It will stay open for 1 hour."
}

cmd_disconnect() {
  ssh "${SSH_OPTS[@]}" -O exit "$DEVVM" 2>/dev/null && echo "Disconnected." || echo "No active connection."
}

cmd_status() {
  if ssh "${SSH_OPTS[@]}" -O check "$DEVVM" 2>/dev/null; then
    echo "Connection to $DEVVM is ACTIVE."
  else
    echo "No active connection to $DEVVM."
  fi
}

cmd_ssh() {
  ensure_connection
  echo "Opening shell on $DEVVM ..."
  ssh "${SSH_OPTS[@]}" -t "$DEVVM" "cd '$REMOTE_DIR' && exec \$SHELL -l"
}

# ── Main ───────────────────────────────────────────────────────────
case "${1:-}" in
  push)       cmd_push ;;
  pull)       cmd_pull ;;
  connect)    cmd_connect ;;
  disconnect) cmd_disconnect ;;
  status)     cmd_status ;;
  ssh)        cmd_ssh ;;
  *)          usage ;;
esac
