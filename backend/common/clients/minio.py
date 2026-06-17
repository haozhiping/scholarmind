"""MinIO object-storage client — PDF upload / figure storage / presigned URLs."""
from typing import Optional

from minio import Minio
from minio.error import S3Error

from common.config import settings
from common.logging import logger

_client: Optional[Minio] = None


def get_minio_client() -> Minio:
    """Singleton Minio client."""
    global _client
    if _client is None:
        _client = Minio(
            endpoint=settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
        )
        logger.info(f"MinIO client connected to {settings.MINIO_ENDPOINT}")
    return _client


def ensure_bucket(bucket_name: str) -> None:
    """Create bucket if it does not exist (idempotent)."""
    client = get_minio_client()
    found = client.bucket_exists(bucket_name)
    if not found:
        client.make_bucket(bucket_name)
        logger.info(f"MinIO bucket '{bucket_name}' created")
    # MinIO buckets are implicitly public-read for presigned access;
    # no explicit policy needed unless fine-grained ACL is required.


def upload_pdf(file_path: str, key: str) -> None:
    """Upload a PDF file to the papers bucket.
    
    Args:
        file_path: local path (e.g. tempfile). 
        key: object key, e.g. ``{user_id}/{paper_id}/original.pdf``.
    """
    bucket = settings.MINIO_BUCKET_PDF
    ensure_bucket(bucket)
    client = get_minio_client()
    client.fput_object(bucket, key, file_path)
    logger.info(f"MinIO uploaded pdf → {bucket}/{key}")


def upload_bytes(data: bytes, bucket: str, key: str, content_type: str = "application/octet-stream") -> None:
    """Upload raw bytes to MinIO."""
    import io
    ensure_bucket(bucket)
    client = get_minio_client()
    client.put_object(bucket, key, io.BytesIO(data), length=len(data), content_type=content_type)


def upload_figure(image_path: str, key: str) -> None:
    """Upload a figure image to the figures bucket."""
    bucket = settings.MINIO_BUCKET_FIG
    ensure_bucket(bucket)
    client = get_minio_client()
    client.fput_object(bucket, key, image_path, content_type="image/png")
    logger.info(f"MinIO uploaded figure → {bucket}/{key}")


def presigned_url(bucket: str, key: str, expires: int = 3600) -> str:
    """Generate a presigned GET URL valid for *expires* seconds."""
    client = get_minio_client()
    return client.presigned_get_object(bucket, key, expires=expires)


def delete_object(bucket: str, key: str) -> None:
    """Remove an object (best-effort, log on missing)."""
    client = get_minio_client()
    try:
        client.remove_object(bucket, key)
    except S3Error as e:
        if e.code == "NoSuchKey":
            logger.warning(f"MinIO delete skipped — key not found: {bucket}/{key}")
        else:
            raise


def object_exists(bucket: str, key: str) -> bool:
    """Check whether an object exists."""
    client = get_minio_client()
    try:
        client.stat_object(bucket, key)
        return True
    except S3Error as e:
        if e.code == "NoSuchKey":
            return False
        raise
