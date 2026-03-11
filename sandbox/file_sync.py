"""
Syncs /workspace files to S3 via presigned upload URLs obtained from the control plane.
"""

import os
import hashlib
import logging
import mimetypes
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

        urls = await self.gateway.get_upload_urls(changed)

        synced = []
        for file_path, url_info in zip(changed, urls):
            full_path = os.path.join(WORKSPACE_DIR, file_path)
            try:
                content_type, _ = mimetypes.guess_type(file_path)
                content_type = content_type or "application/octet-stream"
                with open(full_path, "rb") as f:
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        resp = await client.put(
                            url_info["url"],
                            content=f.read(),
                            headers={"Content-Type": content_type},
                        )
                        resp.raise_for_status()
                synced.append(file_path)
                logger.info(f"Uploaded {file_path} via presigned URL")
            except Exception as e:
                logger.error(f"Failed to upload {file_path}: {e}")

        return synced
