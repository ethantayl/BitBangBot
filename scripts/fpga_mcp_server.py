"""
fpga_mcp.py -- MCP server exposing BitBangBot's FPGA to an AI agent.

Run the Tcl server first (System Console: source jtag_server.tcl), then:
    python fpga_mcp.py

Layering (per the "don't obscure" principle):
  - RAW tools (read/write register + block) are ALWAYS available -- the agent
    can reach any address directly and reason from first principles.
  - SEMANTIC tools (uart_send, logic_capture) are conveniences on top. They
    return raw evidence alongside any interpretation, and every assumption is
    overridable. They never replace raw access.
  - describe_memory_map() is the agent's documentation of the address space.
"""
from mcp.server.fastmcp import FastMCP
from fpga_bench import FpgaBench
import time

mcp = FastMCP("bitbangbot")

_fb = None
def fb():
    global _fb
    if _fb is None:
        _fb = FpgaBench()
    return _fb

# ---- address map (from Qsys Address Map; UPDATE if it changes) ----
LA       = 0x0000_0000     # logic analyzer
STREAMER = 0x0000_8000     # UART TX streamer
RAM      = 0x0001_0000     # on-chip RAM
SWITCHES = 0x0001_1000     # switch PIO (read-only)

CLK = 50_000_000


# ======================= RAW ACCESS (always available) =======================

@mcp.tool()
def read_register(address: int) -> dict:
    """Read one 32-bit word from the FPGA's Avalon address space (byte address).
    The most direct view into the hardware -- use when a semantic tool doesn't
    fit or you want to inspect something yourself."""
    v = fb().read(address)
    return {"address": hex(address), "value": v, "hex": f"{v:#010x}"}

@mcp.tool()
def write_register(address: int, value: int) -> dict:
    """Write one 32-bit word to the FPGA's Avalon address space (byte address)."""
    fb().write(address, value & 0xFFFFFFFF)
    return {"address": hex(address), "wrote": f"{value & 0xFFFFFFFF:#010x}"}

@mcp.tool()
def read_block(address: int, count: int) -> dict:
    """Read `count` consecutive 32-bit words in one transaction. For bulk reads
    (RAM, capture buffers)."""
    vals = fb().read_block(address, count)
    return {"address": hex(address), "count": count, "values": vals}

@mcp.tool()
def write_block(address: int, values: list[int]) -> dict:
    """Write a list of 32-bit words to consecutive addresses in one transaction.
    For bulk loads (messages, RAM)."""
    fb().write_block(address, values)
    return {"address": hex(address), "wrote_words": len(values)}

@mcp.tool()
def describe_memory_map() -> dict:
    """The FPGA's address map and each device's register layout. Read this first
    so you know what lives where and can use raw register access if needed."""
    return {
        "logic_analyzer": {
            "base": hex(LA),
            "registers": {
                "CONTROL(0x00,w)": "bit0 ARM, bit1 CLEAR",
                "STATUS(0x04,r)": "bit0 armed, bit1 triggered, bit2 done, [27:16] captured",
                "SAMPLE_DIV(0x08)": "sample every (n+1) clocks; rate = 50MHz/(n+1)",
                "TRIG_MASK(0x0C)": "channels to watch for trigger",
                "TRIG_VALUE(0x10)": "level/target value on masked channels",
                "TRIG_EDGE(0x14)": "per-channel 1=edge, 0=level",
                "DEPTH(0x18)": "samples to capture",
                "PRE_TRIG(0x1C)": "samples kept before trigger",
                "BUFFER(0x2000,r)": "captured samples, 1 word each, all 32 channels",
            },
        },
        "uart_streamer": {
            "base": hex(STREAMER),
            "note": "buffered UART transmit; load message, set length, GO",
            "registers": {
                "CONTROL(0x00,w)": "bit0 GO",
                "STATUS(0x04,r)": "bit0 busy",
                "LENGTH(0x08,w)": "bytes to send",
                "BUFFER(0x1000,w)": "message bytes, one byte per 32-bit word",
            },
        },
        "ram": {"base": hex(RAM), "size": "4 KB", "note": "scratch, read/write"},
        "switches": {"base": hex(SWITCHES), "note": "read-only, bits[9:0]"},
    }


# ======================= SEMANTIC TOOLS (conveniences) =======================

@mcp.tool()
def uart_send(text: str) -> dict:
    """Transmit a text string over the UART (via the buffered streamer). Returns
    the raw bytes sent so you can verify."""
    data = list(text.encode())
    fb().write_block(STREAMER + 0x1000, data)
    fb().write(STREAMER + 0x08, len(data))
    fb().write(STREAMER + 0x00, 1)            # GO
    while fb().read(STREAMER + 0x04) & 1:     # busy
        pass
    return {"sent_text": text, "bytes": [hex(b) for b in data], "count": len(data)}

@mcp.tool()
def logic_capture(sample_rate_hz: int = 1_000_000, trigger_channel: int = 0,
                  trigger_edge: str = "falling", depth: int = 128) -> dict:
    """Capture digital activity on the probe channels. Arms the analyzer, waits
    for the trigger, returns the raw samples (one 32-bit word per time step,
    one bit per channel) PLUS a summary of which channels were active. Use the
    raw samples to reason about / decode an unknown signal yourself.

    trigger_edge: 'rising', 'falling', or 'none' (capture immediately)."""
    div = CLK // sample_rate_hz - 1
    fb().write(LA + 0x00, 0x2)                      # CLEAR
    fb().write(LA + 0x08, div)
    fb().write(LA + 0x18, depth)
    fb().write(LA + 0x1C, 0)
    if trigger_edge == "none":
        # trigger on the channel being high -> fires ~immediately
        fb().write(LA + 0x0C, 1 << trigger_channel)
        fb().write(LA + 0x10, 1 << trigger_channel)
        fb().write(LA + 0x14, 0)
    else:
        fb().write(LA + 0x0C, 1 << trigger_channel)
        fb().write(LA + 0x10, (1 if trigger_edge == "rising" else 0) << trigger_channel)
        fb().write(LA + 0x14, 1 << trigger_channel)
    fb().write(LA + 0x00, 0x1)                      # ARM

    for _ in range(300):
        if fb().read(LA + 0x04) & 0x4:              # done
            break
        time.sleep(0.01)
    else:
        return {"error": "capture did not trigger",
                "hint": "no activity on the channel, or wrong trigger edge"}

    samples = fb().read_block(LA + 0x2000, depth)
    # summary: which channels toggled
    active = []
    for c in range(16):
        vals = set((w >> c) & 1 for w in samples)
        if len(vals) > 1:
            active.append(c)
    return {
        "captured": depth,
        "sample_rate_hz": sample_rate_hz,
        "active_channels": active,          # hint
        "samples": samples,                 # raw -- decode yourself / with sigrok
    }


if __name__ == "__main__":
    mcp.run()