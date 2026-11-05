# ooc_impl.tcl -- Vivado out-of-context synthesis + place & route of the HLS-generated
# t2t_core RTL; writes post-route utilization and timing reports.
#
#   cd <solution>/impl/verilog && vivado -mode batch -source ooc_impl.tcl -tclargs <part> <period_ns> <outdir>
#
# Equivalent to Vivado HLS "export_design -flow impl" (which is unusable in 2017.2 after 2021
# because of the IP core_revision overflow, see DECISIONS D-19).
set part   [lindex $argv 0]
set period [lindex $argv 1]
set outdir [lindex $argv 2]
file mkdir $outdir

read_verilog [glob *.v]
synth_design -top t2t_core -part $part -mode out_of_context -flatten_hierarchy rebuilt
create_clock -name ap_clk -period $period [get_ports ap_clk]
report_utilization -file $outdir/ooc_synth_utilization.rpt
opt_design
place_design
phys_opt_design
route_design
report_utilization -file $outdir/ooc_utilization.rpt
report_utilization -hierarchical -file $outdir/ooc_utilization_hier.rpt
report_timing_summary -max_paths 10 -file $outdir/ooc_timing.rpt
report_timing -max_paths 5 -sort_by slack -file $outdir/ooc_timing_paths.rpt
set wns [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -setup]]
set fh [open $outdir/ooc_summary.txt w]
puts $fh "part $part"
puts $fh "period_ns $period"
puts $fh "wns_ns $wns"
close $fh
exit
