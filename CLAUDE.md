# CLAUDE.md — Project Context for Claude Code

## What is this project?

A local demo of the **"Isolate the Agent" pattern** for secure AI agent infrastructure, inspired by [Browser Use's architecture post by Larsen Cundric](https://x.com/larsencc/article/2027225210412470668).

A coding agent (opencode running inside a browser-based VS Code) lives in a sandbox container that holds **zero credentials and no public internet egress**. It still does useful work — runs Claude, commits to GitHub, persists files to S3 — because a separate control plane container brokers every outbound call, swapping a session-scoped token for the real credential at the boundary.

This is a **demonstration/teaching tool**, not production software. The owner (Jonathan) is a cybersecurity professional at an IT consultancy who plans to present this architecture to colleagues (software engineers, architects, IT professionals) at AI Fokus. The code should be clear, well-commented, and optimized for "aha moments" during a live presentation. Prefer readable code over edge-case handling.

## Architecture

```
┌──────────────────────────── Docker host ─────────────────────────────────┐
│                                                                          │
│  ┌── agent-network ─────────────────────┐                                │
│  │                                       │                               │
│  │   Sandbox container (per session)     │                               │
│  │     code-server  +  opencode  +  file_sync.py                         │
│  │     env: SESSION_TOKEN, CONTROL_PLANE_URL, SESSION_ID                 │
│  │              (stripped from `env` at startup; left in ~/.bashrc       │
│  │               for opencode's interactive shells)                      │
│  │                            │                                          │
│  │                            ▼ (only allowed egress)                    │
│  │   Control plane (FastAPI :8080)                                       │
│  │     - /v1/messages    → Anthropic Messages API                        │
│  │     - /mcp/github/*   → github-mcp sidecar (streamable HTTP proxy)    │
│  │     - /files/*        → MinIO presigned URLs                          │
│  │     - /sessions/*     → audit + session management                    │
│  │     env: ANTHROPIC_API_KEY, GITHUB_PAT, MinIO creds                   │
│  │                            │                                          │
│  │                            ├──▶ MinIO (S3-compatible)                 │
│  │                            └──▶ api.anthropic.com (via ANTHROPIC_API_KEY)
│  │                                                                       │
│  └───────────────────────────────────────────────────────────────────────┘
│                                                                          │
│  ┌── cp-internal network (sandbox CANNOT reach) ─────────────────────┐   │
│  │                                                                   │   │
│  │   github-mcp container                                            │   │
│  │     github-mcp-server v1.0.3 in HTTP mode at :8090/mcp            │   │
│  │     env: (none — PAT injected per-request by the control plane)   │   │
│  │                            │                                      │   │
│  │                            ▼                                      │   │
│  │                       api.github.com                              │   │
│  └───────────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────┘
```

### Trust boundary

This is the central insight. Each credential lives in exactly one place:

| Credential | Lives in | The sandbox sees |
|------------|---------|------------------|
| `ANTHROPIC_API_KEY` | control-plane env | a session token, swapped at `/v1/messages` |
| `GITHUB_PAT` | control-plane env | a session token, swapped on `/mcp/github/*` forwarding |
| MinIO access/secret | control-plane env | presigned URLs (time- and key-scoped) |
| Session token | sandbox memory (after env strip) | itself; useless outside this session |

Two networks make it physical: the sandbox is on `agent-network` only; the github-mcp sidecar is on `cp-internal` only; the control plane straddles both and is the sole bridge.

## How a session flows

### Launch (`./launch-sandbox.sh`)

1. POST `/sessions` on the control plane → returns `session_id` + `token`.
2. `docker run` a sandbox container, passing the three env vars and mapping `8443:8443` to host.
3. `sandbox/entrypoint.sh` (root) reads the three env vars into shell locals, unsets them from env, then `exec su sandbox -c "/app/start-services.sh ..."`.
4. `start-services.sh` (sandbox user) writes `~/.config/opencode/opencode.json`, templates `/workspace/AGENTS.md` with the configured GitHub repo, appends `ANTHROPIC_API_KEY` and `ANTHROPIC_BASE_URL` to `~/.bashrc` (so interactive shells inherit; `docker exec ... env` stays clean), kicks off `file_sync.py` in the background, then `exec`s `code-server` in the foreground.

### LLM call (opencode → Anthropic)

1. opencode sends a normal Anthropic `POST /v1/messages` to `${ANTHROPIC_BASE_URL}` (the control plane), with `x-api-key: <session-token>`.
2. Control plane validates the session token, logs a one-line audit entry to the session, then forwards the request to `https://api.anthropic.com/v1/messages` with the real `ANTHROPIC_API_KEY` substituted in.
3. The upstream response (streaming SSE or JSON) is relayed back to opencode verbatim.

### GitHub tool call (opencode → GitHub via MCP)

1. opencode sends a Streamable HTTP MCP request to `${CP_URL}/mcp/github/mcp` with `Authorization: Bearer <session-token>`.
2. Control plane's HTTP middleware validates the session token, then `proxy_github_mcp` strips the session-token `Authorization` header and **injects** `Authorization: Bearer <real GITHUB_PAT>` before forwarding to `http://github-mcp:8090/mcp` over the `cp-internal` network.
3. github-mcp-server validates the PAT against GitHub, executes the tool (e.g. `push_files`), returns the result. Streamed back through the proxy.

### File persistence (sandbox → MinIO)

1. `file_sync.py` runs as a background process inside the sandbox, scanning `/workspace` every 3 s for changes.
2. On change, it POSTs the changed paths to `/files/presigned-urls` with the session token.
3. The control plane uses its MinIO credentials to mint short-lived presigned PUT URLs scoped to `{session_id}/{path}`, and returns them.
4. The sandbox PUTs the file contents directly to MinIO. MinIO creds never leave the control plane.

## Project layout

```
├── docker-compose.yml          control plane + minio + github-mcp + two networks
├── launch-sandbox.sh           per-session container spawner
├── README.md                   user-facing docs + demo flow
├── CLAUDE.md                   this file
├── control-plane/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py                 routes + auth middleware + GitHub MCP proxy
│   ├── sessions.py             in-memory session store
│   ├── llm_proxy.py            Anthropic passthrough
│   └── file_storage.py         MinIO presigned URL minting
├── github-mcp/
│   └── Dockerfile              downloads github-mcp-server binary, runs `http` mode
└── sandbox/
    ├── Dockerfile              code-server + opencode + python + git + git wrapper
    ├── entrypoint.sh           env strip + privilege drop
    ├── start-services.sh       opencode config, AGENTS.md, file_sync bg, code-server fg
    ├── git-shim.sh             wrapper that blocks network-touching git subcommands
    ├── requirements.txt
    └── file_sync.py            standalone workspace → MinIO uploader
```

## Things deliberately NOT in the sandbox

These are part of the demo story — be careful not to accidentally add them back:

- **Network-touching `git` operations** — `git` itself is installed and used freely for local work (log/diff/status/branch/checkout/commit/merge/rebase/blame). A wrapper at `/usr/local/bin/git` (earlier on PATH than `/usr/bin/git`) intercepts the subcommands that would talk to github.com — `push`, `pull`, `fetch`, `clone`, `ls-remote` — and points the agent at the github MCP server. The wrapper isn't the security boundary (the network is); it's a clear signpost so the agent doesn't waste turns hitting timeouts. The agent's *remote* git workflow still goes through `push_files` / `create_pull_request` / etc.
- **Internet egress** — the sandbox is on `agent-network` only; nothing on that network can reach anything outside the docker host. Public DNS works (docker's resolver) but no outbound TCP.
- **Real credentials in env** — `ANTHROPIC_API_KEY`, `GITHUB_PAT`, MinIO creds never reach the sandbox. The session token (sent as the "API key" to opencode's view of Anthropic) is session-scoped and useless outside this session.

## Running the project

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export GITHUB_PAT=github_pat_...
export GITHUB_REPO=owner/name           # the agent's commit target
docker compose up -d --build
./launch-sandbox.sh "session label"     # prints URL + session id
```

Verify health:

```bash
curl -s localhost:8080/health | python3 -m json.tool   # control plane + Anthropic
docker logs github-mcp                                  # should show "HTTP server listening"
docker logs control-plane                               # should show "GitHub MCP proxy → ..."
```

## Notable design choices

- **opencode, not Claude Code.** Picked because it has clean config for both custom Anthropic base URLs and remote MCP servers via `~/.config/opencode/opencode.json`. Verified in a Phase 0 spike: it honors `ANTHROPIC_API_KEY` + `ANTHROPIC_BASE_URL` env vars and supports `"type": "remote"` MCP servers with custom `Authorization` headers.
- **`x-api-key` auth on `/v1/messages`, `Authorization: Bearer` everywhere else.** opencode uses the Anthropic SDK natively which sends `x-api-key`; file_sync and the MCP path use Bearer. Two small dependency variants in `main.py`.
- **GitHub MCP via github-mcp-server in HTTP mode**, not a hand-rolled custom MCP. The official server has ~30 tools (issues, PRs, branches, search, push_files, …), runs as a sidecar in `http` mode, and is reachable only by the control plane. We had a brief detour through `mcp-proxy` thinking the server was stdio-only — turned out HTTP mode shipped in v1.0.3.
- **PAT injection in the proxy, not at the sidecar.** github-mcp-server's HTTP mode is multi-tenant; each request must carry an `Authorization: Bearer <PAT>`. The control plane strips the inbound session token and injects the real PAT before forwarding. The sidecar's container has no GitHub PAT in its env — only the control plane does.
- **No persistent session store.** Sessions are in-memory in the control plane (`sessions.py`). A restart wipes them. Fine for the demo; not for production.
- **`docker exec env` is clean, interactive shells aren't.** Credentials needed by opencode are written to `~/.bashrc` so they're set when an interactive shell starts but not in the bare process environment. This makes the "no secrets" demo beat work without sacrificing functionality.

## Tech stack

- **Control plane:** Python 3.12, FastAPI, uvicorn, httpx, boto3, official `anthropic` SDK
- **Sandbox:** code-server + Node.js + opencode (`opencode-ai` from npm), Python 3.12 + httpx for `file_sync`
- **github-mcp sidecar:** debian-slim + the `github-mcp-server` v1.0.3 binary in HTTP mode
- **Storage:** MinIO (S3-compatible), accessed by the sandbox via presigned URLs
- **LLM:** Anthropic Messages API, default model `claude-haiku-4-5-20251001` (configurable via `ANTHROPIC_MODEL` on the control plane)
- **Orchestration:** Docker Compose for the infrastructure stack, bash for spawning sandboxes

## Demo storytelling beats

The four moves are detailed in [README.md](./README.md#demo-flow-for-live-presentations). High level:

1. *"This looks like a normal dev environment"* — open code-server, run opencode.
2. *"Give it a real task"* — agent writes code + commits to GitHub.
3. *"Look at what the agent can't see"* — `docker exec env` (clean), `curl api.github.com` (blocked), `docker exec github-mcp env` (no PAT there either).
4. *"Everything went through the control plane"* — show the session audit, MinIO console.

## Future ideas (not yet scoped)

- **Web dashboard** for live sessions, conversation history, file activity, tool calls.
- **Persistent session store** (Redis) so sessions survive control-plane restarts.
- **Multiple LLM backends** routed by the control plane — drives home "the sandbox doesn't know which LLM it talks to."
- **Prompt-injection demo** — deliberately show an attacker-controlled task trying to exfiltrate creds, and how the architecture stops it (great for a security-focused audience).
- **Starter project seeded into `/workspace`** at session launch, for richer opening demos.
