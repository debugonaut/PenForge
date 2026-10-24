"""Bounded lazy artifact verification; no executable/framework deserialization."""

import hashlib
import json
import math
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field


class RegistryRejected(ValueError):
    """Content-free rejection codes only; never include artifact locations/bytes."""


class RegistryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    task: Literal["segmentation", "recognition", "generation", "style_encoder", "refiner", "rules"]
    format: Literal["onnx", "safetensors", "json_rules"]
    artifact_key: str = Field(pattern=r"^models/[0-9a-f]{32}$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(strict=True, gt=0, le=64 * 1024 * 1024)
    input_schema: dict
    output_schema: dict
    scripts: list[str] = Field(min_length=1)
    styles: list[Literal["print", "cursive", "mixed"]] = Field(min_length=1)
    input_classes: list[str] = Field(min_length=1)
    compatible_worker: str
    license: str = Field(min_length=1, max_length=200)
    provenance: dict = Field(min_length=1)
    status: Literal["candidate", "approved", "deprecated", "revoked"]
    resource_profile: dict


def compatible(version, constraint):
    def parse(value):
        if not re.fullmatch(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?", value):
            raise RegistryRejected("worker_version_invalid")
        return tuple(int(x) for x in (value.split(".") + ["0"])[:3])

    actual = parse(version)
    for part in constraint.split(","):
        found = re.fullmatch(r"\s*(>=|<=|==|>|<)([0-9]+\.[0-9]+(?:\.[0-9]+)?)\s*", part)
        if not found:
            raise RegistryRejected("worker_constraint_invalid")
        op, expected = found[1], parse(found[2])
        if not {
            ">=": actual >= expected,
            "<=": actual <= expected,
            "==": actual == expected,
            ">": actual > expected,
            "<": actual < expected,
        }[op]:
            return False
    return True


def constrained_rules(data):
    """Non-executable enum-to-enum mapping. No imports, expressions or file refs."""

    def unique(pairs):
        result = {}
        for k, v in pairs:
            if k in result:
                raise RegistryRejected("duplicate_json_key")
            result[k] = v
        return result

    try:
        if len(data) > 1024 * 1024:
            raise RegistryRejected("rules_budget_exceeded")
        value = json.loads(
            data,
            object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(RegistryRejected("nonfinite_rule")),
        )
        if (
            set(value) != {"schema_version", "kind", "mapping"}
            or value["schema_version"] != 1
            or value["kind"] != "enum_map"
        ):
            raise RegistryRejected("rules_schema_invalid")
        mapping = value["mapping"]
        if (
            not isinstance(mapping, dict)
            or not 1 <= len(mapping) <= 256
            or any(
                not re.fullmatch(r"[a-z0-9_]{1,64}", k)
                or not isinstance(v, str)
                or not re.fullmatch(r"[a-z0-9_]{1,64}", v)
                for k, v in mapping.items()
            )
        ):
            raise RegistryRejected("rules_schema_invalid")
        return value
    except (ValueError, TypeError, RecursionError):
        raise RegistryRejected("rules_schema_invalid") from None


@dataclass(frozen=True)
class VerifiedArtifact:
    record: RegistryRecord
    data: bytes = field(repr=False)


class ModelCache:
    def __init__(
        self, resolve: Callable, read: Callable, *, max_bytes=64 * 1024 * 1024, max_entries=4
    ):
        if not 1 <= max_entries <= 32 or not 1 <= max_bytes <= 256 * 1024 * 1024:
            raise RegistryRejected("cache_bounds_invalid")
        self.resolve, self.read = resolve, read
        self.max_bytes, self.max_entries = max_bytes, max_entries
        self._cache: OrderedDict[str, bytes] = OrderedDict()

    def evict(self, model_id=None):
        if model_id is None:
            self._cache.clear()
        else:
            self._cache.pop(model_id, None)

    def get(
        self,
        model_id,
        *,
        task,
        script,
        style,
        input_class,
        worker_version,
        input_schema,
        output_schema,
        require_resources=False,
    ):
        # Resolve live approval/revocation on EVERY use, including cache hits.
        try:
            record = RegistryRecord.model_validate(self.resolve(model_id))
        except Exception:
            self.evict(model_id)
            raise RegistryRejected("registry_record_invalid") from None
        if record.id != model_id or record.status != "approved":
            self.evict(model_id)
            raise RegistryRejected("model_not_approved")
        if (
            record.task != task
            or script not in record.scripts
            or style not in record.styles
            or input_class not in record.input_classes
        ):
            raise RegistryRejected("model_support_mismatch")
        if record.input_schema != input_schema or record.output_schema != output_schema:
            raise RegistryRejected("model_schema_mismatch")
        if not compatible(worker_version, record.compatible_worker):
            raise RegistryRejected("worker_incompatible")
        if require_resources and (
            not record.resource_profile.get("benchmark_record_hash")
            or any(
                type(record.resource_profile.get(k)) not in (int, float)
                or not math.isfinite(record.resource_profile[k])
                or record.resource_profile[k] <= 0
                for k in ("peak_mem_mb", "ms_per_unit_p95")
            )
        ):
            raise RegistryRejected("resources_unmeasured")
        if record.size_bytes > self.max_bytes:
            raise RegistryRejected("cache_budget_exceeded")
        data = self._cache.get(model_id)
        if data is None:
            try:
                data = self.read(record.artifact_key, record.size_bytes)
            except Exception:
                raise RegistryRejected("registry_artifact_unavailable") from None
        if (
            not isinstance(data, bytes)
            or len(data) != record.size_bytes
            or hashlib.sha256(data).hexdigest() != record.sha256
        ):
            self.evict(model_id)
            raise RegistryRejected("model_integrity_mismatch")
        if record.format == "json_rules":
            constrained_rules(data)
        elif record.format == "onnx":
            # Canonical ONNX protobuf starts with ir_version (field 1, varint).
            # No ONNX executor exists here. Reject mislabeled Python/ZIP blobs.
            if not data.startswith(b"\x08"):
                raise RegistryRejected("model_container_invalid")
        elif record.format == "safetensors":
            if len(data) < 10:
                raise RegistryRejected("model_container_invalid")
            header_size = int.from_bytes(data[:8], "little")
            if not 2 <= header_size <= min(1024 * 1024, len(data) - 8):
                raise RegistryRejected("model_container_invalid")
            try:
                header = json.loads(data[8 : 8 + header_size])
                if not isinstance(header, dict) or not header:
                    raise ValueError
                offset = 0
                tensors = [v for k, v in header.items() if k != "__metadata__"]
                for tensor in sorted(tensors, key=lambda t: t["data_offsets"]):
                    if set(tensor) != {"dtype", "shape", "data_offsets"} or tensor["dtype"] not in {
                        "BOOL",
                        "I8",
                        "U8",
                        "I16",
                        "U16",
                        "I32",
                        "U32",
                        "I64",
                        "U64",
                        "F16",
                        "BF16",
                        "F32",
                        "F64",
                    }:
                        raise ValueError
                    a, b = tensor["data_offsets"]
                    if (
                        type(a) is not int
                        or type(b) is not int
                        or a != offset
                        or b < a
                        or b > len(data) - header_size - 8
                    ):
                        raise ValueError
                    shape = tensor["shape"]
                    if (
                        not isinstance(shape, list)
                        or len(shape) > 16
                        or any(type(n) is not int or n < 0 or n > 10000000 for n in shape)
                    ):
                        raise ValueError
                    sizes = {
                        "BOOL": 1,
                        "I8": 1,
                        "U8": 1,
                        "I16": 2,
                        "U16": 2,
                        "I32": 4,
                        "U32": 4,
                        "I64": 8,
                        "U64": 8,
                        "F16": 2,
                        "BF16": 2,
                        "F32": 4,
                        "F64": 8,
                    }
                    if math.prod(shape) * sizes[tensor["dtype"]] != b - a:
                        raise ValueError
                    offset = b
                if not tensors or offset != len(data) - header_size - 8:
                    raise ValueError
            except (ValueError, TypeError, KeyError, RecursionError):
                raise RegistryRejected("model_container_invalid") from None
        # ONNX/safetensors are verified opaque bytes. Runtime execution is absent
        # until a separately qualified task-specific safe loader is available.
        self._cache[model_id] = data
        self._cache.move_to_end(model_id)
        while (
            len(self._cache) > self.max_entries
            or sum(map(len, self._cache.values())) > self.max_bytes
        ):
            self._cache.popitem(last=False)
        return VerifiedArtifact(record, data)
