// ============================================================================
// pin_mux.v -- programmable pin crossbar (Avalon-MM slave)
//
// The heart of BitBangBot: the AI reroutes which signal drives each physical
// pin, so it can try UART, SPI, I2C, or raw bit-banging on an unknown
// connection WITHOUT recompiling. Each of N flexible pins can be driven by:
//   - a peripheral output (UART TX, SPI SCLK/MOSI/CS, ... )
//   - a raw GPIO value the AI writes (bit-bang anything)
//   - or tri-stated (high-Z, used as an input)
// and each pin's current level is always readable (so inputs work for any mode).
//
// Peripheral INPUTS (UART RX, SPI MISO, ...) are sourced from a pin the AI
// selects, so a peripheral can listen on whatever pin the target uses.
// I2C is bidirectional on the same wire, so its inputs come from whichever
// pin(s) are routed to I2C SCL/SDA (wired-AND, like the bus itself). With no
// pin routed, the core just sees its own output.
//
// ---- signal-source encoding (what drives a pin's output) ----
//   0  = raw GPIO (the GPIO_OUT register bit for this pin)
//   1  = UART TX
//   2  = SPI SCLK
//   3  = SPI MOSI
//   4  = SPI CS
//   5  = I2C SCL       (set PIN_OD + PIN_OE for this pin: open-drain)
//   6  = I2C SDA       (same)
//   (extend as you add peripherals)
//
// ---- Address map (word-indexed avs_address) ----
//   0x00 PIN0_SRC .. per-pin: which signal drives pin i (word i, i=0..NPIN-1)
//   0x20 PIN_OE       bit i = 1 -> pin i output enabled (drive), 0 -> hi-Z (input)
//   0x24 PIN_OD       bit i = 1 -> open-drain (drive low only; for I2C), 0 -> push-pull
//   0x28 GPIO_OUT     raw output value per pin (used when SRC = 0)
//   0x2C GPIO_IN  (R) live input level of every pin (always valid)
//   0x30 UART_RX_PIN  which pin feeds the UART RX input
//   0x34 SPI_MISO_PIN which pin feeds the SPI MISO input
// ============================================================================

module pin_mux #(
    parameter NPIN  = 8,
    parameter ABITS = 8
)(
    input                   clk,
    input                   reset,

    // ---- physical pins (connect to GPIO in the top level) ----
    inout      [NPIN-1:0]   pins,

    // ---- peripheral signals coming IN from the cores (their outputs) ----
    input                   uart_tx_in,     // UART wants to drive this
    output                  uart_rx_out,    // pin level -> UART RX
    input                   spi_sclk_in,
    input                   spi_mosi_in,
    input                   spi_cs_in,
    output                  spi_miso_out,
    input                   i2c_scl_in,     // value the I2C core drives (0 = pull low)
    input                   i2c_sda_in,
    output                  i2c_scl_out,    // SCL bus level -> I2C core
    output                  i2c_sda_out,    // SDA bus level -> I2C core

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

    // ------------------------------------------------------------------
    // registers
    // ------------------------------------------------------------------
    reg [3:0]        pin_src [0:NPIN-1];   // source select per pin
    reg [NPIN-1:0]   pin_oe;               // output enable per pin
    reg [NPIN-1:0]   pin_od;               // open-drain per pin
    reg [NPIN-1:0]   gpio_out;             // raw output value per pin
    reg [31:0]       uart_rx_pin;          // which pin feeds UART RX
    reg [31:0]       spi_miso_pin;         // which pin feeds SPI MISO

    // ------------------------------------------------------------------
    // input synchronizers for every pin
    // ------------------------------------------------------------------
    wire [NPIN-1:0]  pin_in_raw;
    reg  [NPIN-1:0]  sync0, sync1;
    always @(posedge clk) begin sync0 <= pin_in_raw; sync1 <= sync0; end
    wire [NPIN-1:0]  pin_level = sync1;

    // ------------------------------------------------------------------
    // per-pin output value + output-enable (the crossbar)
    // ------------------------------------------------------------------
    genvar g;
    generate
        for (g = 0; g < NPIN; g = g + 1) begin : gpio_mux
            reg out_val;
            always @(*) begin
                case (pin_src[g])
                    4'd0: out_val = gpio_out[g];   // raw GPIO
                    4'd1: out_val = uart_tx_in;    // UART TX
                    4'd2: out_val = spi_sclk_in;   // SPI SCLK
                    4'd3: out_val = spi_mosi_in;   // SPI MOSI
                    4'd4: out_val = spi_cs_in;     // SPI CS
                    4'd5: out_val = i2c_scl_in;    // I2C SCL
                    4'd6: out_val = i2c_sda_in;    // I2C SDA
                    default: out_val = 1'b1;
                endcase
            end

            // open-drain: only ever drive LOW; release (hi-Z) for a high.
            // push-pull: drive the actual value when OE is set.
            wire drive_en = pin_od[g] ? (pin_oe[g] & ~out_val)   // OD: enable only to pull low
                                      :  pin_oe[g];              // PP: enable always when OE
            wire drive_val = pin_od[g] ? 1'b0 : out_val;

            assign pins[g]       = drive_en ? drive_val : 1'bz;
            assign pin_in_raw[g] = pins[g];
        end
    endgenerate

    // ------------------------------------------------------------------
    // peripheral INPUTS sourced from selected pins
    // ------------------------------------------------------------------
    assign uart_rx_out  = pin_level[ uart_rx_pin[$clog2(NPIN)-1:0] ];
    assign spi_miso_out = pin_level[ spi_miso_pin[$clog2(NPIN)-1:0] ];
    // I2C: bus level = AND of every pin routed to SCL/SDA (open-drain bus);
    // with none routed, loop the core's own output back.
    reg [NPIN-1:0] is_scl, is_sda;
    integer j;
    always @(*) begin
        for (j = 0; j < NPIN; j = j + 1) begin
            is_scl[j] = (pin_src[j] == 4'd5);
            is_sda[j] = (pin_src[j] == 4'd6);
        end
    end
    assign i2c_scl_out  = (|is_scl) ? &(pin_level | ~is_scl) : i2c_scl_in;
    assign i2c_sda_out  = (|is_sda) ? &(pin_level | ~is_sda) : i2c_sda_in;

    // ------------------------------------------------------------------
    // Avalon writes
    // ------------------------------------------------------------------
    integer k;
    always @(posedge clk) begin
        if (reset) begin
            for (k = 0; k < NPIN; k = k + 1) pin_src[k] <= 4'd0;
            pin_oe <= 0; pin_od <= 0; gpio_out <= 0;
            uart_rx_pin <= 0; spi_miso_pin <= 0;
        end else if (avs_write) begin
            if (avs_address < NPIN) begin
                pin_src[avs_address[$clog2(NPIN)-1:0]] <= avs_writedata[3:0];
            end else begin
                case (avs_address)
                    8'h08: pin_oe       <= avs_writedata[NPIN-1:0];   // 0x20
                    8'h09: pin_od       <= avs_writedata[NPIN-1:0];   // 0x24
                    8'h0A: gpio_out     <= avs_writedata[NPIN-1:0];   // 0x28
                    8'h0C: uart_rx_pin  <= avs_writedata;             // 0x30
                    8'h0D: spi_miso_pin <= avs_writedata;             // 0x34
                    default: ;
                endcase
            end
        end
    end

    // ------------------------------------------------------------------
    // Avalon reads
    // ------------------------------------------------------------------
    always @(posedge clk) begin
        avs_readdatavalid <= 1'b0;
        if (avs_read) begin
            avs_readdatavalid <= 1'b1;
            if (avs_address < NPIN) begin
                avs_readdata <= {28'd0, pin_src[avs_address[$clog2(NPIN)-1:0]]};
            end else begin
                case (avs_address)
                    8'h08: avs_readdata <= {{(32-NPIN){1'b0}}, pin_oe};
                    8'h09: avs_readdata <= {{(32-NPIN){1'b0}}, pin_od};
                    8'h0A: avs_readdata <= {{(32-NPIN){1'b0}}, gpio_out};
                    8'h0B: avs_readdata <= {{(32-NPIN){1'b0}}, pin_level}; // 0x2C GPIO_IN
                    8'h0C: avs_readdata <= uart_rx_pin;
                    8'h0D: avs_readdata <= spi_miso_pin;
                    default: avs_readdata <= 32'd0;
                endcase
            end
        end
    end

endmodule