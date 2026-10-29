"""Identical capture policy for API issuance and durable worker verification."""

from penforge_contracts.templates import load_templates
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.storage.base import ObjectStorage
from app.core.config import Settings
from app.modules.identity.service import IdentityService
from app.modules.uploads.service import QueueService, UploadsService


def capture_services(
    settings: Settings,
    engine: AsyncEngine,
    identity: IdentityService,
    storage: ObjectStorage,
) -> tuple[UploadsService, QueueService]:
    uploads = UploadsService(
        engine,
        identity,
        storage,
        upload_issuance_enabled=settings.upload_issuance_enabled,
        upload_max_bytes=settings.upload_max_bytes,
        upload_max_pixels=settings.upload_max_pixels,
        upload_max_dimension=settings.upload_max_dimension,
        upload_files_per_project=settings.upload_files_per_project,
        pending_upload_ttl_seconds=settings.pending_upload_ttl_seconds,
        signed_upload_ttl_seconds=settings.signed_upload_ttl_seconds,
        signed_download_ttl_seconds=settings.signed_download_ttl_seconds,
        derivative_max_bytes=settings.derivative_max_bytes,
        derivative_max_dimension=settings.derivative_max_dimension,
        idempotency_ttl_seconds=settings.idempotency_retention_seconds,
        job_max_attempts=settings.job_max_attempts,
        storage_backend=settings.storage_backend,
        templates=load_templates(),
    )
    return uploads, QueueService(
        engine,
        uploads,
        lease_seconds=settings.lease_seconds,
        max_attempts=settings.job_max_attempts,
    )
