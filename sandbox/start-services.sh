#!/bin/bash
# Runs as the unprivileged `sandbox` user. Sets up opencode to talk to the
# control plane, starts the file-sync background worker, then runs code-server
# in the foreground (which keeps the container alive for the duration of the
# demo session).
set -e

TOKEN="$1"
CP_URL="$2"
SID="$3"

# Pin opencode to a default model. Provider config (base URL, API key) is set
# via env vars below — opencode reads ANTHROPIC_API_KEY and ANTHROPIC_BASE_URL
# from the environment of whatever shell launches it.
mkdir -p "$HOME/.config/opencode"
cat > "$HOME/.config/opencode/opencode.json" <<EOF
{
  "\$schema": "https://opencode.ai/config.json",
  "model": "anthropic/claude-haiku-4-5-20251001"
}
EOF

# Put the env vars in ~/.bashrc rather than exporting them here.
#
# Why: `docker exec sandbox env` runs `env` directly (no shell, no rc files),
# so a clean environment is what an attacker poking at the container would
# see — that's the "no secrets" demo beat. An interactive shell (code-server's
# terminal, or `docker exec -it sandbox bash`) reads .bashrc and gets the vars,
# so opencode picks them up.
#
# The "API key" here is the session token — a session-scoped credential the
# control plane swaps for the real ANTHROPIC_API_KEY before calling Anthropic.
cat >> "$HOME/.bashrc" <<EOF

# === sandbox demo: opencode → control plane ===
export ANTHROPIC_API_KEY="${TOKEN}"
export ANTHROPIC_BASE_URL="${CP_URL}"
EOF

# Background: continuously sync /workspace to MinIO via presigned URLs.
python /app/file_sync.py \
    --token "$TOKEN" \
    --control-plane "$CP_URL" \
    --session "$SID" &

# Foreground: VS Code in the browser. The container lives as long as this runs.
exec code-server \
    --bind-addr 0.0.0.0:8443 \
    --auth none \
    --disable-telemetry \
    --disable-update-check \
    /workspace
