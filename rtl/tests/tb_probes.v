// tb_probes.v -- self-checking testbench for ts_counter, axis_ts_in and axis_ts_out with random
// source gaps and random sink backpressure.  Plain Verilog-2001: runs on Icarus Verilog
// (iverilog -g2005) and on Vivado xsim.  Prints "TB_PROBES PASS" or "TB_PROBES FAIL".
`timescale 1ns / 1ps
module tb_probes;
    parameter integer N_MSGS = 4000;   // messages / records pushed through each probe
    parameter integer CHUNK  = 7;      // TLAST every CHUNK messages on the input side
    parameter integer SEED   = 12345;

    reg aclk = 1'b0;
    reg aresetn = 1'b0;
    always #5 aclk = ~aclk;  // 100 MHz

    wire [63:0] count;
    ts_counter u_cnt (.aclk(aclk), .aresetn(aresetn), .count(count));
    wire [31:0] ts = count[31:0];

    integer seed;
    integer errors;

    // ------------------------------------------------------------------ axis_ts_in
    reg  [63:0] i_sdata;  reg [7:0] i_skeep; reg i_slast; reg i_svalid; wire i_sready;
    wire [63:0] i_mdata;  wire [7:0] i_mkeep; wire i_mlast; wire i_mvalid; reg i_mready;
    wire [31:0] i_muser;
    axis_ts_in u_in (
        .aclk(aclk), .aresetn(aresetn), .ts(ts),
        .s_axis_tdata(i_sdata), .s_axis_tkeep(i_skeep), .s_axis_tlast(i_slast),
        .s_axis_tvalid(i_svalid), .s_axis_tready(i_sready),
        .m_axis_tdata(i_mdata), .m_axis_tkeep(i_mkeep), .m_axis_tlast(i_mlast),
        .m_axis_tuser(i_muser), .m_axis_tvalid(i_mvalid), .m_axis_tready(i_mready));

    // ------------------------------------------------------------------ axis_ts_out
    reg  [63:0] o_sdata;  reg [7:0] o_skeep; reg o_slast; reg o_svalid; wire o_sready;
    wire [63:0] o_mdata;  wire [7:0] o_mkeep; wire o_mlast; wire o_mvalid; reg o_mready;
    axis_ts_out u_out (
        .aclk(aclk), .aresetn(aresetn), .ts(ts),
        .s_axis_tdata(o_sdata), .s_axis_tkeep(o_skeep), .s_axis_tlast(o_slast),
        .s_axis_tvalid(o_svalid), .s_axis_tready(o_sready),
        .m_axis_tdata(o_mdata), .m_axis_tkeep(o_mkeep), .m_axis_tlast(o_mlast),
        .m_axis_tvalid(o_mvalid), .m_axis_tready(o_mready));

    // beat k of message n: data = {n, k, pattern}
    function [63:0] beat_data(input integer n, input integer k);
        beat_data = {n[31:0], 8'hA5, k[7:0], 16'h5A00 | (n[7:0] ^ k[7:0])};
    endfunction

    integer in_beats_sent, in_beats_seen, out_beats_sent, out_beats_seen;
    integer in_tuser_checked, out_ts_checked, out_last_seen;

    // ------------------------------------------------------------------ input-side source
    always @(posedge aclk) begin
        if (!aresetn) begin
            i_svalid <= 1'b0; in_beats_sent <= 0;
            i_sdata <= 64'd0; i_skeep <= 8'hFF; i_slast <= 1'b0;
        end else begin
            if (i_svalid && i_sready) in_beats_sent <= in_beats_sent + 1;
            // AXIS rule: hold data while valid && !ready
            if (!i_svalid || i_sready) begin
                if ((in_beats_sent + (i_svalid && i_sready)) < 4 * N_MSGS && ($random(seed) & 3) != 0) begin
                    i_svalid <= 1'b1;
                    i_sdata <= beat_data((in_beats_sent + (i_svalid && i_sready)) / 4,
                                         (in_beats_sent + (i_svalid && i_sready)) % 4);
                    i_slast <= (((in_beats_sent + (i_svalid && i_sready)) % (4 * CHUNK)) == 4 * CHUNK - 1) ||
                               ((in_beats_sent + (i_svalid && i_sready)) == 4 * N_MSGS - 1);
                end else begin
                    i_svalid <= 1'b0;
                end
            end
        end
    end

    always @(posedge aclk) i_mready <= aresetn && (($random(seed) % 4) != 0);

    // ------------------------------------------------------------------ input-side monitor
    always @(posedge aclk) begin
        if (!aresetn) begin
            in_beats_seen <= 0; in_tuser_checked <= 0;
        end else if (i_mvalid && i_mready) begin
            if (i_mdata !== beat_data(in_beats_seen / 4, in_beats_seen % 4)) begin
                errors = errors + 1;
                $display("IN  data mismatch at beat %0d", in_beats_seen);
            end
            if ((in_beats_seen % 4) == 0) begin
                if (i_muser !== ts) begin
                    errors = errors + 1;
                    $display("IN  TUSER %0d != ts %0d at beat %0d", i_muser, ts, in_beats_seen);
                end
                in_tuser_checked <= in_tuser_checked + 1;
            end else if (i_muser !== 32'd0) begin
                errors = errors + 1;
                $display("IN  TUSER not zero on beat %0d", in_beats_seen);
            end
            if (i_mkeep !== 8'hFF) errors = errors + 1;
            in_beats_seen <= in_beats_seen + 1;
        end
    end

    // ------------------------------------------------------------------ output-side source
    always @(posedge aclk) begin
        if (!aresetn) begin
            o_svalid <= 1'b0; out_beats_sent <= 0;
            o_sdata <= 64'd0; o_skeep <= 8'hFF; o_slast <= 1'b0;
        end else begin
            if (o_svalid && o_sready) out_beats_sent <= out_beats_sent + 1;
            if (!o_svalid || o_sready) begin
                if ((out_beats_sent + (o_svalid && o_sready)) < 4 * N_MSGS && ($random(seed) % 3) != 0) begin
                    o_svalid <= 1'b1;
                    o_sdata <= beat_data((out_beats_sent + (o_svalid && o_sready)) / 4,
                                         (out_beats_sent + (o_svalid && o_sready)) % 4);
                    // TLAST only on beat 3 of every 5th record (like the 'Z' record)
                    o_slast <= (((out_beats_sent + (o_svalid && o_sready)) % 20) == 19);
                    o_skeep <= 8'hFF;
                end else begin
                    o_svalid <= 1'b0;
                end
            end
        end
    end

    always @(posedge aclk) o_mready <= aresetn && (($random(seed) % 5) != 0);

    // ------------------------------------------------------------------ output-side monitor
    reg [63:0] exp_out;
    always @(posedge aclk) begin
        if (!aresetn) begin
            out_beats_seen <= 0; out_ts_checked <= 0; out_last_seen <= 0;
        end else if (o_mvalid && o_mready) begin
            exp_out = beat_data(out_beats_seen / 4, out_beats_seen % 4);
            if ((out_beats_seen % 4) == 3) begin
                if (o_mdata[63:32] !== ts || o_mdata[31:0] !== exp_out[31:0]) begin
                    errors = errors + 1;
                    $display("OUT beat 3 mismatch: t_out %0d ts %0d", o_mdata[63:32], ts);
                end
                out_ts_checked <= out_ts_checked + 1;
            end else if (o_mdata !== exp_out) begin
                errors = errors + 1;
                $display("OUT data mismatch at beat %0d", out_beats_seen);
            end
            if (o_mlast !== ((out_beats_seen % 20) == 19)) begin
                errors = errors + 1;
                $display("OUT TLAST mismatch at beat %0d", out_beats_seen);
            end
            if (o_mlast) out_last_seen <= out_last_seen + 1;
            if (o_mkeep !== 8'hFF) errors = errors + 1;
            out_beats_seen <= out_beats_seen + 1;
        end
    end

    // ------------------------------------------------------------------ counter + run control
    reg [63:0] prev_count;
    integer cyc;
    initial begin
        seed = SEED;
        errors = 0;
        repeat (4) @(posedge aclk);
        if (count !== 64'd0) begin
            errors = errors + 1;
            $display("ts_counter not reset");
        end
        aresetn = 1'b1;
        @(posedge aclk);
        #1;
        prev_count = count;
        for (cyc = 0; cyc < 200000 && !(in_beats_seen == 4 * N_MSGS && out_beats_seen == 4 * N_MSGS); cyc = cyc + 1) begin
            @(posedge aclk);
            #1;
            if (count !== prev_count + 1) begin
                errors = errors + 1;
                $display("ts_counter skipped: %0d -> %0d", prev_count, count);
            end
            prev_count = count;
        end
        if (in_beats_seen != 4 * N_MSGS || out_beats_seen != 4 * N_MSGS) begin
            errors = errors + 1;
            $display("timeout: in %0d out %0d beats", in_beats_seen, out_beats_seen);
        end
        $display("in: %0d beats, %0d TUSER checks; out: %0d beats, %0d t_out checks, %0d TLAST",
                 in_beats_seen, in_tuser_checked, out_beats_seen, out_ts_checked, out_last_seen);
        if (errors == 0) $display("TB_PROBES PASS");
        else             $display("TB_PROBES FAIL (%0d errors)", errors);
        $finish;
    end
endmodule
