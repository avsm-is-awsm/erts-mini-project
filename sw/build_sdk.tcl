# build_sdk.tcl -- Xilinx SDK 2017.2 (xsct) batch build of the bare-metal application.
#
#   xsct sw/build_sdk.tcl <workspace> <hdf> [standalone|freertos]
#
# Normally run through sw/build.py (make sw), which stages the sources so the relative
# includes of the shared core resolve:  <ws>/hls/src/core, <ws>/proto, <ws>/t2t_app/osal.
set ws  [lindex $argv 0]
set hdf [lindex $argv 1]
set os  [lindex $argv 2]
if {$os eq ""} { set os standalone }
if {$os eq "freertos"} { set os freertos901_xilinx }

setws $ws
createhw -name hw0 -hwspec $hdf
createbsp -name bsp0 -hwproject hw0 -proc ps7_cortexa9_0 -os $os
setlib -bsp bsp0 -lib xilffs
if {$os eq "freertos901_xilinx"} {
    # live replay / RMA build (docs/09): 1 kHz tick, preemptive fixed priorities, no time
    # slicing, 1 MB heap.  The Xilinx port needs the timer module; its service task never
    # runs because the application creates no software timers (docs/09).
    configbsp -bsp bsp0 tick_rate 1000
    configbsp -bsp bsp0 use_preemption true
    configbsp -bsp bsp0 use_timeslicing false
    configbsp -bsp bsp0 max_priorities 8
    configbsp -bsp bsp0 total_heap_size 1048576
    configbsp -bsp bsp0 check_for_stack_overflow 2
    configbsp -bsp bsp0 stdin ps7_uart_1
    configbsp -bsp bsp0 stdout ps7_uart_1
}
# Long file names: dataset folders and result files (day_default/, orders_hw.bin,
# counters_hw.json) are not 8.3 names, and xilffs builds FatFs without LFN by default.
configbsp -bsp bsp0 use_lfn true
updatemss -mss $ws/bsp0/system.mss
regenbsp -bsp bsp0
createapp -name t2t_app -hwproject hw0 -bsp bsp0 -proc ps7_cortexa9_0 -os $os \
    -lang C++ -app {Empty Application}
# the FreeRTOS 'Empty Application' template brings a stub main.cc; ours is main_rtos.cpp
file delete -force $ws/t2t_app/src/main.cc
importsources -name t2t_app -path $ws/app_src
# The generated linker script gives 8 KB of stack and heap; the application formats JSON
# on the stack and uses stdio, so give both 256 KB (DDR is 512 MB).
set ld $ws/t2t_app/src/lscript.ld
set fh [open $ld r]; set txt [read $fh]; close $fh
if {![regsub {_STACK_SIZE : 0x2000;} $txt {_STACK_SIZE : 0x40000;} txt] ||
    ![regsub {_HEAP_SIZE : 0x2000;} $txt {_HEAP_SIZE : 0x40000;} txt]} {
    puts "ERROR: could not set stack/heap size in $ld"
    exit 1
}
set fh [open $ld w]; puts -nonewline $fh $txt; close $fh
configapp -app t2t_app build-config release
configapp -app t2t_app compiler-misc {-std=gnu++11 -Wall -Wno-unknown-pragmas -Wno-unused-label}
configapp -app t2t_app compiler-optimization {Optimize more (-O2)}
projects -build
puts "SDK_BUILD_DONE"
exit
