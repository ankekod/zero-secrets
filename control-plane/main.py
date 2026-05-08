"""
Control plane — credential holder and API gateway for sandbox agents.

The sandbox authenticates with a session token. From inside the sandbox,
this service looks like:
  - api.anthropic.com  (via /v1/messages — opencode talks to this)
  - an S3 URL minter   (via /files/presigned-urls — file_sync uses this)
  - (Phase 3) MCP servers for filesystem + git tools

Session state is in-memory. Production would back it with a real store.
"""

import json
import logging
import os

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
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

_log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, _log_level, logging.INFO),
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("control-plane")

app = FastAPI(title="Agent Control Plane", version="0.2.0")
store = SessionStore()


@app.on_event("startup")
async def startup_check():
    logger.info("Control plane starting up...")
    health = await check_llm_health()
    if health["status"] == "healthy":
        logger.info(f"Anthropic API ready. Default model: {health['default_model']}")
    else:
        logger.warning(f"LLM not configured: {health.get('error')}")


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
