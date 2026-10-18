// ============================================================================
// board_io.v -- DE10-Lite seven-segment displays + red LEDs (Avalon-MM slave)
//
// Raw, readable control of every segment and LED so the AI can show status on
// the board. Character decoding (hex digits, letters) is done in software --
// the hardware just holds segment bits.
//
// Segment byte layout (per digit): bit0..6 = segments a..g, bit7 = decimal
// point. 1 = LIT. The board's displays are active-low; the inversion happens
// here so software never has to think about it.
//
//        a
//      f   b
//        g
//      e   c
//        d   dp
//
// ---- Address map (word-indexed avs_address) ----
//   0x00 HEX_LO (RW)  [7:0] HEX0  [15:8] HEX1  [23:16] HEX2  [31:24] HEX3
//   0x04 HEX_HI (RW)  [7:0] HEX4  [15:8] HEX5
//   0x08 LEDR   (RW)  [9:0] LEDR[9:0] (1 = on)
// Reset: everything blank / off.
// ============================================================================

module board_io #(
    parameter ABITS = 2
)(
    input                   clk,
    input                   reset,

    // ---- board outputs (connect straight to the DE10-Lite pins) ----
    output     [7:0]        hex0,
    output     [7:0]        hex1,
    output     [7:0]        hex2,
    output     [7:0]        hex3,
    output     [7:0]        hex4,
    output     [7:0]        hex5,
    output     [9:0]        ledr,

    // ---- Avalon-MM slave ----
    input      [ABITS-1:0]  avs_address,
    input                   avs_read,
    input                   avs_write,
    input      [31:0]       avs_writedata,
    output reg [31:0]       avs_readdata,
    output reg              avs_readdatavalid,
    output                  avs_waitrequest
);
    assign avs_waitrequest = 1'b0;

    reg [31:0] hex_lo;
    reg [15:0] hex_hi;
    reg [9:0]  led;

    // active-low segments on the board
    assign hex0 = ~hex_lo[7:0];
    assign hex1 = ~hex_lo[15:8];
    assign hex2 = ~hex_lo[23:16];
    assign hex3 = ~hex_lo[31:24];
    assign hex4 = ~hex_hi[7:0];
    assign hex5 = ~hex_hi[15:8];
    assign ledr = led;

    // ------------------------------------------------------------------
    // Avalon writes
    // ------------------------------------------------------------------
    always @(posedge clk) begin
        if (reset) begin
            hex_lo <= 0; hex_hi <= 0; led <= 0;
        end else if (avs_write) begin
            case (avs_address)
                2'd0: hex_lo <= avs_writedata;          // 0x00
                2'd1: hex_hi <= avs_writedata[15:0];    // 0x04
                2'd2: led    <= avs_writedata[9:0];     // 0x08
                default: ;
            endcase
        end
    end

    // ------------------------------------------------------------------
    // Avalon reads
    // ------------------------------------------------------------------
    always @(posedge clk) begin
        avs_readdatavalid <= 1'b0;
        if (avs_read) begin
            avs_readdatavalid <= 1'b1;
            case (avs_address)
                2'd0: avs_readdata <= hex_lo;
                2'd1: avs_readdata <= {16'd0, hex_hi};
                2'd2: avs_readdata <= {22'd0, led};
                default: avs_readdata <= 32'd0;
            endcase
        end
    end

endmodule
