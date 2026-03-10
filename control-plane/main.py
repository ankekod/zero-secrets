"""
Control Plane — the single gateway between sandboxes and the outside world.

This is the service that holds all credentials. Sandboxes authenticate
with a session token and can only:
- Invoke the LLM (via /llm/chat)
- Get presigned URLs for file upload/download
- Persist messages to conversation history

The control plane is stateless in the sense that it can be horizontally
scaled. Session state is held in-memory here (production would use a DB).
"""

import logging
from fastapi import FastAPI, HTTPException, Depends, Header
from pydantic import BaseModel

from sessions import SessionStore, Session
from llm_proxy import invoke_llm, check_llm_health
from file_storage import (
    generate_upload_url,
    generate_download_url,
    list_session_files,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Agent Control Plane", version="0.1.0")
store = SessionStore()


@app.on_event("startup")
async def startup_check():
    """Log whether the Anthropic API key is present."""
    logger.info("Control plane starting up...")
    health = await check_llm_health()
    if health["status"] == "healthy":
        logger.info(f"Anthropic API ready. Model: {health['model']}")
    else:
        logger.warning(f"LLM not configured: {health.get('error')}")


# ─── Auth dependency ────────────────────────────────────────────

async def get_current_session(
    authorization: str = Header(..., description="Bearer {session_token}")
) -> Session:
    """Validate the session token from the sandbox."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Invalid authorization header")

    token = authorization[len("Bearer "):]
    session = store.get_by_token(token)

    if session is None:
        raise HTTPException(401, "Invalid or expired session token")
    if not session.active:
        raise HTTPException(403, "Session has been deactivated")

    return session


# ─── Session management (called by orchestrator, not sandbox) ───

class CreateSessionRequest(BaseModel):
    task: str


@app.post("/sessions")
async def create_session(req: CreateSessionRequest):
    """Create a new session. Returns the token to pass to the sandbox."""
    session = store.create_session(task=req.task)
    logger.info(f"Created session {session.session_id} for task: {req.task[:80]}")
    return {
        "session_id": session.session_id,
        "token": session.token,
        "task": session.task,
    }


@app.get("/sessions")
async def list_sessions():
    """List all sessions (for dashboard/debugging)."""
    sessions = store.list_sessions()
    return [
        {
            "session_id": s.session_id,
            "task": s.task[:100],
            "active": s.active,
            "messages": len(s.conversation_history),
            "tokens_used": s.total_tokens_used,
        }
        for s in sessions
    ]


@app.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """Get session details including conversation history."""
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


# ─── LLM Proxy (called by sandbox) ─────────────────────────────

class LLMRequest(BaseModel):
    new_messages: list[dict]
    model: str | None = None


@app.post("/llm/chat")
async def llm_chat(req: LLMRequest, session: Session = Depends(get_current_session)):
    """
    The core LLM proxy endpoint.
    
    The sandbox sends only NEW messages. The control plane:
    1. Fetches full conversation history from the session
    2. Appends new messages
    3. Sends everything to Ollama
    4. Stores the full exchange in history
    5. Returns only the assistant's response
    """
    history = store.get_history(session.session_id)

    result = await invoke_llm(
        history=history,
        new_messages=req.new_messages,
        model=req.model,
    )

    # Persist new messages + response to session history
    store.add_messages(session.session_id, req.new_messages)
    store.add_messages(session.session_id, [result["message"]])

    # Track token usage
    session.total_tokens_used += result.get("tokens_used", 0)

    return {
        "message": result["message"],
        "tokens_used": result["tokens_used"],
        "total_tokens_used": session.total_tokens_used,
    }


# ─── Persist messages (for tool results, etc) ──────────────────

class PersistRequest(BaseModel):
    messages: list[dict]


@app.post("/messages/persist")
async def persist_messages(
    req: PersistRequest, session: Session = Depends(get_current_session)
):
    """Persist messages to session history without calling the LLM."""
    store.add_messages(session.session_id, req.messages)
    return {"stored": len(req.messages)}


# ─── File Storage (presigned URLs) ──────────────────────────────

class PresignedURLRequest(BaseModel):
    paths: list[str]
    action: str = "upload"  # "upload" or "download"


@app.post("/files/presigned-urls")
async def get_presigned_urls(
    req: PresignedURLRequest, session: Session = Depends(get_current_session)
):
    """
    Generate presigned S3 URLs for file upload/download.
    
    The sandbox never sees storage credentials. It gets a time-limited
    URL that can only access files within its session scope.
    """
    urls = []
    for path in req.paths:
        if req.action == "upload":
            urls.append(generate_upload_url(session.session_id, path))
        else:
            urls.append(generate_download_url(session.session_id, path))
    return {"urls": urls}


@app.get("/files")
async def get_files(session: Session = Depends(get_current_session)):
    """List all files in the session's workspace."""
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
