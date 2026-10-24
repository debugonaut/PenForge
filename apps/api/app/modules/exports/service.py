"""Transactional private exports. Only the worker imports the independent engine."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from penforge_contracts import CompileExportJob, FontBuildManifest, GlyphOutline, content_hash
from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError, TimeoutError

from app.adapters.storage.base import StorageError
from app.core.errors import ApiError
from app.modules.exports.repository import ExportsRepository
from app.modules.exports.schemas import ExportCreate
from app.modules.glyphs.service import GlyphsService
from app.modules.identity.service import IdentityService
from app.modules.uploads.service import UploadsService

TOOLCHAIN = (
    Path(__file__).resolve().parents[5] / "packages/contracts/policies/font-toolchain.v1.json"
)
MIME = {"ttf": "font/ttf", "otf": "font/otf", "woff2": "font/woff2"}
FONT_CAP = 2 * 1024 * 1024
REPORT_CAP = 512 * 1024
COMPILE_SECONDS = 60  # provisional per-format hard wall cap, never an ETA


def encoded(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


class ExportsService:
    def __init__(
        self,
        engine,
        identity: IdentityService,
        uploads: UploadsService,
        storage,
        *,
        lease_seconds=60,
    ):
        self.engine, self.identity, self.uploads, self.storage = engine, identity, uploads, storage
        self.glyphs = GlyphsService(engine, identity, storage)
        self.lease_seconds = lease_seconds

    @asynccontextmanager
    async def transaction(self, *, repeatable=False):
        try:
            async with self.engine.connect() as connection:
                if repeatable:
                    await connection.execution_options(isolation_level="REPEATABLE READ")
                async with connection.begin():
                    yield connection
        except (OperationalError, InterfaceError, TimeoutError, StorageError):
            raise ApiError("dependency_unavailable", 503) from None
        except IntegrityError as error:
            if "fence_lost" in str(error.orig):
                raise ApiError("state_conflict", 409) from None
            raise

    @staticmethod
    def active(user, project):
        return (
            user
            and user["account_state"] == "active"
            and user["merged_into_user_id"] is None
            and project
            and project["status"] != "deleting"
        )

    async def scope(self, con, principal, project_id):
        user = await self.identity.resolve_in_transaction(con, principal, datetime.now(UTC))
        repo = ExportsRepository(con)
        project = await repo.project(user["id"], project_id)
        if project is None:
            raise ApiError("not_found", 404)
        if not self.active(user, project):
            raise ApiError("state_conflict", 409)
        return repo, user, project

    async def inputs(self, repo, owner, project, snapshot):
        if content_hash(snapshot["manifest"]) != "sha256:" + snapshot["manifest_sha256"].hex():
            raise ValueError("snapshot_hash_mismatch")
        if snapshot["manifest"]["script_code"] not in {"Latn", "Deva"}:
            raise ApiError("capability_unavailable", 503)
        rows = []
        for gid, rid in sorted(snapshot["manifest"]["glyphs"].items()):
            rev = await repo.revision(owner, project, UUID(gid), UUID(rid))
            if rev is None:
                raise ApiError("not_found", 404)
            raw = rev["outline_inline"]
            if raw is None:
                asset = await repo.asset(owner, rev["outline_asset_id"])
                if asset is None or asset["state"] != "ready" or asset["project_id"] != project:
                    raise ApiError("not_found", 404)
                data = await asyncio.to_thread(
                    self.storage.read_object, asset["storage_key"], max_bytes=2 * 1024 * 1024
                )
                if hashlib.sha256(data).digest() != asset["sha256"]:
                    raise ValueError("outline_hash_mismatch")
                raw = json.loads(data)
            outline = GlyphOutline.model_validate(raw).model_dump(mode="json")
            if (
                content_hash(outline) != "sha256:" + rev["content_hash"].hex()
                or outline["metrics"] != rev["metrics"]
                or outline["codepoints"] != rev["codepoints"]
            ):
                raise ValueError("revision_hash_mismatch")
            rows.append(
                dict(
                    outline=outline,
                    approval="approved",
                    origin=(await repo.candidate_origin(owner, project, rev["source_candidate_id"]))
                    if rev["source_candidate_id"]
                    else rev["origin"],
                    variant_index=rev["variant_index"],
                    revision_hash="sha256:" + rev["content_hash"].hex(),
                    source_hash=await repo.source_hash(owner, project, rev["source_sample_id"]),
                )
            )
        return rows

    async def create(self, principal, project_id, key, body: ExportCreate):
        if not key or len(key) > 255 or any(ord(c) < 33 for c in key):
            raise ApiError("validation_error", 400)
        async with self.transaction(repeatable=True) as con:
            repo, user, project = await self.scope(con, principal, project_id)
            now = await repo.now()
            digest = bytes.fromhex(
                content_hash(dict(path=f"exports:{project_id}", body=body.model_dump(mode="json")))[
                    7:
                ]
            )
            replay = await repo.receipt(user["id"], key, digest, now, now + timedelta(days=1))
            if replay:
                return replay
            snapshot = (
                await repo.snapshot(user["id"], project_id, body.snapshot_id)
                if body.snapshot_id
                else await self.glyphs.freeze_in_transaction(
                    con, user["id"], project, kind="export"
                )
            )
            if snapshot is None:
                raise ApiError("not_found", 404)
            rows = await self.inputs(repo, user["id"], project_id, snapshot)
            if not rows:
                raise ApiError("state_conflict", 409)
            manifest_value = dict(
                snapshot_hash="sha256:" + snapshot["manifest_sha256"].hex(),
                snapshot_created_at=snapshot["created_at"],
                family_name=body.family_name,
                style_name=body.style_name,
                options=body.options.model_dump(mode="json"),
                tool_versions=json.loads(TOOLCHAIN.read_text()),
            )
            if snapshot["manifest"]["script_code"] == "Deva":
                manifest_value.update(
                    script_code="Deva",
                    validation_profile="penforge-devanagari-v1",
                    indic_evidence_hash=content_hash(
                        sorted(
                            [r["outline"] for r in rows if r.get("approval") == "approved"],
                            key=content_hash,
                        )
                    ),
                )
            manifest = FontBuildManifest.model_validate(manifest_value)
            from app.modules.jobs.service import admit

            await admit(con, "compile_export")
            job_id = uuid4()
            export_ids = [uuid4() for _ in body.formats]
            payload = CompileExportJob(
                schema_version=1,
                job_type="compile_export",
                export_ids=[str(x) for x in export_ids],
                snapshot_id=str(snapshot["id"]),
                formats=body.formats,
            )
            await repo.insert_job(
                user["id"],
                dict(
                    id=job_id,
                    project_id=project_id,
                    owner_user_id=user["id"],
                    job_type="compile_export",
                    payload=payload.model_dump(mode="json"),
                    payload_schema_version=1,
                    idempotency_key=f"compile-export:{job_id}",
                    max_attempts=self.uploads.job_max_attempts,
                    stage="queued",
                    resource_profile={
                        "mem_mb": 768,
                        "wall_seconds": COMPILE_SECONDS * len(body.formats),
                    },
                ),
            )
            await repo.reserve(
                user["id"],
                job_id,
                {
                    "exports": len(export_ids),
                    "compute_seconds": COMPILE_SECONDS
                    * len(export_ids)
                    * self.uploads.job_max_attempts,
                },
                now,
            )
            planned = {}
            for eid, fmt in zip(export_ids, body.formats):
                font_id, report_id = uuid4(), uuid4()
                planned[str(eid)] = {"font": str(font_id), "report": str(report_id)}
                for aid, kind, mime, cap in [
                    (font_id, "export", MIME[fmt], FONT_CAP),
                    (report_id, "validation_report", "application/json", REPORT_CAP),
                ]:
                    await self.uploads.stage_worker_artifact(
                        con,
                        user["id"],
                        project_id,
                        artifact_id=aid,
                        worker_job_id=job_id,
                        storage_key=f"exports/{aid.hex}",
                        size_bytes=cap,
                        sha256=b"\0" * 32,
                        kind=kind,
                        mime_type=mime,
                    )
                await repo.insert_export(
                    dict(
                        id=eid,
                        project_id=project_id,
                        snapshot_id=snapshot["id"],
                        job_id=job_id,
                        requested_by_user_id=user["id"],
                        format=fmt,
                        family_name=body.family_name,
                        style_name=body.style_name,
                        version_string="1.000",
                        build_manifest=manifest.model_dump(mode="json"),
                        build_id=bytes.fromhex(manifest.build_id[7:]),
                        tool_versions=manifest.tool_versions,
                        model_version_ids=[
                            UUID(x) for x in snapshot["manifest"].get("model_version_ids", [])
                        ],
                    )
                )
            await repo.update_job(
                user["id"], job_id, checkpoint={"outputs": planned, "completed_units": 0}
            )
            result = dict(
                export_ids=[str(x) for x in export_ids],
                job_id=str(job_id),
                snapshot_id=str(snapshot["id"]),
            )
            await repo.event(job_id, now, from_state=None, to_state="queued", stage="queued")
            await repo.save_receipt(user["id"], key, result)
            return result

    async def get(self, principal, export_id):
        async with self.transaction() as con:
            owner = await self.identity.resolve_in_transaction(con, principal, datetime.now(UTC))
            repo = ExportsRepository(con)
            row = await repo.export(owner["id"], export_id)
            if row is None:
                raise ApiError("not_found", 404)
            project = await repo.project(owner["id"], row["project_id"])
            if not self.active(owner, project):
                raise ApiError("not_found", 404)
            job = await repo.job(owner["id"], row["job_id"]) if row["job_id"] else None
            asset = await repo.asset(owner["id"], row["asset_id"]) if row["asset_id"] else None
            return dict(
                id=str(row["id"]),
                project_id=str(row["project_id"]),
                snapshot_id=str(row["snapshot_id"]),
                job_id=str(row["job_id"]) if row["job_id"] else None,
                format=row["format"],
                status=row["status"],
                stage=row["status"]
                if row["status"] != "pending"
                else job["stage"]
                if job
                else "queued",
                progress=dict(
                    completed_units=job["checkpoint"].get("completed_units", 0) if job else 0,
                    total_units=job["checkpoint"].get("total_units", len(job["payload"]["formats"]))
                    if job
                    else 0,
                    basis="validated_layers"
                    if job and job["stage"] == "validating"
                    else "compiled_formats",
                ),
                eta_seconds=None,
                build_id="sha256:" + row["build_id"].hex() if row["build_id"] else None,
                build_manifest=row["build_manifest"],
                coverage_report=row["coverage_report"],
                validation_status=row["validation_status"],
                validation_report=row["validation_report"],
                sha256="sha256:" + asset["sha256"].hex() if asset else None,
                size_bytes=asset["size_bytes"] if asset else None,
                created_at=row["created_at"],
                finalized_at=row["finalized_at"],
            )

    async def download(self, principal, export_id):
        value = await self.get(principal, export_id)
        if value["status"] != "ready":
            raise ApiError("state_conflict", 409)
        async with self.transaction() as con:
            repo, user, _ = await self.scope(con, principal, UUID(value["project_id"]))
            row = await repo.export(user["id"], export_id)
            aid = row["asset_id"]
        return await self.uploads.download(principal, aid)

    async def fenced(self, repo, job, holder):
        from app.modules.jobs.service import live_job

        return await live_job(repo.connection, job, holder)

    async def attempt_active(self, job_id, attempt):
        async with self.transaction() as con:
            repo = ExportsRepository(con)
            job = await repo.internal_job(job_id)
            return bool(
                job
                and job["state"] == "running"
                and job["attempts"] == attempt
                and job["lease_expires_at"]
                and job["lease_expires_at"] > await repo.now()
            )

    async def claim(self, holder, *, worker_mem_mb=768, job_id=None):
        await self.reap_expired()
        from app.modules.jobs.service import queue_candidates

        async with self.transaction() as con:
            candidates = await queue_candidates(
                con, ("compile_export",), (1,), worker_mem_mb, job_id
            )
            for candidate in candidates:
                abandoned = None
                repo = ExportsRepository(con)
                user = await repo.user(candidate["owner_user_id"])
                project = await repo.project(candidate["owner_user_id"], candidate["project_id"])
                job = await repo.job(candidate["owner_user_id"], candidate["id"], queued=True)
                if job is None:
                    continue
                valid = self.active(user, project)
                for output in job["checkpoint"]["outputs"].values():
                    for aid in output.values():
                        asset = await repo.asset(job["owner_user_id"], UUID(aid))
                        valid = (
                            valid
                            and asset is not None
                            and asset["state"] == "pending_upload"
                            and asset["expires_at"] > await repo.now()
                        )
                if not valid:
                    await self.terminal(
                        repo, job, "export_scope_or_reservation_expired", canceled=True
                    )
                    abandoned = dict(job)
                else:
                    CompileExportJob.model_validate(job["payload"])
                    snapshot = await repo.snapshot(
                        user["id"], project["id"], UUID(job["payload"]["snapshot_id"])
                    )
                    rows = await self.inputs(repo, user["id"], project["id"], snapshot)
                    exports = [
                        dict(await repo.export(user["id"], UUID(x)))
                        for x in job["payload"]["export_ids"]
                    ]
                    for output in job["checkpoint"]["outputs"].values():
                        for aid in output.values():
                            asset = await repo.asset(user["id"], UUID(aid))
                            await asyncio.to_thread(
                                self.storage.delete_object, asset["storage_key"]
                            )
                    now = await repo.now()
                    current = await repo.update_job(
                        user["id"],
                        job["id"],
                        state="running",
                        attempts=job["attempts"] + 1,
                        lease_holder=holder,
                        lease_expires_at=now + timedelta(seconds=self.lease_seconds),
                        last_heartbeat_at=now,
                        started_at=job["started_at"] or now,
                        stage="compiling",
                        progress=0,
                        checkpoint=dict(job["checkpoint"])
                        | {"completed_units": 0, "total_units": len(exports)},
                    )
                    return dict(current) | {
                        "_glyphs": rows,
                        "_exports": exports,
                        "_coverage_context": snapshot["manifest"].get(
                            "coverage_context",
                            {
                                "generated_pending": [],
                                "observation": "not_frozen_in_legacy_snapshot",
                            },
                        ),
                    }
                if abandoned:
                    pass  # Pending assets are cleaned by the shared maintenance pass.
        return None

    async def heartbeat(self, job, holder, stage, completed, total):
        async with self.transaction() as con:
            repo = ExportsRepository(con)
            current = await self.fenced(repo, job, holder)
            if current is None:
                return False
            if (
                stage not in {"compiling", "validating"}
                or not 0 <= completed <= total
                or total <= 0
            ):
                raise ValueError("invalid_progress")
            now = await repo.now()
            await repo.update_job(
                current["owner_user_id"],
                current["id"],
                stage=stage,
                progress=completed / total,
                checkpoint=dict(current["checkpoint"])
                | {
                    "completed_units": completed,
                    "total_units": total,
                    "plan_revision": current["checkpoint"].get("plan_revision", 1)
                    + int(current["checkpoint"].get("total_units") != total),
                    "denominator_change_reason": "stage_work_units_changed"
                    if current["checkpoint"].get("total_units") != total
                    else current["checkpoint"].get("denominator_change_reason"),
                },
                last_heartbeat_at=now,
                lease_expires_at=now + timedelta(seconds=self.lease_seconds),
            )
            return True

    async def stage(self, job, holder, outputs):
        # Ledger was reserved at request creation. Persist hashes before writing;
        # locks fence both the writer and lease reaper, including SIGKILL recovery.
        async with self.transaction() as con:
            repo = ExportsRepository(con)
            current = await self.fenced(repo, job, holder)
            if current is None:
                return False
            planned = {
                UUID(aid)
                for row in current["checkpoint"]["outputs"].values()
                for aid in row.values()
            }
            if not set(outputs) <= planned:
                raise ValueError("unplanned_asset")
            for aid, data in outputs.items():
                asset = await repo.asset(current["owner_user_id"], aid)
                cap = FONT_CAP if asset["kind"] == "export" else REPORT_CAP
                if asset["state"] != "pending_upload" or not 0 < len(data) <= cap:
                    raise ValueError("output_budget_exceeded")
                await repo.update_asset(
                    current["owner_user_id"],
                    aid,
                    size_bytes=len(data),
                    sha256=hashlib.sha256(data).digest(),
                )
        async with self.transaction() as con:
            repo = ExportsRepository(con)
            current = await self.fenced(repo, job, holder)
            if current is None:
                return False
            for aid, data in outputs.items():
                if current["lease_expires_at"] <= await repo.now():
                    return False
                asset = await repo.asset(current["owner_user_id"], aid)
                await asyncio.to_thread(self.storage.put_immutable, asset["storage_key"], data)
            return current["lease_expires_at"] > await repo.now()

    async def publish(self, job, holder, results, usage_seconds):
        cleanup = []
        async with self.transaction() as con:
            repo = ExportsRepository(con)
            current = await self.fenced(repo, job, holder)
            if current is None:
                return False
            expected = set(current["payload"]["export_ids"])
            if set(results) != expected:
                raise ValueError("incomplete_results")
            ready = 0
            for eid in sorted(expected):
                report = results[eid]
                plans = current["checkpoint"]["outputs"][eid]
                rid, fid = UUID(plans["report"]), UUID(plans["font"])
                asset = await repo.asset(current["owner_user_id"], rid)
                data = await asyncio.to_thread(
                    self.storage.read_object, asset["storage_key"], max_bytes=REPORT_CAP
                )
                if hashlib.sha256(data).digest() != asset["sha256"] or json.loads(data) != report:
                    raise ValueError("report_hash_mismatch")
                passed = (
                    report["status"] in {"passed", "passed_with_warnings"}
                    and set(report["layers"])
                    == {"structural", "ots", "fontbakery", "harfbuzz", "render"}
                    and all(x["status"] == "passed" for x in report["layers"].values())
                )
                if report["status"] != "failed" and not passed:
                    raise ValueError("incomplete_validation")
                row = await repo.export(current["owner_user_id"], UUID(eid))
                if (
                    passed
                    and row["build_manifest"].get("script_code", "Latn") == "Latn"
                    and row["build_manifest"].get("options", {}).get("realism", 0) > 0
                    and (
                        report["layers"]["harfbuzz"].get("calt_policy") != "adjacent-repeat-v1"
                        or not report["layers"]["harfbuzz"].get("contextual_cycling")
                    )
                ):
                    raise ValueError("contextual_validation_required")
                if report["build_id"] != "sha256:" + row["build_id"].hex():
                    raise ValueError("build_identity_mismatch")
                await self.uploads.publish_worker_artifact(con, current["owner_user_id"], rid)
                if passed:
                    font = await repo.asset(current["owner_user_id"], fid)
                    if report["font_sha256"] != "sha256:" + font["sha256"].hex():
                        raise ValueError("font_hash_mismatch")
                    await self.uploads.publish_worker_artifact(con, current["owner_user_id"], fid)
                    ready += 1
                else:
                    await repo.update_asset(current["owner_user_id"], fid, state="expired")
                    cleanup.append(fid)
                await repo.update_export(
                    current["owner_user_id"],
                    UUID(eid),
                    status="ready" if passed else "failed",
                    asset_id=fid if passed else None,
                    validation_report_asset_id=rid,
                    validation_report=report,
                    validation_status=report["status"],
                    coverage_report=report["coverage"],
                    finalized_at=await repo.now(),
                    finalized_attempt=current["attempts"],
                )
            await repo.settle_job(
                current["owner_user_id"],
                current["id"],
                {
                    "exports": ready,
                    "compute_seconds": math.ceil(
                        usage_seconds + current["usage"].get("wall_seconds", 0)
                    ),
                },
            )
            await repo.update_job(
                current["owner_user_id"],
                current["id"],
                state="succeeded",
                stage="finished",
                finished_at=await repo.now(),
                progress=1,
                usage={"wall_seconds": usage_seconds + current["usage"].get("wall_seconds", 0)},
                lease_holder=None,
                lease_expires_at=None,
            )
        for aid in cleanup:
            await self.uploads.cleanup_asset(aid)
        return True

    async def fail(
        self, job, holder, code="validation_failed", *, retryable=False, usage_seconds=0
    ):
        from app.modules.jobs.service import JobsService

        return await JobsService(self.uploads).retry_or_fail(
            job, holder, code, retryable=retryable, usage_seconds=usage_seconds
        )

    async def terminal(self, repo, job, code, *, canceled=False):
        now = await repo.now()
        for eid in job["payload"]["export_ids"]:
            await repo.update_export(
                job["owner_user_id"],
                UUID(eid),
                status="failed",
                validation_status="failed",
                validation_report={"status": "failed", "error_code": code},
                finalized_at=now,
                finalized_attempt=job["attempts"],
            )
        await repo.settle_job(
            job["owner_user_id"],
            job["id"],
            {"compute_seconds": math.ceil(job["usage"].get("wall_seconds", 0))},
        )
        await repo.update_job(
            job["owner_user_id"],
            job["id"],
            state="canceled" if canceled else "failed",
            error_code=code,
            finished_at=now,
            lease_holder=None,
            lease_expires_at=None,
        )
        for plans in job["checkpoint"]["outputs"].values():
            for aid in plans.values():
                asset = await repo.asset(job["owner_user_id"], UUID(aid))
                if asset and asset["state"] == "pending_upload":
                    await repo.update_asset(job["owner_user_id"], UUID(aid), state="expired")

    async def cleanup(self, job):
        for plans in job["checkpoint"]["outputs"].values():
            for aid in plans.values():
                await self.uploads.cleanup_asset(UUID(aid))

    async def reap_expired(self):
        from app.modules.jobs.service import JobsService

        return await JobsService(self.uploads).reap(types=("compile_export",))
