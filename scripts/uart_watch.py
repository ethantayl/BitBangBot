"""
uart_watch.py -- print one line each time a complete message lands in the
uart_full RX buffer. Read-only: polls RX_COUNT and never clears the buffer,
so the agent still drains it with the MCP uart_receive tool.

Used as an event source (e.g. Claude Code's Monitor tool) so an agent can
answer UART messages without being prompted. Needs jtag_server.tcl running
(it accepts several clients, so this runs alongside the MCP server).

A message is "complete" once RX_COUNT has stopped growing for IDLE_S.
Output lines:
    UART_RX count=<n>       new data is waiting
    UART_WATCH_ERR <msg>    lost the Tcl server (retries until it is back)
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fpga_bench import FpgaBench

U_STATUS  = 0x0000_8004   # UART + 0x04: [27:16] framing errors
U_RXCOUNT = 0x0000_8010   # UART + 0x10 (see fpga_mcp_server.py)
POLL_S = 0.2
IDLE_S = 0.4

def main():
    fb = None
    reported = 0          # count we last announced; re-arms when it changes
    last, last_change = 0, time.time()
    err_shown = False
    while True:
        try:
            if fb is None:
                fb = FpgaBench()
                if err_shown:
                    print("UART_WATCH reconnected", flush=True)
                err_shown = False
            count = fb.read(U_RXCOUNT)
            # framing errors also mean activity (e.g. wrong baud); fold them
            # into the "something arrived" count so they trigger a report too
            ferr = (fb.read(U_STATUS) >> 16) & 0xFFF
            count += ferr
        except (OSError, ValueError, RuntimeError) as e:
            if not err_shown:
                print(f"UART_WATCH_ERR {e}", flush=True)
                err_shown = True
            try:
                fb and fb.close()
            except Exception:
                pass
            fb = None
            time.sleep(2)
            continue

        now = time.time()
        if count != last:
            last, last_change = count, now
        if count < reported:           # buffer was drained
            reported = count
        if count > reported and now - last_change >= IDLE_S:
            print(f"UART_RX count={count - ferr} framing_errors={ferr}", flush=True)
            reported = count
        time.sleep(POLL_S)

if __name__ == "__main__":
    main()
