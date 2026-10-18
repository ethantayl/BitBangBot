// ============================================================================
// uart_full.v -- full-featured buffered UART peripheral (Avalon-MM slave)
//
// One core that does everything:
//   - Buffered TX: host loads a message in one block-write, hardware streams it
//   - Buffered RX: incoming bytes accumulate in a buffer, host reads in bulk
//   - Runtime baud: change baud rate via a register (for UART discovery)
//   - Framing-error count: detect wrong-baud / bad framing (discovery feedback)
//   - 8N1 (fixed framing for now; data/parity could be added later)
//
// Address map (word-indexed avs_address; byte = index*4):
//   0x00 CONTROL  (W): bit0 TX_GO, bit1 RX_CLEAR, bit2 TX_CLEAR
//   0x04 STATUS   (R): bit0 tx_busy, [15:8] rx_count, [27:16] rx_framing_errors
//   0x08 BAUDDIV  (RW): baud divisor = clk_freq/baud - 1 (default from params)
//   0x0C TX_LEN   (W): number of bytes to transmit
//   0x10 RX_COUNT (R): bytes currently in the RX buffer
//   0x800.. TX_BUFFER (W): bytes to send, one byte per 32-bit word  (word 512+)
//   0xC00.. RX_BUFFER (R): received bytes, one byte per 32-bit word (word 768+)
// ============================================================================

module uart_full #(
    parameter CLK_FREQ = 50_000_000,
    parameter BAUD     = 115_200,
    parameter BUF_LEN  = 256,         // TX and RX buffer depth (bytes each)
    parameter ABITS    = 11           // avs word-address bits
)(
    input                   clk,
    input                   reset,

    // UART pins
    output                  uart_txd,
    input                   uart_rxd,

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

    localparam TX_BASE = 11'd512;   // word index of TX buffer (byte 0x800)
    localparam RX_BASE = 11'd768;   // word index of RX buffer (byte 0xC00)

    // ------------------------------------------------------------------
    // config / control
    // ------------------------------------------------------------------
    reg [31:0] baud_div;
    reg [31:0] tx_len;
    reg        tx_go, rx_clear, tx_clear;

    // ==================================================================
    // TX path: buffer + transmitter + streaming FSM
    // ==================================================================
    reg  [7:0] tx_buffer [0:BUF_LEN-1];
    reg        tx_start;
    reg  [7:0] tx_data;
    wire       tx_busy;

    uart_tx_8n1 #(.DUMMY(0)) u_tx (
        .clk(clk), .reset(reset), .baud_div(baud_div),
        .start(tx_start), .data(tx_data), .txd(uart_txd), .busy(tx_busy)
    );

    localparam T_IDLE=2'd0, T_LOAD=2'd1, T_SEND=2'd2, T_WAIT=2'd3;
    reg [1:0]  tstate;
    reg [31:0] tx_idx;
    reg        tx_active;

    always @(posedge clk) begin
        if (reset) begin
            tstate <= T_IDLE; tx_idx <= 0; tx_active <= 0; tx_start <= 0;
        end else begin
            tx_start <= 1'b0;
            case (tstate)
            T_IDLE: begin
                tx_active <= 1'b0;
                if (tx_go && tx_len != 0) begin
                    tx_idx <= 0; tx_active <= 1'b1; tstate <= T_LOAD;
                end
            end
            T_LOAD: begin
                tx_data <= tx_buffer[tx_idx[7:0]];
                tstate  <= T_SEND;
            end
            T_SEND: begin
                if (!tx_busy) begin
                    tx_start <= 1'b1;
                    tstate   <= T_WAIT;
                end
            end
            T_WAIT: begin
                if (!tx_busy && !tx_start) begin
                    if (tx_idx + 1 >= tx_len) begin
                        tstate <= T_IDLE; tx_active <= 1'b0;
                    end else begin
                        tx_idx <= tx_idx + 1'b1;
                        tstate <= T_LOAD;
                    end
                end
            end
            endcase
        end
    end

    // ==================================================================
    // RX path: receiver + buffer + framing-error counter
    // ==================================================================
    wire       rx_valid;
    wire [7:0] rx_byte;
    wire       rx_frame_err;

    uart_rx_8n1 u_rx (
        .clk(clk), .reset(reset), .baud_div(baud_div),
        .rxd(uart_rxd),
        .data(rx_byte), .valid(rx_valid), .frame_err(rx_frame_err)
    );

    reg  [7:0] rx_buffer [0:BUF_LEN-1];
    reg [31:0] rx_wr;          // write pointer / count
    reg [31:0] rx_err_count;

    always @(posedge clk) begin
        if (reset || rx_clear) begin
            rx_wr <= 0; rx_err_count <= 0;
        end else begin
            if (rx_valid && rx_wr < BUF_LEN) begin
                rx_buffer[rx_wr[7:0]] <= rx_byte;
                rx_wr <= rx_wr + 1'b1;
            end
            if (rx_frame_err) rx_err_count <= rx_err_count + 1'b1;
        end
    end

    // ==================================================================
    // Avalon writes
    // ==================================================================
    always @(posedge clk) begin
        tx_go <= 1'b0; rx_clear <= 1'b0; tx_clear <= 1'b0;
        if (reset) begin
            baud_div <= (CLK_FREQ / BAUD) - 1;
            tx_len   <= 0;
        end else if (avs_write) begin
            if (avs_address >= TX_BASE && avs_address < TX_BASE + BUF_LEN) begin
                tx_buffer[avs_address - TX_BASE] <= avs_writedata[7:0];
            end else begin
                case (avs_address)
                    11'd0: begin
                        tx_go    <= avs_writedata[0];
                        rx_clear <= avs_writedata[1];
                        tx_clear <= avs_writedata[2];
                    end
                    11'd2: baud_div <= avs_writedata;   // 0x08
                    11'd3: tx_len   <= avs_writedata;   // 0x0C
                    default: ;
                endcase
            end
        end
    end

    // ==================================================================
    // Avalon reads (registers + RX buffer)
    //
    // Single cycle of latency: on avs_read, latch either the selected register
    // value or the RX-buffer byte into avs_readdata, and assert readdatavalid
    // the next cycle. The RX buffer is read combinationally (indexed array) and
    // registered in the SAME step as the register case -- no extra pipeline
    // stage, so byte N reads back as byte N.
    // ==================================================================
    wire is_rxbuf = (avs_address >= RX_BASE && avs_address < RX_BASE + BUF_LEN);
    wire [7:0] rx_byte_at = rx_buffer[ is_rxbuf ? (avs_address - RX_BASE)
                                                : {ABITS{1'b0}} ];

    always @(posedge clk) begin
        if (reset) begin
            avs_readdatavalid <= 1'b0;
        end else begin
            avs_readdatavalid <= avs_read;
            if (avs_read) begin
                if (is_rxbuf) begin
                    avs_readdata <= {24'd0, rx_byte_at};
                end else begin
                    case (avs_address)
                        11'd0: avs_readdata <= 32'd0;                      // CONTROL
                        11'd1: avs_readdata <= {rx_err_count[11:0],        // STATUS
                                                rx_wr[7:0], 7'd0, tx_active};
                        11'd2: avs_readdata <= baud_div;
                        11'd3: avs_readdata <= tx_len;
                        11'd4: avs_readdata <= rx_wr;                      // RX_COUNT
                        default: avs_readdata <= 32'd0;
                    endcase
                end
            end
        end
    end

endmodule


// ============================================================================
// uart_tx_8n1 -- 8N1 transmitter, runtime baud
// ============================================================================
module uart_tx_8n1 #(parameter DUMMY=0) (
    input         clk, reset,
    input  [31:0] baud_div,
    input         start,
    input  [7:0]  data,
    output reg    txd,
    output reg    busy
);
    localparam IDLE=0, START=1, DATA=2, STOP=3;
    reg [1:0]  st; reg [31:0] cnt; reg [2:0] bidx; reg [7:0] sh;
    always @(posedge clk) begin
        if (reset) begin st<=IDLE; txd<=1'b1; busy<=0; cnt<=0; bidx<=0; end
        else case (st)
        IDLE:  begin txd<=1'b1; busy<=0;
                     if (start) begin sh<=data; busy<=1; cnt<=0; st<=START; end end
        START: begin txd<=1'b0;
                     if (cnt>=baud_div) begin cnt<=0; bidx<=0; st<=DATA; end
                     else cnt<=cnt+1; end
        DATA:  begin txd<=sh[0];
                     if (cnt>=baud_div) begin cnt<=0; sh<={1'b0,sh[7:1]};
                        if (bidx==3'd7) st<=STOP; else bidx<=bidx+1; end
                     else cnt<=cnt+1; end
        STOP:  begin txd<=1'b1;
                     if (cnt>=baud_div) begin st<=IDLE; busy<=0; end
                     else cnt<=cnt+1; end
        endcase
    end
endmodule


// ============================================================================
// uart_rx_8n1 -- 8N1 receiver, runtime baud, with framing-error flag
// ============================================================================
module uart_rx_8n1 (
    input         clk, reset,
    input  [31:0] baud_div,
    input         rxd,
    output reg [7:0] data,
    output reg       valid,
    output reg       frame_err
);
    reg rxd_m, rxd_s;
    always @(posedge clk) begin rxd_m<=rxd; rxd_s<=rxd_m; end

    localparam IDLE=0, START=1, DATA=2, STOP=3;
    reg [1:0]  st; reg [31:0] cnt; reg [2:0] bidx; reg [7:0] sh;
    wire [31:0] half = baud_div >> 1;

    always @(posedge clk) begin
        if (reset) begin st<=IDLE; valid<=0; frame_err<=0; cnt<=0; bidx<=0; end
        else begin
            valid<=0; frame_err<=0;
            case (st)
            IDLE:  if (!rxd_s) begin cnt<=0; st<=START; end
            START: if (cnt>=half) begin
                       if (!rxd_s) begin cnt<=0; bidx<=0; st<=DATA; end
                       else st<=IDLE;   // false start
                   end else cnt<=cnt+1;
            DATA:  if (cnt>=baud_div) begin
                       cnt<=0; sh<={rxd_s, sh[7:1]};
                       if (bidx==3'd7) st<=STOP; else bidx<=bidx+1;
                   end else cnt<=cnt+1;
            STOP:  if (cnt>=baud_div) begin
                       data<=sh; st<=IDLE;
                       if (rxd_s) valid<=1;        // good stop bit -> valid byte
                       else frame_err<=1;          // bad stop bit -> framing error
                   end else cnt<=cnt+1;
            endcase
        end
    end
endmodule