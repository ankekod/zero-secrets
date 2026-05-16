# Agent Sandbox Demo

A local demonstration of the **"Isolate the Agent"** pattern for AI agent infrastructure: a coding agent in VS Code runs inside a sandbox container with no credentials, and reaches the outside world *only* through a control plane that holds every secret.

Inspired by Browser Use's [architecture write-up by Larsen Cundric](https://x.com/larsencc/article/2027225210412470668).

## The core idea

> "Your agent should have nothing worth stealing and nothing worth preserving."

The sandbox container is given exactly three env vars at launch: `SESSION_TOKEN`, `CONTROL_PLANE_URL`, `SESSION_ID`. They're read into memory, stripped from `env`, then the process drops to a non-root user. From that moment on:

- It has **no** Anthropic API key — yet the agent runs Claude.
- It has **no** GitHub PAT — yet the agent commits to GitHub.
- It has **no** S3 credentials — yet the agent persists files to object storage.
- It has **no** network egress to the public internet — yet everything above works.

Every external call is brokered by a separate control plane container that swaps the sandbox's session token for the real credential at the boundary.

## Architecture

```
┌──────────────────────────── Docker host ─────────────────────────────────┐
│                                                                          │
│  ┌───── agent-network ───────────────────┐                               │
│  │                                       │                               │
│  │   Sandbox container (per session)     │                               │
│  │   ┌─────────────────────────────────┐ │                               │
│  │   │  code-server (browser VS Code)  │ │◀──── presenter's browser     │
│  │   │   └─ terminal → opencode        │ │      http://localhost:8443   │
│  │   │  file_sync.py (background)      │ │                               │
│  │   └────────────┬────────────────────┘ │                               │
│  │                │                       │                               │
│  │                ▼ (only allowed egress) │                               │
│  │   ┌──── Control plane (FastAPI) ────┐ │                               │
│  │   │  /v1/messages   Anthropic proxy │ │── ANTHROPIC_API_KEY ──▶ Anthropic
│  │   │  /mcp/github/*  GitHub MCP proxy│ │            │                  │
│  │   │  /files/*       presigned URLs  │ │            │                  │
│  │   │  /sessions/*    audit + auth    │ │            │                  │
│  │   └────────┬─────────────────┬──────┘ │            │                  │
│  │            │                 │        │            │                  │
│  │            ▼                 │        │            │                  │
│  │   ┌─── MinIO ─────┐          │        │            │                  │
│  │   │ S3-compatible │          │        │            │                  │
│  │   └───────────────┘          │        │            │                  │
│  └──────────────────────────────┼────────┘            │                  │
│                                 │                     │                  │
│  ┌──── cp-internal network ─────┼─────────────┐       │                  │
│  │   (sandbox CANNOT reach)     ▼             │       │                  │
│  │                       ┌──── github-mcp ───┐│       │                  │
│  │                       │ github-mcp-server ││── GITHUB_PAT ──▶ api.github.com
│  │                       │ in HTTP mode      ││                          │
│  │                       └───────────────────┘│                          │
│  └────────────────────────────────────────────┘                          │
└──────────────────────────────────────────────────────────────────────────┘
```

Two network segments enforce the trust boundary: the **sandbox can route only to the control plane and MinIO**, never to GitHub's MCP sidecar or the public internet. The control plane is the sole bridge.

## Prerequisites

- Docker / Docker Desktop (or Podman)
- [An Anthropic API key](https://console.anthropic.com/)
- A [fine-grained GitHub PAT](https://github.com/settings/personal-access-tokens) scoped to a single demo repo with **Contents: read & write** + **Metadata: read** permissions. (Make sure the repo's `main` branch doesn't have protection rules that block direct pushes — or point `GITHUB_BRANCH` at an unprotected branch.)

## Quick start

```bash
# 1. Credentials (only the control plane will see these)
export ANTHROPIC_API_KEY=sk-ant-...
export GITHUB_PAT=github_pat_...
export GITHUB_REPO=your-org/your-demo-repo

# 2. Bring up control plane + MinIO + GitHub MCP sidecar
docker compose up -d --build

# 3. Launch a sandbox (builds the sandbox image on first run; this is slow
#    because code-server + opencode is ~1 GB. Subsequent launches are quick.)
./launch-sandbox.sh "demo session"

# 4. Open VS Code in your browser (URL is printed by launch-sandbox.sh)
#    Then in the integrated terminal:
opencode
> commit a hello.py to the repo that prints "hi"
```

You should see a commit land on the configured GitHub repo within seconds. The agent never saw your PAT.

## Demo flow (for live presentations)

### 1. "This looks like a normal dev environment" — *1 min*

Switch to the browser tab showing code-server. Open the integrated terminal. Run `opencode`. Visually it's just VS Code; the audience won't think anything special is going on yet.

### 2. "Now let's give it a real task" — *3 min*

Ask the agent to write something small and commit it: *"add a function that reverses a string in `util.py` and commit it"*. Watch the agent reason, write code, and call the MCP tool. When it reports done, refresh the GitHub tab — the commit is there.

### 3. "Now let's look at what the agent can't see" — *2 min*

Open a second terminal on the host:

```bash
docker exec sandbox-<id> env
```

No `ANTHROPIC_API_KEY`. No `GITHUB_PAT`. No AWS keys. Just the session token, control plane URL, and session id — and even those were stripped from the running process's env after startup (interactive shells get them back via `.bashrc` because opencode needs them; `env` directly shows nothing).

```bash
docker exec sandbox-<id> curl -m 3 https://api.anthropic.com
docker exec sandbox-<id> curl -m 3 https://api.github.com
```

Both fail — the sandbox network has no public internet egress.

```bash
docker exec github-mcp env | grep -i github
```

Also empty: the PAT isn't on the github-mcp sidecar either. It lives *only* in the control plane container, injected on each forwarded MCP request.

### 4. "Everything went through the control plane" — *2 min*

```bash
curl -s localhost:8080/sessions/<session-id> | python3 -m json.tool
```

The full session audit: every LLM call (model, message count, snippet of the last user prompt), token usage, and the files persisted to MinIO. Open `http://localhost:9001` (minioadmin/minioadmin) — there's the agent's workspace mirrored to object storage, again via presigned URLs the sandbox got from the control plane.

## Project structure

```
.
├── docker-compose.yml          # Control plane + MinIO + github-mcp sidecar, two networks
├── launch-sandbox.sh           # Spawns a sandbox container for a session
├── README.md                   # This file
├── CLAUDE.md                   # Project context for AI-assisted development
│
├── control-plane/              # FastAPI gateway — holds every credential
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py                 # Endpoints: /v1/messages, /mcp/github/*,
│   │                           #   /files/*, /sessions/*, /health
│   ├── sessions.py             # In-memory session + token store
│   ├── llm_proxy.py            # Anthropic API passthrough
│   └── file_storage.py         # MinIO presigned URL minter
│
├── github-mcp/                 # Sidecar: GitHub's official MCP server
│   └── Dockerfile              # github-mcp-server v1.0.3 in HTTP mode
│
└── sandbox/                    # Image launched per session
    ├── Dockerfile              # code-server + opencode + python + git shim
    ├── entrypoint.sh           # Reads session env, strips, drops privileges
    ├── start-services.sh       # Templates opencode config + AGENTS.md;
    │                           #   starts file_sync, runs code-server
    ├── requirements.txt
    └── file_sync.py            # Background workspace → MinIO uploader
```

## Useful commands

```bash
# Rebuild after code changes
docker compose up -d --build

# Tail control plane logs (LLM calls, MCP proxy traffic, auth events)
docker logs -f control-plane

# Shell into a running sandbox as the unprivileged user
docker exec -it --user sandbox sandbox-<session-id> bash

# Stop a sandbox
docker stop sandbox-<session-id>

# List sandbox containers
docker ps --filter "name=sandbox-"

# Full reset (drops MinIO data, deletes all session state)
docker compose down -v
```

## Configuration

| Env var | Where it's set | Purpose |
|---------|---------------|---------|
| `ANTHROPIC_API_KEY` | host → control-plane only | Real Anthropic credential |
| `ANTHROPIC_MODEL` | optional, control-plane | Overrides the default `claude-haiku-4-5-20251001` |
| `GITHUB_PAT` | host → control-plane only | Real GitHub PAT |
| `GITHUB_REPO` | host → sandbox env (not secret) | `owner/name`; templated into AGENTS.md so the agent knows where to commit |
| `GITHUB_BRANCH` | host → sandbox env (not secret) | Defaults to `main` |
| `LOG_LEVEL` | optional, control-plane | `INFO` (default) or `DEBUG` for wire-trace logging |

## Acknowledgements

- The "isolate the agent" pattern is from [Larsen Cundric's Browser Use post](https://x.com/larsencc/article/2027225210412470668).
- [opencode](https://opencode.ai) is the coding agent running inside the sandbox.
- [github/github-mcp-server](https://github.com/github/github-mcp-server) provides the GitHub MCP tools.
- [code-server](https://github.com/coder/code-server) provides the in-browser VS Code.
