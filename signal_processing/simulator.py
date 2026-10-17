"""Deterministic raw UART fixture; no target hardware required."""

from .capture import Channel, SignalCapture, integer
from .uart import UARTSettings


def simulate_uart(data: bytes, settings=UARTSettings(115200), *, sample_rate_hz=4_000_000,
                  channel_bit=0, capture_id="simulated-boot"):
    integer(sample_rate_hz, "sample_rate_hz", 1)
    integer(channel_bit, "channel_bit")
    if channel_bit > 31 or sample_rate_hz / settings.baud < 4:
        raise ValueError("unsupported bit or insufficient sampling rate")
    if type(data) is not bytes or any(v >= 1 << settings.data_bits for v in data):
        raise ValueError("payload does not fit UART data width")
    segments = [(1, 3.0)]
    for value in data:
        bits = [(value >> b) & 1 for b in range(settings.data_bits)]
        segments.extend([(0, 1)] + [(bit, 1) for bit in bits])
        if settings.parity != "none":
            segments.append((sum(bits) % 2 ^ (settings.parity == "odd"), 1))
        segments.append((1, settings.stop_bits))
    segments.append((1, 3))
    samples = bytearray()
    elapsed = 0.0
    width = channel_bit // 8 + 1
    for level, duration in segments:
        elapsed += duration * sample_rate_hz / settings.baud
        target = round(elapsed)
        word = ((level ^ settings.inverted) << channel_bit).to_bytes(width, "little")
        samples.extend(word * (target - len(samples) // width))
    return SignalCapture(1, capture_id, sample_rate_hz, 1,
                         (Channel(channel_bit, "target_tx", "SIM_ONLY"),), width,
                         len(samples) // width, bytes(samples))
