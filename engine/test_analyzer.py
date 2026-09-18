"""
Unit tests for the Kubernetes AIOps Heuristic Policy Analyzer.
"""
import unittest
from engine.analyzer import evaluate


class TestTelemetryPolicyAnalyzer(unittest.TestCase):
    def test_impending_oom_kill(self):
        res = evaluate({"memory_working_set_percentage": 90.0})
        self.assertEqual(res["action"], "TRIGGER_PREVENTATIVE_RESTART")
        self.assertEqual(res["reason"], "Impending OOMKill")
        self.assertGreaterEqual(res["confidence"], 0.90)

    def test_persistent_crash_loop(self):
        res = evaluate({"container_restart_count": 3, "restart_window_seconds": 300})
        self.assertEqual(res["action"], "ISOLATE_POD")
        self.assertEqual(res["reason"], "Persistent crash loop")
        self.assertEqual(res["confidence"], 0.95)

    def test_suppress_transient_probe_failure(self):
        res = evaluate({"consecutive_probe_failures": 1})
        self.assertEqual(res["action"], "SUPPRESS")
        self.assertEqual(res["reason"], "Transient network jitter")
        self.assertEqual(res["confidence"], 0.85)

    def test_sustained_probe_failure(self):
        res = evaluate({"consecutive_probe_failures": 2})
        self.assertEqual(res["action"], "INVESTIGATE_PROBE_FAILURE")

    def test_nominal_workload(self):
        res = evaluate({"memory_working_set_percentage": 50.0})
        self.assertEqual(res["action"], "MONITOR")
        self.assertEqual(res["confidence"], 1.0)


if __name__ == "__main__":
    unittest.main()
