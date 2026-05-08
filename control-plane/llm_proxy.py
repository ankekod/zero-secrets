"""
Transparent proxy to the Anthropic Messages API.

The sandbox sends a normal Anthropic API request to the control plane;
the control plane validates the session token (sent as `x-api-key`),
swaps it for the real `ANTHROPIC_API_KEY`, and forwards the request to
api.anthropic.com — streaming responses pass through verbatim.

The sandbox never sees the real key. From its perspective, the control
plane *is* api.anthropic.com.
"""

import os
import logging

import httpx

logger = logging.getLogger(__name__)

UPSTREAM_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")

# Inbound request headers we forward to Anthropic (others are dropped or
# replaced — notably `x-api-key`, which is the session token, not the real key).
FORWARDED_REQUEST_HEADERS = {"anthropic-version", "anthropic-beta"}

# Upstream response headers we strip before relaying — letting these through
# breaks Starlette's framing of the streamed response.
STRIPPED_RESPONSE_HEADERS = {
    "content-length",
    "transfer-encoding",
    "content-encoding",
    "connection",
}


def upstream_headers(incoming: dict) -> dict:
    headers: dict[str, str] = {
        "x-api-key": os.getenv("ANTHROPIC_API_KEY", ""),
        "content-type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    for name, value in incoming.items():
        if name.lower() in FORWARDED_REQUEST_HEADERS:
            headers[name] = value
    return headers


def filter_response_headers(upstream: dict) -> dict:
    return {k: v for k, v in upstream.items() if k.lower() not in STRIPPED_RESPONSE_HEADERS}


async def open_upstream(
    body: bytes, incoming_headers: dict
) -> tuple[httpx.AsyncClient, httpx.Response]:
    """
    Open a streaming connection to Anthropic. Returns the client and the
    response — the caller is responsible for closing both after the body
    has been fully relayed.
    """
    client = httpx.AsyncClient(timeout=None)
    req = client.build_request(
        "POST",
        UPSTREAM_URL,
        content=body,
        headers=upstream_headers(incoming_headers),
    )
    resp = await client.send(req, stream=True)
    return client, resp


async def check_llm_health() -> dict:
    if os.getenv("ANTHROPIC_API_KEY"):
        return {
            "status": "healthy",
            "provider": "anthropic",
            "default_model": DEFAULT_MODEL,
        }
    return {"status": "unhealthy", "error": "ANTHROPIC_API_KEY not set"}
