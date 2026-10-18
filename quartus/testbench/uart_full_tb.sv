// ============================================================================
// tb_uart_full.v -- testbench for uart_full.v
//
// Internal loopback: the core's uart_txd is wired back to its own uart_rxd,
// so a transmitted byte is received by the same core. We verify the byte
// round-trips through TX FSM -> TX buffer -> wire -> RX FSM -> RX buffer.
//
// Run (Questa):
//   vlog uart_full.v tb_uart_full.v
//   vsim -c tb_uart_full -do "run -all; quit"
// ============================================================================
`timescale 1ns/1ps

module tb_uart_full;

    // small buffers + fast baud so sim is quick
    localparam CLK_FREQ = 50_000_000;
    localparam BAUD     = 115_200;        // divisor ~434
    localparam BUF_LEN  = 256;
    localparam ABITS    = 11;

    localparam TX_BASE = 512;   // word index of TX buffer
    localparam RX_BASE = 768;   // word index of RX buffer

    reg                 clk = 0, reset = 1;
    wire                uart_txd;
    wire                uart_rxd;

    reg  [ABITS-1:0]    avs_address = 0;
    reg                 avs_read = 0, avs_write = 0;
    reg  [31:0]         avs_writedata = 0;
    wire [31:0]         avs_readdata;
    wire                avs_readdatavalid, avs_waitrequest;

    // LOOPBACK: tx -> rx
    assign uart_rxd = uart_txd;

    // register word indices
    localparam CTRL=0, STAT=1, BAUDDIV=2, TXLEN=3, RXCOUNT=4;

    uart_full #(.CLK_FREQ(CLK_FREQ), .BAUD(BAUD), .BUF_LEN(BUF_LEN), .ABITS(ABITS)) dut (
        .clk(clk), .reset(reset),
        .uart_txd(uart_txd), .uart_rxd(uart_rxd),
        .avs_address(avs_address), .avs_read(avs_read), .avs_write(avs_write),
        .avs_writedata(avs_writedata), .avs_readdata(avs_readdata),
        .avs_readdatavalid(avs_readdatavalid), .avs_waitrequest(avs_waitrequest)
    );

    always #10 clk = ~clk;   // 50 MHz

    // ---- bus helpers ----
    task wr(input [ABITS-1:0] a, input [31:0] d);
        begin @(posedge clk); avs_address<=a; avs_writedata<=d; avs_write<=1;
              @(posedge clk); avs_write<=0; end
    endtask

    reg [31:0] rdv;
    task rd(input [ABITS-1:0] a);
        begin @(posedge clk); avs_address<=a; avs_read<=1;
              @(posedge clk); avs_read<=0;
              @(posedge clk); rdv = avs_readdata; end
    endtask

    integer errors = 0;
    task check(input [31:0] got, exp, input [255:0] name);
        begin
            if (got!==exp) begin $display("  FAIL %0s: got %h exp %h",name,got,exp);
                                 errors=errors+1; end
            else $display("  ok   %0s = %h", name, got);
        end
    endtask

    integer i;
    reg [7:0] msg [0:3];

    initial begin
        reset=1; repeat(4) @(posedge clk); reset=0; @(posedge clk);

        // ============ TEST 1: registers ============
        $display("\n=== TEST 1: registers ===");
        rd(BAUDDIV);
        check(rdv, (CLK_FREQ/BAUD)-1, "BAUDDIV default");
        wr(TXLEN, 32'd3); rd(TXLEN); check(rdv, 32'd3, "TX_LEN");

        // ============ TEST 2: TX -> loopback -> RX ============
        // send 3 bytes, expect them to appear in the RX buffer.
        $display("\n=== TEST 2: loopback (TX -> RX) ===");
        msg[0]=8'h55; msg[1]=8'hA3; msg[2]=8'h0F;
        wr(CTRL, 32'd6);                 // RX_CLEAR | TX_CLEAR
        for (i=0;i<3;i=i+1) wr(TX_BASE+i, msg[i]);   // load TX buffer
        wr(TXLEN, 32'd3);
        wr(CTRL, 32'd1);                 // TX_GO

        // wait for 3 bytes to be received (each byte ~10 bit periods;
        // bit period ~434 clks; 3 bytes ~13000 clks -> give plenty)
        for (i=0;i<60000;i=i+1) begin
            @(posedge clk);
            rd(RXCOUNT);
            if (rdv >= 3) i = 60000;     // got them all
        end

        rd(RXCOUNT);
        $display("  RX_COUNT = %0d (expected 3)", rdv);
        check(rdv, 32'd3, "received 3 bytes");

        // read the RX buffer and compare
        for (i=0;i<3;i=i+1) begin
            rd(RX_BASE+i);
            $display("    rx[%0d] = %02h (sent %02h)", i, rdv[7:0], msg[i]);
            check(rdv[7:0], msg[i], "rx byte");
        end

        // ============ TEST 3: framing errors stay zero on clean loopback ===
        $display("\n=== TEST 3: no framing errors on clean loopback ===");
        rd(STAT);
        $display("  STATUS = %h  framing_errors=%0d", rdv, rdv[27:16]);
        check(rdv[27:16], 12'd0, "framing errors == 0");

        // ============ summary ============
        $display("\n========================================");
        if (errors==0) $display("ALL CHECKS PASSED");
        else $display("%0d CHECK(S) FAILED", errors);
        $display("========================================");
        $finish;
    end

    // timeout
    initial begin #5_000_000;
        $display("TIMEOUT -- FSM may be stuck (TX or RX not completing)");
        $finish;
    end

endmodule