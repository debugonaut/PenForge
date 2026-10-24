"""Owner-controlled canonical review and immutable milestones.

Workers register sibling candidates elsewhere. Only these authenticated actions
can turn a pending review revision into an active approved glyph.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from penforge_contracts import GlyphOutline, content_hash
from pydantic import ValidationError
from sqlalchemy.exc import InterfaceError, OperationalError, TimeoutError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.adapters.auth.base import Principal
from app.adapters.storage.base import ObjectStorage, StorageError
from app.core.errors import ApiError
from app.modules.glyphs.repository import GlyphsRepository
from app.modules.glyphs.schemas import GlyphPatch, SelectCandidate, SnapshotCreate
from app.modules.identity.service import IdentityService

CORE = set(range(65, 91)) | set(range(97, 123))
ASCII = set(range(32, 127))
SYNTHETIC = {"geometric_reconstruction", "style_conditioned_generation"}


class GlyphsService:
    def __init__(
        self,
        engine: AsyncEngine,
        identity_service: IdentityService,
        storage: ObjectStorage | None = None,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        ids: Callable[[], UUID] = uuid4,
        idempotency_ttl_seconds: int = 86400,
    ):
        self.engine, self.identity_service, self.storage = engine, identity_service, storage
        self.clock, self.ids, self.idempotency_ttl_seconds = clock, ids, idempotency_ttl_seconds

    @asynccontextmanager
    async def _transaction(self, *, repeatable: bool = False) -> AsyncIterator[AsyncConnection]:
        try:
            async with self.engine.connect() as connection:
                if repeatable:
                    await connection.execution_options(isolation_level="REPEATABLE READ")
                async with connection.begin():
                    yield connection
        except (OperationalError, InterfaceError, TimeoutError, StorageError):
            raise ApiError("dependency_unavailable", 503) from None

    async def _scope(self, connection: AsyncConnection, principal: Principal, project_id: UUID):
        user = await self.identity_service.resolve_in_transaction(
            connection, principal, self.clock()
        )
        repo = GlyphsRepository(connection)
        project = await repo.project(user["id"], project_id, lock=True)
        if project is None:
            raise ApiError("not_found", 404)
        if project["status"] == "deleting":
            raise ApiError("state_conflict", 409)
        return repo, user["id"], project

    @staticmethod
    def _working(revisions):
        return next((row for row in revisions if row["approval"] != "rejected"), None)

    @staticmethod
    def _version(if_match: str | None) -> int:
        import re

        if if_match is None:
            raise ApiError("precondition_required", 428)
        match = re.fullmatch(r'"v([1-9][0-9]{0,8})"', if_match)
        if not match:
            raise ApiError("revision_conflict", 412)
        return int(match[1])

    @staticmethod
    def _check_base(glyph, revisions, version: int, base: UUID | None):
        working = GlyphsService._working(revisions)
        expected = working["id"] if working else None
        if glyph["row_version"] != version or expected != base:
            raise ApiError(
                "revision_conflict",
                412,
                context={
                    "current_row_version": glyph["row_version"],
                    "working_revision_id": str(expected) if expected else None,
                    "active_revision_id": str(glyph["active_revision_id"])
                    if glyph["active_revision_id"]
                    else None,
                },
            )
        return working

    async def _outline(
        self, repo: GlyphsRepository, owner: UUID, project: UUID, row
    ) -> dict[str, Any]:
        if row["outline_inline"] is not None:
            raw = row["outline_inline"]
        else:
            asset = await repo.asset(owner, project, row["outline_asset_id"])
            if asset is None or self.storage is None:
                raise ApiError("dependency_unavailable", 503)
            encoded = await asyncio.to_thread(
                self.storage.read_object, asset["storage_key"], max_bytes=2 * 1024 * 1024
            )
            if (
                len(encoded) != asset["size_bytes"]
                or hashlib.sha256(encoded).digest() != asset["sha256"]
            ):
                raise ApiError("upload_mismatch", 400)
            try:
                raw = json.loads(encoded)
            except (TypeError, ValueError):
                raise ApiError("upload_mismatch", 400) from None
        try:
            outline = GlyphOutline.model_validate(raw).model_dump(mode="json")
        except ValidationError:
            raise ApiError("upload_mismatch", 400) from None
        if (
            content_hash(outline) != "sha256:" + row["content_hash"].hex()
            or outline["metrics"] != row["metrics"]
        ):
            raise ApiError("upload_mismatch", 400)
        return outline

    async def _detail(self, repo, owner, project, glyph, *, geometry: bool = True):
        revisions = await repo.revisions(owner, project, glyph["id"])
        working = self._working(revisions)
        active = next((row for row in revisions if row["id"] == glyph["active_revision_id"]), None)
        candidates = await repo.candidates(owner, project, glyph["id"])
        eligible = [
            row
            for row in candidates
            if row["origin"] not in SYNTHETIC
            and row["review_status"] != "rejected"
            and row["evidence"].get("outline_validity") == "pass"
        ]
        # Unknown recognition/style scores cannot justify an accuracy ranking.
        # Prefer fewer measured capture risks, then stable source order.
        recommended = min(
            eligible,
            key=lambda row: (
                len(set(row["evidence"].get("flags", [])) & {"border_touch", "faint_ink"}),
                row["rank"],
                row["created_at"],
                str(row["id"]),
            ),
            default=None,
        )
        outline = (
            await self._outline(repo, owner, project, working) if geometry and working else None
        )
        active_outline = (
            await self._outline(repo, owner, project, active) if geometry and active else None
        )
        return {
            "id": str(glyph["id"]),
            "project_id": str(project),
            "codepoints": glyph["codepoints"],
            "variant_index": glyph["variant_index"],
            "row_version": glyph["row_version"],
            "coverage_status": glyph["coverage_status"],
            "active_revision_id": str(glyph["active_revision_id"])
            if glyph["active_revision_id"]
            else None,
            "working_revision_id": str(working["id"]) if working else None,
            "recommended_candidate_id": str(recommended["id"]) if recommended else None,
            "outline": outline,
            "outline_edit_supported": outline is not None
            and len(json.dumps(outline, ensure_ascii=False).encode()) <= 30000,
            "active_outline": active_outline,
            "revisions": [
                {
                    "id": str(row["id"]),
                    "seq": row["seq"],
                    "parent_revision_id": str(row["parent_revision_id"])
                    if row["parent_revision_id"]
                    else None,
                    "source_candidate_id": str(row["source_candidate_id"])
                    if row["source_candidate_id"]
                    else None,
                    "source_sample_id": str(row["source_sample_id"])
                    if row["source_sample_id"]
                    else None,
                    "origin": row["origin"],
                    "alternate_source_revision_id": str(row["alternate_source_revision_id"])
                    if row["alternate_source_revision_id"]
                    else None,
                    "alternate_source_snapshot_id": str(row["alternate_source_snapshot_id"])
                    if row["alternate_source_snapshot_id"]
                    else None,
                    "approval": row["approval"],
                    "content_hash": "sha256:" + row["content_hash"].hex(),
                    "created_at": row["created_at"],
                }
                for row in revisions
            ]
            if geometry
            else [],
        }

    async def get(self, principal, project_id: UUID, glyph_id: UUID):
        async with self._transaction() as connection:
            repo, owner, _ = await self._scope(connection, principal, project_id)
            glyph = await repo.glyph(owner, project_id, glyph_id)
            if glyph is None:
                raise ApiError("not_found", 404)
            return await self._detail(repo, owner, project_id, glyph)

    async def list(
        self, principal, project_id: UUID, *, limit: int = 25, cursor: str | None = None
    ):
        try:
            before = UUID(cursor) if cursor else None
        except ValueError:
            raise ApiError("validation_error", 400) from None
        async with self._transaction() as connection:
            repo, owner, _ = await self._scope(connection, principal, project_id)
            rows = await repo.glyphs(owner, project_id, limit=limit + 1, cursor=before)
            return {
                "items": [
                    await self._detail(repo, owner, project_id, row, geometry=False)
                    for row in rows[:limit]
                ],
                "next_cursor": str(rows[limit - 1]["id"]) if len(rows) > limit else None,
            }

    async def candidates(
        self,
        principal,
        project_id: UUID,
        glyph_id: UUID,
        *,
        limit: int = 25,
        cursor: str | None = None,
    ):
        try:
            offset = int(cursor) if cursor else 0
            if not 0 <= offset <= 1000000:
                raise ValueError
        except ValueError:
            raise ApiError("validation_error", 400) from None
        async with self._transaction() as connection:
            repo, owner, _ = await self._scope(connection, principal, project_id)
            if await repo.glyph(owner, project_id, glyph_id) is None:
                raise ApiError("not_found", 404)
            rows = await repo.candidates(owner, project_id, glyph_id)
            items = []
            for row in rows[offset : offset + limit]:
                items.append(
                    {
                        "id": str(row["id"]),
                        "result_artifact_id": str(row["result_artifact_id"]),
                        "glyph_id": str(glyph_id),
                        "source_sample_id": str(row["source_sample_id"]),
                        "rank": row["rank"],
                        "origin": row["origin"],
                        "review_status": row["review_status"],
                        "content_hash": "sha256:" + row["content_hash"].hex(),
                        "evidence": row["evidence"],
                        "outline": await self._outline(repo, owner, project_id, row),
                    }
                )
            return {
                "items": items,
                "next_cursor": str(offset + limit) if len(rows) > offset + limit else None,
            }

    async def _key(self, repo, owner, key, path, body):
        if key is None:
            return None
        if not 1 <= len(key) <= 255 or any(ord(c) < 33 for c in key):
            raise ApiError("validation_error", 400)
        digest = bytes.fromhex(content_hash({"path": path, "body": body}).removeprefix("sha256:"))
        row = await repo.claim_key(
            owner,
            key,
            digest,
            self.clock(),
            self.clock() + timedelta(seconds=self.idempotency_ttl_seconds),
        )
        if row is None:
            return None
        if row["request_hash"] != digest:
            raise ApiError("idempotency_key_reuse", 422)
        if row["state"] != "completed":
            raise ApiError("idempotency_in_progress", 409)
        saved = row["response_body"]
        if "snapshot_receipt" in saved:
            receipt = saved["snapshot_receipt"]
            snapshot = await repo.snapshot(owner, UUID(receipt["project_id"]), UUID(receipt["id"]))
            if snapshot is None:
                raise ApiError("not_found", 404)
            return self._snapshot_dto(snapshot)
        if "style_profile_receipt" in saved:
            profile = await repo.profile_id(owner, UUID(saved["style_profile_receipt"]))
            if profile is None:
                raise ApiError("not_found", 404)
            return self._style_dto(profile)
        if "glyph_review_receipt" in saved:
            # Geometry already has immutable revision/asset storage. Rehydrate it
            # without copying large outlines into the bounded receipt row.
            result = saved["glyph_review_receipt"]
            revisions = await repo.revisions(owner, UUID(result["project_id"]), UUID(result["id"]))
            by_id = {str(revision["id"]): revision for revision in revisions}
            for field, pointer in (
                ("outline", "working_revision_id"),
                ("active_outline", "active_revision_id"),
            ):
                revision_id = result[pointer]
                result[field] = (
                    await self._outline(repo, owner, UUID(result["project_id"]), by_id[revision_id])
                    if revision_id
                    else None
                )
            return result
        return saved

    async def _finish(self, repo, owner, project, glyph, key):
        current = await repo.glyph(owner, project, glyph)
        result = await self._detail(repo, owner, project, current)
        if key:
            encoded = json.loads(json.dumps(result, default=lambda value: value.isoformat()))
            encoded["outline"] = None
            encoded["active_outline"] = None
            receipt = {"glyph_review_receipt": encoded}
            if len(json.dumps(receipt).encode()) > 30000:
                raise ApiError("payload_too_large", 413)
            await repo.complete_key(owner, key, receipt, 200)
        return result

    async def _new_revision(
        self,
        repo,
        owner,
        glyph,
        revisions,
        outline,
        *,
        origin,
        source=None,
        sample=None,
        candidate=None,
        job=None,
        outline_asset_id=None,
    ):
        if outline["codepoints"] != glyph["codepoints"]:
            raise ApiError(
                "validation_error", 400, details={"fields": ["/body/outline/codepoints"]}
            )
        if (
            len(json.dumps(outline, ensure_ascii=False).encode()) > 30000
            and outline_asset_id is None
        ):
            raise ApiError("payload_too_large", 413)
        now = self.clock()
        row = await repo.insert_revision(
            {
                "id": self.ids(),
                "glyph_id": glyph["id"],
                "seq": revisions[0]["seq"] + 1 if revisions else 1,
                "parent_revision_id": source["id"] if source else None,
                "origin": origin,
                "approval": "pending",
                "outline_inline": outline if outline_asset_id is None else None,
                "outline_asset_id": outline_asset_id,
                "metrics": outline["metrics"],
                "content_hash": bytes.fromhex(content_hash(outline).removeprefix("sha256:")),
                "source_sample_id": sample,
                "source_candidate_id": candidate,
                "job_id": job,
                "created_by_user_id": owner,
                "created_at": now,
            }
        )
        await repo.update_glyph(
            glyph["project_id"],
            glyph["id"],
            glyph["row_version"],
            coverage_status="needs_review",
            updated_at=now,
        )
        return row

    async def select(
        self,
        principal,
        project_id: UUID,
        glyph_id: UUID,
        *,
        body: SelectCandidate,
        if_match: str | None,
        idempotency_key: str,
    ):
        version = self._version(if_match)
        async with self._transaction() as connection:
            repo, owner, _ = await self._scope(connection, principal, project_id)
            glyph = await repo.glyph(owner, project_id, glyph_id, lock=True)
            if glyph is None:
                raise ApiError("not_found", 404)
            replay = await self._key(
                repo,
                owner,
                idempotency_key,
                f"select:{project_id}:{glyph_id}",
                body.model_dump(mode="json"),
            )
            if replay:
                return replay
            revisions = await repo.revisions(owner, project_id, glyph_id)
            working = self._check_base(glyph, revisions, version, body.base_revision_id)
            candidates = await repo.candidates(owner, project_id, glyph_id)
            candidate = next((row for row in candidates if row["id"] == body.candidate_id), None)
            if candidate is None:
                raise ApiError("not_found", 404)
            if candidate["review_status"] == "rejected":
                raise ApiError("state_conflict", 409)
            if (
                not working
                or working["source_candidate_id"] != candidate["id"]
                or working["content_hash"] != candidate["content_hash"]
            ):
                outline = await self._outline(repo, owner, project_id, candidate)
                await self._new_revision(
                    repo,
                    owner,
                    glyph,
                    revisions,
                    outline,
                    origin="ml_generated"
                    if candidate["origin"] in SYNTHETIC
                    else "user_capture"
                    if candidate["origin"] == "manual_drawing"
                    else "auto_extract",
                    source=working,
                    sample=candidate["source_sample_id"],
                    candidate=candidate["id"],
                    outline_asset_id=candidate["outline_asset_id"],
                )
            return await self._finish(repo, owner, project_id, glyph_id, idempotency_key)

    async def update(
        self,
        principal,
        project_id: UUID,
        glyph_id: UUID,
        *,
        body: GlyphPatch,
        if_match: str | None,
        idempotency_key: str | None = None,
    ):
        version = self._version(if_match)
        async with self._transaction() as connection:
            repo, owner, _ = await self._scope(connection, principal, project_id)
            glyph = await repo.glyph(owner, project_id, glyph_id, lock=True)
            if glyph is None:
                raise ApiError("not_found", 404)
            replay = await self._key(
                repo,
                owner,
                idempotency_key,
                f"patch:{project_id}:{glyph_id}",
                body.model_dump(mode="json"),
            )
            if replay:
                return replay
            revisions = await repo.revisions(owner, project_id, glyph_id)
            working = self._check_base(glyph, revisions, version, body.base_revision_id)
            if body.action == "edit":
                assert body.outline is not None
                outline = body.outline.model_dump(mode="json")
                if body.metrics is not None and body.metrics != outline["metrics"]:
                    raise ApiError("validation_error", 400, details={"fields": ["/body/metrics"]})
                if (
                    not working
                    or content_hash(outline) != "sha256:" + working["content_hash"].hex()
                ):
                    await self._new_revision(
                        repo,
                        owner,
                        glyph,
                        revisions,
                        outline,
                        origin="user_edit",
                        source=working,
                        sample=working["source_sample_id"] if working else None,
                        candidate=working["source_candidate_id"] if working else None,
                    )
            elif body.action == "rollback":
                target = next(
                    (row for row in revisions if row["id"] == body.target_revision_id), None
                )
                if target is None:
                    raise ApiError("not_found", 404)
                outline = await self._outline(repo, owner, project_id, target)
                await self._new_revision(
                    repo,
                    owner,
                    glyph,
                    revisions,
                    outline,
                    origin="restored",
                    source=working,
                    sample=target["source_sample_id"],
                    candidate=target["source_candidate_id"],
                    outline_asset_id=target["outline_asset_id"],
                )
            else:
                if working is None or working["approval"] != "pending":
                    raise ApiError("state_conflict", 409)
                approved = body.action == "approve"
                await repo.update_revision(
                    glyph_id,
                    working["id"],
                    approval="approved" if approved else "rejected",
                    approved_by_user_id=owner if approved else None,
                    approved_at=self.clock() if approved else None,
                )
                if working["source_candidate_id"]:
                    await repo.review_candidate(
                        project_id,
                        working["source_candidate_id"],
                        "approved" if approved else "rejected",
                    )
                await repo.update_glyph(
                    project_id,
                    glyph_id,
                    version,
                    **(
                        {"active_revision_id": working["id"], "coverage_status": "approved"}
                        if approved
                        else {
                            "coverage_status": "approved"
                            if glyph["active_revision_id"]
                            else "excluded"
                        }
                    ),
                    updated_at=self.clock(),
                )
            return await self._finish(repo, owner, project_id, glyph_id, idempotency_key)

    async def space(
        self, principal, project_id: UUID, *, advance_width: float, idempotency_key: str
    ):
        async with self._transaction() as connection:
            repo, owner, _ = await self._scope(connection, principal, project_id)
            replay = await self._key(
                repo,
                owner,
                idempotency_key,
                f"space:{project_id}",
                {"advance_width": advance_width},
            )
            if replay:
                return replay
            glyph = await repo.ensure_space(project_id, self.ids(), self.clock())
            revisions = await repo.revisions(owner, project_id, glyph["id"])
            # Existing metrics must be edited with the normal ETag/base-revision path.
            if revisions:
                raise ApiError("state_conflict", 409)
            outline = GlyphOutline.model_validate(
                {
                    "schema_version": 1,
                    "units_per_em": 1000,
                    "coordinate_system": {
                        "origin": "baseline",
                        "x_positive": "right",
                        "y_positive": "up",
                    },
                    "codepoints": [32],
                    "metrics": {"advance_width": advance_width, "left_side_bearing": 0.0},
                    "contours": [],
                }
            ).model_dump(mode="json")
            await self._new_revision(repo, owner, glyph, [], outline, origin="user_edit")
            return await self._finish(repo, owner, project_id, glyph["id"], idempotency_key)

    @staticmethod
    def _snapshot_dto(row):
        return {
            "id": str(row["id"]),
            "project_id": str(row["project_id"]),
            "kind": row["kind"],
            "label": row["label"],
            "manifest": row["manifest"],
            "manifest_sha256": "sha256:" + row["manifest_sha256"].hex(),
            "created_at": row["created_at"],
        }

    async def freeze_in_transaction(self, connection, owner, project, *, kind, label=None):
        """Freeze approved references in a caller-owned consistent transaction."""
        repo = GlyphsRepository(connection)
        refs = await repo.snapshot_references(owner, project["id"])
        style = await repo.style_profile(project["id"])
        manifest = {
            "schema_version": 1,
            "glyphs": {str(row["id"]): str(row["active_revision_id"]) for row in refs},
            "style_controls": project["style_controls"],
            "style_profile_id": str(style["id"]) if style else None,
            "style_profile_snapshot": (
                {
                    "id": str(style["id"]),
                    "version": style["version"],
                    "metrics": style["metrics"],
                    "reference_glyph_ids": [str(x) for x in style["reference_glyph_ids"]],
                    "embedding_asset_id": str(style["embedding_asset_id"])
                    if style["embedding_asset_id"]
                    else None,
                    "model_version_id": str(style["model_version_id"])
                    if style["model_version_id"]
                    else None,
                }
                if style
                else None
            ),
            "project_row_version": project["row_version"],
            "coverage_context": await repo.snapshot_coverage_context(owner, project["id"]),
            "script_code": project["script_code"],
            "model_version_ids": sorted(
                {str(row["model_version_id"]) for row in refs if row["model_version_id"]}
            ),
        }
        if project["script_code"] == "Deva":
            manifest["required_set_id"] = project["required_set_id"]
            manifest["unicode_version"] = "17.0.0"
        row = await repo.insert_snapshot(
            {
                "id": self.ids(),
                "project_id": project["id"],
                "kind": kind,
                "label": label,
                "manifest": manifest,
                "manifest_sha256": bytes.fromhex(content_hash(manifest).removeprefix("sha256:")),
                "created_by_user_id": owner,
                "created_at": self.clock(),
            }
        )
        return row

    async def snapshot(
        self, principal, project_id: UUID, *, body: SnapshotCreate, idempotency_key: str
    ):
        async with self._transaction(repeatable=True) as connection:
            repo, owner, project = await self._scope(connection, principal, project_id)
            replay = await self._key(
                repo, owner, idempotency_key, f"snapshot:{project_id}", body.model_dump(mode="json")
            )
            if replay:
                return replay
            row = await self.freeze_in_transaction(
                connection, owner, project, kind="milestone", label=body.label
            )
            result = self._snapshot_dto(row)
            await repo.complete_key(
                owner,
                idempotency_key,
                {"snapshot_receipt": {"id": result["id"], "project_id": result["project_id"]}},
                201,
            )
            return result

    async def get_snapshot(self, principal, project_id: UUID, snapshot_id: UUID):
        async with self._transaction() as connection:
            repo, owner, _ = await self._scope(connection, principal, project_id)
            row = await repo.snapshot(owner, project_id, snapshot_id)
            if row is None:
                raise ApiError("not_found", 404)
            return self._snapshot_dto(row)

    @staticmethod
    def _style_dto(row):
        return {
            "id": str(row["id"]),
            "project_id": str(row["project_id"]),
            "version": row["version"],
            "source_snapshot_id": str(row["source_snapshot_id"]),
            "profile_hash": "sha256:" + row["profile_sha256"].hex(),
            "algorithm_version": row["algorithm_version"],
            "metrics": row["metrics"],
            "created_at": row["created_at"],
        }

    async def style_profile(self, principal, project_id):
        async with self._transaction() as connection:
            repo, _, _ = await self._scope(connection, principal, project_id)
            row = await repo.style_profile(project_id)
            if not row or row["profile_sha256"] is None:
                raise ApiError("not_found", 404)
            return self._style_dto(row)

    async def capabilities(self, principal, project_id):
        async with self._transaction() as con:
            _, _, project = await self._scope(con, principal, project_id)
            states = [
                (
                    "style_measurement",
                    "approved_snapshot",
                    "experimental_opt_in",
                    "Measures approved outlines; handwriting quality is unmeasured.",
                ),
                (
                    "contextual_alternates",
                    "approved_snapshot",
                    "experimental_opt_in",
                    "Needs distinct approved samples and a validated font. Some apps turn alternatives off.",
                ),
                (
                    "recognition",
                    "freeform_photo",
                    "research_only",
                    "Needs licensed handwriting and independent accuracy review.",
                ),
                (
                    "freeform_alignment",
                    "freeform_photo",
                    "research_only",
                    "Use isolated samples while connected writing is being studied.",
                ),
                (
                    "style_embedding",
                    "approved_snapshot",
                    "disabled",
                    "No qualified style model is available.",
                ),
                (
                    "glyph_generation",
                    "approved_snapshot",
                    "disabled",
                    "No qualified generator is available.",
                ),
            ]
            if project["script_code"] == "Deva":
                states = [
                    (
                        "standards_based_shaping_engineering",
                        "approved_snapshot",
                        "experimental_opt_in",
                        "Only tested text and reviewed marks/forms are supported.",
                    ),
                    (
                        "structured_extraction",
                        "structured_capture",
                        "experimental_opt_in",
                        "Review each captured character or cluster.",
                    ),
                    (
                        "recognition",
                        "freeform_photo",
                        "research_only",
                        "No qualified recognition model.",
                    ),
                    (
                        "freeform_cursive_segmentation",
                        "freeform_photo",
                        "research_only",
                        "Use isolated captures.",
                    ),
                    (
                        "writer_fidelity",
                        "approved_snapshot",
                        "research_only",
                        "Writer fidelity is unmeasured.",
                    ),
                    (
                        "corpus_derived_tier2_coverage",
                        "approved_snapshot",
                        "disabled",
                        "A licensed corpus and independent review are required.",
                    ),
                ]
            return {
                "items": [
                    {
                        "task": task,
                        "script": project["script_code"],
                        "style": style,
                        "input_class": input_class,
                        "state": state,
                        "reason": reason,
                    }
                    for task, input_class, state, reason in states
                    for style in ("print", "cursive", "mixed")
                ]
            }

    async def measure_style(self, principal, project_id, snapshot_id, key):
        from app.modules.glyphs.style_measurements import ALGORITHM, measure

        async with self._transaction() as connection:
            repo, owner, _ = await self._scope(connection, principal, project_id)
            # The same project/owner locks serialize erasure and publication. This
            # bounded descriptor runs in the owner transaction, with no worker grants.
            replay = await self._key(
                repo, owner, key, f"style:{project_id}", {"snapshot_id": str(snapshot_id)}
            )
            if replay:
                return replay
            snapshot = await repo.snapshot(owner, project_id, snapshot_id)
            if snapshot is None:
                raise ApiError("not_found", 404)
            if content_hash(snapshot["manifest"]) != "sha256:" + snapshot["manifest_sha256"].hex():
                raise ApiError("state_conflict", 409)
            references = []
            source_observations = {}
            geometry_budget = 0
            observation_budget = 0
            for gid, rid in sorted(snapshot["manifest"]["glyphs"].items()):
                glyph = await repo.glyph(owner, project_id, UUID(gid))
                if not glyph:
                    raise ApiError("not_found", 404)
                revisions = await repo.revisions(owner, project_id, UUID(gid))
                revision = next((r for r in revisions if str(r["id"]) == rid), None)
                if revision is None or revision["approval"] != "approved":
                    raise ApiError("state_conflict", 409)
                # Walk ancestry: editing/restoring a synthetic revision does not
                # make its provenance authentic. Missing provenance fails closed.
                history = {r["id"]: r for r in revisions}
                node, visited = revision, set()
                authentic = bool(revision["source_sample_id"])
                candidates = {
                    r["id"]: r for r in await repo.candidates(owner, project_id, UUID(gid))
                }
                while node:
                    if node["id"] in visited:
                        raise ApiError("state_conflict", 409)
                    visited.add(node["id"])
                    candidate = candidates.get(node["source_candidate_id"])
                    if node["origin"] == "ml_generated" or (
                        candidate and candidate["origin"] in SYNTHETIC
                    ):
                        authentic = False
                    node = history.get(node["parent_revision_id"])
                if not authentic:
                    continue
                geometry = await self._outline(repo, owner, project_id, revision)
                geometry_budget += sum(len(c["segments"]) * 16 for c in geometry["contours"])
                if geometry_budget > 500000:
                    raise ApiError("payload_too_large", 413)
                sid = revision["source_sample_id"]
                if sid not in source_observations:
                    asset = await repo.profile_source(owner, project_id, sid)
                    observation = None
                    if asset and asset["kind"] == "raw_strokes" and self.storage:
                        from penforge_contracts import StrokeDocument

                        data = await asyncio.to_thread(
                            self.storage.read_object, asset["storage_key"], max_bytes=24_000_000
                        )
                        if (
                            hashlib.sha256(data).digest() != asset["sha256"]
                            or len(data) != asset["size_bytes"]
                        ):
                            raise ApiError("upload_mismatch", 400)
                        document = StrokeDocument.model_validate_json(data)
                        observation_budget += sum(len(s.points) for s in document.strokes)
                        if observation_budget > 250000:
                            raise ApiError("payload_too_large", 413)
                        if document.timing == "observed":
                            intervals: list[tuple[float, float]] = []
                            for stroke in document.strokes:
                                start, end = stroke.points[0].t_ms, stroke.points[-1].t_ms
                                assert start is not None and end is not None
                                intervals.append((start, end))
                            observation = {
                                "source_hash": "sha256:" + asset["sha256"].hex(),
                                "stroke_durations_ms": [b - a for a, b in intervals],
                                "interstroke_gaps_ms": [
                                    c - b for (a, b), (c, d) in zip(intervals, intervals[1:])
                                ],
                                "observed_pressure": [
                                    p.pressure
                                    for s in document.strokes
                                    for p in s.points
                                    if p.pressure is not None
                                ],
                            }
                    source_observations[sid] = observation
                references.append(
                    {
                        "glyph_id": gid,
                        "revision_id": rid,
                        "revision_hash": "sha256:" + revision["content_hash"].hex(),
                        "source_sample_id": str(revision["source_sample_id"]),
                        "codepoints": glyph["codepoints"],
                        "variant_index": glyph["variant_index"],
                        "outline": geometry,
                    }
                )
                if source_observations[sid]:
                    references[-1]["capture_observation"] = source_observations[sid]
            if not references or len(references) > 512:
                raise ApiError("state_conflict", 409)
            metrics, digest, _ = await asyncio.to_thread(
                measure, "sha256:" + snapshot["manifest_sha256"].hex(), references
            )
            raw_hash = bytes.fromhex(digest[7:])
            row = await repo.profile_identity(project_id, raw_hash)
            if row is None:
                previous = await repo.style_profile(project_id)
                row = await repo.insert_style(
                    {
                        "id": self.ids(),
                        "project_id": project_id,
                        "version": previous["version"] + 1 if previous else 1,
                        "metrics": metrics,
                        "source_snapshot_id": snapshot_id,
                        "profile_sha256": raw_hash,
                        "algorithm_version": ALGORITHM,
                        "created_by_user_id": owner,
                        "reference_glyph_ids": [UUID(r["glyph_id"]) for r in references],
                    }
                )
            result = self._style_dto(row)
            # Store a compact immutable receipt; geometry is already in the profile.
            await repo.complete_key(owner, key, {"style_profile_receipt": str(row["id"])}, 201)
            return result

    async def add_alternate(self, principal, project_id, glyph_id, body, if_match, key):
        version = self._version(if_match)
        async with self._transaction() as con:
            repo, owner, project = await self._scope(con, principal, project_id)
            base = await repo.glyph(owner, project_id, glyph_id, lock=True)
            if not base or base["variant_index"] != 0 or base["codepoints"] == [32]:
                raise ApiError("not_found", 404)
            replay = await self._key(
                repo, owner, key, f"alternate:{project_id}:{glyph_id}", body.model_dump(mode="json")
            )
            if replay:
                return replay
            revisions = await repo.revisions(owner, project_id, glyph_id)
            self._check_base(base, revisions, version, body.base_revision_id)
            source = next(
                (
                    r
                    for r in revisions
                    if r["id"] == body.source_revision_id and r["approval"] == "approved"
                ),
                None,
            )
            if source is None or source["source_sample_id"] is None:
                raise ApiError("state_conflict", 409)
            history, node, visited = {r["id"]: r for r in revisions}, source, set()
            candidates = {r["id"]: r for r in await repo.candidates(owner, project_id, glyph_id)}
            while node:
                candidate = candidates.get(node["source_candidate_id"])
                if (
                    node["id"] in visited
                    or node["origin"] == "ml_generated"
                    or (candidate and candidate["origin"] in SYNTHETIC)
                ):
                    raise ApiError("state_conflict", 409)
                visited.add(node["id"])
                node = history.get(node["parent_revision_id"])
            variants = [
                g
                for g in await repo.glyphs(owner, project_id, limit=100000)
                if g["codepoints"] == base["codepoints"]
            ]
            source_hash = await repo.sample_hash(owner, project_id, source["source_sample_id"])
            if source_hash is None:
                raise ApiError("state_conflict", 409)
            for variant in variants:
                rid = variant["active_revision_id"]
                # The primary and each sibling must represent distinct captures.
                for r in await repo.revisions(owner, project_id, variant["id"]):
                    if r["id"] == rid or (
                        variant["variant_index"] > 0 and r["approval"] == "pending"
                    ):
                        if (
                            await repo.sample_hash(owner, project_id, r["source_sample_id"])
                            == source_hash
                        ):
                            raise ApiError("state_conflict", 409)
            if len(variants) >= 3:
                raise ApiError("state_conflict", 409)
            geometry = await self._outline(repo, owner, project_id, source)
            manifest = {
                "schema_version": 1,
                "glyphs": {str(glyph_id): str(source["id"])},
                "script_code": project["script_code"],
                "style_controls": project["style_controls"],
                "model_version_ids": [],
                "alternate_source": True,
            }
            snapshot = await repo.insert_snapshot(
                {
                    "id": self.ids(),
                    "project_id": project_id,
                    "kind": "auto",
                    "manifest": manifest,
                    "manifest_sha256": bytes.fromhex(content_hash(manifest)[7:]),
                    "created_by_user_id": owner,
                }
            )
            alternate = await repo.insert_glyph(
                {
                    "id": self.ids(),
                    "project_id": project_id,
                    "codepoints": base["codepoints"],
                    "variant_index": max(g["variant_index"] for g in variants) + 1,
                    "coverage_status": "needs_review",
                }
            )
            await repo.insert_revision(
                {
                    "id": self.ids(),
                    "glyph_id": alternate["id"],
                    "seq": 1,
                    "origin": source["origin"],
                    "approval": "pending",
                    "outline_inline": source["outline_inline"],
                    "outline_asset_id": source["outline_asset_id"],
                    "metrics": geometry["metrics"],
                    "content_hash": source["content_hash"],
                    "source_sample_id": source["source_sample_id"],
                    "alternate_source_revision_id": source["id"],
                    "alternate_source_snapshot_id": snapshot["id"],
                    "created_by_user_id": owner,
                }
            )
            await repo.update_glyph(project_id, glyph_id, version, updated_at=self.clock())
            return await self._finish(repo, owner, project_id, alternate["id"], key)

    async def enable_alternate(self, principal, project_id, glyph_id, enabled, if_match):
        version = self._version(if_match)
        async with self._transaction() as con:
            repo, owner, _ = await self._scope(con, principal, project_id)
            glyph = await repo.glyph(owner, project_id, glyph_id, lock=True)
            if not glyph or glyph["variant_index"] == 0:
                raise ApiError("not_found", 404)
            if glyph["row_version"] != version or not glyph["active_revision_id"]:
                raise ApiError("revision_conflict", 412)
            await repo.update_glyph(
                project_id,
                glyph_id,
                version,
                coverage_status="approved" if enabled else "excluded",
                updated_at=self.clock(),
            )
            return await self._detail(
                repo, owner, project_id, await repo.glyph(owner, project_id, glyph_id)
            )

    async def coverage(self, principal, project_id: UUID):
        async with self._transaction() as connection:
            repo, owner, project = await self._scope(connection, principal, project_id)
            if project["script_code"] == "Deva":
                from penforge_contracts.devanagari import coverage_evidence, tier1

                approved_rows = []
                observed = set()
                pending = set()
                for glyph in await repo.glyphs(owner, project_id, limit=100000):
                    candidates = await repo.candidates(owner, project_id, glyph["id"])
                    if candidates:
                        observed.update(glyph["codepoints"])
                    if glyph["coverage_status"] == "needs_review":
                        pending.update(glyph["codepoints"])
                    if glyph["active_revision_id"]:
                        revisions = await repo.revisions(owner, project_id, glyph["id"])
                        rev = next(
                            (
                                r
                                for r in revisions
                                if r["id"] == glyph["active_revision_id"]
                                and r["approval"] == "approved"
                            ),
                            None,
                        )
                        if rev:
                            approved_rows.append(
                                dict(
                                    approval="approved",
                                    variant_index=glyph["variant_index"],
                                    outline=await self._outline(repo, owner, project_id, rev),
                                )
                            )
                details = coverage_evidence(approved_rows)
                mapped = details["mapped_codepoints"]
                return dict(
                    script_code="Deva",
                    core_required=0,
                    ascii_required=0,
                    core_approved=0,
                    ascii_approved=len(mapped),
                    core_complete=False,
                    ascii_inventory_complete=False,
                    complete_font_validated=False,
                    observed=sorted(observed),
                    recommended=sorted(observed),
                    user_approved=mapped,
                    generated_pending=[],
                    missing=sorted(tier1() - set(mapped)),
                    rejected=[],
                    excluded=[],
                    warnings=details["warnings"],
                    script_details=details,
                )
            if project["script_code"] != "Latn":
                raise ApiError("capability_unavailable", 503)
            sets: dict[str, set[int]] = {
                name: set()
                for name in (
                    "observed",
                    "recommended",
                    "user_approved",
                    "generated_pending",
                    "rejected",
                    "excluded",
                )
            }
            rows = await repo.glyphs(owner, project_id, limit=100000)
            for glyph in rows:
                if (
                    len(glyph["codepoints"]) != 1
                    or glyph["variant_index"] != 0
                    or glyph["codepoints"][0] not in ASCII
                ):
                    continue
                point = glyph["codepoints"][0]
                revisions = await repo.revisions(owner, project_id, glyph["id"])
                candidates = await repo.candidates(owner, project_id, glyph["id"])
                if any(row["origin"] not in SYNTHETIC for row in candidates):
                    sets["observed"].add(point)
                if any(
                    row["origin"] not in SYNTHETIC and row["review_status"] != "rejected"
                    for row in candidates
                ):
                    sets["recommended"].add(point)
                if any(
                    row["origin"] in SYNTHETIC and row["review_status"] == "pending"
                    for row in candidates
                ):
                    sets["generated_pending"].add(point)
                if glyph["active_revision_id"] and any(
                    row["id"] == glyph["active_revision_id"] and row["approval"] == "approved"
                    for row in revisions
                ):
                    sets["user_approved"].add(point)
                if any(row["approval"] == "rejected" for row in revisions) or any(
                    row["review_status"] == "rejected" for row in candidates
                ):
                    sets["rejected"].add(point)
                if glyph["coverage_status"] == "excluded":
                    sets["excluded"].add(point)
            approved = sets["user_approved"]
            return {
                "script_code": "Latn",
                "core_required": 52,
                "ascii_required": 95,
                "core_approved": len(CORE & approved),
                "ascii_approved": len(ASCII & approved),
                "core_complete": CORE <= approved,
                "ascii_inventory_complete": ASCII <= approved,
                "complete_font_validated": False,
                **{name: sorted(values) for name, values in sets.items()},
                "missing": sorted(ASCII - approved),
                "warnings": (["missing_glyphs"] if ASCII - approved else [])
                + ["font_export_unavailable", "real_handwriting_accuracy_unmeasured"],
            }

    async def prompts(self, principal, project_id: UUID):
        from app.modules.analysis.service import calibration_policy

        definition = calibration_policy()
        coverage = await self.coverage(principal, project_id)
        if coverage["script_code"] == "Deva":
            from pathlib import Path

            from penforge_contracts import CalibrationDefinition

            value = CalibrationDefinition.model_validate_json(
                (
                    Path(__file__).resolve().parents[5]
                    / "packages/contracts/policies/deva-calibration.v1.json"
                ).read_bytes()
            ).model_dump(mode="json")
            targets = coverage["missing"][:12]
            return dict(
                script_code="Deva",
                definition_version=value["version"],
                definition_sha256=content_hash(value),
                initial_passage=value["prompt_text"],
                passage_status="unoptimized_example_not_ground_truth",
                capture_type="isolated_characters",
                codepoints=targets,
                prompt_text=" ".join(chr(c) for c in targets),
                reason_codes=["missing_or_unapproved_characters", "engineering_prompt_unreviewed"],
                prompt_is_ground_truth=False,
                natural_cursive_status="experimental_unavailable",
                expected_codepoints=targets,
                observed_approved_codepoints=coverage["user_approved"],
                evidence_hash=content_hash(
                    {
                        "approved": coverage["user_approved"],
                        "missing": coverage["missing"],
                        "policy": "deva-engineering-v1",
                    }
                ),
            )
        missing = coverage["missing"]
        reasons: dict[int, list[str]] = {cp: ["missing_or_unapproved_glyph"] for cp in missing}
        uncertain: set[int] = set()
        samples: dict[int, set[str]] = {}
        async with self._transaction() as connection:
            repo, owner, _ = await self._scope(connection, principal, project_id)
            for glyph in await repo.glyphs(owner, project_id, limit=100000):
                if len(glyph["codepoints"]) != 1:
                    continue
                cp = glyph["codepoints"][0]
                for candidate in await repo.candidates(owner, project_id, glyph["id"]):
                    if candidate["origin"] in SYNTHETIC or candidate["review_status"] == "rejected":
                        continue
                    if candidate["review_status"] == "pending":
                        uncertain.add(cp)
                    elif candidate["review_status"] == "approved":
                        samples.setdefault(cp, set()).add(str(candidate["source_sample_id"]))
        for cp in uncertain:
            reasons.setdefault(cp, []).append("uncertain_assignment")
        for cp in coverage["user_approved"]:
            if cp == 32:
                continue
            if len(samples.get(cp, set())) < 2:
                reasons.setdefault(cp, []).append("missing_authentic_alternative")
            # No line/word aligner is qualified. Isolated observations cannot
            # establish joining/context coverage, even for approved base letters.
            reasons.setdefault(cp, []).append("context_not_observed")
        order = sorted(
            reasons,
            key=lambda cp: (
                0
                if cp in uncertain
                else 1
                if cp in CORE and cp in missing
                else 2
                if cp in missing
                else 3,
                cp,
            ),
        )
        targets = order[: definition["maximum_followup_codepoints"]]
        return {
            "script_code": "Latn",
            "definition_version": definition["definition_version"],
            "definition_sha256": content_hash(definition),
            "initial_passage": definition["initial_passage"],
            "passage_status": definition["passage_status"],
            "capture_type": "isolated_characters",
            "codepoints": targets,
            "prompt_text": " ".join(
                chr(point) if point != 32 else "[space metric]" for point in targets
            ),
            "reason_codes": [
                "missing_or_unapproved_glyphs",
                "isolated_samples_preserve_evidence",
                "natural_cursive_segmentation_unvalidated",
            ]
            if targets
            else ["ascii_inventory_complete_export_unvalidated"],
            "prompt_is_ground_truth": False,
            "natural_cursive_status": "experimental_unavailable",
            "prompt_policy_version": "coverage-followup-v2",
            "selection_reasons": {str(cp): reasons[cp] for cp in targets},
            "expected_codepoints": targets,
            "observed_approved_codepoints": coverage["user_approved"],
            "evidence_hash": content_hash(
                {
                    "approved": coverage["user_approved"],
                    "missing": missing,
                    "uncertain": sorted(uncertain),
                    "distinct_approved_samples": {
                        str(cp): sorted(ids) for cp, ids in sorted(samples.items())
                    },
                }
            ),
        }
