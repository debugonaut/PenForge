from dataclasses import dataclass, field
from typing import Iterable, Iterator, Protocol


class StorageError(Exception):
    pass


class ObjectMissing(StorageError):
    pass


class ImmutableConflict(StorageError):
    pass


@dataclass(frozen=True)
class ObjectMetadata:
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class LocalCapability:
    token: str = field(repr=False)
    method: str
    expires_at: int
    resumable: bool = False


@dataclass(frozen=True)
class CapabilityClaims:
    key: str = field(repr=False)
    method: str
    expires_at: int
    size_bytes: int
    sha256: str | None = None


@dataclass(frozen=True)
class StoredObject:
    """Private maintenance inventory; never an API response."""

    key: str = field(repr=False)
    size_bytes: int
    sha256: str


class ObjectStorage(Protocol):
    def close(self) -> None: ...

    def create_upload(
        self,
        key: str,
        *,
        size_bytes: int,
        sha256: str | None = None,
        max_bytes: int | None = None,
        ttl_seconds: int | None = None,
    ) -> LocalCapability: ...
    def validate_capability(self, token: str, method: str) -> CapabilityClaims: ...
    def put(self, capability: LocalCapability | str, data: bytes) -> ObjectMetadata: ...
    def put_stream(
        self, capability: LocalCapability | str, chunks: Iterable[bytes]
    ) -> ObjectMetadata: ...
    def put_immutable(self, key: str, data: bytes) -> ObjectMetadata: ...
    def put_immutable_stream(
        self,
        key: str,
        chunks: Iterable[bytes],
        *,
        size_bytes: int,
        sha256: str | None = None,
    ) -> ObjectMetadata: ...
    def verify_upload(
        self, key: str, *, size_bytes: int, sha256: str | None = None
    ) -> ObjectMetadata: ...
    def get_download_url(self, key: str, *, ttl_seconds: int | None = None) -> LocalCapability: ...
    def read(self, capability: LocalCapability | str) -> bytes: ...
    def iter_object(self, key: str, *, chunk_size: int = 65536) -> Iterator[bytes]: ...
    def read_object(self, key: str, *, max_bytes: int) -> bytes: ...
    def get_metadata(self, key: str) -> ObjectMetadata: ...
    def list_objects(self, prefix: str = "") -> list[StoredObject]: ...
    def reap_staging(self, *, older_than_seconds: int = 3600) -> int: ...
    def delete_object(self, key: str) -> None: ...
