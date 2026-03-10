from __future__ import annotations

import io
from typing import BinaryIO

try:
    import boto3
    from botocore.exceptions import ClientError
except Exception:  # pragma: no cover - optional dependency for non-R2 environments
    boto3 = None
    ClientError = Exception

from justice.utils import (
    OBJECT_STORAGE_BACKEND,
    S3_ACCESS_KEY_ID,
    S3_BUCKET,
    S3_ENDPOINT,
    S3_SECRET_ACCESS_KEY,
    logger,
)


def build_document_key(subject_id: str, content_sha256: str, extension: str) -> str:
    safe_ext = extension.lstrip(".")
    return f"companies/{subject_id}/documents/{content_sha256}.{safe_ext}"


def _r2_client():
    if boto3 is None:
        raise RuntimeError("boto3 dependency is missing.")
    if not S3_ENDPOINT or not S3_BUCKET or not S3_ACCESS_KEY_ID or not S3_SECRET_ACCESS_KEY:
        raise RuntimeError("Missing S3/R2 configuration.")
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=S3_ACCESS_KEY_ID,
        aws_secret_access_key=S3_SECRET_ACCESS_KEY,
        region_name="auto",
    )


def object_exists(key: str) -> bool:
    if OBJECT_STORAGE_BACKEND != "r2":
        raise RuntimeError("Object storage backend must be R2.")
    try:
        _r2_client().head_object(Bucket=S3_BUCKET, Key=key)
        return True
    except ClientError:
        return False


def upload_bytes_if_missing(key: str, data: bytes, content_type: str) -> str:
    if object_exists(key):
        logger.info(f"object storage hit key={key}")
        return key
    if OBJECT_STORAGE_BACKEND != "r2":
        raise RuntimeError("Object storage backend must be R2.")
    _r2_client().put_object(
        Bucket=S3_BUCKET,
        Key=key,
        Body=data,
        ContentType=content_type,
    )
    logger.info(f"object storage write key={key} bytes={len(data)}")
    return key


def download_bytes(key: str) -> bytes:
    if OBJECT_STORAGE_BACKEND != "r2":
        raise RuntimeError("Object storage backend must be R2.")
    response = _r2_client().get_object(Bucket=S3_BUCKET, Key=key)
    return response["Body"].read()


def open_binary_stream(key: str) -> BinaryIO:
    if OBJECT_STORAGE_BACKEND != "r2":
        raise RuntimeError("Object storage backend must be R2.")
    response = _r2_client().get_object(Bucket=S3_BUCKET, Key=key)
    return response["Body"]


def upload_document_pdf(subject_id: str, content_sha256: str, data: bytes) -> str:
    return upload_bytes_if_missing(
        build_document_key(subject_id, content_sha256, "pdf"),
        data,
        "application/pdf",
    )


def upload_document_text(subject_id: str, content_sha256: str, text: str) -> str:
    data = text.encode("utf-8")
    return upload_bytes_if_missing(
        build_document_key(subject_id, content_sha256, "txt"),
        data,
        "text/plain; charset=utf-8",
    )


def bytes_to_fileobj(data: bytes) -> BinaryIO:
    return io.BytesIO(data)
