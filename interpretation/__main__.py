"""Interpret a saved analysis report using Gemini."""
import argparse
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--model", help="Override GEMINI_MODEL")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        from .agent import interpret_report, format_interpretation
        if args.report.stat().st_size > 10_000_000:
            raise ValueError("Input report exceeds 10 MB")
        report = json.loads(args.report.read_text(encoding="utf-8-sig"))
        result = interpret_report(report, model=args.model)
        text = format_interpretation(result)
        if args.output_dir:
            args.output_dir.mkdir(parents=True, exist_ok=True)
            (args.output_dir / "interpretation.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
            (args.output_dir / "interpretation.txt").write_text(text + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2) if args.json else text)
    except ImportError:
        print('Install agent dependencies: python -m pip install -e ".[agent]"', file=sys.stderr)
        return 1
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        # Avoid echoing report contents or credentials through parse diagnostics.
        known_errors = {"Gemini returned invalid findings or unverifiable evidence",
                        "Set GEMINI_API_KEY in the environment or repo-root .env.local"}
        message = str(exc) if isinstance(exc, RuntimeError) or str(exc) in known_errors else "Invalid input or configuration; check the report format and file paths."
        print(message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
