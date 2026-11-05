# build_bd.tcl -- Vivado 2017.2 project + block design + bitstream + hardware handoff for dk1-t2t
# on the original ZYBO (XC7Z010-1CLG400C, board files digilentinc.com:zybo:part0:1.0).
#
#   vivado -mode batch -source hw/build_bd.tcl -tclargs <work_dir> <ip_repo> <rtl_dir> [-ila] [-jobs N] [-nobit]
#
# Normally run through hw/build.py (make vivado), which stages everything in a space-free
# directory and copies bitstream / handoff / reports back into hw/.
#
# Block design (SPEC 9.4):
#   PS7 (FCLK0 100 MHz, GP0 master, HP0 64-bit slave, IRQ_F2P)
#   axi_dma_0 (MM2S + S2MM, simple mode, 64-bit streams, 23-bit length = 2017.2 maximum)
#     MM2S -> axis_ts_in -> t2t_core.md_in
#     t2t_core.ord_out -> axis_ts_out -> S2MM
#   axi_dma_1 (S2MM only) <- t2t_core.dbg_out
#   ts_counter (64-bit) -> [31:0] -> both probes
#   AXI-Lite: GP0 -> interconnect -> {dma0, dma1, t2t_core}
#   AXI-MM:   {dma0 MM2S, dma0 S2MM, dma1 S2MM} -> interconnect -> HP0
#   IRQ: {dma0 mm2s, dma0 s2mm, dma1 s2mm} -> xlconcat -> IRQ_F2P
set work   [lindex $argv 0]
set iprepo [lindex $argv 1]
set rtldir [lindex $argv 2]
set with_ila [expr {[lsearch $argv -ila] >= 0}]
set nobit  [expr {[lsearch $argv -nobit] >= 0}]
set jobs 4
set ji [lsearch $argv -jobs]
if {$ji >= 0} { set jobs [lindex $argv [expr {$ji + 1}]] }

set part  xc7z010clg400-1
set board digilentinc.com:zybo:part0:1.0
if {[lsearch [get_board_parts -quiet] $board] < 0} {
    puts "ERROR: board files for $board not found. Install the Digilent board files"
    puts "       (github.com/Digilent/vivado-boards) into <Vivado>/data/boards/board_files."
    exit 2
}

create_project -force dk1_t2t $work/vivado -part $part
set_property board_part $board [current_project]
set_property target_language Verilog [current_project]
set_property ip_repo_paths [list $iprepo] [current_project]
update_ip_catalog -rebuild
add_files -norecurse [list $rtldir/ts_counter.v $rtldir/axis_ts_in.v $rtldir/axis_ts_out.v]
update_compile_order -fileset sources_1

create_bd_design system

# ---------------------------------------------------------------- PS7
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 processing_system7_0]
apply_bd_automation -rule xilinx.com:bd_rule:processing_system7 \
    -config {make_external "FIXED_IO, DDR" apply_board_preset "1" Master "Disable" Slave "Disable"} $ps
set_property -dict [list \
    CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ {100} \
    CONFIG.PCW_USE_M_AXI_GP0 {1} \
    CONFIG.PCW_USE_S_AXI_HP0 {1} \
    CONFIG.PCW_S_AXI_HP0_DATA_WIDTH {64} \
    CONFIG.PCW_USE_FABRIC_INTERRUPT {1} \
    CONFIG.PCW_IRQ_F2P_INTR {1} \
] $ps

# ---------------------------------------------------------------- DMAs
set dma0 [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_dma:7.1 axi_dma_0]
set_property -dict [list \
    CONFIG.c_include_sg {0} CONFIG.c_sg_length_width {23} \
    CONFIG.c_include_mm2s {1} CONFIG.c_include_s2mm {1} \
    CONFIG.c_m_axi_mm2s_data_width {64} CONFIG.c_m_axis_mm2s_tdata_width {64} \
    CONFIG.c_m_axi_s2mm_data_width {64} \
    CONFIG.c_mm2s_burst_size {64} CONFIG.c_s2mm_burst_size {64} \
] $dma0
set dma1 [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_dma:7.1 axi_dma_1]
set_property -dict [list \
    CONFIG.c_include_sg {0} CONFIG.c_sg_length_width {23} \
    CONFIG.c_include_mm2s {0} CONFIG.c_include_s2mm {1} \
    CONFIG.c_m_axi_s2mm_data_width {64} \
    CONFIG.c_s2mm_burst_size {64} \
] $dma1

# ---------------------------------------------------------------- HLS core + probes + counter
set core [create_bd_cell -type ip -vlnv dk1:hls:t2t_core:1.0 t2t_core_0]
set tin  [create_bd_cell -type module -reference axis_ts_in  axis_ts_in_0]
set tout [create_bd_cell -type module -reference axis_ts_out axis_ts_out_0]
set cnt  [create_bd_cell -type module -reference ts_counter  ts_counter_0]
set slc  [create_bd_cell -type ip -vlnv xilinx.com:ip:xlslice:1.0 ts_slice]
set_property -dict [list CONFIG.DIN_WIDTH {64} CONFIG.DIN_FROM {31} CONFIG.DIN_TO {0}] $slc
connect_bd_net [get_bd_pins ts_counter_0/count] [get_bd_pins ts_slice/Din]
connect_bd_net [get_bd_pins ts_slice/Dout] [get_bd_pins axis_ts_in_0/ts] [get_bd_pins axis_ts_out_0/ts]

# streams
connect_bd_intf_net [get_bd_intf_pins axi_dma_0/M_AXIS_MM2S] [get_bd_intf_pins axis_ts_in_0/S_AXIS]
connect_bd_intf_net [get_bd_intf_pins axis_ts_in_0/M_AXIS] [get_bd_intf_pins t2t_core_0/md_in]
connect_bd_intf_net [get_bd_intf_pins t2t_core_0/ord_out] [get_bd_intf_pins axis_ts_out_0/S_AXIS]
connect_bd_intf_net [get_bd_intf_pins axis_ts_out_0/M_AXIS] [get_bd_intf_pins axi_dma_0/S_AXIS_S2MM]
connect_bd_intf_net [get_bd_intf_pins t2t_core_0/dbg_out] [get_bd_intf_pins axi_dma_1/S_AXIS_S2MM]

# ---------------------------------------------------------------- AXI connectivity (automation)
foreach slv {axi_dma_0/S_AXI_LITE axi_dma_1/S_AXI_LITE t2t_core_0/s_axi_ctrl} {
    apply_bd_automation -rule xilinx.com:bd_rule:axi4 \
        -config {Master "/processing_system7_0/M_AXI_GP0" Clk "Auto"} [get_bd_intf_pins $slv]
}
# DMA masters -> HP0: explicit interconnect (the 2017.2 axi4 automation rule fails here with
# "find_free_intf_pin_on_interconnect: Invalid option value ''")
set clk  [get_bd_pins processing_system7_0/FCLK_CLK0]
set rst_cell [get_bd_cells -filter {VLNV =~ "*proc_sys_reset*"}]
set rstn [get_bd_pins $rst_cell/peripheral_aresetn]
set icrstn [get_bd_pins $rst_cell/interconnect_aresetn]
set ichp [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 axi_ic_hp0]
set_property -dict [list CONFIG.NUM_SI {3} CONFIG.NUM_MI {1}] $ichp
connect_bd_intf_net [get_bd_intf_pins axi_dma_0/M_AXI_MM2S] [get_bd_intf_pins axi_ic_hp0/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_dma_0/M_AXI_S2MM] [get_bd_intf_pins axi_ic_hp0/S01_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_dma_1/M_AXI_S2MM] [get_bd_intf_pins axi_ic_hp0/S02_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_ic_hp0/M00_AXI] [get_bd_intf_pins processing_system7_0/S_AXI_HP0]
foreach p {ACLK S00_ACLK S01_ACLK S02_ACLK M00_ACLK} { connect_bd_net $clk [get_bd_pins axi_ic_hp0/$p] }
connect_bd_net $icrstn [get_bd_pins axi_ic_hp0/ARESETN]
foreach p {S00_ARESETN S01_ARESETN S02_ARESETN M00_ARESETN} { connect_bd_net $rstn [get_bd_pins axi_ic_hp0/$p] }

# clocks / resets for everything not wired by the automation
foreach c {processing_system7_0/S_AXI_HP0_ACLK axi_dma_0/m_axi_mm2s_aclk axi_dma_0/m_axi_s2mm_aclk
           axi_dma_1/m_axi_s2mm_aclk t2t_core_0/ap_clk axis_ts_in_0/aclk axis_ts_out_0/aclk ts_counter_0/aclk} {
    if {[llength [get_bd_nets -quiet -of_objects [get_bd_pins $c]]] == 0} { connect_bd_net $clk [get_bd_pins $c] }
}
foreach r {t2t_core_0/ap_rst_n axis_ts_in_0/aresetn axis_ts_out_0/aresetn ts_counter_0/aresetn} {
    if {[llength [get_bd_nets -quiet -of_objects [get_bd_pins $r]]] == 0} { connect_bd_net [lindex $rstn 0] [get_bd_pins $r] }
}

# ---------------------------------------------------------------- interrupts
set cc [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconcat:2.1 irq_concat]
set_property CONFIG.NUM_PORTS {3} $cc
connect_bd_net [get_bd_pins axi_dma_0/mm2s_introut] [get_bd_pins irq_concat/In0]
connect_bd_net [get_bd_pins axi_dma_0/s2mm_introut] [get_bd_pins irq_concat/In1]
connect_bd_net [get_bd_pins axi_dma_1/s2mm_introut] [get_bd_pins irq_concat/In2]
connect_bd_net [get_bd_pins irq_concat/dout] [get_bd_pins processing_system7_0/IRQ_F2P]

# ---------------------------------------------------------------- optional ILA on the probes
if {$with_ila} {
    set ila [create_bd_cell -type ip -vlnv xilinx.com:ip:ila:6.2 ila_0]
    set_property -dict [list CONFIG.C_NUM_OF_PROBES {4} CONFIG.C_DATA_DEPTH {4096} \
        CONFIG.C_PROBE0_WIDTH {32} CONFIG.C_PROBE1_WIDTH {1} CONFIG.C_PROBE2_WIDTH {1} CONFIG.C_PROBE3_WIDTH {1}] $ila
    connect_bd_net $clk [get_bd_pins ila_0/clk]
    connect_bd_net [get_bd_pins ts_slice/Dout] [get_bd_pins ila_0/probe0]
    connect_bd_net [get_bd_pins axis_ts_in_0/m_axis_tvalid] [get_bd_pins ila_0/probe1]
    connect_bd_net [get_bd_pins axis_ts_out_0/m_axis_tvalid] [get_bd_pins ila_0/probe2]
    connect_bd_net [get_bd_pins axis_ts_out_0/m_axis_tlast] [get_bd_pins ila_0/probe3]
}

assign_bd_address
validate_bd_design
save_bd_design
write_bd_tcl -force $work/system_bd_export.tcl
report_ip_status

# address map for the software (sw/app/platform_addrs.h is generated from this)
set fh [open $work/address_map.txt w]
foreach seg [get_bd_addr_segs -of_objects [get_bd_addr_spaces processing_system7_0/Data]] {
    puts $fh "[get_property NAME $seg] [get_property OFFSET $seg] [get_property RANGE $seg]"
}
close $fh

set bd [get_files system.bd]
generate_target all $bd
make_wrapper -files $bd -top
add_files -norecurse [glob $work/vivado/dk1_t2t.srcs/sources_1/bd/system/hdl/system_wrapper.v]
set_property top system_wrapper [current_fileset]
update_compile_order -fileset sources_1

if {$nobit} { puts "BD_ONLY_DONE"; exit 0 }

launch_runs synth_1 -jobs $jobs
wait_on_run synth_1
launch_runs impl_1 -to_step write_bitstream -jobs $jobs
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] != "100%"} {
    puts "ERROR: implementation did not complete"
    exit 1
}
open_run impl_1
file mkdir $work/reports
report_utilization -file $work/reports/utilization.rpt
report_utilization -hierarchical -file $work/reports/utilization_hier.rpt
report_timing_summary -max_paths 10 -file $work/reports/timing_summary.rpt
report_power -file $work/reports/power.rpt
set wns [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -setup]]
set whs [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -hold]]
set fh [open $work/reports/summary.txt w]
puts $fh "wns_ns $wns"
puts $fh "whs_ns $whs"
close $fh
file copy -force [glob $work/vivado/dk1_t2t.runs/impl_1/system_wrapper.bit] $work/dk1_t2t.bit
# SDK 2017.2 hardware handoff (.hdf; Vitis 2019.2+ uses .xsa, DECISIONS D-29)
write_hwdef -force -file $work/dk1_t2t.hdf
write_sysdef -force -hwdef $work/dk1_t2t.hdf -bitfile $work/dk1_t2t.bit -file $work/dk1_t2t.sysdef
puts "BUILD_DONE wns=$wns whs=$whs"
exit 0
