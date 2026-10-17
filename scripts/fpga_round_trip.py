"""
fpga_round_trip.py -- prove the bidirectional memory path works.

Writes a pattern into the On-Chip Memory via write_block (AI -> FPGA),
reads it back via read_block (FPGA -> AI), and shows every step.

Set BASE to the On-Chip Memory's base address from the Qsys Address Map tab.
"""

from fpga_bench import FpgaBench
import time

BASE  = 0x0     # On-Chip Memory base (from Address Map). Switches are at 0x1000 now.
COUNT = 256     # words to test

def hexlist(xs, n=8):
    return " ".join(f"{x:08x}" for x in xs[:n]) + (" ..." if len(xs) > n else "")

fb = FpgaBench()
print("=" * 60)
print(f"connected to JTAG bridge.  ping -> {fb.ping()}")
print(f"testing On-Chip Memory at base {BASE:#010x}, {COUNT} words")
print("=" * 60)

# --- 1. show the memory BEFORE we write (should be zeros / garbage) ---
before = fb.read_block(BASE, COUNT)
print(f"\n[1] memory BEFORE write:")
print(f"    {hexlist(before)}")

# --- 2. build a recognizable pattern and write it ---
pattern = [0xDEAD0000 + i for i in range(COUNT)]
print(f"\n[2] writing pattern via write_block (AI -> FPGA):")
print(f"    {hexlist(pattern)}")
t0 = time.time()
fb.write_block(BASE, pattern)
t_write = time.time() - t0
print(f"    write of {COUNT} words took {t_write*1000:.1f} ms"
      f"  ({t_write/COUNT*1e6:.0f} us/word)")

# --- 3. read it back ---
print(f"\n[3] reading back via read_block (FPGA -> AI):")
t0 = time.time()
back = fb.read_block(BASE, COUNT)
t_read = time.time() - t0
print(f"    {hexlist(back)}")
print(f"    read of {COUNT} words took {t_read*1000:.1f} ms"
      f"  ({t_read/COUNT*1e6:.0f} us/word)")

# --- 4. verify ---
print(f"\n[4] verifying ...")
if back == pattern:
    print(f"    *** RAM ROUND-TRIP OK ***  all {COUNT} words match")
else:
    mismatches = [(i, pattern[i], back[i])
                  for i in range(COUNT) if pattern[i] != back[i]]
    print(f"    MISMATCH: {len(mismatches)} of {COUNT} words differ")
    for i, w, r in mismatches[:5]:
        print(f"      word {i}: wrote {w:08x}  read {r:08x}")

# --- 5. prove single read/write too, with a sentinel ---
print(f"\n[5] single write/read at {BASE:#x}:")
fb.write(BASE, 0xCAFEBABE)
v = fb.read(BASE)
print(f"    wrote 0xCAFEBABE, read {v:08x}  ->  {'OK' if v == 0xCAFEBABE else 'FAIL'}")

print("\n" + "=" * 60)
fb.close()
print("done.")