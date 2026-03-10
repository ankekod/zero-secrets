# CLAUDE.md — Project Context for Claude Code

## What is this project?

A local demo of the **"Isolate the Agent" pattern** for secure AI agent infrastructure, inspired by [Browser Use's architecture post by Larsen Cundric](https://x.com/larsencc/article/2027225210412470668).

The core idea: when an AI agent can execute arbitrary code, it should run in a sandbox with **nothing worth stealing and nothing worth preserving**. The sandbox talks to the outside world exclusively through a control plane that holds all credentials.

This is a **demonstration/teaching tool**, not production software. The owner (Jonathan) is a cybersecurity professional at an IT consultancy who plans to present this architecture to colleagues (software engineers, architects, IT professionals). The demo should be clear, well-commented, and optimized for "aha moments" during a live presentation.

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                     Docker Network                        │
│                                                           │
│  ┌──────────┐    ┌───────────────┐    ┌───────────────┐  │
│  │  Sandbox  │───▶│ Control Plane │───▶│    Ollama     │  │
│  │  (Agent)  │    │   (FastAPI)   │    │  (qwen3:4b)│  │
│  └──────────┘    └───────┬───────┘    └───────────────┘  │
│       │                  │                                │
│       │ presigned URLs   │ real credentials               │
│       ▼                  ▼                                │
│                   ┌─────────────┐                         │
│                   │    MinIO    │                         │
│                   │ (S3 storage)│                         │
│                   └─────────────┘                         │
└──────────────────────────────────────────────────────────┘
```

**Pattern 2 from the article**: The entire agent runs in a sandbox with zero secrets. It talks to the outside world through a control plane that holds all credentials.

## Project structure

```
├── CLAUDE.md                    # This file
├── README.md                    # User-facing docs and demo script
├── docker-compose.yml           # Infrastructure: control plane, Ollama, MinIO
├── launch-sandbox.sh            # CLI to spawn sandbox containers
├── control-plane/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py                  # FastAPI app — session mgmt, auth, routing
│   ├── sessions.py              # In-memory session store (token → session → history)
│   ├── llm_proxy.py             # Proxies LLM calls to Ollama, manages history reconstruction
│   └── file_storage.py          # Presigned URL generation via MinIO/boto3
└── sandbox/
    ├── Dockerfile
    ├── requirements.txt          # Minimal — only httpx
    ├── entrypoint.sh             # Reads env vars, strips them, drops privileges
    ├── agent.py                  # Agent loop: call LLM → parse actions → execute → sync files
    ├── gateway.py                # ControlPlaneGateway — HTTP client to control plane
    └── file_sync.py              # Detects workspace changes, uploads via presigned URLs
```

## How it works

### Session lifecycle

1. `launch-sandbox.sh` calls `POST /sessions` on the control plane → gets `session_id` + `token`
2. Launch script seeds the task into conversation history via `POST /messages/persist`
3. A Docker container is spawned with only 3 env vars: `SESSION_TOKEN`, `CONTROL_PLANE_URL`, `SESSION_ID`
4. `entrypoint.sh` reads those env vars into memory, then **strips them from the environment** and **drops root privileges** to the `sandbox` user
5. `agent.py` runs the agent loop, calling the control plane for everything

### Agent loop (sandbox/agent.py)

1. Send new messages to `POST /llm/chat` (control plane reconstructs full history + calls Ollama)
2. Parse JSON action blocks from the LLM response (```json { "action": "create_file", ... } ```)
3. Execute actions locally (write files to /workspace)
4. Sync changed files to MinIO via presigned URLs (sandbox never sees storage credentials)
5. Repeat until `{"action": "done"}` or max iterations (10)

### Control plane endpoints

- `POST /sessions` — create session (returns token)
- `GET /sessions` — list all sessions
- `GET /sessions/{id}` — get session details + conversation history
- `POST /sessions/{id}/deactivate` — deactivate session
- `POST /llm/chat` — proxy LLM call (requires Bearer token)
- `POST /messages/persist` — store messages without LLM call
- `POST /files/presigned-urls` — get presigned S3 upload/download URLs
- `GET /files` — list session files
- `GET /health` — health check (control plane + Ollama status)

## Running the project

```bash
# Start infrastructure
docker compose up -d --build

# Wait for ollama-init to pull the model (first time only, check with):
docker logs -f ollama-init

# Launch an agent
./launch-sandbox.sh "Write a haiku about Docker and save it to haiku.txt"

# Check session history
curl -s localhost:8080/sessions | python3 -m json.tool

# Browse files in MinIO console
open http://localhost:9001   # login: minioadmin / minioadmin
```

## Known bugs and issues

### 1. launch-sandbox.sh argument parsing bug
**Status**: Open
**Problem**: The `--interactive` flag (and any flags mixed with positional args) gets mishandled. Running `./launch-sandbox.sh "my task" --interactive` sets the task to `--interactive` instead of `my task`.
**Root cause**: The `while` loop's `*)` catch-all overwrites `$TASK` with any unrecognized argument, and the flag/positional parsing order isn't enforced.
**Fix**: Either parse all flags first before collecting the positional task, or use a proper argument parser pattern.

### 2. No interactive mode
**Status**: Not implemented (referenced in README but doesn't exist)
**Problem**: The `--interactive` flag in the README demos doesn't do anything. The agent runs autonomously and exits.
**Desired behavior**: An interactive mode where the user can send follow-up messages to the agent mid-conversation. The sandbox would need to wait for user input between iterations. This would also nicely demonstrate the **conversation history resumption** feature — kill the sandbox, resume with the same session, and the conversation continues.
**Implementation notes**: This requires the sandbox to either poll the control plane for new user messages, or have a mechanism (stdin, websocket, or a simple polling loop against a new endpoint) to receive input. A simple approach: add a `POST /sessions/{id}/messages` endpoint on the control plane that the launch script can post to, and have the agent poll `GET /sessions/{id}/pending-messages` between iterations.

### 3. `--resume` is a stub
**Status**: Not implemented
**Problem**: `./launch-sandbox.sh --resume <session-id>` exits with a note about needing token storage. In a real system, the token would be retrieved from a database. For the demo, the simplest fix is to store token → session_id mappings in a local file, or add a `/sessions/{id}/token` endpoint to the control plane (acknowledging this wouldn't be secure in production).

### 4. Small LLM quirks with action formatting
**Status**: Expected behavior with qwen3:4b
**Problem**: The 3B model sometimes emits duplicate actions (e.g., creating an empty file then immediately overwriting it with content), hallucinated actions (like `create_dir` or `edit_file` which aren't implemented), or fails to emit `done` actions and loops until max iterations.
**Mitigations**: Improve the system prompt in `control-plane/llm_proxy.py`, add validation in `agent.py` to skip unknown actions gracefully, or use a larger model.

### 5. `done` action sometimes embedded without ```json fencing
**Status**: Open
**Problem**: The LLM sometimes emits `{"action": "done", "summary": "..."}` as inline text without wrapping in a ```json block, so `parse_actions()` doesn't catch it.
**Fix**: Add a fallback regex in `parse_actions()` that also looks for bare JSON objects containing `"action"`.

## Design principles for development

1. **Demo clarity over production robustness** — prefer readable code with good comments over edge-case handling. Every file should teach something.
2. **The sandbox should have nothing** — any change that puts credentials, API keys, or direct service access into the sandbox container violates the core architectural principle.
3. **Same image everywhere** — the sandbox Docker image should work identically whether run via `docker compose` or `launch-sandbox.sh`. Don't bake in environment-specific config.
4. **Conversation history lives in the control plane** — the sandbox is stateless. This is the key insight to demonstrate: kill it, spin up a new one, conversation continues.

## Tech stack

- **Control plane**: Python 3.12, FastAPI, uvicorn, httpx, boto3
- **Sandbox**: Python 3.12, httpx (intentionally minimal)
- **LLM**: Ollama with qwen3:4b (configurable via `OLLAMA_MODEL` env var on control plane)
- **Storage**: MinIO (S3-compatible), accessed via presigned URLs
- **Orchestration**: Docker Compose for infrastructure, bash script for sandbox spawning

## Future ideas (not yet scoped)

- **Interactive mode**: Let users send messages to a running agent (the most valuable next step for demos)
- **Web dashboard**: Simple React/HTML page showing live sessions, conversation history, file activity
- **Session resume**: Properly implement `--resume` with token persistence
- **Multiple LLM backends**: Show the control plane routing to different providers (Ollama, OpenAI, Anthropic) — reinforces that the sandbox doesn't know or care which LLM it talks to
- **Prompt injection demo**: Deliberately show how a malicious task could try to extract secrets, and how the architecture prevents it (great for the security-focused audience)
