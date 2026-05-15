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
  "model": "anthropic/claude-haiku-4-5-20251001",
  "mcp": {
    "github": {
      "type": "remote",
      "url": "${CP_URL}/mcp/github/mcp",
      "headers": {
        "Authorization": "Bearer ${TOKEN}"
      },
      "enabled": true
    }
  }
}
EOF

# Project-level instructions for opencode (read from AGENTS.md in the working
# directory). This is the strongest nudge for the model to reach for the MCP
# tools rather than shell git/curl/etc.
#
# We template GITHUB_REPO and GITHUB_BRANCH into the file so the agent knows
# exactly which repo to act on without having to discover it.
GITHUB_REPO_INFO="(no repo configured — set GITHUB_REPO when launching the sandbox)"
if [ -n "${GITHUB_REPO:-}" ]; then
    GITHUB_REPO_INFO="**${GITHUB_REPO}** on branch **${GITHUB_BRANCH:-main}**"
fi

cat > /workspace/AGENTS.md <<AGENTS
# Sandbox environment

You are running inside an isolated sandbox container. The environment is
deliberately minimal — get work done using the tools and MCP servers
provided, not by reaching for the usual shell commands.

## What is NOT available

- \`git\`, \`gh\`, or any other VCS client — there is no local git binary
- direct internet access (curl/wget/pip-install will fail to reach anything
  other than the control plane)
- API keys, cloud credentials, or database connection strings

## Committing to GitHub — use the \`github\` MCP server

The project repo is ${GITHUB_REPO_INFO}.

The \`github\` MCP server (GitHub's official one, proxied through the control
plane) is your interface to it. The PAT lives only in the control plane;
you don't need it.

For making commits:

- **\`push_files\`** — commit one or more files in a single commit and push.
  This is what you usually want.
  \`\`\`
  push_files(
      owner="<repo owner>",
      repo="<repo name>",
      branch="${GITHUB_BRANCH:-main}",
      message="...",
      files=[{"path": "src/hello.py", "content": "<full file content>"}],
  )
  \`\`\`
  Pass the FULL file content as a string — do not pass a filesystem path.

- **\`create_or_update_file\`** — same idea but for a single file.

For everything else (read repo state, inspect history, manage issues/PRs,
search code, etc.), the \`github\` MCP server has a comprehensive tool set —
list its tools to see what's available before reaching for shell.

## Files

You may freely read and write files in \`/workspace\`. They are mirrored to
object storage automatically — you do not need to "save", "upload", or
"sync" anything by hand.
AGENTS

# NOTE: the `git` shim that nudges the model toward git_commit lives at
# /usr/local/bin/git — installed system-wide in the Dockerfile so that
# non-interactive bash subprocesses (opencode's bash tool) find it on PATH.

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
