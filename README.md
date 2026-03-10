# Agent Sandbox Demo

A local demonstration of the **"Isolate the Agent"** pattern for secure AI agent infrastructure.

Based on the architecture described by [Larsen Cundric at Browser Use](https://x.com/larsencc/article/2027225210412470668).

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                     Docker Network                        │
│                                                           │
│  ┌──────────┐    ┌───────────────┐    ┌────────────────┐  │
│  │  Sandbox  │───▶│ Control Plane │───▶│ Anthropic API  │  │
│  │  (Agent)  │    │   (FastAPI)   │    │(claude-haiku)  │  │
│  └──────────┘    └───────┬───────┘    └────────────────┘  │
│       │                  │                                │
│       │ presigned URLs   │ real credentials               │
│       ▼                  ▼                                │
│                   ┌─────────────┐                         │
│                   │    MinIO    │                         │
│                   │ (S3 storage)│                         │
│                   └─────────────┘                         │
└──────────────────────────────────────────────────────────┘
```

### Key Principle

> "Your agent should have nothing worth stealing and nothing worth preserving."

The sandbox container receives only 3 env vars: `SESSION_TOKEN`, `CONTROL_PLANE_URL`, `SESSION_ID`.
No API keys, no cloud credentials, no database access. Everything goes through the control plane.

## Prerequisites

- Docker & Docker Compose
- An [Anthropic API key](https://console.anthropic.com/)

## Quick Start

```bash
# 1. Set your Anthropic API key
export ANTHROPIC_API_KEY=sk-ant-...

# 2. Start the infrastructure (control plane + MinIO)
docker compose up -d --build

# 3. Launch a sandbox agent with a task
./launch-sandbox.sh "Write a haiku about Docker and save it to haiku.txt"

# 4. The script follows logs automatically. To detach: Ctrl+C

# 5. Inspect the session history via the control plane API
curl -s localhost:8080/sessions | python3 -m json.tool

# 6. Browse uploaded files in MinIO
# Open http://localhost:9001 (login: minioadmin / minioadmin)
```

## Demo Script (for presentations)

### Demo 1: Zero-Secret Sandbox

Show that the sandbox has nothing worth stealing.

```bash
# Launch an agent
./launch-sandbox.sh "Write a haiku about Docker and save it to haiku.txt"

# While it's running (or after), exec into the container
docker exec sandbox-<session-id> env
# → No secrets! SESSION_TOKEN is stripped from env after reading into memory.
# → No ANTHROPIC_API_KEY — only the control plane has that.

# Check what the agent created
docker exec sandbox-<session-id> cat /workspace/haiku.txt
```

### Demo 2: Conversation History Survives Sandbox Death

Show that state lives in the control plane, not the sandbox.

```bash
# Launch an agent, let it complete
./launch-sandbox.sh "Create a Python script that prints fibonacci numbers"

# The container has exited, but the conversation is preserved:
curl -s localhost:8080/sessions/<session-id> | python3 -m json.tool
# → Full conversation history, file list, token usage — all in the control plane
```

### Demo 3: Parallel Sandboxes

Show independent isolation and scaling.

```bash
# Launch 3 agents simultaneously with different tasks
./launch-sandbox.sh "Write a Python function to sort a list" --no-follow &
./launch-sandbox.sh "Write a bash script to check disk usage" --no-follow &
./launch-sandbox.sh "Write a haiku about cloud computing" --no-follow &

# Each gets its own isolated container, own session, own workspace
docker ps --filter "name=sandbox"

# Each session is independent
curl -s localhost:8080/sessions | python3 -m json.tool
```

### Demo 4: File Sync via Presigned URLs

Show how files move without the sandbox having storage credentials.

```bash
# Launch agent that creates files
./launch-sandbox.sh "Create 3 text files with fun facts about Sweden"

# Check MinIO — files synced without sandbox ever having AWS credentials
# Open http://localhost:9001 (MinIO console, minioadmin/minioadmin)
# Navigate to: agent-workspaces → <session-id> → files are there
```

### Demo 5: The Control Plane as the Single Gateway

Show that the control plane mediates everything.

```bash
# Check system health
curl -s localhost:8080/health | python3 -m json.tool

# List all sessions with token usage
curl -s localhost:8080/sessions | python3 -m json.tool

# Get full detail on one session
curl -s localhost:8080/sessions/<session-id> | python3 -m json.tool
```

## Project Structure

```
├── docker-compose.yml          # Infrastructure: control plane + MinIO
├── launch-sandbox.sh           # CLI to spawn sandbox containers
├── CLAUDE.md                   # Project context for AI-assisted development
├── control-plane/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py                 # FastAPI control plane service
│   ├── sessions.py             # Session management (in-memory)
│   ├── llm_proxy.py            # LLM proxying to Anthropic API
│   └── file_storage.py         # Presigned URL generation via MinIO
└── sandbox/
    ├── Dockerfile
    ├── requirements.txt
    ├── entrypoint.sh           # Privilege drop + env stripping
    ├── agent.py                # The agent loop
    ├── gateway.py              # Control plane gateway protocol
    └── file_sync.py            # Workspace file sync via presigned URLs
```

## Useful Commands

```bash
# Rebuild after code changes
docker compose up -d --build

# Rebuild sandbox image after changes to sandbox/
docker build -t sandbox-agent ./sandbox/

# View control plane logs
docker logs -f control-plane

# Clean up stopped sandbox containers
docker container prune -f --filter "label=sandbox"

# Full reset (removes all data)
docker compose down -v
```

## Changing the LLM Model

The default model is `claude-3-5-haiku-20241022`. To use a different Anthropic model:

1. Set the env var on the control plane in `docker-compose.yml`:
   ```yaml
   environment:
     - ANTHROPIC_MODEL=claude-opus-4-5
   ```
2. Restart: `docker compose up -d`
