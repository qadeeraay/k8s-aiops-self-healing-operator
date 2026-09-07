#!/usr/bin/env python3
"""
Unit Tests for Root Cause Analysis (RCA) & Failure Classification Engine
"""

import unittest

from aiops_operator.rca_engine import FailureType, RCAEngine


class TestRCAEngine(unittest.TestCase):

    def test_classify_oom_killed_exit_code_137(self):
        """Verify exit code 137 correctly classified as OOM_KILLED."""
        pod_dict = {
            "metadata": {"name": "worker-pod-1", "namespace": "aiops", "labels": {"app": "worker"}},
            "status": {
                "container_statuses": [
                    {
                        "name": "worker",
                        "ready": False,
                        "restart_count": 2,
                        "state": {"terminated": {"exit_code": 137, "reason": "OOMKilled"}},
                    }
                ]
            },
        }
        incident = RCAEngine.classify_pod_failure(pod_dict)
        self.assertEqual(incident.failure_type, FailureType.OOM_KILLED)
        self.assertEqual(incident.exit_code, 137)
        self.assertIn("memory limit", incident.recommended_action.lower())

    def test_classify_crashloop_waiting_reason(self):
        """Verify waiting state CrashLoopBackOff is classified properly."""
        pod_dict = {
            "metadata": {"name": "web-pod-9", "namespace": "prod", "labels": {"app": "web"}},
            "status": {
                "container_statuses": [
                    {
                        "name": "web",
                        "ready": False,
                        "restart_count": 4,
                        "state": {"waiting": {"reason": "CrashLoopBackOff", "message": "back-off restarting"}},
                    }
                ]
            },
        }
        incident = RCAEngine.classify_pod_failure(pod_dict)
        self.assertEqual(incident.failure_type, FailureType.CRASH_LOOP_BACKOFF)
        self.assertIn("restart", incident.recommended_action.lower())

    def test_classify_readiness_probe_failure(self):
        """Verify running but unready container is classified as READINESS_PROBE_FAILED."""
        pod_dict = {
            "metadata": {"name": "cache-pod-3", "namespace": "cache", "labels": {"app": "cache"}},
            "status": {
                "container_statuses": [
                    {
                        "name": "cache",
                        "ready": False,
                        "restart_count": 0,
                        "state": {"running": {"started_at": "2026-09-07T08:00:00Z"}},
                    }
                ]
            },
        }
        incident = RCAEngine.classify_pod_failure(pod_dict)
        self.assertEqual(incident.failure_type, FailureType.READINESS_PROBE_FAILED)

    def test_markdown_post_mortem_generation(self):
        """Verify Markdown report formats all essential incident details."""
        pod_dict = {
            "metadata": {"name": "auth-pod-1", "namespace": "sec", "labels": {"app": "auth"}},
            "status": {
                "container_statuses": [
                    {
                        "name": "auth",
                        "ready": False,
                        "restart_count": 1,
                        "state": {"terminated": {"exit_code": 137, "reason": "OOMKilled"}},
                    }
                ]
            },
        }
        incident = RCAEngine.classify_pod_failure(pod_dict, recent_logs="Fatal: out of memory allocating 512MB")
        md = incident.to_markdown()
        self.assertIn("# SRE Incident Post-Mortem", md)
        self.assertIn("OOM_KILLED", md)
        self.assertIn("Fatal: out of memory", md)


if __name__ == "__main__":
    unittest.main()
