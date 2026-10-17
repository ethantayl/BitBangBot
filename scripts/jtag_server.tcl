# jtag_server.tcl -- persistent System Console <-> Avalon bridge over a TCP socket.
#
# Run once:   system-console --script=jtag_server.tcl
# Leave it running. Python connects to localhost:2540 and sends commands.
#
# Protocol (one line per command, newline-terminated):
#   R <hexaddr>              -> reads one 32-bit word, replies "<decimal value>"
#   W <hexaddr> <hexval>     -> writes one 32-bit word, replies "OK"
#   B <hexaddr> <count>      -> block read of <count> words, replies space-separated decimals
#   PING                     -> replies "PONG"
# Any error replies "ERR <message>".

# ---- open the JTAG-to-Avalon master service ----
proc open_master {} {
    set paths [get_service_paths master]
    if {[llength $paths] == 0} {
        error "no master service found -- is the board programmed with the JTAG-to-Avalon master?"
    }
    set m [lindex $paths 0]
    open_service master $m
    return $m
}

set MASTER [open_master]
puts "jtag_server: master opened: $MASTER"

# ---- per-line command handler ----
proc handle_line {sock line} {
    global MASTER
    set line [string trim $line]
    if {$line eq ""} { return }
    set parts [split $line]
    set cmd [lindex $parts 0]

    if {[catch {
        switch -- $cmd {
            R {
                set addr [lindex $parts 1]
                set val  [master_read_32 $MASTER $addr 1]
                puts $sock $val
            }
            W {
                set addr [lindex $parts 1]
                set val  [lindex $parts 2]
                master_write_32 $MASTER $addr $val
                puts $sock "OK"
            }
            B {
                set addr  [lindex $parts 1]
                set count [lindex $parts 2]
                # single JTAG transaction for the whole block -- amortizes latency
                set vals [master_read_32 $MASTER $addr $count]
                puts $sock [join $vals " "]
            }
            BW {
                # block write: "BW <hexaddr> <hexval> <hexval> ..."
                # writes the words to consecutive addresses in one transaction
                set addr [lindex $parts 1]
                set vals [lrange $parts 2 end]
                master_write_32 $MASTER $addr $vals
                puts $sock "OK"
            }
            PING { puts $sock "PONG" }
            default { puts $sock "ERR unknown command: $cmd" }
        }
    } err]} {
        puts $sock "ERR $err"
    }
    flush $sock
}

proc on_readable {sock} {
    if {[eof $sock]} {
        close $sock
        return
    }
    if {[gets $sock line] >= 0} {
        handle_line $sock $line
    }
}

proc accept {sock addr port} {
    fconfigure $sock -buffering line -blocking 0
    fileevent $sock readable [list on_readable $sock]
    puts "jtag_server: client connected from $addr:$port"
}

socket -server accept 2540
puts "jtag_server: listening on localhost:2540  (Ctrl-C to stop)"
vwait forever