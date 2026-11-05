# run_hls.tcl -- Vivado HLS 2017.2 (also works in Vitis HLS 2020.x+ with vitis_hls) flow for t2t_core.
#
#   cd hls/build && vivado_hls -f ../scripts/run_hls.tcl
#
# Controlled by environment variables (tclargs are not portable across HLS versions):
#   T2T_STEPS    comma list of csim,csynth,cosim,export          (default: csim,csynth)
#   T2T_DS       dataset folder name copied into hls/build/ds   (default: cosim_10k)
#   T2T_MAXMSGS  limit messages fed by the testbench (cosim)    (default: all)
#   T2T_CLOCK    clock period in ns                             (default: 10)
#   T2T_PART     FPGA part                                      (default: xc7z010clg400-1, ZYBO)
#   T2T_SOL      solution name                                  (default: sol1)
#
# The testbench runs inside <prj>/<sol>/{csim/build,sim/wrapc}; both are four levels below
# hls/build, so the dataset is staged at hls/build/ds and passed as ../../../../ds.
proc envor {name def} {
    if {[info exists ::env($name)] && $::env($name) ne ""} { return $::env($name) }
    return $def
}
set steps   [split [envor T2T_STEPS "csim,csynth"] ","]
set ds      [envor T2T_DS cosim_10k]
set maxmsgs [envor T2T_MAXMSGS ""]
set clk     [envor T2T_CLOCK 10]
set part    [envor T2T_PART xc7z010clg400-1]
set sol     [envor T2T_SOL sol1]

set here    [file dirname [file normalize [info script]]]
set hlsdir  [file dirname $here]
set root    [file dirname $hlsdir]
set src     "$hlsdir/src"
set tb      "$hlsdir/tb"

# ---- stage the dataset (feed + config + golden files) next to the project
file mkdir ds
if {[file exists "$root/data/out/$ds"]} {
    foreach f [glob -nocomplain -directory "$root/data/out/$ds" feed.bin config.json golden_*] {
        file copy -force $f ds/
    }
}
set tbargs "../../../../ds"
if {$maxmsgs ne ""} { append tbargs " ../../../../ds $maxmsgs" }

proc has {s} { global steps; expr {[lsearch $steps $s] >= 0} }

open_project -reset t2t_prj
set_top t2t_core
set cflags "-I$src"
if {[has cosim]} {
    # C/RTL co-simulation of an ap_ctrl_none design with II > 1 is not supported by
    # Vivado HLS; co-simulate the same datapath with ap_ctrl_hs (DECISIONS D-17).
    append cflags " -DT2T_COSIM_CTRL_HS"
}
add_files "$src/t2t_top.cpp" -cflags $cflags
add_files -tb "$tb/tb_t2t.cpp" -cflags "-I$src -Wno-unknown-pragmas"
open_solution -reset $sol
set_part $part
create_clock -period $clk -name default
config_rtl -reset control -reset_level high
config_interface -m_axi_addr64=0

if {[has csim]} {
    csim_design -argv $tbargs -clean
}
if {[has csynth] || [has cosim] || [has export] || [has impl]} {
    csynth_design
}
if {[has cosim]} {
    cosim_design -argv $tbargs -rtl verilog -trace_level none
}
if {[has export] || [has impl]} {
    # 'impl' = export + Vivado out-of-context place & route, run by hls.py (ooc_impl.tcl)
    export_design -format ip_catalog -vendor "dk1" -library "hls" -version "1.0"         -display_name "t2t_core" -description "DK1 tick-to-trade engine (dk1-t2t)"
}
exit
