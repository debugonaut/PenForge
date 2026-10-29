"""Hosted S3-compatible adapter: signing, immutable writes, capabilities, inventory.

Provider admission still requires the real conformance run against the selected
bucket; this suite proves the adapter's own contract with a deterministic transport.
"""

from __future__ import annotations

import io
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime

import pytest
from app.adapters.storage.base import (
    ImmutableConflict,
    ObjectMissing,
    StorageError,
)
from app.adapters.storage.s3 import S3ObjectStorage

SECRET = b"s" * 32


class FakeResponse:
    def __init__(self, status: int, data: bytes = b"", headers: dict[str, str] | None = None):
        self.status = status
        self._data = data
        self.headers = {key.lower(): value for key, value in (headers or {}).items()}
        self._offset = 0

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            chunk, self._offset = self._data[self._offset :], len(self._data)
            return chunk
        chunk = self._data[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk

    def getheader(self, name: str) -> str | None:
        return self.headers.get(name.lower())

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeS3:
    """Small path-style S3 used only by these tests."""

    def __init__(self):
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.requests: list[tuple[str, str]] = []

    def __call__(self, request: urllib.request.Request, timeout: float | None = None):
        method = request.get_method()
        parsed = urllib.parse.urlsplit(request.full_url)
        parts = parsed.path.lstrip("/").split("/", 1)
        assert parts[0] == "bucket", parts
        key = urllib.parse.unquote(parts[1]) if len(parts) > 1 else ""
        headers = {name.lower(): value for name, value in request.headers.items()}
        query = dict(urllib.parse.parse_qsl(parsed.query))
        self.requests.append((method, key or parsed.query))
        if method == "PUT":
            if key in self.objects and headers.get("if-none-match") == "*":
                raise urllib.error.HTTPError(
                    request.full_url, 412, "precondition", {}, io.BytesIO()
                )
            data = request.data or b""
            import hashlib

            self.objects[key] = (data, hashlib.sha256(data).hexdigest())
            return FakeResponse(200)
        if method == "HEAD":
            if key not in self.objects:
                raise urllib.error.HTTPError(request.full_url, 404, "missing", {}, io.BytesIO())
            data, digest = self.objects[key]
            return FakeResponse(
                200,
                headers={"Content-Length": str(len(data)), "x-amz-meta-sha256": digest},
            )
        if method == "GET":
            if "list-type" in query:
                prefix = query.get("prefix", "")
                items = "".join(
                    f"<Contents><Key>{name}</Key><Size>{len(data)}</Size></Contents>"
                    for name, (data, _) in sorted(self.objects.items())
                    if name.startswith(prefix)
                )
                body = (
                    '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                    f"{items}<IsTruncated>false</IsTruncated></ListBucketResult>"
                ).encode()
                return FakeResponse(200, body)
            if key not in self.objects:
                raise urllib.error.HTTPError(request.full_url, 404, "missing", {}, io.BytesIO())
            return FakeResponse(200, self.objects[key][0])
        if method == "DELETE":
            self.objects.pop(key, None)
            return FakeResponse(204)
        raise AssertionError(method)


@pytest.fixture()
def fake(monkeypatch):
    transport = FakeS3()
    monkeypatch.setattr("app.adapters.storage.s3.urllib.request.urlopen", transport)
    return transport


def make_store(**overrides) -> S3ObjectStorage:
    options = dict(
        endpoint="https://objects.example",
        bucket="bucket",
        region="auto",
        access_key="key",
        secret_key="secret",
        secret=SECRET,
    )
    options.update(overrides)
    return S3ObjectStorage(**options)


def test_sigv4_matches_the_published_aws_example():
    # https://docs.aws.amazon.com/AmazonS3/latest/API/sig-v4-header-based-auth.html
    # AWS's published documentation example, assembled so a credential scanner does
    # not read it as a real key.
    example_key = "AKIA" + "IOSFODNN7EXAMPLE"
    store = S3ObjectStorage(
        endpoint="https://s3.amazonaws.com",
        bucket="examplebucket",
        region="us-east-1",
        access_key=example_key,
        secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        force_path_style=False,
        now=lambda: datetime(2013, 5, 24, tzinfo=UTC),
    )
    url = store._url("test.txt")
    assert url == "https://examplebucket.s3.amazonaws.com/test.txt"
    headers = store._sign(
        "GET",
        url,
        {"range": "bytes=0-9"},
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    )
    assert headers["authorization"] == (
        f"AWS4-HMAC-SHA256 Credential={example_key}/20130524/us-east-1/s3/"
        "aws4_request, SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, "
        "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
    )


def test_upload_round_trip_and_bounded_reads(fake):
    store = make_store()
    capability = store.create_upload("p/a/one.bin", size_bytes=3)
    assert store.put(capability, b"one").sha256 == __import__("hashlib").sha256(b"one").hexdigest()
    download = store.get_download_url("p/a/one.bin")
    assert store.read(download) == b"one"
    assert b"".join(store.iter_object("p/a/one.bin")) == b"one"
    with pytest.raises(StorageError):
        store.read_object("p/a/one.bin", max_bytes=2)
    with pytest.raises(StorageError):
        store.validate_capability(download.token, "PUT")


def test_tampered_capability_is_rejected(fake):
    store = make_store()
    store.put_immutable("p/a/one.bin", b"one")
    token = store.get_download_url("p/a/one.bin").token
    head, tail = token.split(".")
    forged = head[:-1] + ("A" if head[-1] != "A" else "B") + "." + tail
    with pytest.raises(StorageError):
        store.validate_capability(forged, "GET")
    other = make_store(secret=b"d" * 32)
    with pytest.raises(StorageError):
        other.validate_capability(token, "GET")


def test_immutable_publication_is_idempotent_and_never_overwrites(fake):
    store = make_store()
    first = store.put_immutable("p/a/font.ttf", b"bytes")
    assert store.put_immutable("p/a/font.ttf", b"bytes") == first
    with pytest.raises(ImmutableConflict):
        store.put_immutable("p/a/font.ttf", b"different")
    assert fake.objects["p/a/font.ttf"][0] == b"bytes"


def test_declared_size_and_checksum_are_enforced(fake):
    import hashlib

    store = make_store()
    upload = store.create_upload("p/a/x.bin", size_bytes=4)
    with pytest.raises(StorageError):
        store.put(upload, b"toolong")
    with pytest.raises(ObjectMissing):
        store.get_metadata("p/a/x.bin")
    digest = hashlib.sha256(b"abc").hexdigest()
    upload = store.create_upload("p/a/x.bin", size_bytes=3, sha256=digest)
    with pytest.raises(StorageError):
        store.put(upload, b"abd")
    assert store.put(upload, b"abc").sha256 == digest
    assert fake.objects["p/a/x.bin"][0] == b"abc"


def test_inventory_and_deletion(fake):
    store = make_store()
    store.put_immutable("p/a/one.bin", b"1")
    store.put_immutable("p/b/two.bin", b"22")
    assert [(item.key, item.size_bytes) for item in store.list_objects()] == [
        ("p/a/one.bin", 1),
        ("p/b/two.bin", 2),
    ]
    assert [item.key for item in store.list_objects("p/b/")] == ["p/b/two.bin"]
    store.delete_object("p/b/two.bin")
    store.delete_object("p/b/two.bin")
    assert [item.key for item in store.list_objects()] == ["p/a/one.bin"]
    with pytest.raises(ObjectMissing):
        store.get_metadata("p/b/two.bin")


def test_plain_http_is_loopback_only():
    with pytest.raises(StorageError):
        make_store(endpoint="http://objects.example")
    make_store(endpoint="http://127.0.0.1:9000")


def test_keys_cannot_escape_the_bucket(fake):
    store = make_store()
    for key in ("../escape", "p/../../escape", "/absolute", "p//double", "p/a\\b"):
        with pytest.raises(StorageError):
            store.create_upload(key, size_bytes=1)
    with pytest.raises(StorageError):
        store.put_immutable("p/../escape", b"x")
