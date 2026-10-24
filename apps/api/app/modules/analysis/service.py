"""Durable extraction orchestration and fenced registration, never glyph activation."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4, uuid5

from penforge_contracts import (
    AnalysisPayload,
    GlyphOutline,
    MLResultManifest,
    content_hash,
    parse_analysis_payload,
)
from penforge_contracts.canvas import drawing_provenance
from penforge_contracts.planning import structured_route
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError, TimeoutError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.auth.base import Principal
from app.adapters.storage.base import ObjectStorage, StorageError
from app.core.errors import ApiError
from app.modules.analysis.policy import (
    PIPELINE,
    image_config,
    pipeline,
    policy_hash,
)
from app.modules.analysis.policy import calibration_policy as calibration_policy
from app.modules.analysis.repository import AnalysisRepository
from app.modules.analysis.schemas import AnalysisCreate
from app.modules.identity.service import IdentityService
from app.modules.uploads.service import UploadsService


def json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


@dataclass(frozen=True)
class AnalysisArtifact:
    artifact_id: UUID
    kind: str
    media_type: str
    data: bytes = field(repr=False)

    @property
    def digest(self) -> str:
        return "sha256:" + hashlib.sha256(self.data).hexdigest()


class AnalysisService:
    def __init__(
        self,
        engine: AsyncEngine,
        identity: IdentityService,
        uploads: UploadsService,
        storage: ObjectStorage,
        *,
        lease_seconds: int = 60,
    ):
        self.engine, self.identity, self.uploads, self.storage = engine, identity, uploads, storage
        self.lease_seconds = lease_seconds

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Any]:
        try:
            async with self.engine.begin() as connection:
                yield connection
        except (OperationalError, InterfaceError, TimeoutError, StorageError):
            raise ApiError("dependency_unavailable", 503) from None
        except IntegrityError as error:
            # A deferred commit fence can expire during object validation.
            if "fence_lost" in str(error.orig):
                raise ApiError("state_conflict", 409) from None
            raise

    @staticmethod
    def active(owner: Any, project: Any) -> bool:
        return bool(
            owner is not None
            and owner["account_state"] == "active"
            and owner["merged_into_user_id"] is None
            and project is not None
            and project["status"] != "deleting"
        )

    async def scope(
        self, connection: Any, principal: Principal, project_id: UUID
    ) -> tuple[Any, AnalysisRepository, Any]:
        owner = await self.identity.resolve_in_transaction(connection, principal, datetime.now(UTC))
        repo = AnalysisRepository(connection)
        project = await repo.project(owner["id"], project_id)
        if project is None:
            raise ApiError("not_found", 404)
        if not self.active(owner, project):
            raise ApiError("state_conflict", 409)
        return owner, repo, project

    def manifest(self, sample: Any, script: str = "Latn") -> Any:
        key = (sample["template_id"], sample["template_version"], sample["page_index"])
        pinned = self.uploads.templates.get(key)
        if (
            pinned is None
            or pinned[0].script_code != script
            or pinned[1] != "sha256:" + sample["template_manifest_sha256"].hex()
        ):
            raise ApiError("template_mismatch", 400)
        return pinned[0]

    async def sources(
        self, repo: AnalysisRepository, owner: UUID, project: UUID, payload: AnalysisPayload
    ) -> list[dict]:
        result = []
        assigned = {item.sample_id for item in payload.assignments}
        for sid in payload.sample_ids:
            sample = await repo.sample(owner, project, UUID(sid))
            if sample is None:
                raise ApiError("not_found", 404)
            asset = await repo.asset(owner, sample["asset_id"])
            if asset is None or asset["project_id"] != project:
                raise ApiError("not_found", 404)
            source = asset
            if asset["state"] == "rejected" and asset["reject_reason"] == "duplicate":
                source = await repo.asset(owner, asset["duplicate_of"])
            if (
                source is None
                or source["state"] != "ready"
                or source["sha256"] != sample["source_sha256"]
                or asset["sha256"] != sample["source_sha256"]
            ):
                raise ApiError("state_conflict", 409)
            if sample["source_type"] == "canvas_drawing":
                if source["kind"] != "raw_strokes" or sid not in assigned:
                    raise ApiError(
                        "validation_error",
                        400,
                        details={"reasons": ["isolated_drawing_assignment_required"]},
                    )
            elif sample["source_type"] in {"template_scan", "template_photo"}:
                self.manifest(sample, payload.script_code)
                if sid in assigned:
                    raise ApiError("validation_error", 400)
            elif sid in assigned:
                raise ApiError("validation_error", 400)
            result.append({"sample": dict(sample), "asset": dict(source)})
        return result

    @staticmethod
    def dto(job: Any) -> dict:
        checkpoint = job["checkpoint"] or {}
        return {
            "job_id": str(job["id"]),
            "state": job["state"],
            "stage": job["stage"],
            "attempts": job["attempts"],
            "progress": {
                "completed_units": checkpoint.get("completed_units", 0),
                "total_units": len(job["payload"]["sample_ids"]),
                "basis": "assessed_samples",
            },
            "eta_seconds": None,
            "error_code": job["error_code"],
            "requires_user_action": job["state"] == "succeeded",
            "result_artifact_id": checkpoint.get("result_artifact_id"),
            "quality": checkpoint.get("quality", []),
        }

    async def create(
        self, principal: Principal, project_id: UUID, key: str, body: AnalysisCreate
    ) -> tuple[bool, dict]:
        if not 1 <= len(key) <= 255 or any(ord(ch) < 33 for ch in key):
            raise ApiError("validation_error", 400)
        if body.options.fuse_samples or not body.options.enhance:
            raise ApiError("validation_error", 400, details={"reasons": ["fusion_unvalidated"]})
        async with self.transaction() as connection:
            owner, repo, project = await self.scope(connection, principal, project_id)
            if project["script_code"] not in {"Latn", "Deva"}:
                raise ApiError(
                    "validation_error", 400, details={"reasons": ["script_not_validated"]}
                )
            now = await repo.now()
            digest = bytes.fromhex(
                content_hash(
                    {"path": f"/projects/{project_id}/jobs", "body": body.model_dump(mode="json")}
                )[7:]
            )
            replay = await repo.receipt(owner["id"], key, digest, now, now + timedelta(days=1))
            if replay is not None:
                return True, replay
            payload = body.payload(project_id, None, project["script_code"])
            sources = await self.sources(repo, owner["id"], project_id, payload)
            hashes = {
                s["sample"]["template_manifest_sha256"]
                for s in sources
                if s["sample"]["template_id"]
            }
            if len(hashes) > 1:
                raise ApiError(
                    "validation_error",
                    400,
                    details={"reasons": ["analyze_each_template_page_separately"]},
                )
            manifest_hash = "sha256:" + next(iter(hashes)).hex() if hashes else None
            payload = body.payload(project_id, manifest_hash, project["script_code"])
            from app.modules.jobs.service import admit, reserve_resources

            amounts = {"compute_seconds": 30 * len(sources) * 3} | await admit(
                connection, "analyze_samples"
            )
            if await repo.running_count(owner["id"]) >= 4:
                raise ApiError("quota_exceeded", 429)
            job = await repo.insert_job(
                owner["id"],
                {
                    "id": uuid4(),
                    "project_id": project_id,
                    "owner_user_id": owner["id"],
                    "job_type": "analyze_samples",
                    "payload": payload.model_dump(mode="json"),
                    "payload_schema_version": payload.schema_version,
                    "state": "queued",
                    "idempotency_key": "analyze:" + key,
                    "max_attempts": 3,
                    "checkpoint": {"completed_units": 0},
                    "resource_profile": {
                        "mem_mb": 768,
                        "planning": [structured_route(s["sample"]["source_type"]) for s in sources],
                    },
                    "created_at": now,
                },
            )
            await reserve_resources(connection, owner["id"], job["id"], amounts, now)
            await repo.event(job["id"], now, to_state="queued")
            response = self.dto(job)
            await repo.save_receipt(owner["id"], key, response)
            return False, response

    async def get(self, principal: Principal, project_id: UUID, job_id: UUID) -> dict:
        async with self.transaction() as connection:
            owner, repo, _ = await self.scope(connection, principal, project_id)
            job = await repo.job(owner["id"], job_id)
            if job is None or job["project_id"] != project_id:
                raise ApiError("not_found", 404)
            return self.dto(job)

    async def attempt_active(self, job_id: UUID, attempt: int) -> bool:
        import sqlalchemy as sa

        from app.db.models import jobs

        async with self.transaction() as connection:
            return bool(
                await connection.scalar(
                    sa.select(
                        sa.exists().where(
                            jobs.c.id == job_id,
                            jobs.c.job_type == "analyze_samples",
                            jobs.c.attempts == attempt,
                            jobs.c.state.in_(["running", "cancel_requested"]),
                            jobs.c.lease_expires_at > sa.func.clock_timestamp(),
                        )
                    )
                )
            )

    async def claim(
        self,
        worker_id: str,
        *,
        worker_mem_mb: int = 768,
        job_id=None,
        supported_versions: tuple[int, ...] = (1, 2),
    ) -> dict | None:
        if not worker_id or len(worker_id) > 128:
            raise ValueError("invalid_worker_id")
        await self.reap_expired()
        from app.modules.jobs.service import queue_candidates

        async with self.transaction() as connection:
            rows = await queue_candidates(
                connection, ("analyze_samples",), supported_versions, worker_mem_mb, job_id
            )
            for row in rows:
                repo = AnalysisRepository(connection)
                owner = await repo.user(row["owner_user_id"])
                project = await repo.project(row["owner_user_id"], row["project_id"])
                if not self.active(owner, project):
                    continue
                job = await repo.job(owner["id"], row["id"], queued=True)
                if job is None:
                    continue
                now = await repo.now()
                try:
                    payload = parse_analysis_payload(job["payload"])
                    sources = await self.sources(repo, owner["id"], project["id"], payload)
                except (ValidationError, ApiError):
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
                        error_code="invalid_input",
                        finished_at=now,
                        lease_holder=None,
                        lease_expires_at=None,
                    )
                    continue
                job = await repo.update_job(
                    owner["id"],
                    job["id"],
                    state="running",
                    attempts=job["attempts"] + 1,
                    lease_holder=worker_id,
                    lease_expires_at=now + timedelta(seconds=self.lease_seconds),
                    started_at=job["started_at"] or now,
                    last_heartbeat_at=now,
                    stage="assess",
                    checkpoint=dict(job["checkpoint"]),
                )
                await repo.event(
                    job["id"], now, from_state="queued", to_state="running", stage="assess"
                )
                return dict(job) | {"_sources": sources}
        return None

    async def fenced(self, repo, job, worker_id):
        from app.modules.jobs.service import live_job

        return await live_job(repo.connection, job, worker_id)

    async def heartbeat(self, job: dict, worker_id: str, completed: int) -> bool:
        async with self.transaction() as connection:
            repo = AnalysisRepository(connection)
            current = await self.fenced(repo, job, worker_id)
            if current is None:
                return False
            total = len(current["payload"]["sample_ids"])
            if not 0 <= completed <= total:
                raise ValueError("invalid_progress")
            checkpoint = dict(current["checkpoint"]) | {
                "completed_units": completed,
                "total_units": total,
            }
            now = await repo.now()
            await repo.update_job(
                current["owner_user_id"],
                current["id"],
                lease_expires_at=now + timedelta(seconds=self.lease_seconds),
                last_heartbeat_at=now,
                progress=completed / total,
                stage="extract",
                checkpoint=checkpoint,
            )
            return True

    @staticmethod
    def key(job: dict, artifact_id: UUID) -> str:
        return f"u/{job['owner_user_id']}/p/{job['project_id']}/analysis/{job['id']}/a{job['attempts']}/{artifact_id}"

    async def stage(self, job: dict, worker_id: str, artifacts: list[AnalysisArtifact]) -> bool:
        if (
            not artifacts
            or len(artifacts) > 4096
            or sum(len(a.data) for a in artifacts) > 32 * 1024 * 1024
            or len({a.artifact_id for a in artifacts}) != len(artifacts)
        ):
            raise ValueError("artifact_budget_exceeded")
        async with self.transaction() as connection:
            repo = AnalysisRepository(connection)
            current = await self.fenced(repo, job, worker_id)
            if current is None:
                return False
            if current["checkpoint"].get("staged_asset_ids"):
                raise ApiError("state_conflict", 409)
            for artifact in artifacts:
                await self.uploads.stage_worker_artifact(
                    connection,
                    current["owner_user_id"],
                    current["project_id"],
                    artifact_id=artifact.artifact_id,
                    storage_key=self.key(job, artifact.artifact_id),
                    size_bytes=len(artifact.data),
                    sha256=bytes.fromhex(artifact.digest[7:]),
                    kind=artifact.kind,
                    mime_type=artifact.media_type,
                )
            await repo.update_job(
                current["owner_user_id"],
                current["id"],
                checkpoint=dict(current["checkpoint"])
                | {
                    "staged_asset_ids": [str(a.artifact_id) for a in artifacts],
                },
            )
        # A committed ledger survives SIGKILL. Hold the same owner/job locks as
        # claim/deletion/reaping while writing so cleanup cannot race a late writer.
        async with self.transaction() as connection:
            repo = AnalysisRepository(connection)
            current = await self.fenced(repo, job, worker_id)
            if current is None:
                return False
            for artifact in artifacts:
                if current["lease_expires_at"] <= await repo.now():
                    return False
                await asyncio.to_thread(
                    self.storage.put_immutable, self.key(job, artifact.artifact_id), artifact.data
                )
            return current["lease_expires_at"] > await repo.now()

    async def publish(self, job: dict, worker_id: str, manifest_id: UUID) -> bool:
        async with self.transaction() as connection:
            repo = AnalysisRepository(connection)
            current = await self.fenced(repo, job, worker_id)
            if current is None:
                return False
            payload = parse_analysis_payload(current["payload"])
            sources = await self.sources(
                repo, current["owner_user_id"], current["project_id"], payload
            )
            staged = {UUID(x) for x in current["checkpoint"].get("staged_asset_ids", [])}

            async def read(aid: UUID) -> tuple[Any, bytes]:
                row = await repo.asset(current["owner_user_id"], aid)
                if (
                    aid not in staged
                    or row is None
                    or row["state"] != "pending_upload"
                    or row["project_id"] != current["project_id"]
                    or row["storage_key"] != self.key(job, aid)
                ):
                    raise ApiError("upload_mismatch", 400)
                data = await asyncio.to_thread(
                    self.storage.read_object, row["storage_key"], max_bytes=32 * 1024 * 1024
                )
                if len(data) != row["size_bytes"] or hashlib.sha256(data).digest() != row["sha256"]:
                    raise ApiError("upload_mismatch", 400)
                return row, data

            manifest_row, manifest_data = await read(manifest_id)
            if (
                manifest_row["kind"] != "validation_report"
                or manifest_row["mime_type"] != "application/json"
            ):
                raise ApiError("upload_mismatch", 400)
            try:
                manifest = MLResultManifest.model_validate_json(manifest_data)
            except ValidationError:
                raise ApiError("upload_mismatch", 400) from None
            expected_sources = [
                "sha256:" + item["sample"]["source_sha256"].hex() for item in sources
            ]
            if (
                manifest.job_id != str(job["id"])
                or manifest.attempt_id != str(uuid5(job["id"], f"attempt:{job['attempts']}"))
                or manifest.fencing_token != job["attempts"]
                or manifest.input.sample_ids != payload.sample_ids
                or manifest.input.source_hashes != expected_sources
                or manifest.input.template_manifest_hash != payload.template_manifest_hash
                or manifest.pipeline.model_dump(mode="json")
                != pipeline(payload.script_code, getattr(payload, "drawing_mode", None))
                or manifest.stage != "candidate_extraction"
            ):
                raise ApiError("upload_mismatch", 400)
            report_row, report_bytes = await read(UUID(manifest.quality_report.artifact_id))
            self.check_child(manifest.quality_report, report_row)
            if report_row["kind"] != "validation_report":
                raise ApiError("upload_mismatch", 400)
            quality = json.loads(report_bytes)
            if quality.get("policy_hash") != policy_hash(
                payload.script_code, getattr(payload, "drawing_mode", None)
            ):
                raise ApiError("upload_mismatch", 400)
            if not isinstance(quality.get("samples"), list) or len(quality["samples"]) != len(
                sources
            ):
                raise ApiError("upload_mismatch", 400)
            sample_map = {str(x["sample"]["id"]): x for x in sources}
            result_id = uuid5(job["id"], "candidate_extraction")
            parsed = []
            for candidate in manifest.candidates:
                row, data = await read(UUID(candidate.outline.artifact_id))
                self.check_child(candidate.outline, row)
                if row["kind"] != "vector":
                    raise ApiError("upload_mismatch", 400)
                outline = GlyphOutline.model_validate_json(data)
                evidence = quality.get("candidates", {}).get(candidate.candidate_id)
                source = (
                    sample_map.get(str(evidence.get("sample_id")))
                    if isinstance(evidence, dict)
                    else None
                )
                if (
                    source is None
                    or list(outline.codepoints) != candidate.codepoints
                    or candidate.origin
                    != (
                        "manual_drawing"
                        if source["sample"]["source_type"] == "canvas_drawing"
                        else "authentic_extraction"
                    )
                    or candidate.evidence.outline_validity != "pass"
                    or any(
                        getattr(candidate.evidence, field) is not None
                        for field in (
                            "recognition",
                            "segmentation",
                            "style_fidelity",
                            "model_uncertainty",
                        )
                    )
                    or evidence.get("source_hash")
                    != "sha256:" + source["sample"]["source_sha256"].hex()
                    or evidence.get("expected_text_is_ground_truth") is not False
                ):
                    raise ApiError("upload_mismatch", 400)
                # Assignment is a reviewed destination hint, not an OCR assertion.
                if source["sample"]["source_type"] == "canvas_drawing":
                    assignment = next(
                        a for a in payload.assignments if a.sample_id == evidence["sample_id"]
                    )
                    provenance = evidence.get("provenance", {})
                    valid_mapping = (
                        assignment.codepoints == candidate.codepoints
                        and provenance.get("capture_transform")
                        == assignment.capture_transform.model_dump(mode="json")
                        and all(
                            provenance.get(key) == value
                            for key, value in drawing_provenance(
                                getattr(payload, "drawing_mode", None)
                            ).items()
                        )
                        and provenance.get("image_segmentation") is False
                        and provenance.get("ocr") is False
                        and provenance.get("source_unchanged") is True
                        and provenance.get("outline_hash")
                        == content_hash(outline.model_dump(mode="json"))
                        and provenance.get("source_document_hash")
                        == "sha256:" + source["asset"]["document_hash"].hex()
                    )
                else:
                    template = self.manifest(source["sample"], payload.script_code)
                    valid_mapping = any(
                        c.cell_id == evidence.get("cell_id")
                        and c.codepoints == candidate.codepoints
                        for c in template.cells
                    )
                if not valid_mapping:
                    raise ApiError("upload_mismatch", 400)
                parsed.append((candidate, outline, row, data, evidence, source))
            for assessment, source in zip(quality["samples"], sources, strict=True):
                if assessment.get("state") not in {"analyzed", "needs_better_input"}:
                    raise ApiError("upload_mismatch", 400)
                q = assessment.get("quality", {})
                if source["sample"]["source_type"] in {"template_scan", "template_photo"}:
                    if (
                        q.get("configuration") != image_config()
                        or q.get("source_bytes_hash")
                        != "sha256:" + source["sample"]["source_sha256"].hex()
                        or q.get("template_manifest_hash") != payload.template_manifest_hash
                        or q.get("algorithm_version") != PIPELINE["preprocessing_version"]
                        or q.get("vectorizer_version") != "pixel-union-contours-v2"
                    ):
                        raise ApiError("upload_mismatch", 400)

            # Verify all nested private evidence references, not just outlines.
            async def evidence_refs(value):
                if isinstance(value, dict):
                    if "artifact_id" in value:
                        from penforge_contracts.schemas import ChildArtifact

                        ref = ChildArtifact.model_validate(value)
                        row, _ = await read(UUID(ref.artifact_id))
                        self.check_child(ref, row)
                    else:
                        for item in value.values():
                            await evidence_refs(item)
                elif isinstance(value, list):
                    for item in value:
                        await evidence_refs(item)

            await evidence_refs(quality)
            # Verify and promote every ledger object, including private crop/mask evidence.
            for aid in sorted(staged, key=str):
                await read(aid)
                await self.uploads.publish_worker_artifact(
                    connection, current["owner_user_id"], aid
                )
            await repo.insert_result(
                current["owner_user_id"],
                current["project_id"],
                {
                    "id": result_id,
                    "project_id": current["project_id"],
                    "job_id": current["id"],
                    "attempt": current["attempts"],
                    "stage": manifest.stage,
                    "manifest_asset_id": manifest_id,
                    "manifest_sha256": manifest_row["sha256"],
                    "input_hashes": manifest.input.model_dump(mode="json"),
                    "pipeline": manifest.pipeline.model_dump(mode="json"),
                },
            )
            for candidate, outline, row, data, evidence, source in parsed:
                inline = (
                    len(json.dumps(outline.model_dump(mode="json"), ensure_ascii=False).encode())
                    <= 30000
                )
                glyph = await repo.glyph(
                    current["owner_user_id"], current["project_id"], candidate.codepoints
                )
                await repo.insert_candidate(
                    current["owner_user_id"],
                    current["project_id"],
                    {
                        "id": UUID(candidate.candidate_id),
                        "project_id": current["project_id"],
                        "glyph_id": glyph["id"],
                        "result_artifact_id": result_id,
                        "source_sample_id": source["sample"]["id"],
                        "origin": candidate.origin,
                        "rank": candidate.rank,
                        "outline_inline": outline.model_dump(mode="json") if inline else None,
                        "outline_asset_id": None if inline else row["id"],
                        "metrics": outline.metrics.model_dump(mode="json"),
                        "content_hash": bytes.fromhex(
                            content_hash(outline.model_dump(mode="json"))[7:]
                        ),
                        "evidence": candidate.evidence.model_dump(mode="json") | evidence,
                    },
                )
            for assessment, source in zip(quality["samples"], sources, strict=True):
                if assessment.get("sample_id") != str(source["sample"]["id"]):
                    raise ApiError("upload_mismatch", 400)
                await repo.update_sample(
                    current["owner_user_id"],
                    current["project_id"],
                    source["sample"]["id"],
                    state=assessment.get("state", "needs_better_input"),
                    quality=assessment.get("quality", {}),
                    quality_reasons=assessment.get("reasons", []),
                )
            import math

            from app.modules.jobs.service import settle

            await settle(
                connection,
                current,
                {"compute_seconds": math.ceil(current["usage"].get("wall_seconds", 0))},
            )
            now = await repo.now()
            await repo.update_job(
                current["owner_user_id"],
                current["id"],
                state="succeeded",
                stage="review",
                progress=1.0,
                finished_at=now,
                lease_holder=None,
                lease_expires_at=None,
                checkpoint={
                    "completed_units": len(sources),
                    "result_artifact_id": str(result_id),
                    "quality": [
                        {
                            "sample_id": a["sample_id"],
                            "state": a.get("state"),
                            "reasons": a.get("reasons", []),
                        }
                        for a in quality["samples"]
                    ],
                },
            )
            await repo.event(
                current["id"],
                now,
                from_state="running",
                to_state="succeeded",
                stage="review",
                progress=1.0,
            )
            return True

    @staticmethod
    def check_child(ref: Any, row: Any) -> None:
        if (
            ref.sha256 != "sha256:" + row["sha256"].hex()
            or ref.size_bytes != row["size_bytes"]
            or ref.media_type != row["mime_type"]
        ):
            raise ApiError("upload_mismatch", 400)

    async def fail(self, job, worker_id, code, *, retryable=False):
        from app.modules.jobs.service import JobsService

        return await JobsService(self.uploads).retry_or_fail(
            job, worker_id, code, retryable=retryable
        )

    async def reap_expired(self):
        from app.modules.jobs.service import JobsService

        return await JobsService(self.uploads).reap(types=("analyze_samples",))
