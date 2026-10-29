from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment only; importing the application never reads a local secrets file."""

    model_config = SettingsConfigDict(extra="ignore", hide_input_in_errors=True)
    app_env: Literal["local", "test", "preview", "production"] = "local"
    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://penforge:penforge@127.0.0.1:5433/penforge"
    )
    database_pool_size: int = Field(default=5, ge=1, le=50)
    auth_backend: Literal["fake", "firebase", "disabled"] = "disabled"
    local_auth_token: SecretStr = SecretStr("local-penforge-development-only")
    firebase_project_id: str | None = None
    firebase_auth_emulator_host: str | None = None
    firebase_allow_emulator: bool = False
    claim_max_age_seconds: int = Field(default=600, ge=30, le=3600)
    rate_limit_backend: Literal["local", "postgres", "disabled"] = "local"
    identity_rate_limit: int = Field(default=60, ge=1, le=10000)
    identity_first_request_limit: int = Field(default=10, ge=1, le=1000)
    identity_global_first_request_limit: int = Field(default=100, ge=1, le=10000)
    claim_rate_limit: int = Field(default=10, ge=1, le=1000)
    rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)
    hmac_secret: SecretStr | None = None
    # Stable journal/receipt identity, pinned before rotating the general HMAC key.
    # The fallback reads pre-existing journals without rewriting any tombstone.
    privacy_hmac_secret: SecretStr | None = None
    hmac_key_version: str = Field(default="v1", pattern=r"^[a-zA-Z0-9_-]{1,32}$")
    storage_backend: Literal["local_fs", "s3", "disabled"] = "local_fs"
    storage_root: Path = Path(".local/objects")
    # Any private S3-compatible bucket (R2, Supabase Storage, MinIO, AWS S3).
    storage_s3_endpoint: str | None = None
    storage_s3_bucket: str | None = None
    storage_s3_region: str = "auto"
    storage_s3_access_key: SecretStr | None = None
    storage_s3_secret_key: SecretStr | None = None
    storage_s3_prefix: str = ""
    storage_s3_path_style: bool = True
    upload_issuance_enabled: bool = True
    upload_max_bytes: int = Field(default=20 * 1024 * 1024, ge=1024, le=20 * 1024 * 1024)
    upload_max_pixels: int = Field(default=50_000_000, ge=1, le=50_000_000)
    upload_max_dimension: int = Field(default=12000, ge=1, le=12000)
    upload_files_per_project: int = Field(default=200, ge=1, le=10000)
    pending_upload_ttl_seconds: int = Field(default=86400, ge=60, le=604800)
    signed_upload_ttl_seconds: int = Field(default=900, ge=1, le=3600)
    signed_download_ttl_seconds: int = Field(default=600, ge=1, le=3600)
    derivative_max_bytes: int = Field(default=4 * 1024 * 1024, ge=1024, le=20 * 1024 * 1024)
    derivative_max_dimension: int = Field(default=1000, ge=1, le=2000)
    lease_seconds: int = Field(default=60, ge=1, le=600)
    heartbeat_seconds: int = Field(default=20, ge=1, le=200)
    worker_mem_mb: int = Field(default=768, ge=128, le=8192)
    decode_cpu_seconds: int = Field(default=10, ge=1, le=120)
    decode_wall_seconds: int = Field(default=20, ge=1, le=300)
    worker_mode: Literal["persistent", "scheduled"] = "scheduled"
    worker_max_run_seconds: int = Field(default=300, ge=1, le=86400)
    worker_temp_root: Path = Path(".local/worker-temp")
    job_max_attempts: int = Field(default=3, ge=1, le=10)
    readiness_token: SecretStr | None = None
    cors_allowed_origins: list[str] = ["http://localhost:3000"]
    request_max_bytes: int = Field(default=1024 * 1024, ge=1024, le=20 * 1024 * 1024)
    idempotency_retention_seconds: int = Field(default=86400, ge=60, le=604800)

    service_operator_name: str | None = Field(default=None, min_length=2, max_length=200)
    service_operator_contact: str | None = Field(default=None, min_length=5, max_length=254)
    service_terms_reviewed: bool = False

    @property
    def service_ready(self) -> bool:
        # Draft receipts are testable locally. This never authorises a public launch.
        return self.app_env in ("local", "test") or bool(
            self.service_terms_reviewed
            and self.service_operator_name
            and self.service_operator_name.strip()
            and self.service_operator_contact
            and __import__("re").fullmatch(
                r"[^\s@]+@[^\s@]+\.[^\s@]+", self.service_operator_contact
            )
        )

    @field_validator("database_url")
    @classmethod
    def postgres_url(cls, value: SecretStr) -> SecretStr:
        from sqlalchemy.engine import make_url

        try:
            url = make_url(value.get_secret_value())
            if url.drivername not in ("postgresql", "postgresql+psycopg") or not url.database:
                raise ValueError
            if url.port is not None and not 1 <= url.port <= 65535:
                raise ValueError
        except Exception:
            raise ValueError("A syntactically valid PostgreSQL database URL is required") from None
        return value

    @field_validator("cors_allowed_origins")
    @classmethod
    def origins(cls, value: list[str]) -> list[str]:
        from urllib.parse import urlsplit

        for origin in value:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in ("http", "https")
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("CORS requires explicit origins without paths or credentials")
        return value

    @model_validator(mode="after")
    def safe_adapters(self) -> "Settings":
        import os
        from urllib.parse import urlsplit

        if self.heartbeat_seconds >= self.lease_seconds:
            raise ValueError("Heartbeat interval must be shorter than the lease")
        emulator = self.firebase_auth_emulator_host
        if self.firebase_allow_emulator or emulator:
            if (
                self.app_env not in ("local", "test")
                or not self.firebase_allow_emulator
                or not emulator
                or not self.firebase_project_id
                or not self.firebase_project_id.startswith("demo-")
            ):
                raise ValueError(
                    "Auth emulator requires explicit local/test demo-project configuration"
                )
            parsed = urlsplit("http://" + emulator)
            if (
                parsed.hostname not in ("127.0.0.1", "localhost", "::1")
                or not parsed.port
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.username
                or parsed.password
            ):
                raise ValueError("Auth emulator must use an explicit loopback host and port")
        ambient = os.environ.get("FIREBASE_AUTH_EMULATOR_HOST")
        if ambient and (not self.firebase_allow_emulator or ambient != emulator):
            raise ValueError("Ambient Auth emulator configuration is not authorized")
        if self.auth_backend == "firebase" and (
            not self.firebase_project_id
            or not __import__("re").fullmatch(r"[a-z][a-z0-9-]{4,62}", self.firebase_project_id)
        ):
            raise ValueError("Firebase authentication requires an explicit project ID")
        if self.hmac_secret and len(self.hmac_secret.get_secret_value()) < 32:
            raise ValueError("HMAC secret must have at least 32 characters")
        if self.privacy_hmac_secret and len(self.privacy_hmac_secret.get_secret_value()) < 32:
            raise ValueError("Privacy HMAC secret must have at least 32 characters")
        if self.app_env not in ("local", "test") and (
            self.auth_backend == "fake" or self.storage_backend == "local_fs"
        ):
            raise ValueError("Local adapters are restricted to local/test environments")
        if self.storage_backend == "s3":
            from urllib.parse import urlsplit

            parsed = urlsplit(self.storage_s3_endpoint or "")
            if (
                parsed.scheme not in ("http", "https")
                or not parsed.hostname
                or parsed.query
                or parsed.fragment
                or parsed.username
                or parsed.password
            ):
                raise ValueError("S3 storage requires an explicit http(s) endpoint")
            if parsed.scheme == "http" and (
                self.app_env not in ("local", "test")
                or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
            ):
                raise ValueError("Plain HTTP object storage is restricted to loopback")
            if not self.storage_s3_bucket or not __import__("re").fullmatch(
                r"[a-z0-9][a-z0-9.-]{1,62}", self.storage_s3_bucket
            ):
                raise ValueError("S3 storage requires an explicit bucket")
            if not self.storage_s3_access_key or not self.storage_s3_secret_key:
                raise ValueError("S3 storage requires access and secret keys")
        if self.app_env not in ("local", "test") and self.auth_backend == "firebase":
            if self.rate_limit_backend != "postgres":
                raise ValueError(
                    "Hosted identity requires the shared PostgreSQL rate-limit backend"
                )
            if self.storage_backend != "s3":
                raise ValueError("Hosted identity requires private S3-compatible object storage")
        if self.readiness_token and len(self.readiness_token.get_secret_value()) < 32:
            raise ValueError("Readiness token must have at least 32 characters")
        if not self.database_url.get_secret_value().startswith(
            ("postgresql://", "postgresql+psycopg://")
        ):
            raise ValueError("A PostgreSQL URL is required")
        return self

    @property
    def privacy_key(self) -> bytes | None:
        key = self.privacy_hmac_secret or self.hmac_secret
        return key.get_secret_value().encode() if key else None
