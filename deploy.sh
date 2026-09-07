#!/usr/bin/env bash
# ==============================================================================
# Autonomous Kubernetes Self-Healing AIOps Operator - Verification Runner
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

echo "================================================================================"
echo "    AUTONOMOUS KUBERNETES SELF-HEALING AIOPS OPERATOR VERIFICATION RUNNER"
echo "================================================================================"

echo -e "\n[*] Step 1: Checking Python syntax across all modules..."
python3 -m py_compile $(find aiops_operator chaos_suite tests -name "*.py")
echo "✓ Python syntax verified."

echo -e "\n[*] Step 2: Running Unit & Guardrail Assertion Tests..."
python3 -m unittest discover -s tests -v

echo -e "\n[*] Step 3: Executing Chaos Fault-Injection & Self-Healing Simulation..."
python3 chaos_suite/run_demo_simulation.py

echo -e "\n================================================================================"
echo " [✓] ALL AIOPS GATES VERIFIED: Autonomous Self-Healing & MTTR Reduction Confirmed"
echo "================================================================================"
