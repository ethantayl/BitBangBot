"""Versioned, simultaneous samples; packed bytes are little endian per sample."""

from dataclasses import dataclass
import base64


def integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


@dataclass(frozen=True)
class Channel:
    bit: int
    name: str
    pin: str

    def __post_init__(self):
        integer(self.bit, "bit")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("channel name is required")
        if not isinstance(self.pin, str) or not self.pin.strip():
            raise ValueError("physical pin is required")


@dataclass(frozen=True)
class SignalCapture:
    schema_version: int
    capture_id: str
    clock_hz: int
    sample_period_ticks: int
    channels: tuple[Channel, ...]
    bytes_per_sample: int
    sample_count: int
    samples: bytes
    trigger_sample_index: int | None = None
    overflowed: bool = False
    completion: str = "complete"

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("unsupported schema_version")
        if not isinstance(self.capture_id, str) or not self.capture_id.strip():
            raise ValueError("capture_id is required")
        for name in ("clock_hz", "sample_period_ticks", "bytes_per_sample"):
            integer(getattr(self, name), name, 1)
        integer(self.sample_count, "sample_count")
        if type(self.samples) is not bytes:
            raise ValueError("samples must be immutable bytes")
        if len(self.samples) != self.sample_count * self.bytes_per_sample:
            raise ValueError("sample byte length does not match metadata")
        if type(self.channels) is not tuple or not self.channels or not all(isinstance(c, Channel) for c in self.channels):
            raise ValueError("channels must be a nonempty tuple of Channel")
        for attr in ("bit", "name", "pin"):
            if len({getattr(c, attr) for c in self.channels}) != len(self.channels):
                raise ValueError(f"duplicate channel {attr}")
        if any(c.bit >= 8 * self.bytes_per_sample for c in self.channels):
            raise ValueError("channel bit exceeds sample width")
        if self.trigger_sample_index is not None:
            integer(self.trigger_sample_index, "trigger_sample_index")
            if self.trigger_sample_index >= self.sample_count:
                raise ValueError("trigger outside capture")
        if type(self.overflowed) is not bool:
            raise ValueError("overflowed must be boolean")
        if self.completion not in ("complete", "aborted", "timeout"):
            raise ValueError("invalid completion")

    @property
    def sample_rate_hz(self):
        return self.clock_hz / self.sample_period_ticks

    def levels(self, channel):
        c = next((c for c in self.channels if c.name == channel), None)
        if c is None:
            raise ValueError(f"unknown channel: {channel}")
        w = self.bytes_per_sample
        return [(int.from_bytes(self.samples[i:i+w], "little") >> c.bit) & 1
                for i in range(0, len(self.samples), w)]

    def to_dict(self):
        return {**self.__dict__, "channels": [c.__dict__ for c in self.channels],
                "samples": base64.b64encode(self.samples).decode("ascii")}

    @classmethod
    def from_dict(cls, obj):
        try:
            values = dict(obj)
            values["channels"] = tuple(Channel(**c) for c in values["channels"])
            values["samples"] = base64.b64decode(values["samples"], validate=True)
            return cls(**values)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid SignalCapture: {exc}") from exc
