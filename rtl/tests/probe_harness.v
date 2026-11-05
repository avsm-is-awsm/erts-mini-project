// probe_harness.v -- cocotb top level: ts_counter driving both AXIS timestamp probes.
`timescale 1ns / 1ps
module probe_harness (
    input  wire        aclk,
    input  wire        aresetn,
    output wire [63:0] count,
    // axis_ts_in
    input  wire [63:0] in_s_tdata,  input  wire [7:0] in_s_tkeep, input wire in_s_tlast,
    input  wire        in_s_tvalid, output wire in_s_tready,
    output wire [63:0] in_m_tdata,  output wire [7:0] in_m_tkeep, output wire in_m_tlast,
    output wire [31:0] in_m_tuser,  output wire in_m_tvalid, input wire in_m_tready,
    // axis_ts_out
    input  wire [63:0] out_s_tdata,  input  wire [7:0] out_s_tkeep, input wire out_s_tlast,
    input  wire        out_s_tvalid, output wire out_s_tready,
    output wire [63:0] out_m_tdata,  output wire [7:0] out_m_tkeep, output wire out_m_tlast,
    output wire        out_m_tvalid, input wire out_m_tready
);
    ts_counter u_cnt (.aclk(aclk), .aresetn(aresetn), .count(count));
    axis_ts_in u_in (
        .aclk(aclk), .aresetn(aresetn), .ts(count[31:0]),
        .s_axis_tdata(in_s_tdata), .s_axis_tkeep(in_s_tkeep), .s_axis_tlast(in_s_tlast),
        .s_axis_tvalid(in_s_tvalid), .s_axis_tready(in_s_tready),
        .m_axis_tdata(in_m_tdata), .m_axis_tkeep(in_m_tkeep), .m_axis_tlast(in_m_tlast),
        .m_axis_tuser(in_m_tuser), .m_axis_tvalid(in_m_tvalid), .m_axis_tready(in_m_tready));
    axis_ts_out u_out (
        .aclk(aclk), .aresetn(aresetn), .ts(count[31:0]),
        .s_axis_tdata(out_s_tdata), .s_axis_tkeep(out_s_tkeep), .s_axis_tlast(out_s_tlast),
        .s_axis_tvalid(out_s_tvalid), .s_axis_tready(out_s_tready),
        .m_axis_tdata(out_m_tdata), .m_axis_tkeep(out_m_tkeep), .m_axis_tlast(out_m_tlast),
        .m_axis_tvalid(out_m_tvalid), .m_axis_tready(out_m_tready));
endmodule
