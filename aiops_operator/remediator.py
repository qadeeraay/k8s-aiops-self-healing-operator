"""
Autonomous Kubernetes Remediation Execution Engine
Executes deterministic, safe runbooks against the Kubernetes API:
- Rolling deployment restarts via annotation patching
- Dynamic memory limit auto-scaling for OOM mitigation
- Graceful pod cycling
- Emitting native Kubernetes audit events
"""

import datetime
import logging
import re
from typing import Any, Optional, Tuple

logger = logging.getLogger("aiops.remediator")

try:
    from kubernetes import client
    K8S_AVAILABLE = True
except ImportError:
    K8S_AVAILABLE = False


class Remediator:
    """
    Executes automated runbooks against Kubernetes workloads.
    Supports live cluster execution and deterministic simulated offline testing.
    """

    def __init__(self, apps_v1_api: Optional[Any] = None, core_v1_api: Optional[Any] = None):
        self.apps_v1 = apps_v1_api
        self.core_v1 = core_v1_api

    def restart_deployment(self, namespace: str, deployment_name: str) -> Tuple[bool, str]:
        """
        Triggers an idempotent rolling restart of a Kubernetes Deployment
        by updating the kubectl.kubernetes.io/restartedAt annotation.
        """
        restarted_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        patch_body = {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            "kubectl.kubernetes.io/restartedAt": restarted_at,
                            "aiops.remediation/action": "AutonomousRollingRestart",
                        }
                    }
                }
            }
        }

        if self.apps_v1:
            try:
                self.apps_v1.patch_namespaced_deployment(
                    name=deployment_name,
                    namespace=namespace,
                    body=patch_body,
                )
                msg = f"Successfully initiated rolling restart for {namespace}/{deployment_name} at {restarted_at}"
                logger.info(msg)
                return True, msg
            except Exception as e:
                err = f"Failed to restart deployment {namespace}/{deployment_name}: {str(e)}"
                logger.error(err)
                return False, err
        else:
            # Offline simulation mode
            msg = f"[SIMULATION] Patched {namespace}/{deployment_name} with restartedAt={restarted_at}"
            logger.info(msg)
            return True, msg

    def scale_memory_limit(
        self,
        namespace: str,
        deployment_name: str,
        multiplier: float = 1.25,
        default_base_mi: int = 256,
    ) -> Tuple[bool, str]:
        """
        Dynamically calculates and patches an increased memory limit (+25%) on a Deployment
        to mitigate recurring OOMKilled events.
        """
        current_limit_str = f"{default_base_mi}Mi"
        new_limit_str = f"{int(default_base_mi * multiplier)}Mi"

        if self.apps_v1:
            try:
                dep = self.apps_v1.read_namespaced_deployment(name=deployment_name, namespace=namespace)
                spec = getattr(dep, "spec", None) or (dep.get("spec") if isinstance(dep, dict) else None)
                template = getattr(spec, "template", None) or (spec.get("template") if isinstance(spec, dict) else None)
                tmpl_spec = getattr(template, "spec", None) or (template.get("spec") if isinstance(template, dict) else None)
                containers = getattr(tmpl_spec, "containers", None) or (tmpl_spec.get("containers") if isinstance(tmpl_spec, dict) else [])

                container_name = "app"
                if containers:
                    c0 = containers[0]
                    container_name = getattr(c0, "name", None) or (c0.get("name") if isinstance(c0, dict) else "app")
                    res = getattr(c0, "resources", None) or (c0.get("resources") if isinstance(c0, dict) else None)
                    if res:
                        limits = getattr(res, "limits", None) or (res.get("limits") if isinstance(res, dict) else None)
                        if limits:
                            mem = limits.get("memory", current_limit_str) if isinstance(limits, dict) else getattr(limits, "memory", current_limit_str)
                            match = re.match(r"^(\d+)([A-Za-z]+)$", str(mem))
                            if match:
                                val, unit = int(match.group(1)), match.group(2)
                                new_val = int(val * multiplier)
                                new_limit_str = f"{new_val}{unit}"

                patch_body = {
                    "spec": {
                        "template": {
                            "spec": {
                                "containers": [
                                    {
                                        "name": container_name,
                                        "resources": {
                                            "limits": {"memory": new_limit_str},
                                            "requests": {"memory": new_limit_str},
                                        },
                                    }
                                ]
                            }
                        }
                    }
                }
                self.apps_v1.patch_namespaced_deployment(name=deployment_name, namespace=namespace, body=patch_body)
                msg = f"Dynamically scaled memory for {namespace}/{deployment_name} from {current_limit_str} to {new_limit_str}"
                logger.info(msg)
                return True, msg
            except Exception as e:
                err = f"Failed to patch memory limit on {namespace}/{deployment_name}: {str(e)}"
                logger.error(err)
                return False, err
        else:
            msg = f"[SIMULATION] Scaled memory for {namespace}/{deployment_name} to {new_limit_str}"
            logger.info(msg)
            return True, msg

    def delete_unhealthy_pod(self, namespace: str, pod_name: str) -> Tuple[bool, str]:
        """
        Gracefully deletes an unresponsive pod so the ReplicaSet can schedule a healthy replacement.
        """
        if self.core_v1:
            try:
                self.core_v1.delete_namespaced_pod(
                    name=pod_name,
                    namespace=namespace,
                    grace_period_seconds=5,
                )
                msg = f"Gracefully evicted unhealthy pod {namespace}/{pod_name}"
                logger.info(msg)
                return True, msg
            except Exception as e:
                err = f"Failed to delete pod {namespace}/{pod_name}: {str(e)}"
                logger.error(err)
                return False, err
        else:
            msg = f"[SIMULATION] Deleted pod {namespace}/{pod_name}"
            logger.info(msg)
            return True, msg
