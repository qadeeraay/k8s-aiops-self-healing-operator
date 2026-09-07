#!/usr/bin/env python3
"""
Chaos Engineering Fault Injector: Out-Of-Memory (OOMKilled - Exit 137)
Simulates sudden heap allocation / memory leak to trigger Linux cgroup memory limit enforcement.
"""

import argparse
import json
import logging
import subprocess
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("chaos.oom")


def inject_oom_cluster(namespace: str, deployment_name: str):
    """Patches deployment to simulate memory leak."""
    logger.info(f"Injecting Memory Leak into {namespace}/{deployment_name}...")
    patch = {
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "payment-api",
                            "command": ["python3", "-c", "import sys; print('[CHAOS] Allocating 1GB RAM to trigger OOMKill...', file=sys.stderr); x = bytearray(1024*1024*1024)"]
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
        logger.info(f"✓ Injected OOMKill trigger into {deployment_name}. Pod will exit with status 137.")
    else:
        logger.error(f"Failed to inject OOM: {res.stderr}")


def get_mock_oom_pod_payload(namespace: str = "aiops-demo", workload: str = "payment-gateway") -> dict:
    """Returns a mock pod dictionary representing an OOMKilled pod for offline testing."""
    return {
        "metadata": {
            "name": f"{workload}-7c4d8b995-xyz12",
            "namespace": namespace,
            "labels": {"app": workload, "tier": "backend"}
        },
        "status": {
            "phase": "Running",
            "container_statuses": [
                {
                    "name": "payment-api",
                    "ready": False,
                    "restart_count": 3,
                    "state": {
                        "terminated": {
                            "exit_code": 137,
                            "reason": "OOMKilled",
                            "message": "Command terminated by cgroup out-of-memory killer"
                        }
                    }
                }
            ]
        }
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Inject OOMKill fault into target deployment")
    parser.add_argument("--namespace", default="aiops-demo", help="Target namespace")
    parser.add_argument("--deployment", default="payment-gateway", help="Target deployment")
    args = parser.parse_args()

    inject_oom_cluster(args.namespace, args.deployment)
