// ============================================================================
// tb_logic_analyzer.v -- testbench for logic_analyzer.v
//
// Exercises: register read/write, sample-rate divider, level + edge triggers,
// and buffer contents. Drives the Avalon slave the way the real bus does.
//
// Run (ModelSim/Questa):
//   vlog logic_analyzer.v tb_logic_analyzer.v
//   vsim -c tb_logic_analyzer -do "run -all"
// or point your simulator at both files with tb_logic_analyzer as top.
// ============================================================================
`timescale 1ns/1ps

module tb_logic_analyzer;

    // use a SMALL buffer so sim is fast and the buffer base is low
    localparam WIDTH     = 32;
    localparam BUF_WORDS = 64;
    localparam ABITS     = 13;
    localparam BUF_BASE  = 2048;   // must match the core's BUF_BASE

    reg                   clk = 0;
    reg                   reset = 1;
    reg  [WIDTH-1:0]      probe = 0;

    reg  [ABITS-1:0]      avs_address = 0;
    reg                   avs_read = 0;
    reg                   avs_write = 0;
    reg  [31:0]           avs_writedata = 0;
    wire [31:0]           avs_readdata;
    wire                  avs_readdatavalid;
    wire                  avs_waitrequest;

    // register offsets (WORD indices, as the core decodes avs_address)
    localparam CTRL=0, STAT=1, SDIV=2, TMASK=3, TVAL=4, TEDGE=5, DEPTH=6, PRET=7,
               CHAN=8, BUFW=9;

    // DUT
    logic_analyzer #(.WIDTH(WIDTH), .BUF_WORDS(BUF_WORDS), .ABITS(ABITS)) dut (
        .clk(clk), .reset(reset), .probe(probe),
        .avs_address(avs_address), .avs_read(avs_read), .avs_write(avs_write),
        .avs_writedata(avs_writedata), .avs_readdata(avs_readdata),
        .avs_readdatavalid(avs_readdatavalid), .avs_waitrequest(avs_waitrequest)
    );

    // 50 MHz clock
    always #10 clk = ~clk;

    // ---- Avalon bus helpers ----
    task wr(input [ABITS-1:0] a, input [31:0] d);
        begin
            @(posedge clk);
            avs_address <= a; avs_writedata <= d; avs_write <= 1;
            @(posedge clk);
            avs_write <= 0;
        end
    endtask

    reg [31:0] rd_result;
    task rd(input [ABITS-1:0] a);
        begin
            @(posedge clk);
            avs_address <= a; avs_read <= 1;
            @(posedge clk);
            avs_read <= 0;
            // readdatavalid asserts one cycle after read
            @(posedge clk);
            rd_result = avs_readdata;
        end
    endtask

    integer errors = 0;
    task check(input [31:0] got, input [31:0] exp, input [255:0] name);
        begin
            if (got !== exp) begin
                $display("  FAIL %0s: got %h expected %h", name, got, exp);
                errors = errors + 1;
            end else begin
                $display("  ok   %0s = %h", name, got);
            end
        end
    endtask

    // ---- stimulus ----
    integer i;
    initial begin
        // reset
        reset = 1; probe = 0;
        repeat (4) @(posedge clk);
        reset = 0;
        @(posedge clk);

        // ================= TEST 1: register read/write =================
        $display("\n=== TEST 1: registers ===");
        rd(CHAN);  check(rd_result, WIDTH,     "CHANNELS");
        rd(BUFW);  check(rd_result, BUF_WORDS, "BUF_WORDS");

        wr(SDIV, 32'd3);   rd(SDIV); check(rd_result, 32'd3,    "SAMPLE_DIV");
        wr(DEPTH,32'd16);  rd(DEPTH);check(rd_result, 32'd16,   "DEPTH");
        wr(TMASK,32'hF0);  rd(TMASK);check(rd_result, 32'hF0,   "TRIG_MASK");

        // ================= TEST 2: level trigger capture =================
        // Configure: sample every clock (div=0), depth=8, no pre-trigger,
        // trigger when channel 0 == 1 (level).
        $display("\n=== TEST 2: level trigger (ch0 == 1) ===");
        wr(CTRL, 32'd2);        // CLEAR
        wr(SDIV, 32'd0);        // full rate
        wr(PRET, 32'd0);        // no pre-trigger
        wr(DEPTH,32'd8);
        wr(TMASK,32'd1);        // watch ch0
        wr(TVAL, 32'd1);        // level: ch0 == 1
        wr(TEDGE,32'd0);        // level mode

        probe = 32'h0000_0000;  // ch0 low -> should NOT trigger yet
        wr(CTRL, 32'd1);        // ARM
        repeat (10) @(posedge clk);

        rd(STAT);
        $display("  after arming, ch0 low: STATUS=%h (triggered bit1=%b)",
                 rd_result, rd_result[1]);

        // now drive ch0 high -> trigger should fire
        probe = 32'h0000_00AB;  // ch0=1, plus some pattern on other channels
        repeat (20) @(posedge clk);

        rd(STAT);
        $display("  after ch0 high: STATUS=%h  armed=%b trig=%b done=%b captured=%0d",
                 rd_result, rd_result[0], rd_result[1], rd_result[2],
                 rd_result[27:16]);
        check(rd_result[2], 1'b1, "DONE after trigger");

        // read the buffer -- samples should show our pattern (0xAB in low byte)
        $display("  captured samples:");
        for (i = 0; i < 8; i = i + 1) begin
            rd(BUF_BASE + i);
            $display("    [%0d] %h", i, rd_result);
        end

        // ================= TEST 3: edge trigger =================
        $display("\n=== TEST 3: edge trigger (ch1 rising) ===");
        wr(CTRL, 32'd2);        // CLEAR
        wr(DEPTH,32'd8);
        wr(PRET, 32'd0);
        wr(TMASK,32'd2);        // watch ch1
        wr(TVAL, 32'd2);        // rising (tval bit set)
        wr(TEDGE,32'd2);        // edge mode on ch1

        probe = 32'h0000_0000;  // ch1 low
        wr(CTRL, 32'd1);        // ARM
        repeat (5) @(posedge clk);
        probe = 32'h0000_0002;  // ch1 rises -> should trigger
        repeat (20) @(posedge clk);

        rd(STAT);
        $display("  edge trigger: STATUS=%h  trig=%b done=%b",
                 rd_result, rd_result[1], rd_result[2]);
        check(rd_result[2], 1'b1, "DONE after edge trigger");

        // ================= summary =================
        $display("\n========================================");
        if (errors == 0)
            $display("ALL CHECKS PASSED");
        else
            $display("%0d CHECK(S) FAILED", errors);
        $display("========================================");
        $finish;
    end

    // safety timeout
    initial begin
        #100000;
        $display("TIMEOUT -- simulation ran too long (FSM may be stuck)");
        $finish;
    end

endmodule
