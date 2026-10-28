"""Complete stream and path provenance controls, with no extraction/execution."""

import copy
import io
import json
import subprocess
import sys
import tarfile

import pytest

from scripts.image_sbom_inventory import FILTER, PIP_BOM, collect, sbom_components
from scripts.image_security_gate import ROOT, digest

IMAGE_ID = "sha256:" + "a" * 64


def sbom():
    return json.dumps(
        {
            "bomFormat": "CycloneDX",
            "specVersion": "1.4",
            "metadata": {"component": {"type": "library", "name": "pip", "bom-ref": "bom-ref:pip"}},
            "components": [
                {"type": "library", "name": "pip", "bom-ref": "bom-ref:pip"},
                {
                    "type": "library",
                    "name": "msgpack",
                    "version": "1.1.2",
                    "purl": "pkg:pypi/msgpack@1.1.2",
                    "bom-ref": "pkg:pypi/msgpack@1.1.2",
                },
            ],
        }
    ).encode()


def export(entries):
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for path, data in entries:
            if isinstance(path, tarfile.TarInfo):
                member = path
            else:
                member = tarfile.TarInfo(path.lstrip("/"))
                member.size = len(data)
            archive.addfile(member, io.BytesIO(data) if member.isreg() else None)
    return result.getvalue()


@pytest.fixture
def fixture():
    entries = [("/app/ordinary", b"ordinary"), (PIP_BOM, sbom())]
    runtime = {
        "copied_files": {
            path: {"kind": "file", "size": len(data), "sha256": digest(data)}
            for path, data in entries
        }
    }
    return entries, runtime


def run(entries, runtime, **limits):
    raw = export(entries)
    result = collect(io.BytesIO(raw), runtime, image_id=IMAGE_ID, runtime_sha256="b" * 64, **limits)
    return result, raw


def test_complete_export_binds_single_scanner_input_and_parsed_components(fixture):
    entries, runtime = fixture
    excluded = "/app/.venv/lib/python3.12/site-packages/example.dist-info/sboms/auditwheel.cdx.json"
    entries.append((excluded, b"not parsed by the generic SBOM analyzer"))
    result, raw = run(entries, runtime)
    assert result["complete"], result["errors"]
    assert result["tar_sha256"] == digest(raw) and result["tar_bytes"] == len(raw)
    assert result["manifest_files_verified"] == 2 and result["filter"] == FILTER
    assert result["excluded_pep770_paths"] == [excluded]
    assert result["recognized_candidates"][0]["path"] == PIP_BOM
    assert (
        result["recognized_candidates"][0]["sha256"] == runtime["copied_files"][PIP_BOM]["sha256"]
    )
    assert result["recognized_candidates"][0]["components"][-1]["purl"] == "pkg:pypi/msgpack@1.1.2"


def test_api_export_can_have_no_generic_sbom_candidate(fixture):
    entries, runtime = fixture
    entries.pop()
    runtime["copied_files"].pop(PIP_BOM)
    result, _ = run(entries, runtime)
    assert result["complete"] and result["recognized_candidates"] == []


@pytest.mark.parametrize(
    "path", ["/unexpected.cdx.json", "/app/other.spdx", "/app/other.spdx.json", "/app/other.cdx"]
)
def test_extra_scanner_input_is_not_ignored_even_if_generated_outside_manifest(fixture, path):
    entries, runtime = fixture
    entries.append((path, b"additional input"))
    result, _ = run(entries, runtime)
    assert not result["complete"] and result["errors"] == ["sbom_input_ambiguous_or_unreviewed"]


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE])
def test_nonregular_sbom_cannot_supply_provenance(fixture, kind):
    entries, runtime = fixture
    member = tarfile.TarInfo(PIP_BOM.lstrip("/"))
    member.type = kind
    member.linkname = "ordinary"
    entries[-1] = (member, b"")
    result, _ = run(entries, runtime)
    assert not result["complete"]


def test_every_manifest_file_and_alias_must_exist_with_same_kind_and_target(fixture):
    entries, runtime = fixture
    link = tarfile.TarInfo("app/alias")
    link.type = tarfile.SYMTYPE
    link.linkname = "ordinary"
    entries.append((link, b""))
    runtime["copied_files"]["/app/alias"] = {"kind": "symlink", "target": "ordinary"}
    assert run(entries, runtime)[0]["complete"]
    link.linkname = "different"
    assert run(entries, runtime)[0]["errors"] == ["export_manifest_member_mismatch"]


def test_missing_manifest_path_fails_after_complete_stream(fixture):
    entries, runtime = fixture
    entries.pop(0)
    assert run(entries, runtime)[0]["errors"] == ["export_manifest_files_missing"]


def test_changed_candidate_bytes_fail_hash_binding(fixture):
    entries, runtime = fixture
    path, data = entries[-1]
    changed = data.replace(b"msgpack", b"MSGPack")
    entries[-1] = (path, changed)
    assert run(entries, runtime)[0]["errors"] == ["sbom_candidate_hash_mismatch"]


@pytest.mark.parametrize("name", ["../escape", "/absolute", "app/../escape", "app//double"])
def test_unsafe_or_ambiguous_tar_paths_refuse(fixture, name):
    entries, runtime = fixture
    member = tarfile.TarInfo(name)
    member.size = 1
    entries.append((member, b"x"))
    assert run(entries, runtime)[0]["errors"] == ["export_path_invalid"]


def test_duplicate_tar_path_and_resource_limits_fail(fixture):
    entries, runtime = fixture
    assert run(entries + entries[:1], runtime)[0]["errors"] == ["export_duplicate_path"]
    assert run(entries, runtime, maximum_entries=1)[0]["errors"] == ["runtime_file_limit"]
    assert run(entries, runtime, maximum_bytes=100)[0]["errors"] == ["export_byte_limit"]


@pytest.mark.parametrize(
    "variant", ["no_markers", "one_marker", "bad_header", "trailing_data", "partial_record"]
)
def test_permissive_tar_eof_cases_cannot_produce_complete_evidence(fixture, variant):
    entries, runtime = fixture
    raw = export(entries)
    last = (len(raw.rstrip(b"\0")) + 511) // 512 * 512
    if variant == "no_markers":
        raw = raw[:last]
    elif variant == "one_marker":
        raw = raw[: last + 512]
    elif variant == "bad_header":
        raw = raw[:last] + b"x" * 512 + b"\0" * 1024
    elif variant == "trailing_data":
        raw += b"hidden" + b"\0" * 506
    else:
        raw += b"\0"
    result = collect(io.BytesIO(raw), runtime, image_id=IMAGE_ID, runtime_sha256="b" * 64)
    assert not result["complete"] and result["errors"]


def test_malformed_archive_and_document_fail_without_extracting(fixture):
    entries, runtime = fixture
    result = collect(io.BytesIO(b"bad" * 500), runtime, image_id=IMAGE_ID, runtime_sha256="b" * 64)
    assert not result["complete"]
    with pytest.raises(ValueError):
        sbom_components(b'{"bomFormat":"CycloneDX","bomFormat":"other"}')


def test_extended_header_memory_allocation_is_bounded_before_parsing(fixture):
    _, runtime = fixture
    member = tarfile.TarInfo("PaxHeader")
    member.type = tarfile.XHDTYPE
    member.size = 2_000_000_000
    result = collect(
        io.BytesIO(member.tobuf()), runtime, image_id=IMAGE_ID, runtime_sha256="b" * 64
    )
    assert result["errors"] == ["export_extended_header_limit"]


def test_proof_filter_mutation_cannot_change_the_reviewed_global_filter(fixture):
    entries, runtime = fixture
    original = copy.deepcopy(FILTER)
    result, _ = run(entries, runtime)
    result["filter"]["suffixes"].clear()
    assert FILTER == original


def test_stdlib_module_cli_consumes_complete_stdin_and_writes_evidence(fixture, tmp_path):
    entries, runtime = fixture
    manifest = tmp_path / "runtime.json"
    manifest.write_text(json.dumps(runtime))
    output = tmp_path / "sbom.json"
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "-m",
            "scripts.image_sbom_inventory",
            "--image-id",
            IMAGE_ID,
            "--runtime-manifest",
            str(manifest),
            "--output",
            str(output),
        ],
        input=export(entries),
        cwd=ROOT,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    proof = json.loads(output.read_text())
    assert proof["complete"] and proof["runtime_manifest_sha256"] == digest(manifest.read_bytes())
