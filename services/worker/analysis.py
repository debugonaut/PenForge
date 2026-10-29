"""Track A/C execution; the API service validates and fences every saved result."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path
from uuid import UUID, uuid4, uuid5

from app.adapters.storage.base import StorageError
from app.core.errors import ApiError
from app.modules.analysis.policy import image_config, pipeline, policy_hash
from app.modules.analysis.service import AnalysisArtifact, AnalysisService, json_bytes
from app.modules.jobs.service import checkpoint_store
from penforge_contracts import MLResultManifest, content_hash
from penforge_contracts.schemas import GlyphCandidate

from packages.capture_safety.decoder import DecoderUnavailable, ImageRejected
from packages.extraction.isolated import analyze_isolated
from services.worker.checkpoints import dependencies


class AnalysisWorker:
    def __init__(
        self,
        service: AnalysisService,
        *,
        worker_id: str,
        temp_root: Path,
        heartbeat_seconds: float = 20,
        memory_mb: int = 768,
    ):
        self.service, self.storage, self.worker_id = service, service.storage, worker_id
        self.stop = asyncio.Event()
        self.deadline = float("inf")
        self.temp_root, self.heartbeat_seconds, self.memory_mb = (
            temp_root,
            heartbeat_seconds,
            memory_mb,
        )

    async def reap_temporary(self):
        self.temp_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        for path in self.temp_root.iterdir():
            match = re.fullmatch(
                r"penforge-analysis-([0-9a-f-]{36})-a([1-9][0-9]*)-[A-Za-z0-9_-]+", path.name
            )
            if (
                match
                and not path.is_symlink()
                and path.is_dir()
                and path.stat().st_uid == os.getuid()
            ):
                if not await self.service.attempt_active(UUID(match[1]), int(match[2])):
                    await asyncio.to_thread(shutil.rmtree, path)

    async def run_once(self) -> bool:
        await self.service.reap_expired()
        await self.reap_temporary()
        job = await self.service.claim(self.worker_id, worker_mem_mb=self.memory_mb)
        if job is None:
            return False
        await self.execute(job)
        return True

    async def execute(self, job: dict) -> bool:
        lost = threading.Event()
        completed = 0
        started = time.monotonic()

        def check():
            if lost.is_set():
                raise ImageRejected("analysis_lease_lost")

        async def heartbeat():
            while True:
                await asyncio.sleep(min(0.5, self.heartbeat_seconds))
                try:
                    valid = await self.service.heartbeat(job, self.worker_id, completed)
                except Exception:
                    valid = False
                if not valid:
                    lost.set()
                    return

        beat = asyncio.create_task(heartbeat())
        try:
            self.temp_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            artifacts: list[AnalysisArtifact] = []

            def add(data, kind, mime):
                item = AnalysisArtifact(uuid4(), kind, mime, data)
                artifacts.append(item)
                if sum(len(a.data) for a in artifacts) > 32 * 1024 * 1024:
                    raise ValueError("artifact_budget_exceeded")
                return dict(
                    artifact_id=str(item.artifact_id),
                    sha256=item.digest,
                    size_bytes=len(data),
                    media_type=mime,
                )

            candidates, assessments, evidence = [], [], {}
            ranks: dict[tuple[int, ...], int] = {}
            assignments = {x["sample_id"]: x for x in job["payload"].get("assignments", [])}
            with tempfile.TemporaryDirectory(
                prefix=f"penforge-analysis-{job['id']}-a{job['attempts']}-", dir=self.temp_root
            ) as temporary:
                for index, source in enumerate(job["_sources"]):
                    check()
                    if self.stop.is_set() or time.monotonic() >= self.deadline:
                        from app.modules.jobs.service import JobsService

                        await JobsService(self.service.uploads).boundary(
                            job, self.worker_id, shutdown=True
                        )
                        return False
                    sample, asset = source["sample"], source["asset"]
                    sid = str(sample["id"])
                    work = Path(temporary) / f"sample-{index}"
                    work.mkdir(mode=0o700)
                    data = await asyncio.to_thread(
                        self.storage.read_object, asset["storage_key"], max_bytes=24_000_000
                    )
                    source_hash = "sha256:" + hashlib.sha256(data).hexdigest()
                    if source_hash != "sha256:" + sample["source_sha256"].hex():
                        raise ValueError("source_hash_mismatch")
                    (work / "source").write_bytes(data)
                    config = dict(
                        source_type=sample["source_type"],
                        source_hash=source_hash,
                        assignment=assignments.get(sid),
                        image_config=image_config(),
                    )
                    if "drawing_mode" in job["payload"]:
                        config["drawing_mode"] = job["payload"]["drawing_mode"]
                    if "image_mode" in job["payload"]:
                        config["image_mode"] = job["payload"]["image_mode"]
                        config["image_regions"] = [
                            region
                            for region in job["payload"]["image_regions"]
                            if region["sample_id"] == sid
                        ]
                    if sample["template_id"]:
                        manifest = self.service.manifest(sample, job["payload"]["script_code"])
                        config.update(
                            manifest=manifest.model_dump(mode="json"),
                            manifest_hash="sha256:" + sample["template_manifest_sha256"].hex(),
                        )
                    checkpoints = checkpoint_store(self.service.uploads)
                    deps = dependencies(
                        [source_hash],
                        config.get("manifest_hash"),
                        config,
                        pipeline(
                            job["payload"]["script_code"],
                            job["payload"].get("drawing_mode"),
                            job["payload"].get("image_mode"),
                        )["algorithm_version"],
                    )
                    job["_failure_input_hash"] = content_hash(deps)
                    cached = await checkpoints.load(job, self.worker_id, f"sample-{index}", deps)
                    if cached:
                        import json

                        report = json.loads(cached.pop("report"))
                        for name, data in cached.items():
                            (work / name).write_bytes(data)
                    else:
                        report = await asyncio.to_thread(
                            analyze_isolated,
                            work,
                            config,
                            cancel_check=check,
                            memory_mb=self.memory_mb,
                        )
                        checkpoint_outputs = {"report": ("application/json", json_bytes(report))}
                        for item in report["files"]:
                            checkpoint_outputs[item["name"]] = (
                                item["media_type"],
                                (work / item["name"]).read_bytes(),
                            )
                        if not await checkpoints.save(
                            job, self.worker_id, f"sample-{index}", deps, checkpoint_outputs
                        ):
                            return False
                    refs = {}
                    for item in report["files"]:
                        name = item["name"]
                        if not re.fullmatch(r"output-[0-9]+", name):
                            raise ValueError("analysis_protocol_error")
                        path = work / name
                        if (
                            path.is_symlink()
                            or not 0 < item["size_bytes"] <= 32 * 1024 * 1024
                            or path.stat().st_size != item["size_bytes"]
                        ):
                            raise ValueError("analysis_protocol_error")
                        blob = path.read_bytes()
                        if "sha256:" + hashlib.sha256(blob).hexdigest() != item["sha256"]:
                            raise ValueError("analysis_protocol_error")
                        refs[name] = add(blob, item["kind"], item["media_type"])
                    quality = report["quality"]
                    if "normalized_artifact" in quality:
                        quality["normalized_artifact"] = refs[quality["normalized_artifact"]]
                    assessments.append(
                        dict(
                            sample_id=sid,
                            state=report["state"],
                            reasons=report["reasons"],
                            quality=quality,
                            resources=report["resources"],
                            isolation=report["isolation"],
                        )
                    )
                    for item in report["candidates"]:
                        cp = tuple(item["codepoints"])
                        ranks[cp] = ranks.get(cp, 0) + 1
                        cid = str(
                            uuid5(
                                job["id"],
                                f"attempt:{job['attempts']}:{sid}:"
                                f"{item.get('region_id', item.get('cell_id', 'drawing'))}",
                            )
                        )
                        candidates.append(
                            dict(
                                candidate_id=cid,
                                codepoints=list(cp),
                                rank=ranks[cp],
                                origin="manual_drawing"
                                if sample["source_type"] == "canvas_drawing"
                                else "authentic_extraction",
                                outline=refs[item["outline"]],
                                evidence=dict(outline_validity="pass"),
                            )
                        )
                        evidence[cid] = dict(
                            sample_id=sid,
                            flags=item["flags"],
                            source_hash=source_hash,
                            expected_text_is_ground_truth=False,
                            **{
                                k: v
                                for k, v in item.items()
                                if k in {"cell_id", "region_id", "provenance"}
                            },
                        )
                        for key in ("crop_artifact", "mask_artifact"):
                            if key in item:
                                evidence[cid][key] = refs[item[key]]
                    job["_failure_input_hash"] = content_hash(deps)
                    completed += 1
                    if not await self.service.heartbeat(job, self.worker_id, completed):
                        return False
            quality = add(
                json_bytes(
                    dict(
                        schema_version=1,
                        samples=assessments,
                        candidates=evidence,
                        policy_hash=policy_hash(
                            job["payload"]["script_code"],
                            job["payload"].get("drawing_mode"),
                            job["payload"].get("image_mode"),
                        ),
                    )
                ),
                "validation_report",
                "application/json",
            )
            manifest = dict(
                schema_version=1,
                artifact_type="penforge.ml_result",
                job_id=str(job["id"]),
                attempt_id=str(uuid5(job["id"], f"attempt:{job['attempts']}")),
                fencing_token=job["attempts"],
                stage="candidate_extraction",
                input=dict(
                    sample_ids=job["payload"]["sample_ids"],
                    source_hashes=[
                        "sha256:" + x["sample"]["source_sha256"].hex() for x in job["_sources"]
                    ],
                    template_manifest_hash=job["payload"].get("template_manifest_hash"),
                ),
                pipeline=pipeline(
                    job["payload"]["script_code"],
                    job["payload"].get("drawing_mode"),
                    job["payload"].get("image_mode"),
                ),
                candidates=[
                    GlyphCandidate.model_validate(item).model_dump(mode="json")
                    for item in candidates
                ],
                quality_report=quality,
            )
            manifest["artifact_hash"] = content_hash(manifest)
            encoded = MLResultManifest.model_validate(manifest).model_dump_json().encode()
            ref = add(encoded, "validation_report", "application/json")
            if not await self.service.stage(job, self.worker_id, artifacts):
                return False
            from app.modules.jobs.service import measured_usage

            if not await measured_usage(
                self.service.uploads, job, self.worker_id, time.monotonic() - started
            ):
                return False
            return await self.service.publish(job, self.worker_id, UUID(ref["artifact_id"]))
        except (
            ValueError,
            KeyError,
            OSError,
            StorageError,
            DecoderUnavailable,
            ImageRejected,
            ApiError,
        ) as error:
            resource_shortage = (
                isinstance(error, ImageRejected) and error.code == "analysis_resource_limit"
            )
            retry = (
                resource_shortage
                or isinstance(error, (StorageError, DecoderUnavailable, OSError))
                or isinstance(error, ApiError)
                and error.status == 503
            )
            from app.modules.jobs.service import measured_usage

            await measured_usage(
                self.service.uploads, job, self.worker_id, time.monotonic() - started
            )
            await self.service.fail(
                job,
                self.worker_id,
                "resource_unavailable"
                if resource_shortage
                else "dependency_unavailable"
                if retry
                else "invalid_input",
                retryable=retry,
            )
            return False
        finally:
            from app.modules.jobs.service import JobsService, measured_usage

            await measured_usage(
                self.service.uploads, job, self.worker_id, time.monotonic() - started
            )
            await JobsService(self.service.uploads).boundary(job, self.worker_id)
            beat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await beat
