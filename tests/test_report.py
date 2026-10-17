import json
import unittest
from dataclasses import replace
from signal_processing.report import analyze_capture
from signal_processing.simulator import simulate_uart


class ReportTests(unittest.TestCase):
    def test_lossless_payload_and_evidence(self):
        payload = b"Boot\r\n\x80\xff"
        capture = simulate_uart(payload)
        report = analyze_capture(capture, "target_tx", bauds=[115200], limit=20, source="simulated")
        report = json.loads(json.dumps(report, allow_nan=False))
        candidate = next(c for c in report["candidates"] if bytes.fromhex(c["data_hex"]) == payload)
        self.assertFalse(report["analysis"]["settings_confirmed"])
        self.assertEqual(candidate["byte_count"], len(payload))
        for i, frame in enumerate(candidate["frames"]):
            self.assertEqual(frame["value"], payload[i])
            self.assertEqual(frame["byte_index"], i)
            self.assertEqual(capture.levels("target_tx")[frame["start_sample"]], 0)
            self.assertGreater(frame["end_sample_exclusive"], frame["start_sample"])
            self.assertLessEqual(frame["end_sample_exclusive"], capture.sample_count)

    def test_quality_and_no_candidates(self):
        c = simulate_uart(b"A")
        c = replace(c, samples=b"\x01" * c.sample_count, overflowed=True, completion="aborted")
        report = analyze_capture(c, "target_tx")
        self.assertEqual(report["candidates"], [])
        self.assertTrue(report["capture"]["overflowed"])
        self.assertEqual(report["capture"]["completion"], "aborted")
        self.assertEqual(report["signal"]["transition_count"], 0)
        self.assertTrue(any("overflowed" in w for w in report["warnings"]))
        self.assertTrue(any("No UART" in w for w in report["warnings"]))
        with self.assertRaises(ValueError):
            analyze_capture(c, "missing")
