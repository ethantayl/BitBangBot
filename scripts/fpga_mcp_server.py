#!/usr/bin/env python3
"""
fpga_mcp_server.py -- MCP (Model Context Protocol) Server for Intel FPGA JTAG Bridge.

Enables LLMs like Claude to interact directly with FPGA memory-mapped registers
via the System Console JTAG-to-Avalon bridge (jtag_server.tcl).

Features:
- Read 32-bit registers (returns Hex, Dec, Binary bit breakdown, ASCII)
- Write 32-bit registers with automatic read-back verification
- Fast burst block reads (amortizes JTAG latency)
- Named register aliases (e.g. 'switches', 'leds', 'ram')
- Auto-reconnecting socket client to localhost:2540
- Built-in Mock Mode for offline testing/simulation
- Stdio transport for Claude Desktop / Cursor / VS Code integration
"""

import os
import sys
import time
import socket
import logging
from typing import Optional, Dict, Any, List, Union

# Configure logging to STDERR only (never stdout, which breaks MCP stdio JSON-RPC)
logging.basicConfig(
    level=logging.INFO,
    format="[FPGA-MCP] %(asctime)s %(levelname)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("fpga_mcp")

# Import MCP SDK (supports both mcp 2.x and mcp 1.x)
try:
    from mcp.server.mcpserver import MCPServer
except ImportError:
    try:
        from mcp.server.fastmcp import FastMCP as MCPServer
    except ImportError:
        sys.stderr.write(
            "ERROR: The 'mcp' Python package is required.\n"
            "Install it with: pip install mcp\n"
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Default Register Map (customizable at runtime or via environment)
# ---------------------------------------------------------------------------
DEFAULT_REGISTER_MAP: Dict[str, Dict[str, Any]] = {
    "switches": {
        "address": 0x00000000,
        "description": "DE-series 10 slide switches (SW[9:0]), read-only PIO",
    },
    "sw": {
        "address": 0x00000000,
        "description": "Alias for 'switches'",
    },
    "leds": {
        "address": 0x00000010,
        "description": "DE-series 10 red LEDs (LEDR[9:0]), read/write PIO",
    },
    "ledr": {
        "address": 0x00000010,
        "description": "Alias for 'leds'",
    },
    "hex": {
        "address": 0x00000020,
        "description": "7-Segment displays (HEX3..HEX0), read/write PIO",
    },
    "ram_base": {
        "address": 0x00010000,
        "description": "On-chip RAM base address (BRAM)",
    },
}


# ---------------------------------------------------------------------------
# Hardware Client / Socket Interface
# ---------------------------------------------------------------------------
class FpgaJtagClient:
    """Manages the TCP socket connection to jtag_server.tcl (System Console)."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 2540,
        timeout: float = 5.0,
        mock: bool = False,
    ):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.mock = mock
        self.sock: Optional[socket.socket] = None
        self.f = None

        # Simulated memory for mock mode
        self._mock_memory: Dict[int, int] = {
            0x00000000: 0x000003FF,  # 10 switches all ON (0b1111111111)
            0x00000010: 0x00000055,  # Alternating LEDs (0b01010101)
            0x00000020: 0x00001234,  # HEX display
            0x00010000: 0xDEADBEEF,  # RAM word 0
            0x00010004: 0xCAFEBABE,  # RAM word 1
            0x00010008: 0x01234567,  # RAM word 2
            0x0001000C: 0x89ABCDEF,  # RAM word 3
        }

    def connect(self) -> None:
        if self.mock:
            logger.info("Operating in MOCK mode (simulated FPGA hardware).")
            return

        self.close()
        try:
            logger.info(f"Connecting to JTAG server at {self.host}:{self.port}...")
            self.sock = socket.create_connection(
                (self.host, self.port), timeout=self.timeout
            )
            self.f = self.sock.makefile("rw", newline="\n")
            # Verify handshake
            self.f.write("PING\n")
            self.f.flush()
            resp = self.f.readline().strip()
            if resp != "PONG":
                raise ConnectionError(f"Unexpected handshake response: '{resp}'")
            logger.info("Connected to JTAG server successfully.")
        except Exception as e:
            self.close()
            raise ConnectionError(
                f"Could not connect to JTAG server at {self.host}:{self.port}. "
                f"Ensure 'system-console --script=jtag_server.tcl' is running and the "
                f"USB-Blaster cable is plugged in. (Error: {e})"
            ) from e

    def _cmd(self, line: str) -> str:
        if self.mock:
            return self._mock_handle(line)

        if self.sock is None or self.f is None:
            self.connect()

        try:
            self.f.write(line + "\n")
            self.f.flush()
            resp = self.f.readline()
            if not resp:
                raise ConnectionResetError("JTAG server closed connection.")
            resp = resp.strip()
            if resp.startswith("ERR"):
                raise RuntimeError(f"FPGA bus/server error: {resp[4:].strip()}")
            return resp
        except (ConnectionError, BrokenPipeError, socket.error) as e:
            logger.warning(f"Connection lost ({e}); attempting one reconnect...")
            self.connect()
            self.f.write(line + "\n")
            self.f.flush()
            resp = self.f.readline().strip()
            if resp.startswith("ERR"):
                raise RuntimeError(f"FPGA bus/server error: {resp[4:].strip()}")
            return resp

    def _mock_handle(self, line: str) -> str:
        parts = line.strip().split()
        cmd = parts[0]
        if cmd == "PING":
            return "PONG"
        elif cmd == "R":
            addr = int(parts[1], 0)
            return str(self._mock_memory.get(addr, 0))
        elif cmd == "W":
            addr = int(parts[1], 0)
            val = int(parts[2], 0)
            self._mock_memory[addr] = val & 0xFFFFFFFF
            return "OK"
        elif cmd == "B":
            addr = int(parts[1], 0)
            cnt = int(parts[2], 0)
            vals = [str(self._mock_memory.get(addr + i * 4, 0)) for i in range(cnt)]
            return " ".join(vals)
        return "ERR unknown mock command"

    def ping(self) -> str:
        start = time.perf_counter()
        resp = self._cmd("PING")
        elapsed_ms = (time.perf_counter() - start) * 1000
        return f"{resp} (round-trip: {elapsed_ms:.2f} ms)"

    def read_word(self, addr: int) -> int:
        resp = self._cmd(f"R {addr:#x}")
        return int(resp, 0) & 0xFFFFFFFF

    def write_word(self, addr: int, val: int) -> None:
        self._cmd(f"W {addr:#x} {val & 0xFFFFFFFF:#x}")

    def read_block(self, addr: int, count: int) -> List[int]:
        resp = self._cmd(f"B {addr:#x} {count}")
        return [int(x, 0) & 0xFFFFFFFF for x in resp.split()]

    def close(self) -> None:
        try:
            if self.f:
                self.f.close()
            if self.sock:
                self.sock.close()
        except Exception:
            pass
        finally:
            self.f = None
            self.sock = None


# ---------------------------------------------------------------------------
# Global State & Address Parser
# ---------------------------------------------------------------------------
MOCK_ENV = os.environ.get("FPGA_MOCK", "").lower() in ("1", "true", "yes")
client = FpgaJtagClient(
    host=os.environ.get("FPGA_HOST", "127.0.0.1"),
    port=int(os.environ.get("FPGA_PORT", "2540")),
    mock=MOCK_ENV,
)
register_map = dict(DEFAULT_REGISTER_MAP)


def resolve_address(addr_input: Union[str, int]) -> tuple[int, Optional[str]]:
    """
    Parses a user input into a byte integer address.
    Supports hex ('0x10'), decimal ('16'), binary ('0b10000'), or registered alias ('switches').
    Returns (address_int, alias_name_or_None).
    """
    if isinstance(addr_input, int):
        return addr_input, None

    s = str(addr_input).strip()
    s_lower = s.lower()

    # Check register map first
    if s_lower in register_map:
        return register_map[s_lower]["address"], s_lower

    # Parse numerical representations
    try:
        val = int(s, 0)
        return val, None
    except ValueError:
        raise ValueError(
            f"Invalid address: '{addr_input}'. Provide a valid hexadecimal (e.g. 0x00, 0x10), "
            f"decimal integer (e.g. 16), or known register alias (e.g. {list(register_map.keys())})."
        )


def parse_value(val_input: Union[str, int]) -> int:
    """Parses value string into 32-bit unsigned int."""
    if isinstance(val_input, int):
        return val_input & 0xFFFFFFFF
    s = str(val_input).strip()
    try:
        return int(s, 0) & 0xFFFFFFFF
    except ValueError:
        raise ValueError(
            f"Invalid value: '{val_input}'. Provide a hex (e.g. 0x55, 0x00FF), decimal (e.g. 85), or binary string (e.g. 0b01010101)."
        )


def format_word_details(addr: int, val: int, alias: Optional[str] = None) -> str:
    """Creates an extensive, human-friendly hardware report of a 32-bit register value."""
    val &= 0xFFFFFFFF
    signed_val = val if val < 0x80000000 else val - 0x100000000
    bin_str = f"{val:032b}"
    grouped_bin = f"{bin_str[0:8]} {bin_str[8:16]} {bin_str[16:24]} {bin_str[24:32]}"

    # Active bits
    active_bits = [i for i in range(32) if (val & (1 << i))]
    active_str = f"[{', '.join(str(b) for b in active_bits)}]" if active_bits else "None"

    # Little-endian bytes
    b0 = val & 0xFF
    b1 = (val >> 8) & 0xFF
    b2 = (val >> 16) & 0xFF
    b3 = (val >> 24) & 0xFF
    bytes_hex = f"{b0:02X} {b1:02X} {b2:02X} {b3:02X}"
    ascii_repr = "".join(chr(b) if 32 <= b <= 126 else "." for b in [b0, b1, b2, b3])

    alias_header = f" [{alias.upper()}]" if alias else ""
    alignment_warning = ""
    if addr % 4 != 0:
        alignment_warning = f"\n⚠️ Note: Address 0x{addr:X} is not 4-byte aligned. Avalon 32-bit registers expect 4-byte boundaries (e.g. 0x{(addr//4)*4:X})."

    output = [
        f"Memory Address: 0x{addr:08X} ({addr} dec){alias_header}{alignment_warning}",
        f"Value (Hex):     0x{val:08X}",
        f"Value (Decimal): {val} (unsigned) / {signed_val} (signed 32-bit)",
        f"Value (Binary):  {grouped_bin}",
        f"Active Bits (1): {active_str}",
        f"Bytes [B0..B3]:  {bytes_hex}  (ASCII: '{ascii_repr}')",
    ]
    return "\n".join(output)


# ---------------------------------------------------------------------------
# MCP Server Instance & Tools
# ---------------------------------------------------------------------------
mcp = MCPServer("fpga-jtag-bridge")


@mcp.tool()
def read_memory_address(address: str) -> str:
    """
    Read a 32-bit register from the FPGA at the specified memory address.

    Use this when asked questions like 'what is the value at address 0x00?',
    'check the switches', or 'read register 0x10'.

    Args:
        address: Memory address in hex (e.g. '0x00', '0x10', '0x00010000'),
                 decimal (e.g. '16'), binary ('0b10000'), or register alias
                 ('switches', 'leds', 'hex', 'ram_base').
    """
    try:
        addr, alias = resolve_address(address)
        val = client.read_word(addr)
        return format_word_details(addr, val, alias)
    except Exception as e:
        return f"Error reading address '{address}': {e}"


@mcp.tool()
def write_memory_address(address: str, value: str, verify: bool = True) -> str:
    """
    Write a 32-bit word to an FPGA memory-mapped register or peripheral.

    Use this to turn on LEDs, update control registers, or write to BRAM.

    Args:
        address: Target memory address (e.g. '0x10', 'leds', '0x1000').
        value: 32-bit value to write in hex ('0x55', '0x3FF'), decimal ('85'),
               or binary ('0b01010101').
        verify: If True, reads back the register immediately to verify the write.
    """
    try:
        addr, alias = resolve_address(address)
        val = parse_value(value)

        client.write_word(addr, val)

        alias_str = f" ({alias})" if alias else ""
        lines = [
            f"Successfully wrote 0x{val:08X} ({val} dec) to address 0x{addr:08X}{alias_str}."
        ]

        if verify:
            read_back = client.read_word(addr)
            if read_back == val:
                lines.append(f"Verification: MATCH (read-back returned 0x{read_back:08X}).")
            else:
                lines.append(
                    f"Verification: MISMATCH! Read-back returned 0x{read_back:08X}. "
                    "(Note: this register might be write-only, strobe-triggered, or masked by hardware)."
                )

        return "\n".join(lines)
    except Exception as e:
        return f"Error writing to address '{address}': {e}"


@mcp.tool()
def read_memory_block(address: str, count: int = 16) -> str:
    """
    Perform a high-speed burst block read of consecutive 32-bit words.

    Amortizes JTAG USB round-trip latency by fetching up to 256 words in
    a single transaction. Ideal for reading RAM buffers, FIFOs, or tables.

    Args:
        address: Starting memory address (e.g. '0x10000', 'ram_base').
        count: Number of 32-bit words to read (default: 16, max: 256).
    """
    try:
        if count <= 0:
            return "Count must be greater than 0."
        if count > 256:
            count = 256  # Cap to prevent socket timeouts

        start_addr, alias = resolve_address(address)
        words = client.read_block(start_addr, count)

        end_addr = start_addr + (count * 4) - 4
        alias_str = f" [{alias}]" if alias else ""

        table = [
            f"Block Read: 0x{start_addr:08X} - 0x{end_addr:08X} ({count} words / {count * 4} bytes){alias_str}",
            "-" * 72,
            f"{'Offset':<8} {'Address':<12} {'Hex Value':<12} {'Decimal':<12} {'ASCII':<6}",
            "-" * 72,
        ]

        for i, val in enumerate(words):
            cur_addr = start_addr + (i * 4)
            b0 = val & 0xFF
            b1 = (val >> 8) & 0xFF
            b2 = (val >> 16) & 0xFF
            b3 = (val >> 24) & 0xFF
            ascii_repr = "".join(
                chr(b) if 32 <= b <= 126 else "." for b in [b0, b1, b2, b3]
            )
            table.append(
                f"+0x{i*4:04X}  0x{cur_addr:08X}  0x{val:08X}  {val:<12}  {ascii_repr}"
            )

        return "\n".join(table)
    except Exception as e:
        return f"Error reading memory block at '{address}': {e}"


@mcp.tool()
def ping_fpga() -> str:
    """
    Test link health and measure round-trip latency to the JTAG server.

    Use this to verify whether the FPGA board and jtag_server.tcl are reachable.
    """
    try:
        mode = " [MOCK MODE]" if client.mock else ""
        resp = client.ping()
        return (
            f"FPGA JTAG Bridge Status: ONLINE{mode}\n"
            f"Host: {client.host}:{client.port}\n"
            f"Ping Response: {resp}\n"
            f"Registered Aliases: {len(register_map)}"
        )
    except Exception as e:
        return f"FPGA JTAG Bridge Status: OFFLINE\nError: {e}"


@mcp.tool()
def list_registers() -> str:
    """
    List all known register aliases and their memory-mapped addresses.
    """
    lines = [
        "Configured Peripheral Register Map:",
        "-" * 60,
        f"{'Name':<14} {'Address':<12} {'Description'}",
        "-" * 60,
    ]
    for name, info in sorted(register_map.items()):
        addr = info["address"]
        desc = info.get("description", "")
        lines.append(f"{name:<14} 0x{addr:08X}   {desc}")
    return "\n".join(lines)


@mcp.tool()
def add_register_alias(name: str, address: str, description: str = "") -> str:
    """
    Add or update a custom named register alias for easy memory access.

    Args:
        name: Short identifier (e.g. 'uart_status', 'adc_ch0', 'fir_ctrl').
        address: Memory address (e.g. '0x2000').
        description: Optional notes on the peripheral function.
    """
    try:
        addr, _ = resolve_address(address)
        clean_name = name.strip().lower()
        register_map[clean_name] = {
            "address": addr,
            "description": description or f"User mapped register at 0x{addr:08X}",
        }
        return f"Registered alias '{clean_name}' -> 0x{addr:08X} ({description or 'no description'})."
    except Exception as e:
        return f"Error defining alias: {e}"


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------
def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="FPGA JTAG Bridge MCP Server for Claude"
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="JTAG server IP (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--port", type=int, default=2540, help="JTAG server port (default: 2540)"
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Run in mock mode (simulated FPGA hardware)",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Run self-test against JTAG server and exit",
    )

    args = parser.parse_args()

    client.host = args.host
    client.port = args.port
    if args.mock:
        client.mock = True

    if args.test:
        sys.stderr.write(f"Testing connection to {client.host}:{client.port}...\n")
        try:
            res = client.ping()
            sys.stderr.write(f"Ping OK: {res}\n")
            val = client.read_word(0x0)
            sys.stderr.write(f"Read 0x00: 0x{val:08X}\n")
            sys.stderr.write("Self-test PASSED.\n")
            sys.exit(0)
        except Exception as e:
            sys.stderr.write(f"Self-test FAILED: {e}\n")
            sys.exit(1)

    # Run the MCP server over standard I/O (default for Claude Desktop)
    logger.info(
        f"Starting FPGA MCP Server (stdio transport, mock={client.mock})..."
    )
    mcp.run()


if __name__ == "__main__":
    main()
