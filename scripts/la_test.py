"""
la_test.py -- incremental bring-up for the logic analyzer.

Run after flashing the design with the analyzer at base 0x0.
Each STAGE builds on the last; if one fails, stop and debug THAT stage
(SignalTap on the internal FSM is your friend here).

Prereq: Tcl server running, board flashed.
"""

from fpga_bench import FpgaBench
import time

# ---- register map (analyzer base = 0x0) ----
LA         = 0x0
CONTROL    = LA + 0x00      # W: bit0 ARM, bit1 CLEAR
STATUS     = LA + 0x04      # R: bit0 armed, bit1 triggered, bit2 done, [27:16] captured
SAMPLE_DIV = LA + 0x08
TRIG_MASK  = LA + 0x0C
TRIG_VALUE = LA + 0x10
TRIG_EDGE  = LA + 0x14
DEPTH      = LA + 0x18
PRE_TRIG   = LA + 0x1C
CHANNELS   = LA + 0x20      # R: number of channels
BUF_WORDS  = LA + 0x24      # R: buffer depth
BUFFER     = LA + 0x2000    # sample buffer

fb = FpgaBench()
print("connected:", fb.ping())

# =====================================================================
# STAGE 1 -- register read/write round-trip (no capture)
# Proves the Avalon slave works before trusting any capture logic.
# =====================================================================
print("\n=== STAGE 1: registers ===")
print(f"CHANNELS  = {fb.read(CHANNELS)}   (expect 32)")
print(f"BUF_WORDS = {fb.read(BUF_WORDS)}  (expect 2048)")

fb.write(SAMPLE_DIV, 7)
fb.write(DEPTH, 64)
fb.write(TRIG_MASK, 0xABCD)
print(f"SAMPLE_DIV readback = {fb.read(SAMPLE_DIV)}  (expect 7)")
print(f"DEPTH      readback = {fb.read(DEPTH)}       (expect 64)")
print(f"TRIG_MASK  readback = {hex(fb.read(TRIG_MASK))}  (expect 0xabcd)")
print("STAGE 1 OK if the readbacks match.\n")

# =====================================================================
# STAGE 2 -- simplest capture: no pre-trigger, trigger fires immediately.
# We set the trigger to match whatever the pins currently are, so it
# triggers at once and just records DEPTH samples of the current state.
# =====================================================================
print("=== STAGE 2: immediate capture (no pre-trigger) ===")
fb.write(CONTROL, 0x2)          # CLEAR first
fb.write(SAMPLE_DIV, 49)        # ~1 MHz sampling (50MHz/50)
fb.write(PRE_TRIG, 0)           # SIMPLE path
fb.write(DEPTH, 64)
# trigger: watch channel 0 for level == its... we don't know its state,
# so trigger on "channel 0 is 0 OR 1" isn't expressible; instead trigger
# on channel 0 == 0 (level). If ch0 happens to be high it won't fire --
# so we try both. Easiest: set mask to a channel, value to match current.
ch0 = fb.read(BUFFER) & 1   # not meaningful yet; just pick value 0
fb.write(TRIG_MASK, 0x1)        # watch channel 0
fb.write(TRIG_VALUE, 0x0)       # level: channel 0 == 0
fb.write(TRIG_EDGE, 0x0)        # level trigger
fb.write(CONTROL, 0x1)          # ARM

# wait for done (with timeout)
for _ in range(200):
    st = fb.read(STATUS)
    if st & 0x4:                # done
        break
    time.sleep(0.01)

st = fb.read(STATUS)
captured = (st >> 16) & 0xFFF
print(f"STATUS = {hex(st)}  armed={st&1} triggered={(st>>1)&1} "
      f"done={(st>>2)&1} captured={captured}")

if st & 0x4:
    samples = fb.read_block(BUFFER, 64)
    print("first 16 samples (all 32 channels each):")
    for i, s in enumerate(samples[:16]):
        print(f"  [{i:2}] {s:032b}")
    print("STAGE 2 OK if you see sensible pin states (GPIO levels).")
else:
    print("NOT DONE -- trigger didn't fire (ch0 may be high).")
    print("Try TRIG_VALUE=0x1 (trigger on ch0==1), or check SignalTap.")

fb.close()
print("\ndone.")