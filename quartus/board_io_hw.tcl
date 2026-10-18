#
# board_io "board_io" v1.0
# DE10-Lite seven-segment displays + red LEDs (see source/board_io.v)
#

#
# request TCL package from ACDS 16.1
#
package require -exact qsys 16.1


#
# module board_io
#
set_module_property DESCRIPTION "DE10-Lite HEX0-5 + LEDR, raw readable registers"
set_module_property NAME board_io
set_module_property VERSION 1.0
set_module_property INTERNAL false
set_module_property OPAQUE_ADDRESS_MAP true
set_module_property AUTHOR ""
set_module_property DISPLAY_NAME board_io
set_module_property INSTANTIATE_IN_SYSTEM_MODULE true
set_module_property EDITABLE true
set_module_property REPORT_TO_TALKBACK false
set_module_property ALLOW_GREYBOX_GENERATION false
set_module_property REPORT_HIERARCHY false


#
# file sets
#
add_fileset QUARTUS_SYNTH QUARTUS_SYNTH "" ""
set_fileset_property QUARTUS_SYNTH TOP_LEVEL board_io
set_fileset_property QUARTUS_SYNTH ENABLE_RELATIVE_INCLUDE_PATHS false
set_fileset_property QUARTUS_SYNTH ENABLE_FILE_OVERWRITE_MODE false
add_fileset_file board_io.v VERILOG PATH source/board_io.v TOP_LEVEL_FILE


#
# parameters
#
add_parameter ABITS INTEGER 2
set_parameter_property ABITS DEFAULT_VALUE 2
set_parameter_property ABITS DISPLAY_NAME ABITS
set_parameter_property ABITS TYPE INTEGER
set_parameter_property ABITS UNITS None
set_parameter_property ABITS ALLOWED_RANGES 2:2
set_parameter_property ABITS HDL_PARAMETER true


#
# connection point clock
#
add_interface clock clock end
set_interface_property clock clockRate 0
set_interface_property clock ENABLED true
set_interface_property clock EXPORT_OF ""
set_interface_property clock PORT_NAME_MAP ""
set_interface_property clock CMSIS_SVD_VARIABLES ""
set_interface_property clock SVD_ADDRESS_GROUP ""

add_interface_port clock clk clk Input 1


#
# connection point reset
#
add_interface reset reset end
set_interface_property reset associatedClock clock
set_interface_property reset synchronousEdges DEASSERT
set_interface_property reset ENABLED true
set_interface_property reset EXPORT_OF ""
set_interface_property reset PORT_NAME_MAP ""
set_interface_property reset CMSIS_SVD_VARIABLES ""
set_interface_property reset SVD_ADDRESS_GROUP ""

add_interface_port reset reset reset Input 1


#
# connection point avalon_slave_0
#
add_interface avalon_slave_0 avalon end
set_interface_property avalon_slave_0 addressUnits WORDS
set_interface_property avalon_slave_0 associatedClock clock
set_interface_property avalon_slave_0 associatedReset reset
set_interface_property avalon_slave_0 bitsPerSymbol 8
set_interface_property avalon_slave_0 burstOnBurstBoundariesOnly false
set_interface_property avalon_slave_0 burstcountUnits WORDS
set_interface_property avalon_slave_0 explicitAddressSpan 0
set_interface_property avalon_slave_0 holdTime 0
set_interface_property avalon_slave_0 linewrapBursts false
set_interface_property avalon_slave_0 maximumPendingReadTransactions 1
set_interface_property avalon_slave_0 maximumPendingWriteTransactions 0
set_interface_property avalon_slave_0 readLatency 0
set_interface_property avalon_slave_0 readWaitTime 1
set_interface_property avalon_slave_0 setupTime 0
set_interface_property avalon_slave_0 timingUnits Cycles
set_interface_property avalon_slave_0 writeWaitTime 0
set_interface_property avalon_slave_0 ENABLED true
set_interface_property avalon_slave_0 EXPORT_OF ""
set_interface_property avalon_slave_0 PORT_NAME_MAP ""
set_interface_property avalon_slave_0 CMSIS_SVD_VARIABLES ""
set_interface_property avalon_slave_0 SVD_ADDRESS_GROUP ""

add_interface_port avalon_slave_0 avs_address address Input "((ABITS-1)) - (0) + 1"
add_interface_port avalon_slave_0 avs_read read Input 1
add_interface_port avalon_slave_0 avs_write write Input 1
add_interface_port avalon_slave_0 avs_writedata writedata Input 32
add_interface_port avalon_slave_0 avs_readdata readdata Output 32
add_interface_port avalon_slave_0 avs_readdatavalid readdatavalid Output 1
add_interface_port avalon_slave_0 avs_waitrequest waitrequest Output 1
set_interface_assignment avalon_slave_0 embeddedsw.configuration.isFlash 0
set_interface_assignment avalon_slave_0 embeddedsw.configuration.isMemoryDevice 0
set_interface_assignment avalon_slave_0 embeddedsw.configuration.isNonVolatileStorage 0
set_interface_assignment avalon_slave_0 embeddedsw.configuration.isPrintableDevice 0


#
# connection point board (export as "board" -> board_hex0 .. board_ledr)
#
add_interface board conduit end
set_interface_property board associatedClock clock
set_interface_property board associatedReset ""
set_interface_property board ENABLED true
set_interface_property board EXPORT_OF ""
set_interface_property board PORT_NAME_MAP ""
set_interface_property board CMSIS_SVD_VARIABLES ""
set_interface_property board SVD_ADDRESS_GROUP ""

add_interface_port board hex0 hex0 Output 8
add_interface_port board hex1 hex1 Output 8
add_interface_port board hex2 hex2 Output 8
add_interface_port board hex3 hex3 Output 8
add_interface_port board hex4 hex4 Output 8
add_interface_port board hex5 hex5 Output 8
add_interface_port board ledr ledr Output 10
