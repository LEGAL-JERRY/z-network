# ============================================================
# Z-NETWORK POLLER
# MikroTik <-> Supabase command/heartbeat system
#
# Supported commands:
#   throttle
#   suspend
#   resume
#   change_profile
#   reboot
#
# IMPORTANT:
# The reboot command reboots THIS MIKROTIK.
# ============================================================


# ============================================================
# CONFIG
# ============================================================

:local supabaseUrl "https://yohndnmwvgcwadkytmix.supabase.co"
:local anonKey "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InlvaG5kbm13dmdjd2Fka3l0bWl4Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzkwNDEyODgsImV4cCI6MjA5NDYxNzI4OH0.hkUexJ-AS1GpLJL78F7iXDv5PkwuPbShj2fsGVXuEsg"
:local routerId "d0ded7d7-503d-40ec-845d-c8a5fc368d11"
:local routerToken "be97a8cf6ae7c2a123461de4f91d61f074d9fc81308f622ed5829e98fb6f3310"


# ============================================================
# HELPER: LOG PREFIX
# ============================================================

:local logPrefix "znetwork-poll"


# ============================================================
# 1. RECOVER STUCK COMMANDS
# ============================================================

:do {

    /tool fetch \
        url="$supabaseUrl/rest/v1/rpc/rpc_recover_stuck_commands" \
        http-method=post \
        http-header-field="apikey: $anonKey,Authorization: Bearer $anonKey,Content-Type: application/json" \
        http-data="{\"p_router_id\":\"$routerId\",\"p_token\":\"$routerToken\"}" \
        dst-path="recover.txt"

    :local recoverResult [/file get recover.txt contents]

    :log warning ("$logPrefix: recovered stuck rows, result=" . $recoverResult)

} on-error={

    :log warning "$logPrefix: recover-stuck call failed (non-fatal)"

}


# ============================================================
# 2. FETCH PENDING COMMANDS
# ============================================================

:local fetchOk false
:local raw ""

:for i from=1 to=2 do={

    :if ($fetchOk = false) do={

        :do {

            :local fr [/tool fetch \
                url="$supabaseUrl/rest/v1/rpc/rpc_get_pending_commands" \
                http-method=post \
                http-header-field="apikey: $anonKey,Authorization: Bearer $anonKey,Content-Type: application/json" \
                http-data="{\"p_router_id\":\"$routerId\",\"p_token\":\"$routerToken\"}" \
                dst-path="cmds.txt" \
                as-value]

            :if (($fr->"status") = "finished") do={

                :set fetchOk true

                :log warning "$logPrefix: command fetch successful"

            } else={

                :log warning ("$logPrefix: fetch attempt " . $i . " status=" . ($fr->"status"))

                :delay 2s

            }

        } on-error={

            :log warning ("$logPrefix: fetch attempt " . $i . " threw error")

            :delay 2s

        }

    }

}


# ============================================================
# 3. STOP IF FETCH FAILED
# ============================================================

:if ($fetchOk = false) do={

    :log warning "$logPrefix: giving up, could not fetch pending commands"

} else={


    # ========================================================
    # 4. COLLECT SEEN MAC ADDRESSES
    # ========================================================

    :local macList ""

    :foreach h in=[/ip hotspot host find] do={

        :local hmac [/ip hotspot host get $h mac-address]

        :if ($macList = "") do={

            :set macList $hmac

        } else={

            :set macList ($macList . "|" . $hmac)

        }

    }

    :log warning ("$logPrefix: seen macs=" . $macList)


    # ========================================================
    # 5. SEND HEARTBEAT
    # ========================================================

    :do {

        /tool fetch \
            url="$supabaseUrl/rest/v1/rpc/rpc_router_heartbeat" \
            http-method=post \
            http-header-field="apikey: $anonKey,Authorization: Bearer $anonKey,Content-Type: application/json" \
            http-data="{\"p_router_id\":\"$routerId\",\"p_token\":\"$routerToken\",\"p_seen_macs\":\"$macList\"}" \
            dst-path="hb.txt"

        :local hbResult [/file get hb.txt contents]

        :log warning ("$logPrefix: heartbeat response=" . $hbResult)

    } on-error={

        :log warning "$logPrefix: heartbeat call failed (non-fatal)"

    }


    # ========================================================
    # 6. READ COMMAND RESPONSE
    # ========================================================

    :set raw [/file get cmds.txt contents]


    # Remove surrounding [ ]
    :if ([:len $raw] >= 2) do={

        :set raw [:pick $raw 1 ([:len $raw] - 1)]

    }


    :log warning ("$logPrefix: RAW=[" . $raw . "]")


    # ========================================================
    # 7. PROCESS COMMANDS
    # ========================================================

    :if ([:len $raw] > 0) do={


        # ----------------------------------------------------
        # Supabase returns multiple commands separated by:
        # literal backslash + n
        # ----------------------------------------------------

        :local sep "\\n"
        :local sepLen [:len $sep]

        :local raw2 ($raw . $sep)
        :local rawLen [:len $raw2]

        :local buf ""
        :local idx 0


        # ----------------------------------------------------
        # Split commands
        # ----------------------------------------------------

        :while ($idx < $rawLen) do={

            :local isSep false


            :if (($idx + $sepLen) <= $rawLen) do={

                :local window [:pick $raw2 $idx ($idx + $sepLen)]

                :if ($window = $sep) do={

                    :set isSep true

                }

            }


            # =================================================
            # END OF COMMAND LINE
            # =================================================

            :if ($isSep = true) do={


                :if ([:len $buf] >= 1) do={


                    # =========================================
                    # PARSE:
                    #
                    # id|command|username|parameter
                    #
                    # Example:
                    # UUID|reboot||
                    #
                    # IMPORTANT:
                    # Empty username/parameter is valid.
                    # =========================================


                    :local p1 [:find $buf "|"]


                    :if ($p1 != nil) do={


                        :local id [:pick $buf 0 $p1]

                        :local rest1 [:pick $buf ($p1 + 1) [:len $buf]]

                        :local p2 [:find $rest1 "|"]


                        :if ($p2 != nil) do={


                            :local ctype [:pick $rest1 0 $p2]

                            :local rest2 [:pick $rest1 ($p2 + 1) [:len $rest1]]

                            :local p3 [:find $rest2 "|"]


                            :if ($p3 != nil) do={


                                :local uname [:pick $rest2 0 $p3]

                                :local param [:pick $rest2 ($p3 + 1) [:len $rest2]]


                                # =================================
                                # LOG PARSED COMMAND
                                # =================================

                                :log warning (
                                    "$logPrefix: PARSED id=" . $id .
                                    " type=" . $ctype .
                                    " user=" . $uname .
                                    " param=" . $param
                                )


                                # =================================
                                # SPECIAL REBOOT COMMAND
                                # =================================

                                :if ($ctype = "reboot") do={


                                    :log warning (
                                        "$logPrefix: REBOOT COMMAND RECEIVED id=" . $id
                                    )


                                    # ---------------------------------
                                    # ACK BEFORE REBOOT
                                    # ---------------------------------

                                    :local ackOk false

                                    :for j from=1 to=2 do={

                                        :if ($ackOk = false) do={

                                            :do {

                                                :local ar [/tool fetch \
                                                    url="$supabaseUrl/rest/v1/rpc/rpc_ack_command" \
                                                    http-method=post \
                                                    http-header-field="apikey: $anonKey,Authorization: Bearer $anonKey,Content-Type: application/json" \
                                                    http-data="{\"p_command_id\":\"$id\",\"p_router_id\":\"$routerId\",\"p_token\":\"$routerToken\",\"p_status\":\"completed\",\"p_error\":\"\"}" \
                                                    dst-path="ack-reboot.txt" \
                                                    as-value]


                                                :if (($ar->"status") = "finished") do={

                                                    :set ackOk true

                                                    :log warning (
                                                        "$logPrefix: reboot command ACK successful id=" . $id
                                                    )

                                                } else={

                                                    :log warning (
                                                        "$logPrefix: reboot ACK attempt " .
                                                        $j .
                                                        " status=" .
                                                        ($ar->"status")
                                                    )

                                                    :delay 2s

                                                }

                                            } on-error={

                                                :log warning (
                                                    "$logPrefix: reboot ACK attempt " .
                                                    $j .
                                                    " failed"
                                                )

                                                :delay 2s

                                            }

                                        }

                                    }


                                    # ---------------------------------
                                    # ONLY REBOOT IF ACK SUCCEEDED
                                    # ---------------------------------

                                    :if ($ackOk = true) do={

                                        :log warning (
                                            "$logPrefix: ACK confirmed - MikroTik rebooting in 5 seconds"
                                        )

                                        :delay 5s

                                        /system reboot

                                    } else={

                                        :log warning (
                                            "$logPrefix: REBOOT ABORTED - ACK failed"
                                        )

                                    }


                                } else={


                                    # =================================
                                    # NORMAL HOTSPOT COMMANDS
                                    # =================================

                                    :local status "failed"
                                    :local errmsg ""


                                    :do {


                                        # ---------------------------------
                                        # THROTTLE
                                        # ---------------------------------

                                        :if ($ctype = "throttle") do={

                                            :local userId [/ip hotspot user find name=$uname]

                                            :if ([:len $userId] = 0) do={

                                                :error "hotspot user not found"

                                            }

                                            /ip hotspot user set $userId profile=$param

                                            /ip hotspot active remove [find user=$uname]

                                            :set status "completed"

                                            :log warning (
                                                "$logPrefix: throttle completed user=" .
                                                $uname .
                                                " profile=" .
                                                $param
                                            )

                                        }


                                        # ---------------------------------
                                        # SUSPEND
                                        # ---------------------------------

                                        :if ($ctype = "suspend") do={

                                            :local userId [/ip hotspot user find name=$uname]

                                            :if ([:len $userId] = 0) do={

                                                :error "hotspot user not found"

                                            }

                                            /ip hotspot user set $userId disabled=yes

                                            /ip hotspot active remove [find user=$uname]

                                            :set status "completed"

                                            :log warning (
                                                "$logPrefix: suspend completed user=" .
                                                $uname
                                            )

                                        }


                                        # ---------------------------------
                                        # RESUME
                                        # ---------------------------------

                                        :if ($ctype = "resume") do={

                                            :local userId [/ip hotspot user find name=$uname]

                                            :if ([:len $userId] = 0) do={

                                                :error "hotspot user not found"

                                            }

                                            /ip hotspot user set $userId disabled=no profile=$param

                                            :set status "completed"

                                            :log warning (
                                                "$logPrefix: resume completed user=" .
                                                $uname .
                                                " profile=" .
                                                $param
                                            )

                                        }


                                        # ---------------------------------
                                        # CHANGE PROFILE
                                        # ---------------------------------

                                        :if ($ctype = "change_profile") do={

                                            :local userId [/ip hotspot user find name=$uname]

                                            :if ([:len $userId] = 0) do={

                                                :error "hotspot user not found"

                                            }

                                            /ip hotspot user set $userId profile=$param

                                            /ip hotspot active remove [find user=$uname]

                                            :set status "completed"

                                            :log warning (
                                                "$logPrefix: change_profile completed user=" .
                                                $uname .
                                                " profile=" .
                                                $param
                                            )

                                        }


                                        # ---------------------------------
                                        # UNKNOWN COMMAND
                                        # ---------------------------------

                                        :if (
                                            ($ctype != "throttle") &&
                                            ($ctype != "suspend") &&
                                            ($ctype != "resume") &&
                                            ($ctype != "change_profile")
                                        ) do={

                                            :set errmsg ("unknown command type: " . $ctype)

                                            :error $errmsg

                                        }


                                    } on-error={

                                        :set status "failed"

                                        :if ($errmsg = "") do={

                                            :set errmsg "command execution failed"

                                        }

                                        :log warning (
                                            "$logPrefix: command FAILED id=" .
                                            $id .
                                            " type=" .
                                            $ctype .
                                            " error=" .
                                            $errmsg
                                        )

                                    }


                                    # =================================
                                    # ACK NORMAL COMMAND
                                    # =================================

                                    :local ackOk false


                                    :for j from=1 to=2 do={

                                        :if ($ackOk = false) do={

                                            :do {

                                                :local ar [/tool fetch \
                                                    url="$supabaseUrl/rest/v1/rpc/rpc_ack_command" \
                                                    http-method=post \
                                                    http-header-field="apikey: $anonKey,Authorization: Bearer $anonKey,Content-Type: application/json" \
                                                    http-data="{\"p_command_id\":\"$id\",\"p_router_id\":\"$routerId\",\"p_token\":\"$routerToken\",\"p_status\":\"$status\",\"p_error\":\"$errmsg\"}" \
                                                    dst-path="ack.txt" \
                                                    as-value]


                                                :if (($ar->"status") = "finished") do={

                                                    :set ackOk true

                                                    :log warning (
                                                        "$logPrefix: ACK successful id=" .
                                                        $id .
                                                        " status=" .
                                                        $status
                                                    )

                                                } else={

                                                    :log warning (
                                                        "$logPrefix: ACK attempt " .
                                                        $j .
                                                        " status=" .
                                                        ($ar->"status")
                                                    )

                                                    :delay 2s

                                                }

                                            } on-error={

                                                :log warning (
                                                    "$logPrefix: ACK attempt " .
                                                    $j .
                                                    " failed id=" .
                                                    $id
                                                )

                                                :delay 2s

                                            }

                                        }

                                    }


                                    :if ($ackOk = false) do={

                                        :log warning (
                                            "$logPrefix: ACK FAILED after retries id=" .
                                            $id .
                                            " - command may be recovered later"
                                        )

                                    }

                                }


                            } else={

                                :log warning (
                                    "$logPrefix: malformed command - missing third separator: " .
                                    $buf
                                )

                            }


                        } else={

                            :log warning (
                                "$logPrefix: malformed command - missing second separator: " .
                                $buf
                            )

                        }


                    } else={

                        :log warning (
                            "$logPrefix: malformed command - missing first separator: " .
                            $buf
                        )

                    }


                } else={

                    :log warning "$logPrefix: empty command skipped"

                }


                # Reset command buffer

                :set buf ""

                :set idx ($idx + $sepLen)


            } else={


                # Add current character to buffer

                :set buf ($buf . [:pick $raw2 $idx ($idx + 1)])

                :set idx ($idx + 1)

            }

        }

    } else={

        :log warning "$logPrefix: no pending commands"

    }

}
