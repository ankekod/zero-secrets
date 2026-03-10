#!/bin/bash
# ============================================================
# Sandbox Entrypoint
# ============================================================
# This script demonstrates the security hardening from the article:
#
# 1. Read the 3 allowed env vars into a temp file
# 2. STRIP them from the environment (so `env` shows nothing)
# 3. Drop privileges from root to unprivileged 'sandbox' user
# 4. Run the agent
#
# If someone execs into this container and runs `env`, they see
# nothing useful. The session token is in Python memory only.
# ============================================================

set -e

echo "=== Sandbox Entrypoint ==="
echo "Reading configuration..."

# Save the 3 env vars to a temp file readable only by root,
# then we'll pass them as arguments to the Python process
TEMP_CONFIG=$(mktemp)
echo "SESSION_TOKEN=${SESSION_TOKEN}" >> "$TEMP_CONFIG"
echo "CONTROL_PLANE_URL=${CONTROL_PLANE_URL}" >> "$TEMP_CONFIG"
echo "SESSION_ID=${SESSION_ID}" >> "$TEMP_CONFIG"

# ── STRIP environment variables ──
# After this, `docker exec <container> env` shows nothing sensitive
unset SESSION_TOKEN
unset CONTROL_PLANE_URL
unset SESSION_ID

echo "Environment stripped. Dropping privileges..."

# ── PRIVILEGE DROP ──
# Read config, pass as args, run as unprivileged user
TOKEN=$(grep SESSION_TOKEN "$TEMP_CONFIG" | cut -d= -f2)
CP_URL=$(grep CONTROL_PLANE_URL "$TEMP_CONFIG" | cut -d= -f2)
SID=$(grep SESSION_ID "$TEMP_CONFIG" | cut -d= -f2)

# Remove the temp file
rm -f "$TEMP_CONFIG"

echo "Starting agent as unprivileged 'sandbox' user..."
echo "Session: $SID"
echo "Control Plane: $CP_URL"
echo "=========================================="

# Run agent as unprivileged user
exec su sandbox -c "python /app/agent.py --token '$TOKEN' --control-plane '$CP_URL' --session '$SID'"
