"""Bounded simulator capture/interpretation loop: python -m interpretation.session."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys

from .actions import validate_action
from .agent import interpret_report, format_interpretation
from signal_processing.report import analyze_capture
from signal_processing.simulator import simulate_uart

BOOT = b"U-Boot demo\r\nHit any key to stop autoboot: 3\r\n"


class SimulatorBackend:
    """Each call generates a fresh fixture; no target reboot or real I/O occurs."""
    source = "simulated"

    def __init__(self):
        self.sequence = 0

    def capture(self, action):
        if action.action != "capture" or action.channel != "target_tx":
            raise ValueError("Simulator supports only target_tx passive captures")
        self.sequence += 1
        c = simulate_uart(BOOT, capture_id=f"simulated-{self.sequence:03d}")
        count = action.sample_count
        # Pad with idle samples or take a contiguous prefix. Neither inserts gaps.
        raw = (c.samples + b"\x01" * max(0, count - c.sample_count))[:count]
        return replace(c, samples=raw, sample_count=count)


def compare_reports(previous, current):
    a, b = previous["candidates"], current["candidates"]
    matches = [{"previous_candidate": x["candidate_id"], "current_candidate": y["candidate_id"]}
               for x in a for y in b if x["data_hex"] == y["data_hex"]]
    return {"previous_capture_id": previous["capture"]["capture_id"],
            "current_capture_id": current["capture"]["capture_id"],
            "same_leading_bytes": bool(a and b and a[0]["data_hex"] == b[0]["data_hex"]),
            "same_leading_settings": bool(a and b and a[0]["settings"] == b[0]["settings"]),
            "matching_payload_candidates": matches,
            "settings_confirmed": False,
            "limitation": "Matching simulated fixtures do not confirm hardware settings or message meaning."}


def run_session(output_dir, *, interpreter=interpret_report, max_captures=1, model=None, backend=None):
    if type(max_captures) is not int or not 0 <= max_captures <= 3:
        raise ValueError("max_captures must be 0..3")
    backend = backend or SimulatorBackend()
    if backend.source != "simulated":
        raise ValueError("Hardware capture execution is not implemented")
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=False)
    journal = {"schema_version": 1, "source": "simulated", "status": "running",
               "max_followup_captures": max_captures, "followup_captures_executed": 0,
               "hardware_actions_executed": False, "steps": []}

    def save(path, obj):
        path.write_text(json.dumps(obj, indent=2), encoding="utf-8")

    save(directory / "session.json", journal)
    previous = None
    action = validate_action({"action": "capture", "channel": "target_tx", "sample_count": 20000,
                              "reason": "Initial simulated passive capture"},
                             {"signal": {"channel": {"name": "target_tx"}}})
    try:
        for step in range(max_captures + 1):
            capture = backend.capture(action)
            if journal["steps"]:
                journal["steps"][-1]["action_status"] = "executed_in_simulator"
            folder = directory / f"step-{step:02d}"
            folder.mkdir()
            save(folder / "capture.json", capture.to_dict())
            report = analyze_capture(capture, action.channel, source="simulated")
            save(folder / "analysis.json", report)
            comparison = compare_reports(previous, report) if previous else None
            if comparison is not None:
                save(folder / "comparison.json", comparison)
            entry = {"step": step, "capture_id": capture.capture_id, "artifacts": folder.name,
                     "status": "captured"}
            journal["steps"].append(entry)
            journal["followup_captures_executed"] = step
            save(directory / "session.json", journal)
            context = {"remaining_captures": max_captures - step, "comparison": comparison}
            result = interpreter(report, model=model, session_context=context)
            # Revalidate at the execution boundary, even if a custom interpreter is used.
            action = validate_action(result["findings"]["proposed_next_action"], report)
            save(folder / "interpretation.json", result)
            (folder / "interpretation.txt").write_text(format_interpretation(result) + "\n", encoding="utf-8")
            entry.update(status="interpreted", requested_action=action.model_dump())
            if action.action == "stop":
                entry["action_status"] = "stopped"
                journal["status"] = "agent_stopped"
                break
            if step == max_captures:
                entry["action_status"] = "blocked_by_capture_budget"
                journal["status"] = "capture_budget_reached"
                break
            entry["action_status"] = "validated_for_simulator"
            save(directory / "session.json", journal)
            previous = report
    except Exception:
        journal["status"] = "failed"
        # Keep partial captures/reports and a sanitized failure marker.
        save(directory / "session.json", journal)
        raise
    save(directory / "session.json", journal)
    return journal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True, help="New directory for this session")
    parser.add_argument("--max-captures", type=int, choices=range(4), default=1,
                        help="Maximum follow-up captures after the initial capture (0..3)")
    parser.add_argument("--model")
    args = parser.parse_args()
    try:
        result = run_session(args.output_dir, max_captures=args.max_captures, model=args.model)
    except Exception as exc:
        print(str(exc) if isinstance(exc, RuntimeError) else
              "Session failed: check the new output directory, response validation, and configuration. Partial artifacts are retained.", file=sys.stderr)
        return 1
    print("BitBangBot | Simulated experiment loop")
    for entry in result["steps"]:
        print(f"  Step {entry['step']}: {entry['capture_id']} -> analysis -> interpretation")
        print(f"    Model requested: {entry['requested_action']['action']}")
    print(f"Status: {result['status']} | Follow-up captures: {result['followup_captures_executed']}")
    print(f"Saved evidence: {args.output_dir.resolve()}")
    print("No hardware connected. Matching fixtures do not confirm UART settings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
