from fpga_bench import FpgaBench
import time
fb = FpgaBench()
LA = 0x0
fb.write(LA+0x00, 0x2); fb.write(LA+0x08, 10); fb.write(LA+0x0C, 0x1)
fb.write(LA+0x10, 0x1); fb.write(LA+0x14, 0); fb.write(LA+0x18, 32)
fb.write(LA+0x1C, 0); fb.write(LA+0x00, 0x1)
time.sleep(0.05)
if fb.read(LA+0x04) & 0x4:
    s = fb.read_block(LA+0x2000, 32)
    for c in range(16):
        vals = set((w>>c)&1 for w in s)
        print(f"ch{c}: {'TOGGLING' if len(vals)>1 else f'stuck {vals.pop()}'}")
fb.close()