#!/usr/bin/env python3
"""
Unit Tests for SRE Safety Circuit Breaker & Anti-Flapping Engine
"""

import time
import unittest

from aiops_operator.circuit_breaker import CircuitBreaker


class TestCircuitBreaker(unittest.TestCase):

    def setUp(self):
        # 2 remediations within 2 seconds, 1 second cooldown
        self.cb = CircuitBreaker(max_remediations=2, window_seconds=2, cooldown_seconds=1)

    def test_initial_state_permitted(self):
        """Verify new workloads are permitted to remediate immediately."""
        allowed, reason = self.cb.can_remediate("default", "web-app")
        self.assertTrue(allowed)
        self.assertIn("Permitted", reason)

    def test_remediation_threshold_and_tripping(self):
        """Verify circuit breaker trips after exceeding max allowed actions."""
        ns, workload = "prod", "api-gateway"
        
        # Action 1: Permitted
        allowed, _ = self.cb.can_remediate(ns, workload)
        self.assertTrue(allowed)
        self.cb.record_remediation(ns, workload)

        # Action 2: Permitted
        allowed, _ = self.cb.can_remediate(ns, workload)
        self.assertTrue(allowed)
        self.cb.record_remediation(ns, workload)

        # Action 3: Exceeds threshold -> Must trip!
        allowed, reason = self.cb.can_remediate(ns, workload)
        self.assertFalse(allowed)
        self.assertIn("Circuit breaker TRIPPED", reason)

    def test_cooldown_and_half_open_recovery(self):
        """Verify circuit breaker resets after cooldown expiration."""
        ns, workload = "prod", "auth-service"
        self.cb.record_remediation(ns, workload)
        self.cb.record_remediation(ns, workload)
        
        # Trip it
        allowed, _ = self.cb.can_remediate(ns, workload)
        self.assertFalse(allowed)

        # Wait for cooldown to expire
        time.sleep(1.1)

        # Should be permitted again
        allowed, reason = self.cb.can_remediate(ns, workload)
        self.assertTrue(allowed)

    def test_manual_reset(self):
        """Verify SRE engineer can manually reset a tripped circuit breaker."""
        ns, workload = "payments", "checkout"
        self.cb.record_remediation(ns, workload)
        self.cb.record_remediation(ns, workload)
        self.cb.can_remediate(ns, workload) # Trip

        # Manually reset
        self.cb.reset(ns, workload)
        allowed, _ = self.cb.can_remediate(ns, workload)
        self.assertTrue(allowed)


if __name__ == "__main__":
    unittest.main()
