"""
Prometheus SRE Telemetry & MTTR Exporter
Exposes real-time reliability telemetry, MTTR histograms, and remediation counters
for Prometheus scraping and Grafana executive dashboards.
"""

import logging
from typing import Optional

logger = logging.getLogger("aiops.telemetry")

try:
    from prometheus_client import Counter, Histogram, start_http_server
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False


# Prometheus Metrics Definitions
if PROMETHEUS_AVAILABLE:
    REMEDIATIONS_TOTAL = Counter(
        "aiops_remediations_total",
        "Total automated remediation actions executed by the AIOps operator",
        ["namespace", "workload", "root_cause", "action"],
    )

    MTTR_HISTOGRAM = Histogram(
        "aiops_mttr_seconds",
        "Mean Time to Resolution (MTTR) in seconds from incident detection to recovery",
        ["namespace", "workload", "root_cause"],
        buckets=(0.5, 1.0, 2.5, 5.0, 8.0, 10.0, 15.0, 30.0, 60.0, 120.0),
    )

    CIRCUIT_BREAKER_TRIPPED = Counter(
        "aiops_circuit_breaker_tripped_total",
        "Total times the safety circuit breaker tripped to prevent flapping",
        ["namespace", "workload"],
    )

    INCIDENTS_ESCALATED = Counter(
        "aiops_incidents_escalated_total",
        "Total incidents escalated to human on-call engineers after guardrail limits",
        ["namespace", "workload", "reason"],
    )
else:
    REMEDIATIONS_TOTAL = None
    MTTR_HISTOGRAM = None
    CIRCUIT_BREAKER_TRIPPED = None
    INCIDENTS_ESCALATED = None


class TelemetryExporter:
    """Manages Prometheus SRE metrics recording and HTTP server startup."""

    @staticmethod
    def record_remediation(namespace: str, workload: str, root_cause: str, action: str, mttr_seconds: float) -> None:
        """Records a successful autonomous remediation and its MTTR."""
        if PROMETHEUS_AVAILABLE and REMEDIATIONS_TOTAL and MTTR_HISTOGRAM:
            try:
                REMEDIATIONS_TOTAL.labels(
                    namespace=namespace,
                    workload=workload,
                    root_cause=root_cause,
                    action=action,
                ).inc()
                MTTR_HISTOGRAM.labels(
                    namespace=namespace,
                    workload=workload,
                    root_cause=root_cause,
                ).observe(mttr_seconds)
            except Exception as e:
                logger.warning(f"Failed to record Prometheus metrics: {e}")
        logger.info(
            f"[SRE Telemetry] {namespace}/{workload} resolved in {mttr_seconds:.2f}s | "
            f"Cause: {root_cause} | Action: {action}"
        )

    @staticmethod
    def record_circuit_breaker_trip(namespace: str, workload: str) -> None:
        """Records a circuit breaker trip event."""
        if PROMETHEUS_AVAILABLE and CIRCUIT_BREAKER_TRIPPED:
            try:
                CIRCUIT_BREAKER_TRIPPED.labels(namespace=namespace, workload=workload).inc()
            except Exception as e:
                logger.warning(f"Failed to record circuit breaker metric: {e}")
        logger.warning(f"[SRE Telemetry] Circuit breaker tripped for {namespace}/{workload}")

    @staticmethod
    def record_escalation(namespace: str, workload: str, reason: str) -> None:
        """Records an incident escalation to human on-call."""
        if PROMETHEUS_AVAILABLE and INCIDENTS_ESCALATED:
            try:
                INCIDENTS_ESCALATED.labels(namespace=namespace, workload=workload, reason=reason).inc()
            except Exception as e:
                logger.warning(f"Failed to record escalation metric: {e}")
        logger.warning(f"[SRE Telemetry] Incident escalated to on-call for {namespace}/{workload} ({reason})")

    @staticmethod
    def start_server(port: int = 8000) -> bool:
        """Starts the Prometheus metrics HTTP server."""
        if PROMETHEUS_AVAILABLE:
            try:
                start_http_server(port)
                logger.info(f"Prometheus metrics exporter started on port {port} (:8000/metrics)")
                return True
            except Exception as e:
                logger.error(f"Failed to start Prometheus server on port {port}: {e}")
                return False
        return False
