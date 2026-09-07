"""
Autonomous Kubernetes Self-Healing AIOps Controller
Core control loop that intercepts pod failure events, triages root cause,
evaluates safety circuit breakers, and triggers automated remediation runbooks.
"""

import argparse
import datetime
import logging
import os
import sys
import time
from typing import Any, Dict, Optional

from aiops_operator.circuit_breaker import CircuitBreaker
from aiops_operator.rca_engine import FailureType, IncidentContext, RCAEngine
from aiops_operator.remediator import Remediator
from aiops_operator.telemetry import TelemetryExporter

# Structured logging for controller lifecycle and audit trail
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("aiops.controller")

try:
    from kubernetes import client, config, watch
    K8S_AVAILABLE = True
except ImportError:
    K8S_AVAILABLE = False


class AIOpsController:
    """
    Main Kubernetes Autonomous Controller.
    """

    def __init__(
        self,
        namespace: Optional[str] = None,
        dry_run: bool = False,
        metrics_port: int = 8000,
    ):
        self.namespace = namespace
        self.dry_run = dry_run
        self.circuit_breaker = CircuitBreaker(max_remediations=2, window_seconds=600, cooldown_seconds=300)
        self.rca_engine = RCAEngine()
        
        self.core_v1 = None
        self.apps_v1 = None

        if K8S_AVAILABLE and not dry_run:
            try:
                # Try in-cluster service account first, then fallback to ~/.kube/config
                try:
                    config.load_incluster_config()
                    logger.info("Loaded in-cluster Kubernetes credentials.")
                except Exception:
                    config.load_kube_config()
                    logger.info("Loaded local ~/.kube/config credentials.")
                
                self.core_v1 = client.CoreV1Api()
                self.apps_v1 = client.AppsV1Api()
            except Exception as e:
                logger.warning(f"Kubernetes cluster connection not available: {e}. Running in standalone simulation mode.")

        self.remediator = Remediator(apps_v1_api=self.apps_v1, core_v1_api=self.core_v1)
        self.telemetry = TelemetryExporter()
        self.telemetry.start_server(port=metrics_port)

    def handle_incident(self, pod_dict: Dict[str, Any], recent_logs: str = "") -> IncidentContext:
        """
        Processes a single pod failure incident end-to-end.
        """
        start_time = time.time()
        
        # 1. Classify failure via RCA Engine
        incident = self.rca_engine.classify_pod_failure(pod_dict, recent_logs=recent_logs)
        logger.info(
            f"Detected incident on {incident.namespace}/{incident.workload_name} (Pod: {incident.pod_name}) | "
            f"Classification: {incident.failure_type.value} | Reason: {incident.reason}"
        )

        if incident.failure_type == FailureType.UNKNOWN:
            logger.info(f"Pod {incident.pod_name} is in a healthy or transient state. No action needed.")
            incident.resolution_status = "HEALTHY"
            return incident

        # 2. Evaluate Safety Circuit Breaker (Anti-Flapping)
        can_proceed, reason = self.circuit_breaker.can_remediate(incident.namespace, incident.workload_name)
        if not can_proceed:
            logger.warning(f"REMEDIATION HALTED by Circuit Breaker: {reason}")
            self.telemetry.record_circuit_breaker_trip(incident.namespace, incident.workload_name)
            self.telemetry.record_escalation(incident.namespace, incident.workload_name, reason)
            incident.remediation_executed = False
            incident.resolution_status = "CIRCUIT_BREAKER_TRIPPED_ESCALATED"
            incident.mttr_seconds = time.time() - start_time
            self._write_post_mortem(incident)
            return incident

        # 3. Execute Autonomous Remediation Runbook
        logger.info(f"Executing autonomous runbook for {incident.failure_type.value}...")
        action_executed = ""
        success = False

        if incident.failure_type == FailureType.OOM_KILLED:
            action_executed = "ScaleMemoryAndRestart"
            if not self.dry_run:
                s1, _ = self.remediator.scale_memory_limit(incident.namespace, incident.workload_name, multiplier=1.25)
                s2, _ = self.remediator.restart_deployment(incident.namespace, incident.workload_name)
                success = s1 and s2
            else:
                logger.info(f"[DRY RUN] Would scale memory +25% and restart deployment {incident.workload_name}")
                success = True

        elif incident.failure_type == FailureType.CRASH_LOOP_BACKOFF:
            action_executed = "GracefulRollingRestart"
            if not self.dry_run:
                success, _ = self.remediator.restart_deployment(incident.namespace, incident.workload_name)
            else:
                logger.info(f"[DRY RUN] Would perform rolling restart on {incident.workload_name}")
                success = True

        elif incident.failure_type == FailureType.READINESS_PROBE_FAILED:
            action_executed = "EvictUnresponsivePod"
            if not self.dry_run:
                success, _ = self.remediator.delete_unhealthy_pod(incident.namespace, incident.pod_name)
            else:
                logger.info(f"[DRY RUN] Would evict unresponsive pod {incident.pod_name}")
                success = True

        elif incident.failure_type == FailureType.IMAGE_PULL_BACKOFF:
            action_executed = "EscalateRegistryFailure"
            self.telemetry.record_escalation(incident.namespace, incident.workload_name, "ImagePullBackOff")
            success = False

        mttr = time.time() - start_time

        if success:
            self.circuit_breaker.record_remediation(incident.namespace, incident.workload_name)
            incident.remediation_executed = True
            incident.resolution_status = "RESOLVED"
            incident.mttr_seconds = mttr
            self.telemetry.record_remediation(
                namespace=incident.namespace,
                workload=incident.workload_name,
                root_cause=incident.failure_type.value,
                action=action_executed,
                mttr_seconds=mttr,
            )
            logger.info(f"✓ AUTONOMOUSLY HEALED {incident.namespace}/{incident.workload_name} in {mttr:.2f}s!")
        else:
            incident.remediation_executed = False
            incident.resolution_status = "ESCALATED_TO_ONCALL"
            incident.mttr_seconds = mttr

        # 4. Generate Markdown Post-Mortem Report
        self._write_post_mortem(incident)
        return incident

    def _write_post_mortem(self, incident: IncidentContext) -> str:
        """
        Writes the Markdown post-mortem to the reports directory.
        Anchored to repo root to avoid polluting execution cwd.
        """
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        reports_dir = os.path.join(repo_root, "reports")
        os.makedirs(reports_dir, exist_ok=True)
        filename = f"post_mortem_{incident.namespace}_{incident.workload_name}_{int(time.time())}.md"
        filepath = os.path.join(reports_dir, filename)
        with open(filepath, "w") as f:
            f.write(incident.to_markdown())
        logger.info(f"Generated incident post-mortem report: {filepath}")
        return filepath

    def run_watcher(self) -> None:
        """
        Streams events from the Kubernetes API and triggers the reconciliation loop.
        """
        if not self.core_v1:
            logger.error("Kubernetes client not configured. Cannot run live watcher.")
            return

        w = watch.Watch()
        logger.info(f"Starting AIOps Event Watcher on namespace='{self.namespace or 'all'}'...")

        try:
            stream = (
                self.core_v1.list_namespaced_pod(self.namespace, watch=True)
                if self.namespace
                else self.core_v1.list_pod_for_all_namespaces(watch=True)
            )
            for event in stream:
                pod = event["object"]
                pod_dict = pod.to_dict()
                pod_name = pod.metadata.name
                phase = pod.status.phase

                # Check if container in pod has errors or crash status
                has_error = False
                statuses = pod.status.container_statuses or []
                for cs in statuses:
                    if cs.state.waiting and cs.state.waiting.reason in ["CrashLoopBackOff", "Error", "ImagePullBackOff"]:
                        has_error = True
                    if cs.last_state.terminated and cs.last_state.terminated.exit_code != 0:
                        has_error = True

                if has_error:
                    logger.warning(f"Watcher observed unhealthy pod {pod_name} (Phase: {phase})")
                    recent_logs = ""
                    try:
                        recent_logs = self.core_v1.read_namespaced_pod_log(
                            name=pod_name,
                            namespace=pod.metadata.namespace,
                            tail_lines=30,
                        )
                    except Exception:
                        pass
                    self.handle_incident(pod_dict, recent_logs=recent_logs)
        except KeyboardInterrupt:
            logger.info("Operator stopped by user.")
        except Exception as e:
            logger.error(f"Watcher encountered exception: {e}")


def main():
    parser = argparse.ArgumentParser(description="Autonomous Kubernetes Self-Healing AIOps Operator")
    parser.add_argument("--namespace", default=None, help="Target namespace to watch (default: all)")
    parser.add_argument("--dry-run", action="store_true", help="Log actions without modifying Kubernetes resources")
    parser.add_argument("--metrics-port", type=int, default=8000, help="Prometheus metrics port (default: 8000)")
    args = parser.parse_args()

    controller = AIOpsController(namespace=args.namespace, dry_run=args.dry_run, metrics_port=args.metrics_port)
    controller.run_watcher()


if __name__ == "__main__":
    main()
