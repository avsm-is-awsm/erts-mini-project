# build_sdk.tcl -- Xilinx SDK 2017.2 (xsct) batch build of the bare-metal application.
#
#   xsct sw/build_sdk.tcl <workspace> <hdf>
#
# Normally run through sw/build.py (make sw), which stages the sources so the relative
# includes of the shared core resolve:  <ws>/hls/src/core, <ws>/proto, <ws>/t2t_app/osal.
set ws  [lindex $argv 0]
set hdf [lindex $argv 1]

setws $ws
createhw -name hw0 -hwspec $hdf
createbsp -name bsp0 -hwproject hw0 -proc ps7_cortexa9_0 -os standalone
setlib -bsp bsp0 -lib xilffs
updatemss -mss $ws/bsp0/system.mss
regenbsp -bsp bsp0
createapp -name t2t_app -hwproject hw0 -bsp bsp0 -proc ps7_cortexa9_0 -os standalone \
    -lang C++ -app {Empty Application}
importsources -name t2t_app -path $ws/app_src
configapp -app t2t_app build-config release
configapp -app t2t_app compiler-misc {-std=gnu++11 -Wall -Wno-unknown-pragmas -Wno-unused-label}
configapp -app t2t_app compiler-optimization {Optimize more (-O2)}
projects -build
puts "SDK_BUILD_DONE"
exit
