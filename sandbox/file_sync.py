"""
Continuously syncs /workspace to S3 via presigned URLs from the control plane.

Runs as a background process inside the sandbox. The agent itself doesn't
need to know about persistence — file_sync watches the workspace and uploads
anything that changes. The control plane mints the URLs; storage credentials
never enter the sandbox.
"""

import argparse
import asyncio
import hashlib
import logging
import mimetypes
import os
import signal
import sys

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [file_sync] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

WORKSPACE_DIR = "/workspace"
SCAN_INTERVAL_SECONDS = 3


def hash_file(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def detect_changes(seen: dict[str, str]) -> list[str]:
    changed = []
    for root, _dirs, files in os.walk(WORKSPACE_DIR):
        for fname in files:
            full = os.path.join(root, fname)
            rel = os.path.relpath(full, WORKSPACE_DIR)
            try:
                h = hash_file(full)
            except OSError:
                continue
            if seen.get(rel) != h:
                changed.append(rel)
                seen[rel] = h
    return changed


async def get_upload_urls(
    client: httpx.AsyncClient, base: str, headers: dict, paths: list[str]
) -> list[dict]:
    resp = await client.post(
        f"{base}/files/presigned-urls",
        json={"paths": paths, "action": "upload"},
        headers=headers,
        timeout=10.0,
    )
    resp.raise_for_status()
    return resp.json()["urls"]


async def upload(client: httpx.AsyncClient, url: str, path: str) -> None:
    full = os.path.join(WORKSPACE_DIR, path)
    content_type, _ = mimetypes.guess_type(path)
    content_type = content_type or "application/octet-stream"
    with open(full, "rb") as f:
        body = f.read()
    resp = await client.put(
        url, content=body, headers={"Content-Type": content_type}, timeout=30.0
    )
    resp.raise_for_status()


async def run(token: str, control_plane: str) -> None:
    base = control_plane.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    seen: dict[str, str] = {}

    logger.info("watching %s (interval %ds)", WORKSPACE_DIR, SCAN_INTERVAL_SECONDS)

    async with httpx.AsyncClient() as client:
        while True:
            try:
                changed = detect_changes(seen)
                if changed:
                    logger.info("syncing %d file(s): %s", len(changed), changed)
                    urls = await get_upload_urls(client, base, headers, changed)
                    for path, url_info in zip(changed, urls):
                        try:
                            await upload(client, url_info["url"], path)
                            logger.info("  uploaded %s", path)
                        except Exception as e:
                            logger.warning("  failed %s: %s", path, e)
            except Exception as e:
                logger.warning("scan loop error: %s", e)

            await asyncio.sleep(SCAN_INTERVAL_SECONDS)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", required=True)
    parser.add_argument("--control-plane", required=True)
    parser.add_argument("--session", required=True)
    args = parser.parse_args()

    loop = asyncio.new_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: sys.exit(0))
    try:
        loop.run_until_complete(run(args.token, args.control_plane))
    except SystemExit:
        pass


if __name__ == "__main__":
    main()
