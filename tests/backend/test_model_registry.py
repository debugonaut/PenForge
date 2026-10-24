import hashlib
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from packages.ml_core.registry import ModelCache, RegistryRejected, constrained_rules


def fixture():
    data = b'{"schema_version":1,"kind":"enum_map","mapping":{"small":"review"}}'
    model_id = str(uuid.uuid4())
    record = dict(
        id=model_id,
        task="rules",
        format="json_rules",
        artifact_key="models/" + uuid.uuid4().hex,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        input_schema={"enum": ["small"]},
        output_schema={"enum": ["review"]},
        scripts=["Latn"],
        styles=["print"],
        input_classes=["synthetic_control"],
        compatible_worker=">=0.1,<0.2",
        license="CC0-1.0",
        provenance={"synthetic_security_fixture": True},
        status="approved",
        resource_profile={},
    )
    requirements = dict(
        task="rules",
        script="Latn",
        style="print",
        input_class="synthetic_control",
        worker_version="0.1.0",
        input_schema=record["input_schema"],
        output_schema=record["output_schema"],
    )
    return data, record, requirements


@pytest.mark.parametrize(
    "field,value",
    [
        ("sha256", "0" * 64),
        ("size_bytes", 1),
        ("status", "revoked"),
        ("format", "pickle"),
        ("format", "joblib"),
        ("format", "pytorch"),
        ("artifact_key", "/tmp/model.onnx"),
        ("artifact_key", "https://example.com/model"),
        ("artifact_key", "models/../escape"),
        ("task", "recognition"),
        ("scripts", ["Deva"]),
        ("styles", ["cursive"]),
        ("input_classes", ["photo"]),
        ("compatible_worker", ">=1.0,<2.0"),
        ("input_schema", {}),
        ("output_schema", {}),
        ("license", ""),
        ("provenance", {}),
    ],
)
def test_registry_fails_closed(field, value):
    data, record, requirements = fixture()
    record[field] = value
    cache = ModelCache(lambda _: record, lambda *_: data)
    with pytest.raises(RegistryRejected):
        cache.get(record["id"], **requirements)


def test_cache_revocation_tampering_eviction_and_resource_gate():
    data, record, requirements = fixture()
    cache = ModelCache(lambda _: record, lambda *_: data, max_entries=1)
    assert cache.get(record["id"], **requirements).data == data
    cache._cache[record["id"]] = b"x" * len(data)
    with pytest.raises(RegistryRejected, match="integrity"):
        cache.get(record["id"], **requirements)
    cache.get(record["id"], **requirements)
    record["status"] = "revoked"
    with pytest.raises(RegistryRejected):
        cache.get(record["id"], **requirements)
    record["status"] = "approved"
    with pytest.raises(RegistryRejected, match="resources_unmeasured"):
        cache.get(record["id"], **requirements, require_resources=True)
    cache.evict()
    assert not cache._cache


@pytest.mark.parametrize("format", ["onnx", "safetensors"])
def test_mislabeled_pickle_container_is_rejected(format):
    data, record, requirements = fixture()
    data = b"\x80\x04cos\nsystem\nunsafe"
    record.update(format=format, size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    with pytest.raises(RegistryRejected):
        ModelCache(lambda _: record, lambda *_: data).get(record["id"], **requirements)


@pytest.mark.parametrize(
    "value",
    [
        b"{}",
        b"null",
        b'{"schema_version":1,"kind":"python","mapping":{}}',
        b'{"schema_version":1,"kind":"enum_map","mapping":{"a":"b"},"exec":"x"}',
        b'{"schema_version":1,"schema_version":1,"kind":"enum_map","mapping":{"a":"b"}}',
    ],
)
def test_json_rules_are_not_executable(value):
    with pytest.raises(RegistryRejected):
        constrained_rules(value)


def test_security_checks_in_network_denied_worker():
    command = [
        sys.executable,
        "-m",
        "pytest",
        "tests/backend/test_model_registry.py",
        "-q",
        "-k",
        "not network_denied",
    ]
    script = (
        "from packages.capture_safety._sandbox import install_and_check_network_deny; install_and_check_network_deny(); import pytest; raise SystemExit(pytest.main("
        + repr(command[3:])
        + "))"
    )
    command = [sys.executable, "-c", script]
    if sys.platform == "darwin":
        command = [
            "/usr/bin/sandbox-exec",
            "-p",
            "(version 1)(allow default)(deny network*)",
            *command,
        ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-1000:]
    folder = Path("artifacts/phase08")
    folder.mkdir(exist_ok=True)
    (folder / "registry-network-denied.txt").write_text(result.stdout)
