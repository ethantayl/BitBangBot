"""
fpga_bench.py -- Python client for the System Console JTAG bridge.

Prereq: start the Tcl server once, and leave it running:
    system-console --script=jtag_server.tcl
(the board must be programmed and connected via its USB-Blaster.)

Then from Python:
    from fpga_bench import FpgaBench
    fb = FpgaBench()
    print(f"{fb.read(0x0) & 0x3FF:010b}")   # switches
"""

import socket


class FpgaBench:
    def __init__(self, host="localhost", port=2540, timeout=5.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.f = self.sock.makefile("rw", newline="\n")
        # sanity check the link
        if self.ping() != "PONG":
            raise RuntimeError("server did not respond to PING")

    def _cmd(self, line):
        self.f.write(line + "\n")
        self.f.flush()
        resp = self.f.readline().strip()
        if resp.startswith("ERR"):
            raise RuntimeError(f"server error: {resp[4:]}")
        return resp

    def ping(self):
        return self._cmd("PING")

    def read(self, addr):
        """Read one 32-bit word at byte address `addr`."""
        return int(self._cmd(f"R {addr:#x}"), 0)

    def write(self, addr, val):
        """Write one 32-bit word."""
        self._cmd(f"W {addr:#x} {val:#x}")

    def read_block(self, addr, count):
        """Read `count` consecutive 32-bit words in a single JTAG transaction.
        Use this instead of a read() loop for anything bulk -- it amortizes
        the per-transaction JTAG latency."""
        resp = self._cmd(f"B {addr:#x} {count}")
        return [int(x, 0) for x in resp.split()]

    def write_block(self, addr, values):
        """Write a list of 32-bit words to consecutive addresses starting at
        `addr`, in a single JTAG transaction. Use this to load data into RAM
        (AI -> FPGA) instead of a write() loop. Chunks large lists so one
        command line doesn't get unwieldy."""
        values = list(values)
        CHUNK = 256
        for i in range(0, len(values), CHUNK):
            chunk = values[i:i + CHUNK]
            words = " ".join(f"{v & 0xFFFFFFFF:#x}" for v in chunk)
            self._cmd(f"BW {addr + i*4:#x} {words}")

    def close(self):
        try:
            self.f.close()
            self.sock.close()
        except Exception:
            pass


if __name__ == "__main__":
    import time
    fb = FpgaBench()
    print("connected. reading switches (flip them to see changes)...")
    try:
        while True:
            sw = fb.read(0x0) & 0x3FF
            print(f"switches = {sw:010b}  ({sw:#05x})")
            time.sleep(0.3)
    except KeyboardInterrupt:
        fb.close()
        print("\nbye")