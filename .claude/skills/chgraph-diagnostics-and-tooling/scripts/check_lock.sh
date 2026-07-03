#!/bin/sh
# check_lock.sh — is a chdb data directory owned by a live process? By whom?
#
# chdb (embedded ClickHouse) takes an EXCLUSIVE lock on its data directory:
# a second process opening the same dir fails hard, even read-only
# (VERIFIED chdb 26.5.0, 2026-07-03). Lifecycle, VERIFIED on 26.5.0:
#   - session open  -> creates <dir>/status containing "PID: <pid> / Started at / Revision"
#   - clean close   -> status file is REMOVED
#   - crash/kill -9 -> status file REMAINS (stale) but the OS lock is released;
#                      a new session opens fine over the stale file
# So: liveness = a process holding the status file open (lsof), not mere presence.
#
# Usage:   scripts/check_lock.sh <data-dir>
# Output:  ends with exactly one "VERDICT:" line.
# Exit:    0 = UNLOCKED or STALE-LOCK (safe to open)
#          1 = LOCKED (a live process owns it)
#          2 = NOT-A-CHDB-DIR / bad args
#
# Requires: POSIX sh + lsof (present on macOS and most Linux distros).

set -u

if [ $# -ne 1 ]; then
    echo "usage: $0 <data-dir>" >&2
    echo "VERDICT: NOT-A-CHDB-DIR (no path given)"
    exit 2
fi

DIR=$1

if [ ! -d "$DIR" ]; then
    echo "VERDICT: NOT-A-CHDB-DIR ($DIR does not exist or is not a directory)"
    exit 2
fi

STATUS_FILE="$DIR/status"

if [ ! -e "$STATUS_FILE" ]; then
    # No status file: either never-opened / cleanly closed chdb dir, or not
    # a chdb dir at all. A used chdb data dir has metadata/ and store/.
    if [ -d "$DIR/metadata" ] || [ -d "$DIR/store" ]; then
        echo "VERDICT: UNLOCKED ($DIR is a chdb data dir with no owner; safe to open)"
        exit 0
    fi
    echo "VERDICT: NOT-A-CHDB-DIR ($DIR has no status file and no chdb layout (metadata/, store/))"
    exit 2
fi

echo "status file: $STATUS_FILE"
echo "status file contents:"
sed 's/^/  | /' "$STATUS_FILE"

# Only a LIVE process holding the file open means the dir is owned.
HOLDERS=$(lsof -F pcn -- "$STATUS_FILE" 2>/dev/null)

if [ -z "$HOLDERS" ]; then
    STALE_PID=$(awk -F': ' '/^PID:/ { print $2 }' "$STATUS_FILE")
    echo "VERDICT: STALE-LOCK (status file left by dead pid ${STALE_PID:-unknown}; owner crashed or was killed; safe to open — chdb reclaims it)"
    exit 0
fi

# lsof -F emits p<pid> / c<command> line pairs; format them.
echo "$HOLDERS" | awk '
    /^p/ { pid = substr($0, 2) }
    /^c/ { cmd = substr($0, 2); printf "holder: pid=%s command=%s\n", pid, cmd }
'
FIRST_PID=$(echo "$HOLDERS" | awk '/^p/ { print substr($0, 2); exit }')
FIRST_CMD=$(echo "$HOLDERS" | awk '/^c/ { print substr($0, 2); exit }')
echo "VERDICT: LOCKED by pid $FIRST_PID ($FIRST_CMD) — opening this dir from another process will fail with CANNOT_OPEN_FILE"
exit 1
