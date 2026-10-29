"""Exact-image evidence and negative controls for narrow advisory dispositions."""

import copy
import io
import json
import subprocess
import sys
import tarfile
from datetime import UTC, datetime, timedelta

import pytest

from scripts.image_sbom_inventory import ROOT_COMPONENT, collect
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
LAYER = {"DiffID": "sha256:" + "c" * 64}
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
            "PkgID": name + "@" + version,
            "PkgIdentifier": {
                "UID": {"msgpack": "1" * 16, "setuptools": "2" * 16, "urllib3": "3" * 16}[name],
                "PURL": "pkg:pypi/" + name + "@" + version,
                "BOMRef": "pkg:pypi/" + name + "@" + version,
            },
            "Layer": dict(LAYER),
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
        files[path] = {"kind": "file", "sha256": digest(data), "mode": "0o644", "size": len(data)}

    packages = dict(pair for names, _ in OS_RULES.values() for pair in names.items())
    status = "\n\n".join(
        f"Package: {name}\nStatus: install ok installed\nArchitecture: amd64\nVersion: {version}"
        for name, version in packages.items()
    )
    put("/var/lib/dpkg/status", status.encode())
    for path in VALIDATOR_FILES:
        put("/app/" + path, (ROOT / path).read_bytes())
    put(VENDOR + "vendor.txt", b"msgpack==1.1.2\nsetuptools==70.3.0\nurllib3==2.7.0\n")
    components = [dict(ROOT_COMPONENT)] + [
        {
            "type": "library",
            "name": name,
            "version": version,
            "purl": "pkg:pypi/" + name + "@" + version,
            "bom-ref": "pkg:pypi/" + name + "@" + version,
        }
        for name, version in sorted({(name, version) for name, version, _ in PYTHON_RULES.values()})
    ]
    put(
        VENDOR + "bom.cdx.json",
        encode(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.4",
                "metadata": {"component": dict(ROOT_COMPONENT)},
                "components": components,
            }
        ),
    )
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
        "Trivy": {"Version": "0.70.0"},
        "ArtifactType": "container_image",
        "CreatedAt": NOW.isoformat(),
        "Metadata": {
            "ImageID": IMAGE_ID,
            "OS": {"Family": "debian", "Name": "13.7"},
            "ImageConfig": {"config": {}},
            "DiffIDs": [LAYER["DiffID"]],
        },
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
                "Packages": [
                    {
                        "ID": name + "@" + version,
                        "Name": name,
                        "Version": version,
                        "Identifier": {
                            "UID": {
                                "msgpack": "1" * 16,
                                "setuptools": "2" * 16,
                                "urllib3": "3" * 16,
                            }[name],
                            "PURL": "pkg:pypi/" + name + "@" + version,
                            "BOMRef": "pkg:pypi/" + name + "@" + version,
                        },
                        "Layer": dict(LAYER),
                        "AnalyzedBy": "sbom",
                    }
                    for name, version in sorted(
                        {(name, version) for name, version, _ in PYTHON_RULES.values()}
                    )
                ],
            },
        ],
    }
    exported = io.BytesIO()
    with tarfile.open(fileobj=exported, mode="w") as archive:
        for path in files:
            archive.add(image_root / path.lstrip("/"), arcname=path.lstrip("/"))
    sbom_inventory = collect(
        io.BytesIO(exported.getvalue()),
        runtime,
        image_id=IMAGE_ID,
        runtime_sha256=digest(encode(runtime)),
    )
    assert sbom_inventory["complete"], sbom_inventory["errors"]
    return {
        "scan": scan,
        "inventory": inv,
        "runtime": runtime,
        "smoke": smoke,
        "sbom_inventory": sbom_inventory,
    }


def run(evidence, *, bind_manifest=True):
    if bind_manifest:
        evidence["smoke"]["runtime_manifest_sha256"] = digest(encode(evidence["runtime"]))
        if isinstance(evidence["sbom_inventory"], dict):
            evidence["sbom_inventory"]["runtime_manifest_sha256"] = digest(
                encode(evidence["runtime"])
            )
    return evaluate(
        **evidence,
        image_id=IMAGE_ID,
        scan_sha256=digest(encode(evidence["scan"])),
        runtime_sha256=digest(encode(evidence["runtime"])),
        sbom_sha256=digest(encode(evidence["sbom_inventory"])),
        now=NOW,
    )


def test_exact_reviewed_findings_preserve_raw_records_and_report_distinct_reasons(evidence):
    before = copy.deepcopy(evidence)
    result = run(evidence)
    assert result["status"] == "passed" and result["effective_blocking_findings"] == 0
    assert result["raw_findings"] == 49 and result["reviewed_dispositions"] == 49
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
    assert result["status"] == "blocked" and result["effective_blocking_findings"] == 5
    assert all(
        row["disposition"]["reason"] == "sbom_finding_uid_invalid"
        for row in result["findings"][-5:]
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
    assert result["errors"] == ["sbom_manifest_coverage_incomplete"]


def test_inventory_incompleteness_and_inconsistent_presence_cannot_clear_findings(evidence):
    evidence["inventory"]["components"]["infocmp"]["files"][0]["present"] = True
    assert run(evidence)["errors"] == ["inventory_presence_conflict"]


@pytest.mark.parametrize(
    "path",
    [
        "app/../other",
        "/other/vendor.txt",
        "/app/.venv/lib/python3.12/site-packages/urllib3-2.7.0.dist-info/METADATA",
    ],
)
def test_python_findings_require_exact_vendor_origin(evidence, path):
    evidence["scan"]["Results"][1]["Vulnerabilities"] = python_findings(path)
    result = run(evidence)
    assert result["status"] == "blocked" and result["effective_blocking_findings"] == 5


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
    assert result["errors"] == ["sbom_manifest_coverage_incomplete"]


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
    for finding in evidence["scan"]["Results"][1]["Vulnerabilities"]:
        finding.pop("PkgIdentifier")
    evidence["smoke"]["runtime_manifest_sha256"] = digest(encode(evidence["runtime"]))
    arguments = []
    for key, option in (
        ("scan", "scan"),
        ("inventory", "inventory"),
        ("runtime", "runtime-manifest"),
        ("smoke", "smoke"),
        ("sbom_inventory", "sbom-inventory"),
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
    assert report["effective_blocking_findings"] == 5 and IMAGE.fullmatch(report["image_config_id"])


def test_cli_cannot_overwrite_the_raw_report_with_an_effective_result(tmp_path):
    raw = tmp_path / "raw.json"
    raw.write_text('{"retained":"original evidence"}')
    command = [sys.executable, "-S", "-m", "scripts.image_security_gate", "--image-id", IMAGE_ID]
    for option in ("scan", "inventory", "runtime-manifest", "smoke", "sbom-inventory", "output"):
        command.extend(["--" + option, str(raw)])
    result = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=10)
    assert result.returncode == 2
    assert raw.read_text() == '{"retained":"original evidence"}'


@pytest.mark.parametrize("path", [None, ""])
def test_missing_path_requires_exclusive_input_and_full_scanner_identity_join(evidence, path):
    findings = evidence["scan"]["Results"][1]["Vulnerabilities"]
    for finding in findings:
        if path is None:
            finding.pop("PkgPath")
        else:
            finding["PkgPath"] = path
    before = copy.deepcopy(findings)
    report = run(evidence)
    assert report["status"] == "passed"
    for row in report["findings"][-5:]:
        origin = row["disposition"]["derived_origin"]
        assert origin["method"] == "unique_pinned_scanner_input"
        assert origin["path"] == VENDOR + "bom.cdx.json"
    assert findings == before


@pytest.mark.parametrize(
    "change",
    [
        "duplicate",
        "analyzer",
        "combined_analyzer",
        "id",
        "version",
        "purl",
        "layer",
        "unbound_layer",
        "path",
        "missing_packages",
    ],
)
def test_exclusive_input_never_overrides_ambiguous_or_different_package_attribution(
    evidence, change
):
    result = evidence["scan"]["Results"][1]
    for finding in result["Vulnerabilities"]:
        finding.pop("PkgPath")
    package = result["Packages"][0]
    if change == "duplicate":
        result["Packages"].append(copy.deepcopy(package))
    elif change == "analyzer":
        package["AnalyzedBy"] = "python-pkg"
    elif change == "combined_analyzer":
        package["AnalyzedBy"] = ["sbom", "python-pkg"]
    elif change == "id":
        package["ID"] = "different"
    elif change == "version":
        package["Version"] = "other"
    elif change == "purl":
        package["Identifier"]["PURL"] = "pkg:pypi/msgpack@other"
    elif change == "layer":
        package["Layer"] = {"DiffID": "sha256:" + "f" * 64}
    elif change == "unbound_layer":
        evidence["scan"]["Metadata"]["DiffIDs"] = []
    elif change == "path":
        package["FilePath"] = "/other.cdx.json"
    else:
        result.pop("Packages")
    assert run(evidence)["status"] == "blocked"


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "incomplete",
        "filter",
        "scanner",
        "hash",
        "document",
        "components",
        "duplicate_component",
        "image",
        "volume",
        "extra_candidate",
        "root",
    ],
)
def test_invalid_export_or_scanner_provenance_blocks_even_explicit_package_paths(evidence, change):
    proof = evidence["sbom_inventory"]
    candidate = proof["recognized_candidates"][0]
    if change == "missing":
        evidence["sbom_inventory"] = None
    elif change == "incomplete":
        proof["complete"] = False
    elif change == "filter":
        proof["filter"]["suffixes"].append(".json")
    elif change == "scanner":
        evidence["scan"]["Trivy"]["Version"] = "0.71.0"
    elif change == "hash":
        candidate["sha256"] = "0" * 64
    elif change == "document":
        candidate["document"] += " "
    elif change == "components":
        candidate["components"][-1]["version"] = "other"
    elif change == "duplicate_component":
        candidate["components"].append(copy.deepcopy(candidate["components"][-1]))
    elif change == "image":
        proof["image_config_id"] = "sha256:" + "f" * 64
    elif change == "volume":
        evidence["scan"]["Metadata"]["ImageConfig"]["config"]["Volumes"] = {"/hidden": {}}
    elif change == "extra_candidate":
        proof["recognized_candidates"].append(copy.deepcopy(candidate))
    else:
        candidate["root_component"]["name"] = "other"
    assert run(evidence)["status"] == "blocked"


@pytest.mark.parametrize("path", [0, False, [], {}])
def test_falsey_nonstring_finding_path_never_selects_derived_origin(evidence, path):
    for finding in evidence["scan"]["Results"][1]["Vulnerabilities"]:
        finding["PkgPath"] = path
    report = run(evidence)
    assert report["status"] == "blocked" and report["effective_blocking_findings"] == 5


@pytest.mark.parametrize("key", ["Secrets", "Misconfigurations", "Licenses"])
@pytest.mark.parametrize("value", [[{"secret": "DO_NOT_ECHO_THIS_VALUE"}], {}, "", False])
def test_non_vulnerability_findings_or_malformed_fields_block_without_echoing_data(
    evidence, key, value
):
    evidence["scan"]["Results"][0][key] = value
    report = run(evidence)
    assert report["status"] == "blocked" and report["errors"]
    assert "DO_NOT_ECHO_THIS_VALUE" not in json.dumps(report)
