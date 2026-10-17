// ============================================================================
// uart_tx_streamer.v -- buffered UART transmitter (Avalon-MM slave)
//
// Solves the "slow one-byte-at-a-time over JTAG" problem: the host writes a
// whole message into the buffer in ONE block-write, sets length + go, and the
// hardware streams it out the UART TX pin at baud rate -- no per-byte host
// involvement. ~2 JTAG transactions per message instead of ~2*N.
//
// Includes its own UART transmitter (8N1), so it drives the TX pin directly
// and does not depend on any other UART core for transmit.
//
// Address map (word-indexed avs_address; byte = index*4):
//   0x00 CONTROL (W): bit0 GO (write 1 to start streaming `length` bytes)
//   0x04 STATUS  (R): bit0 busy, [23:8] bytes_remaining
//   0x08 LENGTH  (W): number of bytes to send (1..BUF_BYTES)
//   0x0C BAUDDIV (RW): baud divisor = clk_freq/baud - 1 (default from params)
//   0x1000.. BUFFER (W): message bytes. Each 32-bit word holds ONE byte in
//            [7:0] (simple: host write_block of byte values). Word i -> the
//            i-th transmitted byte.
// ============================================================================

module uart_tx_streamer #(
    parameter CLK_FREQ  = 50_000_000,
    parameter BAUD      = 115_200,
    parameter BUF_BYTES = 1024,        // max message length
    parameter ABITS     = 13           // avs word-address width (buffer at 0x400 words)
)(
    input                   clk,
    input                   reset,

    // UART TX pin
    output                  uart_txd,

    // Avalon-MM slave (word-addressed)
    input      [ABITS-1:0]  avs_address,
    input                   avs_read,
    input                   avs_write,
    input      [31:0]       avs_writedata,
    output reg [31:0]       avs_readdata,
    output reg              avs_readdatavalid,
    output                  avs_waitrequest
);
    assign avs_waitrequest = 1'b0;

    localparam BUF_BASE = 13'd1024;    // word index where the byte buffer starts (0x1000 byte)

    // ---- byte buffer (one byte per word slot, low 8 bits used) ----
    reg [7:0] buffer [0:BUF_BYTES-1];

    // ---- config / control registers ----
    reg [31:0] length;
    reg [31:0] baud_div;
    reg        go_pulse;

    // ---- UART transmitter (8N1) ----
    reg        tx_start;
    reg  [7:0] tx_data;
    wire       tx_busy;

    uart_tx_var u_tx (
        .clk(clk), .reset(reset),
        .baud_div(baud_div),
        .start(tx_start), .data(tx_data),
        .txd(uart_txd), .busy(tx_busy)
    );

    // ---- streaming FSM ----
    localparam S_IDLE = 2'd0, S_LOAD = 2'd1, S_SEND = 2'd2, S_WAIT = 2'd3;
    reg [1:0]       state;
    reg [31:0]      idx;          // next byte index to send
    reg             busy;

    always @(posedge clk) begin
        if (reset) begin
            state <= S_IDLE; idx <= 0; busy <= 0; tx_start <= 0;
        end else begin
            tx_start <= 1'b0;   // default: one-cycle pulse

            case (state)
            S_IDLE: begin
                busy <= 1'b0;
                if (go_pulse && length != 0) begin
                    idx   <= 0;
                    busy  <= 1'b1;
                    state <= S_LOAD;
                end
            end

            S_LOAD: begin
                // fetch byte idx from buffer, present to UART
                tx_data <= buffer[idx[ABITS-1:0]];
                state   <= S_SEND;
            end

            S_SEND: begin
                if (!tx_busy) begin         // UART ready for this byte
                    tx_start <= 1'b1;        // launch it
                    state    <= S_WAIT;
                end
            end

            S_WAIT: begin
                // wait for this byte to finish transmitting
                if (tx_busy == 1'b0 && tx_start == 1'b0) begin
                    // byte done (tx_busy went low again after the start pulse)
                    if (idx + 1 >= length) begin
                        state <= S_IDLE;     // all bytes sent
                        busy  <= 1'b0;
                    end else begin
                        idx   <= idx + 1'b1;
                        state <= S_LOAD;
                    end
                end
            end
            endcase
        end
    end

    // ---- Avalon writes ----
    always @(posedge clk) begin
        go_pulse <= 1'b0;   // one-cycle pulse
        if (reset) begin
            length   <= 0;
            baud_div <= (CLK_FREQ / BAUD) - 1;
        end else if (avs_write) begin
            if (avs_address >= BUF_BASE) begin
                buffer[avs_address - BUF_BASE] <= avs_writedata[7:0];
            end else begin
                case (avs_address)
                    13'd0: go_pulse <= avs_writedata[0];     // CONTROL.GO
                    13'd2: length   <= avs_writedata;        // 0x08 LENGTH
                    13'd3: baud_div <= avs_writedata;        // 0x0C BAUDDIV
                    default: ;
                endcase
            end
        end
    end

    // ---- Avalon reads (registers only; buffer is write-only here) ----
    always @(posedge clk) begin
        avs_readdatavalid <= 1'b0;
        if (avs_read) begin
            avs_readdatavalid <= 1'b1;
            case (avs_address)
                13'd1: avs_readdata <= {8'd0, (length - idx), busy}; // STATUS
                13'd2: avs_readdata <= length;
                13'd3: avs_readdata <= baud_div;
                default: avs_readdata <= 32'd0;
            endcase
        end
    end

endmodule


// ============================================================================
// uart_tx_var -- 8N1 UART transmitter with runtime baud divisor
// ============================================================================
module uart_tx_var (
    input         clk,
    input         reset,
    input  [31:0] baud_div,     // clocks per bit minus 1
    input         start,
    input  [7:0]  data,
    output reg    txd,
    output reg    busy
);
    localparam IDLE=0, START=1, DATA=2, STOP=3;
    reg [1:0]  st;
    reg [31:0] cnt;
    reg [2:0]  bit_idx;
    reg [7:0]  sh;

    always @(posedge clk) begin
        if (reset) begin
            st <= IDLE; txd <= 1'b1; busy <= 1'b0; cnt <= 0; bit_idx <= 0;
        end else begin
            case (st)
            IDLE: begin
                txd <= 1'b1; busy <= 1'b0;
                if (start) begin sh <= data; busy <= 1'b1; cnt <= 0; st <= START; end
            end
            START: begin
                txd <= 1'b0;
                if (cnt >= baud_div) begin cnt <= 0; bit_idx <= 0; st <= DATA; end
                else cnt <= cnt + 1'b1;
            end
            DATA: begin
                txd <= sh[0];
                if (cnt >= baud_div) begin
                    cnt <= 0; sh <= {1'b0, sh[7:1]};
                    if (bit_idx == 3'd7) st <= STOP;
                    else bit_idx <= bit_idx + 1'b1;
                end else cnt <= cnt + 1'b1;
            end
            STOP: begin
                txd <= 1'b1;
                if (cnt >= baud_div) begin st <= IDLE; busy <= 1'b0; end
                else cnt <= cnt + 1'b1;
            end
            endcase
        end
    end
endmodule