"""Plain-text presentation of analysis evidence; no message interpretation."""


def visible_text(data):
    """Escape control bytes so target output cannot control the terminal."""
    return "".join(chr(b) if 32 <= b < 127 else f"\\x{b:02x}" for b in data)


def format_report(report):
    capture = report["capture"]
    rate = capture["sample_rate_hz"]
    lines = ["BitBangBot | UART analysis",
             f"Source: {report['source']}" + (" (no hardware connected)" if report["source"] == "simulated" else ""),
             "", "1. Read the raw capture",
             f"   Capture: {capture['capture_id']} | Channel: {report['signal']['channel']['name']}",
             f"   {capture['sample_count']:,} samples at {rate:,.0f} samples/second",
             f"   Recorded window: {capture['sample_count'] / rate * 1000:.3f} ms",
             f"   Completion: {capture['completion']} | Overflow: {'YES' if capture['overflowed'] else 'no'}",
             "", "2. Try UART settings",
             "   Each row is a hypothesis. Scores are not confidence percentages.",
             "   Rank  Baud        Frame  Polarity  Bytes  Frame/parity/cut errors  Score"]
    for c in report["candidates"]:
        s, e = c["settings"], c["errors"]
        frame = f"{s['data_bits']}{ {'none': 'N', 'even': 'E', 'odd': 'O'}[s['parity']]}{s['stop_bits']:g}"
        lines.append(f"   {c['rank']:>4}  {s['baud']:>10.2f}  {frame:<5}  {'inverted' if s['inverted'] else 'normal':<8}  "
                     f"{c['byte_count']:>5}  {e['framing']}/{e['parity']}/{e['truncated_frames']:<19} {c['score']:.3f}")
    lines.extend(["   Frame = data bits / parity (N=none, E=even, O=odd) / stop bits.",
                  "   Cut errors count incomplete final frames.", "", "3. Read the leading candidate"])
    if report["candidates"]:
        top = report["candidates"][0]
        data = bytes.fromhex(top["data_hex"])
        lines.append("   Decoded text (line breaks normalized; other controls escaped):")
        normalized = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        lines.extend("   | " + visible_text(line) for line in normalized.split(b"\n"))
        lines.append("   Exact bytes, hex: " + data.hex(" "))
        if top["frames"]:
            f = top["frames"][0]
            lines.extend(["", "4. Trace a byte back to the samples",
                          f"   Byte 0: 0x{f['value']:02X} ({visible_text(bytes([f['value']]))})",
                          f"   Samples [{f['start_sample']}, {f['end_sample_exclusive']}) "
                          "(start included, end excluded)",
                          f"   Starts {f['start_time_seconds'] * 1e6:.2f} microseconds after capture sample zero.",
                          "   This range is estimated using this candidate's UART settings."])
        matches = [str(c["rank"]) for c in report["candidates"][1:] if c["data_hex"] == top["data_hex"]]
        if matches:
            lines.append("   Candidates " + ", ".join(matches) + " recover the same bytes with different settings.")
    else:
        lines.append("   No UART candidates found. There is no decoded message to interpret.")
    lines.extend(["", "What remains uncertain"])
    lines.extend("   - " + w for w in report["warnings"])
    lines.extend(["", "Next: compare captures and confirm settings before transmitting.",
                  "This report decodes bytes; message meaning is a separate interpretation step."])
    return "\n".join(lines)
