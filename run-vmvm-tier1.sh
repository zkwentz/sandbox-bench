#!/bin/bash
# Run VMVM training_batch suite (Tier 1 only = 256 sandboxes)
set -euo pipefail

# Load Meta-specific env vars
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [[ -f "$SCRIPT_DIR/.env.meta" ]]; then
  set -a; source "$SCRIPT_DIR/.env.meta"; set +a
else
  echo "ERROR: .env.meta not found. Copy .env.meta.example → .env.meta and fill in your values." >&2
  exit 1
fi

BENCH="$SCRIPT_DIR/venv-devvm/bin/sandbox-bench"
OUTDIR="$SCRIPT_DIR/results"

echo "=== VMVM Training Batch - Tier 1 (256 sandboxes) ==="
echo ""

VMVM_TENANT_ID="${VMVM_TENANT_ID_BASIC:?Set VMVM_TENANT_ID_BASIC in .env.meta}" "$BENCH" run -p vmvm \
  -s training_batch \
  -n 1 \
  -o "$OUTDIR/vmvm-training-batch.json"

echo ""
echo "=== Done ==="
echo "Results: $OUTDIR/vmvm-training-batch.json"
