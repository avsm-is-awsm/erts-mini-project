# run_board.tcl -- program the ZYBO over JTAG and start the application (NEEDS-USER: board).
#
#   xsct sw/run_board.tcl [elf]     (default sw/bin/t2t_app.elf; sw/bin/t2t_rtos.elf = FreeRTOS)
#
# Requires hw/dk1_t2t.bit, sw/bin/t2t_app.elf and sw/bin/ps7_init.tcl (all produced by
# make vivado && make sw).  Open the USB-UART at 115200 8N1 before running.
set here [file dirname [file normalize [info script]]]
set root [file dirname $here]
connect
targets -set -nocase -filter {name =~ "APU*"}
rst -system
after 1500
fpga -file $root/hw/dk1_t2t.bit
targets -set -nocase -filter {name =~ "ARM*#0"}
source $here/bin/ps7_init.tcl
ps7_init
ps7_post_config
set elf $here/bin/t2t_app.elf
if {[llength $argv] > 0} { set elf [file normalize [lindex $argv 0]] }
puts "ELF: $elf"
dow $elf
con
puts "t2t_app running: use the UART console (t2t> help)"
