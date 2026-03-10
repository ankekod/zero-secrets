"""
File Sync — uploads workspace files to S3 via presigned URLs.

The sandbox writes files to /workspace. This module syncs them to
MinIO/S3 without ever seeing storage credentials.

Flow:
1. Agent creates files in /workspace
2. file_sync detects changes
3. Asks control plane for presigned upload URLs
4. Uploads directly to MinIO using those URLs
"""

import os
import hashlib
import logging
import httpx

logger = logging.getLogger(__name__)

WORKSPACE_DIR = "/workspace"


class FileSync:
    def __init__(self, gateway):
        self.gateway = gateway
        self._file_hashes: dict[str, str] = {}

    def _hash_file(self, path: str) -> str:
        """Get MD5 hash of a file to detect changes."""
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    def detect_changes(self) -> list[str]:
        """Scan /workspace for new or modified files."""
        changed = []
        for root, dirs, files in os.walk(WORKSPACE_DIR):
            for fname in files:
                full_path = os.path.join(root, fname)
                rel_path = os.path.relpath(full_path, WORKSPACE_DIR)

                current_hash = self._hash_file(full_path)
                if self._file_hashes.get(rel_path) != current_hash:
                    changed.append(rel_path)
                    self._file_hashes[rel_path] = current_hash

        return changed

    async def sync(self) -> list[str]:
        """Sync changed files to S3 via presigned URLs."""
        changed = self.detect_changes()
        if not changed:
            return []

        logger.info(f"Syncing {len(changed)} changed file(s): {changed}")

        # Get presigned upload URLs from control plane
        urls = await self.gateway.get_upload_urls(changed)

        # Upload each file directly to MinIO (no credentials needed!)
        synced = []
        for file_path, url_info in zip(changed, urls):
            full_path = os.path.join(WORKSPACE_DIR, file_path)
            try:
                with open(full_path, "rb") as f:
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        resp = await client.put(
                            url_info["url"],
                            content=f.read(),
                        )
                        resp.raise_for_status()
                synced.append(file_path)
                logger.info(f"Uploaded {file_path} via presigned URL")
            except Exception as e:
                logger.error(f"Failed to upload {file_path}: {e}")

        return synced
