// system_harness.v -- RTL system simulation top: the exported HLS IP (hls/ip/hdl/verilog)
// between the two timestamp probes, exactly as in the Vivado block design:
//   feed -> axis_ts_in -> t2t_core.md_in ; t2t_core.ord_out -> axis_ts_out -> orders
//   t2t_core.dbg_out -> dbg ; ts_counter[31:0] -> both probes
`timescale 1ns / 1ps
module system_harness (
    input  wire        aclk,
    input  wire        aresetn,
    output wire [63:0] count,
    // feed (stands in for DMA MM2S)
    input  wire [63:0] s_tdata,
    input  wire        s_tlast,
    input  wire        s_tvalid,
    output wire        s_tready,
    // orders after axis_ts_out (stands in for DMA S2MM)
    output wire [63:0] o_tdata,
    output wire        o_tlast,
    output wire        o_tvalid,
    input  wire        o_tready,
    // debug BBO stream
    output wire [63:0] d_tdata,
    output wire        d_tlast,
    output wire        d_tvalid,
    input  wire        d_tready,
    // AXI-Lite (CPU)
    input  wire [8:0]  awaddr,  input wire awvalid, output wire awready,
    input  wire [31:0] wdata,   input wire [3:0] wstrb, input wire wvalid, output wire wready,
    output wire [1:0]  bresp,   output wire bvalid, input wire bready,
    input  wire [8:0]  araddr,  input wire arvalid, output wire arready,
    output wire [31:0] rdata,   output wire [1:0] rresp, output wire rvalid, input wire rready
);
    wire [31:0] ts = count[31:0];
    ts_counter u_cnt (.aclk(aclk), .aresetn(aresetn), .count(count));

    wire [63:0] md_tdata; wire [7:0] md_tkeep; wire md_tlast, md_tvalid, md_tready;
    wire [31:0] md_tuser;
    axis_ts_in u_tin (
        .aclk(aclk), .aresetn(aresetn), .ts(ts),
        .s_axis_tdata(s_tdata), .s_axis_tkeep(8'hFF), .s_axis_tlast(s_tlast),
        .s_axis_tvalid(s_tvalid), .s_axis_tready(s_tready),
        .m_axis_tdata(md_tdata), .m_axis_tkeep(md_tkeep), .m_axis_tlast(md_tlast),
        .m_axis_tuser(md_tuser), .m_axis_tvalid(md_tvalid), .m_axis_tready(md_tready));

    wire [63:0] or_tdata; wire [7:0] or_tkeep; wire or_tlast, or_tvalid, or_tready;
    wire [7:0] dbg_tkeep;
    t2t_core u_core (
        .ap_clk(aclk), .ap_rst_n(aresetn),
        .md_in_TDATA(md_tdata), .md_in_TVALID(md_tvalid), .md_in_TREADY(md_tready),
        .md_in_TKEEP(md_tkeep), .md_in_TSTRB(8'hFF), .md_in_TUSER(md_tuser), .md_in_TLAST(md_tlast),
        .md_in_TID(1'b0), .md_in_TDEST(1'b0),
        .ord_out_TDATA(or_tdata), .ord_out_TVALID(or_tvalid), .ord_out_TREADY(or_tready),
        .ord_out_TKEEP(or_tkeep), .ord_out_TSTRB(), .ord_out_TUSER(), .ord_out_TLAST(or_tlast),
        .ord_out_TID(), .ord_out_TDEST(),
        .dbg_out_TDATA(d_tdata), .dbg_out_TVALID(d_tvalid), .dbg_out_TREADY(d_tready),
        .dbg_out_TKEEP(dbg_tkeep), .dbg_out_TSTRB(), .dbg_out_TUSER(), .dbg_out_TLAST(d_tlast),
        .dbg_out_TID(), .dbg_out_TDEST(),
        .s_axi_ctrl_AWVALID(awvalid), .s_axi_ctrl_AWREADY(awready), .s_axi_ctrl_AWADDR(awaddr),
        .s_axi_ctrl_WVALID(wvalid), .s_axi_ctrl_WREADY(wready), .s_axi_ctrl_WDATA(wdata),
        .s_axi_ctrl_WSTRB(wstrb), .s_axi_ctrl_ARVALID(arvalid), .s_axi_ctrl_ARREADY(arready),
        .s_axi_ctrl_ARADDR(araddr), .s_axi_ctrl_RVALID(rvalid), .s_axi_ctrl_RREADY(rready),
        .s_axi_ctrl_RDATA(rdata), .s_axi_ctrl_RRESP(rresp), .s_axi_ctrl_BVALID(bvalid),
        .s_axi_ctrl_BREADY(bready), .s_axi_ctrl_BRESP(bresp));

    wire [7:0] o_tkeep;
    axis_ts_out u_tout (
        .aclk(aclk), .aresetn(aresetn), .ts(ts),
        .s_axis_tdata(or_tdata), .s_axis_tkeep(or_tkeep), .s_axis_tlast(or_tlast),
        .s_axis_tvalid(or_tvalid), .s_axis_tready(or_tready),
        .m_axis_tdata(o_tdata), .m_axis_tkeep(o_tkeep), .m_axis_tlast(o_tlast),
        .m_axis_tvalid(o_tvalid), .m_axis_tready(o_tready));
endmodule
