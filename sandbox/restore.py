"""
Pull every file previously uploaded for this session back into /workspace.

Run once at container start when launching in resume mode. The control plane
holds the MinIO credentials; we ask it for download URLs and stream the
bytes in. Anything already present on disk under the same relative path is
overwritten.
"""

import argparse
import logging
import os
import sys

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [restore] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

WORKSPACE_DIR = "/workspace"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", required=True)
    parser.add_argument("--control-plane", required=True)
    args = parser.parse_args()

    base = args.control_plane.rstrip("/")
    headers = {"Authorization": f"Bearer {args.token}"}

    with httpx.Client(timeout=30.0) as client:
        resp = client.get(f"{base}/files", headers=headers)
        resp.raise_for_status()
        files = resp.json().get("files", [])

        if not files:
            logger.info("no prior files for this session — nothing to restore")
            return

        paths = [f["path"] for f in files]
        logger.info("restoring %d file(s) from S3", len(paths))

        # One round-trip for all presigned download URLs, then stream each.
        urls_resp = client.post(
            f"{base}/files/presigned-urls",
            json={"paths": paths, "action": "download"},
            headers=headers,
        )
        urls_resp.raise_for_status()
        urls = urls_resp.json()["urls"]

        for path, url_info in zip(paths, urls):
            target = os.path.join(WORKSPACE_DIR, path)
            os.makedirs(os.path.dirname(target) or WORKSPACE_DIR, exist_ok=True)
            try:
                got = client.get(url_info["url"])
                got.raise_for_status()
                with open(target, "wb") as f:
                    f.write(got.content)
                logger.info("  restored %s (%d B)", path, len(got.content))
            except Exception as e:
                logger.warning("  failed %s: %s", path, e)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # Don't block code-server startup on restore failure — log and bail.
        logger.error("restore failed: %s", e)
        sys.exit(0)
