"""Review exact known image findings against complete, image-bound evidence.

Raw Trivy results remain unchanged. Unknown findings and ambiguous provenance block.
Run as ``python -m scripts.image_security_gate`` from the repository root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath

from scripts.image_security_inventory import SCOPES, minimal_runtime_components

ROOT = Path(__file__).resolve().parents[1]
SHA = re.compile(r"[0-9a-f]{64}")
IMAGE = re.compile(r"sha256:[0-9a-f]{64}")
VENDOR = "/app/.venv/lib/python3.12/site-packages/pip/_vendor/"
PROBES = ["pip", "pip._vendor.urllib3", "pip_api", "pip_api._call"]
VALIDATOR_FILES = (
    "packages/font-engine/penforge_font_engine/validation.py",
    "packages/font-engine/penforge_font_engine/_fontbakery_child.py",
    "packages/font-engine/penforge_font_engine/fontbakery_profile.py",
)
UTIL_PACKAGES = {
    "bsdutils": "1:2.41.5-0+deb13u1",
    "libblkid1": "2.41.5-0+deb13u1",
    "liblastlog2-2": "2.41.5-0+deb13u1",
    "libmount1": "2.41.5-0+deb13u1",
    "libsmartcols1": "2.41.5-0+deb13u1",
    "libuuid1": "2.41.5-0+deb13u1",
    "login": "1:4.16.0-2+really2.41.5-0+deb13u1",
    "mount": "2.41.5-0+deb13u1",
    "util-linux": "2.41.5-0+deb13u1",
}
# Exact reviewed Debian binary/source versions, not package-wide exclusions.
OS_RULES = {
    "CVE-2026-76642": (UTIL_PACKAGES, ("mount", "libmount")),
    "CVE-2026-78408": (UTIL_PACKAGES, ("nsenter",)),
    "CVE-2026-78409": (UTIL_PACKAGES, ("mount", "libmount")),
    "CVE-2026-78410": (UTIL_PACKAGES, ("mount", "libmount")),
    "CVE-2026-54369": ({"libacl1": "2.3.2-2+b1"}, ("libacl",)),
    "CVE-2025-69720": (
        dict.fromkeys(
            ("libncursesw6", "libtinfo6", "ncurses-base", "ncurses-bin"), "6.5+20250216-2"
        ),
        ("infocmp",),
    ),
    "CVE-2026-16742": (
        dict.fromkeys(("libsystemd0", "libudev1"), "257.13-1~deb13u1"),
        ("systemd_homed",),
    ),
    "CVE-2026-9538": ({"perl-base": "5.40.1-6+deb13u1"}, ("perl_archive_tar",)),
}
PYTHON_RULES = {
    "CVE-2026-114613": ("msgpack", "1.1.2", ("pip_vendor_msgpack_c_extension",)),
    "GHSA-6v7p-g79w-8964": ("msgpack", "1.1.2", ("pip_vendor_msgpack_c_extension",)),
    "CVE-2025-47273": (
        "setuptools",
        "70.3.0",
        ("pip_vendor_setuptools_package_index", "standalone_setuptools_package_index"),
    ),
    "CVE-2026-97687": ("urllib3", "2.7.0", ()),
    "CVE-2026-97689": ("urllib3", "2.7.0", ()),
}
SOURCES = {
    **{name: "https://security-tracker.debian.org/tracker/" + name for name in OS_RULES},
    "GHSA-6v7p-g79w-8964": "https://github.com/msgpack/msgpack-python/security/advisories/GHSA-6v7p-g79w-8964",
    "CVE-2026-114613": "https://github.com/msgpack/msgpack-python/security/advisories/GHSA-j586-36cw-2gc2",
    "CVE-2025-47273": "https://github.com/pypa/setuptools/security/advisories/GHSA-5rjg-fvgr-3xxf",
    "CVE-2026-97687": "https://github.com/urllib3/urllib3/security/advisories/GHSA-8988-9cw3-xx77",
    "CVE-2026-97689": "https://github.com/urllib3/urllib3/security/advisories/GHSA-vxq7-64xx-v4gw",
}


class EvidenceError(ValueError):
    pass


def require(value: bool, code: str) -> None:
    if not value:
        raise EvidenceError(code)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def mapping(value, code: str) -> dict:
    require(isinstance(value, dict), code)
    return value


def rows(value, code: str, maximum: int = 10000) -> list:
    require(isinstance(value, list) and len(value) <= maximum, code)
    return value


def logical_path(value) -> str:
    require(isinstance(value, str) and bool(value), "finding_path_missing")
    path = "/" + value.lstrip("/")
    require(
        str(PurePosixPath(path)) == path
        and ".." not in PurePosixPath(path).parts
        and "\\" not in path
        and not any(ord(char) < 32 for char in path),
        "invalid_evidence_path",
    )
    return path


def file_hash(files: dict, path: str) -> str:
    record = mapping(files.get(path), "runtime_file_missing:" + path)
    require(record.get("kind") == "file", "runtime_file_not_regular:" + path)
    value = record.get("sha256")
    require(isinstance(value, str) and SHA.fullmatch(value) is not None, "runtime_hash_invalid")
    assert isinstance(value, str)
    return value


def manifest_component(path: str, component: str) -> bool:
    name = PurePosixPath(path).name
    if component in {"infocmp", "mount", "nsenter"}:
        return name == component
    if component in {"libacl", "libmount"}:
        return name == component + ".so" or name.startswith(component + ".so.")
    if component == "systemd_homed":
        return name in {"systemd-homed", "systemd-homework"}
    if component == "perl_archive_tar":
        return path.endswith("/Archive/Tar.pm")
    if component == "pip_vendor_msgpack_c_extension":
        return "/pip/_vendor/msgpack/" in path and (
            name.endswith((".so", ".pyd", ".dll", ".dylib")) or ".so." in name
        )
    if component in {"pip_vendor_setuptools_package_index", "standalone_setuptools_package_index"}:
        return "/setuptools/" in path and (
            name in {"package_index.py", "package_index.pyc"}
            or ("/__pycache__/" in path and name.startswith("package_index."))
        )
    raise EvidenceError("unknown_component")


def absent(inventory: dict, files: dict, components) -> None:
    for name in components:
        group = inventory["components"][name]
        require(
            group["complete"] is True and group["present"] is False, "component_present:" + name
        )
        require(
            not any(manifest_component(path, name) for path in files),
            "manifest_component_present:" + name,
        )


def validate_sbom_inventory(scan, runtime, sbom_inventory, image_id, runtime_sha) -> list:
    # Local import avoids a module startup cycle with the collector's shared
    # parsing helpers when either stdlib-only CLI is invoked with -m.
    from scripts.image_sbom_inventory import (
        FILTER,
        MAX_BYTES,
        MAX_ENTRIES,
        MAX_SBOM,
        PIP_BOM,
        ROOT_COMPONENT,
        sbom_components,
    )

    require(scan.get("Trivy") == {"Version": "0.70.0"}, "scanner_version_unreviewed")
    config = mapping(scan["Metadata"].get("ImageConfig"), "image_config_missing")
    image_config = mapping(config.get("config"), "image_runtime_config_missing")
    require(image_config.get("Volumes") in (None, {}), "image_declared_volumes_unreviewed")
    proof = mapping(sbom_inventory, "sbom_inventory_missing")
    require(
        proof.get("schema_version") == 1
        and proof.get("complete") is True
        and proof.get("errors") == [],
        "sbom_inventory_incomplete",
    )
    require(proof.get("image_config_id") == image_id, "sbom_inventory_image_mismatch")
    require(proof.get("runtime_manifest_sha256") == runtime_sha, "sbom_runtime_manifest_mismatch")
    require(proof.get("filter") == FILTER, "sbom_scanner_filter_mismatch")
    copied = runtime["copied_files"]
    require(
        type(proof.get("manifest_files_verified")) is int
        and proof["manifest_files_verified"] == len(copied),
        "sbom_manifest_coverage_incomplete",
    )
    require(
        type(proof.get("entries")) is int and len(copied) <= proof["entries"] <= MAX_ENTRIES,
        "sbom_entry_evidence_invalid",
    )
    total = proof.get("tar_bytes")
    require(
        type(total) is int and 1024 <= total <= MAX_BYTES and total % 512 == 0,
        "sbom_tar_evidence_invalid",
    )
    require(
        isinstance(proof.get("tar_sha256"), str) and SHA.fullmatch(proof["tar_sha256"]) is not None,
        "sbom_tar_hash_invalid",
    )
    expected = sorted(
        path
        for path in copied
        if path.endswith(tuple(FILTER["suffixes"])) and FILTER["excluded_substring"] not in path
    )
    candidates = rows(proof.get("recognized_candidates"), "sbom_candidates_invalid", 1)
    require([row.get("path") for row in candidates] == expected, "sbom_candidates_mismatch")
    for candidate in candidates:
        require(candidate["path"] == PIP_BOM, "sbom_origin_unreviewed")
        require(candidate.get("root_component") == ROOT_COMPONENT, "sbom_root_component_unreviewed")
        require(candidate.get("sha256") == file_hash(copied, PIP_BOM), "sbom_origin_hash_mismatch")
        document = candidate.get("document")
        require(isinstance(document, str), "sbom_document_missing")
        data = document.encode("utf-8")
        require(
            len(data) <= MAX_SBOM
            and type(candidate.get("size")) is int
            and candidate["size"] == len(data) == copied[PIP_BOM].get("size")
            and digest(data) == candidate["sha256"],
            "sbom_document_hash_mismatch",
        )
        components = rows(candidate.get("components"), "sbom_components_missing", 1024)
        require(components == sbom_components(data), "sbom_parsed_components_mismatch")
        require(bool(components), "sbom_components_empty")
        identities = []
        for row in components:
            mapping(row, "sbom_component_invalid")
            require(
                set(row) == {"name", "version", "purl", "bom-ref"}, "sbom_component_shape_invalid"
            )
            identities.append(tuple(row[key] for key in ("name", "version", "purl", "bom-ref")))
        require(len(identities) == len(set(identities)), "sbom_component_duplicate")
    return candidates


def derived_sbom_origin(scan: dict, result: dict, finding: dict, candidates: list) -> dict:
    require(len(candidates) == 1, "sbom_finding_origin_ambiguous")
    identity = mapping(finding.get("PkgIdentifier"), "sbom_finding_identifier_missing")
    uid = identity.get("UID")
    require(
        isinstance(uid, str) and re.fullmatch(r"[0-9a-f]{16}", uid) is not None,
        "sbom_finding_uid_invalid",
    )
    matches = []
    for context in scan["Results"]:
        packages = context.get("Packages")
        for package in rows([] if packages is None else packages, "scan_packages_invalid"):
            mapping(package, "scan_package_invalid")
            if (
                mapping(package.get("Identifier", {}), "scan_package_identifier_invalid").get("UID")
                == uid
            ):
                matches.append((context, package))
    require(len(matches) == 1, "sbom_package_join_ambiguous")
    context, package = matches[0]
    require(
        all(context.get(key) == result.get(key) for key in ("Target", "Class", "Type")),
        "sbom_package_context_mismatch",
    )
    require(
        package.get("AnalyzedBy") == "sbom" and type(package.get("AnalyzedBy")) is str,
        "sbom_package_analyzer_unreviewed",
    )
    require(package.get("FilePath") in (None, ""), "sbom_package_path_conflict")
    require(
        package.get("ID")
        == finding.get("PkgID")
        == finding["PkgName"] + "@" + finding["InstalledVersion"]
        and package.get("Name") == finding["PkgName"]
        and package.get("Version") == finding["InstalledVersion"]
        and package.get("Identifier") == identity,
        "sbom_package_identity_mismatch",
    )
    layer = mapping(finding.get("Layer"), "sbom_finding_layer_missing")
    require(
        package.get("Layer") == layer
        and IMAGE.fullmatch(layer.get("DiffID", "")) is not None
        and layer["DiffID"] in scan["Metadata"].get("DiffIDs", []),
        "sbom_package_layer_mismatch",
    )
    candidate = candidates[0]
    expected = {
        "name": finding["PkgName"],
        "version": finding["InstalledVersion"],
        "purl": identity.get("PURL"),
        "bom-ref": identity.get("BOMRef"),
    }
    require(
        expected["purl"] == "pkg:pypi/" + finding["PkgName"] + "@" + finding["InstalledVersion"]
        and expected["bom-ref"] == expected["purl"],
        "sbom_package_purl_unreviewed",
    )
    require(
        sum(row == expected for row in candidate["components"]) == 1, "sbom_component_join_unproven"
    )
    return {
        "path": candidate["path"],
        "sha256": candidate["sha256"],
        "method": "unique_pinned_scanner_input",
        "package_uid": uid,
        "analyzed_by": "sbom",
    }


def validate_evidence(scan, inventory, runtime, smoke, image_id, runtime_sha, *, root, now):
    require(isinstance(image_id, str) and IMAGE.fullmatch(image_id) is not None, "invalid_image_id")
    for value in (scan, inventory, runtime, smoke):
        mapping(value, "evidence_requires_object")
    require(
        scan.get("SchemaVersion") == 2 and scan.get("ArtifactType") == "container_image",
        "scan_schema_invalid",
    )
    metadata = mapping(scan.get("Metadata"), "scan_metadata_missing")
    require(metadata.get("ImageID") == image_id, "scan_image_mismatch")
    require(
        mapping(metadata.get("OS"), "scan_os_missing").get("Family") == "debian",
        "scan_os_unreviewed",
    )
    try:
        created = datetime.fromisoformat(scan["CreatedAt"].replace("Z", "+00:00"))
        require(created.tzinfo is not None, "scan_timestamp_invalid")
        require(
            timedelta(minutes=-5) <= now - created <= timedelta(hours=24), "scan_stale_or_future"
        )
    except (KeyError, AttributeError, TypeError, ValueError) as error:
        if isinstance(error, EvidenceError):
            raise
        raise EvidenceError("scan_timestamp_invalid") from None
    require(inventory.get("schema_version") == 1, "inventory_schema_invalid")
    require(inventory.get("image_config_id") == image_id, "inventory_image_mismatch")
    require(
        inventory.get("complete") is True and inventory.get("errors") == [], "inventory_incomplete"
    )
    require(inventory.get("scopes") == list(SCOPES), "inventory_scope_mismatch")
    groups = mapping(inventory.get("components"), "inventory_components_missing")
    for group in groups.values():
        mapping(group, "inventory_component_invalid")
        require(group.get("complete") is True, "inventory_component_incomplete")
        observed = [
            *rows(group.get("files"), "inventory_component_files_missing"),
            *rows(group.get("directories", []), "inventory_component_directories_invalid"),
        ]
        require(
            all(isinstance(row, dict) and type(row.get("present")) is bool for row in observed),
            "inventory_presence_invalid",
        )
        require(
            group.get("present") is any(row["present"] for row in observed),
            "inventory_presence_conflict",
        )
    require(
        minimal_runtime_components(inventory)["passed"] is True, "minimal_runtime_policy_failed"
    )
    for row in rows(inventory.get("searches"), "inventory_searches_missing"):
        mapping(row, "inventory_search_invalid")
        require(
            row.get("complete") is True and type(row.get("present")) is bool,
            "inventory_search_incomplete",
        )
    dpkg = mapping(inventory.get("dpkg"), "dpkg_inventory_missing")
    require(dpkg.get("complete") is True, "dpkg_inventory_incomplete")
    packages = rows(dpkg.get("packages"), "dpkg_packages_missing", 1024)
    require(bool(packages), "dpkg_packages_empty")
    require(
        runtime.get("schema_version") == 1
        and runtime.get("status") == "assembled_awaiting_linux_smoke_and_scan",
        "runtime_schema_invalid",
    )
    require(runtime.get("security_disposition") == "none", "runtime_preexisting_disposition")
    role = runtime.get("role")
    require(role in {"api", "worker"}, "runtime_role_invalid")
    files = mapping(runtime.get("copied_files"), "runtime_files_missing")
    require(0 < len(files) <= 100000, "runtime_file_limit")
    for path, record in files.items():
        require(logical_path(path) == path, "runtime_path_invalid")
        mapping(record, "runtime_record_invalid")
        require(record.get("kind") in {"file", "symlink"}, "runtime_kind_invalid")
        if record["kind"] == "file":
            file_hash(files, path)
            require(int(record["mode"], 8) & 0o6000 == 0, "runtime_privileged_file")
    status_hash = file_hash(files, "/var/lib/dpkg/status")
    status_rows = [
        row
        for row in rows(dpkg.get("sources"), "dpkg_sources_missing")
        if row.get("path") == "/var/lib/dpkg/status"
    ]
    require(
        len(status_rows) == 1
        and status_rows[0].get("present") is True
        and status_rows[0].get("sha256") == status_hash,
        "dpkg_runtime_hash_mismatch",
    )
    require(
        smoke.get("schema_version") == 1 and smoke.get("role") == role,
        "smoke_role_or_schema_mismatch",
    )
    require(smoke.get("image_config_id") == image_id, "smoke_image_mismatch")
    require(smoke.get("runtime_manifest_sha256") == runtime_sha, "runtime_manifest_smoke_mismatch")
    require(
        smoke.get("status") == "passed" and smoke.get("application_imports") is True,
        "smoke_not_passed",
    )
    if role == "worker":
        fixture = json.loads(
            (root / "packages/contracts/fixtures/font-runtime-hashes.v1.json").read_bytes()
        )
        require(smoke.get("fonts") == fixture["fonts"], "font_smoke_hash_mismatch")
        for path in VALIDATOR_FILES:
            require(
                file_hash(files, "/app/" + path) == digest((root / path).read_bytes()),
                "reviewed_validator_hash_mismatch:" + path,
            )
        boundary = mapping(smoke.get("validator_installer_boundary"), "validator_boundary_missing")
        require(
            boundary.get("status") == "passed" and boundary.get("probes") == PROBES,
            "validator_boundary_unproven",
        )
        require(
            boundary.get("child_sha256") == file_hash(files, "/app/" + VALIDATOR_FILES[1]),
            "validator_child_hash_mismatch",
        )
    else:
        require(
            smoke.get("fonts") == {} and smoke.get("validator_installer_boundary") is None,
            "api_smoke_scope_invalid",
        )
    return files, packages


def disposition(
    result: dict,
    finding: dict,
    inventory: dict,
    files: dict,
    packages: list,
    *,
    role: str,
    scan: dict,
    candidates: list,
) -> dict:
    origin = None
    identifier, package, version = (
        finding.get(key) for key in ("VulnerabilityID", "PkgName", "InstalledVersion")
    )
    require(
        all(isinstance(value, str) and value for value in (identifier, package, version)),
        "finding_identity_missing",
    )
    assert isinstance(identifier, str) and isinstance(package, str) and isinstance(version, str)
    require(finding.get("Severity") == "HIGH", "severity_requires_review")
    if identifier in OS_RULES:
        expected, components = OS_RULES[identifier]
        require(
            result.get("Class") == "os-pkgs" and result.get("Type") == "debian",
            "os_finding_type_mismatch",
        )
        require(expected.get(package) == version, "os_package_or_version_unreviewed")
        require(
            finding.get("PkgPath", "") in {"", "/var/lib/dpkg/status", "var/lib/dpkg/status"},
            "os_package_path_unreviewed",
        )
        require(
            any(row.get("name") == package and row.get("version") == version for row in packages),
            "os_inventory_version_mismatch",
        )
        absent(inventory, files, components)
        reason, justification = (
            "complete_component_absence:" + ",".join(components),
            "vulnerable_code_not_present",
        )
    elif identifier in PYTHON_RULES:
        name, pinned_version, components = PYTHON_RULES[identifier]
        require(
            result.get("Class") == "lang-pkgs" and result.get("Type") == "python-pkg",
            "python_finding_type_mismatch",
        )
        require(
            (package, version) == (name, pinned_version), "python_package_or_version_unreviewed"
        )
        raw_path = finding.get("PkgPath")
        origin = (
            derived_sbom_origin(scan, result, finding, candidates)
            if raw_path in (None, "")
            else None
        )
        path = origin["path"] if origin else logical_path(raw_path)
        require(
            path in {VENDOR + "vendor.txt", VENDOR + "bom.cdx.json"},
            "pip_finding_origin_unreviewed",
        )
        file_hash(files, path)
        manifests = rows(inventory.get("pip_vendor_manifests"), "pip_vendor_manifests_missing")
        matches = [
            row for row in manifests if row.get("file", {}).get("path") == VENDOR + "vendor.txt"
        ]
        require(len(matches) == 1, "pip_vendor_origin_ambiguous")
        vendor = matches[0]
        require(
            vendor["file"].get("present") is True
            and vendor["file"].get("sha256") == file_hash(files, VENDOR + "vendor.txt"),
            "pip_vendor_manifest_hash_mismatch",
        )
        require(vendor.get("versions", {}).get(package) == version, "pip_vendor_version_mismatch")
        distributions = rows(inventory.get("python_distributions"), "python_distributions_missing")
        pip = [row for row in distributions if row.get("name") == "pip"]
        require(
            len(pip) == 1
            and pip[0].get("version") == "26.2.2"
            and pip[0].get("site_packages") == VENDOR.removesuffix("/pip/_vendor/"),
            "pip_distribution_unreviewed",
        )
        metadata = mapping(pip[0].get("metadata"), "pip_metadata_missing")
        metadata_path = "/app/.venv/lib/python3.12/site-packages/pip-26.2.2.dist-info/METADATA"
        require(
            metadata.get("path") == metadata_path,
            "pip_metadata_path_unreviewed",
        )
        require(
            metadata.get("present") is True
            and metadata.get("sha256") == file_hash(files, metadata_path),
            "pip_metadata_hash_mismatch",
        )
        if components:
            absent(inventory, files, components)
            reason, justification = (
                "complete_component_absence:" + ",".join(components),
                "vulnerable_code_not_present",
            )
        else:
            # validate_evidence requires actual worker fonts, guard probes and all
            # three reviewed source hashes before this specific path disposition.
            require(
                role == "worker" and all("/app/" + path in files for path in VALIDATOR_FILES),
                "fixed_validator_boundary_missing",
            )
            reason, justification = (
                "fixed_fontbakery_profile_rejects_installer_imports",
                "vulnerable_code_not_in_execute_path",
            )
    else:
        raise EvidenceError("unreviewed_advisory")
    decision: dict = {
        "status": "not_affected",
        "justification": justification,
        "reason": reason,
        "source": SOURCES[identifier],
    }
    if identifier in PYTHON_RULES and origin:
        decision["derived_origin"] = origin
    return decision


def evaluate(
    scan,
    inventory,
    runtime,
    smoke,
    *,
    image_id,
    scan_sha256,
    runtime_sha256,
    sbom_inventory=None,
    sbom_sha256=None,
    root=ROOT,
    now=None,
) -> dict:
    report: dict = {
        "schema_version": 1,
        "image_config_id": image_id,
        "status": "blocked",
        "raw_scan_sha256": scan_sha256,
        "runtime_manifest_sha256": runtime_sha256,
        "sbom_inventory_sha256": sbom_sha256,
        "findings": [],
        "errors": [],
    }
    try:
        require(
            isinstance(scan_sha256, str) and SHA.fullmatch(scan_sha256) is not None,
            "scan_hash_invalid",
        )
        require(
            isinstance(runtime_sha256, str) and SHA.fullmatch(runtime_sha256) is not None,
            "runtime_hash_invalid",
        )
        results = rows(
            mapping(scan, "scan_requires_object").get("Results"), "scan_results_missing", 1000
        )
        require(
            any(
                isinstance(row, dict)
                and row.get("Class") == "os-pkgs"
                and row.get("Type") == "debian"
                for row in results
            ),
            "os_scan_result_missing",
        )
        raw = []
        for result in results:
            mapping(result, "scan_result_invalid")
            for key in ("Secrets", "Misconfigurations", "Licenses"):
                value = result.get(key)
                require(
                    value is None or isinstance(value, list),
                    "scan_non_vulnerability_findings_invalid",
                )
                require(not value, "non_vulnerability_findings_require_review")
            findings = result.get("Vulnerabilities")
            for finding in rows([] if findings is None else findings, "scan_findings_invalid"):
                mapping(finding, "scan_finding_invalid")
                raw.append(
                    {
                        "result": {key: result.get(key) for key in ("Target", "Class", "Type")},
                        "finding": finding,
                    }
                )
                require(len(raw) <= 10000, "finding_limit")
        report["findings"] = raw
        files, packages = validate_evidence(
            scan,
            inventory,
            runtime,
            smoke,
            image_id,
            runtime_sha256,
            root=root,
            now=now or datetime.now(UTC),
        )
        require(
            isinstance(sbom_sha256, str) and SHA.fullmatch(sbom_sha256) is not None,
            "sbom_inventory_hash_invalid",
        )
        candidates = validate_sbom_inventory(
            scan, runtime, sbom_inventory, image_id, runtime_sha256
        )
        for row in raw:
            try:
                row["disposition"] = disposition(
                    row["result"],
                    row["finding"],
                    inventory,
                    files,
                    packages,
                    role=runtime["role"],
                    scan=scan,
                    candidates=candidates,
                )
            except EvidenceError as error:
                row["disposition"] = {"status": "blocked", "reason": str(error)}
        blocked = sum(row["disposition"]["status"] == "blocked" for row in raw)
        report.update(
            raw_findings=len(raw),
            effective_blocking_findings=blocked,
            reviewed_dispositions=len(raw) - blocked,
        )
        if not blocked:
            report["status"] = "passed"
    except (EvidenceError, KeyError, TypeError, AttributeError, ValueError, OSError) as fatal:
        # Report code only: generated evidence must never become executable input.
        report["errors"] = [
            str(fatal) if isinstance(fatal, EvidenceError) else "malformed_or_missing_evidence"
        ]
    return report


def load_json(path: Path, maximum: int) -> tuple[dict, str]:
    with path.open("rb") as source:
        raw = source.read(maximum + 1)
    require(len(raw) <= maximum, "evidence_byte_limit")

    def unique(pairs):
        output = {}
        for key, value in pairs:
            require(key not in output, "duplicate_json_key")
            output[key] = value
        return output

    value = json.loads(raw, object_pairs_hook=unique)
    return mapping(value, "evidence_requires_object"), digest(raw)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("scan", "inventory", "runtime-manifest", "smoke", "sbom-inventory", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--image-id", required=True)
    args = parser.parse_args()
    inputs = (args.scan, args.inventory, args.runtime_manifest, args.smoke, args.sbom_inventory)
    if args.output.resolve() in {path.resolve() for path in inputs}:
        parser.error("output must be separate from every evidence input")
    try:
        scan, scan_sha = load_json(args.scan, 20 * 1024 * 1024)
        inventory, _ = load_json(args.inventory, 512 * 1024)
        runtime, runtime_sha = load_json(args.runtime_manifest, 20 * 1024 * 1024)
        smoke, _ = load_json(args.smoke, 64 * 1024)
        sbom_inventory, sbom_sha = load_json(args.sbom_inventory, 2 * 1024 * 1024)
        result = evaluate(
            scan,
            inventory,
            runtime,
            smoke,
            image_id=args.image_id,
            scan_sha256=scan_sha,
            runtime_sha256=runtime_sha,
            sbom_inventory=sbom_inventory,
            sbom_sha256=sbom_sha,
        )
    except (OSError, ValueError, RecursionError):
        result = {
            "schema_version": 1,
            "status": "blocked",
            "errors": ["malformed_or_missing_evidence"],
        }
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "status": result["status"],
                "blocking": result.get("effective_blocking_findings"),
                "errors": result.get("errors", []),
            }
        )
    )
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
