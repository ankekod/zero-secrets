"""
Presigned URL generation for MinIO/S3 file storage.
"""

import os
import logging
from datetime import timedelta

import boto3
from botocore.client import Config

logger = logging.getLogger(__name__)

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "agent-workspaces")

# Presigned URLs must use the endpoint reachable by the URL consumer (sandbox containers).
MINIO_PRESIGN_ENDPOINT = os.getenv("MINIO_PRESIGN_ENDPOINT", f"http://{MINIO_ENDPOINT}")


def get_s3_client():
    """Create an S3 client pointing at MinIO."""
    return boto3.client(
        "s3",
        endpoint_url=f"http://{MINIO_ENDPOINT}",
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


def get_presign_client():
    """S3 client configured with the endpoint embedded in presigned URLs."""
    return boto3.client(
        "s3",
        endpoint_url=MINIO_PRESIGN_ENDPOINT,
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


def generate_upload_url(session_id: str, file_path: str) -> dict:
    """
    Generate a presigned PUT URL for uploading a file.
    Files are scoped to session: {session_id}/{file_path}
    """
    key = f"{session_id}/{file_path}"
    client = get_presign_client()

    url = client.generate_presigned_url(
        "put_object",
        Params={"Bucket": MINIO_BUCKET, "Key": key},
        ExpiresIn=int(timedelta(minutes=15).total_seconds()),
    )

    logger.info(f"Generated upload URL for {key}")
    return {"url": url, "key": key, "method": "PUT"}


def generate_download_url(session_id: str, file_path: str) -> dict:
    """Generate a presigned GET URL for downloading a file."""
    key = f"{session_id}/{file_path}"
    client = get_presign_client()

    url = client.generate_presigned_url(
        "get_object",
        Params={"Bucket": MINIO_BUCKET, "Key": key},
        ExpiresIn=int(timedelta(minutes=15).total_seconds()),
    )

    logger.info(f"Generated download URL for {key}")
    return {"url": url, "key": key, "method": "GET"}


def list_session_files(session_id: str) -> list[str]:
    """List all files stored for a session."""
    client = get_s3_client()
    prefix = f"{session_id}/"

    try:
        response = client.list_objects_v2(Bucket=MINIO_BUCKET, Prefix=prefix)
        files = []
        for obj in response.get("Contents", []):
            path = obj["Key"][len(prefix):]
            files.append({
                "path": path,
                "size": obj["Size"],
                "last_modified": obj["LastModified"].isoformat(),
            })
        return files
    except Exception as e:
        logger.error(f"Failed to list files for session {session_id}: {e}")
        return []
