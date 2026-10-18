"""
fpga_mcp.py -- MCP server exposing BitBangBot's FPGA to an AI agent.

Run the Tcl server first (System Console: source jtag_server.tcl), then:
    python fpga_mcp.py

Layering (per the "don't obscure" principle):
  - RAW tools (read/write register + block) are ALWAYS available -- the agent
    can reach any address directly and reason from first principles.
  - SEMANTIC tools (uart_send, spi_transfer, logic_capture) are conveniences on top. They
    return raw evidence alongside any interpretation, and every assumption is
    overridable. They never replace raw access.
  - describe_memory_map() is the agent's documentation of the address space.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP
from fpga_bench import FpgaBench
import time

mcp = FastMCP("bitbangbot")

_fb = None

class _Reconnecting:
    """Proxy for FpgaBench that reconnects once if the Tcl server was
    restarted (e.g. after reprogramming), instead of failing until the MCP
    server itself is restarted. Only retries on a dropped connection, not on
    timeouts, so a command is never sent twice after it may have run."""
    def __getattr__(self, name):
        def call(*args, **kwargs):
            global _fb
            for attempt in (0, 1):
                if _fb is None:
                    _fb = FpgaBench()
                try:
                    return getattr(_fb, name)(*args, **kwargs)
                except (ConnectionError, BrokenPipeError):
                    try:
                        _fb.close()
                    except Exception:
                        pass
                    _fb = None
                    if attempt:
                        raise
        return call

def fb():
    return _Reconnecting()

# ---- address map (from Qsys Address Map; UPDATE if it changes) ----
LA       = 0x0000_0000     # logic analyzer
UART     = 0x0000_8000     # full UART (buffered TX + RX, runtime baud)
RAM      = 0x0000_a000     # on-chip RAM
PINMUX   = 0x0000_b000     # pin crossbar (route peripherals to physical pins)
I2C      = 0x0000_b400     # Intel Avalon I2C (Master), altera_avalon_i2c
SPI      = 0x0000_b440     # Intel SPI (4 Wire Serial) master, altera_avalon_spi
SWITCHES = 0x0000_b460     # switch PIO (read-only)
BOARD    = 0x0000_b470     # board_io: HEX0-5 seven-segment displays + LEDR

CLK = 50_000_000

# pin_mux register offsets (byte). NPIN = 8 flexible pins.
#   PIN0_SRC..PIN7_SRC at word 0..7  (byte 0x00..0x1C)
#   source codes: 0=raw GPIO, 1=UART_TX, 2=SPI_SCLK, 3=SPI_MOSI, 4=SPI_CS,
#                 5=I2C_SCL, 6=I2C_SDA
PM_SRC0     = PINMUX + 0x00   # per-pin source select (pin i at +i*4)
PM_OE       = PINMUX + 0x20   # bit i = output enable for pin i
PM_OD       = PINMUX + 0x24   # bit i = open-drain for pin i (for I2C)
PM_GPIO_OUT = PINMUX + 0x28   # raw output value per pin (used when SRC=0)
PM_GPIO_IN  = PINMUX + 0x2C   # (r) live input level of all pins
PM_UART_RX_PIN = PINMUX + 0x30  # which pin feeds UART RX
PM_SPI_MISO_PIN = PINMUX + 0x34 # which pin feeds SPI MISO

NPIN = 8
PM_SRC = {"gpio": 0, "uart_tx": 1, "spi_sclk": 2, "spi_mosi": 3,
          "spi_cs": 4, "i2c_scl": 5, "i2c_sda": 6}
PM_SRC_NAME = {v: k for k, v in PM_SRC.items()}
# sources with a real core behind them (all of them, now that I2C is built)
PM_SRC_LIVE = {"gpio", "uart_tx", "spi_sclk", "spi_mosi", "spi_cs",
               "i2c_scl", "i2c_sda"}

def _bad_pin(pin):
    # pins past NPIN would alias onto PIN_OE/PIN_OD/... registers
    if not 0 <= pin < NPIN:
        return {"error": f"pin {pin} out of range", "valid": f"0..{NPIN - 1}"}
    return None

# uart_full register offsets (byte)
U_CONTROL  = UART + 0x00    # W: bit0 TX_GO, bit1 RX_CLEAR, bit2 TX_CLEAR
U_STATUS   = UART + 0x04    # R: bit0 tx_busy, [15:8] rx_count, [27:16] framing_errors
U_BAUDDIV  = UART + 0x08    # RW: baud divisor = CLK/baud - 1
U_TXLEN    = UART + 0x0C    # W: bytes to transmit
U_RXCOUNT  = UART + 0x10    # R: bytes received
U_TXBUF    = UART + 0x800   # W: TX bytes, one per word
U_RXBUF    = UART + 0xC00   # R: RX bytes, one per word
U_TXBUF_LEN = 256           # uart_full BUF_LEN (TX and RX buffer depth)

# altera_avalon_spi register offsets (byte; per the Intel Embedded Peripherals
# IP User Guide, "SPI Core"). Master, 1 MHz SCLK, mode 0, 8-bit, MSB first,
# 1 slave select.
S_RXDATA   = SPI + 0x00     # R: received byte
S_TXDATA   = SPI + 0x04     # W: byte to transmit
S_STATUS   = SPI + 0x08     # R: bit3 ROE, bit4 TOE, bit5 TMT, bit6 TRDY, bit7 RRDY, bit8 E
S_CONTROL  = SPI + 0x0C     # RW: bit10 SSO (hold SS_n asserted)
S_SLAVESEL = SPI + 0x14     # RW: bitmask of SS_n lines to assert
S_TMT  = 1 << 5             # transmit shift register empty (transfer finished)
S_TRDY = 1 << 6             # txdata ready for a new byte
S_RRDY = 1 << 7             # rxdata holds a received byte
S_SSO  = 1 << 10
SPI_SCLK_HZ = 1_000_000

# altera_avalon_i2c register offsets (byte; checked against the generated
# altera_avalon_i2c_csr.v). 7-bit addressing, FIFO depth 16.
I_TFR_CMD  = I2C + 0x00     # W: [9] STA (start/restart), [8] STO (stop after
                            #    this byte), [7:0] addr<<1|rw  or  data byte
I_RX_DATA  = I2C + 0x04     # R: [7:0] received byte (pops RX FIFO)
I_CTRL     = I2C + 0x08     # RW: [0] EN, [1] BUS_SPEED (0=100k, 1=400k)
I_ISR      = I2C + 0x10     # R/W1C: [0] TX_READY, [1] RX_READY, [2] NACK_DET,
                            #        [3] ARBLOST_DET, [4] RX_OVER
I_STATUS   = I2C + 0x14     # R: [0] core busy
I_TFR_LVL  = I2C + 0x18     # R: commands waiting in TFR_CMD FIFO
I_RX_LVL   = I2C + 0x1C     # R: bytes waiting in RX FIFO
I_SCL_LOW  = I2C + 0x20     # RW: SCL low period, clocks (write with EN=0)
I_SCL_HIGH = I2C + 0x24     # RW: SCL high period, clocks (write with EN=0)
I_SDA_HOLD = I2C + 0x28     # RW: SDA hold after SCL falls, clocks (EN=0)
I_STA, I_STO = 1 << 9, 1 << 8
I_NACK, I_ARBLOST, I_RXOVER = 1 << 2, 1 << 3, 1 << 4
I2C_FIFO = 16
# (scl_low, scl_high, sda_hold, BUS_SPEED) in 50 MHz clocks
I2C_TIMING = {100_000: (250, 250, 15, 0),   # 5.0us / 5.0us
              400_000: (70, 55, 15, 1)}     # 1.4us / 1.1us
_i2c_ready = False

# board_io register offsets (byte). Segment byte per digit: bit0..6 = a..g,
# bit7 = decimal point, 1 = lit (hardware inverts for the active-low board).
B_HEX_LO = BOARD + 0x00     # RW: [7:0] HEX0 .. [31:24] HEX3
B_HEX_HI = BOARD + 0x04     # RW: [7:0] HEX4, [15:8] HEX5
B_LEDR   = BOARD + 0x08     # RW: [9:0] LEDR
NDIGIT = 6                  # HEX5 is the leftmost digit, HEX0 the rightmost
SEG_DP = 0x80
SEG_FONT = {
    "0": 0x3F, "1": 0x06, "2": 0x5B, "3": 0x4F, "4": 0x66, "5": 0x6D,
    "6": 0x7D, "7": 0x07, "8": 0x7F, "9": 0x6F,
    "A": 0x77, "b": 0x7C, "C": 0x39, "c": 0x58, "d": 0x5E, "E": 0x79,
    "F": 0x71, "G": 0x3D, "H": 0x76, "h": 0x74, "I": 0x30, "J": 0x1E,
    "L": 0x38, "n": 0x54, "O": 0x3F, "o": 0x5C, "P": 0x73, "q": 0x67,
    "r": 0x50, "S": 0x6D, "t": 0x78, "U": 0x3E, "u": 0x1C, "y": 0x6E,
    "-": 0x40, "_": 0x08, "=": 0x48, " ": 0x00,
}
# letters that only exist in one case on a 7-seg display
SEG_ALIAS = {"a": "A", "B": "b", "D": "d", "e": "E", "f": "F", "g": "G",
             "i": "I", "j": "J", "l": "L", "N": "n", "p": "P", "Q": "q",
             "R": "r", "s": "S", "T": "t", "Y": "y"}
# readback decode: first entry wins, so digits beat look-alike letters (5/S, 0/O)
SEG_DECODE = {}
for _k, _v in SEG_FONT.items():
    SEG_DECODE.setdefault(_v, _k)


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
        "uart": {
            "base": hex(UART),
            "note": "full UART: buffered TX + buffered RX, runtime baud, "
                    "framing-error count. Use for talking to a target and "
                    "reading its responses. Sweep baud + watch framing errors "
                    "to discover an unknown device's rate.",
            "registers": {
                "CONTROL(0x00,w)": "bit0 TX_GO, bit1 RX_CLEAR, bit2 TX_CLEAR",
                "STATUS(0x04,r)": "bit0 tx_busy, [15:8] rx_count, [27:16] framing_errors",
                "BAUDDIV(0x08,rw)": "baud divisor = 50MHz/baud - 1",
                "TX_LEN(0x0C,w)": "bytes to transmit",
                "RX_COUNT(0x10,r)": "bytes received since last RX_CLEAR",
                "TX_BUFFER(0x800,w)": "TX bytes, one byte per 32-bit word",
                "RX_BUFFER(0xC00,r)": "received bytes, one byte per 32-bit word",
            },
        },
        "pin_mux": {
            "base": hex(PINMUX),
            "note": "programmable pin crossbar: route any peripheral (or raw "
                    "GPIO bit-banging) onto any of the 8 flexible pins "
                    "(GPIO[7:0]). Reroute pins to try different protocols on an "
                    "unknown connection without recompiling.",
            "registers": {
                "PIN0_SRC..PIN7_SRC(0x00..0x1C)": "per-pin source: 0=raw GPIO, "
                    "1=UART_TX, 2=SPI_SCLK, 3=SPI_MOSI, 4=SPI_CS, 5=I2C_SCL, 6=I2C_SDA",
                "PIN_OE(0x20)": "bit i = output enable for pin i (0 = input/hi-Z)",
                "PIN_OD(0x24)": "bit i = open-drain for pin i (for I2C)",
                "GPIO_OUT(0x28,w)": "raw output value per pin (when src=raw GPIO)",
                "GPIO_IN(0x2C,r)": "live input level of all 8 pins",
                "UART_RX_PIN(0x30,rw)": "which pin feeds the UART RX input",
                "SPI_MISO_PIN(0x34,rw)": "which pin feeds the SPI MISO input",
            },
            "i2c_note": "I2C SCL/SDA inputs come from whichever pin is routed "
                        "to source 5/6 (same wire, open-drain) -- no separate "
                        "input-select register. Route with PIN_OE and PIN_OD "
                        "both set. Pins have FPGA weak pull-ups (~tens of "
                        "kOhm); fine for 100 kHz on short wires, add 4.7k "
                        "pull-ups if the target board has none.",
        },
        "i2c": {
            "base": hex(I2C),
            "note": "Intel Avalon I2C (Master) core (altera_avalon_i2c), 7-bit "
                    "addressing, 16-deep command and RX FIFOs. Routed through "
                    "pin_mux (sources i2c_scl / i2c_sda, open-drain). A "
                    "transaction is a stream of TFR_CMD writes: "
                    "STA|addr<<1|rw, then one command per byte (data for "
                    "writes, 0 for reads -- each read command clocks in one "
                    "byte to RX_DATA), with STO on the last. STA on a later "
                    "command = repeated start. If the FIFO runs dry mid-"
                    "transfer the core holds SCL low and waits. On NACK the "
                    "core sends STOP and flushes both FIFOs until ISR.NACK "
                    "is cleared (write 1).",
            "registers": {
                "TFR_CMD(0x00,w)": "[9] STA, [8] STO, [7:0] addr<<1|rw or data",
                "RX_DATA(0x04,r)": "[7:0] received byte",
                "CTRL(0x08,rw)": "[0] EN, [1] BUS_SPEED (0=100k,1=400k), "
                                 "[3:2] TFR_CMD FIFO thd, [5:4] RX FIFO thd",
                "ISER(0x0C,rw)": "interrupt enables (unused, no CPU)",
                "ISR(0x10,r/w1c)": "[0] TX_READY, [1] RX_READY, [2] NACK_DET, "
                                   "[3] ARBLOST_DET, [4] RX_OVER",
                "STATUS(0x14,r)": "[0] core busy",
                "TFR_CMD_FIFO_LVL(0x18,r)": "commands queued",
                "RX_DATA_FIFO_LVL(0x1C,r)": "bytes waiting",
                "SCL_LOW(0x20,rw)": "SCL low clocks (set while EN=0)",
                "SCL_HIGH(0x24,rw)": "SCL high clocks (set while EN=0)",
                "SDA_HOLD(0x28,rw)": "SDA hold clocks (set while EN=0)",
            },
        },
        "spi": {
            "base": hex(SPI),
            "note": "Intel SPI (4 Wire Serial) core (altera_avalon_spi), MASTER. "
                    "Fixed 1 MHz SCLK, mode 0 (CPOL=0, CPHA=0), 8-bit, MSB first, "
                    "1 slave select (SS_n[0], active low). Its SCLK/MOSI/SS_n go "
                    "through pin_mux (sources spi_sclk/spi_mosi/spi_cs) and MISO "
                    "comes from the pin in pin_mux SPI_MISO_PIN -- route those "
                    "first. Byte transfer: wait TRDY, write TXDATA, wait RRDY, "
                    "read RXDATA. To hold SS_n across several bytes, write "
                    "SLAVESELECT=1 and set CONTROL.SSO, transfer, wait TMT, then "
                    "clear CONTROL.",
            "registers": {
                "RXDATA(0x00,r)": "received byte (reading clears RRDY)",
                "TXDATA(0x04,w)": "byte to transmit (starts a transfer)",
                "STATUS(0x08,r)": "bit3 ROE (rx overrun), bit4 TOE, bit5 TMT "
                                  "(tx shift reg empty), bit6 TRDY (tx ready), "
                                  "bit7 RRDY (rx ready), bit8 E (ROE|TOE); "
                                  "write any value to clear ROE/TOE",
                "CONTROL(0x0C,rw)": "bit10 SSO (force SS_n asserted); "
                                    "bits 3,4,6,7,8 = interrupt enables (unused)",
                "SLAVESELECT(0x14,rw)": "bitmask of SS_n lines to assert "
                                        "(bit0 = SS_n[0])",
            },
        },
        "board_io": {
            "base": hex(BOARD),
            "note": "DE10-Lite six seven-segment displays (HEX5 leftmost .. "
                    "HEX0 rightmost) and 10 red LEDs. Raw segment control, "
                    "readable. Segment byte per digit: bit0..6 = segments "
                    "a..g (a top, b top-right, c bottom-right, d bottom, "
                    "e bottom-left, f top-left, g middle), bit7 = decimal "
                    "point; 1 = lit. Reset = all blank/off.",
            "registers": {
                "HEX_LO(0x00,rw)": "[7:0] HEX0, [15:8] HEX1, [23:16] HEX2, [31:24] HEX3",
                "HEX_HI(0x04,rw)": "[7:0] HEX4, [15:8] HEX5",
                "LEDR(0x08,rw)": "[9:0] LEDR[9:0], 1 = on",
            },
        },
        "ram": {"base": hex(RAM), "size": "4 KB", "note": "scratch, read/write"},
        "switches": {"base": hex(SWITCHES), "note": "read-only, bits[9:0]"},
    }


# ======================= SEMANTIC TOOLS (conveniences) =======================

def _baud_div(baud): return CLK // baud - 1

@mcp.tool()
def uart_set_baud(baud: int) -> dict:
    """Set the UART baud rate at runtime (no recompile). Returns the divisor."""
    div = _baud_div(baud)
    fb().write(U_BAUDDIV, div)
    return {"baud": baud, "divisor": div}

@mcp.tool()
def uart_send(text: str) -> dict:
    """Transmit a text string over the UART (buffered; sent in one bulk load).
    Returns the raw bytes sent so you can verify."""
    data = list(text.encode())
    # TX buffer is U_TXBUF_LEN bytes; longer messages go out in chunks
    # (otherwise the hardware wraps its index and repeats the start).
    for i in range(0, len(data), U_TXBUF_LEN):
        chunk = data[i:i + U_TXBUF_LEN]
        fb().write_block(U_TXBUF, chunk)
        fb().write(U_TXLEN, len(chunk))
        fb().write(U_CONTROL, 0x1)             # TX_GO
        while fb().read(U_STATUS) & 1:         # tx_busy
            pass
    return {"sent_text": text, "bytes": [hex(b) for b in data], "count": len(data)}

@mcp.tool()
def uart_receive(clear_after: bool = True) -> dict:
    """Read whatever bytes the UART has received into its RX buffer. Returns the
    bytes (hex + decoded text where printable), the count, and the framing-error
    count (nonzero framing errors usually mean the baud rate is wrong)."""
    status = fb().read(U_STATUS)
    count = fb().read(U_RXCOUNT)
    frame_errors = (status >> 16) & 0xFFF
    data = []
    if count > 0:
        words = fb().read_block(U_RXBUF, count)
        data = [w & 0xFF for w in words]
    if clear_after:
        fb().write(U_CONTROL, 0x2)             # RX_CLEAR
    text = bytes(data).decode("ascii", errors="replace")
    return {
        "count": count,
        "framing_errors": frame_errors,
        "bytes": [hex(b) for b in data],
        "text": text,
    }

@mcp.tool()
def uart_discover_baud(candidates: list[int] = None) -> dict:
    """Try to find an unknown device's baud rate. For each candidate baud, set
    it, clear RX, wait briefly for incoming bytes, and check the framing-error
    count. The rate with received bytes and NO framing errors is the match.
    Requires the device to be transmitting something while this runs."""
    if candidates is None:
        candidates = [9600, 19200, 38400, 57600, 115200, 230400]
    results = []
    best = None
    for baud in candidates:
        fb().write(U_BAUDDIV, _baud_div(baud))
        fb().write(U_CONTROL, 0x2)             # RX_CLEAR
        time.sleep(0.2)                        # let bytes arrive
        status = fb().read(U_STATUS)
        count = fb().read(U_RXCOUNT)
        frame_errors = (status >> 16) & 0xFFF
        r = {"baud": baud, "bytes_received": count, "framing_errors": frame_errors}
        results.append(r)
        if count > 0 and frame_errors == 0 and best is None:
            best = baud
    return {"results": results, "likely_baud": best,
            "note": "device must be transmitting during the sweep"}

@mcp.tool()
def pin_route(pin: int, source: str, output_enable: bool = True,
              open_drain: bool = False) -> dict:
    """Route a signal onto one of the 8 flexible pins (GPIO[7:0]).
    `source` is one of: gpio (raw bit-bang), uart_tx, spi_sclk, spi_mosi,
    spi_cs (SPI SS_n, active low), i2c_scl, i2c_sda. Set output_enable=False
    to make the pin an input (hi-Z), e.g. pin_route(p, "gpio",
    output_enable=False) for a SPI MISO pin. I2C pins need open_drain=True
    (drive low only) with output_enable=True; the I2C core also reads the
    bus from these same pins. (open_drain is forced on for I2C sources --
    driving an I2C line high would fight the devices on the bus.)"""
    if (err := _bad_pin(pin)):
        return err
    if source in ("i2c_scl", "i2c_sda"):
        open_drain = True
    if source not in PM_SRC:
        return {"error": f"unknown source {source}", "valid": sorted(PM_SRC_LIVE)}
    if source not in PM_SRC_LIVE:
        return {"error": f"source {source} has no core yet (tied idle in the "
                         "top level); use gpio + pin_set to bit-bang it",
                "valid": sorted(PM_SRC_LIVE)}
    fb().write(PM_SRC0 + pin * 4, PM_SRC[source])
    oe = fb().read(PM_OE); od = fb().read(PM_OD)
    oe = (oe | (1 << pin)) if output_enable else (oe & ~(1 << pin))
    od = (od | (1 << pin)) if open_drain else (od & ~(1 << pin))
    fb().write(PM_OE, oe)
    fb().write(PM_OD, od)
    return {"pin": pin, "source": source, "output_enable": output_enable,
            "open_drain": open_drain}

@mcp.tool()
def pin_set(pin: int, value: int) -> dict:
    """Set a raw GPIO output pin high/low (the pin must be routed to source
    'gpio' and output-enabled). For bit-banging a protocol directly."""
    if (err := _bad_pin(pin)):
        return err
    out =fb().read(PM_GPIO_OUT)
    out = (out | (1 << pin)) if value else (out & ~(1 << pin))
    fb().write(PM_GPIO_OUT, out)
    return {"pin": pin, "value": 1 if value else 0}

@mcp.tool()
def pin_read_all() -> dict:
    """Read the live logic level of all 8 flexible pins. Works regardless of
    each pin's routing -- use to observe inputs while bit-banging or probing."""
    val = fb().read(PM_GPIO_IN) & 0xFF
    return {"raw": f"{val:#04x}",
            "pins": {i: (val >> i) & 1 for i in range(8)}}

@mcp.tool()
def uart_set_rx_pin(pin: int) -> dict:
    """Choose which of the 8 flexible pins feeds the UART receiver. Combine with
    pin_route(pin, 'uart_tx') on another pin to put UART on an arbitrary 2-wire
    connection."""
    if (err := _bad_pin(pin)):
        return err
    fb().write(PM_UART_RX_PIN, pin)
    return {"uart_rx_pin": pin}

@mcp.tool()
def spi_set_miso_pin(pin: int) -> dict:
    """Choose which of the 8 flexible pins feeds the SPI master's MISO input.
    Make that pin an input with pin_route(pin, 'gpio', output_enable=False)."""
    if (err := _bad_pin(pin)):
        return err
    fb().write(PM_SPI_MISO_PIN, pin)
    return {"spi_miso_pin": pin}

@mcp.tool()
def pin_status() -> dict:
    """Read back the full pin_mux configuration: each pin's source, output
    enable, open-drain, raw GPIO output value and live input level, plus which
    pins feed UART RX and SPI MISO. Check this before rerouting."""
    srcs = fb().read_block(PM_SRC0, NPIN)
    oe = fb().read(PM_OE)
    od = fb().read(PM_OD)
    gout = fb().read(PM_GPIO_OUT)
    gin = fb().read(PM_GPIO_IN)
    pins = {}
    for i in range(NPIN):
        pins[i] = {
            "source": PM_SRC_NAME.get(srcs[i] & 0xF, f"unknown({srcs[i]})"),
            "output_enable": bool((oe >> i) & 1),
            "open_drain": bool((od >> i) & 1),
            "gpio_out": (gout >> i) & 1,
            "level": (gin >> i) & 1,
        }
    return {"pins": pins, "uart_rx_pin": fb().read(PM_UART_RX_PIN),
            "spi_miso_pin": fb().read(PM_SPI_MISO_PIN)}

def _spi_wait(mask, timeout=0.5):
    """Poll SPI STATUS until any bit in `mask` is set; returns STATUS or None."""
    deadline = time.monotonic() + timeout
    while True:
        st = fb().read(S_STATUS)
        if st & mask:
            return st
        if time.monotonic() > deadline:
            return None

@mcp.tool()
def spi_transfer(data: list[int]) -> dict:
    """Full-duplex SPI transfer: clock out `data` (bytes) on MOSI and return the
    bytes read on MISO at the same time. SS_n (active low) is held asserted for
    the whole transfer. Core is fixed at 1 MHz, mode 0, 8-bit, MSB first.

    ROUTING FIRST -- the SPI signals only reach physical pins via the pin_mux:
      pin_route(<pin>, "spi_sclk", output_enable=True)
      pin_route(<pin>, "spi_mosi", output_enable=True)
      pin_route(<pin>, "spi_cs",   output_enable=True)
      pin_route(<pin>, "gpio",     output_enable=False)   # MISO input
      spi_set_miso_pin(<that pin>)
    Unrouted, the transfer still "works" but nothing leaves the FPGA and MISO
    reads whatever pin SPI_MISO_PIN points at (0 after reset).
    To send only (ignore MISO) or read only (send 0x00/0xFF dummies), just use
    this and ignore the other half. For anything else, drive the SPI registers
    directly (see describe_memory_map)."""
    data = [b & 0xFF for b in data]
    rx = []
    fb().read(S_RXDATA)                        # drop any stale byte (clears RRDY)
    fb().write(S_STATUS, 0)                    # clear ROE/TOE
    fb().write(S_SLAVESEL, 0x1)                # SS_n[0]
    fb().write(S_CONTROL, S_SSO)               # hold SS_n across all bytes
    try:
        for b in data:
            if _spi_wait(S_TRDY) is None:
                return {"error": "SPI TRDY timeout", "sent": [hex(x) for x in data],
                        "received": [hex(x) for x in rx],
                        "status": f"{fb().read(S_STATUS):#x}"}
            fb().write(S_TXDATA, b)
            if _spi_wait(S_RRDY) is None:
                return {"error": "SPI RRDY timeout", "sent": [hex(x) for x in data],
                        "received": [hex(x) for x in rx],
                        "status": f"{fb().read(S_STATUS):#x}"}
            rx.append(fb().read(S_RXDATA) & 0xFF)
        _spi_wait(S_TMT)                       # last bit out before releasing SS_n
    finally:
        fb().write(S_CONTROL, 0)               # release SS_n
    return {"sent": [hex(b) for b in data], "received": [hex(b) for b in rx],
            "count": len(data), "sclk_hz": SPI_SCLK_HZ, "mode": 0,
            "miso_pin": fb().read(PM_SPI_MISO_PIN)}

def _i2c_setup(speed_hz):
    """Program SCL timing (core must be disabled for that), then enable."""
    global _i2c_ready
    lo, hi, hold, fast = I2C_TIMING[speed_hz]
    fb().write(I_CTRL, 0)
    fb().write(I_SCL_LOW, lo)
    fb().write(I_SCL_HIGH, hi)
    fb().write(I_SDA_HOLD, hold)
    fb().write(I_ISR, I_NACK | I_ARBLOST | I_RXOVER)
    fb().write(I_CTRL, 0x1 | fast << 1)
    _i2c_ready = True

def _i2c_wait_idle(timeout=0.5):
    deadline = time.monotonic() + timeout
    while fb().read(I_STATUS) & 1 or fb().read(I_TFR_LVL):
        if time.monotonic() > deadline:
            return False
    return True

def _i2c_txn(address, write=(), read_count=0):
    """Run one I2C transaction: optional write phase, optional read phase
    (repeated start if both), STOP at the end. Returns a raw-evidence dict."""
    if not _i2c_ready:
        _i2c_setup(100_000)
    if not 0 <= address <= 0x7F:
        return {"error": f"address {address:#x} out of 7-bit range"}
    write = [b & 0xFF for b in write]
    cmds = []
    if write or not read_count:                  # write phase (or address-only probe)
        cmds.append(("addr_w", I_STA | address << 1))
        cmds += [("data", b) for b in write]
    if read_count:
        cmds.append(("addr_r", I_STA | address << 1 | 1))
        cmds += [("read", 0)] * read_count
    kind, last = cmds[-1]
    cmds[-1] = (kind, last | I_STO)

    fb().write(I_ISR, I_NACK | I_ARBLOST | I_RXOVER)
    rx, isr = [], 0
    for kind, cmd in cmds:
        fb().write(I_TFR_CMD, cmd)
        if kind == "read":                       # drain as we go (RX FIFO = 16)
            while fb().read(I_RX_LVL) == 0:
                isr = fb().read(I_ISR)
                if isr & (I_NACK | I_ARBLOST) or not fb().read(I_STATUS) & 1:
                    break
            if fb().read(I_RX_LVL):
                rx.append(fb().read(I_RX_DATA) & 0xFF)
        isr = fb().read(I_ISR)
        if isr & (I_NACK | I_ARBLOST):
            break
    idle = _i2c_wait_idle()
    isr = fb().read(I_ISR)
    while fb().read(I_RX_LVL):                   # anything left over
        rx.append(fb().read(I_RX_DATA) & 0xFF)
    fb().write(I_ISR, I_NACK | I_ARBLOST | I_RXOVER)   # clear -> un-flush FIFOs

    nack = bool(isr & I_NACK)
    result = {"address": f"{address:#04x}", "ack": not nack,
              "arbitration_lost": bool(isr & I_ARBLOST),
              "wrote": [hex(b) for b in write], "read": [hex(b) for b in rx],
              "isr": f"{isr:#x}", "bus_idle_after": idle}
    if nack:
        result["note"] = ("NACK -- no device at this address, or it refused a "
                          "byte; core sent STOP")
    if isr & I_ARBLOST:
        result["note"] = ("arbitration lost -- SDA/SCL didn't follow the core: "
                          "check routing (open_drain + output_enable), pull-"
                          "ups, or another master on the bus")
    return result

@mcp.tool()
def i2c_set_speed(speed_hz: int = 100_000) -> dict:
    """Set the I2C bus speed: 100000 (standard) or 400000 (fast). The other
    I2C tools default to 100 kHz if this was never called."""
    if speed_hz not in I2C_TIMING:
        return {"error": f"unsupported speed {speed_hz}", "valid": sorted(I2C_TIMING)}
    _i2c_setup(speed_hz)
    lo, hi, hold, fast = I2C_TIMING[speed_hz]
    return {"speed_hz": speed_hz, "scl_low_clks": lo, "scl_high_clks": hi,
            "sda_hold_clks": hold}

@mcp.tool()
def i2c_scan(first: int = 0x08, last: int = 0x77) -> dict:
    """Find devices on the I2C bus: address each 7-bit address (write, no
    data) and record which ACK. Like i2cdetect.

    ROUTING FIRST -- I2C only reaches physical pins via the pin_mux:
      pin_route(<pin>, "i2c_scl", output_enable=True, open_drain=True)
      pin_route(<pin>, "i2c_sda", output_enable=True, open_drain=True)
    The bus needs pull-ups; the FPGA pins have weak ones, most breakout
    boards have proper 4.7k ones. Unrouted, every address NACKs."""
    found, arblost = [], []
    for a in range(first, last + 1):
        r = _i2c_txn(a)
        if r["arbitration_lost"]:
            arblost.append(hex(a))
        elif r["ack"]:
            found.append(hex(a))
    out = {"found": found, "scanned": f"{first:#04x}..{last:#04x}"}
    if arblost:
        out["arbitration_lost_at"] = arblost
        out["note"] = "bus problem: check routing/pull-ups (see i2c_write note)"
    return out

@mcp.tool()
def i2c_write(address: int, data: list[int]) -> dict:
    """Write bytes to a 7-bit I2C address: START, addr+W, data..., STOP.
    Returns whether the device ACKed plus the raw ISR. To set a device
    register, data is usually [register, value, ...]. Route the pins first
    (see i2c_scan)."""
    return _i2c_txn(address, write=data)

@mcp.tool()
def i2c_read(address: int, count: int) -> dict:
    """Read `count` bytes from a 7-bit I2C address: START, addr+R, bytes...,
    STOP. Route the pins first (see i2c_scan)."""
    return _i2c_txn(address, read_count=count)

@mcp.tool()
def i2c_write_read(address: int, data: list[int], count: int) -> dict:
    """Write then read with a repeated START (no STOP in between) -- the
    usual way to read a device register: data=[register], count=bytes.
    Route the pins first (see i2c_scan)."""
    return _i2c_txn(address, write=data, read_count=count)

def _hex_read():
    """Segment bytes for HEX0..HEX5 (index = digit number)."""
    lo = fb().read(B_HEX_LO); hi = fb().read(B_HEX_HI)
    return [(lo >> 8 * i) & 0xFF for i in range(4)] + \
           [(hi >> 8 * i) & 0xFF for i in range(2)]

def _hex_write(segs):
    lo = segs[0] | segs[1] << 8 | segs[2] << 16 | segs[3] << 24
    hi = segs[4] | segs[5] << 8
    fb().write(B_HEX_LO, lo)
    fb().write(B_HEX_HI, hi)

def _hex_report(segs):
    # left-to-right as seen on the board (HEX5 first)
    shown = ""
    for d in reversed(range(NDIGIT)):
        s = segs[d]
        shown += SEG_DECODE.get(s & 0x7F, "?") + ("." if s & SEG_DP else "")
    return {"shown": shown,
            "segments": {f"HEX{d}": f"{segs[d]:#04x}" for d in range(NDIGIT)}}

@mcp.tool()
def display_text(text: str, align: str = "left") -> dict:
    """Show text on the six seven-segment displays (HEX5 = leftmost).
    Supports 0-9, A-F and the letters a 7-seg can draw (G H I J L n o P q r
    S t U u y, case is adjusted where only one form exists), plus '-', '_',
    '=', ' '. A '.' lights the previous digit's decimal point. At most 6
    digits. align: 'left' or 'right'. Empty text blanks the display.
    Returns the raw segment bytes written; for arbitrary patterns use
    display_raw or write the board_io registers directly."""
    cells = []
    for ch in text:
        if ch == ".":
            if cells and not cells[-1] & SEG_DP:
                cells[-1] |= SEG_DP
            else:
                cells.append(SEG_DP)
            continue
        key = ch if ch in SEG_FONT else SEG_ALIAS.get(ch, ch.upper())
        if key not in SEG_FONT:
            return {"error": f"character {ch!r} can't be shown on 7-seg",
                    "valid": "".join(sorted(SEG_FONT)) + "."}
        cells.append(SEG_FONT[key])
    if len(cells) > NDIGIT:
        return {"error": f"{len(cells)} digits, display has {NDIGIT}"}
    pad = [0] * (NDIGIT - len(cells))
    cells = cells + pad if align == "left" else pad + cells
    segs = list(reversed(cells))               # cells[0] is HEX5
    _hex_write(segs)
    return _hex_report(segs)

@mcp.tool()
def display_hex(value: int) -> dict:
    """Show a value as 6 hex digits (low 24 bits) on the seven-segment
    displays. Handy for showing a register value on the board."""
    return display_text(f"{value & 0xFFFFFF:06X}")

@mcp.tool()
def display_raw(digit: int, segments: int) -> dict:
    """Set one digit (0 = HEX0 rightmost .. 5 = HEX5 leftmost) to a raw
    segment pattern: bit0..6 = segments a..g, bit7 = decimal point, 1 = lit.
    Other digits are left unchanged."""
    if not 0 <= digit < NDIGIT:
        return {"error": f"digit {digit} out of range", "valid": f"0..{NDIGIT - 1}"}
    segs = _hex_read()
    segs[digit] = segments & 0xFF
    _hex_write(segs)
    return _hex_report(segs)

@mcp.tool()
def display_read() -> dict:
    """Read back what the seven-segment displays are showing: raw segment
    byte per digit plus a best-effort decode ('?' = not a known character)."""
    return _hex_report(_hex_read())

@mcp.tool()
def led_set(mask: int) -> dict:
    """Set the 10 red LEDs: bit i = LEDR[i], 1 = on."""
    fb().write(B_LEDR, mask & 0x3FF)
    return {"ledr": f"{mask & 0x3FF:#05x}",
            "on": [i for i in range(10) if (mask >> i) & 1]}

@mcp.tool()
def led_get() -> dict:
    """Read back the 10 red LEDs (bit i = LEDR[i], 1 = on)."""
    v = fb().read(B_LEDR) & 0x3FF
    return {"ledr": f"{v:#05x}", "on": [i for i in range(10) if (v >> i) & 1]}

@mcp.tool()
def logic_capture(sample_rate_hz: int = 1_000_000, trigger_channel: int = 0,
                  trigger_edge: str = "falling", depth: int = 128) -> dict:
    """Capture digital activity on the probe channels.

    CHANNEL MAP:
      channels 0-7  = the 8 flexible muxable pins (GPIO[7:0]) -- the SAME pins
                      pin_route/pin_set drive. So you can route a signal onto a
                      pin and capture it here, or snoop an unknown connection on
                      those pins while leaving them as inputs.
      channels 8-23 = fixed probe range (GPIO[10:25]).

    Arms the analyzer, waits for the trigger, returns the raw samples (one
    32-bit word per time step, one bit per channel) PLUS which channels were
    active. Decode the raw samples yourself or with sigrok.
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
    # summary: which channels toggled (0-7 = muxable pins, 8-23 = fixed probes)
    active = []
    for c in range(24):
        vals = set((w >> c) & 1 for w in samples)
        if len(vals) > 1:
            active.append(c)
    return {
        "captured": depth,
        "sample_rate_hz": sample_rate_hz,
        "active_channels": active,          # hint (0-7 muxable, 8-23 fixed)
        "samples": samples,                 # raw -- decode yourself / with sigrok
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")