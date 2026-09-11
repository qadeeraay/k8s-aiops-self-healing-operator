#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

echo "Checking Python module syntax..."
python3 -m py_compile $(find aiops_operator chaos_suite tests -name "*.py")

echo "Running unit and guardrail assertion tests..."
python3 -m unittest discover -s tests -v

echo "Running chaos simulation..."
python3 chaos_suite/run_demo_simulation.py

echo "All operator validation checks passed."
