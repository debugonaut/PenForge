"""Inventory scanner-visible SBOM inputs from a complete Docker export stream.

No extraction, native execution or network access occurs. The trusted CI caller
verifies the nonstarted container's image ID and absence of mounts before export.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path, PurePosixPath

from scripts.image_security_gate import (
    IMAGE,
    SHA,
    EvidenceError,
    digest,
    load_json,
    mapping,
    require,
    rows,
)

FILTER: dict = {
    "trivy_version": "0.70.0",
    "suffixes": [".spdx", ".spdx.json", ".cdx", ".cdx.json"],
    "excluded_substring": ".dist-info/sboms/",
    "source": "https://raw.githubusercontent.com/aquasecurity/trivy/v0.70.0/pkg/fanal/analyzer/sbom/sbom.go",
    "source_sha256": "4347d65abde803932f7cf9525227b3ea82a04ccbc5c28c8388f6fc1b51d1882e",
}
PIP_BOM = "/app/.venv/lib/python3.12/site-packages/pip/_vendor/bom.cdx.json"
ROOT_COMPONENT = {"type": "library", "name": "pip", "bom-ref": "bom-ref:pip"}
MAX_BYTES = 2_000_000_000
MAX_ENTRIES = 100000
MAX_SBOM = 1024 * 1024


class BoundedTarInfo(tarfile.TarInfo):
    def _proc_member(self, archive):
        # tarfile reads extended metadata bodies into memory before yielding a
        # member. Bound those headers before that allocation, not only data files.
        require(self.size >= 0, "export_member_invalid")
        if self.type in {
            tarfile.XHDTYPE,
            tarfile.XGLTYPE,
            tarfile.GNUTYPE_LONGNAME,
            tarfile.GNUTYPE_LONGLINK,
            b"X",
        }:
            require(self.size <= 65536, "export_extended_header_limit")
        require(self.type != tarfile.GNUTYPE_SPARSE, "export_sparse_member_unreviewed")
        return super()._proc_member(archive)  # type: ignore[misc]


class ExportReader(io.RawIOBase):
    """Bound total tar bytes and retain only a small window for EOF validation."""

    def __init__(self, source, maximum):
        self.source, self.maximum = source, maximum
        self.count = 0
        self.tail = b""
        self.sha256 = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        require(0 < size <= 1024 * 1024, "export_read_size_invalid")
        data = self.source.read(min(size, self.maximum - self.count + 1))
        self.count += len(data)
        require(self.count <= self.maximum, "export_byte_limit")
        self.sha256.update(data)
        self.tail = (self.tail + data)[-65536:]
        return data

    def recent(self, offset, size):
        start = offset - (self.count - len(self.tail))
        require(0 <= start and start + size <= len(self.tail), "export_end_unverifiable")
        return self.tail[start : start + size]


def archive_path(name: str) -> str:
    require(isinstance(name, str) and len(name) <= 4096, "export_path_invalid")
    while name.startswith("./"):
        name = name[2:]
    name = name.removesuffix("/")
    if name in {"", "."}:
        return "/"
    require(
        not name.startswith("/")
        and str(PurePosixPath(name)) == name
        and ".." not in PurePosixPath(name).parts
        and "\\" not in name
        and not any(ord(char) < 32 for char in name),
        "export_path_invalid",
    )
    return "/" + name


def sbom_components(data: bytes) -> list[dict]:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "sbom_duplicate_key")
            result[key] = value
        return result

    document = mapping(json.loads(data, object_pairs_hook=unique), "sbom_document_invalid")
    require(document.get("bomFormat") == "CycloneDX", "sbom_format_unreviewed")
    require(document.get("specVersion") == "1.4", "sbom_version_unreviewed")
    metadata = mapping(document.get("metadata"), "sbom_metadata_missing")
    require(metadata.get("component") == ROOT_COMPONENT, "sbom_root_component_unreviewed")
    components = []
    for row in rows(document.get("components"), "sbom_components_invalid", 1024):
        mapping(row, "sbom_component_invalid")
        require(row.get("type") == "library", "sbom_component_type_unreviewed")
        value = {key: row.get(key) for key in ("name", "version", "purl", "bom-ref")}
        root_component = value == {
            "name": "pip",
            "version": None,
            "purl": None,
            "bom-ref": "bom-ref:pip",
        }
        require(
            root_component
            or all(isinstance(text, str) and 0 < len(text) <= 1000 for text in value.values()),
            "sbom_component_identity_invalid",
        )
        require(not row.get("components"), "sbom_nested_components_unreviewed")
        components.append(value)
    identities = {(row["name"], row["version"], row["purl"], row["bom-ref"]) for row in components}
    require(len(identities) == len(components), "sbom_component_duplicate")
    return components


def collect(
    source,
    runtime: dict,
    *,
    image_id: str,
    runtime_sha256: str,
    maximum_bytes=MAX_BYTES,
    maximum_entries=MAX_ENTRIES,
) -> dict:
    result: dict = {
        "schema_version": 1,
        "complete": False,
        "image_config_id": image_id,
        "runtime_manifest_sha256": runtime_sha256,
        "filter": copy.deepcopy(FILTER),
        "recognized_candidates": [],
        "excluded_pep770_paths": [],
        "errors": [],
    }
    try:
        require(IMAGE.fullmatch(image_id) is not None, "invalid_image_id")
        require(SHA.fullmatch(runtime_sha256) is not None, "runtime_hash_invalid")
        copied = mapping(runtime.get("copied_files"), "runtime_files_missing")
        require(0 < len(copied) <= maximum_entries, "runtime_file_limit")
        stream = ExportReader(source, maximum_bytes)
        seen: set[str] = set()
        verified: set[str] = set()
        candidates: list[dict] = []
        excluded = []
        with tarfile.open(
            fileobj=stream, mode="r|", bufsize=10240, tarinfo=BoundedTarInfo
        ) as archive:
            assert archive.fileobj is not None
            for member in archive:
                path = archive_path(member.name)
                require(path not in seen, "export_duplicate_path")
                seen.add(path)
                require(len(seen) <= maximum_entries, "export_entry_limit")
                require(member.size >= 0 and not member.issparse(), "export_member_invalid")
                if path in copied:
                    record = mapping(copied[path], "runtime_record_invalid")
                    require(
                        (
                            record.get("kind") == "file"
                            and member.isreg()
                            and member.size == record.get("size")
                        )
                        or (
                            record.get("kind") == "symlink"
                            and member.issym()
                            and member.linkname == record.get("target")
                        ),
                        "export_manifest_member_mismatch",
                    )
                    verified.add(path)
                if not path.endswith(tuple(FILTER["suffixes"])):
                    continue
                if FILTER["excluded_substring"] in path:
                    excluded.append(path)
                    continue
                require(member.isreg(), "sbom_candidate_not_regular")
                require(path == PIP_BOM and not candidates, "sbom_input_ambiguous_or_unreviewed")
                require(
                    path in copied and member.size <= MAX_SBOM, "sbom_candidate_unbound_or_large"
                )
                reader = archive.extractfile(member)
                require(reader is not None, "sbom_candidate_unreadable")
                assert reader is not None
                data = reader.read(MAX_SBOM + 1)
                require(len(data) == member.size, "sbom_candidate_truncated")
                require(digest(data) == copied[path].get("sha256"), "sbom_candidate_hash_mismatch")
                candidates.append(
                    {
                        "path": path,
                        "size": member.size,
                        "sha256": digest(data),
                        "components": sbom_components(data),
                        "root_component": dict(ROOT_COMPONENT),
                        "document": data.decode("utf-8"),
                    }
                )
            # tarfile accepts missing EOF markers and can stop on a bad trailing
            # header. Require the actual zero header, second marker and zero-only
            # remaining record padding, consuming the entire source stream.
            require(archive.fileobj.tell() == archive.offset + 512, "export_end_missing")
            require(stream.recent(archive.offset, 512) == b"\0" * 512, "export_end_invalid")
            require(archive.fileobj.read(512) == b"\0" * 512, "export_second_end_marker_missing")
            while data := archive.fileobj.read(65536):
                require(not any(data), "export_trailing_data")
        require(stream.count % 512 == 0, "export_record_truncated")
        require(verified == set(copied), "export_manifest_files_missing")
        result.update(
            complete=True,
            recognized_candidates=candidates,
            excluded_pep770_paths=sorted(excluded),
            entries=len(seen),
            manifest_files_verified=len(verified),
            tar_bytes=stream.count,
            tar_sha256=stream.sha256.hexdigest(),
        )
    except (
        EvidenceError,
        OSError,
        ValueError,
        TypeError,
        AttributeError,
        tarfile.TarError,
    ) as error:
        result["errors"] = [
            str(error) if isinstance(error, EvidenceError) else "export_malformed_or_unreadable"
        ]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--runtime-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve() == args.runtime_manifest.resolve():
        parser.error("output must be separate from runtime manifest")
    try:
        runtime, runtime_sha = load_json(args.runtime_manifest, 20 * 1024 * 1024)
        result = collect(
            sys.stdin.buffer, runtime, image_id=args.image_id, runtime_sha256=runtime_sha
        )
    except (OSError, ValueError, RecursionError):
        result = {"schema_version": 1, "complete": False, "errors": ["runtime_manifest_invalid"]}
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"complete": result["complete"], "errors": result["errors"]}))
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
