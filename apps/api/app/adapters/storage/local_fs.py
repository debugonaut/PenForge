"""Private immutable local objects and opaque, short-lived transport capabilities.

The HTTP service wraps capabilities in browser URLs and checks live ownership/state.
All directory walking uses pinned descriptors and O_NOFOLLOW, including inventory.
"""

import base64
import hashlib
import json
import os
import re
import secrets
import stat
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.adapters.storage.base import (
    CapabilityClaims,
    ImmutableConflict,
    LocalCapability,
    ObjectMetadata,
    ObjectMissing,
    StorageError,
    StoredObject,
)


class LocalObjectStorage:
    def __init__(
        self,
        root: Path,
        *,
        secret: bytes | None = None,
        ttl_seconds: int = 600,
        max_upload_bytes: int = 20 * 1024 * 1024,
    ):
        absolute = root.absolute()
        if ".." in absolute.parts:
            raise StorageError("Storage root must not contain parent traversal")
        # Pin each ancestor using descriptor-relative operations; checking and
        # subsequently opening a full path would leave an ancestor symlink race.
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in absolute.parts[1:]:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            root_stat = os.fstat(fd)
            if root_stat.st_uid != os.getuid() or stat.S_IMODE(root_stat.st_mode) & 0o077:
                raise StorageError("Storage root must be owner-private (0700)")
        except (OSError, StorageError):
            os.close(fd)
            raise StorageError("Storage root must be private and free of symlinks") from None
        self.root = absolute
        self._root_fd = fd
        self._secret = secret or secrets.token_bytes(32)
        self._ttl = ttl_seconds
        self._max_upload_bytes = max_upload_bytes
        self._aead = AESGCM(
            hashlib.sha256(b"penforge.local-capability.v1\0" + self._secret).digest()
        )

    def close(self) -> None:
        if self._root_fd >= 0:
            os.close(self._root_fd)
            self._root_fd = -1

    @staticmethod
    def _parts(key: str) -> list[str]:
        parts = key.split("/")
        if (
            len(key) > 1024
            or not parts
            or any(
                not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", part) or part in (".", "..")
                for part in parts
            )
        ):
            raise StorageError("Invalid object key")
        return parts

    @contextmanager
    def _parent(self, key: str, *, create: bool = False) -> Iterator[tuple[int, str]]:
        parts = self._parts(key)
        fd = os.dup(self._root_fd)
        try:
            for part in parts[:-1]:
                if create:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            yield fd, parts[-1]
        except FileNotFoundError:
            raise ObjectMissing("Object does not exist") from None
        except OSError:
            raise StorageError("Unsafe or inaccessible object path") from None
        finally:
            os.close(fd)

    @contextmanager
    def _open(self, key: str) -> Iterator:
        with self._parent(key) as (fd, name):
            file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            with os.fdopen(file_fd, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise StorageError("Object must be a regular file")
                yield stream

    def get_metadata(self, key: str) -> ObjectMetadata:
        with self._open(key) as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
            return ObjectMetadata(os.fstat(stream.fileno()).st_size, digest)

    def verify_upload(
        self, key: str, *, size_bytes: int, sha256: str | None = None
    ) -> ObjectMetadata:
        metadata = self.get_metadata(key)
        if metadata.size_bytes != size_bytes or (sha256 is not None and metadata.sha256 != sha256):
            raise StorageError("Object checksum or size mismatch")
        return metadata

    def _capability(
        self, key: str, method: str, *, ttl_seconds: int | None = None, **claims: object
    ) -> LocalCapability:
        self._parts(key)
        ttl = self._ttl if ttl_seconds is None else ttl_seconds
        if not 1 <= ttl <= 3600:
            raise StorageError("Capability expiry must be short lived")
        expires = int(time.time()) + ttl
        body = json.dumps(
            {"key": key, "method": method, "exp": expires, **claims},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        nonce = secrets.token_bytes(12)
        encrypted = self._aead.encrypt(nonce, body, b"penforge.local-capability.v1")
        token = base64.urlsafe_b64encode(nonce + encrypted).decode().rstrip("=")
        return LocalCapability(token, method, expires)

    def validate_capability(self, token: str, method: str) -> CapabilityClaims:
        try:
            if not re.fullmatch(r"[A-Za-z0-9_-]{32,4096}", token):
                raise ValueError
            encrypted = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
            body = self._aead.decrypt(
                encrypted[:12], encrypted[12:], b"penforge.local-capability.v1"
            )
            claims = json.loads(body)
            # Require the canonical unpadded encoding (reject equivalent token spellings).
            if base64.urlsafe_b64encode(encrypted).decode().rstrip("=") != token:
                raise ValueError
            if claims["method"] != method or claims["exp"] <= int(time.time()):
                raise ValueError
            if type(claims["size_bytes"]) is not int or claims["size_bytes"] < 0:
                raise ValueError
            digest = claims.get("sha256")
            if digest is not None and not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError
            self._parts(claims["key"])
            return CapabilityClaims(
                claims["key"], method, claims["exp"], claims["size_bytes"], digest
            )
        except (ValueError, KeyError, TypeError, InvalidTag):
            raise StorageError("Invalid or expired local capability") from None

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
            (sha256 is not None and not re.fullmatch("[0-9a-f]{64}", sha256))
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
        )

    def put_immutable(self, key: str, data: bytes) -> ObjectMetadata:
        return self.put_immutable_stream(key, [data], size_bytes=len(data))

    def put_immutable_stream(
        self, key: str, chunks: Iterable[bytes], *, size_bytes: int, sha256: str | None = None
    ) -> ObjectMetadata:
        if type(size_bytes) is not int or size_bytes < 0:
            raise StorageError("Invalid object size")
        return self._write(key, chunks, size_bytes=size_bytes, sha256=sha256)

    def _write(
        self,
        key: str,
        chunks: Iterable[bytes],
        *,
        size_bytes: int,
        sha256: str | None,
        expires_at: int | None = None,
    ) -> ObjectMetadata:
        with self._parent(key, create=True) as (fd, name):
            temporary = ".staged-" + secrets.token_hex(16)
            temporary_fd = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd
            )
            try:
                digest = hashlib.sha256()
                count = 0
                with os.fdopen(temporary_fd, "wb") as stream:
                    for chunk in chunks:
                        if not isinstance(chunk, bytes):
                            raise StorageError("Object chunks must be bytes")
                        count += len(chunk)
                        if count > size_bytes:
                            raise StorageError("Upload exceeds its size limit")
                        if expires_at is not None and expires_at <= int(time.time()):
                            raise StorageError("Upload capability expired")
                        digest.update(chunk)
                        stream.write(chunk)
                    actual = digest.hexdigest()
                    if count != size_bytes or (sha256 is not None and sha256 != actual):
                        raise StorageError("Upload checksum or size mismatch")
                    if expires_at is not None and expires_at <= int(time.time()):
                        raise StorageError("Upload capability expired")
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    # link is atomic and never replaces an existing object.
                    os.link(temporary, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
                    os.fsync(fd)
                except FileExistsError:
                    if self.get_metadata(key) != ObjectMetadata(count, actual):
                        raise ImmutableConflict("Immutable object already has different content")
            finally:
                os.unlink(temporary, dir_fd=fd)
        return ObjectMetadata(count, actual)

    def get_download_url(self, key: str, *, ttl_seconds: int | None = None) -> LocalCapability:
        metadata = self.get_metadata(key)
        return self._capability(
            key,
            "GET",
            sha256=metadata.sha256,
            size_bytes=metadata.size_bytes,
            ttl_seconds=ttl_seconds,
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
        with self._open(key) as stream:
            while chunk := stream.read(chunk_size):
                yield chunk

    def read_object(self, key: str, *, max_bytes: int) -> bytes:
        if type(max_bytes) is not int or max_bytes < 0:
            raise StorageError("Invalid read limit")
        with self._open(key) as stream:
            if os.fstat(stream.fileno()).st_size > max_bytes:
                raise StorageError("Object exceeds read limit")
            data = stream.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise StorageError("Object exceeds read limit")
        return data

    def _inventory(self, fd: int, prefix: str = "") -> Iterator[tuple[str, int, str]]:
        # Never follow symlinks or expose temporary files as committed objects.
        for name in sorted(os.listdir(fd)):
            try:
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            key = prefix + name
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    yield from self._inventory(child, key + "/")
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode) and not name.startswith(".staged-"):
                self._parts(key)
                file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                with os.fdopen(file_fd, "rb") as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        raise StorageError("Object must be a regular file")
                    yield (
                        key,
                        os.fstat(stream.fileno()).st_size,
                        hashlib.file_digest(stream, "sha256").hexdigest(),
                    )

    def list_objects(self, prefix: str = "") -> list[StoredObject]:
        if prefix:
            self._parts(prefix.rstrip("/"))
        try:
            return [
                StoredObject(key, size, digest)
                for key, size, digest in self._inventory(self._root_fd)
                if key.startswith(prefix)
            ]
        except OSError:
            raise StorageError("Unsafe or inaccessible inventory path") from None

    def reap_staging(self, *, older_than_seconds: int = 3600) -> int:
        if older_than_seconds < 60:
            raise StorageError("Staging expiry must allow active writes to finish")
        cutoff = time.time() - older_than_seconds

        def walk(fd: int) -> int:
            removed = 0
            for name in os.listdir(fd):
                try:
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        removed += walk(child)
                    finally:
                        os.close(child)
                elif (
                    stat.S_ISREG(info.st_mode)
                    and re.fullmatch(r"\.staged-[0-9a-f]{32}", name)
                    and info.st_mtime < cutoff
                ):
                    os.unlink(name, dir_fd=fd)
                    removed += 1
            return removed

        try:
            return walk(self._root_fd)
        except OSError:
            raise StorageError("Unsafe or inaccessible staging path") from None

    def delete_object(self, key: str) -> None:
        try:
            with self._parent(key) as (fd, name):
                mode = os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode
                if not stat.S_ISREG(mode):
                    raise StorageError("Object must be a regular file")
                os.unlink(name, dir_fd=fd)
                os.fsync(fd)
        except ObjectMissing:
            pass
