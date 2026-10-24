"""Immutable content-addressed stage units, with owner/lease and dependency validation."""

import asyncio
import hashlib
import json
import re
from datetime import timedelta
from uuid import UUID, uuid4

import sqlalchemy as sa
from penforge_contracts import content_hash

from app.adapters.storage.base import StorageError
from app.core.errors import ApiError
from app.db import models as m
from app.modules.jobs import repository


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def identity(stage, dependencies):
    required = {"inputs", "template", "model", "configuration", "algorithm", "dependencies"}
    if set(dependencies) != required or not re.fullmatch(r"[a-z0-9_-]{1,80}", stage):
        raise ValueError("checkpoint_dependencies_incomplete")
    return content_hash({"schema_version": 1, "stage": stage, "dependencies": dependencies})


class Checkpoints:
    def __init__(self, jobs):
        self.jobs, self.uploads, self.storage = jobs, jobs.uploads, jobs.uploads.storage

    async def save(self, job, holder, stage, dependencies, outputs):
        from app.modules.jobs.service import live_job

        cache_key = identity(stage, dependencies)
        if (
            not outputs
            or len(outputs) > 1024
            or sum(len(v[1]) for v in outputs.values()) > 32 * 1024 * 1024
        ):
            raise ValueError("checkpoint_budget_exceeded")
        refs, blobs = [], {}
        for name, (mime, data) in sorted(outputs.items()):
            if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", name) or mime not in {
                "application/json",
                "image/png",
                "font/ttf",
                "font/otf",
                "font/woff2",
            }:
                raise ValueError("checkpoint_media_type")
            aid = uuid4()
            refs.append(
                dict(
                    name=name,
                    asset_id=str(aid),
                    media_type=mime,
                    size_bytes=len(data),
                    sha256="sha256:" + hashlib.sha256(data).hexdigest(),
                )
            )
            blobs[aid] = data
        manifest = dict(
            schema_version=1,
            artifact_type="penforge.checkpoint",
            job_id=str(job["id"]),
            owner_user_id=str(job["owner_user_id"]),
            project_id=str(job["project_id"]),
            stage=stage,
            cache_key=cache_key,
            dependencies=dependencies,
            outputs=refs,
        )
        manifest_id = uuid4()
        manifest_bytes = encoded(manifest)
        # Stage all immutable outputs first, then manifest, then fenced DB reference.
        blobs[manifest_id] = manifest_bytes
        async with self.jobs.engine.begin() as con:
            current = await live_job(con, job, holder)
            if current is None:
                return False
            existing = await con.scalar(
                sa.select(m.job_checkpoints.c.id).where(
                    m.job_checkpoints.c.job_id == job["id"],
                    m.job_checkpoints.c.stage == stage,
                    m.job_checkpoints.c.cache_key == cache_key,
                )
            )
            if existing:
                return True
            for aid, data in blobs.items():
                mime = (
                    "application/json"
                    if aid == manifest_id
                    else next(r["media_type"] for r in refs if r["asset_id"] == str(aid))
                )
                await self.uploads.stage_worker_artifact(
                    con,
                    job["owner_user_id"],
                    job["project_id"],
                    artifact_id=aid,
                    storage_key=f"checkpoints/{job['id'].hex}/{cache_key[7:]}/{aid.hex}",
                    size_bytes=len(data),
                    sha256=hashlib.sha256(data).digest(),
                    kind="checkpoint",
                    mime_type=mime,
                )
            await con.execute(
                m.jobs.update()
                .where(m.jobs.c.id == job["id"])
                .values(
                    checkpoint=dict(current["checkpoint"])
                    | {"checkpoint_staged_asset_ids": [str(x) for x in blobs]}
                )
            )
        # Ledger survives a crash even before the first byte is written.
        async with self.jobs.engine.begin() as con:
            current = await live_job(con, job, holder)
            if current is None:
                return False
            for aid, data in blobs.items():
                if current["lease_expires_at"] <= await self.jobs.now(con):
                    return False
                key = f"checkpoints/{job['id'].hex}/{cache_key[7:]}/{aid.hex}"
                await asyncio.to_thread(self.storage.put_immutable, key, data)
            for aid in blobs:
                await self.uploads.publish_worker_artifact(con, job["owner_user_id"], aid)
            cleared = dict(current["checkpoint"])
            cleared.pop("checkpoint_staged_asset_ids", None)
            await con.execute(
                m.jobs.update().where(m.jobs.c.id == job["id"]).values(checkpoint=cleared)
            )
            config = await repository.policy(con)
            await con.execute(
                m.job_checkpoints.insert().values(
                    id=uuid4(),
                    job_id=job["id"],
                    owner_user_id=job["owner_user_id"],
                    project_id=job["project_id"],
                    stage=stage,
                    cache_key=cache_key,
                    manifest_asset_id=manifest_id,
                    manifest_sha256=hashlib.sha256(manifest_bytes).digest(),
                    dependencies=dependencies,
                    attempt=job["attempts"],
                    lease_holder=holder,
                    expires_at=await self.jobs.now(con)
                    + timedelta(seconds=config.checkpoint_ttl_seconds),
                )
            )
            await con.execute(
                m.job_events.insert().values(
                    job_id=job["id"],
                    kind="stage",
                    stage=stage,
                    metadata={"checkpoint_verified": True, "completed_units": 1, "total_units": 1},
                )
            )
        return True

    async def load(self, job, holder, stage, dependencies):
        from app.modules.jobs.service import live_job

        cache_key = identity(stage, dependencies)
        async with self.jobs.engine.begin() as con:
            if await live_job(con, job, holder) is None:
                return None
            row = (
                (
                    await con.execute(
                        sa.select(m.job_checkpoints).where(
                            m.job_checkpoints.c.job_id == job["id"],
                            m.job_checkpoints.c.owner_user_id == job["owner_user_id"],
                            m.job_checkpoints.c.project_id == job["project_id"],
                            m.job_checkpoints.c.stage == stage,
                            m.job_checkpoints.c.cache_key == cache_key,
                            m.job_checkpoints.c.expires_at > sa.func.clock_timestamp(),
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if not row:
                return None

            async def read(aid, digest, mime, size=None):
                asset = (
                    (
                        await con.execute(
                            sa.select(m.assets).where(
                                m.assets.c.id == UUID(str(aid)),
                                m.assets.c.owner_user_id == job["owner_user_id"],
                                m.assets.c.project_id == job["project_id"],
                                m.assets.c.kind == "checkpoint",
                                m.assets.c.state == "ready",
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if (
                    not asset
                    or asset["mime_type"] != mime
                    or bytes(asset["sha256"]).hex() != digest.removeprefix("sha256:")
                    or (size is not None and asset["size_bytes"] != size)
                ):
                    raise ValueError("checkpoint_binding_mismatch")
                data = await asyncio.to_thread(
                    self.storage.read_object, asset["storage_key"], max_bytes=32 * 1024 * 1024
                )
                if len(data) != asset["size_bytes"] or hashlib.sha256(data).digest() != bytes(
                    asset["sha256"]
                ):
                    raise ValueError("checkpoint_hash_mismatch")
                return data

            try:
                raw = await read(
                    row["manifest_asset_id"], row["manifest_sha256"].hex(), "application/json"
                )
                manifest = json.loads(raw)
                if (
                    set(manifest)
                    != {
                        "schema_version",
                        "artifact_type",
                        "job_id",
                        "owner_user_id",
                        "project_id",
                        "stage",
                        "cache_key",
                        "dependencies",
                        "outputs",
                    }
                    or manifest["schema_version"] != 1
                    or manifest["artifact_type"] != "penforge.checkpoint"
                    or manifest["job_id"] != str(job["id"])
                    or manifest["owner_user_id"] != str(job["owner_user_id"])
                    or manifest["project_id"] != str(job["project_id"])
                    or manifest["stage"] != stage
                    or manifest["cache_key"] != cache_key
                    or manifest["dependencies"] != dependencies
                    or row["dependencies"] != dependencies
                    or not 0 < len(manifest["outputs"]) <= 1024
                ):
                    raise ValueError("checkpoint_manifest_mismatch")
                outputs = {}
                total_bytes = 0
                for ref in manifest["outputs"]:
                    if (
                        set(ref) != {"name", "asset_id", "media_type", "size_bytes", "sha256"}
                        or ref["name"] in outputs
                    ):
                        raise ValueError("checkpoint_reference_invalid")
                    if (
                        not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", ref["name"])
                        or type(ref["size_bytes"]) is not int
                        or not 0 < ref["size_bytes"] <= 32 * 1024 * 1024
                        or not isinstance(ref["sha256"], str)
                        or not re.fullmatch(r"sha256:[0-9a-f]{64}", ref["sha256"])
                        or ref["media_type"]
                        not in {
                            "application/json",
                            "image/png",
                            "font/ttf",
                            "font/otf",
                            "font/woff2",
                        }
                    ):
                        raise ValueError("checkpoint_output_schema")
                    total_bytes += ref["size_bytes"]
                    if total_bytes > 32 * 1024 * 1024:
                        raise ValueError("checkpoint_budget_exceeded")
                    outputs[ref["name"]] = await read(
                        ref["asset_id"], ref["sha256"], ref["media_type"], ref["size_bytes"]
                    )
                return outputs
            except (ValueError, KeyError, TypeError, OSError, StorageError):
                # Fail safely; immutable bad evidence is retained for reconciliation.
                raise ApiError("checkpoint_invalid", 409) from None

    async def reconcile(self):
        async with self.jobs.engine.connect() as con:
            accepted = set(
                (await con.execute(sa.select(m.job_checkpoints.c.manifest_asset_id))).scalars()
            )
            rows = list(
                (
                    await con.execute(sa.select(m.assets).where(m.assets.c.kind == "checkpoint"))
                ).mappings()
            )
        return {
            "dry_run": True,
            "accepted_manifests": len(accepted),
            "pending_assets": [str(a["id"]) for a in rows if a["state"] == "pending_upload"],
            "missing_objects": [
                str(a["id"])
                for a in rows
                if not await asyncio.to_thread(self._exists, a["storage_key"])
            ],
            "deleted": 0,
        }

    def _exists(self, key):
        from app.adapters.storage.base import ObjectMissing

        try:
            self.storage.get_metadata(key)
            return True
        except ObjectMissing:
            return False
