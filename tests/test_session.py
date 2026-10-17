import json
from pathlib import Path
import tempfile
import unittest

try:
    from interpretation.actions import validate_action
    from interpretation.session import run_session, SimulatorBackend, compare_reports
except ImportError:
    run_session = None


@unittest.skipIf(run_session is None, "Install .[agent] for session tests")
class SessionTests(unittest.TestCase):
    def interpreter(self, report, *, model=None, session_context=None):
        self.contexts.append(session_context)
        action = {"action": "capture", "channel": "target_tx", "sample_count": 20000,
                  "reason": "Compare another capture"}
        return {"source": "simulated", "model": "test-double", "capture_id": report["capture"]["capture_id"],
                "analysis_warnings": report["warnings"], "findings": {"observations": [],
                "interpretations": [], "uncertainties": ["Simulation only"], "proposed_next_action": action}}

    def setUp(self):
        self.contexts = []
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name) / "session"

    def test_bounded_loop_and_evidence(self):
        result = run_session(self.out, interpreter=self.interpreter, max_captures=1)
        self.assertEqual(result["status"], "capture_budget_reached")
        self.assertEqual(len(result["steps"]), 2)
        self.assertEqual(result["followup_captures_executed"], 1)
        self.assertFalse(result["hardware_actions_executed"])
        comparison = json.loads((self.out / "step-01/comparison.json").read_text())
        self.assertTrue(comparison["same_leading_bytes"])
        self.assertTrue(comparison["same_leading_settings"])
        self.assertFalse(comparison["settings_confirmed"])
        self.assertEqual(self.contexts[1]["remaining_captures"], 0)
        self.assertEqual(self.contexts[1]["comparison"], comparison)
        self.assertEqual(result["steps"][0]["action_status"], "executed_in_simulator")
        with self.assertRaises(FileExistsError):
            run_session(self.out, interpreter=self.interpreter)

    def test_stop_executes_no_followup(self):
        def stop(report, **kwargs):
            result = self.interpreter(report, **kwargs)
            result["findings"]["proposed_next_action"] = {"action": "stop", "channel": None,
                "sample_count": None, "reason": "Need real hardware evidence"}
            return result
        result = run_session(self.out, interpreter=stop)
        self.assertEqual(result["status"], "agent_stopped")
        self.assertEqual(result["followup_captures_executed"], 0)

    def test_reject_invalid_requests(self):
        report = {"signal": {"channel": {"name": "target_tx"}}}
        base = {"action": "capture", "channel": "target_tx", "sample_count": 20000, "reason": "Observe"}
        for change in ({"action": "uart.write"}, {"channel": "other"}, {"sample_count": 100001},
                       {"sample_count": True}, {"sample_count": None}, {"extra": "command"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_action({**base, **change}, report)

    def test_failure_retains_capture_without_followup(self):
        def invalid(report, **kwargs):
            result = self.interpreter(report, **kwargs)
            result["findings"]["proposed_next_action"]["action"] = "uart.write"
            return result
        backend = SimulatorBackend()
        with self.assertRaises(ValueError):
            run_session(self.out, interpreter=invalid, backend=backend)
        self.assertEqual(backend.sequence, 1)
        journal = json.loads((self.out / "session.json").read_text())
        self.assertEqual(journal["status"], "failed")
        self.assertTrue((self.out / "step-00/capture.json").exists())

    def test_provider_failure_retains_analysis(self):
        def fail(*args, **kwargs):
            raise RuntimeError("Provider unavailable")
        with self.assertRaises(RuntimeError):
            run_session(self.out, interpreter=fail)
        self.assertTrue((self.out / "step-00/analysis.json").exists())
        self.assertFalse((self.out / "step-01").exists())
