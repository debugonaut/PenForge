"""One storage construction path for the API and the durable worker."""

import hashlib

from app.adapters.storage.base import ObjectStorage
from app.adapters.storage.local_fs import LocalObjectStorage
from app.adapters.storage.s3 import S3ObjectStorage
from app.core.config import Settings


def _capability_secret(config: Settings) -> bytes | None:
    if config.hmac_secret is None:
        return None
    return hashlib.sha256(
        b"penforge-local-storage-v1\0" + config.hmac_secret.get_secret_value().encode()
    ).digest()


def build_storage(config: Settings) -> ObjectStorage | None:
    """Hosted builds use S3; local/test may keep the private filesystem adapter."""
    if config.storage_backend == "disabled":
        return None
    if config.storage_backend == "local_fs":
        if config.app_env not in ("local", "test"):
            raise ValueError("Local filesystem storage is restricted to local/test")
        return LocalObjectStorage(
            config.storage_root,
            secret=_capability_secret(config),
            max_upload_bytes=config.upload_max_bytes,
        )
    if not (
        config.storage_s3_endpoint
        and config.storage_s3_bucket
        and config.storage_s3_access_key
        and config.storage_s3_secret_key
    ):
        raise ValueError("S3 storage configuration is incomplete")
    return S3ObjectStorage(
        endpoint=config.storage_s3_endpoint,
        bucket=config.storage_s3_bucket,
        region=config.storage_s3_region,
        access_key=config.storage_s3_access_key.get_secret_value(),
        secret_key=config.storage_s3_secret_key.get_secret_value(),
        prefix=config.storage_s3_prefix,
        force_path_style=config.storage_s3_path_style,
        secret=_capability_secret(config),
        max_upload_bytes=config.upload_max_bytes,
    )
