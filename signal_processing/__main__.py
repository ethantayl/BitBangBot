"""Run a simulated boot capture and generate its evidence report."""
import argparse
import json
from pathlib import Path
from .report import analyze_capture
from .simulator import simulate_uart
from .display import format_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, help="Save raw capture and analysis JSON")
    parser.add_argument("--json", action="store_true", help="Print the full JSON report instead of the walkthrough")
    args = parser.parse_args()
    capture = simulate_uart(b"U-Boot demo\r\nHit any key to stop autoboot: 3\r\n")
    report = analyze_capture(capture, "target_tx", source="simulated")
    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "capture.json").write_text(json.dumps(capture.to_dict(), indent=2), encoding="utf-8")
        (args.output_dir / "analysis.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        (args.output_dir / "summary.txt").write_text(format_report(report) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2) if args.json else format_report(report))
    if args.output_dir and not args.json:
        print(f"\nSaved capture.json, analysis.json, and summary.txt in {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
