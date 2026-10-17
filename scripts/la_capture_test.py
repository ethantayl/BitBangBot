"""
la_uart_loopback.py -- capture the UART streamer's own TX with the logic
analyzer, and decode it back to the byte that was sent. Validates BOTH cores
against each other.

Wiring:
  - Jumper: GPIO[3] (UART TX, header pin 4) --> GPIO[10] (probe ch0, header pin 13)
  - Nothing else needed (same board, shared ground).

Probe mapping: probe = {16'h0000, GPIO[25:10]}, so GPIO[10] = probe channel 0.
"""
from fpga_bench import FpgaBench
import time

# ---- addresses (from Qsys Address Map) ----
LA       = 0x0000_0000
STREAMER = 0x0000_8000

LA_CONTROL, LA_STATUS, LA_SAMPLE_DIV = LA+0x00, LA+0x04, LA+0x08
LA_TRIG_MASK, LA_TRIG_VALUE, LA_TRIG_EDGE = LA+0x0C, LA+0x10, LA+0x14
LA_DEPTH, LA_PRE_TRIG, LA_BUFFER = LA+0x18, LA+0x1C, LA+0x2000

ST_CONTROL, ST_STATUS, ST_LENGTH, ST_BUFFER = \
    STREAMER+0x00, STREAMER+0x04, STREAMER+0x08, STREAMER+0x1000

PROBE_CH  = 0
BAUD      = 115200
CLK       = 50_000_000
OVERSAMP  = 8                      # sample the UART 8x per bit
SAMPLE_HZ = BAUD * OVERSAMP
DEPTH     = 120                    # ~1.5 bytes worth at 8x oversample
TEST_BYTE = 0x55                   # 0b01010101 -- alternating

fb = FpgaBench()
print("connected:", fb.ping())

div = CLK // SAMPLE_HZ - 1
SPB = SAMPLE_HZ // BAUD            # samples per UART bit (= OVERSAMP)

def arm():
    fb.write(LA_CONTROL, 0x2)                  # CLEAR
    fb.write(LA_SAMPLE_DIV, div)
    fb.write(LA_TRIG_MASK, 1 << PROBE_CH)
    fb.write(LA_TRIG_VALUE, 0 << PROBE_CH)     # trigger on FALLING -> start bit
    fb.write(LA_TRIG_EDGE, 1 << PROBE_CH)      # edge mode
    fb.write(LA_DEPTH, DEPTH)
    fb.write(LA_PRE_TRIG, 0)
    fb.write(LA_CONTROL, 0x1)                  # ARM

def send(b):
    fb.write_block(ST_BUFFER, [b])
    fb.write(ST_LENGTH, 1)
    fb.write(ST_CONTROL, 1)                    # GO

# arm, then send the byte repeatedly until the start-bit edge is caught
arm()
for attempt in range(50):
    send(TEST_BYTE)
    time.sleep(0.01)
    if fb.read(LA_STATUS) & 0x4:               # DONE
        print(f"captured on attempt {attempt+1}")
        break
else:
    print("no capture -- is the GPIO[3]->GPIO[10] jumper connected?")
    fb.close(); exit()

# read the captured UART line
samples = fb.read_block(LA_BUFFER, DEPTH)
line = [(s >> PROBE_CH) & 1 for s in samples]

print("\ncaptured UART waveform (ch0):")
for r in range(0, len(line), 60):
    print("  " + "".join(str(b) for b in line[r:r+60]))

# decode: capture triggered on the falling edge (start bit) at sample 0-ish.
# data bit i center is at: start + 1.5 bits + i bits, in samples.
try:
    start = line.index(0)                      # first low = start bit
    bits = []
    for i in range(8):
        center = start + SPB + SPB//2 + SPB*i   # center of data bit i
        bits.append(line[center])
    decoded = sum(b << i for i, b in enumerate(bits))   # UART is LSB-first
    print(f"\nsent:    {TEST_BYTE:#04x}  (0b{TEST_BYTE:08b})")
    print(f"decoded: {decoded:#04x}  (0b{decoded:08b})")
    if decoded == TEST_BYTE:
        print("\n*** MATCH -- logic analyzer captured & decoded the UART byte! ***")
        print("    Both cores validated against each other.")
    else:
        print("\nmismatch -- waveform captured but decode is off (bit alignment).")
except (ValueError, IndexError) as e:
    print(f"\ncould not decode: {e}")

fb.close()