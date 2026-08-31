# ==== CONFIG ====
:local supabaseUrl "https://yohndnmwvgcwadkytmix.supabase.co"
:local anonKey "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InlvaG5kbm13dmdjd2Fka3l0bWl4Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzkwNDEyODgsImV4cCI6MjA5NDYxNzI4OH0.hkUexJ-AS1GpLJL78F7iXDv5PkwuPbShj2fsGVXuEsg"
:local routerId "d0ded7d7-503d-40ec-845d-c8a5fc368d11"
:local routerToken "be97a8cf6ae7c2a123461de4f91d61f074d9fc81308f622ed5829e98fb6f3310"

# ==== RECOVER STUCK COMMANDS ====
:do {
/tool fetch url="$supabaseUrl/rest/v1/rpc/rpc_recover_stuck_commands" \
http-method=post \
http-header-field="apikey: $anonKey,Authorization: Bearer $anonKey,Content-Type: application/json" \
http-data="{\"p_router_id\":\"$routerId\",\"p_token\":\"$routerToken\"}" \
dst-path="recover.txt"
:local recoverResult [/file get recover.txt contents]
:if ($recoverResult != "0") do={
:log warning ("znetwork-poll: recovered stuck rows, result=" . $recoverResult)
}
} on-error={
:log warning "znetwork-poll: recover-stuck call threw error (non-fatal, continuing)"
}

# ==== FETCH PENDING COMMANDS ====
:local fetchOk false
:local raw ""
:for i from=1 to=2 do={
:if ($fetchOk = false) do={
:do {
:local fr [/tool fetch url="$supabaseUrl/rest/v1/rpc/rpc_get_pending_commands" \
http-method=post \
http-header-field="apikey: $anonKey,Authorization: Bearer $anonKey,Content-Type: application/json" \
http-data="{\"p_router_id\":\"$routerId\",\"p_token\":\"$routerToken\"}" \
dst-path="cmds.txt" as-value]
:if (($fr->"status") = "finished") do={
:set fetchOk true
} else={
:log warning ("znetwork-poll: fetch attempt $i status=" . ($fr->"status"))
:delay 2
}
} on-error={
:log warning "znetwork-poll: fetch attempt $i threw error"
:delay 2
}
}
}

:if ($fetchOk = false) do={
:log warning "znetwork-poll: giving up, could not fetch pending commands"
} else={
# ==== COLLECT SEEN MACS (from hotspot host table — includes idle/recently-seen) ====
:local macList ""
:foreach h in=[/ip hotspot host find] do={
:local hmac [/ip hotspot host get $h mac-address]
:if ($macList = "") do={
:set macList $hmac
} else={
:set macList ($macList . "|" . $hmac)
}
}
:log warning ("znetwork-poll: seen macs=" . $macList)

# ==== HEARTBEAT (always fires on a successful poll, even with 0 pending) ====
:do {
/tool fetch url="$supabaseUrl/rest/v1/rpc/rpc_router_heartbeat" \
http-method=post \
http-header-field="apikey: $anonKey,Authorization: Bearer $anonKey,Content-Type: application/json" \
http-data="{\"p_router_id\":\"$routerId\",\"p_token\":\"$routerToken\",\"p_seen_macs\":\"$macList\"}" \
dst-path="hb.txt"
:local hbResult [/file get hb.txt contents]
:log warning ("znetwork-poll: heartbeat response=" . $hbResult)
} on-error={
:log warning "znetwork-poll: heartbeat call threw error (non-fatal, continuing)"
}

:local raw [/file get cmds.txt contents]
:set raw [:pick $raw 1 ([:len $raw]-1)]
:log warning ("znetwork-poll: RAW=[" . $raw . "]")

:if ([:len $raw] > 0) do={
# separator is the 2 literal chars: backslash + n
:local sep "\\n"
:local sepLen [:len $sep]
:local raw2 ($raw . $sep)
:local rawLen [:len $raw2]
:local buf ""
:local idx 0

:while ($idx < $rawLen) do={
:local isSep false
:if (($idx + $sepLen) <= $rawLen) do={
:local window [:pick $raw2 $idx ($idx+$sepLen)]
:if ($window = $sep) do={:set isSep true}
}
:if ($isSep = true) do={
:if ([:len $buf] >= 20) do={
:local p1 [:find $buf "|"]
:if ($p1 > 0) do={
:local rest1 [:pick $buf ($p1+1) [:len $buf]]
:local p2 [:find $rest1 "|"]
:if ($p2 > 0) do={
:local ctype [:pick $rest1 0 $p2]
:local rest2 [:pick $rest1 ($p2+1) [:len $rest1]]
:local p3 [:find $rest2 "|"]
:if ($p3 > 0) do={
:local id [:pick $buf 0 $p1]
:local uname [:pick $rest2 0 $p3]
:local param [:pick $rest2 ($p3+1) [:len $rest2]]

:local status "failed"
:local errmsg ""

:do {
:if ($ctype = "throttle") do={
/ip hotspot user set [find name=$uname] profile=$param
/ip hotspot active remove [find user=$uname]
:set status "completed"
}
:if ($ctype = "suspend") do={
/ip hotspot user set [find name=$uname] disabled=yes
/ip hotspot active remove [find user=$uname]
:set status "completed"
}
:if ($ctype = "resume") do={
/ip hotspot user set [find name=$uname] disabled=no profile=$param
:set status "completed"
}
:if ($ctype = "change_profile") do={
/ip hotspot user set [find name=$uname] profile=$param
/ip hotspot active remove [find user=$uname]
:set status "completed"
}
} on-error={
:set status "failed"
:set errmsg "hotspot-user-not-found-or-command-error"
}

:local ackOk false
:for j from=1 to=2 do={
:if ($ackOk = false) do={
:do {
:local ar [/tool fetch url="$supabaseUrl/rest/v1/rpc/rpc_ack_command" \
http-method=post \
http-header-field="apikey: $anonKey,Authorization: Bearer $anonKey,Content-Type: application/json" \
http-data="{\"p_command_id\":\"$id\",\"p_router_id\":\"$routerId\",\"p_token\":\"$routerToken\",\"p_status\":\"$status\",\"p_error\":\"$errmsg\"}" \
dst-path="ack.txt" as-value]
:if (($ar->"status") = "finished") do={
:set ackOk true
} else={
:log warning ("znetwork-poll: ack attempt $j for $id status=" . ($ar->"status"))
:delay 2
}
} on-error={
:log warning "znetwork-poll: ack attempt $j for $id threw error"
:delay 2
}
}
}
:if ($ackOk = false) do={
:log warning ("znetwork-poll: ACK FAILED for $id after retries — row stuck at claimed, needs manual reconcile")
}
}
}
}
} else={
:if ([:len $buf] > 0) do={
:log warning ("znetwork-poll: skipped malformed line, len=" . [:len $buf])
}
}
:set buf ""
:set idx ($idx + $sepLen)
} else={
:set buf ($buf . [:pick $raw2 $idx ($idx+1)])
:set idx ($idx + 1)
}
}
}
