"""Proposed JSON-lines transport to a persistent System Console Tcl server.

No register offsets are assumed. A matching Tcl server is not yet implemented.
"""

import json
import socket
import threading
from .capture import SignalCapture, integer


class BridgeError(RuntimeError):
    pass


class BridgeClient:
    def __init__(self, port, *, host="127.0.0.1", timeout=5, max_response_bytes=16*1024*1024):
        if host not in ("127.0.0.1", "::1"):
            raise ValueError("bridge must use a literal loopback address")
        integer(port, "port", 1)
        if port > 65535 or timeout <= 0:
            raise ValueError("invalid port or timeout")
        integer(max_response_bytes, "max_response_bytes", 1)
        self.host, self.port, self.timeout = host, port, timeout
        self.max_response_bytes = max_response_bytes
        self._socket = self._reader = None
        self._id = 0
        self._lock = threading.Lock()

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.close()

    def connect(self):
        self.close()
        self._socket = socket.create_connection((self.host, self.port), self.timeout)
        self._reader = self._socket.makefile("rb")

    def close(self):
        if self._reader:
            self._reader.close()
        if self._socket:
            self._socket.close()
        self._reader = self._socket = None

    def request(self, operation, params=None):
        if operation not in ("hello", "capture.read", "uart.configure", "uart.read", "uart.write"):
            raise ValueError("operation is not allowlisted")
        with self._lock:
            if self._socket is None:
                self.connect()
            self._id += 1
            request = {"version": 1, "id": self._id, "operation": operation, "params": params or {}}
            try:
                self._socket.sendall(json.dumps(request, allow_nan=False).encode() + b"\n")
                line = self._reader.readline(self.max_response_bytes + 1)
                if not line.endswith(b"\n") or len(line) > self.max_response_bytes:
                    raise BridgeError("response disconnected, unterminated, or oversized")
                response = json.loads(line)
                if not isinstance(response, dict) or type(response.get("id")) is not int or response["id"] != self._id or type(response.get("version")) is not int or response["version"] != 1:
                    raise BridgeError("response version/id mismatch")
                if response.get("ok") is not True:
                    raise BridgeError(str(response.get("error", "bridge rejected request")))
                if "result" not in response:
                    raise BridgeError("missing result")
                return response["result"]
            except (OSError, ValueError, BridgeError) as exc:
                self.close()  # No replay: a timed-out write may already have executed.
                raise BridgeError(str(exc)) from exc

    def read_capture(self, capture_id):
        if not isinstance(capture_id, str) or not capture_id:
            raise ValueError("capture_id required")
        result = SignalCapture.from_dict(self.request("capture.read", {"capture_id": capture_id}))
        if result.capture_id != capture_id:
            raise BridgeError("capture identity mismatch")
        return result
