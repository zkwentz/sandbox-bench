#!/usr/bin/env python3
"""Merge per-provider result files into docs/data.json for the live dashboard.

Usage:
    python scripts/build-dashboard-data.py

Reads full-*.json and vmvm-full.json from results/, merges them into a
single consolidated JSON file at docs/data.json.
"""

import json
import glob
import os

RESULTS_DIR = os.path.join(os.path.dirname(__file__), '..', 'results')
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), '..', 'docs', 'data.json')

# Result files to merge (order determines display order)
RESULT_FILES = [
    'full-blaxel.json',
    'full-codesandbox.json',
    'full-daytona.json',
    'full-e2b.json',
    'full-fly.json',
    'full-modal.json',
    'vmvm-full.json',
]


def main():
    results = []
    providers = []

    for fname in RESULT_FILES:
        path = os.path.join(RESULTS_DIR, fname)
        if not os.path.exists(path):
            print(f"  skip {fname} (not found)")
            continue

        with open(path) as f:
            data = json.load(f)

        for r in data.get('results', []):
            results.append(r)
            providers.append(r['provider'])
            suites = list(r.get('suite_results', {}).keys())
            print(f"  {r['provider']}: {len(suites)} suites, score={r.get('score')}, grade={r.get('grade')}")

    output = {
        'config': {
            'providers': providers,
            'suites': ['full'],
            'agent_mode': False,
            'model': 'claude-opus-4',
            'runs': 3,
        },
        'results': results,
    }

    with open(OUTPUT_PATH, 'w') as f:
        json.dump(output, f, separators=(',', ':'))

    size_kb = os.path.getsize(OUTPUT_PATH) / 1024
    print(f"\nWrote {OUTPUT_PATH} ({size_kb:.0f} KB, {len(results)} providers)")


if __name__ == '__main__':
    main()
