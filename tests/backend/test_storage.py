import hashlib
import os
import time
from dataclasses import replace

import pytest
from app.adapters.storage.base import ImmutableConflict, ObjectMissing, StorageError
from app.adapters.storage.local_fs import LocalObjectStorage


@pytest.fixture
def storage(tmp_path):
    result = LocalObjectStorage(tmp_path / "private")
    yield result
    result.close()


def upload(storage, key="u/test/orig/object", data=b"original"):
    capability = storage.create_upload(
        key, sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data)
    )
    return capability, storage.put(capability, data)


def test_immutable_replay_conflict_hash_and_download(storage):
    capability, metadata = upload(storage)
    assert storage.put(capability, b"original") == metadata
    assert storage.read(storage.get_download_url("u/test/orig/object")) == b"original"
    with pytest.raises(StorageError):
        storage.put(capability, b"changed")
    with pytest.raises(ImmutableConflict):
        upload(storage, data=b"different")
    with pytest.raises(StorageError):
        storage.verify_upload("u/test/orig/object", sha256="0" * 64, size_bytes=8)
    assert (
        storage.verify_upload(
            "u/test/orig/object", sha256=metadata.sha256, size_bytes=metadata.size_bytes
        )
        == metadata
    )


def test_missing_delete_repeated_and_no_staging_files(storage):
    storage.delete_object("not/present")
    with pytest.raises(ObjectMissing):
        storage.get_metadata("not/present")
    upload(storage)
    storage.delete_object("u/test/orig/object")
    storage.delete_object("u/test/orig/object")
    assert not list(storage.root.rglob(".staged-*"))


@pytest.mark.parametrize(
    "key", ["../outside", "/absolute", "x/../../outside", "x//y", "x/./y", "a\\b", "x\x00"]
)
def test_traversal_is_rejected(storage, key):
    with pytest.raises(StorageError):
        storage.create_upload(key, sha256="0" * 64, size_bytes=0)


def test_symlink_root_parent_and_object_are_rejected(storage, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (storage.root / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(StorageError):
        upload(storage, "escape/object")
    (outside / "secret").write_bytes(b"secret")
    (storage.root / "object").symlink_to(outside / "secret")
    with pytest.raises(StorageError):
        storage.get_metadata("object")
    with pytest.raises(StorageError):
        storage.delete_object("object")
    root_link = tmp_path / "root-link"
    root_link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(StorageError):
        LocalObjectStorage(root_link)
    with pytest.raises(StorageError):
        LocalObjectStorage(root_link / "nested")


def test_capabilities_are_method_bound_expiring_and_tamper_proof(storage, monkeypatch):
    capability, _ = upload(storage)
    with pytest.raises(StorageError):
        storage.read(capability)
    with pytest.raises(StorageError):
        storage.put(replace(capability, token=capability.token + "0"), b"original")
    monkeypatch.setattr("time.time", lambda: capability.expires_at + 1)
    with pytest.raises(StorageError):
        storage.put(capability, b"original")


def test_content_corruption_is_detected(storage):
    upload(storage)
    capability = storage.get_download_url("u/test/orig/object")
    (storage.root / "u/test/orig/object").write_bytes(b"tampered")
    with pytest.raises(StorageError):
        storage.read(capability)


def test_concurrent_writes_never_overwrite_the_winner(storage):
    from concurrent.futures import ThreadPoolExecutor

    def write(data):
        try:
            return upload(storage, "shared/object", data)[1]
        except ImmutableConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(write, [b"first", b"second"]))
    assert sum(item is not None for item in results) == 1
    winner = next(item for item in results if item is not None)
    assert storage.get_metadata("shared/object") == winner


def test_insecure_existing_root_is_rejected(tmp_path):
    root = tmp_path / "public"
    root.mkdir(mode=0o755)
    with pytest.raises(StorageError):
        LocalObjectStorage(root)


def test_optional_hash_exact_size_streaming_and_early_size_rejection(storage):
    capability = storage.create_upload("bounded/original", size_bytes=6, max_bytes=6)
    assert storage.put_stream(capability.token, [b"ab", b"cd", b"ef"]).size_bytes == 6
    assert b"".join(storage.iter_object("bounded/original", chunk_size=2)) == b"abcdef"
    assert storage.read_object("bounded/original", max_bytes=6) == b"abcdef"
    with pytest.raises(StorageError):
        storage.read_object("bounded/original", max_bytes=5)
    with pytest.raises(StorageError):
        storage.create_upload("too-large", size_bytes=7, max_bytes=6)
    consumed = []

    def chunks():
        for chunk in [b"abc", b"defg", b"never consumed"]:
            consumed.append(chunk)
            yield chunk

    other = storage.create_upload("bounded/over", size_bytes=6)
    with pytest.raises(StorageError):
        storage.put_stream(other, chunks())
    assert len(consumed) == 2
    with pytest.raises(ObjectMissing):
        storage.get_metadata("bounded/over")
    with pytest.raises(StorageError):
        storage.put_stream(other, [b"short"])
    assert not list(storage.root.rglob(".staged-*"))


def test_transport_token_opaque_nonce_stable_secret_and_method_scope(tmp_path):
    root = tmp_path / "private"
    storage = LocalObjectStorage(root, secret=b"stable-test-key")
    capability = storage.create_upload("private/owner/object", size_bytes=3)
    other = storage.create_upload("private/owner/object", size_bytes=3)
    assert capability.token != other.token
    assert "private" not in capability.token
    assert capability.token not in repr(capability)
    claims = storage.validate_capability(capability.token, "PUT")
    assert claims.key == "private/owner/object"
    assert claims.size_bytes == 3 and claims.sha256 is None
    assert "private" not in repr(claims)
    storage.close()
    restarted = LocalObjectStorage(root, secret=b"stable-test-key")
    try:
        restarted.put(capability.token, b"abc")
        download = restarted.get_download_url("private/owner/object", ttl_seconds=2)
        assert restarted.read(download.token) == b"abc"
        with pytest.raises(StorageError):
            restarted.validate_capability(download.token, "PUT")
        with pytest.raises(StorageError):
            restarted.validate_capability(capability.token, "GET")
        with pytest.raises(StorageError):
            restarted.create_upload("x", size_bytes=0, ttl_seconds=3601)
    finally:
        restarted.close()


def test_capability_expiry_is_rechecked_before_object_publication(storage, monkeypatch):
    capability = storage.create_upload("expiry/object", size_bytes=3, ttl_seconds=1)

    def chunks():
        yield b"a"
        monkeypatch.setattr("time.time", lambda: capability.expires_at)
        yield b"bc"

    with pytest.raises(StorageError):
        storage.put_stream(capability, chunks())
    with pytest.raises(ObjectMissing):
        storage.get_metadata("expiry/object")
    assert not list(storage.root.rglob(".staged-*"))


def test_internal_immutable_writes_inventory_and_safe_staging_cleanup(storage, tmp_path):
    storage.put_immutable("u/a/p/one/original", b"one")
    storage.put_immutable_stream("u/a/p/two/thumbnail", [b"t", b"wo"], size_bytes=3)
    assert storage.put_immutable("u/a/p/one/original", b"one").size_bytes == 3
    with pytest.raises(ImmutableConflict):
        storage.put_immutable("u/a/p/one/original", b"different")
    outside = tmp_path / "unrelated"
    outside.write_bytes(b"outside")
    (storage.root / "symlink").symlink_to(outside)
    old = storage.root / (".staged-" + "a" * 32)
    old.write_bytes(b"incomplete")
    os.utime(old, (time.time() - 7200,) * 2)
    active = storage.root / (".staged-" + "b" * 32)
    active.write_bytes(b"active")
    assert {item.key for item in storage.list_objects("u/a/p/one/")} == {"u/a/p/one/original"}
    assert len(storage.list_objects()) == 2
    assert storage.reap_staging() == 1
    assert storage.reap_staging() == 0
    assert active.read_bytes() == b"active" and outside.read_bytes() == b"outside"


def test_interleaved_streams_preserve_atomic_winner_and_clean_partial_failure(storage):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    barrier = Barrier(2)

    def write(data):
        def chunks():
            yield data[:1]
            barrier.wait(timeout=5)
            yield data[1:]

        try:
            return storage.put_immutable_stream(
                "interleaved/object", chunks(), size_bytes=len(data)
            )
        except ImmutableConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(write, [b"first", b"second"]))
    assert sum(result is not None for result in results) == 1
    winner = next(result for result in results if result is not None)
    assert storage.get_metadata("interleaved/object") == winner
    assert not list(storage.root.rglob(".staged-*"))

    def failure():
        yield b"part"
        raise RuntimeError("simulated interrupted stream")

    with pytest.raises(RuntimeError):
        storage.put_immutable_stream("interleaved/failed", failure(), size_bytes=8)
    with pytest.raises(ObjectMissing):
        storage.get_metadata("interleaved/failed")
    assert not list(storage.root.rglob(".staged-*"))
