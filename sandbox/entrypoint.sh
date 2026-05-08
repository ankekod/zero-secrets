#!/bin/bash
# Runs as root. Reads the session credentials from the environment into
# memory, strips them, then drops privileges and hands off to start-services.sh.
# After this script execs, the sandbox process tree contains no env vars
# referencing the session token — `docker exec sandbox env` is empty of secrets.
set -e

TEMP=$(mktemp)
echo "SESSION_TOKEN=${SESSION_TOKEN}" >> "$TEMP"
echo "CONTROL_PLANE_URL=${CONTROL_PLANE_URL}" >> "$TEMP"
echo "SESSION_ID=${SESSION_ID}" >> "$TEMP"

unset SESSION_TOKEN CONTROL_PLANE_URL SESSION_ID

TOKEN=$(grep '^SESSION_TOKEN=' "$TEMP" | cut -d= -f2-)
CP_URL=$(grep '^CONTROL_PLANE_URL=' "$TEMP" | cut -d= -f2-)
SID=$(grep '^SESSION_ID=' "$TEMP" | cut -d= -f2-)
rm -f "$TEMP"

echo "[entrypoint] session=$SID control-plane=$CP_URL"

# Credentials are passed as positional args (not env vars) so they don't
# survive into the child process's environment.
exec su sandbox -c "/app/start-services.sh '$TOKEN' '$CP_URL' '$SID'"
