# Proposed bridge contract — not hardware-approved

Python speaks UTF-8 JSON lines over one persistent loopback TCP connection to a
future Tcl server running inside Quartus System Console. System Console owns the
JTAG-to-Avalon master service. No external host USB-to-UART adapter is required.
This repository does not yet contain that server or an FPGA register map.

Request example:

```json
{"version":1,"id":1,"operation":"capture.read","params":{"capture_id":"boot-001"}}
```

Every response echoes integer `version` and `id`, and has either `ok: true` with
`result`, or `ok: false` with `error`. One request is outstanding at a time.
Responses end with a newline. The client bounds response size and socket waiting
time, closes on protocol errors, and never automatically replays requests.

Proposed operations: `hello`, `capture.read`, `uart.configure`, `uart.read`,
`uart.write`. Only capture.read currently has a typed Python wrapper. Generic
requests are an internal transport API, not an AI tool interface. Do not expose
them directly to AI calls. Server-side validation and typed command policies are
still needed before enabling target writes.

`capture.read` returns a SignalCapture dictionary; `samples` is strict base64.
Version 1 contains schema_version, capture_id, clock_hz, sample_period_ticks,
channels (bit/name/pin), bytes_per_sample, sample_count, samples,
trigger_sample_index (null if absent), overflowed, and completion
(complete/aborted/timeout). Each fixed-width little-endian word contains all
simultaneous channel bits. Unmapped bits are ignored. Sample zero is the oldest;
the producer must linearize any circular RAM before returning samples. Python
checks sizes and mapping, but cannot prove chronological order from raw bytes.
Overflow indicates possible loss outside the returned contiguous segment, never
permission to concatenate segments across gaps. Capture metadata must be latched
atomically with RAM ownership held until read completion.

FPGA-team decisions required:

- Avalon address map, word width, alignment, RAM depth and byte ordering.
- Capture arm/status/abort protocol, trigger semantics and overflow behavior.
- Clock source, decimation period and channel-to-header assignments.
- Snapshot ownership, circular-buffer linearization and chunked transfer limits.
- UART configuration acknowledgment, FIFO counts, overrun flags and TX completion.
- Bridge discovery/version negotiation, session ownership and local access policy.

No host-timed polling is used as a substitute for FPGA sampling or UART timing.
