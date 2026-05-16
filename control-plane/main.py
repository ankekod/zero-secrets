"""
Control plane — credential holder and API gateway for sandbox agents.

The sandbox authenticates with a session token. From inside the sandbox,
this service looks like:
  - api.anthropic.com  (via /v1/messages — opencode talks to this)
  - an S3 URL minter   (via /files/presigned-urls — file_sync uses this)
  - (Phase 3) MCP servers for filesystem + git tools

Session state is in-memory. Production would back it with a real store.
"""

import asyncio
import json
import logging
import os
import re
import shutil
import tempfile

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from sessions import Session, SessionStore
from llm_proxy import (
    DEFAULT_MODEL,
    check_llm_health,
    filter_response_headers,
    open_upstream,
)
from file_storage import (
    generate_download_url,
    generate_upload_url,
    list_session_files,
)

GITHUB_MCP_UPSTREAM = os.getenv("GITHUB_MCP_UPSTREAM", "").rstrip("/")
GITHUB_PAT = os.getenv("GITHUB_PAT", "")

_log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, _log_level, logging.INFO),
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("control-plane")

app = FastAPI(title="Agent Control Plane", version="0.3.0")
store = SessionStore()


@app.middleware("http")
async def mcp_auth(request: Request, call_next):
    """
    All /mcp/* routes (mounted MCP servers) must carry a valid session token
    as `Authorization: Bearer <token>`. We validate here once, before routing
    into the mounted Starlette apps, since FastAPI's Depends() doesn't reach
    inside ASGI mounts.
    """
    if request.url.path.startswith("/mcp/"):
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse({"error": "missing bearer token"}, status_code=401)
        session = store.get_by_token(auth[len("Bearer "):])
        if session is None or not session.active:
            return JSONResponse({"error": "invalid or inactive session"}, status_code=401)
        # Tag the request so tools/handlers can see who called.
        request.scope["session_id"] = session.session_id
    return await call_next(request)


@app.on_event("startup")
async def startup_check():
    logger.info("Control plane starting up...")
    health = await check_llm_health()
    if health["status"] == "healthy":
        logger.info(f"Anthropic API ready. Default model: {health['default_model']}")
    else:
        logger.warning(f"LLM not configured: {health.get('error')}")
    if GITHUB_MCP_UPSTREAM:
        logger.info("GitHub MCP proxy → %s (mounted at /mcp/github)", GITHUB_MCP_UPSTREAM)
    else:
        logger.warning("GitHub MCP proxy NOT mounted (GITHUB_MCP_UPSTREAM unset)")


# ─── GitHub MCP proxy ───────────────────────────────────────────
# Transparent HTTP proxy to the github-mcp sidecar, which runs GitHub's
# official github-mcp-server in HTTP/Streamable-HTTP mode.
#
# The sandbox connects to /mcp/github/mcp; we strip the session token
# (already validated by the mcp_auth middleware), forward to the sidecar,
# and stream the response back. The GITHUB_PAT never reaches the sandbox.

# host is per-hop. authorization is our session token, not the GitHub PAT.
# content-length will be re-set by httpx based on the body we hand it.
_DROP_REQUEST_HEADERS = {"host", "authorization", "content-length"}

# Headers we drop from the upstream response — letting these through
# breaks Starlette's framing of the streamed response.
_DROP_RESPONSE_HEADERS = {"content-length", "transfer-encoding", "content-encoding", "connection"}


@app.api_route("/mcp/github/{path:path}", methods=["GET", "POST", "DELETE"])
async def proxy_github_mcp(request: Request, path: str):
    if not GITHUB_MCP_UPSTREAM:
        raise HTTPException(503, "GitHub MCP proxy not configured")

    upstream_url = f"{GITHUB_MCP_UPSTREAM}/{path}"
    if request.url.query:
        upstream_url += f"?{request.url.query}"

    body = await request.body()
    headers = {
        k: v for k, v in request.headers.items() if k.lower() not in _DROP_REQUEST_HEADERS
    }
    # Inject the real GitHub PAT. github-mcp-server's HTTP mode is multi-
    # tenant and requires an Authorization header on every request; we
    # strip the sandbox's session token (above) and substitute the PAT
    # held in the control-plane env. The sandbox never sees the PAT.
    if GITHUB_PAT:
        headers["authorization"] = f"Bearer {GITHUB_PAT}"

    logger.debug("[mcp/github] %s %s → %s", request.method, request.url.path, upstream_url)

    client = httpx.AsyncClient(timeout=None)
    upstream_req = client.build_request(
        request.method,
        upstream_url,
        headers=headers,
        content=body if body else None,
    )
    upstream_resp = await client.send(upstream_req, stream=True)

    async def relay():
        try:
            async for chunk in upstream_resp.aiter_bytes():
                yield chunk
        finally:
            await upstream_resp.aclose()
            await client.aclose()

    return StreamingResponse(
        relay(),
        status_code=upstream_resp.status_code,
        headers={
            k: v for k, v in upstream_resp.headers.items()
            if k.lower() not in _DROP_RESPONSE_HEADERS
        },
        media_type=upstream_resp.headers.get("content-type"),
    )


# ─── Auth ───────────────────────────────────────────────────────
# Two flavors of the same check. opencode (the Anthropic SDK) sends
# `x-api-key`; file_sync and (Phase 3) MCP clients send `Authorization: Bearer`.

def _resolve(token: str) -> Session:
    session = store.get_by_token(token)
    if session is None:
        raise HTTPException(401, "Invalid or expired session token")
    if not session.active:
        raise HTTPException(403, "Session has been deactivated")
    return session


async def get_session_bearer(
    authorization: str = Header(..., description="Bearer {session_token}"),
) -> Session:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Invalid authorization header")
    return _resolve(authorization[len("Bearer "):])


async def get_session_apikey(
    x_api_key: str = Header(..., alias="x-api-key", description="Session token"),
) -> Session:
    return _resolve(x_api_key)


# ─── Sessions ───────────────────────────────────────────────────

class CreateSessionRequest(BaseModel):
    task: str


@app.post("/sessions")
async def create_session(req: CreateSessionRequest):
    session = store.create_session(task=req.task)
    logger.info(f"Created session {session.session_id} ({req.task[:80]})")
    return {
        "session_id": session.session_id,
        "token": session.token,
        "task": session.task,
    }


@app.get("/sessions")
async def list_sessions():
    return [
        {
            "session_id": s.session_id,
            "task": s.task[:100],
            "active": s.active,
            "messages": len(s.conversation_history),
            "tokens_used": s.total_tokens_used,
        }
        for s in store.list_sessions()
    ]


@app.get("/sessions/{session_id}")
async def get_session(session_id: str):
    session = store.get_by_id(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    return {
        "session_id": session.session_id,
        "task": session.task,
        "active": session.active,
        "conversation_history": session.conversation_history,
        "tokens_used": session.total_tokens_used,
        "files": list_session_files(session.session_id),
    }


@app.post("/sessions/{session_id}/deactivate")
async def deactivate_session(session_id: str):
    store.deactivate(session_id)
    return {"status": "deactivated"}


# ─── Anthropic-compatible LLM proxy ─────────────────────────────

async def _proxy_anthropic(request: Request, session: Session) -> StreamingResponse:
    body = await request.body()

    # Pull the bits we want for both auditing and for stdout logging. We don't
    # store the full message bodies in the session (they can be huge and
    # contain transient context) — but we DO log them to the control-plane
    # container's stdout so `docker logs control-plane` is a live wire-trace.
    payload: dict = {}
    last_text = ""
    try:
        payload = json.loads(body) if body else {}
        last_user = next(
            (m for m in reversed(payload.get("messages", [])) if m.get("role") == "user"),
            None,
        )
        if last_user is not None:
            content = last_user.get("content")
            if isinstance(content, str):
                last_text = content
            elif isinstance(content, list):
                last_text = next(
                    (b.get("text", "") for b in content if b.get("type") == "text"),
                    "",
                )
        store.add_messages(
            session.session_id,
            [
                {
                    "role": "system",
                    "content": (
                        f"[llm] model={payload.get('model', DEFAULT_MODEL)} "
                        f"messages={len(payload.get('messages', []))} "
                        f"stream={payload.get('stream', False)} "
                        f"last_user={last_text[:200]}"
                    ),
                }
            ],
        )
    except (ValueError, AttributeError):
        pass  # malformed body — let the upstream return the proper error

    is_stream = bool(payload.get("stream"))
    logger.info(
        "─── /messages session=%s model=%s msgs=%d stream=%s",
        session.session_id,
        payload.get("model", DEFAULT_MODEL),
        len(payload.get("messages", [])),
        is_stream,
    )
    if last_text:
        logger.info("    last_user: %s", last_text[:500])
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("    full request body: %s", body.decode("utf-8", errors="replace")[:4000])

    client, upstream = await open_upstream(body, dict(request.headers))
    logger.info(
        "    upstream → %d %s",
        upstream.status_code,
        upstream.headers.get("content-type", "?"),
    )

    async def relay():
        total = 0
        first_preview_logged = False
        try:
            # aiter_bytes auto-decompresses (gzip/deflate) so we relay plain
            # text/event-stream or application/json bytes downstream, matching
            # the headers we forward. Using aiter_raw here was a bug: it
            # yielded gzipped bytes that opencode silently failed to parse.
            async for chunk in upstream.aiter_bytes():
                total += len(chunk)
                if not first_preview_logged and chunk:
                    preview = chunk[:300].decode("utf-8", errors="replace")
                    logger.info("    first chunk (%d B): %s", len(chunk), preview.replace("\n", "\\n"))
                    first_preview_logged = True
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()
            logger.info("    relayed %d bytes", total)

    headers = filter_response_headers(dict(upstream.headers))
    return StreamingResponse(
        relay(),
        status_code=upstream.status_code,
        headers=headers,
        media_type=upstream.headers.get("content-type", "application/json"),
    )


@app.post("/v1/messages")
async def v1_messages(request: Request, session: Session = Depends(get_session_apikey)):
    return await _proxy_anthropic(request, session)


# Some Anthropic clients are configured with a baseURL that already ends in
# `/v1`, so the SDK appends just `/messages`. Accept that form too — opencode
# "just works" regardless of how its baseURL is set.
@app.post("/messages")
async def messages(request: Request, session: Session = Depends(get_session_apikey)):
    return await _proxy_anthropic(request, session)


# ─── GitHub repo clone ──────────────────────────────────────────
# github-mcp-server has no "clone" tool, and GitHub's tarball API returns
# a snapshot without .git/ history. To give the sandbox a real working
# tree (so log/diff/branch/checkout/merge work against actual history),
# we do a server-side `git clone` here — with the PAT injected via
# http.extraHeader so it never lands in .git/config — then tar the result
# (including .git/) and stream it back. The temp dir is cleaned up after
# the stream finishes.

# Conservative validation. We pass these as argv (no shell), so this is
# defense in depth — mainly to keep `..` out of clone_path and weird
# characters out of git's CLI surface.
_NAME_RE = re.compile(r"[A-Za-z0-9._-]+")
_REF_RE = re.compile(r"[A-Za-z0-9._/-]+")


@app.get("/v1/repo/tarball")
async def repo_tarball(
    owner: str,
    repo: str,
    ref: str | None = None,
    session: Session = Depends(get_session_bearer),
):
    if not GITHUB_PAT:
        raise HTTPException(503, "GITHUB_PAT not configured on control plane")
    if not _NAME_RE.fullmatch(owner) or not _NAME_RE.fullmatch(repo):
        raise HTTPException(400, "invalid owner/repo (alphanumeric, ., _, - only)")
    if ref and not _REF_RE.fullmatch(ref):
        raise HTTPException(400, "invalid ref")

    logger.info(
        "─── /v1/repo/tarball session=%s repo=%s/%s ref=%s",
        session.session_id, owner, repo, ref or "(default branch)",
    )
    store.add_messages(session.session_id, [{
        "role": "system",
        "content": f"[clone] {owner}/{repo}@{ref or 'default'}",
    }])

    tmpdir = tempfile.mkdtemp(prefix=f"clone-{session.session_id}-")
    clone_path = os.path.join(tmpdir, repo)

    # PAT auth via URL embedding — GitHub's documented pattern for HTTPS git.
    # `x-access-token` is the special username GitHub recognizes for PAT auth.
    # The credentialed URL ends up in .git/config after a normal clone; we
    # rewrite remote.origin.url and delete FETCH_HEAD below to scrub it.
    clean_url = f"https://github.com/{owner}/{repo}.git"
    auth_url = f"https://x-access-token:{GITHUB_PAT}@github.com/{owner}/{repo}.git"

    clone_args = ["git", "clone", "--quiet"]
    if ref:
        clone_args += ["--branch", ref]
    clone_args += [auth_url, clone_path]

    proc = await asyncio.create_subprocess_exec(
        *clone_args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        shutil.rmtree(tmpdir, ignore_errors=True)
        msg = err.decode("utf-8", errors="replace")[:1000].replace(GITHUB_PAT, "[PAT]")
        logger.error(
            "    clone FAILED (rc=%d) for %s/%s@%s: %s",
            proc.returncode, owner, repo, ref or "default", msg,
        )
        raise HTTPException(400, f"git clone failed (rc={proc.returncode}): {msg}")

    # Scrub the PAT out of the cloned repo before tar'ing. set-url replaces
    # the URL in .git/config; FETCH_HEAD can also carry the credentialed
    # URL on some git versions, so we delete it (cosmetic — it's regenerated
    # on the next fetch, which can't happen from the sandbox anyway).
    scrub = await asyncio.create_subprocess_exec(
        "git", "-C", clone_path, "remote", "set-url", "origin", clean_url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, scrub_err = await scrub.communicate()
    if scrub.returncode != 0:
        shutil.rmtree(tmpdir, ignore_errors=True)
        logger.error(
            "    remote set-url FAILED for %s/%s: %s",
            owner, repo,
            scrub_err.decode("utf-8", errors="replace")[:500].replace(GITHUB_PAT, "[PAT]"),
        )
        raise HTTPException(500, "failed to scrub remote URL")

    fetch_head = os.path.join(clone_path, ".git", "FETCH_HEAD")
    if os.path.exists(fetch_head):
        os.remove(fetch_head)

    logger.info("    clone OK → streaming tar back to sandbox")

    # `-C clone_path .` makes tar entries start at `./` — no wrapper dir,
    # so sandbox-clone untars straight into its target without --strip.
    tar_proc = await asyncio.create_subprocess_exec(
        "tar", "-czf", "-", "-C", clone_path, ".",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def relay():
        try:
            while True:
                chunk = await tar_proc.stdout.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                await tar_proc.wait()
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)

    return StreamingResponse(
        relay(),
        media_type="application/gzip",
        headers={"content-disposition": f'attachment; filename="{repo}.tar.gz"'},
    )


# ─── File storage ───────────────────────────────────────────────

class PresignedURLRequest(BaseModel):
    paths: list[str]
    action: str = "upload"


@app.post("/files/presigned-urls")
async def get_presigned_urls(
    req: PresignedURLRequest, session: Session = Depends(get_session_bearer)
):
    urls = []
    for path in req.paths:
        if req.action == "upload":
            urls.append(generate_upload_url(session.session_id, path))
        else:
            urls.append(generate_download_url(session.session_id, path))
    return {"urls": urls}


@app.get("/files")
async def get_files(session: Session = Depends(get_session_bearer)):
    return {"files": list_session_files(session.session_id)}


# ─── Health ─────────────────────────────────────────────────────

@app.get("/health")
async def health():
    llm = await check_llm_health()
    return {
        "control_plane": "healthy",
        "llm": llm,
        "active_sessions": len([s for s in store.list_sessions() if s.active]),
    }
