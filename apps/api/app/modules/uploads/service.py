"""Private capture lifecycle, immutable provenance and the durable verification queue.

All domain transactions acquire the current user before project/job/asset rows. This
matches identity claiming, so a claim cannot race a successful publication or PUT.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from penforge_contracts import AssetVerifyJob, StrokeDocument, TemplateManifest, content_hash
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError, TimeoutError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.adapters.auth.base import Principal
from app.adapters.storage.base import ObjectMetadata, ObjectMissing, ObjectStorage, StorageError
from app.core.errors import ApiError
from app.modules.identity.service import IdentityService
from app.modules.uploads.repository import UploadsRepository
from app.modules.uploads.schemas import (
    AssetSampleCreate,
    SampleCreate,
    StrokeSampleCreate,
    TemplateReference,
    UploadInit,
)


@dataclass(frozen=True)
class VerifiedAsset:
    sha256: str
    size_bytes: int
    mime_type: str
    width_px: int
    height_px: int
    derivative_key: str
    derivative_size_bytes: int
    derivative_sha256: str
    derivative_width_px: int
    derivative_height_px: int


@dataclass(frozen=True)
class PublishResult:
    accepted: bool
    state: str | None = None
    duplicate_of: UUID | None = None
    cleanup_keys: tuple[str, ...] = ()


class UploadsService:
    def __init__(
        self,
        engine: AsyncEngine,
        identity_service: IdentityService,
        storage: ObjectStorage,
        *,
        templates: Iterable[TemplateManifest] = (),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        ids: Callable[[], UUID] = uuid4,
        upload_issuance_enabled: bool = True,
        upload_max_bytes: int = 20 * 1024 * 1024,
        upload_max_pixels: int = 50_000_000,
        upload_max_dimension: int = 12000,
        upload_files_per_project: int = 200,
        pending_upload_ttl_seconds: int = 86400,
        signed_upload_ttl_seconds: int = 900,
        signed_download_ttl_seconds: int = 600,
        derivative_max_bytes: int = 4 * 1024 * 1024,
        derivative_max_dimension: int = 1000,
        idempotency_ttl_seconds: int = 86400,
        job_max_attempts: int = 3,
    ):
        self.engine, self.identity_service, self.storage = engine, identity_service, storage
        self.clock, self.ids = clock, ids
        self.upload_issuance_enabled = upload_issuance_enabled
        self.upload_max_bytes, self.upload_max_pixels = upload_max_bytes, upload_max_pixels
        self.upload_max_dimension = upload_max_dimension
        self.upload_files_per_project = upload_files_per_project
        self.pending_upload_ttl_seconds = pending_upload_ttl_seconds
        self.signed_upload_ttl_seconds, self.signed_download_ttl_seconds = (
            signed_upload_ttl_seconds,
            signed_download_ttl_seconds,
        )
        self.derivative_max_bytes, self.derivative_max_dimension = (
            derivative_max_bytes,
            derivative_max_dimension,
        )
        self.idempotency_ttl_seconds, self.job_max_attempts = (
            idempotency_ttl_seconds,
            job_max_attempts,
        )
        self.templates: dict[tuple[str, str, int], tuple[TemplateManifest, str]] = {}
        for manifest in templates:
            key = (manifest.template_id, manifest.template_version, manifest.page.index)
            value = (manifest, content_hash(manifest.model_dump(mode="json")))
            if key in self.templates and self.templates[key][1] != value[1]:
                raise ValueError("template_identity_has_conflicting_digest")
            self.templates[key] = value

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[AsyncConnection]:
        try:
            async with self.engine.begin() as connection:
                yield connection
        except (OperationalError, InterfaceError, TimeoutError):
            raise ApiError("dependency_unavailable", 503) from None
        except StorageError:
            raise ApiError("dependency_unavailable", 503) from None

    async def _owner(self, connection: AsyncConnection, principal: Principal) -> Any:
        return await self.identity_service.resolve_in_transaction(
            connection, principal, self.clock()
        )

    async def _project(self, repo: UploadsRepository, owner_id: UUID, project_id: UUID) -> Any:
        row = await repo.project(owner_id, project_id)
        if row is None:
            raise ApiError("not_found", 404)
        if row["status"] == "deleting":
            raise ApiError("state_conflict", 409)
        return row

    def _template(
        self, template: TemplateReference | None, source_type: str, script: str
    ) -> dict[str, Any]:
        if source_type.startswith("template_") != (template is not None):
            raise ApiError("template_mismatch", 400)
        if template is None:
            return {
                "template_id": None,
                "template_version": None,
                "template_manifest_sha256": None,
                "page_index": 0,
            }
        pinned = self.templates.get(
            (template.template_id, template.template_version, template.page_index)
        )
        if (
            pinned is None
            or pinned[1] != template.manifest_sha256
            or pinned[0].script_code != script
        ):
            raise ApiError("template_mismatch", 400)
        return {
            "template_id": template.template_id,
            "template_version": template.template_version,
            "template_manifest_sha256": bytes.fromhex(
                template.manifest_sha256.removeprefix("sha256:")
            ),
            "page_index": template.page_index,
        }

    def _validate_pinned(self, row: Any, script: str) -> None:
        if row["template_id"] is None:
            return
        reference = TemplateReference(
            template_id=row["template_id"],
            template_version=row["template_version"],
            manifest_sha256="sha256:" + row["template_manifest_sha256"].hex(),
            page_index=row["page_index"],
        )
        self._template(reference, row["source_type"], script)

    @staticmethod
    def _asset_dto(row: Any) -> dict[str, Any]:
        return {
            "asset_id": str(row["id"]),
            "project_id": str(row["project_id"]),
            "source_type": row["source_type"],
            "mime_type": row["mime_type"],
            "size_bytes": row["size_bytes"],
            "sha256": "sha256:" + row["sha256"].hex() if row["sha256"] else None,
            "state": row["state"],
            "reject_reason": row["reject_reason"],
            "duplicate_of": str(row["duplicate_of"]) if row["duplicate_of"] else None,
            "job_id": str(row["verify_job_id"]) if row["verify_job_id"] else None,
            "expires_at": row["expires_at"],
            "width_px": row["width_px"],
            "height_px": row["height_px"],
            "document_hash": "sha256:" + row["document_hash"].hex()
            if row["document_hash"]
            else None,
        }

    @staticmethod
    def _sample_dto(row: Any, document: StrokeDocument | None = None) -> dict[str, Any]:
        template = None
        if row["template_id"]:
            template = {
                "template_id": row["template_id"],
                "template_version": row["template_version"],
                "manifest_sha256": "sha256:" + row["template_manifest_sha256"].hex(),
                "page_index": row["page_index"],
            }
        return {
            "sample_id": str(row["id"]),
            "project_id": str(row["project_id"]),
            "asset_id": str(row["asset_id"]),
            "source_type": row["source_type"],
            "source_sha256": "sha256:" + row["source_sha256"].hex(),
            "document_hash": "sha256:" + row["document_hash"].hex()
            if row["document_hash"]
            else None,
            "template": template,
            "state": row["state"],
            "quality": row["quality"],
            "quality_reasons": row["quality_reasons"],
            "capture_size_px": document.capture_size_px.model_dump()
            if document and document.capture_size_px
            else None,
            "timing": document.timing if document else None,
        }

    @staticmethod
    def _key(key: str) -> None:
        if not 1 <= len(key) <= 255 or any(ord(ch) < 33 for ch in key):
            raise ApiError("validation_error", 400, details={"fields": ["/header/idempotency-key"]})

    async def _receipt(
        self, connection: AsyncConnection, owner_id: UUID, key: str, path: str, body: dict[str, Any]
    ) -> dict[str, Any] | None:
        # Kept in this module's repository boundary, independent of project services.
        self._key(key)
        digest = hashlib.sha256(
            ("POST\n" + path + "\n").encode()
            + json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).digest()
        return await UploadsRepository(connection).receipt(
            owner_id,
            key,
            digest,
            self.clock(),
            self.clock() + timedelta(seconds=self.idempotency_ttl_seconds),
        )

    async def _save_receipt(
        self,
        connection: AsyncConnection,
        owner_id: UUID,
        key: str,
        body: dict[str, Any],
        status: int = 201,
    ) -> None:
        await UploadsRepository(connection).save_receipt(
            owner_id, key, json.loads(json.dumps(body, default=str)), status
        )

    async def _reserve(
        self, repo: UploadsRepository, owner: UUID, asset_id: UUID, amount: int, expires: datetime
    ) -> None:
        balances = await repo.storage_balances(owner, self.clock())
        if not any(r["scope"] == "user" for r in balances) or not any(
            r["scope"] == "global" for r in balances
        ):
            raise ApiError("quota_unconfigured", 503)
        if any(r["committed"] + r["reserved"] + amount > r["limit_amount"] for r in balances):
            raise ApiError("quota_exceeded", 429)
        await repo.reserve(owner, asset_id, amount, expires, balances, self.ids, self.clock())

    async def _issue(self, repo: UploadsRepository, row: Any, method: str) -> dict[str, Any]:
        if method == "PUT":
            capability = self.storage.create_upload(
                row["storage_key"],
                sha256=row["declared_sha256"].hex() if row["declared_sha256"] else None,
                size_bytes=row["size_bytes"],
                max_bytes=self.upload_max_bytes,
                ttl_seconds=self.signed_upload_ttl_seconds,
            )
            prefix = "upload"
        else:
            capability = self.storage.get_download_url(
                row["storage_key"], ttl_seconds=self.signed_download_ttl_seconds
            )
            prefix = "download"
            await repo.update_asset(row["owner_user_id"], row["id"], last_used_at=self.clock())
        await repo.update_asset(
            row["owner_user_id"],
            row["id"],
            **{
                prefix + "_capability_hash": hashlib.sha256(capability.token.encode()).digest(),
                prefix + "_issued_owner_id": row["owner_user_id"],
            },
        )
        return {
            "method": method,
            "url": "/api/v1/storage/objects/" + capability.token,
            "headers": {"Content-Type": row["mime_type"]} if method == "PUT" else {},
            "expires_at": datetime.fromtimestamp(capability.expires_at, UTC),
            "resumable": False,
        }

    async def initialize(
        self, principal: Principal, project_id: UUID, *, idempotency_key: str, body: UploadInit
    ) -> tuple[bool, dict[str, Any]]:
        async with self._transaction() as connection:
            user = await self._owner(connection, principal)
            repo = UploadsRepository(connection)
            project = await self._project(repo, user["id"], project_id)
            replay = await self._receipt(
                connection,
                user["id"],
                idempotency_key,
                f"/api/v1/projects/{project_id}/uploads",
                body.model_dump(mode="json"),
            )
            if replay is not None:
                return True, replay
            if not self.upload_issuance_enabled:
                raise ApiError("upload_issuance_disabled", 503)
            if body.declared_size > self.upload_max_bytes:
                raise ApiError("payload_too_large", 413)
            if await repo.asset_count(user["id"], project_id) >= self.upload_files_per_project:
                raise ApiError("quota_exceeded", 429)
            template = self._template(body.template, body.source_type, project["script_code"])
            asset_id = self.ids()
            expires = self.clock() + timedelta(seconds=self.pending_upload_ttl_seconds)
            row = await repo.insert_asset(
                {
                    "id": asset_id,
                    "owner_user_id": user["id"],
                    "project_id": project_id,
                    "kind": "original_upload",
                    "storage_backend": "local_fs",
                    "storage_key": f"u/{user['id']}/p/{project_id}/orig/{asset_id}",
                    "mime_type": body.declared_mime,
                    "size_bytes": body.declared_size,
                    "source_type": body.source_type,
                    "declared_sha256": bytes.fromhex(body.sha256.removeprefix("sha256:"))
                    if body.sha256
                    else None,
                    "state": "pending_upload",
                    "retention_class": "original",
                    "expires_at": expires,
                    "created_at": self.clock(),
                    **template,
                }
            )
            await self._reserve(
                repo, user["id"], asset_id, body.declared_size + self.derivative_max_bytes, expires
            )
            response = {"asset_id": str(asset_id), "upload": await self._issue(repo, row, "PUT")}
            await self._save_receipt(connection, user["id"], idempotency_key, response)
            return False, json.loads(json.dumps(response, default=str))

    async def renew_upload(self, principal: Principal, asset_id: UUID) -> dict[str, Any]:
        async with self._transaction() as connection:
            user = await self._owner(connection, principal)
            repo = UploadsRepository(connection)
            row = await self._owned_asset(repo, user["id"], asset_id)
            if row["state"] != "pending_upload":
                raise ApiError("state_conflict", 409)
            if row["expires_at"] <= self.clock():
                raise ApiError("upload_expired", 409)
            return {"asset_id": str(asset_id), "upload": await self._issue(repo, row, "PUT")}

    async def _owned_asset(self, repo: UploadsRepository, owner_id: UUID, asset_id: UUID) -> Any:
        row = await repo.asset(owner_id, asset_id)
        if row is None:
            raise ApiError("not_found", 404)
        await self._project(repo, owner_id, row["project_id"])
        return row

    async def get(self, principal: Principal, asset_id: UUID) -> dict[str, Any]:
        async with self._transaction() as connection:
            user = await self._owner(connection, principal)
            return self._asset_dto(
                await self._owned_asset(UploadsRepository(connection), user["id"], asset_id)
            )

    async def complete(self, principal: Principal, asset_id: UUID) -> dict[str, Any]:
        async with self._transaction() as connection:
            user = await self._owner(connection, principal)
            repo = UploadsRepository(connection)
            row = await self._owned_asset(repo, user["id"], asset_id)
            if row["verify_job_id"] is not None:
                return {
                    "asset": self._asset_dto(row),
                    "job_id": str(row["verify_job_id"]),
                    "poll_url": f"/api/v1/jobs/{row['verify_job_id']}",
                }
            if row["state"] != "pending_upload":
                raise ApiError("state_conflict", 409)
            if row["expires_at"] <= self.clock():
                raise ApiError("upload_expired", 409)
            try:
                metadata = await asyncio.to_thread(self.storage.get_metadata, row["storage_key"])
            except ObjectMissing:
                raise ApiError("object_missing", 409) from None
            if (
                metadata.size_bytes != row["size_bytes"]
                or metadata.size_bytes > self.upload_max_bytes
            ):
                raise ApiError("upload_mismatch", 400)
            from app.modules.jobs.service import admit, reserve_resources

            amounts = {"compute_seconds": 60 * self.job_max_attempts} | await admit(
                connection, "asset_verify"
            )
            job_id = self.ids()
            await repo.insert_job(
                {
                    "id": job_id,
                    "owner_user_id": user["id"],
                    "project_id": row["project_id"],
                    "job_type": "asset_verify",
                    "payload_schema_version": 1,
                    "payload": {"schema_version": 1, "asset_id": str(asset_id)},
                    "idempotency_key": f"asset-verify:{asset_id}",
                    "state": "queued",
                    "max_attempts": self.job_max_attempts,
                    "stage": "queued",
                    "run_after": await repo.now(),
                    "created_at": self.clock(),
                    "checkpoint": {"completed_units": 0, "total_units": 4},
                }
            )
            row = await repo.update_asset(
                user["id"],
                asset_id,
                state="uploaded",
                verify_job_id=job_id,
                upload_capability_hash=None,
            )
            await repo.attach_reservations(user["id"], asset_id, job_id)
            await reserve_resources(connection, user["id"], job_id, amounts, await repo.now())
            await repo.event(
                job_id,
                self.clock(),
                to_state="queued",
                stage="queued",
                metadata={"completed_units": 0, "total_units": 4},
            )
            return {
                "asset": self._asset_dto(row),
                "job_id": str(job_id),
                "poll_url": f"/api/v1/jobs/{job_id}",
            }

    async def _source(self, repo: UploadsRepository, owner_id: UUID, row: Any) -> Any:
        if row["state"] == "ready":
            return row
        if (
            row["state"] == "rejected"
            and row["reject_reason"] == "duplicate"
            and row["duplicate_of"]
        ):
            canonical = await repo.asset(owner_id, row["duplicate_of"])
            if (
                canonical is not None
                and canonical["state"] == "ready"
                and canonical["sha256"] == row["sha256"]
            ):
                await self._project(repo, owner_id, canonical["project_id"])
                return canonical
        raise ApiError("state_conflict", 409)

    async def download(self, principal: Principal, asset_id: UUID) -> dict[str, Any]:
        async with self._transaction() as connection:
            user = await self._owner(connection, principal)
            repo = UploadsRepository(connection)
            row = await self._owned_asset(repo, user["id"], asset_id)
            return await self._issue(repo, await self._source(repo, user["id"], row), "GET")

    async def _capability(self, connection: AsyncConnection, token: str, method: str) -> Any:
        try:
            claims = self.storage.validate_capability(token, method)
        except StorageError:
            raise ApiError("not_found", 404) from None
        repo = UploadsRepository(connection)
        candidate = await repo.asset_for_capability(claims.key)
        if candidate is None:
            raise ApiError("not_found", 404)
        user = await repo.user(candidate["owner_user_id"])
        if (
            user is None
            or user["account_state"] != "active"
            or user["merged_into_user_id"] is not None
        ):
            raise ApiError("not_found", 404)
        row = await self._owned_asset(repo, user["id"], candidate["id"])
        prefix = "upload" if method == "PUT" else "download"
        if (
            row[prefix + "_issued_owner_id"] != user["id"]
            or row[prefix + "_capability_hash"] != hashlib.sha256(token.encode()).digest()
        ):
            raise ApiError("not_found", 404)
        if method == "PUT" and (
            row["state"] != "pending_upload" or row["expires_at"] <= self.clock()
        ):
            raise ApiError("not_found", 404)
        if method == "GET" and row["state"] != "ready":
            raise ApiError("not_found", 404)
        return row

    async def authorize_capability(self, principal: Principal, token: str, method: str) -> None:
        """A signed URL also requires the currently verified account owner."""
        async with self._transaction() as connection:
            user = await self._owner(connection, principal)
            row = await self._capability(connection, token, method)
            if row["owner_user_id"] != user["id"]:
                raise ApiError("not_found", 404)

    async def put_capability(self, token: str, data: bytes) -> ObjectMetadata:
        async with self._transaction() as connection:
            row = await self._capability(connection, token, "PUT")
            if len(data) != row["size_bytes"] or len(data) > self.upload_max_bytes:
                raise ApiError("upload_mismatch", 400)
            try:
                return await asyncio.to_thread(self.storage.put, token, data)
            except StorageError:
                raise ApiError("upload_mismatch", 400) from None

    async def read_capability(self, token: str) -> tuple[bytes, str]:
        async with self._transaction() as connection:
            row = await self._capability(connection, token, "GET")
            data = await asyncio.to_thread(self.storage.read, token)
            return data, row["mime_type"]

    async def register_sample(
        self, principal: Principal, project_id: UUID, *, idempotency_key: str, body: SampleCreate
    ) -> tuple[bool, dict[str, Any]]:
        async with self._transaction() as connection:
            user = await self._owner(connection, principal)
            repo = UploadsRepository(connection)
            project = await self._project(repo, user["id"], project_id)
            replay = await self._receipt(
                connection,
                user["id"],
                idempotency_key,
                f"/api/v1/projects/{project_id}/samples",
                body.model_dump(mode="json"),
            )
            if replay is not None:
                return True, replay
            if isinstance(body, AssetSampleCreate):
                row = await self._owned_asset(repo, user["id"], body.asset_id)
                if row["project_id"] != project_id:
                    raise ApiError("not_found", 404)
                source = await self._source(repo, user["id"], row)
                template = self._template(body.template, body.source_type, project["script_code"])
                if row["source_type"] != body.source_type or any(
                    row[k] != v for k, v in template.items()
                ):
                    raise ApiError("template_mismatch", 400)
                if source["sha256"] != row["sha256"]:
                    raise ApiError("upload_mismatch", 400)
            else:
                row = await self._store_strokes(repo, user["id"], project, body)
            sample = await repo.insert_sample(
                {
                    "id": self.ids(),
                    "project_id": project_id,
                    "asset_id": row["id"],
                    "source_type": row["source_type"],
                    "template_id": row["template_id"],
                    "template_version": row["template_version"],
                    "page_index": row["page_index"] or 0,
                    "source_sha256": row["sha256"],
                    "document_hash": row["document_hash"],
                    "template_manifest_sha256": row["template_manifest_sha256"],
                    "prompt_context": body.prompt_context.model_dump(mode="json")
                    if body.prompt_context
                    else None,
                    "created_at": self.clock(),
                }
            )
            response = self._sample_dto(
                sample, body.stroke_document if isinstance(body, StrokeSampleCreate) else None
            )
            await self._save_receipt(connection, user["id"], idempotency_key, response)
            return False, response

    async def _store_strokes(
        self, repo: UploadsRepository, owner_id: UUID, project: Any, body: StrokeSampleCreate
    ) -> Any:
        document = body.stroke_document.model_dump(mode="json")
        if body.template is not None:
            raise ApiError("template_mismatch", 400)
        if body.stroke_document.parent_source_hash is not None:
            if body.parent_asset_id is None:
                raise ApiError("validation_error", 400)
            parent = await repo.asset(owner_id, body.parent_asset_id)
            if (
                parent is None
                or parent["project_id"] != project["id"]
                or parent["state"] != "ready"
                or parent["kind"] != "raw_strokes"
            ):
                raise ApiError("not_found", 404)
            if (
                parent["document_hash"] is None
                or "sha256:" + parent["document_hash"].hex()
                != body.stroke_document.parent_source_hash
            ):
                raise ApiError("upload_mismatch", 400)
        elif body.parent_asset_id is not None:
            raise ApiError("validation_error", 400)
        if await repo.asset_count(owner_id, project["id"]) >= self.upload_files_per_project:
            raise ApiError("quota_exceeded", 429)
        data = json.dumps(
            document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        if len(data) > self.upload_max_bytes:
            raise ApiError("payload_too_large", 413)
        asset_id = self.ids()
        now = self.clock()
        digest = hashlib.sha256(data).digest()
        key = f"u/{owner_id}/p/{project['id']}/strokes/{asset_id}.json"
        row = await repo.insert_asset(
            {
                "id": asset_id,
                "owner_user_id": owner_id,
                "project_id": project["id"],
                "kind": "raw_strokes",
                "storage_backend": "local_fs",
                "storage_key": key,
                "mime_type": "application/json",
                "size_bytes": len(data),
                "sha256": digest,
                "document_hash": bytes.fromhex(content_hash(document).removeprefix("sha256:")),
                "source_type": "canvas_drawing",
                "width_px": body.stroke_document.capture_size_px.width
                if body.stroke_document.capture_size_px
                else None,
                "height_px": body.stroke_document.capture_size_px.height
                if body.stroke_document.capture_size_px
                else None,
                "parent_asset_id": body.parent_asset_id,
                "state": "ready",
                "retention_class": "original",
                "verified_at": now,
                "created_at": now,
                "page_index": 0,
            }
        )
        await self._reserve(
            repo,
            owner_id,
            asset_id,
            len(data),
            now + timedelta(seconds=self.pending_upload_ttl_seconds),
        )
        await asyncio.to_thread(self.storage.put_immutable, key, data)
        await repo.settle(owner_id, asset_id, len(data), now)
        return row

    async def list_samples(self, principal: Principal, project_id: UUID) -> dict[str, Any]:
        async with self._transaction() as connection:
            user = await self._owner(connection, principal)
            repo = UploadsRepository(connection)
            await self._project(repo, user["id"], project_id)
            rows = await repo.samples(user["id"], project_id)
            items = []
            for row in rows:
                if row["source_sha256"] is None:
                    continue
                document = None
                if row["source_type"] == "canvas_drawing":
                    asset = await self._owned_asset(repo, user["id"], row["asset_id"])
                    data = await asyncio.to_thread(
                        self.storage.read_object,
                        asset["storage_key"],
                        max_bytes=self.upload_max_bytes,
                    )
                    if hashlib.sha256(data).digest() != row["source_sha256"]:
                        raise ApiError("upload_mismatch", 400)
                    document = StrokeDocument.model_validate_json(data)
                items.append(self._sample_dto(row, document))
            return {"items": items}

    async def stage_worker_artifact(
        self,
        connection: AsyncConnection,
        owner_id: UUID,
        project_id: UUID,
        *,
        artifact_id: UUID,
        storage_key: str,
        size_bytes: int,
        sha256: bytes,
        kind: str,
        mime_type: str,
        worker_job_id: UUID | None = None,
    ) -> None:
        """Reserve and register an attempt's output before any storage write.

        The caller owns a fenced job transaction. A crash leaves inventoried pending
        assets, never an uncounted blob. Publication and cleanup settle each once.
        """
        if kind not in {
            "vector",
            "segment",
            "preprocessed",
            "validation_report",
            "checkpoint",
            "export",
        }:
            raise ValueError("invalid_worker_artifact_kind")
        if not 0 < size_bytes <= 32 * 1024 * 1024 or len(sha256) != 32:
            raise ValueError("invalid_worker_artifact_size")
        import sqlalchemy as sa

        context = await connection.scalar(
            sa.select(sa.func.current_setting("penforge.worker_fence", True))
        )
        if worker_job_id is None and context:
            worker_job_id = UUID(json.loads(context)["job_id"])
        repo = UploadsRepository(connection)
        await self._project(repo, owner_id, project_id)
        now = await repo.now()
        await repo.insert_asset(
            {
                "id": artifact_id,
                "worker_job_id": worker_job_id,
                "owner_user_id": owner_id,
                "project_id": project_id,
                "kind": kind,
                "storage_backend": "local_fs",
                "storage_key": storage_key,
                "mime_type": mime_type,
                "size_bytes": size_bytes,
                "sha256": sha256,
                "state": "pending_upload",
                "retention_class": "export"
                if kind in {"export", "validation_report"}
                else "vector"
                if kind == "vector"
                else "derived",
                "expires_at": now + timedelta(seconds=self.pending_upload_ttl_seconds),
            }
        )
        await self._reserve(
            repo,
            owner_id,
            artifact_id,
            size_bytes,
            now + timedelta(seconds=self.pending_upload_ttl_seconds),
        )

    async def publish_worker_artifact(
        self,
        connection: AsyncConnection,
        owner_id: UUID,
        artifact_id: UUID,
    ) -> None:
        repo = UploadsRepository(connection)
        row = await self._owned_asset(repo, owner_id, artifact_id)
        if row["state"] != "pending_upload" or row["kind"] == "original_upload":
            raise ApiError("state_conflict", 409)
        metadata = await asyncio.to_thread(
            self.storage.verify_upload,
            row["storage_key"],
            size_bytes=row["size_bytes"],
            sha256=row["sha256"].hex(),
        )
        if metadata.sha256 != row["sha256"].hex():
            raise ApiError("upload_mismatch", 400)
        for state in ("uploaded", "verifying"):
            await repo.update_asset(owner_id, artifact_id, state=state)
        now = await repo.now()
        await repo.update_asset(
            owner_id, artifact_id, state="ready", verified_at=now, expires_at=None
        )
        await repo.settle(owner_id, artifact_id, row["size_bytes"], now)

    async def discard_worker_artifacts(self, owner_id: UUID, artifact_ids: list[UUID]) -> None:
        """Only pending attempt outputs are eligible; ready results are retained."""
        eligible: list[UUID] = []
        async with self._transaction() as connection:
            repo = UploadsRepository(connection)
            owner = await repo.user(owner_id)
            if owner is None:
                return
            for asset_id in artifact_ids:
                row = await repo.asset(owner_id, asset_id)
                if (
                    row is not None
                    and row["state"] == "pending_upload"
                    and row["kind"]
                    in {
                        "vector",
                        "segment",
                        "preprocessed",
                        "validation_report",
                        "checkpoint",
                        "export",
                    }
                ):
                    await repo.update_asset(owner_id, asset_id, state="expired")
                    eligible.append(asset_id)
        for asset_id in eligible:
            await self.cleanup_asset(asset_id)

    async def settle_artifact_in_transaction(self, connection, owner, asset, actual):
        repo = UploadsRepository(connection)
        await repo.settle(owner, asset, actual, await repo.now())

    async def get_job(self, principal: Principal, job_id: UUID) -> dict[str, Any]:
        async with self._transaction() as connection:
            user = await self._owner(connection, principal)
            repo = UploadsRepository(connection)
            job = await repo.job(user["id"], job_id)
            if job is None or job["job_type"] != "asset_verify":
                raise ApiError("not_found", 404)
            await self._project(repo, user["id"], job["project_id"])
            payload = AssetVerifyJob.model_validate(job["payload"])
            return {
                "job_id": str(job_id),
                "asset_id": str(payload.asset_id),
                "state": job["state"],
                "stage": job["stage"],
                "attempts": job["attempts"],
                "progress": {
                    "completed_units": job["checkpoint"].get("completed_units", 0),
                    "total_units": job["checkpoint"].get("total_units", 4),
                    "basis": "verified_checks",
                },
                "eta_seconds": None,
                "error_code": job["error_code"],
                "result_asset_id": str(payload.asset_id) if job["state"] == "succeeded" else None,
            }

    async def cleanup_asset(self, asset_id: UUID) -> bool:
        async with self._transaction() as connection:
            repo = UploadsRepository(connection)
            candidate = await repo.asset_by_id_internal(asset_id)
            if candidate is None:
                return False
            await repo.user(candidate["owner_user_id"])
            row = await repo.asset(candidate["owner_user_id"], asset_id)
            if (
                row is None
                or row["state"] not in ("expired", "rejected")
                or row["cleanup_completed_at"] is not None
            ):
                return False
            cleanup_keys = [row["storage_key"]]
            if row["kind"] == "original_upload":
                prefix = row["storage_key"].rsplit("/orig/", 1)[0]
                job = (
                    await repo.job(row["owner_user_id"], row["verify_job_id"])
                    if row["verify_job_id"]
                    else None
                )
                attempts = job["max_attempts"] if job is not None else self.job_max_attempts
                cleanup_keys.extend(
                    f"{prefix}/thumb/{row['id']}/a{attempt}.png"
                    for attempt in range(1, attempts + 1)
                )
            for key in cleanup_keys:
                await asyncio.to_thread(self.storage.delete_object, key)
                try:
                    await asyncio.to_thread(self.storage.get_metadata, key)
                except ObjectMissing:
                    pass
                else:
                    raise StorageError("cleanup_object_still_exists")
            await repo.settle(
                row["owner_user_id"],
                asset_id,
                None,
                self.clock(),
                expired=row["state"] == "expired",
            )
            await repo.update_asset(
                row["owner_user_id"],
                asset_id,
                cleanup_completed_at=self.clock(),
                upload_capability_hash=None,
                download_capability_hash=None,
            )
            return True

    async def reap_abandoned(self) -> int:
        async with self._transaction() as connection:
            candidates = await UploadsRepository(connection).cleanup_candidates(self.clock())
        count = 0
        for candidate in candidates:
            async with self._transaction() as connection:
                repo = UploadsRepository(connection)
                await repo.user(candidate["owner_user_id"])
                row = await repo.asset(candidate["owner_user_id"], candidate["id"])
                if row is None:
                    continue
                if row["state"] == "pending_upload" and row["expires_at"] <= self.clock():
                    await repo.update_asset(
                        row["owner_user_id"],
                        row["id"],
                        state="expired",
                        upload_capability_hash=None,
                    )
            count += int(await self.cleanup_asset(candidate["id"]))
        return count

    async def reconcile(self) -> dict[str, Any]:
        """Always a dry run: orphan deletion requires separate reviewed maintenance."""
        async with self._transaction() as connection:
            inventory = await UploadsRepository(connection).inventory()
        present = {entry.key for entry in await asyncio.to_thread(self.storage.list_objects)}
        known = {row["storage_key"] for row in inventory}
        return {
            "dry_run": True,
            "orphan_keys": sorted(present - known),
            "missing_asset_ids": [
                str(row["id"])
                for row in inventory
                if row["state"] in ("ready", "uploaded", "verifying")
                and row["storage_key"] not in present
            ],
            "objects_examined": len(present),
            "assets_examined": len(inventory),
        }


class QueueService:
    """The shared PostgreSQL lease interface; only asset_verify is emitted in Phase 03."""

    def __init__(
        self,
        engine: AsyncEngine,
        uploads: UploadsService,
        *,
        lease_seconds: int = 60,
        max_attempts: int = 3,
        retry_backoff_seconds: int = 30,
    ):
        self.engine, self.uploads = engine, uploads
        self.lease_seconds, self.max_attempts, self.retry_backoff_seconds = (
            lease_seconds,
            max_attempts,
            retry_backoff_seconds,
        )

    async def claim(
        self,
        worker_id: str,
        *,
        supported_types: tuple[str, ...] = ("asset_verify",),
        supported_versions: tuple[int, ...] = (1,),
        worker_mem_mb: int = 512,
        job_id: UUID | None = None,
    ) -> dict[str, Any] | None:
        if not worker_id or len(worker_id) > 128:
            raise ValueError("invalid_worker_id")
        from app.modules.jobs.service import queue_candidates

        async with self.uploads._transaction() as connection:
            candidates = await queue_candidates(
                connection, supported_types, supported_versions, worker_mem_mb, job_id
            )
            for candidate in candidates:
                repo = UploadsRepository(connection)
                owner = await repo.user(candidate["owner_user_id"])
                if (
                    owner is None
                    or owner["account_state"] != "active"
                    or owner["merged_into_user_id"] is not None
                ):
                    continue
                project = await repo.project(owner["id"], candidate["project_id"])
                if project is None or project["status"] == "deleting":
                    continue
                job = await repo.lock_candidate(owner["id"], candidate["id"])
                if job is None:
                    continue
                try:
                    payload = AssetVerifyJob.model_validate(job["payload"])
                except ValidationError:
                    # Payload skew is filtered before claim; malformed accepted schema
                    # is isolated permanently instead of breaking every worker loop.
                    now = await repo.now()
                    job = await repo.update_job(
                        owner["id"],
                        job["id"],
                        state="running",
                        attempts=job["attempts"] + 1,
                        lease_holder=worker_id,
                        lease_expires_at=now + timedelta(seconds=self.lease_seconds),
                    )
                    await repo.update_job(
                        owner["id"],
                        job["id"],
                        state="failed",
                        error_code="unsupported_payload",
                        finished_at=now,
                        lease_holder=None,
                        lease_expires_at=None,
                    )
                    rejected = await repo.asset_for_job(owner["id"], job["id"])
                    if rejected is not None and rejected["state"] in ("uploaded", "verifying"):
                        if rejected["state"] == "uploaded":
                            await repo.update_asset(owner["id"], rejected["id"], state="verifying")
                        await repo.update_asset(
                            owner["id"],
                            rejected["id"],
                            state="rejected",
                            reject_reason="unsupported_payload",
                        )
                    await repo.event(
                        job["id"],
                        now,
                        kind="error",
                        to_state="failed",
                        metadata={"error_code": "unsupported_payload"},
                    )
                    continue
                asset = await repo.asset(owner["id"], UUID(payload.asset_id))
                if (
                    asset is None
                    or asset["project_id"] != project["id"]
                    or asset["verify_job_id"] != job["id"]
                    or asset["state"] not in ("uploaded", "verifying")
                ):
                    continue
                now = await repo.now()
                claimed = await repo.update_job(
                    owner["id"],
                    job["id"],
                    state="running",
                    attempts=job["attempts"] + 1,
                    lease_holder=worker_id,
                    lease_expires_at=now + timedelta(seconds=self.lease_seconds),
                    last_heartbeat_at=now,
                    started_at=job["started_at"] or now,
                    stage="hash",
                    checkpoint=dict(job["checkpoint"]) | {"completed_units": 0, "total_units": 4},
                )
                if asset["state"] == "uploaded":
                    asset = await repo.update_asset(owner["id"], asset["id"], state="verifying")
                await repo.event(
                    job["id"],
                    now,
                    from_state="queued",
                    to_state="running",
                    stage="hash",
                    metadata={
                        "attempt": claimed["attempts"],
                        "completed_units": 0,
                        "total_units": 4,
                    },
                )
                return dict(claimed) | {"_asset": dict(asset), "_project": dict(project)}
        return None

    async def _fenced(
        self, repo: UploadsRepository, job_id: UUID, worker_id: str, attempt: int, owner_id: UUID
    ) -> tuple[Any, Any, Any, datetime] | None:
        owner = await repo.user(owner_id)
        if (
            owner is None
            or owner["account_state"] != "active"
            or owner["merged_into_user_id"] is not None
        ):
            return None
        candidate = await repo.job(owner_id, job_id)
        if candidate is None:
            return None
        project = await repo.project(owner_id, candidate["project_id"])
        if project is None or project["status"] == "deleting":
            return None
        now = await repo.now()
        if (
            candidate["state"] != "running"
            or candidate["cancel_requested"]
            or candidate["attempts"] != attempt
            or candidate["lease_holder"] != worker_id
            or candidate["lease_expires_at"] is None
            or candidate["lease_expires_at"] <= now
        ):
            return None
        payload = AssetVerifyJob.model_validate(candidate["payload"])
        asset = await repo.asset(owner_id, UUID(payload.asset_id))
        if (
            asset is None
            or asset["project_id"] != project["id"]
            or asset["verify_job_id"] != job_id
            or asset["state"] != "verifying"
        ):
            return None
        from app.modules.jobs.service import set_worker_fence

        await set_worker_fence(repo.connection, candidate, worker_id)
        return candidate, asset, project, now

    async def heartbeat(
        self,
        job_id: UUID,
        worker_id: str,
        attempt: int,
        owner_id: UUID,
        *,
        stage: str,
        completed_units: int,
        total_units: int = 4,
    ) -> bool:
        if (
            stage not in ("hash", "decode", "derivative", "publish")
            or not 0 <= completed_units <= total_units
            or total_units != 4
        ):
            raise ValueError("invalid_verification_progress")
        async with self.uploads._transaction() as connection:
            repo = UploadsRepository(connection)
            fenced = await self._fenced(repo, job_id, worker_id, attempt, owner_id)
            if fenced is None:
                return False
            job, asset, project, now = fenced
            del asset, project
            await repo.update_job(
                owner_id,
                job_id,
                lease_expires_at=now + timedelta(seconds=self.lease_seconds),
                last_heartbeat_at=now,
                stage=stage,
                progress=completed_units / total_units,
                checkpoint=dict(job["checkpoint"])
                | {"completed_units": completed_units, "total_units": total_units},
            )
            await repo.event(
                job_id,
                now,
                kind="progress",
                stage=stage,
                progress=completed_units / total_units,
                metadata={
                    "completed_units": completed_units,
                    "total_units": total_units,
                    "attempt": job["attempts"],
                },
            )
            return True

    @staticmethod
    def derivative_key(job: dict[str, Any]) -> str:
        asset = job["_asset"]
        return (
            asset["storage_key"].rsplit("/orig/", 1)[0]
            + f"/thumb/{asset['id']}/a{job['attempts']}.png"
        )

    async def stage_derivative(
        self,
        job_id: UUID,
        worker_id: str,
        attempt: int,
        owner_id: UUID,
        *,
        chunks: Iterable[bytes],
        size_bytes: int,
        sha256: str,
    ) -> bool:
        """Keep at most one bounded attempt derivative under its storage reservation.

        Writes take the same owner/job locks as reaping, claiming and publication.
        A lost holder cannot create output after cleanup releases its capacity.
        Crashed earlier outputs are removed before the next attempt stages bytes.
        """
        if not 0 < size_bytes <= self.uploads.derivative_max_bytes:
            raise ApiError("upload_mismatch", 400)
        async with self.uploads._transaction() as connection:
            repo = UploadsRepository(connection)
            fenced = await self._fenced(repo, job_id, worker_id, attempt, owner_id)
            if fenced is None:
                return False
            job, asset, project, now = fenced
            del project, now
            key = self.derivative_key(dict(job) | {"_asset": dict(asset)})
            prefix = asset["storage_key"].rsplit("/orig/", 1)[0]
            for previous in range(1, job["max_attempts"] + 1):
                if previous != attempt:
                    old_key = f"{prefix}/thumb/{asset['id']}/a{previous}.png"
                    await asyncio.to_thread(self.uploads.storage.delete_object, old_key)
            if job["lease_expires_at"] <= await repo.now():
                return False
            await asyncio.to_thread(
                self.uploads.storage.put_immutable_stream,
                key,
                chunks,
                size_bytes=size_bytes,
                sha256=sha256.removeprefix("sha256:"),
            )
            if job["lease_expires_at"] <= await repo.now():
                await asyncio.to_thread(self.uploads.storage.delete_object, key)
                return False
            return True

    async def publish_verified(
        self, job_id: UUID, worker_id: str, attempt: int, owner_id: UUID, result: VerifiedAsset
    ) -> PublishResult:
        try:
            return await self._publish_verified(job_id, worker_id, attempt, owner_id, result)
        except IntegrityError as error:
            if getattr(getattr(error.orig, "diag", None), "message_primary", None) in {
                "asset_publication_fence_lost",
                "worker_asset_fence_lost",
            }:
                return PublishResult(False)
            raise

    async def _publish_verified(
        self, job_id: UUID, worker_id: str, attempt: int, owner_id: UUID, result: VerifiedAsset
    ) -> PublishResult:
        async with self.uploads._transaction() as connection:
            repo = UploadsRepository(connection)
            fenced = await self._fenced(repo, job_id, worker_id, attempt, owner_id)
            if fenced is None:
                return PublishResult(False)
            job, asset, project, now = fenced
            self.uploads._validate_pinned(asset, project["script_code"])
            expected_key = self.derivative_key(dict(job) | {"_asset": dict(asset)})
            if (
                result.derivative_key != expected_key
                or result.size_bytes != asset["size_bytes"]
                or result.mime_type != asset["mime_type"]
                or min(
                    result.width_px,
                    result.height_px,
                    result.derivative_width_px,
                    result.derivative_height_px,
                )
                <= 0
                or max(result.width_px, result.height_px) > self.uploads.upload_max_dimension
                or result.width_px * result.height_px > self.uploads.upload_max_pixels
                or max(result.derivative_width_px, result.derivative_height_px)
                > self.uploads.derivative_max_dimension
                or result.derivative_size_bytes > self.uploads.derivative_max_bytes
            ):
                raise ApiError("upload_mismatch", 400)
            digest = bytes.fromhex(result.sha256.removeprefix("sha256:"))
            if asset["declared_sha256"] is not None and asset["declared_sha256"] != digest:
                raise ApiError("upload_mismatch", 400)
            await asyncio.to_thread(
                self.uploads.storage.verify_upload,
                asset["storage_key"],
                sha256=digest.hex(),
                size_bytes=result.size_bytes,
            )
            await asyncio.to_thread(
                self.uploads.storage.verify_upload,
                result.derivative_key,
                sha256=result.derivative_sha256.removeprefix("sha256:"),
                size_bytes=result.derivative_size_bytes,
            )
            if asset["verify_job_id"] != job_id or job["lease_expires_at"] <= await repo.now():
                return PublishResult(False)
            cleanup: tuple[str, ...]
            # The current owner row lock serializes publication and ownership claims,
            # including identical hashes from simultaneous jobs for this owner.
            canonical = await repo.canonical(owner_id, digest)
            if canonical is not None:
                await repo.update_asset(
                    owner_id,
                    asset["id"],
                    state="rejected",
                    reject_reason="duplicate",
                    duplicate_of=canonical["id"],
                    sha256=digest,
                    width_px=result.width_px,
                    height_px=result.height_px,
                )
                cleanup = (asset["storage_key"], result.derivative_key)
                state = "rejected"
            else:
                await repo.update_asset(
                    owner_id,
                    asset["id"],
                    state="ready",
                    sha256=digest,
                    width_px=result.width_px,
                    height_px=result.height_px,
                    verified_at=now,
                    expires_at=None,
                )
                await repo.insert_asset(
                    {
                        "id": self.uploads.ids(),
                        "owner_user_id": owner_id,
                        "project_id": asset["project_id"],
                        "kind": "thumbnail",
                        "worker_job_id": job_id,
                        "storage_backend": asset["storage_backend"],
                        "storage_key": result.derivative_key,
                        "mime_type": "image/png",
                        "size_bytes": result.derivative_size_bytes,
                        "sha256": bytes.fromhex(result.derivative_sha256.removeprefix("sha256:")),
                        "width_px": result.derivative_width_px,
                        "height_px": result.derivative_height_px,
                        "parent_asset_id": asset["id"],
                        "state": "ready",
                        "retention_class": "derived",
                        "verified_at": now,
                        "created_at": now,
                    }
                )
                await repo.settle(
                    owner_id, asset["id"], result.size_bytes + result.derivative_size_bytes, now
                )
                cleanup = ()
                state = "ready"
            import math

            from app.modules.jobs.service import settle

            await settle(
                connection, job, {"compute_seconds": math.ceil(job["usage"].get("wall_seconds", 0))}
            )
            if not await repo.finalize_fenced(owner_id, job_id, worker_id, attempt, now):
                await connection.rollback()
                return PublishResult(False)
            await repo.event(
                job_id,
                now,
                from_state="running",
                to_state="succeeded",
                stage="verified",
                progress=1,
                metadata={
                    "completed_units": 4,
                    "total_units": 4,
                    "duplicate": canonical is not None,
                },
            )
            return PublishResult(True, state, canonical["id"] if canonical else None, cleanup)

    async def fail(self, job_id, worker_id, attempt, owner_id, *, code, retryable):
        from app.modules.jobs.service import JobsService

        async with self.uploads._transaction() as connection:
            job = await UploadsRepository(connection).job(owner_id, job_id)
        if job is None:
            return PublishResult(False)
        accepted = await JobsService(self.uploads).retry_or_fail(
            dict(job) | {"attempts": attempt},
            worker_id,
            code,
            retryable=retryable,
            base=self.retry_backoff_seconds,
        )
        return PublishResult(accepted)

    async def reap_expired(self):
        from app.modules.jobs.service import JobsService

        return await JobsService(self.uploads).reap(
            types=("asset_verify",), base=self.retry_backoff_seconds
        )

    async def attempt_active(self, job_id: UUID, attempt: int) -> bool:
        async with self.uploads._transaction() as connection:
            repo = UploadsRepository(connection)
            candidate = await repo.job_by_id_internal(job_id)
            if candidate is None or candidate["owner_user_id"] is None:
                return False
            owner = await repo.user(candidate["owner_user_id"])
            if owner is None:
                return False
            job = await repo.job(owner["id"], job_id)
            return (
                job is not None
                and job["state"] in ("running", "cancel_requested")
                and job["attempts"] == attempt
                and job["lease_expires_at"] is not None
                and job["lease_expires_at"] > await repo.now()
            )
