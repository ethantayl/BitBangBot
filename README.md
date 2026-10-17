# BitBangBot

BitBangBot is an AI-assisted hardware testing tool built for SwampHacks. It combines a natural-language interface with an FPGA to help users interact with, test, and understand embedded devices in an authorized lab environment.

## How It Works

1. A user describes a hardware testing task through a web dashboard.
2. An AI agent translates the request into structured tool calls.
3. A Python controller validates commands and communicates with the FPGA.
4. The FPGA performs hardware interactions and returns measurements.
5. The AI analyzes the results and presents its findings.

## Hardware

- **FPGA board:** Terasic DE10-Lite with Intel MAX 10
- **Host connection:** Onboard USB-Blaster/JTAG → JTAG-to-Avalon Master Bridge
- **Target interfaces:** UART and GPIO
- **Planned interfaces:** I²C and SPI
- **Target devices:** ESP32, Arduino, and other embedded systems

The FPGA will expose control registers, capture RAM, and target UART RX/TX FIFOs.
A persistent Quartus System Console Tcl bridge will provide a localhost interface
to Python. The FPGA handles precise sampling and timing; Python reads buffered
captures. No external host USB-to-UART adapter is needed. Verify target voltage
compatibility, provide protection/level shifting where needed, and use common ground.

## Software

- Web dashboard for prompts, status, and results
- Gemini API for AI-assisted planning and analysis
- Python hardware controller for command validation and communication
- Custom FPGA logic for protocol handling and signal capture

## Project Status

BitBangBot is a hackathon prototype under development. The HTML dashboard is a
standalone simulation, not connected to hardware. The Python package now provides
a validated capture model, proposed bridge client, UART candidate analysis, and
deterministic simulator. The Tcl server, FPGA design, dashboard integration, AI
command validation, and persistent evidence storage remain to be implemented.

## Python development

Python 3.11+; runtime and tests use only the standard library. From the repository:

```sh
python -m unittest discover -s tests -v
python -m signal_processing
python -m signal_processing --output-dir demo_output
python -m signal_processing --json
```

`analyze_capture(capture, channel)` returns a versioned JSON-ready UART analysis
report: capture metadata and quality, ranked settings, lossless decoded hex, ASCII
display, error counts, and per-byte sample ranges. Settings remain unconfirmed;
scores are heuristics rather than probabilities. ASCII display is for convenience;
use `data_hex` to recover exact decoded bytes. Frame ranges are inferred from each
candidate's timing and use start-inclusive/end-exclusive sample indices, with
times relative to sample zero. Error counts are currently per candidate, not per byte.

The default demo output is a readable walkthrough: capture quality, a candidate
comparison, decoded text, and a byte traced back to its sample range. `--json`
prints the full structured report instead. `--output-dir` also saves `summary.txt`.

The demo explicitly marks its source as simulated. `--output-dir` saves
`capture.json` with original samples and metadata alongside `analysis.json`.
Keep both: the report references the capture ID and SHA-256 of its sample bytes,
but does not embed the raw samples. This is deterministic evidence for a future
agent interpreter; it does not interpret boot messages or confirm UART settings.

`SignalCapture` checks byte length, channel mapping, timing metadata and trigger
bounds, and supports JSON dictionaries with base64 samples. UART analysis ranks
baud/polarity/data/parity/stop hypotheses and reports framing, parity and truncated
frame errors. Scores are heuristic, not confidence probabilities; short, noisy,
constant or undersampled signals may remain ambiguous. Stop lengths can be
indistinguishable when frames have idle gaps. Overflow/completion metadata must
remain attached to any interpretation. Confirm UART settings before using FPGA
UART FIFOs for interactive communication.

The proposed transport and unresolved hardware decisions are in
[docs/bridge-contract.md](docs/bridge-contract.md). No register offsets are assumed.

The demo goal is passive observation of our authorized Orange Pi target's boot
UART, identification of an advertised interruption window, and bootloader
interaction. A Linux shell depends on the installed firmware/recovery setup;
bootloader access alone does not imply shell access. I²C and SPI are future work.

## Message interpretation with Gemini

Install the optional agent dependencies in a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[agent]"
.\.venv\Scripts\python.exe -m signal_processing --output-dir demo_output
.\.venv\Scripts\python.exe -m interpretation demo_output/analysis.json --output-dir demo_output
```

Put `GEMINI_API_KEY=your_key` in repo-root `.env.local` (ignored by Git).
Existing environment variables take precedence. Set `GEMINI_MODEL` there or pass
`--model` to select another structured-output-capable model; the default is
`gemini-3.1-flash-lite`. Interpretation sends the report's decoded candidates and
metadata to Google's Gemini API; raw sample bytes and the API key are not included
in the prompt. Each invocation makes one generation request with automatic retries
disabled and a 45-second request timeout. Transport behavior may affect actual duration.

The readable result separates observations, possible meanings, uncertainties,
and a proposed next step. `--json` prints structured output. `--output-dir` saves
`interpretation.json` and `interpretation.txt`. Every finding must cite an existing
candidate and exact byte range with matching hex; Python rejects invalid citations.
This validates references, not the truth of the model's reasoning. Capture quality,
source provenance, analysis hash and warnings are retained independently of model
claims. No hardware tools are exposed and no proposed action is executed.

The provider receives a basic JSON Schema with references expanded. Strict field,
length, and evidence constraints are enforced locally after generation. Errors
include status-specific guidance without printing provider request details or keys.

The interpretation module uses optional dependencies; signal processing remains
standard-library-only. Run the full suite with the virtual environment's Python;
agent tests skip when those optional dependencies are absent.

### Structured follow-up requests and simulated sessions

Interpretation schema version 2 replaces the old free-text `proposed_next_action`
with a required structured object:

```json
{"action":"capture","channel":"target_tx","sample_count":20000,"reason":"Check repeatability"}
```

The other allowed action is `stop`, with `channel` and `sample_count` both null.
Python rejects unsupported actions, unexpected fields, mismatched channels, and
sample counts outside 1000..100000. Neither action supports target writes or resets.
Existing saved version 1 interpretations remain historical artifacts; regenerate
them to obtain structured requests. The standalone `interpretation` command only
proposes an action. The session command validates and executes simulated captures:

```powershell
.\.venv\Scripts\python.exe -m interpretation.session --output-dir demo_output/my-session --max-captures 1
```

Choose a new output directory for each session. It starts with one simulated
capture and allows 0..3 additional captures (one by default). Each step saves raw
capture, analysis, and interpretation; subsequent steps also save a comparison.
The model receives the comparison and remaining capture budget. Python enforces
the budget even if the model asks to continue, or stops earlier if requested.
`session.json` records requested/executed actions and final status. Partial evidence
survives invalid responses and API failures; failed actions are not retried.

The simulator generates another known boot fixture for each capture request,
padding idle samples or taking a contiguous prefix to meet the requested size.
Matching leading settings or bytes show fixture repeatability, not confirmed
hardware settings. All UART candidates and ambiguity remain available in each
analysis report. The loop never changes `settings_confirmed` to true. Real capture
execution still requires the agreed FPGA arm/status interface and Tcl server.

## Contributors

- Ethan Taylor
- Jack Penn
- Nikola Peric
- Henry Canapp

## Responsible Use

Use BitBangBot only with devices you own or have explicit permission to test. Verify pin assignments and voltage compatibility before connecting hardware.
