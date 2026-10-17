# PenPilot

PenPilot is an AI-assisted hardware testing tool built for SwampHacks. It combines a natural-language interface with an FPGA to help users interact with, test, and understand embedded devices in an authorized lab environment.

## How It Works

1. A user describes a hardware testing task through a web dashboard.
2. An AI agent translates the request into structured tool calls.
3. A Python controller validates commands and communicates with the FPGA.
4. The FPGA performs hardware interactions and returns measurements.
5. The AI analyzes the results and presents its findings.

## Hardware

- **FPGA board:** Terasic DE10-Lite with Intel MAX 10
- **Host connection:** External USB-to-UART adapter with 3.3 V logic
- **Target interfaces:** UART and GPIO
- **Planned interfaces:** I²C and SPI
- **Target devices:** ESP32, Arduino, and other embedded systems

External level shifting and signal protection are used where required by the target device.

## Software

- Web dashboard for prompts, status, and results
- Gemini API for AI-assisted planning and analysis
- Python hardware controller for command validation and communication
- Custom FPGA logic for protocol handling and signal capture

## Project Status

HackPilot is a hackathon prototype under development. Features and interfaces described here represent the intended architecture and may change during implementation.

## Contributors

- Ethan Taylor
- Jack Penn
- Nikola Peric
- Henry Canapp

## Responsible Use

Use HackPilot only with devices you own or have explicit permission to test. Verify pin assignments and voltage compatibility before connecting hardware.