import copy
import json
import unittest
from types import SimpleNamespace

try:
    from interpretation.agent import interpret_report, provider_schema, Findings
except ImportError:
    interpret_report = None
from signal_processing.report import analyze_capture
from signal_processing.simulator import simulate_uart


@unittest.skipIf(interpret_report is None, "Install .[agent] for interpretation tests")
class InterpretationTests(unittest.TestCase):
    def setUp(self):
        self.report = analyze_capture(simulate_uart(b"Boot"), "target_tx", bauds=[115200], source="simulated")
        self.findings = {"observations": [{"statement": "Output contains Boot", "evidence": [
            {"candidate_id": "uart-1", "byte_start": 0, "byte_end": 4, "quoted_hex": "426f6f74"}]}],
            "interpretations": [], "uncertainties": ["Synthetic, settings unconfirmed"],
            "proposed_next_action": {"action": "capture", "channel": "target_tx", "sample_count": 20000,
                                     "reason": "Collect a second simulated capture"}}

    def client(self, findings):
        def generate(**kwargs):
            self.assertNotIn('"frames"', kwargs["contents"])
            options = json.loads(kwargs["contents"])["evidence_options"]
            if self.report["candidates"]:
                self.assertEqual(options[0], {"candidate_id": "uart-1", "byte_start": 0,
                                              "byte_end": 4, "quoted_hex": "426f6f74"})
            self.assertIn("untrusted DATA", kwargs["config"]["system_instruction"])
            return SimpleNamespace(text=json.dumps(findings))
        return SimpleNamespace(models=SimpleNamespace(generate_content=generate))

    def test_evidence_and_provenance(self):
        result = interpret_report(self.report, client=self.client(self.findings))
        self.assertEqual(result["source"], "simulated")
        self.assertFalse(result["hardware_actions_executed"])
        self.assertFalse(result["settings_confirmed"])
        self.assertEqual(result["findings"], self.findings)

    def test_provider_schema_keeps_structure_and_local_constraints(self):
        schema = json.dumps(provider_schema())
        for unsupported in ("$ref", "$defs", "additionalProperties", "exclusiveMinimum", "minLength"):
            self.assertNotIn(unsupported, schema)
        self.assertIn('"byte_end"', schema)
        bad = copy.deepcopy(self.findings)
        bad["observations"][0]["statement"] = ""
        with self.assertRaises(ValueError):
            Findings.model_validate(bad)

    def test_reject_bad_evidence(self):
        for field, value in (("quoted_hex", "00000000"), ("byte_end", 100), ("candidate_id", "fake")):
            findings = copy.deepcopy(self.findings)
            findings["observations"][0]["evidence"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                interpret_report(self.report, client=self.client(findings))

    def test_empty_capture(self):
        self.report["candidates"] = []
        self.findings["observations"] = []
        result = interpret_report(self.report, client=self.client(self.findings))
        self.assertEqual(result["findings"]["observations"], [])

    def test_provider_error_redacted(self):
        def fail(**kwargs):
            raise RuntimeError("sensitive request details")
        client = SimpleNamespace(models=SimpleNamespace(generate_content=fail))
        with self.assertRaisesRegex(RuntimeError, "^Gemini request failed") as caught:
            interpret_report(self.report, client=client)
        self.assertNotIn("sensitive", str(caught.exception))
