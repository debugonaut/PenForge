"""Exact-image evidence and negative controls for narrow advisory dispositions."""

import copy
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest

from scripts.image_security_gate import (
    IMAGE,
    OS_RULES,
    PROBES,
    PYTHON_RULES,
    ROOT,
    VALIDATOR_FILES,
    VENDOR,
    EvidenceError,
    digest,
    evaluate,
    load_json,
)
from scripts.image_security_inventory import inventory

IMAGE_ID = "sha256:" + "a" * 64
NOW = datetime(2026, 10, 28, 12, tzinfo=UTC)


def encode(value):
    return (json.dumps(value, sort_keys=True) + "\n").encode()


def os_findings():
    return [
        {
            "VulnerabilityID": identifier,
            "PkgName": package,
            "InstalledVersion": version,
            "Severity": "HIGH",
            "Title": "retained raw scanner title",
        }
        for identifier, (packages, _) in OS_RULES.items()
        for package, version in packages.items()
    ]


def python_findings(path=VENDOR + "bom.cdx.json"):
    return [
        {
            "VulnerabilityID": identifier,
            "PkgName": name,
            "InstalledVersion": version,
            "Severity": "HIGH",
            "PkgPath": path,
        }
        for identifier, (name, version, _) in PYTHON_RULES.items()
    ]


@pytest.fixture
def evidence(tmp_path):
    image_root = tmp_path / "image"
    files = {}

    def put(path, data):
        target = image_root / path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        files[path] = {"kind": "file", "sha256": digest(data), "mode": "0o644"}

    packages = dict(pair for names, _ in OS_RULES.values() for pair in names.items())
    status = "\n\n".join(
        f"Package: {name}\nStatus: install ok installed\nArchitecture: amd64\nVersion: {version}"
        for name, version in packages.items()
    )
    put("/var/lib/dpkg/status", status.encode())
    for path in VALIDATOR_FILES:
        put("/app/" + path, (ROOT / path).read_bytes())
    put(VENDOR + "vendor.txt", b"msgpack==1.1.2\nsetuptools==70.3.0\nurllib3==2.7.0\n")
    put(VENDOR + "bom.cdx.json", b'{"synthetic_scanner_origin":true}')
    put(
        "/app/.venv/lib/python3.12/site-packages/pip-26.2.2.dist-info/METADATA",
        b"Name: pip\nVersion: 26.2.2\n",
    )
    runtime = {
        "schema_version": 1,
        "role": "worker",
        "status": "assembled_awaiting_linux_smoke_and_scan",
        "security_disposition": "none",
        "copied_files": files,
    }
    inv = inventory(image_root)
    assert inv["complete"], inv["errors"]
    inv["image_config_id"] = IMAGE_ID
    smoke = {
        "schema_version": 1,
        "role": "worker",
        "image_config_id": IMAGE_ID,
        "status": "passed",
        "application_imports": True,
        "fonts": json.loads(
            (ROOT / "packages/contracts/fixtures/font-runtime-hashes.v1.json").read_text()
        )["fonts"],
        "runtime_manifest_sha256": digest(encode(runtime)),
        "validator_installer_boundary": {
            "status": "passed",
            "probes": list(PROBES),
            "child_sha256": files["/app/" + VALIDATOR_FILES[1]]["sha256"],
        },
    }
    scan = {
        "SchemaVersion": 2,
        "ArtifactType": "container_image",
        "CreatedAt": NOW.isoformat(),
        "Metadata": {"ImageID": IMAGE_ID, "OS": {"Family": "debian", "Name": "13.7"}},
        "Results": [
            {
                "Target": "synthetic-image (debian 13.7)",
                "Class": "os-pkgs",
                "Type": "debian",
                "Vulnerabilities": os_findings(),
            },
            {
                "Target": "Python",
                "Class": "lang-pkgs",
                "Type": "python-pkg",
                "Vulnerabilities": python_findings(),
            },
        ],
    }
    return {"scan": scan, "inventory": inv, "runtime": runtime, "smoke": smoke}


def run(evidence, *, bind_manifest=True):
    if bind_manifest:
        evidence["smoke"]["runtime_manifest_sha256"] = digest(encode(evidence["runtime"]))
    return evaluate(
        **evidence,
        image_id=IMAGE_ID,
        scan_sha256=digest(encode(evidence["scan"])),
        runtime_sha256=digest(encode(evidence["runtime"])),
        now=NOW,
    )


def test_exact_reviewed_findings_preserve_raw_records_and_report_distinct_reasons(evidence):
    before = copy.deepcopy(evidence)
    result = run(evidence)
    assert result["status"] == "passed" and result["effective_blocking_findings"] == 0
    assert result["raw_findings"] == 48 and result["reviewed_dispositions"] == 48
    assert result["raw_scan_sha256"] == digest(encode(evidence["scan"]))
    assert result["findings"][0]["finding"] == before["scan"]["Results"][0]["Vulnerabilities"][0]
    assert (
        result["findings"][-1]["disposition"]["justification"]
        == "vulnerable_code_not_in_execute_path"
    )
    assert evidence == before


def test_aggregate_python_findings_without_origin_remain_blocked_even_with_matching_bom_ref(
    evidence,
):
    for finding in evidence["scan"]["Results"][1]["Vulnerabilities"]:
        finding.pop("PkgPath")
        finding["PkgIdentifier"] = {
            "BOMRef": f"pkg:pypi/{finding['PkgName']}@{finding['InstalledVersion']}"
        }
    result = run(evidence)
    assert result["status"] == "blocked" and result["effective_blocking_findings"] == 4
    assert all(
        row["disposition"]["reason"] == "finding_path_missing" for row in result["findings"][-4:]
    )


@pytest.mark.parametrize("field", ["Metadata", "inventory", "smoke"])
def test_mismatched_image_identity_blocks_all_dispositions(evidence, field):
    if field == "Metadata":
        evidence["scan"][field]["ImageID"] = "sha256:" + "b" * 64
    else:
        evidence[field]["image_config_id"] = "sha256:" + "b" * 64
    result = run(evidence)
    assert result["status"] == "blocked" and result["errors"]
    assert all("disposition" not in row for row in result["findings"])


@pytest.mark.parametrize("hours", [25, -1])
def test_stale_and_future_scans_fail(evidence, hours):
    evidence["scan"]["CreatedAt"] = (NOW - timedelta(hours=hours)).isoformat()
    assert run(evidence)["errors"] == ["scan_stale_or_future"]


def test_manifest_substitution_is_detected(evidence):
    evidence["runtime"]["copied_files"]["/app/extra"] = {
        "kind": "file",
        "mode": "0o644",
        "sha256": "f" * 64,
    }
    assert run(evidence, bind_manifest=False)["errors"] == ["runtime_manifest_smoke_mismatch"]


@pytest.mark.parametrize("path", VALIDATOR_FILES)
def test_changed_or_substituted_installed_validator_source_blocks(evidence, path):
    evidence["runtime"]["copied_files"]["/app/" + path]["sha256"] = "0" * 64
    assert run(evidence)["errors"][0].startswith("reviewed_validator_hash_mismatch:")


@pytest.mark.parametrize("change", ["missing_probe", "failed_probe", "child_hash", "font_hash"])
def test_worker_smoke_cannot_omit_boundary_or_font_checks(evidence, change):
    boundary = evidence["smoke"]["validator_installer_boundary"]
    if change == "missing_probe":
        boundary["probes"].pop()
    elif change == "failed_probe":
        boundary["status"] = "failed"
    elif change == "child_hash":
        boundary["child_sha256"] = "0" * 64
    else:
        evidence["smoke"]["fonts"]["ttf"] = "0" * 64
    assert run(evidence)["status"] == "blocked"


@pytest.mark.parametrize("field", ["CVE", "package", "version", "type", "path", "critical"])
def test_unknown_os_finding_tuple_is_not_covered_by_package_family(evidence, field):
    result = evidence["scan"]["Results"][0]
    finding = result["Vulnerabilities"][0]
    if field == "CVE":
        finding["VulnerabilityID"] = "CVE-2099-1234"
    elif field == "package":
        finding["PkgName"] = "unreviewed-util-linux-package"
    elif field == "version":
        finding["InstalledVersion"] = "2.41.5-unreviewed"
    elif field == "type":
        result["Type"] = "alpine"
    elif field == "path":
        finding["PkgPath"] = "unreviewed/status"
    else:
        finding["Severity"] = "CRITICAL"
    assert run(evidence)["status"] == "blocked"


def test_dpkg_version_and_bytes_must_match_inventory_and_runtime(evidence):
    evidence["inventory"]["dpkg"]["sources"][0]["sha256"] = "0" * 64
    assert run(evidence)["errors"] == ["dpkg_runtime_hash_mismatch"]


def test_inventory_cannot_claim_absence_while_manifest_contains_the_component(evidence):
    evidence["runtime"]["copied_files"]["/unusual/location/infocmp"] = {
        "kind": "file",
        "mode": "0o755",
        "sha256": "f" * 64,
    }
    result = run(evidence)
    assert result["status"] == "blocked"
    assert any(
        row.get("disposition", {}).get("reason") == "manifest_component_present:infocmp"
        for row in result["findings"]
    )


def test_inventory_incompleteness_and_inconsistent_presence_cannot_clear_findings(evidence):
    evidence["inventory"]["components"]["infocmp"]["files"][0]["present"] = True
    assert run(evidence)["errors"] == ["inventory_presence_conflict"]


@pytest.mark.parametrize(
    "path",
    [
        "",
        "app/../other",
        "/other/vendor.txt",
        "/app/.venv/lib/python3.12/site-packages/urllib3-2.7.0.dist-info/METADATA",
    ],
)
def test_python_findings_require_exact_vendor_origin(evidence, path):
    evidence["scan"]["Results"][1]["Vulnerabilities"] = python_findings(path)
    result = run(evidence)
    assert result["status"] == "blocked" and result["effective_blocking_findings"] == 4


def test_vendor_version_and_manifest_hash_must_match(evidence):
    evidence["inventory"]["pip_vendor_manifests"][0]["versions"]["urllib3"] = "2.8.0"
    result = run(evidence)
    assert result["status"] == "blocked" and result["effective_blocking_findings"] == 2


def test_sourceless_package_index_bytecode_prevents_absence_disposition(evidence):
    evidence["runtime"]["copied_files"][
        VENDOR + "setuptools/__pycache__/package_index.cpython-312.pyc"
    ] = {"kind": "file", "mode": "0o644", "sha256": "f" * 64}
    result = run(evidence)
    assert result["status"] == "blocked"
    assert result["findings"][-3]["disposition"]["reason"].startswith("manifest_component_present:")


def test_api_does_not_get_worker_execution_path_disposition(evidence):
    evidence["runtime"]["role"] = evidence["smoke"]["role"] = "api"
    evidence["smoke"].update(fonts={}, validator_installer_boundary=None)
    result = run(evidence)
    assert result["status"] == "blocked" and result["effective_blocking_findings"] == 2


def test_clean_api_scan_still_requires_evidence_but_has_no_dispositions(evidence):
    evidence["runtime"]["role"] = evidence["smoke"]["role"] = "api"
    evidence["smoke"].update(fonts={}, validator_installer_boundary=None)
    for result in evidence["scan"]["Results"]:
        result["Vulnerabilities"] = []
    assert run(evidence)["status"] == "passed"


@pytest.mark.parametrize("value", [{}, "", 0, False])
def test_falsey_malformed_findings_cannot_become_a_clean_scan(evidence, value):
    evidence["scan"]["Results"][0]["Vulnerabilities"] = value
    assert run(evidence)["errors"] == ["scan_findings_invalid"]


def test_json_input_rejects_duplicate_keys_and_byte_overflow(tmp_path):
    path = tmp_path / "input.json"
    path.write_text('{"complete":true,"complete":false}')
    with pytest.raises(EvidenceError, match="duplicate_json_key"):
        load_json(path, 1000)
    with pytest.raises(EvidenceError, match="byte_limit"):
        load_json(path, 4)


def test_stdlib_only_module_cli_preserves_raw_hash_and_blocks_ambiguous_origin(evidence, tmp_path):
    evidence["scan"]["CreatedAt"] = datetime.now(UTC).isoformat()
    evidence["scan"]["Results"][1]["Vulnerabilities"] = python_findings("")
    evidence["smoke"]["runtime_manifest_sha256"] = digest(encode(evidence["runtime"]))
    arguments = []
    for key, option in (
        ("scan", "scan"),
        ("inventory", "inventory"),
        ("runtime", "runtime-manifest"),
        ("smoke", "smoke"),
    ):
        path = tmp_path / (key + ".json")
        path.write_bytes(encode(evidence[key]))
        arguments.extend(["--" + option, str(path)])
    output = tmp_path / "effective.json"
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "-m",
            "scripts.image_security_gate",
            *arguments,
            "--image-id",
            IMAGE_ID,
            "--output",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 1, result.stderr
    report = json.loads(output.read_text())
    assert report["raw_scan_sha256"] == digest((tmp_path / "scan.json").read_bytes())
    assert report["effective_blocking_findings"] == 4 and IMAGE.fullmatch(report["image_config_id"])


def test_cli_cannot_overwrite_the_raw_report_with_an_effective_result(tmp_path):
    raw = tmp_path / "raw.json"
    raw.write_text('{"retained":"original evidence"}')
    command = [sys.executable, "-S", "-m", "scripts.image_security_gate", "--image-id", IMAGE_ID]
    for option in ("scan", "inventory", "runtime-manifest", "smoke", "output"):
        command.extend(["--" + option, str(raw)])
    result = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=10)
    assert result.returncode == 2
    assert raw.read_text() == '{"retained":"original evidence"}'
