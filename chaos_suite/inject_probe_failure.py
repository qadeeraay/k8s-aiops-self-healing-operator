#!/usr/bin/env python3
"""
Chaos Engineering Fault Injector: ReadinessProbe Failure (Deadlock / Latency Hang)
Simulates socket hang / thread exhaustion causing Kubernetes ReadinessProbe to fail continuously.
"""

import argparse
import json
import logging
import subprocess
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("chaos.probe")


def inject_probe_failure_cluster(namespace: str, deployment_name: str):
    """Patches deployment to simulate readiness probe failure."""
    logger.info(f"Injecting Health Probe Deadlock into {namespace}/{deployment_name}...")
    patch = {
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "payment-api",
                            "env": [{"name": "CHAOS_DEADLOCK", "value": "true"}]
                        }
                    ]
                }
            }
        }
    }
    cmd = [
        "kubectl", "patch", "deployment", deployment_name,
        "-n", namespace,
        "--type", "strategic",
        "-p", json.dumps(patch)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0:
        logger.info(f"✓ Injected ReadinessProbe failure trigger into {deployment_name}.")
    else:
        logger.error(f"Failed to inject deadlock: {res.stderr}")


def get_mock_probe_failure_payload(namespace: str = "aiops-demo", workload: str = "payment-gateway") -> dict:
    """Returns a mock pod dictionary representing a ReadinessProbe failure."""
    return {
        "metadata": {
            "name": f"{workload}-559d8f6b-probe99",
            "namespace": namespace,
            "labels": {"app": workload, "tier": "backend"}
        },
        "status": {
            "phase": "Running",
            "container_statuses": [
                {
                    "name": "payment-api",
                    "ready": False,
                    "restart_count": 0,
                    "state": {
                        "running": {
                            "started_at": "2026-09-07T08:00:00Z"
                        }
                    }
                }
            ]
        }
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Inject ReadinessProbe failure into target deployment")
    parser.add_argument("--namespace", default="aiops-demo", help="Target namespace")
    parser.add_argument("--deployment", default="payment-gateway", help="Target deployment")
    args = parser.parse_args()

    inject_probe_failure_cluster(args.namespace, args.deployment)
