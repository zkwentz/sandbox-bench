#!/bin/bash
# sandbox-bench runner script

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Activate venv
source "$SCRIPT_DIR/venv/bin/activate"

# Load API keys
source "$SCRIPT_DIR/.env"

# Run benchmark
sandbox-bench run "$@"
