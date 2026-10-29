"""Durable worker entry point; local filesystem or hosted S3-compatible storage."""

import argparse
import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

from app.adapters.storage.factory import build_storage
from app.core.config import Settings
from app.db.session import make_async_engine
from app.modules.analysis.service import AnalysisService
from app.modules.exports.service import ExportsService
from app.modules.identity.service import IdentityService
from app.modules.uploads.factory import capture_services

from packages.capture_safety import DecodeLimits
from services.worker.analysis import AnalysisWorker
from services.worker.exports import ExportWorker
from services.worker.runtime import PipelineWorker


async def main(
    report: Path | None = None,
    retention_report: Path | None = None,
    retention_apply: Path | None = None,
) -> None:
    settings = Settings()
    storage = build_storage(settings)
    if storage is None:
        raise SystemExit("Worker requires configured object storage.")
    engine = make_async_engine(
        settings.database_url.get_secret_value(), pool_size=settings.database_pool_size
    )
    uploads, queue = capture_services(settings, engine, IdentityService(engine), storage)
    try:
        privacy = None
        if settings.privacy_key:
            from app.modules.privacy.service import PrivacyService

            admin = None
            if settings.auth_backend == "firebase":
                from app.adapters.auth.firebase import FirebaseAuthVerifier

                admin = FirebaseAuthVerifier(settings)
            privacy = PrivacyService(
                engine,
                uploads.identity_service,
                uploads,
                storage,
                settings.privacy_key,
                identity_admin=admin,
                lease_seconds=settings.lease_seconds,
            )
            await privacy.restore_reconcile()
        if retention_report or retention_apply:
            if privacy is None:
                raise SystemExit("Stable privacy key is required for retention.")
            if retention_report:
                result = await privacy.retention_report()
                fd = os.open(
                    retention_report, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
                )
                with os.fdopen(fd, "w") as stream:
                    json.dump(result, stream, indent=2)
                print("Retention dry-run saved; no objects deleted.")
            else:
                assert retention_apply is not None
                result = await privacy.retention_apply(json.loads(retention_apply.read_text()))
                print(json.dumps(result, sort_keys=True))
            return
        if report:
            result = await uploads.reconcile()
            fd = os.open(report, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w") as stream:
                json.dump(result, stream, indent=2)
                stream.write("\n")
            print("Private dry-run reconciliation report saved; no orphan objects deleted.")
            return
        analysis = AnalysisWorker(
            AnalysisService(
                engine,
                uploads.identity_service,
                uploads,
                storage,
                lease_seconds=settings.lease_seconds,
            ),
            worker_id=f"local-analysis-{uuid4()}",
            temp_root=settings.worker_temp_root,
            heartbeat_seconds=settings.heartbeat_seconds,
            memory_mb=settings.worker_mem_mb,
        )
        worker = PipelineWorker(
            queue,
            uploads,
            storage,
            privacy=privacy,
            analysis=analysis,
            exports=ExportWorker(
                ExportsService(
                    engine,
                    uploads.identity_service,
                    uploads,
                    storage,
                    lease_seconds=settings.lease_seconds,
                ),
                worker_id=f"local-export-{uuid4()}",
                temp_root=settings.worker_temp_root,
                heartbeat_seconds=settings.heartbeat_seconds,
                memory_mb=settings.worker_mem_mb,
            ),
            worker_id=f"local-{uuid4()}",
            heartbeat_seconds=settings.heartbeat_seconds,
            temp_root=settings.worker_temp_root,
            limits=DecodeLimits(
                max_bytes=settings.upload_max_bytes,
                max_pixels=settings.upload_max_pixels,
                max_dimension=settings.upload_max_dimension,
                derivative_max_dimension=settings.derivative_max_dimension,
                max_derived_bytes=settings.derivative_max_bytes,
                memory_mb=settings.worker_mem_mb,
                cpu_seconds=settings.decode_cpu_seconds,
                wall_seconds=settings.decode_wall_seconds,
            ),
        )
        await worker.run(mode=settings.worker_mode, max_seconds=settings.worker_max_run_seconds)
    finally:
        if "admin" in locals() and admin:
            admin.close()
        storage.close()
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reconcile-report",
        type=Path,
        help="Write a new private dry-run inventory file, then exit.",
    )
    parser.add_argument(
        "--retention-report", type=Path, help="Save a new private dry-run retention report."
    )
    parser.add_argument(
        "--retention-apply",
        type=Path,
        help="Apply a previously saved dry-run report with live reference checks.",
    )
    args = parser.parse_args()
    if (
        sum(bool(v) for v in (args.reconcile_report, args.retention_report, args.retention_apply))
        > 1
    ):
        parser.error("Select one maintenance operation.")
    asyncio.run(main(args.reconcile_report, args.retention_report, args.retention_apply))
