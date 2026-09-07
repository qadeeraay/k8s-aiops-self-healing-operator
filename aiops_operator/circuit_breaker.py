"""
SRE Safety Circuit Breaker & Anti-Flapping Engine
Prevents automated remediation flapping spirals, enforces sliding-window rate limits,
and bounds the blast radius of automated interventions on Kubernetes workloads.
"""

import time
import logging
from typing import Dict, List, Tuple

logger = logging.getLogger("aiops.circuit_breaker")


class CircuitBreaker:
    """
    Implements a sliding-window circuit breaker for Kubernetes automated remediations.
    
    Attributes:
        max_remediations: Maximum allowed automatic actions within the window before tripping.
        window_seconds: Duration of the sliding time window in seconds (default: 600s = 10 mins).
        cooldown_seconds: Minimum cooldown period after tripping before half-open testing (default: 300s).
    """

    def __init__(
        self,
        max_remediations: int = 2,
        window_seconds: int = 600,
        cooldown_seconds: int = 300,
    ):
        self.max_remediations = max_remediations
        self.window_seconds = window_seconds
        self.cooldown_seconds = cooldown_seconds
        
        # History map: workload_key -> List of timestamps (epoch seconds)
        self._history: Dict[str, List[float]] = {}
        
        # Tripped status: workload_key -> trip_timestamp
        self._tripped_at: Dict[str, float] = {}

    def _get_key(self, namespace: str, workload: str) -> str:
        return f"{namespace}/{workload}"

    def can_remediate(self, namespace: str, workload: str) -> Tuple[bool, str]:
        """
        Determines whether a remediation action is permitted for the given workload.
        
        Returns:
            Tuple[bool, str]: (is_allowed, reason_message)
        """
        key = self._get_key(namespace, workload)
        current_time = time.time()

        # Check if circuit is currently tripped
        if key in self._tripped_at:
            time_since_trip = current_time - self._tripped_at[key]
            if time_since_trip < self.cooldown_seconds:
                remaining = int(self.cooldown_seconds - time_since_trip)
                return (
                    False,
                    f"Circuit breaker TRIPPED for {key}. Flapping protection active. "
                    f"Cooldown remaining: {remaining}s. Escalated to human SRE.",
                )
            else:
                # Cooldown expired: Reset to half-open state
                logger.info(f"Circuit breaker cooldown expired for {key}. Resetting to half-open.")
                del self._tripped_at[key]
                self._history[key] = []

        # Prune timestamps outside the sliding window
        timestamps = self._history.get(key, [])
        valid_timestamps = [t for t in timestamps if current_time - t <= self.window_seconds]
        self._history[key] = valid_timestamps

        # Check if threshold reached
        if len(valid_timestamps) >= self.max_remediations:
            self._tripped_at[key] = current_time
            return (
                False,
                f"Circuit breaker TRIPPED for {key}: exceeded {self.max_remediations} "
                f"remediations in {self.window_seconds}s. Halting automated actions to prevent cascading failure.",
            )

        return (True, f"Permitted ({len(valid_timestamps)}/{self.max_remediations} actions used in window)")

    def record_remediation(self, namespace: str, workload: str) -> None:
        """
        Records a successful or executed remediation timestamp against the workload.
        """
        key = self._get_key(namespace, workload)
        current_time = time.time()
        if key not in self._history:
            self._history[key] = []
        self._history[key].append(current_time)
        logger.info(
            f"Recorded remediation for {key}. Total in active window: {len(self._history[key])}/{self.max_remediations}"
        )

    def reset(self, namespace: str, workload: str) -> None:
        """
        Manually resets the circuit breaker for a workload (e.g., after human acknowledgment).
        """
        key = self._get_key(namespace, workload)
        if key in self._history:
            del self._history[key]
        if key in self._tripped_at:
            del self._tripped_at[key]
        logger.info(f"Circuit breaker manually reset for {key}.")
