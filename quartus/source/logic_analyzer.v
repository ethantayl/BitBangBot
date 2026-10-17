// ============================================================================
// logic_analyzer.v -- register-controlled logic analyzer core (Avalon-MM slave)
//
// The AI (via FpgaBench over JTAG) sets sample rate, trigger, and depth through
// registers, arms the core, then reads the captured samples back out of the
// built-in buffer. Every sample stores ALL channels (not just triggered ones),
// so the AI always sees the full picture.
//
// Address map (byte addresses, word-aligned). Avalon address is WORD-indexed;
// this core uses avs_address[12:0] = word index.
//   Registers occupy word indices 0..15 (byte 0x00..0x3C).
//   Sample buffer occupies word indices 2048..(2048+DEPTH-1)  (byte 0x2000+).
//
//   0x00 CONTROL    (W): bit0 ARM (write 1 to start), bit1 CLEAR (abort/reset)
//   0x04 STATUS     (R): bit0 armed, bit1 triggered, bit2 done,
//                        bits[27:16] samples_captured
//   0x08 SAMPLE_DIV (RW): sample every (SAMPLE_DIV+1) clocks. 0 => full rate.
//   0x0C TRIG_MASK  (RW): channels that participate in the trigger (1=watch)
//   0x10 TRIG_VALUE (RW): level/target value on masked channels
//   0x14 TRIG_EDGE  (RW): per-channel 1 => edge-triggered, 0 => level.
//                         Edge direction taken from TRIG_VALUE bit
//                         (1=rising, 0=falling) for channels marked edge.
//   0x18 DEPTH      (RW): total samples to capture (1..BUF_WORDS)
//   0x1C PRE_TRIG   (RW): samples to keep from BEFORE the trigger (0..DEPTH-1)
//   0x20 CHANNELS   (R): number of channels this build captured (= WIDTH)
//   0x24 BUF_WORDS  (R): buffer depth in words (compile-time)
//
//   0x2000.. SAMPLE BUFFER (R): captured words, oldest first (chronological).
//
// NOTE: reads are registered (1-cycle latency). This core asserts
// avs_readdatavalid one cycle after a read with waitrequest held low.
// ============================================================================

module logic_analyzer #(
    parameter WIDTH     = 32,     // number of input channels captured per sample
    parameter BUF_WORDS = 2048,   // sample buffer depth (block-RAM words)
    parameter ABITS     = 13      // avs word-address bits (buffer base at 2048)
)(
    input                   clk,
    input                   reset,

    // ---- probe inputs (route your GPIO etc. here; async, synchronized below) ----
    input      [WIDTH-1:0]  probe,

    // ---- Avalon-MM slave (word-addressed) ----
    input      [ABITS-1:0]  avs_address,
    input                   avs_read,
    input                   avs_write,
    input      [31:0]       avs_writedata,
    output reg [31:0]       avs_readdata,       // driven combinationally below
    output reg              avs_readdatavalid,
    output                  avs_waitrequest
);
    assign avs_waitrequest = 1'b0;   // always ready; reads complete next cycle

    localparam BUF_BASE = 2048;      // word index where the sample buffer starts

    // ------------------------------------------------------------------
    // Input synchronizers (two flops per channel) -- mandatory for async pins
    // ------------------------------------------------------------------
    reg [WIDTH-1:0] sync0, sync1, prev;
    always @(posedge clk) begin
        sync0 <= probe;
        sync1 <= sync0;
        // prev updated at sample time (below) for edge detection
    end
    wire [WIDTH-1:0] sampled_now = sync1;

    // ------------------------------------------------------------------
    // Configuration registers
    // ------------------------------------------------------------------
    reg [31:0] sample_div;   // 0 => sample every clock
    reg [31:0] trig_mask;
    reg [31:0] trig_value;
    reg [31:0] trig_edge;
    reg [31:0] depth;
    reg [31:0] pre_trig;

    reg ctrl_arm, ctrl_clear;   // one-cycle control pulses (driven in write block below)

    // ------------------------------------------------------------------
    // Sample-rate divider -> sample_tick
    // ------------------------------------------------------------------
    reg [31:0] div_cnt;
    wire       sample_tick = (div_cnt == 32'd0);
    always @(posedge clk) begin
        if (reset) div_cnt <= 32'd0;
        else if (sample_tick) div_cnt <= sample_div;
        else div_cnt <= div_cnt - 32'd1;
    end

    // ------------------------------------------------------------------
    // Trigger detector (evaluated at each sample_tick)
    //   level channels: masked bits must equal trig_value
    //   edge  channels: selected transition must occur this tick
    // ------------------------------------------------------------------
    wire [WIDTH-1:0] mask  = trig_mask[WIDTH-1:0];
    wire [WIDTH-1:0] tval  = trig_value[WIDTH-1:0];
    wire [WIDTH-1:0] tedge = trig_edge[WIDTH-1:0];

    wire [WIDTH-1:0] level_ch = mask & ~tedge;          // level-triggered channels
    wire [WIDTH-1:0] edge_ch  = mask &  tedge;          // edge-triggered channels

    wire level_ok = ((sampled_now & level_ch) == (tval & level_ch));

    wire [WIDTH-1:0] rose = sampled_now & ~prev;        // 0->1 this tick
    wire [WIDTH-1:0] fell = ~sampled_now & prev;        // 1->0 this tick
    // for each edge channel: want rising if tval bit=1, falling if 0
    wire [WIDTH-1:0] edge_want = (edge_ch & tval & rose) | (edge_ch & ~tval & fell);
    wire edge_ok = (edge_ch == 0) ? 1'b0 : (edge_want != 0);

    // trigger fires if level condition holds AND (no edge channels, or an edge hit)
    wire trig_now = (mask != 0) &&
                    level_ok &&
                    ((edge_ch == 0) ? 1'b1 : edge_ok);

    // ------------------------------------------------------------------
    // Capture FSM with circular pre-trigger buffer
    // ------------------------------------------------------------------
    localparam S_IDLE = 2'd0, S_PRE = 2'd1, S_POST = 2'd2, S_DONE = 2'd3;
    reg [1:0]       state;
    reg             armed, triggered, done;
    reg [ABITS-1:0] wr_ptr;        // where the next sample is written (0..depth-1 region)
    reg [31:0]      pre_count;     // pre-trigger samples collected
    reg [31:0]      post_count;    // post-trigger samples collected
    reg [31:0]      captured;      // total written (for STATUS)
    reg [ABITS-1:0] trig_ptr;      // buffer index where trigger occurred

    // sample buffer (dual-port: port A write from FSM, port B read via Avalon)
    reg [31:0] buffer [0:BUF_WORDS-1];
    reg [31:0] padded_sample;
    always @(*) begin
        padded_sample = 32'd0;
        padded_sample[WIDTH-1:0] = sampled_now;
    end

    reg        buf_we;
    reg [ABITS-1:0] buf_waddr;
    always @(posedge clk) begin
        if (buf_we) buffer[buf_waddr] <= padded_sample;
    end

    always @(posedge clk) begin
        if (reset) begin
            state <= S_IDLE; armed <= 0; triggered <= 0; done <= 0;
            wr_ptr <= 0; pre_count <= 0; post_count <= 0; captured <= 0;
            buf_we <= 0;
        end else begin
            buf_we <= 0;

            // latch prev at each sample tick for edge detection
            if (sample_tick) prev <= sampled_now;

            case (state)
            S_IDLE: begin
                armed <= 0; triggered <= 0; done <= 0;
                if (ctrl_arm) begin
                    // start fresh
                    wr_ptr <= 0; pre_count <= 0; post_count <= 0;
                    captured <= 0; triggered <= 0; done <= 0;
                    armed <= 1;
                    state <= (pre_trig == 0) ? S_POST : S_PRE;
                    // in S_POST with pre_trig==0 we simply wait for trigger then fill
                end
            end

            // Fill the pre-trigger region (linear for simplicity: collect
            // pre_trig samples, then begin watching the trigger).
            S_PRE: begin
                if (sample_tick) begin
                    buf_we    <= 1;
                    buf_waddr <= wr_ptr;
                    wr_ptr    <= wr_ptr + 1'b1;
                    pre_count <= pre_count + 1'b1;
                    captured  <= captured + 1'b1;
                    if (pre_count + 1 >= pre_trig) state <= S_POST;
                end
            end

            // Watch for trigger; keep sampling. Once triggered, collect the
            // remaining (depth - pre_trig) samples then finish.
            S_POST: begin
                if (sample_tick) begin
                    buf_we    <= 1;
                    buf_waddr <= wr_ptr;
                    wr_ptr    <= wr_ptr + 1'b1;
                    captured  <= captured + 1'b1;

                    if (!triggered) begin
                        if (trig_now) begin
                            triggered <= 1;
                            trig_ptr  <= wr_ptr;
                            post_count <= 0;
                        end
                    end else begin
                        post_count <= post_count + 1'b1;
                    end

                    // stop when we've filled depth total, but only after trigger
                    if (triggered && (post_count + 1 >= (depth - pre_trig))) begin
                        state <= S_DONE;
                        done  <= 1;
                        armed <= 0;
                    end else if (wr_ptr + 1 >= depth && !triggered) begin
                        // buffer full before trigger: wrap the post region
                        // (simple version: keep overwriting from pre_trig point)
                        wr_ptr <= pre_trig[ABITS-1:0];
                    end else if (wr_ptr + 1 >= depth) begin
                        // safety: never exceed buffer
                        state <= S_DONE; done <= 1; armed <= 0;
                    end
                end
            end

            S_DONE: begin
                armed <= 0;
                if (ctrl_clear) state <= S_IDLE;
            end
            endcase

            if (ctrl_clear) begin
                state <= S_IDLE; armed <= 0; triggered <= 0; done <= 0;
                wr_ptr <= 0; captured <= 0; pre_count <= 0; post_count <= 0;
            end
        end
    end

    // ------------------------------------------------------------------
    // Avalon slave: register writes
    // ------------------------------------------------------------------
    always @(posedge clk) begin
        ctrl_arm   <= 1'b0;   // one-cycle pulses
        ctrl_clear <= 1'b0;
        if (reset) begin
            sample_div <= 0; trig_mask <= 0; trig_value <= 0;
            trig_edge  <= 0; depth <= BUF_WORDS; pre_trig <= 0;
        end else if (avs_write) begin
            case (avs_address)
                13'd0: begin
                    ctrl_arm   <= avs_writedata[0];
                    ctrl_clear <= avs_writedata[1];
                end
                13'd2:  sample_div <= avs_writedata;   // 0x08
                13'd3:  trig_mask  <= avs_writedata;   // 0x0C
                13'd4:  trig_value <= avs_writedata;   // 0x10
                13'd5:  trig_edge  <= avs_writedata;   // 0x14
                13'd6:  depth      <= avs_writedata;   // 0x18
                13'd7:  pre_trig   <= avs_writedata;   // 0x1C
                default: ;
            endcase
        end
    end

    // ------------------------------------------------------------------
    // Avalon slave: registered reads (registers + sample buffer)
    //
    // 1-cycle read latency (readdatavalid one cycle after avs_read).
    // The BRAM index is clamped so a register read never does an
    // out-of-bounds buffer access.
    // ------------------------------------------------------------------
    wire             is_buf  = (avs_address >= BUF_BASE[ABITS-1:0]);
    wire [ABITS-1:0] buf_idx = is_buf ? (avs_address - BUF_BASE[ABITS-1:0])
                                      : {ABITS{1'b0}};

    reg [31:0] buf_rdata;     // BRAM data, 1 cycle after request
    reg        rd_was_buf;    // was the in-flight read a buffer read?
    reg [31:0] reg_rdata;     // register data captured at request time

    always @(posedge clk) begin
        buf_rdata <= buffer[buf_idx];
    end

    always @(posedge clk) begin
        if (reset) begin
            avs_readdatavalid <= 1'b0;
            rd_was_buf        <= 1'b0;
        end else begin
            avs_readdatavalid <= avs_read;     // valid 1 cycle after read
            rd_was_buf        <= is_buf;
            if (avs_read) begin
                case (avs_address)
                    13'd0:  reg_rdata <= 32'd0;                         // CONTROL (wo)
                    13'd1:  reg_rdata <= {captured[11:0], 17'd0,        // STATUS
                                          done, triggered, armed};
                    13'd2:  reg_rdata <= sample_div;
                    13'd3:  reg_rdata <= trig_mask;
                    13'd4:  reg_rdata <= trig_value;
                    13'd5:  reg_rdata <= trig_edge;
                    13'd6:  reg_rdata <= depth;
                    13'd7:  reg_rdata <= pre_trig;
                    13'd8:  reg_rdata <= WIDTH;                         // CHANNELS
                    13'd9:  reg_rdata <= BUF_WORDS;                     // BUF_WORDS
                    default: reg_rdata <= 32'h0;
                endcase
            end
        end
    end

    // final mux: when the valid read was a buffer read, return BRAM data
    always @(*) begin
        avs_readdata = rd_was_buf ? buf_rdata : reg_rdata;
    end

endmodule