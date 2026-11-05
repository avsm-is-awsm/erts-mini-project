// ts_counter.v -- free-running 64-bit cycle counter with synchronous active-low reset.
// The probes use count[31:0]; latency_cycles = (t_out - t_in) mod 2^32.
`timescale 1ns / 1ps
module ts_counter #(
    parameter integer WIDTH = 64
) (
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 aclk CLK" *)
    (* X_INTERFACE_PARAMETER = "ASSOCIATED_RESET aresetn, FREQ_HZ 100000000" *)
    input  wire             aclk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 aresetn RST" *)
    (* X_INTERFACE_PARAMETER = "POLARITY ACTIVE_LOW" *)
    input  wire             aresetn,
    output reg [WIDTH-1:0]  count
);
    always @(posedge aclk) begin
        if (!aresetn) count <= {WIDTH{1'b0}};
        else          count <= count + 1'b1;
    end
endmodule
