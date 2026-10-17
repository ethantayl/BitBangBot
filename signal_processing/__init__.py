"""BitBangBot's hardware-independent capture and analysis layer."""

from .capture import Channel, SignalCapture
from .report import analyze_capture

__all__ = ["Channel", "SignalCapture", "analyze_capture"]
