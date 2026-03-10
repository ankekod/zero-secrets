"""
Gateway Protocol — the sandbox's interface to the outside world.

Mirrors the article's Gateway pattern:
    class AgentGateway(Protocol):
        async def invoke_llm(...) -> LLMResponse
        async def persist_messages(...) -> None

The sandbox only knows about this interface. In production, it sends HTTP
to the control plane. For local testing, you could swap in a DirectGateway
that calls Ollama directly. The agent code doesn't change.
"""

import httpx
import logging

logger = logging.getLogger(__name__)


class ControlPlaneGateway:
    """
    Production gateway: all requests go through the control plane via HTTP.
    The sandbox has no direct access to LLMs, storage, or any external service.
    """

    def __init__(self, control_plane_url: str, session_token: str):
        self.base_url = control_plane_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {session_token}"}

    async def invoke_llm(self, new_messages: list[dict], model: str = None) -> dict:
        """
        Send new messages to the LLM via the control plane.
        The control plane reconstructs full history and forwards to Ollama.
        """
        payload = {"new_messages": new_messages}
        if model:
            payload["model"] = model

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{self.base_url}/llm/chat",
                json=payload,
                headers=self.headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def persist_messages(self, messages: list[dict]) -> None:
        """Store messages in session history without calling the LLM."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{self.base_url}/messages/persist",
                json={"messages": messages},
                headers=self.headers,
            )
            resp.raise_for_status()

    async def get_upload_urls(self, paths: list[str]) -> list[dict]:
        """Get presigned S3 URLs for uploading files."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{self.base_url}/files/presigned-urls",
                json={"paths": paths, "action": "upload"},
                headers=self.headers,
            )
            resp.raise_for_status()
            return resp.json()["urls"]

    async def get_download_urls(self, paths: list[str]) -> list[dict]:
        """Get presigned S3 URLs for downloading files."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{self.base_url}/files/presigned-urls",
                json={"paths": paths, "action": "download"},
                headers=self.headers,
            )
            resp.raise_for_status()
            return resp.json()["urls"]
