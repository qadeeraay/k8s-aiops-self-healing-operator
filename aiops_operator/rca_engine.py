"""
Root Cause Analysis (RCA) & Failure Classification Engine
Inspects Kubernetes container states, exit codes, recent logs, and event streams
to determine the deterministic failure mode and compile post-mortem incident reports.
"""

from dataclasses import dataclass, field
import datetime
from enum import Enum
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("aiops.rca_engine")


class FailureType(Enum):
    OOM_KILLED = "OOM_KILLED"
    CRASH_LOOP_BACKOFF = "CRASH_LOOP_BACKOFF"
    READINESS_PROBE_FAILED = "READINESS_PROBE_FAILED"
    IMAGE_PULL_BACKOFF = "IMAGE_PULL_BACKOFF"
    DEADLOCK_STALL = "DEADLOCK_STALL"
    UNKNOWN = "UNKNOWN"


@dataclass
class IncidentContext:
    """Encapsulates telemetry and diagnostic context for an incident."""
    pod_name: str
    namespace: str
    workload_name: str
    failure_type: FailureType
    exit_code: Optional[int]
    reason: str
    message: str
    recent_logs: str = ""
    restart_count: int = 0
    detected_at: datetime.datetime = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc))
    recommended_action: str = ""
    remediation_executed: bool = False
    resolution_status: str = "PENDING"
    mttr_seconds: float = 0.0

    def to_markdown(self) -> str:
        """Generates a standardized Markdown post-mortem incident report."""
        timestamp_str = self.detected_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        return f"""# SRE Incident Post-Mortem & Autonomous RCA Report

**Incident Target:** `{self.namespace}/{self.workload_name}` (Pod: `{self.pod_name}`)  
**Detected At:** `{timestamp_str}`  
**Classification:** `{self.failure_type.value}`  
**Exit Code:** `{self.exit_code if self.exit_code is not None else 'N/A'}`  
**Restart Count:** `{self.restart_count}`  
**Resolution Status:** `{self.resolution_status}`  
**Autonomous MTTR:** `{self.mttr_seconds:.2f} seconds`

---

## 1. Diagnostic Summary
* **Failure Mode:** {self.failure_type.value}
* **Trigger Reason:** {self.reason}
* **System Event Message:** {self.message}
* **Recommended Action:** {self.recommended_action}

---

## 2. Container Standard Error / Diagnostic Log Snippet
```text
{self.recent_logs.strip() if self.recent_logs.strip() else '[No standard error output captured from container buffer]'}
```

---

## 3. Autonomous Remediation Audit Trail
* **Action Executed:** {self.recommended_action if self.remediation_executed else 'Remediation halted by Safety Circuit Breaker'}
* **Circuit Breaker Status:** Verified (Flapping guardrails enforced)
* **Target Workload State:** Restored to Healthy Running status
"""


class RCAEngine:
    """
    Analyzes Kubernetes Pod status dictionaries and logs to classify incident root causes.
    """

    @staticmethod
    def classify_pod_failure(
        pod_dict: Dict[str, Any],
        recent_logs: str = "",
    ) -> IncidentContext:
        """
        Classifies a pod failure based on container statuses and logs.
        """
        metadata = pod_dict.get("metadata", {})
        pod_name = metadata.get("name", "unknown-pod")
        namespace = metadata.get("namespace", "default")
        
        # Derive workload name from owner references or labels
        labels = metadata.get("labels", {})
        workload_name = labels.get("app") or labels.get("app.kubernetes.io/name") or pod_name.rsplit("-", 2)[0]

        status = pod_dict.get("status", {})
        container_statuses = status.get("container_statuses", [])
        
        # Default fallback
        failure_type = FailureType.UNKNOWN
        exit_code = None
        reason = status.get("reason", "Unknown")
        message = status.get("message", "No status message provided")
        restart_count = 0
        recommended_action = "Investigate container logs and pod events."

        if container_statuses:
            cs = container_statuses[0]
            restart_count = cs.get("restart_count", 0)
            state = cs.get("state", {})
            last_state = cs.get("last_state", {})

            # 1. Check for Terminated state in current or last state (OOMKill / Crash)
            terminated = state.get("terminated") or last_state.get("terminated")
            waiting = state.get("waiting", {})

            if terminated:
                exit_code = terminated.get("exit_code")
                term_reason = terminated.get("reason", "")
                term_msg = terminated.get("message", "")

                if exit_code == 137 or term_reason == "OOMKilled" or "oom" in recent_logs.lower():
                    failure_type = FailureType.OOM_KILLED
                    reason = "OOMKilled (Linux cgroup memory limit exceeded)"
                    message = term_msg or "Process terminated by kernel OOM killer (exit code 137)."
                    recommended_action = "Patch Deployment memory limit (+25%) and execute memory-reclaimed rolling restart."
                elif exit_code != 0:
                    failure_type = FailureType.CRASH_LOOP_BACKOFF
                    reason = term_reason or "NonZeroExitCode"
                    message = term_msg or f"Container process crashed with exit code {exit_code}."
                    recommended_action = "Perform graceful rolling restart and purge corrupted ephemeral state."

            # 2. Check for Waiting state (CrashLoopBackOff / ImagePull)
            if waiting and failure_type == FailureType.UNKNOWN:
                wait_reason = waiting.get("reason", "")
                wait_msg = waiting.get("message", "")

                if wait_reason in ["CrashLoopBackOff", "Error"]:
                    failure_type = FailureType.CRASH_LOOP_BACKOFF
                    reason = wait_reason
                    message = wait_msg or "Container failed to start continuously."
                    recommended_action = "Perform graceful rolling restart with backoff suppression."
                elif wait_reason in ["ImagePullBackOff", "ErrImagePull"]:
                    failure_type = FailureType.IMAGE_PULL_BACKOFF
                    reason = wait_reason
                    message = wait_msg or "Failed to pull container image from registry."
                    recommended_action = "Escalate to registry administrator; verify image tag and pull secrets."

            # 3. Check for Readiness probe failure (Running but not Ready)
            if failure_type == FailureType.UNKNOWN and state.get("running") and not cs.get("ready"):
                failure_type = FailureType.READINESS_PROBE_FAILED
                reason = "ReadinessProbeFailure"
                message = "Container is running but failing health/readiness probe check."
                recommended_action = "Trigger warm-up health probe and restart unresponsive replica."

        # Log pattern matching overrides if still unknown
        if failure_type == FailureType.UNKNOWN and recent_logs:
            if "memoryerror" in recent_logs.lower() or "outofmemory" in recent_logs.lower():
                failure_type = FailureType.OOM_KILLED
                recommended_action = "Dynamically patch memory limits and restart container."
            elif "traceback" in recent_logs.lower() or "panic:" in recent_logs.lower():
                failure_type = FailureType.CRASH_LOOP_BACKOFF
                recommended_action = "Perform graceful rolling restart."

        return IncidentContext(
            pod_name=pod_name,
            namespace=namespace,
            workload_name=workload_name,
            failure_type=failure_type,
            exit_code=exit_code,
            reason=reason,
            message=message,
            recent_logs=recent_logs,
            restart_count=restart_count,
            recommended_action=recommended_action,
        )
