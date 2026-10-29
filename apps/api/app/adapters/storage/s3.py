"""Private S3-compatible object storage for hosted deployments.

Only the API and worker read this bucket; browser transfers stay behind the existing
owner-checked ``/storage/objects`` routes. Object reads and writes use the same
size/checksum/capability rules as the local adapter, and immutable keys refuse a
second write through a conditional PUT (S3/R2/MinIO support ``If-None-Match: *``).

The client is deliberately stdlib-only (SigV4 + urllib) so the locked runtime images
gain no new dependency. It runs in the existing ``asyncio.to_thread`` storage calls.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ElementTree
from datetime import UTC, datetime
from typing import Iterable, Iterator

from app.adapters.storage.base import (
    CapabilityClaims,
    ImmutableConflict,
    LocalCapability,
    ObjectMetadata,
    ObjectMissing,
    StorageError,
    StoredObject,
)

_SAFE_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_TOKEN = re.compile(r"[A-Za-z0-9_-]{16,4096}\.[A-Za-z0-9_-]{16,512}")


def _split_key(key: str) -> list[str]:
    parts = key.split("/")
    if (
        len(key) > 1024
        or not parts
        or any(not _SAFE_KEY.fullmatch(part) or part in (".", "..") for part in parts)
    ):
        raise StorageError("Invalid object key")
    return parts


class S3ObjectStorage:
    """Immutable private objects with opaque, owner-checked transport capabilities."""

    def __init__(
        self,
        *,
        endpoint: str,
        bucket: str,
        region: str = "auto",
        access_key: str,
        secret_key: str,
        prefix: str = "",
        force_path_style: bool = True,
        secret: bytes | None = None,
        ttl_seconds: int = 600,
        max_upload_bytes: int = 20 * 1024 * 1024,
        timeout_seconds: int = 30,
        clock=time.time,
        now=lambda: datetime.now(UTC),
    ):
        parsed = urllib.parse.urlsplit(endpoint)
        if (
            parsed.scheme not in ("https", "http")
            or not parsed.hostname
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise StorageError("S3 endpoint must be a plain http(s) origin")
        if parsed.scheme == "http" and parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise StorageError("Plain HTTP object storage is restricted to loopback")
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,62}", bucket):
            raise StorageError("Invalid S3 bucket name")
        if not re.fullmatch(r"[a-z0-9-]{1,32}", region):
            raise StorageError("Invalid S3 region")
        if not access_key or not secret_key:
            raise StorageError("S3 credentials are required")
        if prefix:
            prefix = prefix.strip("/")
            _split_key(prefix)
            prefix += "/"
        if not 1 <= ttl_seconds <= 3600:
            raise StorageError("Capability expiry must be short lived")
        self._endpoint = parsed
        self._bucket = bucket
        self._region = region
        self._access_key = access_key
        self._secret_key = secret_key.encode()
        self._prefix = prefix
        self._force_path_style = force_path_style
        self._secret = secret or secrets.token_bytes(32)
        self._ttl = ttl_seconds
        self._max_upload_bytes = max_upload_bytes
        self._timeout = max(1, min(timeout_seconds, 120))
        self._clock = clock
        self._now = now

    def close(self) -> None:  # Protocol compatibility; urllib holds no pool.
        return None

    # ------------------------------------------------------------------ signing
    def _key_prefix(self, key: str) -> str:
        _split_key(key)
        return self._prefix + key

    def _url(self, key: str = "", query: dict[str, str] | None = None) -> str:
        host = self._endpoint.hostname or ""
        if self._endpoint.port:
            host += f":{self._endpoint.port}"
        if key:
            _split_key(key)
        stored = self._prefix + key if key else ""
        quoted_key = "/".join(urllib.parse.quote(part, safe="-_.~") for part in stored.split("/"))
        base_path = self._endpoint.path.rstrip("/")
        virtual = not self._force_path_style and self._endpoint.hostname not in (
            "127.0.0.1",
            "localhost",
            "::1",
        )
        if virtual:
            host = f"{self._bucket}." + host
            path = f"{base_path}/{quoted_key}" if quoted_key else base_path or "/"
        else:
            path = f"{base_path}/{self._bucket}" + (f"/{quoted_key}" if quoted_key else "")
        path = path or "/"
        params = urllib.parse.urlencode(sorted((query or {}).items()), quote_via=urllib.parse.quote)
        return urllib.parse.urlunsplit((self._endpoint.scheme, host, path, params, ""))

    def _sign(
        self, method: str, url: str, headers: dict[str, str], payload_hash: str
    ) -> dict[str, str]:
        parsed = urllib.parse.urlsplit(url)
        now = self._now()
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date = now.strftime("%Y%m%d")
        headers = {
            **headers,
            "host": parsed.netloc,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        }
        canonical_headers = "".join(
            f"{name.lower()}:{' '.join(value.strip().split())}\n"
            for name, value in sorted(headers.items())
        )
        signed_headers = ";".join(sorted(name.lower() for name in headers))
        canonical_query = "&".join(
            f"{urllib.parse.quote(name, safe='-_.~')}={urllib.parse.quote(value, safe='-_.~')}"
            for name, value in sorted(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
        )
        canonical_request = "\n".join(
            [
                method,
                parsed.path or "/",
                canonical_query,
                canonical_headers,
                signed_headers,
                payload_hash,
            ]
        )
        scope = f"{date}/{self._region}/s3/aws4_request"
        string_to_sign = "\n".join(
            [
                "AWS4-HMAC-SHA256",
                amz_date,
                scope,
                hashlib.sha256(canonical_request.encode()).hexdigest(),
            ]
        )
        key = ("AWS4" + self._secret_key.decode()).encode()
        for part in (date, self._region, "s3", "aws4_request"):
            key = hmac.new(key, part.encode(), hashlib.sha256).digest()
        signature = hmac.new(key, string_to_sign.encode(), hashlib.sha256).hexdigest()
        headers["authorization"] = (
            f"AWS4-HMAC-SHA256 Credential={self._access_key}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        return headers

    def _request(
        self,
        method: str,
        key: str = "",
        *,
        query: dict[str, str] | None = None,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        stream: bool = False,
        headers_only: bool = False,
    ):
        url = self._url(key, query)
        payload_hash = (
            hashlib.sha256(body).hexdigest()
            if body is not None
            else hashlib.sha256(b"").hexdigest()
        )
        signed = self._sign(method, url, dict(headers or {}), payload_hash)
        request = urllib.request.Request(url, data=body, method=method, headers=signed)
        try:
            response = urllib.request.urlopen(request, timeout=self._timeout)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise ObjectMissing("Object does not exist") from None
            if error.code in (409, 412):
                raise ImmutableConflict("Immutable object already exists") from None
            raise StorageError(f"Object storage returned status {error.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise StorageError("Object storage is unavailable") from None
        if stream:
            return response
        with response:
            if headers_only:
                return {name.lower(): value for name, value in response.headers.items()}
            return response.read()

    # --------------------------------------------------------------- capabilities
    def _capability(
        self, key: str, method: str, *, ttl_seconds: int | None = None, **claims: object
    ) -> LocalCapability:
        self._key_prefix(key)
        ttl = self._ttl if ttl_seconds is None else ttl_seconds
        if not 1 <= ttl <= 3600:
            raise StorageError("Capability expiry must be short lived")
        expires = int(self._clock()) + ttl
        payload = json.dumps(
            {"key": key, "method": method, "exp": expires, **claims},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
        signature = hmac.new(
            self._secret, b"penforge.s3-capability.v1\0" + encoded.encode(), hashlib.sha256
        ).digest()
        token = encoded + "." + base64.urlsafe_b64encode(signature).decode().rstrip("=")
        return LocalCapability(token, method, expires)

    def validate_capability(self, token: str, method: str) -> CapabilityClaims:
        try:
            if not _TOKEN.fullmatch(token) or token.count(".") != 1:
                raise ValueError
            encoded, signature = token.split(".")
            expected = hmac.new(
                self._secret, b"penforge.s3-capability.v1\0" + encoded.encode(), hashlib.sha256
            ).digest()
            supplied = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
            if not hmac.compare_digest(expected, supplied):
                raise ValueError
            claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            if claims["method"] != method or claims["exp"] <= int(self._clock()):
                raise ValueError
            if type(claims["size_bytes"]) is not int or claims["size_bytes"] < 0:
                raise ValueError
            digest = claims.get("sha256")
            if digest is not None and not _SHA256.fullmatch(digest):
                raise ValueError
            self._key_prefix(claims["key"])
            return CapabilityClaims(
                claims["key"], method, claims["exp"], claims["size_bytes"], digest
            )
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            raise StorageError("Invalid or expired object capability") from None

    # ------------------------------------------------------------------- protocol
    def create_upload(
        self,
        key: str,
        *,
        size_bytes: int,
        sha256: str | None = None,
        max_bytes: int | None = None,
        ttl_seconds: int | None = None,
    ) -> LocalCapability:
        maximum = (
            self._max_upload_bytes if max_bytes is None else min(max_bytes, self._max_upload_bytes)
        )
        if (
            (sha256 is not None and not _SHA256.fullmatch(sha256))
            or type(size_bytes) is not int
            or not 0 <= size_bytes <= maximum
        ):
            raise StorageError("Invalid upload metadata")
        return self._capability(
            key, "PUT", sha256=sha256, size_bytes=size_bytes, ttl_seconds=ttl_seconds
        )

    def put(self, capability: LocalCapability | str, data: bytes) -> ObjectMetadata:
        return self.put_stream(capability, [data])

    def put_stream(
        self, capability: LocalCapability | str, chunks: Iterable[bytes]
    ) -> ObjectMetadata:
        token = capability.token if isinstance(capability, LocalCapability) else capability
        claims = self.validate_capability(token, "PUT")
        return self._write(
            claims.key,
            chunks,
            size_bytes=claims.size_bytes,
            sha256=claims.sha256,
            expires_at=claims.expires_at,
            immutable=False,
        )

    def put_immutable(self, key: str, data: bytes) -> ObjectMetadata:
        return self.put_immutable_stream(key, [data], size_bytes=len(data))

    def put_immutable_stream(
        self, key: str, chunks: Iterable[bytes], *, size_bytes: int, sha256: str | None = None
    ) -> ObjectMetadata:
        if type(size_bytes) is not int or size_bytes < 0:
            raise StorageError("Invalid object size")
        return self._write(key, chunks, size_bytes=size_bytes, sha256=sha256, immutable=True)

    def _write(
        self,
        key: str,
        chunks: Iterable[bytes],
        *,
        size_bytes: int,
        sha256: str | None,
        immutable: bool,
        expires_at: int | None = None,
    ) -> ObjectMetadata:
        self._key_prefix(key)
        buffer = bytearray()
        digest = hashlib.sha256()
        for chunk in chunks:
            if not isinstance(chunk, bytes):
                raise StorageError("Object chunks must be bytes")
            if len(buffer) + len(chunk) > size_bytes:
                raise StorageError("Upload exceeds its size limit")
            if expires_at is not None and expires_at <= int(self._clock()):
                raise StorageError("Upload capability expired")
            buffer.extend(chunk)
            digest.update(chunk)
        actual = digest.hexdigest()
        if len(buffer) != size_bytes or (sha256 is not None and sha256 != actual):
            raise StorageError("Upload checksum or size mismatch")
        if expires_at is not None and expires_at <= int(self._clock()):
            raise StorageError("Upload capability expired")
        headers = {"x-amz-meta-sha256": actual, "if-none-match": "*"}
        try:
            self._request("PUT", key, body=bytes(buffer), headers=headers)
        except ImmutableConflict:
            # A retried upload may reuse an identical object, never replace different bytes.
            existing = self.get_metadata(key)
            if existing.size_bytes == size_bytes and existing.sha256 == actual:
                return existing
            raise
        verified = self.get_metadata(key)
        if verified.size_bytes != size_bytes or verified.sha256 != actual:
            self.delete_object(key)
            raise StorageError("Stored object does not match the uploaded bytes")
        return verified

    def get_metadata(self, key: str) -> ObjectMetadata:
        self._key_prefix(key)
        response = self._request("HEAD", key, headers_only=True)
        size = int(response.get("content-length") or 0)
        digest = response.get("x-amz-meta-sha256")
        if digest and _SHA256.fullmatch(digest):
            return ObjectMetadata(size, digest)
        return ObjectMetadata(
            size, hashlib.sha256(self.read_object(key, max_bytes=size)).hexdigest()
        )

    def verify_upload(
        self, key: str, *, size_bytes: int, sha256: str | None = None
    ) -> ObjectMetadata:
        metadata = self.get_metadata(key)
        if metadata.size_bytes != size_bytes or (sha256 is not None and metadata.sha256 != sha256):
            raise StorageError("Object checksum or size mismatch")
        return metadata

    def get_download_url(self, key: str, *, ttl_seconds: int | None = None) -> LocalCapability:
        metadata = self.get_metadata(key)
        return self._capability(
            key,
            "GET",
            ttl_seconds=ttl_seconds,
            sha256=metadata.sha256,
            size_bytes=metadata.size_bytes,
        )

    def read(self, capability: LocalCapability | str) -> bytes:
        token = capability.token if isinstance(capability, LocalCapability) else capability
        claims = self.validate_capability(token, "GET")
        data = self.read_object(claims.key, max_bytes=claims.size_bytes)
        if len(data) != claims.size_bytes or hashlib.sha256(data).hexdigest() != claims.sha256:
            raise StorageError("Object integrity check failed")
        return data

    def iter_object(self, key: str, *, chunk_size: int = 65536) -> Iterator[bytes]:
        if not 1 <= chunk_size <= 1024 * 1024:
            raise StorageError("Invalid read chunk size")
        self._key_prefix(key)
        response = self._request("GET", key, stream=True)
        with response:
            while chunk := response.read(chunk_size):
                yield chunk

    def read_object(self, key: str, *, max_bytes: int) -> bytes:
        if type(max_bytes) is not int or max_bytes < 0:
            raise StorageError("Invalid read limit")
        chunks = bytearray()
        for chunk in self.iter_object(key):
            chunks.extend(chunk)
            if len(chunks) > max_bytes:
                raise StorageError("Object exceeds read limit")
        return bytes(chunks)

    def list_objects(self, prefix: str = "") -> list[StoredObject]:
        if prefix:
            _split_key(prefix.rstrip("/"))
        stored: list[StoredObject] = []
        token: str | None = None
        for _ in range(100):
            query = {"list-type": "2"}
            if prefix:
                query["prefix"] = self._prefix + prefix
            if token:
                query["continuation-token"] = token
            try:
                body = self._request("GET", query=query)
                root = ElementTree.fromstring(body)
            except StorageError:
                raise
            except ElementTree.ParseError:
                raise StorageError("Object inventory response was invalid") from None
            namespace = "{http://s3.amazonaws.com/doc/2006-03-01/}"
            contents = root.findall(f"{namespace}Contents") or root.findall("Contents")
            for item in contents:
                key = (
                    (item.findtext(f"{namespace}Key") or item.findtext("Key")) or ""
                ).removeprefix(self._prefix)
                size = int((item.findtext(f"{namespace}Size") or item.findtext("Size")) or 0)
                if not key.startswith(prefix):
                    continue
                digest = self.get_metadata(key).sha256
                stored.append(StoredObject(key, size, digest))
            truncated = (
                root.findtext(f"{namespace}IsTruncated") or root.findtext("IsTruncated") or "false"
            ).lower() == "true"
            token = root.findtext(f"{namespace}NextContinuationToken") or root.findtext(
                "NextContinuationToken"
            )
            if not truncated or not token:
                return sorted(stored, key=lambda item: item.key)
        raise StorageError("Object inventory exceeded its bounded page count")

    def reap_staging(self, *, older_than_seconds: int = 3600) -> int:
        # No local staging files exist in the hosted adapter.
        return 0

    def delete_object(self, key: str) -> None:
        self._key_prefix(key)
        try:
            self._request("DELETE", key)
        except ObjectMissing:
            pass
