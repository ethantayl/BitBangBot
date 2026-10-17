import json
import socketserver
import threading
import unittest
from dataclasses import replace

from signal_processing.capture import SignalCapture, Channel
from signal_processing.bridge import BridgeClient, BridgeError
from signal_processing.simulator import simulate_uart
from signal_processing.uart import UARTSettings, decode, infer


class CaptureTests(unittest.TestCase):
    def test_roundtrip_and_multibyte_mapping(self):
        capture = simulate_uart(b"Boot\r\n", channel_bit=9)
        self.assertEqual(SignalCapture.from_dict(capture.to_dict()), capture)
        self.assertEqual(decode(capture, "target_tx", UARTSettings(115200)).data, b"Boot\r\n")

    def test_invalid_metadata(self):
        c = simulate_uart(b"A")
        for fields in ({"samples": b""}, {"sample_period_ticks": 0},
                       {"channels": (Channel(8, "tx", "P1"),)},
                       {"channels": (Channel(0, "tx", "P1"), Channel(0, "rx", "P2"))},
                       {"trigger_sample_index": c.sample_count}, {"overflowed": 1},
                       {"sample_count": True}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                replace(c, **fields)


class UARTTests(unittest.TestCase):
    def test_framing_variants(self):
        for bits in (5, 6, 7, 8):
            for parity in ("none", "even", "odd"):
                for inverted in (False, True):
                    for stop in (1, 1.5, 2):
                        s = UARTSettings(57600, bits, parity, stop, inverted)
                        with self.subTest(settings=s):
                            result = decode(simulate_uart(bytes([0, 21, 31]), s), "target_tx", s)
                            self.assertEqual(result.data, bytes([0, 21, 31]))
                            self.assertEqual((result.framing_errors, result.parity_errors), (0, 0))

    def test_errors_and_truncation(self):
        s = UARTSettings(10000, 8, "even")
        c = simulate_uart(b"A", s, sample_rate_hz=100000)
        raw = bytearray(c.samples)
        raw[125] ^= 1  # parity center: idle 30 + 9.5 bits
        raw[135] = 0  # stop center
        r = decode(replace(c, samples=bytes(raw)), "target_tx", s)
        self.assertEqual((r.parity_errors, r.framing_errors), (1, 1))
        r = decode(replace(c, samples=c.samples[:90], sample_count=90), "target_tx", s)
        self.assertEqual(r.truncated_frames, 1)

    def test_ranked_candidates_and_idle(self):
        c = simulate_uart(b"U-Boot: Hit any key to stop autoboot\r\n")
        candidates = infer(c, "target_tx", bauds=[9600, 115200], limit=20)
        self.assertTrue(any(r.data == b"U-Boot: Hit any key to stop autoboot\r\n" for r in candidates))
        self.assertEqual([r.score for r in candidates], sorted([r.score for r in candidates], reverse=True))
        self.assertEqual(infer(replace(c, samples=b"\x01" * c.sample_count), "target_tx"), [])


class BridgeTests(unittest.TestCase):
    def run_server(self, response):
        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                while line := self.rfile.readline():
                    request = json.loads(line)
                    self.wfile.write(response(request) + b"\n")
        server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def test_persistent_capture_transport(self):
        c = simulate_uart(b"boot")
        port = self.run_server(lambda r: json.dumps({"version": 1, "id": r["id"], "ok": True, "result": c.to_dict()}).encode())
        with BridgeClient(port) as client:
            self.assertEqual(client.read_capture(c.capture_id), c)
            self.assertEqual(client.read_capture(c.capture_id), c)

    def test_protocol_failures(self):
        for payload in (b"not json", b'{"version":1,"id":999,"ok":true}', b'{"version":1,"id":1,"ok":false,"error":"busy"}'):
            port = self.run_server(lambda r, p=payload: p)
            with BridgeClient(port) as client, self.assertRaises(BridgeError):
                client.request("hello")
        with self.assertRaises(ValueError):
            BridgeClient(1234, host="example.com")


if __name__ == "__main__":
    unittest.main()
