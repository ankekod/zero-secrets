#!/bin/bash
# Reads session credentials from environment into memory, strips them from
# the environment, drops root privileges, then runs the agent.

set -e

# Write env vars to a temp file, then unset them so the environment is clean
TEMP_CONFIG=$(mktemp)
echo "SESSION_TOKEN=${SESSION_TOKEN}" >> "$TEMP_CONFIG"
echo "CONTROL_PLANE_URL=${CONTROL_PLANE_URL}" >> "$TEMP_CONFIG"
echo "SESSION_ID=${SESSION_ID}" >> "$TEMP_CONFIG"

unset SESSION_TOKEN
unset CONTROL_PLANE_URL
unset SESSION_ID

TOKEN=$(grep SESSION_TOKEN "$TEMP_CONFIG" | cut -d= -f2)
CP_URL=$(grep CONTROL_PLANE_URL "$TEMP_CONFIG" | cut -d= -f2)
SID=$(grep SESSION_ID "$TEMP_CONFIG" | cut -d= -f2)

rm -f "$TEMP_CONFIG"

echo "Starting sandbox agent (session: $SID, control-plane: $CP_URL)"

exec su sandbox -c "python /app/agent.py --token '$TOKEN' --control-plane '$CP_URL' --session '$SID'"
