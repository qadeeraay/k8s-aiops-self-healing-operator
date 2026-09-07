#!/usr/bin/env python3
"""
Unit Tests for Autonomous Remediation Actions & Runbook Execution
"""

import unittest

from aiops_operator.remediator import Remediator


class TestRemediationActions(unittest.TestCase):

    def setUp(self):
        # Initialized without live Kubernetes API clients (runs in safe simulation mode)
        self.remediator = Remediator(apps_v1_api=None, core_v1_api=None)

    def test_restart_deployment_simulation(self):
        """Verify rolling restart generates proper annotation and succeeds."""
        success, msg = self.remediator.restart_deployment("default", "order-api")
        self.assertTrue(success)
        self.assertIn("restartedAt=", msg)

    def test_scale_memory_limit_simulation(self):
        """Verify memory scaling calculates +25% overhead correctly."""
        success, msg = self.remediator.scale_memory_limit("prod", "payment-service", multiplier=1.25, default_base_mi=256)
        self.assertTrue(success)
        self.assertIn("320Mi", msg)

    def test_delete_unhealthy_pod_simulation(self):
        """Verify pod deletion simulation executes cleanly."""
        success, msg = self.remediator.delete_unhealthy_pod("aiops-demo", "payment-gateway-xyz")
        self.assertTrue(success)
        self.assertIn("Deleted pod", msg)


    def test_scale_memory_limit_with_mock_api(self):
        """Verify memory scaling safely extracts memory limits from deployment mocks."""
        class MockContainer:
            name = "payment-api"
            resources = type("Res", (), {"limits": {"memory": "256Mi"}})()

        class MockDeployment:
            spec = type("Spec", (), {
                "template": type("Template", (), {
                    "spec": type("PodSpec", (), {
                        "containers": [MockContainer()]
                    })()
                })()
            })()

        class MockAppsV1:
            def __init__(self):
                self.patched_body = None

            def read_namespaced_deployment(self, name, namespace):
                return MockDeployment()

            def patch_namespaced_deployment(self, name, namespace, body):
                self.patched_body = body
                return True

        mock_api = MockAppsV1()
        remediator = Remediator(apps_v1_api=mock_api)
        success, msg = remediator.scale_memory_limit("aiops-demo", "payment-gateway", multiplier=1.25)
        self.assertTrue(success)
        self.assertIn("320Mi", msg)
        self.assertEqual(mock_api.patched_body["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"]["memory"], "320Mi")


if __name__ == "__main__":
    unittest.main()
