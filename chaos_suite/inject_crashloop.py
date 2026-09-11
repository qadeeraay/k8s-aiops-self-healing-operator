#!/usr/bin/env python3
"""
Chaos Engineering Fault Injector: CrashLoopBackOff (Fatal Unhandled Exception)
Injects poison-pill configuration causing immediate process crash upon container startup.
"""

import argparse
import json
import logging
import subprocess

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("chaos.crashloop")


def inject_crashloop_cluster(namespace: str, deployment_name: str):
    """Patches deployment to trigger CrashLoopBackOff."""
    logger.info(f"Injecting Poison Pill Crash into {namespace}/{deployment_name}...")
    patch = {
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "payment-api",
                            "env": [{"name": "CHAOS_CRASH", "value": "true"}]
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
        logger.info(f"✓ Injected CrashLoopBackOff trigger into {deployment_name}.")
    else:
        logger.error(f"Failed to inject crash: {res.stderr}")


def get_mock_crashloop_pod_payload(namespace: str = "aiops-demo", workload: str = "payment-gateway") -> dict:
    """Returns a mock pod dictionary representing a CrashLoopBackOff pod."""
    return {
        "metadata": {
            "name": f"{workload}-6f89cb44-crash01",
            "namespace": namespace,
            "labels": {"app": workload, "tier": "backend"}
        },
        "status": {
            "phase": "Running",
            "container_statuses": [
                {
                    "name": "payment-api",
                    "ready": False,
                    "restart_count": 5,
                    "state": {
                        "waiting": {
                            "reason": "CrashLoopBackOff",
                            "message": "back-off 40s restarting failed container=payment-api pod=payment-gateway-6f89cb44"
                        }
                    },
                    "last_state": {
                        "terminated": {
                            "exit_code": 1,
                            "reason": "Error",
                            "message": "Process failed with unhandled exception"
                        }
                    }
                }
            ]
        }
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Inject CrashLoopBackOff fault into target deployment")
    parser.add_argument("--namespace", default="aiops-demo", help="Target namespace")
    parser.add_argument("--deployment", default="payment-gateway", help="Target deployment")
    args = parser.parse_args()

    inject_crashloop_cluster(args.namespace, args.deployment)
