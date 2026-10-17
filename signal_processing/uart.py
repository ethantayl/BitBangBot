"""Rank UART hypotheses. Scores are heuristics, never probabilities."""

from dataclasses import dataclass
from .capture import SignalCapture, integer


@dataclass(frozen=True)
class UARTSettings:
    baud: float
    data_bits: int = 8
    parity: str = "none"
    stop_bits: float = 1
    inverted: bool = False

    def __post_init__(self):
        import math
        if type(self.baud) not in (int, float) or not math.isfinite(self.baud) or self.baud <= 0:
            raise ValueError("baud must be positive and finite")
        if type(self.data_bits) is not int or self.data_bits not in (5, 6, 7, 8):
            raise ValueError("data_bits must be 5..8")
        if self.parity not in ("none", "even", "odd") or self.stop_bits not in (1, 1.5, 2):
            raise ValueError("unsupported UART framing")
        if type(self.inverted) is not bool:
            raise ValueError("inverted must be boolean")


@dataclass(frozen=True)
class Decode:
    settings: UARTSettings
    data: bytes
    start_samples: tuple[int, ...]
    framing_errors: int
    parity_errors: int
    truncated_frames: int
    score: float


def decode(capture: SignalCapture, channel: str, settings: UARTSettings) -> Decode:
    levels = [v ^ settings.inverted for v in capture.levels(channel)]
    period = capture.sample_rate_hz / settings.baud
    if period < 4:
        raise ValueError("UART analysis requires at least four samples per bit")
    data, starts = [], []
    framing = parity_errors = truncated = 0
    i = 1  # A low first sample cannot establish a start edge.
    while i < len(levels):
        if levels[i-1] != 1 or levels[i] != 0:
            i += 1
            continue
        start = i
        parity_count = settings.parity != "none"
        stop_start = 1 + settings.data_bits + parity_count
        end = start + (stop_start + settings.stop_bits) * period
        if end > len(levels):
            truncated += 1
            break
        at = lambda bit: levels[int(start + bit * period)]
        if at(0.5) != 0:
            i += 1
            continue
        bits = [at(1.5 + b) for b in range(settings.data_bits)]
        value = sum(b << n for n, b in enumerate(bits))
        if parity_count:
            expected = sum(bits) % 2 ^ (settings.parity == "odd")
            parity_errors += at(1.5 + settings.data_bits) != expected
        # Check throughout the stop interval, including the final half bit.
        checks = [stop_start + 0.25, stop_start + 0.5, stop_start + settings.stop_bits - 0.25]
        framing += any(at(p) != 1 for p in checks)
        data.append(value)
        starts.append(start)
        # Preserve the next start edge even with nonintegral sample periods.
        i = max(start + 1, int(end))
    count = len(data)
    printable = sum(v in (9, 10, 13) or 32 <= v < 127 for v in data)
    score = ((count - framing - parity_errors) / count + 0.15 * printable / count
             + min(count, 32) / 320 - truncated / (count + 1)) if count else -1.0
    return Decode(settings, bytes(data), tuple(starts), framing, parity_errors, truncated, score)


def infer(capture, channel, *, bauds=None, limit=10):
    integer(limit, "limit", 1)
    levels = capture.levels(channel)
    edges = [i for i in range(1, len(levels)) if levels[i] != levels[i-1]]
    if len(edges) < 2:
        return []
    if bauds is None:
        common = [1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600]
        # Runs are integer multiples of bit time; include measured fundamentals.
        from collections import Counter
        runs = Counter(b-a for a, b in zip(edges, edges[1:]))
        measured = [capture.sample_rate_hz / width for width, _ in runs.most_common(8)]
        bauds = common + [b for b in measured if all(abs(b-c)/c > .02 for c in common)]
    results = []
    for baud in bauds:
        for inverted in (False, True):
            for bits in (8, 7, 6, 5):
                for parity in ("none", "even", "odd"):
                    for stop in (1, 1.5, 2):
                        settings = UARTSettings(baud, bits, parity, stop, inverted)
                        if capture.sample_rate_hz / baud >= 4:
                            result = decode(capture, channel, settings)
                            if result.data:
                                results.append(result)
    return sorted(results, key=lambda r: r.score, reverse=True)[:limit]
