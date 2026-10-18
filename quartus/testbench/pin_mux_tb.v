// ============================================================================
// tb_pin_mux.v -- testbench for pin_mux.v
//
// Verifies the crossbar: raw-GPIO output, peripheral routing (UART TX onto a
// pin), output-enable/tri-state, open-drain behavior, and input readback
// (reading a pin level that an external driver sets).
//
// The `pins` net is inout. The testbench models the outside world with pull-ups
// (weak 1) and can also actively drive a pin to test input readback.
//
// Run (Questa):
//   vlog pin_mux.v tb_pin_mux.v
//   vsim -c tb_pin_mux -do "run -all; quit"
// ============================================================================
`timescale 1ns/1ps

module tb_pin_mux;

    localparam NPIN  = 8;
    localparam ABITS = 8;

    reg                 clk = 0, reset = 1;
    wire  [NPIN-1:0]    pins;

    // peripheral signals we drive into the mux
    reg                 uart_tx_in = 1'b1;
    wire                uart_rx_out;
    reg                 spi_sclk_in=0, spi_mosi_in=0, spi_cs_in=1;
    wire                spi_miso_out;
    reg                 i2c_scl_in=1, i2c_sda_in=1;
    wire                i2c_sda_out;

    reg  [ABITS-1:0]    avs_address=0;
    reg                 avs_read=0, avs_write=0;
    reg  [31:0]         avs_writedata=0;
    wire [31:0]         avs_readdata;
    wire                avs_readdatavalid, avs_waitrequest;

    // ---- model the outside world on `pins` ----
    // weak pull-ups on every pin (like real floating CMOS / a bus idle-high)
    pullup(pins[0]); pullup(pins[1]); pullup(pins[2]); pullup(pins[3]);
    pullup(pins[4]); pullup(pins[5]); pullup(pins[6]); pullup(pins[7]);
    // an optional strong external driver for testing input readback:
    reg        ext_drive_en = 0;
    reg        ext_drive_val = 0;
    reg [2:0]  ext_drive_pin = 0;
    assign pins = ext_drive_en ? (ext_drive_val << ext_drive_pin)
                               : {NPIN{1'bz}};
    // note: the line above drives ONE pin; others stay hi-Z (pulled up).
    // (simple model; good enough for these checks)

    // register word offsets
    localparam PIN_OE=8'h08, PIN_OD=8'h09, GPIO_OUT=8'h0A, GPIO_IN=8'h0B,
               UART_RX_PIN=8'h0C;

    pin_mux #(.NPIN(NPIN), .ABITS(ABITS)) dut (
        .clk(clk), .reset(reset), .pins(pins),
        .uart_tx_in(uart_tx_in), .uart_rx_out(uart_rx_out),
        .spi_sclk_in(spi_sclk_in), .spi_mosi_in(spi_mosi_in),
        .spi_cs_in(spi_cs_in), .spi_miso_out(spi_miso_out),
        .i2c_scl_in(i2c_scl_in), .i2c_sda_in(i2c_sda_in), .i2c_sda_out(i2c_sda_out),
        .avs_address(avs_address), .avs_read(avs_read), .avs_write(avs_write),
        .avs_writedata(avs_writedata), .avs_readdata(avs_readdata),
        .avs_readdatavalid(avs_readdatavalid), .avs_waitrequest(avs_waitrequest)
    );

    always #10 clk = ~clk;

    task wr(input [ABITS-1:0] a, input [31:0] d);
        begin @(posedge clk); avs_address<=a; avs_writedata<=d; avs_write<=1;
              @(posedge clk); avs_write<=0; end
    endtask
    reg [31:0] rdv;
    task rd(input [ABITS-1:0] a);
        begin @(posedge clk); avs_address<=a; avs_read<=1;
              @(posedge clk); avs_read<=0; @(posedge clk); rdv=avs_readdata; end
    endtask

    integer errors = 0;
    task check(input got, exp, input [255:0] name);
        begin if (got!==exp) begin
                $display("  FAIL %0s: got %b exp %b",name,got,exp); errors=errors+1;
              end else $display("  ok   %0s = %b",name,got); end
    endtask

    initial begin
        reset=1; repeat(4) @(posedge clk); reset=0; @(posedge clk);

        // ===== TEST 1: raw GPIO output, push-pull =====
        $display("\n=== TEST 1: raw GPIO push-pull ===");
        wr(0, 32'd0);            // pin0 source = raw GPIO
        wr(PIN_OE, 32'h01);      // pin0 output enabled
        wr(PIN_OD, 32'h00);      // push-pull
        wr(GPIO_OUT, 32'h00);    // drive 0
        repeat(4) @(posedge clk);
        check(pins[0], 1'b0, "pin0 driven low");
        wr(GPIO_OUT, 32'h01);    // drive 1
        repeat(4) @(posedge clk);
        check(pins[0], 1'b1, "pin0 driven high");

        // ===== TEST 2: tri-state (OE off -> pin floats, pulled up) =====
        $display("\n=== TEST 2: tri-state ===");
        wr(PIN_OE, 32'h00);      // pin0 output DISABLED
        repeat(4) @(posedge clk);
        check(pins[0], 1'b1, "pin0 hi-Z -> pulled up");

        // ===== TEST 3: route UART TX onto pin 1 =====
        $display("\n=== TEST 3: UART TX routed to pin 1 ===");
        wr(1, 32'd1);            // pin1 source = UART_TX (code 1)
        wr(PIN_OE, 32'h02);      // pin1 output enabled
        uart_tx_in = 1'b0;
        repeat(4) @(posedge clk);
        check(pins[1], 1'b0, "pin1 follows UART TX (low)");
        uart_tx_in = 1'b1;
        repeat(4) @(posedge clk);
        check(pins[1], 1'b1, "pin1 follows UART TX (high)");

        // ===== TEST 4: open-drain (only pulls low) =====
        $display("\n=== TEST 4: open-drain ===");
        wr(2, 32'd0);            // pin2 raw GPIO
        wr(PIN_OE, 32'h04);      // pin2 enabled
        wr(PIN_OD, 32'h04);      // pin2 OPEN-DRAIN
        wr(GPIO_OUT, 32'h00);    // value 0 -> OD pulls low
        repeat(4) @(posedge clk);
        check(pins[2], 1'b0, "OD value0 -> pulled low");
        wr(GPIO_OUT, 32'h04);    // value 1 -> OD releases -> pulled up
        repeat(4) @(posedge clk);
        check(pins[2], 1'b1, "OD value1 -> released -> high");

        // ===== TEST 5: input readback (external driver sets a pin) =====
        $display("\n=== TEST 5: input readback ===");
        wr(PIN_OE, 32'h00);           // all outputs off
        ext_drive_pin = 3; ext_drive_val = 0; ext_drive_en = 1;  // drive pin3 low
        repeat(5) @(posedge clk);     // let synchronizers settle
        rd(GPIO_IN);
        $display("  GPIO_IN = %h (pin3 should read 0)", rdv);
        check(rdv[3], 1'b0, "pin3 input reads low");
        ext_drive_en = 0;             // release -> pulled up
        repeat(5) @(posedge clk);
        rd(GPIO_IN);
        check(rdv[3], 1'b1, "pin3 input reads high (pulled up)");

        // ===== TEST 6: UART RX sourced from a selected pin =====
        $display("\n=== TEST 6: UART RX from selected pin ===");
        wr(UART_RX_PIN, 32'd4);       // UART RX listens on pin4
        ext_drive_pin = 4; ext_drive_val = 0; ext_drive_en = 1;  // drive pin4 low
        repeat(5) @(posedge clk);
        check(uart_rx_out, 1'b0, "uart_rx_out follows pin4 (low)");
        ext_drive_en = 0;
        repeat(5) @(posedge clk);
        check(uart_rx_out, 1'b1, "uart_rx_out follows pin4 (high)");

        $display("\n========================================");
        if (errors==0) $display("ALL CHECKS PASSED");
        else $display("%0d CHECK(S) FAILED", errors);
        $display("========================================");
        $finish;
    end

    initial begin #200000; $display("TIMEOUT"); $finish; end

endmodule