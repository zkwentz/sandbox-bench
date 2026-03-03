#!/bin/bash
# Run VMVM through all sandbox-bench suites with the correct tenant per suite.
#
# Tenant IDs are read from .env.meta (see .env.meta.example).

set -euo pipefail

# Load Meta-specific env vars
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [[ -f "$SCRIPT_DIR/.env.meta" ]]; then
  set -a; source "$SCRIPT_DIR/.env.meta"; set +a
else
  echo "ERROR: .env.meta not found. Copy .env.meta.example → .env.meta and fill in your values." >&2
  exit 1
fi

BENCH="$SCRIPT_DIR/venv/bin/sandbox-bench"
OUTDIR="$SCRIPT_DIR/results"
RUNS="${1:-3}"

echo "=== VMVM sandbox-bench (${RUNS} runs per suite) ==="
echo ""

# 1) basic + performance
echo "--- basic + performance ---"
VMVM_TENANT_ID="${VMVM_TENANT_ID_BASIC:?Set VMVM_TENANT_ID_BASIC in .env.meta}" "$BENCH" run -p vmvm \
  -s basic -s performance \
  -n "$RUNS" \
  -o "$OUTDIR/vmvm-basic-perf.json"
echo ""

# 2) competitive
echo "--- competitive ---"
VMVM_TENANT_ID="${VMVM_TENANT_ID_COMPETITIVE:?Set VMVM_TENANT_ID_COMPETITIVE in .env.meta}" "$BENCH" run -p vmvm \
  -s competitive \
  -n "$RUNS" \
  -o "$OUTDIR/vmvm-competitive.json"
echo ""

# 3) swe
echo "--- swe ---"
VMVM_TENANT_ID="${VMVM_TENANT_ID_SWE:?Set VMVM_TENANT_ID_SWE in .env.meta}" "$BENCH" run -p vmvm \
  -s swe \
  -n "$RUNS" \
  -o "$OUTDIR/vmvm-swe.json"
echo ""

# 4) environment
echo "--- environment ---"
VMVM_TENANT_ID="${VMVM_TENANT_ID_ENVIRONMENT:?Set VMVM_TENANT_ID_ENVIRONMENT in .env.meta}" "$BENCH" run -p vmvm \
  -s environment \
  -n "$RUNS" \
  -o "$OUTDIR/vmvm-environment.json"
echo ""

# 5) Merge all suite results into one combined file
echo "--- Merging results ---"
"$SCRIPT_DIR/venv/bin/python3" - "$OUTDIR" <<'PYEOF'
import json, sys, os

outdir = sys.argv[1]

files = [
    ("vmvm-basic-perf.json",  ["basic", "performance"]),
    ("vmvm-competitive.json", ["competitive"]),
    ("vmvm-swe.json",         ["swe"]),
    ("vmvm-environment.json", ["environment"]),
]

# Collect per-suite data from each run
all_suite_results = {}
all_capabilities = {}
all_trace = []
total_time = 0.0
total_tool_calls = 0
total_friction = 0
total_errors = 0
error_messages = []
cold_start = 0.0
warm_start = None
agent_spawn = None
file_io_tp = None
discoverability = 3.5

for fname, expected_suites in files:
    path = os.path.join(outdir, fname)
    if not os.path.exists(path):
        print(f"  SKIP {fname} (not found)")
        continue

    with open(path) as f:
        data = json.load(f)

    r = data["results"][0]
    print(f"  {fname}: {r['total_time_seconds']:.1f}s, grade {r['grade']}, suites {r['suites_run']}")

    total_time += r["total_time_seconds"]
    total_tool_calls += r["tool_calls"]
    total_friction += r["friction_points"]
    total_errors += r["errors"]
    error_messages.extend(r.get("error_messages", []))
    all_trace.extend(r.get("trace", []))

    for suite_name, phases in r.get("suite_results", {}).items():
        all_suite_results[suite_name] = phases

    for cap, supported in r.get("capabilities", {}).items():
        all_capabilities[cap] = supported

    if r.get("cold_start_seconds", 0) > cold_start:
        cold_start = r["cold_start_seconds"]
    if r.get("warm_start_seconds") is not None:
        warm_start = r["warm_start_seconds"]
    if r.get("agent_spawn_seconds") is not None:
        agent_spawn = r["agent_spawn_seconds"]
    if r.get("file_io_throughput_mbps") is not None:
        file_io_tp = r["file_io_throughput_mbps"]

# Compute capability score
cap_tested = len(all_capabilities)
cap_supported = sum(1 for v in all_capabilities.values() if v)
cap_score = cap_supported / cap_tested if cap_tested > 0 else 0.0

# Build merged result using the scoring module
suites_run = list(all_suite_results.keys())

merged_result = {
    "provider": "vmvm",
    "success": True,
    "total_time_seconds": total_time,
    "tool_calls": total_tool_calls,
    "friction_points": total_friction,
    "errors": total_errors,
    "error_messages": error_messages,
    "estimated_cost_usd": 0.0,
    "sandbox_cost_usd": 0.0,
    "discoverability_score": discoverability,
    "score": 0.0,  # will be recalculated
    "grade": "",
    "trace": all_trace,
    "suites_run": suites_run,
    "suite_results": all_suite_results,
    "capabilities": all_capabilities,
    "capability_score": cap_score,
    "cold_start_seconds": cold_start,
    "warm_start_seconds": warm_start,
    "agent_spawn_seconds": agent_spawn,
    "file_io_throughput_mbps": file_io_tp,
}

# Recalculate score using the same algorithm as sandbox-bench
MAX_TIME = 300; MAX_CALLS = 50; MAX_FRICTION = 5; MAX_ERRORS = 10; MAX_COST = 5.0

def norm(v, mx): return min(1.0, v / mx)

has_caps = bool(all_capabilities)
if has_caps:
    w = {"time": 0.25, "tool_calls": 0.10, "friction": 0.15, "errors": 0.20, "cost": 0.10, "discoverability": 0.10, "capabilities": 0.10}
else:
    w = {"time": 0.30, "tool_calls": 0.15, "friction": 0.15, "errors": 0.20, "cost": 0.10, "discoverability": 0.10}

score = (
    (1 - norm(total_time, MAX_TIME)) * w["time"]
    + (1 - norm(total_tool_calls, MAX_CALLS)) * w["tool_calls"]
    + (1 - norm(total_friction, MAX_FRICTION)) * w["friction"]
    + (1 - norm(total_errors, MAX_ERRORS)) * w["errors"]
    + (1 - norm(0.0, MAX_COST)) * w["cost"]
    + (discoverability / 5.0) * w["discoverability"]
)
if has_caps:
    score += cap_score * w["capabilities"]

score = round(score * 100, 1)
if score >= 85: grade = "A"
elif score >= 70: grade = "B"
elif score >= 55: grade = "C"
elif score >= 40: grade = "D"
else: grade = "F"

merged_result["score"] = score
merged_result["grade"] = grade

combined = {
    "config": {
        "providers": ["vmvm"],
        "suites": ["full"],
        "agent_mode": False,
        "model": "claude-opus-4",
        "runs": 3,
    },
    "results": [merged_result],
}

out_path = os.path.join(outdir, "vmvm-full.json")
with open(out_path, "w") as f:
    json.dump(combined, f, indent=2)

print(f"\n  Merged: {len(suites_run)} suites, {cap_supported}/{cap_tested} capabilities")
print(f"  Score: {score} / Grade: {grade}")
print(f"  Written to {out_path}")
PYEOF

echo ""
echo "=== Done ==="
