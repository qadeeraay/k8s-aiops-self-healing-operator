"""
Kubernetes AIOps Heuristic Policy Layer.
Evaluates pod telemetry to distinguish transient jitter from genuine persistent failures.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Dict


def evaluate(telemetry: Dict[str, Any]) -> Dict[str, Any]:
    """
    Evaluates pod telemetry against SRE reliability thresholds.
    Returns: {"action": str, "confidence": float, "reason": str}
    """
    mem = float(telemetry.get("memory_working_set_percentage", telemetry.get("memory_pct", 0.0)))
    restarts = int(telemetry.get("container_restart_count", telemetry.get("restart_count", 0)))
    window = int(telemetry.get("restart_window_seconds", 300))
    probes = int(telemetry.get("consecutive_probe_failures", telemetry.get("probe_failures", 0)))

    if mem >= 90.0:
        return {
            "action": "TRIGGER_PREVENTATIVE_RESTART",
            "confidence": round(min(0.99, 0.90 + (mem - 90.0) / 100.0), 2),
            "reason": "Impending OOMKill",
        }
    if restarts >= 3 and window <= 300:
        return {
            "action": "ISOLATE_POD",
            "confidence": 0.95,
            "reason": "Persistent crash loop",
        }
    if 0 < probes < 2:
        return {
            "action": "SUPPRESS",
            "confidence": 0.85,
            "reason": "Transient network jitter",
        }
    if probes >= 2:
        return {
            "action": "INVESTIGATE_PROBE_FAILURE",
            "confidence": 0.90,
            "reason": "Consecutive probe failures exceeded jitter tolerance",
        }
    return {
        "action": "MONITOR",
        "confidence": 1.00,
        "reason": "Nominal telemetry: workload healthy",
    }


if __name__ == "__main__":
    payload = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read().strip()
    data = json.loads(payload) if payload else {"memory_working_set_percentage": 92.0}
    print(json.dumps(evaluate(data), indent=2))
