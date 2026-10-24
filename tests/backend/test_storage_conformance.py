"""Provider-neutral storage conformance.

Any adapter added for a hosted provider must satisfy these invariants before it can
be selected. Today only the local filesystem adapter is registered; adding a factory
entry here is how a cloud adapter gets admitted.

The suite is deliberately written against the ``ObjectStorage`` protocol only, so it
never depends on filesystem details.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Protocol

import pytest
from app.adapters.storage.base import (
    ImmutableConflict,
    ObjectMissing,
    ObjectStorage,
    StorageError,
)
from app.adapters.storage.local_fs import LocalObjectStorage

SECRET = b"s" * 32


class StorageFactory(Protocol):
    def __call__(self, root: Path, *, secret: bytes = SECRET) -> ObjectStorage: ...


FACTORIES: dict[str, StorageFactory] = {
    "local_fs": lambda root, secret=SECRET: LocalObjectStorage(root / "objects", secret=secret),
}
BACKENDS = sorted(FACTORIES)


@pytest.fixture(params=BACKENDS)
def storage(request, tmp_path):
    instance = FACTORIES[request.param](tmp_path)
    try:
        yield request.param, instance
    finally:
        instance.close()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def put(storage, key: str, data: bytes, **overrides):
    capability = storage.create_upload(key, size_bytes=len(data), **overrides)
    return storage.put(capability, data)


def test_upload_capability_is_write_only_and_single_key(storage):
    _, store = storage
    put(store, "p/a/one.bin", b"one")
    upload = store.create_upload("p/a/two.bin", size_bytes=3)
    with pytest.raises(StorageError):
        store.validate_capability(upload.token, "GET")
    with pytest.raises(StorageError):
        store.read(upload.token)
    assert store.read(store.get_download_url("p/a/one.bin")) == b"one"
    # The download capability names exactly one object.
    assert store.validate_capability(store.get_download_url("p/a/one.bin").token, "GET").key == (
        "p/a/one.bin"
    )


def test_capability_expiry_is_short_lived_and_enforced(storage):
    _, store = storage
    with pytest.raises(StorageError):
        store.create_upload("p/a/x.bin", size_bytes=1, ttl_seconds=0)
    with pytest.raises(StorageError):
        store.create_upload("p/a/x.bin", size_bytes=1, ttl_seconds=3601)
    short = store.create_upload("p/a/x.bin", size_bytes=1, ttl_seconds=1)
    time.sleep(1.1)
    with pytest.raises(StorageError):
        store.put(short, b"x")


def test_declared_size_and_checksum_are_enforced_without_partial_objects(storage):
    _, store = storage
    upload = store.create_upload("p/a/size.bin", size_bytes=4)
    with pytest.raises(StorageError):
        store.put(upload, b"toolong")
    with pytest.raises(StorageError):
        store.put(upload, b"ab")
    with pytest.raises(ObjectMissing):
        store.get_metadata("p/a/size.bin")
    assert store.list_objects("p/a/") == []

    upload = store.create_upload("p/a/hash.bin", size_bytes=3, sha256=digest(b"abc"))
    with pytest.raises(StorageError):
        store.put(upload, b"abd")
    with pytest.raises(ObjectMissing):
        store.get_metadata("p/a/hash.bin")

    assert store.put(upload, b"abc").sha256 == digest(b"abc")
    assert store.verify_upload("p/a/hash.bin", size_bytes=3, sha256=digest(b"abc"))


def test_oversized_declaration_is_refused_at_issuance(storage):
    _, store = storage
    with pytest.raises(StorageError):
        store.create_upload("p/a/huge.bin", size_bytes=21 * 1024 * 1024)
    with pytest.raises(StorageError):
        store.create_upload("p/a/neg.bin", size_bytes=-1)
    with pytest.raises(StorageError):
        store.create_upload("p/a/bad.bin", size_bytes=1, sha256="not-a-hash")


def test_publication_is_immutable_and_idempotent(storage):
    _, store = storage
    first = store.put_immutable("p/a/font.ttf", b"bytes")
    second = store.put_immutable("p/a/font.ttf", b"bytes")
    assert first == second
    with pytest.raises(ImmutableConflict):
        store.put_immutable("p/a/font.ttf", b"different")


def test_inventory_is_complete_and_never_lists_staging(storage):
    _, store = storage
    store.put_immutable("p/a/one.bin", b"1")
    store.put_immutable("p/a/two.bin", b"22")
    store.put_immutable("p/b/three.bin", b"333")
    inventory = {(item.key, item.size_bytes) for item in store.list_objects()}
    assert inventory == {
        ("p/a/one.bin", 1),
        ("p/a/two.bin", 2),
        ("p/b/three.bin", 3),
    }
    assert [item.key for item in store.list_objects("p/b/")] == ["p/b/three.bin"]
    assert store.reap_staging(older_than_seconds=60) == 0


def test_deletion_is_idempotent_and_reconciles_inventory(storage):
    _, store = storage
    store.put_immutable("p/a/gone.bin", b"data")
    store.delete_object("p/a/gone.bin")
    store.delete_object("p/a/gone.bin")
    assert store.list_objects() == []
    with pytest.raises(ObjectMissing):
        store.get_metadata("p/a/gone.bin")


def test_keys_cannot_escape_the_root(storage):
    _, store = storage
    for key in ("../escape", "p/../../escape", "/absolute", "p//double", "p/a\\b"):
        with pytest.raises(StorageError):
            store.create_upload(key, size_bytes=1)
    with pytest.raises(StorageError):
        store.put_immutable("p/../escape", b"x")


def test_reads_are_bounded(storage):
    _, store = storage
    store.put_immutable("p/a/big.bin", b"x" * 100)
    assert len(store.read_object("p/a/big.bin", max_bytes=100)) == 100
    with pytest.raises(StorageError):
        store.read_object("p/a/big.bin", max_bytes=99)
    with pytest.raises(StorageError):
        list(store.iter_object("p/a/big.bin", chunk_size=0))


def test_tampered_capability_is_rejected_by_a_different_key(storage, tmp_path):
    name, store = storage
    store.put_immutable("p/a/one.bin", b"one")
    token = store.get_download_url("p/a/one.bin").token
    other = FACTORIES[name](tmp_path / "other", secret=b"d" * 32)
    try:
        with pytest.raises(StorageError):
            other.validate_capability(token, "GET")
    finally:
        other.close()
