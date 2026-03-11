"""
Session management for the control plane.

Each sandbox agent gets a session. The session holds:
- Conversation history (so sandboxes are stateless & resumable)
- File metadata
- Cost tracking
"""

import uuid
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Session:
    session_id: str
    token: str
    task: str
    created_at: float = field(default_factory=time.time)
    conversation_history: list = field(default_factory=list)
    files: list = field(default_factory=list)
    total_tokens_used: int = 0
    active: bool = True


class SessionStore:
    """In-memory session store. Production would use a persistent database."""

    def __init__(self):
        self._sessions: dict[str, Session] = {}
        self._token_index: dict[str, str] = {}  # token -> session_id

    def create_session(self, task: str) -> Session:
        session_id = str(uuid.uuid4())[:8]
        token = f"st_{uuid.uuid4().hex}"

        session = Session(
            session_id=session_id,
            token=token,
            task=task,
        )

        self._sessions[session_id] = session
        self._token_index[token] = session_id
        return session

    def get_by_token(self, token: str) -> Optional[Session]:
        session_id = self._token_index.get(token)
        if session_id is None:
            return None
        return self._sessions.get(session_id)

    def get_by_id(self, session_id: str) -> Optional[Session]:
        return self._sessions.get(session_id)

    def add_messages(self, session_id: str, messages: list[dict]):
        session = self._sessions.get(session_id)
        if session:
            session.conversation_history.extend(messages)

    def get_history(self, session_id: str) -> list[dict]:
        session = self._sessions.get(session_id)
        if session:
            return session.conversation_history
        return []

    def list_sessions(self) -> list[Session]:
        return list(self._sessions.values())

    def deactivate(self, session_id: str):
        session = self._sessions.get(session_id)
        if session:
            session.active = False
